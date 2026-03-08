"""Tests for per-athlete ML models — recovery prediction and performance forecasting."""

import sys
import math
import tempfile
from pathlib import Path

import pytest

# Ensure project root is on sys.path so modules can be imported
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from modules.athlete_models import (
    RecoveryPredictor,
    PerformanceForecaster,
    ModelMetadata,
    _safe_float,
    _build_daily_tss,
)


# ---------------------------------------------------------------------------
# Check if sklearn is available
# ---------------------------------------------------------------------------

def _sklearn_available():
    try:
        import sklearn  # noqa: F401
        return True
    except ImportError:
        return False


needs_sklearn = pytest.mark.skipif(
    not _sklearn_available(),
    reason="scikit-learn not installed"
)


# ---------------------------------------------------------------------------
# Synthetic data generators
# ---------------------------------------------------------------------------

def generate_wellness_data(n_days: int = 40, start_ctl: float = 42.0) -> list[dict]:
    """Generate n_days of plausible synthetic wellness data.

    HRV: 50-70ms, RHR: 40-50bpm, Sleep: 6-8.5h, CTL: slowly rising, ATL: fluctuating.
    """
    import random
    random.seed(42)

    data = []
    ctl = start_ctl
    atl = start_ctl + 5

    for day in range(n_days):
        date = f"2025-02-{(day % 28) + 1:02d}" if day < 28 else f"2025-03-{(day - 28) + 1:02d}"
        hrv = 55.0 + random.gauss(0, 5)     # 50-70 range roughly
        rhr = 45.0 + random.gauss(0, 2.5)   # 40-50 range roughly
        sleep_hours = 7.0 + random.gauss(0, 0.7)
        sleep_secs = max(18000, int(sleep_hours * 3600))
        sleep_score = max(40, min(100, int(70 + random.gauss(0, 10))))

        # CTL slowly rises, ATL fluctuates
        ctl += random.uniform(-0.2, 0.8)
        atl = ctl + random.gauss(5, 8)

        data.append({
            "id": date,
            "hrv": round(max(35, min(80, hrv)), 1),
            "restingHR": int(max(35, min(55, rhr))),
            "sleepSecs": sleep_secs,
            "sleepScore": sleep_score,
            "ctl": round(max(10, ctl), 1),
            "atl": round(max(10, atl), 1),
        })

    return data


def generate_activity_data(n_days: int = 40) -> list[dict]:
    """Generate synthetic activity data with mix of runs, rides, rest days."""
    import random
    random.seed(42)

    activities = []
    for day in range(n_days):
        # ~5 sessions per week
        if random.random() < 0.28:  # rest day
            continue

        date = f"2025-02-{(day % 28) + 1:02d}" if day < 28 else f"2025-03-{(day - 28) + 1:02d}"
        activity_type = random.choice(["Run", "Run", "Ride", "WeightTraining"])
        tss = random.uniform(30, 120)

        activities.append({
            "type": activity_type,
            "start_date_local": f"{date}T07:00:00",
            "name": f"{activity_type} session",
            "moving_time": int(random.uniform(1800, 7200)),
            "distance": int(random.uniform(5000, 50000)),
            "icu_training_load": round(tss, 1),
            "average_heartrate": int(random.uniform(120, 165)),
        })

    return activities


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def wellness_40d():
    """40 days of synthetic wellness data."""
    return generate_wellness_data(40)


@pytest.fixture
def wellness_15d():
    """15 days of synthetic wellness data (border case)."""
    return generate_wellness_data(15)


@pytest.fixture
def wellness_5d():
    """5 days of data — insufficient for training."""
    return generate_wellness_data(5)


@pytest.fixture
def activities_40d():
    """40 days of synthetic activity data."""
    return generate_activity_data(40)


@pytest.fixture
def temp_model_dir():
    """Temporary directory for model persistence tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


# ===========================================================================
# ModelMetadata
# ===========================================================================

class TestModelMetadata:
    def test_serialization_roundtrip(self):
        meta = ModelMetadata(
            model_type="recovery_predictor",
            trained_at="2025-03-07T10:00:00",
            training_samples=50,
            features=["hrv", "rhr"],
            score=0.65,
            version=2,
        )
        d = meta.to_dict()
        restored = ModelMetadata.from_dict(d)
        assert restored.model_type == "recovery_predictor"
        assert restored.training_samples == 50
        assert restored.score == 0.65
        assert restored.version == 2
        assert restored.features == ["hrv", "rhr"]


# ===========================================================================
# Helper function tests
# ===========================================================================

class TestSafeFloat:
    def test_none(self):
        assert _safe_float(None) == 0.0

    def test_valid_int(self):
        assert _safe_float(42) == 42.0

    def test_valid_float(self):
        assert _safe_float(3.14) == 3.14

    def test_string(self):
        assert _safe_float("not_a_number") == 0.0

    def test_inf(self):
        assert _safe_float(float("inf")) == 0.0

    def test_nan(self):
        assert _safe_float(float("nan")) == 0.0


class TestBuildDailyTss:
    def test_empty_activities(self):
        assert _build_daily_tss(None) == {}
        assert _build_daily_tss([]) == {}

    def test_aggregates_by_date(self):
        activities = [
            {"type": "Run", "start_date_local": "2025-03-01T07:00:00", "icu_training_load": 60},
            {"type": "Run", "start_date_local": "2025-03-01T17:00:00", "icu_training_load": 40},
            {"type": "Ride", "start_date_local": "2025-03-02T08:00:00", "icu_training_load": 80},
        ]
        result = _build_daily_tss(activities)
        assert result["2025-03-01"] == pytest.approx(100.0)
        assert result["2025-03-02"] == pytest.approx(80.0)

    def test_skips_no_type(self):
        activities = [{"start_date_local": "2025-03-01T07:00:00", "icu_training_load": 60}]
        result = _build_daily_tss(activities)
        assert result == {}


# ===========================================================================
# RecoveryPredictor
# ===========================================================================

class TestRecoveryPredictorFeatures:
    """Test feature extraction."""

    def test_feature_vector_length(self, wellness_40d, activities_40d):
        rp = RecoveryPredictor()
        features = rp._build_features(
            wellness_40d[-1], wellness_40d, activities_40d
        )
        assert len(features) == len(RecoveryPredictor.FEATURES)

    def test_feature_values_are_floats(self, wellness_40d, activities_40d):
        rp = RecoveryPredictor()
        features = rp._build_features(
            wellness_40d[-1], wellness_40d, activities_40d
        )
        for f in features:
            assert isinstance(f, float)
            assert math.isfinite(f)

    def test_handles_missing_fields(self):
        """Feature extraction handles entries with missing/None fields."""
        rp = RecoveryPredictor()
        entry = {"id": "2025-03-01"}  # Almost all fields missing
        features = rp._build_features(entry, [entry])
        assert len(features) == len(RecoveryPredictor.FEATURES)
        # All should be 0.0 for missing data
        for f in features:
            assert isinstance(f, float)

    def test_handles_none_values_in_entry(self):
        rp = RecoveryPredictor()
        entry = {
            "id": "2025-03-01",
            "hrv": None,
            "restingHR": None,
            "sleepSecs": None,
            "sleepScore": None,
            "ctl": None,
            "atl": None,
        }
        features = rp._build_features(entry, [entry])
        assert len(features) == len(RecoveryPredictor.FEATURES)
        assert all(f == 0.0 for f in features)


class TestRecoveryTarget:
    """Test recovery score computation for training labels."""

    def test_recovery_score_range(self, wellness_40d):
        rp = RecoveryPredictor()
        for entry in wellness_40d:
            score = rp._compute_recovery_target(entry, wellness_40d)
            assert 0.0 <= score <= 100.0

    def test_good_values_high_score(self):
        """Good HRV, low RHR, good sleep, positive TSB -> high score."""
        rp = RecoveryPredictor()
        # Create history where this entry is the best
        history = [
            {"id": f"day{i}", "hrv": 50 + i, "restingHR": 50 - i,
             "sleepSecs": 25200, "ctl": 45, "atl": 50}
            for i in range(10)
        ]
        best_entry = {
            "id": "best",
            "hrv": 70,      # highest
            "restingHR": 38, # lowest
            "sleepSecs": 30600, # 8.5h
            "ctl": 50,
            "atl": 35,      # positive TSB
        }
        score = rp._compute_recovery_target(best_entry, history + [best_entry])
        assert score > 60

    def test_bad_values_low_score(self):
        """Low HRV, high RHR, poor sleep, negative TSB -> low score."""
        rp = RecoveryPredictor()
        history = [
            {"id": f"day{i}", "hrv": 50 + i, "restingHR": 40 + i,
             "sleepSecs": 25200, "ctl": 45, "atl": 50}
            for i in range(10)
        ]
        worst_entry = {
            "id": "worst",
            "hrv": 40,      # lowest
            "restingHR": 55, # highest
            "sleepSecs": 14400, # 4h
            "ctl": 30,
            "atl": 65,      # deep negative TSB
        }
        score = rp._compute_recovery_target(worst_entry, history + [worst_entry])
        assert score < 50


class TestRecoveryPredictorHeuristic:
    """Test heuristic fallback prediction (no sklearn needed)."""

    def test_predict_returns_dict(self, wellness_40d):
        rp = RecoveryPredictor()
        # Force heuristic by not training
        result = rp.predict(
            wellness_40d[-1], wellness_history=wellness_40d
        )
        assert "predicted_score" in result
        assert "confidence" in result
        assert "feature_importances" in result
        assert "model_type" in result
        assert result["model_type"] == "heuristic"

    def test_predicted_score_range(self, wellness_40d):
        rp = RecoveryPredictor()
        result = rp.predict(
            wellness_40d[-1], wellness_history=wellness_40d
        )
        assert 0 <= result["predicted_score"] <= 100

    def test_heuristic_with_empty_history(self):
        rp = RecoveryPredictor()
        entry = {"hrv": 55, "sleepSecs": 27000, "ctl": 45, "atl": 50}
        result = rp.predict(entry)
        assert result["model_type"] == "heuristic"
        assert 0 <= result["predicted_score"] <= 100

    def test_heuristic_with_none_values(self):
        rp = RecoveryPredictor()
        entry = {"id": "2025-03-01"}
        result = rp.predict(entry)
        assert result["model_type"] == "heuristic"
        assert isinstance(result["predicted_score"], float)


class TestRecoveryPredictorInsufficientData:
    """Test behavior with insufficient data."""

    def test_train_fails_with_too_few_samples(self, wellness_5d):
        rp = RecoveryPredictor()
        with pytest.raises(ValueError, match="at least 10"):
            rp.train(wellness_5d)


@needs_sklearn
class TestRecoveryPredictorML:
    """Test ML training and prediction (requires sklearn)."""

    def test_train_with_sufficient_data(self, wellness_40d, activities_40d, temp_model_dir):
        rp = RecoveryPredictor(model_dir=temp_model_dir)
        meta = rp.train(wellness_40d, activities_40d)
        assert isinstance(meta, ModelMetadata)
        assert meta.training_samples > 0
        assert meta.model_type in ("gradient_boosting", "ridge")
        # R^2 should be a real number (may be negative if model is poor)
        assert isinstance(meta.score, float)

    def test_train_with_moderate_data(self, wellness_15d, temp_model_dir):
        """10-29 samples should use Ridge regression."""
        rp = RecoveryPredictor(model_dir=temp_model_dir)
        meta = rp.train(wellness_15d)
        assert meta.model_type == "ridge"

    def test_predict_after_training(self, wellness_40d, activities_40d, temp_model_dir):
        rp = RecoveryPredictor(model_dir=temp_model_dir)
        rp.train(wellness_40d, activities_40d)
        result = rp.predict(
            wellness_40d[-1],
            wellness_history=wellness_40d,
            recent_activities=activities_40d,
        )
        assert result["model_type"] in ("gradient_boosting", "ridge")
        assert 0 <= result["predicted_score"] <= 100
        assert result["confidence"] in ("high", "medium", "low")
        assert isinstance(result["feature_importances"], dict)

    def test_save_load_roundtrip(self, wellness_40d, activities_40d, temp_model_dir):
        """Train, save, load in new instance, predict should work."""
        rp1 = RecoveryPredictor(model_dir=temp_model_dir)
        rp1.train(wellness_40d, activities_40d)
        pred1 = rp1.predict(
            wellness_40d[-1],
            wellness_history=wellness_40d,
        )

        rp2 = RecoveryPredictor(model_dir=temp_model_dir)
        loaded = rp2.load()
        assert loaded is True

        pred2 = rp2.predict(
            wellness_40d[-1],
            wellness_history=wellness_40d,
        )
        # Predictions should be identical
        assert pred2["predicted_score"] == pytest.approx(pred1["predicted_score"], abs=0.1)

    def test_load_nonexistent_returns_false(self, temp_model_dir):
        rp = RecoveryPredictor(model_dir=temp_model_dir)
        assert rp.load() is False


# ===========================================================================
# PerformanceForecaster
# ===========================================================================

class TestPerformanceForecasterFeatures:
    """Test feature extraction."""

    def test_feature_vector_length(self, wellness_40d, activities_40d):
        pf = PerformanceForecaster()
        features = pf._build_features(wellness_40d, activities_40d)
        assert len(features) == len(PerformanceForecaster.FEATURES)

    def test_feature_values_are_floats(self, wellness_40d, activities_40d):
        pf = PerformanceForecaster()
        features = pf._build_features(wellness_40d, activities_40d)
        for f in features:
            assert isinstance(f, float)
            assert math.isfinite(f)

    def test_handles_empty_history(self):
        pf = PerformanceForecaster()
        entry = {"ctl": 45, "atl": 50, "hrv": 55, "sleepSecs": 27000}
        features = pf._build_features([entry])
        assert len(features) == len(PerformanceForecaster.FEATURES)


class TestPerformanceForecasterExtrapolation:
    """Test extrapolation fallback (no sklearn needed)."""

    def test_forecast_returns_dict(self, wellness_40d):
        pf = PerformanceForecaster()
        result = pf.forecast(wellness_40d[-1], wellness_history=wellness_40d)
        assert "horizon_days" in result
        assert "predicted_ctl" in result
        assert "predicted_atl" in result
        assert "predicted_tsb" in result
        assert "confidence" in result
        assert "trend" in result
        assert "model_type" in result

    def test_forecast_default_horizon(self, wellness_40d):
        pf = PerformanceForecaster()
        result = pf.forecast(wellness_40d[-1], wellness_history=wellness_40d)
        assert result["horizon_days"] == 14
        assert result["model_type"] == "extrapolation"

    def test_forecast_custom_horizon(self, wellness_40d):
        pf = PerformanceForecaster()
        result = pf.forecast(
            wellness_40d[-1], wellness_history=wellness_40d, horizon_days=7
        )
        assert result["horizon_days"] == 7

    def test_forecast_positive_ctl(self, wellness_40d):
        pf = PerformanceForecaster()
        result = pf.forecast(wellness_40d[-1], wellness_history=wellness_40d)
        assert result["predicted_ctl"] >= 0

    def test_trend_detection(self):
        """Strongly rising CTL should produce 'improving' trend."""
        pf = PerformanceForecaster()
        rising_data = [
            {"id": f"day{i}", "hrv": 55, "restingHR": 43, "sleepSecs": 27000,
             "ctl": 30 + i * 2, "atl": 40 + i}
            for i in range(20)
        ]
        result = pf.forecast(rising_data[-1], wellness_history=rising_data)
        assert result["trend"] == "improving"

    def test_trend_declining(self):
        """Strongly declining CTL should produce 'declining' trend."""
        pf = PerformanceForecaster()
        declining_data = [
            {"id": f"day{i}", "hrv": 55, "restingHR": 43, "sleepSecs": 27000,
             "ctl": 60 - i * 2, "atl": 50 - i}
            for i in range(20)
        ]
        result = pf.forecast(declining_data[-1], wellness_history=declining_data)
        assert result["trend"] == "declining"


@needs_sklearn
class TestPerformanceForecasterML:
    """Test ML training and forecasting (requires sklearn)."""

    def test_train_with_sufficient_data(self, wellness_40d, activities_40d, temp_model_dir):
        pf = PerformanceForecaster(model_dir=temp_model_dir)
        meta = pf.train(wellness_40d, activities_40d)
        assert isinstance(meta, ModelMetadata)
        assert meta.training_samples > 0

    def test_forecast_after_training(self, wellness_40d, activities_40d, temp_model_dir):
        pf = PerformanceForecaster(model_dir=temp_model_dir)
        pf.train(wellness_40d, activities_40d)
        result = pf.forecast(
            wellness_40d[-1],
            wellness_history=wellness_40d,
            recent_activities=activities_40d,
        )
        assert result["model_type"] in ("gradient_boosting", "ridge")
        assert result["predicted_ctl"] >= 0
        assert result["trend"] in ("improving", "stable", "declining")

    def test_save_load_roundtrip(self, wellness_40d, activities_40d, temp_model_dir):
        pf1 = PerformanceForecaster(model_dir=temp_model_dir)
        pf1.train(wellness_40d, activities_40d)

        pf2 = PerformanceForecaster(model_dir=temp_model_dir)
        loaded = pf2.load()
        assert loaded is True

    def test_train_insufficient_data_raises(self, wellness_5d, temp_model_dir):
        pf = PerformanceForecaster(model_dir=temp_model_dir)
        with pytest.raises(ValueError):
            pf.train(wellness_5d)
