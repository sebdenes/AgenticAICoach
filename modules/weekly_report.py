"""Weekly training report module — comprehensive weekly summary and Telegram formatting."""

from __future__ import annotations

from datetime import datetime, timedelta


def generate_weekly_report(
    wellness_data: list,
    activities: list,
    events: list,
    athlete_config: dict,
    race_date: str,
) -> dict:
    """Generate a comprehensive weekly training report.

    Args:
        wellness_data: List of daily wellness entries (sleep, HRV, CTL, ATL, etc.).
        activities: List of completed activity dicts (type, distance, moving_time, tss, etc.).
        events: List of planned/upcoming calendar events.
        athlete_config: Athlete profile with name, weight_kg, sleep_target, etc.
        race_date: Target race date as "YYYY-MM-DD".

    Returns:
        dict with week_number, totals, by_sport, sleep_summary, fitness_summary,
        hrv_summary, compliance, highlights, next_week_preview, and days_to_race.
    """
    now = datetime.now()
    iso_cal = now.isocalendar()
    week_number = iso_cal[1]

    # Week boundaries (Monday to Sunday)
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    week_start_str = week_start.strftime("%Y-%m-%d")
    week_end_str = week_end.strftime("%Y-%m-%d")

    # ---- Filter activities for this week ----
    week_activities = _filter_activities_for_week(activities, week_start_str, week_end_str)

    # ---- Totals ----
    total_tss = 0
    total_duration_min = 0
    total_distance_km = 0.0

    for a in week_activities:
        total_tss += _get_tss(a)
        total_duration_min += _get_duration_min(a)
        total_distance_km += _get_distance_km(a)

    # ---- By sport ----
    by_sport = _aggregate_by_sport(week_activities)

    # ---- Sleep summary ----
    week_wellness = _filter_wellness_for_week(wellness_data, week_start_str, week_end_str)
    sleep_target = athlete_config.get("sleep_target", 7.5)
    sleep_summary = _build_sleep_summary(week_wellness, sleep_target)

    # ---- Fitness summary ----
    fitness_summary = _build_fitness_summary(wellness_data, week_wellness)

    # ---- HRV summary ----
    hrv_summary = _build_hrv_summary(week_wellness)

    # ---- Compliance ----
    compliance = _build_compliance(events, week_activities, week_start_str, week_end_str)

    # ---- Days to race ----
    days_to_race = None
    if race_date:
        try:
            race_dt = datetime.strptime(race_date, "%Y-%m-%d")
            days_to_race = (race_dt - now).days
        except ValueError:
            pass

    # ---- Highlights ----
    highlights = _build_highlights(
        total_tss,
        total_distance_km,
        by_sport,
        sleep_summary,
        fitness_summary,
        compliance,
        days_to_race,
        athlete_config,
    )

    # ---- Next week preview ----
    next_week_start = week_end + timedelta(seconds=1)
    next_week_end = next_week_start + timedelta(days=6)
    next_week_preview = _build_next_week_preview(
        events,
        next_week_start.strftime("%Y-%m-%d"),
        next_week_end.strftime("%Y-%m-%d"),
    )

    return {
        "week_number": week_number,
        "week_start": week_start_str,
        "week_end": week_end_str,
        "athlete": athlete_config.get("name", "Athlete"),
        "total_tss": round(total_tss),
        "total_duration_min": round(total_duration_min),
        "total_distance_km": round(total_distance_km, 1),
        "by_sport": by_sport,
        "sleep_summary": sleep_summary,
        "fitness_summary": fitness_summary,
        "hrv_summary": hrv_summary,
        "compliance": compliance,
        "highlights": highlights,
        "next_week_preview": next_week_preview,
        "days_to_race": days_to_race,
    }


def format_weekly_report(report: dict) -> str:
    """Format the weekly report as a Markdown string for Telegram (max 30 lines).

    Sections: header with dates/countdown, training volume by sport,
    sleep & recovery, fitness trend, key highlights, next week preview.
    """
    lines: list[str] = []

    # ---- Header ----
    race_tag = ""
    if report.get("days_to_race") is not None:
        race_tag = f" | Race in {report['days_to_race']}d"
    lines.append(
        f"**Week {report['week_number']} Report** "
        f"({report['week_start']} - {report['week_end']}{race_tag})"
    )
    lines.append("")

    # ---- Training volume ----
    lines.append("**Training Volume**")
    dur_h = report["total_duration_min"] / 60
    lines.append(
        f"Total: {report['total_tss']} TSS | "
        f"{dur_h:.1f}h | {report['total_distance_km']} km"
    )
    for sport, data in report["by_sport"].items():
        sport_dur_h = data["duration_min"] / 60
        lines.append(
            f"  {sport.capitalize()}: {data['count']}x | "
            f"{data['tss']} TSS | {data['distance_km']} km | {sport_dur_h:.1f}h"
        )
    lines.append("")

    # ---- Sleep & recovery ----
    ss = report["sleep_summary"]
    lines.append("**Sleep & Recovery**")
    lines.append(
        f"Avg: {ss['avg_hours']}h | "
        f"Best: {ss['best_night']}h | Worst: {ss['worst_night']}h | "
        f"Debt: {ss['debt_hours']}h | Trend: {ss['trend']}"
    )
    hrv = report["hrv_summary"]
    if hrv["avg"] is not None:
        lines.append(
            f"HRV: avg {hrv['avg']} | "
            f"range {hrv['min']}-{hrv['max']} | trend: {hrv['trend']}"
        )
    lines.append("")

    # ---- Fitness trend ----
    fs = report["fitness_summary"]
    lines.append("**Fitness Trend**")
    lines.append(
        f"CTL: {fs['ctl_start']} -> {fs['ctl_end']} ({fs['ctl_change']:+.1f}) | "
        f"ATL: {fs['atl']} | TSB: {fs['tsb']} | Ramp: {fs['ramp_rate']}/wk"
    )
    lines.append("")

    # ---- Compliance ----
    comp = report["compliance"]
    lines.append(
        f"**Compliance**: {comp['completed_sessions']}/{comp['planned_sessions']} "
        f"sessions ({comp['rate_pct']}%)"
    )
    lines.append("")

    # ---- Highlights ----
    if report["highlights"]:
        lines.append("**Highlights**")
        for h in report["highlights"]:
            lines.append(f"- {h}")
        lines.append("")

    # ---- Next week preview ----
    if report["next_week_preview"]:
        lines.append("**Next Week**")
        for item in report["next_week_preview"]:
            lines.append(f"- {item}")

    # Trim to 30 lines max
    return "\n".join(lines[:30])


def compare_weeks(current_report: dict, previous_report: dict) -> dict:
    """Compare two weekly reports and return week-over-week deltas.

    Returns:
        dict with delta_tss, delta_distance_km, delta_sleep_avg, delta_ctl,
        and pct changes where applicable.
    """
    cur_tss = current_report.get("total_tss", 0)
    prev_tss = previous_report.get("total_tss", 0)

    cur_dist = current_report.get("total_distance_km", 0)
    prev_dist = previous_report.get("total_distance_km", 0)

    cur_sleep = current_report.get("sleep_summary", {}).get("avg_hours", 0)
    prev_sleep = previous_report.get("sleep_summary", {}).get("avg_hours", 0)

    cur_ctl = current_report.get("fitness_summary", {}).get("ctl_end", 0)
    prev_ctl = previous_report.get("fitness_summary", {}).get("ctl_end", 0)

    cur_dur = current_report.get("total_duration_min", 0)
    prev_dur = previous_report.get("total_duration_min", 0)

    return {
        "delta_tss": round(cur_tss - prev_tss),
        "delta_tss_pct": _pct_change(prev_tss, cur_tss),
        "delta_distance_km": round(cur_dist - prev_dist, 1),
        "delta_distance_pct": _pct_change(prev_dist, cur_dist),
        "delta_duration_min": round(cur_dur - prev_dur),
        "delta_sleep_avg": round(cur_sleep - prev_sleep, 1),
        "delta_ctl": round(cur_ctl - prev_ctl, 1),
        "current_week": current_report.get("week_number"),
        "previous_week": previous_report.get("week_number"),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_tss(activity: dict) -> float:
    return activity.get("icu_training_load", 0) or activity.get("tss", 0) or 0


def _get_duration_min(activity: dict) -> float:
    return (activity.get("moving_time", 0) or 0) / 60


def _get_distance_km(activity: dict) -> float:
    return (activity.get("distance", 0) or 0) / 1000


def _classify_sport(activity: dict) -> str:
    """Map an activity type to one of: run, ride, other."""
    atype = (activity.get("type", "") or "").lower()
    if "run" in atype:
        return "run"
    if "ride" in atype or "cycling" in atype:
        return "ride"
    return "other"


def _filter_activities_for_week(
    activities: list, week_start: str, week_end: str,
) -> list[dict]:
    result = []
    for a in activities:
        if not a.get("type"):
            continue
        date = (a.get("start_date_local", a.get("date", "")) or "")[:10]
        if week_start <= date <= week_end:
            result.append(a)
    return result


def _filter_wellness_for_week(
    wellness_data: list, week_start: str, week_end: str,
) -> list[dict]:
    result = []
    for d in wellness_data:
        date = (d.get("id", d.get("date", "")) or "")[:10]
        if week_start <= date <= week_end:
            result.append(d)
    return result


def _aggregate_by_sport(activities: list) -> dict[str, dict]:
    buckets: dict[str, dict] = {}
    for a in activities:
        sport = _classify_sport(a)
        if sport not in buckets:
            buckets[sport] = {
                "count": 0,
                "tss": 0,
                "distance_km": 0.0,
                "duration_min": 0,
            }
        buckets[sport]["count"] += 1
        buckets[sport]["tss"] += round(_get_tss(a))
        buckets[sport]["distance_km"] += round(_get_distance_km(a), 1)
        buckets[sport]["duration_min"] += round(_get_duration_min(a))

    # Round final aggregates
    for data in buckets.values():
        data["tss"] = round(data["tss"])
        data["distance_km"] = round(data["distance_km"], 1)
        data["duration_min"] = round(data["duration_min"])

    return buckets


def _build_sleep_summary(week_wellness: list, target_hours: float) -> dict:
    sleep_hours: list[float] = []
    for d in week_wellness:
        sleep_s = d.get("sleepSecs", 0) or d.get("sleep_seconds", 0) or 0
        if sleep_s > 0:
            sleep_hours.append(sleep_s / 3600)

    if not sleep_hours:
        return {
            "avg_hours": 0,
            "worst_night": 0,
            "best_night": 0,
            "debt_hours": 0,
            "trend": "no_data",
        }

    avg_h = round(sum(sleep_hours) / len(sleep_hours), 1)
    worst = round(min(sleep_hours), 1)
    best = round(max(sleep_hours), 1)
    debt = round(sum(max(0, target_hours - h) for h in sleep_hours), 1)

    # Trend: compare second half of week vs first half
    trend = "stable"
    mid = len(sleep_hours) // 2
    if mid > 0 and len(sleep_hours) > mid:
        first_half_avg = sum(sleep_hours[:mid]) / mid
        second_half_avg = sum(sleep_hours[mid:]) / len(sleep_hours[mid:])
        if second_half_avg > first_half_avg + 0.3:
            trend = "improving"
        elif second_half_avg < first_half_avg - 0.3:
            trend = "declining"

    return {
        "avg_hours": avg_h,
        "worst_night": worst,
        "best_night": best,
        "debt_hours": debt,
        "trend": trend,
    }


def _build_fitness_summary(
    all_wellness: list, week_wellness: list,
) -> dict:
    # CTL at start and end of the week
    ctl_start = week_wellness[0].get("ctl", 0) if week_wellness else 0
    ctl_end = week_wellness[-1].get("ctl", 0) if week_wellness else 0
    ctl_change = ctl_end - ctl_start

    latest = week_wellness[-1] if week_wellness else (all_wellness[-1] if all_wellness else {})
    atl = latest.get("atl", 0)
    tsb = ctl_end - atl

    ramp_rate = latest.get("rampRate", 0) or 0

    return {
        "ctl_start": round(ctl_start, 1),
        "ctl_end": round(ctl_end, 1),
        "ctl_change": round(ctl_change, 1),
        "atl": round(atl, 1),
        "tsb": round(tsb, 1),
        "ramp_rate": round(ramp_rate, 1),
    }


def _build_hrv_summary(week_wellness: list) -> dict:
    hrv_values = [
        d["hrv"] for d in week_wellness
        if d.get("hrv") is not None and d.get("hrv", 0) > 0
    ]

    if not hrv_values:
        return {"avg": None, "min": None, "max": None, "trend": "no_data"}

    avg_hrv = round(sum(hrv_values) / len(hrv_values), 1)
    min_hrv = round(min(hrv_values), 1)
    max_hrv = round(max(hrv_values), 1)

    trend = "stable"
    mid = len(hrv_values) // 2
    if mid > 0 and len(hrv_values) > mid:
        first_avg = sum(hrv_values[:mid]) / mid
        second_avg = sum(hrv_values[mid:]) / len(hrv_values[mid:])
        if second_avg > first_avg + 3:
            trend = "improving"
        elif second_avg < first_avg - 3:
            trend = "declining"

    return {
        "avg": avg_hrv,
        "min": min_hrv,
        "max": max_hrv,
        "trend": trend,
    }


def _build_compliance(
    events: list,
    week_activities: list,
    week_start: str,
    week_end: str,
) -> dict:
    planned_sessions = 0
    for e in events:
        date = (e.get("start_date_local", e.get("date", "")) or "")[:10]
        if week_start <= date <= week_end:
            planned_sessions += 1

    completed_sessions = len(week_activities)
    rate = (
        round(completed_sessions / planned_sessions * 100, 1)
        if planned_sessions > 0
        else 0
    )

    return {
        "planned_sessions": planned_sessions,
        "completed_sessions": completed_sessions,
        "rate_pct": rate,
    }


def _build_highlights(
    total_tss: float,
    total_distance_km: float,
    by_sport: dict,
    sleep_summary: dict,
    fitness_summary: dict,
    compliance: dict,
    days_to_race: int | None,
    athlete_config: dict,
) -> list[str]:
    highlights: list[str] = []

    # High training load
    if total_tss >= 500:
        highlights.append(f"Big training week: {round(total_tss)} TSS")
    elif total_tss < 150 and total_tss > 0:
        highlights.append(f"Light week: only {round(total_tss)} TSS")

    # Distance milestones
    run_data = by_sport.get("run", {})
    run_dist = run_data.get("distance_km", 0)
    if run_dist >= 80:
        highlights.append(f"High run volume: {run_dist} km")
    if run_dist >= 100:
        highlights.append("Century run week achieved!")

    ride_data = by_sport.get("ride", {})
    ride_dist = ride_data.get("distance_km", 0)
    if ride_dist >= 200:
        highlights.append(f"Big ride volume: {ride_dist} km")

    # Sleep concerns
    if sleep_summary.get("avg_hours", 0) < 6.5:
        highlights.append(
            f"Sleep deficit concern: avg {sleep_summary['avg_hours']}h "
            f"(target: {athlete_config.get('sleep_target', 7.5)}h)"
        )
    elif sleep_summary.get("avg_hours", 0) >= 8.0:
        highlights.append(
            f"Excellent sleep: avg {sleep_summary['avg_hours']}h"
        )

    if sleep_summary.get("debt_hours", 0) > 5:
        highlights.append(
            f"Accumulated sleep debt: {sleep_summary['debt_hours']}h this week"
        )

    # Fitness trend
    if fitness_summary.get("ctl_change", 0) > 3:
        highlights.append(
            f"Fitness building well: CTL +{fitness_summary['ctl_change']:.1f}"
        )
    elif fitness_summary.get("ctl_change", 0) < -3:
        highlights.append(
            f"Fitness declining: CTL {fitness_summary['ctl_change']:.1f}"
        )

    if fitness_summary.get("ramp_rate", 0) > 7:
        highlights.append(
            f"High ramp rate ({fitness_summary['ramp_rate']}): watch for overtraining"
        )

    # Compliance
    rate = compliance.get("rate_pct", 0)
    if rate == 100 and compliance.get("planned_sessions", 0) > 0:
        highlights.append("Perfect compliance: all planned sessions completed")
    elif 0 < rate < 70:
        highlights.append(
            f"Low compliance: {compliance['completed_sessions']}/"
            f"{compliance['planned_sessions']} sessions ({rate}%)"
        )

    # Race proximity
    if days_to_race is not None:
        if days_to_race <= 14:
            highlights.append(
                f"Race week approaching: {days_to_race} days to go — taper time"
            )
        elif days_to_race <= 28:
            highlights.append(
                f"{days_to_race} days to race — final build phase"
            )

    return highlights


def _build_next_week_preview(
    events: list, next_start: str, next_end: str,
) -> list[str]:
    preview: list[str] = []
    for e in events:
        date = (e.get("start_date_local", e.get("date", "")) or "")[:10]
        if next_start <= date <= next_end:
            name = e.get("name", "Session")
            category = e.get("category", "")
            tag = f" [{category}]" if category else ""
            preview.append(f"{date}: {name}{tag}")
    return preview


def _pct_change(old: float, new: float) -> float:
    """Calculate percentage change, returning 0.0 when old is zero."""
    if old == 0:
        return 0.0
    return round((new - old) / old * 100, 1)
