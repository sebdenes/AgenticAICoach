"""Pattern detection and athlete modelling — analyze historical data for actionable insights."""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

log = logging.getLogger("coach.intelligence")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val, default: float = 0.0) -> float:
    """Coerce a value to float, returning *default* on None / non-numeric."""
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _sleep_hours(wellness_entry: dict) -> float | None:
    """Extract sleep hours from a wellness dict, returning None when absent."""
    secs = wellness_entry.get("sleepSecs", 0) or wellness_entry.get("sleep_seconds", 0) or 0
    return secs / 3600 if secs > 0 else None


def _activity_date(activity: dict) -> str:
    """Return the YYYY-MM-DD date string from an activity dict."""
    return (activity.get("start_date_local", activity.get("date", "")) or "")[:10]


def _wellness_date(entry: dict) -> str:
    return (entry.get("id", entry.get("date", "")) or "")[:10]


def _pearson_r(xs: list[float], ys: list[float]) -> float | None:
    """Compute Pearson correlation coefficient for two equal-length sequences.

    Returns None when there are fewer than 5 paired observations or zero
    variance in either series.
    """
    if len(xs) < 5 or len(ys) < 5 or len(xs) != len(ys):
        return None
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    num = sum(a * b for a, b in zip(dx, dy))
    denom_x = sum(a * a for a in dx) ** 0.5
    denom_y = sum(b * b for b in dy) ** 0.5
    if denom_x == 0 or denom_y == 0:
        return None
    return num / (denom_x * denom_y)


def _linear_slope(ys: list[float]) -> float | None:
    """Return the slope of a simple least-squares fit of *ys* against [0..n).

    Returns None when fewer than 3 data points.
    """
    n = len(ys)
    if n < 3:
        return None
    xs = list(range(n))
    mean_x = (n - 1) / 2
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None
    return num / denom


def _classify_sport(activity: dict) -> str:
    atype = (activity.get("type", "") or "").lower()
    if "run" in atype:
        return "run"
    if "ride" in atype or "cycling" in atype:
        return "ride"
    if "strength" in atype or "weight" in atype:
        return "strength"
    if "swim" in atype:
        return "swim"
    return "other"


# ---------------------------------------------------------------------------
# Pattern Detection Functions
# ---------------------------------------------------------------------------

def detect_sleep_performance_correlation(
    wellness_data: list[dict],
    activities: list[dict],
) -> dict:
    """Correlate sleep hours/quality with *next-day* training performance.

    Pairs each night's sleep with the first workout on the following day.
    Computes Pearson-r for sleep-hours vs TSS and sleep-hours vs IF%.

    Returns
    -------
    dict
        correlation_tss : float | None
        correlation_if  : float | None
        strength        : str   ("strong" / "moderate" / "weak" / "insufficient_data")
        insight         : str
        sample_size     : int
    """
    # Build date -> sleep mapping
    sleep_by_date: dict[str, float] = {}
    quality_by_date: dict[str, float | None] = {}
    for w in wellness_data:
        sh = _sleep_hours(w)
        if sh is not None and sh > 0:
            sleep_by_date[_wellness_date(w)] = sh
            quality_by_date[_wellness_date(w)] = _safe_float(w.get("sleepScore"), default=0.0) or None

    # Build date -> best activity metrics
    activity_by_date: dict[str, dict] = {}
    for a in activities:
        if not a.get("type"):
            continue
        d = _activity_date(a)
        tss = _safe_float(a.get("icu_training_load"))
        if_pct = _safe_float(a.get("icu_intensity"))
        if d not in activity_by_date or tss > _safe_float(activity_by_date[d].get("tss")):
            activity_by_date[d] = {"tss": tss, "if_pct": if_pct}

    # Pair: sleep on date D -> performance on date D+1
    sleep_vals: list[float] = []
    tss_vals: list[float] = []
    if_vals: list[float] = []

    sorted_dates = sorted(sleep_by_date.keys())
    for date_str in sorted_dates:
        try:
            next_day = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        except ValueError:
            continue
        if next_day in activity_by_date:
            perf = activity_by_date[next_day]
            tss = perf["tss"]
            if_pct = perf["if_pct"]
            if tss > 0:
                sleep_vals.append(sleep_by_date[date_str])
                tss_vals.append(tss)
                if_vals.append(if_pct)

    r_tss = _pearson_r(sleep_vals, tss_vals)
    r_if = _pearson_r(sleep_vals, if_vals)

    # Determine overall strength from the stronger signal
    best_r = max(abs(r_tss or 0), abs(r_if or 0))
    if len(sleep_vals) < 5:
        strength = "insufficient_data"
    elif best_r >= 0.5:
        strength = "strong"
    elif best_r >= 0.3:
        strength = "moderate"
    else:
        strength = "weak"

    # Build insight text
    if strength == "insufficient_data":
        insight = "Not enough paired sleep-performance data to detect a pattern yet."
    elif strength == "strong":
        direction = "better" if (r_tss or 0) > 0 else "worse"
        insight = (
            f"Strong link detected: more sleep correlates with {direction} training output "
            f"(r={best_r:.2f}, n={len(sleep_vals)}). Prioritize sleep before key sessions."
        )
    elif strength == "moderate":
        insight = (
            f"Moderate sleep-performance link (r={best_r:.2f}, n={len(sleep_vals)}). "
            "Sleep quality matters for your training — keep tracking."
        )
    else:
        insight = (
            f"Weak sleep-performance correlation (r={best_r:.2f}, n={len(sleep_vals)}). "
            "Other factors may dominate your day-to-day performance variation."
        )

    return {
        "correlation_tss": round(r_tss, 3) if r_tss is not None else None,
        "correlation_if": round(r_if, 3) if r_if is not None else None,
        "strength": strength,
        "insight": insight,
        "sample_size": len(sleep_vals),
    }


def detect_hrv_trends(
    wellness_data: list[dict],
    window: int = 7,
) -> dict:
    """Detect whether HRV is trending up / down / stable over *window* days.

    Also computes a coefficient of variation to assess day-to-day volatility,
    and flags a declining trend when the slope is significantly negative.

    Returns
    -------
    dict
        trend       : str   ("rising" / "declining" / "stable" / "insufficient_data")
        slope       : float | None   (units per day)
        avg         : float | None
        cv          : float | None   (coefficient of variation, %)
        latest      : float | None
        flag        : str | None     (warning message if declining)
        values      : list[float]    (the HRV readings in the window)
    """
    # Collect the last *window* HRV readings that are present
    hrv_entries: list[tuple[str, float]] = []
    for w in wellness_data:
        hrv = w.get("hrv")
        if hrv is not None and _safe_float(hrv) > 0:
            hrv_entries.append((_wellness_date(w), float(hrv)))

    # Take only the most recent *window* entries
    hrv_entries = hrv_entries[-window:]
    values = [v for _, v in hrv_entries]

    if len(values) < 3:
        return {
            "trend": "insufficient_data",
            "slope": None,
            "avg": None,
            "cv": None,
            "latest": values[-1] if values else None,
            "flag": None,
            "values": values,
        }

    avg = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) >= 2 else 0
    cv = (stdev / avg * 100) if avg > 0 else 0
    slope = _linear_slope(values)
    latest = values[-1]

    # Trend classification — slope is per-day change
    if slope is not None and abs(slope) > 0.5:
        trend = "rising" if slope > 0 else "declining"
    else:
        trend = "stable"

    flag = None
    if trend == "declining":
        pct_drop = abs(slope * len(values)) / avg * 100 if avg > 0 else 0
        flag = (
            f"HRV declining over the last {len(values)} days "
            f"(slope {slope:+.1f}/day, ~{pct_drop:.0f}% total drop). "
            "Consider reducing training load or improving recovery."
        )

    # High volatility flag
    if cv > 20 and flag is None:
        flag = (
            f"HRV is highly variable (CV={cv:.0f}%). "
            "This can indicate inconsistent recovery or measurement timing."
        )

    return {
        "trend": trend,
        "slope": round(slope, 2) if slope is not None else None,
        "avg": round(avg, 1),
        "cv": round(cv, 1),
        "latest": round(latest, 1),
        "flag": flag,
        "values": [round(v, 1) for v in values],
    }


def detect_training_response(
    wellness_data: list[dict],
    activities: list[dict],
) -> dict:
    """How does the athlete's HRV respond to hard training days?

    A "hard day" is defined as one where TSS exceeds the median TSS by >= 30%.
    We measure the HRV delta on the following day(s) and the number of days
    until HRV returns to the rolling 7-day average.

    Returns
    -------
    dict
        avg_hrv_drop      : float | None   (HRV points lost next day after hard day)
        avg_recovery_days : float | None   (days until HRV returns to baseline)
        hard_day_count    : int
        pattern           : str            ("quick_recoverer" / "slow_recoverer" / "resilient" / "insufficient_data")
        insight           : str
    """
    # Build date-keyed lookups
    hrv_by_date: dict[str, float] = {}
    for w in wellness_data:
        hrv = w.get("hrv")
        if hrv is not None and float(hrv) > 0:
            hrv_by_date[_wellness_date(w)] = float(hrv)

    daily_tss: dict[str, float] = defaultdict(float)
    for a in activities:
        if not a.get("type"):
            continue
        d = _activity_date(a)
        daily_tss[d] += _safe_float(a.get("icu_training_load"))

    all_tss = [v for v in daily_tss.values() if v > 0]
    if len(all_tss) < 5 or len(hrv_by_date) < 7:
        return {
            "avg_hrv_drop": None,
            "avg_recovery_days": None,
            "hard_day_count": 0,
            "pattern": "insufficient_data",
            "insight": "Not enough data to assess training response pattern.",
        }

    median_tss = statistics.median(all_tss)
    hard_threshold = median_tss * 1.3

    hrv_drops: list[float] = []
    recovery_days_list: list[int] = []
    sorted_hrv_dates = sorted(hrv_by_date.keys())

    for date_str, tss in daily_tss.items():
        if tss < hard_threshold:
            continue
        # HRV on that day
        hrv_today = hrv_by_date.get(date_str)
        if hrv_today is None:
            continue

        # Next-day HRV drop
        try:
            next_day = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        except ValueError:
            continue
        hrv_next = hrv_by_date.get(next_day)
        if hrv_next is not None:
            hrv_drops.append(hrv_today - hrv_next)

        # Days to recover — compute a rolling 7-day mean as baseline
        baseline_dates = [
            d for d in sorted_hrv_dates
            if d <= date_str and d >= (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
        ]
        if len(baseline_dates) < 3:
            continue
        baseline_mean = statistics.mean(hrv_by_date[d] for d in baseline_dates)

        # Walk forward from next day to find recovery
        days_to_recover = 0
        for offset in range(1, 8):
            check_date = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=offset)).strftime("%Y-%m-%d")
            check_hrv = hrv_by_date.get(check_date)
            if check_hrv is not None and check_hrv >= baseline_mean * 0.95:
                days_to_recover = offset
                break
        else:
            days_to_recover = 7  # Did not recover within a week

        recovery_days_list.append(days_to_recover)

    hard_day_count = len(hrv_drops)
    avg_drop = statistics.mean(hrv_drops) if hrv_drops else None
    avg_recovery = statistics.mean(recovery_days_list) if recovery_days_list else None

    # Classify
    if hard_day_count < 3:
        pattern = "insufficient_data"
        insight = f"Only {hard_day_count} hard training days recorded — need more data for a reliable pattern."
    elif avg_drop is not None and avg_drop < 2:
        pattern = "resilient"
        insight = (
            f"Your HRV barely drops after hard days (avg drop {avg_drop:.1f} pts). "
            "Your body handles high training loads well."
        )
    elif avg_recovery is not None and avg_recovery <= 1.5:
        pattern = "quick_recoverer"
        insight = (
            f"HRV drops {avg_drop:.1f} pts after hard days but recovers in ~{avg_recovery:.1f} days. "
            "You can handle back-to-back hard sessions."
        )
    elif avg_recovery is not None and avg_recovery > 2.5:
        pattern = "slow_recoverer"
        insight = (
            f"HRV drops {avg_drop:.1f} pts after hard days and takes ~{avg_recovery:.1f} days to recover. "
            "Space hard sessions with at least 2 easy days between."
        )
    else:
        pattern = "quick_recoverer"
        insight = (
            f"Average HRV drop of {avg_drop:.1f} pts after hard days, "
            f"recovering in ~{avg_recovery:.1f} days. Normal response."
        )

    return {
        "avg_hrv_drop": round(avg_drop, 1) if avg_drop is not None else None,
        "avg_recovery_days": round(avg_recovery, 1) if avg_recovery is not None else None,
        "hard_day_count": hard_day_count,
        "pattern": pattern,
        "insight": insight,
    }


def detect_optimal_training_days(activities: list[dict]) -> dict:
    """Which days of the week tend to produce the best workouts?

    Evaluates by highest average IF% and most consistent high-quality output.

    Returns
    -------
    dict
        by_day      : dict[str, dict]  (day_name -> {avg_if, avg_tss, count, avg_distance_km})
        best_day    : str | None
        worst_day   : str | None
        insight     : str
    """
    DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    day_metrics: dict[int, list[dict]] = defaultdict(list)

    for a in activities:
        if not a.get("type"):
            continue
        date_str = _activity_date(a)
        if not date_str:
            continue
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        tss = _safe_float(a.get("icu_training_load"))
        if_pct = _safe_float(a.get("icu_intensity"))
        dist_km = _safe_float(a.get("distance")) / 1000
        day_metrics[dt.weekday()].append({
            "tss": tss,
            "if_pct": if_pct,
            "distance_km": dist_km,
        })

    by_day: dict[str, dict] = {}
    for dow in range(7):
        entries = day_metrics.get(dow, [])
        name = DAY_NAMES[dow]
        if not entries:
            by_day[name] = {"avg_if": 0, "avg_tss": 0, "count": 0, "avg_distance_km": 0}
            continue
        ifs = [e["if_pct"] for e in entries if e["if_pct"] > 0]
        tsses = [e["tss"] for e in entries if e["tss"] > 0]
        dists = [e["distance_km"] for e in entries]
        by_day[name] = {
            "avg_if": round(statistics.mean(ifs), 1) if ifs else 0,
            "avg_tss": round(statistics.mean(tsses), 1) if tsses else 0,
            "count": len(entries),
            "avg_distance_km": round(statistics.mean(dists), 1) if dists else 0,
        }

    # Find best / worst by average IF among days with >= 2 workouts
    qualified = {k: v for k, v in by_day.items() if v["count"] >= 2 and v["avg_if"] > 0}
    best_day = max(qualified, key=lambda k: qualified[k]["avg_if"]) if qualified else None
    worst_day = min(qualified, key=lambda k: qualified[k]["avg_if"]) if qualified else None

    if best_day and worst_day and best_day != worst_day:
        insight = (
            f"Your best training days tend to be {best_day}s "
            f"(avg IF {by_day[best_day]['avg_if']}%, n={by_day[best_day]['count']}). "
            f"Weakest output on {worst_day}s "
            f"(avg IF {by_day[worst_day]['avg_if']}%, n={by_day[worst_day]['count']}). "
            "Consider scheduling key sessions on your strongest days."
        )
    elif best_day:
        insight = (
            f"Your best training days are {best_day}s "
            f"(avg IF {by_day[best_day]['avg_if']}%, n={by_day[best_day]['count']})."
        )
    else:
        insight = "Not enough data to determine optimal training days."

    return {
        "by_day": by_day,
        "best_day": best_day,
        "worst_day": worst_day,
        "insight": insight,
    }


def detect_fatigue_accumulation(
    wellness_data: list[dict],
    activities: list[dict],
) -> dict:
    """Is fatigue accumulating faster than the athlete can recover?

    Compares ATL ramp rate against recovery signals (HRV trend, sleep trend,
    RHR trend).  When load is rising but recovery markers are declining, the
    athlete is accumulating fatigue.

    Returns
    -------
    dict
        atl_ramp        : float | None      (ATL change per day over last 7 days)
        hrv_slope       : float | None
        sleep_slope     : float | None
        rhr_slope       : float | None
        balance         : str               ("balanced" / "overreaching" / "underloading" / "insufficient_data")
        fatigue_score   : int               (0-100; higher = more fatigued)
        insight         : str
    """
    recent = wellness_data[-7:] if len(wellness_data) >= 7 else wellness_data

    if len(recent) < 4:
        return {
            "atl_ramp": None,
            "hrv_slope": None,
            "sleep_slope": None,
            "rhr_slope": None,
            "balance": "insufficient_data",
            "fatigue_score": 0,
            "insight": "Not enough recent wellness data to assess fatigue accumulation.",
        }

    # ATL ramp
    atl_values = [_safe_float(w.get("atl")) for w in recent]
    atl_slope = _linear_slope(atl_values)

    # HRV slope
    hrv_values = [float(w["hrv"]) for w in recent if w.get("hrv") is not None and float(w.get("hrv", 0)) > 0]
    hrv_slope = _linear_slope(hrv_values) if len(hrv_values) >= 3 else None

    # Sleep slope
    sleep_values = []
    for w in recent:
        sh = _sleep_hours(w)
        if sh is not None and sh > 0:
            sleep_values.append(sh)
    sleep_slope = _linear_slope(sleep_values) if len(sleep_values) >= 3 else None

    # RHR slope (rising RHR = bad)
    rhr_values = [float(w["restingHR"]) for w in recent if w.get("restingHR") is not None and float(w.get("restingHR", 0)) > 0]
    rhr_slope = _linear_slope(rhr_values) if len(rhr_values) >= 3 else None

    # Composite fatigue score (0-100)
    score = 50  # Start neutral

    # ATL rising = more acute load
    if atl_slope is not None:
        if atl_slope > 2:
            score += 15
        elif atl_slope > 1:
            score += 8
        elif atl_slope < -1:
            score -= 10

    # HRV declining = worse recovery
    if hrv_slope is not None:
        if hrv_slope < -1:
            score += 15
        elif hrv_slope < -0.5:
            score += 8
        elif hrv_slope > 1:
            score -= 10

    # Sleep declining = worse recovery
    if sleep_slope is not None:
        if sleep_slope < -0.2:
            score += 10
        elif sleep_slope < -0.1:
            score += 5
        elif sleep_slope > 0.1:
            score -= 5

    # RHR rising = worse recovery
    if rhr_slope is not None:
        if rhr_slope > 1:
            score += 10
        elif rhr_slope > 0.5:
            score += 5
        elif rhr_slope < -0.5:
            score -= 5

    # TSB check — deep negative TSB is a strong fatigue signal
    latest = wellness_data[-1] if wellness_data else {}
    tsb = _safe_float(latest.get("ctl")) - _safe_float(latest.get("atl"))
    if tsb < -30:
        score += 10
    elif tsb < -20:
        score += 5

    score = max(0, min(100, score))

    # Classify balance
    load_rising = atl_slope is not None and atl_slope > 0.5
    recovery_declining = (
        (hrv_slope is not None and hrv_slope < -0.3)
        or (sleep_slope is not None and sleep_slope < -0.1)
        or (rhr_slope is not None and rhr_slope > 0.3)
    )

    if load_rising and recovery_declining:
        balance = "overreaching"
    elif not load_rising and not recovery_declining:
        balance = "balanced"
    elif not load_rising and recovery_declining:
        balance = "overreaching"  # Recovery declining even without load increase
    else:
        balance = "underloading" if score < 30 else "balanced"

    # Insight
    if balance == "overreaching":
        parts = []
        if atl_slope and atl_slope > 0.5:
            parts.append(f"ATL rising ({atl_slope:+.1f}/day)")
        if hrv_slope and hrv_slope < -0.3:
            parts.append(f"HRV declining ({hrv_slope:+.1f}/day)")
        if sleep_slope and sleep_slope < -0.1:
            parts.append(f"sleep declining ({sleep_slope:+.2f}h/day)")
        if rhr_slope and rhr_slope > 0.3:
            parts.append(f"RHR rising ({rhr_slope:+.1f}/day)")
        detail = ", ".join(parts) if parts else "multiple recovery markers declining"
        insight = (
            f"Fatigue accumulating: {detail}. "
            f"Fatigue score: {score}/100. "
            "Consider a recovery day or reduced volume."
        )
    elif balance == "underloading":
        insight = (
            f"Training load is light and recovery signals are strong (fatigue score {score}/100). "
            "You have capacity for more load if the plan calls for it."
        )
    else:
        insight = (
            f"Training load and recovery are in balance (fatigue score {score}/100). "
            "Continue as planned."
        )

    return {
        "atl_ramp": round(atl_slope, 2) if atl_slope is not None else None,
        "hrv_slope": round(hrv_slope, 2) if hrv_slope is not None else None,
        "sleep_slope": round(sleep_slope, 3) if sleep_slope is not None else None,
        "rhr_slope": round(rhr_slope, 2) if rhr_slope is not None else None,
        "balance": balance,
        "fatigue_score": score,
        "insight": insight,
    }


def build_athlete_model(
    wellness_data: list[dict],
    activities: list[dict],
    athlete_config: dict,
) -> dict:
    """Build a comprehensive snapshot of the athlete's profile and current state.

    Aggregates training patterns, recovery characteristics, sleep patterns,
    strengths, and areas for improvement into a single dict for downstream
    consumption by the coaching engine.

    Parameters
    ----------
    wellness_data : list[dict]
        Daily wellness entries from Intervals.icu (recent 14-30 days).
    activities : list[dict]
        Activity entries from Intervals.icu (recent 14-30 days).
    athlete_config : dict
        Athlete profile fields (or AthleteConfig.__dict__).

    Returns
    -------
    dict with keys: training_patterns, recovery_characteristics,
    sleep_patterns, strengths, weaknesses, current_form, summary.
    """
    config = athlete_config if isinstance(athlete_config, dict) else athlete_config.__dict__

    # --- Training patterns ---
    sport_volumes: dict[str, dict] = defaultdict(lambda: {"count": 0, "total_tss": 0.0, "total_km": 0.0, "total_min": 0})
    weekly_tss: dict[str, float] = defaultdict(float)
    all_tss: list[float] = []

    for a in activities:
        if not a.get("type"):
            continue
        sport = _classify_sport(a)
        tss = _safe_float(a.get("icu_training_load"))
        dist_km = _safe_float(a.get("distance")) / 1000
        dur_min = _safe_float(a.get("moving_time")) / 60
        sport_volumes[sport]["count"] += 1
        sport_volumes[sport]["total_tss"] += tss
        sport_volumes[sport]["total_km"] += dist_km
        sport_volumes[sport]["total_min"] += dur_min
        if tss > 0:
            all_tss.append(tss)
        # Weekly aggregation
        date_str = _activity_date(a)
        if date_str:
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                week_key = dt.strftime("%Y-W%U")
                weekly_tss[week_key] += tss
            except ValueError:
                pass

    avg_session_tss = statistics.mean(all_tss) if all_tss else 0
    weekly_values = list(weekly_tss.values()) if weekly_tss else [0]
    avg_weekly_tss = statistics.mean(weekly_values) if weekly_values else 0

    training_patterns = {
        "sport_distribution": {
            k: {
                "count": v["count"],
                "total_tss": round(v["total_tss"]),
                "total_km": round(v["total_km"], 1),
                "total_hours": round(v["total_min"] / 60, 1),
            }
            for k, v in sport_volumes.items()
        },
        "avg_session_tss": round(avg_session_tss, 1),
        "avg_weekly_tss": round(avg_weekly_tss),
        "total_sessions": sum(v["count"] for v in sport_volumes.values()),
        "sessions_per_week": round(sum(v["count"] for v in sport_volumes.values()) / max(len(weekly_tss), 1), 1),
    }

    # --- Recovery characteristics ---
    hrv_trend = detect_hrv_trends(wellness_data, window=7)
    training_resp = detect_training_response(wellness_data, activities)
    recovery_characteristics = {
        "hrv_baseline": _safe_float(config.get("hrv_baseline", 57)),
        "hrv_current_avg": hrv_trend.get("avg"),
        "hrv_trend": hrv_trend.get("trend"),
        "hrv_cv": hrv_trend.get("cv"),
        "recovery_pattern": training_resp.get("pattern"),
        "avg_hrv_drop_after_hard": training_resp.get("avg_hrv_drop"),
        "avg_recovery_days": training_resp.get("avg_recovery_days"),
    }

    # --- Sleep patterns ---
    sleep_hours_list: list[float] = []
    sleep_scores: list[float] = []
    for w in wellness_data:
        sh = _sleep_hours(w)
        if sh is not None and sh > 0:
            sleep_hours_list.append(sh)
        score = _safe_float(w.get("sleepScore"))
        if score > 0:
            sleep_scores.append(score)

    target = _safe_float(config.get("sleep_target_hours", 7.5))
    nights_below_target = sum(1 for h in sleep_hours_list if h < target) if sleep_hours_list else 0
    nights_below_6 = sum(1 for h in sleep_hours_list if h < 6) if sleep_hours_list else 0

    sleep_patterns = {
        "avg_hours": round(statistics.mean(sleep_hours_list), 1) if sleep_hours_list else None,
        "std_hours": round(statistics.stdev(sleep_hours_list), 2) if len(sleep_hours_list) >= 2 else None,
        "min_hours": round(min(sleep_hours_list), 1) if sleep_hours_list else None,
        "max_hours": round(max(sleep_hours_list), 1) if sleep_hours_list else None,
        "target_hours": target,
        "nights_below_target": nights_below_target,
        "nights_below_6h": nights_below_6,
        "consistency_score": round(100 - (statistics.stdev(sleep_hours_list) / target * 100), 1) if len(sleep_hours_list) >= 2 else None,
        "avg_sleep_score": round(statistics.mean(sleep_scores), 1) if sleep_scores else None,
    }

    # --- Current form ---
    latest = wellness_data[-1] if wellness_data else {}
    ctl = _safe_float(latest.get("ctl"))
    atl = _safe_float(latest.get("atl"))
    tsb = ctl - atl
    rhr = _safe_float(latest.get("restingHR"))
    rhr_baseline = _safe_float(config.get("rhr_baseline", 42))

    current_form = {
        "ctl": round(ctl, 1),
        "atl": round(atl, 1),
        "tsb": round(tsb, 1),
        "rhr": rhr,
        "rhr_vs_baseline": round(rhr - rhr_baseline, 1) if rhr > 0 else None,
        "latest_hrv": hrv_trend.get("latest"),
        "phase": _classify_phase(tsb, ctl),
    }

    # --- Strengths and weaknesses ---
    strengths: list[str] = []
    weaknesses: list[str] = []

    if training_resp.get("pattern") == "quick_recoverer":
        strengths.append("Quick recovery between hard sessions")
    elif training_resp.get("pattern") == "resilient":
        strengths.append("HRV resilient to high training loads")
    elif training_resp.get("pattern") == "slow_recoverer":
        weaknesses.append("Slow HRV recovery after hard sessions — needs extra rest days")

    if sleep_patterns.get("avg_hours") and sleep_patterns["avg_hours"] >= target:
        strengths.append(f"Consistently good sleep ({sleep_patterns['avg_hours']}h avg)")
    elif sleep_patterns.get("avg_hours") and sleep_patterns["avg_hours"] < target - 0.5:
        weaknesses.append(f"Sleep deficit ({sleep_patterns['avg_hours']}h avg vs {target}h target)")

    if sleep_patterns.get("consistency_score") and sleep_patterns["consistency_score"] >= 85:
        strengths.append("Consistent sleep schedule")
    elif sleep_patterns.get("consistency_score") and sleep_patterns["consistency_score"] < 70:
        weaknesses.append("Irregular sleep schedule — high night-to-night variation")

    if hrv_trend.get("cv") and hrv_trend["cv"] < 10:
        strengths.append("Stable HRV — good autonomic regulation")
    elif hrv_trend.get("cv") and hrv_trend["cv"] > 20:
        weaknesses.append("High HRV variability — inconsistent recovery")

    if training_patterns["sessions_per_week"] >= 5:
        strengths.append(f"High training frequency ({training_patterns['sessions_per_week']}/week)")
    elif training_patterns["sessions_per_week"] < 3:
        weaknesses.append(f"Low training frequency ({training_patterns['sessions_per_week']}/week)")

    if ctl > 50:
        strengths.append(f"Good aerobic base (CTL {ctl:.0f})")
    elif ctl < 25:
        weaknesses.append(f"Low aerobic fitness (CTL {ctl:.0f})")

    # --- Summary ---
    summary = _build_model_summary(
        training_patterns, recovery_characteristics, sleep_patterns,
        current_form, strengths, weaknesses, config,
    )

    return {
        "training_patterns": training_patterns,
        "recovery_characteristics": recovery_characteristics,
        "sleep_patterns": sleep_patterns,
        "current_form": current_form,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "summary": summary,
    }


def _classify_phase(tsb: float, ctl: float) -> str:
    """Classify the current training phase from TSB and CTL."""
    if tsb > 15:
        return "fresh" if ctl > 30 else "detrained"
    if tsb > 0:
        return "recovered"
    if tsb > -15:
        return "productive_training"
    if tsb > -30:
        return "functional_overreaching"
    return "excessive_fatigue"


def _build_model_summary(
    training: dict,
    recovery: dict,
    sleep: dict,
    form: dict,
    strengths: list[str],
    weaknesses: list[str],
    config: dict,
) -> str:
    """Build a concise text summary of the athlete model."""
    lines: list[str] = []

    name = config.get("name", "Athlete")
    lines.append(f"{name}'s Athlete Model")
    lines.append(f"Current phase: {form['phase']} (CTL {form['ctl']}, TSB {form['tsb']})")

    if training.get("sport_distribution"):
        sports = ", ".join(
            f"{k} ({v['count']}x, {v['total_hours']}h)"
            for k, v in training["sport_distribution"].items()
        )
        lines.append(f"Training: {training['sessions_per_week']} sessions/week — {sports}")

    if recovery.get("recovery_pattern") and recovery["recovery_pattern"] != "insufficient_data":
        lines.append(f"Recovery: {recovery['recovery_pattern']} (HRV trend: {recovery['hrv_trend']})")

    if sleep.get("avg_hours"):
        lines.append(f"Sleep: {sleep['avg_hours']}h avg (target {sleep['target_hours']}h)")

    if strengths:
        lines.append("Strengths: " + "; ".join(strengths))
    if weaknesses:
        lines.append("Areas to improve: " + "; ".join(weaknesses))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------

def analyze_patterns(
    wellness_data: list[dict],
    activities: list[dict],
    athlete_config: dict,
) -> dict:
    """Run all pattern detectors and return combined insights.

    This is the primary entry point.  Each detector is run independently and
    wrapped in try/except so that a failure in one does not block others.

    Returns
    -------
    dict with keys: sleep_performance, hrv_trends, training_response,
    optimal_days, fatigue, athlete_model, and a top-level 'summary' string.
    """
    results: dict = {}

    # Sleep-performance correlation
    try:
        results["sleep_performance"] = detect_sleep_performance_correlation(wellness_data, activities)
    except Exception:
        log.exception("sleep_performance detection failed")
        results["sleep_performance"] = {"strength": "error", "insight": "Analysis unavailable."}

    # HRV trends
    try:
        results["hrv_trends"] = detect_hrv_trends(wellness_data)
    except Exception:
        log.exception("hrv_trends detection failed")
        results["hrv_trends"] = {"trend": "error", "flag": None}

    # Training response
    try:
        results["training_response"] = detect_training_response(wellness_data, activities)
    except Exception:
        log.exception("training_response detection failed")
        results["training_response"] = {"pattern": "error", "insight": "Analysis unavailable."}

    # Optimal training days
    try:
        results["optimal_days"] = detect_optimal_training_days(activities)
    except Exception:
        log.exception("optimal_days detection failed")
        results["optimal_days"] = {"insight": "Analysis unavailable."}

    # Fatigue accumulation
    try:
        results["fatigue"] = detect_fatigue_accumulation(wellness_data, activities)
    except Exception:
        log.exception("fatigue detection failed")
        results["fatigue"] = {"balance": "error", "fatigue_score": 0, "insight": "Analysis unavailable."}

    # Full athlete model
    try:
        results["athlete_model"] = build_athlete_model(wellness_data, activities, athlete_config)
    except Exception:
        log.exception("athlete_model build failed")
        results["athlete_model"] = {"summary": "Athlete model unavailable."}

    # Top-level summary
    results["summary"] = _compile_summary(results)

    return results


def _compile_summary(results: dict) -> str:
    """Compile a compact summary from all analysis results."""
    lines: list[str] = []

    hrv = results.get("hrv_trends", {})
    if hrv.get("flag"):
        lines.append(hrv["flag"])

    fatigue = results.get("fatigue", {})
    if fatigue.get("balance") == "overreaching":
        lines.append(fatigue.get("insight", ""))
    elif fatigue.get("fatigue_score", 0) > 70:
        lines.append(f"High fatigue score ({fatigue['fatigue_score']}/100). {fatigue.get('insight', '')}")

    sleep_perf = results.get("sleep_performance", {})
    if sleep_perf.get("strength") in ("strong", "moderate"):
        lines.append(sleep_perf.get("insight", ""))

    training_resp = results.get("training_response", {})
    if training_resp.get("pattern") not in ("insufficient_data", "error", None):
        lines.append(training_resp.get("insight", ""))

    return "\n".join(lines) if lines else "No significant patterns flagged."


def format_intelligence_context(analysis: dict) -> str:
    """Format pattern analysis results for injection into the coaching engine system prompt.

    Produces a concise block of text that the coaching LLM can use
    to personalise responses.
    """
    sections: list[str] = ["INTELLIGENCE ANALYSIS:"]

    # Athlete model summary
    model = analysis.get("athlete_model", {})
    if model.get("summary"):
        sections.append(model["summary"])

    # Current fatigue balance
    fatigue = analysis.get("fatigue", {})
    if fatigue.get("balance") and fatigue["balance"] != "insufficient_data":
        sections.append(
            f"Fatigue balance: {fatigue['balance']} "
            f"(score {fatigue.get('fatigue_score', '?')}/100)"
        )

    # HRV trend
    hrv = analysis.get("hrv_trends", {})
    if hrv.get("trend") and hrv["trend"] != "insufficient_data":
        parts = [f"HRV trend: {hrv['trend']}"]
        if hrv.get("avg"):
            parts.append(f"avg {hrv['avg']}")
        if hrv.get("latest"):
            parts.append(f"latest {hrv['latest']}")
        if hrv.get("cv"):
            parts.append(f"CV {hrv['cv']}%")
        sections.append(" | ".join(parts))

    # Training response
    resp = analysis.get("training_response", {})
    if resp.get("pattern") and resp["pattern"] not in ("insufficient_data", "error"):
        sections.append(
            f"Training response: {resp['pattern']} "
            f"(avg HRV drop {resp.get('avg_hrv_drop', '?')} pts, "
            f"recovery {resp.get('avg_recovery_days', '?')} days)"
        )

    # Sleep-performance
    sp = analysis.get("sleep_performance", {})
    if sp.get("strength") and sp["strength"] not in ("insufficient_data", "error"):
        sections.append(f"Sleep-performance link: {sp['strength']} ({sp.get('insight', '')})")

    # Optimal days
    od = analysis.get("optimal_days", {})
    if od.get("best_day"):
        sections.append(f"Best training day: {od['best_day']} | Worst: {od.get('worst_day', 'N/A')}")

    # Strengths and weaknesses
    strengths = model.get("strengths", [])
    weaknesses = model.get("weaknesses", [])
    if strengths:
        sections.append("Strengths: " + "; ".join(strengths))
    if weaknesses:
        sections.append("Areas to improve: " + "; ".join(weaknesses))

    # Top-level flags
    summary = analysis.get("summary", "")
    if summary and summary != "No significant patterns flagged.":
        sections.append(f"\nFLAGS:\n{summary}")

    return "\n".join(sections)
