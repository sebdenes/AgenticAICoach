"""Unit tests for engine_tools.CoachTools."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure project root is on sys.path so engine_tools can be imported
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from engine_tools import CoachTools, TOOL_SCHEMAS


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_wellness():
    return [
        {"date": "2026-03-01", "hrv": 58.0, "restingHR": 42, "sleepSecs": 27000,
         "ctl": 38.0, "atl": 24.0, "tsb": 14.0, "steps": 8000},
        {"date": "2026-03-02", "hrv": 55.0, "restingHR": 44, "sleepSecs": 25200,
         "ctl": 38.5, "atl": 24.5, "tsb": 14.0, "steps": 7500},
    ]


@pytest.fixture
def mock_activities():
    return [
        {"type": "Run", "name": "Easy Run", "date": "2026-03-01",
         "moving_time": 3600, "tss": 55.0, "average_heartrate": 138},
        {"type": "Ride", "name": "Recovery Ride", "date": "2026-03-02",
         "moving_time": 2700, "tss": 35.0, "average_heartrate": 125},
    ]


@pytest.fixture
def mock_athlete():
    a = MagicMock()
    a.name = "Sebastien"
    a.race_date = "2026-04-20"
    a.race_name = "Boston Marathon"
    a.sleep_target_hours = 7.5
    a.hrv_baseline = 57
    a.rhr_baseline = 42
    a.latitude = 48.8566
    a.longitude = 2.3522
    return a


@pytest.fixture
def mock_iv(mock_wellness, mock_activities):
    iv = MagicMock()
    iv.wellness = AsyncMock(return_value=mock_wellness)
    iv.activities = AsyncMock(return_value=mock_activities)
    iv.events = AsyncMock(return_value=[
        {"name": "Easy Run", "start_date_local": "2026-03-09T09:00:00",
         "description": "45min easy", "type": "Run"}
    ])
    return iv


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_state = MagicMock(return_value=None)
    db.get_all_state = MagicMock(
        return_value={"last_checkin": "2026-03-07", "goal": "sub-3h marathon"}
    )
    return db


@pytest.fixture
def tools(mock_iv, mock_db, mock_athlete):
    return CoachTools(iv=mock_iv, db=mock_db, athlete=mock_athlete)


# ── TOOL_SCHEMAS ──────────────────────────────────────────────────────────────

class TestToolSchemas:
    def test_has_12_tools(self):
        assert len(TOOL_SCHEMAS) == 12

    def test_all_have_required_fields(self):
        for t in TOOL_SCHEMAS:
            assert "name" in t
            assert "description" in t
            assert "input_schema" in t
            assert len(t["description"]) > 20  # meaningful description

    def test_tool_names_match_executor_methods(self):
        # Every schema name must have a corresponding _tool_{name} method
        ct = CoachTools.__dict__
        for t in TOOL_SCHEMAS:
            assert f"_tool_{t['name']}" in ct, f"Missing _tool_{t['name']}"


# ── execute() dispatch ────────────────────────────────────────────────────────

class TestExecuteDispatch:
    @pytest.mark.anyio
    async def test_execute_unknown_tool_returns_error(self, tools):
        result = await tools.execute("nonexistent_tool", {})
        data = json.loads(result)
        assert "error" in data
        assert "nonexistent_tool" in data["error"]

    @pytest.mark.anyio
    async def test_execute_returns_json_string(self, tools):
        result = await tools.execute("get_wellness", {"days": 3})
        assert isinstance(result, str)
        data = json.loads(result)  # must be valid JSON
        assert isinstance(data, dict)

    @pytest.mark.anyio
    async def test_execute_exception_returns_error_json(self, mock_db, mock_athlete):
        bad_iv = MagicMock()
        bad_iv.wellness = AsyncMock(side_effect=RuntimeError("API down"))
        tools = CoachTools(iv=bad_iv, db=mock_db, athlete=mock_athlete)
        result = await tools.execute("get_wellness", {})
        data = json.loads(result)
        assert "error" in data


# ── get_wellness ──────────────────────────────────────────────────────────────

class TestGetWellness:
    @pytest.mark.anyio
    async def test_returns_records(self, tools, mock_iv, mock_wellness):
        result = json.loads(await tools.execute("get_wellness", {"days": 7}))
        assert result["count"] == len(mock_wellness)
        assert result["records"] == mock_wellness
        mock_iv.wellness.assert_called_once_with(days=7)

    @pytest.mark.anyio
    async def test_clamps_days_to_90(self, tools, mock_iv):
        await tools.execute("get_wellness", {"days": 999})
        mock_iv.wellness.assert_called_once_with(days=90)

    @pytest.mark.anyio
    async def test_clamps_days_minimum_1(self, tools, mock_iv):
        await tools.execute("get_wellness", {"days": 0})
        mock_iv.wellness.assert_called_once_with(days=1)

    @pytest.mark.anyio
    async def test_default_days_is_7(self, tools, mock_iv):
        await tools.execute("get_wellness", {})
        mock_iv.wellness.assert_called_once_with(days=7)


# ── get_activities ────────────────────────────────────────────────────────────

class TestGetActivities:
    @pytest.mark.anyio
    async def test_returns_all_activities(self, tools, mock_activities):
        result = json.loads(await tools.execute("get_activities", {}))
        assert result["count"] == 2
        assert len(result["activities"]) == 2

    @pytest.mark.anyio
    async def test_filters_by_activity_type(self, tools):
        result = json.loads(await tools.execute("get_activities", {"activity_type": "Run"}))
        assert result["count"] == 1
        assert result["activities"][0]["type"] == "Run"

    @pytest.mark.anyio
    async def test_type_filter_case_insensitive(self, tools):
        result = json.loads(await tools.execute("get_activities", {"activity_type": "run"}))
        assert result["count"] == 1

    @pytest.mark.anyio
    async def test_no_match_returns_empty(self, tools):
        result = json.loads(await tools.execute("get_activities", {"activity_type": "Swim"}))
        assert result["count"] == 0


# ── analyze_sleep ─────────────────────────────────────────────────────────────

class TestAnalyzeSleep:
    @pytest.mark.anyio
    async def test_calls_sleep_module(self, tools):
        with patch("modules.sleep.analyze_sleep",
                   return_value={"grade": "B", "hours_avg_7d": 7.1}) as mock:
            result = json.loads(await tools.execute("analyze_sleep", {"days": 7}))
            mock.assert_called_once()
            assert result["grade"] == "B"

    @pytest.mark.anyio
    async def test_clamps_days_minimum_7(self, tools, mock_iv):
        with patch("modules.sleep.analyze_sleep", return_value={}):
            await tools.execute("analyze_sleep", {"days": 3})
            mock_iv.wellness.assert_called_once_with(days=7)


# ── analyze_recovery ──────────────────────────────────────────────────────────

class TestAnalyzeRecovery:
    @pytest.mark.anyio
    async def test_returns_recovery_score(self, tools):
        # calculate_recovery_score returns a dict; the tool extracts the score value
        with patch("modules.recovery.calculate_recovery_score",
                   return_value={"score": 78, "grade": "B", "signals": []}):
            with patch("modules.thresholds.PersonalizedThresholds") as mock_thr:
                mock_thr.return_value.hrv_baseline = 57.0
                mock_thr.return_value.rhr_baseline = 42.0
                mock_thr.return_value.sleep_baseline = 7.5
                result = json.loads(await tools.execute("analyze_recovery", {}))
                assert "recovery_score" in result

    @pytest.mark.anyio
    async def test_falls_back_to_athlete_baselines_when_insufficient_data(
        self, mock_db, mock_athlete
    ):
        iv = MagicMock()
        iv.wellness = AsyncMock(
            return_value=[{"date": "2026-03-01", "hrv": 55}]  # < 7 days
        )
        iv.activities = AsyncMock(return_value=[])
        tools = CoachTools(iv=iv, db=mock_db, athlete=mock_athlete)
        with patch("modules.recovery.calculate_recovery_score",
                   return_value={"score": 65, "grade": "C", "signals": []}):
            result = json.loads(await tools.execute("analyze_recovery", {}))
            assert result["baselines"]["hrv"] == float(mock_athlete.hrv_baseline)


# ── get_weather ───────────────────────────────────────────────────────────────

class TestGetWeather:
    @pytest.mark.anyio
    async def test_returns_unavailable_when_no_provider(self, tools):
        result = json.loads(await tools.execute("get_weather", {}))
        assert result["available"] is False

    @pytest.mark.anyio
    async def test_returns_conditions_with_pace_adjustment(
        self, mock_iv, mock_db, mock_athlete
    ):
        wp = MagicMock()
        wp.fetch_current = AsyncMock(return_value={
            "temperature_c": 28.0,
            "feels_like_c": 30.0,
            "humidity_pct": 75,
            "wind_speed_kmh": 10.0,
            "wind_direction_deg": 180,
            "wind_gusts_kmh": 15.0,
            "precipitation_mm": 0.0,
            "precipitation_probability": 0,
            "uv_index": 6,
            "weather_code": 0,
            "description": "Sunny",
        })
        adj = MagicMock()
        adj.pace_modifier = 1.05
        adj.hydration_ml_per_hour = 800
        adj.clothing = "Light singlet"
        adj.warnings = ["High UV index"]
        we = MagicMock()
        we.assess_conditions = MagicMock(return_value=adj)
        tools = CoachTools(
            iv=mock_iv, db=mock_db, athlete=mock_athlete,
            weather_provider=wp, weather_engine=we,
        )
        result = json.loads(await tools.execute("get_weather", {}))
        assert result["available"] is True
        assert result["pace_adjustment_pct"] == pytest.approx(5.0)
        assert result["safety_warnings"] == ["High UV index"]


# ── get_race_countdown ────────────────────────────────────────────────────────

class TestGetRaceCountdown:
    @pytest.mark.anyio
    async def test_returns_days_to_race(self, tools, mock_wellness):
        result = json.loads(await tools.execute("get_race_countdown", {}))
        assert result["race_name"] == "Boston Marathon"
        assert isinstance(result["days_to_race"], int)
        assert result["days_to_race"] > 0

    @pytest.mark.anyio
    async def test_no_race_date_returns_none(self, mock_iv, mock_db, mock_athlete):
        mock_athlete.race_date = None
        tools = CoachTools(iv=mock_iv, db=mock_db, athlete=mock_athlete)
        result = json.loads(await tools.execute("get_race_countdown", {}))
        assert result["days_to_race"] is None


# ── query_knowledge_base ──────────────────────────────────────────────────────

class TestQueryKnowledgeBase:
    @pytest.mark.anyio
    async def test_returns_unavailable_when_no_rag(self, tools):
        result = json.loads(
            await tools.execute("query_knowledge_base", {"topic": "HRV"})
        )
        assert result["available"] is False

    @pytest.mark.anyio
    async def test_calls_rag_retrieve(self, mock_iv, mock_db, mock_athlete):
        rag = MagicMock()
        rag.retrieve_context = MagicMock(return_value="Rule 1: HRV > 7d mean is good.")
        tools = CoachTools(iv=mock_iv, db=mock_db, athlete=mock_athlete, rag=rag)
        result = json.loads(
            await tools.execute("query_knowledge_base", {"topic": "HRV recovery"})
        )
        assert result["available"] is True
        assert "Rule 1" in result["rules"]
        rag.retrieve_context.assert_called_once_with("HRV recovery", max_rules=4)


# ── run_scenario ──────────────────────────────────────────────────────────────

class TestRunScenario:
    @pytest.mark.anyio
    async def test_returns_unavailable_when_no_simulator(self, tools):
        result = json.loads(
            await tools.execute("run_scenario", {"description": "3h ride"})
        )
        assert result["available"] is False

    @pytest.mark.anyio
    async def test_calls_simulator(self, mock_iv, mock_db, mock_athlete):
        sim = MagicMock()
        workout = MagicMock()
        workout.sport = "Ride"
        workout.duration_minutes = 180
        workout.estimated_tss = 120.0
        workout.intensity = "easy"
        with patch(
            "modules.simulation.ScenarioSimulator.parse_workout_description",
            return_value=workout,
        ):
            sim.simulate = MagicMock(return_value=MagicMock())
            sim.format_result = MagicMock(return_value={"summary": "Go for it"})
            tools = CoachTools(
                iv=mock_iv, db=mock_db, athlete=mock_athlete, simulator=sim
            )
            result = json.loads(
                await tools.execute("run_scenario", {"description": "3h easy ride"})
            )
            assert result["available"] is True
            assert result["workout"]["sport"] == "Ride"


# ── get_training_plan ─────────────────────────────────────────────────────────

class TestGetTrainingPlan:
    @pytest.mark.anyio
    async def test_returns_none_when_no_plan(self, tools, mock_db):
        mock_db.get_state.return_value = None
        result = json.loads(await tools.execute("get_training_plan", {}))
        assert result["plan_exists"] is False
        assert result["sessions"] == []

    @pytest.mark.anyio
    async def test_returns_plan_when_present(self, tools, mock_db):
        # A minimal but valid plan dict — reconstruction will fail gracefully
        plan = {"mesocycles": [], "race_date": "2026-04-20", "race_name": "Boston", "version": 1, "goal_time": "3:00:00"}
        mock_db.get_state.return_value = plan
        result = json.loads(await tools.execute("get_training_plan", {}))
        assert result["plan_exists"] is True

    @pytest.mark.anyio
    async def test_calls_db_get_state_with_training_plan_key(self, tools, mock_db):
        mock_db.get_state.return_value = None
        await tools.execute("get_training_plan", {})
        mock_db.get_state.assert_called_once_with("training_plan")


# ── get_coaching_state ────────────────────────────────────────────────────────

class TestGetCoachingState:
    @pytest.mark.anyio
    async def test_returns_all_state_minus_training_plan(self, tools, mock_db):
        mock_db.get_all_state.return_value = {
            "last_checkin": "2026-03-07",
            "goal": "sub-3h marathon",
            "training_plan": {"mesocycles": []},  # should be excluded
        }
        result = json.loads(await tools.execute("get_coaching_state", {}))
        assert "training_plan" not in result
        assert result["goal"] == "sub-3h marathon"

    @pytest.mark.anyio
    async def test_returns_specific_keys(self, tools, mock_db):
        mock_db.get_all_state.return_value = {
            "key1": "val1",
            "key2": "val2",
            "key3": "val3",
        }
        result = json.loads(
            await tools.execute("get_coaching_state", {"keys": ["key1", "key3"]})
        )
        assert "key1" in result
        assert "key3" in result
        assert "key2" not in result

    @pytest.mark.anyio
    async def test_empty_state_returns_empty_dict(self, tools, mock_db):
        mock_db.get_all_state.return_value = {}
        result = json.loads(await tools.execute("get_coaching_state", {}))
        assert result == {}

    @pytest.mark.anyio
    async def test_keys_filter_ignores_missing_keys(self, tools, mock_db):
        mock_db.get_all_state.return_value = {"key1": "val1"}
        result = json.loads(
            await tools.execute(
                "get_coaching_state", {"keys": ["key1", "does_not_exist"]}
            )
        )
        assert result == {"key1": "val1"}


# ── get_planned_events ────────────────────────────────────────────────────────

class TestGetPlannedEvents:
    @pytest.mark.anyio
    async def test_returns_events(self, tools, mock_iv):
        result = json.loads(await tools.execute("get_planned_events", {}))
        assert "events" in result
        assert isinstance(result["events"], list)

    @pytest.mark.anyio
    async def test_passes_days_ahead_to_iv(self, tools, mock_iv):
        await tools.execute("get_planned_events", {"days_ahead": 7})
        mock_iv.events.assert_called_once_with(days_ahead=7)

    @pytest.mark.anyio
    async def test_default_days_ahead_is_3(self, tools, mock_iv):
        await tools.execute("get_planned_events", {})
        mock_iv.events.assert_called_once_with(days_ahead=3)


# ── analyze_training_load ─────────────────────────────────────────────────────

class TestAnalyzeTrainingLoad:
    @pytest.mark.anyio
    async def test_calls_performance_module(self, tools):
        with patch(
            "modules.performance.analyze_training",
            return_value={"ctl": 38.5, "atl": 24.5, "tsb": 14.0, "ctl_trend": "stable"},
        ) as mock:
            result = json.loads(await tools.execute("analyze_training_load", {"days": 14}))
            mock.assert_called_once()
            assert result["ctl"] == 38.5

    @pytest.mark.anyio
    async def test_default_days_is_14(self, tools, mock_iv):
        with patch("modules.performance.analyze_training", return_value={}):
            await tools.execute("analyze_training_load", {})
            mock_iv.wellness.assert_called_once_with(days=14)
