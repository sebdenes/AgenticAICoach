"""Explainability layer — trace coaching recommendations back to data + reasoning."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger("coach.explainability")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ReasoningStep:
    """A single observation → rule → conclusion step."""
    observation: str       # "HRV dropped 15% over 3 days (62 → 53ms)"
    data_points: list      # [{"date": "2025-03-05", "hrv": 62}, ...]
    rule_applied: str      # "HRV decline >10% over 3+ days suggests fatigue"
    source: str            # "personalized_thresholds" or "knowledge_base:recovery:hrv_trends"
    confidence: float = 0.8


@dataclass
class ReasoningChain:
    """A complete reasoning chain from observations to conclusion."""
    conclusion: str                          # "Reduce intensity today"
    steps: list[ReasoningStep] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    overall_confidence: float = 0.8
    created_at: datetime = field(default_factory=datetime.now)

    def to_athlete_summary(self) -> str:
        """Human-readable 3-5 line summary for Telegram."""
        if not self.steps:
            return self.conclusion
        lines = [f"*{self.conclusion}*", ""]
        for step in self.steps[:3]:  # max 3 key observations
            lines.append(f"  - {step.observation}")
        if self.alternatives:
            lines.append(f"  (Also considered: {self.alternatives[0]})")
        return "\n".join(lines)

    def to_coach_detail(self) -> str:
        """Full technical breakdown for dashboard / debugging."""
        lines = [f"CONCLUSION: {self.conclusion} (confidence: {self.overall_confidence:.0%})", ""]
        for i, step in enumerate(self.steps, 1):
            lines.append(f"Step {i}:")
            lines.append(f"  Observation: {step.observation}")
            lines.append(f"  Rule: {step.rule_applied}")
            lines.append(f"  Source: {step.source}")
            lines.append(f"  Confidence: {step.confidence:.0%}")
            if step.data_points:
                dp_str = ", ".join(str(d) for d in step.data_points[:5])
                lines.append(f"  Data: [{dp_str}]")
            lines.append("")
        if self.alternatives:
            lines.append("Alternatives considered:")
            for alt in self.alternatives:
                lines.append(f"  - {alt}")
        return "\n".join(lines)

    def to_prompt_context(self) -> str:
        """Compact format for inclusion in the LLM system prompt."""
        lines = [f"ANALYSIS: {self.conclusion}"]
        for step in self.steps:
            lines.append(f"  [{step.source}] {step.observation} → {step.rule_applied}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class ExplainabilityEngine:
    """Build reasoning chains from wellness/training data analysis."""

    def __init__(self, thresholds=None):
        """Initialise with optional PersonalizedThresholds instance.

        Parameters
        ----------
        thresholds : modules.thresholds.PersonalizedThresholds | None
        """
        self.thresholds = thresholds
        self._chains: list[ReasoningChain] = []

    def analyze_wellness(self, wellness: list[dict], athlete_config=None) -> ReasoningChain:
        """Build reasoning chain from current wellness state."""
        steps = []
        alternatives = []
        conclusion_parts = []

        if not wellness:
            chain = ReasoningChain(conclusion="Insufficient wellness data for analysis", overall_confidence=0.0)
            self._chains.append(chain)
            return chain

        latest = wellness[-1]

        # -- HRV analysis --
        hrv = _safe_float(latest.get("hrv"))
        if hrv and hrv > 0:
            if self.thresholds:
                a = self.thresholds.assess_hrv(hrv)
                steps.append(ReasoningStep(
                    observation=f"HRV is {hrv:.1f}ms ({a.status}, z={a.z_score:+.1f}, P{a.percentile:.0f}, {a.trend})",
                    data_points=[{"date": _w_date(latest), "hrv": hrv, "baseline": a.baseline}],
                    rule_applied=f"Compared to 30-day baseline {a.baseline:.1f}ms ± {self.thresholds.hrv_std:.1f}",
                    source="personalized_thresholds",
                    confidence=0.9 if len(self.thresholds._hrv_series) >= 14 else 0.6,
                ))
                if a.status in ("low", "critical"):
                    conclusion_parts.append("recovery focus needed (HRV below baseline)")
                elif a.status == "optimal":
                    conclusion_parts.append("HRV is strong")
            else:
                steps.append(ReasoningStep(
                    observation=f"HRV is {hrv:.1f}ms",
                    data_points=[{"date": _w_date(latest), "hrv": hrv}],
                    rule_applied="HRV noted (no personalised baseline available)",
                    source="raw_data",
                    confidence=0.5,
                ))

        # -- HRV trend (7-day) --
        hrv_values = [_safe_float(w.get("hrv")) for w in wellness[-7:] if _safe_float(w.get("hrv")) > 0]
        if len(hrv_values) >= 4:
            from modules.thresholds import linear_slope
            slope = linear_slope(hrv_values)
            first, last = hrv_values[0], hrv_values[-1]
            pct_change = ((last - first) / first * 100) if first else 0
            if pct_change < -10:
                steps.append(ReasoningStep(
                    observation=f"HRV declined {abs(pct_change):.0f}% over 7 days ({first:.0f} → {last:.0f}ms)",
                    data_points=[{"hrv_7d": hrv_values}],
                    rule_applied="HRV decline >10% over 7 days suggests accumulated fatigue",
                    source="knowledge_base:recovery:hrv_guided_training",
                    confidence=0.85,
                ))
                conclusion_parts.append("HRV trend declining")
            elif pct_change > 10:
                steps.append(ReasoningStep(
                    observation=f"HRV improved {pct_change:.0f}% over 7 days ({first:.0f} → {last:.0f}ms)",
                    data_points=[{"hrv_7d": hrv_values}],
                    rule_applied="Rising HRV trend indicates good adaptation",
                    source="knowledge_base:recovery:hrv_guided_training",
                    confidence=0.85,
                ))
                conclusion_parts.append("positive HRV trend")

        # -- RHR analysis --
        rhr = _safe_float(latest.get("restingHR") or latest.get("rhr"))
        if rhr and rhr > 0 and self.thresholds:
            a = self.thresholds.assess_rhr(rhr)
            if a.status in ("high", "critical"):
                steps.append(ReasoningStep(
                    observation=f"RHR elevated at {rhr:.0f}bpm ({a.status}, baseline {a.baseline:.0f}bpm)",
                    data_points=[{"date": _w_date(latest), "rhr": rhr, "baseline": a.baseline}],
                    rule_applied="Elevated RHR >1.5 SD above baseline may indicate incomplete recovery or illness",
                    source="personalized_thresholds",
                    confidence=0.8,
                ))
                conclusion_parts.append("elevated RHR")

        # -- Sleep analysis --
        sleep_secs = latest.get("sleepSecs", 0) or 0
        if sleep_secs > 0:
            sleep_h = sleep_secs / 3600
            if self.thresholds:
                a = self.thresholds.assess_sleep_duration(sleep_h)
                steps.append(ReasoningStep(
                    observation=f"Sleep: {sleep_h:.1f}h ({a.status}, baseline {a.baseline:.1f}h)",
                    data_points=[{"date": _w_date(latest), "sleep_h": sleep_h}],
                    rule_applied=f"Compared to 14-day average of {a.baseline:.1f}h",
                    source="personalized_thresholds",
                    confidence=0.85,
                ))
                if a.status in ("low", "critical"):
                    conclusion_parts.append(f"poor sleep ({sleep_h:.1f}h)")
                    alternatives.append("Consider nap before training if sleep debt is high")

        # -- Training load balance --
        ctl = _safe_float(latest.get("ctl"))
        atl = _safe_float(latest.get("atl"))
        if ctl > 0 and atl > 0 and self.thresholds:
            a = self.thresholds.assess_recovery(ctl, atl)
            acwr = a.value
            steps.append(ReasoningStep(
                observation=f"ACWR: {acwr:.2f} (ATL:{atl:.0f}/CTL:{ctl:.0f}) — {a.status}",
                data_points=[{"ctl": ctl, "atl": atl, "acwr": acwr}],
                rule_applied="Acute:Chronic Workload Ratio 0.8-1.3 is optimal training zone",
                source="knowledge_base:training_load:acwr",
                confidence=0.85,
            ))
            if a.status == "critical":
                conclusion_parts.append("injury risk zone (ACWR >1.5)")
            elif a.status == "low":
                conclusion_parts.append("detraining risk (ACWR <0.5)")

        # Build conclusion
        if conclusion_parts:
            conclusion = "Key findings: " + "; ".join(conclusion_parts)
        else:
            conclusion = "All wellness markers within normal range"

        chain = ReasoningChain(
            conclusion=conclusion,
            steps=steps,
            alternatives=alternatives,
            overall_confidence=_avg_confidence(steps),
        )
        self._chains.append(chain)
        return chain

    def analyze_training_readiness(
        self, wellness: list[dict], activities: list[dict],
        planned_workout: dict | None = None,
    ) -> ReasoningChain:
        """Determine training readiness with full reasoning."""
        steps = []
        alternatives = []
        ready = True

        if not wellness:
            chain = ReasoningChain(conclusion="Insufficient data for readiness assessment", overall_confidence=0.0)
            self._chains.append(chain)
            return chain

        latest = wellness[-1]

        # Check HRV readiness
        hrv = _safe_float(latest.get("hrv"))
        if hrv > 0 and self.thresholds:
            a = self.thresholds.assess_hrv(hrv)
            if a.status in ("low", "critical"):
                ready = False
                steps.append(ReasoningStep(
                    observation=f"HRV {hrv:.0f}ms is {a.status} (baseline {a.baseline:.0f}ms)",
                    data_points=[{"hrv": hrv, "status": a.status}],
                    rule_applied="Low HRV indicates parasympathetic suppression — reduce intensity",
                    source="knowledge_base:recovery:hrv_guided_training",
                    confidence=0.85,
                ))

        # Check RHR readiness
        rhr = _safe_float(latest.get("restingHR") or latest.get("rhr"))
        if rhr > 0 and self.thresholds:
            a = self.thresholds.assess_rhr(rhr)
            if a.status in ("high", "critical"):
                ready = False
                steps.append(ReasoningStep(
                    observation=f"RHR {rhr:.0f}bpm is elevated (+{a.z_score:.1f} SD)",
                    data_points=[{"rhr": rhr, "baseline": a.baseline}],
                    rule_applied="Elevated RHR alongside low HRV strongly suggests recovery needed",
                    source="personalized_thresholds",
                    confidence=0.8,
                ))

        # Check sleep readiness
        sleep_h = (latest.get("sleepSecs", 0) or 0) / 3600
        if sleep_h > 0 and self.thresholds:
            a = self.thresholds.assess_sleep_duration(sleep_h)
            if a.status in ("low", "critical"):
                steps.append(ReasoningStep(
                    observation=f"Sleep {sleep_h:.1f}h is below baseline ({a.baseline:.1f}h)",
                    data_points=[{"sleep_h": sleep_h, "baseline": a.baseline}],
                    rule_applied="Inadequate sleep impairs glycogen resynthesis and motor learning",
                    source="knowledge_base:sleep:sleep_extension",
                    confidence=0.8,
                ))
                if sleep_h < 5.0:
                    ready = False
                    alternatives.append("Consider a nap before any high-intensity work")

        # Check recent training load
        if activities:
            recent_tss = sum(
                _safe_float(a.get("icu_training_load"))
                for a in activities[-3:]
                if a.get("type")
            )
            if recent_tss > 0 and self.thresholds:
                avg_3d = recent_tss / 3
                a = self.thresholds.assess_training_load(avg_3d)
                if a.status in ("high", "critical"):
                    steps.append(ReasoningStep(
                        observation=f"3-day avg TSS {avg_3d:.0f} is {a.status} (typical: {a.baseline:.0f})",
                        data_points=[{"tss_3d_avg": avg_3d}],
                        rule_applied="High recent load requires supercompensation window (36-72h)",
                        source="knowledge_base:recovery:supercompensation_window",
                        confidence=0.8,
                    ))
                    if a.status == "critical":
                        ready = False

        # Planned workout context
        if planned_workout and not ready:
            alternatives.append(
                f"Planned: {planned_workout.get('name', 'workout')} — "
                "consider reducing intensity or swapping for recovery"
            )

        if ready:
            conclusion = "Training readiness: GREEN — proceed as planned"
        elif sum(1 for s in steps if s.confidence > 0.7) >= 2:
            conclusion = "Training readiness: RED — recovery day recommended"
        else:
            conclusion = "Training readiness: AMBER — proceed with caution, reduce intensity"

        chain = ReasoningChain(
            conclusion=conclusion,
            steps=steps,
            alternatives=alternatives,
            overall_confidence=_avg_confidence(steps),
        )
        self._chains.append(chain)
        return chain

    def analyze_sleep(self, wellness: list[dict], athlete_config=None) -> ReasoningChain:
        """Sleep-specific analysis with debt tracking and recommendations."""
        steps = []
        alternatives = []
        target_h = 7.5
        if athlete_config:
            target_h = getattr(athlete_config, "sleep_target_hours", 7.5) or 7.5

        sleep_hours = []
        for w in wellness:
            secs = w.get("sleepSecs", 0) or 0
            if secs > 0:
                sleep_hours.append({"date": _w_date(w), "hours": secs / 3600})

        if not sleep_hours:
            chain = ReasoningChain(conclusion="No sleep data available", overall_confidence=0.0)
            self._chains.append(chain)
            return chain

        # Recent sleep stats
        recent = sleep_hours[-7:]
        avg_h = sum(s["hours"] for s in recent) / len(recent)
        debt_h = sum(max(0, target_h - s["hours"]) for s in recent)
        last_night = recent[-1]["hours"]

        steps.append(ReasoningStep(
            observation=f"Last night: {last_night:.1f}h | 7-day avg: {avg_h:.1f}h | Target: {target_h}h",
            data_points=recent,
            rule_applied="7-day rolling average provides more stable sleep assessment than single nights",
            source="knowledge_base:sleep:sleep_assessment",
            confidence=0.9,
        ))

        # Sleep debt
        if debt_h > 5:
            steps.append(ReasoningStep(
                observation=f"7-day sleep debt: {debt_h:.1f}h (>{debt_h/7:.1f}h/night below target)",
                data_points=[{"debt_h": debt_h, "target": target_h}],
                rule_applied="Cumulative sleep debt >5h/week impairs reaction time and endurance",
                source="knowledge_base:sleep:sleep_debt",
                confidence=0.85,
            ))

        # Grade last night
        if last_night >= target_h:
            grade = "GREEN"
        elif last_night >= target_h - 1:
            grade = "AMBER"
        else:
            grade = "RED"
            alternatives.append(f"Aim for bedtime by 22:00 tonight to recover lost sleep")

        # Trend
        if len(sleep_hours) >= 5:
            from modules.thresholds import linear_slope
            trend_vals = [s["hours"] for s in sleep_hours[-7:]]
            slope = linear_slope(trend_vals)
            if slope < -0.1:
                steps.append(ReasoningStep(
                    observation=f"Sleep trend declining ({slope:+.2f}h/night over 7 days)",
                    data_points=[{"slope": slope}],
                    rule_applied="Declining sleep trend requires intervention before performance drops",
                    source="knowledge_base:sleep:sleep_hygiene",
                    confidence=0.8,
                ))

        conclusion = (
            f"Sleep: {grade} | {last_night:.1f}h last night, {avg_h:.1f}h avg, {debt_h:.1f}h debt this week"
        )

        chain = ReasoningChain(
            conclusion=conclusion,
            steps=steps,
            alternatives=alternatives,
            overall_confidence=_avg_confidence(steps),
        )
        self._chains.append(chain)
        return chain

    def get_last_chain(self) -> ReasoningChain | None:
        """Return the most recent reasoning chain (for /explain command)."""
        return self._chains[-1] if self._chains else None

    def get_all_chains(self) -> list[ReasoningChain]:
        """Return all reasoning chains from this session."""
        return list(self._chains)

    def format_all_context(self) -> str:
        """Format all recent reasoning chains for the LLM prompt."""
        if not self._chains:
            return ""
        lines = ["REASONING ANALYSIS:"]
        for chain in self._chains[-3:]:  # last 3 chains max
            lines.append(chain.to_prompt_context())
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _w_date(entry: dict) -> str:
    return (entry.get("id", entry.get("date", "")) or "")[:10]


def _avg_confidence(steps: list[ReasoningStep]) -> float:
    if not steps:
        return 0.5
    return sum(s.confidence for s in steps) / len(steps)
