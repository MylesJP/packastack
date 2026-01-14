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

"""Auto type selection for package builds based on openstack/releases data.

Determines whether to build from release, milestone, or snapshot based on:
- Series phase (pre-final vs post-final)
- Project release status (has releases, beta/RC/final)
- Release model (cycle-with-rc, cycle-with-intermediary, cycle-trailing, etc.)
"""

from __future__ import annotations

import concurrent.futures
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packastack.upstream.releases import (
    ProjectRelease,
    load_openstack_packages,
    load_project_releases,
    load_series_info,
)

if TYPE_CHECKING:
    from packastack.upstream.retirement import RetirementChecker, RetirementInfo


class BuildType(str, Enum):
    """Build type for a package."""

    RELEASE = "release"
    MILESTONE = "milestone"
    SNAPSHOT = "snapshot"


class CycleStage(str, Enum):
    """Stage of the OpenStack release cycle."""

    PRE_FINAL = "pre_final"  # Series is in development
    POST_FINAL = "post_final"  # Series has been released
    UNKNOWN = "unknown"


class DeliverableKind(str, Enum):
    """Kind of OpenStack deliverable."""

    SERVICE = "service"  # Core services like nova, glance
    LIBRARY = "library"  # Oslo libraries, clients
    CLIENT = "client"  # API clients (python-*client)
    HORIZON_PLUGIN = "horizon_plugin"  # Horizon dashboard plugins
    TEMPEST_PLUGIN = "tempest_plugin"  # Tempest test plugins
    OTHER = "other"  # Everything else
    UNKNOWN = "unknown"


class KindConfidence(str, Enum):
    """Confidence level for deliverable kind inference."""

    METADATA = "metadata"  # From deliverable YAML type field
    HEURISTIC = "heuristic"  # Inferred from project name/patterns
    DEFAULT = "default"  # Fallback when no information available


class ReasonCode(str, Enum):
    """Reason codes for type selection decisions."""

    # Release reasons
    HAS_RELEASE = "HAS_RELEASE"  # Beta/RC/final exists
    POST_FINAL_RELEASE = "POST_FINAL_RELEASE"  # Post-final, use release
    CYCLE_TRAILING_RELEASE = "CYCLE_TRAILING_RELEASE"  # Cycle-trailing has release

    # Milestone reasons
    HAS_MILESTONE_ONLY = "HAS_MILESTONE_ONLY"  # Only pre-beta releases
    INTERMEDIARY_RELEASE = "INTERMEDIARY_RELEASE"  # cycle-with-intermediary has release

    # Snapshot reasons
    NO_RELEASE_YET = "NO_RELEASE_YET"  # No releases in series yet
    PRE_FINAL_NO_RELEASE = "PRE_FINAL_NO_RELEASE"  # Pre-final and no release
    NOT_IN_RELEASES = "NOT_IN_RELEASES"  # Project not in openstack/releases
    SNAPSHOT_FORCED = "SNAPSHOT_FORCED"  # User forced snapshot mode
    CLIENT_LIBRARY_NO_SNAPSHOT = "CLIENT_LIBRARY_NO_SNAPSHOT"  # Clients/oslo packages always use releases

    # Retirement reasons
    RETIRED_PROJECT = "RETIRED_PROJECT"  # Project is retired upstream

    # Error/fallback reasons
    RELEASE_MODEL_UNKNOWN = "RELEASE_MODEL_UNKNOWN"  # Unknown release model
    CYCLE_STAGE_UNKNOWN = "CYCLE_STAGE_UNKNOWN"  # Can't determine cycle stage


class PackageStatus(str, Enum):
    """Status of a package relative to the releases repository."""

    ACTIVE = "active"  # Normal package in releases
    NEW = "new"  # Package exists locally but not in releases (new to OpenStack)
    DEFUNCT = "defunct"  # In releases but no local packaging repo
    RETIRED = "retired"  # Project is retired upstream
    UNKNOWN = "unknown"


class UpstreamAuthority(str, Enum):
    """Authority used for upstream version discovery."""

    RELEASES = "releases"  # openstack/releases repository
    WATCH = "watch"  # debian/watch + uscan
    NONE = "none"  # No upstream authority available


@dataclass
class UpstreamResolution:
    """Information about how upstream version was resolved.

    Tracks whether openstack/releases or debian/watch was used as the
    authority for upstream version discovery, and the outcome.
    """

    authority: UpstreamAuthority
    watch_used: bool = False
    uscan_used: bool = False
    reason: str = ""
    upstream_version: str = ""
    download_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "authority": self.authority.value,
            "watch_used": self.watch_used,
            "uscan_used": self.uscan_used,
            "reason": self.reason,
            "upstream_version": self.upstream_version,
            "download_url": self.download_url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UpstreamResolution:
        """Create from dictionary."""
        return cls(
            authority=UpstreamAuthority(data.get("authority", "none")),
            watch_used=data.get("watch_used", False),
            uscan_used=data.get("uscan_used", False),
            reason=data.get("reason", ""),
            upstream_version=data.get("upstream_version", ""),
            download_url=data.get("download_url", ""),
        )


@dataclass
class WatchInfo:
    """Information from debian/watch file parsing and uscan execution.

    Captures the state of debian/watch parsing and optional uscan
    execution for upstream version detection during planning.
    """

    parsed: bool = False
    mode: str = "unknown"  # DetectedWatchMode value
    uscan_attempted: bool = False
    uscan_status: str = ""  # UscanStatus value
    uscan_error: str = ""
    packaged_version: str = ""  # Current version from d/changelog
    upstream_version: str = ""  # Discovered upstream version
    newer_available: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "parsed": self.parsed,
            "mode": self.mode,
            "uscan_attempted": self.uscan_attempted,
            "uscan_status": self.uscan_status,
            "uscan_error": self.uscan_error,
            "packaged_version": self.packaged_version,
            "upstream_version": self.upstream_version,
            "newer_available": self.newer_available,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WatchInfo:
        """Create from dictionary."""
        return cls(
            parsed=data.get("parsed", False),
            mode=data.get("mode", "unknown"),
            uscan_attempted=data.get("uscan_attempted", False),
            uscan_status=data.get("uscan_status", ""),
            uscan_error=data.get("uscan_error", ""),
            packaged_version=data.get("packaged_version", ""),
            upstream_version=data.get("upstream_version", ""),
            newer_available=data.get("newer_available", False),
        )


@dataclass
class WatchConfig:
    """Configuration for watch file processing and uscan execution.

    Controls how debian/watch files are processed during planning,
    including whether uscan is run to discover upstream versions.
    """

    enabled: bool = True
    """Master switch for watch processing. Disabled by --offline."""

    fallback_for_not_in_releases: bool = True
    """Use watch/uscan for packages not in openstack/releases."""

    check_upstream: bool = True
    """Run uscan to discover upstream versions."""

    timeout_seconds: int = 30
    """Timeout for each uscan execution."""

    max_projects: int = 0
    """Maximum projects to run uscan for (0 = unlimited)."""


@dataclass
class TypeSelectionResult:
    """Result of type selection for a single package."""

    source_package: str
    deliverable: str
    release_model: str
    deliverable_kind: DeliverableKind
    kind_confidence: KindConfidence
    has_release_for_cycle: bool
    has_beta_rc_final: bool
    latest_version: str
    cycle_stage: CycleStage
    chosen_type: BuildType
    reason_code: ReasonCode
    reason_human: str
    package_status: PackageStatus = PackageStatus.ACTIVE
    upstream_resolution: UpstreamResolution | None = None
    watch_info: WatchInfo | None = None
    retirement_info: RetirementInfo | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "source_package": self.source_package,
            "deliverable": self.deliverable,
            "release_model": self.release_model,
            "deliverable_kind": self.deliverable_kind.value,
            "kind_confidence": self.kind_confidence.value,
            "has_release_for_cycle": self.has_release_for_cycle,
            "has_beta_rc_final": self.has_beta_rc_final,
            "latest_version": self.latest_version,
            "cycle_stage": self.cycle_stage.value,
            "chosen_type": self.chosen_type.value,
            "reason_code": self.reason_code.value,
            "reason_human": self.reason_human,
            "package_status": self.package_status.value,
        }
        if self.upstream_resolution:
            result["upstream_resolution"] = self.upstream_resolution.to_dict()
        if self.watch_info:
            result["watch_info"] = self.watch_info.to_dict()
        if self.retirement_info:
            result["retirement_info"] = self.retirement_info.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TypeSelectionResult:
        """Create from dictionary."""
        from packastack.upstream.retirement import RetirementInfo

        upstream_resolution = None
        if "upstream_resolution" in data:
            upstream_resolution = UpstreamResolution.from_dict(data["upstream_resolution"])
        watch_info = None
        if "watch_info" in data:
            watch_info = WatchInfo.from_dict(data["watch_info"])
        retirement_info = None
        if "retirement_info" in data:
            retirement_info = RetirementInfo.from_dict(data["retirement_info"])

        return cls(
            source_package=data["source_package"],
            deliverable=data["deliverable"],
            release_model=data.get("release_model", ""),
            deliverable_kind=DeliverableKind(data.get("deliverable_kind", "unknown")),
            kind_confidence=KindConfidence(data.get("kind_confidence", "default")),
            has_release_for_cycle=data.get("has_release_for_cycle", False),
            has_beta_rc_final=data.get("has_beta_rc_final", False),
            latest_version=data.get("latest_version", ""),
            cycle_stage=CycleStage(data.get("cycle_stage", "unknown")),
            chosen_type=BuildType(data["chosen_type"]),
            reason_code=ReasonCode(data["reason_code"]),
            reason_human=data.get("reason_human", ""),
            package_status=PackageStatus(data.get("package_status", "active")),
            upstream_resolution=upstream_resolution,
            watch_info=watch_info,
            retirement_info=retirement_info,
        )


@dataclass
class TypeSelectionReport:
    """Complete type selection report for multiple packages."""

    run_id: str
    target: str
    ubuntu_series: str
    generated_at_utc: str
    type_mode: str  # "auto", "release", "milestone", "snapshot"
    cycle_stage: CycleStage
    packages: list[TypeSelectionResult] = field(default_factory=list)

    # Summary counts
    count_release: int = 0
    count_milestone: int = 0
    count_snapshot: int = 0
    count_retired: int = 0

    # Counts by reason
    counts_by_reason: dict[str, int] = field(default_factory=dict)

    # Counts by cycle stage
    counts_by_stage: dict[str, int] = field(default_factory=dict)

    # New/defunct/retired package tracking
    new_packages: list[str] = field(default_factory=list)
    defunct_packages: list[str] = field(default_factory=list)
    retired_packages: list[str] = field(default_factory=list)
    """Packages that are retired upstream (from project-config)."""

    possibly_retired_packages: list[str] = field(default_factory=list)
    """Packages that are possibly retired (from releases-inference)."""

    # Cross-reference warnings
    missing_upstream: list[str] = field(default_factory=list)
    """Packages with no entry in openstack/releases AND not in upstreams.yaml."""

    missing_packaging: list[str] = field(default_factory=list)
    """Libraries/services in openstack/releases without a packaging repo."""

    needs_upstream_mapping: list[str] = field(default_factory=list)
    """Packages using uscan/watch but not in upstreams.yaml (should be added)."""

    @property
    def total_count(self) -> int:
        """Total number of packages analyzed."""
        return len(self.packages)

    @property
    def counts_by_type(self) -> dict[str, int]:
        """Counts grouped by build type."""
        return {
            "release": self.count_release,
            "milestone": self.count_milestone,
            "snapshot": self.count_snapshot,
        }

    def add_result(self, result: TypeSelectionResult) -> None:
        """Add a type selection result and update counts."""
        self.packages.append(result)

        # Update type counts
        if result.chosen_type == BuildType.RELEASE:
            self.count_release += 1
        elif result.chosen_type == BuildType.MILESTONE:
            self.count_milestone += 1
        else:
            self.count_snapshot += 1

        # Update reason counts
        reason = result.reason_code.value
        self.counts_by_reason[reason] = self.counts_by_reason.get(reason, 0) + 1

        # Update stage counts
        stage = result.cycle_stage.value
        self.counts_by_stage[stage] = self.counts_by_stage.get(stage, 0) + 1

        # Track new/defunct/retired
        if result.package_status == PackageStatus.NEW:
            self.new_packages.append(result.source_package)
        elif result.package_status == PackageStatus.DEFUNCT:
            self.defunct_packages.append(result.source_package)
        elif result.package_status == PackageStatus.RETIRED:
            self.retired_packages.append(result.source_package)
            self.count_retired += 1

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "run_id": self.run_id,
            "target": self.target,
            "ubuntu_series": self.ubuntu_series,
            "generated_at_utc": self.generated_at_utc,
            "type_mode": self.type_mode,
            "cycle_stage": self.cycle_stage.value,
            "summary": {
                "total": len(self.packages),
                "release": self.count_release,
                "milestone": self.count_milestone,
                "snapshot": self.count_snapshot,
                "retired": self.count_retired,
            },
            "counts_by_reason": self.counts_by_reason,
            "counts_by_stage": self.counts_by_stage,
            "new_packages": self.new_packages,
            "defunct_packages": self.defunct_packages,
            "retired_packages": self.retired_packages,
            "possibly_retired_packages": self.possibly_retired_packages,
            "missing_upstream": self.missing_upstream,
            "missing_packaging": self.missing_packaging,
            "needs_upstream_mapping": self.needs_upstream_mapping,
            "packages": [p.to_dict() for p in self.packages],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TypeSelectionReport:
        """Create from dictionary."""
        report = cls(
            run_id=data["run_id"],
            target=data["target"],
            ubuntu_series=data["ubuntu_series"],
            generated_at_utc=data["generated_at_utc"],
            type_mode=data["type_mode"],
            cycle_stage=CycleStage(data.get("cycle_stage", "unknown")),
        )
        summary = data.get("summary", {})
        report.count_release = summary.get("release", 0)
        report.count_milestone = summary.get("milestone", 0)
        report.count_snapshot = summary.get("snapshot", 0)
        report.count_retired = summary.get("retired", 0)
        report.counts_by_reason = data.get("counts_by_reason", {})
        report.counts_by_stage = data.get("counts_by_stage", {})
        report.new_packages = data.get("new_packages", [])
        report.defunct_packages = data.get("defunct_packages", [])
        report.retired_packages = data.get("retired_packages", [])
        report.possibly_retired_packages = data.get("possibly_retired_packages", [])
        report.missing_upstream = data.get("missing_upstream", [])
        report.missing_packaging = data.get("missing_packaging", [])
        report.needs_upstream_mapping = data.get("needs_upstream_mapping", [])
        report.packages = [
            TypeSelectionResult.from_dict(p) for p in data.get("packages", [])
        ]
        return report


def get_default_parallel_workers() -> int:
    """Get the default number of parallel workers.

    Returns half the CPU count, minimum 1.
    """
    cpu_count = os.cpu_count() or 4
    return max(1, cpu_count // 2)


def determine_cycle_stage(
    releases_repo: Path,
    series: str,
) -> CycleStage:
    """Determine if a series is pre-final or post-final.

    Pre-final: Series is in active development (status = "development")
    Post-final: Series has been released (status = "maintained", "extended maintenance", etc.)

    Args:
        releases_repo: Path to openstack/releases repository.
        series: OpenStack series name.

    Returns:
        CycleStage indicating pre-final, post-final, or unknown.
    """
    if not releases_repo or not releases_repo.exists():
        return CycleStage.UNKNOWN

    series_info = load_series_info(releases_repo)
    info = series_info.get(series)

    if info is None:
        return CycleStage.UNKNOWN

    if info.status == "development":
        return CycleStage.PRE_FINAL

    if info.status in ("maintained", "extended maintenance", "unmaintained"):
        return CycleStage.POST_FINAL

    return CycleStage.UNKNOWN


def infer_deliverable_kind(
    project: ProjectRelease | None,
    source_package: str,
    deliverable: str,
) -> tuple[DeliverableKind, KindConfidence]:
    """Infer the deliverable kind from metadata or heuristics.

    Priority:
    1. Use 'type' field from deliverable YAML if available
    2. Use naming heuristics based on project name

    Args:
        project: ProjectRelease data (may be None).
        source_package: Ubuntu source package name.
        deliverable: OpenStack project/deliverable name.

    Returns:
        Tuple of (kind, confidence).
    """
    # Try metadata first
    if project and project.type:
        type_mapping = {
            "service": DeliverableKind.SERVICE,
            "library": DeliverableKind.LIBRARY,
            "client": DeliverableKind.CLIENT,
            "horizon-plugin": DeliverableKind.HORIZON_PLUGIN,
            "tempest-plugin": DeliverableKind.TEMPEST_PLUGIN,
            "other": DeliverableKind.OTHER,
        }
        kind = type_mapping.get(project.type, DeliverableKind.OTHER)
        return kind, KindConfidence.METADATA

    # Heuristic: client packages
    if deliverable.endswith("client") or source_package.endswith("client"):
        return DeliverableKind.CLIENT, KindConfidence.HEURISTIC

    # Heuristic: oslo libraries
    if deliverable.startswith("oslo.") or deliverable.startswith("oslo-"):
        return DeliverableKind.LIBRARY, KindConfidence.HEURISTIC

    # Heuristic: python-* packages are typically libraries
    if source_package.startswith("python-") and not source_package.endswith("client"):
        return DeliverableKind.LIBRARY, KindConfidence.HEURISTIC

    # Heuristic: horizon plugins
    if "horizon" in deliverable and "plugin" in deliverable:
        return DeliverableKind.HORIZON_PLUGIN, KindConfidence.HEURISTIC
    if "-dashboard" in deliverable or "-ui" in deliverable:
        return DeliverableKind.HORIZON_PLUGIN, KindConfidence.HEURISTIC

    # Heuristic: tempest plugins
    if "tempest" in deliverable and "plugin" in deliverable:
        return DeliverableKind.TEMPEST_PLUGIN, KindConfidence.HEURISTIC

    # Heuristic: core services (known list)
    core_services = {
        "nova", "glance", "cinder", "neutron", "keystone", "swift",
        "heat", "horizon", "barbican", "designate", "ironic", "magnum",
        "manila", "mistral", "murano", "octavia", "sahara", "senlin",
        "trove", "zaqar", "placement", "aodh", "ceilometer", "gnocchi",
        "panko", "watcher", "vitrage", "blazar", "cyborg", "freezer",
        "karbor", "masakari", "monasca", "searchlight", "solum", "tacker",
        "zun",
    }
    if deliverable in core_services:
        return DeliverableKind.SERVICE, KindConfidence.HEURISTIC

    return DeliverableKind.UNKNOWN, KindConfidence.DEFAULT


def select_build_type(
    releases_repo: Path | None,
    series: str,
    source_package: str,
    deliverable: str,
    cycle_stage: CycleStage,
    force_snapshot: bool = False,
    package_status: PackageStatus = PackageStatus.ACTIVE,
    packaging_repo: Path | None = None,
    watch_config: WatchConfig | None = None,
    uscan_cache: dict | None = None,
    retirement_info: Any | None = None,
) -> TypeSelectionResult:
    """Select the build type for a package using the auto-selection matrix.

    Auto Type Selection Matrix:
    ===========================

    1. If force_snapshot is True: SNAPSHOT (SNAPSHOT_FORCED)

    2. If project not in openstack/releases: SNAPSHOT (NOT_IN_RELEASES)
       - If watch_config.fallback_for_not_in_releases, use debian/watch + uscan

    3. POST-FINAL series:
       - Any project with release: RELEASE (POST_FINAL_RELEASE)
       - No release: SNAPSHOT (PRE_FINAL_NO_RELEASE) [rare edge case]

    4. PRE-FINAL series with beta/RC/final release:
       - RELEASE (HAS_RELEASE)

    5. PRE-FINAL series with only milestone/alpha releases:
       - cycle-with-intermediary: RELEASE (INTERMEDIARY_RELEASE)
       - cycle-trailing with release: RELEASE (CYCLE_TRAILING_RELEASE)
       - others: MILESTONE (HAS_MILESTONE_ONLY)

    6. PRE-FINAL series with no releases:
       - SNAPSHOT (NO_RELEASE_YET)

    For RELEASE builds, when watch_config is provided and enabled, uscan
    is run to discover the upstream version and download URL. This information
    is stored in upstream_resolution and watch_info fields.

    Args:
        releases_repo: Path to openstack/releases repository.
        series: OpenStack series name.
        source_package: Ubuntu source package name.
        deliverable: OpenStack project/deliverable name.
        cycle_stage: Pre-computed cycle stage.
        force_snapshot: Force snapshot mode.
        package_status: Status of package (active, new, defunct).
        packaging_repo: Optional path to packaging repo for watch file access.
        watch_config: Optional watch/uscan configuration.
        uscan_cache: Optional dict for caching uscan results.
        retirement_info: Optional retirement information.

    Returns:
        TypeSelectionResult with chosen type and reasoning.
    """
    # Load project data if available
    project: ProjectRelease | None = None
    if releases_repo and releases_repo.exists():
        project = load_project_releases(releases_repo, series, deliverable)

    # Infer deliverable kind
    kind, kind_confidence = infer_deliverable_kind(project, source_package, deliverable)

    # Extract project info
    release_model = project.release_model if project else ""
    has_releases = project.has_releases() if project else False
    has_beta_rc_final = project.has_beta_rc_or_final() if project else False
    latest_version = project.get_latest_version() or "" if project else ""

    # Policy: Client libraries and oslo packages should never be built as snapshots
    # They should always use released tarballs to reduce maintenance burden
    is_client_or_library = kind in (DeliverableKind.CLIENT, DeliverableKind.LIBRARY)
    should_prevent_snapshot = is_client_or_library and not force_snapshot

    # Helper to add watch/uscan info to result
    def _add_watch_info(result: TypeSelectionResult) -> TypeSelectionResult:
        """Add watch file, uscan information, and retirement info to result."""
        # Add retirement info
        result.retirement_info = retirement_info

        if not watch_config or not watch_config.enabled:
            return result

        # Only run uscan for RELEASE builds or NOT_IN_RELEASES with fallback
        should_run_uscan = False
        if result.chosen_type == BuildType.RELEASE or (
            result.reason_code == ReasonCode.NOT_IN_RELEASES
            and watch_config.fallback_for_not_in_releases
        ):
            should_run_uscan = True

        if not should_run_uscan or not packaging_repo:
            return result

        # Import here to avoid circular imports
        from packastack.debpkg.watch import (
            DetectedWatchMode,
            UscanResult,
            cache_uscan_result,
            get_cached_uscan_result,
            parse_watch_file,
            run_uscan_dehs,
        )

        # Parse watch file
        watch_path = packaging_repo / "debian" / "watch"
        watch_result = parse_watch_file(watch_path)

        watch_info = WatchInfo(
            parsed=watch_result.mode != DetectedWatchMode.UNKNOWN,
            mode=watch_result.mode.value,
        )

        # Check cache first
        uscan_result: UscanResult | None = None
        if uscan_cache is not None:
            uscan_result = get_cached_uscan_result(source_package, uscan_cache)
            if uscan_result:
                watch_info.uscan_attempted = True

        # Run uscan if not cached and check_upstream is enabled
        if uscan_result is None and watch_config.check_upstream:
            if watch_result.mode != DetectedWatchMode.UNKNOWN:
                uscan_result = run_uscan_dehs(
                    packaging_repo,
                    timeout_seconds=watch_config.timeout_seconds,
                )
                watch_info.uscan_attempted = True

                # Cache the result
                if uscan_cache is not None:
                    cache_uscan_result(
                        source_package,
                        uscan_result,
                        uscan_cache,
                        str(packaging_repo),
                    )

        # Populate watch_info from uscan result
        if uscan_result:
            watch_info.uscan_status = uscan_result.status.value
            watch_info.uscan_error = uscan_result.error
            watch_info.upstream_version = uscan_result.upstream_version
            watch_info.packaged_version = uscan_result.debian_upstream_version
            watch_info.newer_available = uscan_result.newer_available

        # Build upstream resolution
        if result.reason_code == ReasonCode.NOT_IN_RELEASES:
            # Watch is the authority for non-OpenStack packages
            authority = UpstreamAuthority.WATCH if watch_info.parsed else UpstreamAuthority.NONE
            reason = "Package not in openstack/releases, using debian/watch"
        else:
            # openstack/releases is primary, watch supplements
            authority = UpstreamAuthority.RELEASES
            reason = "Version from openstack/releases"

        upstream_resolution = UpstreamResolution(
            authority=authority,
            watch_used=watch_info.parsed,
            uscan_used=watch_info.uscan_attempted and uscan_result is not None and uscan_result.success,
            reason=reason,
            upstream_version=watch_info.upstream_version or result.latest_version,
            download_url=uscan_result.upstream_url if uscan_result and uscan_result.success else "",
        )

        result.watch_info = watch_info
        result.upstream_resolution = upstream_resolution
        return result

    # Decision logic
    if force_snapshot:
        return _add_watch_info(TypeSelectionResult(
            source_package=source_package,
            deliverable=deliverable,
            release_model=release_model,
            deliverable_kind=kind,
            kind_confidence=kind_confidence,
            has_release_for_cycle=has_releases,
            has_beta_rc_final=has_beta_rc_final,
            latest_version=latest_version,
            cycle_stage=cycle_stage,
            chosen_type=BuildType.SNAPSHOT,
            reason_code=ReasonCode.SNAPSHOT_FORCED,
            reason_human="Snapshot mode forced by user",
            package_status=package_status,
        ))

    if project is None:
        # For clients and libraries not in releases, use debian/watch (RELEASE mode)
        # instead of falling back to SNAPSHOT
        if should_prevent_snapshot:
            return _add_watch_info(TypeSelectionResult(
                source_package=source_package,
                deliverable=deliverable,
                release_model="",
                deliverable_kind=kind,
                kind_confidence=kind_confidence,
                has_release_for_cycle=False,
                has_beta_rc_final=False,
                latest_version="",
                cycle_stage=cycle_stage,
                chosen_type=BuildType.RELEASE,
                reason_code=ReasonCode.CLIENT_LIBRARY_NO_SNAPSHOT,
                reason_human=f"Client/library package '{deliverable}' uses debian/watch (no snapshots)",
                package_status=package_status,
            ))
        return _add_watch_info(TypeSelectionResult(
            source_package=source_package,
            deliverable=deliverable,
            release_model="",
            deliverable_kind=kind,
            kind_confidence=kind_confidence,
            has_release_for_cycle=False,
            has_beta_rc_final=False,
            latest_version="",
            cycle_stage=cycle_stage,
            chosen_type=BuildType.SNAPSHOT,
            reason_code=ReasonCode.NOT_IN_RELEASES,
            reason_human=f"Project '{deliverable}' not found in openstack/releases for {series}",
            package_status=package_status,
        ))

    # Post-final series: always prefer release if available
    if cycle_stage == CycleStage.POST_FINAL:
        if has_releases:
            return _add_watch_info(TypeSelectionResult(
                source_package=source_package,
                deliverable=deliverable,
                release_model=release_model,
                deliverable_kind=kind,
                kind_confidence=kind_confidence,
                has_release_for_cycle=True,
                has_beta_rc_final=has_beta_rc_final,
                latest_version=latest_version,
                cycle_stage=cycle_stage,
                chosen_type=BuildType.RELEASE,
                reason_code=ReasonCode.POST_FINAL_RELEASE,
                reason_human=f"Post-final series: use release {latest_version}",
                package_status=package_status,
            ))
        else:
            # Rare: post-final but no release (edge case)
            # For clients/libraries, try debian/watch instead of snapshot
            if should_prevent_snapshot:
                return _add_watch_info(TypeSelectionResult(
                    source_package=source_package,
                    deliverable=deliverable,
                    release_model=release_model,
                    deliverable_kind=kind,
                    kind_confidence=kind_confidence,
                    has_release_for_cycle=False,
                    has_beta_rc_final=False,
                    latest_version="",
                    cycle_stage=cycle_stage,
                    chosen_type=BuildType.RELEASE,
                    reason_code=ReasonCode.CLIENT_LIBRARY_NO_SNAPSHOT,
                    reason_human="Post-final client/library uses debian/watch (no snapshots)",
                    package_status=package_status,
                ))
            return _add_watch_info(TypeSelectionResult(
                source_package=source_package,
                deliverable=deliverable,
                release_model=release_model,
                deliverable_kind=kind,
                kind_confidence=kind_confidence,
                has_release_for_cycle=False,
                has_beta_rc_final=False,
                latest_version="",
                cycle_stage=cycle_stage,
                chosen_type=BuildType.SNAPSHOT,
                reason_code=ReasonCode.PRE_FINAL_NO_RELEASE,
                reason_human="Post-final but no release available (unusual)",
                package_status=package_status,
            ))

    # Pre-final or unknown stage
    if has_beta_rc_final:
        # Prefer to treat final releases as RELEASE; however, if the latest
        # release is a beta or RC and there is evidence of an upstream
        # tarball (indicated by a release entry with project info), classify
        # it as a MILESTONE build instead of a full RELEASE. This captures
        # the case where a pre-release artifact (beta/rc) exists upstream
        # and we should perform a milestone-style build.
        latest_release = project.get_latest_release() if project else None

        if latest_release is not None:
            if latest_release.is_final():
                # Final release -> RELEASE
                return _add_watch_info(TypeSelectionResult(
                    source_package=source_package,
                    deliverable=deliverable,
                    release_model=release_model,
                    deliverable_kind=kind,
                    kind_confidence=kind_confidence,
                    has_release_for_cycle=True,
                    has_beta_rc_final=True,
                    latest_version=latest_version,
                    cycle_stage=cycle_stage,
                    chosen_type=BuildType.RELEASE,
                    reason_code=ReasonCode.HAS_RELEASE,
                    reason_human=f"Final release {latest_version} available",
                    package_status=package_status,
                ))

            # Beta or RC release: classify as MILESTONE only if there's
            # evidence of an upstream release artifact (projects list).
            if latest_release.is_beta() or latest_release.is_rc():
                has_upstream_artifact = bool(getattr(latest_release, "projects", None))
                if has_upstream_artifact:
                    return _add_watch_info(TypeSelectionResult(
                        source_package=source_package,
                        deliverable=deliverable,
                        release_model=release_model,
                        deliverable_kind=kind,
                        kind_confidence=kind_confidence,
                        has_release_for_cycle=True,
                        has_beta_rc_final=True,
                        latest_version=latest_version,
                        cycle_stage=cycle_stage,
                        chosen_type=BuildType.MILESTONE,
                        reason_code=ReasonCode.HAS_MILESTONE_ONLY,
                        reason_human=f"Beta/RC release {latest_version} available -> milestone",
                        package_status=package_status,
                    ))

        # Fallback: treat as RELEASE if we can't determine
        return _add_watch_info(TypeSelectionResult(
            source_package=source_package,
            deliverable=deliverable,
            release_model=release_model,
            deliverable_kind=kind,
            kind_confidence=kind_confidence,
            has_release_for_cycle=True,
            has_beta_rc_final=True,
            latest_version=latest_version,
            cycle_stage=cycle_stage,
            chosen_type=BuildType.RELEASE,
            reason_code=ReasonCode.HAS_RELEASE,
            reason_human=f"Beta/RC/final release {latest_version} available",
            package_status=package_status,
        ))

    if has_releases:
        # Has releases but no beta/RC/final (only milestones/alphas)
        # Check release model for special handling

        # cycle-with-intermediary: release at each milestone
        if release_model == "cycle-with-intermediary":
            return _add_watch_info(TypeSelectionResult(
                source_package=source_package,
                deliverable=deliverable,
                release_model=release_model,
                deliverable_kind=kind,
                kind_confidence=kind_confidence,
                has_release_for_cycle=True,
                has_beta_rc_final=False,
                latest_version=latest_version,
                cycle_stage=cycle_stage,
                chosen_type=BuildType.RELEASE,
                reason_code=ReasonCode.INTERMEDIARY_RELEASE,
                reason_human=f"cycle-with-intermediary: use release {latest_version}",
                package_status=package_status,
            ))

        # cycle-trailing: release after main cycle
        if release_model == "cycle-trailing":
            return _add_watch_info(TypeSelectionResult(
                source_package=source_package,
                deliverable=deliverable,
                release_model=release_model,
                deliverable_kind=kind,
                kind_confidence=kind_confidence,
                has_release_for_cycle=True,
                has_beta_rc_final=False,
                latest_version=latest_version,
                cycle_stage=cycle_stage,
                chosen_type=BuildType.RELEASE,
                reason_code=ReasonCode.CYCLE_TRAILING_RELEASE,
                reason_human=f"cycle-trailing: use release {latest_version}",
                package_status=package_status,
            ))

        # Default: use milestone for pre-beta releases
        return _add_watch_info(TypeSelectionResult(
            source_package=source_package,
            deliverable=deliverable,
            release_model=release_model,
            deliverable_kind=kind,
            kind_confidence=kind_confidence,
            has_release_for_cycle=True,
            has_beta_rc_final=False,
            latest_version=latest_version,
            cycle_stage=cycle_stage,
            chosen_type=BuildType.MILESTONE,
            reason_code=ReasonCode.HAS_MILESTONE_ONLY,
            reason_human=f"Only pre-beta releases (milestone {latest_version})",
            package_status=package_status,
        ))

    # No releases at all
    # For clients/libraries, use debian/watch instead of snapshot
    if should_prevent_snapshot:
        return _add_watch_info(TypeSelectionResult(
            source_package=source_package,
            deliverable=deliverable,
            release_model=release_model,
            deliverable_kind=kind,
            kind_confidence=kind_confidence,
            has_release_for_cycle=False,
            has_beta_rc_final=False,
            latest_version="",
            cycle_stage=cycle_stage,
            chosen_type=BuildType.RELEASE,
            reason_code=ReasonCode.CLIENT_LIBRARY_NO_SNAPSHOT,
            reason_human="Client/library package uses debian/watch (no snapshots)",
            package_status=package_status,
        ))
    return _add_watch_info(TypeSelectionResult(
        source_package=source_package,
        deliverable=deliverable,
        release_model=release_model,
        deliverable_kind=kind,
        kind_confidence=kind_confidence,
        has_release_for_cycle=False,
        has_beta_rc_final=False,
        latest_version="",
        cycle_stage=cycle_stage,
        chosen_type=BuildType.SNAPSHOT,
        reason_code=ReasonCode.NO_RELEASE_YET,
        reason_human="No releases yet for this series",
        package_status=package_status,
    ))


def _select_type_worker(
    args: tuple[Path | None, str, str, str, CycleStage, bool, PackageStatus, Path | None, WatchConfig | None, dict | None, Any],
) -> TypeSelectionResult:
    """Worker function for parallel type selection."""
    (
        releases_repo,
        series,
        source_package,
        deliverable,
        cycle_stage,
        force_snapshot,
        pkg_status,
        packaging_repo,
        watch_config,
        uscan_cache,
        retirement_info,
    ) = args
    return select_build_type(
        releases_repo=releases_repo,
        series=series,
        source_package=source_package,
        deliverable=deliverable,
        cycle_stage=cycle_stage,
        force_snapshot=force_snapshot,
        package_status=pkg_status,
        packaging_repo=packaging_repo,
        watch_config=watch_config,
        uscan_cache=uscan_cache,
        retirement_info=retirement_info,
    )


def find_new_and_defunct_packages(
    releases_repo: Path | None,
    series: str,
    local_packages: set[str],
) -> tuple[set[str], set[str]]:
    """Find packages that are new or defunct relative to releases repo.

    Args:
        releases_repo: Path to openstack/releases repository.
        series: OpenStack series name.
        local_packages: Set of local source package names.

    Returns:
        Tuple of (new_packages, defunct_packages).
        - new_packages: In local cache but not in releases
        - defunct_packages: In releases but not in local cache
    """
    if not releases_repo or not releases_repo.exists():
        return set(), set()

    # Get packages from releases repo
    releases_packages = load_openstack_packages(releases_repo, series)
    releases_source_pkgs = set(releases_packages.keys())

    # New: local but not in releases
    new_packages = local_packages - releases_source_pkgs

    # Defunct: in releases but not local
    defunct_packages = releases_source_pkgs - local_packages

    return new_packages, defunct_packages


def select_build_types_for_packages(
    releases_repo: Path | None,
    series: str,
    packages: list[tuple[str, str]],  # List of (source_package, deliverable)
    run_id: str,
    ubuntu_series: str,
    type_mode: str = "auto",
    force_snapshot: bool = False,
    parallel: int | None = None,
    local_packages: set[str] | None = None,
    watch_config: WatchConfig | None = None,
    packaging_repos: dict[str, Path] | None = None,
    uscan_cache_path: Path | None = None,
    retirement_checker: RetirementChecker | None = None,
    registry: Any | None = None,
    progress_callback: Callable[[int], None] | None = None,
) -> TypeSelectionReport:
    """Select build types for multiple packages.

    Args:
        releases_repo: Path to openstack/releases repository.
        series: OpenStack series name.
        packages: List of (source_package, deliverable) tuples.
        run_id: Run identifier for the report.
        ubuntu_series: Ubuntu series target.
        type_mode: "auto", "release", "milestone", or "snapshot".
        force_snapshot: Force snapshot for all packages.
        parallel: Number of parallel workers (None = default).
        local_packages: Set of all local package names for new/defunct detection.
        watch_config: Optional watch/uscan configuration.
        packaging_repos: Optional dict mapping source_package to packaging repo path.
        uscan_cache_path: Optional path to uscan cache JSON file.
        retirement_checker: Optional retirement checker instance.
        registry: Optional upstreams registry for mapping packages.
        progress_callback: Optional callback invoked with increment count as packages complete.

    Returns:
        TypeSelectionReport with all results.
    """
    # Import cache functions
    from packastack.debpkg.watch import load_uscan_cache, save_uscan_cache
    from packastack.upstream.retirement import MappingConfidence, RetirementStatus

    cycle_stage = determine_cycle_stage(releases_repo, series) if releases_repo else CycleStage.UNKNOWN

    report = TypeSelectionReport(
        run_id=run_id,
        target=series,
        ubuntu_series=ubuntu_series,
        generated_at_utc=datetime.now(UTC).isoformat(),
        type_mode=type_mode,
        cycle_stage=cycle_stage,
    )

    # Load uscan cache if path provided
    uscan_cache: dict = {}
    if uscan_cache_path:
        uscan_cache = load_uscan_cache(uscan_cache_path)
        # Drop stale cache entries when repo path is missing or changed
        if packaging_repos:
            stale_keys: list[str] = []
            for pkg, entry in uscan_cache.items():
                repo_path = packaging_repos.get(pkg)
                if not repo_path or not repo_path.exists() or (entry.packaging_repo_path and entry.packaging_repo_path != str(repo_path)):
                    stale_keys.append(pkg)
            for key in stale_keys:
                uscan_cache.pop(key, None)

    # Detect new and defunct packages
    if local_packages and releases_repo:
        new_pkgs, defunct_pkgs = find_new_and_defunct_packages(
            releases_repo, series, local_packages
        )
    else:
        new_pkgs, defunct_pkgs = set(), set()

    # Build package status map
    pkg_status_map: dict[str, PackageStatus] = {}
    for src_pkg in new_pkgs:
        pkg_status_map[src_pkg] = PackageStatus.NEW
    for src_pkg in defunct_pkgs:
        pkg_status_map[src_pkg] = PackageStatus.DEFUNCT

    # Check retirement status for packages
    retirement_map: dict[str, RetirementInfo] = {}
    if retirement_checker:
        for src_pkg, _ in packages:
            retirement_info = retirement_checker.check(src_pkg)
            retirement_map[src_pkg] = retirement_info
            # Override package status if retired
            if retirement_info.status == RetirementStatus.RETIRED:
                pkg_status_map[src_pkg] = PackageStatus.RETIRED
            if retirement_info.status == RetirementStatus.POSSIBLY_RETIRED:
                report.possibly_retired_packages.append(src_pkg)
            # Track packages needing upstream mapping (uscan-based but low confidence)
            if (
                retirement_info.mapping_confidence == MappingConfidence.LOW
                and src_pkg in new_pkgs  # Only warn for packages using uscan fallback
            ):
                report.needs_upstream_mapping.append(src_pkg)

    # Determine actual parallel workers
    workers = parallel if parallel is not None else get_default_parallel_workers()

    # If not auto mode, force all packages to specified type
    force_type: BuildType | None = None
    force_reason: ReasonCode | None = None
    if type_mode == "release":
        force_type = BuildType.RELEASE
        force_reason = ReasonCode.HAS_RELEASE
    elif type_mode == "milestone":
        force_type = BuildType.MILESTONE
        force_reason = ReasonCode.HAS_MILESTONE_ONLY
    elif type_mode == "snapshot":
        force_type = BuildType.SNAPSHOT
        force_reason = ReasonCode.SNAPSHOT_FORCED

    if force_type is not None and force_reason is not None:
        # Non-auto mode: apply forced type (can still parallelize metadata lookup)
        for source_package, deliverable in packages:
            pkg_status = pkg_status_map.get(source_package, PackageStatus.ACTIVE)
            pkg_retirement_info = retirement_map.get(source_package)

            # Handle retired packages specially
            if pkg_status == PackageStatus.RETIRED and pkg_retirement_info:
                result = TypeSelectionResult(
                    source_package=source_package,
                    deliverable=deliverable,
                    release_model="",
                    deliverable_kind=DeliverableKind.UNKNOWN,
                    kind_confidence=KindConfidence.DEFAULT,
                    has_release_for_cycle=False,
                    has_beta_rc_final=False,
                    latest_version="",
                    cycle_stage=cycle_stage,
                    chosen_type=BuildType.SNAPSHOT,  # Won't be built anyway
                    reason_code=ReasonCode.RETIRED_PROJECT,
                    reason_human=f"Project is retired: {pkg_retirement_info.description or 'RETIRED in project-config'}",
                    package_status=PackageStatus.RETIRED,
                    retirement_info=pkg_retirement_info,
                )
                report.add_result(result)
                if progress_callback:
                    progress_callback(1)
                continue

            project = None
            if releases_repo and releases_repo.exists():
                project = load_project_releases(releases_repo, series, deliverable)

            kind, kind_confidence = infer_deliverable_kind(project, source_package, deliverable)
            has_releases = project.has_releases() if project else False
            has_beta_rc_final = project.has_beta_rc_or_final() if project else False
            latest_version = project.get_latest_version() or "" if project else ""
            release_model = project.release_model if project else ""

            result = TypeSelectionResult(
                source_package=source_package,
                deliverable=deliverable,
                release_model=release_model,
                deliverable_kind=kind,
                kind_confidence=kind_confidence,
                has_release_for_cycle=has_releases,
                has_beta_rc_final=has_beta_rc_final,
                latest_version=latest_version,
                cycle_stage=cycle_stage,
                chosen_type=force_type,
                reason_code=force_reason,
                reason_human=f"Type '{type_mode}' requested by user",
                package_status=pkg_status,
                retirement_info=pkg_retirement_info,
            )
            report.add_result(result)
            if progress_callback:
                progress_callback(1)
    else:
        # Auto mode with optional parallelism
        # Determine which packages to run uscan for (respect max_projects limit)
        uscan_limit = watch_config.max_projects if watch_config and watch_config.max_projects > 0 else len(packages)

        # Separate retired packages from active packages
        retired_results: list[TypeSelectionResult] = []
        active_packages: list[tuple[str, str]] = []
        for src_pkg, deliv in packages:
            pkg_status = pkg_status_map.get(src_pkg, PackageStatus.ACTIVE)
            pkg_retirement_info = retirement_map.get(src_pkg)

            if pkg_status == PackageStatus.RETIRED and pkg_retirement_info:
                # Create result for retired package immediately
                result = TypeSelectionResult(
                    source_package=src_pkg,
                    deliverable=deliv,
                    release_model="",
                    deliverable_kind=DeliverableKind.UNKNOWN,
                    kind_confidence=KindConfidence.DEFAULT,
                    has_release_for_cycle=False,
                    has_beta_rc_final=False,
                    latest_version="",
                    cycle_stage=cycle_stage,
                    chosen_type=BuildType.SNAPSHOT,
                    reason_code=ReasonCode.RETIRED_PROJECT,
                    reason_human=f"Project is retired: {pkg_retirement_info.description or 'RETIRED in project-config'}",
                    package_status=PackageStatus.RETIRED,
                    retirement_info=pkg_retirement_info,
                )
                retired_results.append(result)
            else:
                active_packages.append((src_pkg, deliv))

        # Add retired packages to report first
        for result in retired_results:
            report.add_result(result)
            if progress_callback:
                progress_callback(1)

        if workers > 1 and len(active_packages) > 1:
            # Parallel execution
            # Note: uscan_cache is shared across workers; since we're using ThreadPoolExecutor
            # and dict operations are atomic in CPython, this is safe for our use case
            work_items = []
            uscan_count = 0
            for src_pkg, deliv in active_packages:
                pkg_repo = packaging_repos.get(src_pkg) if packaging_repos else None
                pkg_retirement_info = retirement_map.get(src_pkg)
                # Apply uscan limit
                pkg_watch_config = watch_config
                if watch_config and uscan_count >= uscan_limit:
                    # Disable uscan for packages beyond the limit
                    pkg_watch_config = WatchConfig(
                        enabled=watch_config.enabled,
                        fallback_for_not_in_releases=watch_config.fallback_for_not_in_releases,
                        check_upstream=False,  # Disable uscan
                        timeout_seconds=watch_config.timeout_seconds,
                        max_projects=watch_config.max_projects,
                    )
                else:
                    uscan_count += 1

                work_items.append((
                    releases_repo,
                    series,
                    src_pkg,
                    deliv,
                    cycle_stage,
                    force_snapshot,
                    pkg_status_map.get(src_pkg, PackageStatus.ACTIVE),
                    pkg_repo,
                    pkg_watch_config,
                    uscan_cache,
                    pkg_retirement_info,
                ))

            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(_select_type_worker, item) for item in work_items]
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    report.add_result(result)
                    if progress_callback:
                        progress_callback(1)
        else:
            # Sequential execution
            uscan_count = 0
            for source_package, deliverable in active_packages:
                pkg_status = pkg_status_map.get(source_package, PackageStatus.ACTIVE)
                pkg_repo = packaging_repos.get(source_package) if packaging_repos else None
                pkg_retirement_info = retirement_map.get(source_package)

                # Apply uscan limit
                pkg_watch_config = watch_config
                if watch_config and watch_config.max_projects > 0 and uscan_count >= uscan_limit:
                    pkg_watch_config = WatchConfig(
                        enabled=watch_config.enabled,
                        fallback_for_not_in_releases=watch_config.fallback_for_not_in_releases,
                        check_upstream=False,
                        timeout_seconds=watch_config.timeout_seconds,
                        max_projects=watch_config.max_projects,
                    )
                else:
                    uscan_count += 1

                result = select_build_type(
                    releases_repo=releases_repo,
                    series=series,
                    source_package=source_package,
                    deliverable=deliverable,
                    cycle_stage=cycle_stage,
                    force_snapshot=force_snapshot,
                    package_status=pkg_status,
                    packaging_repo=pkg_repo,
                    watch_config=pkg_watch_config,
                    uscan_cache=uscan_cache,
                    retirement_info=pkg_retirement_info,
                )
                report.add_result(result)
                if progress_callback:
                    progress_callback(1)

    # Save uscan cache if path provided (even if empty to persist pruning)
    if uscan_cache_path:
        save_uscan_cache(uscan_cache, uscan_cache_path)

    # Add defunct packages to report (they won't be in the packages list)
    for defunct_pkg in defunct_pkgs:
        if defunct_pkg not in {p.source_package for p in report.packages}:
            report.defunct_packages.append(defunct_pkg)

    # Sort packages by source_package name
    report.packages.sort(key=lambda r: r.source_package)
    report.new_packages.sort()
    report.defunct_packages.sort()
    report.retired_packages.sort()
    report.possibly_retired_packages.sort()
    report.needs_upstream_mapping.sort()

    return report
