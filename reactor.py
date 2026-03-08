"""Autonomous Coaching Reactor — event-driven analysis, adaptation, and proactive alerts.

Runs on schedule (morning at 07:00) and on events (new activity detected).
Connects the intelligence modules (alerts, patterns, plan_adapter) to the coaching loop.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from intervals import IntervalsClient
    from database import Database
    from config import AthleteConfig

log = logging.getLogger("coach.reactor")


# ---------------------------------------------------------------------------
# Alert urgency classification
# ---------------------------------------------------------------------------
_CRITICAL_TYPES = {"overtraining_risk", "injury_risk", "hrv_crash", "rhr_spike"}
_WARNING_TYPES = {"sleep_debt", "high_ramp_rate", "compliance_drop", "sleep_crisis"}


class CoachingReactor:
    """Autonomous coaching reactor that analyzes data and triggers adaptations."""

    def __init__(
        self,
        iv: "IntervalsClient",
        db: "Database",
        athlete: "AthleteConfig",
        whoop=None,
        strava=None,
        state_machine=None,
    ):
        self.iv = iv
        self.db = db
        self.athlete = athlete
        self.whoop = whoop
        self.strava = strava
        self.state_machine = state_machine

    # ------------------------------------------------------------------
    # Morning reactor — runs at 07:00 before the 08:30 check-in
    # ------------------------------------------------------------------
    async def run_morning(self) -> dict:
        """Run the full morning analysis cycle.

        Returns a dict with:
          alerts, adaptation, today_plan, yesterday_review, weekly_load,
          brief (formatted string for check-in injection).
        """
        log.info("Morning reactor starting")
        result = {
            "alerts": [],
            "adaptation": None,
            "today_plan": None,
            "yesterday_review": None,
            "weekly_load": {},
            "brief": "",
        }

        try:
            # Fetch data
            wellness = await self.iv.wellness(days=30, force=True)
            activities = await self.iv.activities(days=30, force=True)
            today_str = date.today().isoformat()

            # 1. Generate alerts
            result["alerts"] = self._run_alerts(wellness, activities)

            # 2. Assess adaptation needs for today's session
            adaptation = await self._assess_today(wellness, activities)
            result["adaptation"] = adaptation

            # 3. If adaptation needed, update Intervals.icu calendar
            if adaptation and adaptation.get("action") != "proceed":
                await self._apply_adaptation(adaptation, today_str)

            # 4. Review yesterday's session
            result["yesterday_review"] = self._review_yesterday(activities, today_str)

            # 5. Weekly load summary
            result["weekly_load"] = self._weekly_load(activities)

            # 6. Evaluate training phase state machine
            if self.state_machine:
                recovery_score = 70  # default
                if adaptation:
                    # Infer recovery score from adaptation action
                    action = adaptation.get("action", "proceed")
                    if action == "rest":
                        recovery_score = 25
                    elif action == "reduce":
                        recovery_score = 40
                    elif action == "swap":
                        recovery_score = 50

                race_countdown = None
                if getattr(self.athlete, "race_date", None):
                    try:
                        days_to = (date.fromisoformat(self.athlete.race_date) - date.today()).days
                        race_countdown = days_to
                    except ValueError:
                        pass

                state_result = self.state_machine.evaluate(
                    alerts=result["alerts"],
                    recovery_score=recovery_score,
                    race_countdown=race_countdown,
                )
                result["state"] = state_result

            # 7. Build brief for check-in
            result["brief"] = self._build_morning_brief(result)

            # Store for check-in use
            self.db.set_state("reactor_morning_brief", result["brief"])
            self.db.set_state("reactor_last_run", datetime.now().isoformat())

            log.info(
                "Morning reactor complete: %d alerts, adaptation=%s",
                len(result["alerts"]),
                adaptation.get("action") if adaptation else "n/a",
            )

        except Exception as exc:
            log.error("Morning reactor failed: %s", exc)
            result["brief"] = f"[Reactor error: {exc}]"

        return result

    # ------------------------------------------------------------------
    # Post-activity reactor — called when a new Strava activity is detected
    # ------------------------------------------------------------------
    async def on_activity(self, activity: dict) -> dict:
        """Analyze a newly completed activity and generate coaching feedback.

        Returns a dict with:
          compliance, deviation, coaching_notes, adaptation (if needed for rest of week).
        """
        log.info("Post-activity reactor for: %s", activity.get("name", "?"))
        result = {
            "compliance": None,
            "deviation": {},
            "coaching_notes": [],
            "adaptation_triggered": False,
        }

        try:
            today_str = (activity.get("start_date_local") or "")[:10]

            # 1. Fetch today's planned events
            events = await self.iv.events(days_ahead=1, force=True)
            planned = self._find_planned_for_date(events, today_str)

            # 2. Compare actual vs planned
            if planned:
                deviation = self._compute_deviation(activity, planned)
                result["deviation"] = deviation
                result["coaching_notes"] = self._deviation_notes(deviation, activity)
            else:
                result["coaching_notes"].append("Unplanned session — no deviation to check.")

            # 3. Check if significant deviation warrants adaptation
            if result["deviation"].get("significant"):
                result["adaptation_triggered"] = True
                log.info("Significant deviation detected — adaptation may be needed for upcoming sessions")

        except Exception as exc:
            log.error("Post-activity reactor failed: %s", exc)
            result["coaching_notes"].append(f"Analysis incomplete: {exc}")

        return result

    # ------------------------------------------------------------------
    # Alert generation
    # ------------------------------------------------------------------
    def _run_alerts(self, wellness: list, activities: list) -> list:
        """Run alert detectors and return classified alerts."""
        try:
            from modules.alerts import generate_alerts
            config = {
                "rhr_baseline": getattr(self.athlete, "rhr_baseline", 45),
                "hrv_baseline": getattr(self.athlete, "hrv_baseline", 55),
                "sleep_target_hours": getattr(self.athlete, "sleep_target_hours", 7.5),
                "race_date": getattr(self.athlete, "race_date", None),
                "race_name": getattr(self.athlete, "race_name", None),
            }
            alerts = generate_alerts(wellness, activities, config)
            # Classify urgency
            for alert in alerts:
                atype = alert.get("type", "")
                if atype in _CRITICAL_TYPES or alert.get("severity") == "critical":
                    alert["urgency"] = "critical"
                elif atype in _WARNING_TYPES or alert.get("severity") == "warning":
                    alert["urgency"] = "warning"
                else:
                    alert["urgency"] = "info"
            return alerts
        except Exception as exc:
            log.error("Alert generation failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Adaptation assessment
    # ------------------------------------------------------------------
    async def _assess_today(self, wellness: list, activities: list) -> dict | None:
        """Assess whether today's planned session needs adaptation."""
        try:
            from modules.recovery import calculate_recovery_score
            from modules.sleep import analyze_sleep
            from modules.compliance import analyze_compliance
            from modules.performance import analyze_training
            from modules.plan_adapter import assess_adaptation_needs
            from modules.thresholds import PersonalizedThresholds

            if len(wellness) < 7:
                return {"action": "proceed", "reasons": ["Insufficient data (<7 days)"]}

            latest = wellness[-1] if wellness else {}

            # Compute recovery
            thr = PersonalizedThresholds(wellness, activities)
            baselines = {
                "hrv": thr.hrv_baseline,
                "rhr": thr.rhr_baseline,
                "sleep": thr.sleep_baseline,
            }
            recovery = calculate_recovery_score(latest, baselines)
            recovery_score = recovery.get("score", 70)

            # Sleep analysis
            sleep_analysis = analyze_sleep(wellness, getattr(self.athlete, "sleep_target_hours", 7.5))

            # Compliance
            plan_data = self.db.get_state("training_plan")
            compliance = {"compliance_rate": 100.0, "missed_days": [], "consecutive_missed": 0}
            if plan_data:
                try:
                    from engine_tools import _reconstruct_plan
                    from modules.periodization_calendar import PeriodizationCalendar
                    plan = _reconstruct_plan(plan_data)
                    cal = PeriodizationCalendar(self.iv)
                    comp_result = await cal.sync_completion(plan, activities)
                    total = comp_result.get("completed", 0) + comp_result.get("missed", 0)
                    if total > 0:
                        compliance["compliance_rate"] = (comp_result["completed"] / total) * 100
                    compliance["missed_days"] = [
                        d["date"] for d in comp_result.get("details", [])
                        if d.get("status") == "missed"
                    ]
                except Exception as exc:
                    log.warning("Compliance check failed: %s", exc)

            # Performance
            performance = analyze_training(wellness, activities, getattr(self.athlete, "race_date", None))

            # Run adaptation assessment
            adaptation = assess_adaptation_needs(
                recovery_score=recovery_score,
                sleep_analysis=sleep_analysis,
                compliance=compliance,
                performance=performance,
            )
            return adaptation

        except Exception as exc:
            log.error("Adaptation assessment failed: %s", exc)
            return {"action": "proceed", "reasons": [f"Assessment error: {exc}"]}

    async def _apply_adaptation(self, adaptation: dict, today_str: str):
        """Apply adaptation to today's Intervals.icu calendar event."""
        try:
            from modules.plan_adapter import adapt_workout_description

            events = await self.iv.events(days_ahead=1, force=True)
            today_event = None
            for e in events:
                edate = (e.get("start_date_local") or e.get("date") or "")[:10]
                cat = e.get("category", "")
                if edate == today_str and cat == "WORKOUT":
                    today_event = e
                    break

            if not today_event:
                log.info("No WORKOUT event found for %s — skipping adaptation", today_str)
                return

            event_id = today_event.get("id")
            description = today_event.get("description", "")
            intensity_mod = adaptation.get("intensity_modifier", 1.0)
            volume_mod = adaptation.get("volume_modifier", 1.0)

            # Rewrite the workout description with adjusted paces/durations
            new_description = adapt_workout_description(description, adaptation)

            # Build update for Intervals.icu
            updates = {"description": new_description}
            if adaptation.get("swap_suggestion"):
                updates["name"] = f"[Adapted] {today_event.get('name', '')}"

            await self.iv.update_event(event_id, updates)
            log.info(
                "Adapted event %s: action=%s, intensity=%.2f, volume=%.2f",
                event_id, adaptation["action"], intensity_mod, volume_mod,
            )

            # Store adaptation record
            self.db.set_state(f"adaptation_{today_str}", {
                "date": today_str,
                "action": adaptation["action"],
                "intensity_modifier": intensity_mod,
                "volume_modifier": volume_mod,
                "reasons": adaptation.get("reasons", []),
                "event_id": event_id,
                "timestamp": datetime.now().isoformat(),
            })

        except Exception as exc:
            log.error("Failed to apply adaptation: %s", exc)

    # ------------------------------------------------------------------
    # Yesterday review
    # ------------------------------------------------------------------
    def _review_yesterday(self, activities: list, today_str: str) -> dict | None:
        """Review yesterday's completed session."""
        yesterday = (date.fromisoformat(today_str) - timedelta(days=1)).isoformat()
        yesterday_acts = [
            a for a in activities
            if (a.get("start_date_local") or a.get("date") or "")[:10] == yesterday
            and a.get("type")
        ]
        if not yesterday_acts:
            return {"date": yesterday, "status": "rest_day", "summary": "Rest day"}

        summaries = []
        for a in yesterday_acts:
            tss = a.get("icu_training_load") or a.get("suffer_score") or 0
            dur = (a.get("moving_time") or 0) // 60
            summaries.append({
                "type": a.get("type"),
                "name": a.get("name", ""),
                "duration_min": dur,
                "tss": tss,
                "avg_hr": a.get("average_heartrate"),
                "distance_km": round((a.get("distance") or 0) / 1000, 1),
            })
        return {"date": yesterday, "status": "completed", "sessions": summaries}

    # ------------------------------------------------------------------
    # Weekly load
    # ------------------------------------------------------------------
    def _weekly_load(self, activities: list) -> dict:
        """Aggregate this week's training load."""
        week_ago = (date.today() - timedelta(days=7)).isoformat()
        total_tss = 0
        total_dur = 0
        count = 0
        for a in activities:
            adate = (a.get("start_date_local") or a.get("date") or "")[:10]
            if adate >= week_ago and a.get("type"):
                total_tss += a.get("icu_training_load") or a.get("suffer_score") or 0
                total_dur += (a.get("moving_time") or 0) // 60
                count += 1

        # Get target from training plan
        target_tss = 0
        plan_data = self.db.get_state("training_plan")
        if plan_data:
            try:
                from engine_tools import _reconstruct_plan
                plan = _reconstruct_plan(plan_data)
                today_str = date.today().isoformat()
                for meso in plan.mesocycles:
                    for mc in meso.microcycles:
                        dates = [s.date for s in mc.sessions]
                        if dates and min(dates) <= today_str <= max(dates):
                            target_tss = mc.target_weekly_tss or 0
                            break
            except Exception:
                pass

        return {
            "total_tss": round(total_tss, 1),
            "total_duration_min": total_dur,
            "activity_count": count,
            "target_tss": target_tss,
            "pct_complete": round(total_tss / target_tss * 100, 1) if target_tss else 0,
        }

    # ------------------------------------------------------------------
    # Deviation detection (actual vs planned)
    # ------------------------------------------------------------------
    def _find_planned_for_date(self, events: list, date_str: str) -> dict | None:
        """Find the planned WORKOUT event for a given date."""
        for e in events:
            edate = (e.get("start_date_local") or e.get("date") or "")[:10]
            if edate == date_str and e.get("category") == "WORKOUT":
                return e
        return None

    def _compute_deviation(self, actual: dict, planned: dict) -> dict:
        """Compare actual activity metrics with planned event."""
        actual_tss = actual.get("icu_training_load") or actual.get("suffer_score") or 0
        planned_tss = planned.get("icu_training_load") or 0
        actual_dur = (actual.get("moving_time") or 0) // 60
        planned_dur = (planned.get("moving_time") or 0) // 60

        tss_delta = actual_tss - planned_tss if planned_tss else 0
        dur_delta = actual_dur - planned_dur if planned_dur else 0

        # Significant if TSS deviates by more than 20% or duration by more than 15 min
        significant = False
        if planned_tss and abs(tss_delta) / planned_tss > 0.2:
            significant = True
        if abs(dur_delta) > 15:
            significant = True

        return {
            "actual_tss": actual_tss,
            "planned_tss": planned_tss,
            "tss_delta": tss_delta,
            "actual_duration_min": actual_dur,
            "planned_duration_min": planned_dur,
            "duration_delta_min": dur_delta,
            "significant": significant,
        }

    def _deviation_notes(self, deviation: dict, activity: dict) -> list[str]:
        """Generate coaching notes from deviation analysis."""
        notes = []
        tss_d = deviation.get("tss_delta", 0)
        dur_d = deviation.get("duration_delta_min", 0)
        avg_hr = activity.get("average_heartrate")

        if tss_d > 0 and deviation.get("planned_tss"):
            pct = abs(tss_d) / deviation["planned_tss"] * 100
            notes.append(f"Session was {pct:.0f}% harder than planned (TSS +{tss_d:.0f})")
        elif tss_d < 0 and deviation.get("planned_tss"):
            pct = abs(tss_d) / deviation["planned_tss"] * 100
            notes.append(f"Session was {pct:.0f}% easier than planned (TSS {tss_d:.0f})")

        if dur_d > 15:
            notes.append(f"Ran {dur_d}min longer than planned")
        elif dur_d < -15:
            notes.append(f"Cut {abs(dur_d)}min short from planned duration")

        if avg_hr and avg_hr > getattr(self.athlete, "hr_at_mp", 160):
            notes.append(f"Heart rate ({avg_hr:.0f}) exceeded marathon pace HR target")

        return notes

    # ------------------------------------------------------------------
    # Brief builders
    # ------------------------------------------------------------------
    def _build_morning_brief(self, reactor_result: dict) -> str:
        """Build a concise morning brief for the check-in prompt."""
        lines = ["## Morning Coaching Brief (auto-generated by reactor)\n"]

        # Alerts
        alerts = reactor_result.get("alerts", [])
        if alerts:
            critical = [a for a in alerts if a.get("urgency") == "critical"]
            warnings = [a for a in alerts if a.get("urgency") == "warning"]
            if critical:
                lines.append("**CRITICAL ALERTS:**")
                for a in critical:
                    lines.append(f"- {a.get('title', '?')}: {a.get('message', '')[:100]}")
            if warnings:
                lines.append("**Warnings:**")
                for a in warnings:
                    lines.append(f"- {a.get('title', '?')}: {a.get('message', '')[:100]}")
            lines.append("")

        # Adaptation
        adaptation = reactor_result.get("adaptation")
        if adaptation and adaptation.get("action") != "proceed":
            lines.append(f"**Today's plan adapted** ({adaptation['action']}):")
            for r in adaptation.get("reasons", []):
                lines.append(f"- {r}")
            i_mod = adaptation.get("intensity_modifier", 1.0)
            v_mod = adaptation.get("volume_modifier", 1.0)
            if i_mod < 1.0 or v_mod < 1.0:
                lines.append(f"- Intensity: {i_mod:.0%} | Volume: {v_mod:.0%}")
            lines.append("")
        elif adaptation:
            lines.append("**Today's plan: proceed as planned.**\n")

        # Yesterday
        yesterday = reactor_result.get("yesterday_review")
        if yesterday:
            if yesterday.get("status") == "rest_day":
                lines.append(f"**Yesterday ({yesterday['date']}):** Rest day\n")
            else:
                sessions = yesterday.get("sessions", [])
                for s in sessions:
                    lines.append(
                        f"**Yesterday:** {s['type']} — {s['name']} | "
                        f"{s['duration_min']}min | TSS {s['tss']:.0f} | "
                        f"{s['distance_km']}km"
                    )
                lines.append("")

        # Weekly load
        wl = reactor_result.get("weekly_load", {})
        if wl.get("total_tss"):
            target = wl.get("target_tss")
            if target:
                lines.append(
                    f"**Weekly load:** {wl['total_tss']:.0f}/{target:.0f} TSS "
                    f"({wl['pct_complete']:.0f}%) | {wl['total_duration_min']}min | "
                    f"{wl['activity_count']} sessions"
                )
            else:
                lines.append(
                    f"**Weekly load:** {wl['total_tss']:.0f} TSS | "
                    f"{wl['total_duration_min']}min | {wl['activity_count']} sessions"
                )
            lines.append("")

        # Training phase (state machine)
        if self.state_machine:
            lines.append(self.state_machine.format_state_brief())
            state_result = reactor_result.get("state", {})
            if state_result.get("changed"):
                lines.append(
                    f"  *State changed:* {state_result['previous']} -> {state_result['state']} "
                    f"({state_result.get('reason', '')})"
                )
            lines.append("")

        # Race countdown
        if getattr(self.athlete, "race_date", None):
            try:
                days_to = (date.fromisoformat(self.athlete.race_date) - date.today()).days
                lines.append(f"**Race:** {self.athlete.race_name} in {days_to} days")
            except ValueError:
                pass

        return "\n".join(lines)

    def build_evening_brief(self, wellness: list = None, activities: list = None) -> str:
        """Build evening brief with today's compliance + tomorrow's preview."""
        lines = ["## Evening Coaching Brief\n"]
        today_str = date.today().isoformat()

        # Today's compliance
        if activities:
            today_acts = [
                a for a in activities
                if (a.get("start_date_local") or "")[:10] == today_str
                and a.get("type")
            ]
            if today_acts:
                for a in today_acts:
                    tss = a.get("icu_training_load") or a.get("suffer_score") or 0
                    dur = (a.get("moving_time") or 0) // 60
                    lines.append(
                        f"**Completed today:** {a['type']} — {a.get('name', '')} | "
                        f"{dur}min | TSS {tss:.0f}"
                    )
            else:
                lines.append("**Today:** No training session recorded.")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Alert push helpers
    # ------------------------------------------------------------------
    def get_critical_alerts(self, alerts: list = None) -> list:
        """Filter alerts to critical-only, with dedup check against recent pushes."""
        if alerts is None:
            return []

        critical = [a for a in alerts if a.get("urgency") == "critical"]
        if not critical:
            return []

        # Dedup: check if we already pushed this alert type in the last 12 hours
        recent = self.db.get_state("recent_alert_pushes") or {}
        cutoff = (datetime.now() - timedelta(hours=12)).isoformat()
        deduped = []
        for alert in critical:
            atype = alert.get("type", "unknown")
            last_push = recent.get(atype, "")
            if last_push < cutoff:
                deduped.append(alert)

        return deduped

    def mark_alert_pushed(self, alert: dict):
        """Record that an alert was pushed to prevent re-alerting."""
        recent = self.db.get_state("recent_alert_pushes") or {}
        recent[alert.get("type", "unknown")] = datetime.now().isoformat()
        self.db.set_state("recent_alert_pushes", recent)

    def format_alert_message(self, alert: dict) -> str:
        """Format an alert for Telegram push notification."""
        severity = alert.get("severity", "info").upper()
        title = alert.get("title", "Alert")
        message = alert.get("message", "")
        return f"*[{severity}] {title}*\n\n{message}"
