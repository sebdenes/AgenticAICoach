"""Add model_metadata and prediction_log tables for ML model tracking."""

import sqlite3


def up(conn: sqlite3.Connection):
    conn.execute("""
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
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prediction_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            model_type TEXT,
            input_json TEXT,
            prediction_json TEXT,
            actual_value REAL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_model_type ON model_metadata(model_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pred_ts ON prediction_log(timestamp)"
    )


def down(conn: sqlite3.Connection):
    conn.execute("DROP INDEX IF EXISTS idx_pred_ts")
    conn.execute("DROP INDEX IF EXISTS idx_model_type")
    conn.execute("DROP TABLE IF EXISTS prediction_log")
    conn.execute("DROP TABLE IF EXISTS model_metadata")
