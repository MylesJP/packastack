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

"""Implementation of the build-rc command.

Discovers all OpenStack packages that have a release candidate (RC) release
for the current development series, and builds them using the build-all
infrastructure.

Usage::

    packastack build-rc              # Build all RC packages for devel series
    packastack build-rc --dry-run    # Show which packages have RC releases
    packastack build-rc --ppa-upload # Build and upload to PPA
    packastack build-rc --no-ai      # Build without AI diagnosis
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

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
from packastack.planning.package_discovery import (
    discover_packages,
    filter_by_managed_packages,
)
from packastack.upstream.releases import (
    get_current_development_series,
    load_openstack_packages,
    load_project_releases,
)


def _update_openstack_repos(
    paths: dict[str, Path],
    run: RunContext,
    offline: bool = False,
) -> bool:
    """Update OpenStack metadata repositories with git pull.

    Args:
        paths: Resolved path configuration.
        run: RunContext for logging.
        offline: If True, skip network operations.

    Returns:
        True if update succeeded or skipped (offline), False on error.
    """
    if offline:
        activity("rc", "Skipping repo updates (offline mode)")
        run.log_event({"event": "rc.repos_skipped", "reason": "offline"})
        return True

    activity("rc", "Updating OpenStack metadata repositories...")

    releases_path = paths.get("openstack_releases_repo")
    if releases_path:
        try:
            _clone_or_update_releases(releases_path, run, phase="rc")
            run.log_event({
                "event": "rc.releases_updated",
                "path": str(releases_path),
            })
        except Exception as e:
            activity("rc", f"Warning: Could not update openstack-releases: {e}")
            run.log_event({
                "event": "rc.releases_update_failed",
                "error": str(e),
            })

    project_config_path = paths.get("openstack_project_config")
    if project_config_path:
        try:
            _clone_or_update_project_config(project_config_path, run, phase="rc")
            run.log_event({
                "event": "rc.project_config_updated",
                "path": str(project_config_path),
            })
        except Exception as e:
            activity("rc", f"Warning: Could not update openstack-project-config: {e}")
            run.log_event({
                "event": "rc.project_config_update_failed",
                "error": str(e),
            })

    activity("rc", "Repository updates complete")
    return True


def _filter_packages_with_rc(
    packages: list[str],
    releases_repo: Path,
    openstack_target: str,
    rc_number: int | None = None,
) -> list[tuple[str, str]]:
    """Filter packages to only those with an RC release.

    Args:
        packages: List of Ubuntu source package names.
        releases_repo: Path to openstack/releases repository.
        openstack_target: OpenStack series name.
        rc_number: If set, only match this specific RC (e.g. 1 for RC1).
            If None, match any RC version.

    Returns:
        List of (package_name, rc_version) tuples for packages that have
        an RC release, sorted by package name.
    """
    openstack_pkgs = load_openstack_packages(releases_repo, openstack_target)

    results: list[tuple[str, str]] = []
    for package in packages:
        deliverable = openstack_pkgs.get(package, package)
        project = load_project_releases(releases_repo, openstack_target, deliverable)
        if not project:
            continue

        # Find the latest RC release for this project
        best_rc: str | None = None
        for rel in project.releases:
            if not rel.is_rc():
                continue
            # Match specific RC number (e.g. "rc1" at end of version)
            if rc_number is not None and not re.search(
                rf"rc{rc_number}\b", rel.version, re.IGNORECASE
            ):
                continue
            best_rc = rel.version

        if best_rc:
            results.append((package, best_rc))

    return sorted(results, key=lambda t: t[0])


def run_build_rc(
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
    ppa_upload: bool = False,
    rc_number: int | None = None,
    build_deps: bool = False,
    archive_deps: bool = False,
) -> int:
    """Build all packages that have an RC release for the target series.

    Discovers packages from Launchpad, checks which ones have an RC release
    in openstack/releases, and builds them using the build-all infrastructure.

    Args:
        target: OpenStack series target (e.g., "devel", "gazpacho").
        ubuntu_series: Ubuntu series target (e.g., "noble").
        cloud_archive: Cloud archive pocket.
        build_type: Build type (default "release" since RCs are releases).
        binary: Whether to build binary packages.
        keep_going: Continue on failure.
        max_failures: Stop after N failures (0=unlimited).
        parallel: Number of parallel workers (0=auto).
        force: Proceed despite warnings.
        offline: Run in offline mode.
        dry_run: Show plan without building.
        ppa_upload: Upload to PPA on success.
        rc_number: Specific RC number to filter on (e.g. 1 for RC1).
        build_deps: Whether to auto-build missing dependencies.
        archive_deps: Use archive dependencies only; do not inject local repo into sbuild.

    Returns:
        Exit code.
    """
    rc_label = f"RC{rc_number}" if rc_number else "RC"

    with RunContext(f"build-{rc_label.lower()}") as run:
        try:
            cfg = load_config()
            paths = resolve_paths(cfg)

            # Step 1: Update OpenStack repositories
            if not _update_openstack_repos(paths, run, offline=offline):
                activity("rc", "Failed to update repositories")
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

            activity("rc", f"Target: OpenStack {resolved_target}, Ubuntu {ubuntu_series}")
            activity("rc", f"Looking for {rc_label} releases...")
            run.log_event({
                "event": "rc.target_resolved",
                "openstack": resolved_target,
                "ubuntu": ubuntu_series,
                "rc_number": rc_number,
            })

            # Step 3: Discover all available packages
            discovery = discover_packages(
                releases_repo=releases_repo,
                offline=offline,
            )

            if not discovery.packages:
                activity("rc", "No packages discovered")
                run.log_event({
                    "event": "rc.no_packages",
                    "errors": discovery.errors,
                })
                run.write_summary(
                    status="failed",
                    error="No packages discovered",
                    exit_code=EXIT_DISCOVERY_FAILED,
                )
                return EXIT_DISCOVERY_FAILED

            activity("rc", f"Discovered {len(discovery.packages)} total packages")

            # Step 4: Filter to packages that have an RC release
            if not releases_repo:
                activity("rc", "No openstack-releases repo configured")
                return EXIT_CONFIG_ERROR

            rc_packages = _filter_packages_with_rc(
                packages=discovery.packages,
                releases_repo=releases_repo,
                openstack_target=resolved_target,
                rc_number=rc_number,
            )

            # Step 4b: Filter by managed_packages
            from packastack.upstream.pkg_scripts import load_managed_packages

            cache_root = paths.get("cache_root")
            managed_packages = load_managed_packages(cache_root) if cache_root else []
            if managed_packages:
                rc_pkg_names = [name for name, _ver in rc_packages]
                managed_filtered, skipped = filter_by_managed_packages(
                    rc_pkg_names, managed_packages
                )
                if skipped:
                    managed_set = set(managed_filtered)
                    rc_packages = [(n, v) for n, v in rc_packages if n in managed_set]
                    activity(
                        "rc",
                        f"Filtered to managed packages: {len(rc_packages)} of {len(rc_pkg_names)}",
                    )

            if not rc_packages:
                activity("rc", f"No packages with {rc_label} releases found for {resolved_target}")
                run.log_event({
                    "event": "rc.no_rc_packages",
                    "total_discovered": len(discovery.packages),
                })
                run.write_summary(
                    status="success",
                    packages_found=0,
                    rc_label=rc_label,
                    exit_code=EXIT_SUCCESS,
                )
                return EXIT_SUCCESS

            # Display what we found
            activity("rc", f"Found {len(rc_packages)} packages with {rc_label} releases:")
            for pkg_name, rc_version in rc_packages:
                activity("rc", f"  {pkg_name} ({rc_version})")
            run.log_event({
                "event": "rc.packages_found",
                "rc_label": rc_label,
                "count": len(rc_packages),
                "packages": dict(rc_packages),
            })

            if dry_run:
                activity("rc", "Dry run - no builds will be started")
                run.write_summary(
                    status="success",
                    dry_run=True,
                    packages=dict(rc_packages),
                    rc_label=rc_label,
                    exit_code=EXIT_SUCCESS,
                )
                return EXIT_SUCCESS

            # Step 5: Build via build-all with a packages file
            from packastack.commands.build import run_build_all

            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".txt",
                delete=False,
            ) as f:
                for pkg_name, _ver in rc_packages:
                    f.write(f"{pkg_name}\n")
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
                    dry_run=False,
                    ppa_upload=ppa_upload,
                    build_deps=build_deps,
                    archive_deps=archive_deps,
                )
            finally:
                Path(packages_file).unlink(missing_ok=True)

            return exit_code

        except Exception as e:
            import traceback

            activity("rc", f"RC build failed: {e}")
            for line in traceback.format_exc().splitlines():
                activity("rc", f"  {line}")
            run.log_event({
                "event": "rc.exception",
                "error": str(e),
                "traceback": traceback.format_exc(),
            })
            return EXIT_CONFIG_ERROR
