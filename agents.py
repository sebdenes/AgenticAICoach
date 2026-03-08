"""Specialized coaching agents — domain-specific system prompts + tool subsets."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from engine_tools import TOOL_SCHEMAS

if TYPE_CHECKING:
    from engine_tools import CoachTools

log = logging.getLogger("coach.agents")

PROMPTS_DIR = Path(__file__).parent / "prompts"


# ── Agent definitions ─────────────────────────────────────────────────────────

@dataclass
class AgentConfig:
    """Configuration for a specialized coaching agent."""
    name: str
    prompt_file: str  # relative to prompts/
    allowed_tools: list[str] = field(default_factory=list)

    @property
    def tool_schemas(self) -> list[dict]:
        """Filter TOOL_SCHEMAS to only include this agent's allowed tools."""
        if not self.allowed_tools:
            return list(TOOL_SCHEMAS)  # all tools
        return [s for s in TOOL_SCHEMAS if s["name"] in self.allowed_tools]

    def load_prompt(self) -> str:
        """Load the agent-specific system prompt from file."""
        path = PROMPTS_DIR / self.prompt_file
        if path.exists():
            return path.read_text().strip()
        log.warning("Agent prompt not found: %s", path)
        return ""


# Agent registry
AGENTS = {
    "daily_coach": AgentConfig(
        name="daily_coach",
        prompt_file="agent_daily_coach.md",
        allowed_tools=[],  # empty = all tools
    ),
    "analysis": AgentConfig(
        name="analysis",
        prompt_file="agent_analysis.md",
        allowed_tools=[
            "get_wellness",
            "get_activities",
            "analyze_sleep",
            "analyze_recovery",
            "analyze_training_load",
            "get_alerts",
            "get_patterns",
            "get_coaching_state",
        ],
    ),
    "planning": AgentConfig(
        name="planning",
        prompt_file="agent_planning.md",
        allowed_tools=[
            "get_training_plan",
            "get_planned_events",
            "run_scenario",
            "get_race_countdown",
            "query_knowledge_base",
            "get_activities",
            "get_wellness",
            "get_coaching_state",
            "create_workout",
        ],
    ),
}


# ── Intent classification ─────────────────────────────────────────────────────

INTENT_KEYWORDS = {
    "analysis": [
        "pattern", "trend", "correlat", "data", "analyz", "insight",
        "hrv trend", "recovery trend", "sleep pattern", "how did i sleep",
        "how is my recovery", "what do the numbers", "metrics",
    ],
    "planning": [
        "plan", "scenario", "what if", "race", "taper", "replan",
        "schedule", "next week", "modify plan", "change plan",
        "race strategy", "pacing", "predict", "forecast",
        "workout", "create workout", "build workout", "interval session",
        "tempo run", "easy run", "long run", "sweet spot", "threshold",
    ],
}


def classify_intent(message: str) -> str:
    """Classify user message intent using keyword heuristics (zero LLM cost).

    Returns agent name: 'daily_coach', 'analysis', or 'planning'.
    Defaults to 'daily_coach' if no keywords match.
    """
    lower = message.lower()
    scores = {"analysis": 0, "planning": 0}

    for intent, keywords in INTENT_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                scores[intent] += 1

    # Return the intent with the highest score, or default
    best = max(scores, key=scores.get)
    if scores[best] > 0:
        log.debug("Intent classified as '%s' (score=%d) for: %s", best, scores[best], message[:80])
        return best

    return "daily_coach"


def get_agent(name: str) -> AgentConfig:
    """Get agent config by name, with fallback to daily_coach."""
    return AGENTS.get(name, AGENTS["daily_coach"])
