"""Add weather_cache table for caching Open-Meteo API responses."""

import sqlite3


def up(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weather_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            latitude REAL,
            longitude REAL,
            conditions_json TEXT NOT NULL,
            forecast_json TEXT,
            expires_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_weather_ts ON weather_cache(timestamp)")


def down(conn: sqlite3.Connection):
    conn.execute("DROP INDEX IF EXISTS idx_weather_ts")
    conn.execute("DROP TABLE IF EXISTS weather_cache")
