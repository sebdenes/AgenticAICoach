"""Recovery module — composite recovery scoring and readiness assessment."""

from datetime import datetime


def calculate_recovery_score(wellness_entry: dict, baselines: dict) -> dict:
    """Calculate a composite recovery score from multiple signals."""
    score = 100  # Start at 100, deduct for risk factors
    signals = []

    # Sleep
    sleep_h = (wellness_entry.get("sleepSecs", 0) or wellness_entry.get("sleep_seconds", 0) or 0) / 3600
    if sleep_h > 0:
        if sleep_h < 5:
            score -= 30
            signals.append(f"Sleep critical: {sleep_h:.1f}h")
        elif sleep_h < 6:
            score -= 20
            signals.append(f"Sleep poor: {sleep_h:.1f}h")
        elif sleep_h < 7:
            score -= 10
            signals.append(f"Sleep suboptimal: {sleep_h:.1f}h")
        else:
            signals.append(f"Sleep good: {sleep_h:.1f}h")

    # HRV
    hrv = wellness_entry.get("hrv")
    hrv_baseline = baselines.get("hrv", 57)
    if hrv:
        hrv_pct = (hrv - hrv_baseline) / hrv_baseline * 100
        if hrv_pct < -20:
            score -= 20
            signals.append(f"HRV suppressed: {hrv:.0f} ({hrv_pct:+.0f}% vs baseline)")
        elif hrv_pct < -10:
            score -= 10
            signals.append(f"HRV below baseline: {hrv:.0f} ({hrv_pct:+.0f}%)")
        else:
            signals.append(f"HRV OK: {hrv:.0f} ({hrv_pct:+.0f}%)")

    # RHR
    rhr = wellness_entry.get("restingHR") or wellness_entry.get("rhr")
    rhr_baseline = baselines.get("rhr", 42)
    if rhr:
        rhr_diff = rhr - rhr_baseline
        if rhr_diff > 8:
            score -= 15
            signals.append(f"RHR elevated: {rhr} (+{rhr_diff} vs baseline)")
        elif rhr_diff > 4:
            score -= 8
            signals.append(f"RHR slightly high: {rhr} (+{rhr_diff})")
        else:
            signals.append(f"RHR normal: {rhr}")

    # TSB (form)
    ctl = wellness_entry.get("ctl", 0)
    atl = wellness_entry.get("atl", 0)
    tsb = ctl - atl
    if tsb < -30:
        score -= 20
        signals.append(f"Deep fatigue: TSB {tsb:.0f}")
    elif tsb < -15:
        score -= 10
        signals.append(f"Accumulated fatigue: TSB {tsb:.0f}")
    elif tsb > 15:
        signals.append(f"Well rested: TSB {tsb:.0f}")
    else:
        signals.append(f"TSB: {tsb:.0f}")

    # Clamp
    score = max(0, min(100, score))

    # Grade
    if score >= 80:
        grade = "green"
        recommendation = "Full training — push when the plan says push"
    elif score >= 60:
        grade = "yellow"
        recommendation = "Modified training — reduce intensity or volume"
    else:
        grade = "red"
        recommendation = "Easy or rest day — recovery is the priority"

    return {
        "score": score,
        "grade": grade,
        "recommendation": recommendation,
        "signals": signals,
        "sleep_hours": round(sleep_h, 1),
        "hrv": hrv,
        "rhr": rhr,
        "tsb": round(tsb, 1),
    }


def format_recovery_context(recovery: dict) -> str:
    emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(recovery["grade"], "⚪")
    lines = [
        f"RECOVERY: {emoji} {recovery['score']}/100 ({recovery['grade']})",
        f"Recommendation: {recovery['recommendation']}",
        "Signals: " + " | ".join(recovery["signals"]),
    ]
    return "\n".join(lines)
