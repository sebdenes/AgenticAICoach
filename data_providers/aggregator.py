"""Data aggregator — merge data from multiple providers with conflict resolution."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import date, datetime

from data_providers.base import DataProvider, NormalizedRecord

log = logging.getLogger("coach.data_providers.aggregator")


class DataAggregator:
    """Fetch from multiple DataProviders in parallel, merge, and deduplicate."""

    def __init__(self, providers: list[DataProvider], source_priority: list[str] | None = None):
        """Initialise with a list of providers.

        Parameters
        ----------
        providers : list[DataProvider]
            Active data providers.
        source_priority : list[str] | None
            Tie-break order when confidence is equal.
            Default: ["whoop", "intervals", "garmin", "strava", "oura"]
        """
        self.providers = providers
        self.source_priority = source_priority or [
            "whoop", "intervals", "garmin", "strava", "oura",
        ]

    async def fetch_all_wellness(self, start: date, end: date) -> list[NormalizedRecord]:
        """Fetch wellness data from all providers in parallel, merge and deduplicate."""
        all_records = await self._parallel_fetch("fetch_wellness", start, end)
        return self._merge(all_records)

    async def fetch_all_activities(self, start: date, end: date) -> list[NormalizedRecord]:
        """Fetch activities from all providers in parallel, merge and deduplicate."""
        all_records = await self._parallel_fetch("fetch_activities", start, end)
        return self._merge(all_records)

    async def fetch_all_sleep(self, start: date, end: date) -> list[NormalizedRecord]:
        """Fetch sleep data from all providers in parallel, merge and deduplicate."""
        all_records = await self._parallel_fetch("fetch_sleep", start, end)
        return self._merge(all_records)

    async def fetch_all(self, start: date, end: date) -> dict[str, list[NormalizedRecord]]:
        """Fetch all categories in parallel and return as a dict."""
        wellness, activities, sleep = await asyncio.gather(
            self.fetch_all_wellness(start, end),
            self.fetch_all_activities(start, end),
            self.fetch_all_sleep(start, end),
            return_exceptions=True,
        )
        return {
            "wellness": wellness if not isinstance(wellness, Exception) else [],
            "activities": activities if not isinstance(activities, Exception) else [],
            "sleep": sleep if not isinstance(sleep, Exception) else [],
        }

    def get_latest(self, records: list[NormalizedRecord], metric: str) -> float | None:
        """Get the most recent value for a given metric from a list of records."""
        best_ts = None
        best_val = None
        for rec in records:
            val = rec.get(metric)
            if val is not None and (best_ts is None or rec.timestamp > best_ts):
                best_ts = rec.timestamp
                best_val = val
        return best_val

    async def get_provider_status(self) -> list[dict]:
        """Check connectivity of all providers."""
        results = []
        for p in self.providers:
            try:
                connected = await p.is_connected()
            except Exception:
                connected = False
            results.append({
                "name": p.name,
                "connected": connected,
                "categories": p.supported_categories,
            })
        return results

    # -- Internal methods -----------------------------------------------------

    async def _parallel_fetch(
        self, method_name: str, start: date, end: date,
    ) -> list[NormalizedRecord]:
        """Call the same fetch method on all providers in parallel."""
        tasks = []
        for provider in self.providers:
            method = getattr(provider, method_name, None)
            if method:
                tasks.append(self._safe_fetch(provider.name, method, start, end))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_records: list[NormalizedRecord] = []
        for result in results:
            if isinstance(result, Exception):
                log.warning("Provider fetch failed: %s", result)
            elif isinstance(result, list):
                all_records.extend(result)
        return all_records

    @staticmethod
    async def _safe_fetch(name: str, method, start: date, end: date) -> list[NormalizedRecord]:
        """Wrap a provider fetch call with error handling."""
        try:
            return await method(start, end)
        except Exception as exc:
            log.warning("Provider %s fetch error: %s", name, exc)
            return []

    def _merge(self, records: list[NormalizedRecord]) -> list[NormalizedRecord]:
        """Merge records by date+category, preferring higher-confidence sources.

        For each (date, category) bucket:
        - Collect all metrics from all sources.
        - When the same metric appears from multiple sources, keep the one with
          higher confidence (or higher source priority if confidence is equal).
        - Produce a single merged NormalizedRecord per (date, category).
        """
        # Group by (date_str, category)
        buckets: dict[tuple[str, str], list[NormalizedRecord]] = defaultdict(list)
        for rec in records:
            buckets[(rec.date_str, rec.category)].append(rec)

        merged: list[NormalizedRecord] = []
        for (date_str, category), bucket in sorted(buckets.items()):
            if len(bucket) == 1:
                merged.append(bucket[0])
                continue

            # Merge metrics from all records in this bucket
            best_metrics: dict[str, tuple[float | str, float, str]] = {}  # metric -> (value, confidence, source)
            best_ts = bucket[0].timestamp
            combined_raw = {}

            for rec in bucket:
                if rec.timestamp > best_ts:
                    best_ts = rec.timestamp
                combined_raw[rec.source] = rec.raw
                for metric_key, value in rec.metrics.items():
                    existing = best_metrics.get(metric_key)
                    if existing is None:
                        best_metrics[metric_key] = (value, rec.confidence, rec.source)
                    else:
                        _, ex_conf, ex_source = existing
                        if rec.confidence > ex_conf:
                            best_metrics[metric_key] = (value, rec.confidence, rec.source)
                        elif rec.confidence == ex_conf:
                            # Tie-break by source priority
                            if self._source_rank(rec.source) < self._source_rank(ex_source):
                                best_metrics[metric_key] = (value, rec.confidence, rec.source)

            # Build merged record
            final_metrics = {k: v[0] for k, v in best_metrics.items()}
            sources = set(r.source for r in bucket)
            avg_conf = sum(r.confidence for r in bucket) / len(bucket)

            merged.append(NormalizedRecord(
                timestamp=best_ts,
                category=category,
                source="+".join(sorted(sources)),
                metrics=final_metrics,
                confidence=avg_conf,
                raw=combined_raw,
            ))

        return sorted(merged, key=lambda r: r.timestamp)

    def _source_rank(self, source: str) -> int:
        """Lower rank = higher priority."""
        try:
            return self.source_priority.index(source)
        except ValueError:
            return len(self.source_priority)
