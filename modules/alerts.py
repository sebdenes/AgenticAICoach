"""Proactive alert system — detect risks, opportunities, and actionable conditions."""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

log = logging.getLogger("coach.alerts")


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


def _sleep_hours(entry: dict) -> float | None:
    """Extract sleep hours from a wellness dict, returning None when absent."""
    secs = entry.get("sleepSecs", 0) or entry.get("sleep_seconds", 0) or 0
    return secs / 3600 if secs > 0 else None


def _wellness_date(entry: dict) -> str:
    """Return the YYYY-MM-DD date string from a wellness dict."""
    return (entry.get("id", entry.get("date", "")) or "")[:10]


def _activity_date(activity: dict) -> str:
    """Return the YYYY-MM-DD date string from an activity dict."""
    return (activity.get("start_date_local", activity.get("date", "")) or "")[:10]


def _activity_tss(activity: dict) -> float:
    """Extract training stress score from an activity."""
    return _safe_float(activity.get("icu_training_load", 0) or activity.get("tss", 0) or 0)


def _rhr(entry: dict) -> float | None:
    """Extract resting heart rate from a wellness dict."""
    val = entry.get("restingHR") or entry.get("rhr")
    if val is None:
        return None
    f = _safe_float(val, default=-1)
    return f if f > 0 else None


def _hrv(entry: dict) -> float | None:
    """Extract HRV from a wellness dict."""
    val = entry.get("hrv")
    if val is None:
        return None
    f = _safe_float(val, default=-1)
    return f if f > 0 else None


def _linear_slope(ys: list[float]) -> float | None:
    """Return the slope of a simple least-squares fit of *ys* against [0..n).

    Returns None when fewer than 3 data points.
    """
    n = len(ys)
    if n < 3:
        return None
    mean_x = (n - 1) / 2
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in enumerate(ys))
    denom = sum((x - mean_x) ** 2 for x in range(n))
    if denom == 0:
        return None
    return num / denom


def _recent_wellness(wellness: list[dict], days: int) -> list[dict]:
    """Return the last *days* wellness entries (assumes chronological order)."""
    return wellness[-days:] if len(wellness) >= days else list(wellness)


def _make_alert(
    alert_type: str,
    severity: str,
    title: str,
    message: str,
    data: dict | None = None,
) -> dict:
    """Construct a standardised alert dict."""
    return {
        "type": alert_type,
        "severity": severity,
        "title": title,
        "message": message,
        "data": data or {},
    }


def _days_since_last_activity(activities: list[dict]) -> int | None:
    """Return the number of days since the most recent activity, or None."""
    if not activities:
        return None
    latest_date: str | None = None
    for a in activities:
        d = _activity_date(a)
        if d and (latest_date is None or d > latest_date):
            latest_date = d
    if latest_date is None:
        return None
    try:
        dt = datetime.strptime(latest_date, "%Y-%m-%d")
        return (datetime.now() - dt).days
    except ValueError:
        return None


SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


# ---------------------------------------------------------------------------
# Individual Alert Detectors
# ---------------------------------------------------------------------------

def _detect_overtraining_risk(
    wellness: list[dict],
    activities: list[dict],
) -> list[dict]:
    """ATL rising while HRV declining or RHR rising over a 7-day window.

    Severity escalates to *critical* when both HRV and RHR are trending badly
    alongside rising ATL.
    """
    alerts: list[dict] = []
    recent = _recent_wellness(wellness, 7)
    if len(recent) < 4:
        return alerts

    # ATL trend
    atl_values = [_safe_float(w.get("atl")) for w in recent if w.get("atl") is not None]
    atl_slope = _linear_slope(atl_values) if len(atl_values) >= 3 else None

    # HRV trend
    hrv_values = [v for w in recent if (v := _hrv(w)) is not None]
    hrv_slope = _linear_slope(hrv_values) if len(hrv_values) >= 3 else None

    # RHR trend
    rhr_values = [v for w in recent if (v := _rhr(w)) is not None]
    rhr_slope = _linear_slope(rhr_values) if len(rhr_values) >= 3 else None

    atl_rising = atl_slope is not None and atl_slope > 0.5
    hrv_declining = hrv_slope is not None and hrv_slope < -0.3
    rhr_rising = rhr_slope is not None and rhr_slope > 0.3

    if not (atl_rising and (hrv_declining or rhr_rising)):
        return alerts

    # Severity: critical when both recovery markers are worsening
    severity = "critical" if (hrv_declining and rhr_rising) else "warning"

    detail_parts: list[str] = [f"ATL rising ({atl_slope:+.1f}/day)"]
    if hrv_declining:
        detail_parts.append(f"HRV declining ({hrv_slope:+.1f}/day)")
    if rhr_rising:
        detail_parts.append(f"RHR rising ({rhr_slope:+.1f}/day)")

    alerts.append(_make_alert(
        alert_type="overtraining_risk",
        severity=severity,
        title="Overtraining risk detected",
        message=(
            f"Training load is increasing while recovery markers are worsening: "
            f"{', '.join(detail_parts)}. Consider reducing volume or adding a rest day."
        ),
        data={
            "atl_slope": round(atl_slope, 2) if atl_slope is not None else None,
            "hrv_slope": round(hrv_slope, 2) if hrv_slope is not None else None,
            "rhr_slope": round(rhr_slope, 2) if rhr_slope is not None else None,
            "window_days": len(recent),
        },
    ))
    return alerts


def _detect_sleep_crisis(wellness: list[dict]) -> list[dict]:
    """7-day average sleep < 6h or 3+ consecutive nights < 5h.

    Severity is always *critical* for a sleep crisis.
    """
    alerts: list[dict] = []
    recent = _recent_wellness(wellness, 7)
    if len(recent) < 3:
        return alerts

    sleep_vals: list[float] = []
    for w in recent:
        sh = _sleep_hours(w)
        if sh is not None:
            sleep_vals.append(sh)

    if not sleep_vals:
        return alerts

    avg_sleep = statistics.mean(sleep_vals)

    # Check 7-day average < 6h
    if avg_sleep < 6.0:
        alerts.append(_make_alert(
            alert_type="sleep_crisis",
            severity="critical",
            title="Sleep crisis: severe deficit",
            message=(
                f"Average sleep over the last {len(sleep_vals)} nights is only "
                f"{avg_sleep:.1f}h. This level of sleep deprivation significantly "
                f"impairs recovery, immune function, and performance. "
                f"Prioritise sleep immediately."
            ),
            data={
                "avg_sleep_hours": round(avg_sleep, 1),
                "nights_tracked": len(sleep_vals),
                "min_sleep_hours": round(min(sleep_vals), 1),
            },
        ))
        return alerts  # Don't duplicate with the consecutive check

    # Check 3+ consecutive nights < 5h
    consecutive_bad = 0
    max_consecutive_bad = 0
    for sh in sleep_vals:
        if sh < 5.0:
            consecutive_bad += 1
            max_consecutive_bad = max(max_consecutive_bad, consecutive_bad)
        else:
            consecutive_bad = 0

    if max_consecutive_bad >= 3:
        alerts.append(_make_alert(
            alert_type="sleep_crisis",
            severity="critical",
            title="Sleep crisis: consecutive poor nights",
            message=(
                f"{max_consecutive_bad} consecutive nights with less than 5 hours of sleep. "
                f"This level of accumulated sleep loss seriously impacts recovery and health. "
                f"Make sleep the top priority."
            ),
            data={
                "consecutive_nights_below_5h": max_consecutive_bad,
                "avg_sleep_hours": round(avg_sleep, 1),
            },
        ))

    return alerts


def _detect_illness_risk(
    wellness: list[dict],
    athlete_config: dict,
) -> list[dict]:
    """RHR elevated >5bpm above baseline AND HRV declining.

    Severity is *critical* when RHR elevation exceeds 10bpm, otherwise *warning*.
    """
    alerts: list[dict] = []
    recent = _recent_wellness(wellness, 7)
    if len(recent) < 3:
        return alerts

    config = athlete_config if isinstance(athlete_config, dict) else {}

    # Determine RHR baseline: from config or estimated from full history
    rhr_baseline = _safe_float(config.get("rhr_baseline"), default=0)
    if rhr_baseline <= 0:
        all_rhr = [v for w in wellness if (v := _rhr(w)) is not None]
        rhr_baseline = statistics.median(all_rhr) if len(all_rhr) >= 5 else 0

    if rhr_baseline <= 0:
        return alerts

    # Current RHR — average of last 3 days for robustness
    recent_rhr = [v for w in recent[-3:] if (v := _rhr(w)) is not None]
    if not recent_rhr:
        return alerts
    current_rhr = statistics.mean(recent_rhr)
    rhr_elevation = current_rhr - rhr_baseline

    # HRV trend
    hrv_values = [v for w in recent if (v := _hrv(w)) is not None]
    hrv_slope = _linear_slope(hrv_values) if len(hrv_values) >= 3 else None
    hrv_declining = hrv_slope is not None and hrv_slope < -0.3

    if rhr_elevation > 5 and hrv_declining:
        severity = "critical" if rhr_elevation > 10 else "warning"

        alerts.append(_make_alert(
            alert_type="illness_risk",
            severity=severity,
            title="Possible illness: elevated RHR + declining HRV",
            message=(
                f"Resting heart rate is {rhr_elevation:.0f}bpm above baseline "
                f"({current_rhr:.0f} vs {rhr_baseline:.0f} bpm) while HRV is declining "
                f"({hrv_slope:+.1f}/day). These are classic early signs of illness or "
                f"overreaching. Consider a rest day and monitor symptoms."
            ),
            data={
                "current_rhr": round(current_rhr, 1),
                "rhr_baseline": round(rhr_baseline, 1),
                "rhr_elevation": round(rhr_elevation, 1),
                "hrv_slope": round(hrv_slope, 2) if hrv_slope is not None else None,
            },
        ))

    return alerts


def _detect_detraining_risk(
    wellness: list[dict],
    activities: list[dict],
) -> list[dict]:
    """No activities in 5+ days or CTL dropped >10% in 2 weeks.

    Severity is *info* for early signs, *warning* for prolonged inactivity
    (7+ days) or large CTL drops (>15%).
    """
    alerts: list[dict] = []

    # --- Check days since last activity ---
    gap_days = _days_since_last_activity(activities)
    if gap_days is not None and gap_days >= 5:
        severity = "warning" if gap_days >= 7 else "info"
        alerts.append(_make_alert(
            alert_type="detraining_risk",
            severity=severity,
            title="Detraining risk: extended inactivity",
            message=(
                f"No recorded activities in {gap_days} days. Aerobic fitness begins "
                f"to decline after 5-7 days of inactivity. Even light sessions help "
                f"maintain fitness."
            ),
            data={"days_since_last_activity": gap_days},
        ))
        return alerts  # Don't also flag CTL drop — they are related

    # --- Check CTL drop over 2 weeks ---
    if len(wellness) >= 14:
        ctl_now = _safe_float(wellness[-1].get("ctl"))
        ctl_2wk_ago = _safe_float(wellness[-14].get("ctl"))
        if ctl_2wk_ago > 0:
            ctl_change_pct = (ctl_now - ctl_2wk_ago) / ctl_2wk_ago * 100
            if ctl_change_pct < -10:
                severity = "warning" if ctl_change_pct < -15 else "info"
                alerts.append(_make_alert(
                    alert_type="detraining_risk",
                    severity=severity,
                    title="Detraining risk: fitness declining",
                    message=(
                        f"CTL has dropped {abs(ctl_change_pct):.0f}% over the last 2 weeks "
                        f"({ctl_2wk_ago:.0f} -> {ctl_now:.0f}). Some fitness loss is expected "
                        f"during tapers, but unplanned drops may indicate insufficient "
                        f"training stimulus."
                    ),
                    data={
                        "ctl_current": round(ctl_now, 1),
                        "ctl_2_weeks_ago": round(ctl_2wk_ago, 1),
                        "ctl_change_pct": round(ctl_change_pct, 1),
                    },
                ))

    return alerts


def _detect_race_readiness(
    wellness: list[dict],
    activities: list[dict],
    athlete_config: dict,
) -> list[dict]:
    """Assess readiness when race_date in athlete_config is within 21 days.

    Evaluates TSB, CTL, sleep, and HRV trends to produce a holistic readiness
    assessment with positive factors and concerns.
    """
    alerts: list[dict] = []
    config = athlete_config if isinstance(athlete_config, dict) else {}

    race_date_str = config.get("race_date") or config.get("raceDate") or ""
    if not race_date_str:
        return alerts

    try:
        race_dt = datetime.strptime(str(race_date_str)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return alerts

    days_to_race = (race_dt - datetime.now()).days
    if days_to_race < 0 or days_to_race > 21:
        return alerts

    # --- Gather current metrics ---
    latest = wellness[-1] if wellness else {}
    ctl = _safe_float(latest.get("ctl"))
    atl = _safe_float(latest.get("atl"))
    tsb = ctl - atl

    # HRV trend (7 days)
    recent = _recent_wellness(wellness, 7)
    hrv_values = [v for w in recent if (v := _hrv(w)) is not None]
    hrv_slope = _linear_slope(hrv_values) if len(hrv_values) >= 3 else None
    hrv_avg = statistics.mean(hrv_values) if hrv_values else None

    # Sleep average (7 days)
    sleep_vals = [sh for w in recent if (sh := _sleep_hours(w)) is not None]
    sleep_avg = statistics.mean(sleep_vals) if sleep_vals else None

    # --- Build readiness factors ---
    factors: list[str] = []
    concerns: list[str] = []

    # TSB assessment
    if tsb > 5:
        factors.append(f"Good form (TSB {tsb:+.0f})")
    elif tsb > -10:
        factors.append(f"Moderate form (TSB {tsb:+.0f})")
    else:
        concerns.append(f"Fatigued (TSB {tsb:+.0f}) — may need more taper")

    # CTL assessment
    if ctl > 40:
        factors.append(f"Strong fitness base (CTL {ctl:.0f})")
    elif ctl > 20:
        factors.append(f"Moderate fitness (CTL {ctl:.0f})")
    else:
        concerns.append(f"Low fitness base (CTL {ctl:.0f})")

    # HRV trend
    if hrv_slope is not None:
        if hrv_slope > 0.3:
            factors.append("HRV trending up (good recovery)")
        elif hrv_slope < -0.5:
            concerns.append("HRV declining — recovery may be incomplete")

    # Sleep
    if sleep_avg is not None:
        if sleep_avg >= 7.0:
            factors.append(f"Good sleep ({sleep_avg:.1f}h avg)")
        elif sleep_avg < 6.0:
            concerns.append(f"Poor sleep ({sleep_avg:.1f}h avg) — will impair race performance")

    # Overall severity
    severity = "warning" if len(concerns) >= 2 else "info"

    readiness_items: list[str] = []
    if factors:
        readiness_items.append("Positive: " + "; ".join(factors))
    if concerns:
        readiness_items.append("Concerns: " + "; ".join(concerns))

    race_name = config.get("race_name", config.get("raceName", "race"))

    alerts.append(_make_alert(
        alert_type="race_readiness",
        severity=severity,
        title=f"Race readiness: {days_to_race} days to {race_name}",
        message=(
            f"{days_to_race} days until {race_name}. "
            + " ".join(readiness_items)
        ),
        data={
            "days_to_race": days_to_race,
            "race_date": str(race_date_str)[:10],
            "ctl": round(ctl, 1),
            "atl": round(atl, 1),
            "tsb": round(tsb, 1),
            "hrv_slope": round(hrv_slope, 2) if hrv_slope is not None else None,
            "hrv_avg": round(hrv_avg, 1) if hrv_avg is not None else None,
            "sleep_avg": round(sleep_avg, 1) if sleep_avg is not None else None,
            "positive_factors": len(factors),
            "concerns": len(concerns),
        },
    ))

    return alerts


def _detect_recovery_mismatch(
    wellness: list[dict],
    activities: list[dict],
) -> list[dict]:
    """Training hard (TSS>100) on day after poor sleep (<5h) or low HRV.

    Low HRV is defined as below 75% of the rolling baseline.  Checks the
    most recent 7 days and reports the latest mismatch as a *warning*.
    """
    alerts: list[dict] = []

    # Build date-keyed wellness lookups
    sleep_by_date: dict[str, float] = {}
    hrv_by_date: dict[str, float] = {}
    for w in wellness:
        d = _wellness_date(w)
        if not d:
            continue
        sh = _sleep_hours(w)
        if sh is not None:
            sleep_by_date[d] = sh
        h = _hrv(w)
        if h is not None:
            hrv_by_date[d] = h

    # Compute HRV baseline for the "low HRV" threshold
    all_hrv = list(hrv_by_date.values())
    hrv_baseline = statistics.mean(all_hrv) if len(all_hrv) >= 5 else None
    hrv_low_threshold = hrv_baseline * 0.75 if hrv_baseline else None

    # Daily TSS
    daily_tss: dict[str, float] = defaultdict(float)
    for a in activities:
        d = _activity_date(a)
        if d:
            daily_tss[d] += _activity_tss(a)

    # Check recent days (last 7)
    recent_dates = sorted(daily_tss.keys())[-7:]
    mismatches: list[dict] = []

    for date_str in recent_dates:
        tss = daily_tss[date_str]
        if tss < 100:
            continue

        # Wellness on the same date reflects the morning before training
        prev_sleep = sleep_by_date.get(date_str)
        prev_hrv = hrv_by_date.get(date_str)

        bad_sleep = prev_sleep is not None and prev_sleep < 5.0
        low_hrv = (
            hrv_low_threshold is not None
            and prev_hrv is not None
            and prev_hrv < hrv_low_threshold
        )

        if bad_sleep or low_hrv:
            reasons: list[str] = []
            if bad_sleep:
                reasons.append(f"only {prev_sleep:.1f}h sleep")
            if low_hrv:
                reasons.append(
                    f"low HRV ({prev_hrv:.0f} vs {hrv_baseline:.0f} baseline)"
                )
            mismatches.append({
                "date": date_str,
                "tss": round(tss, 1),
                "sleep_hours": round(prev_sleep, 1) if prev_sleep is not None else None,
                "hrv": round(prev_hrv, 1) if prev_hrv is not None else None,
                "reasons": reasons,
            })

    if mismatches:
        latest = mismatches[-1]
        alerts.append(_make_alert(
            alert_type="recovery_mismatch",
            severity="warning",
            title="Recovery mismatch: hard training on poor recovery",
            message=(
                f"Hard training (TSS {latest['tss']:.0f}) on {latest['date']} despite "
                f"{' and '.join(latest['reasons'])}. Training hard when under-recovered "
                f"increases injury risk and reduces adaptation. Consider adjusting "
                f"intensity based on morning readiness markers."
            ),
            data={
                "mismatches": mismatches,
                "mismatch_count": len(mismatches),
            },
        ))

    return alerts


def _detect_hydration_reminder(
    wellness: list[dict],
    activities: list[dict],
    athlete_config: dict,
) -> list[dict]:
    """Info alert when it is a training day and temperature data is available.

    Temperature can come from *athlete_config* (e.g. weather integration) or
    from the latest wellness entry.
    """
    alerts: list[dict] = []
    config = athlete_config if isinstance(athlete_config, dict) else {}

    today = datetime.now().strftime("%Y-%m-%d")

    # Is today a training day?
    is_training_day = any(_activity_date(a) == today for a in activities)
    if not is_training_day:
        return alerts

    # Look for temperature data
    temp = config.get("temperature") or config.get("temp")
    if temp is None and wellness:
        latest = wellness[-1]
        temp = latest.get("temperature") or latest.get("temp")

    if temp is None:
        return alerts

    temp_val = _safe_float(temp)
    if temp_val <= 0:
        return alerts

    # Tailor message to temperature band
    if temp_val > 30:
        message = (
            f"Temperature is {temp_val:.0f}C. Increase fluid intake significantly "
            f"for today's training. Aim for 500-800ml/hour and include electrolytes."
        )
    elif temp_val > 25:
        message = (
            f"Temperature is {temp_val:.0f}C. Remember to hydrate well before, "
            f"during, and after training. Aim for 400-600ml/hour."
        )
    else:
        message = (
            f"Training day. Stay hydrated: aim for at least 300-500ml/hour of "
            f"activity. Current temperature: {temp_val:.0f}C."
        )

    alerts.append(_make_alert(
        alert_type="hydration_reminder",
        severity="info",
        title="Hydration reminder",
        message=message,
        data={"temperature_c": round(temp_val, 1), "date": today},
    ))

    return alerts


def _detect_sleep_debt(
    wellness: list[dict],
    athlete_config: dict,
) -> list[dict]:
    """Running sleep debt > 5h over 7 days vs target.

    Target hours come from *athlete_config.sleep_target_hours* (default 7.5).
    Severity is *warning* when debt exceeds 7h, otherwise *info*.
    """
    alerts: list[dict] = []
    config = athlete_config if isinstance(athlete_config, dict) else {}

    target = _safe_float(config.get("sleep_target_hours", 7.5))
    if target <= 0:
        target = 7.5

    recent = _recent_wellness(wellness, 7)
    sleep_vals: list[float] = []
    for w in recent:
        sh = _sleep_hours(w)
        if sh is not None:
            sleep_vals.append(sh)

    if len(sleep_vals) < 3:
        return alerts

    # Cumulative debt: only count nights that fell short of target
    total_debt = sum(max(0, target - sh) for sh in sleep_vals)

    # Scale to a full 7-night window if we have fewer tracked nights
    if len(sleep_vals) < 7:
        estimated_debt = total_debt / len(sleep_vals) * 7
    else:
        estimated_debt = total_debt

    if estimated_debt <= 5.0:
        return alerts

    avg_sleep = statistics.mean(sleep_vals)
    nightly_deficit = target - avg_sleep
    severity = "warning" if estimated_debt > 7.0 else "info"

    alerts.append(_make_alert(
        alert_type="sleep_debt_accumulated",
        severity=severity,
        title="Accumulated sleep debt",
        message=(
            f"Estimated sleep debt of {estimated_debt:.1f}h over the last 7 days "
            f"(averaging {avg_sleep:.1f}h vs {target:.1f}h target, "
            f"~{nightly_deficit:.1f}h/night deficit). Sleep debt impairs recovery, "
            f"cognitive function, and immune health. Try to add 30-60 min of sleep "
            f"per night to pay it down."
        ),
        data={
            "estimated_debt_hours": round(estimated_debt, 1),
            "avg_sleep_hours": round(avg_sleep, 1),
            "target_hours": target,
            "nightly_deficit": round(nightly_deficit, 1),
            "nights_tracked": len(sleep_vals),
        },
    ))

    return alerts


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def generate_alerts(
    wellness: list[dict],
    activities: list[dict],
    athlete_config: dict,
) -> list[dict]:
    """Run all alert detectors and return a combined list of alerts.

    Each detector is wrapped in try/except so that a failure in one does not
    block others.  Alerts are returned sorted by severity (critical first).

    Parameters
    ----------
    wellness : list[dict]
        Daily wellness entries, chronologically ordered (oldest first).
    activities : list[dict]
        Activity entries, chronologically ordered (oldest first).
    athlete_config : dict
        Athlete profile and preferences.  Expected keys include:
        ``rhr_baseline``, ``sleep_target_hours``, ``race_date``,
        ``race_name``, ``temperature``.

    Returns
    -------
    list[dict]
        Each dict has keys: ``type``, ``severity``, ``title``, ``message``,
        ``data``.
    """
    alerts: list[dict] = []
    config = athlete_config if isinstance(athlete_config, dict) else {}

    detectors: list[tuple[str, object]] = [
        ("overtraining_risk", lambda: _detect_overtraining_risk(wellness, activities)),
        ("sleep_crisis", lambda: _detect_sleep_crisis(wellness)),
        ("illness_risk", lambda: _detect_illness_risk(wellness, config)),
        ("detraining_risk", lambda: _detect_detraining_risk(wellness, activities)),
        ("race_readiness", lambda: _detect_race_readiness(wellness, activities, config)),
        ("recovery_mismatch", lambda: _detect_recovery_mismatch(wellness, activities)),
        ("hydration_reminder", lambda: _detect_hydration_reminder(wellness, activities, config)),
        ("sleep_debt_accumulated", lambda: _detect_sleep_debt(wellness, config)),
    ]

    for name, detector in detectors:
        try:
            alerts.extend(detector())
        except Exception:
            log.exception("alert detector '%s' failed", name)

    # Sort by severity: critical > warning > info
    alerts.sort(key=lambda a: SEVERITY_ORDER.get(a.get("severity", "info"), 9))

    return alerts


# ---------------------------------------------------------------------------
# Formatting Utility
# ---------------------------------------------------------------------------

def format_alerts_context(alerts: list[dict]) -> str:
    """Format alerts for injection into the coaching engine system prompt.

    Produces a concise block the coaching LLM can act on.
    """
    if not alerts:
        return "ALERTS: None — all systems normal."

    severity_icons = {
        "critical": "[CRITICAL]",
        "warning": "[WARNING]",
        "info": "[INFO]",
    }

    lines: list[str] = [f"ALERTS ({len(alerts)}):"]

    for alert in alerts:
        icon = severity_icons.get(alert["severity"], "[?]")
        lines.append(f"{icon} {alert['title']}")
        # Include a condensed version of the message (first sentence)
        msg = alert.get("message", "")
        first_sentence = msg.split("\n")[0].split(". ")[0] + "."
        if len(first_sentence) > 150:
            first_sentence = first_sentence[:147] + "..."
        lines.append(f"  {first_sentence}")

    # Action guidance for the coaching LLM
    critical_count = sum(1 for a in alerts if a["severity"] == "critical")
    warning_count = sum(1 for a in alerts if a["severity"] == "warning")

    if critical_count > 0:
        lines.append(
            f"\nACTION REQUIRED: {critical_count} critical alert(s). "
            "Address these before proceeding with normal coaching."
        )
    elif warning_count > 0:
        lines.append(
            f"\nATTENTION: {warning_count} warning(s). "
            "Factor these into today's training recommendations."
        )

    return "\n".join(lines)
