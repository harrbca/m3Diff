"""File logging for post-mortem debugging (ADR-021).

The GUI's engine runs headless — when something fails in the field, the only
evidence is what we wrote down. ``setup_logging`` gives the ``m3diff.*``
loggers a rotating file under ``%APPDATA%/m3diff/logs/`` and arms
``faulthandler`` with its own file so even hard crashes (access violations)
leave a traceback. Logging must never break the engine: any setup failure
degrades to no-op.

Level via ``M3DIFF_LOG_LEVEL`` (default INFO; set DEBUG when chasing a bug).
"""
from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import os
import sys
from pathlib import Path


def default_log_dir() -> Path:
    root = os.environ.get("APPDATA")
    base = Path(root) / "m3diff" if root else Path.home() / ".m3diff"
    return base / "logs"


def setup_logging(log_dir: str | os.PathLike[str] | None = None) -> Path | None:
    """Attach a rotating file handler to the ``m3diff`` logger tree and enable
    faulthandler crash dumps. Returns the log directory, or None on failure."""
    try:
        directory = Path(log_dir) if log_dir is not None else default_log_dir()
        directory.mkdir(parents=True, exist_ok=True)

        handler = logging.handlers.RotatingFileHandler(
            directory / "engine.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        level = os.environ.get("M3DIFF_LOG_LEVEL", "INFO").upper()
        root = logging.getLogger("m3diff")
        root.setLevel(getattr(logging, level, logging.INFO))
        root.addHandler(handler)
        root.propagate = False

        # Hard-crash forensics: faulthandler needs a file object that stays open.
        crash_file = open(directory / "faulthandler.log", "a", encoding="utf-8")
        crash_file.write(f"--- session start pid={os.getpid()} python={sys.version.split()[0]} ---\n")
        crash_file.flush()
        faulthandler.enable(file=crash_file)
        return directory
    except Exception:
        return None
