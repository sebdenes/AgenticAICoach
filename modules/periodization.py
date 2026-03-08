"""Periodization engine -- structured training plan generation for marathon preparation.

Generates multi-week training plans with mesocycle/microcycle structure,
progressive overload, recovery weeks, and taper. Integrates with the
knowledge base, personalized thresholds, and plan adapter for
evidence-based coaching.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

log = logging.getLogger("coach.periodization")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TrainingSession:
    """A single planned training session."""
    date: str                    # "2026-03-15"
    session_type: str            # "easy_run", "tempo_run", "long_run", "intervals",
                                 # "marathon_pace", "recovery_run", "strength",
                                 # "ride_easy", "ride_endurance", "rest"
    sport: str                   # "Run", "Ride", "Workout", "Note"
    name: str                    # "Easy Run"
    description: str             # Full workout description with paces, HR targets
    duration_minutes: int        # Planned duration
    target_tss: float            # Expected TSS
    intensity_zone: str          # "z1", "z2", "z3", "z4", "z5"
    is_key_session: bool         # True for long runs, tempo, intervals
    priority: int                # 1=must do, 2=important, 3=nice to have
    adaptable: bool = True       # Can be moved/reduced?


@dataclass
class Microcycle:
    """A single training week."""
    week_number: int             # 1-indexed from plan start
    start_date: str              # Monday of the week
    end_date: str                # Sunday
    phase: str                   # "base", "build", "peak", "taper", "recovery", "race"
    theme: str                   # "Rebuild frequency", "Add quality", etc.
    target_weekly_tss: float
    target_run_km: float
    sessions: list[TrainingSession]
    is_recovery_week: bool


@dataclass
class Mesocycle:
    """A block of training (multiple weeks)."""
    name: str                    # "Base Building", "Specific Preparation", "Taper"
    phase: str
    microcycles: list[Microcycle]
    start_date: str
    end_date: str


@dataclass
class TrainingPlan:
    """Complete training plan from now to race day."""
    athlete_name: str
    race_name: str
    race_date: str
    goal_time: str
    mesocycles: list[Mesocycle]
    created_at: str
    version: int = 1


# ---------------------------------------------------------------------------
# Phase themes
# ---------------------------------------------------------------------------

_PHASE_THEMES = {
    "base": [
        "Rebuild aerobic base",
        "Establish frequency",
        "Build consistency",
        "Extend duration",
    ],
    "build": [
        "Add quality sessions",
        "Introduce tempo work",
        "Marathon pace development",
        "Peak quality",
    ],
    "peak": [
        "Race-specific fitness",
        "Final long run",
    ],
    "taper": [
        "Volume reduction",
        "Sharpening",
        "Race preparation",
    ],
    "recovery": [
        "Absorb training load",
    ],
    "race": [
        "Race week",
    ],
}

# Mapping from session_type to sport
_SESSION_SPORT = {
    "easy_run": "Run",
    "tempo_run": "Run",
    "long_run": "Run",
    "intervals": "Run",
    "marathon_pace": "Run",
    "recovery_run": "Run",
    "strength": "Workout",
    "ride_easy": "Ride",
    "ride_endurance": "Ride",
    "rest": "Note",
}

# Default TSS for session types (per minute of effort)
_TSS_PER_MINUTE = {
    "easy_run": 0.75,
    "tempo_run": 1.2,
    "long_run": 0.8,
    "intervals": 1.4,
    "marathon_pace": 1.1,
    "recovery_run": 0.55,
    "strength": 0.6,
    "ride_easy": 0.6,
    "ride_endurance": 0.75,
    "rest": 0.0,
}


# ---------------------------------------------------------------------------
# PeriodizationEngine
# ---------------------------------------------------------------------------

class PeriodizationEngine:
    """Generate and manage structured training plans for marathon preparation.

    Integrates knowledge base rules, personalized thresholds, and the plan
    adapter to produce evidence-based periodized training.
    """

    def __init__(
        self,
        athlete,
        thresholds=None,
        knowledge_base=None,
    ):
        """Initialize the engine.

        Parameters
        ----------
        athlete : config.AthleteConfig
            Athlete configuration with race details and pace targets.
        thresholds : modules.thresholds.PersonalizedThresholds | None
            Personalized baselines for load calibration.
        knowledge_base : modules.knowledge_base.KnowledgeBase | None
            Evidence-based rules for periodization decisions.
        """
        self._athlete = athlete
        self._thresholds = thresholds
        self._kb = knowledge_base

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_plan(
        self,
        current_ctl: float,
        weeks_available: int | None = None,
    ) -> TrainingPlan:
        """Generate a full training plan working backward from race_date.

        Parameters
        ----------
        current_ctl : float
            Athlete's current CTL (chronic training load).
        weeks_available : int | None
            Number of weeks to plan. If not given, computed from today to
            race_date.

        Returns
        -------
        TrainingPlan
        """
        today = datetime.now().date()
        if weeks_available is None:
            race_dt = datetime.strptime(self._athlete.race_date, "%Y-%m-%d").date()
            delta = (race_dt - today).days
            weeks_available = max(1, delta // 7)

        # 1. Assign phases
        phases = self._assign_phases(weeks_available)

        # 2. Compute weekly TSS targets
        tss_targets = self._compute_weekly_tss_progression(phases, current_ctl * 7)

        # 3. Build microcycles and group into mesocycles
        mesocycles: list[Mesocycle] = []
        # Determine the Monday of the first training week
        plan_start = _next_monday(today)
        week_idx = 0
        # Track phases for each week to find last load week's TSS
        week_phases: list[str] = []
        for pn, nw in phases:
            week_phases.extend([pn] * nw)

        for phase_name, num_weeks in phases:
            phase_microcycles: list[Microcycle] = []
            for i in range(num_weeks):
                week_start = plan_start + timedelta(weeks=week_idx)
                week_end = week_start + timedelta(days=6)
                is_recovery = (phase_name == "recovery")
                target_tss = tss_targets[week_idx] if week_idx < len(tss_targets) else tss_targets[-1]
                # For the 10% cap, use the last LOAD week's TSS (skip recovery weeks)
                prev_tss = target_tss
                if week_idx > 0:
                    for j in range(week_idx - 1, -1, -1):
                        if week_phases[j] not in ("recovery", "taper", "race"):
                            prev_tss = tss_targets[j]
                            break
                    else:
                        prev_tss = tss_targets[week_idx - 1]

                mc = self._generate_microcycle(
                    week_num=week_idx + 1,
                    phase=phase_name,
                    target_tss=target_tss,
                    prev_week_tss=prev_tss,
                    start_date=week_start.strftime("%Y-%m-%d"),
                    is_recovery=is_recovery,
                )
                phase_microcycles.append(mc)
                week_idx += 1

            # Determine mesocycle name
            meso_name = _mesocycle_name(phase_name)
            meso_start = phase_microcycles[0].start_date
            meso_end = phase_microcycles[-1].end_date
            mesocycles.append(Mesocycle(
                name=meso_name,
                phase=phase_name,
                microcycles=phase_microcycles,
                start_date=meso_start,
                end_date=meso_end,
            ))

        return TrainingPlan(
            athlete_name=self._athlete.name,
            race_name=self._athlete.race_name,
            race_date=self._athlete.race_date,
            goal_time=self._athlete.goal_time,
            mesocycles=mesocycles,
            created_at=datetime.now().isoformat(),
            version=1,
        )

    def _assign_phases(self, weeks: int) -> list[tuple[str, int]]:
        """Decide phase allocation.

        The total weeks MUST equal the input. Recovery weeks are budgeted
        from the total (3:1 pattern — every 4th week is recovery).

        Rules:
        - Race week: last week (always 1 week)
        - Taper: 2 weeks for <= 8 total, 3 weeks for > 12, else 2
        - Recovery: 1 per 4 remaining weeks, inserted after every 3 load weeks
        - Load weeks split: base (~60%) + build (~40%)

        Returns
        -------
        list[tuple[str, int]]
            Ordered list of (phase_name, num_weeks).
        """
        if weeks <= 0:
            return [("race", 1)]

        # Race week
        race_weeks = 1
        remaining = weeks - race_weeks

        if remaining <= 0:
            return [("race", 1)]

        # Taper
        if weeks <= 8:
            taper_weeks = min(2, remaining)
        elif weeks > 12:
            taper_weeks = min(3, remaining)
        else:
            taper_weeks = min(2, remaining)
        remaining -= taper_weeks

        if remaining <= 0:
            return [("taper", taper_weeks), ("race", race_weeks)]

        # Budget recovery weeks from within remaining (3:1 pattern)
        recovery_weeks = remaining // 4 if remaining >= 4 else 0
        load_weeks = remaining - recovery_weeks

        # Split load between base (60%) and build (40%)
        base_count = max(1, round(load_weeks * 0.6))
        build_count = load_weeks - base_count

        # Build flat load list: base then build
        load_list = ["base"] * base_count + ["build"] * build_count

        # Insert recovery after every 3rd load week
        phases_flat: list[str] = []
        load_counter = 0
        recoveries_placed = 0
        for phase in load_list:
            phases_flat.append(phase)
            load_counter += 1
            if load_counter == 3 and recoveries_placed < recovery_weeks:
                phases_flat.append("recovery")
                recoveries_placed += 1
                load_counter = 0

        # Append taper + race
        phases_flat.extend(["taper"] * taper_weeks)
        phases_flat.append("race")

        # Collapse consecutive same-phase into (phase, count) tuples
        result: list[tuple[str, int]] = []
        for phase in phases_flat:
            if result and result[-1][0] == phase:
                result[-1] = (phase, result[-1][1] + 1)
            else:
                result.append((phase, 1))

        return result

    def _generate_microcycle(
        self,
        week_num: int,
        phase: str,
        target_tss: float,
        prev_week_tss: float,
        start_date: str,
        is_recovery: bool = False,
    ) -> Microcycle:
        """Generate one training week.

        Rules:
        - Progressive overload: max 10% increase from prev week
        - Polarized: ~80% easy, ~20% hard
        - Key sessions: Tue (speed/tempo), Thu (tempo/marathon pace), Sat (long run)
        - Easy days: Mon, Wed, Fri
        - Sun: rest or easy recovery
        - Recovery weeks: cut volume 40%, keep some quality
        """
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = start_dt + timedelta(days=6)

        # Enforce 10% cap on non-recovery, non-taper weeks
        if not is_recovery and phase not in ("taper", "race", "recovery"):
            max_tss = prev_week_tss * 1.10
            if target_tss > max_tss and prev_week_tss > 0:
                target_tss = max_tss

        if is_recovery or phase == "recovery":
            target_tss = prev_week_tss * 0.60

        # Theme
        themes = _PHASE_THEMES.get(phase, ["Training"])
        theme_idx = min((week_num - 1) % len(themes), len(themes) - 1)
        theme = themes[theme_idx]

        # Build daily sessions
        sessions: list[TrainingSession] = []
        daily_plan = self._daily_template(phase, is_recovery)

        # Distribute TSS across sessions
        total_weight = sum(w for _, w in daily_plan)
        for day_offset, (session_type, weight) in enumerate(daily_plan):
            date_str = (start_dt + timedelta(days=day_offset)).strftime("%Y-%m-%d")
            session_tss = (weight / total_weight) * target_tss if total_weight > 0 else 0
            session = self._generate_session(date_str, session_type, phase, session_tss)
            sessions.append(session)

        # Estimate run km (sum of run sessions: TSS -> approx km)
        run_km = 0.0
        for s in sessions:
            if s.sport == "Run":
                # Rough approximation: easy pace ~5:30/km -> ~11 km/h
                # duration in minutes / pace_factor
                pace_factor = 5.5  # minutes per km for easy
                if s.session_type in ("tempo_run", "intervals"):
                    pace_factor = 4.5
                elif s.session_type == "marathon_pace":
                    pace_factor = 4.8
                elif s.session_type == "long_run":
                    pace_factor = 5.5
                run_km += s.duration_minutes / pace_factor

        return Microcycle(
            week_number=week_num,
            start_date=start_date,
            end_date=end_dt.strftime("%Y-%m-%d"),
            phase=phase,
            theme=theme,
            target_weekly_tss=round(target_tss, 1),
            target_run_km=round(run_km, 1),
            sessions=sessions,
            is_recovery_week=is_recovery or phase == "recovery",
        )

    def _daily_template(
        self, phase: str, is_recovery: bool
    ) -> list[tuple[str, float]]:
        """Return a 7-day session template as list of (session_type, tss_weight).

        Day ordering: Mon=0 .. Sun=6.
        """
        if phase == "race":
            return [
                ("easy_run", 1.0),       # Mon
                ("recovery_run", 0.8),   # Tue
                ("rest", 0.0),           # Wed
                ("rest", 0.0),           # Thu
                ("rest", 0.0),           # Fri
                ("rest", 0.0),           # Sat
                ("rest", 0.0),           # Sun  (race day placeholder)
            ]

        if phase == "taper":
            return [
                ("easy_run", 1.2),       # Mon
                ("tempo_run", 1.5),      # Tue - keep some quality
                ("rest", 0.0),           # Wed
                ("easy_run", 1.0),       # Thu
                ("rest", 0.0),           # Fri
                ("easy_run", 1.0),       # Sat
                ("rest", 0.0),           # Sun
            ]

        if is_recovery or phase == "recovery":
            return [
                ("easy_run", 1.0),       # Mon
                ("ride_easy", 0.8),      # Tue
                ("rest", 0.0),           # Wed
                ("easy_run", 1.0),       # Thu
                ("rest", 0.0),           # Fri
                ("long_run", 1.5),       # Sat  (shorter long run)
                ("rest", 0.0),           # Sun
            ]

        if phase == "base":
            return [
                ("easy_run", 1.0),       # Mon
                ("tempo_run", 1.5),      # Tue  - key session
                ("ride_easy", 0.8),      # Wed
                ("easy_run", 1.0),       # Thu
                ("strength", 0.7),       # Fri
                ("long_run", 2.5),       # Sat  - key session
                ("rest", 0.0),           # Sun
            ]

        if phase == "build":
            return [
                ("easy_run", 1.0),       # Mon
                ("intervals", 1.8),      # Tue  - key session
                ("ride_easy", 0.8),      # Wed
                ("marathon_pace", 1.6),  # Thu  - key session
                ("easy_run", 0.8),       # Fri
                ("long_run", 2.8),       # Sat  - key session
                ("rest", 0.0),           # Sun
            ]

        if phase == "peak":
            return [
                ("easy_run", 1.0),       # Mon
                ("intervals", 2.0),      # Tue  - key session
                ("ride_easy", 0.8),      # Wed
                ("marathon_pace", 1.8),  # Thu  - key session
                ("recovery_run", 0.6),   # Fri
                ("long_run", 3.0),       # Sat  - key session
                ("rest", 0.0),           # Sun
            ]

        # Fallback
        return [
            ("easy_run", 1.0),
            ("easy_run", 1.0),
            ("rest", 0.0),
            ("easy_run", 1.0),
            ("rest", 0.0),
            ("long_run", 2.0),
            ("rest", 0.0),
        ]

    def _generate_session(
        self,
        date: str,
        session_type: str,
        phase: str,
        allocated_tss: float = 0.0,
    ) -> TrainingSession:
        """Build a single session with proper paces and descriptions.

        Uses athlete's marathon_pace, easy_pace, tempo_pace from config.
        Includes HR targets where appropriate.
        """
        mp = self._athlete.marathon_pace or "4:37"
        ep = self._athlete.easy_pace or "5:30"
        tp = self._athlete.tempo_pace or "4:15"
        hr_at_mp = self._athlete.hr_at_mp or "160"

        sport = _SESSION_SPORT.get(session_type, "Note")
        is_key = session_type in ("long_run", "tempo_run", "intervals", "marathon_pace")
        priority = 1 if is_key else (3 if session_type == "rest" else 2)

        # Determine duration from allocated TSS
        tss_rate = _TSS_PER_MINUTE.get(session_type, 0.7)
        if tss_rate > 0 and allocated_tss > 0:
            duration = max(20, round(allocated_tss / tss_rate))
        else:
            duration = _default_duration(session_type, phase)

        target_tss = round(allocated_tss, 1) if allocated_tss > 0 else round(duration * tss_rate, 1)

        # Build name and description
        name, description, zone = self._build_session_details(
            session_type, phase, duration, mp, ep, tp, hr_at_mp
        )

        return TrainingSession(
            date=date,
            session_type=session_type,
            sport=sport,
            name=name,
            description=description,
            duration_minutes=duration,
            target_tss=target_tss,
            intensity_zone=zone,
            is_key_session=is_key,
            priority=priority,
            adaptable=(session_type != "rest"),
        )

    def _build_session_details(
        self,
        session_type: str,
        phase: str,
        duration: int,
        mp: str,
        ep: str,
        tp: str,
        hr_at_mp: str,
    ) -> tuple[str, str, str]:
        """Return (name, description, zone) for a session.

        Descriptions use the Intervals.icu workout builder syntax so the
        Intervals engine can parse them into structured steps with pace/HR
        targets and auto-generate workout graphs.

        Format rules:
          - Section headers: ``Warmup``, ``Main Set Nx``, ``Cooldown``
          - Steps: ``- <duration> <target>``
          - Duration: ``10m``, ``30s``, ``5m30s``
          - Pace targets: ``5:30/km Pace``
          - HR targets: ``Z2 HR``
          - Cadence: ``90rpm``
        """
        # Helper: add a slower margin to easy pace for recovery
        def _slower_pace(pace_str: str, add_secs: int = 30) -> str:
            secs = _pace_to_seconds(pace_str) + add_secs
            return f"{secs // 60}:{secs % 60:02d}"

        if session_type == "easy_run":
            return (
                "Easy Run",
                f"- {duration}m {ep}/km Pace",
                "z2",
            )

        if session_type == "recovery_run":
            slow = _slower_pace(ep, 30)
            return (
                "Recovery Run",
                f"- {duration}m {slow}/km Pace",
                "z1",
            )

        if session_type == "tempo_run":
            tempo_dur = max(10, duration - 20)
            return (
                "Tempo Run",
                (
                    f"Warmup\n"
                    f"- 10m {ep}/km Pace\n"
                    f"\n"
                    f"Main Set\n"
                    f"- {tempo_dur}m {tp}/km Pace\n"
                    f"\n"
                    f"Cooldown\n"
                    f"- 10m {ep}/km Pace"
                ),
                "z4",
            )

        if session_type == "intervals":
            reps = max(3, duration // 12)
            rep_dur = 3 if phase == "base" else 4
            return (
                "Interval Session",
                (
                    f"Warmup\n"
                    f"- 10m {ep}/km Pace\n"
                    f"- 4x 20s strides\n"
                    f"- 40s {ep}/km Pace\n"
                    f"\n"
                    f"Main Set {reps}x\n"
                    f"- {rep_dur}m {tp}/km Pace\n"
                    f"- 2m {ep}/km Pace\n"
                    f"\n"
                    f"Cooldown\n"
                    f"- 10m {ep}/km Pace"
                ),
                "z4",
            )

        if session_type == "marathon_pace":
            mp_dur = max(15, duration - 25)
            return (
                "Marathon Pace Run",
                (
                    f"Warmup\n"
                    f"- 10m {ep}/km Pace\n"
                    f"\n"
                    f"Main Set\n"
                    f"- {mp_dur}m {mp}/km Pace\n"
                    f"\n"
                    f"Cooldown\n"
                    f"- 15m {ep}/km Pace"
                ),
                "z3",
            )

        if session_type == "long_run":
            if phase in ("base", "recovery"):
                return (
                    "Long Run",
                    f"- {duration}m {ep}/km Pace",
                    "z2",
                )
            # Build/peak: progressive long run with MP finish
            easy_portion = round(duration * 0.75)
            mp_portion = max(10, duration - easy_portion - 10)
            return (
                "Long Run with MP Finish",
                (
                    f"Long Run\n"
                    f"- {easy_portion}m {ep}/km Pace\n"
                    f"\n"
                    f"Marathon Pace\n"
                    f"- {mp_portion}m {mp}/km Pace\n"
                    f"\n"
                    f"Cooldown\n"
                    f"- 10m {ep}/km Pace"
                ),
                "z2",
            )

        if session_type == "strength":
            # Strength sessions stay as notes — not parseable as run/ride steps
            return (
                "Strength Training",
                (
                    f"Running-specific strength ({duration}min):\n"
                    f"- Single-leg squats 3x10 each\n"
                    f"- Calf raises 3x15 (slow eccentric)\n"
                    f"- Romanian deadlift 3x10\n"
                    f"- Glute bridges 3x12\n"
                    f"- Side plank 3x30s each\n"
                    f"- Dead bugs 3x10 each"
                ),
                "z2",
            )

        if session_type == "ride_easy":
            return (
                "Easy Ride",
                f"- {duration}m Z2 90rpm",
                "z2",
            )

        if session_type == "ride_endurance":
            return (
                "Endurance Ride",
                (
                    f"Warmup\n"
                    f"- 10m Z1\n"
                    f"\n"
                    f"Main Set\n"
                    f"- {max(10, duration - 15)}m Z2\n"
                    f"\n"
                    f"Cooldown\n"
                    f"- 5m Z1"
                ),
                "z2",
            )

        if session_type == "rest":
            return (
                "Rest Day",
                "Full rest. Stretch, hydrate, sleep.",
                "z1",
            )

        # Fallback
        return (
            session_type.replace("_", " ").title(),
            f"- {duration}m",
            "z2",
        )

    def _compute_weekly_tss_progression(
        self,
        phases: list[tuple[str, int]],
        starting_tss: float,
    ) -> list[float]:
        """Compute target weekly TSS for each week.

        Base: start at current level, progress +5-8%/week
        Build: higher progression +8-10%/week
        Peak: maintain high level
        Taper: reduce 20% (week 1), 40% (week 2), 60% (week 3)
        Recovery: 60% of previous load week
        """
        tss_values: list[float] = []
        current_tss = max(starting_tss, 150.0)  # floor at 150 weekly TSS
        last_load_tss = current_tss

        for phase_name, num_weeks in phases:
            for i in range(num_weeks):
                if phase_name == "base":
                    growth = 1.05 + (0.03 * min(i, 3) / 3)  # 5-8%
                    current_tss = last_load_tss * growth
                    last_load_tss = current_tss
                    tss_values.append(round(current_tss, 1))

                elif phase_name == "build":
                    growth = 1.08 + (0.02 * min(i, 3) / 3)  # 8-10%
                    current_tss = last_load_tss * growth
                    last_load_tss = current_tss
                    tss_values.append(round(current_tss, 1))

                elif phase_name == "peak":
                    # Maintain or very slight increase
                    current_tss = last_load_tss * 1.02
                    last_load_tss = current_tss
                    tss_values.append(round(current_tss, 1))

                elif phase_name == "recovery":
                    # 60% of the last load week
                    recovery_tss = last_load_tss * 0.60
                    tss_values.append(round(recovery_tss, 1))
                    # Don't update last_load_tss -- resume from pre-recovery

                elif phase_name == "taper":
                    # Progressive taper
                    taper_reductions = [0.75, 0.60, 0.40]
                    reduction = taper_reductions[min(i, len(taper_reductions) - 1)]
                    taper_tss = last_load_tss * reduction
                    tss_values.append(round(taper_tss, 1))

                elif phase_name == "race":
                    # Minimal load in race week
                    tss_values.append(round(last_load_tss * 0.30, 1))

                else:
                    tss_values.append(round(current_tss, 1))

        return tss_values

    # ------------------------------------------------------------------
    # Plan adaptation
    # ------------------------------------------------------------------

    def adapt_plan(
        self,
        plan: TrainingPlan,
        missed_dates: list[str],
        current_recovery: dict,
        current_thresholds,
    ) -> TrainingPlan:
        """Re-plan remaining sessions when athlete misses days or recovery shifts.

        Uses plan_adapter.assess_adaptation_needs() for each remaining session.

        Parameters
        ----------
        plan : TrainingPlan
            The original plan.
        missed_dates : list[str]
            ISO dates of missed sessions.
        current_recovery : dict
            Current recovery state with keys: recovery_score, sleep_analysis,
            compliance, performance.
        current_thresholds : PersonalizedThresholds
            Current thresholds for recalibration.

        Returns
        -------
        TrainingPlan
            Adapted plan with modified remaining sessions.
        """
        from modules.plan_adapter import assess_adaptation_needs, adapt_workout_description

        today = datetime.now().strftime("%Y-%m-%d")
        missed_set = set(missed_dates)

        recovery_score = current_recovery.get("recovery_score", 75.0)
        sleep_analysis = current_recovery.get("sleep_analysis", {
            "avg_7d": 7.0, "debt_7d": 0.0, "last_night": {"hours": 7.0}
        })
        compliance = current_recovery.get("compliance", {
            "compliance_rate": 100.0, "missed_days": []
        })
        performance = current_recovery.get("performance", {
            "tsb": 0.0, "overtraining_risk": "low", "ramp_rate": 0.0
        })

        # Build compliance missed_days from missed_dates
        compliance_missed = [{"date": d} for d in missed_dates]
        compliance["missed_days"] = compliance_missed
        if missed_dates:
            total_sessions = sum(
                len(mc.sessions) for meso in plan.mesocycles for mc in meso.microcycles
            )
            if total_sessions > 0:
                compliance["compliance_rate"] = max(0, 100.0 * (1 - len(missed_dates) / total_sessions))

        adaptation = assess_adaptation_needs(
            recovery_score=recovery_score,
            sleep_analysis=sleep_analysis,
            compliance=compliance,
            performance=performance,
        )

        # Apply adaptation to future sessions
        for meso in plan.mesocycles:
            for mc in meso.microcycles:
                for session in mc.sessions:
                    if session.date < today:
                        continue
                    if session.date in missed_set:
                        session.session_type = "rest"
                        session.name = "Rest (Missed)"
                        session.description = "Session missed. Rest day."
                        session.target_tss = 0.0
                        session.adaptable = False
                        continue

                    if session.adaptable and session.is_key_session:
                        session.description = adapt_workout_description(
                            session.description, adaptation
                        )
                        # Adjust TSS by volume modifier
                        v_mod = adaptation.get("volume_modifier", 1.0)
                        session.target_tss = round(session.target_tss * v_mod, 1)
                        session.duration_minutes = max(
                            15, round(session.duration_minutes * v_mod)
                        )

        plan.version += 1
        return plan

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_current_week(self, plan: TrainingPlan) -> Microcycle | None:
        """Return the microcycle containing today's date."""
        today = datetime.now().strftime("%Y-%m-%d")
        for meso in plan.mesocycles:
            for mc in meso.microcycles:
                if mc.start_date <= today <= mc.end_date:
                    return mc
        return None

    def get_today_session(self, plan: TrainingPlan) -> TrainingSession | None:
        """Return today's planned session."""
        today = datetime.now().strftime("%Y-%m-%d")
        for meso in plan.mesocycles:
            for mc in meso.microcycles:
                for session in mc.sessions:
                    if session.date == today:
                        return session
        return None

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def format_week_summary(self, microcycle: Microcycle) -> str:
        """Format a microcycle for Telegram display.

        Example output:
            Week 5 -- Build Phase (Peak Quality)
            Weekly target: 380 TSS | 55 km running

            Mon: Easy Run 45min Z1-2
            Tue: * Tempo Run 55min (20min @4:30/km)
            ...
        """
        phase_label = microcycle.phase.title()
        lines = [
            f"Week {microcycle.week_number} -- {phase_label} Phase ({microcycle.theme})",
            f"Weekly target: {microcycle.target_weekly_tss:.0f} TSS | {microcycle.target_run_km:.0f} km running",
            "",
        ]

        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for i, session in enumerate(microcycle.sessions):
            day = day_names[i] if i < len(day_names) else f"Day{i+1}"
            key_marker = "* " if session.is_key_session else ""
            zone_label = session.intensity_zone.upper() if session.intensity_zone else ""
            if session.session_type == "rest":
                lines.append(f"{day}: Rest")
            else:
                lines.append(
                    f"{day}: {key_marker}{session.name} {session.duration_minutes}min {zone_label}"
                )

        return "\n".join(lines)

    def format_plan_overview(self, plan: TrainingPlan) -> str:
        """High-level plan overview with mesocycle breakdown."""
        lines = [
            f"TRAINING PLAN: {plan.race_name}",
            f"Race Date: {plan.race_date} | Goal: {plan.goal_time}",
            f"Athlete: {plan.athlete_name}",
            f"Created: {plan.created_at[:10]} | Version: {plan.version}",
            "",
            "MESOCYCLE BREAKDOWN:",
        ]

        total_weeks = 0
        for meso in plan.mesocycles:
            num_weeks = len(meso.microcycles)
            total_weeks += num_weeks
            total_tss = sum(mc.target_weekly_tss for mc in meso.microcycles)
            avg_tss = total_tss / num_weeks if num_weeks > 0 else 0
            lines.append(
                f"  {meso.name} ({meso.phase}): "
                f"{meso.start_date} to {meso.end_date} "
                f"({num_weeks} weeks, avg {avg_tss:.0f} TSS/wk)"
            )

        lines.append(f"\nTotal: {total_weeks} weeks")

        # Week-by-week summary
        lines.append("\nWEEKLY OVERVIEW:")
        for meso in plan.mesocycles:
            for mc in meso.microcycles:
                recovery_tag = " [RECOVERY]" if mc.is_recovery_week else ""
                key_count = sum(1 for s in mc.sessions if s.is_key_session)
                lines.append(
                    f"  Wk {mc.week_number:>2}: {mc.phase:>8} | "
                    f"{mc.target_weekly_tss:>5.0f} TSS | "
                    f"{mc.target_run_km:>5.1f} km | "
                    f"{key_count} key sessions{recovery_tag}"
                )

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _next_monday(dt) -> datetime.date:
    """Return the next Monday on or after *dt*."""
    if hasattr(dt, 'date'):
        dt = dt.date()
    days_ahead = (7 - dt.weekday()) % 7
    if days_ahead == 0 and dt.weekday() != 0:
        days_ahead = 7
    if dt.weekday() == 0:
        return dt
    return dt + timedelta(days=days_ahead)


def _mesocycle_name(phase: str) -> str:
    """Human-readable mesocycle name."""
    names = {
        "base": "Base Building",
        "build": "Specific Preparation",
        "peak": "Peak Fitness",
        "taper": "Taper",
        "recovery": "Recovery",
        "race": "Race Week",
    }
    return names.get(phase, phase.title())


def _default_duration(session_type: str, phase: str) -> int:
    """Fallback duration in minutes when TSS allocation is zero."""
    defaults = {
        "easy_run": 45,
        "recovery_run": 30,
        "tempo_run": 50,
        "intervals": 55,
        "marathon_pace": 60,
        "long_run": 90 if phase != "recovery" else 70,
        "strength": 30,
        "ride_easy": 50,
        "ride_endurance": 60,
        "rest": 0,
    }
    return defaults.get(session_type, 40)


def _hr_ceiling(hr_at_mp: str, fraction: float) -> int:
    """Compute a HR ceiling as a fraction of HR at marathon pace."""
    try:
        hr = int(hr_at_mp)
    except (ValueError, TypeError):
        hr = 160
    return round(hr * fraction)


def _pace_to_seconds(pace_str: str) -> int:
    """Convert a pace string like '4:37' to total seconds."""
    try:
        parts = pace_str.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return 300  # default 5:00/km
