"""Sleep coaching module — analysis, debt tracking, recommendations."""

from datetime import datetime, timedelta


def analyze_sleep(wellness_data: list, target_hours: float = 7.5) -> dict:
    """Comprehensive sleep analysis from wellness data."""
    sleep_entries = []
    for d in wellness_data:
        sleep_s = d.get("sleepSecs", 0) or d.get("sleep_seconds", 0) or 0
        if sleep_s > 0:
            sleep_entries.append({
                "date": d.get("id", d.get("date", "")),
                "hours": sleep_s / 3600,
                "score": d.get("sleepScore", d.get("sleep_score")),
                "hrv": d.get("hrv"),
                "rhr": d.get("restingHR") or d.get("rhr"),
            })

    if not sleep_entries:
        return {"status": "no_data"}

    hours = [e["hours"] for e in sleep_entries]
    last_7 = hours[-7:] if len(hours) >= 7 else hours
    last_night = sleep_entries[-1] if sleep_entries else None

    avg_7d = sum(last_7) / len(last_7) if last_7 else 0
    debt_7d = sum(max(0, target_hours - h) for h in last_7)

    # Streak: consecutive nights >= target
    streak = 0
    for e in reversed(sleep_entries):
        if e["hours"] >= target_hours * 0.9:  # 90% of target counts
            streak += 1
        else:
            break

    # Grade last night
    if last_night:
        if last_night["hours"] >= 7:
            grade = "green"
        elif last_night["hours"] >= 6:
            grade = "yellow"
        else:
            grade = "red"
    else:
        grade = "unknown"

    # Trend: compare last 3 days vs previous 3 days
    trend = "stable"
    if len(hours) >= 6:
        recent = sum(hours[-3:]) / 3
        prior = sum(hours[-6:-3]) / 3
        if recent > prior + 0.5:
            trend = "improving"
        elif recent < prior - 0.5:
            trend = "declining"

    # Sleep-HRV correlation
    hrv_correlation = None
    if len(sleep_entries) >= 5:
        paired = [(e["hours"], e["hrv"]) for e in sleep_entries if e["hrv"]]
        if len(paired) >= 5:
            h_vals, hrv_vals = zip(*paired)
            # Simple correlation direction
            h_above = [1 if h > avg_7d else 0 for h in h_vals[-7:]]
            hrv_avg = sum(hrv_vals) / len(hrv_vals)
            hrv_above = [1 if v > hrv_avg else 0 for v in hrv_vals[-7:]]
            matches = sum(1 for a, b in zip(h_above, hrv_above) if a == b)
            if matches / len(h_above) > 0.7:
                hrv_correlation = "strong"
            elif matches / len(h_above) > 0.5:
                hrv_correlation = "moderate"

    return {
        "status": "ok",
        "last_night": last_night,
        "grade": grade,
        "avg_7d": round(avg_7d, 1),
        "debt_7d": round(debt_7d, 1),
        "streak": streak,
        "trend": trend,
        "min_7d": round(min(last_7), 1) if last_7 else 0,
        "max_7d": round(max(last_7), 1) if last_7 else 0,
        "hrv_correlation": hrv_correlation,
        "needs_medical": avg_7d < 5.5,
        "entries": sleep_entries,
    }


def format_sleep_context(analysis: dict) -> str:
    """Format sleep analysis for inclusion in coaching context."""
    if analysis.get("status") != "ok":
        return "Sleep data: insufficient"

    lines = [
        "SLEEP ANALYSIS:",
        f"Last night: {analysis['last_night']['hours']:.1f}h (grade: {analysis['grade']})",
        f"7-day avg: {analysis['avg_7d']}h | debt: {analysis['debt_7d']}h",
        f"Range: {analysis['min_7d']}-{analysis['max_7d']}h | Trend: {analysis['trend']}",
        f"Good sleep streak: {analysis['streak']} nights",
    ]
    if analysis["hrv_correlation"]:
        lines.append(f"Sleep-HRV correlation: {analysis['hrv_correlation']}")
    if analysis["needs_medical"]:
        lines.append("⚠️ 7-day avg below 5.5h — consider medical consultation")
    return "\n".join(lines)
