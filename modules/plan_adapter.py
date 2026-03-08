"""Plan adaptation module — modify upcoming workouts based on recovery, compliance, and sleep."""

from __future__ import annotations

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SWAP_TARGETS = {
    "hard_run": "easy_run",
    "interval": "easy_run",
    "tempo": "easy_run",
    "threshold": "easy_run",
    "vo2max": "easy_run",
    "long_run": "moderate_run",
    "race_pace": "easy_run",
    "fartlek": "easy_run",
    "hill_repeats": "easy_run",
    "ride_hard": "ride_easy",
    "ride_interval": "ride_easy",
}

_WORKOUT_LABELS = {
    "easy_run": "Easy Run (recovery pace)",
    "moderate_run": "Moderate Run (comfortable effort)",
    "ride_easy": "Easy Ride (zone 1-2)",
    "yoga": "Yoga / Mobility Session",
    "walk": "Brisk Walk",
    "rest": "Full Rest Day",
}


# ---------------------------------------------------------------------------
# 1. Assess adaptation needs
# ---------------------------------------------------------------------------

def assess_adaptation_needs(
    recovery_score: float,
    sleep_analysis: dict,
    compliance: dict,
    performance: dict,
) -> dict:
    """Decide how to adapt an upcoming planned workout.

    Parameters
    ----------
    recovery_score : float
        Composite recovery score 0-100 (from recovery module).
    sleep_analysis : dict
        Output of ``sleep.analyze_sleep`` — must include ``avg_7d``,
        ``debt_7d``, ``last_night``.
    compliance : dict
        Output of ``compliance.analyze_compliance`` — must include
        ``compliance_rate``, ``missed_days``.
    performance : dict
        Output of ``performance.analyze_training`` — must include
        ``tsb``, ``overtraining_risk``, ``ramp_rate``.

    Returns
    -------
    dict with keys: action, intensity_modifier, volume_modifier, reasons,
    swap_suggestion.
    """
    reasons: list[str] = []
    action = "proceed"
    intensity_mod = 1.0
    volume_mod = 1.0
    swap_suggestion: Optional[str] = None

    # --- Extract values safely -------------------------------------------
    sleep_avg = sleep_analysis.get("avg_7d", 7.0)
    sleep_debt = sleep_analysis.get("debt_7d", 0.0)
    last_night = sleep_analysis.get("last_night") or {}
    last_night_hours = last_night.get("hours", 7.0) if isinstance(last_night, dict) else 7.0

    compliance_rate = compliance.get("compliance_rate", 100.0)
    missed_days = compliance.get("missed_days", [])

    tsb = performance.get("tsb", 0.0)
    overtraining_risk = performance.get("overtraining_risk", "low")
    ramp_rate = performance.get("ramp_rate", 0.0)

    # --- Consecutive missed sessions -------------------------------------
    consecutive_missed = _count_consecutive_missed(missed_days)

    # === Rule: sleep_avg < 5h => never allow hard sessions ===============
    hard_blocked = sleep_avg < 5.0
    if hard_blocked:
        reasons.append(
            f"7-day sleep avg critically low ({sleep_avg:.1f}h) — hard sessions blocked"
        )

    # === Rule: 3+ consecutive missed => force easy week ==================
    if consecutive_missed >= 3:
        action = "swap"
        intensity_mod = min(intensity_mod, 0.6)
        volume_mod = min(volume_mod, 0.6)
        swap_suggestion = "easy_run"
        reasons.append(
            f"{consecutive_missed} consecutive missed sessions — easy week recommended"
        )

    # === Rule: compliance_rate < 60% => swap to easier session ============
    if compliance_rate < 60.0:
        if action != "rest":
            action = "swap"
        intensity_mod = min(intensity_mod, 0.65)
        volume_mod = min(volume_mod, 0.7)
        swap_suggestion = swap_suggestion or "easy_run"
        reasons.append(
            f"Low compliance ({compliance_rate:.0f}%) — athlete is struggling, swap to easier session"
        )

    # === Recovery-score tiers ============================================
    if recovery_score < 50:
        action = "rest"
        intensity_mod = min(intensity_mod, 0.5)
        volume_mod = min(volume_mod, 0.5)
        swap_suggestion = "rest"
        reasons.append(
            f"Recovery critically low ({recovery_score:.0f}/100) — rest day mandatory"
        )
    elif recovery_score < 65:
        # Reduce: cut intensity 20-30%
        if action not in ("rest",):
            action = "reduce"
        reduction = _scale_in_range(recovery_score, 50, 65, 0.30, 0.20)
        intensity_mod = min(intensity_mod, 1.0 - reduction)
        volume_mod = min(volume_mod, 1.0 - reduction * 0.8)
        reasons.append(
            f"Recovery moderate-low ({recovery_score:.0f}/100) — reducing intensity by {reduction * 100:.0f}%"
        )
    elif recovery_score < 75:
        if sleep_debt > 10.0:
            if action not in ("rest", "swap"):
                action = "reduce"
            mild_cut = 0.15
            intensity_mod = min(intensity_mod, 1.0 - mild_cut)
            volume_mod = min(volume_mod, 1.0 - mild_cut * 0.7)
            reasons.append(
                f"Recovery OK ({recovery_score:.0f}/100) but sleep debt high ({sleep_debt:.1f}h) — mild reduction"
            )
        else:
            # Proceed with very mild reduction
            mild_cut = 0.05
            intensity_mod = min(intensity_mod, 1.0 - mild_cut)
            volume_mod = min(volume_mod, 1.0 - mild_cut)
            reasons.append(
                f"Recovery acceptable ({recovery_score:.0f}/100) — proceeding with minor adjustment"
            )
    else:
        # 75+ — proceed fully
        reasons.append(f"Recovery good ({recovery_score:.0f}/100) — proceed as planned")

    # === Hard-block override (sleep < 5h avg) ============================
    if hard_blocked and action == "proceed":
        action = "reduce"
        intensity_mod = min(intensity_mod, 0.7)
        volume_mod = min(volume_mod, 0.75)

    # === Additional context signals ======================================
    if overtraining_risk == "high":
        intensity_mod = min(intensity_mod, 0.7)
        volume_mod = min(volume_mod, 0.7)
        if action == "proceed":
            action = "reduce"
        reasons.append(f"Overtraining risk is high (TSB {tsb:.0f}) — additional reduction applied")

    if ramp_rate > 7:
        intensity_mod = min(intensity_mod, 0.85)
        reasons.append(f"Ramp rate elevated ({ramp_rate:.1f}/wk) — capping intensity")

    if last_night_hours < 5.0:
        intensity_mod = min(intensity_mod, 0.7)
        reasons.append(f"Last night sleep very poor ({last_night_hours:.1f}h) — capping today's intensity")

    # === Clamp modifiers =================================================
    intensity_mod = round(max(0.5, min(1.0, intensity_mod)), 2)
    volume_mod = round(max(0.5, min(1.0, volume_mod)), 2)

    return {
        "action": action,
        "intensity_modifier": intensity_mod,
        "volume_modifier": volume_mod,
        "reasons": reasons,
        "swap_suggestion": swap_suggestion,
    }


# ---------------------------------------------------------------------------
# 2. Adapt workout description
# ---------------------------------------------------------------------------

def adapt_workout_description(original_description: str, adaptation: dict) -> str:
    """Rewrite a workout description applying intensity/volume modifiers.

    Adjusts pace targets (e.g. ``4:30/km``), durations (e.g. ``60min``),
    interval counts (e.g. ``8x400m``), and heart-rate zones while preserving
    overall structure.  Appends an adaptation note.

    Parameters
    ----------
    original_description : str
        Raw workout description from the Intervals.icu event.
    adaptation : dict
        Output of :func:`assess_adaptation_needs`.

    Returns
    -------
    str  Modified description with adaptation note appended.
    """
    if not original_description:
        return _adaptation_note(adaptation)

    action = adaptation.get("action", "proceed")
    i_mod = adaptation.get("intensity_modifier", 1.0)
    v_mod = adaptation.get("volume_modifier", 1.0)

    if action == "rest":
        swap = adaptation.get("swap_suggestion", "rest")
        label = _WORKOUT_LABELS.get(swap, "Full Rest / Recovery")
        return (
            f"[ADAPTED — Rest Day]\n"
            f"Original session replaced with: {label}\n\n"
            f"Original plan:\n{original_description}\n\n"
            + _adaptation_note(adaptation)
        )

    if action == "swap":
        swap = adaptation.get("swap_suggestion", "easy_run")
        label = _WORKOUT_LABELS.get(swap, swap)
        return (
            f"[ADAPTED — Swapped Session]\n"
            f"Replaced with: {label}\n"
            f"Keep duration to ~{_modify_duration_text(30, v_mod)}min at easy effort.\n\n"
            f"Original plan:\n{original_description}\n\n"
            + _adaptation_note(adaptation)
        )

    # --- action in ("proceed", "reduce") — modify in-place ---------------
    modified = original_description

    # Adjust pace targets like "4:30/km" or "5:00 min/km"
    modified = _adjust_paces(modified, i_mod)

    # Adjust durations like "60min" or "45 min"
    modified = _adjust_durations(modified, v_mod)

    # Adjust interval counts like "8x400m" or "6 x 800m"
    modified = _adjust_interval_counts(modified, v_mod)

    # Adjust heart-rate ceilings like "HR<155" or "HR 150-160"
    modified = _adjust_hr_targets(modified, i_mod)

    header = "[ADAPTED]" if (i_mod < 1.0 or v_mod < 1.0) else "[REVIEWED — no changes needed]"
    return f"{header}\n{modified}\n\n" + _adaptation_note(adaptation)


# ---------------------------------------------------------------------------
# 3. Generate adaptation summary
# ---------------------------------------------------------------------------

def generate_adaptation_summary(adaptation: dict) -> str:
    """Return a human-readable summary of the adaptation decision.

    Suitable for inclusion in a coaching LLM context window or a chat
    response to the athlete.
    """
    action = adaptation.get("action", "proceed")
    i_mod = adaptation.get("intensity_modifier", 1.0)
    v_mod = adaptation.get("volume_modifier", 1.0)
    reasons = adaptation.get("reasons", [])
    swap = adaptation.get("swap_suggestion")

    action_labels = {
        "proceed": "Proceed as planned",
        "reduce": "Reduce load",
        "swap": "Swap to easier session",
        "rest": "Mandatory rest day",
    }

    lines = [
        "PLAN ADAPTATION:",
        f"Action: {action_labels.get(action, action)}",
        f"Intensity modifier: {i_mod:.0%} of planned",
        f"Volume modifier:    {v_mod:.0%} of planned",
    ]

    if swap and action in ("swap", "rest"):
        label = _WORKOUT_LABELS.get(swap, swap)
        lines.append(f"Swap suggestion: {label}")

    if reasons:
        lines.append("Reasons:")
        for r in reasons:
            lines.append(f"  - {r}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. Should insert rest day?
# ---------------------------------------------------------------------------

def should_insert_rest_day(
    sleep_debt: float,
    consecutive_training_days: int,
    recovery_score: float,
    recent_recovery_scores: Optional[list[float]] = None,
) -> bool:
    """Determine whether an unplanned rest day should be injected.

    Parameters
    ----------
    sleep_debt : float
        Cumulative sleep debt in hours (7-day window).
    consecutive_training_days : int
        Number of consecutive days with a completed workout.
    recovery_score : float
        Today's composite recovery score (0-100).
    recent_recovery_scores : list[float], optional
        Recovery scores for the last *consecutive_training_days* days.
        Used to compute average recovery over the training streak.

    Returns
    -------
    bool  ``True`` when an extra rest day should be inserted.
    """
    # Rule 1: high sleep debt + low recovery
    if sleep_debt > 15.0 and recovery_score < 60:
        return True

    # Rule 2: long streak with poor average recovery
    if consecutive_training_days >= 5:
        if recent_recovery_scores and len(recent_recovery_scores) >= consecutive_training_days:
            streak_scores = recent_recovery_scores[-consecutive_training_days:]
            avg_recovery = sum(streak_scores) / len(streak_scores)
            if avg_recovery < 70:
                return True
        else:
            # Without detailed history, use today's score as proxy
            if recovery_score < 70:
                return True

    return False


# ===========================================================================
# Internal helpers
# ===========================================================================

def _count_consecutive_missed(missed_days: list[dict]) -> int:
    """Count the longest run of consecutive missed days (most recent)."""
    if not missed_days:
        return 0

    dates: list[str] = sorted(d.get("date", "") for d in missed_days if d.get("date"))
    if not dates:
        return 0

    # Walk backwards from the most recent missed date
    streak = 1
    for i in range(len(dates) - 1, 0, -1):
        try:
            cur = dates[i]
            prev = dates[i - 1]
            # Simple date-string arithmetic (YYYY-MM-DD)
            cur_day = int(cur.replace("-", ""))
            prev_day = int(prev.replace("-", ""))
            # Adjacent days differ by 1 in the day field (handles most cases)
            if 0 < cur_day - prev_day <= 1:
                streak += 1
            else:
                break
        except (ValueError, TypeError):
            break
    return streak


def _scale_in_range(
    value: float, lo: float, hi: float, out_lo: float, out_hi: float,
) -> float:
    """Linearly interpolate *value* from [lo, hi] to [out_lo, out_hi]."""
    if hi == lo:
        return out_lo
    t = max(0.0, min(1.0, (value - lo) / (hi - lo)))
    return out_lo + t * (out_hi - out_lo)


# --- Description-rewriting helpers ----------------------------------------

_PACE_RE = re.compile(
    r"(?P<mins>\d{1,2}):(?P<secs>\d{2})\s*(?P<unit>/km|min/km|/mi|min/mi)",
)

_DURATION_RE = re.compile(
    r"(?P<val>\d+)\s*(?P<unit>min(?:ute)?s?|hrs?|hours?)",
    re.IGNORECASE,
)

_INTERVAL_RE = re.compile(
    r"(?P<count>\d+)\s*[xX×]\s*(?P<dist>\d+)\s*(?P<unit>m|km|mi)",
)

_HR_CEILING_RE = re.compile(
    r"HR\s*[<≤]?\s*(?P<hr>\d{2,3})",
    re.IGNORECASE,
)

_HR_RANGE_RE = re.compile(
    r"HR\s*(?P<lo>\d{2,3})\s*[-–]\s*(?P<hi>\d{2,3})",
    re.IGNORECASE,
)


def _adjust_paces(text: str, i_mod: float) -> str:
    """Slow paces proportionally (lower intensity modifier → slower pace)."""
    if i_mod >= 1.0:
        return text

    def _slower(m: re.Match) -> str:
        total_sec = int(m.group("mins")) * 60 + int(m.group("secs"))
        # Slower pace = divide by modifier (e.g. 0.8 → 25% slower)
        new_sec = int(total_sec / i_mod)
        new_min = new_sec // 60
        new_s = new_sec % 60
        return f"{new_min}:{new_s:02d}{m.group('unit')}"

    return _PACE_RE.sub(_slower, text)


def _adjust_durations(text: str, v_mod: float) -> str:
    """Scale durations down by volume modifier."""
    if v_mod >= 1.0:
        return text

    def _shorter(m: re.Match) -> str:
        val = int(m.group("val"))
        new_val = max(1, round(val * v_mod))
        return f"{new_val}{m.group('unit')}"

    return _DURATION_RE.sub(_shorter, text)


def _adjust_interval_counts(text: str, v_mod: float) -> str:
    """Reduce the number of intervals (reps) by volume modifier."""
    if v_mod >= 1.0:
        return text

    def _fewer(m: re.Match) -> str:
        count = int(m.group("count"))
        new_count = max(1, round(count * v_mod))
        return f"{new_count}x{m.group('dist')}{m.group('unit')}"

    return _INTERVAL_RE.sub(_fewer, text)


def _adjust_hr_targets(text: str, i_mod: float) -> str:
    """Lower HR ceilings / ranges by intensity modifier delta."""
    if i_mod >= 1.0:
        return text

    reduction_bpm = round((1.0 - i_mod) * 20)  # up to ~10 bpm at 0.5 mod

    def _lower_ceiling(m: re.Match) -> str:
        hr = int(m.group("hr"))
        return m.group(0).replace(str(hr), str(hr - reduction_bpm))

    text = _HR_CEILING_RE.sub(_lower_ceiling, text)

    def _lower_range(m: re.Match) -> str:
        lo = int(m.group("lo")) - reduction_bpm
        hi = int(m.group("hi")) - reduction_bpm
        return f"HR {lo}-{hi}"

    text = _HR_RANGE_RE.sub(_lower_range, text)
    return text


def _modify_duration_text(base_min: int, v_mod: float) -> int:
    """Return adjusted duration in minutes."""
    return max(10, round(base_min * v_mod))


def _adaptation_note(adaptation: dict) -> str:
    """Build the trailing note appended to adapted descriptions."""
    reasons = adaptation.get("reasons", [])
    i_mod = adaptation.get("intensity_modifier", 1.0)
    v_mod = adaptation.get("volume_modifier", 1.0)
    lines = [
        "--- Adaptation Note ---",
        f"Intensity: {i_mod:.0%} | Volume: {v_mod:.0%}",
    ]
    if reasons:
        lines.append("Why: " + "; ".join(reasons))
    return "\n".join(lines)
