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

"""Build manifest computation for unified dependency planning.

Computes all package versions upfront using topological sort to ensure
consistent dependency resolution across the entire build.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from packastack.planning.graph import DependencyGraph
from packastack.planning.type_selection import (
    BuildType,
    CycleStage,
    TypeSelectionResult,
)

if TYPE_CHECKING:
    from packastack.apt.packages import PackageIndex
    from packastack.upstream.registry import UpstreamRegistry

logger = logging.getLogger(__name__)


@dataclass
class PackageVersion:
    """Version information for a package in the manifest."""

    source_package: str
    deliverable: str
    upstream_version: str
    debian_revision: str
    epoch: int
    build_type: BuildType
    # Full debian version string (epoch:upstream-revision)
    full_version: str = ""
    # Whether this version was computed (True) or pre-existing (False)
    computed: bool = True
    # Source of version (release, milestone tag, git snapshot, archive)
    version_source: str = ""

    def __post_init__(self) -> None:
        """Compute full version string if not set."""
        if not self.full_version:
            if self.epoch:
                self.full_version = f"{self.epoch}:{self.upstream_version}-{self.debian_revision}"
            else:
                self.full_version = f"{self.upstream_version}-{self.debian_revision}"


@dataclass
class BuildManifest:
    """Complete manifest of all packages to be built with their versions.

    The manifest ensures consistent version resolution across all packages
    by computing versions in topological order (dependencies before dependents).
    """

    series: str
    cycle_stage: CycleStage
    packages: dict[str, PackageVersion] = field(default_factory=dict)
    build_order: list[str] = field(default_factory=list)
    # Type selection results for each package
    type_selections: dict[str, TypeSelectionResult] = field(default_factory=dict)
    # Dependencies between packages (source_package -> list of dependencies)
    dependency_edges: dict[str, list[str]] = field(default_factory=dict)
    # Warnings generated during manifest computation
    warnings: list[str] = field(default_factory=list)

    def get_version(self, source_package: str) -> str | None:
        """Get the planned version for a package.

        Args:
            source_package: Source package name.

        Returns:
            Full debian version string, or None if not in manifest.
        """
        if source_package in self.packages:
            return self.packages[source_package].full_version
        return None

    def get_upstream_version(self, source_package: str) -> str | None:
        """Get the upstream version for a package.

        Args:
            source_package: Source package name.

        Returns:
            Upstream version string, or None if not in manifest.
        """
        if source_package in self.packages:
            return self.packages[source_package].upstream_version
        return None

    def get_build_type(self, source_package: str) -> BuildType | None:
        """Get the build type for a package.

        Args:
            source_package: Source package name.

        Returns:
            BuildType, or None if not in manifest.
        """
        if source_package in self.packages:
            return self.packages[source_package].build_type
        return None

    def is_in_manifest(self, source_package: str) -> bool:
        """Check if a package is in the manifest."""
        return source_package in self.packages

    def add_package(
        self,
        source_package: str,
        deliverable: str,
        upstream_version: str,
        debian_revision: str,
        build_type: BuildType,
        epoch: int = 0,
        version_source: str = "",
    ) -> PackageVersion:
        """Add a package to the manifest.

        Args:
            source_package: Source package name.
            deliverable: OpenStack deliverable name.
            upstream_version: Upstream version string.
            debian_revision: Debian revision string.
            build_type: Build type for this package.
            epoch: Epoch (default 0).
            version_source: Source of the version.

        Returns:
            The PackageVersion that was added.
        """
        pkg_version = PackageVersion(
            source_package=source_package,
            deliverable=deliverable,
            upstream_version=upstream_version,
            debian_revision=debian_revision,
            epoch=epoch,
            build_type=build_type,
            version_source=version_source,
        )
        self.packages[source_package] = pkg_version
        return pkg_version

    def to_dict(self) -> dict:
        """Convert manifest to a dictionary for serialization."""
        return {
            "series": self.series,
            "cycle_stage": self.cycle_stage.value,
            "packages": {
                name: {
                    "source_package": pkg.source_package,
                    "deliverable": pkg.deliverable,
                    "upstream_version": pkg.upstream_version,
                    "debian_revision": pkg.debian_revision,
                    "epoch": pkg.epoch,
                    "full_version": pkg.full_version,
                    "build_type": pkg.build_type.value,
                    "version_source": pkg.version_source,
                }
                for name, pkg in self.packages.items()
            },
            "build_order": self.build_order,
            "dependency_edges": self.dependency_edges,
            "warnings": self.warnings,
        }


def compute_build_order(
    packages: list[str],
    dependency_graph: DependencyGraph,
) -> list[str]:
    """Compute build order for packages using topological sort.

    Args:
        packages: List of package names to include.
        dependency_graph: Graph of package dependencies.

    Returns:
        List of packages in build order (dependencies first).
    """
    # Filter graph to only include requested packages
    filtered_order = []
    try:
        full_order = dependency_graph.topological_sort()
        package_set = set(packages)
        filtered_order = [p for p in full_order if p in package_set]
    except ValueError as e:
        logger.warning(f"Cycle detected in dependency graph: {e}")
        # Fall back to original order
        filtered_order = packages

    return filtered_order


def resolve_version_for_package(
    source_package: str,
    build_type: BuildType,
    releases_repo: Path | None,
    series: str,
    deliverable: str,
    packaging_repo: Path | None = None,
) -> tuple[str, str, int, str]:
    """Resolve the version for a package based on build type.

    Args:
        source_package: Source package name.
        build_type: The build type (release, milestone, snapshot).
        releases_repo: Path to openstack/releases checkout.
        series: Target OpenStack series.
        deliverable: OpenStack deliverable name.
        packaging_repo: Path to the packaging repository.

    Returns:
        Tuple of (upstream_version, debian_revision, epoch, version_source).
    """
    # This is a stub - actual implementation will use:
    # - For RELEASE: Get latest release from openstack/releases
    # - For MILESTONE: Get milestone tag from releases or git
    # - For SNAPSHOT: Generate version from git describe
    #
    # The packaging_repo's d/changelog provides epoch and debian revision hints.

    upstream_version = "0.0.0"
    debian_revision = "0ubuntu1"
    epoch = 0
    version_source = "placeholder"

    if build_type == BuildType.RELEASE:
        # Look up release version from openstack/releases
        if releases_repo and releases_repo.exists():
            from packastack.upstream.releases import load_project_releases

            releases = load_project_releases(
                releases_repo=releases_repo,
                series=series,
                project=deliverable,
            )
            if releases:
                # Get the latest release version
                latest = releases[-1] if releases else None
                if latest:
                    upstream_version = latest.version
                    version_source = "openstack/releases"
    elif build_type == BuildType.MILESTONE:
        # Look up milestone from openstack/releases
        if releases_repo and releases_repo.exists():
            from packastack.upstream.releases import load_project_releases

            releases = load_project_releases(
                releases_repo=releases_repo,
                series=series,
                project=deliverable,
            )
            if releases:
                # Get the latest milestone (could be beta/rc)
                latest = releases[-1] if releases else None
                if latest:
                    upstream_version = latest.version
                    version_source = "openstack/releases"
    else:
        # SNAPSHOT: Will be computed from git describe at build time
        version_source = "git-snapshot"

    return upstream_version, debian_revision, epoch, version_source


def create_manifest(
    packages: list[str],
    series: str,
    cycle_stage: CycleStage,
    type_selections: dict[str, TypeSelectionResult],
    dependency_graph: DependencyGraph,
    releases_repo: Path | None = None,
) -> BuildManifest:
    """Create a build manifest with versions for all packages.

    Computes versions in topological order to ensure dependencies
    are resolved before dependents.

    Args:
        packages: List of source packages to include.
        series: Target OpenStack series.
        cycle_stage: Current cycle stage (pre/post final).
        type_selections: Type selection results for each package.
        dependency_graph: Dependency graph for the packages.
        releases_repo: Path to openstack/releases checkout.

    Returns:
        BuildManifest with all package versions computed.
    """
    manifest = BuildManifest(
        series=series,
        cycle_stage=cycle_stage,
    )

    # Store type selections
    manifest.type_selections = type_selections

    # Compute build order
    manifest.build_order = compute_build_order(packages, dependency_graph)

    # Store dependency edges
    for pkg in packages:
        deps = dependency_graph.get_dependencies(pkg)
        manifest.dependency_edges[pkg] = list(deps)

    # Compute versions in build order (dependencies first)
    for source_package in manifest.build_order:
        type_result = type_selections.get(source_package)
        if not type_result:
            manifest.warnings.append(
                f"No type selection for {source_package}, defaulting to snapshot"
            )
            build_type = BuildType.SNAPSHOT
            deliverable = source_package
        else:
            build_type = type_result.chosen_type
            deliverable = type_result.deliverable

        upstream_version, debian_revision, epoch, version_source = (
            resolve_version_for_package(
                source_package=source_package,
                build_type=build_type,
                releases_repo=releases_repo,
                series=series,
                deliverable=deliverable,
            )
        )

        manifest.add_package(
            source_package=source_package,
            deliverable=deliverable,
            upstream_version=upstream_version,
            debian_revision=debian_revision,
            build_type=build_type,
            epoch=epoch,
            version_source=version_source,
        )

    return manifest
