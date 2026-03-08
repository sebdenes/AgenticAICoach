"""Workout compliance module — planned vs completed analysis."""

from __future__ import annotations

from datetime import datetime, timedelta


def analyze_compliance(activities: list, events: list, days: int = 7) -> dict:
    """Compare planned events vs completed activities over a period."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # Build planned workouts map by date
    planned = {}
    for e in events:
        date = (e.get("start_date_local", e.get("date", "")) or "")[:10]
        if date >= cutoff:
            planned.setdefault(date, []).append({
                "name": e.get("name", ""),
                "category": e.get("category", ""),
                "description": (e.get("description", "") or "")[:300],
            })

    # Build completed activities map by date
    completed = {}
    for a in activities:
        if not a.get("type"):
            continue
        date = (a.get("start_date_local", a.get("date", "")) or "")[:10]
        if date >= cutoff:
            completed.setdefault(date, []).append({
                "name": a.get("name", ""),
                "type": a.get("type", ""),
                "duration_min": (a.get("moving_time", 0) or 0) // 60,
                "distance_km": round((a.get("distance", 0) or 0) / 1000, 1),
                "tss": a.get("icu_training_load", 0) or 0,
                "avg_hr": a.get("average_heartrate"),
                "avg_pace": _calc_pace(a),
            })

    # Compare
    days_planned = len(planned)
    days_completed = len(completed)
    missed_days = []
    extra_days = []
    compliance_days = []

    all_dates = sorted(set(list(planned.keys()) + list(completed.keys())))
    for date in all_dates:
        p = planned.get(date, [])
        c = completed.get(date, [])
        if p and not c:
            missed_days.append({"date": date, "planned": p})
        elif c and not p:
            extra_days.append({"date": date, "activities": c})
        elif p and c:
            compliance_days.append({"date": date, "planned": p, "completed": c})

    total_planned = sum(len(v) for v in planned.values())
    total_completed = sum(len(v) for v in completed.values())
    compliance_rate = (len(compliance_days) / days_planned * 100) if days_planned > 0 else 0

    return {
        "period_days": days,
        "days_planned": days_planned,
        "days_completed": days_completed,
        "total_planned_sessions": total_planned,
        "total_completed_sessions": total_completed,
        "compliance_rate": round(compliance_rate, 1),
        "missed_days": missed_days,
        "extra_days": extra_days,
        "compliance_days": compliance_days,
    }


def format_compliance_context(analysis: dict) -> str:
    lines = [
        "WORKOUT COMPLIANCE:",
        f"Rate: {analysis['compliance_rate']}% ({analysis['days_completed']}/{analysis['days_planned']} days)",
        f"Sessions: {analysis['total_completed_sessions']} completed / {analysis['total_planned_sessions']} planned",
    ]
    if analysis["missed_days"]:
        missed = [d["date"] for d in analysis["missed_days"][:3]]
        lines.append(f"Missed: {', '.join(missed)}")
    if analysis["extra_days"]:
        extra = [d["date"] for d in analysis["extra_days"][:3]]
        lines.append(f"Extra sessions: {', '.join(extra)}")
    return "\n".join(lines)


def _calc_pace(activity: dict) -> str | None:
    """Calculate pace in min/km for running activities."""
    atype = (activity.get("type", "") or "").lower()
    if "run" not in atype:
        return None
    dist = activity.get("distance", 0) or 0
    time = activity.get("moving_time", 0) or 0
    if dist > 0 and time > 0:
        pace_sec_per_km = time / (dist / 1000)
        mins = int(pace_sec_per_km // 60)
        secs = int(pace_sec_per_km % 60)
        return f"{mins}:{secs:02d}"
    return None
