"""SQLite database for persistent state, conversation memory, and data caching."""

from __future__ import annotations

import json
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import contextmanager

log = logging.getLogger("coach.db")


class Database:
    def __init__(self, db_path: str = "coach.db"):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)
        log.info(f"Database initialized at {self.db_path}")

    # ── Conversation Memory ───────────────────────────────

    def add_message(self, role: str, content: str, checkin_type: str = None):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO conversations (timestamp, role, content, checkin_type) VALUES (?, ?, ?, ?)",
                (datetime.now().isoformat(), role, content, checkin_type),
            )

    def get_recent_messages(self, limit: int = 30) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT role, content FROM conversations ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def get_conversation_summary(self, days: int = 7) -> str:
        """Get a summary of recent coaching topics for context."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT timestamp, role, content, checkin_type FROM conversations "
                "WHERE timestamp > ? AND (checkin_type IS NOT NULL OR role = 'user') "
                "ORDER BY id DESC LIMIT 50",
                (cutoff,),
            ).fetchall()
        if not rows:
            return "No recent conversations."
        lines = []
        for r in reversed(rows):
            prefix = f"[{r['checkin_type']}]" if r["checkin_type"] else f"[{r['role']}]"
            lines.append(f"{r['timestamp'][:16]} {prefix} {r['content'][:100]}")
        return "\n".join(lines)

    # ── Data Cache ────────────────────────────────────────

    def cache_set(self, key: str, data: str, ttl_minutes: int = 15):
        expires = (datetime.now() + timedelta(minutes=ttl_minutes)).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO data_cache (key, data, expires_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (key, data, expires, datetime.now().isoformat()),
            )

    def cache_get(self, key: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data, expires_at FROM data_cache WHERE key = ?", (key,)
            ).fetchone()
        if not row:
            return None
        if datetime.fromisoformat(row["expires_at"]) < datetime.now():
            return None  # expired
        return row["data"]

    def cache_clear(self):
        with self._conn() as conn:
            conn.execute("DELETE FROM data_cache")

    # ── Wellness History ──────────────────────────────────

    def store_wellness(self, date: str, data: dict):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO wellness_history "
                "(date, ctl, atl, rhr, hrv, sleep_seconds, sleep_score, steps, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    date,
                    data.get("ctl"),
                    data.get("atl"),
                    data.get("restingHR"),
                    data.get("hrv"),
                    data.get("sleepSecs"),
                    data.get("sleepScore"),
                    data.get("steps"),
                    json.dumps(data),
                ),
            )

    def get_wellness_history(self, days: int = 30) -> list[dict]:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM wellness_history WHERE date >= ? ORDER BY date",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Activity History ──────────────────────────────────

    def store_activity(self, activity_id: str, date: str, data: dict):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO activity_history "
                "(activity_id, date, type, name, duration_secs, distance_m, tss, intensity, "
                "avg_hr, max_hr, avg_power, np, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    activity_id,
                    date,
                    data.get("type"),
                    data.get("name"),
                    data.get("moving_time"),
                    data.get("distance"),
                    data.get("icu_training_load"),
                    data.get("icu_intensity"),
                    data.get("average_heartrate"),
                    data.get("max_heartrate"),
                    data.get("average_watts"),
                    data.get("icu_weighted_avg_watts"),
                    json.dumps(data),
                ),
            )

    def get_activity_history(self, days: int = 30) -> list[dict]:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM activity_history WHERE date >= ? ORDER BY date",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Coaching State ────────────────────────────────────

    def set_state(self, key: str, value):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO coaching_state (key, value, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), datetime.now().isoformat()),
            )

    def get_state(self, key: str, default=None):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM coaching_state WHERE key = ?", (key,)
            ).fetchone()
        if row:
            return json.loads(row["value"])
        return default

    def get_all_state(self) -> dict:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM coaching_state").fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    # ── Strength Training ─────────────────────────────────

    def log_strength_session(self, date: str, exercises: list[dict], notes: str = ""):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO strength_log (date, exercises, notes) VALUES (?, ?, ?)",
                (date, json.dumps(exercises), notes),
            )

    def get_strength_history(self, days: int = 30) -> list[dict]:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM strength_log WHERE date >= ? ORDER BY date",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Nutrition Log ─────────────────────────────────────

    def log_meal(self, date: str, meal_type: str, description: str,
                 calories: float = None, protein: float = None,
                 carbs: float = None, fat: float = None):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO nutrition_log "
                "(date, meal_type, description, calories, protein_g, carbs_g, fat_g) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (date, meal_type, description, calories, protein, carbs, fat),
            )

    def get_daily_nutrition(self, date: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM nutrition_log WHERE date = ? ORDER BY id", (date,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_nutrition_history(self, days: int = 7) -> list[dict]:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT date, SUM(calories) as total_cal, SUM(protein_g) as total_protein, "
                "SUM(carbs_g) as total_carbs, SUM(fat_g) as total_fat, COUNT(*) as meals "
                "FROM nutrition_log WHERE date >= ? GROUP BY date ORDER BY date",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Training Plans ─────────────────────────────────────

    def store_training_plan(self, plan_json: str, version: int = 1):
        """Store a training plan and deactivate previous ones."""
        with self._conn() as conn:
            conn.execute("UPDATE training_plans SET active = 0 WHERE active = 1")
            conn.execute(
                "INSERT INTO training_plans (version, race_name, race_date, plan_json, active) "
                "VALUES (?, ?, ?, ?, 1)",
                (version, "", "", plan_json),
            )

    def get_active_plan(self) -> dict | None:
        """Get the currently active training plan."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM training_plans WHERE active = 1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row:
            return dict(row)
        return None

    def update_plan_session_status(self, date: str, status: str, activity_id: str = None):
        """Update a plan session status (planned, completed, missed, adapted)."""
        with self._conn() as conn:
            if activity_id:
                conn.execute(
                    "UPDATE plan_sessions SET status = ?, actual_activity_id = ? WHERE date = ?",
                    (status, activity_id, date),
                )
            else:
                conn.execute(
                    "UPDATE plan_sessions SET status = ? WHERE date = ?",
                    (status, date),
                )

    # ── Weather Cache ──────────────────────────────────────

    def cache_weather(self, lat: float, lon: float, conditions_json: str,
                      forecast_json: str = None, ttl_minutes: int = 30):
        """Cache weather data with TTL."""
        expires = (datetime.now() + timedelta(minutes=ttl_minutes)).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO weather_cache (timestamp, latitude, longitude, conditions_json, "
                "forecast_json, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(), lat, lon, conditions_json,
                 forecast_json, expires),
            )

    def get_cached_weather(self, lat: float, lon: float) -> dict | None:
        """Get cached weather data if not expired."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT conditions_json, forecast_json, expires_at FROM weather_cache "
                "WHERE latitude = ? AND longitude = ? ORDER BY id DESC LIMIT 1",
                (lat, lon),
            ).fetchone()
        if not row:
            return None
        if datetime.fromisoformat(row["expires_at"]) < datetime.now():
            return None
        result = {"conditions_json": row["conditions_json"]}
        if row["forecast_json"]:
            result["forecast_json"] = row["forecast_json"]
        return result

    # ── Model Metadata ─────────────────────────────────────

    def store_model_metadata(self, model_type: str, version: int, score: float,
                             model_path: str = None):
        """Store ML model metadata and deactivate previous versions of the same type."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE model_metadata SET active = 0 WHERE model_type = ? AND active = 1",
                (model_type,),
            )
            conn.execute(
                "INSERT INTO model_metadata (model_type, version, trained_at, score, "
                "model_path, active) VALUES (?, ?, ?, ?, ?, 1)",
                (model_type, version, datetime.now().isoformat(), score, model_path),
            )

    def get_active_model(self, model_type: str) -> dict | None:
        """Get metadata for the active model of a given type."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM model_metadata WHERE model_type = ? AND active = 1 "
                "ORDER BY id DESC LIMIT 1",
                (model_type,),
            ).fetchone()
        if row:
            return dict(row)
        return None

    def log_prediction(self, model_type: str, input_json: str,
                       prediction_json: str, actual_value: float = None):
        """Log a prediction for future evaluation."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO prediction_log (timestamp, model_type, input_json, "
                "prediction_json, actual_value) VALUES (?, ?, ?, ?, ?)",
                (datetime.now().isoformat(), model_type, input_json,
                 prediction_json, actual_value),
            )


# ── Schema ────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    checkin_type TEXT
);

CREATE TABLE IF NOT EXISTS data_cache (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wellness_history (
    date TEXT PRIMARY KEY,
    ctl REAL,
    atl REAL,
    rhr INTEGER,
    hrv REAL,
    sleep_seconds INTEGER,
    sleep_score REAL,
    steps INTEGER,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS activity_history (
    activity_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    type TEXT,
    name TEXT,
    duration_secs INTEGER,
    distance_m REAL,
    tss REAL,
    intensity REAL,
    avg_hr REAL,
    max_hr REAL,
    avg_power REAL,
    np REAL,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS coaching_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strength_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    exercises TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS nutrition_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    meal_type TEXT,
    description TEXT,
    calories REAL,
    protein_g REAL,
    carbs_g REAL,
    fat_g REAL
);

CREATE INDEX IF NOT EXISTS idx_conversations_ts ON conversations(timestamp);
CREATE INDEX IF NOT EXISTS idx_wellness_date ON wellness_history(date);
CREATE INDEX IF NOT EXISTS idx_activity_date ON activity_history(date);
CREATE INDEX IF NOT EXISTS idx_nutrition_date ON nutrition_log(date);
CREATE INDEX IF NOT EXISTS idx_strength_date ON strength_log(date);

CREATE TABLE IF NOT EXISTS training_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER NOT NULL DEFAULT 1,
    race_name TEXT NOT NULL,
    race_date TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS plan_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    session_type TEXT NOT NULL,
    sport TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    duration_minutes INTEGER,
    target_tss REAL,
    status TEXT NOT NULL DEFAULT 'planned',
    actual_activity_id TEXT,
    adaptation_note TEXT,
    intervals_event_id TEXT,
    FOREIGN KEY (plan_id) REFERENCES training_plans(id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_plan_sessions_date ON plan_sessions(date);
CREATE INDEX IF NOT EXISTS idx_plan_sessions_plan_id ON plan_sessions(plan_id);

CREATE TABLE IF NOT EXISTS weather_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    latitude REAL,
    longitude REAL,
    conditions_json TEXT NOT NULL,
    forecast_json TEXT,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_weather_ts ON weather_cache(timestamp);

CREATE TABLE IF NOT EXISTS model_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_type TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    trained_at TEXT,
    training_samples INTEGER,
    features_json TEXT,
    score REAL,
    model_path TEXT,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS prediction_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    model_type TEXT,
    input_json TEXT,
    prediction_json TEXT,
    actual_value REAL
);

CREATE INDEX IF NOT EXISTS idx_model_type ON model_metadata(model_type);
CREATE INDEX IF NOT EXISTS idx_pred_ts ON prediction_log(timestamp);
"""
