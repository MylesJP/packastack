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
    EXIT_SUCCESS,
    EXIT_TOOL_MISSING,
)
from packastack.build.git_helpers import (
    _ensure_no_merge_paths,
    _get_git_author_env,
    _maybe_disable_gpg_sign,
    _maybe_enable_sphinxdoc,
)
from packastack.build.tarball import _fetch_release_tarball
from packastack.core.run import activity
from packastack.core.spinner import activity_spinner
from packastack.debpkg.gbp import run_command
from packastack.upstream.gitfetch import GitFetcher

if TYPE_CHECKING:
    from packastack.apt.packages import PackageIndex
    from packastack.build.provenance import BuildProvenance
    from packastack.core.run import RunContext
    from packastack.planning.type_selection import BuildType
    from packastack.upstream.registry import ResolvedUpstream
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
    milestone: str
    binary: bool
    builder: str
    force: bool
    offline: bool
    use_gbp_dch: bool
    skip_repo_regen: bool
    no_spinner: bool
    build_deps: bool

    # Paths
    paths: dict[str, Path]
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
    openstack_pkgs: dict[str, str] | None = None

    # Schroot
    schroot_name: str | None = None

    # Provenance
    provenance: BuildProvenance | None = None


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
    milestone: str
    binary: bool
    builder: str
    force: bool
    offline: bool
    use_gbp_dch: bool
    skip_repo_regen: bool
    no_spinner: bool
    build_deps: bool
    include_retired: bool
    
    # Resolved values from planning
    resolved_build_type_str: str
    milestone_from_cli: str
    
    # Paths and config
    paths: dict[str, Path]
    cfg: dict[str, Any]
    
    # Run context
    run: Any  # RunContext


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
    from packastack.commands.build import (
        _build_type_from_string,
        get_previous_series,
        is_snapshot_eligible,
        load_openstack_packages,
        select_upstream_source,
    )
    from packastack.core.context import resolve_series
    from packastack.debpkg.arch import get_host_arch
    from packastack.planning.type_selection import BuildType
    from packastack.upstream.releases import get_current_development_series
    
    run = inputs.run
    paths = inputs.paths
    cfg = inputs.cfg
    pkg_name = inputs.pkg_name
    
    # Derive project name from package name
    if pkg_name.startswith("python-"):
        package = pkg_name[7:]
    else:
        package = pkg_name
    
    # Resolve series
    resolved_ubuntu = resolve_series(inputs.ubuntu_series)
    releases_repo = paths["openstack_releases_repo"]
    if inputs.target == "devel":
        openstack_target = get_current_development_series(releases_repo) or inputs.target
    else:
        openstack_target = inputs.target
    local_repo = paths["local_apt_repo"]
    
    activity("resolve", f"Package: {pkg_name}")
    run.log_event({"event": "resolve.package", "name": pkg_name})
    
    # -------------------------------------------------------------------------
    # Phase 1: Retirement check
    # -------------------------------------------------------------------------
    project_config_path = paths.get("openstack_project_config")
    retirement_result, retirement_info = check_retirement_status(
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
    
    # Build type already resolved before planning
    build_type = _build_type_from_string(inputs.resolved_build_type_str)
    milestone_str = inputs.milestone_from_cli
    
    run.log_event({"event": "resolve.build_type", "type": build_type.value, "milestone": milestone_str})
    
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
        eligible, reason, preferred = is_snapshot_eligible(releases_repo, openstack_target, package)
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
    
    # Load OpenStack packages
    openstack_pkgs = load_openstack_packages(releases_repo, openstack_target)
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
        milestone_str,
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
        milestone=milestone_str,
        binary=inputs.binary,
        builder=inputs.builder,
        force=inputs.force,
        offline=inputs.offline,
        use_gbp_dch=inputs.use_gbp_dch,
        skip_repo_regen=inputs.skip_repo_regen,
        no_spinner=inputs.no_spinner,
        build_deps=inputs.build_deps,
        paths=paths,
        local_repo=local_repo,
        tarball_cache_base=tarball_cache_base,
        upstream_config=upstream_config,
        upstream=upstream,
        resolution_source=resolution_source,
        prev_series=prev_series,
        ubuntu_index=ubuntu_index,
        ca_index=ca_index,
        local_index=local_index,
        openstack_pkgs=openstack_pkgs,
        schroot_name=schroot_name,
        provenance=provenance,
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

    # Create workspace
    build_root = ctx.paths.get("build_root", ctx.paths["cache_root"] / "build")
    workspace = build_root / run.run_id / ctx.pkg_name
    workspace.mkdir(parents=True, exist_ok=True)
    if workspace_ref:
        workspace_ref(workspace)

    result.workspace = workspace
    ctx.workspace = workspace

    # Mirror RunContext logs into the build workspace
    try:
        run.add_log_mirror(workspace / "logs")
    except Exception:
        pass

    # Clone packaging repo
    fetcher = GitFetcher()
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

    # Protect packaging-only files from being removed during upstream merges
    _ensure_no_merge_paths(pkg_repo, ["launchpad.yaml"])

    # Commit .gitattributes so it's active during import-orig merge
    gitattributes = pkg_repo / ".gitattributes"
    if gitattributes.exists():
        try:
            activity("fetch", "Committing .gitattributes for merge protection")
            run_command(["git", "add", ".gitattributes"], cwd=pkg_repo)
            commit_cmd = _maybe_disable_gpg_sign(
                ["git", "commit", "-m", "Protect packaging files during merge"]
            )
            returncode, stdout, stderr = run_command(
                commit_cmd, cwd=pkg_repo, env=_get_git_author_env()
            )
            if returncode == 0:
                activity("fetch", ".gitattributes committed successfully")
            else:
                activity("fetch", f".gitattributes commit result: {returncode}")
        except Exception as e:
            activity("fetch", f".gitattributes commit failed: {e}")

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
        fix_oslo_watch_pattern,
        parse_watch_file,
        update_signing_key,
        upgrade_watch_version,
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
    signing_key_updated = False
    files_to_commit = []

    if upgrade_watch_version(watch_path):
        activity("prepare", "Updated debian/watch to version=4")
        watch_updated = True

    # Fix oslo.* watch patterns to accept both oslo.* and oslo_* naming
    if fix_oslo_watch_pattern(watch_path, ctx.package):
        activity(
            "prepare",
            f"Updated debian/watch to accept {ctx.package} or {ctx.package.replace('.', '_')} naming",
        )
        watch_updated = True

    # Update or remove signing key based on build type
    from packastack.planning.type_selection import BuildType

    is_snapshot = ctx.build_type == BuildType.SNAPSHOT
    releases_repo = ctx.paths.get("openstack_releases_repo")
    if update_signing_key(pkg_repo, releases_repo, ctx.openstack_target, is_snapshot):
        signing_key_updated = True
        if is_snapshot:
            activity("prepare", "Removed debian/upstream/signing-key.asc for snapshot build")
        else:
            activity("prepare", f"Updated debian/upstream/signing-key.asc for {ctx.openstack_target}")

    # Commit watch file and signing key together before uscan runs
    if watch_updated:
        files_to_commit.append("debian/watch")
    if signing_key_updated:
        files_to_commit.append("debian/upstream/signing-key.asc")

    if files_to_commit:
        commit_parts = []
        if watch_updated:
            commit_parts.append("Update debian/watch")
        if signing_key_updated:
            if is_snapshot:
                commit_parts.append("remove signing key for snapshot")
            else:
                commit_parts.append(f"update signing key for {ctx.openstack_target}")

        commit_msg = " and ".join(commit_parts)
        commit_cmd = _maybe_disable_gpg_sign(["git", "commit", "-m", commit_msg] + files_to_commit)

        exit_code, stdout, stderr = run_command(commit_cmd, cwd=pkg_repo, env=_get_git_author_env())
        if exit_code == 0:
            activity("prepare", "Committed watch and signing key updates")
        else:
            activity("warn", f"Failed to commit updates: {stderr}")

    # Ensure sphinxdoc addon is enabled before patch application/commits
    _maybe_enable_sphinxdoc(pkg_repo)

    result.watch_updated = watch_updated
    result.signing_key_updated = signing_key_updated

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
    from packastack.debpkg.changelog import get_current_version, increment_upstream_version, parse_version
    from packastack.debpkg.launchpad_yaml import update_launchpad_yaml_series
    from packastack.planning.type_selection import BuildType
    from packastack.upstream.releases import load_series_info
    from packastack.upstream.source import (
        SnapshotAcquisitionResult,
        SnapshotRequest,
        TarballResult,
        acquire_upstream_snapshot,
        apply_signature_policy,
        select_upstream_source,
    )
    from packastack.upstream.tarball_cache import TarballCacheEntry, cache_tarball, find_cached_tarball

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
        ctx.milestone,
    )
    ctx.upstream = upstream

    if upstream is None and ctx.build_type != BuildType.SNAPSHOT:
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
            snapshot_request = SnapshotRequest(
                project=ctx.package,
                base_version=base_version,
                branch=upstream_branch,
                git_ref="HEAD",
                package_name=ctx.pkg_name,
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
        # Release/milestone: uscan first, then official, then fallbacks
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
    from packastack.planning.type_selection import BuildType
    from packastack.planning.validated_plan import (
        extract_upstream_deps,
        map_python_to_debian,
        project_to_source_package,
        resolve_dependency_with_spec,
    )
    from packastack.upstream.tarball_cache import extract_tarball

    result = ValidateDepsResult()
    run = ctx.run

    activity("validate-deps", "Validating upstream dependencies")

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
            debian_name, uncertain = map_python_to_debian(python_dep)
            if not debian_name:
                activity("validate-deps", f"  {python_dep} -> (unmapped)")
                continue

            version, source, satisfied = resolve_dependency_with_spec(
                debian_name, version_spec, ctx.local_index, ctx.ca_index, ctx.ubuntu_index
            )

            spec_display = f" (req: {version_spec})" if version_spec else ""
            if version:
                resolved_count += 1
                status = "✓ SATISFIED" if satisfied else "✗ OUTDATED"
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
                        "satisfied": satisfied,
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
        else:
            activity("validate-deps", "All dependencies resolved")
    else:
        activity("validate-deps", "Skipping - no upstream repo available")

    result.missing_deps = missing_deps_list
    result.buildable_deps = new_deps_to_build

    # Auto-build phase
    if ctx.build_deps and new_deps_to_build:
        phase_result = _auto_build_deps(ctx, new_deps_to_build)
        if not phase_result.success:
            return phase_result, result

    return PhaseResult.ok(), result


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


def import_and_patch(
    ctx: SingleBuildContext,
    upstream_tarball: Path | None,
    snapshot_result: SnapshotAcquisitionResult | None,
) -> PhaseResult:
    """Import upstream tarball and apply patches.

    This phase:
    1. Ensures upstream branch exists
    2. Imports upstream tarball with gbp import-orig
    3. Applies patches with gbp pq
    4. Exports refreshed patches

    Args:
        ctx: Build context.
        upstream_tarball: Path to upstream tarball.
        snapshot_result: Snapshot result (for version info).

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

        # Extract version for import-orig
        if ctx.build_type == BuildType.SNAPSHOT and snapshot_result:
            import_version = snapshot_result.upstream_version
        elif ctx.upstream:
            import_version = ctx.upstream.version
        else:
            import_version = None

        import_result = import_orig(
            pkg_repo,
            upstream_tarball,
            upstream_version=import_version,
            upstream_branch=upstream_branch_name,
            pristine_tar=True,
            merge=True,
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
        if not ctx.force:
            activity("patches", "Use --force to continue with potentially upstreamed patches")
            run.write_summary(
                status="failed",
                error="Patches appear to be upstreamed",
                patches=[str(r) for r in upstreamed],
                exit_code=EXIT_PATCH_FAILED,
            )
            return PhaseResult.fail(EXIT_PATCH_FAILED, "Patches upstreamed")
        run.log_event({"event": "patches.upstreamed", "patches": [r.patch_name for r in upstreamed]})

    pq_result = pq_import(pkg_repo)
    if pq_result.success:
        activity("patches", "Patches applied successfully")
    elif pq_result.needs_refresh:
        activity("patches", "Patches need refresh - forcing import with time-machine")
        force_result = pq_import(pkg_repo, time_machine=0)
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
            return PhaseResult.fail(EXIT_PATCH_FAILED, "Patch import failed")

    run.log_event({"event": "patches.complete", "success": pq_result.success})

    # Export patches and return to master branch
    if (pkg_repo / ".git").exists():
        branch_rc, branch_out, _ = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=pkg_repo)
        current_branch = branch_out.strip() if branch_rc == 0 else ""
        on_patch_queue = current_branch.startswith("patch-queue/")

        if on_patch_queue:
            export_result = pq_export(pkg_repo)
            if export_result.success:
                activity("patches", "Patches exported (back to debian branch)")
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
            run_command(["git", "add", "debian/patches"], cwd=pkg_repo)
            commit_cmd = _maybe_disable_gpg_sign(["git", "commit", "-m", "Refresh patches"])
            commit_rc, commit_out, commit_err = run_command(
                commit_cmd, cwd=pkg_repo, env=_get_git_author_env()
            )
            if commit_rc == 0:
                activity("patches", "Recorded refreshed patches commit")
            else:
                activity(
                    "patches",
                    f"Patch commit skipped: {commit_err or commit_out or 'no changes to commit'}",
                )
    else:
        activity("patches", "Skipping patch export/checkout (not a git repo)")

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
    from packastack.apt import localrepo
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
                if not ctx.skip_repo_regen:
                    from packastack.commands.build import _refresh_local_repo_indexes
                    _refresh_local_repo_indexes(ctx.local_repo, host_arch, run, phase="build")

                sbuild_config = SbuildConfig(
                    dsc_path=source_result.dsc_file,
                    output_dir=build_output,
                    distribution=ctx.resolved_ubuntu,
                    arch=host_arch,
                    local_repo_root=ctx.local_repo,
                    chroot_name=ctx.schroot_name,
                    run_log_dir=run.logs_path,
                    source_package=ctx.package,
                    version=str(
                        parse_version(get_current_version(pkg_repo / "debian" / "changelog"))
                    )
                    if pkg_repo
                    else None,
                    lintian_suppress_tags=["inconsistent-maintainer"],
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
                    run.log_event(
                        {
                            "event": "build.binary_complete",
                            "builder": "sbuild",
                            "artifacts": [str(a) for a in sbuild_result.artifacts],
                            "deb_count": deb_count,
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

        deb_artifacts = [a for a in build_result.artifacts if a.suffix in {".deb", ".udeb", ".ddeb"}]

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
                from packastack.commands.build import _refresh_local_repo_indexes
                _refresh_local_repo_indexes(ctx.local_repo, host_arch, run)
        else:
            activity("verify", f"Warning: Failed to publish artifacts: {publish_result.error}")
            run.log_event({"event": "verify.publish_failed", "error": publish_result.error})
            if not ctx.skip_repo_regen:
                from packastack.commands.build import _refresh_local_repo_indexes
                _refresh_local_repo_indexes(ctx.local_repo, host_arch, run)
    else:
        activity("verify", "No build artifacts to publish; ensuring local repo metadata exists")
        if not ctx.skip_repo_regen:
            from packastack.commands.build import _refresh_local_repo_indexes
            _refresh_local_repo_indexes(ctx.local_repo, host_arch, run)

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
    fetch_result_phase, fetch_data = fetch_packaging_repo(ctx, workspace_ref)
    if not fetch_result_phase.success:
        outcome.exit_code = fetch_result_phase.exit_code
        outcome.error = fetch_result_phase.error
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
    validate_result_phase, validate_data = validate_and_build_deps(ctx)
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
    )
    if not import_result_phase.success:
        outcome.exit_code = import_result_phase.exit_code
        outcome.error = import_result_phase.error
        return outcome

    # -------------------------------------------------------------------------
    # Phase 5: Build packages
    # -------------------------------------------------------------------------
    build_result_phase, build_data = build_packages(ctx)
    if not build_result_phase.success:
        outcome.exit_code = build_result_phase.exit_code
        outcome.error = build_result_phase.error
        return outcome

    outcome.artifacts = build_data.artifacts

    # -------------------------------------------------------------------------
    # Phase 6: Verify and publish
    # -------------------------------------------------------------------------
    verify_result_phase = verify_and_publish(ctx, build_data)
    if not verify_result_phase.success:
        outcome.exit_code = verify_result_phase.exit_code
        outcome.error = verify_result_phase.error
        return outcome

    # Success!
    outcome.success = True
    outcome.exit_code = EXIT_SUCCESS
    return outcome

