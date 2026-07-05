"""Post-mortem logging setup (ADR-021)."""
from __future__ import annotations

import logging

from m3diff.log import setup_logging


def test_setup_logging_writes_engine_log_and_arms_faulthandler(tmp_path):
    directory = setup_logging(tmp_path / "logs")
    assert directory is not None
    logging.getLogger("m3diff.test").info("hello post-mortem")
    engine_log = (directory / "engine.log").read_text(encoding="utf-8")
    assert "hello post-mortem" in engine_log
    crash_log = (directory / "faulthandler.log").read_text(encoding="utf-8")
    assert "session start" in crash_log  # faulthandler file open and stamped


def test_setup_logging_failure_degrades_to_none(tmp_path):
    # a file where a directory must go -> cannot create the log dir
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    assert setup_logging(blocker / "logs") is None
