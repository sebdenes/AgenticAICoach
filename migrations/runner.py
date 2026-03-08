"""Lightweight migration runner for SQLite schema evolution."""

from __future__ import annotations

import importlib
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger("coach.migrations")


class MigrationRunner:
    """Run sequential Python migrations against an SQLite database.

    Each migration file lives in the ``migrations/`` directory with the name
    pattern ``NNN_description.py`` (e.g. ``001_add_thresholds_history.py``).

    Every migration module must define:
    - ``up(conn: sqlite3.Connection)`` — apply the migration
    - ``down(conn: sqlite3.Connection)`` — revert the migration

    Applied migrations are tracked in a ``schema_version`` table.
    """

    def __init__(self, db_path: str, migrations_dir: str | None = None):
        self.db_path = db_path
        if migrations_dir is None:
            migrations_dir = str(Path(__file__).parent)
        self.migrations_dir = Path(migrations_dir)
        self._ensure_version_table()

    def _ensure_version_table(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def _get_applied(self) -> set[str]:
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("SELECT version FROM schema_version").fetchall()
            return {row[0] for row in rows}
        finally:
            conn.close()

    def _discover_migrations(self) -> list[tuple[str, Path]]:
        """Find all migration files, sorted by name."""
        migrations = []
        for path in sorted(self.migrations_dir.glob("[0-9]*.py")):
            version = path.stem  # e.g. "001_add_thresholds_history"
            migrations.append((version, path))
        return migrations

    def pending(self) -> list[str]:
        """Return list of migration versions that have not been applied."""
        applied = self._get_applied()
        return [
            version for version, _ in self._discover_migrations()
            if version not in applied
        ]

    def migrate(self) -> list[str]:
        """Apply all pending migrations in order.

        Returns list of applied migration versions.
        """
        applied = self._get_applied()
        migrations = self._discover_migrations()
        newly_applied = []

        for version, path in migrations:
            if version in applied:
                continue
            log.info("Applying migration: %s", version)
            try:
                module = self._load_module(version, path)
                conn = sqlite3.connect(self.db_path)
                try:
                    module.up(conn)
                    conn.execute(
                        "INSERT INTO schema_version (version) VALUES (?)",
                        (version,),
                    )
                    conn.commit()
                    newly_applied.append(version)
                    log.info("Migration applied: %s", version)
                except Exception as exc:
                    conn.rollback()
                    log.error("Migration failed: %s — %s", version, exc)
                    raise
                finally:
                    conn.close()
            except Exception as exc:
                log.error("Failed to apply migration %s: %s", version, exc)
                raise

        if not newly_applied:
            log.info("Database is up to date — no pending migrations")
        return newly_applied

    def rollback(self, version: str) -> bool:
        """Rollback a specific migration."""
        applied = self._get_applied()
        if version not in applied:
            log.warning("Migration %s not applied, nothing to rollback", version)
            return False

        migrations = dict(self._discover_migrations())
        path = migrations.get(version)
        if not path:
            log.error("Migration file not found for %s", version)
            return False

        log.info("Rolling back migration: %s", version)
        module = self._load_module(version, path)
        conn = sqlite3.connect(self.db_path)
        try:
            module.down(conn)
            conn.execute("DELETE FROM schema_version WHERE version = ?", (version,))
            conn.commit()
            log.info("Rollback complete: %s", version)
            return True
        except Exception as exc:
            conn.rollback()
            log.error("Rollback failed: %s — %s", version, exc)
            return False
        finally:
            conn.close()

    @staticmethod
    def _load_module(version: str, path: Path):
        """Dynamically load a migration module from file path."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(f"migration_{version}", str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def run_migrations(db_path: str):
    """Convenience function — apply all pending migrations."""
    runner = MigrationRunner(db_path)
    return runner.migrate()
