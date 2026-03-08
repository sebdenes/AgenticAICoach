"""Centralized configuration management."""

import json
from pathlib import Path
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).parent
TZ = ZoneInfo("Europe/Paris")


@dataclass
class AthleteConfig:
    name: str = ""
    weight_kg: float = 80.0
    ftp: int = 315
    eftp_ride: float = 299.0
    eftp_run: float = 423.0
    rhr_baseline: int = 42
    hrv_baseline: float = 57.0
    timezone: str = "Europe/Paris"
    sports: list = field(default_factory=lambda: ["cycling", "running", "strength"])
    # Race
    race_name: str = ""
    race_date: str = ""
    race_type: str = ""
    goal_time: str = ""
    marathon_pace: str = ""
    easy_pace: str = ""
    tempo_pace: str = ""
    hr_at_mp: str = ""
    # Nutrition
    protein_gkg: float = 2.0
    min_fat_gkg: float = 1.0
    # Sleep
    sleep_target_hours: float = 7.5
    bedtime_target: str = "22:30"
    # Location
    latitude: float = 0.0
    longitude: float = 0.0


@dataclass
class AppConfig:
    intervals_api_key: str = ""
    intervals_athlete_id: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    anthropic_api_key: str = ""
    whoop_client_id: str = ""
    whoop_client_secret: str = ""
    whoop_redirect_uri: str = "http://localhost:8765/whoop/callback"
    db_path: str = str(BASE_DIR / "coach.db")
    log_dir: str = str(BASE_DIR / "logs")


def load_env() -> dict:
    """Load key=value pairs from config.env."""
    env = {}
    env_path = BASE_DIR / "config.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"')
    return env


def load_app_config() -> AppConfig:
    env = load_env()
    return AppConfig(
        intervals_api_key=env.get("INTERVALS_API_KEY", ""),
        intervals_athlete_id=env.get("INTERVALS_ATHLETE_ID", ""),
        telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=env.get("TELEGRAM_CHAT_ID", ""),
        anthropic_api_key=env.get("ANTHROPIC_API_KEY", ""),
        whoop_client_id=env.get("WHOOP_CLIENT_ID", ""),
        whoop_client_secret=env.get("WHOOP_CLIENT_SECRET", ""),
    )


def load_athlete_config() -> AthleteConfig:
    path = BASE_DIR / "athlete_profile.json"
    if not path.exists():
        return AthleteConfig()
    data = json.loads(path.read_text())
    race = data.get("target_race", {})
    nutr = data.get("nutrition", {})
    sleep = data.get("sleep", {})
    loc = data.get("location", {})
    return AthleteConfig(
        name=data.get("name", ""),
        weight_kg=data.get("weight_kg", 80),
        ftp=data.get("ftp", 0),
        eftp_ride=data.get("eftp_ride", 0),
        eftp_run=data.get("eftp_run", 0),
        rhr_baseline=data.get("rhr_baseline", 0),
        hrv_baseline=data.get("hrv_baseline", 0),
        timezone=data.get("timezone", "Europe/Paris"),
        sports=data.get("sports", []),
        race_name=race.get("name", ""),
        race_date=race.get("date", ""),
        race_type=race.get("type", ""),
        goal_time=race.get("goal_time", ""),
        marathon_pace=race.get("marathon_pace_km", ""),
        easy_pace=race.get("easy_pace_km", ""),
        tempo_pace=race.get("tempo_pace_km", ""),
        hr_at_mp=race.get("hr_at_mp", ""),
        protein_gkg=nutr.get("protein_target_gkg", 2.0),
        min_fat_gkg=nutr.get("min_fat_gkg", 1.0),
        sleep_target_hours=sleep.get("target_hours", 7.5),
        bedtime_target=sleep.get("bedtime_target", "22:30"),
        latitude=loc.get("latitude", 0.0),
        longitude=loc.get("longitude", 0.0),
    )


def load_coaching_prompt() -> str:
    path = BASE_DIR / "prompts" / "coaching_system.md"
    return path.read_text() if path.exists() else ""


def load_checkin_prompt(checkin_type: str) -> str:
    path = BASE_DIR / "prompts" / f"{checkin_type}_checkin.md"
    return path.read_text() if path.exists() else ""
