"""Race prediction module — marathon finish-time estimates using multiple models."""

from __future__ import annotations

import math
from datetime import datetime, timedelta


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MARATHON_DISTANCE_KM = 42.195

# Riegel exponent (empirical constant for endurance fatigue curve)
RIEGEL_EXPONENT = 1.06

# CTL-to-marathon mapping (linear interpolation anchors)
# Each tuple: (CTL value, estimated marathon seconds)
_CTL_ANCHORS: list[tuple[float, float]] = [
    (30,  4 * 3600),           # CTL 30  -> ~4:00:00
    (40,  3 * 3600 + 30 * 60), # CTL 40  -> ~3:30:00
    (50,  3 * 3600 + 15 * 60), # CTL 50  -> ~3:15:00
    (60,  3 * 3600 + 5 * 60),  # CTL 60  -> ~3:05:00
    (70,  2 * 3600 + 55 * 60), # CTL 70  -> ~2:55:00
    (80,  2 * 3600 + 45 * 60), # CTL 80  -> ~2:45:00
    (100, 2 * 3600 + 30 * 60), # CTL 100 -> ~2:30:00
]

# VDOT table subset — maps VDOT score to estimated marathon seconds.
# Source: Jack Daniels' Running Formula, condensed for lookup.
_VDOT_MARATHON: list[tuple[float, float]] = [
    (30, 4 * 3600 + 49 * 60 + 17),
    (33, 4 * 3600 + 24 * 60 + 39),
    (35, 4 * 3600 + 9 * 60 + 30),
    (37, 3 * 3600 + 55 * 60 + 13),
    (40, 3 * 3600 + 34 * 60 + 36),
    (42, 3 * 3600 + 21 * 60 + 42),
    (45, 3 * 3600 + 3 * 60 + 2),
    (47, 2 * 3600 + 52 * 60 + 9),
    (50, 2 * 3600 + 38 * 60 + 54),
    (52, 2 * 3600 + 30 * 60 + 3),
    (55, 2 * 3600 + 18 * 60 + 37),
    (58, 2 * 3600 + 8 * 60 + 24),
    (60, 2 * 3600 + 1 * 60 + 39),
]

# Pace boundaries for identifying tempo/threshold efforts.
# Threshold pace is roughly 85-90 % of VO2max pace.  We use pace-per-km
# range (seconds) that is plausible for threshold work.
_THRESHOLD_PACE_RANGE_SEC_KM = (210, 360)  # 3:30 – 6:00 /km


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seconds_to_hms(total_seconds: float) -> str:
    """Convert seconds to 'H:MM:SS' string."""
    total_seconds = max(0, int(round(total_seconds)))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h}:{m:02d}:{s:02d}"


def _hms_to_seconds(hms: str) -> float:
    """Parse 'H:MM:SS' or 'MM:SS' into total seconds."""
    parts = hms.strip().split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return float(parts[0])


def _lerp_table(table: list[tuple[float, float]], x: float) -> float:
    """Linearly interpolate *y* from a sorted (x, y) table.

    Clamps to the boundary values when *x* is outside the table range.
    """
    if x <= table[0][0]:
        return table[0][1]
    if x >= table[-1][0]:
        return table[-1][1]
    for i in range(len(table) - 1):
        x0, y0 = table[i]
        x1, y1 = table[i + 1]
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return table[-1][1]  # fallback


# ---------------------------------------------------------------------------
# Individual prediction methods
# ---------------------------------------------------------------------------

def _riegel_predict(recent_runs: list[dict], reference_date: datetime | None = None) -> dict | None:
    """Predict marathon time with the Riegel formula.

    Scans *recent_runs* for any effort longer than 5 km recorded in the last
    30 days.  For each qualifying effort the formula ``T2 = T1 * (D2/D1)^1.06``
    is applied.  The best (fastest) prediction is returned.

    Each run dict is expected to have at minimum:
        - ``distance`` (metres **or** km — auto-detected by magnitude)
        - ``moving_time`` or ``elapsed_time`` (seconds)
        - ``start_date_local`` or ``date`` (ISO date string, optional but used
          for the 30-day window)
        - ``type`` (string, must contain 'run')
    """
    if not recent_runs:
        return None

    ref = reference_date or datetime.now()
    cutoff = ref - timedelta(days=30)
    best_seconds: float | None = None
    best_source: str = ""

    for run in recent_runs:
        # Filter to runs only
        rtype = (run.get("type") or "").lower()
        if "run" not in rtype:
            continue

        # Date filter
        date_str = run.get("start_date_local", run.get("date", ""))
        if date_str:
            try:
                run_date = datetime.fromisoformat(date_str[:19])
            except (ValueError, TypeError):
                run_date = ref  # assume recent if unparseable
        else:
            run_date = ref

        if run_date < cutoff:
            continue

        # Distance (auto-detect m vs km)
        raw_dist = run.get("distance", 0) or 0
        dist_km = raw_dist / 1000.0 if raw_dist > 1000 else float(raw_dist)
        if dist_km < 5.0:
            continue

        # Time in seconds
        time_sec = run.get("moving_time") or run.get("elapsed_time") or 0
        if time_sec <= 0:
            continue

        # Riegel projection
        predicted = time_sec * (MARATHON_DISTANCE_KM / dist_km) ** RIEGEL_EXPONENT
        if best_seconds is None or predicted < best_seconds:
            best_seconds = predicted
            pace_str = _seconds_to_hms(time_sec / dist_km).lstrip("0:").lstrip("0")
            best_source = f"{dist_km:.1f} km in {_seconds_to_hms(time_sec)} ({pace_str}/km)"

    if best_seconds is None:
        return None

    return {
        "method": "Riegel formula",
        "predicted_seconds": round(best_seconds),
        "predicted_time": _seconds_to_hms(best_seconds),
        "detail": f"Based on: {best_source}",
    }


def _ctl_predict(ctl: float) -> dict | None:
    """Predict marathon time from current Chronic Training Load.

    Uses a piecewise-linear mapping derived from population-level coaching
    data.  This is the coarsest model — useful mainly as a sanity check.
    """
    if ctl <= 0:
        return None

    predicted = _lerp_table(_CTL_ANCHORS, ctl)
    return {
        "method": "CTL-based estimate",
        "predicted_seconds": round(predicted),
        "predicted_time": _seconds_to_hms(predicted),
        "detail": f"CTL {ctl:.0f} maps to ~{_seconds_to_hms(predicted)}",
    }


def _estimate_vdot_from_runs(recent_runs: list[dict], reference_date: datetime | None = None) -> float | None:
    """Estimate VDOT from tempo / threshold-pace running efforts.

    Looks at runs tagged as 'Run' where average pace falls in the threshold
    range.  Uses the Daniels VO2 approximation:

        VO2 = -4.60 + 0.182258 * v + 0.000104 * v^2
        %VO2max ≈ 0.8 + 0.1894393 * e^(-0.012778 * t)
                      + 0.2989558 * e^(-0.1932605 * t)

    where *v* is velocity in m/min and *t* is duration in minutes.
    """
    if not recent_runs:
        return None

    ref = reference_date or datetime.now()
    cutoff = ref - timedelta(days=30)
    best_vdot: float | None = None

    for run in recent_runs:
        rtype = (run.get("type") or "").lower()
        if "run" not in rtype:
            continue

        date_str = run.get("start_date_local", run.get("date", ""))
        if date_str:
            try:
                run_date = datetime.fromisoformat(date_str[:19])
            except (ValueError, TypeError):
                run_date = ref
        else:
            run_date = ref

        if run_date < cutoff:
            continue

        raw_dist = run.get("distance", 0) or 0
        dist_km = raw_dist / 1000.0 if raw_dist > 1000 else float(raw_dist)
        time_sec = run.get("moving_time") or run.get("elapsed_time") or 0
        if dist_km <= 0 or time_sec <= 0:
            continue

        pace_sec_km = time_sec / dist_km
        if not (_THRESHOLD_PACE_RANGE_SEC_KM[0] <= pace_sec_km <= _THRESHOLD_PACE_RANGE_SEC_KM[1]):
            continue

        # Convert to m/min
        dist_m = dist_km * 1000.0
        time_min = time_sec / 60.0
        v = dist_m / time_min  # m/min

        # Daniels VO2 cost equation
        vo2 = -4.60 + 0.182258 * v + 0.000104 * v * v

        # Percent VO2max sustained for the duration
        pct_vo2max = (0.8 + 0.1894393 * math.exp(-0.012778 * time_min)
                      + 0.2989558 * math.exp(-0.1932605 * time_min))

        if pct_vo2max <= 0:
            continue

        vdot = vo2 / pct_vo2max

        if best_vdot is None or vdot > best_vdot:
            best_vdot = vdot

    return best_vdot


def _vdot_predict(recent_runs: list[dict], reference_date: datetime | None = None) -> dict | None:
    """Predict marathon time via Jack Daniels VDOT estimation."""
    vdot = _estimate_vdot_from_runs(recent_runs, reference_date)
    if vdot is None:
        return None

    predicted = _lerp_table(_VDOT_MARATHON, vdot)
    return {
        "method": "Jack Daniels VDOT",
        "predicted_seconds": round(predicted),
        "predicted_time": _seconds_to_hms(predicted),
        "detail": f"Estimated VDOT {vdot:.1f} -> marathon {_seconds_to_hms(predicted)}",
    }


# ---------------------------------------------------------------------------
# Limiting-factor analysis
# ---------------------------------------------------------------------------

def _detect_limiting_factors(
    ctl: float,
    recent_runs: list[dict],
    weight_kg: float,
    wellness_data: list[dict] | None = None,
    reference_date: datetime | None = None,
) -> list[str]:
    """Return a list of plain-English limiting factors."""
    factors: list[str] = []
    ref = reference_date or datetime.now()

    # --- Low weekly run volume ---
    cutoff_7d = ref - timedelta(days=7)
    runs_this_week = 0
    for run in recent_runs:
        rtype = (run.get("type") or "").lower()
        if "run" not in rtype:
            continue
        date_str = run.get("start_date_local", run.get("date", ""))
        if date_str:
            try:
                run_date = datetime.fromisoformat(date_str[:19])
            except (ValueError, TypeError):
                run_date = ref
        else:
            run_date = ref
        if run_date >= cutoff_7d:
            runs_this_week += 1

    if runs_this_week < 3:
        factors.append(
            f"Low run frequency: only {runs_this_week} run(s) in the last 7 days (target >= 3)"
        )

    # --- No long runs > 25 km in last 30 days ---
    cutoff_30d = ref - timedelta(days=30)
    has_long_run = False
    for run in recent_runs:
        rtype = (run.get("type") or "").lower()
        if "run" not in rtype:
            continue
        date_str = run.get("start_date_local", run.get("date", ""))
        if date_str:
            try:
                run_date = datetime.fromisoformat(date_str[:19])
            except (ValueError, TypeError):
                run_date = ref
        else:
            run_date = ref
        if run_date < cutoff_30d:
            continue
        raw_dist = run.get("distance", 0) or 0
        dist_km = raw_dist / 1000.0 if raw_dist > 1000 else float(raw_dist)
        if dist_km >= 25.0:
            has_long_run = True
            break

    if not has_long_run:
        factors.append("No long run >= 25 km in the last 30 days")

    # --- CTL declining trend (use wellness data if available) ---
    if wellness_data and len(wellness_data) >= 14:
        ctl_now = wellness_data[-1].get("ctl", 0)
        ctl_14d_ago = wellness_data[-14].get("ctl", 0)
        if ctl_now < ctl_14d_ago - 2:
            factors.append(
                f"CTL declining: {ctl_14d_ago:.0f} -> {ctl_now:.0f} over 14 days"
            )

    # --- Sleep debt (rolling 7-day average < 7 h) ---
    if wellness_data and len(wellness_data) >= 7:
        recent_sleep = [
            w.get("sleepSecs", 0) or 0
            for w in wellness_data[-7:]
        ]
        avg_sleep_hours = sum(recent_sleep) / (7 * 3600) if any(recent_sleep) else 0
        if 0 < avg_sleep_hours < 7.0:
            factors.append(
                f"Sleep debt: averaging {avg_sleep_hours:.1f} h/night over last 7 days (target >= 7 h)"
            )

    # --- Weight consideration for marathon ---
    if weight_kg > 85:
        factors.append(
            f"Body weight ({weight_kg:.0f} kg) is above typical competitive marathon range"
        )

    return factors


# ---------------------------------------------------------------------------
# Recommendations engine
# ---------------------------------------------------------------------------

def _generate_recommendations(
    predicted_seconds: float,
    target_seconds: float,
    ctl: float,
    limiting_factors: list[str],
    methods: list[dict],
) -> list[str]:
    """Generate actionable recommendations based on the gap and limiters."""
    recs: list[str] = []
    gap = predicted_seconds - target_seconds  # positive = slower than goal

    # Pacing guidance
    target_pace = target_seconds / MARATHON_DISTANCE_KM
    predicted_pace = predicted_seconds / MARATHON_DISTANCE_KM
    if gap > 300:  # more than 5 min off
        recs.append(
            f"Current predicted pace ({_seconds_to_hms(predicted_pace).lstrip('0:')}/km) "
            f"is slower than goal pace ({_seconds_to_hms(target_pace).lstrip('0:')}/km). "
            f"Focus on threshold and tempo sessions to close the gap."
        )
    elif gap > 0:
        recs.append(
            f"Predicted pace is within {gap:.0f}s of goal — keep current training rhythm and "
            f"sharpen with race-pace workouts."
        )
    else:
        recs.append(
            "Fitness suggests you can meet the goal. Prioritise freshness and taper execution."
        )

    # Address specific limiters
    for factor in limiting_factors:
        fl = factor.lower()
        if "long run" in fl:
            recs.append(
                "Schedule a long run of 28-32 km at easy pace within the next two weeks."
            )
        if "run frequency" in fl or "low run" in fl:
            recs.append(
                "Add one easy 40-50 min run per week to build run-specific durability."
            )
        if "ctl declining" in fl:
            recs.append(
                "CTL is trending down — review recent volume and ensure consistency "
                "before entering taper."
            )
        if "sleep" in fl:
            recs.append(
                "Address sleep debt: aim for >= 7.5 h/night in the weeks before the race."
            )
        if "weight" in fl:
            recs.append(
                "Consider a modest caloric deficit on easy days if body-composition "
                "improvement is safe and sustainable."
            )

    # CTL-specific advice
    if ctl < 50:
        recs.append(
            f"CTL ({ctl:.0f}) is below 50 — a sub-3:00 marathon typically requires CTL >= 60. "
            f"Prioritise consistent volume to build aerobic base."
        )

    # Method-spread warning
    times = [m["predicted_seconds"] for m in methods]
    if times:
        spread = max(times) - min(times)
        if spread > 600:
            recs.append(
                f"Prediction spread is {_seconds_to_hms(spread)} across methods — "
                f"gather more race data (e.g. a half-marathon tune-up) for higher confidence."
            )

    return recs


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

def _assess_confidence(methods: list[dict], target_seconds: float) -> str:
    """Return 'high', 'medium', or 'low' based on method count and agreement."""
    if len(methods) < 2:
        return "low"

    times = [m["predicted_seconds"] for m in methods]
    spread = max(times) - min(times)
    avg = sum(times) / len(times)
    gap_pct = abs(avg - target_seconds) / target_seconds * 100

    if len(methods) >= 3 and spread < 300:
        return "high"
    if len(methods) >= 2 and spread < 600:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def predict_marathon(
    ctl: float,
    recent_runs: list[dict],
    weight_kg: float = 80.0,
    target_time: str = "3:00:00",
    wellness_data: list[dict] | None = None,
    reference_date: datetime | None = None,
) -> dict:
    """Predict marathon finish time and return a comprehensive analysis dict.

    Parameters
    ----------
    ctl : float
        Current Chronic Training Load (e.g. from Intervals.icu).
    recent_runs : list[dict]
        Activity dicts from the last ~30 days.  Expected keys per run:
        ``distance``, ``moving_time``/``elapsed_time``, ``type``,
        ``start_date_local``/``date``.
    weight_kg : float
        Athlete body weight in kilograms.
    target_time : str
        Goal marathon time as ``'H:MM:SS'``.
    wellness_data : list[dict] | None
        Optional daily wellness rows (sorted oldest-first) used for CTL
        trend and sleep analysis.  Each row may include ``ctl``,
        ``sleepSecs``, etc.
    reference_date : datetime | None
        Override "now" for testing.  Defaults to ``datetime.now()``.

    Returns
    -------
    dict with keys:
        predicted_time, confidence, gap_to_goal, methods,
        limiting_factors, recommendations
    """
    target_seconds = _hms_to_seconds(target_time)

    # Gather individual method predictions
    methods: list[dict] = []

    riegel = _riegel_predict(recent_runs, reference_date)
    if riegel:
        methods.append(riegel)

    ctl_pred = _ctl_predict(ctl)
    if ctl_pred:
        methods.append(ctl_pred)

    vdot = _vdot_predict(recent_runs, reference_date)
    if vdot:
        methods.append(vdot)

    # Composite prediction — weighted average favouring race-data methods
    if methods:
        weights_map = {
            "Riegel formula": 3.0,
            "Jack Daniels VDOT": 2.5,
            "CTL-based estimate": 1.0,
        }
        total_weight = 0.0
        weighted_sum = 0.0
        for m in methods:
            w = weights_map.get(m["method"], 1.0)
            weighted_sum += m["predicted_seconds"] * w
            total_weight += w
        composite_seconds = weighted_sum / total_weight
    else:
        # Fallback — rough guess from CTL even if zero
        composite_seconds = _lerp_table(_CTL_ANCHORS, max(ctl, 30))

    gap = round(composite_seconds - target_seconds)  # +ve = slower

    # Limiting factors
    limiting_factors = _detect_limiting_factors(
        ctl, recent_runs, weight_kg, wellness_data, reference_date
    )

    # Confidence
    confidence = _assess_confidence(methods, target_seconds)

    # Recommendations
    recommendations = _generate_recommendations(
        composite_seconds, target_seconds, ctl, limiting_factors, methods
    )

    return {
        "predicted_time": _seconds_to_hms(composite_seconds),
        "predicted_seconds": round(composite_seconds),
        "confidence": confidence,
        "gap_to_goal": gap,
        "target_time": target_time,
        "methods": methods,
        "limiting_factors": limiting_factors,
        "recommendations": recommendations,
    }


def format_race_prediction_context(prediction: dict) -> str:
    """Format a prediction dict into a human-readable text block.

    Designed for embedding into the coaching LLM's context window or for
    display in a CLI/Telegram interface.
    """
    gap = prediction["gap_to_goal"]
    if gap > 0:
        gap_str = f"+{_seconds_to_hms(gap)} slower than goal"
    elif gap < 0:
        gap_str = f"-{_seconds_to_hms(abs(gap))} faster than goal"
    else:
        gap_str = "exactly on goal"

    lines: list[str] = [
        "MARATHON RACE PREDICTION:",
        f"  Predicted finish: {prediction['predicted_time']}  "
        f"(goal {prediction['target_time']})",
        f"  Gap to goal: {gap_str}",
        f"  Confidence: {prediction['confidence']}",
    ]

    if prediction["methods"]:
        lines.append("")
        lines.append("  Methods:")
        for m in prediction["methods"]:
            lines.append(f"    - {m['method']}: {m['predicted_time']}  ({m['detail']})")

    if prediction["limiting_factors"]:
        lines.append("")
        lines.append("  Limiting factors:")
        for lf in prediction["limiting_factors"]:
            lines.append(f"    ! {lf}")

    if prediction["recommendations"]:
        lines.append("")
        lines.append("  Recommendations:")
        for i, rec in enumerate(prediction["recommendations"], 1):
            lines.append(f"    {i}. {rec}")

    return "\n".join(lines)
