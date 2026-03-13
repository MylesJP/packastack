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

"""Tests for packastack.core.run module."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest import mock

from packastack.core import run


class TestRunContext:
    """Tests for RunContext class."""

    def test_creates_run_directory(self, temp_home: Path, mock_config: Path) -> None:
        with run.RunContext("test") as ctx:
            assert ctx.run_path.exists()
            assert ctx.run_path.is_dir()

    def test_run_id_format(self, temp_home: Path, mock_config: Path) -> None:
        with run.RunContext("mycommand") as ctx:
            # Format: YYYYMMDD-HHMMSS
            pattern = r"^\d{8}-\d{6}$"
            assert re.match(pattern, ctx.run_id), f"Run ID {ctx.run_id} doesn't match pattern"

    def test_build_id_equals_run_id(self, temp_home: Path, mock_config: Path) -> None:
        with run.RunContext("test") as ctx:
            assert ctx.build_id == ctx.run_id

    def test_staging_directory_under_build_root(self, temp_home: Path, mock_config: Path) -> None:
        with run.RunContext("test") as ctx:
            assert ".runs" in str(ctx.run_path)
            assert ctx.build_root in ctx.run_path.parents

    def test_package_directory_when_package_provided(
        self, temp_home: Path, mock_config: Path
    ) -> None:
        with run.RunContext("build", package="ceilometer") as ctx:
            assert ".runs" not in str(ctx.run_path)
            assert ctx.run_path == ctx.build_root / "ceilometer" / ctx.build_id
            assert ctx.logs_path == ctx.run_path / "logs"

    def test_creates_stdout_log(self, temp_home: Path, mock_config: Path) -> None:
        with run.RunContext("test") as ctx:
            print("test output")
            stdout_log = ctx.logs_path / "stdout.log"

        assert stdout_log.exists()
        assert "test output" in stdout_log.read_text()

    def test_creates_stderr_log(self, temp_home: Path, mock_config: Path) -> None:
        import sys

        with run.RunContext("test") as ctx:
            print("error output", file=sys.stderr)
            stderr_log = ctx.logs_path / "stderr.log"

        assert stderr_log.exists()
        assert "error output" in stderr_log.read_text()

    def test_creates_events_jsonl(self, temp_home: Path, mock_config: Path) -> None:
        with run.RunContext("test") as ctx:
            ctx.log_event({"event": "custom", "data": "value"})
            events_file = ctx.logs_path / "events.jsonl"

        assert events_file.exists()
        lines = events_file.read_text().strip().split("\n")
        # Should have at least start event, custom event, and end event
        assert len(lines) >= 3

        # Check custom event
        events = [json.loads(line) for line in lines]
        custom_events = [e for e in events if e.get("event") == "custom"]
        assert len(custom_events) == 1
        assert custom_events[0]["data"] == "value"

    def test_creates_summary_json(self, temp_home: Path, mock_config: Path) -> None:
        with run.RunContext("test") as ctx:
            ctx.write_summary(custom_key="custom_value")
            summary_file = ctx.logs_path / "summary.json"

        assert summary_file.exists()
        summary = json.loads(summary_file.read_text())
        assert summary["command"] == "test"
        assert summary["status"] == "success"
        assert summary["custom_key"] == "custom_value"
        assert "start_utc" in summary
        assert "end_utc" in summary

    def test_summary_records_failure_on_exception(
        self, temp_home: Path, mock_config: Path
    ) -> None:
        try:
            with run.RunContext("test") as ctx:
                raise ValueError("test error")
        except ValueError:
            pass

        summary_file = ctx.logs_path / "summary.json"
        summary = json.loads(summary_file.read_text())
        assert summary["status"] == "failed"
        assert "test error" in summary["error"]

    def test_restores_stdout_stderr_after_context(
        self, temp_home: Path, mock_config: Path
    ) -> None:
        import sys

        original_stdout = sys.stdout
        original_stderr = sys.stderr

        with run.RunContext("test"):
            pass

        assert sys.stdout is original_stdout
        assert sys.stderr is original_stderr

    def test_creates_logs_subdirectory(self, temp_home: Path, mock_config: Path) -> None:
        with run.RunContext("test") as ctx:
            logs_dir = ctx.logs_path

        assert logs_dir.exists()
        assert logs_dir.is_dir()


class TestRelocateToPackageDir:
    """Tests for RunContext.relocate_to_package_dir()."""

    def test_moves_logs_to_package_dir(self, temp_home: Path, mock_config: Path) -> None:
        with run.RunContext("build") as ctx:
            old_path = ctx.run_path
            print("before relocate")
            new_path = ctx.relocate_to_package_dir("aodh")
            print("after relocate")

        # Old staging dir should be gone (or empty and cleaned up)
        assert not old_path.exists() or old_path == new_path
        # New path should be under build/aodh/{build_id}
        assert "aodh" in str(new_path)
        assert ctx.build_id in str(new_path)
        assert ctx.run_path == new_path
        assert ctx.logs_path == new_path / "logs"
        # Logs should contain content from both before and after relocate
        stdout = (new_path / "logs" / "stdout.log").read_text()
        assert "before relocate" in stdout
        assert "after relocate" in stdout

    def test_updates_run_path(self, temp_home: Path, mock_config: Path) -> None:
        with run.RunContext("build") as ctx:
            ctx.relocate_to_package_dir("cinder")
            assert ctx.run_path == ctx.build_root / "cinder" / ctx.build_id

    def test_noop_when_same_path(self, temp_home: Path, mock_config: Path) -> None:
        with run.RunContext("build") as ctx:
            # Manually set run_path to what relocate would produce
            target = ctx.build_root / "nova" / ctx.build_id
            target.mkdir(parents=True, exist_ok=True)
            # relocate to same path is a no-op when paths match
            ctx.run_path = target
            result = ctx._relocate(target)
            assert result == target


class TestRelocateFailureRecovery:
    """Tests that _relocate() recovers gracefully when the move fails."""

    def test_returns_original_path_on_move_failure(
        self, temp_home: Path, mock_config: Path
    ) -> None:
        with run.RunContext("build") as ctx:
            old_path = ctx.run_path
            print("before failed relocate")

            # Make shutil.move fail by patching it
            with mock.patch("shutil.move", side_effect=OSError("disk full")):
                result = ctx.relocate_to_package_dir("aodh")

            # Should return the original path, not the target
            assert result == old_path
            assert ctx.run_path == old_path

            # File handles should still work — logging must not be broken
            print("after failed relocate")
            ctx.log_event({"event": "test", "data": "still works"})

        # Verify both messages made it to the log
        stdout = (old_path / "logs" / "stdout.log").read_text()
        assert "before failed relocate" in stdout
        assert "after failed relocate" in stdout

        events = (old_path / "logs" / "events.jsonl").read_text()
        assert "still works" in events

    def test_returns_original_path_on_reopen_failure(
        self, temp_home: Path, mock_config: Path
    ) -> None:
        with run.RunContext("build") as ctx:
            old_path = ctx.run_path
            print("before reopen failure")

            # Move succeeds but opening files at new location raises an
            # exception.  We patch Path.open so it fails only for files
            # under the target directory.
            original_move = __import__("shutil").move
            target_dir = ctx.build_root / "aodh"
            real_open = Path.open

            def failing_open(self_path: Path, *args: object, **kwargs: object) -> object:
                if str(self_path).startswith(str(target_dir)):
                    raise PermissionError("simulated permission denied")
                return real_open(self_path, *args, **kwargs)

            with mock.patch("shutil.move", side_effect=original_move):
                with mock.patch.object(Path, "open", failing_open):
                    result = ctx.relocate_to_package_dir("aodh")

            # Should have fallen back to original (re-created) path
            assert result == old_path
            assert ctx.run_path == old_path

            # File handles should still work
            print("after reopen failure")
            ctx.log_event({"event": "recovery", "ok": True})

        stdout = (old_path / "logs" / "stdout.log").read_text()
        assert "after reopen failure" in stdout

    def test_successful_relocate_then_log_event(
        self, temp_home: Path, mock_config: Path
    ) -> None:
        """Verify the normal happy path: relocate then log_event works."""
        with run.RunContext("build") as ctx:
            ctx.relocate_to_package_dir("neutron-fwaas-dashboard")
            # This is exactly the call that was failing in the bug report
            ctx.log_event(
                {
                    "event": "fetch.complete",
                    "path": "/some/path",
                    "branches": ["main"],
                    "cloned": True,
                    "updated": False,
                }
            )

        events_text = (ctx.logs_path / "events.jsonl").read_text()
        assert "fetch.complete" in events_text


class TestRelocateToBuildAllDir:
    """Tests for RunContext.relocate_to_build_all_dir()."""

    def test_moves_logs_to_build_all_dir(self, temp_home: Path, mock_config: Path) -> None:
        with run.RunContext("build-all") as ctx:
            old_path = ctx.run_path
            print("build-all output")
            new_path = ctx.relocate_to_build_all_dir()
            print("after relocate")

        assert not old_path.exists() or old_path == new_path
        assert ".build-all" in str(new_path)
        assert ctx.build_id in str(new_path)
        assert ctx.run_path == new_path
        stdout = (new_path / "logs" / "stdout.log").read_text()
        assert "build-all output" in stdout
        assert "after relocate" in stdout

    def test_updates_run_path(self, temp_home: Path, mock_config: Path) -> None:
        with run.RunContext("build-all") as ctx:
            ctx.relocate_to_build_all_dir()
            assert ctx.run_path == ctx.build_root / ".build-all" / ctx.build_id


class TestActivity:
    """Tests for activity function."""

    def test_activity_writes_to_real_stdout(
        self, temp_home: Path, mock_config: Path
    ) -> None:
        # Mock sys.__stdout__ to capture output
        with mock.patch("sys.__stdout__") as mock_stdout:
            mock_stdout.isatty.return_value = False
            run.activity("test", "doing something")

        mock_stdout.write.assert_called()

    def test_activity_format(self, temp_home: Path, mock_config: Path) -> None:
        output = []
        with mock.patch("sys.__stdout__") as mock_stdout:
            mock_stdout.write = lambda x: output.append(x)
            run.activity("init", "Creating directories")

        # print() calls write multiple times (content + newline)
        full_output = "".join(output)
        assert "[init]" in full_output
        assert "Creating directories" in full_output
