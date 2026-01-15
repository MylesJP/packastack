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

"""OpenStack releases repository data access utilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ReleaseVersion:
    """Represents a single release version of a project."""

    version: str
    projects: list[dict[str, Any]] = field(default_factory=list)
    diff_start: str = ""

    def is_beta(self) -> bool:
        """Check if this is a beta release (contains 'b' marker)."""
        # Beta versions: 26.0.0b1, 26.0.0.0b1, etc.
        return "b" in self.version.lower() and "rc" not in self.version.lower()

    def is_rc(self) -> bool:
        """Check if this is a release candidate (contains 'rc' marker)."""
        return "rc" in self.version.lower()

    def is_final(self) -> bool:
        """Check if this is a final release (no beta/rc/alpha markers)."""
        v = self.version.lower()
        return "b" not in v and "rc" not in v and "a" not in v

    def is_beta_rc_or_final(self) -> bool:
        """Check if this is a beta, RC, or final release."""
        return self.is_beta() or self.is_rc() or self.is_final()


@dataclass
class ProjectRelease:
    """Represents release information for an OpenStack project."""

    name: str
    team: str = ""
    release_model: str = ""  # cycle-with-rc, cycle-with-intermediary, independent
    releases: list[ReleaseVersion] = field(default_factory=list)
    branches: list[dict[str, Any]] = field(default_factory=list)
    type: str = ""  # service, library, other

    def get_latest_version(self) -> str | None:
        """Get the latest release version."""
        if not self.releases:
            return None
        return self.releases[-1].version

    def is_library(self) -> bool:
        """Check if this is a library project."""
        return self.type == "library"

    def has_releases(self) -> bool:
        """Check if this project has any releases."""
        return len(self.releases) > 0

    def has_beta_rc_or_final(self) -> bool:
        """Check if this project has any beta, RC, or final release."""
        return any(r.is_beta_rc_or_final() for r in self.releases)

    def get_latest_release(self) -> ReleaseVersion | None:
        """Get the latest release."""
        if not self.releases:
            return None
        return self.releases[-1]


@dataclass
class SeriesInfo:
    """Represents information about an OpenStack series."""

    name: str
    status: str = ""  # development, maintained, extended maintenance, unmaintained
    initial_release: str = ""
    release_id: str = ""  # e.g., "2024.2" for dalmatian


def load_series_status(releases_repo: Path) -> list[SeriesInfo]:
    """Load the ordered list of OpenStack series from series_status.yaml.

    The series_status.yaml file contains a list of all series in order
    from newest (development) to oldest. This is the authoritative source
    for series ordering.

    Args:
        releases_repo: Path to the openstack/releases repository.

    Returns:
        List of SeriesInfo ordered from newest to oldest.
    """
    status_file = releases_repo / "data" / "series_status.yaml"

    if not status_file.exists():
        return []

    try:
        with status_file.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if not data or not isinstance(data, list):
                return []

            result: list[SeriesInfo] = []
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name", "")
                if not name:
                    continue
                result.append(
                    SeriesInfo(
                        name=name,
                        status=entry.get("status", ""),
                        initial_release=entry.get("initial-release", ""),
                        release_id=entry.get("release-id", ""),
                    )
                )
            return result
    except Exception:
        return []


def load_series_info(releases_repo: Path) -> dict[str, SeriesInfo]:
    """Load information about all OpenStack series.

    Args:
        releases_repo: Path to the openstack/releases repository.

    Returns:
        Dict mapping series name to SeriesInfo.
    """
    series_list = load_series_status(releases_repo)
    series: dict[str, SeriesInfo] = {}

    for info in series_list:
        series[info.name] = info
        # Also add by release-id for lookups like "2024.2"
        if info.release_id:
            series[info.release_id] = info

    return series


def get_current_development_series(releases_repo: Path) -> str | None:
    """Get the name of the current development series.

    Args:
        releases_repo: Path to the openstack/releases repository.

    Returns:
        Series name (e.g., "2025.1", "gazpacho") or None if not found.
    """
    series = load_series_info(releases_repo)
    for name, info in series.items():
        if info.status == "development":
            return name

    # Fallback: scan deliverables directory for the latest series
    deliverables_dir = releases_repo / "deliverables"
    if not deliverables_dir.exists():
        return None

    numbered_series: list[str] = []
    named_series: list[str] = []

    for d in deliverables_dir.iterdir():
        if not d.is_dir():
            continue
        name = d.name
        if name.startswith("_") or name.startswith("."):
            continue

        if name and name[0].isdigit():
            numbered_series.append(name)
        else:
            named_series.append(name)

    # Prefer highest numbered series (e.g., "2025.1" > "2024.2")
    if numbered_series:
        return max(numbered_series)
    # Fall back to alphabetically last named series (e.g., "gazpacho" > "flamingo")
    if named_series:
        return max(named_series)

    return None


# Cache for load_openstack_packages results
_openstack_packages_cache: dict[tuple[Path, str], dict[str, str]] = {}


def load_openstack_packages(
    releases_repo: Path, series: str
) -> dict[str, str]:
    """Load mapping of Ubuntu source package names to OpenStack project names.

    Scans the deliverables directory for the given series and builds a mapping
    from Ubuntu source package names to OpenStack project names. The mapping
    is determined by the project type:
    - Libraries (type: library) use python-{project} as source package
    - Services and other types use {project} as source package

    Args:
        releases_repo: Path to the openstack/releases repository.
        series: OpenStack series name (e.g., "2024.2", "dalmatian").

    Returns:
        Dict mapping Ubuntu source package name to OpenStack project name.
        Example: {"python-oslo.config": "oslo.config", "nova": "nova"}
    """
    cache_key = (releases_repo, series)
    if cache_key in _openstack_packages_cache:
        return _openstack_packages_cache[cache_key]

    packages: dict[str, str] = {}
    deliverables_dir = releases_repo / "deliverables" / series

    if not deliverables_dir.exists():
        return packages

    for yaml_file in deliverables_dir.glob("*.yaml"):
        project = yaml_file.stem
        try:
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
            if not data:
                continue

            project_type = data.get("type", "")

            # Determine Ubuntu source package name based on type
            if project_type == "library":
                if project.startswith("python-"):
                    source_pkg = project
                else:
                    source_pkg = f"python-{project}"
            else:
                source_pkg = project

            packages[source_pkg] = project

        except (OSError, yaml.YAMLError):
            # Skip files that can't be read or parsed
            continue

    _openstack_packages_cache[cache_key] = packages
    return packages


def project_to_package_name(project: str, local_repo: Path) -> str:
    """Map an OpenStack project name to Ubuntu source package name.

    Args:
        project: OpenStack project name (e.g., "oslo.messaging", "nova").
        local_repo: Path to local apt repo with package sources.

    Returns:
        Ubuntu source package name.

    Examples:
        "nova" -> "nova" (if exists)
        "oslo.messaging" -> "python-oslo.messaging" (if exists)
    """
    # Check if project exists as-is in local_repo
    if (local_repo / project / "debian" / "control").exists():
        return project

    # Handle oslo.* projects with various naming conventions
    if project.startswith("oslo."):
        variants = [
            f"python-{project}",  # python-oslo.messaging
            project.replace(".", "-"),  # oslo-messaging
            f"python-{project.replace('.', '-')}",  # python-oslo-messaging
        ]
        for variant in variants:
            if (local_repo / variant / "debian" / "control").exists():
                return variant
        # Default to python-oslo.* if not found in local repo
        return f"python-{project}"

    # Try python- prefix for other projects
    python_prefixed = f"python-{project}"
    if (local_repo / python_prefixed / "debian" / "control").exists():
        return python_prefixed

    # Return original name if no mapping found
    return project


def load_project_releases(releases_repo: Path, series: str, project: str) -> ProjectRelease | None:
    """Load release information for a specific project and series.

    Args:
        releases_repo: Path to the openstack/releases repository.
        series: OpenStack series name (e.g., "2024.2", "zed").
        project: Project name (e.g., "nova", "oslo.messaging").

    Returns:
        ProjectRelease or None if not found.
    """
    deliverables_dir = releases_repo / "deliverables" / series
    if not deliverables_dir.exists():
        return None

    # Try exact match first
    yaml_file = deliverables_dir / f"{project}.yaml"
    if not yaml_file.exists():
        # Try with underscores replaced by dots (oslo_messaging -> oslo.messaging)
        alt_name = project.replace("_", ".")
        yaml_file = deliverables_dir / f"{alt_name}.yaml"

    if not yaml_file.exists():
        # Try with python- prefix (openstackclient -> python-openstackclient)
        yaml_file = deliverables_dir / f"python-{project}.yaml"

    if not yaml_file.exists():
        return None

    try:
        with yaml_file.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if not data:
                return None

            releases: list[ReleaseVersion] = []
            for rel in data.get("releases", []):
                releases.append(ReleaseVersion(
                    version=rel.get("version", ""),
                    projects=rel.get("projects", []),
                    diff_start=rel.get("diff-start", ""),
                ))

            return ProjectRelease(
                name=yaml_file.stem,
                team=data.get("team", ""),
                release_model=data.get("release-model", ""),
                releases=releases,
                branches=data.get("branches", []),
                type=data.get("type", ""),
            )
    except Exception:
        return None


def find_projects_by_prefix(releases_repo: Path, series: str, prefix: str) -> list[str]:
    """Find all projects matching a prefix in a series.

    Args:
        releases_repo: Path to the openstack/releases repository.
        series: OpenStack series name.
        prefix: Prefix to match (e.g., "oslo" matches "oslo.messaging", "oslo.config").

    Returns:
        List of matching project names.
    """
    deliverables_dir = releases_repo / "deliverables" / series
    if not deliverables_dir.exists():
        return []

    matches: list[str] = []
    for yaml_file in deliverables_dir.glob("*.yaml"):
        name = yaml_file.stem
        if name.startswith(prefix) or name.replace(".", "_").startswith(prefix):
            matches.append(name)

    return sorted(matches)


def is_snapshot_eligible(
    releases_repo: Path, series: str, project: str
) -> tuple[bool, str, str | None]:
    """Check if a project is eligible for snapshot builds.

    Policy:
    - If project has beta, RC, or final release: block snapshots, use release
    - If project has only pre-beta releases: allow snapshots with warning
    - If project has no releases: allow snapshots

    Args:
        releases_repo: Path to the openstack/releases repository.
        series: OpenStack series name.
        project: Project name.

    Returns:
        Tuple of (eligible, reason, preferred_version).
        preferred_version is set when a release should be used instead.
    """
    proj = load_project_releases(releases_repo, series, project)
    if proj is None:
        return False, f"Project {project} not found in series {series}", None

    if not proj.has_releases():
        # No releases yet - snapshots allowed
        return True, "No releases yet - snapshots allowed", None

    latest = proj.get_latest_release()
    assert latest is not None  # has_releases() guarantees this

    if proj.has_beta_rc_or_final():
        # Beta, RC, or final exists - block snapshots, use release
        return (
            False,
            f"Release {latest.version} available - use release tarball",
            latest.version,
        )

    # Has releases but none are beta/rc/final (e.g., milestones only)
    # Allow snapshots but warn
    return (
        True,
        f"Warning: releases exist ({latest.version}) but no beta/rc/final yet",
        None,
    )


def list_series(releases_repo: Path) -> list[str]:
    """List all available OpenStack series.

    Uses data/series_status.yaml which is ordered from newest (development)
    to oldest series.

    Args:
        releases_repo: Path to the openstack/releases repository.

    Returns:
        List of series names, ordered from most recent to oldest.
    """
    series_list = load_series_status(releases_repo)
    if series_list:
        return [s.name for s in series_list]

    # Fallback: scan deliverables directory (less accurate ordering)
    deliverables_dir = releases_repo / "deliverables"
    if not deliverables_dir.exists():
        return []

    series: list[str] = []
    for d in deliverables_dir.iterdir():
        if d.is_dir() and not d.name.startswith("."):
            series.append(d.name)

    # Sort with numeric series (2024.2) after named series (zed)
    def sort_key(s: str) -> tuple[int, str]:
        if s[0].isdigit():
            return (1, s)
        return (0, s)

    return sorted(series, key=sort_key, reverse=True)


def get_previous_series(releases_repo: Path, target_series: str) -> str | None:
    """Get the series immediately preceding the target series.

    Uses the ordering from list_series() to determine the previous series.
    For example:
        - "2025.1" -> "2024.2"
        - "2024.2" -> "2024.1"
        - "caracal" -> "bobcat"

    Args:
        releases_repo: Path to the openstack/releases repository.
        target_series: Target OpenStack series name.

    Returns:
        Previous series name, or None if target is oldest or not found.
    """
    all_series = list_series(releases_repo)

    if target_series not in all_series:
        return None

    idx = all_series.index(target_series)

    # list_series returns newest first, so previous is next in list
    if idx + 1 < len(all_series):
        return all_series[idx + 1]

    return None


def get_series_codename(releases_repo: Path, series: str) -> str | None:
    """Get the codename for a series if it has one.

    Some series are identified by year.release (e.g., "2024.2") but also
    have a codename (e.g., "dalmatian"). This function returns the
    codename if available.

    Args:
        releases_repo: Path to the openstack/releases repository.
        series: Series identifier (numeric or codename).

    Returns:
        Codename if available, None otherwise.
    """
    # Check series_status for codename info
    series_info = load_series_info(releases_repo)
    info = series_info.get(series)

    if info and hasattr(info, "codename") and info.codename:
        return info.codename

    # For named series, return the series name itself as the codename
    if series and not series[0].isdigit():
        return series

    return None


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        repo_path = Path(sys.argv[1])
        print("Available series:")
        for s in list_series(repo_path):
            info = load_series_info(repo_path).get(s)
            status = info.status if info else "unknown"
            print(f"  {s}: {status}")
