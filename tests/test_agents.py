"""Tests for the agent system — intent classification and tool filtering."""

import pytest

from agents import classify_intent, get_agent, AGENTS, INTENT_KEYWORDS
from engine_tools import TOOL_SCHEMAS


# ── Intent classification ────────────────────────────────────

class TestClassifyIntent:
    """Test keyword-based intent classification."""

    def test_analysis_keywords(self):
        assert classify_intent("How did I sleep this week?") == "analysis"
        assert classify_intent("Show me HRV trends") == "analysis"
        assert classify_intent("Analyze my recovery patterns") == "analysis"
        assert classify_intent("What do the metrics say?") == "analysis"

    def test_planning_keywords(self):
        assert classify_intent("What if I do a tempo run tomorrow?") == "planning"
        assert classify_intent("Create a sweet spot workout") == "planning"
        assert classify_intent("Build me an interval session") == "planning"
        assert classify_intent("What's my race strategy?") == "planning"
        assert classify_intent("Can you schedule a long run for Sunday?") == "planning"

    def test_default_to_daily_coach(self):
        assert classify_intent("Good morning") == "daily_coach"
        assert classify_intent("Hello") == "daily_coach"
        assert classify_intent("How should I train today?") == "daily_coach"
        assert classify_intent("Thanks coach") == "daily_coach"

    def test_case_insensitive(self):
        assert classify_intent("SHOW ME HRV TRENDS") == "analysis"
        assert classify_intent("Create A Workout") == "planning"


# ── Agent registry ───────────────────────────────────────────

class TestAgentRegistry:
    """Test agent definitions and tool filtering."""

    def test_three_agents_defined(self):
        assert len(AGENTS) == 3
        assert set(AGENTS.keys()) == {"daily_coach", "analysis", "planning"}

    def test_daily_coach_has_all_tools(self):
        agent = AGENTS["daily_coach"]
        assert agent.allowed_tools == []  # empty = all tools
        assert len(agent.tool_schemas) == len(TOOL_SCHEMAS)

    def test_analysis_agent_tools(self):
        agent = AGENTS["analysis"]
        assert len(agent.allowed_tools) > 0
        assert "get_wellness" in agent.allowed_tools
        assert "get_activities" in agent.allowed_tools
        assert "analyze_sleep" in agent.allowed_tools
        # Should NOT have planning tools
        assert "run_scenario" not in agent.allowed_tools
        assert "create_workout" not in agent.allowed_tools

    def test_planning_agent_tools(self):
        agent = AGENTS["planning"]
        assert "get_training_plan" in agent.allowed_tools
        assert "run_scenario" in agent.allowed_tools
        assert "create_workout" in agent.allowed_tools
        # Should NOT have analysis-only tools
        assert "analyze_sleep" not in agent.allowed_tools

    def test_tool_schemas_filtered(self):
        agent = AGENTS["analysis"]
        schema_names = {s["name"] for s in agent.tool_schemas}
        assert schema_names == set(agent.allowed_tools)

    def test_get_agent_fallback(self):
        agent = get_agent("nonexistent")
        assert agent.name == "daily_coach"

    def test_agent_prompt_files_exist(self):
        for agent in AGENTS.values():
            prompt = agent.load_prompt()
            assert len(prompt) > 0, f"Agent {agent.name} has empty prompt"


# ── Tool count ───────────────────────────────────────────────

def test_total_tool_count():
    """Ensure we have the expected number of tools."""
    assert len(TOOL_SCHEMAS) >= 15, f"Expected >=15 tools, got {len(TOOL_SCHEMAS)}"


def test_create_workout_tool_exists():
    tool_names = [s["name"] for s in TOOL_SCHEMAS]
    assert "create_workout" in tool_names
