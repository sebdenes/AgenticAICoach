"""Add provider_metadata table to track data source connectivity and freshness."""

import sqlite3


def up(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS provider_metadata (
            provider TEXT PRIMARY KEY,
            connected INTEGER DEFAULT 0,
            last_fetch_at TEXT,
            last_success_at TEXT,
            last_error TEXT,
            records_fetched INTEGER DEFAULT 0,
            avg_latency_ms REAL DEFAULT 0,
            categories TEXT DEFAULT '[]'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reasoning_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT (datetime('now')),
            chain_type TEXT,
            conclusion TEXT,
            steps_json TEXT,
            overall_confidence REAL
        )
    """)


def down(conn: sqlite3.Connection):
    conn.execute("DROP TABLE IF EXISTS provider_metadata")
    conn.execute("DROP TABLE IF EXISTS reasoning_log")
