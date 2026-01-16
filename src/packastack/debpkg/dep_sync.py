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

"""Dependency synchronization between upstream requirements and debian/control.

Syncs version constraints from upstream requirements files to the corresponding
debian/control Build-Depends and Depends fields, applying version bumps as
needed while considering Cloud Archive vs development mode constraints.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from packastack.debpkg.control import (
    ParsedDependency,
)
from packastack.debpkg.version import (
    upstream_version_newer,
)
from packastack.planning.validated_plan import (
    UpstreamDeps,
    extract_upstream_deps,
    map_python_to_debian,
)

if TYPE_CHECKING:
    from packastack.apt.packages import PackageIndex
    from packastack.planning.build_manifest import BuildManifest

logger = logging.getLogger(__name__)


@dataclass
class VersionBump:
    """Represents a version bump for a dependency."""

    debian_package: str
    python_package: str
    old_version: str  # Empty if not previously specified
    new_version: str
    source: str  # Where the new version came from (manifest, LTS, upstream spec)


@dataclass
class SyncResult:
    """Result of dependency synchronization."""

    # Dependencies to add (not currently in d/control)
    additions: list[ParsedDependency] = field(default_factory=list)
    # Version bumps to apply (package exists but needs version update)
    version_bumps: list[VersionBump] = field(default_factory=list)
    # Dependencies that could not be resolved
    unresolved: list[str] = field(default_factory=list)
    # Warnings (e.g., version downgrade requests)
    warnings: list[str] = field(default_factory=list)
    # Packages found in manifest (built locally)
    from_manifest: list[str] = field(default_factory=list)
    # Packages from LTS archive
    from_lts: list[str] = field(default_factory=list)


def get_lts_version(
    debian_package: str,
    ubuntu_index: PackageIndex | None,
    is_cloud_archive: bool = False,
) -> str | None:
    """Get the version of a package from the appropriate LTS.

    For Cloud Archive builds, we use previous LTS as the base.
    For devel builds, we use current LTS.

    Args:
        debian_package: Debian package name.
        ubuntu_index: Package index to query.
        is_cloud_archive: True if building for Cloud Archive.

    Returns:
        Version string, or None if not found.
    """
    if not ubuntu_index:
        return None

    try:
        version = ubuntu_index.get_version(debian_package)
        return version
    except Exception:
        return None


def resolve_upstream_version_constraint(
    python_package: str,
    version_spec: str,
    debian_package: str,
    manifest: BuildManifest | None,
    ubuntu_index: PackageIndex | None,
    is_cloud_archive: bool = False,
) -> tuple[str, str]:
    """Resolve the appropriate version constraint for a dependency.

    Priority:
    1. If package is in the build manifest, use manifest version
    2. If package is in LTS archive, use LTS version (with min constraints)
    3. Parse upstream version specifier and apply

    Args:
        python_package: Python package name (normalized).
        version_spec: Upstream version specifier (e.g., ">=1.0,<2.0").
        debian_package: Debian package name.
        manifest: Build manifest with planned versions.
        ubuntu_index: Package index for LTS versions.
        is_cloud_archive: True if building for Cloud Archive.

    Returns:
        Tuple of (resolved_version, source) where source describes
        where the version came from.
    """
    # 1. Check if package is in the build manifest
    if manifest and manifest.is_in_manifest(python_package):
        version = manifest.get_version(python_package)
        if version:
            # Extract upstream portion of debian version
            # Version format: [epoch:]upstream-revision
            upstream = version
            if ":" in upstream:
                upstream = upstream.split(":", 1)[1]
            if "-" in upstream:
                upstream = upstream.rsplit("-", 1)[0]
            return upstream, "manifest"

    # 2. Check LTS archive
    lts_version = get_lts_version(debian_package, ubuntu_index, is_cloud_archive)
    if lts_version:
        # Extract upstream version from debian version
        upstream = lts_version
        if ":" in upstream:
            upstream = upstream.split(":", 1)[1]
        if "-" in upstream:
            upstream = upstream.rsplit("-", 1)[0]
        return upstream, "lts"

    # 3. Parse upstream specifier
    if version_spec:
        # Extract minimum version from specifier (simple approach)
        # Handle >=X.Y.Z, >X.Y.Z, ==X.Y.Z patterns
        import re

        min_match = re.search(r">=?\s*([0-9][0-9a-zA-Z.]*)", version_spec)
        if min_match:
            return min_match.group(1), "upstream_spec"

        eq_match = re.search(r"==\s*([0-9][0-9a-zA-Z.]*)", version_spec)
        if eq_match:
            return eq_match.group(1), "upstream_spec"

    return "", "none"


def compute_version_bumps(
    existing_deps: list[ParsedDependency],
    upstream_deps: UpstreamDeps,
    manifest: BuildManifest | None = None,
    ubuntu_index: PackageIndex | None = None,
    is_cloud_archive: bool = False,
) -> SyncResult:
    """Compute version bumps needed to sync debian deps with upstream.

    Args:
        existing_deps: Current dependencies from d/control.
        upstream_deps: Parsed upstream dependencies.
        manifest: Build manifest with planned versions.
        ubuntu_index: Package index for LTS versions.
        is_cloud_archive: True if building for Cloud Archive.

    Returns:
        SyncResult with computed changes.
    """
    result = SyncResult()

    # Create mapping of existing deps by name
    existing_by_name: dict[str, ParsedDependency] = {}
    for dep in existing_deps:
        existing_by_name[dep.name] = dep

    # Process all upstream deps
    all_upstream = upstream_deps.all_deps()
    for python_name, version_spec in all_upstream:
        # Map to debian package name
        debian_name, is_uncertain = map_python_to_debian(python_name)
        if not debian_name:
            # Package should be skipped (stdlib, etc.)
            continue

        # Resolve version constraint
        resolved_version, source = resolve_upstream_version_constraint(
            python_package=python_name,
            version_spec=version_spec,
            debian_package=debian_name,
            manifest=manifest,
            ubuntu_index=ubuntu_index,
            is_cloud_archive=is_cloud_archive,
        )

        # Track source of version
        if source == "manifest":
            result.from_manifest.append(debian_name)
        elif source == "lts":
            result.from_lts.append(debian_name)
        elif source == "none" and is_uncertain:
            result.unresolved.append(python_name)
            continue

        # Check if dep exists in d/control
        if debian_name in existing_by_name:
            existing = existing_by_name[debian_name]
            old_version = existing.version

            # Compare versions if both exist
            if resolved_version and old_version:
                # upstream_version_newer(current, candidate) checks if candidate > current
                if upstream_version_newer(old_version, resolved_version):
                    result.version_bumps.append(
                        VersionBump(
                            debian_package=debian_name,
                            python_package=python_name,
                            old_version=old_version,
                            new_version=resolved_version,
                            source=source,
                        )
                    )
            elif resolved_version and not old_version:
                # Add version constraint where there was none
                result.version_bumps.append(
                    VersionBump(
                        debian_package=debian_name,
                        python_package=python_name,
                        old_version="",
                        new_version=resolved_version,
                        source=source,
                    )
                )
        else:
            # New dependency to add
            if resolved_version:
                new_dep = ParsedDependency(
                    name=debian_name,
                    relation=">=",
                    version=resolved_version,
                )
            else:
                new_dep = ParsedDependency(name=debian_name)
            result.additions.append(new_dep)

    return result


def apply_version_bumps(
    deps: list[ParsedDependency],
    bumps: list[VersionBump],
) -> list[ParsedDependency]:
    """Apply version bumps to a dependency list.

    Args:
        deps: Original dependency list.
        bumps: Version bumps to apply.

    Returns:
        New list with bumps applied.
    """
    # Create bump lookup by debian package name
    bump_by_name = {b.debian_package: b for b in bumps}

    result = []
    for dep in deps:
        if dep.name in bump_by_name:
            bump = bump_by_name[dep.name]
            new_dep = ParsedDependency(
                name=dep.name,
                relation=">=",
                version=bump.new_version,
                arch_qualifiers=dep.arch_qualifiers,
                alternatives=dep.alternatives,
            )
            result.append(new_dep)
        else:
            result.append(dep)

    return result


def sync_upstream_deps(
    packaging_repo: Path,
    upstream_repo: Path,
    manifest: BuildManifest | None = None,
    ubuntu_index: PackageIndex | None = None,
    is_cloud_archive: bool = False,
    use_glob: bool = False,
    dry_run: bool = True,
) -> SyncResult:
    """Synchronize debian/control dependencies with upstream requirements.

    Args:
        packaging_repo: Path to the debian packaging repository.
        upstream_repo: Path to the upstream source repository.
        manifest: Build manifest with planned versions.
        ubuntu_index: Package index for LTS versions.
        is_cloud_archive: True if building for Cloud Archive.
        use_glob: If True, glob for *requirements*.txt files.
        dry_run: If True, compute changes but don't apply them.

    Returns:
        SyncResult with computed changes.
    """
    from packastack.debpkg.control import parse_control

    # Parse upstream deps
    upstream_deps = extract_upstream_deps(upstream_repo, use_glob=use_glob)

    # Parse existing d/control
    control_path = packaging_repo / "debian" / "control"
    if not control_path.exists():
        return SyncResult(
            unresolved=[name for name, _ in upstream_deps.all_deps()],
            warnings=["debian/control not found"],
        )

    source = parse_control(control_path)

    # Combine build-depends and all binary depends
    all_existing: list[ParsedDependency] = []
    all_existing.extend(source.build_depends)
    all_existing.extend(source.build_depends_indep)
    for binary in source.binaries:
        all_existing.extend(binary.depends)
        all_existing.extend(binary.pre_depends)

    # Compute version bumps
    result = compute_version_bumps(
        existing_deps=all_existing,
        upstream_deps=upstream_deps,
        manifest=manifest,
        ubuntu_index=ubuntu_index,
        is_cloud_archive=is_cloud_archive,
    )

    # TODO: Apply changes to d/control if not dry_run

    return result
