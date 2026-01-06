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

"""Build phase implementations.

This module contains individual phase functions extracted from _run_build.
Each phase function accepts structured inputs and returns a PhaseResult.

Phase functions follow these conventions:
- Accept only the data they need (no large contexts)
- Return PhaseResult with success/failure status
- Log activity via the `activity()` function
- Use phase_error() for fatal errors that should stop the build
- Document side effects in docstrings
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from packastack.core.run import activity
from packastack.core.spinner import activity_spinner
from packastack.commands.init import _clone_or_update_project_config
from packastack.upstream.retirement import (
    RetirementChecker,
    RetirementStatus,
)

from packastack.build.errors import (
    EXIT_RETIRED_PROJECT,
    EXIT_REGISTRY_ERROR,
    phase_error,
)
from packastack.build.types import PhaseResult

if TYPE_CHECKING:
    from packastack.core.run import RunContext as RunContextType
    from packastack.upstream.registry import ResolvedUpstream, UpstreamsRegistry


@dataclass
class RetirementCheckResult:
    """Result of retirement status check.
    
    Attributes:
        is_retired: True if project is retired and build should skip
        is_possibly_retired: True if project may be retired (warning)
        upstream_project: Name of upstream project if known
        source: Source of retirement information
        description: Optional description of retirement reason
    """
    is_retired: bool = False
    is_possibly_retired: bool = False
    upstream_project: str | None = None
    source: str = ""
    description: str = ""


def check_retirement_status(
    pkg_name: str,
    package: str,
    project_config_path: Path | None,
    releases_repo: Path,
    openstack_target: str,
    include_retired: bool,
    offline: bool,
    run: "RunContextType",
) -> tuple[PhaseResult, RetirementCheckResult | None]:
    """Check if a package's upstream project is retired.
    
    This phase checks whether the upstream project has been retired
    from OpenStack development. If retired, the build should be skipped
    unless --include-retired is specified.
    
    Args:
        pkg_name: Debian source package name
        package: Project name (without python- prefix)
        project_config_path: Path to openstack/project-config repo
        releases_repo: Path to openstack/releases repo
        openstack_target: Target OpenStack series
        include_retired: If True, skip retirement checks
        offline: If True, don't clone project-config if missing
        run: RunContext for logging
        
    Returns:
        Tuple of (PhaseResult, RetirementCheckResult).
        PhaseResult.success is False if project is retired and should skip.
        
    Side Effects:
        - May clone project-config repository if missing
        - Logs retirement status via activity()
        - Logs events to run context
    """
    result = RetirementCheckResult()
    
    if include_retired:
        return PhaseResult.ok(), result
    
    if not project_config_path:
        return PhaseResult.ok(), result
    
    # Clone project-config if missing and not in offline mode
    if not project_config_path.exists() and not offline:
        with activity_spinner("retire", "Cloning openstack/project-config repository"):
            _clone_or_update_project_config(project_config_path, run)
    
    if not project_config_path.exists():
        return PhaseResult.ok(), result
    
    retirement_checker = RetirementChecker(
        project_config_path=project_config_path,
        releases_path=releases_repo,
        target_series=openstack_target,
    )
    
    # Infer deliverable name from package for retirement lookup
    deliverable_for_retire = package
    if pkg_name.startswith("python-"):
        deliverable_for_retire = pkg_name[7:]
    
    retirement_info = retirement_checker.check_retirement(pkg_name, deliverable_for_retire)
    
    if retirement_info.status == RetirementStatus.RETIRED:
        result.is_retired = True
        result.upstream_project = retirement_info.upstream_project
        result.source = retirement_info.source
        result.description = retirement_info.description
        
        activity("policy", f"Package {pkg_name} is RETIRED upstream; skipping build")
        activity("policy", f"  Upstream project: {retirement_info.upstream_project or 'unknown'}")
        activity("policy", f"  Source: {retirement_info.source}")
        if retirement_info.description:
            activity("policy", f"  Reason: {retirement_info.description}")
        activity("policy", "Use --include-retired to override")
        
        run.log_event({
            "event": "policy.retired_project",
            "package": pkg_name,
            "upstream_project": retirement_info.upstream_project,
            "source": retirement_info.source,
            "description": retirement_info.description,
        })
        
        run.write_summary(
            status="skipped",
            error=f"Package {pkg_name} upstream is retired",
            exit_code=EXIT_RETIRED_PROJECT,
        )
        
        return PhaseResult.fail(EXIT_RETIRED_PROJECT, "Project is retired"), result
    
    elif retirement_info.status == RetirementStatus.POSSIBLY_RETIRED:
        result.is_possibly_retired = True
        result.source = retirement_info.source
        
        activity("policy", f"Warning: {pkg_name} may be retired upstream (not released in 3+ cycles)")
        activity("policy", f"  Source: {retirement_info.source}")
        
        run.log_event({
            "event": "policy.possibly_retired",
            "package": pkg_name,
            "source": retirement_info.source,
        })
    
    return PhaseResult.ok(), result


@dataclass
class RegistryResolutionResult:
    """Result of upstream registry resolution.
    
    Attributes:
        registry: The loaded UpstreamsRegistry instance
        resolved: The resolved upstream configuration
        project_key: Resolved project key from registry
        is_openstack_governed: Whether package is in openstack/releases
    """
    registry: "UpstreamsRegistry | None" = None
    resolved: "ResolvedUpstream | None" = None
    project_key: str = ""
    is_openstack_governed: bool = False


def resolve_upstream_registry(
    package: str,
    pkg_name: str,
    releases_repo: Path,
    openstack_target: str,
    run: "RunContextType",
) -> tuple[PhaseResult, RegistryResolutionResult | None]:
    """Load upstreams registry and resolve upstream configuration.
    
    This phase loads the upstreams registry and resolves the upstream
    configuration for the package, including tarball preferences and
    signature verification settings.
    
    Args:
        package: Project name (without python- prefix)
        pkg_name: Debian source package name
        releases_repo: Path to openstack/releases repo
        openstack_target: Target OpenStack series
        run: RunContext for logging
        
    Returns:
        Tuple of (PhaseResult, RegistryResolutionResult).
        PhaseResult.success is False if registry loading or resolution fails.
        
    Side Effects:
        - Loads upstreams registry from disk
        - Logs registry info and resolution via activity()
        - Logs events to run context
    """
    from packastack.upstream.registry import (
        ProjectNotFoundError,
        UpstreamsRegistry,
    )
    from packastack.upstream.releases import load_openstack_packages
    
    result = RegistryResolutionResult()
    
    activity("resolve", "Loading upstreams registry")
    
    try:
        registry = UpstreamsRegistry()
        result.registry = registry
        
        run.log_event({
            "event": "registry.loaded",
            "version": registry.version,
            "override_applied": registry.override_applied,
            "override_path": registry.override_path,
        })
        
        if registry.override_applied:
            activity("resolve", f"Registry override applied: {registry.override_path}")
        
        for warning in registry.warnings:
            activity("resolve", f"Registry warning: {warning}")
            
    except Exception as e:
        activity("resolve", f"Registry error: {e}")
        run.write_summary(
            status="failed",
            error=f"Registry error: {e}",
            exit_code=EXIT_REGISTRY_ERROR,
        )
        return PhaseResult.fail(EXIT_REGISTRY_ERROR, str(e)), None
    
    # Check if project is OpenStack-governed (in openstack/releases)
    openstack_pkgs = load_openstack_packages(releases_repo, openstack_target)
    result.is_openstack_governed = (
        pkg_name in openstack_pkgs
        or package in openstack_pkgs.values()
        or package in openstack_pkgs
    )
    
    # Resolve upstream configuration from registry
    try:
        resolved_upstream = registry.resolve(package, openstack_governed=result.is_openstack_governed)
        result.resolved = resolved_upstream
        result.project_key = resolved_upstream.project
        
        upstream_config = resolved_upstream.config
        resolution_source = resolved_upstream.resolution_source
        
        activity("resolve", f"Upstream resolution: {resolution_source.value}")
        run.log_event({
            "event": "registry.resolved",
            "project": package,
            "project_key": resolved_upstream.project,
            "resolution_source": resolution_source.value,
            "upstream_host": upstream_config.upstream.host,
            "upstream_url": upstream_config.upstream.url,
        })
        
        # Log tarball and verification config
        tarball_methods = [m.value for m in upstream_config.tarball.prefer]
        activity("policy", f"Tarball prefer: {', '.join(tarball_methods)}")
        activity("policy", f"Signature mode: {upstream_config.signatures.mode.value}")
        run.log_event({
            "event": "policy.tarball_verification",
            "tarball_prefer": tarball_methods,
            "signature_mode": upstream_config.signatures.mode.value,
        })
        
    except ProjectNotFoundError as e:
        activity("resolve", f"Registry error: {e}")
        run.write_summary(
            status="failed",
            error=str(e),
            exit_code=EXIT_REGISTRY_ERROR,
        )
        return PhaseResult.fail(EXIT_REGISTRY_ERROR, str(e)), None
    
    return PhaseResult.ok(), result


@dataclass
class PolicyCheckResult:
    """Result of policy check phase.
    
    Attributes:
        snapshot_eligible: Whether snapshot build is allowed
        snapshot_reason: Reason for eligibility decision
        preferred_version: Preferred version if snapshot blocked
        forced: Whether --force was used to override
    """
    snapshot_eligible: bool = True
    snapshot_reason: str = ""
    preferred_version: str = ""
    forced: bool = False


def check_policy(
    build_type: "BuildType",
    package: str,
    releases_repo: Path,
    openstack_target: str,
    force: bool,
    run: "RunContextType",
) -> tuple[PhaseResult, PolicyCheckResult]:
    """Check build policy constraints.
    
    This phase validates that the build type is allowed by policy.
    For snapshot builds, checks if there's a released version available
    that should be used instead.
    
    Args:
        build_type: The requested build type (release, snapshot, etc.)
        package: Project name
        releases_repo: Path to openstack/releases repo
        openstack_target: Target OpenStack series
        force: If True, allow policy overrides
        run: RunContext for logging
        
    Returns:
        Tuple of (PhaseResult, PolicyCheckResult).
        PhaseResult.success is False if policy blocks the build.
        
    Side Effects:
        - Logs policy check status via activity()
        - Logs events to run context
    """
    from packastack.planning.type_selection import BuildType
    from packastack.upstream.releases import is_snapshot_eligible
    from packastack.build.errors import EXIT_POLICY_BLOCKED
    
    result = PolicyCheckResult()
    
    activity("policy", "Checking snapshot eligibility")
    
    if build_type == BuildType.SNAPSHOT:
        eligible, reason, preferred = is_snapshot_eligible(
            releases_repo, openstack_target, package
        )
        result.snapshot_eligible = eligible
        result.snapshot_reason = reason
        result.preferred_version = preferred or ""
        
        if not eligible:
            activity("policy", f"Blocked: {reason}")
            if preferred:
                activity("policy", f"Preferred version: {preferred}")
            if not force:
                run.write_summary(
                    status="failed",
                    error=f"Snapshot build blocked: {reason}",
                    exit_code=EXIT_POLICY_BLOCKED,
                )
                return PhaseResult.fail(EXIT_POLICY_BLOCKED, reason), result
            activity("policy", "Continuing with --force")
            result.forced = True
        elif "Warning" in reason:
            activity("policy", f"Warning: {reason}")
        
        run.log_event({
            "event": "policy.snapshot",
            "eligible": eligible,
            "reason": reason,
        })
    
    activity("policy", "Policy check: OK")
    return PhaseResult.ok(), result


@dataclass
class PackageIndexes:
    """Collection of loaded package indexes.
    
    Attributes:
        ubuntu: Package index from Ubuntu archive
        cloud_archive: Package index from Cloud Archive (optional)
        local_repo: Package index from local repository (optional)
    """
    ubuntu: "PackageIndex"
    cloud_archive: "PackageIndex | None" = None
    local_repo: "PackageIndex | None" = None
    
    @property
    def all_indexes(self) -> list["PackageIndex"]:
        """Return list of all available indexes."""
        indexes = [self.ubuntu]
        if self.cloud_archive:
            indexes.append(self.cloud_archive)
        if self.local_repo:
            indexes.append(self.local_repo)
        return indexes


def load_package_indexes(
    ubuntu_cache: Path,
    resolved_ubuntu: str,
    ubuntu_pockets: list[str],
    ubuntu_components: list[str],
    cloud_archive: str | None,
    cache_root: Path,
    local_repo_root: Path | None,
    arch: str,
    run: "RunContextType",
) -> tuple[PhaseResult, PackageIndexes | None]:
    """Load package indexes from Ubuntu, Cloud Archive, and local repository.
    
    This phase loads package indexes needed for dependency resolution.
    Indexes are loaded from cached data on disk.
    
    Args:
        ubuntu_cache: Path to Ubuntu archive cache
        resolved_ubuntu: Ubuntu series codename (e.g., 'noble')
        ubuntu_pockets: Ubuntu pockets to load (e.g., ['release', 'updates'])
        ubuntu_components: Ubuntu components to load (e.g., ['main', 'universe'])
        cloud_archive: Cloud Archive series name (optional)
        cache_root: Root cache directory
        local_repo_root: Path to local apt repository (optional)
        arch: Host architecture for local repo indexing
        run: RunContext for logging
        
    Returns:
        Tuple of (PhaseResult, PackageIndexes).
        PhaseResult.success is False if critical index loading fails.
        
    Side Effects:
        - Loads package indexes from disk
        - Logs index sizes via activity()
        - Logs events to run context
    """
    from packastack.apt.packages import (
        PackageIndex,
        load_cloud_archive_index,
        load_local_repo_index,
        load_package_index,
    )
    
    # Load Ubuntu index
    with activity_spinner("plan", "Loading package indexes"):
        ubuntu_index = load_package_index(ubuntu_cache, resolved_ubuntu, ubuntu_pockets, ubuntu_components)
    activity("plan", f"Ubuntu index: {len(ubuntu_index.packages)} packages")
    run.log_event({"event": "plan.ubuntu_index", "count": len(ubuntu_index.packages)})
    
    # Load cloud archive index if specified
    ca_index: PackageIndex | None = None
    if cloud_archive:
        with activity_spinner("plan", "Loading cloud archive index"):
            ca_index = load_cloud_archive_index(cache_root, resolved_ubuntu, cloud_archive)
        activity("plan", f"Cloud archive index: {len(ca_index.packages)} packages")
        run.log_event({"event": "plan.cloud_archive_index", "count": len(ca_index.packages)})
    
    # Load local repository index
    local_index: PackageIndex | None = None
    if local_repo_root and local_repo_root.exists():
        local_index = load_local_repo_index(local_repo_root, arch=arch)
        if local_index.packages:
            activity("plan", f"Local repo index: {len(local_index.packages)} packages")
            run.log_event({"event": "plan.local_repo_index", "count": len(local_index.packages)})
    
    indexes = PackageIndexes(
        ubuntu=ubuntu_index,
        cloud_archive=ca_index,
        local_repo=local_index,
    )
    
    return PhaseResult.ok(), indexes


@dataclass
class ToolCheckResult:
    """Result of required tools check.
    
    Attributes:
        is_complete: True if all required tools are available
        missing_tools: List of missing tool names
        error_message: Formatted message about missing tools
    """
    is_complete: bool = True
    missing_tools: list[str] | None = None
    error_message: str = ""


def check_tools(
    need_sbuild: bool,
    run: "RunContextType",
) -> tuple[PhaseResult, ToolCheckResult]:
    """Check that all required build tools are available.
    
    This phase validates that git, gbp, and optionally sbuild
    are available in the system PATH.
    
    Args:
        need_sbuild: Whether sbuild is required (for binary builds)
        run: RunContext for logging
        
    Returns:
        Tuple of (PhaseResult, ToolCheckResult).
        PhaseResult.success is False if required tools are missing.
        
    Side Effects:
        - Logs tool check status via activity()
        - Writes summary on failure
    """
    from packastack.build.tools import check_required_tools, get_missing_tools_message
    from packastack.build.errors import EXIT_TOOL_MISSING
    
    activity("plan", "Checking required tools")
    
    tool_check = check_required_tools(need_sbuild=need_sbuild)
    
    if not tool_check.is_complete():
        msg = get_missing_tools_message(tool_check.missing)
        activity("plan", "Missing required tools:")
        for line in msg.split("\n"):
            activity("plan", f"  {line}")
        
        result = ToolCheckResult(
            is_complete=False,
            missing_tools=tool_check.missing,
            error_message=msg,
        )
        
        run.write_summary(
            status="failed",
            error="Missing required tools",
            exit_code=EXIT_TOOL_MISSING,
        )
        return PhaseResult.fail(EXIT_TOOL_MISSING, "Missing required tools"), result
    
    activity("plan", "All required tools available")
    return PhaseResult.ok(), ToolCheckResult(is_complete=True)


@dataclass
class SchrootSetupResult:
    """Result of schroot setup phase.
    
    Attributes:
        schroot_name: Name of the schroot (empty if not needed)
        created: True if schroot was created during this run
        skipped: True if schroot setup was skipped (not needed)
    """
    schroot_name: str = ""
    created: bool = False
    skipped: bool = False


def ensure_schroot_ready(
    binary: bool,
    builder: str,
    resolved_ubuntu: str,
    mirror: str,
    components: list[str],
    offline: bool,
    run: "RunContextType",
) -> tuple[PhaseResult, SchrootSetupResult]:
    """Ensure schroot exists for sbuild-based binary builds.
    
    This phase checks if an sbuild schroot is needed for binary builds,
    and if so, ensures it exists or creates it.
    
    Args:
        binary: Whether binary builds are requested
        builder: Build tool to use ('sbuild' or other)
        resolved_ubuntu: Ubuntu series codename (e.g., 'noble')
        mirror: Ubuntu mirror URL
        components: Ubuntu components (e.g., ['main', 'universe'])
        offline: If True, don't create schroot if missing
        run: RunContext for logging
        
    Returns:
        Tuple of (PhaseResult, SchrootSetupResult).
        PhaseResult.success is False if schroot is needed but can't be created.
        
    Side Effects:
        - May create a new schroot
        - Logs schroot status via activity()
        - Logs events to run context
    """
    from packastack.build.schroot import SchrootConfig, ensure_schroot, get_schroot_name
    from packastack.target.arch import get_host_arch
    from packastack.build.errors import EXIT_TOOL_MISSING, EXIT_CONFIG_ERROR
    
    result = SchrootSetupResult()
    
    # Only needed for sbuild binary builds
    if not binary or builder != "sbuild":
        result.skipped = True
        return PhaseResult.ok(), result
    
    schroot_name = get_schroot_name(resolved_ubuntu, get_host_arch())
    result.schroot_name = schroot_name
    
    schroot_config = SchrootConfig.from_lists(
        series=resolved_ubuntu,
        arch=get_host_arch(),
        mirror=mirror,
        components=components,
    )
    
    schroot_result = ensure_schroot(config=schroot_config, offline=offline)
    
    if not schroot_result.exists:
        activity("plan", f"Schroot error: {schroot_result.error}")
        exit_code = EXIT_TOOL_MISSING if "not found" in schroot_result.error else EXIT_CONFIG_ERROR
        run.write_summary(
            status="failed",
            error=schroot_result.error,
            exit_code=exit_code,
        )
        return PhaseResult.fail(exit_code, schroot_result.error), result
    
    if schroot_result.created:
        activity("plan", f"Created schroot: {schroot_result.name}")
        result.created = True
    
    run.log_event({
        "event": "schroot.ready",
        "name": schroot_result.name,
        "created": schroot_result.created,
    })
    
    return PhaseResult.ok(), result
