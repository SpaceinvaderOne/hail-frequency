"""Centralized logging configuration for HailFrequency."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import get_setting

import os

_data_dir = os.environ.get("HF_DATA_DIR", "/app/config")
LOG_DIR = Path(_data_dir) / "logs"
LOG_FILE = LOG_DIR / "hailfrequency.log"
MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
BACKUP_COUNT = 3  # keep 3 rotated files (20 MB max total)
LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_stderr_logging() -> None:
    """Configure the stderr handler. Safe to call before the DB exists —
    call this FIRST in the lifespan so early log lines aren't lost."""
    root = logging.getLogger("hailfrequency")
    root.setLevel(logging.DEBUG)

    # Clear any default handlers
    root.handlers.clear()

    # Stderr handler — INFO and above
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.INFO)
    stderr_handler.setFormatter(formatter)
    root.addHandler(stderr_handler)


def restore_debug_logging() -> None:
    """Re-apply the persisted debug-file-logging flag. Needs the DB — call
    after init_db()."""
    enabled = get_setting("debug_logging") == "true"
    if enabled:
        apply_debug_logging(True)


def apply_debug_logging(enabled: bool) -> None:
    """Enable or disable debug file logging at runtime."""
    root = logging.getLogger("hailfrequency")

    # Remove any existing file handler
    for handler in root.handlers[:]:
        if isinstance(handler, RotatingFileHandler):
            handler.close()
            root.removeHandler(handler)

    if not enabled:
        root.info("Debug logging disabled")
        return

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
        root.info("Debug logging enabled — writing to %s", LOG_FILE)
    except Exception:
        root.warning("Failed to enable debug file logging", exc_info=True)
