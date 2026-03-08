"""Add thresholds_history table to store daily computed baselines."""

import sqlite3


def up(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS thresholds_history (
            date TEXT PRIMARY KEY,
            hrv_baseline REAL,
            hrv_std REAL,
            rhr_baseline REAL,
            rhr_std REAL,
            sleep_baseline_h REAL,
            ctl_baseline REAL,
            tss_daily_avg REAL,
            computed_at TEXT DEFAULT (datetime('now')),
            data_json TEXT
        )
    """)


def down(conn: sqlite3.Connection):
    conn.execute("DROP TABLE IF EXISTS thresholds_history")
