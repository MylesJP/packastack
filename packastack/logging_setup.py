# Helper to configure standard CLI logging across the packastack project
import logging
from datetime import datetime
from pathlib import Path


def _setup_cli_logging(root: Path | None = None):
    """Configures a logfile under the given root/logs or CWD/logs.

    Uses a timestamp to create a unique file per run.
    """
    logs_root = Path(root) if root else Path.cwd()
    logs_dir = logs_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    log_file = logs_dir / f"packastack-{timestamp}.log"

    # Avoid adding duplicate FileHandlers on repeated imports
    root_logger = logging.getLogger()
    # Ensure root logger level is low enough for our handlers to receive
    # INFO and higher level messages.
    root_logger.setLevel(logging.INFO)
    for h in list(root_logger.handlers):
        if isinstance(h, logging.FileHandler) and getattr(h, "packastack_cli", False):
            root_logger.removeHandler(h)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
    )
    # Mark handler so we can remove it in future runs
    setattr(fh, "packastack_cli", True)
    root_logger.addHandler(fh)


__all__ = ["_setup_cli_logging"]
