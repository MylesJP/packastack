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

"""Error handling utilities for build command phases.

This module provides helpers for consistent error handling and logging
across build phases, ensuring exit codes and log event keys remain
identical to the original implementation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from packastack.core.run import activity

if TYPE_CHECKING:
    from packastack.core.run import RunContext


def log_phase_event(
    run: RunContext,
    phase: str,
    message: str,
    event_key: str,
    **event_data: Any,
) -> None:
    """Log a phase activity message and structured event together.

    This helper centralizes the repeated logging pattern where every phase
    action logs both a human-readable activity message and a structured
    event for machine consumption.

    Args:
        run: RunContext for structured logging.
        phase: Phase name for activity logging (e.g., "fetch", "build").
        message: Human-readable message for activity output.
        event_key: Event key for structured logging (e.g., "fetch.clone").
        **event_data: Additional data to include in the log event.

    Example:
        log_phase_event(
            run, "fetch", f"Cloned to: {pkg_repo}",
            "fetch.clone",
            path=str(pkg_repo),
            branches=branches,
        )
    """
    activity(phase, message)
    event = {
        "event": event_key,
        **event_data,
    }
    run.log_event(event)


def phase_error(
    run: RunContext,
    phase: str,
    message: str,
    exit_code: int,
    *,
    event_key: str | None = None,
    summary_error: str | None = None,
    **event_data: Any,
) -> int:
    """Log a phase error and write summary, returning the exit code.

    This helper centralizes the repeated error handling pattern:
    1. Log activity message with phase prefix
    2. Log structured event with phase.error key
    3. Write run summary with failed status
    4. Return exit code

    The log event keys follow the pattern "{phase}.error" or can be
    customized via event_key parameter to preserve existing event keys.

    Args:
        run: RunContext for logging.
        phase: Phase name for activity logging.
        message: Human-readable error message.
        exit_code: Exit code to return and include in summary.
        event_key: Custom event key (default: "{phase}.error").
        summary_error: Custom error for summary (default: message).
        **event_data: Additional data to include in the log event.

    Returns:
        The exit_code parameter, for use in `return phase_error(...)`.

    Example:
        if not tarball_path:
            return phase_error(
                run, "fetch", "Failed to fetch upstream tarball",
                EXIT_FETCH_FAILED,
                tarball_url=url,
            )
    """
    # Log to UI
    activity(phase, f"ERROR: {message}")

    # Log structured event
    event = {
        "event": event_key or f"{phase}.error",
        "message": message,
        "exit_code": exit_code,
        **event_data,
    }
    run.log_event(event)

    # Write summary
    run.write_summary(
        status="failed",
        error=summary_error or message,
        exit_code=exit_code,
    )

    return exit_code


def phase_warning(
    run: RunContext,
    phase: str,
    message: str,
    *,
    event_key: str | None = None,
    **event_data: Any,
) -> None:
    """Log a phase warning without affecting exit status.

    Args:
        run: RunContext for logging.
        phase: Phase name for activity logging.
        message: Human-readable warning message.
        event_key: Custom event key (default: "{phase}.warning").
        **event_data: Additional data to include in the log event.
    """
    activity(phase, f"Warning: {message}")

    event = {
        "event": event_key or f"{phase}.warning",
        "message": message,
        **event_data,
    }
    run.log_event(event)


# Exit codes - these must match the constants in build.py
EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR = 1
EXIT_TOOL_MISSING = 2
EXIT_FETCH_FAILED = 3
EXIT_PATCH_FAILED = 4
EXIT_MISSING_PACKAGES = 5
EXIT_CYCLE_DETECTED = 6
EXIT_BUILD_FAILED = 7
EXIT_POLICY_BLOCKED = 8
EXIT_REGISTRY_ERROR = 9
EXIT_RETIRED_PROJECT = 10

# Build-all specific exit codes
EXIT_DISCOVERY_FAILED = 11
EXIT_GRAPH_ERROR = 12
EXIT_ALL_BUILD_FAILED = 13
EXIT_RESUME_ERROR = 14
