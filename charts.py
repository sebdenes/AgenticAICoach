"""Chart generation for Telegram — sleep, fitness, performance visuals."""

from __future__ import annotations

import io
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("coach.charts")

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    HAS_MPL = True
except ImportError:
    HAS_MPL = False
    log.warning("matplotlib not installed — charts disabled")


COLORS = {
    "green": "#2ecc71",
    "yellow": "#f1c40f",
    "red": "#e74c3c",
    "blue": "#3498db",
    "purple": "#9b59b6",
    "dark": "#2c3e50",
    "gray": "#95a5a6",
    "bg": "#1a1a2e",
    "grid": "#333355",
}


def _style_ax(ax):
    ax.set_facecolor(COLORS["bg"])
    ax.tick_params(colors=COLORS["gray"], labelsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(COLORS["grid"])
    ax.spines["left"].set_color(COLORS["grid"])
    ax.grid(True, alpha=0.2, color=COLORS["grid"])


def _to_bytes(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=COLORS["bg"], edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def sleep_chart(wellness_data: list) -> bytes | None:
    """Generate a sleep trend chart."""
    if not HAS_MPL or not wellness_data:
        return None

    dates, hours, scores = [], [], []
    for d in wellness_data:
        sleep_s = d.get("sleepSecs", 0) or d.get("sleep_seconds", 0) or 0
        if sleep_s > 0:
            dt = datetime.strptime(d.get("id", d.get("date", ""))[:10], "%Y-%m-%d")
            dates.append(dt)
            hours.append(sleep_s / 3600)
            scores.append(d.get("sleepScore", d.get("sleep_score")) or 0)

    if not dates:
        return None

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 5), sharex=True)
    fig.patch.set_facecolor(COLORS["bg"])

    # Sleep hours
    _style_ax(ax1)
    bar_colors = [COLORS["green"] if h >= 7 else COLORS["yellow"] if h >= 6 else COLORS["red"] for h in hours]
    ax1.bar(dates, hours, color=bar_colors, alpha=0.8, width=0.6)
    ax1.axhline(y=7.5, color=COLORS["green"], linestyle="--", alpha=0.5, label="Target 7.5h")
    ax1.set_ylabel("Hours", color=COLORS["gray"], fontsize=9)
    ax1.set_title("Sleep Duration", color="white", fontsize=11, fontweight="bold")
    ax1.legend(fontsize=7, facecolor=COLORS["bg"], edgecolor=COLORS["grid"], labelcolor=COLORS["gray"])

    # Sleep score
    _style_ax(ax2)
    ax2.plot(dates, scores, color=COLORS["blue"], marker="o", markersize=4, linewidth=1.5)
    ax2.fill_between(dates, scores, alpha=0.15, color=COLORS["blue"])
    ax2.set_ylabel("Score", color=COLORS["gray"], fontsize=9)
    ax2.set_title("Sleep Score", color="white", fontsize=11, fontweight="bold")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    plt.xticks(rotation=45)

    fig.tight_layout()
    return _to_bytes(fig)


def fitness_chart(wellness_data: list) -> bytes | None:
    """Generate CTL/ATL/TSB fitness chart."""
    if not HAS_MPL or not wellness_data:
        return None

    dates, ctls, atls, tsbs = [], [], [], []
    for d in wellness_data:
        ctl = d.get("ctl", 0)
        atl = d.get("atl", 0)
        if ctl or atl:
            dt = datetime.strptime(d.get("id", d.get("date", ""))[:10], "%Y-%m-%d")
            dates.append(dt)
            ctls.append(ctl)
            atls.append(atl)
            tsbs.append(ctl - atl)

    if not dates:
        return None

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 5), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    fig.patch.set_facecolor(COLORS["bg"])

    # CTL + ATL
    _style_ax(ax1)
    ax1.plot(dates, ctls, color=COLORS["blue"], linewidth=2, label="Fitness (CTL)")
    ax1.plot(dates, atls, color=COLORS["purple"], linewidth=1.5, alpha=0.7, label="Fatigue (ATL)")
    ax1.fill_between(dates, ctls, alpha=0.1, color=COLORS["blue"])
    ax1.set_ylabel("Load", color=COLORS["gray"], fontsize=9)
    ax1.set_title("Fitness & Fatigue", color="white", fontsize=11, fontweight="bold")
    ax1.legend(fontsize=7, facecolor=COLORS["bg"], edgecolor=COLORS["grid"], labelcolor=COLORS["gray"])

    # TSB (form)
    _style_ax(ax2)
    tsb_colors = [COLORS["green"] if t > 5 else COLORS["yellow"] if t > -10 else COLORS["red"] for t in tsbs]
    ax2.bar(dates, tsbs, color=tsb_colors, alpha=0.8, width=0.6)
    ax2.axhline(y=0, color=COLORS["gray"], linewidth=0.5)
    ax2.set_ylabel("TSB", color=COLORS["gray"], fontsize=9)
    ax2.set_title("Form (TSB)", color="white", fontsize=11, fontweight="bold")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    plt.xticks(rotation=45)

    fig.tight_layout()
    return _to_bytes(fig)


def hrv_chart(wellness_data: list) -> bytes | None:
    """Generate HRV + RHR trend chart."""
    if not HAS_MPL or not wellness_data:
        return None

    dates, hrvs, rhrs = [], [], []
    for d in wellness_data:
        hrv = d.get("hrv")
        rhr = d.get("restingHR") or d.get("rhr")
        if hrv:
            dt = datetime.strptime(d.get("id", d.get("date", ""))[:10], "%Y-%m-%d")
            dates.append(dt)
            hrvs.append(hrv)
            rhrs.append(rhr or 0)

    if not dates:
        return None

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 5), sharex=True)
    fig.patch.set_facecolor(COLORS["bg"])

    # HRV
    _style_ax(ax1)
    ax1.plot(dates, hrvs, color=COLORS["green"], marker="o", markersize=3, linewidth=1.5)
    ax1.fill_between(dates, hrvs, alpha=0.1, color=COLORS["green"])
    ax1.axhline(y=57, color=COLORS["green"], linestyle="--", alpha=0.4, label="Baseline 57ms")
    ax1.set_ylabel("HRV (ms)", color=COLORS["gray"], fontsize=9)
    ax1.set_title("HRV Trend", color="white", fontsize=11, fontweight="bold")
    ax1.legend(fontsize=7, facecolor=COLORS["bg"], edgecolor=COLORS["grid"], labelcolor=COLORS["gray"])

    # RHR
    _style_ax(ax2)
    rhr_filtered = [r for r in rhrs if r > 0]
    dates_filtered = [d for d, r in zip(dates, rhrs) if r > 0]
    if rhr_filtered:
        ax2.plot(dates_filtered, rhr_filtered, color=COLORS["red"], marker="o", markersize=3, linewidth=1.5)
        ax2.axhline(y=42, color=COLORS["red"], linestyle="--", alpha=0.4, label="Baseline 42bpm")
        ax2.set_ylabel("RHR (bpm)", color=COLORS["gray"], fontsize=9)
        ax2.set_title("Resting Heart Rate", color="white", fontsize=11, fontweight="bold")
        ax2.legend(fontsize=7, facecolor=COLORS["bg"], edgecolor=COLORS["grid"], labelcolor=COLORS["gray"])
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    plt.xticks(rotation=45)

    fig.tight_layout()
    return _to_bytes(fig)


def weekly_load_chart(activities: list) -> bytes | None:
    """Generate weekly training load distribution chart."""
    if not HAS_MPL or not activities:
        return None

    # Group by week
    weeks = {}
    for a in activities:
        if not a.get("type"):
            continue
        date_str = a.get("start_date_local", a.get("date", ""))[:10]
        if not date_str:
            continue
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        week_start = dt - __import__("datetime").timedelta(days=dt.weekday())
        week_key = week_start.strftime("%b %d")
        if week_key not in weeks:
            weeks[week_key] = {"run": 0, "ride": 0, "other": 0}
        tss = a.get("icu_training_load", 0) or a.get("tss", 0) or 0
        atype = (a.get("type", "") or "").lower()
        if "run" in atype:
            weeks[week_key]["run"] += tss
        elif "ride" in atype:
            weeks[week_key]["ride"] += tss
        else:
            weeks[week_key]["other"] += tss

    if not weeks:
        return None

    labels = list(weeks.keys())
    run_tss = [weeks[w]["run"] for w in labels]
    ride_tss = [weeks[w]["ride"] for w in labels]
    other_tss = [weeks[w]["other"] for w in labels]

    fig, ax = plt.subplots(figsize=(8, 4))
    fig.patch.set_facecolor(COLORS["bg"])
    _style_ax(ax)

    x = range(len(labels))
    ax.bar(x, run_tss, color=COLORS["green"], label="Run", alpha=0.8)
    ax.bar(x, ride_tss, bottom=run_tss, color=COLORS["blue"], label="Ride", alpha=0.8)
    combined = [r + ri for r, ri in zip(run_tss, ride_tss)]
    ax.bar(x, other_tss, bottom=combined, color=COLORS["purple"], label="Other", alpha=0.8)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, color=COLORS["gray"])
    ax.set_ylabel("TSS", color=COLORS["gray"], fontsize=9)
    ax.set_title("Weekly Training Load by Sport", color="white", fontsize=11, fontweight="bold")
    ax.legend(fontsize=7, facecolor=COLORS["bg"], edgecolor=COLORS["grid"], labelcolor=COLORS["gray"])

    fig.tight_layout()
    return _to_bytes(fig)
