# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only
#
# Packastack is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License version 3, as published by the
# Free Software Foundation.
#
# Packastack is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranties of MERCHANTABILITY,
# SATISFACTORY QUALITY, or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# Packastack. If not, see <http://www.gnu.org/licenses/>.

"""Run context manager for Packastack CLI runs.

This module implements the run directory creation, stdout/stderr capture to
files, JSONL event logging, and summary.json generation. The spinner output
must never go into the log files; therefore spinner/console output writes to
sys.__stdout__ when available.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from packastack.config import load_config


class RunContext:
    """Context manager that creates a run directory and captures runtime logs.

    Usage:
        with RunContext("init") as run:
            run.log_event({"msg": "starting"})
            ...
    """

    def __init__(self, command: str) -> None:
        self.command = command
        self.cfg = load_config()
        self.paths = {k: Path(v).expanduser().resolve() for k, v in self.cfg.get("paths", {}).items()}
        self.runs_root = self.paths.get("runs_root", Path.home() / ".cache" / "packastack" / "runs")
        self.run_id = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ") + f"-{command}-" + uuid.uuid4().hex[:8]
        self.run_path = self.runs_root / self.run_id
        self.logs_path = self.run_path / "logs"
        self.stdout_file: Any | None = None
        self.stderr_file: Any | None = None
        self.events_file: Any | None = None
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        self.summary: dict[str, Any] = {"command": command, "start_utc": datetime.datetime.utcnow().isoformat()}

    def __enter__(self) -> RunContext:
        # ensure directories
        self.run_path.mkdir(parents=True, exist_ok=True)
        self.logs_path.mkdir(parents=True, exist_ok=True)

        # Open log files and redirect stdout/stderr to them
        self.stdout_file = (self.run_path / "stdout.log").open("w", encoding="utf-8")
        self.stderr_file = (self.run_path / "stderr.log").open("w", encoding="utf-8")
        self.events_file = (self.run_path / "events.jsonl").open("a", encoding="utf-8")

        sys.stdout = self.stdout_file
        sys.stderr = self.stderr_file

        # Write initial event
        self.log_event({"event": "run.start", "run_id": self.run_id})
        return self

    def log_event(self, event: dict[str, Any]) -> None:
        """Write a JSONL event with a timestamp."""
        if self.events_file is None:  # pragma: no cover
            return
        payload = {"timestamp": datetime.datetime.utcnow().isoformat(), **event}
        self.events_file.write(json.dumps(payload, default=str) + "\n")
        self.events_file.flush()

    def write_summary(self, **kwargs: Any) -> None:
        self.summary.update(kwargs)
        (self.run_path / "summary.json").write_text(json.dumps(self.summary, indent=2))

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> bool | None:
        status = "success"
        if exc is not None:
            status = "failed"
            self.summary["error"] = str(exc)

        self.summary["end_utc"] = datetime.datetime.utcnow().isoformat()
        self.summary["status"] = status
        self.write_summary()

        # Write a final event
        with contextlib.suppress(Exception):
            self.log_event({"event": "run.end", "status": status})

        # Restore stdout/stderr and close files
        try:
            if self.stdout_file:
                self.stdout_file.close()
            if self.stderr_file:
                self.stderr_file.close()
            if self.events_file:
                self.events_file.close()
        finally:
            sys.stdout = self._orig_stdout
            sys.stderr = self._orig_stderr

        # Print report path only on failure so users can inspect logs.
        if status != "success":
            with contextlib.suppress(Exception):
                print(f"[report] Logs: {self.run_path}", file=sys.__stdout__)

        # Do not suppress exceptions
        return None


# Lightweight helper for activity lines which should appear even if stdout is
# redirected to log files during a RunContext. These write to the real
# terminal (sys.__stdout__).

def activity(phase: str, description: str) -> None:
    with contextlib.suppress(Exception):
        print(f"[{phase}] {description}", file=sys.__stdout__, flush=True)


if __name__ == "__main__":
    with RunContext("smoke") as r:
        activity("test", "running smoke test")
        r.log_event({"msg": "hello"})
