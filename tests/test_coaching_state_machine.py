"""Tests for the coaching state machine — training phase lifecycle."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from config import AthleteConfig
from database import Database
from coaching_state_machine import CoachingStateMachine, STATES


# ── Fixtures ─────────────────────────────────────────────────

@pytest.fixture
def db():
    """Fresh in-memory database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    d = Database(db_path)
    # Run the migration manually (since runner won't pick it up in test)
    from migrations import runner
    runner.run_migrations(db_path)
    return d


@pytest.fixture
def athlete():
    return AthleteConfig(
        name="Test Athlete",
        race_name="Marathon",
        race_date="2025-06-15",
        ftp=315,
    )


@pytest.fixture
def sm(db, athlete):
    return CoachingStateMachine(db, athlete)


# ── State definitions ────────────────────────────────────────

def test_all_7_states_defined():
    assert len(STATES) == 7
    expected = {
        "healthy_loading", "recovery_week", "adaptation_needed",
        "injury_risk", "taper", "race_week", "post_race",
    }
    assert set(STATES.keys()) == expected


def test_each_state_has_required_fields():
    for name, config in STATES.items():
        assert "max_tss_pct" in config, f"{name} missing max_tss_pct"
        assert "tone" in config, f"{name} missing tone"
        assert "description" in config, f"{name} missing description"


# ── Default state ────────────────────────────────────────────

def test_default_state_is_healthy_loading(sm):
    assert sm.current_state == "healthy_loading"


def test_get_state_config_returns_dict(sm):
    config = sm.get_state_config()
    assert isinstance(config, dict)
    assert "max_tss_pct" in config
    assert "tone" in config


# ── Transitions ──────────────────────────────────────────────

def test_transition_on_critical_alerts(sm):
    """Critical alerts with very low recovery triggers adaptation or injury state."""
    critical_alerts = [{"severity": "critical", "title": "HRV crash"}]
    result = sm.evaluate(alerts=critical_alerts, recovery_score=30, race_countdown=60)
    assert result["state"] in ("adaptation_needed", "injury_risk")
    assert result["changed"] is True
    assert sm.current_state == result["state"]


def test_no_transition_without_alerts(sm):
    result = sm.evaluate(alerts=[], recovery_score=80, race_countdown=60)
    assert result["state"] == "healthy_loading"
    assert result["changed"] is False


def test_taper_transition_overrides_alerts(sm):
    """Race-driven transitions take priority."""
    critical_alerts = [{"severity": "critical", "title": "HRV crash"}]
    result = sm.evaluate(alerts=critical_alerts, recovery_score=30, race_countdown=12)
    assert result["state"] == "taper"
    assert result["changed"] is True


def test_race_week_transition(sm):
    result = sm.evaluate(alerts=[], recovery_score=70, race_countdown=5)
    assert result["state"] == "race_week"
    assert result["changed"] is True


def test_post_race_when_negative_countdown(sm):
    result = sm.evaluate(alerts=[], recovery_score=70, race_countdown=-2)
    assert result["state"] == "post_race"


def test_adaptation_to_healthy_no_alerts(sm):
    """If in adaptation_needed and no alerts, should transition back."""
    sm.force_state("adaptation_needed", "test")
    assert sm.current_state == "adaptation_needed"
    result = sm.evaluate(alerts=[], recovery_score=75, race_countdown=60)
    assert result["state"] == "healthy_loading"
    assert result["changed"] is True


# ── Force state ──────────────────────────────────────────────

def test_force_state(sm):
    sm.force_state("recovery_week", "Manual override")
    assert sm.current_state == "recovery_week"


def test_force_invalid_state_no_change(sm):
    """Invalid state name should not change the current state."""
    original = sm.current_state
    sm.force_state("invalid_state", "Should not change")
    assert sm.current_state == original


# ── State brief ──────────────────────────────────────────────

def test_format_state_brief(sm):
    brief = sm.format_state_brief()
    assert isinstance(brief, str)
    assert "healthy_loading" in brief.lower() or "healthy" in brief.lower()
