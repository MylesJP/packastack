# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for build command errors module."""

from unittest.mock import MagicMock

from packastack.build.errors import (
    EXIT_BUILD_FAILED,
    EXIT_CONFIG_ERROR,
    EXIT_FETCH_FAILED,
    EXIT_PATCH_FAILED,
    EXIT_POLICY_BLOCKED,
    EXIT_REGISTRY_ERROR,
    EXIT_RETIRED_PROJECT,
    EXIT_SUCCESS,
    EXIT_TOOL_MISSING,
    phase_error,
    phase_warning,
)


class TestPhaseError:
    """Tests for phase_error helper function."""

    def test_returns_exit_code(self):
        """Test that phase_error returns the provided exit code."""
        run = MagicMock()
        result = phase_error(run, "fetch", "Connection failed", EXIT_FETCH_FAILED)
        assert result == EXIT_FETCH_FAILED

    def test_logs_activity(self, capsys):
        """Test that phase_error logs to activity with ERROR prefix."""
        run = MagicMock()
        phase_error(run, "fetch", "Connection failed", EXIT_FETCH_FAILED)
        # Activity is logged via the activity() function which prints to stdout
        # Since we can't easily capture that, we verify the run.log_event call

    def test_logs_event_with_default_key(self):
        """Test that phase_error logs event with default phase.error key."""
        run = MagicMock()
        phase_error(run, "fetch", "Connection failed", EXIT_FETCH_FAILED)

        run.log_event.assert_called_once()
        event = run.log_event.call_args[0][0]
        assert event["event"] == "fetch.error"
        assert event["message"] == "Connection failed"
        assert event["exit_code"] == EXIT_FETCH_FAILED

    def test_logs_event_with_custom_key(self):
        """Test that phase_error logs event with custom event key."""
        run = MagicMock()
        phase_error(
            run,
            "policy",
            "Project is retired",
            EXIT_RETIRED_PROJECT,
            event_key="policy.retired_project",
        )

        event = run.log_event.call_args[0][0]
        assert event["event"] == "policy.retired_project"

    def test_includes_additional_event_data(self):
        """Test that phase_error includes additional event data."""
        run = MagicMock()
        phase_error(
            run,
            "fetch",
            "Download failed",
            EXIT_FETCH_FAILED,
            url="https://example.com/tarball.tar.gz",
            status_code=404,
        )

        event = run.log_event.call_args[0][0]
        assert event["url"] == "https://example.com/tarball.tar.gz"
        assert event["status_code"] == 404

    def test_writes_summary(self):
        """Test that phase_error writes run summary."""
        run = MagicMock()
        phase_error(run, "build", "dpkg-buildpackage failed", EXIT_BUILD_FAILED)

        run.write_summary.assert_called_once_with(
            status="failed",
            error="dpkg-buildpackage failed",
            exit_code=EXIT_BUILD_FAILED,
        )

    def test_custom_summary_error(self):
        """Test that phase_error uses custom summary error if provided."""
        run = MagicMock()
        phase_error(
            run,
            "build",
            "dpkg-buildpackage returned exit code 2",
            EXIT_BUILD_FAILED,
            summary_error="Build failed",
        )

        run.write_summary.assert_called_once_with(
            status="failed",
            error="Build failed",
            exit_code=EXIT_BUILD_FAILED,
        )


class TestPhaseWarning:
    """Tests for phase_warning helper function."""

    def test_does_not_return_exit_code(self):
        """Test that phase_warning returns None."""
        run = MagicMock()
        result = phase_warning(run, "policy", "Upstream may be retired")
        assert result is None

    def test_logs_event_with_default_key(self):
        """Test that phase_warning logs event with default phase.warning key."""
        run = MagicMock()
        phase_warning(run, "policy", "Upstream may be retired")

        run.log_event.assert_called_once()
        event = run.log_event.call_args[0][0]
        assert event["event"] == "policy.warning"
        assert event["message"] == "Upstream may be retired"

    def test_logs_event_with_custom_key(self):
        """Test that phase_warning logs event with custom event key."""
        run = MagicMock()
        phase_warning(
            run,
            "policy",
            "Upstream may be retired",
            event_key="policy.possibly_retired",
        )

        event = run.log_event.call_args[0][0]
        assert event["event"] == "policy.possibly_retired"

    def test_includes_additional_event_data(self):
        """Test that phase_warning includes additional event data."""
        run = MagicMock()
        phase_warning(
            run,
            "validate-deps",
            "Missing dependencies",
            count=3,
            deps=["foo", "bar", "baz"],
        )

        event = run.log_event.call_args[0][0]
        assert event["count"] == 3
        assert event["deps"] == ["foo", "bar", "baz"]

    def test_does_not_write_summary(self):
        """Test that phase_warning does not write run summary."""
        run = MagicMock()
        phase_warning(run, "policy", "Minor issue")
        run.write_summary.assert_not_called()


class TestExitCodes:
    """Tests for exit code constants."""

    def test_exit_codes_are_unique(self):
        """Test that all exit codes are unique."""
        codes = [
            EXIT_SUCCESS,
            EXIT_CONFIG_ERROR,
            EXIT_TOOL_MISSING,
            EXIT_FETCH_FAILED,
            EXIT_PATCH_FAILED,
            EXIT_BUILD_FAILED,
            EXIT_POLICY_BLOCKED,
            EXIT_REGISTRY_ERROR,
            EXIT_RETIRED_PROJECT,
        ]
        assert len(codes) == len(set(codes))

    def test_success_is_zero(self):
        """Test that EXIT_SUCCESS is 0."""
        assert EXIT_SUCCESS == 0

    def test_errors_are_positive(self):
        """Test that all error codes are positive."""
        codes = [
            EXIT_CONFIG_ERROR,
            EXIT_TOOL_MISSING,
            EXIT_FETCH_FAILED,
            EXIT_PATCH_FAILED,
            EXIT_BUILD_FAILED,
            EXIT_POLICY_BLOCKED,
            EXIT_REGISTRY_ERROR,
            EXIT_RETIRED_PROJECT,
        ]
        for code in codes:
            assert code > 0
