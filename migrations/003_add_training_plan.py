"""Add training_plans and plan_sessions tables for periodization engine persistence."""

import sqlite3


def up(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS training_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version INTEGER NOT NULL DEFAULT 1,
            race_name TEXT NOT NULL,
            race_date TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            active INTEGER NOT NULL DEFAULT 1
        )
    """)

    conn.execute("""
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
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_plan_sessions_date
        ON plan_sessions(date)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_plan_sessions_plan_id
        ON plan_sessions(plan_id)
    """)


def down(conn: sqlite3.Connection):
    conn.execute("DROP INDEX IF EXISTS idx_plan_sessions_plan_id")
    conn.execute("DROP INDEX IF EXISTS idx_plan_sessions_date")
    conn.execute("DROP TABLE IF EXISTS plan_sessions")
    conn.execute("DROP TABLE IF EXISTS training_plans")
