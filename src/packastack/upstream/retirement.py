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

"""Retirement detection for OpenStack projects.

This module detects retired upstream projects using:
1. Primary (authoritative): openstack/project-config gerrit/projects.yaml
   - Projects with description starting with "RETIRED" are retired
2. Fallback (inference): openstack/releases data
   - If a project is not seen in releases for >= 3 cycles, mark as possibly_retired

The module also handles mapping Ubuntu source package names to upstream
project identifiers (e.g., "glance" -> "openstack/glance").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class RetirementStatus(str, Enum):
    """Status of a project's retirement state."""

    ACTIVE = "active"  # Project is actively maintained
    RETIRED = "retired"  # Project is definitively retired (from project-config)
    POSSIBLY_RETIRED = "possibly_retired"  # Inferred as likely retired (from releases)
    UNKNOWN = "unknown"  # Unable to determine retirement status


class MappingConfidence(str, Enum):
    """Confidence level for package-to-upstream mapping."""

    HIGH = "high"  # Explicit entry in upstreams.yaml
    MEDIUM = "medium"  # Matched via common patterns/heuristics
    LOW = "low"  # Best-effort guess
    UNKNOWN = "unknown"  # Unable to map


@dataclass
class RetirementInfo:
    """Retirement information for a package."""

    status: RetirementStatus = RetirementStatus.UNKNOWN
    authoritative: bool = False  # True if from project-config
    source: str = "none"  # "project-config", "releases-inference", "none"
    description: str = ""  # Full description from project-config if available
    upstream_project: str = ""  # Resolved upstream project key (e.g., "openstack/glance")
    mapping_confidence: MappingConfidence = MappingConfidence.UNKNOWN
    last_seen_series: str = ""  # For releases-inference
    cycles_since_last_seen: int = 0  # For releases-inference

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "status": self.status.value,
            "authoritative": self.authoritative,
            "source": self.source,
            "description": self.description,
            "upstream_project": self.upstream_project,
            "mapping_confidence": self.mapping_confidence.value,
            "last_seen_series": self.last_seen_series,
            "cycles_since_last_seen": self.cycles_since_last_seen,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RetirementInfo":
        """Create from dictionary."""
        return cls(
            status=RetirementStatus(data.get("status", "unknown")),
            authoritative=data.get("authoritative", False),
            source=data.get("source", "none"),
            description=data.get("description", ""),
            upstream_project=data.get("upstream_project", ""),
            mapping_confidence=MappingConfidence(data.get("mapping_confidence", "unknown")),
            last_seen_series=data.get("last_seen_series", ""),
            cycles_since_last_seen=data.get("cycles_since_last_seen", 0),
        )


@dataclass
class ProjectConfigEntry:
    """Entry from gerrit/projects.yaml."""

    project: str
    description: str = ""
    acl_config: str = ""

    @property
    def is_retired(self) -> bool:
        """Check if the project is retired based on description."""
        return self.description.startswith("RETIRED")


@dataclass
class ProjectConfigData:
    """Parsed data from gerrit/projects.yaml."""

    projects: dict[str, ProjectConfigEntry] = field(default_factory=dict)
    load_error: str = ""

    def find_project(self, project_key: str) -> ProjectConfigEntry | None:
        """Find a project by its key (e.g., 'openstack/glance')."""
        return self.projects.get(project_key)


def load_project_config(project_config_path: Path) -> ProjectConfigData:
    """Load and parse gerrit/projects.yaml from project-config.

    Args:
        project_config_path: Path to the openstack/project-config clone.

    Returns:
        ProjectConfigData with parsed entries.
    """
    projects_yaml = project_config_path / "gerrit" / "projects.yaml"

    if not projects_yaml.exists():
        return ProjectConfigData(load_error=f"File not found: {projects_yaml}")

    try:
        with open(projects_yaml) as f:
            data = yaml.safe_load(f) or []
    except yaml.YAMLError as e:
        return ProjectConfigData(load_error=f"YAML parse error: {e}")

    projects: dict[str, ProjectConfigEntry] = {}

    # projects.yaml is a list of project entries
    if not isinstance(data, list):
        return ProjectConfigData(load_error="Expected list format in projects.yaml")

    for entry in data:
        if not isinstance(entry, dict):
            continue

        project_name = entry.get("project", "")
        if not project_name:
            continue

        projects[project_name] = ProjectConfigEntry(
            project=project_name,
            description=entry.get("description", ""),
            acl_config=entry.get("acl-config", ""),
        )

    return ProjectConfigData(projects=projects)


def map_package_to_upstream(
    source_package: str,
    registry: Any | None = None,
    releases_deliverables: set[str] | None = None,
) -> tuple[str, MappingConfidence]:
    """Map a source package name to an upstream project key.

    Uses the following resolution order:
    1. Explicit entry in upstreams.yaml registry
    2. Heuristics for common OpenStack patterns

    Args:
        source_package: Ubuntu source package name.
        registry: UpstreamsRegistry instance (optional).
        releases_deliverables: Set of known deliverable names from releases (optional).

    Returns:
        Tuple of (upstream_project_key, confidence).
        upstream_project_key is in the form "openstack/name" or similar.
    """
    # Step 1: Check explicit registry entry
    if registry is not None:
        try:
            if registry.has_explicit_entry(source_package):
                resolved = registry.resolve(source_package, openstack_governed=True)
                # Derive project key from URL or project name
                config = resolved.config
                if config.upstream.url:
                    # Parse URL to get namespace/project
                    url = config.upstream.url
                    if "opendev.org" in url:
                        # e.g., https://opendev.org/openstack/glance.git
                        parts = url.removesuffix(".git").split("/")
                        if len(parts) >= 2:
                            namespace = parts[-2]
                            name = parts[-1]
                            return f"{namespace}/{name}", MappingConfidence.HIGH
                    elif "github.com" in url:
                        # e.g., https://github.com/gnocchixyz/gnocchi.git
                        parts = url.removesuffix(".git").split("/")
                        if len(parts) >= 2:
                            # For non-OpenStack GitHub projects, use the full key
                            namespace = parts[-2]
                            name = parts[-1]
                            return f"github:{namespace}/{name}", MappingConfidence.HIGH
                # Fallback to project key with openstack/ prefix
                return f"openstack/{resolved.project}", MappingConfidence.HIGH
        except Exception:
            pass  # Fall through to heuristics

    # Step 2: Check if it's a known releases deliverable
    if releases_deliverables and source_package in releases_deliverables:
        return f"openstack/{source_package}", MappingConfidence.MEDIUM

    # Step 3: Heuristics for common OpenStack patterns
    # Most OpenStack packages map directly to openstack/<name>
    # Common patterns:
    # - python-*client -> openstack/python-*client
    # - *-dashboard -> openstack/*-dashboard
    # - oslo.* -> openstack/oslo.*

    if source_package.startswith("python-") and source_package.endswith("client"):
        # python-novaclient, python-glanceclient, etc.
        return f"openstack/{source_package}", MappingConfidence.MEDIUM

    if source_package.startswith("oslo"):
        # oslo.config, oslo.messaging, etc.
        return f"openstack/{source_package}", MappingConfidence.MEDIUM

    if "-dashboard" in source_package:
        # horizon plugins
        return f"openstack/{source_package}", MappingConfidence.MEDIUM

    if "-tempest-plugin" in source_package:
        # tempest plugins
        return f"openstack/{source_package}", MappingConfidence.MEDIUM

    # Default: assume openstack/<name> with low confidence
    return f"openstack/{source_package}", MappingConfidence.LOW


def get_series_order(releases_path: Path) -> list[str]:
    """Get ordered list of OpenStack series from releases repository.

    Args:
        releases_path: Path to openstack/releases clone.

    Returns:
        List of series names, oldest to newest.
    """
    series_dir = releases_path / "deliverables"
    if not series_dir.exists():
        return []

    # Get all series directories
    series_list = []
    for entry in series_dir.iterdir():
        if entry.is_dir() and not entry.name.startswith("_"):
            series_list.append(entry.name)

    # Sort by known OpenStack series order
    # This is a simplified approach - ideally we'd read series.yaml
    # for actual release dates
    known_order = [
        "austin", "bexar", "cactus", "diablo", "essex", "folsom",
        "grizzly", "havana", "icehouse", "juno", "kilo", "liberty",
        "mitaka", "newton", "ocata", "pike", "queens", "rocky",
        "stein", "train", "ussuri", "victoria", "wallaby", "xena",
        "yoga", "zed", "antelope", "bobcat", "caracal", "dalmatian",
        "epoxy", "flamingo", "gazpacho",
    ]

    def sort_key(s: str) -> int:
        try:
            return known_order.index(s.lower())
        except ValueError:
            return 999  # Unknown series at end

    return sorted(series_list, key=sort_key)


def find_last_seen_series(
    deliverable: str,
    releases_path: Path,
    target_series: str,
) -> tuple[str, int]:
    """Find the last series where a deliverable was present.

    Searches backwards from the target series through release history
    to find the most recent series where the deliverable was released.

    Args:
        deliverable: The deliverable name (e.g., "nova").
        releases_path: Path to openstack/releases clone.
        target_series: The current target series to search from.

    Returns:
        Tuple of (last_seen_series, cycles_since_last_seen).
        If never seen, returns ("", -1).
    """
    series_order = get_series_order(releases_path)
    if not series_order:
        return "", -1

    # Find target series index
    try:
        target_idx = series_order.index(target_series)
    except ValueError:
        # Target series not in order, use last known series as reference
        target_idx = len(series_order) - 1

    # Search backwards from target series through all history
    last_seen = ""
    last_seen_idx = -1

    # Start from target series and work backwards to find the last occurrence
    for idx in range(target_idx, -1, -1):
        series = series_order[idx]
        deliverables_dir = releases_path / "deliverables" / series

        if not deliverables_dir.exists():
            continue

        # Check if deliverable file exists
        deliverable_file = deliverables_dir / f"{deliverable}.yaml"
        if deliverable_file.exists():
            last_seen = series
            last_seen_idx = idx
            break

    if not last_seen:
        return "", -1

    # Calculate cycles since last seen relative to target
    cycles_since = target_idx - last_seen_idx
    return last_seen, cycles_since


def check_retirement(
    source_package: str,
    project_config_path: Path | None,
    releases_path: Path | None,
    target_series: str,
    registry: Any | None = None,
    releases_deliverables: set[str] | None = None,
    project_config_data: ProjectConfigData | None = None,
) -> RetirementInfo:
    """Check retirement status for a package.

    Args:
        source_package: Ubuntu source package name.
        project_config_path: Path to openstack/project-config clone.
        releases_path: Path to openstack/releases clone.
        target_series: The target OpenStack series.
        registry: UpstreamsRegistry instance (optional).
        releases_deliverables: Set of known deliverable names (optional).
        project_config_data: Pre-loaded project-config data (optional).

    Returns:
        RetirementInfo with retirement status and details.
    """
    # Step 0: Check upstreams registry for explicit retired flag
    # This is authoritative for non-OpenStack projects
    if registry is not None:
        try:
            if registry.is_retired(source_package):
                return RetirementInfo(
                    status=RetirementStatus.RETIRED,
                    authoritative=True,
                    source="upstreams-registry",
                    upstream_project=source_package,
                    mapping_confidence=MappingConfidence.HIGH,
                )
        except Exception:
            pass  # Fall through to other checks

    # Step 1: Map package to upstream project
    upstream_project, mapping_confidence = map_package_to_upstream(
        source_package, registry, releases_deliverables
    )

    info = RetirementInfo(
        upstream_project=upstream_project,
        mapping_confidence=mapping_confidence,
    )

    # Can't check retirement with unknown mapping
    if mapping_confidence == MappingConfidence.UNKNOWN:
        return info

    # Step 2: Check project-config (authoritative)
    if project_config_path is not None or project_config_data is not None:
        if project_config_data is None:
            project_config_data = load_project_config(project_config_path)

        if not project_config_data.load_error:
            entry = project_config_data.find_project(upstream_project)
            if entry is not None:
                if entry.is_retired:
                    info.status = RetirementStatus.RETIRED
                    info.authoritative = True
                    info.source = "project-config"
                    info.description = entry.description
                    return info
                else:
                    info.status = RetirementStatus.ACTIVE
                    info.authoritative = True
                    info.source = "project-config"
                    info.description = entry.description

    # Step 3: Fallback to releases-inference if project-config didn't match
    if releases_path is not None and releases_path.exists():
        # Extract deliverable name from upstream_project
        deliverable = source_package
        if "/" in upstream_project:
            deliverable = upstream_project.split("/")[-1]

        last_seen, cycles_since = find_last_seen_series(
            deliverable, releases_path, target_series
        )

        info.last_seen_series = last_seen
        info.cycles_since_last_seen = cycles_since

        if last_seen:
            # Project was found in releases at some point
            if cycles_since >= 3:
                # Not seen for 3+ cycles, possibly retired
                info.status = RetirementStatus.POSSIBLY_RETIRED
                info.source = "releases-inference"
                info.authoritative = False
                return info
            if info.authoritative:
                return info
            # Recently active
            info.status = RetirementStatus.ACTIVE
            info.source = "releases-inference"
            return info
        # else: Not found in releases at all, keep as UNKNOWN

    return info


class RetirementChecker:
    """Cached retirement checker for multiple packages.

    Pre-loads project-config data and provides efficient lookups.
    """

    def __init__(
        self,
        project_config_path: Path | None = None,
        releases_path: Path | None = None,
        target_series: str = "",
        registry: Any | None = None,
        releases_deliverables: set[str] | None = None,
    ):
        """Initialize the checker.

        Args:
            project_config_path: Path to openstack/project-config clone.
            releases_path: Path to openstack/releases clone.
            target_series: The target OpenStack series.
            registry: UpstreamsRegistry instance.
            releases_deliverables: Set of known deliverable names.
        """
        self.project_config_path = project_config_path
        self.releases_path = releases_path
        self.target_series = target_series
        self.registry = registry
        self.releases_deliverables = releases_deliverables

        # Pre-load project-config
        self._project_config: ProjectConfigData | None = None
        if project_config_path is not None and project_config_path.exists():
            self._project_config = load_project_config(project_config_path)

        # Cache for results
        self._cache: dict[str, RetirementInfo] = {}

    @property
    def project_config_loaded(self) -> bool:
        """Whether project-config data was successfully loaded."""
        return self._project_config is not None and not self._project_config.load_error

    @property
    def project_config_error(self) -> str:
        """Error message if project-config failed to load."""
        if self._project_config is None:
            return "project-config not provided"
        return self._project_config.load_error

    def check(self, source_package: str) -> RetirementInfo:
        """Check retirement status for a package.

        Args:
            source_package: Ubuntu source package name.

        Returns:
            RetirementInfo with retirement status and details.
        """
        if source_package in self._cache:
            return self._cache[source_package]

        info = check_retirement(
            source_package=source_package,
            project_config_path=self.project_config_path,
            releases_path=self.releases_path,
            target_series=self.target_series,
            registry=self.registry,
            releases_deliverables=self.releases_deliverables,
            project_config_data=self._project_config,
        )

        self._cache[source_package] = info
        return info

    def check_retirement(self, source_package: str, *args: object, **kwargs: object) -> RetirementInfo:
        """Backward compatible wrapper.

        Historically callers invoked `check_retirement` on the checker instance.
        Keep that behaviour by forwarding to :meth:`check` and ignore any
        extra positional/keyword arguments (for example `deliverable`) which
        were previously passed by callers.
        """
        return self.check(source_package)

    def check_batch(self, packages: list[str]) -> dict[str, RetirementInfo]:
        """Check retirement status for multiple packages.

        Args:
            packages: List of source package names.

        Returns:
            Dict mapping package name to RetirementInfo.
        """
        return {pkg: self.check(pkg) for pkg in packages}

    def get_retired_packages(self, packages: list[str]) -> list[str]:
        """Get list of retired packages from a set.

        Args:
            packages: List of source package names.

        Returns:
            List of package names that are retired.
        """
        return [
            pkg for pkg in packages
            if self.check(pkg).status == RetirementStatus.RETIRED
        ]

    def get_possibly_retired_packages(self, packages: list[str]) -> list[str]:
        """Get list of possibly retired packages from a set.

        Args:
            packages: List of source package names.

        Returns:
            List of package names that are possibly retired.
        """
        return [
            pkg for pkg in packages
            if self.check(pkg).status == RetirementStatus.POSSIBLY_RETIRED
        ]
