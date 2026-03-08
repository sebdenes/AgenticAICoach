"""Migration 008: Add api_usage table for token and cost tracking."""


def up(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT,
            endpoint TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_write_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            agent TEXT,
            request_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON api_usage(timestamp);
        CREATE INDEX IF NOT EXISTS idx_usage_provider ON api_usage(provider);
    """)


def down(conn):
    conn.executescript("""
        DROP INDEX IF EXISTS idx_usage_provider;
        DROP INDEX IF EXISTS idx_usage_timestamp;
        DROP TABLE IF EXISTS api_usage;
    """)
