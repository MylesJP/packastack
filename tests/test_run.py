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

"""Tests for packastack.run module."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest import mock

from packastack import run


class TestRunContext:
    """Tests for RunContext class."""

    def test_creates_run_directory(self, temp_home: Path, mock_config: Path) -> None:
        with run.RunContext("test") as ctx:
            assert ctx.run_path.exists()
            assert ctx.run_path.is_dir()

    def test_run_id_format(self, temp_home: Path, mock_config: Path) -> None:
        with run.RunContext("mycommand") as ctx:
            # Format: YYYYMMDDTHHMMSSZ-<command>-<shortid>
            pattern = r"^\d{8}T\d{6}Z-mycommand-[a-f0-9]{8}$"
            assert re.match(pattern, ctx.run_id), f"Run ID {ctx.run_id} doesn't match pattern"

    def test_creates_stdout_log(self, temp_home: Path, mock_config: Path) -> None:
        with run.RunContext("test") as ctx:
            print("test output")
            stdout_log = ctx.run_path / "stdout.log"

        assert stdout_log.exists()
        assert "test output" in stdout_log.read_text()

    def test_creates_stderr_log(self, temp_home: Path, mock_config: Path) -> None:
        import sys

        with run.RunContext("test") as ctx:
            print("error output", file=sys.stderr)
            stderr_log = ctx.run_path / "stderr.log"

        assert stderr_log.exists()
        assert "error output" in stderr_log.read_text()

    def test_creates_events_jsonl(self, temp_home: Path, mock_config: Path) -> None:
        with run.RunContext("test") as ctx:
            ctx.log_event({"event": "custom", "data": "value"})
            events_file = ctx.run_path / "events.jsonl"

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
            summary_file = ctx.run_path / "summary.json"

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

        summary_file = ctx.run_path / "summary.json"
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
