"""Intervals.icu data provider — wraps IntervalsClient and normalises to NormalizedRecord."""

from __future__ import annotations

import logging
from datetime import date, datetime

from data_providers.base import DataProvider, NormalizedRecord, MetricType

log = logging.getLogger("coach.data_providers.intervals")


class IntervalsProvider:
    """DataProvider implementation backed by IntervalsClient."""

    def __init__(self, intervals_client):
        """Wrap an existing IntervalsClient instance.

        Parameters
        ----------
        intervals_client : intervals.IntervalsClient
            An already-initialised Intervals.icu client.
        """
        self._client = intervals_client

    # -- Protocol properties --------------------------------------------------

    @property
    def name(self) -> str:
        return "intervals"

    @property
    def supported_categories(self) -> list[str]:
        return ["wellness", "activity"]

    # -- Fetch methods --------------------------------------------------------

    async def fetch_wellness(self, start: date, end: date) -> list[NormalizedRecord]:
        """Fetch wellness data from Intervals.icu and normalise."""
        days = max((end - start).days, 1)
        records: list[NormalizedRecord] = []
        try:
            raw = await self._client.wellness(days=days, force=True)
        except Exception as exc:
            log.warning("Intervals wellness fetch failed: %s", exc)
            return records

        for entry in raw:
            date_str = (entry.get("id") or "")[:10]
            if not date_str:
                continue
            try:
                ts = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue

            metrics = {}
            # Training load metrics (Intervals.icu is the authority)
            if entry.get("ctl") is not None:
                metrics[MetricType.CTL.value] = float(entry["ctl"])
            if entry.get("atl") is not None:
                metrics[MetricType.ATL.value] = float(entry["atl"])
            if entry.get("ctl") is not None and entry.get("atl") is not None:
                metrics[MetricType.TSB.value] = float(entry["ctl"]) - float(entry["atl"])
            # Wellness metrics (may be synced from Whoop)
            if entry.get("hrv") is not None:
                metrics[MetricType.HRV_RMSSD_MS.value] = float(entry["hrv"])
            if entry.get("restingHR") is not None:
                metrics[MetricType.RHR.value] = float(entry["restingHR"])
            # Sleep (from device sync)
            sleep_secs = entry.get("sleepSecs", 0) or 0
            if sleep_secs > 0:
                metrics[MetricType.SLEEP_DURATION_S.value] = float(sleep_secs)
            if entry.get("sleepScore") is not None:
                metrics[MetricType.SLEEP_SCORE.value] = float(entry["sleepScore"])
            if entry.get("weight") is not None:
                metrics[MetricType.BODY_WEIGHT_KG.value] = float(entry["weight"])

            if metrics:
                # CTL/ATL/TSB from Intervals = high confidence; HRV/sleep synced = medium
                has_load = MetricType.CTL.value in metrics
                records.append(NormalizedRecord(
                    timestamp=ts,
                    category="wellness",
                    source="intervals",
                    metrics=metrics,
                    confidence=0.9 if has_load else 0.7,
                    raw=entry,
                ))
        return records

    async def fetch_activities(self, start: date, end: date) -> list[NormalizedRecord]:
        """Fetch activities from Intervals.icu and normalise."""
        days = max((end - start).days, 1)
        records: list[NormalizedRecord] = []
        try:
            raw = await self._client.activities(days=days, force=True)
        except Exception as exc:
            log.warning("Intervals activities fetch failed: %s", exc)
            return records

        for act in raw:
            if not act.get("type"):
                continue
            date_str = (act.get("start_date_local") or act.get("date") or "")[:10]
            if not date_str:
                continue
            try:
                ts = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue

            metrics = {
                MetricType.SPORT.value: act.get("type", "Unknown"),
            }
            if act.get("icu_training_load") is not None:
                metrics[MetricType.TSS.value] = float(act["icu_training_load"])
            if act.get("icu_intensity") is not None:
                metrics[MetricType.INTENSITY_FACTOR.value] = float(act["icu_intensity"])
            moving = act.get("moving_time", 0) or 0
            if moving > 0:
                metrics[MetricType.DURATION_S.value] = float(moving)
            dist = act.get("distance", 0) or 0
            if dist > 0:
                metrics[MetricType.DISTANCE_M.value] = float(dist)
            if act.get("average_heartrate") is not None:
                metrics[MetricType.AVG_HR.value] = float(act["average_heartrate"])
            if act.get("max_heartrate") is not None:
                metrics[MetricType.MAX_HR.value] = float(act["max_heartrate"])
            if act.get("weighted_average_watts") is not None:
                metrics[MetricType.NORM_POWER.value] = float(act["weighted_average_watts"])
            if act.get("average_watts") is not None:
                metrics[MetricType.AVG_POWER.value] = float(act["average_watts"])
            if act.get("total_elevation_gain") is not None:
                metrics[MetricType.ELEVATION_M.value] = float(act["total_elevation_gain"])

            records.append(NormalizedRecord(
                timestamp=ts,
                category="activity",
                source="intervals",
                metrics=metrics,
                confidence=0.9,
                raw=act,
            ))
        return records

    async def fetch_sleep(self, start: date, end: date) -> list[NormalizedRecord]:
        """Intervals.icu has limited sleep data (synced from devices).

        Returns sleep records derived from wellness data.
        """
        days = max((end - start).days, 1)
        records: list[NormalizedRecord] = []
        try:
            raw = await self._client.wellness(days=days, force=True)
        except Exception as exc:
            log.warning("Intervals wellness (for sleep) fetch failed: %s", exc)
            return records

        for entry in raw:
            sleep_secs = entry.get("sleepSecs", 0) or 0
            if sleep_secs <= 0:
                continue
            date_str = (entry.get("id") or "")[:10]
            if not date_str:
                continue
            try:
                ts = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue
            metrics = {
                MetricType.SLEEP_DURATION_S.value: float(sleep_secs),
            }
            if entry.get("sleepScore") is not None:
                metrics[MetricType.SLEEP_SCORE.value] = float(entry["sleepScore"])
            records.append(NormalizedRecord(
                timestamp=ts,
                category="sleep",
                source="intervals",
                metrics=metrics,
                confidence=0.6,  # synced data, less reliable than primary source
                raw=entry,
            ))
        return records

    async def is_connected(self) -> bool:
        try:
            await self._client.wellness(days=1, force=True)
            return True
        except Exception:
            return False
