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

"""Single package build phases.

This module contains the extracted phase functions for building a single package.
Each phase function takes a context object and returns a result, enabling:
- Testability: Each phase can be unit tested independently
- Readability: Clear phase boundaries with well-defined inputs/outputs
- Maintainability: Phases can be modified without understanding the entire flow

Phase functions follow the pattern:
    def phase_name(ctx: BuildContext, ...) -> PhaseResult | tuple[PhaseResult, Data]

Where PhaseResult contains success/exit_code and Data contains phase-specific outputs.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packastack.build.errors import (
    EXIT_BUILD_FAILED,
    EXIT_CONFIG_ERROR,
    EXIT_FETCH_FAILED,
    EXIT_MISSING_PACKAGES,
    EXIT_PATCH_FAILED,
    EXIT_POLICY_BLOCKED,
    EXIT_RESUME_ERROR,
    EXIT_SUCCESS,
    EXIT_TOOL_MISSING,
)
from packastack.build.git_helpers import (
    GitCommitError,
    extract_upstream_version,
    git_commit,
    maybe_enable_sphinxdoc,
)
from packastack.build.tarball import _fetch_release_tarball
from packastack.core.run import activity
from packastack.core.spinner import activity_spinner
from packastack.debpkg.gbp import run_command
from packastack.logs.deps_satisfaction import write_dependency_satisfaction_reports
from packastack.upstream.gitfetch import GitFetcher

if TYPE_CHECKING:
    from packastack.apt.packages import PackageIndex
    from packastack.build.provenance import BuildProvenance
    from packastack.core.run import RunContext
    from packastack.planning.type_selection import BuildType
    from packastack.upstream.source import SnapshotAcquisitionResult, UpstreamSource


# =============================================================================
# Phase Result Types
# =============================================================================


@dataclass
class PhaseResult:
    """Result of a build phase execution."""

    success: bool
    exit_code: int = EXIT_SUCCESS
    error: str = ""

    @classmethod
    def ok(cls) -> PhaseResult:
        """Create a successful result."""
        return cls(success=True, exit_code=EXIT_SUCCESS)

    @classmethod
    def fail(cls, exit_code: int, error: str = "") -> PhaseResult:
        """Create a failed result."""
        return cls(success=False, exit_code=exit_code, error=error)


@dataclass
class FetchResult:
    """Result of the fetch phase."""

    pkg_repo: Path | None = None
    workspace: Path | None = None
    watch_updated: bool = False
    signing_key_updated: bool = False


@dataclass
class PrepareResult:
    """Result of the prepare phase."""

    upstream_tarball: Path | None = None
    signature_verified: bool = False
    signature_warning: str = ""
    git_sha: str = ""
    git_date: str = ""
    snapshot_result: SnapshotAcquisitionResult | None = None
    new_version: str = ""


@dataclass
class ValidateDepsResult:
    """Result of the validate-deps phase."""

    missing_deps: list[str] = field(default_factory=list)
    buildable_deps: list[str] = field(default_factory=list)
    upstream_repo_path: Path | None = None


@dataclass
class BuildResult:
    """Result of the build phase."""

    source_success: bool = False
    binary_success: bool = False
    artifacts: list[Path] = field(default_factory=list)
    dsc_file: Path | None = None
    changes_file: Path | None = None
    sbuild_result: Any = None  # SbuildResult, kept as Any to avoid circular import


# =============================================================================
# Launchpad Bug Key Resolution
# =============================================================================


def _series_commit_name(series: str | None) -> str:
    """Return the short series name used in package commit subjects."""

    if not series:
        return "Unknown"
    return series.split()[0].capitalize()


def resolve_lp_bug_key(
    build_type: str, is_library: bool, upstream_version: str
) -> str | None:
    """Resolve the Launchpad bug config key type for a build.

    Maps a build scenario to the appropriate ``launchpad_bugs`` config key:

    * ``snapshot``          -> ``milestone2``
    * ``release`` + library -> ``client-lib-release``
    * ``release`` + RC ver  -> ``service-rc1``
    * ``release`` (other)   -> ``service-release``

    Returns the key suffix (without the series prefix) or *build_type* itself
    for unrecognised types.
    """
    if build_type == "snapshot":
        return "milestone2"
    if build_type == "release":
        if is_library:
            return "client-lib-release"
        if "rc" in upstream_version.lower():
            return "service-rc1"
        return "service-release"
    return build_type


# =============================================================================
# Build Context
# =============================================================================


@dataclass
class SingleBuildContext:
    """Context for a single package build.

    This collects all the resolved values needed across phases,
    reducing parameter passing between functions.
    """

    # Identity
    pkg_name: str
    package: str  # Project name (without python- prefix)
    run: RunContext

    # Targets
    target: str
    openstack_target: str
    ubuntu_series: str
    resolved_ubuntu: str
    cloud_archive: str

    # Build configuration
    build_type: BuildType
    build_type_str: str
    binary: bool
    builder: str
    force: bool
    offline: bool
    skip_repo_regen: bool
    no_spinner: bool
    build_deps: bool
    archive_deps: bool
    min_version_policy: str
    dep_report: bool
    fail_on_cloud_archive_required: bool
    fail_on_mir_required: bool
    update_control_min_versions: bool
    normalize_to_prev_lts_floor: bool
    dry_run_control_edit: bool

    # Paths
    paths: dict[str, Path]
    ai_enabled: bool = True
    cfg: dict[str, Any] | None = None
    workspace: Path | None = None
    pkg_repo: Path | None = None
    local_repo: Path | None = None
    tarball_cache_base: Path | None = None

    # Resolved upstream
    upstream_config: Any = None
    upstream: UpstreamSource | None = None
    resolution_source: Any = None
    prev_series: str | None = None

    # Package indexes
    ubuntu_index: PackageIndex | None = None
    ca_index: PackageIndex | None = None
    local_index: PackageIndex | None = None
    current_lts_codename: str | None = None
    current_lts_index: PackageIndex | None = None
    openstack_pkgs: dict[str, str] | None = None

    # Schroot
    schroot_name: str | None = None

    # Provenance
    provenance: BuildProvenance | None = None
    dependency_reports: dict[str, Path] | None = None
    upstream_min_versions: dict[str, str] | None = None

    # Resume support
    resume_workspace_path: Path | None = None


# =============================================================================
# Setup: Create and populate SingleBuildContext
# =============================================================================


@dataclass
class SetupInputs:
    """Inputs for setting up a single package build context.

    These are the values available at the start of the per-package loop,
    before any package-specific resolution has been done.
    """

    # Package identity
    pkg_name: str

    # Request values
    target: str
    ubuntu_series: str
    cloud_archive: str
    build_type_str: str
    binary: bool
    builder: str
    force: bool
    offline: bool
    skip_repo_regen: bool
    no_spinner: bool
    build_deps: bool
    archive_deps: bool
    min_version_policy: str
    dep_report: bool
    include_retired: bool
    fail_on_cloud_archive_required: bool
    fail_on_mir_required: bool
    update_control_min_versions: bool
    normalize_to_prev_lts_floor: bool
    dry_run_control_edit: bool

    # Resolved values from planning
    resolved_build_type_str: str

    # Paths and config
    paths: dict[str, Path]
    cfg: dict[str, Any]

    # Run context
    run: Any  # RunContext

    # Resume support
    resume_workspace_path: Path | None = None
    ai_enabled: bool = True


def setup_build_context(inputs: SetupInputs) -> tuple[PhaseResult, SingleBuildContext | None]:
    """Set up the build context by running pre-build phases.

    This function runs phases 1-6:
    1. Retirement check
    2. Registry resolution
    3. Policy check
    4. Load package indexes
    5. Check tools
    6. Ensure schroot ready

    If any phase fails, returns (failure_result, None).
    On success, returns (ok_result, populated_context).

    Args:
        inputs: Setup inputs with request values and paths.

    Returns:
        Tuple of (PhaseResult, SingleBuildContext or None).
    """
    from packastack.build.phases import (
        check_retirement_status,
        check_tools,
        ensure_schroot_ready,
        load_package_indexes,
        resolve_upstream_registry,
    )
    from packastack.build.provenance import create_provenance
    from packastack.build.type_resolution import (
        build_type_from_string as _build_type_from_string,
    )
    from packastack.build.type_resolution import (
        resolve_build_type_auto,
    )
    from packastack.planning.type_selection import BuildType
    from packastack.target.arch import get_host_arch
    from packastack.target.distro_info import get_current_lts
    from packastack.target.series import resolve_series
    from packastack.upstream.releases import (
        get_current_development_series,
        get_previous_series,
        is_snapshot_eligible,
        load_openstack_packages,
    )
    from packastack.upstream.source import select_upstream_source

    run = inputs.run
    paths = inputs.paths
    cfg = inputs.cfg
    pkg_name = inputs.pkg_name

    # Resolve series
    resolved_ubuntu = resolve_series(inputs.ubuntu_series)
    releases_repo = paths["openstack_releases_repo"]
    if inputs.target == "devel":
        openstack_target = get_current_development_series(releases_repo) or inputs.target
    else:
        openstack_target = inputs.target
    local_repo = paths["local_apt_repo"]

    # Derive project name from releases metadata when possible
    openstack_pkgs = load_openstack_packages(releases_repo, openstack_target)
    package = openstack_pkgs.get(pkg_name)
    if not package:
        package = pkg_name[7:] if pkg_name.startswith("python-") else pkg_name

    activity("resolve", f"Package: {pkg_name}")
    run.log_event({"event": "resolve.package", "name": pkg_name})

    # -------------------------------------------------------------------------
    # Phase 1: Retirement check
    # -------------------------------------------------------------------------
    project_config_path = paths.get("openstack_project_config")
    retirement_result, _retirement_info = check_retirement_status(
        pkg_name=pkg_name,
        package=package,
        project_config_path=project_config_path,
        releases_repo=releases_repo,
        openstack_target=openstack_target,
        include_retired=inputs.include_retired,
        offline=inputs.offline,
        run=run,
    )
    if not retirement_result.success:
        return retirement_result, None

    # -------------------------------------------------------------------------
    # Phase 2: Registry resolution
    # -------------------------------------------------------------------------
    registry_result, registry_info = resolve_upstream_registry(
        package=package,
        pkg_name=pkg_name,
        releases_repo=releases_repo,
        openstack_target=openstack_target,
        run=run,
    )
    if not registry_result.success:
        return registry_result, None

    # Extract values from registry resolution result
    registry = registry_info.registry
    resolved_upstream = registry_info.resolved
    upstream_config = resolved_upstream.config
    resolution_source = resolved_upstream.resolution_source

    # Build type: if caller left it as "auto", resolve per-package here
    if inputs.resolved_build_type_str == "auto":
        try:
            # Determine the correct project name for checking releases
            # Priority: 1) deliverable if exists in releases, 2) pkg_name, 3) URL-derived name
            from packastack.upstream.releases import load_project_releases

            deliverable_name = upstream_config.release_source.deliverable

            # Try deliverable first
            if deliverable_name:
                test_releases = load_project_releases(releases_repo, openstack_target, deliverable_name)
                if test_releases:
                    # Deliverable exists in releases, use it
                    pass
                else:
                    # Deliverable doesn't exist, try pkg_name
                    test_releases = load_project_releases(releases_repo, openstack_target, pkg_name)
                    if test_releases:
                        deliverable_name = pkg_name
            else:
                # No deliverable set, use pkg_name
                deliverable_name = pkg_name

            chosen, reason = resolve_build_type_auto(
                releases_repo=releases_repo,
                series=openstack_target,
                source_package=pkg_name,
                deliverable=deliverable_name,
                offline=inputs.offline,
                run=run,
            )
        except Exception:
            # Propagate exceptions as fatal for this package
            raise
        build_type = chosen
        run.log_event({
            "event": "resolve.build_type",
            "type": build_type.value,
            "auto_reason": reason,
        })
        activity("resolve", f"Chosen build type: {build_type.value} (auto: {reason})")
    else:
        build_type = _build_type_from_string(inputs.resolved_build_type_str)
        run.log_event({"event": "resolve.build_type", "type": build_type.value})

    # Get previous series
    prev_series = get_previous_series(releases_repo, openstack_target)
    if prev_series:
        activity("resolve", f"Previous series: {prev_series}")
    run.log_event({"event": "resolve.prev_series", "prev": prev_series, "target": openstack_target})

    # Initialize provenance
    provenance = create_provenance(pkg_name, run.run_id)
    provenance.registry_version = registry.version
    provenance.resolution_source = resolution_source.value
    provenance.project_key = resolved_upstream.project
    provenance.build_type = build_type.value
    provenance.upstream.url = upstream_config.upstream.url
    provenance.upstream.branch = upstream_config.upstream.default_branch
    provenance.release_source.type = upstream_config.release_source.type.value
    provenance.release_source.deliverable = upstream_config.release_source.deliverable
    if registry.override_applied:
        provenance.registry_override_path = registry.override_path

    # -------------------------------------------------------------------------
    # Phase 3: Policy check
    # -------------------------------------------------------------------------
    activity("policy", "Checking snapshot eligibility")

    if build_type == BuildType.SNAPSHOT:
        from packastack.upstream.registry import ReleaseSourceType

        # Projects that don't use openstack/releases for version discovery
        # (e.g. git_tags, pypi, pinned) are always eligible for snapshots
        # since the openstack/releases eligibility check is irrelevant.
        if upstream_config.release_source.type != ReleaseSourceType.OPENSTACK_RELEASES:
            eligible = True
            reason = (
                f"Snapshots allowed (release source: "
                f"{upstream_config.release_source.type.value})"
            )
            preferred = None
            activity("policy", f"Note: {reason}")
        else:
            # Determine the correct project name for checking releases
            # Priority: deliverable if exists in releases, otherwise pkg_name
            from packastack.upstream.releases import load_project_releases

            deliverable_name = upstream_config.release_source.deliverable
            project_name = deliverable_name

            # Check if deliverable exists in releases
            if deliverable_name:
                test_releases = load_project_releases(releases_repo, openstack_target, deliverable_name)
                if not test_releases:
                    # Deliverable doesn't exist, try pkg_name
                    test_releases = load_project_releases(releases_repo, openstack_target, pkg_name)
                    if test_releases:
                        project_name = pkg_name
            else:
                project_name = pkg_name

            eligible, reason, preferred = is_snapshot_eligible(releases_repo, openstack_target, project_name)

        if not eligible:
            activity("policy", f"Blocked: {reason}")
            if preferred:
                activity("policy", f"Preferred version: {preferred}")
            if not inputs.force:
                run.write_summary(
                    status="failed",
                    error=f"Snapshot build blocked: {reason}",
                    exit_code=EXIT_POLICY_BLOCKED,
                )
                return PhaseResult.fail(EXIT_POLICY_BLOCKED, f"Snapshot build blocked: {reason}"), None
            activity("policy", "Continuing with --force")
        elif "Warning" in reason:
            activity("policy", f"Warning: {reason}")
        run.log_event({"event": "policy.snapshot", "eligible": eligible, "reason": reason})

    activity("policy", "Policy check: OK")

    # -------------------------------------------------------------------------
    # Phase 4: Load package indexes
    # -------------------------------------------------------------------------
    pockets = cfg.get("defaults", {}).get("ubuntu_pockets", ["release", "updates", "security"])
    components = cfg.get("defaults", {}).get("ubuntu_components", ["main", "universe"])
    result, indexes = load_package_indexes(
        ubuntu_cache=paths["ubuntu_archive_cache"],
        resolved_ubuntu=resolved_ubuntu,
        ubuntu_pockets=pockets,
        ubuntu_components=components,
        cloud_archive=inputs.cloud_archive,
        cache_root=paths["cache_root"],
        local_repo_root=paths.get("local_apt_repo"),
        arch=get_host_arch(),
        run=run,
    )
    if not result.success:
        return result, None

    ubuntu_index = indexes.ubuntu
    ca_index = indexes.cloud_archive
    local_index = indexes.local_repo

    # Load current LTS index for dependency satisfaction checks
    from packastack.apt.packages import load_package_index

    current_lts = get_current_lts()
    current_lts_codename = current_lts.codename if current_lts else None
    current_lts_index = None
    if current_lts_codename:
        try:
            current_lts_index = load_package_index(paths["ubuntu_archive_cache"], current_lts_codename, pockets, components)
            activity("plan", f"Current LTS index ({current_lts_codename}): {len(current_lts_index.packages)} packages")
            run.log_event({"event": "plan.current_lts_index", "series": current_lts_codename, "count": len(current_lts_index.packages)})
        except Exception as exc:
            activity("warn", f"Failed to load current LTS index ({current_lts_codename}): {exc}")
            run.log_event({"event": "plan.current_lts_index_failed", "series": current_lts_codename, "error": str(exc)})

    activity("plan", f"OpenStack packages: {len(openstack_pkgs)} in {openstack_target}")

    # -------------------------------------------------------------------------
    # Phase 5: Check tools
    # -------------------------------------------------------------------------
    result, _ = check_tools(need_sbuild=inputs.binary, run=run)
    if not result.success:
        return result, None

    # -------------------------------------------------------------------------
    # Phase 6: Ensure schroot ready
    # -------------------------------------------------------------------------
    mirror = cfg.get("mirrors", {}).get("ubuntu_archive", "http://archive.ubuntu.com/ubuntu")
    result, schroot_info = ensure_schroot_ready(
        binary=inputs.binary,
        builder=inputs.builder,
        resolved_ubuntu=resolved_ubuntu,
        mirror=mirror,
        components=components,
        offline=inputs.offline,
        run=run,
    )
    if not result.success:
        return result, None
    schroot_name = schroot_info.schroot_name

    # Select upstream source
    upstream = select_upstream_source(
        releases_repo,
        openstack_target,
        package,
        build_type,
    )

    # Calculate tarball cache base
    tarball_cache_base = paths.get("upstream_tarballs")
    if tarball_cache_base is None:
        tarball_cache_base = paths["cache_root"] / "upstream-tarballs"

    # -------------------------------------------------------------------------
    # Build the context
    # -------------------------------------------------------------------------
    ctx = SingleBuildContext(
        pkg_name=pkg_name,
        package=package,
        run=run,
        target=inputs.target,
        openstack_target=openstack_target,
        ubuntu_series=inputs.ubuntu_series,
        resolved_ubuntu=resolved_ubuntu,
        cloud_archive=inputs.cloud_archive,
        build_type=build_type,
        build_type_str=build_type.value,
        binary=inputs.binary,
        builder=inputs.builder,
        force=inputs.force,
        offline=inputs.offline,
        skip_repo_regen=inputs.skip_repo_regen,
        no_spinner=inputs.no_spinner,
        build_deps=inputs.build_deps,
        archive_deps=inputs.archive_deps,
        min_version_policy=inputs.min_version_policy,
        dep_report=inputs.dep_report,
        fail_on_cloud_archive_required=inputs.fail_on_cloud_archive_required,
        fail_on_mir_required=inputs.fail_on_mir_required,
        update_control_min_versions=inputs.update_control_min_versions,
        normalize_to_prev_lts_floor=inputs.normalize_to_prev_lts_floor,
        dry_run_control_edit=inputs.dry_run_control_edit,
        ai_enabled=inputs.ai_enabled,
        paths=paths,
        cfg=cfg,
        local_repo=local_repo,
        tarball_cache_base=tarball_cache_base,
        upstream_config=upstream_config,
        upstream=upstream,
        resolution_source=resolution_source,
        prev_series=prev_series,
        current_lts_codename=current_lts_codename,
        ubuntu_index=ubuntu_index,
        ca_index=ca_index,
        local_index=local_index,
        current_lts_index=current_lts_index,
        openstack_pkgs=openstack_pkgs,
        schroot_name=schroot_name,
        provenance=provenance,
        resume_workspace_path=inputs.resume_workspace_path,
    )

    return PhaseResult.ok(), ctx


# =============================================================================
# Phase: Fetch Packaging Repository
# =============================================================================


def fetch_packaging_repo(
    ctx: SingleBuildContext,
    workspace_ref: Any = None,
) -> tuple[PhaseResult, FetchResult]:
    """Clone/update the packaging repository and prepare for build.

    This phase:
    1. Creates the workspace directory
    2. Clones or updates the packaging repository
    3. Protects packaging-only files from merge
    4. Updates debian/watch version and signing keys
    5. Enables sphinxdoc addon if needed

    Args:
        ctx: Build context with resolved configuration.
        workspace_ref: Optional callback to receive workspace path.

    Returns:
        Tuple of (PhaseResult, FetchResult).
    """
    result = FetchResult()
    run = ctx.run

    # If resuming from existing workspace, use it instead of creating new one
    if ctx.resume_workspace_path and ctx.resume_workspace_path.exists():
        workspace = ctx.resume_workspace_path
        activity("resume", f"Using existing workspace: {workspace}")

        # Verify the package repo exists in the workspace
        pkg_repo = workspace / ctx.pkg_name
        if not pkg_repo.exists() or not (pkg_repo / ".git").is_dir():
            error = f"Resume workspace does not contain valid git repository at {pkg_repo}"
            activity("resume", f"ERROR: {error}")
            run.write_summary(status="failed", error=error, exit_code=EXIT_RESUME_ERROR)
            return PhaseResult.fail(EXIT_RESUME_ERROR, error), result

        result.workspace = workspace
        result.pkg_repo = pkg_repo
        ctx.workspace = workspace
        ctx.pkg_repo = pkg_repo

        # Mirror logs into existing workspace
        with contextlib.suppress(Exception):
            run.add_log_mirror(workspace / "logs")

        activity("resume", f"Resuming from: {pkg_repo}")
        run.log_event({
            "event": "resume.workspace_reused",
            "workspace": str(workspace),
            "pkg_repo": str(pkg_repo),
        })

        # Skip the rest of fetch - we're using existing state
        return PhaseResult.ok(), result

    # Create workspace under build/{pkg}/{build_id}/
    build_root = ctx.paths.get("build_root", ctx.paths["cache_root"] / "build")
    workspace = build_root / ctx.pkg_name / run.build_id
    workspace.mkdir(parents=True, exist_ok=True)
    if workspace_ref:
        workspace_ref(workspace)

    result.workspace = workspace
    ctx.workspace = workspace

    # Relocate RunContext logs into the package build directory.
    # _relocate() handles failures gracefully — if the move fails, file
    # handles are re-opened at the original staging location.
    run.relocate_to_package_dir(ctx.pkg_name)

    # Clone packaging repo
    launchpad_username = ctx.cfg.get("git", {}).get("launchpad_username")
    repo_name_overrides = ctx.cfg.get("repo_name_overrides", {})
    fetcher = GitFetcher(
        launchpad_username=launchpad_username,
        repo_name_overrides=repo_name_overrides,
    )
    with activity_spinner("fetch", f"Cloning packaging repository: {ctx.pkg_name}"):
        fetch_result = fetcher.fetch_and_checkout(
            ctx.pkg_name,
            workspace,
            ctx.resolved_ubuntu,
            ctx.openstack_target,
            offline=ctx.offline,
        )

    if fetch_result.error:
        activity("fetch", f"Clone failed: {fetch_result.error}")
        run.write_summary(status="failed", error=fetch_result.error, exit_code=EXIT_FETCH_FAILED)
        return PhaseResult.fail(EXIT_FETCH_FAILED, fetch_result.error), result

    pkg_repo = fetch_result.path
    result.pkg_repo = pkg_repo
    ctx.pkg_repo = pkg_repo

    activity("fetch", f"Cloned to: {pkg_repo}")
    activity("fetch", f"Branches: {', '.join(fetch_result.branches[:5])}...")
    run.log_event(
        {
            "event": "fetch.complete",
            "path": str(pkg_repo),
            "branches": fetch_result.branches,
            "cloned": fetch_result.cloned,
            "updated": fetch_result.updated,
        }
    )

    # Check debian/watch for mismatch with registry (advisory only)
    from packastack.debpkg.watch import (
        check_watch_mismatch,
        fix_malformed_watch_opts,
        fix_oslo_watch_pattern,
        has_upstream_signing_key,
        parse_watch_file,
        remove_pgp_options_from_watch,
        restore_pgp_options_to_watch,
        update_signing_key,
        upgrade_watch_version,
        verify_signing_key_with_uscan,
    )

    watch_path = pkg_repo / "debian" / "watch"

    watch_result = parse_watch_file(watch_path)
    if watch_result.mode.value != "unknown" and ctx.upstream_config:
        mismatch = check_watch_mismatch(
            ctx.pkg_name,
            watch_result,
            ctx.upstream_config.upstream.host,
            ctx.upstream_config.upstream.url,
        )
        if mismatch:
            activity(
                "policy",
                f"debian/watch mismatch (warn): registry={ctx.upstream_config.upstream.host} "
                f"watch={mismatch.watch_mode.value}",
            )
            run.log_event(
                {
                    "event": "policy.watch_mismatch",
                    "package": ctx.pkg_name,
                    "registry_host": ctx.upstream_config.upstream.host,
                    "watch_mode": mismatch.watch_mode.value,
                    "watch_url": mismatch.watch_url,
                }
            )
            # Record in provenance
            if ctx.provenance:
                ctx.provenance.watch_mismatch.detected = True
                ctx.provenance.watch_mismatch.watch_mode = mismatch.watch_mode.value
                ctx.provenance.watch_mismatch.watch_url = mismatch.watch_url
                ctx.provenance.watch_mismatch.registry_mode = ctx.upstream_config.upstream.host
                ctx.provenance.watch_mismatch.message = mismatch.message

    watch_updated = False
    watch_changes: list[str] = []
    signing_key_updated = False

    # Fix malformed opts= quoting before any other watch processing.
    # Some packaging repos have options outside the closing quote which
    # causes uscan to reject the line entirely.
    if fix_malformed_watch_opts(watch_path):
        activity("prepare", "Fixed malformed opts= quoting in debian/watch")
        watch_updated = True
        watch_changes.append("fix malformed opts= quoting")

    if upgrade_watch_version(watch_path):
        activity("prepare", "Updated debian/watch to version=4")
        watch_updated = True
        watch_changes.append("upgrade to version=4")

    # Fix oslo.* watch patterns to accept both oslo.* and oslo_* naming
    if fix_oslo_watch_pattern(watch_path, ctx.package):
        activity(
            "prepare",
            f"Updated debian/watch to accept {ctx.package} or {ctx.package.replace('.', '_')} naming",
        )
        watch_updated = True
        watch_changes.append("fix oslo.* naming pattern")

    # Update or remove signing key based on build type
    from packastack.planning.type_selection import BuildType

    is_snapshot = ctx.build_type == BuildType.SNAPSHOT
    releases_repo = ctx.paths.get("openstack_releases_repo")

    # Update or remove the signing key on disk first (committed separately below)
    # so that the PGP-options check in the watch file can see the current state.
    if update_signing_key(pkg_repo, releases_repo, ctx.openstack_target, is_snapshot):
        signing_key_updated = True
        if is_snapshot:
            activity("prepare", "Removed debian/upstream/signing-key.asc for snapshot build")
        else:
            activity("prepare", f"Updated debian/upstream/signing-key.asc for {ctx.openstack_target}")

    # For snapshot builds, remove PGP signature verification options from watch file
    # since there are no official signed tarballs for snapshots
    if is_snapshot and remove_pgp_options_from_watch(watch_path):
        activity("prepare", "Removed PGP options from debian/watch for snapshot build")
        watch_updated = True
        watch_changes.append("remove pgpsigurlmangle for snapshot")

    # For release/RC builds with a signing key, restore pgpsigurlmangle so uscan
    # can verify the detached GPG signature.  A prior snapshot build may have
    # stripped this option.
    if (
        not is_snapshot
        and has_upstream_signing_key(pkg_repo / "debian")
        and restore_pgp_options_to_watch(watch_path)
    ):
        activity("prepare", "Restored PGP options in debian/watch for signed upstream tarball")
        watch_updated = True
        watch_changes.append("restore pgpsigurlmangle for signed tarball")

    # Commit watch file update (separate commit per file)
    if watch_updated:
        watch_commit_msg = "d/watch: " + ", ".join(watch_changes)
        commit_result = git_commit(
            pkg_repo,
            watch_commit_msg,
            files=["debian/watch"],
        )
        if commit_result.returncode == 0:
            activity("prepare", "Committed watch file update")
        else:
            raise GitCommitError(
                "Failed to commit watch file update",
                stderr=commit_result.stderr,
                returncode=commit_result.returncode,
            )

    # Commit signing key update (separate commit per file)
    if signing_key_updated:
        if is_snapshot:
            signing_key_msg = "d/u/signing-key.asc: remove for snapshot"
        else:
            signing_key_msg = f"d/u/signing-key.asc: update for {_series_commit_name(ctx.openstack_target)}"
        commit_result = git_commit(
            pkg_repo,
            signing_key_msg,
            files=["debian/upstream/signing-key.asc"],
        )
        if commit_result.returncode == 0:
            activity("prepare", "Committed signing key update")
        else:
            raise GitCommitError(
                "Failed to commit signing key update",
                stderr=commit_result.stderr,
                returncode=commit_result.returncode,
            )

    # Ensure sphinxdoc addon is enabled before patch application/commits
    sphinxdoc_updated = maybe_enable_sphinxdoc(pkg_repo)
    if sphinxdoc_updated:
        commit_result = git_commit(
            pkg_repo,
            "d/rules: enable sphinxdoc to build documentation",
            files=["debian/rules"],
        )
        if commit_result.returncode == 0:
            activity("prepare", "Committed sphinxdoc enablement")
        else:
            raise GitCommitError(
                "Failed to commit sphinxdoc enablement",
                stderr=commit_result.stderr,
                returncode=commit_result.returncode,
            )

    result.watch_updated = watch_updated
    result.signing_key_updated = signing_key_updated

    # Verify signing key with uscan after all watch and signing-key changes
    # have been committed, so uscan sees the final state of both files.
    if not is_snapshot:
        signing_key_path = pkg_repo / "debian" / "upstream" / "signing-key.asc"
        if signing_key_path.exists():
            activity("prepare", "Verifying signing key with uscan --force-download...")
            verify_result = verify_signing_key_with_uscan(pkg_repo)
            if verify_result.remediation_succeeded:
                # Key was refreshed from keyserver - commit the updated key
                activity(
                    "prepare",
                    "Signing key refreshed from keyserver after GPG verification failure",
                )
                commit_result = git_commit(
                    pkg_repo,
                    "d/u/signing-key.asc: refresh from keyserver (subkey rotation)",
                    files=["debian/upstream/signing-key.asc"],
                )
                if commit_result.returncode == 0:
                    activity("prepare", "Committed keyserver-refreshed signing key")
                else:
                    raise GitCommitError(
                        "Failed to commit keyserver-refreshed signing key",
                        stderr=commit_result.stderr,
                        returncode=commit_result.returncode,
                    )
                result.signing_key_updated = True
            elif verify_result.success:
                activity("prepare", "Signing key verified successfully")
            else:
                # Verification failed but not fatally - warn and continue
                activity(
                    "prepare",
                    f"Signing key verification warning: {verify_result.error}",
                )

    return PhaseResult.ok(), result


# =============================================================================
# Phase: Prepare Upstream Source
# =============================================================================


def prepare_upstream_source(
    ctx: SingleBuildContext,
) -> tuple[PhaseResult, PrepareResult]:
    """Acquire the upstream source tarball.

    This phase:
    1. Updates launchpad.yaml if previous series exists
    2. Selects upstream source based on build type
    3. Fetches/generates the upstream tarball
    4. Applies signature policy

    Args:
        ctx: Build context.

    Returns:
        Tuple of (PhaseResult, PrepareResult).
    """
    from packastack.debpkg.changelog import (
        generate_release_or_milestone_version,
        get_current_version,
        increment_upstream_version,
        parse_version,
    )
    from packastack.debpkg.launchpad_yaml import update_launchpad_yaml_series
    from packastack.planning.type_selection import BuildType
    from packastack.upstream.releases import load_project_releases, load_series_info
    from packastack.upstream.source import (
        SnapshotAcquisitionResult,
        SnapshotRequest,
        TarballResult,
        acquire_upstream_snapshot,
        apply_signature_policy,
        select_upstream_source,
    )
    from packastack.upstream.tarball_cache import (
        TarballCacheEntry,
        cache_tarball,
        find_cached_tarball,
    )

    result = PrepareResult()
    run = ctx.run
    pkg_repo = ctx.pkg_repo
    debian_dir = pkg_repo / "debian"
    releases_repo = ctx.paths.get("openstack_releases_repo")

    activity("prepare", "Preparing packaging repository")

    # Update launchpad.yaml if previous series exists
    if ctx.prev_series:
        success, updated_fields, error = update_launchpad_yaml_series(
            pkg_repo, ctx.prev_series, ctx.openstack_target
        )
        if success:
            if updated_fields:
                activity("prepare", f"Updated launchpad.yaml: {len(updated_fields)} fields")
                run.log_event({"event": "prepare.launchpad_yaml", "fields": updated_fields})
            else:
                activity("prepare", "launchpad.yaml: no changes needed")
        else:
            activity("prepare", f"launchpad.yaml warning: {error}")
            run.log_event({"event": "prepare.launchpad_yaml_warning", "error": error})

    # Select upstream source
    activity("prepare", f"Looking for upstream {ctx.build_type.value} tarball for {ctx.package}")
    upstream = select_upstream_source(
        releases_repo,
        ctx.openstack_target,
        ctx.package,
        ctx.build_type,
    )
    ctx.upstream = upstream

    if upstream is None and ctx.build_type != BuildType.SNAPSHOT:
        # Try to diagnose specifics for the error message
        proj_info = load_project_releases(releases_repo, ctx.openstack_target, ctx.package)
        if proj_info is not None and not proj_info.has_releases():
            error_msg = (
                f"Project {ctx.package} is tracked in OpenStack {ctx.openstack_target} "
                "but has no releases defined yet."
            )
        else:
            error_msg = (
                f"No {ctx.build_type.value} tarball found for {ctx.package} "
                f"in OpenStack {ctx.openstack_target}"
            )
        activity("prepare", error_msg)
        run.write_summary(status="failed", error=error_msg, exit_code=EXIT_CONFIG_ERROR)
        return PhaseResult.fail(EXIT_CONFIG_ERROR, error_msg), result

    # Apply signature policy (remove signing keys for snapshots)
    removed_keys = apply_signature_policy(debian_dir, ctx.build_type)
    if removed_keys:
        activity("prepare", f"Removed signing keys: {len(removed_keys)} files")
        run.log_event({"event": "prepare.signing_keys_removed", "files": [str(f) for f in removed_keys]})

    # Get/fetch upstream source
    upstream_tarball: Path | None = None
    signature_verified = False
    signature_warning = ""
    git_sha = ""
    git_date = ""
    snapshot_result: SnapshotAcquisitionResult | None = None

    if ctx.build_type == BuildType.SNAPSHOT:
        if ctx.offline:
            cached_path, cached_meta = find_cached_tarball(
                project=ctx.package,
                build_type=ctx.build_type.value,
                cache_base=ctx.tarball_cache_base,
                allow_latest=True,
            )
            if not cached_path or not cached_meta:
                error_msg = f"Offline snapshot build requires a cached tarball for {ctx.package}"
                activity("prepare", error_msg)
                run.write_summary(status="failed", error=error_msg, exit_code=EXIT_FETCH_FAILED)
                return PhaseResult.fail(EXIT_FETCH_FAILED, error_msg), result

            git_sha = cached_meta.git_sha or "cached"
            git_date = cached_meta.git_date or "00000000"
            upstream_tarball = cached_path
            snapshot_result = SnapshotAcquisitionResult(
                success=True,
                repo_path=None,
                tarball_result=TarballResult(success=True, path=cached_path),
                git_sha=git_sha,
                git_sha_short=git_sha[:7],
                git_date=git_date,
                upstream_version=cached_meta.version,
                project=cached_meta.project or ctx.package,
                git_ref=cached_meta.git_ref or "cached",
                cloned=False,
            )

            activity("prepare", f"Snapshot: cached tarball {cached_path.name}")
            run.log_event(
                {
                    "event": "prepare.snapshot.cached",
                    "tarball": str(cached_path),
                    "git_sha": git_sha,
                    "git_date": git_date,
                    "upstream_version": cached_meta.version,
                }
            )

            if ctx.provenance:
                ctx.provenance.upstream.ref = cached_meta.git_ref or "cached"
                ctx.provenance.upstream.sha = git_sha
                ctx.provenance.tarball.method = "cache"
                ctx.provenance.tarball.path = str(cached_path)
                ctx.provenance.verification.mode = "none"
                ctx.provenance.verification.result = "not_applicable"
            signature_warning = "Snapshot build from cached tarball - no signature verification"
        else:
            # For snapshot, clone upstream and generate tarball from git
            activity("prepare", "Snapshot build - cloning upstream repository")

            # Determine base version from current packaging
            current_version = get_current_version(debian_dir / "changelog")
            if current_version:
                parsed_ver = parse_version(current_version)
                base_version = increment_upstream_version(parsed_ver.upstream) if parsed_ver else "0.0.0"
            else:
                base_version = "0.0.0"

            # Determine upstream branch
            upstream_branch = None
            if ctx.openstack_target:
                series_info = load_series_info(releases_repo)
                is_development = (
                    ctx.openstack_target in series_info
                    and series_info[ctx.openstack_target].status == "development"
                )
                upstream_branch = None if is_development else f"stable/{ctx.openstack_target}"

            # Clone upstream and generate snapshot tarball
            upstream_work_dir = ctx.workspace / "upstream"
            # Determine the correct project name for cloning upstream
            # Use pkg_name since it's the authoritative Debian package name
            # and load_project_releases will find the correct releases entry
            # by trying variants (with/without python- prefix)
            project_name = ctx.pkg_name

            # Verify the project exists in releases for this series
            test_releases = load_project_releases(releases_repo, ctx.openstack_target, project_name)
            if not test_releases and ctx.upstream_config.release_source.deliverable:
                # Fallback: try the deliverable name from registry
                test_releases = load_project_releases(
                    releases_repo, ctx.openstack_target, ctx.upstream_config.release_source.deliverable
                )
                if test_releases:
                    project_name = ctx.upstream_config.release_source.deliverable

            snapshot_request = SnapshotRequest(
                project=project_name,
                base_version=base_version,
                branch=upstream_branch,
                git_ref="HEAD",
                package_name=ctx.pkg_name,
                upstream_url=ctx.upstream_config.upstream.url,
            )
            snapshot_result = acquire_upstream_snapshot(
                request=snapshot_request,
                work_dir=upstream_work_dir,
                output_dir=ctx.workspace,
            )

            if not snapshot_result.success:
                activity("prepare", f"Snapshot acquisition failed: {snapshot_result.error}")
                if not ctx.force:
                    run.write_summary(
                        status="failed",
                        error=f"Snapshot acquisition failed: {snapshot_result.error}",
                        exit_code=EXIT_FETCH_FAILED,
                    )
                    return PhaseResult.fail(EXIT_FETCH_FAILED, snapshot_result.error), result
                git_sha = "HEAD"
                git_date = "00000000"
            else:
                git_sha = snapshot_result.git_sha
                git_date = snapshot_result.git_date
                upstream_tarball = (
                    snapshot_result.tarball_result.path if snapshot_result.tarball_result else None
                )
                activity(
                    "prepare",
                    f"Snapshot: git {snapshot_result.git_sha_short} from {snapshot_result.git_date}",
                )
                if snapshot_result.cloned:
                    activity("prepare", "Cloned upstream from OpenDev")
                run.log_event(
                    {
                        "event": "prepare.snapshot",
                        "git_sha": snapshot_result.git_sha,
                        "git_sha_short": snapshot_result.git_sha_short,
                        "git_date": snapshot_result.git_date,
                        "upstream_version": snapshot_result.upstream_version,
                        "cloned": snapshot_result.cloned,
                    }
                )

                if ctx.provenance:
                    ctx.provenance.upstream.ref = upstream_branch or "HEAD"
                    ctx.provenance.upstream.sha = snapshot_result.git_sha
                    ctx.provenance.tarball.method = "git_archive"
                    if upstream_tarball:
                        ctx.provenance.tarball.path = str(upstream_tarball)
                    ctx.provenance.verification.mode = "none"
                    ctx.provenance.verification.result = "not_applicable"

                if upstream_tarball and snapshot_result.upstream_version:
                    cache_tarball(
                        tarball_path=upstream_tarball,
                        entry=TarballCacheEntry(
                            project=ctx.package,
                            package_name=ctx.pkg_name,
                            version=snapshot_result.upstream_version,
                            build_type=ctx.build_type.value,
                            source_method="git_archive",
                            git_sha=snapshot_result.git_sha,
                            git_date=snapshot_result.git_date,
                            git_ref=upstream_branch or "HEAD",
                        ),
                        cache_base=ctx.tarball_cache_base,
                    )

            signature_warning = "Snapshot build - no signature verification"
    else:
        # Release: uscan first, then official, then fallbacks
        upstream_tarball, signature_verified, signature_warning = _fetch_release_tarball(
            upstream=upstream,
            upstream_config=ctx.upstream_config,
            pkg_repo=pkg_repo,
            workspace=ctx.workspace,
            provenance=ctx.provenance,
            offline=ctx.offline,
            project_key=ctx.package,
            package_name=ctx.pkg_name,
            build_type=ctx.build_type,
            cache_base=ctx.tarball_cache_base,
            force=ctx.force,
            run=run,
        )

        if upstream_tarball is None:
            if not ctx.force:
                run.write_summary(
                    status="failed",
                    error=signature_warning or "Failed to fetch upstream tarball",
                    exit_code=EXIT_FETCH_FAILED,
                )
                return PhaseResult.fail(EXIT_FETCH_FAILED, signature_warning), result
            activity("prepare", "Proceeding without upstream tarball due to --force")

    result.upstream_tarball = upstream_tarball
    result.signature_verified = signature_verified
    result.signature_warning = signature_warning
    result.git_sha = git_sha
    result.git_date = git_date
    result.snapshot_result = snapshot_result

    # Compute the new version based on build type and upstream source
    current_version = get_current_version(debian_dir / "changelog")
    if current_version:
        parsed = parse_version(current_version)
        activity("prepare", f"Current version: {current_version}")
    else:
        parsed = None

    if ctx.build_type == BuildType.RELEASE and ctx.upstream:
        new_version = generate_release_or_milestone_version(
            ctx.upstream.version, epoch=parsed.epoch if parsed else 0
        )
    elif ctx.build_type == BuildType.SNAPSHOT:
        # Use the version computed by acquire_upstream_snapshot using git describe
        if snapshot_result and snapshot_result.upstream_version:
            upstream_ver = snapshot_result.upstream_version
        else:
            # Fallback for forced builds or errors
            next_upstream = increment_upstream_version(parsed.upstream) if parsed else "0.0.0"
            upstream_ver = f"{next_upstream}~git{git_date}.{git_sha[:7]}"

        # Apply epoch and debian revision
        epoch = parsed.epoch if parsed else 0
        new_version = f"{epoch}:{upstream_ver}-0ubuntu1" if epoch else f"{upstream_ver}-0ubuntu1"
    else:
        new_version = current_version or "0.0.0-0ubuntu1"

    activity("prepare", f"New version: {new_version}")
    run.log_event({"event": "prepare.version", "current": current_version, "new": new_version})
    result.new_version = new_version

    return PhaseResult.ok(), result


# =============================================================================
# Phase: Validate Dependencies and Auto-Build
# =============================================================================


def validate_and_build_deps(
    ctx: SingleBuildContext,
    upstream_tarball: Path | None,
    snapshot_result: SnapshotAcquisitionResult | None,
) -> tuple[PhaseResult, ValidateDepsResult]:
    """Validate upstream dependencies and optionally build missing ones.

    This phase:
    1. Extracts dependencies from upstream source
    2. Validates each dependency against package indexes
    3. Identifies buildable OpenStack dependencies
    4. Auto-builds missing dependencies if enabled

    Args:
        ctx: Build context.
        upstream_tarball: Path to upstream tarball (for release builds).
        snapshot_result: Snapshot acquisition result (for snapshot builds).

    Returns:
        Tuple of (PhaseResult, ValidateDepsResult).
    """
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    from packastack.logs.dep_sync import (
        DependencySatisfactionSummary,
        save_satisfaction_report,
    )
    from packastack.planning.type_selection import BuildType
    from packastack.planning.validated_plan import (
        check_version_satisfies,
        extract_upstream_deps,
        map_python_to_debian,
        project_to_source_package,
        resolve_dependency_with_spec,
    )
    from packastack.upstream.tarball_cache import extract_tarball

    result = ValidateDepsResult()
    run = ctx.run

    activity("validate-deps", "Validating upstream dependencies")

    policy = getattr(ctx, "min_version_policy", "enforce")
    enforce_min_versions = policy != "ignore"
    overridden_specs = 0
    source_counts: dict[str, int] = {"ubuntu": 0, "cloud-archive": 0, "local": 0}
    outdated_deps: list[str] = []

    # Extract dependencies from upstream repo (if available)
    upstream_repo_path = None
    if ctx.build_type == BuildType.SNAPSHOT and snapshot_result and snapshot_result.repo_path:
        upstream_repo_path = snapshot_result.repo_path
    elif ctx.build_type == BuildType.RELEASE and upstream_tarball:
        activity("validate-deps", f"Extracting tarball for dependency analysis: {upstream_tarball.name}")
        tarball_version = ctx.upstream.version if ctx.upstream else ctx.pkg_name

        extraction_result = extract_tarball(
            tarball_path=upstream_tarball,
            project=ctx.pkg_name,
            version=tarball_version,
            cache_base=ctx.tarball_cache_base,
        )

        if extraction_result.success and extraction_result.extraction_path:
            upstream_repo_path = extraction_result.extraction_path
            if extraction_result.from_cache:
                activity("validate-deps", "Using cached tarball extraction")
            else:
                activity("validate-deps", f"Extracted to: {extraction_result.extraction_path}")
        else:
            activity("validate-deps", f"Could not extract tarball: {extraction_result.error}")

    result.upstream_repo_path = upstream_repo_path
    missing_deps_list: list[str] = []
    new_deps_to_build: list[str] = []
    upstream_min_versions: dict[str, str] = {}

    def _min_version_from_spec(spec: str) -> str | None:
        if not spec:
            return None
        try:
            spec_set = SpecifierSet(spec)
        except Exception:
            return None

        mins: list[str] = []
        for s in spec_set:
            if s.operator in {">=", "==", ">"}:
                mins.append(s.version)

        if not mins:
            return None

        with contextlib.suppress(Exception):
            mins.sort(key=Version)
        return mins[0]

    if upstream_repo_path and upstream_repo_path.exists():
        upstream_deps = extract_upstream_deps(upstream_repo_path)
        activity("validate-deps", f"Found {len(upstream_deps.runtime)} runtime dependencies")
        run.log_event(
            {
                "event": "validate-deps.extracted",
                "runtime_count": len(upstream_deps.runtime),
                "test_count": len(upstream_deps.test),
                "build_count": len(upstream_deps.build),
            }
        )

        resolved_count = 0
        for python_dep, version_spec in upstream_deps.runtime:
            debian_name, _uncertain = map_python_to_debian(python_dep)
            if not debian_name:
                activity("validate-deps", f"  {python_dep} -> (unmapped)")
                continue

            min_ver = _min_version_from_spec(version_spec)
            if min_ver:
                upstream_min_versions[debian_name] = min_ver

            version, source, satisfied = resolve_dependency_with_spec(
                debian_name,
                version_spec,
                ctx.local_index,
                ctx.ca_index,
                ctx.ubuntu_index,
                enforce_min_versions=enforce_min_versions,
            )

            spec_satisfied = check_version_satisfies(version_spec, version) if version else False
            if version and source:
                source_counts[source] = source_counts.get(source, 0) + 1

            effective_satisfied = satisfied
            if not enforce_min_versions and not spec_satisfied and version:
                # Policy override: allow older than minimum
                effective_satisfied = True
                overridden_specs += 1

            spec_display = f" (req: {version_spec})" if version_spec else ""
            if version:
                resolved_count += 1
                if spec_satisfied or not version_spec:
                    status = "✓ SATISFIED"
                else:
                    status = "✗ OUTDATED"
                    outdated_deps.append(debian_name)
                activity(
                    "validate-deps",
                    f"  {python_dep}{spec_display} -> {debian_name} = {version} ({source}) [{status}]",
                )
                run.log_event(
                    {
                        "event": "validate-deps.resolved",
                        "python_dep": python_dep,
                        "version_spec": version_spec,
                        "debian_name": debian_name,
                        "version": version,
                        "source": source,
                        "satisfied": effective_satisfied,
                    }
                )
            else:
                missing_deps_list.append(debian_name)
                activity("validate-deps", f"  {python_dep}{spec_display} -> {debian_name} [✗ MISSING]")

        activity("validate-deps", f"Resolved {resolved_count}/{len(upstream_deps.runtime)} dependencies")

        if missing_deps_list:
            activity("validate-deps", f"Warning: {len(missing_deps_list)} dependencies not resolved")
            run.log_event(
                {
                    "event": "validate-deps.missing",
                    "count": len(missing_deps_list),
                    "deps": missing_deps_list,
                }
            )

            # Check which missing deps are OpenStack packages we could build
            if isinstance(ctx.openstack_pkgs, dict):
                openstack_projects = set(ctx.openstack_pkgs.values())
            else:
                openstack_projects = set(ctx.openstack_pkgs) if ctx.openstack_pkgs else set()
            buildable_deps: list[str] = []

            for dep in missing_deps_list:
                if dep.startswith("python3-"):
                    potential_project = dep[8:]
                elif dep.startswith("python-"):
                    potential_project = dep[7:]
                else:
                    potential_project = dep

                if potential_project in openstack_projects:
                    source_pkg = project_to_source_package(potential_project)
                    if source_pkg not in buildable_deps:
                        buildable_deps.append(source_pkg)

            if buildable_deps:
                activity("validate-deps", f"The following {len(buildable_deps)} packages could be built first:")
                for dep in buildable_deps[:10]:
                    type_hint = f" --type {ctx.build_type.value}" if ctx.build_type != BuildType.RELEASE else ""
                    activity("validate-deps", f"  packastack build {dep}{type_hint}")
                if len(buildable_deps) > 10:
                    activity("validate-deps", f"  ... and {len(buildable_deps) - 10} more")

                run.log_event({"event": "validate-deps.buildable", "packages": buildable_deps})
                new_deps_to_build.extend(buildable_deps)

        # Summary and report
        total = len(upstream_deps.runtime)
        outdated_count = len(outdated_deps)
        missing_count = len(missing_deps_list)
        satisfied_count = max(resolved_count - outdated_count, 0)

        activity(
            "validate-deps",
            f"Summary (policy={policy}): {satisfied_count}/{total} satisfied, {outdated_count} need newer version, {missing_count} missing",
        )

        if ctx.dep_report and ctx.run and getattr(ctx.run, "logs_path", None):
            report_dir = Path(ctx.run.logs_path)
            summary = DependencySatisfactionSummary(
                package=ctx.pkg_name,
                policy=policy,
                total=total,
                satisfied=satisfied_count,
                outdated=outdated_count,
                missing=missing_count,
                overridden=overridden_specs,
                by_source=source_counts,
                missing_deps=missing_deps_list,
                outdated_deps=outdated_deps,
            )
            report_paths = save_satisfaction_report(summary, report_dir)
            for path in report_paths:
                activity("validate-deps", f"Report written: {path}")
    else:
        activity("validate-deps", "Skipping - no upstream repo available")

    # Persist upstream minimum versions for later control min-version policy
    if upstream_min_versions:
        ctx.upstream_min_versions = upstream_min_versions

    result.missing_deps = missing_deps_list
    result.buildable_deps = new_deps_to_build

    # Auto-build phase
    if ctx.build_deps and new_deps_to_build:
        phase_result = _auto_build_deps(ctx, new_deps_to_build)
        if not phase_result.success:
            return phase_result, result

    return PhaseResult.ok(), result


# =============================================================================
# Phase: Dependency satisfaction reporting (build-time)
# =============================================================================


def report_dependency_satisfaction(ctx: SingleBuildContext) -> PhaseResult:
    """Evaluate debian/control deps against dev and previous LTS and write reports."""

    from packastack.debpkg.control import format_dependency_list, parse_control
    from packastack.planning.control_min_versions import (
        apply_min_version_policy,
        decisions_to_report,
    )
    from packastack.planning.dependency_satisfaction import evaluate_dependencies

    control_path = ctx.pkg_repo / "debian" / "control"
    if not control_path.exists():
        activity("deps", "debian/control not found; skipping dependency satisfaction report")
        return PhaseResult.ok()

    dev_index = ctx.ubuntu_index
    current_lts_index = ctx.current_lts_index
    if dev_index is None:
        activity("deps", "Ubuntu index unavailable; skipping dependency satisfaction")
        return PhaseResult.ok()

    source_pkg = parse_control(control_path)
    build_dep_list = list(source_pkg.build_depends)
    build_dep_indep = list(source_pkg.build_depends_indep)
    build_deps = build_dep_list + build_dep_indep
    runtime_deps = source_pkg.get_runtime_depends()

    build_results, build_summary = evaluate_dependencies(build_deps, dev_index, current_lts_index, kind="build")
    runtime_results, runtime_summary = evaluate_dependencies(runtime_deps, dev_index, current_lts_index, kind="runtime")

    def _count_components(results: list) -> tuple[int, int]:
        main_count = 0
        universe_count = 0
        for dep in results:
            if dep.dev.satisfied:
                if dep.dev.component in ("main", "", None):
                    main_count += 1
                else:
                    universe_count += 1
        return main_count, universe_count

    dev_main, dev_universe = _count_components(build_results)
    current_lts_main, current_lts_universe = (0, 0)
    for dep in build_results:
        if dep.prev_lts.satisfied:
            if dep.prev_lts.component in ("main", "", None):
                current_lts_main += 1
            else:
                current_lts_universe += 1

    summary = {
        "build_deps_total": build_summary.total,
        "build_deps_dev_satisfied": build_summary.dev_satisfied,
        "build_deps_current_lts_satisfied": build_summary.prev_lts_satisfied,
        "runtime_deps_total": runtime_summary.total,
        "runtime_deps_dev_satisfied": runtime_summary.dev_satisfied,
        "runtime_deps_current_lts_satisfied": runtime_summary.prev_lts_satisfied,
        "cloud_archive_required_count": build_summary.cloud_archive_required + runtime_summary.cloud_archive_required,
        "mir_warning_count": build_summary.mir_warnings + runtime_summary.mir_warnings,
        "dev_main_satisfied": dev_main,
        "dev_universe_satisfied": dev_universe,
        "current_lts_main_satisfied": current_lts_main,
        "current_lts_universe_satisfied": current_lts_universe,
    }

    build_payload = [r.to_dict() for r in build_results]
    runtime_payload = [r.to_dict() for r in runtime_results]

    report = {
        "run_id": ctx.run.run_id,
        "target": {"source_package": ctx.pkg_name},
        "openstack_target": ctx.openstack_target,
        "ubuntu_series": ctx.resolved_ubuntu,
        "current_lts": ctx.current_lts_codename,
        "dependencies": {"build": build_payload, "runtime": runtime_payload},
        "summary": summary,
    }

    reports_dir = Path(ctx.run.logs_path)
    saved = write_dependency_satisfaction_reports(report, reports_dir)
    ctx.dependency_reports = saved

    # Optional control-file min-version normalization
    if ctx.update_control_min_versions:
        upstream_min_map = ctx.upstream_min_versions or {}
        if upstream_min_map or current_lts_index is not None:
            current_lts_versions = {dep.name: current_lts_index.get_version(dep.name) if current_lts_index else None for dep in build_deps}
            updated_build, decisions_build = apply_min_version_policy(
                existing=build_dep_list,
                upstream_mins=upstream_min_map,
                prev_lts_versions=current_lts_versions,
                normalize=ctx.normalize_to_prev_lts_floor,
                dry_run=ctx.dry_run_control_edit,
            )
            updated_indep, decisions_indep = apply_min_version_policy(
                existing=build_dep_indep,
                upstream_mins=upstream_min_map,
                prev_lts_versions=current_lts_versions,
                normalize=ctx.normalize_to_prev_lts_floor,
                dry_run=ctx.dry_run_control_edit,
            )

            if not ctx.dry_run_control_edit:
                # Rewrite Build-Depends/Build-Depends-Indep with updated ordering
                text = control_path.read_text()
                original_text = text

                def _replace_field(body: str, field: str, value: str) -> str:
                    import re

                    pattern = rf"{field}:(?:[^\n]*\n(?:[ \t].*\n)*)"
                    replacement = f"{field}: {value}\n"
                    return re.sub(pattern, replacement, body, count=1)

                build_field = format_dependency_list(updated_build)
                indep_field = format_dependency_list(updated_indep)
                text = _replace_field(text, "Build-Depends", build_field)
                text = _replace_field(text, "Build-Depends-Indep", indep_field)
                control_path.write_text(text)
                if text != original_text:
                    series_name = _series_commit_name(ctx.current_lts_codename or ctx.resolved_ubuntu)
                    control_msg = f"d/control: Bump dependencies for {series_name}."
                    if (ctx.pkg_repo / ".git").exists():
                        # gbp dch later picks up this subject as the changelog bullet.
                        commit_result = git_commit(
                            ctx.pkg_repo,
                            control_msg,
                            files=["debian/control"],
                        )
                        if commit_result.returncode == 0:
                            activity("deps", f"Committed control dependency update: {control_msg}")
                        else:
                            raise GitCommitError(
                                "Failed to commit control dependency update",
                                stderr=commit_result.stderr,
                                returncode=commit_result.returncode,
                            )

            # Write control min-version report
            decisions = decisions_build + decisions_indep
            cmv_report = decisions_to_report(decisions)
            cmv_path = reports_dir / "control-min-versions.json"
            cmv_path.write_text(json.dumps(cmv_report, indent=2))
            if ctx.dependency_reports is not None:
                ctx.dependency_reports["control_min_versions"] = cmv_path

            ca_required = [d for d in decisions if d.cloud_archive_required]
            if ca_required:
                activity("deps", "[deps] Cloud-archive required (upstream min exceeds latest LTS):")
                for d in ca_required:
                    activity(
                        "deps",
                        f"[deps]   - {d.name} (>= {d.upstream_min_required}) latest-lts={d.prev_lts_version or 'none'}",
                    )
        else:
            activity("deps", "[deps] Skipping control min-version update (no upstream minima or latest LTS index available)")

    activity("deps", "[deps] Dependency satisfaction:")
    activity(
        "deps",
        f"[deps]   ubuntu-series ({ctx.resolved_ubuntu}):  {build_summary.dev_satisfied}/{build_summary.total} satisfied "
        f"(main={dev_main}, universe={dev_universe})",
    )
    activity(
        "deps",
        f"[deps]   current-lts ({ctx.current_lts_codename or 'unknown'}):   {build_summary.prev_lts_satisfied}/{build_summary.total} satisfied "
        f"(main={current_lts_main}, universe={current_lts_universe})",
    )
    activity(
        "deps",
        f"[deps]   cloud-archive required:    {summary['cloud_archive_required_count']} deps",
    )
    activity("deps", f"[deps]   MIR warnings (universe):   {summary['mir_warning_count']} deps")

    cloud_required = [d for d in build_payload + runtime_payload if d.get("cloud_archive_required")]
    if cloud_required:
        activity("deps", "[deps] Cloud-archive required deps:")
        for dep in cloud_required:
            constraint = f"{dep.get('relation','')} {dep.get('version','')}".strip()
            activity("deps", f"[deps]   - {dep.get('name')} {constraint}")

    mir_list = [d for d in build_payload + runtime_payload if d.get("mir_warning")]
    if mir_list:
        activity("deps", "[deps] MIR warnings (universe):")
        for dep in mir_list:
            constraint = f"{dep.get('relation','')} {dep.get('version','')}".strip()
            activity("deps", f"[deps]   - {dep.get('name')} {constraint}")

    if ctx.fail_on_cloud_archive_required and summary["cloud_archive_required_count"]:
        return PhaseResult.fail(EXIT_POLICY_BLOCKED, "Cloud-archive required for dependencies")
    if ctx.fail_on_mir_required and summary["mir_warning_count"]:
        return PhaseResult.fail(EXIT_POLICY_BLOCKED, "MIR required for dependencies")

    return PhaseResult.ok()


def _auto_build_deps(ctx: SingleBuildContext, deps_to_build: list[str]) -> PhaseResult:
    """Auto-build missing dependencies.

    Args:
        ctx: Build context.
        deps_to_build: List of source packages to build.

    Returns:
        PhaseResult indicating success or failure.
    """
    from packastack.apt.packages import load_package_index

    run = ctx.run
    activity("auto-build", f"Auto-building {len(deps_to_build)} missing dependencies")

    current_depth = int(os.environ.get("PACKASTACK_BUILD_DEPTH", "0"))
    max_depth = 10

    if current_depth >= max_depth:
        activity("auto-build", f"Maximum build depth ({max_depth}) reached, aborting")
        run.log_event(
            {
                "event": "auto-build.max_depth",
                "current_depth": current_depth,
                "max_depth": max_depth,
            }
        )
        run.write_summary(
            status="failed",
            error=f"Maximum dependency build depth ({max_depth}) exceeded",
            exit_code=EXIT_MISSING_PACKAGES,
        )
        return PhaseResult.fail(EXIT_MISSING_PACKAGES, "Maximum build depth exceeded")

    for i, dep_pkg in enumerate(deps_to_build, 1):
        activity("auto-build", f"[{i}/{len(deps_to_build)}] Building dependency: {dep_pkg}")
        run.log_event(
            {
                "event": "auto-build.start",
                "package": dep_pkg,
                "index": i,
                "total": len(deps_to_build),
                "depth": current_depth + 1,
            }
        )

        child_env = os.environ.copy()
        child_env["PACKASTACK_BUILD_DEPTH"] = str(current_depth + 1)

        cmd = [
            "packastack",
            "build",
            dep_pkg,
            "--target",
            ctx.target,
            "--ubuntu-series",
            ctx.ubuntu_series,
            "--type",
            ctx.build_type.value,
        ]
        if ctx.cloud_archive:
            cmd.extend(["--cloud-archive", ctx.cloud_archive])
        if ctx.force:
            cmd.append("--force")
        if ctx.offline:
            cmd.append("--offline")
        if not ctx.binary:
            cmd.append("--no-binary")
        cmd.append("--build-deps")
        cmd.append("--yes")

        activity("auto-build", f"Running: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                env=child_env,
                cwd=str(ctx.local_repo),
                capture_output=False,
            )

            if result.returncode != 0:
                activity(
                    "auto-build",
                    f"Dependency build failed: {dep_pkg} (exit code: {result.returncode})",
                )
                run.log_event(
                    {
                        "event": "auto-build.failed",
                        "package": dep_pkg,
                        "exit_code": result.returncode,
                    }
                )
                run.write_summary(
                    status="failed",
                    error=f"Dependency build failed: {dep_pkg}",
                    exit_code=result.returncode,
                )
                return PhaseResult.fail(result.returncode, f"Dependency build failed: {dep_pkg}")

            activity("auto-build", f"Successfully built dependency: {dep_pkg}")
            run.log_event({"event": "auto-build.success", "package": dep_pkg})

        except FileNotFoundError:
            activity("auto-build", "Error: packastack command not found")
            run.write_summary(
                status="failed",
                error="packastack command not found for auto-build",
                exit_code=EXIT_TOOL_MISSING,
            )
            return PhaseResult.fail(EXIT_TOOL_MISSING, "packastack command not found")

    activity("auto-build", f"All {len(deps_to_build)} dependencies built successfully")
    run.log_event({"event": "auto-build.complete", "count": len(deps_to_build)})

    # Refresh local package index after building dependencies
    activity("auto-build", "Refreshing local package index")
    ctx.local_index = load_package_index(ctx.local_repo)
    if ctx.local_index:
        run.log_event({"event": "auto-build.index_refreshed"})

    return PhaseResult.ok()


# =============================================================================
# Phase: Import and Patch
# =============================================================================


def _resolve_modify_delete_conflicts(
    pkg_repo: Path,
    upstream_tag: str,
) -> tuple[bool, list[str]]:
    """Auto-resolve modify/delete merge conflicts by deleting the files.

    After a ``git merge -Xtheirs`` that leaves modify/delete conflicts
    (packaging branch deleted files that upstream still ships, e.g.
    pbr-generated AUTHORS, ChangeLog, PKG-INFO, *.egg-info/*), this
    function removes the conflicting files and concludes the merge.

    Args:
        pkg_repo: Path to the packaging repository.
        upstream_tag: The upstream tag being merged (for commit message).

    Returns:
        Tuple of (success, resolved_files).
    """
    unmerged_rc, unmerged_out, _ = run_command(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        cwd=pkg_repo,
    )
    unmerged_files = [
        f for f in unmerged_out.strip().splitlines() if f
    ] if unmerged_rc == 0 else []

    if not unmerged_files:
        return False, []

    for ufile in unmerged_files:
        run_command(["git", "rm", "-f", "--", ufile], cwd=pkg_repo)

    conclude_rc, _conclude_out, _conclude_err = run_command(
        [
            "git", "commit", "--no-edit",
            "-m", f"Merging upstream release {upstream_tag}",
        ],
        cwd=pkg_repo,
    )
    if conclude_rc == 0:
        return True, unmerged_files
    return False, unmerged_files


def import_and_patch(
    ctx: SingleBuildContext,
    upstream_tarball: Path | None,
    snapshot_result: SnapshotAcquisitionResult | None,
    new_version: str,
    *,
    ignore_new: bool = False,
) -> PhaseResult:
    """Import upstream tarball and apply patches.

    This phase:
    1. Ensures upstream branch exists
    2. Imports upstream tarball with gbp import-orig
    3. Applies patches with gbp pq
    4. Exports refreshed patches
    5. Updates debian/changelog with new version

    Args:
        ctx: Build context.
        upstream_tarball: Path to upstream tarball.
        snapshot_result: Snapshot result (for version info).
        new_version: The computed new version string.
        ignore_new: Pass --ignore-new to gbp pq import so it tolerates
            uncommitted changes (used during retry after patch refresh).

    Returns:
        PhaseResult indicating success or failure.
    """
    from packastack.debpkg.gbp import (
        check_upstreamed_patches,
        ensure_upstream_branch,
        import_orig,
        pq_export,
        pq_import,
    )
    from packastack.planning.type_selection import BuildType

    run = ctx.run
    pkg_repo = ctx.pkg_repo

    # Import-orig phase
    if upstream_tarball and upstream_tarball.exists():
        activity("import-orig", f"Importing upstream tarball: {upstream_tarball.name}")

        upstream_branch_name = f"upstream-{ctx.openstack_target}"
        branch_result = ensure_upstream_branch(pkg_repo, ctx.openstack_target, ctx.prev_series)

        if branch_result.success:
            if branch_result.created:
                activity("import-orig", f"Created upstream branch: {upstream_branch_name}")
                if ctx.prev_series:
                    activity("import-orig", f"  (branched from upstream-{ctx.prev_series})")
            else:
                activity("import-orig", f"Using upstream branch: {upstream_branch_name}")
            run.log_event(
                {
                    "event": "import-orig.branch",
                    "branch": upstream_branch_name,
                    "created": branch_result.created,
                }
            )
        else:
            activity("import-orig", f"Failed to ensure upstream branch: {branch_result.error}")
            if not ctx.force:
                run.write_summary(
                    status="failed",
                    error=branch_result.error,
                    exit_code=EXIT_FETCH_FAILED,
                )
                return PhaseResult.fail(EXIT_FETCH_FAILED, branch_result.error)
            run.log_event({"event": "import-orig.branch_failed", "error": branch_result.error})

        # Extract version for import-orig.
        # For RC/beta versions, normalize to Debian tilde form (e.g. "22.0.0.0rc1" ->
        # "22.0.0~rc1") so that gbp import-orig:
        #   • stores the pristine-tar entry as "pkg_22.0.0~rc1.orig.tar.gz" (what
        #     gbp buildpackage will look for based on debian/changelog), and
        #   • creates the git tag as "22.0.0_rc1" (gbp replaces "~" with "_" for
        #     git-safe tag names, matching historical RC tag conventions).
        if ctx.build_type == BuildType.SNAPSHOT and snapshot_result:
            import_version = snapshot_result.upstream_version
        elif ctx.upstream:
            import_version = ctx.upstream.version
        else:
            import_version = None

        if import_version:
            from packastack.debpkg.changelog import split_milestone_version

            milestone_parts = split_milestone_version(import_version)
            if milestone_parts:
                base, marker = milestone_parts
                import_version = f"{base}~{marker}"

        # Import without merging - we'll handle the merge manually to preserve packaging files.
        # If the package declares gbp components (e.g., xstatic), the component
        # tarballs must exist *before* the main import so that gbp import-orig
        # can handle everything in a single atomic call.  We invoke the same
        # debian/bundle-<component>.sh scripts that uploaders use manually,
        # keeping the process transparent and reproducible.
        from packastack.debpkg.gbpconf import get_components

        components = get_components(pkg_repo)

        for comp_name in components:
            bundle_script = pkg_repo / "debian" / f"bundle-{comp_name}.sh"
            if not bundle_script.exists():
                activity(
                    "import-orig",
                    f"Component '{comp_name}' declared but no bundle script found",
                )
                continue

            comp_version = import_version or ""
            if not comp_version:
                activity(
                    "import-orig",
                    f"Skipping component '{comp_name}': no version available",
                )
                continue

            activity(
                "import-orig",
                f"Generating component tarball: bundle-{comp_name}.sh {comp_version}",
            )
            bundle_cmd = ["bash", str(bundle_script), comp_version]
            bundle_rc, bundle_out, bundle_err = run_command(
                bundle_cmd, cwd=pkg_repo
            )

            if bundle_rc != 0:
                activity(
                    "import-orig",
                    f"Component bundle script failed: {bundle_err or bundle_out}",
                )
                run.log_event(
                    {
                        "event": "import-orig.component_bundle_failed",
                        "component": comp_name,
                        "error": bundle_err or bundle_out,
                    }
                )
                continue

            comp_tarball = (
                pkg_repo.parent
                / f"{ctx.pkg_name}_{comp_version}.orig-{comp_name}.tar.gz"
            )
            if comp_tarball.exists():
                activity(
                    "import-orig",
                    f"Component tarball generated: {comp_tarball.name}",
                )
                run.log_event(
                    {
                        "event": "import-orig.component_generated",
                        "component": comp_name,
                        "tarball": str(comp_tarball),
                    }
                )
            else:
                activity(
                    "import-orig",
                    f"Component tarball not found after bundle: {comp_tarball.name}",
                )

        # Now import the main tarball.  If component tarballs were pre-generated
        # above, gbp import-orig finds them alongside the main tarball (they
        # share the same parent directory) and imports everything at once.
        import_result = import_orig(
            pkg_repo,
            upstream_tarball,
            upstream_version=import_version,
            upstream_branch=upstream_branch_name,
            pristine_tar=True,
            merge=False,  # Don't let gbp do the merge
        )

        if import_result.success:
            activity("import-orig", "Upstream tarball imported successfully")
            run.log_event(
                {
                    "event": "import-orig.complete",
                    "tarball": str(upstream_tarball),
                    "version": import_result.upstream_version,
                }
            )

            # Now manually merge the upstream tag, preserving packaging files.
            # gbp replaces "~" with "_" in git tag names (since "~" is an invalid
            # git ref character), so convert accordingly for all git operations.
            upstream_tag = (import_result.upstream_version or "").replace("~", "_")
            if upstream_tag:
                activity("import-orig", f"Merging upstream tag '{upstream_tag}' with -Xtheirs strategy")

                # Check if tag is already merged
                check_merged = ["git", "branch", "--contains", upstream_tag]
                merged_rc, merged_out, _ = run_command(check_merged, cwd=pkg_repo)

                if merged_rc == 0 and "master" in merged_out:
                    activity("import-orig", f"Tag '{upstream_tag}' already merged into master")
                else:
                    # Perform merge with -Xtheirs to prefer upstream for conflicts
                    merge_cmd = ["git", "merge", "-Xtheirs", "-m", f"Merging upstream release {upstream_tag}", upstream_tag]
                    merge_rc, merge_out, merge_err = run_command(merge_cmd, cwd=pkg_repo)

                    if merge_rc == 0:
                        activity("import-orig", "Upstream tag merged successfully")
                    else:
                        # Merge had conflicts — try to auto-resolve modify/delete
                        # conflicts.  These occur when the packaging branch deleted
                        # files (e.g. pbr-generated AUTHORS, ChangeLog, PKG-INFO,
                        # *.egg-info/*) that upstream still ships.  We honour the
                        # packaging branch's deletion.
                        resolved, resolved_files = _resolve_modify_delete_conflicts(
                            pkg_repo, upstream_tag,
                        )
                        if resolved:
                            activity(
                                "import-orig",
                                f"Auto-resolved {len(resolved_files)} modify/delete conflict(s)",
                            )
                            for rfile in resolved_files:
                                activity("import-orig", f"  Resolved conflict: {rfile} (delete)")
                            merge_rc = 0
                            run.log_event({
                                "event": "import-orig.merge_conflict_resolved",
                                "resolved_files": resolved_files,
                            })

                    if merge_rc == 0:
                        packaging_files = [".launchpad.yaml", ".gitattributes"]
                        for pfile in packaging_files:
                            file_path = pkg_repo / pfile
                            # Check if file exists in HEAD but was deleted in merge
                            check_cmd = ["git", "ls-tree", "HEAD", pfile]
                            check_rc, check_out, _ = run_command(check_cmd, cwd=pkg_repo)

                            if check_rc == 0 and check_out.strip() and not file_path.exists():
                                # File existed before merge but is now missing - restore it
                                restore_cmd = ["git", "checkout", "HEAD", "--", pfile]
                                restore_rc, _restore_out, restore_err = run_command(restore_cmd, cwd=pkg_repo)

                                if restore_rc == 0:
                                    activity("import-orig", f"Restored {pfile} after merge")
                                    # Stage the restored file
                                    stage_cmd = ["git", "add", pfile]
                                    run_command(stage_cmd, cwd=pkg_repo)
                                else:
                                    activity("import-orig", f"Warning: Could not restore {pfile}: {restore_err}")

                        # Amend the merge commit if we restored any files
                        amend_cmd = ["git", "commit", "--amend", "--no-edit"]
                        amend_rc, _, _ = run_command(amend_cmd, cwd=pkg_repo)
                        if amend_rc == 0:
                            activity("import-orig", "Updated merge commit with restored packaging files")

                        run.log_event({"event": "import-orig.merge_complete", "tag": upstream_tag})
                    else:
                        activity("import-orig", f"Merge failed: {merge_err or merge_out}")
                        if not ctx.force:
                            run.write_summary(
                                status="failed",
                                error="Failed to merge upstream tag",
                                exit_code=EXIT_FETCH_FAILED,
                            )
                            return PhaseResult.fail(EXIT_FETCH_FAILED, "Merge failed")
                        run.log_event({"event": "import-orig.merge_failed", "error": merge_err or merge_out})
        else:
            activity("import-orig", f"Import failed: {import_result.output}")
            if not ctx.force:
                run.write_summary(
                    status="failed",
                    error="Failed to import upstream tarball",
                    exit_code=EXIT_FETCH_FAILED,
                )
                return PhaseResult.fail(EXIT_FETCH_FAILED, "Import failed")
            run.log_event({"event": "import-orig.failed", "output": import_result.output})
    else:
        activity("import-orig", "No upstream tarball to import")

    # Patches phase
    activity("patches", "Applying patches with gbp pq")

    upstreamed = check_upstreamed_patches(pkg_repo)
    if upstreamed:
        activity("patches", f"Potentially upstreamed patches: {len(upstreamed)}")
        for report in upstreamed:
            activity("patches", f"  {report.patch_name}: {report.suggested_action}")

        # Attempt AI-powered auto-drop of upstreamed patches
        auto_dropped = False
        if ctx.ai_enabled and ctx.cfg:
            from packastack.ai.client import is_ai_available

            if is_ai_available(ctx.cfg):
                from packastack.ai.patch_diagnosis import auto_drop_upstreamed_patches

                drop_result = auto_drop_upstreamed_patches(
                    pkg_repo=pkg_repo,
                    upstreamed_reports=upstreamed,
                    pkg_name=ctx.pkg_name,
                    version=ctx.upstream.version if ctx.upstream else "",
                    ubuntu_series=ctx.resolved_ubuntu,
                    cfg=ctx.cfg,
                )
                for name in drop_result.dropped:
                    activity("patches", f"  Auto-dropped upstreamed patch: {name}")
                for name in drop_result.skipped:
                    activity("patches", f"  AI says keep patch: {name}")
                for err in drop_result.errors:
                    activity("patches", f"  Auto-drop error: {err}")

                auto_dropped = drop_result.all_dropped
                run.log_event({
                    "event": "patches.auto_drop",
                    "dropped": drop_result.dropped,
                    "skipped": drop_result.skipped,
                })

        if not auto_dropped and not ctx.force:
            activity("patches", "Use --force to continue with potentially upstreamed patches")
            run.write_summary(
                status="failed",
                error="Patches appear to be upstreamed",
                patches=[str(r) for r in upstreamed],
                exit_code=EXIT_PATCH_FAILED,
            )
            return PhaseResult.fail(EXIT_PATCH_FAILED, "Patches upstreamed")
        run.log_event({"event": "patches.upstreamed", "patches": [r.patch_name for r in upstreamed]})

    pq_result = pq_import(pkg_repo, ignore_new=ignore_new)
    if pq_result.success:
        activity("patches", "Patches applied successfully")
    elif pq_result.needs_refresh:
        activity("patches", "Patches need refresh - forcing import with time-machine")
        force_result = pq_import(pkg_repo, time_machine=0, ignore_new=ignore_new)
        if force_result.success:
            activity("patches", "Patches imported with offset/fuzz - exporting refreshed patches")
            export_result = pq_export(pkg_repo)
            if export_result.success:
                activity("patches", "Patches refreshed successfully")
            else:
                activity("patches", f"Patch export failed: {export_result.output}")
                if not ctx.force:
                    run.write_summary(
                        status="failed",
                        error="Patch export failed",
                        exit_code=EXIT_PATCH_FAILED,
                    )
                    return PhaseResult.fail(EXIT_PATCH_FAILED, "Patch export failed")
        else:
            activity("patches", f"Forced import failed: {force_result.output}")
            if not ctx.force:
                run.write_summary(
                    status="failed",
                    error="Patch refresh failed",
                    exit_code=EXIT_PATCH_FAILED,
                )
                return PhaseResult.fail(EXIT_PATCH_FAILED, "Patch refresh failed")
    else:
        activity("patches", f"Patch import failed: {pq_result.output}")
        for report in pq_result.patch_reports:
            activity("patches", f"  {report}")
        if not ctx.force:
            run.write_summary(
                status="failed",
                error="Patch import failed",
                patches=[str(r) for r in pq_result.patch_reports],
                exit_code=EXIT_PATCH_FAILED,
            )
            return PhaseResult.fail(
                EXIT_PATCH_FAILED,
                f"Patch import failed\n{pq_result.output}",
            )

    run.log_event({"event": "patches.complete", "success": pq_result.success})

    # Export patches and return to master branch
    if (pkg_repo / ".git").exists():
        branch_rc, branch_out, _ = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=pkg_repo)
        current_branch = branch_out.strip() if branch_rc == 0 else None

        if current_branch and current_branch.startswith("patch-queue/"):
            export_result = pq_export(pkg_repo)
            if not export_result.success:
                activity("patches", f"Patch export failed: {export_result.output}")
                if not ctx.force:
                    run.write_summary(
                        status="failed",
                        error="Patch export failed",
                        exit_code=EXIT_PATCH_FAILED,
                    )
                    return PhaseResult.fail(EXIT_PATCH_FAILED, "Patch export failed")
        else:
            activity("patches", f"Skipping patch export: current branch {current_branch or 'unknown'}")

        checkout_rc, checkout_out, checkout_err = run_command(
            ["git", "checkout", "master"], cwd=pkg_repo
        )
        if checkout_rc != 0:
            activity(
                "patches",
                f"Failed to checkout master after export: {checkout_err or checkout_out}",
            )
            if not ctx.force:
                run.write_summary(
                    status="failed",
                    error="Failed to checkout master after patch export",
                    exit_code=EXIT_PATCH_FAILED,
                )
                return PhaseResult.fail(EXIT_PATCH_FAILED, "Checkout failed")
        else:
            activity("patches", "Checked out master after patch export")
            activity("patches", "Committing refreshed patches on master")
            patches_path = pkg_repo / "debian" / "patches"
            if patches_path.exists():
                commit_result = git_commit(
                    pkg_repo,
                    "d/patches/*: refresh patches",
                    files=["debian/patches"],
                )
                if commit_result.returncode == 0:
                    activity("patches", "Committed refreshed patches")
                else:
                    raise GitCommitError(
                        "Failed to commit refreshed patches",
                        stderr=commit_result.stderr,
                        returncode=commit_result.returncode,
                    )
            else:
                activity("patches", "No debian/patches to commit; skipping")
    else:
        activity("patches", "Skipping patch export/checkout (not a git repo)")

    # -------------------------------------------------------------------------
    # Fix sudoers files for sudo-rs compatibility (before changelog)
    # -------------------------------------------------------------------------
    _fix_sudoers_args(ctx)

    # -------------------------------------------------------------------------
    # Update debian/changelog with the new version
    # -------------------------------------------------------------------------
    from packastack.debpkg.changelog import (
        generate_changelog_message,
        get_current_version,
        update_changelog,
    )

    debian_dir = pkg_repo / "debian"

    # Skip changelog update if the version already matches (e.g. resume).
    current_changelog_version = get_current_version(debian_dir / "changelog")
    if current_changelog_version == new_version:
        activity("changelog", f"debian/changelog already at {new_version}, skipping update")
        run.log_event({
            "event": "changelog.already_current",
            "version": new_version,
        })
        return PhaseResult.ok()

    git_sha = snapshot_result.git_sha if snapshot_result else ""
    signature_verified = False  # Will be updated from ctx if available

    # Look up Launchpad bug from config
    lp_bug = None
    from packastack.core.config import load_config

    cfg = load_config()
    if cfg:
        from packastack.upstream.releases import load_project_releases

        lp_bugs = cfg.get("launchpad_bugs", {})
        build_type = ctx.build_type.value if ctx.build_type else "release"

        # Check deliverable type from openstack-releases registry
        is_library = False
        releases_repo = ctx.paths.get("openstack_releases_repo")
        if releases_repo:
            proj_info = load_project_releases(releases_repo, ctx.openstack_target, ctx.package)
            if proj_info:
                is_library = proj_info.is_library()

        # Determine the upstream version to check for RC markers
        upstream_version = ""
        if ctx.upstream:
            upstream_version = ctx.upstream.version

        bug_key_type = resolve_lp_bug_key(build_type, is_library, upstream_version)

        if bug_key_type:
            lp_bug = lp_bugs.get(bug_key_type)

    # Determine upstream version for changelog message
    # For snapshots, use the version from snapshot_result
    # For releases, use the version from ctx.upstream
    if snapshot_result and snapshot_result.upstream_version:
        upstream_version_for_changelog = snapshot_result.upstream_version
    elif ctx.upstream:
        upstream_version_for_changelog = ctx.upstream.version
    else:
        upstream_version_for_changelog = ""

    changes = generate_changelog_message(
        ctx.build_type.value if ctx.build_type else "release",
        upstream_version_for_changelog,
        git_sha,
        signature_verified,
        "",  # signature_warning
        lp_bug=lp_bug,
        openstack_series=ctx.openstack_target,
    )
    if isinstance(changes, str):
        changes = [changes]

    # Use gbp dch for snapshots as well to keep changelog handling consistent.
    use_gbp = True

    changelog_updated, changelog_error = update_changelog(
        debian_dir / "changelog",
        ctx.pkg_name,
        new_version,
        ctx.resolved_ubuntu,
        changes,
        prefer_gbp=use_gbp,
    )

    if not changelog_updated:
        error_msg = f"Failed to update debian/changelog to version {new_version}: {changelog_error}"
        activity("changelog", f"ERROR: {error_msg}")
        run.log_event({"event": "changelog.update_failed", "version": new_version, "error": changelog_error})
        if not ctx.force:
            run.write_summary(
                status="failed",
                error=error_msg,
                exit_code=EXIT_PATCH_FAILED,
            )
            return PhaseResult.fail(EXIT_PATCH_FAILED, error_msg)
        activity("changelog", "Continuing despite changelog update failure (--force enabled)")

    if changelog_updated:
        activity("changelog", f"Updated debian/changelog to {new_version}")
        run.log_event({
            "event": "changelog.updated",
            "version": new_version,
        })

        # Commit the changelog update
        if (pkg_repo / ".git").exists():
            # Extract upstream version for commit message
            upstream_ver = extract_upstream_version(new_version)
            # Use simple commit message to avoid duplication in changelog
            changelog_msg = f"d/changelog: release {upstream_ver}"
            if lp_bug:
                changelog_msg += f" (LP: #{lp_bug})"

            commit_result = git_commit(
                pkg_repo,
                changelog_msg,
                files=["debian/changelog"],
            )
            if commit_result.returncode == 0:
                activity("changelog", "Committed changelog update")
                run.log_event({"event": "changelog.committed", "message": changelog_msg})
            else:
                raise GitCommitError(
                    "Failed to commit changelog update",
                    stderr=commit_result.stderr,
                    returncode=commit_result.returncode,
                )

    return PhaseResult.ok()


# =============================================================================
# Phase: Build Packages
# =============================================================================


def build_packages(
    ctx: SingleBuildContext,
    new_version: str,
) -> tuple[PhaseResult, BuildResult]:
    """Build source and optionally binary packages.

    This phase:
    1. Builds the source package with gbp buildpackage
    2. Optionally builds binary packages with sbuild or dpkg

    Args:
        ctx: Build context.
        new_version: The version to build.

    Returns:
        Tuple of (PhaseResult, BuildResult).
    """
    from packastack.build.mode import Builder
    from packastack.build.sbuild import SbuildConfig, is_sbuild_available, run_sbuild
    from packastack.debpkg.changelog import get_current_version, parse_version
    from packastack.debpkg.gbp import build_binary, build_source
    from packastack.planning.type_selection import BuildType
    from packastack.target.arch import get_host_arch

    result = BuildResult()
    run = ctx.run
    pkg_repo = ctx.pkg_repo

    activity("build", "Building source package")

    build_output = ctx.workspace / "build-output"
    build_output.mkdir(parents=True, exist_ok=True)

    use_pristine_tar = ctx.build_type != BuildType.SNAPSHOT
    source_result = build_source(pkg_repo, build_output, pristine_tar=use_pristine_tar)

    if source_result.success:
        activity("build", "Source package built successfully")
        for artifact in source_result.artifacts:
            activity("build", f"  {artifact.name}")
        run.log_event(
            {
                "event": "build.source_complete",
                "artifacts": [str(a) for a in source_result.artifacts],
            }
        )
        result.source_success = True
        result.artifacts = list(source_result.artifacts)
        result.dsc_file = source_result.dsc_file
        result.changes_file = source_result.changes_file
    else:
        activity("build", f"Source build failed: {source_result.output}")
        run.write_summary(status="failed", error="Source build failed", exit_code=EXIT_BUILD_FAILED)
        return PhaseResult.fail(EXIT_BUILD_FAILED, "Source build failed"), result

    # Optional binary build
    if ctx.binary and source_result.dsc_file:
        use_builder = Builder.SBUILD if ctx.builder == "sbuild" else Builder.DPKG
        host_arch = get_host_arch()

        if use_builder == Builder.SBUILD:
            if not is_sbuild_available():
                activity("build", "sbuild not available, falling back to dpkg-buildpackage")
                use_builder = Builder.DPKG
            else:
                # Ensure local repo has indexes before sbuild
                if not ctx.skip_repo_regen and not ctx.archive_deps:
                    from packastack.build.localrepo_helpers import refresh_local_repo_indexes
                    refresh_local_repo_indexes(ctx.local_repo, host_arch, run, phase="build")

                sbuild_config = SbuildConfig(
                    dsc_path=source_result.dsc_file,
                    output_dir=build_output,
                    distribution=ctx.resolved_ubuntu,
                    arch=host_arch,
                    local_repo_root=None if ctx.archive_deps else ctx.local_repo,
                    chroot_name=ctx.schroot_name,
                    run_log_dir=run.logs_path,
                    source_package=ctx.package,
                    version=str(
                        parse_version(get_current_version(pkg_repo / "debian" / "changelog"))
                    )
                    if pkg_repo
                    else None,
                    lintian_suppress_tags=["inconsistent-maintainer"],
                    # -proposed needs network for the session apt update
                    proposed=not ctx.offline,
                    mirror=(ctx.cfg or {})
                    .get("mirrors", {})
                    .get("ubuntu_archive", "http://archive.ubuntu.com/ubuntu"),
                    components=(ctx.cfg or {})
                    .get("defaults", {})
                    .get("ubuntu_components", ["main", "universe"]),
                )

                activity("build", f"Running sbuild (binary): {source_result.dsc_file.name}")
                activity("build", f"sbuild logs will be captured to: {run.logs_path}/sbuild.*.log")

                with activity_spinner(
                    "sbuild",
                    f"Building {source_result.dsc_file.name} ({ctx.resolved_ubuntu}/{host_arch})",
                    disable=ctx.no_spinner,
                ):
                    sbuild_result = run_sbuild(sbuild_config)

                activity("build", f"sbuild exited: {sbuild_result.exit_code}")

                run.log_event(
                    {
                        "event": "build.sbuild_command",
                        "command": sbuild_result.command,
                        "exit_code": sbuild_result.exit_code,
                        "stdout_path": str(sbuild_result.stdout_log_path)
                        if sbuild_result.stdout_log_path
                        else None,
                        "stderr_path": str(sbuild_result.stderr_log_path)
                        if sbuild_result.stderr_log_path
                        else None,
                    }
                )

                result.sbuild_result = sbuild_result

                if sbuild_result.success:
                    deb_count = sum(
                        1
                        for a in sbuild_result.collected_artifacts
                        if a.source_path.suffix in {".deb", ".udeb"}
                    )
                    activity(
                        "build",
                        f"collected binaries: {deb_count} debs",
                    )
                    activity("build", "Binary package built successfully (sbuild)")
                    for artifact in sbuild_result.artifacts:
                        activity("build", f"  {artifact.name}")
                    versions = sbuild_result.python_versions_tested
                    if versions:
                        activity(
                            "build",
                            f"pybuild exercised Python versions: {', '.join(versions)}",
                        )
                        if len(versions) == 1:
                            activity(
                                "build",
                                f"Note: only Python {versions[0]} was exercised "
                                "(package likely does not build for all supported "
                                "versions; not a failure)",
                            )
                    run.log_event(
                        {
                            "event": "build.binary_complete",
                            "builder": "sbuild",
                            "artifacts": [str(a) for a in sbuild_result.artifacts],
                            "deb_count": deb_count,
                            "python_versions_tested": versions,
                        }
                    )
                    result.artifacts.extend(sbuild_result.artifacts)
                    result.binary_success = True
                else:
                    activity("build", "ERROR: no binaries found; check logs")
                    activity("build", f"Binary build failed: {sbuild_result.validation_message}")
                    run.log_event(
                        {
                            "event": "build.binary_failed",
                            "builder": "sbuild",
                            "exit_code": sbuild_result.exit_code,
                            "validation_message": sbuild_result.validation_message,
                        }
                    )
                    run.write_summary(
                        status="failed",
                        error=f"Binary build failed: {sbuild_result.validation_message}",
                        exit_code=EXIT_BUILD_FAILED,
                    )
                    return PhaseResult.fail(EXIT_BUILD_FAILED, "Binary build failed"), result

        if use_builder == Builder.DPKG:
            activity("build", "Building binary package with dpkg-buildpackage")
            binary_result = build_binary(source_result.dsc_file, build_output, ctx.resolved_ubuntu)
            if binary_result.success:
                activity("build", "Binary package built successfully (dpkg)")
                for artifact in binary_result.artifacts:
                    activity("build", f"  {artifact.name}")
                run.log_event(
                    {
                        "event": "build.binary_complete",
                        "builder": "dpkg",
                        "artifacts": [str(a) for a in binary_result.artifacts],
                    }
                )
                result.binary_success = True
            else:
                activity("build", f"Binary build failed: {binary_result.output}")
                run.log_event(
                    {"event": "build.binary_failed", "builder": "dpkg", "output": binary_result.output}
                )

    return PhaseResult.ok(), result


# =============================================================================
# Phase: Verify and Publish
# =============================================================================


def verify_and_publish(
    ctx: SingleBuildContext,
    build_result: BuildResult,
) -> PhaseResult:
    """Verify build artifacts and publish to local repository.

    This phase:
    1. Verifies build artifacts exist
    2. Publishes artifacts to local APT repository
    3. Regenerates package indexes

    Args:
        ctx: Build context.
        build_result: Result from build phase.

    Returns:
        PhaseResult indicating success or failure.
    """
    from packastack.apt import localrepo
    from packastack.target.arch import get_host_arch

    run = ctx.run

    activity("verify", "Verifying build artifacts")

    if build_result.dsc_file and build_result.dsc_file.exists():
        activity("verify", f"Source: {build_result.dsc_file.name}")
    if build_result.changes_file and build_result.changes_file.exists():
        activity("verify", f"Changes: {build_result.changes_file.name}")

    host_arch = get_host_arch()

    if build_result.artifacts:
        activity("verify", "Publishing artifacts to local APT repository")

        for art in build_result.artifacts:
            activity("verify", f"  artifact to publish: {art}")

        [a for a in build_result.artifacts if a.suffix in {".deb", ".udeb", ".ddeb"}]

        publish_result = localrepo.publish_artifacts(
            artifact_paths=build_result.artifacts,
            repo_root=ctx.local_repo,
            arch=host_arch,
        )

        if publish_result.success:
            published_debs = [
                p for p in publish_result.published_paths if p.suffix in {".deb", ".udeb", ".ddeb"}
            ]
            activity("verify", f"Published binaries: {len(published_debs)} debs")
            activity("verify", f"Published {len(publish_result.published_paths)} files to local repo")
            run.log_event(
                {
                    "event": "verify.publish",
                    "published": [str(p) for p in publish_result.published_paths],
                    "deb_count": len(published_debs),
                }
            )

            if not ctx.skip_repo_regen:
                from packastack.build.localrepo_helpers import refresh_local_repo_indexes
                refresh_local_repo_indexes(ctx.local_repo, host_arch, run)
        else:
            activity("verify", f"Warning: Failed to publish artifacts: {publish_result.error}")
            run.log_event({"event": "verify.publish_failed", "error": publish_result.error})
            if not ctx.skip_repo_regen:
                from packastack.build.localrepo_helpers import refresh_local_repo_indexes
                refresh_local_repo_indexes(ctx.local_repo, host_arch, run)
    else:
        activity("verify", "No build artifacts to publish; ensuring local repo metadata exists")
        if not ctx.skip_repo_regen:
            from packastack.build.localrepo_helpers import refresh_local_repo_indexes
            refresh_local_repo_indexes(ctx.local_repo, host_arch, run)

    activity("verify", "Verification complete")
    return PhaseResult.ok()


# =============================================================================
# Orchestrator: Build Single Package
# =============================================================================


@dataclass
class SingleBuildOutcome:
    """Complete result of building a single package."""

    success: bool
    exit_code: int = EXIT_SUCCESS
    error: str = ""
    new_version: str = ""
    build_type: str = ""
    artifacts: list[Path] = field(default_factory=list)
    signature_verified: bool = False
    python_versions_tested: list[str] = field(default_factory=list)


# =============================================================================
# Debian Fixup Helpers
# =============================================================================


def _fix_sudoers_args(ctx: SingleBuildContext) -> None:
    """Remove command arguments from sudoers files for sudo-rs compatibility.

    sudo-rs does not support wildcard (*) matching in command arguments.
    In sudoers syntax a command with no arguments already matches any
    invocation, so stripping all arguments preserves the intended behaviour.
    This fixes any ``debian/*_sudoers`` files and commits the change.
    """
    from packastack.debpkg.sudoers import fix_sudoers_in_debian_dir

    debian_dir = ctx.pkg_repo / "debian"
    result = fix_sudoers_in_debian_dir(debian_dir)

    if result.files_fixed:
        fixed_names = ", ".join(result.files_fixed)
        activity("sudoers", f"Fixed sudoers command arguments in: {fixed_names}")
        ctx.run.log_event({
            "event": "sudoers.fixed",
            "files": result.files_fixed,
        })
        commit_result = git_commit(
            ctx.pkg_repo,
            "d/sudoers: remove command arguments for sudo-rs compatibility",
            files=[f"debian/{f}" for f in result.files_fixed],
        )
        if commit_result.returncode == 0:
            activity("sudoers", "Committed sudoers fix")
        else:
            activity("sudoers", f"Warning: failed to commit sudoers fix: {commit_result.stderr}")

    if result.errors:
        for err in result.errors:
            activity("sudoers", f"Warning: {err}")


# AI Diagnosis Helpers
# =============================================================================


def _ai_diagnose_patch_failure(
    ctx: SingleBuildContext,
    phase_result: PhaseResult,
) -> bool:
    """Run AI diagnosis on a patch failure, drop confirmed patches if safe.

    Extracts failing patch names from the error output, asks AI whether
    each can be safely dropped, and removes confirmed patches so the
    caller can retry ``import_and_patch``.

    Args:
        ctx: Build context.
        phase_result: Failed PhaseResult from import_and_patch.

    Returns:
        True if one or more patches were dropped (caller should retry).
    """
    from packastack.ai.patch_diagnosis import (
        attempt_mechanical_refresh,
        diagnose_patch_failure,
        refresh_failing_patch,
    )
    from packastack.debpkg.gbp import (
        _extract_patch_name_from_line,
        drop_patch,
        run_command,
    )

    activity("ai", "Diagnosing patch failure...")

    # Ensure .pc/ (quilt state dir) is excluded from git so gbp does not
    # treat it as an uncommitted change during retry.
    _exclude_path = ctx.pkg_repo / ".git" / "info" / "exclude"
    try:
        _exclude_path.parent.mkdir(parents=True, exist_ok=True)
        _existing = _exclude_path.read_text() if _exclude_path.exists() else ""
        if ".pc" not in _existing.splitlines():
            with _exclude_path.open("a") as fh:
                fh.write(".pc\n")
    except OSError:
        pass  # best-effort — not critical

    pq_output = phase_result.error or ""
    version = ctx.upstream.version if ctx.upstream else ""

    # Extract failing patch names from the gbp error output
    failing_patches: list[str] = []
    for line in pq_output.splitlines():
        name = _extract_patch_name_from_line(line)
        if name and name not in failing_patches:
            failing_patches.append(name)

    # If we couldn't parse any names, try all patches from the series file
    if not failing_patches:
        series_file = ctx.pkg_repo / "debian" / "patches" / "series"
        if series_file.exists():
            with contextlib.suppress(OSError):
                failing_patches = [
                    line.strip()
                    for line in series_file.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.strip().startswith("#")
                ]

    if not failing_patches:
        activity("ai", "Could not identify failing patches from error output")
        return False

    dropped_any = False
    refreshed_any = False
    for patch_name in failing_patches:
        patch_path = ctx.pkg_repo / "debian" / "patches" / patch_name
        if not patch_path.exists():
            continue

        # ---- Gate: verify the patch is actually in upstream ----
        # Only consider dropping when `git apply --check --reverse` succeeds,
        # proving every hunk is already present in the working tree.
        reverse_rc, _, _reverse_err = run_command(
            ["git", "apply", "--check", "--reverse", str(patch_path)],
            cwd=ctx.pkg_repo,
        )
        if reverse_rc != 0:
            activity(
                "ai",
                f"Patch '{patch_name}' is NOT fully upstreamed "
                f"(reverse-apply failed) — attempting refresh",
            )

            # ---- Strategy 1: mechanical refresh (fuzz / whitespace) ----
            mech_result = attempt_mechanical_refresh(
                patch_name=patch_name,
                patch_path=patch_path,
                pkg_repo=ctx.pkg_repo,
            )

            # ---- Strategy 2: AI refresh (fallback) ----
            if not mech_result.refreshed:
                activity(
                    "ai",
                    f"Mechanical refresh failed for '{patch_name}': "
                    f"{mech_result.error} — trying AI refresh",
                )

                # Read patch content for AI context
                refresh_patch_content = ""
                with contextlib.suppress(OSError):
                    refresh_patch_content = patch_path.read_text(
                        encoding="utf-8", errors="replace"
                    )

                mech_result = refresh_failing_patch(
                    patch_name=patch_name,
                    patch_content=refresh_patch_content,
                    pq_output=pq_output,
                    pkg_repo=ctx.pkg_repo,
                    pkg_name=ctx.pkg_name,
                    version=version,
                    cfg=ctx.cfg,
                )

            # ---- Apply the result (from either strategy) ----
            refresh_result = mech_result
            if refresh_result.refreshed:
                # Overwrite the original patch with the refreshed version.
                # Do NOT commit here — the retry of import_and_patch will
                # run pq_export which commits all refreshed patches together
                # as "d/patches/*: refresh patches".
                try:
                    patch_path.write_text(
                        refresh_result.patch_content, encoding="utf-8"
                    )
                except OSError as exc:
                    activity("ai", f"Failed to write refreshed patch: {exc}")
                    continue

                activity("ai", f"Refreshed patch: {patch_name}")
                ctx.run.log_event({
                    "event": "ai.patch_refreshed",
                    "patch_name": patch_name,
                    "explanation": refresh_result.explanation,
                })
                refreshed_any = True
            else:
                reason = refresh_result.error or refresh_result.explanation
                activity(
                    "ai",
                    f"Could not refresh '{patch_name}': {reason}",
                )
            continue

        # Read patch content for AI context
        patch_content = ""
        with contextlib.suppress(OSError):
            patch_content = patch_path.read_text(encoding="utf-8", errors="replace")

        diagnosis = diagnose_patch_failure(
            patch_name=patch_name,
            patch_content=patch_content,
            pq_output=pq_output,
            pkg_name=ctx.pkg_name,
            version=version,
            ubuntu_series=ctx.resolved_ubuntu,
            cfg=ctx.cfg,
        )

        if not diagnosis.diagnosed:
            if diagnosis.error:
                activity("ai", f"AI diagnosis unavailable: {diagnosis.error}")
            continue

        activity("ai", f"Diagnosis: {diagnosis.explanation}")

        if not diagnosis.can_drop:
            activity("ai", f"Patch '{patch_name}' should be kept — cannot auto-fix")
            continue

        activity("ai", f"Patch '{patch_name}' appears upstreamed and safe to drop")

        # Drop the patch
        drop_result = drop_patch(ctx.pkg_repo, patch_name)
        if not drop_result.success:
            activity("ai", f"Failed to drop patch: {drop_result.error}")
            continue

        # Commit the removal
        commit_result = git_commit(
            ctx.pkg_repo,
            f"d/patches: drop upstreamed {patch_name}",
            files=["debian/patches"],
        )
        if commit_result.returncode != 0:
            activity("ai", f"Failed to commit patch drop: {commit_result.stderr}")
            continue

        activity("ai", f"Dropped patch: {patch_name}")
        ctx.run.log_event({
            "event": "ai.patch_dropped",
            "patch_name": patch_name,
            "explanation": diagnosis.explanation,
        })
        dropped_any = True

    return dropped_any or refreshed_any


def _ai_diagnose_and_retry_build(
    ctx: SingleBuildContext,
    build_data: BuildResult,
    new_version: str,
) -> tuple[PhaseResult | None, BuildResult]:
    """Run AI diagnosis on a build failure, apply patch and retry if proposed.

    Loads any previous AI memory for this package so the model can
    learn from earlier attempts.  On failure the memory is saved; on
    success it is deleted.

    Args:
        ctx: Build context.
        build_data: Failed BuildResult containing sbuild_result.
        new_version: Version string for the build.

    Returns:
        Tuple of (retry_phase_result, retry_build_data) if retry succeeded,
        or (None, build_data) if no retry was attempted or retry failed.
    """
    from packastack.ai.build_diagnosis import (
        apply_ai_fix,
        diagnose_build_failure,
    )
    from packastack.ai.memory import (
        AIMemory,
        delete_memory,
        find_latest_memory,
        save_memory,
    )

    activity("ai", "Diagnosing build failure...")

    if not build_data.sbuild_result:
        activity("ai", "No sbuild result available for diagnosis")
        return None, build_data

    # Load previous AI memory for this package
    build_root = ctx.paths.get("build_root", ctx.paths["cache_root"] / "build")
    previous_memory = find_latest_memory(build_root / ctx.pkg_name, ctx.pkg_name)
    memory_context = previous_memory.format_for_prompt() if previous_memory else ""
    if memory_context:
        activity("ai", f"Loaded {len(previous_memory.attempts)} previous AI attempt(s)")

    from packastack.target.arch import get_host_arch

    diagnosis = diagnose_build_failure(
        sbuild_result=build_data.sbuild_result,
        pkg_repo=ctx.pkg_repo,
        pkg_name=ctx.pkg_name,
        version=new_version,
        ubuntu_series=ctx.resolved_ubuntu,
        arch=get_host_arch(),
        cfg=ctx.cfg,
        ai_memory_context=memory_context,
    )

    if not diagnosis.diagnosed:
        if diagnosis.error:
            activity("ai", f"AI diagnosis unavailable: {diagnosis.error}")
        return None, build_data

    if diagnosis.needs_patch:
        activity("ai", f"AI proposes quilt patch: {diagnosis.patch_filename}")
        activity("ai", diagnosis.explanation)
    else:
        activity("ai", f"AI diagnosis: {diagnosis.explanation}")

    if diagnosis.needs_patch:
        if apply_ai_fix(ctx.pkg_repo, diagnosis, cfg=ctx.cfg):
            activity("ai", "Retrying build with AI-proposed fix...")
            retry_phase, retry_data = build_packages(ctx, new_version)
            if retry_phase.success:
                activity(
                    "ai",
                    f"Build passed with AI patch: {diagnosis.patch_filename}",
                )
                # Clean up memory on success
                delete_memory(ctx.run.logs_path)
                return retry_phase, retry_data

            # Build failed after AI fix -- save memory for next run
            activity("ai", "Build still failed after AI fix. Saving memory for next run.")
            memory = previous_memory or AIMemory(
                package=ctx.pkg_name, version=new_version
            )
            sbuild_error = ""
            if retry_data.sbuild_result:
                sbuild_error = getattr(retry_data.sbuild_result, "validation_message", "")
            memory.add_attempt(
                patch_filename=diagnosis.patch_filename,
                patch_content=diagnosis.patch_content,
                build_error=sbuild_error,
                diagnosis=diagnosis.explanation,
                outcome="build_failed",
            )
            save_memory(memory, ctx.run.logs_path)
        else:
            activity("ai", "Failed to apply AI-proposed fix")
            # Save memory about invalid fix
            memory = previous_memory or AIMemory(
                package=ctx.pkg_name, version=new_version
            )
            memory.add_attempt(
                patch_filename=diagnosis.patch_filename,
                patch_content=diagnosis.patch_content,
                build_error="Fix failed to apply",
                diagnosis=diagnosis.explanation,
                outcome="patch_invalid",
            )
            save_memory(memory, ctx.run.logs_path)
    else:
        activity("ai", "Build Failure Diagnosis:")
        activity("ai", diagnosis.explanation)

    return None, build_data


def build_single_package(
    ctx: SingleBuildContext,
    workspace_ref: Any = None,
) -> SingleBuildOutcome:
    """Orchestrate building a single package through all phases.

    This function coordinates all the phase functions to build a package:
    1. fetch_packaging_repo - Clone/update packaging repo
    2. prepare_upstream_source - Acquire upstream tarball
    3. validate_and_build_deps - Validate dependencies
    4. import_and_patch - Import tarball and apply patches
    5. build_packages - Build source and binary packages
    6. verify_and_publish - Publish to local repo

    Args:
        ctx: Fully configured SingleBuildContext.
        workspace_ref: Optional callback to receive workspace path.

    Returns:
        SingleBuildOutcome with build results.
    """
    run = ctx.run
    outcome = SingleBuildOutcome(
        success=False,
        build_type=ctx.build_type_str,
    )

    # -------------------------------------------------------------------------
    # Phase 1: Fetch packaging repository
    # -------------------------------------------------------------------------
    fetch_result_phase, _fetch_data = fetch_packaging_repo(ctx, workspace_ref)
    if not fetch_result_phase.success:
        outcome.exit_code = fetch_result_phase.exit_code
        outcome.error = fetch_result_phase.error
        return outcome

    # Dependency satisfaction reporting (build and runtime)
    deps_report_result = report_dependency_satisfaction(ctx)
    if not deps_report_result.success:
        outcome.exit_code = deps_report_result.exit_code
        outcome.error = deps_report_result.error
        return outcome

    # -------------------------------------------------------------------------
    # Phase 2: Prepare upstream source
    # -------------------------------------------------------------------------
    prepare_result_phase, prepare_data = prepare_upstream_source(ctx)
    if not prepare_result_phase.success:
        outcome.exit_code = prepare_result_phase.exit_code
        outcome.error = prepare_result_phase.error
        return outcome

    outcome.new_version = prepare_data.new_version
    outcome.signature_verified = prepare_data.signature_verified

    # -------------------------------------------------------------------------
    # Phase 3: Validate dependencies (and auto-build if enabled)
    # -------------------------------------------------------------------------
    validate_result_phase, _validate_data = validate_and_build_deps(
        ctx,
        upstream_tarball=prepare_data.upstream_tarball,
        snapshot_result=prepare_data.snapshot_result,
    )
    if not validate_result_phase.success:
        outcome.exit_code = validate_result_phase.exit_code
        outcome.error = validate_result_phase.error
        return outcome

    # -------------------------------------------------------------------------
    # Phase 4: Import upstream and apply patches
    # -------------------------------------------------------------------------
    import_result_phase = import_and_patch(
        ctx,
        upstream_tarball=prepare_data.upstream_tarball,
        snapshot_result=prepare_data.snapshot_result,
        new_version=prepare_data.new_version,
    )
    if not import_result_phase.success:
        # AI patch diagnosis — drop confirmed patches and retry
        patches_dropped = False
        if ctx.ai_enabled and ctx.cfg:
            from packastack.ai.client import is_ai_available

            if is_ai_available(ctx.cfg):
                patches_dropped = _ai_diagnose_patch_failure(ctx, import_result_phase)

        if patches_dropped:
            activity("ai", "Retrying patch import after fixing patches...")
            import_result_phase = import_and_patch(
                ctx,
                upstream_tarball=prepare_data.upstream_tarball,
                snapshot_result=prepare_data.snapshot_result,
                new_version=prepare_data.new_version,
                ignore_new=True,
            )

        if not import_result_phase.success:
            outcome.exit_code = import_result_phase.exit_code
            outcome.error = import_result_phase.error
            return outcome

    # -------------------------------------------------------------------------
    # Phase 5: Build packages
    # -------------------------------------------------------------------------
    build_result_phase, build_data = build_packages(ctx, prepare_data.new_version)
    if not build_result_phase.success:
        # AI build failure diagnosis with optional retry
        if ctx.ai_enabled and ctx.cfg:
            from packastack.ai.client import is_ai_available

            if is_ai_available(ctx.cfg):
                retry_phase, retry_data = _ai_diagnose_and_retry_build(
                    ctx, build_data, prepare_data.new_version
                )
                if retry_phase is not None and retry_phase.success:
                    outcome.success = True
                    outcome.exit_code = EXIT_SUCCESS
                    outcome.artifacts = retry_data.artifacts
                    # Continue to Phase 6 with retry data
                    build_result_phase = retry_phase
                    build_data = retry_data

        if not build_result_phase.success:
            outcome.exit_code = build_result_phase.exit_code
            outcome.error = build_result_phase.error
            return outcome

    outcome.artifacts = build_data.artifacts
    if build_data.sbuild_result is not None:
        outcome.python_versions_tested = build_data.sbuild_result.python_versions_tested

    # -------------------------------------------------------------------------
    # Phase 6: Verify and publish
    # -------------------------------------------------------------------------
    verify_result_phase = verify_and_publish(ctx, build_data)
    if not verify_result_phase.success:
        outcome.exit_code = verify_result_phase.exit_code
        outcome.error = verify_result_phase.error
        return outcome

    # -------------------------------------------------------------------------
    # Phase 7: Provenance
    # -------------------------------------------------------------------------
    if ctx.provenance:
        from packastack.build.provenance import write_provenance

        # Update provenance with final details
        ctx.provenance.verification.result = (
            "verified" if prepare_data.signature_verified else "skipped"
        )
        if prepare_data.signature_warning:
            ctx.provenance.verification.result = "not_applicable"

        # Write provenance file
        try:
            provenance_path = write_provenance(ctx.provenance, run.logs_path)
            activity("provenance", f"Written to: {provenance_path}")
            run.log_event({
                "event": "provenance.written",
                "path": str(provenance_path),
            })
        except Exception as e:
            activity("provenance", f"Warning: Failed to write provenance: {e}")
            run.log_event({"event": "provenance.write_failed", "error": str(e)})

    # -------------------------------------------------------------------------
    # Phase 8: Report
    # -------------------------------------------------------------------------
    activity("report", "Build Summary")
    activity("report", f"  Package: {ctx.pkg_name}")
    activity("report", f"  Version: {prepare_data.new_version}")
    activity("report", f"  Build type: {ctx.build_type.value}")
    if ctx.resolution_source:
        activity("report", f"  Upstream resolution: {ctx.resolution_source.value}")
    activity("report", f"  Workspace: {ctx.workspace}")

    # Success!
    outcome.success = True
    outcome.exit_code = EXIT_SUCCESS
    return outcome
