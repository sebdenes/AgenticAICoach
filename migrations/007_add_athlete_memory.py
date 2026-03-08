"""Add athlete_memory table for long-term coaching memory."""


def up(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS athlete_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_type TEXT NOT NULL,
            content TEXT NOT NULL,
            embedding_id TEXT,
            importance REAL DEFAULT 0.5,
            created_at TEXT NOT NULL,
            last_accessed TEXT,
            access_count INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_memory_type
            ON athlete_memory(memory_type);
        CREATE INDEX IF NOT EXISTS idx_memory_importance
            ON athlete_memory(importance);
    """)


def down(conn):
    conn.executescript("""
        DROP INDEX IF EXISTS idx_memory_importance;
        DROP INDEX IF EXISTS idx_memory_type;
        DROP TABLE IF EXISTS athlete_memory;
    """)
