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

"""Implementation of subset build commands (libraries and clients).

Provides functionality for building specific subsets of OpenStack packages:
- `packastack build libraries`: Build oslo libraries and other library packages
- `packastack build clients`: Build Python client packages
"""

from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from packastack.build import (
    EXIT_CONFIG_ERROR,
    EXIT_DISCOVERY_FAILED,
    EXIT_SUCCESS,
)
from packastack.commands.init import (
    _clone_or_update_project_config,
    _clone_or_update_releases,
)
from packastack.core.config import load_config
from packastack.core.paths import resolve_paths
from packastack.core.run import RunContext, activity
from packastack.planning.package_discovery import discover_packages
from packastack.planning.type_selection import DeliverableKind, infer_deliverable_kind
from packastack.upstream.releases import (
    get_current_development_series,
    load_openstack_packages,
    load_project_releases,
)

if TYPE_CHECKING:
    from packastack.core.run import RunContext as RunContextType


class SubsetType(str, Enum):  # noqa: UP042
    """Type of package subset to build."""

    LIBRARIES = "libraries"
    CLIENTS = "clients"


def _update_openstack_repos(
    paths: dict[str, Path],
    run: RunContextType,
    offline: bool = False,
) -> bool:
    """Update OpenStack metadata repositories with git pull.

    Fetches latest changes from openstack/releases and openstack/project-config
    repositories to ensure we have the most current package metadata.

    Args:
        paths: Resolved path configuration.
        run: RunContext for logging.
        offline: If True, skip network operations.

    Returns:
        True if update succeeded or skipped (offline), False on error.
    """
    if offline:
        activity("subset", "Skipping repo updates (offline mode)")
        run.log_event({"event": "subset.repos_skipped", "reason": "offline"})
        return True

    activity("subset", "Updating OpenStack metadata repositories...")

    # Update openstack/releases
    releases_path = paths.get("openstack_releases_repo")
    if releases_path:
        try:
            _clone_or_update_releases(releases_path, run, phase="subset")
            run.log_event({
                "event": "subset.releases_updated",
                "path": str(releases_path),
            })
        except Exception as e:
            activity("subset", f"Warning: Could not update openstack-releases: {e}")
            run.log_event({
                "event": "subset.releases_update_failed",
                "error": str(e),
            })
            # Continue even if update fails - we may have cached data

    # Update openstack/project-config
    project_config_path = paths.get("openstack_project_config")
    if project_config_path:
        try:
            _clone_or_update_project_config(project_config_path, run, phase="subset")
            run.log_event({
                "event": "subset.project_config_updated",
                "path": str(project_config_path),
            })
        except Exception as e:
            activity("subset", f"Warning: Could not update openstack-project-config: {e}")
            run.log_event({
                "event": "subset.project_config_update_failed",
                "error": str(e),
            })
            # Continue even if update fails - we may have cached data

    activity("subset", "Repository updates complete")
    return True


def _filter_packages_by_subset(
    packages: list[str],
    subset_type: SubsetType,
    releases_repo: Path,
    openstack_target: str,
) -> list[str]:
    """Filter packages to include only those matching the subset type.

    Args:
        packages: List of package names to filter.
        subset_type: Type of subset (libraries or clients).
        releases_repo: Path to openstack/releases repository.
        openstack_target: OpenStack series target.

    Returns:
        Filtered list of packages matching the subset type.
    """
    # Get OpenStack package mapping for the target series
    openstack_pkgs = load_openstack_packages(releases_repo, openstack_target)

    filtered: list[str] = []
    for package in packages:
        # Map source package to OpenStack project name
        deliverable = openstack_pkgs.get(package, package)

        # Load project release info to check type
        project = load_project_releases(releases_repo, openstack_target, deliverable)

        # Infer the deliverable kind
        kind, _confidence = infer_deliverable_kind(project, package, deliverable)

        # Filter based on subset type
        if subset_type == SubsetType.LIBRARIES:
            # Include LIBRARY and CLIENT_LIBRARY types
            # CLIENT_LIBRARY are Python client libraries like python-novaclient
            if kind in (DeliverableKind.LIBRARY, DeliverableKind.CLIENT_LIBRARY):
                filtered.append(package)
        elif (
            subset_type == SubsetType.CLIENTS
            and kind == DeliverableKind.CLIENT_LIBRARY
        ):
            # Include CLIENT_LIBRARY (python-*client packages)
            filtered.append(package)

    return filtered


def run_subset_build(
    subset_type: SubsetType,
    target: str,
    ubuntu_series: str,
    cloud_archive: str,
    build_type: str,
    binary: bool,
    keep_going: bool,
    max_failures: int,
    parallel: int,
    force: bool,
    offline: bool,
    dry_run: bool,
) -> int:
    """Run a subset build (libraries or clients) and return exit code.

    This function discovers all packages, filters them by subset type,
    and builds them using the build-all infrastructure.

    Args:
        subset_type: Type of subset to build (libraries or clients).
        target: OpenStack series target (e.g., "devel", "caracal").
        ubuntu_series: Ubuntu series target (e.g., "noble").
        cloud_archive: Cloud archive pocket (e.g., "caracal").
        build_type: Build type: auto, release, snapshot.
        binary: Whether to build binary packages.
        keep_going: Continue on failure.
        max_failures: Stop after N failures (0=unlimited).
        parallel: Number of parallel workers (0=auto).
        force: Proceed despite warnings.
        offline: Run in offline mode.
        dry_run: Show plan without building.

    Returns:
        Exit code.
    """
    with RunContext(f"build-{subset_type.value}") as run:
        try:
            cfg = load_config()
            paths = resolve_paths(cfg)

            # Step 1: Update OpenStack repositories
            if not _update_openstack_repos(paths, run, offline=offline):
                activity("subset", "Failed to update repositories")
                run.write_summary(
                    status="failed",
                    error="Repository update failed",
                    exit_code=EXIT_CONFIG_ERROR,
                )
                return EXIT_CONFIG_ERROR

            # Step 2: Resolve OpenStack target
            releases_repo = paths.get("openstack_releases_repo")
            if target == "devel" and releases_repo:
                resolved_target = get_current_development_series(releases_repo) or target
            else:
                resolved_target = target

            activity("subset", f"Target: OpenStack {resolved_target}, Ubuntu {ubuntu_series}")
            run.log_event({
                "event": "subset.target_resolved",
                "openstack": resolved_target,
                "ubuntu": ubuntu_series,
            })

            # Step 3: Discover all available packages
            local_repo = paths.get("local_apt_repo")
            discovery = discover_packages(
                cache_dir=local_repo,
                releases_repo=releases_repo,
                offline=offline,
            )

            if not discovery.packages:
                activity("subset", "No packages discovered")
                run.log_event({
                    "event": "subset.no_packages",
                    "errors": discovery.errors,
                })
                run.write_summary(
                    status="failed",
                    error="No packages discovered",
                    exit_code=EXIT_DISCOVERY_FAILED,
                )
                return EXIT_DISCOVERY_FAILED

            activity("subset", f"Discovered {len(discovery.packages)} total packages")

            # Step 4: Filter packages by subset type
            filtered_packages = _filter_packages_by_subset(
                packages=discovery.packages,
                subset_type=subset_type,
                releases_repo=releases_repo,
                openstack_target=resolved_target,
            )

            if not filtered_packages:
                activity("subset", f"No {subset_type.value} found in discovered packages")
                run.log_event({
                    "event": "subset.no_matching_packages",
                    "subset_type": subset_type.value,
                    "total_packages": len(discovery.packages),
                })
                run.write_summary(
                    status="success",
                    packages_found=0,
                    subset_type=subset_type.value,
                    exit_code=EXIT_SUCCESS,
                )
                return EXIT_SUCCESS

            activity(
                "subset",
                f"Found {len(filtered_packages)} {subset_type.value} to build: "
                f"{', '.join(sorted(filtered_packages)[:10])}"
                f"{'...' if len(filtered_packages) > 10 else ''}"
            )
            run.log_event({
                "event": "subset.packages_filtered",
                "subset_type": subset_type.value,
                "count": len(filtered_packages),
                "packages": sorted(filtered_packages),
            })

            if dry_run:
                activity("subset", "Dry run - would build the following packages:")
                for i, pkg in enumerate(sorted(filtered_packages), 1):
                    activity("subset", f"  {i}. {pkg}")
                run.write_summary(
                    status="success",
                    dry_run=True,
                    packages=sorted(filtered_packages),
                    subset_type=subset_type.value,
                    exit_code=EXIT_SUCCESS,
                )
                return EXIT_SUCCESS

            # Step 5: Create packages file for build-all
            # We use build-all mode with a packages file to leverage existing infrastructure
            import tempfile

            from packastack.commands.build import run_build_all

            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".txt",
                delete=False,
            ) as f:
                for pkg in sorted(filtered_packages):
                    f.write(f"{pkg}\n")
                packages_file = f.name

            try:
                exit_code = run_build_all(
                    target=resolved_target,
                    ubuntu_series=ubuntu_series,
                    cloud_archive=cloud_archive,
                    build_type=build_type,
                    binary=binary,
                    keep_going=keep_going,
                    max_failures=max_failures,
                    resume=False,
                    resume_run_id="",
                    retry_failed=False,
                    skip_failed=True,
                    parallel=parallel,
                    packages_file=packages_file,
                    force=force,
                    offline=offline,
                    dry_run=dry_run,
                )
            finally:
                # Clean up temp file
                Path(packages_file).unlink(missing_ok=True)

            return exit_code

        except Exception as e:
            import traceback

            activity("subset", f"Subset build failed: {e}")
            for line in traceback.format_exc().splitlines():
                activity("subset", f"  {line}")
            run.log_event({
                "event": "subset.exception",
                "error": str(e),
                "traceback": traceback.format_exc(),
            })
            return EXIT_CONFIG_ERROR


def build_libraries(
    target: str = "devel",
    ubuntu_series: str = "devel",
    cloud_archive: str = "",
    build_type: str = "release",
    binary: bool = True,
    keep_going: bool = True,
    max_failures: int = 0,
    parallel: int = 0,
    force: bool = False,
    offline: bool = False,
    dry_run: bool = False,
) -> None:
    """Build all Oslo and other OpenStack library packages.

    Discovers and builds all packages that are classified as libraries
    (DeliverableKind.LIBRARY) or client libraries (DeliverableKind.CLIENT_LIBRARY)
    in the OpenStack releases metadata.

    Before building, updates the openstack/releases and openstack/project-config
    repositories to ensure we have the latest package metadata.

    Args:
        target: OpenStack series target (e.g., "devel", "caracal").
        ubuntu_series: Ubuntu series target (e.g., "noble").
        cloud_archive: Cloud archive pocket (e.g., "caracal").
        build_type: Build type: auto, release, snapshot.
        binary: Whether to build binary packages.
        keep_going: Continue on failure.
        max_failures: Stop after N failures (0=unlimited).
        parallel: Number of parallel workers (0=auto).
        force: Proceed despite warnings.
        offline: Run in offline mode (skip repo updates).
        dry_run: Show plan without building.
    """
    exit_code = run_subset_build(
        subset_type=SubsetType.LIBRARIES,
        target=target,
        ubuntu_series=ubuntu_series,
        cloud_archive=cloud_archive,
        build_type=build_type,
        binary=binary,
        keep_going=keep_going,
        max_failures=max_failures,
        parallel=parallel,
        force=force,
        offline=offline,
        dry_run=dry_run,
    )
    sys.exit(exit_code)


def build_clients(
    target: str = "devel",
    ubuntu_series: str = "devel",
    cloud_archive: str = "",
    build_type: str = "release",
    binary: bool = True,
    keep_going: bool = True,
    max_failures: int = 0,
    parallel: int = 0,
    force: bool = False,
    offline: bool = False,
    dry_run: bool = False,
) -> None:
    """Build all Python client packages.

    Discovers and builds all packages that are classified as client libraries
    (DeliverableKind.CLIENT_LIBRARY) in the OpenStack releases metadata.
    These are packages like python-novaclient, python-neutronclient, etc.

    Before building, updates the openstack/releases and openstack/project-config
    repositories to ensure we have the latest package metadata.

    Args:
        target: OpenStack series target (e.g., "devel", "caracal").
        ubuntu_series: Ubuntu series target (e.g., "noble").
        cloud_archive: Cloud archive pocket (e.g., "caracal").
        build_type: Build type: auto, release, snapshot.
        binary: Whether to build binary packages.
        keep_going: Continue on failure.
        max_failures: Stop after N failures (0=unlimited).
        parallel: Number of parallel workers (0=auto).
        force: Proceed despite warnings.
        offline: Run in offline mode (skip repo updates).
        dry_run: Show plan without building.
    """
    exit_code = run_subset_build(
        subset_type=SubsetType.CLIENTS,
        target=target,
        ubuntu_series=ubuntu_series,
        cloud_archive=cloud_archive,
        build_type=build_type,
        binary=binary,
        keep_going=keep_going,
        max_failures=max_failures,
        parallel=parallel,
        force=force,
        offline=offline,
        dry_run=dry_run,
    )
    sys.exit(exit_code)
