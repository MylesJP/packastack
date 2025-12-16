import logging

from packastack.logging_setup import _setup_cli_logging


def test_setup_cli_logging_creates_log_dir_and_file(tmp_path):
    logs_root = tmp_path
    _setup_cli_logging(logs_root)
    logs_dir = logs_root / "logs"
    assert logs_dir.exists()
    files = list(logs_dir.glob("packastack-*.log"))
    assert len(files) >= 1
    # Ensure the log file contains something once a logger emits an INFO message
    logging.getLogger().info("test message from logging setup check")
    # There should be at least one log file now with content
    assert any(f.stat().st_size > 0 for f in files)


def test_setup_cli_logging_idempotent(tmp_path):
    logs_root = tmp_path
    # Remove existing handlers for a clean test
    root_logger = logging.getLogger()
    for h in list(root_logger.handlers):
        if getattr(h, "packastack_cli", False):
            root_logger.removeHandler(h)

    _setup_cli_logging(logs_root)
    # Record current handlers
    handlers_first = [
        h
        for h in root_logger.handlers
        if getattr(h, "packastack_cli", False)
    ]
    assert len(handlers_first) == 1

    # Call again; should not add duplicate handlers
    _setup_cli_logging(logs_root)
    handlers_second = [
        h
        for h in root_logger.handlers
        if getattr(h, "packastack_cli", False)
    ]
    assert len(handlers_second) == 1
