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

"""Helper functions for build-all mode.

Contains utility functions for dependency graph building, version extraction,
and package filtering used by the build-all command.
"""

from __future__ import annotations

from pathlib import Path

from packastack.apt.packages import PackageIndex
from packastack.core.run import RunContext, activity
from packastack.debpkg.control import get_changelog_version
from packastack.debpkg.version import extract_upstream_version
from packastack.planning.build_all_state import BuildAllState, PackageStatus
from packastack.planning.graph import DependencyGraph
from packastack.upstream.retirement import RetirementChecker


def build_dependency_graph(
    packages: list[str],
    cache_dir: Path,
    pkg_index: PackageIndex,
) -> tuple[DependencyGraph, dict[str, list[str]]]:
    """Build dependency graph from debian/control files.

    This is a simplified wrapper around graph_builder.build_graph_from_control
    for use in build-all mode.

    Args:
        packages: List of source package names to include in graph.
        cache_dir: Path to directory containing packaging repos.
        pkg_index: Package index for resolving binary dependencies.

    Returns:
        Tuple of (DependencyGraph, missing_deps_dict).
    """
    from packastack.planning.graph_builder import build_graph_from_control

    result = build_graph_from_control(
        packages=packages,
        packaging_repos_path=cache_dir,
        package_index=pkg_index,
    )

    return result.graph, result.missing_deps


def build_upstream_versions_from_packaging(
    packages: list[str],
    packaging_root: Path,
) -> dict[str, str]:
    """Derive upstream versions from debian/changelog entries.

    Args:
        packages: List of source package names.
        packaging_root: Root directory containing packaging repos.

    Returns:
        Dict mapping package name to upstream version.
    """
    versions: dict[str, str] = {}
    for pkg in packages:
        changelog_path = packaging_root / pkg / "debian" / "changelog"
        if not changelog_path.exists():
            continue
        debian_version = get_changelog_version(changelog_path)
        if not debian_version:
            continue
        upstream_version = extract_upstream_version(debian_version)
        if upstream_version:
            versions[pkg] = upstream_version
    return versions


def filter_retired_packages(
    packages: list[str],
    project_config_path: Path | None,
    releases_repo: Path | None,
    openstack_target: str,
    offline: bool,
    run: RunContext,
    clone_project_config_fn: callable = None,
) -> tuple[list[str], list[str], list[str]]:
    """Filter retired packages using openstack/project-config and releases inference.

    Args:
        packages: List of package names to filter.
        project_config_path: Path to openstack/project-config clone.
        releases_repo: Path to openstack/releases clone.
        openstack_target: OpenStack series target.
        offline: Whether running in offline mode.
        run: RunContext for logging.
        clone_project_config_fn: Optional function to clone project-config.

    Returns:
        Tuple of (filtered_packages, retired_packages, possibly_retired_packages).
    """
    if not packages:
        return packages, [], []

    if project_config_path and not project_config_path.exists() and not offline:
        if clone_project_config_fn:
            activity("all", "Cloning openstack/project-config for retirement checks")
            clone_project_config_fn(project_config_path, run)

    if project_config_path is None or not project_config_path.exists():
        return packages, [], []

    retirement_checker = RetirementChecker(
        project_config_path=project_config_path,
        releases_path=releases_repo,
        target_series=openstack_target,
    )

    retired = retirement_checker.get_retired_packages(packages)
    possibly_retired = retirement_checker.get_possibly_retired_packages(packages)
    exclude = set(retired) | set(possibly_retired)
    if not exclude:
        return packages, retired, possibly_retired

    filtered = [pkg for pkg in packages if pkg not in exclude]
    return filtered, retired, possibly_retired


def get_parallel_batches(
    graph: DependencyGraph,
    state: BuildAllState,
) -> list[list[str]]:
    """Compute parallel build batches from dependency graph.

    Returns packages grouped by dependency level:
    - Batch 0: packages with no dependencies
    - Batch 1: packages depending only on batch 0
    - etc.

    Args:
        graph: Dependency graph.
        state: Current build state.

    Returns:
        List of batches, each batch is a list of package names.
    """
    # Get remaining packages to build
    remaining = {
        name for name, pkg_state in state.packages.items()
        if pkg_state.status == PackageStatus.PENDING
    }

    # Get already built packages
    built = {
        name for name, pkg_state in state.packages.items()
        if pkg_state.status == PackageStatus.SUCCESS
    }

    batches: list[list[str]] = []
    processed: set[str] = set(built)

    while remaining:
        # Find packages whose dependencies are all processed
        ready = []
        for pkg in remaining:
            deps = graph.get_dependencies(pkg)
            # A package is ready if all its deps are processed or not in our graph
            deps_in_graph = deps & set(graph.nodes.keys())
            if deps_in_graph <= processed:
                ready.append(pkg)

        if not ready:
            # Remaining packages have unmet deps (cycles or blocked)
            break

        batches.append(sorted(ready))
        processed.update(ready)
        remaining -= set(ready)

    return batches
