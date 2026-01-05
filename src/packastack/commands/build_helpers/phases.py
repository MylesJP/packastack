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

from packastack.commands.build_helpers.errors import (
    EXIT_RETIRED_PROJECT,
    EXIT_REGISTRY_ERROR,
    phase_error,
)
from packastack.commands.build_helpers.types import PhaseResult

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
