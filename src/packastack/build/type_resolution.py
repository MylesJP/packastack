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

"""Build type resolution functions.

Handles parsing CLI build type options and auto-selecting build types
based on openstack/releases data.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer

from packastack.build.errors import EXIT_CONFIG_ERROR
from packastack.core.run import activity
from packastack.planning.type_selection import (
    BuildType,
    determine_cycle_stage,
    select_build_type,
)

if TYPE_CHECKING:
    from packastack.core.run import RunContext as RunContextType

# Valid build type values for CLI
VALID_BUILD_TYPES = {"auto", "release", "snapshot", "milestone"}


def resolve_build_type_from_cli(
    build_type_str: str,
    milestone: str,
) -> tuple[str, str]:
    """Parse and validate CLI build type options.

    Args:
        build_type_str: --type value (auto, release, snapshot, milestone)
        milestone: --milestone value (e.g., "b1", "rc1")

    Returns:
        Tuple of (build_type_str, milestone_string).
        For "auto", returns ("auto", "") to indicate auto-selection needed.

    Raises:
        typer.BadParameter: If invalid build type specified.
    """
    # Milestone flag implies milestone type
    if milestone:
        return "milestone", milestone

    build_type_str = build_type_str.lower()
    if build_type_str not in VALID_BUILD_TYPES:
        raise typer.BadParameter(
            f"Invalid build type: {build_type_str}. "
            f"Must be one of: {', '.join(sorted(VALID_BUILD_TYPES))}"
        )

    return build_type_str, ""


def resolve_build_type_auto(
    releases_repo: Path | None,
    series: str,
    source_package: str,
    deliverable: str,
    offline: bool,
    run: RunContextType,
) -> tuple[BuildType, str, str]:
    """Auto-select build type based on openstack/releases data.

    Args:
        releases_repo: Path to openstack/releases repository.
        series: OpenStack series name.
        source_package: Ubuntu source package name.
        deliverable: OpenStack project/deliverable name.
        offline: Whether running in offline mode.
        run: RunContext for logging.

    Returns:
        Tuple of (BuildType, milestone_string, reason).

    Raises:
        typer.Exit: If releases repo is missing in offline mode.
    """
    # Check releases repo availability
    if not releases_repo or not releases_repo.exists():
        if offline:
            activity("resolve", "ERROR: Auto type selection requires openstack/releases repo")
            activity("resolve", "In offline mode, the releases repo must be pre-cached")
            run.log_event({
                "event": "resolve.auto_type_failed",
                "reason": "releases_repo_missing_offline",
            })
            raise typer.Exit(EXIT_CONFIG_ERROR)
        else:
            # Would fetch here in online mode, but for now just use snapshot
            activity("resolve", "WARNING: openstack/releases repo not found, defaulting to snapshot")
            return BuildType.SNAPSHOT, "", "releases_repo_unavailable"

    # Get cycle stage
    cycle_stage = determine_cycle_stage(releases_repo, series)

    # Run auto-selection
    result = select_build_type(
        releases_repo=releases_repo,
        series=series,
        source_package=source_package,
        deliverable=deliverable,
        cycle_stage=cycle_stage,
    )

    run.log_event({
        "event": "resolve.auto_type_selected",
        "chosen_type": result.chosen_type.value,
        "reason_code": result.reason_code.value,
        "reason": result.reason_human,
        "deliverable": deliverable,
        "cycle_stage": cycle_stage.value,
    })

    # Reject snapshot builds for client/library packages
    if result.chosen_type == BuildType.SNAPSHOT:
        import sys

        from packastack.upstream.releases import load_project_releases

        proj_info = load_project_releases(releases_repo, series, source_package)
        if proj_info and proj_info.type in ("client-library", "library"):
            error_msg = (
                f"Package {source_package} is a {proj_info.type} and cannot be built as a snapshot. "
                "Client/library packages must use official release tarballs. "
                "Please wait for an official release or use --force to override."
            )
            activity("resolve", f"{error_msg}")
            run.log_event({
                "event": "resolve.snapshot_rejected_for_library",
                "package": source_package,
                "type": proj_info.type,
            })
            sys.exit(EXIT_CONFIG_ERROR)

    activity("resolve", f"Auto-selected: {result.chosen_type.value} ({result.reason_human})")

    return result.chosen_type, "", result.reason_code.value


def build_type_from_string(build_type_str: str) -> BuildType:
    """Convert string to BuildType enum."""
    return BuildType(build_type_str)
