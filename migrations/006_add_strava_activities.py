"""Add strava_activities table for persistent Strava history storage.

Enables full history backfill via /strava sync — activities are stored with
strava_id as PRIMARY KEY so re-syncing is safe (INSERT OR IGNORE).
"""

import sqlite3


def up(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS strava_activities (
            strava_id   TEXT PRIMARY KEY,
            date        TEXT NOT NULL,
            type        TEXT,
            name        TEXT,
            start_date_local TEXT,
            duration_secs    INTEGER,
            distance_m       REAL,
            avg_hr           REAL,
            max_hr           REAL,
            avg_power        REAL,
            np               REAL,
            elevation_m      REAL,
            avg_speed        REAL,
            kudos            INTEGER,
            suffer_score     REAL,
            raw_json         TEXT,
            synced_at        TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_strava_date ON strava_activities(date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_strava_type ON strava_activities(type)"
    )


def down(conn: sqlite3.Connection):
    conn.execute("DROP INDEX IF EXISTS idx_strava_type")
    conn.execute("DROP INDEX IF EXISTS idx_strava_date")
    conn.execute("DROP TABLE IF EXISTS strava_activities")
