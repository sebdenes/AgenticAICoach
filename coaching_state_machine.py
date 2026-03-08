"""Coaching state machine — tracks training phase lifecycle with data-driven transitions."""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from database import Database
    from config import AthleteConfig

log = logging.getLogger("coach.state_machine")

# ── State definitions ────────────────────────────────────────────────────────

STATES = {
    "healthy_loading": {
        "max_tss_pct": 1.0,
        "tone": "push",
        "description": "Normal training load — push when data supports it",
    },
    "recovery_week": {
        "max_tss_pct": 0.6,
        "tone": "gentle",
        "description": "Planned recovery — reduced volume and intensity",
    },
    "adaptation_needed": {
        "max_tss_pct": 0.8,
        "tone": "cautious",
        "description": "Alerts detected — monitor closely and reduce if needed",
    },
    "injury_risk": {
        "max_tss_pct": 0.5,
        "tone": "protective",
        "description": "High injury risk — prioritize recovery over fitness",
    },
    "taper": {
        "max_tss_pct": 0.55,
        "tone": "confident",
        "description": "Race taper — reduce volume, maintain sharpness",
    },
    "race_week": {
        "max_tss_pct": 0.3,
        "tone": "focused",
        "description": "Race week — minimal load, race prep focus",
    },
    "post_race": {
        "max_tss_pct": 0.4,
        "tone": "recovery",
        "description": "Post-race recovery — easy movement only",
    },
}

DEFAULT_STATE = "healthy_loading"


class CoachingStateMachine:
    """Data-driven training phase state machine.

    Reads/writes current state via Database coaching_state KV store.
    Evaluate transitions based on alerts, recovery score, and race countdown.
    """

    def __init__(self, db: "Database", athlete: "AthleteConfig"):
        self.db = db
        self.athlete = athlete

    @property
    def current_state(self) -> str:
        """Read current training phase from DB."""
        return self.db.get_state("training_phase") or DEFAULT_STATE

    def get_state_config(self) -> dict:
        """Return current state's configuration parameters."""
        state = self.current_state
        config = dict(STATES.get(state, STATES[DEFAULT_STATE]))
        config["state"] = state
        return config

    def evaluate(
        self,
        alerts: list,
        recovery_score: float = 70,
        race_countdown: int | None = None,
    ) -> dict:
        """Evaluate whether a state transition is needed.

        Args:
            alerts: List of alert dicts from generate_alerts()
            recovery_score: Current recovery score (0-100)
            race_countdown: Days until race (None if no race set)

        Returns:
            dict with {state, changed, previous, reason, config}
        """
        current = self.current_state
        new_state = current
        reason = ""

        # Race-driven transitions take priority
        if race_countdown is not None:
            if race_countdown <= 0:
                new_state = "post_race"
                reason = "Race day has passed"
            elif race_countdown <= 7:
                new_state = "race_week"
                reason = f"Race in {race_countdown} days"
            elif race_countdown <= 14:
                new_state = "taper"
                reason = f"Taper period — {race_countdown} days to race"

        # If no race-driven transition, evaluate data-driven transitions
        if new_state == current:
            critical_alerts = [
                a for a in alerts
                if a.get("urgency") == "critical" or a.get("severity") == "critical"
            ]
            warning_alerts = [
                a for a in alerts
                if a.get("urgency") == "warning" or a.get("severity") == "warning"
            ]

            if current == "healthy_loading":
                if critical_alerts and recovery_score < 35:
                    new_state = "injury_risk"
                    reason = f"Critical alerts + low recovery ({recovery_score:.0f})"
                elif critical_alerts or recovery_score < 40:
                    new_state = "adaptation_needed"
                    titles = [a.get("title", "?") for a in critical_alerts[:2]]
                    reason = f"Alerts: {', '.join(titles)}" if titles else f"Low recovery ({recovery_score:.0f})"

            elif current == "adaptation_needed":
                if critical_alerts and recovery_score < 35:
                    new_state = "injury_risk"
                    reason = f"Escalated — critical alerts persist, recovery {recovery_score:.0f}"
                elif not critical_alerts and not warning_alerts and recovery_score > 60:
                    new_state = "healthy_loading"
                    reason = "No alerts, recovery improved"

            elif current == "injury_risk":
                if not critical_alerts and recovery_score > 50:
                    new_state = "adaptation_needed"
                    reason = "De-escalated — recovery improving"

            elif current == "recovery_week":
                if not critical_alerts and recovery_score > 65:
                    new_state = "healthy_loading"
                    reason = "Recovery complete — resuming normal load"

            elif current == "post_race":
                if recovery_score > 70:
                    new_state = "healthy_loading"
                    reason = "Post-race recovery complete"

        changed = new_state != current
        if changed:
            self.transition(new_state, reason)
            log.info(
                "State transition: %s -> %s (reason: %s)", current, new_state, reason
            )

        return {
            "state": new_state,
            "changed": changed,
            "previous": current if changed else None,
            "reason": reason if changed else None,
            "config": STATES.get(new_state, STATES[DEFAULT_STATE]),
        }

    def transition(self, new_state: str, reason: str):
        """Persist a state change to DB."""
        if new_state not in STATES:
            log.warning("Invalid state: %s", new_state)
            return
        previous = self.current_state
        self.db.set_state("training_phase", new_state)
        self.db.set_state("training_phase_changed_at", datetime.now().isoformat())
        self.db.set_state("training_phase_reason", reason)
        self.db.set_state("training_phase_previous", previous)

    def force_state(self, state: str, reason: str = "Manual override"):
        """Force a specific state (e.g., for manual recovery week scheduling)."""
        if state not in STATES:
            log.warning("Invalid state: %s", state)
            return
        self.transition(state, reason)
        log.info("Forced state: %s (reason: %s)", state, reason)

    def format_state_brief(self) -> str:
        """Format current state for inclusion in coaching brief."""
        state = self.current_state
        config = STATES.get(state, STATES[DEFAULT_STATE])
        changed_at = self.db.get_state("training_phase_changed_at") or ""
        reason = self.db.get_state("training_phase_reason") or ""

        days_in_state = ""
        if changed_at:
            try:
                entered = datetime.fromisoformat(changed_at)
                days = (datetime.now() - entered).days
                days_in_state = f" (day {days + 1})"
            except ValueError:
                pass

        line = f"**Phase:** {state.replace('_', ' ').title()}{days_in_state}"
        if reason:
            line += f" — {reason}"
        line += f"\n  TSS cap: {config['max_tss_pct']:.0%} | Tone: {config['tone']}"
        return line
