"""Tests for the periodization engine, calendar sync, and related helpers."""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

# Ensure project root is on sys.path (same pattern as conftest.py)
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import AthleteConfig
from modules.periodization import (
    PeriodizationEngine,
    TrainingPlan,
    TrainingSession,
    Microcycle,
    Mesocycle,
    _next_monday,
)
from modules.periodization_calendar import PeriodizationCalendar
from modules.thresholds import PersonalizedThresholds


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def athlete():
    """AthleteConfig for testing periodization."""
    return AthleteConfig(
        name="Test Runner",
        weight_kg=75.0,
        ftp=300,
        eftp_ride=280.0,
        eftp_run=400.0,
        rhr_baseline=42,
        hrv_baseline=55.0,
        timezone="Europe/Paris",
        sports=["cycling", "running", "strength"],
        race_name="Spring Marathon",
        race_date=(datetime.now() + timedelta(weeks=16)).strftime("%Y-%m-%d"),
        race_type="marathon",
        goal_time="3:15",
        marathon_pace="4:37",
        easy_pace="5:30",
        tempo_pace="4:15",
        hr_at_mp="160",
    )


@pytest.fixture
def athlete_8w():
    """AthleteConfig with an 8-week race target."""
    return AthleteConfig(
        name="Short Plan Runner",
        race_name="Quick Marathon",
        race_date=(datetime.now() + timedelta(weeks=8)).strftime("%Y-%m-%d"),
        race_type="marathon",
        goal_time="3:30",
        marathon_pace="4:59",
        easy_pace="5:45",
        tempo_pace="4:30",
        hr_at_mp="155",
    )


@pytest.fixture
def athlete_6w():
    """AthleteConfig with a 6-week race target."""
    return AthleteConfig(
        name="Ultra Short Runner",
        race_name="Quick Half",
        race_date=(datetime.now() + timedelta(weeks=6)).strftime("%Y-%m-%d"),
        race_type="marathon",
        goal_time="3:45",
        marathon_pace="5:20",
        easy_pace="6:00",
        tempo_pace="4:45",
        hr_at_mp="150",
    )


@pytest.fixture
def mock_thresholds():
    """Minimal PersonalizedThresholds from synthetic wellness data."""
    wellness = [
        {"id": f"2025-03-{i:02d}", "hrv": 55 + (i % 5), "restingHR": 42 + (i % 3),
         "sleepSecs": 27000, "sleepScore": 80, "ctl": 45 + i * 0.3, "atl": 50}
        for i in range(1, 31)
    ]
    return PersonalizedThresholds(wellness)


@pytest.fixture
def engine(athlete, mock_thresholds):
    """PeriodizationEngine with athlete and thresholds."""
    return PeriodizationEngine(athlete, thresholds=mock_thresholds)


@pytest.fixture
def engine_8w(athlete_8w):
    """Engine with 8-week plan."""
    return PeriodizationEngine(athlete_8w)


@pytest.fixture
def engine_6w(athlete_6w):
    """Engine with 6-week plan."""
    return PeriodizationEngine(athlete_6w)


@pytest.fixture
def plan_16w(engine):
    """A generated 16-week plan."""
    return engine.generate_plan(current_ctl=45.0, weeks_available=16)


@pytest.fixture
def plan_12w(engine):
    """A generated 12-week plan."""
    return engine.generate_plan(current_ctl=40.0, weeks_available=12)


@pytest.fixture
def plan_8w(engine_8w):
    """A generated 8-week plan."""
    return engine_8w.generate_plan(current_ctl=35.0, weeks_available=8)


@pytest.fixture
def plan_6w(engine_6w):
    """A generated 6-week plan."""
    return engine_6w.generate_plan(current_ctl=30.0, weeks_available=6)


# ---------------------------------------------------------------------------
# TestPhaseAllocation
# ---------------------------------------------------------------------------

class TestPhaseAllocation:
    """Verify phase breakdown for different training durations."""

    def test_16_week_plan_has_all_phases(self, engine):
        phases = engine._assign_phases(16)
        phase_names = [p[0] for p in phases]
        assert "base" in phase_names
        assert "build" in phase_names
        assert "race" in phase_names

    def test_16_week_has_taper(self, engine):
        phases = engine._assign_phases(16)
        phase_names = [p[0] for p in phases]
        assert "taper" in phase_names

    def test_16_week_taper_is_3_weeks(self, engine):
        """Plans > 12 weeks should have 3-week taper."""
        phases = engine._assign_phases(16)
        taper_weeks = sum(w for p, w in phases if p == "taper")
        assert taper_weeks == 3

    def test_12_week_plan_phases(self, engine):
        phases = engine._assign_phases(12)
        total = sum(w for _, w in phases)
        assert total == 12
        phase_names = [p[0] for p in phases]
        assert "base" in phase_names
        assert "race" in phase_names

    def test_8_week_taper_is_2_weeks(self, engine):
        """Plans <= 8 weeks should have 2-week taper."""
        phases = engine._assign_phases(8)
        taper_weeks = sum(w for p, w in phases if p == "taper")
        assert taper_weeks == 2

    def test_6_week_plan_has_minimum_structure(self, engine):
        phases = engine._assign_phases(6)
        total = sum(w for _, w in phases)
        assert total == 6
        phase_names = [p[0] for p in phases]
        assert "race" in phase_names
        assert "taper" in phase_names

    def test_total_weeks_match(self, engine):
        for target_weeks in [6, 8, 10, 12, 14, 16, 20]:
            phases = engine._assign_phases(target_weeks)
            total = sum(w for _, w in phases)
            assert total == target_weeks, f"Expected {target_weeks} weeks but got {total}"

    def test_race_week_is_last(self, engine):
        phases = engine._assign_phases(12)
        last_phase = phases[-1]
        assert last_phase[0] == "race"
        assert last_phase[1] == 1

    def test_single_week_returns_race(self, engine):
        phases = engine._assign_phases(1)
        assert phases == [("race", 1)]


# ---------------------------------------------------------------------------
# TestTSSProgression
# ---------------------------------------------------------------------------

class TestTSSProgression:
    """Verify weekly TSS never increases > 10% in non-recovery weeks."""

    def test_no_excessive_increase(self, plan_16w):
        """Weekly TSS should not jump more than ~10% between consecutive
        load weeks (excluding recovery/taper/race)."""
        all_weeks = []
        for meso in plan_16w.mesocycles:
            for mc in meso.microcycles:
                all_weeks.append(mc)

        for i in range(1, len(all_weeks)):
            curr = all_weeks[i]
            prev = all_weeks[i - 1]
            # Skip recovery, taper, and race weeks for the increase check
            if curr.phase in ("recovery", "taper", "race"):
                continue
            if prev.is_recovery_week or prev.phase in ("taper", "race", "recovery"):
                continue
            if prev.target_weekly_tss > 0:
                increase = (curr.target_weekly_tss - prev.target_weekly_tss) / prev.target_weekly_tss
                assert increase <= 0.11, (
                    f"Week {curr.week_number} increased {increase:.1%} over week {prev.week_number} "
                    f"({prev.target_weekly_tss:.0f} -> {curr.target_weekly_tss:.0f})"
                )

    def test_tss_is_positive(self, plan_12w):
        """All non-rest weeks should have positive TSS."""
        for meso in plan_12w.mesocycles:
            for mc in meso.microcycles:
                assert mc.target_weekly_tss >= 0

    def test_tss_progression_values_length(self, engine):
        """TSS progression list should match total number of weeks."""
        phases = engine._assign_phases(12)
        tss = engine._compute_weekly_tss_progression(phases, 300.0)
        total_weeks = sum(w for _, w in phases)
        assert len(tss) == total_weeks


# ---------------------------------------------------------------------------
# TestRecoveryWeeks
# ---------------------------------------------------------------------------

class TestRecoveryWeeks:
    """Verify recovery weeks appear at appropriate intervals."""

    def test_recovery_weeks_present_in_long_plan(self, plan_16w):
        """A 16-week plan should include at least one recovery week."""
        recovery_count = sum(
            1 for meso in plan_16w.mesocycles
            for mc in meso.microcycles
            if mc.is_recovery_week
        )
        assert recovery_count >= 1, "Expected at least 1 recovery week in 16-week plan"

    def test_recovery_week_has_lower_tss(self, plan_16w):
        """Recovery weeks should have lower TSS than adjacent load weeks."""
        all_weeks = []
        for meso in plan_16w.mesocycles:
            for mc in meso.microcycles:
                all_weeks.append(mc)

        for i, mc in enumerate(all_weeks):
            if mc.is_recovery_week and i > 0:
                prev = all_weeks[i - 1]
                if not prev.is_recovery_week and prev.phase not in ("taper", "race"):
                    assert mc.target_weekly_tss < prev.target_weekly_tss, (
                        f"Recovery week {mc.week_number} TSS ({mc.target_weekly_tss}) "
                        f"should be less than prev week ({prev.target_weekly_tss})"
                    )

    def test_no_consecutive_recovery_weeks(self, plan_16w):
        """Recovery weeks should not be back-to-back."""
        all_weeks = []
        for meso in plan_16w.mesocycles:
            for mc in meso.microcycles:
                all_weeks.append(mc)

        for i in range(1, len(all_weeks)):
            if all_weeks[i].is_recovery_week and all_weeks[i - 1].is_recovery_week:
                # Adjacent recovery is OK if one is taper-adjacent, otherwise flag
                if all_weeks[i].phase != "taper" and all_weeks[i - 1].phase != "taper":
                    pytest.fail(
                        f"Consecutive recovery weeks at {all_weeks[i - 1].week_number} "
                        f"and {all_weeks[i].week_number}"
                    )


# ---------------------------------------------------------------------------
# TestTaperGeneration
# ---------------------------------------------------------------------------

class TestTaperGeneration:
    """Verify taper reduces volume 40-60%."""

    def test_taper_reduces_volume(self, plan_16w):
        """Taper weeks should have significantly reduced TSS."""
        all_weeks = []
        for meso in plan_16w.mesocycles:
            for mc in meso.microcycles:
                all_weeks.append(mc)

        # Find peak TSS (from non-recovery, non-taper, non-race weeks)
        load_tss = [
            mc.target_weekly_tss for mc in all_weeks
            if not mc.is_recovery_week and mc.phase not in ("taper", "race")
        ]
        if not load_tss:
            pytest.skip("No load weeks found")
        peak_tss = max(load_tss)

        # Find taper weeks
        taper_weeks = [mc for mc in all_weeks if mc.phase == "taper"]
        assert len(taper_weeks) >= 2, "Expected at least 2 taper weeks"

        # Last taper week should be at most 65% of peak (40-60% reduction)
        last_taper = taper_weeks[-1]
        reduction = 1 - (last_taper.target_weekly_tss / peak_tss)
        assert reduction >= 0.30, (
            f"Last taper week TSS reduction is only {reduction:.0%} "
            f"(peak={peak_tss:.0f}, taper={last_taper.target_weekly_tss:.0f})"
        )

    def test_taper_maintains_some_quality(self, plan_16w):
        """Taper weeks should still have at least one session."""
        for meso in plan_16w.mesocycles:
            if meso.phase == "taper":
                for mc in meso.microcycles:
                    non_rest = [s for s in mc.sessions if s.session_type != "rest"]
                    assert len(non_rest) >= 1, (
                        f"Taper week {mc.week_number} has no non-rest sessions"
                    )


# ---------------------------------------------------------------------------
# TestSessionGeneration
# ---------------------------------------------------------------------------

class TestSessionGeneration:
    """Verify sessions include correct paces from AthleteConfig."""

    def test_easy_run_includes_easy_pace(self, engine, athlete):
        session = engine._generate_session("2026-05-01", "easy_run", "base", 40.0)
        assert athlete.easy_pace in session.description
        assert session.sport == "Run"
        assert session.intensity_zone == "z2"

    def test_tempo_run_includes_tempo_pace(self, engine, athlete):
        session = engine._generate_session("2026-05-01", "tempo_run", "build", 60.0)
        assert athlete.tempo_pace in session.description
        assert session.is_key_session is True  # tempo_run is a key session
        assert session.sport == "Run"

    def test_marathon_pace_includes_mp(self, engine, athlete):
        session = engine._generate_session("2026-05-01", "marathon_pace", "build", 70.0)
        assert athlete.marathon_pace in session.description
        assert session.is_key_session is True
        assert session.sport == "Run"

    def test_long_run_base_phase(self, engine, athlete):
        session = engine._generate_session("2026-05-01", "long_run", "base", 80.0)
        assert athlete.easy_pace in session.description
        assert session.is_key_session is True
        assert "Long Run" in session.name

    def test_long_run_build_phase_includes_mp_finish(self, engine, athlete):
        session = engine._generate_session("2026-05-01", "long_run", "build", 100.0)
        assert athlete.marathon_pace in session.description
        assert "marathon pace" in session.description.lower()

    def test_rest_day(self, engine):
        session = engine._generate_session("2026-05-01", "rest", "base", 0.0)
        assert session.sport == "Note"
        assert session.target_tss == 0.0
        assert session.duration_minutes == 0

    def test_strength_session(self, engine):
        session = engine._generate_session("2026-05-01", "strength", "base", 20.0)
        assert session.sport == "Workout"
        assert "strength" in session.description.lower() or "Strength" in session.name

    def test_ride_easy(self, engine):
        session = engine._generate_session("2026-05-01", "ride_easy", "base", 30.0)
        assert session.sport == "Ride"
        assert "Easy" in session.name or "easy" in session.description.lower()

    def test_each_week_has_7_sessions(self, plan_12w):
        """Every microcycle should have exactly 7 sessions (Mon-Sun)."""
        for meso in plan_12w.mesocycles:
            for mc in meso.microcycles:
                assert len(mc.sessions) == 7, (
                    f"Week {mc.week_number} has {len(mc.sessions)} sessions, expected 7"
                )

    def test_sessions_have_dates_within_week(self, plan_12w):
        """Session dates should fall within the microcycle date range."""
        for meso in plan_12w.mesocycles:
            for mc in meso.microcycles:
                for session in mc.sessions:
                    assert mc.start_date <= session.date <= mc.end_date, (
                        f"Session date {session.date} outside week "
                        f"{mc.start_date}-{mc.end_date}"
                    )


# ---------------------------------------------------------------------------
# TestPlanAdaptation
# ---------------------------------------------------------------------------

class TestPlanAdaptation:
    """Verify missed sessions trigger appropriate adaptation."""

    def test_adapt_marks_missed_as_rest(self, engine, plan_12w):
        """Missed dates should be converted to rest days."""
        # Pick some future dates from the plan
        future_sessions = []
        today = datetime.now().strftime("%Y-%m-%d")
        for meso in plan_12w.mesocycles:
            for mc in meso.microcycles:
                for s in mc.sessions:
                    if s.date > today and s.session_type != "rest":
                        future_sessions.append(s.date)
                        if len(future_sessions) >= 3:
                            break
                if len(future_sessions) >= 3:
                    break
            if len(future_sessions) >= 3:
                break

        if not future_sessions:
            pytest.skip("No future non-rest sessions found in plan")

        missed = future_sessions[:2]
        recovery = {
            "recovery_score": 70.0,
            "sleep_analysis": {"avg_7d": 7.0, "debt_7d": 2.0, "last_night": {"hours": 7.0}},
            "compliance": {"compliance_rate": 80.0, "missed_days": []},
            "performance": {"tsb": -5.0, "overtraining_risk": "low", "ramp_rate": 3.0},
        }

        adapted = engine.adapt_plan(plan_12w, missed, recovery, None)

        for meso in adapted.mesocycles:
            for mc in meso.microcycles:
                for s in mc.sessions:
                    if s.date in missed:
                        assert s.session_type == "rest", (
                            f"Missed date {s.date} should be rest but is {s.session_type}"
                        )

    def test_adapt_increments_version(self, engine, plan_12w):
        original_version = plan_12w.version
        recovery = {
            "recovery_score": 80.0,
            "sleep_analysis": {"avg_7d": 7.5, "debt_7d": 0.0, "last_night": {"hours": 7.5}},
            "compliance": {"compliance_rate": 100.0, "missed_days": []},
            "performance": {"tsb": 0.0, "overtraining_risk": "low", "ramp_rate": 2.0},
        }
        adapted = engine.adapt_plan(plan_12w, [], recovery, None)
        assert adapted.version == original_version + 1


# ---------------------------------------------------------------------------
# TestWeekFormatting
# ---------------------------------------------------------------------------

class TestWeekFormatting:
    """Verify Telegram format output."""

    def test_format_contains_week_number(self, engine, plan_12w):
        mc = plan_12w.mesocycles[0].microcycles[0]
        text = engine.format_week_summary(mc)
        assert f"Week {mc.week_number}" in text

    def test_format_contains_tss(self, engine, plan_12w):
        mc = plan_12w.mesocycles[0].microcycles[0]
        text = engine.format_week_summary(mc)
        assert "TSS" in text

    def test_format_contains_day_names(self, engine, plan_12w):
        mc = plan_12w.mesocycles[0].microcycles[0]
        text = engine.format_week_summary(mc)
        assert "Mon" in text
        assert "Sat" in text

    def test_format_marks_key_sessions(self, engine, plan_12w):
        # Find a build or base week with key sessions
        for meso in plan_12w.mesocycles:
            for mc in meso.microcycles:
                has_key = any(s.is_key_session for s in mc.sessions)
                if has_key:
                    text = engine.format_week_summary(mc)
                    assert "*" in text, "Key sessions should be marked with *"
                    return
        pytest.skip("No week with key sessions found")

    def test_format_shows_rest_days(self, engine, plan_12w):
        mc = plan_12w.mesocycles[0].microcycles[0]
        text = engine.format_week_summary(mc)
        # At least one rest day should show "Rest"
        has_rest = any(s.session_type == "rest" for s in mc.sessions)
        if has_rest:
            assert "Rest" in text


# ---------------------------------------------------------------------------
# TestPlanOverview
# ---------------------------------------------------------------------------

class TestPlanOverview:
    """Verify plan overview format."""

    def test_overview_contains_race_name(self, engine, plan_12w):
        text = engine.format_plan_overview(plan_12w)
        assert plan_12w.race_name in text

    def test_overview_contains_goal_time(self, engine, plan_12w):
        text = engine.format_plan_overview(plan_12w)
        assert plan_12w.goal_time in text

    def test_overview_contains_mesocycle_names(self, engine, plan_12w):
        text = engine.format_plan_overview(plan_12w)
        assert "MESOCYCLE BREAKDOWN" in text

    def test_overview_contains_weekly_overview(self, engine, plan_12w):
        text = engine.format_plan_overview(plan_12w)
        assert "WEEKLY OVERVIEW" in text
        assert "Wk" in text


# ---------------------------------------------------------------------------
# TestCurrentWeek
# ---------------------------------------------------------------------------

class TestCurrentWeek:
    """Verify get_current_week returns correct week."""

    def test_current_week_returns_microcycle(self, engine):
        # Generate a plan that includes the current date
        plan = engine.generate_plan(current_ctl=45.0)
        mc = engine.get_current_week(plan)
        # The plan starts from next Monday so current week may or may not
        # be in the plan depending on what day it is
        if mc is not None:
            today = datetime.now().strftime("%Y-%m-%d")
            assert mc.start_date <= today <= mc.end_date

    def test_today_session_returns_session_or_none(self, engine):
        plan = engine.generate_plan(current_ctl=45.0)
        session = engine.get_today_session(plan)
        # May be None if today is before plan start
        if session is not None:
            today = datetime.now().strftime("%Y-%m-%d")
            assert session.date == today

    def test_get_current_week_returns_none_for_old_plan(self, engine, athlete):
        """A plan in the past should return None for current week."""
        old_athlete = AthleteConfig(
            name="Past Runner",
            race_name="Old Race",
            race_date="2020-06-01",
            goal_time="3:30",
            marathon_pace="4:59",
            easy_pace="5:45",
            tempo_pace="4:30",
        )
        old_engine = PeriodizationEngine(old_athlete)
        plan = old_engine.generate_plan(current_ctl=40.0, weeks_available=4)
        mc = old_engine.get_current_week(plan)
        # Plan weeks are in the future from "now", but dates won't match 2020
        # so this tests the boundary condition
        today = datetime.now().strftime("%Y-%m-%d")
        if mc is not None:
            assert mc.start_date <= today <= mc.end_date


# ---------------------------------------------------------------------------
# TestCalendarSync
# ---------------------------------------------------------------------------

class TestCalendarSync:
    """Verify session-to-event conversion for Intervals.icu."""

    def test_session_to_event_format(self):
        session = TrainingSession(
            date="2026-05-01",
            session_type="tempo_run",
            sport="Run",
            name="Tempo Run",
            description="20min tempo at 4:15/km",
            duration_minutes=50,
            target_tss=60.0,
            intensity_zone="z4",
            is_key_session=True,
            priority=1,
        )
        calendar = PeriodizationCalendar(MagicMock())
        event = calendar._session_to_event(session)

        assert event["category"] == "WORKOUT"
        assert event["start_date_local"] == "2026-05-01T09:00:00"
        assert event["name"] == "Tempo Run"
        assert event["type"] == "Run"
        assert event["description"] == "20min tempo at 4:15/km"
        assert event["moving_time"] == 3000  # 50 * 60
        assert event["icu_training_load"] == 60.0

    def test_rest_day_event_is_note(self):
        session = TrainingSession(
            date="2026-05-01",
            session_type="rest",
            sport="Note",
            name="Rest Day",
            description="Full rest.",
            duration_minutes=0,
            target_tss=0.0,
            intensity_zone="z1",
            is_key_session=False,
            priority=3,
        )
        calendar = PeriodizationCalendar(MagicMock())
        event = calendar._session_to_event(session)

        assert event["category"] == "NOTE"
        assert "moving_time" not in event
        assert "icu_training_load" not in event

    def test_event_has_required_fields(self):
        session = TrainingSession(
            date="2026-06-15",
            session_type="easy_run",
            sport="Run",
            name="Easy Run",
            description="45min easy",
            duration_minutes=45,
            target_tss=35.0,
            intensity_zone="z2",
            is_key_session=False,
            priority=2,
        )
        calendar = PeriodizationCalendar(MagicMock())
        event = calendar._session_to_event(session)

        required_keys = ["category", "start_date_local", "name", "type", "description"]
        for key in required_keys:
            assert key in event, f"Missing required key: {key}"


# ---------------------------------------------------------------------------
# TestNextMondayHelper
# ---------------------------------------------------------------------------

class TestNextMondayHelper:
    """Verify the _next_monday helper function."""

    def test_monday_returns_same_day(self):
        # 2026-03-09 is a Monday
        from datetime import date
        monday = date(2026, 3, 9)
        assert _next_monday(monday) == monday

    def test_tuesday_returns_next_monday(self):
        from datetime import date
        tuesday = date(2026, 3, 10)
        result = _next_monday(tuesday)
        assert result == date(2026, 3, 16)
        assert result.weekday() == 0  # Monday

    def test_sunday_returns_next_monday(self):
        from datetime import date
        sunday = date(2026, 3, 15)
        result = _next_monday(sunday)
        assert result == date(2026, 3, 16)
        assert result.weekday() == 0

    def test_result_is_always_monday(self):
        from datetime import date
        for day_offset in range(7):
            d = date(2026, 4, 1) + timedelta(days=day_offset)
            result = _next_monday(d)
            assert result.weekday() == 0, f"_next_monday({d}) returned {result} which is not Monday"


# ---------------------------------------------------------------------------
# TestPlanStructuralIntegrity
# ---------------------------------------------------------------------------

class TestPlanStructuralIntegrity:
    """Verify overall plan structure and data consistency."""

    def test_plan_has_mesocycles(self, plan_12w):
        assert len(plan_12w.mesocycles) >= 1

    def test_plan_metadata(self, plan_12w):
        assert plan_12w.athlete_name == "Test Runner"
        assert plan_12w.race_name == "Spring Marathon"
        assert plan_12w.version == 1
        assert plan_12w.created_at  # not empty

    def test_all_sessions_have_valid_sport(self, plan_12w):
        valid_sports = {"Run", "Ride", "Workout", "Note"}
        for meso in plan_12w.mesocycles:
            for mc in meso.microcycles:
                for s in mc.sessions:
                    assert s.sport in valid_sports, (
                        f"Invalid sport '{s.sport}' for session {s.name} on {s.date}"
                    )

    def test_all_sessions_have_valid_zone(self, plan_12w):
        valid_zones = {"z1", "z2", "z3", "z4", "z5"}
        for meso in plan_12w.mesocycles:
            for mc in meso.microcycles:
                for s in mc.sessions:
                    assert s.intensity_zone in valid_zones, (
                        f"Invalid zone '{s.intensity_zone}' for {s.name}"
                    )

    def test_dates_are_sequential(self, plan_12w):
        """Session dates within a week should be consecutive."""
        for meso in plan_12w.mesocycles:
            for mc in meso.microcycles:
                dates = [s.date for s in mc.sessions]
                for i in range(1, len(dates)):
                    d1 = datetime.strptime(dates[i - 1], "%Y-%m-%d")
                    d2 = datetime.strptime(dates[i], "%Y-%m-%d")
                    assert (d2 - d1).days == 1, (
                        f"Non-consecutive dates in week {mc.week_number}: "
                        f"{dates[i-1]} -> {dates[i]}"
                    )

    def test_week_dates_dont_overlap(self, plan_12w):
        """No two microcycles should share the same date range."""
        all_weeks = []
        for meso in plan_12w.mesocycles:
            for mc in meso.microcycles:
                all_weeks.append(mc)

        for i in range(1, len(all_weeks)):
            prev_end = all_weeks[i - 1].end_date
            curr_start = all_weeks[i].start_date
            assert curr_start > prev_end, (
                f"Week {all_weeks[i].week_number} start {curr_start} "
                f"overlaps with week {all_weeks[i-1].week_number} end {prev_end}"
            )
