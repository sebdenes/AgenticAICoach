"""Structured metrics collector — JSON-lines file, no external dependencies."""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

log = logging.getLogger("coach.metrics")


class MetricsCollector:
    """Lightweight metrics collector writing JSON-lines to a file.

    Each line is a JSON object with:
    - timestamp: ISO-8601
    - event: event type string
    - data: dict of key-value pairs
    """

    def __init__(self, log_dir: str | None = None, max_file_mb: int = 10):
        if log_dir is None:
            log_dir = str(Path(__file__).parent / "logs")
        self._dir = Path(log_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "metrics.jsonl"
        self._max_bytes = max_file_mb * 1024 * 1024

    def record(self, event: str, **data):
        """Record a metric event."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": event,
            "data": data,
        }
        try:
            self._rotate_if_needed()
            with open(self._path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            log.debug("Failed to write metric: %s", exc)

    @contextmanager
    def timer(self, event: str, **extra):
        """Context manager that records duration of a block.

        Usage::

            with metrics.timer("api_fetch", source="whoop"):
                data = await client.recovery()
        """
        start = time.monotonic()
        error = None
        try:
            yield
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            self.record(
                event,
                duration_ms=round(elapsed_ms, 1),
                error=error,
                **extra,
            )

    def api_call(self, source: str, endpoint: str, duration_ms: float, success: bool, records: int = 0):
        """Record an API call metric."""
        self.record(
            "api_call",
            source=source,
            endpoint=endpoint,
            duration_ms=round(duration_ms, 1),
            success=success,
            records=records,
        )

    def alert_fired(self, alert_type: str, severity: str):
        """Record when an alert fires."""
        self.record("alert_fired", alert_type=alert_type, severity=severity)

    def llm_call(self, model: str, input_tokens: int, output_tokens: int, duration_ms: float):
        """Record an LLM API call."""
        self.record(
            "llm_call",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=round(duration_ms, 1),
        )

    def checkin(self, checkin_type: str, success: bool, duration_ms: float):
        """Record a check-in execution."""
        self.record(
            "checkin",
            checkin_type=checkin_type,
            success=success,
            duration_ms=round(duration_ms, 1),
        )

    def data_freshness(self, source: str, latest_record_age_minutes: float):
        """Record how fresh the data is from a source."""
        self.record(
            "data_freshness",
            source=source,
            age_minutes=round(latest_record_age_minutes, 1),
        )

    def get_recent(self, event: str | None = None, limit: int = 100) -> list[dict]:
        """Read recent metrics from the file. Useful for dashboard display."""
        if not self._path.exists():
            return []
        entries = []
        try:
            with open(self._path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if event is None or entry.get("event") == event:
                            entries.append(entry)
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass
        return entries[-limit:]

    def _rotate_if_needed(self):
        """Rotate log file if it exceeds max size."""
        if self._path.exists() and self._path.stat().st_size > self._max_bytes:
            rotated = self._path.with_suffix(".jsonl.old")
            if rotated.exists():
                rotated.unlink()
            self._path.rename(rotated)
            log.info("Metrics file rotated")


# Module-level singleton
_collector: MetricsCollector | None = None


def get_metrics(log_dir: str | None = None) -> MetricsCollector:
    """Get the global MetricsCollector instance."""
    global _collector
    if _collector is None:
        _collector = MetricsCollector(log_dir=log_dir)
    return _collector
