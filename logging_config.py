"""Centralized logging configuration with log rotation."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_dir: Path, level: str = "INFO"):
    """Configure logging with console output and rotating file handler.

    Args:
        log_dir: Directory for log files (created if needed).
        level: Log level name (DEBUG, INFO, WARNING, ERROR).
    """
    log_dir.mkdir(exist_ok=True)

    log_level = getattr(logging, level.upper(), logging.INFO)

    handlers = [
        logging.StreamHandler(),
        RotatingFileHandler(
            log_dir / "coach.log",
            maxBytes=10_000_000,  # 10 MB
            backupCount=5,
        ),
    ]

    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=log_level,
        handlers=handlers,
    )
