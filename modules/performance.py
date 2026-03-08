"""Performance analysis module — fitness trends, training load, race readiness."""

from datetime import datetime, timedelta


def analyze_training(wellness_data: list, activities: list, race_date: str = None) -> dict:
    """Analyze training load, fitness trends, and race readiness."""
    # Current fitness metrics
    latest = wellness_data[-1] if wellness_data else {}
    ctl = latest.get("ctl", 0)
    atl = latest.get("atl", 0)
    tsb = ctl - atl

    # CTL trend (14-day)
    ctl_trend = "stable"
    if len(wellness_data) >= 14:
        ctl_14d_ago = wellness_data[-14].get("ctl", 0)
        if ctl > ctl_14d_ago + 2:
            ctl_trend = "rising"
        elif ctl < ctl_14d_ago - 2:
            ctl_trend = "declining"

    # Weekly TSS
    week_tss = 0
    week_activities = 0
    week_run_tss = 0
    week_ride_tss = 0
    week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y-%m-%d")
    for a in activities:
        date = a.get("start_date_local", a.get("date", ""))[:10]
        if date >= week_start and a.get("type"):
            tss = a.get("icu_training_load", 0) or a.get("tss", 0) or 0
            week_tss += tss
            week_activities += 1
            atype = (a.get("type", "") or "").lower()
            if "run" in atype:
                week_run_tss += tss
            elif "ride" in atype:
                week_ride_tss += tss

    # Run vs ride distribution (last 14 days)
    run_count = sum(1 for a in activities if "run" in (a.get("type", "") or "").lower())
    ride_count = sum(1 for a in activities if "ride" in (a.get("type", "") or "").lower())

    # Ramp rate
    ramp_rate = wellness_data[-1].get("rampRate", 0) if wellness_data else 0

    # Race readiness
    days_to_race = None
    race_readiness = "unknown"
    if race_date:
        try:
            race_dt = datetime.strptime(race_date, "%Y-%m-%d")
            days_to_race = (race_dt - datetime.now()).days
            if days_to_race <= 0:
                race_readiness = "race_day"
            elif tsb > 10 and ctl > 35:
                race_readiness = "fresh_and_fit"
            elif tsb > 0 and ctl > 30:
                race_readiness = "good"
            elif tsb < -20:
                race_readiness = "fatigued"
            else:
                race_readiness = "building"
        except ValueError:
            pass

    # Overtraining risk
    overtraining_risk = "low"
    if tsb < -30:
        overtraining_risk = "high"
    elif tsb < -20:
        overtraining_risk = "moderate"
    elif ramp_rate and ramp_rate > 7:
        overtraining_risk = "moderate"

    return {
        "ctl": round(ctl, 1),
        "atl": round(atl, 1),
        "tsb": round(tsb, 1),
        "ctl_trend": ctl_trend,
        "ramp_rate": round(ramp_rate, 1) if ramp_rate else 0,
        "week_tss": round(week_tss),
        "week_activities": week_activities,
        "week_run_tss": round(week_run_tss),
        "week_ride_tss": round(week_ride_tss),
        "run_count_14d": run_count,
        "ride_count_14d": ride_count,
        "days_to_race": days_to_race,
        "race_readiness": race_readiness,
        "overtraining_risk": overtraining_risk,
    }


def format_performance_context(analysis: dict) -> str:
    lines = [
        "PERFORMANCE ANALYSIS:",
        f"CTL: {analysis['ctl']} ({analysis['ctl_trend']}) | ATL: {analysis['atl']} | TSB: {analysis['tsb']}",
        f"Ramp rate: {analysis['ramp_rate']}/week | Overtraining risk: {analysis['overtraining_risk']}",
        f"This week: {analysis['week_tss']} TSS ({analysis['week_activities']} sessions) | Run: {analysis['week_run_tss']} | Ride: {analysis['week_ride_tss']}",
        f"14d count: {analysis['run_count_14d']} runs, {analysis['ride_count_14d']} rides",
    ]
    if analysis["days_to_race"] is not None:
        lines.append(f"Race in {analysis['days_to_race']} days | Readiness: {analysis['race_readiness']}")
    return "\n".join(lines)
