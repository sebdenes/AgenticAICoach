"""Per-athlete ML models for recovery prediction and performance forecasting."""

from __future__ import annotations

import logging
import json
import math
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime

from modules.thresholds import rolling_mean, rolling_std, linear_slope, percentile_rank

log = logging.getLogger("coach.athlete_models")

# Default model directory
DEFAULT_MODEL_DIR = str(Path(__file__).parent.parent / "models")

# Time constants for CTL/ATL exponential decay
CTL_TAU = 42  # days
ATL_TAU = 7   # days


@dataclass
class ModelMetadata:
    """Metadata about a trained model."""

    model_type: str  # "recovery_predictor", "performance_forecaster"
    trained_at: str = ""  # ISO timestamp
    training_samples: int = 0
    features: list[str] = field(default_factory=list)
    score: float = 0.0  # R^2 or similar
    version: int = 1

    def to_dict(self) -> dict:
        """Serialize to dict for JSON storage."""
        return {
            "model_type": self.model_type,
            "trained_at": self.trained_at,
            "training_samples": self.training_samples,
            "features": self.features,
            "score": self.score,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ModelMetadata:
        """Deserialize from dict."""
        return cls(
            model_type=d.get("model_type", ""),
            trained_at=d.get("trained_at", ""),
            training_samples=d.get("training_samples", 0),
            features=d.get("features", []),
            score=d.get("score", 0.0),
            version=d.get("version", 1),
        )


# ---------------------------------------------------------------------------
# Recovery Predictor
# ---------------------------------------------------------------------------


class RecoveryPredictor:
    """Predict next-day recovery score from current wellness + training data.

    Uses scikit-learn GradientBoostingRegressor trained on athlete's own data.
    Falls back to Ridge regression if <30 training samples.
    Falls back to heuristic if scikit-learn not installed.
    """

    FEATURES = [
        "hrv",
        "rhr",
        "sleep_hours",
        "sleep_score",
        "ctl",
        "atl",
        "tsb",
        "daily_tss",
        "tss_3day_avg",
        "hrv_7d_trend",
        "rhr_7d_trend",
    ]

    def __init__(self, model_dir: str = None):
        self.model_dir = Path(model_dir or DEFAULT_MODEL_DIR)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self._model = None
        self._scaler = None
        self._metadata: ModelMetadata | None = None
        self._sklearn_available = False
        try:
            import sklearn  # noqa: F401

            self._sklearn_available = True
        except ImportError:
            log.warning("scikit-learn not installed — using heuristic fallback")

    def train(
        self,
        wellness_history: list[dict],
        activity_history: list[dict] | None = None,
    ) -> ModelMetadata:
        """Train recovery prediction model on athlete's historical data.

        Process:
        1. Build feature matrix from wellness + activity data
        2. Compute recovery scores as targets using a simplified recovery formula
        3. Offset targets by 1 day (predict tomorrow from today)
        4. Train/test split (80/20 temporal -- not random!)
        5. Choose model: GradientBoosting if >=30 samples, Ridge if >=10, fail if <10
        6. Evaluate R^2 on test set
        7. Save model + scaler to models/ via joblib

        Returns ModelMetadata with score and feature list.
        """
        if len(wellness_history) < 10:
            raise ValueError(
                f"Need at least 10 days of data to train, got {len(wellness_history)}"
            )

        # Build daily TSS lookup from activities
        daily_tss = _build_daily_tss(activity_history)

        # Build feature matrix and targets
        X = []
        y = []
        for i in range(len(wellness_history) - 1):
            entry = wellness_history[i]
            features = self._build_features(
                entry, wellness_history[: i + 1], activity_history, daily_tss
            )
            target = self._compute_recovery_target(
                wellness_history[i + 1], wellness_history[: i + 2]
            )
            X.append(features)
            y.append(target)

        n_samples = len(X)
        if n_samples < 10:
            raise ValueError(
                f"Only {n_samples} usable samples after feature extraction (need >= 10)"
            )

        if not self._sklearn_available:
            # Store metadata without actual model
            self._metadata = ModelMetadata(
                model_type="heuristic",
                trained_at=datetime.now().isoformat(),
                training_samples=n_samples,
                features=list(self.FEATURES),
                score=0.0,
                version=1,
            )
            return self._metadata

        # Temporal train/test split (80/20)
        from sklearn.preprocessing import StandardScaler

        split_idx = int(n_samples * 0.8)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]

        # Scale features
        self._scaler = StandardScaler()
        X_train_scaled = self._scaler.fit_transform(X_train)
        X_test_scaled = self._scaler.transform(X_test)

        # Choose model based on sample count
        if n_samples >= 30:
            from sklearn.ensemble import GradientBoostingRegressor

            self._model = GradientBoostingRegressor(
                n_estimators=100,
                max_depth=3,
                learning_rate=0.1,
                subsample=0.8,
                random_state=42,
            )
            model_type = "gradient_boosting"
        else:
            from sklearn.linear_model import Ridge

            self._model = Ridge(alpha=1.0)
            model_type = "ridge"

        self._model.fit(X_train_scaled, y_train)

        # Evaluate
        score = self._model.score(X_test_scaled, y_test)

        self._metadata = ModelMetadata(
            model_type=model_type,
            trained_at=datetime.now().isoformat(),
            training_samples=n_samples,
            features=list(self.FEATURES),
            score=round(score, 4),
            version=1,
        )

        self.save()
        log.info(
            "Recovery predictor trained: type=%s, samples=%d, R^2=%.4f",
            model_type,
            n_samples,
            score,
        )
        return self._metadata

    def predict(
        self,
        current_wellness: dict,
        wellness_history: list[dict] | None = None,
        recent_activities: list[dict] | None = None,
    ) -> dict:
        """Predict tomorrow's recovery score.

        Returns:
        {
            "predicted_score": float,  # 0-100
            "confidence": str,         # "high", "medium", "low"
            "feature_importances": dict,  # {feature: importance}
            "model_type": str,         # "gradient_boosting", "ridge", "heuristic"
        }

        Falls back to heuristic if no trained model:
        score = 50 + (hrv_pct - 50)*0.3 + (sleep_pct - 50)*0.3 + tsb*0.5
        """
        history = wellness_history or []
        daily_tss = _build_daily_tss(recent_activities)

        # Use heuristic if no trained model or sklearn unavailable
        if self._model is None or not self._sklearn_available:
            return self._heuristic_predict(current_wellness, history)

        # Build features for current day
        features = self._build_features(
            current_wellness, history, recent_activities, daily_tss
        )

        try:
            scaled = self._scaler.transform([features])
            raw_score = self._model.predict(scaled)[0]
            predicted = max(0.0, min(100.0, raw_score))
        except Exception as exc:
            log.warning("ML prediction failed, falling back to heuristic: %s", exc)
            return self._heuristic_predict(current_wellness, history)

        # Feature importances (if available)
        importances = {}
        if hasattr(self._model, "feature_importances_"):
            for fname, imp in zip(self.FEATURES, self._model.feature_importances_):
                importances[fname] = round(float(imp), 4)
        elif hasattr(self._model, "coef_"):
            for fname, coef in zip(self.FEATURES, self._model.coef_):
                importances[fname] = round(abs(float(coef)), 4)

        # Confidence based on R^2
        r2 = self._metadata.score if self._metadata else 0.0
        if r2 > 0.6:
            confidence = "high"
        elif r2 > 0.3:
            confidence = "medium"
        else:
            confidence = "low"

        return {
            "predicted_score": round(predicted, 1),
            "confidence": confidence,
            "feature_importances": importances,
            "model_type": self._metadata.model_type if self._metadata else "unknown",
        }

    def _heuristic_predict(
        self, current_wellness: dict, history: list[dict]
    ) -> dict:
        """Heuristic fallback prediction when no ML model is available."""
        hrv = _safe_float(current_wellness.get("hrv"))
        sleep_secs = _safe_float(
            current_wellness.get("sleepSecs")
            or current_wellness.get("sleep_seconds")
        )
        sleep_hours = sleep_secs / 3600.0 if sleep_secs > 0 else 7.0
        ctl = _safe_float(current_wellness.get("ctl"))
        atl = _safe_float(current_wellness.get("atl"))
        tsb = ctl - atl

        # HRV percentile from history
        hrv_values = [_safe_float(w.get("hrv")) for w in history if w.get("hrv") is not None]
        if hrv_values and hrv > 0:
            hrv_pct = percentile_rank(hrv, hrv_values)
        else:
            hrv_pct = 50.0

        # Sleep percentage (target: 7.5h = 100th pct approx)
        sleep_pct = min(100.0, (sleep_hours / 7.5) * 100.0)

        # Heuristic score
        score = 50.0 + (hrv_pct - 50.0) * 0.3 + (sleep_pct - 50.0) * 0.3 + tsb * 0.5
        score = max(0.0, min(100.0, score))

        return {
            "predicted_score": round(score, 1),
            "confidence": "low",
            "feature_importances": {
                "hrv_percentile": 0.3,
                "sleep_quality": 0.3,
                "tsb": 0.4,
            },
            "model_type": "heuristic",
        }

    def load(self) -> bool:
        """Load saved model from disk. Returns True if successful."""
        model_path = self.model_dir / "recovery_predictor.joblib"
        scaler_path = self.model_dir / "recovery_predictor_scaler.joblib"
        meta_path = self.model_dir / "recovery_predictor_meta.json"

        if not model_path.exists():
            log.debug("No saved recovery predictor model found at %s", model_path)
            return False

        try:
            import joblib

            self._model = joblib.load(str(model_path))
            if scaler_path.exists():
                self._scaler = joblib.load(str(scaler_path))
            if meta_path.exists():
                self._metadata = ModelMetadata.from_dict(
                    json.loads(meta_path.read_text())
                )
            log.info("Loaded recovery predictor model from %s", model_path)
            return True
        except ImportError:
            log.warning("joblib not installed — cannot load saved model")
            return False
        except Exception as exc:
            log.warning("Failed to load recovery predictor: %s", exc)
            return False

    def save(self):
        """Persist model to disk using joblib."""
        if self._model is None:
            return

        try:
            import joblib
        except ImportError:
            log.warning("joblib not installed — cannot save model")
            return

        model_path = self.model_dir / "recovery_predictor.joblib"
        scaler_path = self.model_dir / "recovery_predictor_scaler.joblib"
        meta_path = self.model_dir / "recovery_predictor_meta.json"

        try:
            joblib.dump(self._model, str(model_path))
            if self._scaler is not None:
                joblib.dump(self._scaler, str(scaler_path))
            if self._metadata is not None:
                meta_path.write_text(json.dumps(self._metadata.to_dict(), indent=2))
            log.info("Saved recovery predictor to %s", model_path)
        except Exception as exc:
            log.warning("Failed to save recovery predictor: %s", exc)

    def _build_features(
        self,
        wellness_entry: dict,
        wellness_history: list[dict],
        activities: list[dict] | None = None,
        daily_tss: dict | None = None,
    ) -> list[float]:
        """Extract feature vector from a single day's data.

        Features:
        - hrv: raw HRV value
        - rhr: raw resting HR
        - sleep_hours: sleepSecs / 3600
        - sleep_score: raw sleep score
        - ctl, atl: from wellness entry
        - tsb: ctl - atl
        - daily_tss: sum of activity TSS for that day
        - tss_3day_avg: avg TSS over last 3 days
        - hrv_7d_trend: linear_slope of last 7 HRV values
        - rhr_7d_trend: linear_slope of last 7 RHR values

        Returns feature vector as list[float]. Uses 0.0 for missing values.
        """
        hrv = _safe_float(wellness_entry.get("hrv"))
        rhr = _safe_float(
            wellness_entry.get("restingHR") or wellness_entry.get("rhr")
        )
        sleep_secs = _safe_float(
            wellness_entry.get("sleepSecs") or wellness_entry.get("sleep_seconds")
        )
        sleep_hours = sleep_secs / 3600.0 if sleep_secs > 0 else 0.0
        sleep_score = _safe_float(wellness_entry.get("sleepScore"))
        ctl = _safe_float(wellness_entry.get("ctl"))
        atl = _safe_float(wellness_entry.get("atl"))
        tsb = ctl - atl

        # Daily TSS for this entry's date
        entry_date = str(wellness_entry.get("id", ""))[:10]
        if daily_tss is None:
            daily_tss = _build_daily_tss(activities)
        day_tss = daily_tss.get(entry_date, 0.0)

        # TSS 3-day average
        tss_3day_values = []
        for w in wellness_history[-3:]:
            d = str(w.get("id", ""))[:10]
            tss_3day_values.append(daily_tss.get(d, 0.0))
        tss_3day_avg = rolling_mean(tss_3day_values, 3) if tss_3day_values else 0.0

        # HRV 7-day trend
        hrv_values = [
            _safe_float(w.get("hrv"))
            for w in wellness_history[-7:]
            if w.get("hrv") is not None
        ]
        hrv_7d_trend = linear_slope(hrv_values) if len(hrv_values) >= 3 else 0.0

        # RHR 7-day trend
        rhr_values = [
            _safe_float(w.get("restingHR") or w.get("rhr"))
            for w in wellness_history[-7:]
            if (w.get("restingHR") or w.get("rhr")) is not None
        ]
        rhr_7d_trend = linear_slope(rhr_values) if len(rhr_values) >= 3 else 0.0

        return [
            hrv,
            rhr,
            sleep_hours,
            sleep_score,
            ctl,
            atl,
            tsb,
            day_tss,
            tss_3day_avg,
            hrv_7d_trend,
            rhr_7d_trend,
        ]

    def _compute_recovery_target(
        self, wellness_entry: dict, wellness_history: list[dict]
    ) -> float:
        """Compute a recovery score (0-100) for training labels.

        Simplified formula:
        - HRV component (30%): percentile rank in history
        - RHR component (20%): inverse percentile (lower = better)
        - Sleep component (30%): hours vs 7.5h target
        - TSB component (20%): clamped to -30..+30, mapped to 0..100
        """
        # HRV component (30%)
        hrv = _safe_float(wellness_entry.get("hrv"))
        hrv_values = [
            _safe_float(w.get("hrv"))
            for w in wellness_history
            if w.get("hrv") is not None
        ]
        if hrv_values and hrv > 0:
            hrv_pct = percentile_rank(hrv, hrv_values)
        else:
            hrv_pct = 50.0

        # RHR component (20%): lower is better -> inverse percentile
        rhr = _safe_float(
            wellness_entry.get("restingHR") or wellness_entry.get("rhr")
        )
        rhr_values = [
            _safe_float(w.get("restingHR") or w.get("rhr"))
            for w in wellness_history
            if (w.get("restingHR") or w.get("rhr")) is not None
        ]
        if rhr_values and rhr > 0:
            rhr_pct = 100.0 - percentile_rank(rhr, rhr_values)
        else:
            rhr_pct = 50.0

        # Sleep component (30%): hours vs 7.5h target
        sleep_secs = _safe_float(
            wellness_entry.get("sleepSecs") or wellness_entry.get("sleep_seconds")
        )
        sleep_hours = sleep_secs / 3600.0 if sleep_secs > 0 else 7.0
        sleep_score = min(100.0, (sleep_hours / 7.5) * 100.0)

        # TSB component (20%): clamp to -30..+30, map to 0..100
        ctl = _safe_float(wellness_entry.get("ctl"))
        atl = _safe_float(wellness_entry.get("atl"))
        tsb = ctl - atl
        tsb_clamped = max(-30.0, min(30.0, tsb))
        tsb_score = ((tsb_clamped + 30.0) / 60.0) * 100.0

        # Weighted combination
        recovery = (
            hrv_pct * 0.3
            + rhr_pct * 0.2
            + sleep_score * 0.3
            + tsb_score * 0.2
        )
        return max(0.0, min(100.0, recovery))


# ---------------------------------------------------------------------------
# Performance Forecaster
# ---------------------------------------------------------------------------


class PerformanceForecaster:
    """Forecast CTL and race readiness N days into the future.

    Uses scikit-learn GradientBoostingRegressor.
    Falls back to exponential extrapolation if sklearn unavailable.
    """

    FEATURES = [
        "current_ctl",
        "current_atl",
        "ctl_trend_7d",
        "atl_trend_7d",
        "avg_weekly_tss",
        "training_consistency",
        "avg_sleep_7d",
        "avg_hrv_7d",
    ]

    def __init__(self, model_dir: str = None):
        self.model_dir = Path(model_dir or DEFAULT_MODEL_DIR)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self._model = None
        self._scaler = None
        self._metadata: ModelMetadata | None = None
        self._sklearn_available = False
        try:
            import sklearn  # noqa: F401

            self._sklearn_available = True
        except ImportError:
            log.warning("scikit-learn not installed — using extrapolation fallback")

    def train(
        self,
        wellness_history: list[dict],
        activity_history: list[dict] | None = None,
        horizon_days: int = 14,
    ) -> ModelMetadata:
        """Train performance forecasting model.

        Target: CTL value N days in the future (default N=14).
        Uses temporal train/test split.
        """
        if len(wellness_history) < horizon_days + 10:
            raise ValueError(
                f"Need at least {horizon_days + 10} days of data, "
                f"got {len(wellness_history)}"
            )

        daily_tss = _build_daily_tss(activity_history)

        # Build feature matrix and targets
        X = []
        y = []
        for i in range(len(wellness_history) - horizon_days):
            features = self._build_features(
                wellness_history[: i + 1], activity_history, daily_tss
            )
            # Target: CTL at i + horizon_days
            future_ctl = _safe_float(wellness_history[i + horizon_days].get("ctl"))
            X.append(features)
            y.append(future_ctl)

        n_samples = len(X)
        if n_samples < 10:
            raise ValueError(
                f"Only {n_samples} usable samples (need >= 10)"
            )

        if not self._sklearn_available:
            self._metadata = ModelMetadata(
                model_type="extrapolation",
                trained_at=datetime.now().isoformat(),
                training_samples=n_samples,
                features=list(self.FEATURES),
                score=0.0,
                version=1,
            )
            return self._metadata

        from sklearn.preprocessing import StandardScaler

        split_idx = int(n_samples * 0.8)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]

        self._scaler = StandardScaler()
        X_train_scaled = self._scaler.fit_transform(X_train)
        X_test_scaled = self._scaler.transform(X_test)

        if n_samples >= 30:
            from sklearn.ensemble import GradientBoostingRegressor

            self._model = GradientBoostingRegressor(
                n_estimators=80,
                max_depth=3,
                learning_rate=0.1,
                subsample=0.8,
                random_state=42,
            )
            model_type = "gradient_boosting"
        else:
            from sklearn.linear_model import Ridge

            self._model = Ridge(alpha=1.0)
            model_type = "ridge"

        self._model.fit(X_train_scaled, y_train)
        score = self._model.score(X_test_scaled, y_test)

        self._metadata = ModelMetadata(
            model_type=model_type,
            trained_at=datetime.now().isoformat(),
            training_samples=n_samples,
            features=list(self.FEATURES),
            score=round(score, 4),
            version=1,
        )

        self.save()
        log.info(
            "Performance forecaster trained: type=%s, samples=%d, R^2=%.4f",
            model_type,
            n_samples,
            score,
        )
        return self._metadata

    def forecast(
        self,
        current_wellness: dict,
        wellness_history: list[dict] | None = None,
        recent_activities: list[dict] | None = None,
        horizon_days: int = 14,
    ) -> dict:
        """Forecast CTL and derived metrics.

        Returns:
        {
            "horizon_days": int,
            "predicted_ctl": float,
            "predicted_atl": float,
            "predicted_tsb": float,
            "confidence": str,
            "trend": str,   # "improving", "stable", "declining"
            "model_type": str,
        }

        Fallback (no ML): simple exponential projection
        CTL_future = CTL + trend_slope * horizon_days (clamped)
        """
        history = wellness_history or []
        # Ensure current_wellness is in history for feature building
        full_history = history if history else [current_wellness]

        if self._model is not None and self._sklearn_available and self._scaler is not None:
            daily_tss = _build_daily_tss(recent_activities)
            features = self._build_features(full_history, recent_activities, daily_tss)
            try:
                scaled = self._scaler.transform([features])
                predicted_ctl = float(self._model.predict(scaled)[0])
            except Exception as exc:
                log.warning("ML forecast failed, falling back: %s", exc)
                return self._extrapolation_forecast(
                    current_wellness, history, horizon_days
                )

            current_ctl = _safe_float(current_wellness.get("ctl"))
            current_atl = _safe_float(current_wellness.get("atl"))

            # ATL decays faster than CTL: exponential decay approximation
            predicted_atl = current_atl * math.exp(-horizon_days / ATL_TAU)

            predicted_tsb = predicted_ctl - predicted_atl

            # Determine trend
            if predicted_ctl > current_ctl + 2:
                trend = "improving"
            elif predicted_ctl < current_ctl - 2:
                trend = "declining"
            else:
                trend = "stable"

            r2 = self._metadata.score if self._metadata else 0.0
            confidence = "high" if r2 > 0.6 else ("medium" if r2 > 0.3 else "low")

            return {
                "horizon_days": horizon_days,
                "predicted_ctl": round(predicted_ctl, 1),
                "predicted_atl": round(predicted_atl, 1),
                "predicted_tsb": round(predicted_tsb, 1),
                "confidence": confidence,
                "trend": trend,
                "model_type": self._metadata.model_type if self._metadata else "unknown",
            }

        return self._extrapolation_forecast(current_wellness, history, horizon_days)

    def _extrapolation_forecast(
        self,
        current_wellness: dict,
        history: list[dict],
        horizon_days: int,
    ) -> dict:
        """Exponential extrapolation fallback for CTL forecasting."""
        current_ctl = _safe_float(current_wellness.get("ctl"))
        current_atl = _safe_float(current_wellness.get("atl"))

        # Compute CTL trend from history
        ctl_values = [_safe_float(w.get("ctl")) for w in history[-14:] if w.get("ctl") is not None]
        ctl_slope = linear_slope(ctl_values) if len(ctl_values) >= 3 else 0.0

        # Simple linear projection with damping
        predicted_ctl = current_ctl + ctl_slope * horizon_days * 0.7
        predicted_ctl = max(0.0, predicted_ctl)

        # ATL exponential decay
        predicted_atl = current_atl * math.exp(-horizon_days / ATL_TAU)

        predicted_tsb = predicted_ctl - predicted_atl

        if predicted_ctl > current_ctl + 2:
            trend = "improving"
        elif predicted_ctl < current_ctl - 2:
            trend = "declining"
        else:
            trend = "stable"

        return {
            "horizon_days": horizon_days,
            "predicted_ctl": round(predicted_ctl, 1),
            "predicted_atl": round(predicted_atl, 1),
            "predicted_tsb": round(predicted_tsb, 1),
            "confidence": "low",
            "trend": trend,
            "model_type": "extrapolation",
        }

    def load(self) -> bool:
        """Load saved model from disk. Returns True if successful."""
        model_path = self.model_dir / "performance_forecaster.joblib"
        scaler_path = self.model_dir / "performance_forecaster_scaler.joblib"
        meta_path = self.model_dir / "performance_forecaster_meta.json"

        if not model_path.exists():
            log.debug("No saved performance forecaster found at %s", model_path)
            return False

        try:
            import joblib

            self._model = joblib.load(str(model_path))
            if scaler_path.exists():
                self._scaler = joblib.load(str(scaler_path))
            if meta_path.exists():
                self._metadata = ModelMetadata.from_dict(
                    json.loads(meta_path.read_text())
                )
            log.info("Loaded performance forecaster from %s", model_path)
            return True
        except ImportError:
            log.warning("joblib not installed — cannot load saved model")
            return False
        except Exception as exc:
            log.warning("Failed to load performance forecaster: %s", exc)
            return False

    def save(self):
        """Persist model to disk using joblib."""
        if self._model is None:
            return

        try:
            import joblib
        except ImportError:
            log.warning("joblib not installed — cannot save model")
            return

        model_path = self.model_dir / "performance_forecaster.joblib"
        scaler_path = self.model_dir / "performance_forecaster_scaler.joblib"
        meta_path = self.model_dir / "performance_forecaster_meta.json"

        try:
            joblib.dump(self._model, str(model_path))
            if self._scaler is not None:
                joblib.dump(self._scaler, str(scaler_path))
            if self._metadata is not None:
                meta_path.write_text(json.dumps(self._metadata.to_dict(), indent=2))
            log.info("Saved performance forecaster to %s", model_path)
        except Exception as exc:
            log.warning("Failed to save performance forecaster: %s", exc)

    def _build_features(
        self,
        wellness_history: list[dict],
        activity_history: list[dict] | None = None,
        daily_tss: dict | None = None,
    ) -> list[float]:
        """Extract feature vector from recent history.

        - current_ctl, current_atl: from latest wellness
        - ctl_trend_7d: linear_slope of last 7 CTL values
        - atl_trend_7d: linear_slope of last 7 ATL values
        - avg_weekly_tss: average daily TSS * 7 over last 4 weeks
        - training_consistency: sessions per week over last 4 weeks
        - avg_sleep_7d: average sleep hours over last 7 days
        - avg_hrv_7d: average HRV over last 7 days
        """
        latest = wellness_history[-1] if wellness_history else {}

        current_ctl = _safe_float(latest.get("ctl"))
        current_atl = _safe_float(latest.get("atl"))

        # CTL and ATL trends (7-day)
        ctl_values = [
            _safe_float(w.get("ctl"))
            for w in wellness_history[-7:]
            if w.get("ctl") is not None
        ]
        atl_values = [
            _safe_float(w.get("atl"))
            for w in wellness_history[-7:]
            if w.get("atl") is not None
        ]
        ctl_trend_7d = linear_slope(ctl_values) if len(ctl_values) >= 3 else 0.0
        atl_trend_7d = linear_slope(atl_values) if len(atl_values) >= 3 else 0.0

        # Average weekly TSS over last 4 weeks
        if daily_tss is None:
            daily_tss = _build_daily_tss(activity_history)
        tss_values = list(daily_tss.values()) if daily_tss else []
        last_28_tss = tss_values[-28:] if len(tss_values) >= 28 else tss_values
        avg_daily_tss = rolling_mean(last_28_tss, len(last_28_tss)) if last_28_tss else 0.0
        avg_weekly_tss = avg_daily_tss * 7

        # Training consistency: sessions per week over last 4 weeks
        if activity_history:
            session_count = len([
                a for a in activity_history[-28:]
                if a.get("type") and a.get("icu_training_load")
            ])
            training_consistency = session_count / 4.0  # sessions per week
        else:
            training_consistency = 0.0

        # Average sleep (7 days)
        sleep_values = []
        for w in wellness_history[-7:]:
            secs = _safe_float(w.get("sleepSecs") or w.get("sleep_seconds"))
            if secs > 0:
                sleep_values.append(secs / 3600.0)
        avg_sleep_7d = rolling_mean(sleep_values, 7) if sleep_values else 0.0

        # Average HRV (7 days)
        hrv_values = [
            _safe_float(w.get("hrv"))
            for w in wellness_history[-7:]
            if w.get("hrv") is not None
        ]
        avg_hrv_7d = rolling_mean(hrv_values, 7) if hrv_values else 0.0

        return [
            current_ctl,
            current_atl,
            ctl_trend_7d,
            atl_trend_7d,
            avg_weekly_tss,
            training_consistency,
            avg_sleep_7d,
            avg_hrv_7d,
        ]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _safe_float(value) -> float:
    """Convert a value to float, returning 0.0 on failure."""
    if value is None:
        return 0.0
    try:
        f = float(value)
        return f if math.isfinite(f) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _build_daily_tss(activities: list[dict] | None) -> dict[str, float]:
    """Build a {date: total_tss} lookup from activity list."""
    result: dict[str, float] = {}
    if not activities:
        return result
    for a in activities:
        if not a.get("type"):
            continue
        date = (a.get("start_date_local", a.get("date", "")) or "")[:10]
        if not date:
            continue
        tss = _safe_float(a.get("icu_training_load") or a.get("tss"))
        result[date] = result.get(date, 0.0) + tss
    return result
