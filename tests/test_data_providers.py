"""Tests for the data provider abstraction layer — NormalizedRecord, DataAggregator."""

import asyncio
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_providers.base import NormalizedRecord, MetricType, DataProvider
from data_providers.aggregator import DataAggregator


# ===========================================================================
# NormalizedRecord
# ===========================================================================

class TestNormalizedRecord:
    def test_creation(self):
        rec = NormalizedRecord(
            timestamp=datetime(2025, 3, 7, 8, 0, 0),
            category="wellness",
            source="whoop",
            metrics={
                MetricType.HRV_RMSSD_MS.value: 55.2,
                MetricType.RHR.value: 43,
                MetricType.RECOVERY_SCORE.value: 78,
            },
            confidence=0.9,
            raw={"original_data": "here"},
        )
        assert rec.category == "wellness"
        assert rec.source == "whoop"
        assert rec.confidence == 0.9
        assert rec.date_str == "2025-03-07"

    def test_get_by_metric_type(self):
        rec = NormalizedRecord(
            timestamp=datetime(2025, 3, 7),
            category="wellness",
            source="whoop",
            metrics={MetricType.HRV_RMSSD_MS.value: 55.2},
        )
        assert rec.get(MetricType.HRV_RMSSD_MS) == 55.2

    def test_get_by_string(self):
        rec = NormalizedRecord(
            timestamp=datetime(2025, 3, 7),
            category="wellness",
            source="whoop",
            metrics={"hrv_rmssd_ms": 55.2},
        )
        assert rec.get("hrv_rmssd_ms") == 55.2

    def test_get_missing_returns_default(self):
        rec = NormalizedRecord(
            timestamp=datetime(2025, 3, 7),
            category="wellness",
            source="whoop",
            metrics={},
        )
        assert rec.get(MetricType.HRV_RMSSD_MS) is None
        assert rec.get(MetricType.HRV_RMSSD_MS, 0.0) == 0.0

    def test_date_str_format(self):
        rec = NormalizedRecord(
            timestamp=datetime(2025, 1, 5, 14, 30),
            category="sleep",
            source="intervals",
            metrics={},
        )
        assert rec.date_str == "2025-01-05"

    def test_default_confidence(self):
        rec = NormalizedRecord(
            timestamp=datetime(2025, 3, 7),
            category="activity",
            source="strava",
            metrics={},
        )
        assert rec.confidence == 0.8

    def test_default_raw(self):
        rec = NormalizedRecord(
            timestamp=datetime(2025, 3, 7),
            category="activity",
            source="strava",
            metrics={},
        )
        assert rec.raw == {}


# ===========================================================================
# MetricType enum
# ===========================================================================

class TestMetricType:
    def test_wellness_metrics_exist(self):
        assert MetricType.HRV_RMSSD_MS.value == "hrv_rmssd_ms"
        assert MetricType.RHR.value == "resting_heart_rate"
        assert MetricType.RECOVERY_SCORE.value == "recovery_score"

    def test_sleep_metrics_exist(self):
        assert MetricType.SLEEP_DURATION_S.value == "sleep_duration_seconds"
        assert MetricType.SLEEP_SCORE.value == "sleep_score"
        assert MetricType.SLEEP_DEEP_S.value == "deep_seconds"

    def test_training_load_metrics_exist(self):
        assert MetricType.CTL.value == "ctl"
        assert MetricType.ATL.value == "atl"
        assert MetricType.TSS.value == "training_stress_score"

    def test_activity_metrics_exist(self):
        assert MetricType.DURATION_S.value == "duration_seconds"
        assert MetricType.DISTANCE_M.value == "distance_meters"
        assert MetricType.AVG_HR.value == "average_heart_rate"


# ===========================================================================
# Mock provider helper
# ===========================================================================

def _make_mock_provider(name, categories, wellness_data=None, activity_data=None, sleep_data=None):
    """Create a mock DataProvider with specified return data."""
    provider = AsyncMock()
    provider.name = name
    provider.supported_categories = categories
    provider.fetch_wellness = AsyncMock(return_value=wellness_data or [])
    provider.fetch_activities = AsyncMock(return_value=activity_data or [])
    provider.fetch_sleep = AsyncMock(return_value=sleep_data or [])
    provider.is_connected = AsyncMock(return_value=True)
    return provider


def _make_record(date_str, source, category, metrics, confidence=0.8):
    """Convenience to create a NormalizedRecord from a date string."""
    return NormalizedRecord(
        timestamp=datetime.strptime(date_str, "%Y-%m-%d"),
        category=category,
        source=source,
        metrics=metrics,
        confidence=confidence,
    )


# ===========================================================================
# DataAggregator — merge logic
# ===========================================================================

class TestDataAggregatorMerge:
    def test_single_source_no_merge_needed(self):
        """With only one source, records pass through unchanged."""
        rec1 = _make_record("2025-03-07", "whoop", "wellness",
                            {MetricType.HRV_RMSSD_MS.value: 55.0}, confidence=0.9)
        rec2 = _make_record("2025-03-06", "whoop", "wellness",
                            {MetricType.HRV_RMSSD_MS.value: 52.0}, confidence=0.9)
        agg = DataAggregator([])
        merged = agg._merge([rec1, rec2])
        assert len(merged) == 2

    def test_conflicting_records_higher_confidence_wins(self):
        """When two sources have the same metric, higher confidence wins."""
        whoop_rec = _make_record("2025-03-07", "whoop", "wellness",
                                 {MetricType.HRV_RMSSD_MS.value: 55.0}, confidence=0.9)
        intervals_rec = _make_record("2025-03-07", "intervals", "wellness",
                                     {MetricType.HRV_RMSSD_MS.value: 52.0}, confidence=0.7)
        agg = DataAggregator([])
        merged = agg._merge([whoop_rec, intervals_rec])
        assert len(merged) == 1
        assert merged[0].get(MetricType.HRV_RMSSD_MS) == 55.0  # whoop wins (0.9 > 0.7)

    def test_complementary_metrics_are_combined(self):
        """Different metrics from different sources should both appear in merged record."""
        whoop_rec = _make_record("2025-03-07", "whoop", "wellness",
                                 {MetricType.HRV_RMSSD_MS.value: 55.0}, confidence=0.9)
        intervals_rec = _make_record("2025-03-07", "intervals", "wellness",
                                     {MetricType.CTL.value: 45.0}, confidence=0.8)
        agg = DataAggregator([])
        merged = agg._merge([whoop_rec, intervals_rec])
        assert len(merged) == 1
        assert merged[0].get(MetricType.HRV_RMSSD_MS) == 55.0
        assert merged[0].get(MetricType.CTL) == 45.0

    def test_source_priority_tie_breaking(self):
        """When confidence is equal, source priority decides."""
        whoop_rec = _make_record("2025-03-07", "whoop", "wellness",
                                 {MetricType.HRV_RMSSD_MS.value: 55.0}, confidence=0.8)
        intervals_rec = _make_record("2025-03-07", "intervals", "wellness",
                                     {MetricType.HRV_RMSSD_MS.value: 52.0}, confidence=0.8)
        # Default priority: whoop > intervals
        agg = DataAggregator([], source_priority=["whoop", "intervals"])
        merged = agg._merge([whoop_rec, intervals_rec])
        assert len(merged) == 1
        assert merged[0].get(MetricType.HRV_RMSSD_MS) == 55.0  # whoop wins

    def test_custom_source_priority(self):
        """Custom source priority reverses the tie-break."""
        whoop_rec = _make_record("2025-03-07", "whoop", "wellness",
                                 {MetricType.HRV_RMSSD_MS.value: 55.0}, confidence=0.8)
        intervals_rec = _make_record("2025-03-07", "intervals", "wellness",
                                     {MetricType.HRV_RMSSD_MS.value: 52.0}, confidence=0.8)
        # Reversed priority: intervals > whoop
        agg = DataAggregator([], source_priority=["intervals", "whoop"])
        merged = agg._merge([whoop_rec, intervals_rec])
        assert len(merged) == 1
        assert merged[0].get(MetricType.HRV_RMSSD_MS) == 52.0  # intervals wins

    def test_merged_source_field_shows_both(self):
        """The merged record's source field should show both contributing sources."""
        whoop_rec = _make_record("2025-03-07", "whoop", "wellness",
                                 {MetricType.HRV_RMSSD_MS.value: 55.0}, confidence=0.9)
        intervals_rec = _make_record("2025-03-07", "intervals", "wellness",
                                     {MetricType.CTL.value: 45.0}, confidence=0.8)
        agg = DataAggregator([])
        merged = agg._merge([whoop_rec, intervals_rec])
        assert "whoop" in merged[0].source
        assert "intervals" in merged[0].source

    def test_different_dates_not_merged(self):
        """Records from different dates should remain separate."""
        rec1 = _make_record("2025-03-06", "whoop", "wellness",
                            {MetricType.HRV_RMSSD_MS.value: 52.0}, confidence=0.9)
        rec2 = _make_record("2025-03-07", "whoop", "wellness",
                            {MetricType.HRV_RMSSD_MS.value: 55.0}, confidence=0.9)
        agg = DataAggregator([])
        merged = agg._merge([rec1, rec2])
        assert len(merged) == 2

    def test_different_categories_not_merged(self):
        """Records from different categories on the same date should remain separate."""
        wellness = _make_record("2025-03-07", "whoop", "wellness",
                                {MetricType.HRV_RMSSD_MS.value: 55.0}, confidence=0.9)
        sleep = _make_record("2025-03-07", "whoop", "sleep",
                             {MetricType.SLEEP_DURATION_S.value: 27000}, confidence=0.9)
        agg = DataAggregator([])
        merged = agg._merge([wellness, sleep])
        assert len(merged) == 2

    def test_merged_records_sorted_by_timestamp(self):
        """Output should be sorted by timestamp."""
        rec_late = _make_record("2025-03-07", "whoop", "wellness",
                                {MetricType.HRV_RMSSD_MS.value: 55.0})
        rec_early = _make_record("2025-03-05", "whoop", "wellness",
                                 {MetricType.HRV_RMSSD_MS.value: 52.0})
        agg = DataAggregator([])
        merged = agg._merge([rec_late, rec_early])
        assert merged[0].timestamp < merged[1].timestamp

    def test_empty_records(self):
        agg = DataAggregator([])
        merged = agg._merge([])
        assert merged == []


# ===========================================================================
# DataAggregator — parallel fetch
# ===========================================================================

class TestParallelFetch:
    def test_fetch_all_wellness(self):
        """Mock providers should be called and results merged."""
        rec_whoop = _make_record("2025-03-07", "whoop", "wellness",
                                 {MetricType.HRV_RMSSD_MS.value: 55.0}, confidence=0.9)
        rec_intervals = _make_record("2025-03-07", "intervals", "wellness",
                                     {MetricType.CTL.value: 45.0}, confidence=0.8)

        p1 = _make_mock_provider("whoop", ["wellness"], wellness_data=[rec_whoop])
        p2 = _make_mock_provider("intervals", ["wellness"], wellness_data=[rec_intervals])

        agg = DataAggregator([p1, p2])
        result = asyncio.get_event_loop().run_until_complete(
            agg.fetch_all_wellness(date(2025, 3, 1), date(2025, 3, 7))
        )

        assert len(result) == 1  # merged into one record
        assert result[0].get(MetricType.HRV_RMSSD_MS) == 55.0
        assert result[0].get(MetricType.CTL) == 45.0

    def test_failing_provider_handled_gracefully(self):
        """If one provider fails, the other's data should still come through."""
        rec = _make_record("2025-03-07", "whoop", "wellness",
                           {MetricType.HRV_RMSSD_MS.value: 55.0}, confidence=0.9)

        p1 = _make_mock_provider("whoop", ["wellness"], wellness_data=[rec])
        p2 = _make_mock_provider("intervals", ["wellness"])
        p2.fetch_wellness = AsyncMock(side_effect=ConnectionError("API down"))

        agg = DataAggregator([p1, p2])
        result = asyncio.get_event_loop().run_until_complete(
            agg.fetch_all_wellness(date(2025, 3, 1), date(2025, 3, 7))
        )

        assert len(result) == 1
        assert result[0].get(MetricType.HRV_RMSSD_MS) == 55.0

    def test_fetch_all_categories(self):
        """fetch_all should return a dict with wellness, activities, sleep."""
        rec_w = _make_record("2025-03-07", "whoop", "wellness",
                             {MetricType.HRV_RMSSD_MS.value: 55.0})
        rec_a = _make_record("2025-03-07", "intervals", "activity",
                             {MetricType.DURATION_S.value: 3600})
        rec_s = _make_record("2025-03-07", "whoop", "sleep",
                             {MetricType.SLEEP_DURATION_S.value: 27000})

        p1 = _make_mock_provider("whoop", ["wellness", "sleep"],
                                 wellness_data=[rec_w], sleep_data=[rec_s])
        p2 = _make_mock_provider("intervals", ["activity"],
                                 activity_data=[rec_a])

        agg = DataAggregator([p1, p2])
        result = asyncio.get_event_loop().run_until_complete(
            agg.fetch_all(date(2025, 3, 1), date(2025, 3, 7))
        )

        assert "wellness" in result
        assert "activities" in result
        assert "sleep" in result
        assert len(result["wellness"]) == 1
        assert len(result["sleep"]) == 1


# ===========================================================================
# DataAggregator — empty provider list
# ===========================================================================

class TestEmptyProviders:
    def test_no_providers_wellness(self):
        agg = DataAggregator([])
        result = asyncio.get_event_loop().run_until_complete(
            agg.fetch_all_wellness(date(2025, 3, 1), date(2025, 3, 7))
        )
        assert result == []

    def test_no_providers_activities(self):
        agg = DataAggregator([])
        result = asyncio.get_event_loop().run_until_complete(
            agg.fetch_all_activities(date(2025, 3, 1), date(2025, 3, 7))
        )
        assert result == []

    def test_no_providers_sleep(self):
        agg = DataAggregator([])
        result = asyncio.get_event_loop().run_until_complete(
            agg.fetch_all_sleep(date(2025, 3, 1), date(2025, 3, 7))
        )
        assert result == []

    def test_no_providers_fetch_all(self):
        agg = DataAggregator([])
        result = asyncio.get_event_loop().run_until_complete(
            agg.fetch_all(date(2025, 3, 1), date(2025, 3, 7))
        )
        assert result["wellness"] == []
        assert result["activities"] == []
        assert result["sleep"] == []


# ===========================================================================
# DataAggregator — get_latest
# ===========================================================================

class TestGetLatest:
    def test_returns_most_recent_value(self):
        rec1 = _make_record("2025-03-05", "whoop", "wellness",
                            {MetricType.HRV_RMSSD_MS.value: 52.0})
        rec2 = _make_record("2025-03-07", "whoop", "wellness",
                            {MetricType.HRV_RMSSD_MS.value: 58.0})
        rec3 = _make_record("2025-03-06", "whoop", "wellness",
                            {MetricType.HRV_RMSSD_MS.value: 55.0})
        agg = DataAggregator([])
        latest = agg.get_latest([rec1, rec2, rec3], MetricType.HRV_RMSSD_MS.value)
        assert latest == 58.0

    def test_missing_metric_returns_none(self):
        rec = _make_record("2025-03-07", "whoop", "wellness", {})
        agg = DataAggregator([])
        assert agg.get_latest([rec], MetricType.HRV_RMSSD_MS.value) is None

    def test_empty_records_returns_none(self):
        agg = DataAggregator([])
        assert agg.get_latest([], MetricType.HRV_RMSSD_MS.value) is None


# ===========================================================================
# DataAggregator — source rank
# ===========================================================================

class TestSourceRank:
    def test_default_priority(self):
        agg = DataAggregator([])
        assert agg._source_rank("whoop") == 0
        assert agg._source_rank("intervals") == 1
        assert agg._source_rank("garmin") == 2
        assert agg._source_rank("strava") == 3
        assert agg._source_rank("oura") == 4

    def test_unknown_source_is_last(self):
        agg = DataAggregator([])
        assert agg._source_rank("unknown_source") == 5  # len(default list)

    def test_custom_priority(self):
        agg = DataAggregator([], source_priority=["intervals", "garmin"])
        assert agg._source_rank("intervals") == 0
        assert agg._source_rank("garmin") == 1
        assert agg._source_rank("whoop") == 2  # not in list


# ===========================================================================
# Provider status
# ===========================================================================

class TestProviderStatus:
    def test_connected_providers(self):
        p1 = _make_mock_provider("whoop", ["wellness", "sleep"])
        p2 = _make_mock_provider("intervals", ["wellness", "activity"])

        agg = DataAggregator([p1, p2])
        result = asyncio.get_event_loop().run_until_complete(agg.get_provider_status())

        assert len(result) == 2
        assert result[0]["name"] == "whoop"
        assert result[0]["connected"] is True
        assert result[1]["name"] == "intervals"

    def test_disconnected_provider(self):
        p1 = _make_mock_provider("whoop", ["wellness"])
        p1.is_connected = AsyncMock(side_effect=Exception("no auth"))

        agg = DataAggregator([p1])
        result = asyncio.get_event_loop().run_until_complete(agg.get_provider_status())

        assert len(result) == 1
        assert result[0]["connected"] is False
