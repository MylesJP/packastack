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

Build outputs are organized under ``build/{package}/{build_id}/`` where
*build_id* is a timestamp string (``YYYYMMDD-HHMMSS``).  Because the
package name is not known at context-manager entry, RunContext initially
writes to a staging directory ``build/.runs/{build_id}/`` and relocates
when :meth:`relocate_to_package_dir` is called.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from packastack.core.config import load_config


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
        self.build_root = self.paths.get("build_root", Path.home() / ".cache" / "packastack" / "build")
        now_utc = datetime.datetime.now(datetime.UTC)
        self.build_id = now_utc.strftime("%Y%m%d-%H%M%S")
        self.run_id = self.build_id  # backward-compat alias
        self.run_path = self.build_root / ".runs" / self.build_id
        self.logs_path = self.run_path / "logs"
        self.stdout_file: Any | None = None
        self.stderr_file: Any | None = None
        self.events_file: Any | None = None
        self._event_files: list[Any] = []
        self._mirror_files: list[Any] = []
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        self.summary: dict[str, Any] = {"command": command, "start_utc": now_utc.isoformat()}

    def add_log_mirror(self, mirror_logs_path: Path) -> None:
        """Mirror stdout/stderr/events/summary into an additional logs directory."""
        mirror_logs_path.mkdir(parents=True, exist_ok=True)

        # Only mirror if we're inside an active RunContext.
        if self.stdout_file is None or self.stderr_file is None:
            return

        mirror_stdout = (mirror_logs_path / "stdout.log").open("a", encoding="utf-8")
        mirror_stderr = (mirror_logs_path / "stderr.log").open("a", encoding="utf-8")
        mirror_events = (mirror_logs_path / "events.jsonl").open("a", encoding="utf-8")
        self._mirror_files.extend([mirror_stdout, mirror_stderr, mirror_events])
        self._event_files.append(mirror_events)

        class _TeeTextIO:
            def __init__(self, streams: list[Any]) -> None:
                self._streams = streams

            def write(self, s: str) -> int:
                for stream in self._streams:
                    stream.write(s)
                return len(s)

            def flush(self) -> None:
                for stream in self._streams:
                    stream.flush()

            def isatty(self) -> bool:
                return False

        sys.stdout = _TeeTextIO([self.stdout_file, mirror_stdout])
        sys.stderr = _TeeTextIO([self.stderr_file, mirror_stderr])

    # ------------------------------------------------------------------
    # Relocation helpers
    # ------------------------------------------------------------------

    def _relocate(self, new_run_path: Path) -> Path:
        """Move the staging run directory to *new_run_path*.

        Closes log file handles, moves the directory tree, re-opens
        files at the new location, and re-wires ``sys.stdout`` /
        ``sys.stderr``.

        Returns:
            The new *run_path*.

        Raises:
            OSError: If the move fails.
        """
        if new_run_path == self.run_path:
            return self.run_path

        # Close current handles
        for f in (self.stdout_file, self.stderr_file):
            if f is not None:
                with contextlib.suppress(Exception):
                    f.flush()
                    f.close()
        for f in self._event_files:
            with contextlib.suppress(Exception):
                f.flush()
                f.close()

        # Move the directory
        new_run_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(self.run_path), str(new_run_path))

        # Update paths
        self.run_path = new_run_path
        self.logs_path = new_run_path / "logs"

        # Re-open log files in append mode
        self.stdout_file = (self.logs_path / "stdout.log").open("a", encoding="utf-8")
        self.stderr_file = (self.logs_path / "stderr.log").open("a", encoding="utf-8")
        self.events_file = (self.logs_path / "events.jsonl").open("a", encoding="utf-8")
        self._event_files = [self.events_file]

        # Re-wire stdout/stderr
        sys.stdout = self.stdout_file
        sys.stderr = self.stderr_file

        return new_run_path

    def relocate_to_package_dir(self, pkg_name: str) -> Path:
        """Move logs from the staging area to ``build/{pkg}/{build_id}/``.

        Args:
            pkg_name: Package name (e.g. ``aodh``, ``cinder``).

        Returns:
            New run_path.
        """
        new_path = self.build_root / pkg_name / self.build_id
        return self._relocate(new_path)

    def relocate_to_build_all_dir(self) -> Path:
        """Move logs from the staging area to ``build/.build-all/{build_id}/``.

        Returns:
            New run_path.
        """
        new_path = self.build_root / ".build-all" / self.build_id
        return self._relocate(new_path)

    # ------------------------------------------------------------------

    def __enter__(self) -> RunContext:
        # ensure directories
        self.run_path.mkdir(parents=True, exist_ok=True)
        self.logs_path.mkdir(parents=True, exist_ok=True)

        # Open log files and redirect stdout/stderr to them
        self.stdout_file = (self.logs_path / "stdout.log").open("w", encoding="utf-8")
        self.stderr_file = (self.logs_path / "stderr.log").open("w", encoding="utf-8")
        self.events_file = (self.logs_path / "events.jsonl").open("a", encoding="utf-8")
        self._event_files = [self.events_file]

        # Backwards-compatible links at run root
        def _link(src: Path, dst: Path) -> None:
            with contextlib.suppress(FileNotFoundError):
                dst.unlink()
            with contextlib.suppress(Exception):
                dst.symlink_to(src)

        _link(self.logs_path / "stdout.log", self.run_path / "stdout.log")
        _link(self.logs_path / "stderr.log", self.run_path / "stderr.log")
        _link(self.logs_path / "events.jsonl", self.run_path / "events.jsonl")

        sys.stdout = self.stdout_file
        sys.stderr = self.stderr_file

        # Write initial event
        self.log_event({"event": "run.start", "run_id": self.run_id})
        return self

    def log_event(self, event: dict[str, Any]) -> None:
        """Write a JSONL event with a timestamp."""
        if not self._event_files:  # pragma: no cover
            return
        payload = {"timestamp": datetime.datetime.now(datetime.UTC).isoformat(), **event}
        line = json.dumps(payload, default=str) + "\n"
        for f in list(self._event_files):
            f.write(line)
            f.flush()

    def write_summary(self, **kwargs: Any) -> None:
        self.summary.update(kwargs)
        blob = json.dumps(self.summary, indent=2)
        (self.run_path / "summary.json").write_text(blob)
        # Convenience copy alongside logs
        (self.logs_path / "summary.json").write_text(blob)
        for f in self._mirror_files:
            # mirror_files are file handles; derive their directory to place summary
            try:
                mirror_dir = Path(f.name).parent
                (mirror_dir / "summary.json").write_text(blob)
            except Exception:
                continue

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

        self.summary["end_utc"] = datetime.datetime.now(datetime.UTC).isoformat()
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
            for f in self._event_files:
                with contextlib.suppress(Exception):
                    f.close()
            for f in self._mirror_files:
                with contextlib.suppress(Exception):
                    f.close()
        finally:
            sys.stdout = self._orig_stdout
            sys.stderr = self._orig_stderr

        # Print report path only on failure so users can inspect logs.
        if status != "success":
            with contextlib.suppress(Exception):
                print(f"[report] Logs: {self.run_path}", file=sys.__stdout__)

        # Clean up empty staging directory if it was relocated
        staging_dir = self.build_root / ".runs" / self.build_id
        if staging_dir.exists() and staging_dir != self.run_path:
            with contextlib.suppress(Exception):
                staging_dir.rmdir()  # Only removes if empty

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
