"""Tests for the scenario simulation module."""

import sys
from pathlib import Path

import pytest

# Ensure project root is on sys.path (match pattern from existing tests)
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from modules.simulation import ScenarioSimulator, SimulatedWorkout, SimulationResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def simulator():
    return ScenarioSimulator()


@pytest.fixture
def simulator_with_athlete():
    """Simulator with athlete config for race-day projections."""
    from config import AthleteConfig
    athlete = AthleteConfig(
        race_date="2026-04-20",
        race_name="Boston Marathon",
        goal_time="3:00:00",
        ftp=315,
    )
    return ScenarioSimulator(athlete=athlete)


@pytest.fixture
def mock_wellness():
    """14 days of mock wellness data with CTL/ATL."""
    return [
        {"id": "2026-02-22", "ctl": 42.0, "atl": 50.0, "hrv": 55.0, "restingHR": 43, "sleepSecs": 27000},
        {"id": "2026-02-23", "ctl": 42.5, "atl": 51.0, "hrv": 53.0, "restingHR": 44, "sleepSecs": 25200},
        {"id": "2026-02-24", "ctl": 43.0, "atl": 49.0, "hrv": 58.0, "restingHR": 41, "sleepSecs": 28800},
        {"id": "2026-02-25", "ctl": 43.5, "atl": 52.0, "hrv": 54.0, "restingHR": 43, "sleepSecs": 26400},
        {"id": "2026-02-26", "ctl": 44.0, "atl": 55.0, "hrv": 50.0, "restingHR": 46, "sleepSecs": 21600},
        {"id": "2026-02-27", "ctl": 44.5, "atl": 54.0, "hrv": 51.0, "restingHR": 45, "sleepSecs": 24000},
        {"id": "2026-02-28", "ctl": 45.0, "atl": 49.0, "hrv": 60.0, "restingHR": 40, "sleepSecs": 28200},
        {"id": "2026-03-01", "ctl": 45.5, "atl": 50.5, "hrv": 58.0, "restingHR": 42, "sleepSecs": 27600},
        {"id": "2026-03-02", "ctl": 46.0, "atl": 53.0, "hrv": 54.0, "restingHR": 44, "sleepSecs": 25800},
        {"id": "2026-03-03", "ctl": 46.5, "atl": 48.0, "hrv": 62.0, "restingHR": 40, "sleepSecs": 28800},
        {"id": "2026-03-04", "ctl": 47.0, "atl": 51.0, "hrv": 57.0, "restingHR": 42, "sleepSecs": 27000},
        {"id": "2026-03-05", "ctl": 47.5, "atl": 57.0, "hrv": 48.0, "restingHR": 47, "sleepSecs": 18000},
        {"id": "2026-03-06", "ctl": 48.0, "atl": 55.0, "hrv": 51.0, "restingHR": 46, "sleepSecs": 23400},
        {"id": "2026-03-07", "ctl": 48.5, "atl": 49.0, "hrv": 65.0, "restingHR": 40, "sleepSecs": 28800},
    ]


@pytest.fixture
def mock_activities():
    """Recent activities for context."""
    return [
        {
            "type": "Run", "start_date_local": "2026-03-01T07:00:00",
            "name": "Easy Run", "moving_time": 3600, "distance": 10000,
            "icu_training_load": 65,
        },
        {
            "type": "Ride", "start_date_local": "2026-03-03T08:30:00",
            "name": "Zone 2 Ride", "moving_time": 5400, "distance": 40000,
            "icu_training_load": 85,
        },
        {
            "type": "Run", "start_date_local": "2026-03-05T06:30:00",
            "name": "Tempo Run", "moving_time": 3000, "distance": 8500,
            "icu_training_load": 95,
        },
    ]


# ===========================================================================
# TSS Estimation
# ===========================================================================

class TestTSSEstimation:
    """Verify TSS estimation for various sports/durations/intensities."""

    def test_easy_run_60min(self, simulator):
        w = SimulatedWorkout(sport="Run", duration_minutes=60, estimated_tss=0, intensity="easy")
        tss = simulator._estimate_tss(w)
        assert tss == pytest.approx(50.0)  # 1h * 50 TSS/h

    def test_hard_run_60min(self, simulator):
        w = SimulatedWorkout(sport="Run", duration_minutes=60, estimated_tss=0, intensity="hard")
        tss = simulator._estimate_tss(w)
        assert tss == pytest.approx(90.0)  # 1h * 90 TSS/h

    def test_easy_ride_180min(self, simulator):
        w = SimulatedWorkout(sport="Ride", duration_minutes=180, estimated_tss=0, intensity="easy")
        tss = simulator._estimate_tss(w)
        assert tss == pytest.approx(120.0)  # 3h * 40 TSS/h

    def test_moderate_ride_120min(self, simulator):
        w = SimulatedWorkout(sport="Ride", duration_minutes=120, estimated_tss=0, intensity="moderate")
        tss = simulator._estimate_tss(w)
        assert tss == pytest.approx(120.0)  # 2h * 60 TSS/h

    def test_strength_60min(self, simulator):
        w = SimulatedWorkout(sport="Strength", duration_minutes=60, estimated_tss=0, intensity="moderate")
        tss = simulator._estimate_tss(w)
        assert tss == pytest.approx(45.0)  # 1h * 45 TSS/h

    def test_custom_tss_preserved(self, simulator, mock_wellness):
        """If TSS is provided in the workout, do not override it."""
        w = SimulatedWorkout(sport="Run", duration_minutes=60, estimated_tss=120.0, intensity="easy")
        result = simulator.simulate(w, mock_wellness)
        # The provided TSS should be used, not estimated
        assert result.workout.estimated_tss == 120.0


# ===========================================================================
# CTL/ATL Projection
# ===========================================================================

class TestCTLATLProjection:
    """Verify exponential decay math."""

    def test_ctl_increases_after_workout(self, simulator, mock_wellness):
        w = SimulatedWorkout(sport="Run", duration_minutes=60, estimated_tss=100, intensity="hard")
        result = simulator.simulate(w, mock_wellness)
        assert result.projected_ctl > result.current_ctl

    def test_atl_increases_more_than_ctl(self, simulator, mock_wellness):
        w = SimulatedWorkout(sport="Run", duration_minutes=60, estimated_tss=100, intensity="hard")
        result = simulator.simulate(w, mock_wellness)
        # ATL has a shorter time constant so reacts faster
        assert result.atl_delta > result.ctl_delta

    def test_tsb_drops_after_hard_workout(self, simulator, mock_wellness):
        """A hard workout with high TSS should cause TSB to drop."""
        w = SimulatedWorkout(sport="Run", duration_minutes=120, estimated_tss=200, intensity="hard")
        result = simulator.simulate(w, mock_wellness)
        assert result.tsb_delta < 0

    def test_zero_tss_decays(self, simulator):
        """With TSS=0, CTL and ATL should decay toward zero."""
        wellness = [{"id": "2026-03-07", "ctl": 50.0, "atl": 60.0}]
        w = SimulatedWorkout(sport="Run", duration_minutes=0, estimated_tss=0, intensity="easy")
        # Manually set tss to 0 for this test
        w.estimated_tss = 0
        result = simulator.simulate(w, wellness)
        # With TSS=0, CTL should decay: new = 50 + (0-50)/42 = 50 - 1.19 ~ 48.8
        assert result.projected_ctl < 50.0
        # ATL decays faster: new = 60 + (0-60)/7 = 60 - 8.57 ~ 51.4
        assert result.projected_atl < 60.0

    def test_math_matches_formula(self, simulator, mock_wellness):
        """Verify the projected values match the EWMA formula exactly."""
        w = SimulatedWorkout(sport="Run", duration_minutes=60, estimated_tss=80, intensity="moderate")
        result = simulator.simulate(w, mock_wellness)

        # Latest: CTL=48.5, ATL=49.0
        expected_ctl = 48.5 + (80 - 48.5) / 42
        expected_atl = 49.0 + (80 - 49.0) / 7
        expected_tsb = expected_ctl - expected_atl

        assert result.projected_ctl == pytest.approx(round(expected_ctl, 1), abs=0.15)
        assert result.projected_atl == pytest.approx(round(expected_atl, 1), abs=0.15)
        assert result.projected_tsb == pytest.approx(round(expected_tsb, 1), abs=0.15)


# ===========================================================================
# Recovery Timeline
# ===========================================================================

class TestRecoveryTimeline:
    """Verify days-to-recovery calculation."""

    def test_easy_workout_quick_recovery(self, simulator, mock_wellness):
        w = SimulatedWorkout(sport="Run", duration_minutes=45, estimated_tss=40, intensity="easy")
        result = simulator.simulate(w, mock_wellness)
        # Easy workout should have short recovery
        assert result.days_to_baseline_tsb <= 5

    def test_hard_workout_longer_recovery(self, simulator, mock_wellness):
        w_easy = SimulatedWorkout(sport="Run", duration_minutes=45, estimated_tss=40, intensity="easy")
        w_hard = SimulatedWorkout(sport="Run", duration_minutes=120, estimated_tss=200, intensity="hard")
        r_easy = simulator.simulate(w_easy, mock_wellness)
        r_hard = simulator.simulate(w_hard, mock_wellness)
        assert r_hard.days_to_baseline_tsb >= r_easy.days_to_baseline_tsb

    def test_max_iterations_cap(self, simulator, mock_wellness):
        """Recovery days should never exceed 60."""
        w = SimulatedWorkout(sport="Run", duration_minutes=300, estimated_tss=500, intensity="hard")
        result = simulator.simulate(w, mock_wellness)
        assert result.days_to_recovery <= 60
        assert result.days_to_baseline_tsb <= 60


# ===========================================================================
# Race Day Projection
# ===========================================================================

class TestRaceDayProjection:
    """Verify race-day CTL/TSB projection."""

    def test_projects_ctl_on_race_day(self, simulator_with_athlete, mock_wellness):
        w = SimulatedWorkout(sport="Run", duration_minutes=60, estimated_tss=70, intensity="moderate")
        result = simulator_with_athlete.simulate(w, mock_wellness)
        assert result.race_ctl_projected is not None
        assert result.race_tsb_projected is not None
        assert result.days_to_race is not None
        assert result.days_to_race > 0

    def test_readiness_improved(self, simulator_with_athlete, mock_wellness):
        """A moderate workout far from race should improve readiness."""
        w = SimulatedWorkout(sport="Run", duration_minutes=60, estimated_tss=70, intensity="moderate")
        result = simulator_with_athlete.simulate(w, mock_wellness)
        # With training, CTL should be higher at race day than without
        assert result.race_readiness_change in ("improved", "unchanged")

    def test_readiness_worsened_near_race(self, mock_wellness):
        """A very hard workout close to race day should worsen readiness."""
        from config import AthleteConfig
        # Race in 3 days
        from datetime import datetime, timedelta
        near_race_date = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        athlete = AthleteConfig(race_date=near_race_date, race_name="Test Race")
        sim = ScenarioSimulator(athlete=athlete)
        w = SimulatedWorkout(sport="Run", duration_minutes=180, estimated_tss=250, intensity="hard")
        result = sim.simulate(w, mock_wellness)
        # Near race with high TSS should worsen or trigger skip
        assert result.race_readiness_change in ("worsened", "unchanged") or \
               "Skip" in result.recommendation or "preserve" in result.recommendation.lower()

    def test_no_race_date_skips_projection(self, simulator, mock_wellness):
        """Without a race date, race projections should be None."""
        w = SimulatedWorkout(sport="Run", duration_minutes=60, estimated_tss=70, intensity="moderate")
        result = simulator.simulate(w, mock_wellness)
        assert result.race_ctl_projected is None
        assert result.race_tsb_projected is None
        assert result.days_to_race is None


# ===========================================================================
# Workout Parsing
# ===========================================================================

class TestWorkoutParsing:
    """Verify natural language parsing."""

    def test_3h_ride(self):
        w = ScenarioSimulator.parse_workout_description("3h ride")
        assert w.sport == "Ride"
        assert w.duration_minutes == 180

    def test_3h_easy_ride(self):
        w = ScenarioSimulator.parse_workout_description("3h easy ride")
        assert w.sport == "Ride"
        assert w.duration_minutes == 180
        assert w.intensity == "easy"

    def test_60min_easy_run(self):
        w = ScenarioSimulator.parse_workout_description("60min easy run")
        assert w.sport == "Run"
        assert w.duration_minutes == 60
        assert w.intensity == "easy"

    def test_10km_tempo_run(self):
        w = ScenarioSimulator.parse_workout_description("10km tempo run")
        assert w.sport == "Run"
        assert w.intensity == "hard"
        assert w.duration_minutes > 0  # Should estimate from distance

    def test_intervals(self):
        w = ScenarioSimulator.parse_workout_description("intervals 8x400m")
        assert w.sport == "Run"
        assert w.intensity == "hard"
        assert w.duration_minutes > 0

    def test_long_run_2h(self):
        w = ScenarioSimulator.parse_workout_description("2h long run")
        assert w.sport == "Run"
        assert w.duration_minutes == 120
        assert w.intensity == "moderate"

    def test_strength(self):
        w = ScenarioSimulator.parse_workout_description("strength session")
        assert w.sport == "Strength"
        assert w.intensity == "moderate"
        assert w.duration_minutes == 60  # default

    def test_z2_ride(self):
        w = ScenarioSimulator.parse_workout_description("90min Z2 ride")
        assert w.sport == "Ride"
        assert w.duration_minutes == 90
        assert w.intensity == "easy"

    def test_unknown_defaults(self):
        """An ambiguous description should produce reasonable defaults."""
        w = ScenarioSimulator.parse_workout_description("45min workout")
        assert w.duration_minutes == 45
        assert w.intensity == "moderate"


# ===========================================================================
# Full Simulation (end-to-end)
# ===========================================================================

class TestFullSimulation:
    """End-to-end simulation tests."""

    def test_simulation_returns_all_fields(self, simulator, mock_wellness):
        w = SimulatedWorkout(sport="Run", duration_minutes=60, estimated_tss=70, intensity="moderate")
        result = simulator.simulate(w, mock_wellness)

        assert isinstance(result, SimulationResult)
        assert result.current_ctl > 0
        assert result.current_atl > 0
        assert result.projected_ctl > 0
        assert result.projected_atl > 0
        assert isinstance(result.days_to_recovery, int)
        assert isinstance(result.days_to_baseline_tsb, int)
        assert result.recommendation != ""
        assert result.workout is not None

    def test_recommendation_skip_when_tsb_very_low(self, simulator):
        """When starting TSB is already very negative and workout is hard, recommend skip."""
        # Athlete already deeply fatigued: CTL=30, ATL=65 -> TSB=-35
        wellness = [{"id": "2026-03-07", "ctl": 30.0, "atl": 65.0}]
        w = SimulatedWorkout(sport="Run", duration_minutes=120, estimated_tss=200, intensity="hard")
        result = simulator.simulate(w, wellness)
        assert "Skip" in result.recommendation or "Reduce" in result.recommendation

    def test_recommendation_go_when_tsb_ok(self, simulator, mock_wellness):
        """When TSB is reasonable and workout is easy, recommend go for it."""
        w = SimulatedWorkout(sport="Run", duration_minutes=45, estimated_tss=35, intensity="easy")
        result = simulator.simulate(w, mock_wellness)
        assert "Go for it" in result.recommendation


# ===========================================================================
# Formatting
# ===========================================================================

class TestFormatting:
    """Verify output formatting."""

    def test_telegram_format_contains_key_info(self, simulator, mock_wellness):
        w = SimulatedWorkout(
            sport="Run", duration_minutes=60, estimated_tss=70,
            intensity="moderate", description="60min moderate run",
        )
        result = simulator.simulate(w, mock_wellness)
        text = simulator.format_result(result)

        assert "Simulation" in text
        assert "CTL" in text
        assert "ATL" in text
        assert "TSB" in text
        assert "Recommendation" in text
        assert "Recovery" in text

    def test_prompt_format_compact(self, simulator, mock_wellness):
        w = SimulatedWorkout(
            sport="Ride", duration_minutes=120, estimated_tss=100,
            intensity="moderate", description="2h moderate ride",
        )
        result = simulator.simulate(w, mock_wellness)
        text = simulator.format_result_for_prompt(result)

        assert "SIMULATION" in text
        assert "CTL" in text
        assert "Recommendation" in text
        # Should be shorter than the telegram format
        telegram_text = simulator.format_result(result)
        assert len(text) < len(telegram_text)
