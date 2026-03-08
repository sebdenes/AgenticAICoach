"""Scenario simulation — 'What if I do X tomorrow?' impact prediction."""

from __future__ import annotations

import re
import math
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict

log = logging.getLogger("coach.simulation")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SimulatedWorkout:
    """A hypothetical workout to simulate."""
    sport: str                # "Run", "Ride", "Swim", "Strength"
    duration_minutes: int     # Total duration
    estimated_tss: float      # TSS (can be provided or estimated)
    intensity: str            # "easy", "moderate", "hard", "race_pace"
    description: str = ""     # Original text description


@dataclass
class SimulationResult:
    """Full simulation output."""
    # Pre-workout state
    current_ctl: float
    current_atl: float
    current_tsb: float

    # Post-workout projected state (next day)
    projected_ctl: float
    projected_atl: float
    projected_tsb: float

    # Deltas
    ctl_delta: float
    atl_delta: float
    tsb_delta: float

    # Recovery projection
    days_to_recovery: int      # Days until TSB returns to 0 (if currently negative)
    days_to_baseline_tsb: int  # Days until TSB returns to current level

    # Race impact (if race_date known)
    race_ctl_projected: Optional[float] = None
    race_tsb_projected: Optional[float] = None
    race_readiness_change: str = "unknown"  # "improved", "unchanged", "worsened"
    days_to_race: Optional[int] = None

    # Reasoning
    reasoning: object = None   # ReasoningChain from explainability module

    # Recommendation
    recommendation: str = ""        # "Go for it", "Reduce to 2h", "Skip - rest day better"
    alternative: Optional[str] = None  # "Consider 90min Z2 ride instead"

    # Input workout
    workout: Optional[SimulatedWorkout] = None


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------

class ScenarioSimulator:
    """Simulate the impact of hypothetical workouts on fitness and recovery."""

    # TSS estimation rates by sport and intensity (TSS per hour)
    TSS_RATES: Dict[Tuple[str, str], int] = {
        ("run", "easy"): 50,
        ("run", "moderate"): 70,
        ("run", "hard"): 90,
        ("run", "race_pace"): 100,
        ("ride", "easy"): 40,
        ("ride", "moderate"): 60,
        ("ride", "hard"): 80,
        ("ride", "race_pace"): 95,
        ("swim", "easy"): 35,
        ("swim", "moderate"): 55,
        ("swim", "hard"): 75,
        ("swim", "race_pace"): 90,
        ("strength", "easy"): 30,
        ("strength", "moderate"): 45,
        ("strength", "hard"): 60,
        # Default fallback
        ("other", "easy"): 35,
        ("other", "moderate"): 55,
        ("other", "hard"): 75,
        ("other", "race_pace"): 85,
    }

    CTL_TAU = 42  # CTL time constant (days)
    ATL_TAU = 7   # ATL time constant (days)

    def __init__(self, thresholds=None, explainability_engine=None, athlete=None):
        """
        Parameters
        ----------
        thresholds : PersonalizedThresholds | None
        explainability_engine : ExplainabilityEngine | None
        athlete : AthleteConfig | None
        """
        self.thresholds = thresholds
        self.explainability = explainability_engine
        self.athlete = athlete

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def simulate(self, workout: SimulatedWorkout,
                 wellness_history: List[dict],
                 activities: Optional[List[dict]] = None) -> SimulationResult:
        """Simulate impact of a hypothetical workout.

        Algorithm:
        1. Get current CTL/ATL from latest wellness entry
        2. Estimate TSS if not provided
        3. Compute new CTL: CTL_new = CTL_old + (TSS - CTL_old) / CTL_TAU
        4. Compute new ATL: ATL_new = ATL_old + (TSS - ATL_old) / ATL_TAU
        5. TSB_new = CTL_new - ATL_new
        6. Project recovery timeline (iterate days forward with TSS=0)
        7. Project race-day CTL/TSB if race_date is known
        8. Build reasoning chain
        9. Generate recommendation
        """
        # 1. Current state from latest wellness
        latest = wellness_history[-1] if wellness_history else {}
        current_ctl = float(latest.get("ctl", 0) or 0)
        current_atl = float(latest.get("atl", 0) or 0)
        current_tsb = current_ctl - current_atl

        # 2. Estimate TSS if not already set (0 means not provided)
        tss = workout.estimated_tss
        if tss <= 0:
            tss = self._estimate_tss(workout)
            workout.estimated_tss = tss

        # 3-4. Exponential weighted moving average update
        projected_ctl = current_ctl + (tss - current_ctl) / self.CTL_TAU
        projected_atl = current_atl + (tss - current_atl) / self.ATL_TAU

        # 5. New TSB
        projected_tsb = projected_ctl - projected_atl

        # Deltas
        ctl_delta = projected_ctl - current_ctl
        atl_delta = projected_atl - current_atl
        tsb_delta = projected_tsb - current_tsb

        # 6. Recovery timeline
        days_to_recovery, days_to_baseline = self._project_recovery_timeline(
            projected_ctl, projected_atl, current_tsb
        )

        # 7. Race-day projection
        race_ctl = None
        race_tsb = None
        readiness_change = "unknown"
        days_to_race = None

        if self.athlete and self.athlete.race_date:
            try:
                race_dt = datetime.strptime(self.athlete.race_date, "%Y-%m-%d")
                days_to_race = (race_dt - datetime.now()).days
                if days_to_race > 0:
                    # With workout
                    race_ctl, race_tsb = self._project_race_day(
                        projected_ctl, projected_atl, days_to_race
                    )
                    # Without workout (baseline comparison)
                    base_race_ctl, base_race_tsb = self._project_race_day(
                        current_ctl, current_atl, days_to_race
                    )
                    # Determine readiness change — higher CTL and reasonable TSB is better
                    ctl_improvement = race_ctl - base_race_ctl
                    tsb_diff = race_tsb - base_race_tsb
                    if ctl_improvement > 0.1 and race_tsb > -10:
                        readiness_change = "improved"
                    elif ctl_improvement < -0.1 or race_tsb < -15:
                        readiness_change = "worsened"
                    else:
                        readiness_change = "unchanged"
            except (ValueError, TypeError):
                pass

        # Build partial result for reasoning and recommendation
        result = SimulationResult(
            current_ctl=round(current_ctl, 1),
            current_atl=round(current_atl, 1),
            current_tsb=round(current_tsb, 1),
            projected_ctl=round(projected_ctl, 1),
            projected_atl=round(projected_atl, 1),
            projected_tsb=round(projected_tsb, 1),
            ctl_delta=round(ctl_delta, 1),
            atl_delta=round(atl_delta, 1),
            tsb_delta=round(tsb_delta, 1),
            days_to_recovery=days_to_recovery,
            days_to_baseline_tsb=days_to_baseline,
            race_ctl_projected=round(race_ctl, 1) if race_ctl is not None else None,
            race_tsb_projected=round(race_tsb, 1) if race_tsb is not None else None,
            race_readiness_change=readiness_change,
            days_to_race=days_to_race,
            workout=workout,
        )

        # 8. Build reasoning chain
        result_data = {
            "tss": tss,
            "current_ctl": current_ctl,
            "current_atl": current_atl,
            "current_tsb": current_tsb,
            "projected_ctl": projected_ctl,
            "projected_atl": projected_atl,
            "projected_tsb": projected_tsb,
            "days_to_recovery": days_to_recovery,
            "days_to_race": days_to_race,
            "race_ctl": race_ctl,
            "race_tsb": race_tsb,
        }
        result.reasoning = self._build_reasoning(workout, result_data)

        # 9. Generate recommendation
        recommendation, alternative = self._generate_recommendation(result)
        result.recommendation = recommendation
        result.alternative = alternative

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _estimate_tss(self, workout: SimulatedWorkout) -> float:
        """Estimate TSS from duration, intensity, and sport.

        Uses TSS_RATES lookup table.
        Returns duration_hours * rate.
        """
        sport_key = workout.sport.lower().strip()
        # Normalise sport name
        if "ride" in sport_key or "cycl" in sport_key or "bike" in sport_key:
            sport_key = "ride"
        elif "run" in sport_key:
            sport_key = "run"
        elif "swim" in sport_key:
            sport_key = "swim"
        elif "strength" in sport_key or "weight" in sport_key:
            sport_key = "strength"
        else:
            sport_key = "other"

        intensity_key = workout.intensity.lower().strip()
        rate = self.TSS_RATES.get((sport_key, intensity_key))
        if rate is None:
            # Try with moderate fallback
            rate = self.TSS_RATES.get((sport_key, "moderate"))
        if rate is None:
            rate = self.TSS_RATES.get(("other", intensity_key), 55)

        duration_hours = workout.duration_minutes / 60.0
        return duration_hours * rate

    def _project_recovery_timeline(self, ctl: float, atl: float,
                                    baseline_tsb: float) -> Tuple[int, int]:
        """Project days until TSB recovers.

        Iterates day-by-day with TSS=0:
          new_ctl = ctl + (0 - ctl) / CTL_TAU  (i.e., ctl * (1 - 1/CTL_TAU))
          new_atl = atl + (0 - atl) / ATL_TAU
          tsb = new_ctl - new_atl

        Returns (days_to_tsb_zero, days_to_baseline_tsb).
        Max 60 days iteration.
        """
        c, a = ctl, atl
        days_to_zero = 0
        days_to_baseline = 0
        found_zero = False
        found_baseline = False

        for day in range(1, 61):
            c = c + (0 - c) / self.CTL_TAU  # decay toward 0
            a = a + (0 - a) / self.ATL_TAU
            tsb = c - a

            if not found_zero and tsb >= 0:
                days_to_zero = day
                found_zero = True

            if not found_baseline and tsb >= baseline_tsb:
                days_to_baseline = day
                found_baseline = True

            if found_zero and found_baseline:
                break

        if not found_zero:
            days_to_zero = 60
        if not found_baseline:
            days_to_baseline = 60

        return days_to_zero, days_to_baseline

    def _project_race_day(self, ctl: float, atl: float,
                          days_to_race: int) -> Tuple[float, float]:
        """Project CTL and TSB on race day.

        Uses day-by-day exponential decay. Assumes daily TSS equals
        thresholds.tss_daily_avg if available, otherwise TSS=0 for
        a conservative projection.

        Returns (race_day_ctl, race_day_tsb).
        """
        daily_tss = 0.0
        if self.thresholds and hasattr(self.thresholds, "tss_daily_avg"):
            daily_tss = self.thresholds.tss_daily_avg

        c, a = ctl, atl
        for _ in range(days_to_race):
            c = c + (daily_tss - c) / self.CTL_TAU
            a = a + (daily_tss - a) / self.ATL_TAU

        return c, c - a

    def _build_reasoning(self, workout: SimulatedWorkout,
                         result_data: dict) -> object:
        """Build a ReasoningChain explaining the simulation.

        If self.explainability is set, use it. Otherwise return None.
        """
        if self.explainability is None:
            return None

        try:
            from modules.explainability import ReasoningStep, ReasoningChain
        except ImportError:
            return None

        steps = []
        tss = result_data["tss"]

        # Step 1: TSS estimation
        steps.append(ReasoningStep(
            observation=(
                f"Estimated {tss:.0f} TSS for {workout.duration_minutes}min "
                f"{workout.intensity} {workout.sport}"
            ),
            data_points=[{
                "sport": workout.sport,
                "duration": workout.duration_minutes,
                "intensity": workout.intensity,
                "tss": tss,
            }],
            rule_applied=(
                f"TSS = duration_hours * rate_per_hour "
                f"({workout.sport.lower()} {workout.intensity})"
            ),
            source="simulation:tss_estimation",
            confidence=0.8,
        ))

        # Step 2: CTL/ATL projection
        steps.append(ReasoningStep(
            observation=(
                f"CTL {result_data['current_ctl']:.1f} -> {result_data['projected_ctl']:.1f}, "
                f"ATL {result_data['current_atl']:.1f} -> {result_data['projected_atl']:.1f}, "
                f"TSB {result_data['current_tsb']:.1f} -> {result_data['projected_tsb']:.1f}"
            ),
            data_points=[{
                "ctl_before": result_data["current_ctl"],
                "ctl_after": result_data["projected_ctl"],
                "atl_before": result_data["current_atl"],
                "atl_after": result_data["projected_atl"],
            }],
            rule_applied="EWMA: CTL_new = CTL_old + (TSS - CTL_old)/42, ATL_new = ATL_old + (TSS - ATL_old)/7",
            source="simulation:ewma_projection",
            confidence=0.9,
        ))

        # Step 3: Recovery
        steps.append(ReasoningStep(
            observation=f"Recovery to baseline TSB in ~{result_data['days_to_recovery']} days",
            data_points=[{"days_to_recovery": result_data["days_to_recovery"]}],
            rule_applied="Day-by-day TSS=0 decay until TSB returns to pre-workout level",
            source="simulation:recovery_projection",
            confidence=0.85,
        ))

        # Step 4: Race impact (if applicable)
        if result_data.get("days_to_race") and result_data["days_to_race"] > 0:
            steps.append(ReasoningStep(
                observation=(
                    f"Race-day projection ({result_data['days_to_race']}d out): "
                    f"CTL={result_data['race_ctl']:.1f}, TSB={result_data['race_tsb']:.1f}"
                ),
                data_points=[{
                    "days_to_race": result_data["days_to_race"],
                    "race_ctl": result_data["race_ctl"],
                    "race_tsb": result_data["race_tsb"],
                }],
                rule_applied="Forward projection with assumed average daily TSS",
                source="simulation:race_day_projection",
                confidence=0.7,
            ))

        conclusion = (
            f"Simulation: {workout.duration_minutes}min {workout.intensity} {workout.sport} "
            f"({tss:.0f} TSS) -> TSB {result_data['projected_tsb']:.1f}"
        )

        chain = ReasoningChain(
            conclusion=conclusion,
            steps=steps,
            overall_confidence=0.85,
        )
        return chain

    def _generate_recommendation(self, result: SimulationResult) -> Tuple[str, Optional[str]]:
        """Generate recommendation and alternative.

        Rules:
        - If TSB drops below -30: "Skip -- high injury risk"
        - If TSB drops below -20: "Reduce intensity" + suggest shorter version
        - If race in <7 days and TSB drops: "Skip -- preserve freshness for race"
        - If recovery_days > 3: "Consider reducing to stay available for next key session"
        - Otherwise: "Go for it"

        Returns (recommendation, alternative).
        """
        alternative = None

        # Race proximity override
        if result.days_to_race is not None and result.days_to_race < 7:
            if result.tsb_delta < -2:
                return (
                    "Skip -- preserve freshness for race",
                    "Consider a short easy shakeout instead (20-30min easy)"
                )

        # Severe fatigue
        if result.projected_tsb < -30:
            if result.workout:
                reduced_mins = max(30, result.workout.duration_minutes // 2)
                alternative = (
                    f"Consider {reduced_mins}min easy {result.workout.sport} instead"
                )
            return "Skip -- high injury risk from deep fatigue", alternative

        # Moderate fatigue
        if result.projected_tsb < -20:
            if result.workout:
                reduced_mins = max(30, int(result.workout.duration_minutes * 0.6))
                alternative = (
                    f"Consider {reduced_mins}min easy {result.workout.sport} instead"
                )
            return "Reduce intensity -- fatigue is accumulating", alternative

        # Slow recovery
        if result.days_to_baseline_tsb > 3:
            return (
                "Go for it, but note extended recovery needed",
                "Consider reducing duration to stay available for next key session"
            )

        return "Go for it -- builds fitness with manageable fatigue", None

    # ------------------------------------------------------------------
    # Workout parsing
    # ------------------------------------------------------------------

    @staticmethod
    def parse_workout_description(text: str) -> SimulatedWorkout:
        """Parse natural language workout description.

        Handles formats:
        - "3h ride" -> Ride, 180min, easy
        - "3h easy ride" -> Ride, 180min, easy
        - "10km tempo run" -> Run, ~50min, hard
        - "60min easy run" -> Run, 60min, easy
        - "90min Z2 ride" -> Ride, 90min, easy
        - "intervals 8x400m" -> Run, ~45min, hard
        - "2h long run" -> Run, 120min, moderate
        - "strength session" -> Strength, 60min, moderate
        """
        original = text.strip()
        lower = original.lower()

        # -- Parse sport --
        sport = "Run"  # default
        if re.search(r'\b(rid[ei]|cycl[ei]|bik[ei])\b', lower):
            sport = "Ride"
        elif re.search(r'\bswim\b', lower):
            sport = "Swim"
        elif re.search(r'\b(strength|weights?|gym|weight\s*training)\b', lower):
            sport = "Strength"
        elif re.search(r'\brun\b', lower):
            sport = "Run"
        elif re.search(r'\bsession\b', lower) and not re.search(r'\b(run|rid|swim)\b', lower):
            # "strength session" already caught, generic "session" -> other
            sport = "Other"
        elif not re.search(r'\b(run|rid|cycl|swim|strength)\b', lower):
            # No sport keyword at all
            sport = "Other"

        # -- Parse intensity --
        intensity = "moderate"  # default
        if re.search(r'\b(easy|recovery|z[12]|zone\s*[12])\b', lower):
            intensity = "easy"
        elif re.search(r'\b(hard|tempo|threshold|interval|z[45]|zone\s*[45]|vo2|speed|fartlek)\b', lower):
            intensity = "hard"
        elif re.search(r'\b(race[\s_]*pace|race)\b', lower):
            intensity = "race_pace"
        elif re.search(r'\blong\b', lower):
            intensity = "moderate"
        elif re.search(r'\b(moderate|steady|z3|zone\s*3|endurance)\b', lower):
            intensity = "moderate"

        # -- Parse duration --
        duration_minutes = 0

        # Pattern: Xh or X.Xh
        m_h = re.search(r'(\d+(?:\.\d+)?)\s*h(?:ours?|r(?:s)?)?(?:\b|$)', lower)
        # Pattern: Xmin or X minutes
        m_min = re.search(r'(\d+)\s*min(?:utes?)?(?:\b|$)', lower)
        # Pattern: X:XX (hours:minutes)
        m_hm = re.search(r'(\d+):(\d{2})(?:\b|$)', lower)

        if m_h and m_min:
            # Both hours and minutes: "1h30min"
            duration_minutes = int(float(m_h.group(1)) * 60) + int(m_min.group(1))
        elif m_h:
            duration_minutes = int(float(m_h.group(1)) * 60)
        elif m_min:
            duration_minutes = int(m_min.group(1))
        elif m_hm:
            duration_minutes = int(m_hm.group(1)) * 60 + int(m_hm.group(2))

        # Parse distance: Xkm or Xmi -> estimate duration
        if duration_minutes == 0:
            m_km = re.search(r'(\d+(?:\.\d+)?)\s*km\b', lower)
            m_mi = re.search(r'(\d+(?:\.\d+)?)\s*mi(?:les?)?\b', lower)
            if m_km:
                dist_km = float(m_km.group(1))
                # Estimate duration from rough paces
                if sport == "Run":
                    pace_min_km = {"easy": 6.0, "moderate": 5.5, "hard": 5.0, "race_pace": 4.5}
                    duration_minutes = int(dist_km * pace_min_km.get(intensity, 5.5))
                elif sport == "Ride":
                    speed_kmh = {"easy": 25, "moderate": 28, "hard": 32, "race_pace": 35}
                    duration_minutes = int(dist_km / speed_kmh.get(intensity, 28) * 60)
            elif m_mi:
                dist_mi = float(m_mi.group(1))
                dist_km = dist_mi * 1.60934
                if sport == "Run":
                    pace_min_km = {"easy": 6.0, "moderate": 5.5, "hard": 5.0, "race_pace": 4.5}
                    duration_minutes = int(dist_km * pace_min_km.get(intensity, 5.5))

        # Interval pattern: NxDISTANCE
        if duration_minutes == 0 and re.search(r'\d+\s*x\s*\d+', lower):
            m_interval = re.search(r'(\d+)\s*x\s*(\d+)\s*(m|km)?', lower)
            if m_interval:
                reps = int(m_interval.group(1))
                dist = int(m_interval.group(2))
                unit = m_interval.group(3) or "m"
                if unit == "km":
                    dist *= 1000
                # Estimate: each rep + recovery = roughly 2x the rep time
                rep_time_sec = dist / 4.0  # ~4 m/s for hard running (~5:00/km pace est.)
                total_sec = reps * rep_time_sec * 2 + 600  # + 10 min warmup/cooldown
                duration_minutes = max(30, int(total_sec / 60))
                intensity = "hard"  # intervals are always hard
                sport = "Run"  # intervals default to run

        # Default durations if still 0
        if duration_minutes == 0:
            if sport == "Strength":
                duration_minutes = 60
            elif "session" in lower or "workout" in lower:
                duration_minutes = 60
            else:
                duration_minutes = 60  # generic default

        return SimulatedWorkout(
            sport=sport,
            duration_minutes=duration_minutes,
            estimated_tss=0.0,  # will be estimated later
            intensity=intensity,
            description=original,
        )

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def format_result(self, result: SimulationResult) -> str:
        """Format for Telegram display."""
        w = result.workout
        workout_desc = ""
        if w:
            workout_desc = w.description or f"{w.duration_minutes}min {w.intensity} {w.sport}"

        lines = [
            f"*Simulation: {workout_desc}*",
            "",
            "*Current State*",
            f"CTL: {result.current_ctl} | ATL: {result.current_atl} | TSB: {result.current_tsb}",
            "",
        ]

        tss_str = ""
        if w and w.estimated_tss > 0:
            tss_str = f" (est. {w.estimated_tss:.0f} TSS)"
        lines.append(f"*After Workout*{tss_str}")
        lines.append(
            f"CTL: {result.projected_ctl} ({result.ctl_delta:+.1f}) | "
            f"ATL: {result.projected_atl} ({result.atl_delta:+.1f}) | "
            f"TSB: {result.projected_tsb} ({result.tsb_delta:+.1f})"
        )
        lines.append("")

        lines.append(f"Recovery: ~{result.days_to_baseline_tsb} days to baseline TSB")

        if result.race_ctl_projected is not None and result.days_to_race is not None:
            lines.append("")
            lines.append(f"*Race Impact* ({result.days_to_race} days out)")
            lines.append(
                f"Race-day CTL: {result.race_ctl_projected} | TSB: {result.race_tsb_projected}"
            )
            readiness_symbol = {
                "improved": "improved",
                "unchanged": "unchanged",
                "worsened": "worsened",
            }
            lines.append(
                f"Readiness: {readiness_symbol.get(result.race_readiness_change, result.race_readiness_change)}"
            )

        lines.append("")
        lines.append(f"*Recommendation*: {result.recommendation}")
        if result.alternative:
            lines.append(f"Alternative: {result.alternative}")

        return "\n".join(lines)

    def format_result_for_prompt(self, result: SimulationResult) -> str:
        """Compact format for LLM context injection."""
        w = result.workout
        workout_desc = ""
        if w:
            workout_desc = w.description or f"{w.duration_minutes}min {w.intensity} {w.sport}"

        parts = [
            f"SIMULATION: {workout_desc}",
            f"  TSS: {w.estimated_tss:.0f}" if w else "",
            f"  CTL: {result.current_ctl} -> {result.projected_ctl} ({result.ctl_delta:+.1f})",
            f"  ATL: {result.current_atl} -> {result.projected_atl} ({result.atl_delta:+.1f})",
            f"  TSB: {result.current_tsb} -> {result.projected_tsb} ({result.tsb_delta:+.1f})",
            f"  Recovery: {result.days_to_baseline_tsb}d to baseline",
        ]

        if result.race_ctl_projected is not None:
            parts.append(
                f"  Race ({result.days_to_race}d): CTL={result.race_ctl_projected} "
                f"TSB={result.race_tsb_projected} ({result.race_readiness_change})"
            )

        parts.append(f"  Recommendation: {result.recommendation}")

        return "\n".join(p for p in parts if p)
