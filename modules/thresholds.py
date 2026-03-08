"""Personalized thresholds — data-driven baselines replacing hardcoded magic numbers."""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass

log = logging.getLogger("coach.thresholds")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Assessment:
    """Result of assessing a single metric against personalised baselines."""
    status: str        # "optimal", "normal", "low", "high", "critical"
    value: float
    baseline: float
    z_score: float
    percentile: float  # 0-100 within athlete's own history
    trend: str         # "improving", "stable", "declining"
    trend_slope: float


# ---------------------------------------------------------------------------
# Pure-Python stats helpers (no numpy)
# ---------------------------------------------------------------------------

def _clean(values: list) -> list[float]:
    """Filter out None / non-numeric entries."""
    out = []
    for v in values:
        if v is None:
            continue
        try:
            f = float(v)
            if math.isfinite(f):
                out.append(f)
        except (TypeError, ValueError):
            continue
    return out


def rolling_mean(values: list[float], window: int) -> float:
    """Mean of the last *window* values (or all if fewer)."""
    subset = values[-window:] if len(values) >= window else values
    return sum(subset) / len(subset) if subset else 0.0


def rolling_std(values: list[float], window: int) -> float:
    """Population std dev of the last *window* values."""
    subset = values[-window:] if len(values) >= window else values
    if len(subset) < 2:
        return 0.0
    mean = sum(subset) / len(subset)
    var = sum((x - mean) ** 2 for x in subset) / len(subset)
    return math.sqrt(var)


def linear_slope(values: list[float]) -> float:
    """OLS slope of values indexed 0..n-1. Returns 0.0 if < 3 points."""
    n = len(values)
    if n < 3:
        return 0.0
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    num = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(values))
    denom = sum((i - mean_x) ** 2 for i in range(n))
    if denom == 0:
        return 0.0
    return num / denom


def percentile_rank(value: float, history: list[float]) -> float:
    """Percentage of history values that are <= value (0-100)."""
    if not history:
        return 50.0
    count = sum(1 for h in history if h <= value)
    return 100.0 * count / len(history)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class PersonalizedThresholds:
    """Compute athlete-specific baselines from historical wellness data.

    Parameters
    ----------
    wellness_history : list[dict]
        Intervals.icu-format wellness entries. Expected keys:
        id (date str), hrv, restingHR, sleepSecs, sleepScore, ctl, atl, ...
    activities : list[dict] | None
        Intervals.icu-format activities for training load baselines.
    """

    def __init__(self, wellness_history: list[dict], activities: list[dict] | None = None):
        # Extract data series
        self._hrv_series = _clean([w.get("hrv") for w in wellness_history])
        self._rhr_series = _clean([w.get("restingHR") or w.get("rhr") for w in wellness_history])
        self._sleep_series = _clean([
            (w.get("sleepSecs", 0) or 0) / 3600
            for w in wellness_history
            if (w.get("sleepSecs", 0) or 0) > 0
        ])
        self._ctl_series = _clean([w.get("ctl") for w in wellness_history])
        self._atl_series = _clean([w.get("atl") for w in wellness_history])
        self._sleep_score_series = _clean([w.get("sleepScore") for w in wellness_history])

        # Training load from activities
        self._daily_tss: list[float] = []
        if activities:
            from collections import defaultdict
            by_date: dict[str, float] = defaultdict(float)
            for a in activities:
                if not a.get("type"):
                    continue
                d = (a.get("start_date_local", a.get("date", "")) or "")[:10]
                tss = 0.0
                try:
                    tss = float(a.get("icu_training_load", 0) or 0)
                except (TypeError, ValueError):
                    pass
                by_date[d] += tss
            if by_date:
                self._daily_tss = [by_date[k] for k in sorted(by_date)]

        # Compute baselines (30-day window for stability)
        self.hrv_baseline = rolling_mean(self._hrv_series, 30)
        self.hrv_std = rolling_std(self._hrv_series, 30)
        self.hrv_low = self.hrv_baseline - 1.5 * self.hrv_std if self.hrv_std else self.hrv_baseline * 0.8
        self.hrv_high = self.hrv_baseline + 1.5 * self.hrv_std if self.hrv_std else self.hrv_baseline * 1.2

        self.rhr_baseline = rolling_mean(self._rhr_series, 30)
        self.rhr_std = rolling_std(self._rhr_series, 30)
        self.rhr_elevated = self.rhr_baseline + 1.5 * self.rhr_std if self.rhr_std else self.rhr_baseline + 5

        self.sleep_baseline = rolling_mean(self._sleep_series, 14)
        self.sleep_std = rolling_std(self._sleep_series, 14)

        self.ctl_baseline = rolling_mean(self._ctl_series, 30)
        self.atl_baseline = rolling_mean(self._atl_series, 14)

        self.tss_daily_avg = rolling_mean(self._daily_tss, 28) if self._daily_tss else 0.0
        self.tss_daily_std = rolling_std(self._daily_tss, 28) if self._daily_tss else 0.0

        log.debug(
            "Thresholds computed: HRV=%.1f±%.1f  RHR=%.1f±%.1f  Sleep=%.1fh  CTL=%.1f",
            self.hrv_baseline, self.hrv_std,
            self.rhr_baseline, self.rhr_std,
            self.sleep_baseline, self.ctl_baseline,
        )

    # -- Assessors ------------------------------------------------------------

    def assess_hrv(self, value: float) -> Assessment:
        """Assess an HRV reading against personalised baseline."""
        z = self._z_score(value, self.hrv_baseline, self.hrv_std)
        pct = percentile_rank(value, self._hrv_series)
        trend, slope = self._compute_trend(self._hrv_series, 7)
        status = self._status_from_z_inverted(z)  # lower HRV = worse
        return Assessment(status=status, value=value, baseline=self.hrv_baseline,
                          z_score=z, percentile=pct, trend=trend, trend_slope=slope)

    def assess_rhr(self, value: float) -> Assessment:
        """Assess a resting heart rate reading."""
        z = self._z_score(value, self.rhr_baseline, self.rhr_std)
        pct = percentile_rank(value, self._rhr_series)
        trend, slope = self._compute_trend(self._rhr_series, 7)
        # Higher RHR = worse
        if z > 2.0:
            status = "critical"
        elif z > 1.5:
            status = "high"
        elif z > 0.5:
            status = "normal"
        elif z > -0.5:
            status = "optimal"
        else:
            status = "low"
        return Assessment(status=status, value=value, baseline=self.rhr_baseline,
                          z_score=z, percentile=pct, trend=trend, trend_slope=slope)

    def assess_sleep_duration(self, hours: float) -> Assessment:
        """Assess sleep duration against personalised baseline."""
        z = self._z_score(hours, self.sleep_baseline, self.sleep_std)
        pct = percentile_rank(hours, self._sleep_series)
        trend, slope = self._compute_trend(self._sleep_series, 7)
        status = self._status_from_z_inverted(z)  # less sleep = worse
        return Assessment(status=status, value=hours, baseline=self.sleep_baseline,
                          z_score=z, percentile=pct, trend=trend, trend_slope=slope)

    def assess_training_load(self, tss: float) -> Assessment:
        """Assess a day's training load (TSS) against typical load."""
        z = self._z_score(tss, self.tss_daily_avg, self.tss_daily_std)
        pct = percentile_rank(tss, self._daily_tss)
        trend, slope = self._compute_trend(self._daily_tss, 7)
        if z > 2.0:
            status = "critical"
        elif z > 1.3:
            status = "high"
        elif z > -0.5:
            status = "normal"
        else:
            status = "low"
        return Assessment(status=status, value=tss, baseline=self.tss_daily_avg,
                          z_score=z, percentile=pct, trend=trend, trend_slope=slope)

    def assess_recovery(self, ctl: float, atl: float) -> Assessment:
        """Assess training balance via CTL/ATL ratio (ACWR proxy)."""
        if ctl == 0:
            ratio = 0.0
        else:
            ratio = atl / ctl
        # Optimal ACWR: 0.8-1.3
        if ratio > 1.5:
            status = "critical"
        elif ratio > 1.3:
            status = "high"
        elif ratio >= 0.8:
            status = "optimal"
        elif ratio >= 0.5:
            status = "normal"
        else:
            status = "low"
        z = self._z_score(ratio, 1.0, 0.3)  # centred on 1.0
        pct = 50.0  # no history for ratio
        trend, slope = "stable", 0.0
        if len(self._ctl_series) >= 7 and len(self._atl_series) >= 7:
            ratios = []
            for c, a in zip(self._ctl_series[-7:], self._atl_series[-7:]):
                ratios.append(a / c if c else 0.0)
            trend, slope = self._compute_trend(ratios, 7)
        return Assessment(status=status, value=ratio, baseline=1.0,
                          z_score=z, percentile=pct, trend=trend, trend_slope=slope)

    def assess(self, metric: str, value: float) -> Assessment:
        """Dispatch to the correct assessor by metric name."""
        dispatch = {
            "hrv": self.assess_hrv,
            "rhr": self.assess_rhr,
            "sleep": self.assess_sleep_duration,
            "training_load": self.assess_training_load,
            "tss": self.assess_training_load,
        }
        fn = dispatch.get(metric.lower())
        if fn is None:
            # Generic assessment against no baseline
            return Assessment(
                status="normal", value=value, baseline=0.0,
                z_score=0.0, percentile=50.0, trend="stable", trend_slope=0.0,
            )
        return fn(value)

    def get_all_baselines(self) -> dict:
        """Return all computed baselines for display/logging."""
        return {
            "hrv": {"baseline": round(self.hrv_baseline, 1), "std": round(self.hrv_std, 1),
                    "low": round(self.hrv_low, 1), "high": round(self.hrv_high, 1),
                    "n": len(self._hrv_series)},
            "rhr": {"baseline": round(self.rhr_baseline, 1), "std": round(self.rhr_std, 1),
                    "elevated": round(self.rhr_elevated, 1), "n": len(self._rhr_series)},
            "sleep": {"baseline_h": round(self.sleep_baseline, 1), "std_h": round(self.sleep_std, 1),
                      "n": len(self._sleep_series)},
            "ctl": {"baseline": round(self.ctl_baseline, 1), "n": len(self._ctl_series)},
            "tss_daily": {"avg": round(self.tss_daily_avg, 1), "std": round(self.tss_daily_std, 1),
                          "n": len(self._daily_tss)},
        }

    def format_context(self) -> str:
        """Format thresholds as text for the LLM prompt context."""
        b = self.get_all_baselines()
        lines = ["PERSONALISED BASELINES (computed from athlete history):"]
        if b["hrv"]["n"] >= 5:
            lines.append(
                f"  HRV: {b['hrv']['baseline']}ms baseline (±{b['hrv']['std']}ms) | "
                f"Low <{b['hrv']['low']}ms | High >{b['hrv']['high']}ms | n={b['hrv']['n']}"
            )
        if b["rhr"]["n"] >= 5:
            lines.append(
                f"  RHR: {b['rhr']['baseline']}bpm baseline (±{b['rhr']['std']}bpm) | "
                f"Elevated >{b['rhr']['elevated']}bpm | n={b['rhr']['n']}"
            )
        if b["sleep"]["n"] >= 5:
            lines.append(
                f"  Sleep: {b['sleep']['baseline_h']}h baseline (±{b['sleep']['std_h']}h) | n={b['sleep']['n']}"
            )
        if b["ctl"]["n"] >= 5:
            lines.append(f"  CTL: {b['ctl']['baseline']} baseline | n={b['ctl']['n']}")
        if b["tss_daily"]["n"] >= 5:
            lines.append(
                f"  Daily TSS: {b['tss_daily']['avg']} avg (±{b['tss_daily']['std']}) | n={b['tss_daily']['n']}"
            )
        return "\n".join(lines) if len(lines) > 1 else ""

    # -- Internal helpers -----------------------------------------------------

    @staticmethod
    def _z_score(value: float, mean: float, std: float) -> float:
        if std == 0 or std is None:
            return 0.0
        return (value - mean) / std

    @staticmethod
    def _status_from_z_inverted(z: float) -> str:
        """For metrics where lower = worse (HRV, sleep)."""
        if z < -2.0:
            return "critical"
        if z < -1.5:
            return "low"
        if z < 0.5:
            return "normal"
        return "optimal"

    @staticmethod
    def _compute_trend(series: list[float], window: int) -> tuple[str, float]:
        """Compute trend direction from the last *window* values."""
        subset = series[-window:] if len(series) >= window else series
        if len(subset) < 3:
            return "stable", 0.0
        slope = linear_slope(subset)
        std = rolling_std(subset, len(subset))
        if std == 0:
            return "stable", slope
        # Normalise slope by std to get a sense of significance
        norm_slope = slope / std if std else 0.0
        if norm_slope > 0.3:
            return "improving", slope
        elif norm_slope < -0.3:
            return "declining", slope
        return "stable", slope
