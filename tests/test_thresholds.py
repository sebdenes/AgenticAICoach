"""Tests for PersonalizedThresholds module — data-driven baselines."""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.thresholds import (
    PersonalizedThresholds,
    Assessment,
    _clean,
    rolling_mean,
    rolling_std,
    linear_slope,
    percentile_rank,
)


# ===========================================================================
# Helper stats tests
# ===========================================================================

class TestClean:
    def test_filters_none(self):
        assert _clean([1.0, None, 3.0]) == [1.0, 3.0]

    def test_filters_non_numeric(self):
        assert _clean([1.0, "bad", 3.0]) == [1.0, 3.0]

    def test_filters_inf_and_nan(self):
        assert _clean([1.0, float("inf"), float("nan"), 3.0]) == [1.0, 3.0]

    def test_empty_list(self):
        assert _clean([]) == []

    def test_all_none(self):
        assert _clean([None, None]) == []

    def test_integer_conversion(self):
        result = _clean([1, 2, 3])
        assert result == [1.0, 2.0, 3.0]


class TestRollingMean:
    def test_basic(self):
        assert rolling_mean([1.0, 2.0, 3.0, 4.0, 5.0], 3) == pytest.approx(4.0)

    def test_window_larger_than_data(self):
        assert rolling_mean([2.0, 4.0], 10) == pytest.approx(3.0)

    def test_empty(self):
        assert rolling_mean([], 5) == 0.0


class TestRollingStd:
    def test_basic(self):
        vals = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        std = rolling_std(vals, 8)
        # population std
        mean = sum(vals) / len(vals)
        expected = math.sqrt(sum((x - mean) ** 2 for x in vals) / len(vals))
        assert std == pytest.approx(expected)

    def test_single_value(self):
        assert rolling_std([5.0], 5) == 0.0

    def test_two_identical(self):
        assert rolling_std([3.0, 3.0], 5) == 0.0


class TestLinearSlope:
    def test_positive_trend(self):
        # Strictly increasing
        slope = linear_slope([1.0, 2.0, 3.0, 4.0, 5.0])
        assert slope == pytest.approx(1.0)

    def test_negative_trend(self):
        slope = linear_slope([5.0, 4.0, 3.0, 2.0, 1.0])
        assert slope == pytest.approx(-1.0)

    def test_flat(self):
        slope = linear_slope([3.0, 3.0, 3.0, 3.0])
        assert slope == pytest.approx(0.0)

    def test_fewer_than_3_points(self):
        assert linear_slope([1.0, 2.0]) == 0.0
        assert linear_slope([]) == 0.0


class TestPercentileRank:
    def test_middle(self):
        history = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert percentile_rank(3.0, history) == pytest.approx(60.0)

    def test_lowest(self):
        assert percentile_rank(1.0, [1.0, 2.0, 3.0, 4.0, 5.0]) == pytest.approx(20.0)

    def test_highest(self):
        assert percentile_rank(5.0, [1.0, 2.0, 3.0, 4.0, 5.0]) == pytest.approx(100.0)

    def test_empty_history(self):
        assert percentile_rank(5.0, []) == 50.0


# ===========================================================================
# PersonalizedThresholds — normal data (14+ days)
# ===========================================================================

class TestThresholdsNormalData:
    """Test with full 14-day wellness history."""

    def test_construction(self, mock_wellness_14d, mock_activities_7d):
        pt = PersonalizedThresholds(mock_wellness_14d, mock_activities_7d)
        assert pt.hrv_baseline > 0
        assert pt.rhr_baseline > 0
        assert pt.sleep_baseline > 0

    def test_hrv_baseline_in_expected_range(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        # All HRV values are 48-65, so baseline should be in that range
        assert 48 <= pt.hrv_baseline <= 65

    def test_rhr_baseline_in_expected_range(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        assert 40 <= pt.rhr_baseline <= 48

    def test_sleep_baseline_in_expected_range(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        # Sleep: 18000-28800 seconds = 5.0-8.0 hours
        assert 5.0 <= pt.sleep_baseline <= 8.0

    def test_std_is_positive(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        assert pt.hrv_std > 0
        assert pt.rhr_std > 0
        assert pt.sleep_std > 0

    def test_hrv_bounds(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        assert pt.hrv_low < pt.hrv_baseline < pt.hrv_high


# ===========================================================================
# Assess methods
# ===========================================================================

class TestAssessHRV:
    def test_normal_hrv(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        a = pt.assess_hrv(pt.hrv_baseline)
        assert isinstance(a, Assessment)
        assert a.status == "normal"
        assert a.z_score == pytest.approx(0.0, abs=0.01)

    def test_low_hrv(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        very_low = pt.hrv_baseline - 2.5 * pt.hrv_std
        a = pt.assess_hrv(very_low)
        assert a.status == "critical"

    def test_optimal_hrv(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        high = pt.hrv_baseline + 1.0 * pt.hrv_std
        a = pt.assess_hrv(high)
        assert a.status == "optimal"

    def test_z_score_correctness(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        value = 60.0
        expected_z = (value - pt.hrv_baseline) / pt.hrv_std
        a = pt.assess_hrv(value)
        assert a.z_score == pytest.approx(expected_z)


class TestAssessRHR:
    def test_normal_rhr(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        a = pt.assess_rhr(pt.rhr_baseline)
        assert a.status in ("optimal", "normal")

    def test_elevated_rhr(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        high = pt.rhr_baseline + 2.5 * pt.rhr_std
        a = pt.assess_rhr(high)
        assert a.status == "critical"

    def test_low_rhr(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        low = pt.rhr_baseline - 2.0 * pt.rhr_std
        a = pt.assess_rhr(low)
        assert a.status == "low"


class TestAssessSleep:
    def test_normal_sleep(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        a = pt.assess_sleep_duration(pt.sleep_baseline)
        assert a.status == "normal"

    def test_poor_sleep(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        low = pt.sleep_baseline - 2.5 * pt.sleep_std
        a = pt.assess_sleep_duration(low)
        assert a.status == "critical"

    def test_great_sleep(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        high = pt.sleep_baseline + 1.0 * pt.sleep_std
        a = pt.assess_sleep_duration(high)
        assert a.status == "optimal"


class TestAssessTrainingLoad:
    def test_normal_load(self, mock_wellness_14d, mock_activities_7d):
        pt = PersonalizedThresholds(mock_wellness_14d, mock_activities_7d)
        a = pt.assess_training_load(pt.tss_daily_avg)
        assert a.status == "normal"

    def test_high_load(self, mock_wellness_14d, mock_activities_7d):
        pt = PersonalizedThresholds(mock_wellness_14d, mock_activities_7d)
        if pt.tss_daily_std > 0:
            extreme = pt.tss_daily_avg + 2.5 * pt.tss_daily_std
            a = pt.assess_training_load(extreme)
            assert a.status in ("high", "critical")

    def test_low_load(self, mock_wellness_14d, mock_activities_7d):
        pt = PersonalizedThresholds(mock_wellness_14d, mock_activities_7d)
        a = pt.assess_training_load(0.0)
        assert a.status == "low"


class TestAssessRecovery:
    def test_optimal_acwr(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        # CTL=50, ATL=50 -> ratio=1.0 -> optimal
        a = pt.assess_recovery(50.0, 50.0)
        assert a.status == "optimal"

    def test_critical_acwr(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        # CTL=30, ATL=60 -> ratio=2.0 -> critical
        a = pt.assess_recovery(30.0, 60.0)
        assert a.status == "critical"

    def test_low_acwr(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        # CTL=50, ATL=10 -> ratio=0.2 -> low
        a = pt.assess_recovery(50.0, 10.0)
        assert a.status == "low"

    def test_zero_ctl(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        a = pt.assess_recovery(0.0, 50.0)
        assert a.value == 0.0
        assert a.status == "low"


class TestAssessDispatch:
    """Test the generic assess() dispatcher."""

    def test_dispatch_hrv(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        a = pt.assess("hrv", 55.0)
        assert isinstance(a, Assessment)
        assert a.value == 55.0

    def test_dispatch_rhr(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        a = pt.assess("rhr", 43.0)
        assert isinstance(a, Assessment)

    def test_dispatch_sleep(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        a = pt.assess("sleep", 7.0)
        assert isinstance(a, Assessment)

    def test_dispatch_training_load(self, mock_wellness_14d, mock_activities_7d):
        pt = PersonalizedThresholds(mock_wellness_14d, mock_activities_7d)
        a = pt.assess("training_load", 80.0)
        assert isinstance(a, Assessment)

    def test_dispatch_tss_alias(self, mock_wellness_14d, mock_activities_7d):
        pt = PersonalizedThresholds(mock_wellness_14d, mock_activities_7d)
        a = pt.assess("tss", 80.0)
        assert isinstance(a, Assessment)

    def test_dispatch_unknown_metric(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        a = pt.assess("unknown_metric", 42.0)
        assert a.status == "normal"
        assert a.z_score == 0.0
        assert a.percentile == 50.0


# ===========================================================================
# Sparse and empty data
# ===========================================================================

class TestThresholdsSparseData:
    """Test with only 3 days of data, some None values."""

    def test_sparse_construction(self):
        sparse = [
            {"id": "2025-03-05", "hrv": 52.0, "restingHR": 44, "sleepSecs": 25200, "sleepScore": 72, "ctl": 45.0, "atl": 50.0},
            {"id": "2025-03-06", "hrv": None, "restingHR": 46, "sleepSecs": None, "sleepScore": None, "ctl": 45.5, "atl": 51.0},
            {"id": "2025-03-07", "hrv": 58.0, "restingHR": 42, "sleepSecs": 27000, "sleepScore": 80, "ctl": 46.0, "atl": 49.0},
        ]
        pt = PersonalizedThresholds(sparse)
        # HRV series should have 2 values (None filtered out)
        assert len(pt._hrv_series) == 2
        # Still produces a baseline from available data
        assert pt.hrv_baseline > 0

    def test_sparse_assess_still_works(self):
        sparse = [
            {"id": "2025-03-05", "hrv": 55.0, "restingHR": 43, "sleepSecs": 25200},
            {"id": "2025-03-06", "hrv": 53.0, "restingHR": 45, "sleepSecs": 24000},
            {"id": "2025-03-07", "hrv": 57.0, "restingHR": 41, "sleepSecs": 27000},
        ]
        pt = PersonalizedThresholds(sparse)
        a = pt.assess_hrv(55.0)
        assert isinstance(a, Assessment)
        # Trend should be "stable" with < 3 points in some cases
        assert a.trend in ("stable", "improving", "declining")


class TestThresholdsEmptyData:
    """Test with completely empty wellness history."""

    def test_empty_construction(self):
        pt = PersonalizedThresholds([])
        assert pt.hrv_baseline == 0.0
        assert pt.rhr_baseline == 0.0
        assert pt.sleep_baseline == 0.0

    def test_empty_assess_hrv(self):
        pt = PersonalizedThresholds([])
        a = pt.assess_hrv(55.0)
        # z_score should be 0.0 because std is 0
        assert a.z_score == 0.0
        assert a.baseline == 0.0

    def test_empty_get_all_baselines(self):
        pt = PersonalizedThresholds([])
        b = pt.get_all_baselines()
        assert b["hrv"]["n"] == 0
        assert b["rhr"]["n"] == 0
        assert b["sleep"]["n"] == 0


# ===========================================================================
# Trend detection
# ===========================================================================

class TestTrendDetection:
    def test_improving_trend(self):
        # Create strongly improving data
        improving = [{"id": f"2025-03-{i:02d}", "hrv": 40 + i * 3} for i in range(1, 15)]
        pt = PersonalizedThresholds(improving)
        # The last 7 values should show an improving trend
        trend, slope = pt._compute_trend(pt._hrv_series, 7)
        assert trend == "improving"
        assert slope > 0

    def test_declining_trend(self):
        declining = [{"id": f"2025-03-{i:02d}", "hrv": 70 - i * 3} for i in range(1, 15)]
        pt = PersonalizedThresholds(declining)
        trend, slope = pt._compute_trend(pt._hrv_series, 7)
        assert trend == "declining"
        assert slope < 0

    def test_stable_trend(self):
        stable = [{"id": f"2025-03-{i:02d}", "hrv": 55.0} for i in range(1, 15)]
        pt = PersonalizedThresholds(stable)
        trend, slope = pt._compute_trend(pt._hrv_series, 7)
        assert trend == "stable"

    def test_short_series_is_stable(self):
        pt = PersonalizedThresholds([{"id": "2025-03-01", "hrv": 55.0}])
        trend, slope = pt._compute_trend(pt._hrv_series, 7)
        assert trend == "stable"
        assert slope == 0.0


# ===========================================================================
# format_context and get_all_baselines
# ===========================================================================

class TestFormatContext:
    def test_format_with_sufficient_data(self, mock_wellness_14d, mock_activities_7d):
        pt = PersonalizedThresholds(mock_wellness_14d, mock_activities_7d)
        ctx = pt.format_context()
        assert "PERSONALISED BASELINES" in ctx
        assert "HRV" in ctx
        assert "RHR" in ctx
        assert "Sleep" in ctx

    def test_format_with_insufficient_data(self):
        sparse = [
            {"id": "2025-03-07", "hrv": 55.0, "restingHR": 43, "sleepSecs": 25200},
        ]
        pt = PersonalizedThresholds(sparse)
        ctx = pt.format_context()
        # Should be empty when n < 5 for all metrics
        assert ctx == ""


class TestGetAllBaselines:
    def test_returns_expected_keys(self, mock_wellness_14d, mock_activities_7d):
        pt = PersonalizedThresholds(mock_wellness_14d, mock_activities_7d)
        b = pt.get_all_baselines()
        assert "hrv" in b
        assert "rhr" in b
        assert "sleep" in b
        assert "ctl" in b
        assert "tss_daily" in b

    def test_hrv_sub_keys(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        hrv = pt.get_all_baselines()["hrv"]
        assert "baseline" in hrv
        assert "std" in hrv
        assert "low" in hrv
        assert "high" in hrv
        assert "n" in hrv
        assert hrv["n"] == 14

    def test_rhr_sub_keys(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        rhr = pt.get_all_baselines()["rhr"]
        assert "baseline" in rhr
        assert "std" in rhr
        assert "elevated" in rhr
        assert "n" in rhr

    def test_values_are_rounded(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        b = pt.get_all_baselines()
        # All baselines should be rounded to 1 decimal
        assert b["hrv"]["baseline"] == round(b["hrv"]["baseline"], 1)
        assert b["rhr"]["baseline"] == round(b["rhr"]["baseline"], 1)
