"""Shared pytest fixtures with realistic mock data for the coach platform."""

import sys
from pathlib import Path

import pytest

# Ensure project root is on sys.path so modules can be imported
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import AthleteConfig
from modules.knowledge_base import KnowledgeBase


# ---------------------------------------------------------------------------
# 14-day wellness data (Intervals.icu format)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_wellness_14d():
    """14 days of realistic Intervals.icu-format wellness data.

    HRV varies 48-65ms, RHR 40-48bpm, sleep 4-8h (18000-28800s),
    CTL slowly rising from ~42 to ~49, ATL fluctuating around 50-55.
    """
    return [
        {"id": "2025-02-22", "hrv": 55.2, "restingHR": 43, "sleepSecs": 27000, "sleepScore": 78, "ctl": 42.1, "atl": 50.3, "weight": 80.0},
        {"id": "2025-02-23", "hrv": 52.8, "restingHR": 44, "sleepSecs": 25200, "sleepScore": 72, "ctl": 42.5, "atl": 51.8, "weight": 80.1},
        {"id": "2025-02-24", "hrv": 58.1, "restingHR": 41, "sleepSecs": 28800, "sleepScore": 85, "ctl": 43.0, "atl": 49.2, "weight": 79.8},
        {"id": "2025-02-25", "hrv": 54.3, "restingHR": 43, "sleepSecs": 26400, "sleepScore": 76, "ctl": 43.4, "atl": 52.5, "weight": 80.0},
        {"id": "2025-02-26", "hrv": 49.7, "restingHR": 46, "sleepSecs": 21600, "sleepScore": 65, "ctl": 43.9, "atl": 55.0, "weight": 80.2},
        {"id": "2025-02-27", "hrv": 51.4, "restingHR": 45, "sleepSecs": 24000, "sleepScore": 70, "ctl": 44.3, "atl": 54.1, "weight": 80.1},
        {"id": "2025-02-28", "hrv": 60.5, "restingHR": 40, "sleepSecs": 28200, "sleepScore": 88, "ctl": 44.8, "atl": 48.7, "weight": 79.9},
        {"id": "2025-03-01", "hrv": 57.9, "restingHR": 42, "sleepSecs": 27600, "sleepScore": 82, "ctl": 45.2, "atl": 50.5, "weight": 80.0},
        {"id": "2025-03-02", "hrv": 53.6, "restingHR": 44, "sleepSecs": 25800, "sleepScore": 74, "ctl": 45.7, "atl": 53.3, "weight": 80.0},
        {"id": "2025-03-03", "hrv": 62.3, "restingHR": 40, "sleepSecs": 28800, "sleepScore": 90, "ctl": 46.2, "atl": 47.9, "weight": 79.7},
        {"id": "2025-03-04", "hrv": 56.8, "restingHR": 42, "sleepSecs": 27000, "sleepScore": 80, "ctl": 46.8, "atl": 51.2, "weight": 79.9},
        {"id": "2025-03-05", "hrv": 48.2, "restingHR": 47, "sleepSecs": 18000, "sleepScore": 55, "ctl": 47.3, "atl": 56.8, "weight": 80.3},
        {"id": "2025-03-06", "hrv": 50.9, "restingHR": 46, "sleepSecs": 23400, "sleepScore": 68, "ctl": 47.8, "atl": 55.4, "weight": 80.2},
        {"id": "2025-03-07", "hrv": 64.7, "restingHR": 40, "sleepSecs": 28800, "sleepScore": 92, "ctl": 48.5, "atl": 49.0, "weight": 79.8},
    ]


# ---------------------------------------------------------------------------
# 7-day activities (Intervals.icu format)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_activities_7d():
    """7 days of Intervals.icu-format activities — mix of runs, rides, strength, rest."""
    return [
        {
            "type": "Run", "start_date_local": "2025-03-01T07:00:00",
            "name": "Easy Run", "moving_time": 3600, "distance": 10000,
            "icu_training_load": 65, "icu_intensity": 70, "average_heartrate": 142,
        },
        {
            "type": "Ride", "start_date_local": "2025-03-02T08:30:00",
            "name": "Zone 2 Ride", "moving_time": 5400, "distance": 40000,
            "icu_training_load": 85, "icu_intensity": 65, "average_heartrate": 135,
        },
        {
            "type": "WeightTraining", "start_date_local": "2025-03-03T17:00:00",
            "name": "Strength Session", "moving_time": 2700, "distance": 0,
            "icu_training_load": 45, "icu_intensity": 75, "average_heartrate": 118,
        },
        # 2025-03-04 — rest day, no activity entry
        {
            "type": "Run", "start_date_local": "2025-03-05T06:30:00",
            "name": "Tempo Run", "moving_time": 3000, "distance": 8500,
            "icu_training_load": 95, "icu_intensity": 85, "average_heartrate": 158,
        },
        {
            "type": "Run", "start_date_local": "2025-03-06T07:00:00",
            "name": "Recovery Run", "moving_time": 2400, "distance": 6000,
            "icu_training_load": 35, "icu_intensity": 55, "average_heartrate": 130,
        },
        {
            "type": "Ride", "start_date_local": "2025-03-07T09:00:00",
            "name": "Long Ride", "moving_time": 10800, "distance": 80000,
            "icu_training_load": 150, "icu_intensity": 72, "average_heartrate": 140,
        },
    ]


# ---------------------------------------------------------------------------
# Athlete config
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_athlete_config():
    """AthleteConfig with realistic test values."""
    return AthleteConfig(
        name="Test Athlete",
        weight_kg=80.0,
        ftp=315,
        eftp_ride=299.0,
        eftp_run=423.0,
        rhr_baseline=42,
        hrv_baseline=57.0,
        timezone="Europe/Paris",
        sports=["cycling", "running", "strength"],
        race_name="Paris Marathon",
        race_date="2025-04-06",
        race_type="marathon",
        goal_time="3:15",
        marathon_pace="4:37",
        easy_pace="5:30",
        tempo_pace="4:15",
        hr_at_mp="160",
        protein_gkg=2.0,
        min_fat_gkg=1.0,
        sleep_target_hours=7.5,
        bedtime_target="22:30",
    )


# ---------------------------------------------------------------------------
# Knowledge base (loaded from the real YAML files)
# ---------------------------------------------------------------------------

@pytest.fixture
def knowledge_base():
    """KnowledgeBase instance loaded from the real knowledge/ YAML files."""
    kb_dir = str(Path(__file__).resolve().parent.parent / "knowledge")
    return KnowledgeBase(knowledge_dir=kb_dir)
