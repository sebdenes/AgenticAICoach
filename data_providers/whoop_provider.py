"""Whoop data provider — wraps WhoopClient and normalises to NormalizedRecord."""

from __future__ import annotations

import logging
from datetime import date, datetime

from data_providers.base import DataProvider, NormalizedRecord, MetricType

log = logging.getLogger("coach.data_providers.whoop")


class WhoopProvider:
    """DataProvider implementation backed by WhoopClient."""

    def __init__(self, whoop_client):
        """Wrap an existing WhoopClient instance.

        Parameters
        ----------
        whoop_client : whoop.WhoopClient
            An already-initialised Whoop client (may or may not be authenticated).
        """
        self._client = whoop_client

    # -- Protocol properties --------------------------------------------------

    @property
    def name(self) -> str:
        return "whoop"

    @property
    def supported_categories(self) -> list[str]:
        return ["wellness", "sleep", "activity", "recovery"]

    # -- Fetch methods --------------------------------------------------------

    async def fetch_wellness(self, start: date, end: date) -> list[NormalizedRecord]:
        """Fetch recovery + cycle data and normalise to wellness records."""
        days = max((end - start).days, 1)
        records: list[NormalizedRecord] = []
        try:
            raw_recovery = await self._client.recovery(days=days)
        except Exception as exc:
            log.warning("Whoop recovery fetch failed: %s", exc)
            raw_recovery = []

        for rec in raw_recovery:
            score = rec.get("score") or {}
            ts = _parse_ts(rec.get("created_at") or rec.get("start") or "")
            if ts is None:
                continue
            metrics = {}
            if score.get("hrv_rmssd_milli") is not None:
                metrics[MetricType.HRV_RMSSD_MS.value] = float(score["hrv_rmssd_milli"])
            if score.get("resting_heart_rate") is not None:
                metrics[MetricType.RHR.value] = float(score["resting_heart_rate"])
            if score.get("spo2_percentage") is not None:
                metrics[MetricType.SPO2.value] = float(score["spo2_percentage"])
            if score.get("skin_temp_celsius") is not None:
                metrics[MetricType.SKIN_TEMP_C.value] = float(score["skin_temp_celsius"])
            if score.get("recovery_score") is not None:
                metrics[MetricType.RECOVERY_SCORE.value] = float(score["recovery_score"])
            if metrics:
                records.append(NormalizedRecord(
                    timestamp=ts,
                    category="wellness",
                    source="whoop",
                    metrics=metrics,
                    confidence=0.9,
                    raw=rec,
                ))
        return records

    async def fetch_activities(self, start: date, end: date) -> list[NormalizedRecord]:
        """Fetch workouts and normalise to activity records."""
        days = max((end - start).days, 1)
        records: list[NormalizedRecord] = []
        try:
            raw_workouts = await self._client.workouts(days=days)
        except Exception as exc:
            log.warning("Whoop workouts fetch failed: %s", exc)
            return records

        from whoop import SPORT_MAP

        for w in raw_workouts:
            score = w.get("score") or {}
            ts = _parse_ts(w.get("start") or "")
            if ts is None:
                continue
            sport_id = w.get("sport_id", -1)
            sport_name = SPORT_MAP.get(sport_id, w.get("sport_name", "Unknown"))
            metrics = {MetricType.SPORT.value: sport_name}
            if score.get("strain") is not None:
                metrics[MetricType.STRAIN.value] = float(score["strain"])
            if score.get("average_heart_rate") is not None:
                metrics[MetricType.AVG_HR.value] = float(score["average_heart_rate"])
            if score.get("max_heart_rate") is not None:
                metrics[MetricType.MAX_HR.value] = float(score["max_heart_rate"])
            if score.get("kilojoule") is not None:
                metrics[MetricType.CALORIES.value] = float(score["kilojoule"]) / 4.184
            if score.get("distance_meter") is not None:
                metrics[MetricType.DISTANCE_M.value] = float(score["distance_meter"])
            records.append(NormalizedRecord(
                timestamp=ts,
                category="activity",
                source="whoop",
                metrics=metrics,
                confidence=0.7,
                raw=w,
            ))
        return records

    async def fetch_sleep(self, start: date, end: date) -> list[NormalizedRecord]:
        """Fetch sleep records and normalise."""
        days = max((end - start).days, 1)
        records: list[NormalizedRecord] = []
        try:
            raw_sleep = await self._client.sleep(days=days)
        except Exception as exc:
            log.warning("Whoop sleep fetch failed: %s", exc)
            return records

        for s in raw_sleep:
            score = s.get("score") or {}
            ts = _parse_ts(s.get("start") or "")
            if ts is None:
                continue
            stage = score.get("stage_summary") or {}
            need = score.get("sleep_needed") or {}

            total_bed_ms = stage.get("total_in_bed_time_milli", 0) or 0
            awake_ms = stage.get("total_awake_time_milli", 0) or 0
            sleep_ms = total_bed_ms - awake_ms
            rem_ms = stage.get("total_rem_sleep_time_milli", 0) or 0
            deep_ms = stage.get("total_slow_wave_sleep_time_milli", 0) or 0
            light_ms = stage.get("total_light_sleep_time_milli", 0) or 0
            need_total_ms = (
                (need.get("baseline_milli", 0) or 0)
                + (need.get("need_from_sleep_debt_milli", 0) or 0)
                + (need.get("need_from_recent_strain_milli", 0) or 0)
                - (need.get("need_from_recent_nap_milli", 0) or 0)
            )
            debt_ms = need.get("need_from_sleep_debt_milli", 0) or 0

            metrics = {
                MetricType.SLEEP_DURATION_S.value: sleep_ms / 1000,
                MetricType.SLEEP_IN_BED_S.value: total_bed_ms / 1000,
                MetricType.SLEEP_REM_S.value: rem_ms / 1000,
                MetricType.SLEEP_DEEP_S.value: deep_ms / 1000,
                MetricType.SLEEP_LIGHT_S.value: light_ms / 1000,
                MetricType.SLEEP_AWAKE_S.value: awake_ms / 1000,
                MetricType.SLEEP_NEED_S.value: need_total_ms / 1000,
                MetricType.SLEEP_DEBT_S.value: debt_ms / 1000,
            }
            if score.get("sleep_performance_percentage") is not None:
                metrics[MetricType.SLEEP_PERFORMANCE.value] = float(score["sleep_performance_percentage"])
            if score.get("sleep_efficiency_percentage") is not None:
                metrics[MetricType.SLEEP_EFFICIENCY.value] = float(score["sleep_efficiency_percentage"])
            if score.get("respiratory_rate") is not None:
                metrics[MetricType.SLEEP_RESP_RATE.value] = float(score["respiratory_rate"])

            records.append(NormalizedRecord(
                timestamp=ts,
                category="sleep",
                source="whoop",
                metrics=metrics,
                confidence=0.9,
                raw=s,
            ))
        return records

    async def is_connected(self) -> bool:
        return self._client.is_authenticated


# -- Helpers ------------------------------------------------------------------

def _parse_ts(ts_str: str) -> datetime | None:
    """Parse an ISO-8601-ish timestamp from Whoop."""
    if not ts_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(ts_str.replace("+00:00", "Z").split("+")[0].split("Z")[0] + "Z"
                                     if "Z" in ts_str or "+" in ts_str
                                     else ts_str, fmt)
        except ValueError:
            continue
    # Last-resort: just try date portion
    try:
        return datetime.strptime(ts_str[:10], "%Y-%m-%d")
    except ValueError:
        return None
