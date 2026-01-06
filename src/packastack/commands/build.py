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

"""Implementation of `packastack build` command.

The BUILD phase: clones packaging repos, validates the plan against upstream,
handles patches with gbp patch-queue, builds source (and optionally binary)
packages, and produces upload orders.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import typer

from packastack.core.context import BuildContext, BuildOptions, BuildRequest, PlanRequest, PolicyConfig, TargetConfig

from packastack.debpkg.changelog import (
    generate_changelog_message,
    generate_milestone_version,
    generate_release_version,
    generate_snapshot_version,
    get_current_version,
    increment_upstream_version,
    parse_version,
    update_changelog,
)
from packastack.core.config import load_config
from packastack.debpkg.gbp import (
    PatchHealthReport,
    build_binary,
    build_source,
    check_upstreamed_patches,
    ensure_upstream_branch,
    import_orig,
    pq_export,
    pq_import,
    run_command,
)
from packastack.debpkg.gbpconf import update_gbp_conf_from_launchpad_yaml
from packastack.upstream.gitfetch import GitFetcher
from packastack.planning.graph import DependencyGraph
from packastack.debpkg.launchpad_yaml import update_launchpad_yaml_series
from packastack.apt.packages import (
    PackageIndex,
    load_cloud_archive_index,
    load_local_repo_index,
    load_package_index,
    merge_package_indexes,
)
from packastack.core.paths import resolve_paths
from packastack.upstream.releases import (
    get_current_development_series,
    get_previous_series,
    is_snapshot_eligible,
    load_openstack_packages,
    load_project_releases,
    load_series_info,
)
from packastack.core.run import RunContext, activity
from packastack.target.series import resolve_series
from packastack.core.spinner import activity_spinner
from packastack.build.tools import check_required_tools, get_missing_tools_message
from packastack.build.schroot import SchrootConfig, ensure_schroot, get_schroot_name
from packastack.planning.type_selection import (
    BuildType,
    CycleStage,
    select_build_type,
    determine_cycle_stage,
)
from packastack.upstream.source import (
    SnapshotAcquisitionResult,
    SnapshotRequest,
    TarballResult,
    UpstreamSource,
    acquire_upstream_snapshot,
    apply_signature_policy,
    download_file,
    download_and_verify_tarball,
    generate_snapshot_tarball,
    get_git_snapshot_info,
    select_upstream_source,
)
from packastack.upstream.tarball_cache import (
    TarballCacheEntry,
    cache_tarball,
    find_cached_tarball,
)
from packastack.planning.validated_plan import extract_upstream_deps, validate_plan
from packastack.apt import localrepo
from packastack.target.arch import get_host_arch
from packastack.build.mode import BuildMode, Builder
from packastack.planning.deploop import check_dependencies, DependencyBuildPlan
from packastack.build.sbuild import SbuildConfig, run_sbuild, is_sbuild_available
from packastack.build.provenance import (
    BuildProvenance,
    create_provenance,
    summarize_provenance,
    write_provenance,
    ReleaseSourceProvenance,
    TarballProvenance,
    UpstreamProvenance,
    VerificationProvenance,
    WatchMismatchProvenance,
)
from packastack.upstream.registry import (
    ResolutionSource,
)
from packastack.upstream.retirement import (
    RetirementChecker,
)
from packastack.commands.init import _clone_or_update_project_config
from packastack.debpkg.watch import (
    check_watch_mismatch,
    ensure_pgp_verification_valid,
    fix_oslo_watch_pattern,
    format_mismatch_warning,
    parse_watch_file,
    update_signing_key,
    upgrade_watch_version,
)
from packastack.debpkg.manpages import apply_man_pages_support
from packastack.debpkg.control import fix_priority_extra, ensure_misc_pre_depends
from packastack.debpkg.rules import add_doctree_cleanup

# Build helpers (refactored modules)
from packastack.build import (
    # Git helpers
    _ensure_no_merge_paths,
    _get_git_author_env,
    _maybe_disable_gpg_sign,
    _maybe_enable_sphinxdoc,
    _no_gpg_sign_enabled,
    # Tarball helpers
    _download_github_release_tarball,
    _download_pypi_tarball,
    _fetch_release_tarball,
    _run_uscan,
    # Phase functions
    check_retirement_status,
    check_tools,
    ensure_schroot_ready,
    load_package_indexes,
    resolve_upstream_registry,
    # Exit codes
    EXIT_ALL_BUILD_FAILED,
    EXIT_BUILD_FAILED,
    EXIT_CONFIG_ERROR,
    EXIT_CYCLE_DETECTED,
    EXIT_DISCOVERY_FAILED,
    EXIT_FETCH_FAILED,
    EXIT_GRAPH_ERROR,
    EXIT_MISSING_PACKAGES,
    EXIT_PATCH_FAILED,
    EXIT_POLICY_BLOCKED,
    EXIT_REGISTRY_ERROR,
    EXIT_RESUME_ERROR,
    EXIT_RETIRED_PROJECT,
    EXIT_SUCCESS,
    EXIT_TOOL_MISSING,
)
from packastack.build.type_resolution import (
    VALID_BUILD_TYPES,
    build_type_from_string,
    resolve_build_type_auto,
    resolve_build_type_from_cli,
)

# Build-all imports
import concurrent.futures
import contextlib
import json
import threading
from collections import defaultdict
from datetime import datetime

from packastack.core.context import BuildAllRequest
from packastack.debpkg.control import get_changelog_version
from packastack.debpkg.version import extract_upstream_version
from packastack.planning.cycle_suggestions import suggest_cycle_edge_exclusions
from packastack.planning.build_all_state import (
    BuildAllState,
    FailureType,
    MissingDependency,
    PackageState,
    PackageStatus,
    create_initial_state,
    load_state,
    save_state,
)
from packastack.planning.graph_builder import OPTIONAL_BUILD_DEPS
from packastack.planning.package_discovery import (
    DiscoveryResult,
    discover_packages,
    discover_packages_from_cache,
)
from packastack.planning.type_selection import get_default_parallel_workers
from packastack.reports.plan_graph import PlanGraph, render_waves

if TYPE_CHECKING:
    from packastack.core.run import RunContext as RunContextType


# Known optional dependencies that can be ignored for cycle breaking.
OPTIONAL_DEPS_FOR_CYCLE = OPTIONAL_BUILD_DEPS


# =============================================================================
# Build-all functions (migrated from build_all.py)
# =============================================================================


def _build_dependency_graph(
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


def _build_upstream_versions_from_packaging(
    packages: list[str],
    packaging_root: Path,
) -> dict[str, str]:
    """Derive upstream versions from debian/changelog entries."""
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


def _filter_retired_packages(
    packages: list[str],
    project_config_path: Path | None,
    releases_repo: Path | None,
    openstack_target: str,
    offline: bool,
    run: RunContext,
) -> tuple[list[str], list[str], list[str]]:
    """Filter retired packages using openstack/project-config and releases inference."""
    if not packages:
        return packages, [], []

    if project_config_path and not project_config_path.exists() and not offline:
        activity("all", "Cloning openstack/project-config for retirement checks")
        _clone_or_update_project_config(project_config_path, run)

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


def _get_parallel_batches(
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


def _run_single_build(
    package: str,
    target: str,
    ubuntu_series: str,
    cloud_archive: str,
    build_type: str,
    binary: bool,
    force: bool,
    run_dir: Path,
) -> tuple[bool, FailureType | None, str, str]:
    """Run a single package build as a subprocess.

    Args:
        package: Package name to build.
        target: OpenStack target series.
        ubuntu_series: Ubuntu series.
        cloud_archive: Cloud archive pocket.
        build_type: release/snapshot/milestone.
        binary: Build binary packages.
        force: Force through warnings.
        run_dir: Directory for logs.

    Returns:
        Tuple of (success, failure_type, message, log_path).
    """
    import subprocess

    cmd = [
        sys.executable, "-m", "packastack", "build",
        package,
        "--target", target,
        "--ubuntu-series", ubuntu_series,
        "--yes",  # No prompts
        "--no-cleanup",  # Keep workspace for debugging
        "--skip-repo-regen",  # Coordinator handles repo regeneration
    ]

    if cloud_archive:
        cmd.extend(["--cloud-archive", cloud_archive])

    # Pass build type - "auto" means each package resolves its own type
    if build_type == "auto":
        cmd.extend(["--type", "auto"])
    elif build_type == "snapshot":
        cmd.extend(["--type", "snapshot"])
    elif build_type == "milestone":
        cmd.extend(["--type", "milestone", "--milestone", "b1"])
    elif build_type == "release":
        cmd.extend(["--type", "release"])

    if binary:
        cmd.append("--binary")
    else:
        cmd.append("--no-binary")

    if force:
        cmd.append("--force")

    # Set env to prevent recursive build-deps
    env = os.environ.copy()
    env["PACKASTACK_BUILD_DEPTH"] = "10"  # Prevent auto-build-deps
    env["PACKASTACK_NO_GPG_SIGN"] = "1"  # Don't require GPG signing

    log_dir = run_dir / "logs" / package
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "build.log"

    try:
        with log_file.open("w") as f:
            result = subprocess.run(
                cmd,
                stdout=f,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=3600,  # 1 hour timeout per package
            )

        if result.returncode == 0:
            return True, None, "", str(log_file)

        # Determine failure type from exit code
        failure_type = FailureType.UNKNOWN
        if result.returncode == 3:
            failure_type = FailureType.FETCH_FAILED
        elif result.returncode == 4:
            failure_type = FailureType.PATCH_FAILED
        elif result.returncode == 5:
            failure_type = FailureType.MISSING_DEP
        elif result.returncode == 6:
            failure_type = FailureType.CYCLE
        elif result.returncode == 7:
            failure_type = FailureType.BUILD_FAILED
        elif result.returncode == 8:
            failure_type = FailureType.POLICY_BLOCKED

        return False, failure_type, f"Exit code {result.returncode}", str(log_file)

    except subprocess.TimeoutExpired:
        return False, FailureType.BUILD_FAILED, "Build timed out after 1 hour", str(log_file)
    except Exception as e:
        return False, FailureType.UNKNOWN, str(e), str(log_file)


def _generate_reports(
    state: BuildAllState,
    run_dir: Path,
) -> tuple[Path, Path]:
    """Generate build-all summary reports.

    Args:
        state: Final build state.
        run_dir: Run directory.

    Returns:
        Tuple of (json_report_path, md_report_path).
    """
    from packastack.build.all_reports import generate_build_all_reports
    return generate_build_all_reports(state, run_dir)


def run_build_all(
    target: str,
    ubuntu_series: str,
    cloud_archive: str,
    build_type: str,
    milestone: str,
    binary: bool,
    keep_going: bool,
    max_failures: int,
    resume: bool,
    resume_run_id: str,
    retry_failed: bool,
    skip_failed: bool,
    parallel: int,
    packages_file: str,
    force: bool,
    offline: bool,
    dry_run: bool,
) -> int:
    """Run build-all and return exit code (without sys.exit).

    This function is called by the unified build --all command.

    Returns:
        Exit code.
    """
    with RunContext("build-all") as run:
        exit_code = EXIT_SUCCESS

        try:
            request = BuildAllRequest(
                target=target,
                ubuntu_series=ubuntu_series,
                cloud_archive=cloud_archive,
                build_type=build_type,
                milestone=milestone,
                binary=binary,
                keep_going=keep_going,
                max_failures=max_failures,
                resume=resume,
                resume_run_id=resume_run_id,
                retry_failed=retry_failed,
                skip_failed=skip_failed,
                parallel=parallel,
                packages_file=packages_file,
                force=force,
                offline=offline,
                dry_run=dry_run,
            )
            exit_code = _run_build_all(run=run, request=request)
        except Exception as e:
            import traceback
            activity("error", f"Build-all failed: {e}")
            for line in traceback.format_exc().splitlines():
                activity("error", f"  {line}")
            exit_code = EXIT_CONFIG_ERROR

    return exit_code


def _run_build_all(
    run: RunContext,
    request: BuildAllRequest,
) -> int:
    """Main build-all implementation.

    Args:
        run: RunContext for logging and run directory management.
        request: BuildAllRequest containing CLI inputs before resolution.

    Returns:
        Exit code.
    """
    # Unpack request for local use (will transition to ctx.* access)
    target = request.target
    ubuntu_series = request.ubuntu_series
    cloud_archive = request.cloud_archive
    build_type = request.build_type
    milestone = request.milestone
    binary = request.binary
    keep_going = request.keep_going
    max_failures = request.max_failures
    resume = request.resume
    resume_run_id = request.resume_run_id
    retry_failed = request.retry_failed
    skip_failed = request.skip_failed
    parallel = request.parallel
    packages_file = request.packages_file
    force = request.force
    offline = request.offline
    dry_run = request.dry_run

    cfg = load_config()
    paths = resolve_paths(cfg)
    runs_root = paths.get("runs_root", paths["cache_root"] / "runs")

    # Resolve parallel workers (0 = auto)
    if parallel == 0:
        parallel = get_default_parallel_workers()

    # Handle milestone as override
    if milestone and build_type == "auto":
        build_type = "milestone"

    # Resolve series
    resolved_ubuntu = resolve_series(ubuntu_series)
    releases_repo = paths.get("openstack_releases_repo")
    if target == "devel":
        openstack_target = get_current_development_series(releases_repo) if releases_repo else None
        if not openstack_target:
            openstack_target = target  # Fall back to "devel" if can't resolve
    else:
        openstack_target = target

    activity("all", f"Target: OpenStack {openstack_target} on Ubuntu {resolved_ubuntu}")
    activity("all", f"Build type: {build_type}")

    # Determine state directory
    run_dir = runs_root / run.run_id
    state_dir = run_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    # Check for resume
    state: BuildAllState | None = None
    graph: DependencyGraph | None = None
    cycles: list[list[str]] = []
    
    if resume:
        if resume_run_id:
            resume_state_dir = runs_root / resume_run_id / "state"
        else:
            # Find most recent build-all run
            resume_state_dir = state_dir

        state = load_state(resume_state_dir)
        if state is None:
            activity("all", "No previous run found to resume")
            if resume_run_id:
                return EXIT_RESUME_ERROR
            # Fall through to new run
        else:
            activity("all", f"Resuming run: {state.run_id}")
            activity("all", f"  Previous: {len(state.get_success_packages())} succeeded, {len(state.get_failed_packages())} failed")

            if retry_failed:
                # Reset failed packages to pending
                for pkg_state in state.packages.values():
                    if pkg_state.status == PackageStatus.FAILED:
                        pkg_state.status = PackageStatus.PENDING
                        pkg_state.failure_type = None
                        pkg_state.failure_message = ""
                activity("all", "  Retrying failed packages")
            elif skip_failed:
                activity("all", "  Skipping previously failed packages")

    # Discover packages if not resuming
    if state is None:
        activity("all", "Discovering packages...")

        # Use build_root for cached packaging repos (consistent with build command)
        build_root = paths.get("build_root", Path.home() / ".cache" / "packastack" / "build")

        packages_file_path = Path(packages_file) if packages_file else None
        discovery = discover_packages(
            cache_dir=build_root if offline else None,  # Only use cache in offline mode
            packages_file=packages_file_path,
            offline=offline,
            releases_repo=releases_repo,
        )

        if discovery.errors:
            for err in discovery.errors:
                activity("all", f"Discovery error: {err}")
            if not discovery.packages:
                return EXIT_DISCOVERY_FAILED

        activity("all", f"Discovered {discovery.total_repos} repos")
        activity("all", f"  Filtered out: {len(discovery.filtered_repos)}")
        activity("all", f"  Build targets: {len(discovery.packages)}")
        activity("all", f"  Source: {discovery.source}")

        run.log_event({
            "event": "discovery.complete",
            "total_repos": discovery.total_repos,
            "filtered": len(discovery.filtered_repos),
            "packages": len(discovery.packages),
            "source": discovery.source,
        })

        project_config_path = paths.get("openstack_project_config")
        filtered_packages, retired, possibly_retired = _filter_retired_packages(
            packages=discovery.packages,
            project_config_path=project_config_path,
            releases_repo=releases_repo,
            openstack_target=openstack_target,
            offline=offline,
            run=run,
        )
        if retired or possibly_retired:
            discovery.packages = filtered_packages
            if retired:
                activity("all", f"Excluded retired packages: {len(retired)}")
                run.log_event({
                    "event": "build_all.retired_excluded",
                    "count": len(retired),
                    "packages": retired,
                })
            if possibly_retired:
                activity("all", f"Excluded possibly retired packages: {len(possibly_retired)}")
                run.log_event({
                    "event": "build_all.possibly_retired_excluded",
                    "count": len(possibly_retired),
                    "packages": possibly_retired,
                })
            activity("all", f"  Build targets after retirement filter: {len(discovery.packages)}")

        # Load package indexes for dependency resolution
        activity("all", "Loading package indexes...")

        local_repo = paths.get("local_apt_repo", paths["cache_root"] / "apt-repo")
        ubuntu_cache = paths.get("ubuntu_archive_cache", paths["cache_root"] / "ubuntu-archive")
        host_arch = get_host_arch()
        defaults = cfg.get("defaults", {})
        pockets = defaults.get("ubuntu_pockets", ["release", "updates", "security"])
        components = defaults.get("ubuntu_components", ["main", "universe"])

        indexes: list[PackageIndex] = []

        # Ubuntu archive
        ubuntu_index = load_package_index(
            ubuntu_cache,
            resolved_ubuntu,
            pockets,
            components,
        )
        if ubuntu_index:
            indexes.append(ubuntu_index)

        # Cloud archive if specified
        if cloud_archive:
            ca_index = load_cloud_archive_index(
                ubuntu_cache,
                resolved_ubuntu,
                cloud_archive,
            )
            if ca_index:
                indexes.append(ca_index)

        # Local repo
        local_index = load_local_repo_index(local_repo, host_arch)
        if local_index:
            indexes.append(local_index)

        pkg_index = merge_package_indexes(*indexes) if indexes else PackageIndex()

        activity("all", f"Loaded {len(pkg_index.packages)} packages from indexes")

        # Build dependency graph using the same function as plan --all
        activity("all", "Building dependency graph...")
        
        # Import the plan command's graph builder for consistency
        from packastack.commands.plan import _build_dependency_graph as plan_build_graph
        
        graph, mir_candidates = plan_build_graph(
            targets=discovery.packages,
            local_repo=local_repo,
            local_index=local_index,
            ubuntu_index=pkg_index,
            run=run,
            releases_repo=releases_repo,
            offline=offline,
            ubuntu_series=resolved_ubuntu,
            openstack_series=openstack_target,
        )
        
        run.log_event({
            "event": "build_all.graph_built",
            "nodes": len(graph.nodes),
            "edges": sum(len(e) for e in graph.edges.values()),
        })
        
        activity("all", f"Graph: {len(graph.nodes)} packages, {sum(len(e) for e in graph.edges.values())} dependencies")
        
        # Report MIR candidates if any
        if mir_candidates:
            activity("all", f"MIR candidates: {sum(len(d) for d in mir_candidates.values())} dependencies")
            run.log_event({
                "event": "build_all.mir_candidates",
                "candidates": mir_candidates,
            })

        # Detect cycles
        cycles = graph.detect_cycles()
        if cycles:
            cycle_edges = graph.get_cycle_edges()
            run.log_event({
                "event": "build_all.cycle_edges",
                "edges": cycle_edges,
            })
            activity("all", f"Warning: {len(cycles)} dependency cycles detected")
            for cycle in cycles[:5]:
                activity("all", f"  Cycle: {' -> '.join(cycle)}")
            source_to_project = {}
            if releases_repo and openstack_target:
                source_to_project = load_openstack_packages(releases_repo, openstack_target)
            suggestions = suggest_cycle_edge_exclusions(
                edges=cycle_edges,
                packaging_repos={pkg: build_root / pkg for pkg in discovery.packages},
                upstream_versions=_build_upstream_versions_from_packaging(discovery.packages, build_root),
                source_to_project=source_to_project,
                package_index=pkg_index,
                upstream_cache_base=paths.get("upstream_tarballs"),
            )
            if suggestions:
                run.log_event({
                    "event": "build_all.cycle_exclusion_suggestions",
                    "suggestions": [suggestion.to_dict() for suggestion in suggestions],
                })
                activity(
                    "all",
                    f"Suggested {len(suggestions)} edge exclusion(s) based on upstream requirements",
                )
                for suggestion in suggestions[:5]:
                    activity(
                        "all",
                        f"  Suggest exclude {suggestion.source} -> {suggestion.dependency} ({suggestion.requirements_source})",
                    )
                if len(suggestions) > 5:
                    activity("all", f"  ... and {len(suggestions) - 5} more")

        # Compute topological order
        try:
            build_order = graph.topological_sort()
        except ValueError as e:
            activity("all", f"Cannot compute build order: {e}")
            return EXIT_GRAPH_ERROR

        activity("all", f"Build order computed: {len(build_order)} packages")

        # Create initial state
        state = create_initial_state(
            run_id=run.run_id,
            target=openstack_target,
            ubuntu_series=resolved_ubuntu,
            build_type=build_type,
            packages=discovery.packages,
            build_order=build_order,
            max_failures=max_failures,
            keep_going=keep_going,
            parallel=parallel,
        )
        state.cycles = cycles

        save_state(state, state_dir)

    # Show plan
    pending = state.get_pending_packages()
    activity("all", f"Build plan: {len(pending)} packages pending")
    activity("all", f"  Mode: {'keep-going' if state.keep_going else 'fail-fast'}")
    if state.max_failures > 0:
        activity("all", f"  Max failures: {state.max_failures}")
    if parallel > 1:
        activity("all", f"  Parallel: {parallel} jobs")

    if dry_run:
        # Need graph for dry run - reconstruct if resuming
        if graph is None:
            graph = DependencyGraph()
            for pkg in state.build_order:
                graph.add_node(pkg)
        
        # Use the same PlanGraph and render_waves as the plan command
        plan_graph = PlanGraph.from_dependency_graph(
            dep_graph=graph,
            run_id=run.run_id,
            target=openstack_target,
            ubuntu_series=resolved_ubuntu,
            type_report=None,
            cycles=cycles,
        )
        
        waves_output = render_waves(plan_graph, focus=None)
        print(f"\n{waves_output}", file=sys.__stdout__, flush=True)
        return EXIT_SUCCESS

    # Execute builds - reconstruct graph if needed
    if graph is None:
        graph = DependencyGraph()
        for pkg in state.build_order:
            graph.add_node(pkg)
    # Reconstruct edges from build order (approximation)

    if parallel > 1:
        exit_code = _run_parallel_builds(
            state=state,
            graph=graph,
            run_dir=run_dir,
            state_dir=state_dir,
            target=openstack_target,
            ubuntu_series=resolved_ubuntu,
            cloud_archive=cloud_archive,
            build_type=build_type,
            binary=binary,
            force=force,
            parallel=parallel,
            local_repo=local_repo,
            run=run,
        )
    else:
        exit_code = _run_sequential_builds(
            state=state,
            run_dir=run_dir,
            state_dir=state_dir,
            target=openstack_target,
            ubuntu_series=resolved_ubuntu,
            cloud_archive=cloud_archive,
            build_type=build_type,
            binary=binary,
            force=force,
            local_repo=local_repo,
            run=run,
        )

    # Mark completion
    state.completed_at = datetime.utcnow().isoformat()
    save_state(state, state_dir)

    # Generate reports
    activity("all", "Generating reports...")
    json_report, md_report = _generate_reports(state, run_dir)
    activity("all", f"  JSON: {json_report}")
    activity("all", f"  Markdown: {md_report}")

    # Final summary
    succeeded = len(state.get_success_packages())
    failed = len(state.get_failed_packages())
    blocked = len(state.get_blocked_packages())

    activity("all", "")
    activity("all", "=" * 60)
    activity("all", f"BUILD-ALL COMPLETE: {succeeded} succeeded, {failed} failed, {blocked} blocked")
    activity("all", "=" * 60)

    run.write_summary(
        status="success" if failed == 0 else "partial",
        succeeded=succeeded,
        failed=failed,
        blocked=blocked,
        reports={
            "json": str(json_report),
            "markdown": str(md_report),
        },
    )

    return EXIT_SUCCESS if failed == 0 else EXIT_ALL_BUILD_FAILED


def _run_sequential_builds(
    state: BuildAllState,
    run_dir: Path,
    state_dir: Path,
    target: str,
    ubuntu_series: str,
    cloud_archive: str,
    build_type: str,
    binary: bool,
    force: bool,
    local_repo: Path,
    run: RunContext,
) -> int:
    """Run builds sequentially in topological order."""
    from rich.console import Console
    from rich.progress import Progress, BarColumn, TextColumn, TaskProgressColumn, TimeRemainingColumn

    total = len(state.build_order)
    built = 0
    failed_set: set[str] = set()
    host_arch = get_host_arch()

    progress_context = contextlib.nullcontext()
    if total:
        console = Console(file=sys.__stdout__, force_terminal=True)
        progress_context = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeRemainingColumn(),
            console=console,
            transient=True,
        )

    with progress_context as progress:
        task = None
        if progress:
            task = progress.add_task("Building packages", total=total)

        for i, pkg in enumerate(state.build_order, 1):
            pkg_state = state.packages.get(pkg)
            if pkg_state is None:
                if progress and task is not None:
                    progress.advance(task)
                continue

            if progress and task is not None:
                progress.update(task, description=f"Building {pkg}")

            # Skip non-pending packages
            if pkg_state.status != PackageStatus.PENDING:
                if pkg_state.status == PackageStatus.SUCCESS:
                    built += 1
                if progress and task is not None:
                    progress.advance(task)
                continue

            # Check if blocked by failed dependency
            # (Would need graph edges to check properly - simplified here)

            activity("all", f"[{i}/{total}] Building: {pkg}")

            state.mark_started(pkg)
            save_state(state, state_dir)

            success, failure_type, message, log_path = _run_single_build(
                package=pkg,
                target=target,
                ubuntu_series=ubuntu_series,
                cloud_archive=cloud_archive,
                build_type=build_type,
                binary=binary,
                force=force,
                run_dir=run_dir,
            )

            if success:
                state.mark_success(pkg, log_path)
                built += 1
                activity("all", f"[ok]    {pkg} ({pkg_state.duration_seconds:.0f}s)")
                # Regenerate local repo indexes after each successful build
                # so subsequent packages can find newly built dependencies
                _refresh_local_repo_indexes(local_repo, host_arch, run, phase="all")
            else:
                state.mark_failed(pkg, failure_type or FailureType.UNKNOWN, message, log_path)
                failed_set.add(pkg)
                activity("all", f"[fail]  {pkg}: {message}")
                if log_path:
                    activity("all", f"        Log: {log_path}")

            save_state(state, state_dir)

            if progress and task is not None:
                progress.advance(task)

            # Check failure policy
            if state.should_stop():
                activity("all", f"Stopping: failure limit reached ({len(failed_set)} failures)")
                break

            # Progress update every 10 packages
            if i % 10 == 0:
                activity("all", f"Progress: {built} ok, {len(failed_set)} fail, {total - i} remaining")

    return EXIT_SUCCESS if not failed_set else EXIT_ALL_BUILD_FAILED


def _run_parallel_builds(
    state: BuildAllState,
    graph: DependencyGraph,
    run_dir: Path,
    state_dir: Path,
    target: str,
    ubuntu_series: str,
    cloud_archive: str,
    build_type: str,
    binary: bool,
    force: bool,
    parallel: int,
    local_repo: Path,
    run: RunContext,
) -> int:
    """Run builds in parallel, respecting dependencies."""
    from rich.console import Console
    from rich.progress import Progress, BarColumn, TextColumn, TaskProgressColumn, TimeRemainingColumn

    total = len(state.build_order)
    built = 0
    failed_set: set[str] = set()
    lock = threading.Lock()
    host_arch = get_host_arch()

    def on_complete(pkg: str, success: bool, failure_type: FailureType | None, message: str, log_path: str) -> None:
        nonlocal built
        with lock:
            if success:
                state.mark_success(pkg, log_path)
                built += 1
                activity("all", f"[ok]    {pkg}")
            else:
                state.mark_failed(pkg, failure_type or FailureType.UNKNOWN, message, log_path)
                failed_set.add(pkg)
                activity("all", f"[fail]  {pkg}: {message}")
            save_state(state, state_dir)

    progress_context = contextlib.nullcontext()
    if total:
        console = Console(file=sys.__stdout__, force_terminal=True)
        progress_context = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeRemainingColumn(),
            console=console,
            transient=True,
        )

    with progress_context as progress:
        task = None
        if progress:
            completed = sum(
                1 for pkg in state.build_order
                if (state.packages.get(pkg) and state.packages[pkg].status != PackageStatus.PENDING)
            )
            task = progress.add_task("Building packages", total=total, completed=completed)

        # Get batches for parallel execution
        batches = _get_parallel_batches(graph, state)

        batch_num = 0
        for batch in batches:
            batch_num += 1
            if not batch:
                continue

            activity("all", f"Batch {batch_num}: {len(batch)} packages (parallel={min(parallel, len(batch))})")

            with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as executor:
                futures = {}

                for pkg in batch:
                    pkg_state = state.packages.get(pkg)
                    if pkg_state is None or pkg_state.status != PackageStatus.PENDING:
                        continue

                    if progress and task is not None:
                        progress.update(task, description=f"Building {pkg}")

                    activity("all", f"[start] {pkg}")
                    state.mark_started(pkg)

                    future = executor.submit(
                        _run_single_build,
                        package=pkg,
                        target=target,
                        ubuntu_series=ubuntu_series,
                        cloud_archive=cloud_archive,
                        build_type=build_type,
                        binary=binary,
                        force=force,
                        run_dir=run_dir,
                    )
                    futures[future] = pkg

                # Wait for batch to complete
                for future in concurrent.futures.as_completed(futures):
                    pkg = futures[future]
                    try:
                        success, failure_type, message, log_path = future.result()
                        on_complete(pkg, success, failure_type, message, log_path)
                    except Exception as e:
                        on_complete(pkg, False, FailureType.UNKNOWN, str(e), "")
                    if progress and task is not None:
                        progress.advance(task)

            # Regenerate local repo indexes after each batch completes
            # so packages in the next batch can find newly built dependencies
            _refresh_local_repo_indexes(local_repo, host_arch, run, phase="all")

            # Check failure policy after each batch
            if state.should_stop():
                activity("all", f"Stopping: failure limit reached ({len(failed_set)} failures)")
                break

            activity("all", f"Batch {batch_num} complete: {built} ok, {len(failed_set)} fail total")

    return EXIT_SUCCESS if not failed_set else EXIT_ALL_BUILD_FAILED


# =============================================================================
# End of build-all functions
# =============================================================================


# Build type resolution delegated to packastack.build.type_resolution
_resolve_build_type_from_cli = resolve_build_type_from_cli
_resolve_build_type_auto = resolve_build_type_auto
_build_type_from_string = build_type_from_string



def build(
    package: str = typer.Argument("", help="Package name or OpenStack project to build (omit for --all)"),
    target: str = typer.Option("devel", "-t", "--target", help="OpenStack series target"),
    ubuntu_series: str = typer.Option("devel", "-u", "--ubuntu-series", help="Ubuntu series target"),
    cloud_archive: str = typer.Option("", "-c", "--cloud-archive", help="Cloud archive pocket (e.g., caracal)"),
    build_type: str = typer.Option("auto", "--type", help="Build type: auto, release, snapshot, or milestone"),
    milestone: str = typer.Option("", "-m", "--milestone", help="Milestone version (e.g., b1, rc1) - implies --type milestone"),
    force: bool = typer.Option(False, "-f", "--force", help="Proceed despite warnings"),
    offline: bool = typer.Option(False, "-o", "--offline", help="Run in offline mode"),
    validate_plan_only: bool = typer.Option(False, "-v", "--validate-plan", help="Stop after validated plan"),
    plan_upload: bool = typer.Option(False, "-p", "--plan-upload", help="Show validated plan with upload order"),
    upload: bool = typer.Option(False, "-U", "--upload", help="Print upload commands"),
    binary: bool = typer.Option(True, "-b/-B", "--binary/--no-binary", help="Build binary packages with sbuild (default: on)"),
    builder: str = typer.Option("sbuild", "-x", "--builder", help="Builder for binary packages: sbuild or dpkg"),
    build_deps: bool = typer.Option(True, "-d/-D", "--build-deps/--no-build-deps", help="Auto-build missing dependencies"),
    no_cleanup: bool = typer.Option(False, "-k", "--no-cleanup", help="Don't cleanup workspace on success (keep)"),
    no_spinner: bool = typer.Option(False, "-q", "--no-spinner", help="Disable spinner output (quiet)"),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmations"),
    use_gbp_dch: bool = typer.Option(True, "--use-gbp-dch/--no-gbp-dch", help="Use gbp dch for changelog updates (default on)"),
    include_retired: bool = typer.Option(False, "--include-retired", help="Build retired upstream projects (default: refuse)"),
    skip_repo_regen: bool = typer.Option(False, "--skip-repo-regen", hidden=True, help="Skip local repo regeneration (internal use)"),
    # --all mode options
    all_packages: bool = typer.Option(False, "-a", "--all", help="Build all discovered packages in dependency order"),
    keep_going: bool = typer.Option(True, "--keep-going/--fail-fast", help="Continue on failure (default: keep-going) [--all only]"),
    max_failures: int = typer.Option(0, "--max-failures", help="Stop after N failures (0=unlimited) [--all only]"),
    resume: bool = typer.Option(False, "--resume", help="Resume a previous run [--all only]"),
    resume_run_id: str = typer.Option("", "--resume-run-id", help="Specific run ID to resume [--all only]"),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Retry failed packages on resume [--all only]"),
    skip_failed: bool = typer.Option(True, "--skip-failed/--no-skip-failed", help="Skip previously failed on resume [--all only]"),
    parallel: int = typer.Option(0, "-j", "--parallel", help="Parallel workers (0=auto) [--all only]"),
    packages_file: str = typer.Option("", "--packages-file", help="File with package names (one per line) [--all only]"),
    dry_run: bool = typer.Option(False, "-n", "--dry-run", help="Show plan without building [--all only]"),
) -> None:
    """Build OpenStack packages for Ubuntu.

    Clones packaging repositories, validates the plan against upstream,
    applies patches with gbp patch-queue, builds source packages, and
    optionally builds binary packages with sbuild.

    When --all is specified, discovers all ubuntu-openstack-dev packages
    and builds them in topological (dependency) order. Supports parallel
    execution and resuming interrupted runs.

    Exit codes:
      0 - Success
      1 - Configuration error
      2 - Required tools missing / Discovery failed
      3 - Fetch failed / Graph error (cycles)
      4 - Patch application failed / Some builds failed
      5 - Missing packages detected / Resume error
      6 - Dependency cycle detected
      7 - Build failed
      8 - Policy blocked
      9 - Registry error
      10 - Retired project (skipped)
    """
    # Validate inputs
    if not package and not all_packages:
        activity("error", "Either specify a package or use --all")
        sys.exit(EXIT_CONFIG_ERROR)

    if package and all_packages:
        activity("error", "Cannot specify both a package and --all")
        sys.exit(EXIT_CONFIG_ERROR)

    # Route to appropriate implementation
    if all_packages:
        _build_all_mode(
            target=target,
            ubuntu_series=ubuntu_series,
            cloud_archive=cloud_archive,
            build_type=build_type,
            milestone=milestone,
            force=force,
            offline=offline,
            binary=binary,
            keep_going=keep_going,
            max_failures=max_failures,
            resume=resume,
            resume_run_id=resume_run_id,
            retry_failed=retry_failed,
            skip_failed=skip_failed,
            parallel=parallel,
            packages_file=packages_file,
            dry_run=dry_run,
        )
    else:
        _build_single_mode(
            package=package,
            target=target,
            ubuntu_series=ubuntu_series,
            cloud_archive=cloud_archive,
            build_type=build_type,
            milestone=milestone,
            force=force,
            offline=offline,
            validate_plan_only=validate_plan_only,
            plan_upload=plan_upload,
            upload=upload,
            binary=binary,
            builder=builder,
            build_deps=build_deps,
            no_cleanup=no_cleanup,
            no_spinner=no_spinner,
            yes=yes,
            use_gbp_dch=use_gbp_dch,
            include_retired=include_retired,
            skip_repo_regen=skip_repo_regen,
        )


def _build_single_mode(
    package: str,
    target: str,
    ubuntu_series: str,
    cloud_archive: str,
    build_type: str,
    milestone: str,
    force: bool,
    offline: bool,
    validate_plan_only: bool,
    plan_upload: bool,
    upload: bool,
    binary: bool,
    builder: str,
    build_deps: bool,
    no_cleanup: bool,
    no_spinner: bool,
    yes: bool,
    use_gbp_dch: bool,
    include_retired: bool,
    skip_repo_regen: bool = False,
) -> None:
    """Build a single package."""
    with RunContext("build") as run:
        exit_code = EXIT_SUCCESS
        workspace: Path | None = None
        cleanup_on_exit = not no_cleanup

        try:
            request = BuildRequest(
                package=package,
                target=target,
                ubuntu_series=ubuntu_series,
                cloud_archive=cloud_archive,
                build_type_str=build_type,
                milestone=milestone,
                force=force,
                offline=offline,
                include_retired=include_retired,
                yes=yes,
                binary=binary,
                builder=builder,
                build_deps=build_deps,
                use_gbp_dch=use_gbp_dch,
                no_cleanup=no_cleanup,
                no_spinner=no_spinner,
                validate_plan_only=validate_plan_only,
                plan_upload=plan_upload,
                upload=upload,
                skip_repo_regen=skip_repo_regen,
                workspace_ref=lambda w: _set_workspace(w, locals()),
            )
            exit_code = _run_build(run=run, request=request)
        except Exception as e:
            import traceback
            activity("report", f"Build failed: {e}")
            activity("report", "Traceback:")
            for line in traceback.format_exc().splitlines():
                activity("report", f"  {line}")
            run.log_event({"event": "build.exception", "error": str(e), "traceback": traceback.format_exc()})
            exit_code = EXIT_BUILD_FAILED
            cleanup_on_exit = False
        finally:
            # Cleanup on success only
            if cleanup_on_exit and exit_code == EXIT_SUCCESS and workspace and workspace.exists():
                activity("report", f"Cleaning up workspace: {workspace}")
                try:
                    shutil.rmtree(workspace)
                except Exception:
                    pass
            elif workspace and workspace.exists():
                activity("report", f"Workspace preserved: {workspace}")

    sys.exit(exit_code)


def _build_all_mode(
    target: str,
    ubuntu_series: str,
    cloud_archive: str,
    build_type: str,
    milestone: str,
    force: bool,
    offline: bool,
    binary: bool,
    keep_going: bool,
    max_failures: int,
    resume: bool,
    resume_run_id: str,
    retry_failed: bool,
    skip_failed: bool,
    parallel: int,
    packages_file: str,
    dry_run: bool,
) -> None:
    """Build all packages in dependency order."""
    exit_code = run_build_all(
        target=target,
        ubuntu_series=ubuntu_series,
        cloud_archive=cloud_archive,
        build_type=build_type,
        milestone=milestone,
        binary=binary,
        keep_going=keep_going,
        max_failures=max_failures,
        resume=resume,
        resume_run_id=resume_run_id,
        retry_failed=retry_failed,
        skip_failed=skip_failed,
        parallel=parallel,
        packages_file=packages_file,
        force=force,
        offline=offline,
        dry_run=dry_run,
    )
    sys.exit(exit_code)


def _set_workspace(w: Path, local_vars: dict) -> None:
    """Helper to set workspace in outer scope."""
    local_vars["workspace"] = w


def _refresh_local_repo_indexes(
    local_repo: Path,
    arch: str,
    run: RunContextType,
    phase: str = "verify",
) -> tuple[localrepo.IndexResult, localrepo.SourceIndexResult]:
    """Regenerate binary and source indexes for the local APT repository.

    Ensures `Packages`/`Packages.gz` and `Sources`/`Sources.gz` exist even when
    no artifacts were published, avoiding confusing missing-metadata errors.
    """
    index_result = localrepo.regenerate_indexes(local_repo, arch=arch)
    if index_result.success:
        activity(phase, f"Regenerated Packages index ({index_result.package_count} packages)")
        run.log_event(
            {
                "event": f"{phase}.index",
                "package_count": index_result.package_count,
                "packages_file": str(index_result.packages_file) if index_result.packages_file else None,
            }
        )
    else:
        activity(phase, f"Warning: Failed to regenerate binary indexes: {index_result.error}")
        run.log_event({"event": f"{phase}.index_failed", "error": index_result.error})

    source_index_result = localrepo.regenerate_source_indexes(local_repo)
    if source_index_result.success:
        activity(phase, f"Regenerated Sources index ({source_index_result.source_count} sources)")
        run.log_event(
            {
                "event": f"{phase}.source_index",
                "source_count": source_index_result.source_count,
                "sources_file": str(source_index_result.sources_file)
                if source_index_result.sources_file
                else None,
            }
        )
    else:
        activity(phase, f"Warning: Failed to regenerate source indexes: {source_index_result.error}")
        run.log_event({"event": f"{phase}.source_index_failed", "error": source_index_result.error})

    return index_result, source_index_result


def _run_build(
    run: RunContextType,
    request: BuildRequest,
) -> int:
    """Main build logic.

    Args:
        run: RunContext for logging and run directory management.
        request: BuildRequest containing CLI inputs before resolution.

    Returns:
        Exit code.
    """
    cfg = load_config()
    paths = resolve_paths(cfg)
    tarball_cache_base = paths.get("upstream_tarballs")
    if tarball_cache_base is None:
        tarball_cache_base = paths["cache_root"] / "upstream-tarballs"

    # =========================================================================
    # PHASE: Resolve build type (before planning to avoid policy blocks)
    # =========================================================================
    from packastack.planning.type_selection import BuildType
    from packastack.upstream.releases import get_current_development_series
    
    # Parse CLI build type options
    parsed_type_str, milestone_from_cli = _resolve_build_type_from_cli(
        request.build_type_str, request.milestone
    )
    
    # Resolve build type early (especially for auto)
    resolved_build_type_str: str | None = None
    if parsed_type_str == "auto":
        # Need to resolve auto before planning
        releases_repo = paths["openstack_releases_repo"]
        resolved_ubuntu = resolve_series(request.ubuntu_series)
        
        # Resolve OpenStack target
        if request.target == "devel":
            openstack_target = get_current_development_series(releases_repo) or request.target
        else:
            openstack_target = request.target
        
        # Infer deliverable name from package
        deliverable = request.package
        if request.package.startswith("python-"):
            deliverable = request.package[7:]
        
        build_type_resolved, milestone_resolved, _reason = _resolve_build_type_auto(
            releases_repo=releases_repo,
            series=openstack_target,
            source_package=request.package,
            deliverable=deliverable,
            offline=request.offline,
            run=run,
        )
        resolved_build_type_str = build_type_resolved.value
        activity("resolve", f"Build type (auto): {resolved_build_type_str}")
    else:
        resolved_build_type_str = parsed_type_str
        activity("resolve", f"Build type: {resolved_build_type_str}")
    
    # =========================================================================
    # PHASE: Planning (reuse plan command logic)
    # =========================================================================
    from packastack.commands.plan import run_plan_for_package
    
    plan_request = request.to_plan_request()
    # Pass resolved build type to planning to skip snapshot checks for release/milestone
    # Also skip local repo since packages haven't been cloned yet
    from dataclasses import replace
    plan_request = replace(plan_request, build_type=resolved_build_type_str, skip_local=True)
    
    plan_result, plan_exit_code = run_plan_for_package(
        request=plan_request,
        run=run,
        cfg=cfg,
        paths=paths,
        verbose_output=False,  # Only show warnings/errors during build planning
    )
    
    # Handle plan-only modes
    if request.validate_plan_only or request.plan_upload:
        activity("report", f"Build order: {len(plan_result.build_order)} packages")
        for i, pkg in enumerate(plan_result.build_order, 1):
            activity("report", f"  {i}. {pkg}")
        
        if request.plan_upload:
            activity("report", f"Upload order: {len(plan_result.upload_order)} packages")
            for i, pkg in enumerate(plan_result.upload_order, 1):
                activity("report", f"  {i}. {pkg}")
        
        return plan_exit_code
    
    # Honor plan exit codes unless --force
    if plan_exit_code != EXIT_SUCCESS:
        if request.force:
            activity("warn", "Planning detected issues but continuing due to --force")
        else:
            run.write_summary(
                status="failed",
                error="Planning failed",
                exit_code=plan_exit_code,
            )
            return plan_exit_code
    
    # Get the resolved package names from plan result
    if not plan_result.build_order:
        activity("error", "No packages to build")
        run.write_summary(status="failed", error="No packages in build order", exit_code=EXIT_CONFIG_ERROR)
        return EXIT_CONFIG_ERROR
    
    # Build all packages in dependency order (respecting waves for parallelism)
    # For now, we'll iterate sequentially through the build order
    # TODO: Extract and reuse the parallel batch logic from build-all
    activity("build", f"Building {len(plan_result.build_order)} package(s) in dependency order")
    
    for pkg_idx, pkg_name in enumerate(plan_result.build_order, 1):
        activity("build", f"[{pkg_idx}/{len(plan_result.build_order)}] Building: {pkg_name}")
        
        # Derive project name from package name
        # For python-* packages, strip the prefix to get project name
        if pkg_name.startswith("python-"):
            package = pkg_name[7:]  # Remove "python-" prefix
        else:
            package = pkg_name
        
        target = request.target
        ubuntu_series = request.ubuntu_series
        cloud_archive = request.cloud_archive
        build_type_str = request.build_type_str
        milestone = request.milestone
        force = request.force
        offline = request.offline
        upload = request.upload
        binary = request.binary
        builder = request.builder
        build_deps = request.build_deps
        no_spinner = request.no_spinner
        yes = request.yes
        use_gbp_dch = request.use_gbp_dch
        include_retired = request.include_retired
        skip_repo_regen = request.skip_repo_regen
        workspace_ref = request.workspace_ref
    
        # Resolve series (already done in planning, but needed for later phases)
        resolved_ubuntu = resolve_series(ubuntu_series)
        releases_repo = paths["openstack_releases_repo"]
        if target == "devel":
            openstack_target = get_current_development_series(releases_repo) or target
        else:
            openstack_target = target
        local_repo = paths["local_apt_repo"]
    
        activity("resolve", f"Package: {pkg_name}")
        run.log_event({"event": "resolve.package", "name": pkg_name})

        # =========================================================================
        # PHASE: retirement check
        # =========================================================================
        project_config_path = paths.get("openstack_project_config")
        retirement_result, retirement_info = check_retirement_status(
            pkg_name=pkg_name,
            package=package,
            project_config_path=project_config_path,
            releases_repo=releases_repo,
            openstack_target=openstack_target,
            include_retired=include_retired,
            offline=offline,
            run=run,
        )
        if not retirement_result.success:
            return retirement_result.exit_code

        # Build type already resolved before planning (see earlier in function)
        # Just convert string back to enum and handle milestone
        build_type = _build_type_from_string(resolved_build_type_str)
        milestone_str = milestone_from_cli
    
        # Log what we're using (already shown during early resolution)
        run.log_event({"event": "resolve.build_type", "type": build_type.value, "milestone": milestone_str})

        # Get previous series for launchpad.yaml update
        prev_series = get_previous_series(releases_repo, openstack_target)
        if prev_series:
            activity("resolve", f"Previous series: {prev_series}")
        run.log_event({"event": "resolve.prev_series", "prev": prev_series, "target": openstack_target})

        # =========================================================================
        # PHASE: registry
        # =========================================================================
        registry_result, registry_info = resolve_upstream_registry(
            package=package,
            pkg_name=pkg_name,
            releases_repo=releases_repo,
            openstack_target=openstack_target,
            run=run,
        )
        if not registry_result.success:
            return registry_result.exit_code

        # Extract values from registry resolution result
        registry = registry_info.registry
        resolved_upstream = registry_info.resolved
        upstream_config = resolved_upstream.config
        resolution_source = resolved_upstream.resolution_source

        # Initialize provenance record
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

        # =========================================================================
        # PHASE: policy
        # =========================================================================
        activity("policy", "Checking snapshot eligibility")

        if build_type == BuildType.SNAPSHOT:
            eligible, reason, preferred = is_snapshot_eligible(releases_repo, openstack_target, package)
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
                    return EXIT_POLICY_BLOCKED
                activity("policy", "Continuing with --force")
            elif "Warning" in reason:
                activity("policy", f"Warning: {reason}")
            run.log_event({"event": "policy.snapshot", "eligible": eligible, "reason": reason})

        activity("policy", "Policy check: OK")

        # =========================================================================
        # PHASE: plan
        # =========================================================================

        # Load package indexes (Ubuntu, Cloud Archive, local repo)
        pockets = cfg.get("defaults", {}).get("ubuntu_pockets", ["release", "updates", "security"])
        components = cfg.get("defaults", {}).get("ubuntu_components", ["main", "universe"])
        result, indexes = load_package_indexes(
            ubuntu_cache=paths["ubuntu_archive_cache"],
            resolved_ubuntu=resolved_ubuntu,
            ubuntu_pockets=pockets,
            ubuntu_components=components,
            cloud_archive=cloud_archive,
            cache_root=paths["cache_root"],
            local_repo_root=paths.get("local_apt_repo"),
            arch=get_host_arch(),
            run=run,
        )
        if not result.success:
            return result.exit_code
        
        ubuntu_index = indexes.ubuntu
        ca_index = indexes.cloud_archive
        local_index = indexes.local_repo

        # Build preliminary graph
        openstack_pkgs = load_openstack_packages(releases_repo, openstack_target)
        activity("plan", f"OpenStack packages: {len(openstack_pkgs)} in {openstack_target}")

        # For build command, we focus on single package
        build_order = [pkg_name]
        upload_order = [pkg_name]

        activity("plan", f"Build order: {', '.join(build_order)}")
        run.log_event({"event": "plan.build_order", "order": build_order})

        # Validate tools before proceeding
        result, _ = check_tools(need_sbuild=binary, run=run)
        if not result.success:
            return result.exit_code

        # Ensure schroot exists for sbuild-based binary builds
        mirror = cfg.get("mirrors", {}).get("ubuntu_archive", "http://archive.ubuntu.com/ubuntu")
        components = cfg.get("defaults", {}).get("ubuntu_components", ["main", "universe"])
        result, schroot_info = ensure_schroot_ready(
            binary=binary,
            builder=builder,
            resolved_ubuntu=resolved_ubuntu,
            mirror=mirror,
            components=components,
            offline=offline,
            run=run,
        )
        if not result.success:
            return result.exit_code
        schroot_name = schroot_info.schroot_name

        # =========================================================================
        # PHASE: fetch
        # =========================================================================

        # Create workspace
        build_root = paths.get("build_root", paths["cache_root"] / "build")
        workspace = build_root / run.run_id / pkg_name
        workspace.mkdir(parents=True, exist_ok=True)
        if workspace_ref:
            workspace_ref(workspace)

        # Mirror RunContext logs into the build workspace
        try:
            run.add_log_mirror(workspace / "logs")
        except Exception:
            pass

        # Clone packaging repo
        fetcher = GitFetcher()
        pkg_workspace = workspace / "packaging"
        with activity_spinner("fetch", f"Cloning packaging repository: {pkg_name}"):
            result = fetcher.fetch_and_checkout(
                pkg_name,
                workspace,
                resolved_ubuntu,
                openstack_target,
                offline=offline,
            )

        if result.error:
            activity("fetch", f"Clone failed: {result.error}")
            run.write_summary(status="failed", error=result.error, exit_code=EXIT_FETCH_FAILED)
            return EXIT_FETCH_FAILED

        pkg_repo = result.path
        # Protect packaging-only files from being removed during upstream merges
        _ensure_no_merge_paths(pkg_repo, ["launchpad.yaml"])
    
        # Commit .gitattributes so it's active during import-orig merge
        gitattributes = pkg_repo / ".gitattributes"
        if gitattributes.exists():
            try:
                activity("fetch", "Committing .gitattributes for merge protection")
                run_command(["git", "add", ".gitattributes"], cwd=pkg_repo)
                commit_cmd = _maybe_disable_gpg_sign(["git", "commit", "-m", "Protect packaging files during merge"])
                returncode, stdout, stderr = run_command(commit_cmd, cwd=pkg_repo, env=_get_git_author_env())
                if returncode == 0:
                    activity("fetch", ".gitattributes committed successfully")
                else:
                    activity("fetch", f".gitattributes commit result: {returncode}")
            except Exception as e:
                activity("fetch", f".gitattributes commit failed: {e}")

        activity("fetch", f"Cloned to: {pkg_repo}")
        activity("fetch", f"Branches: {', '.join(result.branches[:5])}...")
        run.log_event({
            "event": "fetch.complete",
            "path": str(pkg_repo),
            "branches": result.branches,
            "cloned": result.cloned,
            "updated": result.updated,
        })

        # Check debian/watch for mismatch with registry (advisory only)
        watch_path = pkg_repo / "debian" / "watch"
        watch_result = parse_watch_file(watch_path)
        if watch_result.mode.value != "unknown":
            mismatch = check_watch_mismatch(
                pkg_name,
                watch_result,
                upstream_config.upstream.host,
                upstream_config.upstream.url,
            )
            if mismatch:
                activity("policy", f"debian/watch mismatch (warn): registry={upstream_config.upstream.host} watch={mismatch.watch_mode.value}")
                run.log_event({
                    "event": "policy.watch_mismatch",
                    "package": pkg_name,
                    "registry_host": upstream_config.upstream.host,
                    "watch_mode": mismatch.watch_mode.value,
                    "watch_url": mismatch.watch_url,
                })
                # Record in provenance
                provenance.watch_mismatch.detected = True
                provenance.watch_mismatch.watch_mode = mismatch.watch_mode.value
                provenance.watch_mismatch.watch_url = mismatch.watch_url
                provenance.watch_mismatch.registry_mode = upstream_config.upstream.host
                provenance.watch_mismatch.message = mismatch.message

        watch_updated = False
        signing_key_updated = False
        files_to_commit = []
        
        if upgrade_watch_version(watch_path):
            activity("prepare", "Updated debian/watch to version=4")
            watch_updated = True

        # Fix oslo.* watch patterns to accept both oslo.* and oslo_* naming
        if fix_oslo_watch_pattern(watch_path, package):
            activity("prepare", f"Updated debian/watch to accept {package} or {package.replace('.', '_')} naming")
            watch_updated = True

        # Update or remove signing key based on build type
        is_snapshot = build_type == BuildType.SNAPSHOT
        if update_signing_key(pkg_repo, releases_repo, openstack_target, is_snapshot):
            signing_key_updated = True
            if is_snapshot:
                activity("prepare", "Removed debian/upstream/signing-key.asc for snapshot build")
            else:
                activity("prepare", f"Updated debian/upstream/signing-key.asc for {openstack_target}")
        
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
                    commit_parts.append(f"update signing key for {openstack_target}")
            
            commit_msg = " and ".join(commit_parts)
            commit_cmd = _maybe_disable_gpg_sign([
                "git", "commit", "-m", commit_msg
            ] + files_to_commit)
            
            exit_code, stdout, stderr = run_command(commit_cmd, cwd=pkg_repo, env=_get_git_author_env())
            if exit_code == 0:
                activity("prepare", "Committed watch and signing key updates")
            else:
                activity("warn", f"Failed to commit updates: {stderr}")

        # Ensure sphinxdoc addon is enabled before patch application/commits
        _maybe_enable_sphinxdoc(pkg_repo)

        # =========================================================================
        # PHASE: prepare
        # =========================================================================
        activity("prepare", "Preparing packaging repository")

        # Update launchpad.yaml if previous series exists
        if prev_series:
            success, updated_fields, error = update_launchpad_yaml_series(
                pkg_repo, prev_series, openstack_target
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
        activity("prepare", f"Looking for upstream {build_type.value} tarball for {package}")
        upstream = select_upstream_source(
            releases_repo,
            openstack_target,
            package,  # Use original project name
            build_type,
            milestone_str,
        )

        if upstream is None and build_type != BuildType.SNAPSHOT:
            error_msg = f"No {build_type.value} tarball found for {package} in OpenStack {openstack_target}"
            activity("prepare", error_msg)
            run.write_summary(status="failed", error=error_msg, exit_code=EXIT_CONFIG_ERROR)
            return EXIT_CONFIG_ERROR

        # Apply signature policy (remove signing keys for snapshots)
        debian_dir = pkg_repo / "debian"
        removed_keys = apply_signature_policy(debian_dir, build_type)
        if removed_keys:
            activity("prepare", f"Removed signing keys: {len(removed_keys)} files")
            run.log_event({"event": "prepare.signing_keys_removed", "files": [str(f) for f in removed_keys]})

        # Get/fetch upstream source
        upstream_tarball: Path | None = None
        signature_verified = False
        signature_warning = ""
        git_sha = ""
        git_date = ""

        if build_type == BuildType.SNAPSHOT:
            if offline:
                cached_path, cached_meta = find_cached_tarball(
                    project=package,
                    build_type=build_type.value,
                    cache_base=tarball_cache_base,
                    allow_latest=True,
                )
                if not cached_path or not cached_meta:
                    error_msg = f"Offline snapshot build requires a cached tarball for {package}"
                    activity("prepare", error_msg)
                    run.write_summary(status="failed", error=error_msg, exit_code=EXIT_FETCH_FAILED)
                    return EXIT_FETCH_FAILED

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
                    project=cached_meta.project or package,
                    git_ref=cached_meta.git_ref or "cached",
                    cloned=False,
                )

                activity("prepare", f"Snapshot: cached tarball {cached_path.name}")
                run.log_event({
                    "event": "prepare.snapshot.cached",
                    "tarball": str(cached_path),
                    "git_sha": git_sha,
                    "git_date": git_date,
                    "upstream_version": cached_meta.version,
                })

                provenance.upstream.ref = cached_meta.git_ref or "cached"
                provenance.upstream.sha = git_sha
                provenance.tarball.method = "cache"
                provenance.tarball.path = str(cached_path)
                provenance.verification.mode = "none"
                provenance.verification.result = "not_applicable"
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
                # Development series use master/main, released series use stable/series
                upstream_branch = None
                if openstack_target:
                    series_info = load_series_info(releases_repo)
                    is_development = (
                        openstack_target in series_info
                        and series_info[openstack_target].status == "development"
                    )
                    # Development series don't have stable/ branches yet
                    upstream_branch = None if is_development else f"stable/{openstack_target}"

                # Clone upstream and generate snapshot tarball
                upstream_work_dir = workspace / "upstream"
                snapshot_request = SnapshotRequest(
                    project=package,  # Use original project name (not pkg_name which has python- prefix)
                    base_version=base_version,
                    branch=upstream_branch,
                    git_ref="HEAD",
                    package_name=pkg_name,
                )
                snapshot_result = acquire_upstream_snapshot(
                    request=snapshot_request,
                    work_dir=upstream_work_dir,
                    output_dir=workspace,
                )

                if not snapshot_result.success:
                    activity("prepare", f"Snapshot acquisition failed: {snapshot_result.error}")
                    if not force:
                        run.write_summary(
                            status="failed",
                            error=f"Snapshot acquisition failed: {snapshot_result.error}",
                            exit_code=EXIT_FETCH_FAILED,
                        )
                        return EXIT_FETCH_FAILED
                    # Continue with placeholder values if forced
                    git_sha = "HEAD"
                    git_date = "00000000"
                else:
                    git_sha = snapshot_result.git_sha
                    git_date = snapshot_result.git_date
                    upstream_tarball = snapshot_result.tarball_result.path if snapshot_result.tarball_result else None
                    activity("prepare", f"Snapshot: git {snapshot_result.git_sha_short} from {snapshot_result.git_date}")
                    if snapshot_result.cloned:
                        activity("prepare", "Cloned upstream from OpenDev")
                    run.log_event({
                        "event": "prepare.snapshot",
                        "git_sha": snapshot_result.git_sha,
                        "git_sha_short": snapshot_result.git_sha_short,
                        "git_date": snapshot_result.git_date,
                        "upstream_version": snapshot_result.upstream_version,
                        "cloned": snapshot_result.cloned,
                    })
                    # Update provenance with snapshot details
                    provenance.upstream.ref = upstream_branch or "HEAD"
                    provenance.upstream.sha = snapshot_result.git_sha
                    provenance.tarball.method = "git_archive"
                    if upstream_tarball:
                        provenance.tarball.path = str(upstream_tarball)
                    provenance.verification.mode = "none"
                    provenance.verification.result = "not_applicable"
                    if upstream_tarball and snapshot_result.upstream_version:
                        cache_tarball(
                            tarball_path=upstream_tarball,
                            entry=TarballCacheEntry(
                                project=package,
                                package_name=pkg_name,
                                version=snapshot_result.upstream_version,
                                build_type=build_type.value,
                                source_method="git_archive",
                                git_sha=snapshot_result.git_sha,
                                git_date=snapshot_result.git_date,
                                git_ref=upstream_branch or "HEAD",
                            ),
                            cache_base=tarball_cache_base,
                        )

                signature_warning = "Snapshot build - no signature verification"
        else:
            # Release/milestone: uscan first, then official, then fallbacks
            upstream_tarball, signature_verified, signature_warning = _fetch_release_tarball(
                upstream=upstream,
                upstream_config=upstream_config,
                pkg_repo=pkg_repo,
                workspace=workspace,
                provenance=provenance,
                offline=offline,
                project_key=package,
                package_name=pkg_name,
                build_type=build_type,
                cache_base=tarball_cache_base,
                force=force,
                run=run,
            )

            if upstream_tarball is None:
                if not force:
                    run.write_summary(
                        status="failed",
                        error=signature_warning or "Failed to fetch upstream tarball",
                        exit_code=EXIT_FETCH_FAILED,
                    )
                    return EXIT_FETCH_FAILED
                activity("prepare", "Proceeding without upstream tarball due to --force")

        # =========================================================================
        # PHASE: validate-deps
        # =========================================================================
        activity("validate-deps", "Validating upstream dependencies")

        # Extract dependencies from upstream repo (if available)
        upstream_repo_path = None
        if build_type == BuildType.SNAPSHOT and snapshot_result and snapshot_result.repo_path:
            upstream_repo_path = snapshot_result.repo_path
        elif build_type == BuildType.RELEASE and upstream_tarball:
            # For release builds, extract the tarball to cache for dependency analysis
            from packastack.upstream.tarball_cache import extract_tarball

            activity("validate-deps", f"Extracting tarball for dependency analysis: {upstream_tarball.name}")
            tarball_version = upstream.version if upstream else pkg_name

            extraction_result = extract_tarball(
                tarball_path=upstream_tarball,
                project=pkg_name,
                version=tarball_version,
                cache_base=tarball_cache_base,
            )

            if extraction_result.success and extraction_result.extraction_path:
                upstream_repo_path = extraction_result.extraction_path
                if extraction_result.from_cache:
                    activity("validate-deps", "Using cached tarball extraction")
                else:
                    activity("validate-deps", f"Extracted to: {extraction_result.extraction_path}")
            else:
                activity("validate-deps", f"Could not extract tarball: {extraction_result.error}")

        upstream_deps = None
        missing_deps_list: list[str] = []
        new_deps_to_build: list[str] = []

        if upstream_repo_path and upstream_repo_path.exists():
            upstream_deps = extract_upstream_deps(upstream_repo_path)
            activity("validate-deps", f"Found {len(upstream_deps.runtime)} runtime dependencies")
            run.log_event({
                "event": "validate-deps.extracted",
                "runtime_count": len(upstream_deps.runtime),
                "test_count": len(upstream_deps.test),
                "build_count": len(upstream_deps.build),
            })

            # Validate each dependency
            from packastack.planning.validated_plan import (
                map_python_to_debian,
                resolve_dependency_with_spec,
            )

            resolved_count = 0
            for python_dep, version_spec in upstream_deps.runtime:
                debian_name, uncertain = map_python_to_debian(python_dep)
                if not debian_name:
                    activity("validate-deps", f"  {python_dep} -> (unmapped)")
                    continue

                # Try to resolve the dependency with version checking
                version, source, satisfied = resolve_dependency_with_spec(
                    debian_name, version_spec, local_index, ca_index, ubuntu_index
                )

                spec_display = f" (req: {version_spec})" if version_spec else ""
                if version:
                    resolved_count += 1
                    status = "✓ SATISFIED" if satisfied else "✗ OUTDATED"
                    activity("validate-deps", f"  {python_dep}{spec_display} -> {debian_name} = {version} ({source}) [{status}]")
                    run.log_event({
                        "event": "validate-deps.resolved",
                        "python_dep": python_dep,
                        "version_spec": version_spec,
                        "debian_name": debian_name,
                        "version": version,
                        "source": source,
                        "satisfied": satisfied,
                    })
                else:
                    missing_deps_list.append(debian_name)
                    activity("validate-deps", f"  {python_dep}{spec_display} -> {debian_name} [✗ MISSING]")

            activity("validate-deps", f"Resolved {resolved_count}/{len(upstream_deps.runtime)} dependencies")

            if missing_deps_list:
                activity("validate-deps", f"Warning: {len(missing_deps_list)} dependencies not resolved")

                run.log_event({
                    "event": "validate-deps.missing",
                    "count": len(missing_deps_list),
                    "deps": missing_deps_list,
                })

                # Check which missing deps are OpenStack packages we could build
                from packastack.planning.validated_plan import project_to_source_package
                # openstack_pkgs may be a dict mapping source package -> project name (runtime)
                # or a simple iterable of project names (older tests). Handle both.
                if isinstance(openstack_pkgs, dict):
                    openstack_projects = set(openstack_pkgs.values())
                else:
                    openstack_projects = set(openstack_pkgs)
                buildable_deps: list[str] = []
            
                for dep in missing_deps_list:
                    # Infer project name from debian package name
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
                        type_hint = f" --type {build_type.value}" if build_type != BuildType.RELEASE else ""
                        activity("validate-deps", f"  packastack build {dep}{type_hint}")
                    if len(buildable_deps) > 10:
                        activity("validate-deps", f"  ... and {len(buildable_deps) - 10} more")
                
                    run.log_event({
                        "event": "validate-deps.buildable",
                        "packages": buildable_deps,
                    })
                
                    # Store for auto-build phase
                    new_deps_to_build.extend(buildable_deps)
            else:
                activity("validate-deps", "All dependencies resolved")
        else:
            activity("validate-deps", "Skipping - no upstream repo available")

        # =========================================================================
        # PHASE: auto-build (if enabled and missing deps detected)
        # =========================================================================
        if build_deps and new_deps_to_build:
            activity("auto-build", f"Auto-building {len(new_deps_to_build)} missing dependencies")
        
            # Get the current build depth from environment or default to 0
            import os
            current_depth = int(os.environ.get("PACKASTACK_BUILD_DEPTH", "0"))
            max_depth = 10
        
            if current_depth >= max_depth:
                activity("auto-build", f"Maximum build depth ({max_depth}) reached, aborting")
                run.log_event({
                    "event": "auto-build.max_depth",
                    "current_depth": current_depth,
                    "max_depth": max_depth,
                })
                run.write_summary(
                    status="failed",
                    error=f"Maximum dependency build depth ({max_depth}) exceeded",
                    exit_code=EXIT_MISSING_PACKAGES,
                )
                return EXIT_MISSING_PACKAGES
        
            # Build dependencies in order (they should already be topologically sorted)
            for i, dep_pkg in enumerate(new_deps_to_build, 1):
                activity("auto-build", f"[{i}/{len(new_deps_to_build)}] Building dependency: {dep_pkg}")
                run.log_event({
                    "event": "auto-build.start",
                    "package": dep_pkg,
                    "index": i,
                    "total": len(new_deps_to_build),
                    "depth": current_depth + 1,
                })
            
                # Set depth environment variable for child builds
                child_env = os.environ.copy()
                child_env["PACKASTACK_BUILD_DEPTH"] = str(current_depth + 1)
            
                # Build the dependency using subprocess
                import subprocess
                cmd = [
                    "packastack", "build", dep_pkg,
                    "--target", target,
                    "--ubuntu-series", ubuntu_series,
                    "--type", build_type.value,
                ]
                if cloud_archive:
                    cmd.extend(["--cloud-archive", cloud_archive])
                if force:
                    cmd.append("--force")
                if offline:
                    cmd.append("--offline")
                if not binary:
                    cmd.append("--no-binary")
                # Continue building deps of deps
                cmd.append("--build-deps")
                # Don't ask for confirmations
                cmd.append("--yes")
            
                activity("auto-build", f"Running: {' '.join(cmd)}")
            
                try:
                    result = subprocess.run(
                        cmd,
                        env=child_env,
                        cwd=str(paths["local_apt_repo"]),
                        capture_output=False,  # Stream output to console
                    )
                
                    if result.returncode != 0:
                        activity("auto-build", f"Dependency build failed: {dep_pkg} (exit code: {result.returncode})")
                        run.log_event({
                            "event": "auto-build.failed",
                            "package": dep_pkg,
                            "exit_code": result.returncode,
                        })
                        run.write_summary(
                            status="failed",
                            error=f"Dependency build failed: {dep_pkg}",
                            exit_code=result.returncode,
                        )
                        return result.returncode
                
                    activity("auto-build", f"Successfully built dependency: {dep_pkg}")
                    run.log_event({
                        "event": "auto-build.success",
                        "package": dep_pkg,
                    })
                
                except FileNotFoundError:
                    activity("auto-build", "Error: packastack command not found")
                    run.write_summary(
                        status="failed",
                        error="packastack command not found for auto-build",
                        exit_code=EXIT_TOOL_MISSING,
                    )
                    return EXIT_TOOL_MISSING
        
            activity("auto-build", f"All {len(new_deps_to_build)} dependencies built successfully")
            run.log_event({
                "event": "auto-build.complete",
                "count": len(new_deps_to_build),
            })
        
            # Refresh local package index after building dependencies
            activity("auto-build", "Refreshing local package index")
            local_index = load_package_index(local_repo)
            if local_index:
                run.log_event({"event": "auto-build.index_refreshed"})

        # Determine version
        current_version = get_current_version(debian_dir / "changelog")
        if current_version:
            parsed = parse_version(current_version)
            activity("prepare", f"Current version: {current_version}")
        else:
            parsed = None

        if build_type == BuildType.RELEASE and upstream:
            new_version = generate_release_version(
                upstream.version, epoch=parsed.epoch if parsed else 0
            )
        elif build_type == BuildType.MILESTONE and upstream:
            new_version = generate_milestone_version(
                upstream.version, milestone_str, epoch=parsed.epoch if parsed else 0
            )
        elif build_type == BuildType.SNAPSHOT:
            # Use the version computed by acquire_upstream_snapshot using git describe
            # This accurately reflects the upstream state (tag + commits)
            if snapshot_result and snapshot_result.upstream_version:
                upstream_ver = snapshot_result.upstream_version
            else:
                # Fallback for forced builds or errors
                if parsed:
                    next_upstream = increment_upstream_version(parsed.upstream)
                else:
                    next_upstream = "0.0.0"
                upstream_ver = f"{next_upstream}~git{git_date}.{git_sha[:7]}"

            # Apply epoch and debian revision
            epoch = parsed.epoch if parsed else 0
            if epoch:
                new_version = f"{epoch}:{upstream_ver}-0ubuntu1"
            else:
                new_version = f"{upstream_ver}-0ubuntu1"
        else:
            new_version = current_version or "0.0.0-0ubuntu1"

        activity("prepare", f"New version: {new_version}")
        run.log_event({"event": "prepare.version", "current": current_version, "new": new_version})

        # Update changelog
        changes = generate_changelog_message(
            build_type.value,
            upstream.version if upstream else "",
            git_sha,
            signature_verified,
            signature_warning,
        )
        if update_changelog(
            debian_dir / "changelog",
            pkg_name,
            new_version,
            resolved_ubuntu,
            changes,
            prefer_gbp=use_gbp_dch,
        ):
            activity("prepare", "Updated debian/changelog")
        else:
            activity("prepare", "Warning: failed to update changelog")

        # Update debian/gbp.conf to match launchpad.yaml branch
        gbp_success, gbp_updated, gbp_error = update_gbp_conf_from_launchpad_yaml(pkg_repo)
        if gbp_success and gbp_updated:
            activity("prepare", "Updated debian/gbp.conf")
            for update in gbp_updated:
                activity("prepare", f"  {update}")
        elif gbp_error and "No launchpad.yaml" not in gbp_error:
            activity("prepare", f"Warning: gbp.conf update issue: {gbp_error}")

        # Apply man pages support if upstream has Sphinx man_pages configured
        man_result = apply_man_pages_support(pkg_repo)
        if man_result.applied:
            activity("prepare", "Applied man pages support from upstream Sphinx docs")
            if man_result.control_modified:
                activity("prepare", "  - Added python3-sphinx to Build-Depends")
            if man_result.rules_modified:
                activity("prepare", "  - Updated debian/rules to build man pages")
            if man_result.manpages_created:
                activity("prepare", "  - Created .manpages file for installation")
            run.log_event({
                "event": "prepare.manpages",
                "control_modified": man_result.control_modified,
                "rules_modified": man_result.rules_modified,
                "manpages_created": man_result.manpages_created,
            })

        # Apply lintian fixes
        # Fix deprecated Priority: extra -> optional
        if fix_priority_extra(debian_dir / "control"):
            activity("prepare", "Fixed deprecated Priority: extra -> optional")
            run.log_event({"event": "prepare.fix_priority_extra"})

        # Add doctree cleanup to prevent package-contains-python-doctree-file
        if add_doctree_cleanup(pkg_repo / "debian" / "rules"):
            activity("prepare", "Added .doctrees cleanup to debian/rules")
            run.log_event({"event": "prepare.doctree_cleanup"})

        # Ensure PGP verification in watch file is valid (remove if no key exists)
        pgp_modified, pgp_msg = ensure_pgp_verification_valid(debian_dir)
        if pgp_modified:
            activity("prepare", pgp_msg)
            run.log_event({"event": "prepare.pgp_watch_fix", "message": pgp_msg})

        # Ensure packages with systemd units have proper Pre-Depends
        if ensure_misc_pre_depends(debian_dir / "control"):
            activity("prepare", "Added ${misc:Pre-Depends} for init-system-helpers")
            run.log_event({"event": "prepare.misc_pre_depends"})

        # Commit changes to ensure clean working directory for gbp pq
        # gbp pq requires a clean git tree to operate
        # Include changelog entries in commit message so gbp dch attributes them correctly
        activity("prepare", "Committing packaging changes")
        run_command(["git", "add", "."], cwd=pkg_repo)
        
        # Build commit message with changelog entries for gbp dch to extract
        commit_message_lines = [f"Prepare {pkg_name} {new_version}", ""]
        commit_message_lines.extend(changes)
        commit_message = "\n".join(commit_message_lines)
        
        commit_cmd = _maybe_disable_gpg_sign(["git", "commit", "-m", commit_message])
        git_env = _get_git_author_env()
        activity("prepare", f"Git author env for Prepare commit: {git_env}")
        run_command(commit_cmd, cwd=pkg_repo, env=git_env)

        # =========================================================================
        # PHASE: import-orig
        # =========================================================================
        # Import the upstream tarball so it's available in pristine-tar branch
        if upstream_tarball and upstream_tarball.exists():
            activity("import-orig", f"Importing upstream tarball: {upstream_tarball.name}")

            # Ensure the upstream branch for this series exists
            upstream_branch_name = f"upstream-{openstack_target}"

            branch_result = ensure_upstream_branch(pkg_repo, openstack_target, prev_series)
            if branch_result.success:
                if branch_result.created:
                    activity("import-orig", f"Created upstream branch: {upstream_branch_name}")
                    if prev_series:
                        activity("import-orig", f"  (branched from upstream-{prev_series})")
                else:
                    activity("import-orig", f"Using upstream branch: {upstream_branch_name}")
                run.log_event({
                    "event": "import-orig.branch",
                    "branch": upstream_branch_name,
                    "created": branch_result.created,
                })
            else:
                activity("import-orig", f"Failed to ensure upstream branch: {branch_result.error}")
                if not force:
                    run.write_summary(
                        status="failed",
                        error=branch_result.error,
                        exit_code=EXIT_FETCH_FAILED,
                    )
                    return EXIT_FETCH_FAILED
                run.log_event({
                    "event": "import-orig.branch_failed",
                    "error": branch_result.error,
                })

            # Extract the upstream version for import-orig
            # For snapshots, use the version from git describe
            # For releases, use the upstream version
            if build_type == BuildType.SNAPSHOT and snapshot_result:
                import_version = snapshot_result.upstream_version
            elif upstream:
                import_version = upstream.version
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
                run.log_event({
                    "event": "import-orig.complete",
                    "tarball": str(upstream_tarball),
                    "version": import_result.upstream_version,
                })
            else:
                activity("import-orig", f"Import failed: {import_result.output}")
                if not force:
                    run.write_summary(
                        status="failed",
                        error="Failed to import upstream tarball",
                        exit_code=EXIT_FETCH_FAILED,
                    )
                    return EXIT_FETCH_FAILED
                run.log_event({
                    "event": "import-orig.failed",
                    "output": import_result.output,
                })
        else:
            activity("import-orig", "No upstream tarball to import")

        # =========================================================================
        # PHASE: patches
        # =========================================================================
        activity("patches", "Applying patches with gbp pq")

        # Check for upstreamed patches first
        upstreamed = check_upstreamed_patches(pkg_repo)
        if upstreamed:
            activity("patches", f"Potentially upstreamed patches: {len(upstreamed)}")
            for report in upstreamed:
                activity("patches", f"  {report.patch_name}: {report.suggested_action}")
            if not force:
                activity("patches", "Use --force to continue with potentially upstreamed patches")
                run.write_summary(
                    status="failed",
                    error="Patches appear to be upstreamed",
                    patches=[str(r) for r in upstreamed],
                    exit_code=EXIT_PATCH_FAILED,
                )
                return EXIT_PATCH_FAILED
            run.log_event({"event": "patches.upstreamed", "patches": [r.patch_name for r in upstreamed]})

        # Import patches
        pq_result = pq_import(pkg_repo)
        if pq_result.success:
            activity("patches", "Patches applied successfully")
        elif pq_result.needs_refresh:
            activity("patches", "Patches need refresh - forcing import with time-machine")
            # Force import with time-machine=0 to accept offset/fuzz
            force_result = pq_import(pkg_repo, time_machine=0)
            if force_result.success:
                activity("patches", "Patches imported with offset/fuzz - exporting refreshed patches")
                export_result = pq_export(pkg_repo)
                if export_result.success:
                    activity("patches", "Patches refreshed successfully")
                else:
                    activity("patches", f"Patch export failed: {export_result.output}")
                    if not force:
                        run.write_summary(
                            status="failed",
                            error="Patch export failed",
                            exit_code=EXIT_PATCH_FAILED,
                        )
                        return EXIT_PATCH_FAILED
            else:
                activity("patches", f"Forced import failed: {force_result.output}")
                if not force:
                    run.write_summary(
                        status="failed",
                        error="Patch refresh failed",
                        exit_code=EXIT_PATCH_FAILED,
                    )
                    return EXIT_PATCH_FAILED
        else:
            activity("patches", f"Patch import failed: {pq_result.output}")
            # Generate patch health report
            for report in pq_result.patch_reports:
                activity("patches", f"  {report}")
            if not force:
                run.write_summary(
                    status="failed",
                    error="Patch import failed",
                    patches=[str(r) for r in pq_result.patch_reports],
                    exit_code=EXIT_PATCH_FAILED,
                )
                return EXIT_PATCH_FAILED

        run.log_event({"event": "patches.complete", "success": pq_result.success})

        # Export patches and return to master branch for subsequent steps
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
                    if not force:
                        run.write_summary(
                            status="failed",
                            error="Patch export failed",
                            exit_code=EXIT_PATCH_FAILED,
                        )
                        return EXIT_PATCH_FAILED
            else:
                activity("patches", f"Skipping patch export: current branch {current_branch or 'unknown'}")

            checkout_rc, checkout_out, checkout_err = run_command(["git", "checkout", "master"], cwd=pkg_repo)
            if checkout_rc != 0:
                activity("patches", f"Failed to checkout master after export: {checkout_err or checkout_out}")
                if not force:
                    run.write_summary(
                        status="failed",
                        error="Failed to checkout master after patch export",
                        exit_code=EXIT_PATCH_FAILED,
                    )
                    return EXIT_PATCH_FAILED
            else:
                activity("patches", "Checked out master after patch export")
                activity("patches", "Committing refreshed patches on master")
                run_command(["git", "add", "debian/patches"], cwd=pkg_repo)
                commit_cmd = _maybe_disable_gpg_sign(["git", "commit", "-m", "Refresh patches"])
                commit_rc, commit_out, commit_err = run_command(commit_cmd, cwd=pkg_repo, env=_get_git_author_env())
                if commit_rc == 0:
                    activity("patches", "Recorded refreshed patches commit")
                else:
                    activity(
                        "patches",
                        f"Patch commit skipped: {commit_err or commit_out or 'no changes to commit'}",
                    )
        else:
            activity("patches", "Skipping patch export/checkout (not a git repo)")

        # =========================================================================
        # PHASE: build
        # =========================================================================
        activity("build", "Building source package")

        # Use a dedicated build output directory to avoid conflicts with git repo
        # (gbp --git-export-dir creates a subdir with package name, which would
        # conflict if output_dir already contains the packaging repo)
        build_output = workspace / "build-output"
        build_output.mkdir(parents=True, exist_ok=True)

        # Disable pristine-tar for snapshot builds (tarball not in pristine-tar branch)
        use_pristine_tar = build_type != BuildType.SNAPSHOT
        build_result = build_source(pkg_repo, build_output, pristine_tar=use_pristine_tar)
        if build_result.success:
            activity("build", "Source package built successfully")
            for artifact in build_result.artifacts:
                activity("build", f"  {artifact.name}")
            run.log_event({
                "event": "build.source_complete",
                "artifacts": [str(a) for a in build_result.artifacts],
            })
        else:
            activity("build", f"Source build failed: {build_result.output}")
            run.write_summary(status="failed", error="Source build failed", exit_code=EXIT_BUILD_FAILED)
            return EXIT_BUILD_FAILED

        # Optional binary build
        if binary and build_result.dsc_file:
            # Determine which builder to use
            use_builder = Builder.SBUILD if builder == "sbuild" else Builder.DPKG

            if use_builder == Builder.SBUILD:
                # Check sbuild availability
                if not is_sbuild_available():
                    activity("build", "sbuild not available, falling back to dpkg-buildpackage")
                    use_builder = Builder.DPKG
                else:
                    # Ensure local repo has indexes before sbuild (may be empty but needs structure)
                    # Skip regeneration if caller (e.g., build-all coordinator) manages indexes
                    if not skip_repo_regen:
                        _refresh_local_repo_indexes(local_repo, get_host_arch(), run, phase="build")
                    
                    # Use sbuild wrapper with local repo support and log capture
                    sbuild_config = SbuildConfig(
                        dsc_path=build_result.dsc_file,
                        output_dir=build_output,
                        distribution=resolved_ubuntu,
                        arch=get_host_arch(),
                        local_repo_root=local_repo,
                        chroot_name=schroot_name,
                        run_log_dir=run.logs_path,
                        source_package=package,
                        version=str(parse_version(get_current_version(pkg_repo / "debian" / "changelog"))) if pkg_repo else None,
                        # Suppress lintian error for maintainer mismatch (local user vs Ubuntu Developers)
                        lintian_suppress_tags=["inconsistent-maintainer"],
                    )
                
                    # Pre-build diagnostic message
                    activity("build", f"Running sbuild (binary): {build_result.dsc_file.name}")
                    activity("build", f"sbuild logs will be captured to: {run.logs_path}/sbuild.*.log")
                
                    with activity_spinner(
                        "sbuild",
                        f"Building {build_result.dsc_file.name} ({resolved_ubuntu}/{get_host_arch()})",
                        disable=no_spinner,
                    ):
                        sbuild_result = run_sbuild(sbuild_config)
                
                    # Post-build diagnostic messages
                    activity("build", f"sbuild exited: {sbuild_result.exit_code}")
                
                    # Log sbuild command and result to events.jsonl
                    run.log_event({
                        "event": "build.sbuild_command",
                        "command": sbuild_result.command,
                        "exit_code": sbuild_result.exit_code,
                        "stdout_path": str(sbuild_result.stdout_log_path) if sbuild_result.stdout_log_path else None,
                        "stderr_path": str(sbuild_result.stderr_log_path) if sbuild_result.stderr_log_path else None,
                    })
                
                    if sbuild_result.searched_dirs:
                        top_dirs = sbuild_result.searched_dirs[:3]
                        activity("build", f"artifact search paths (top 3): {', '.join(top_dirs)}")
                
                    if sbuild_result.success:
                        # Count collected artifacts
                        deb_count = sum(1 for a in sbuild_result.collected_artifacts if a.source_path.suffix in {".deb", ".udeb"})
                        changes_count = sum(1 for a in sbuild_result.collected_artifacts if a.source_path.suffix == ".changes")
                        buildinfo_count = sum(1 for a in sbuild_result.collected_artifacts if a.source_path.suffix == ".buildinfo")
                    
                        activity("build", f"collected binaries: {deb_count} debs, changes: {changes_count}, buildinfo: {buildinfo_count}")
                        activity("build", f"sbuild logs copied: {len(sbuild_result.collected_logs)}" + 
                                 (f" (primary: {sbuild_result.primary_log_path})" if sbuild_result.primary_log_path else ""))
                    
                        activity("build", "Binary package built successfully (sbuild)")
                        for artifact in sbuild_result.artifacts:
                            activity("build", f"  {artifact.name}")
                        run.log_event({
                            "event": "build.binary_complete",
                            "builder": "sbuild",
                            "artifacts": [str(a) for a in sbuild_result.artifacts],
                            "deb_count": deb_count,
                            "report_path": str(sbuild_result.report_path) if sbuild_result.report_path else None,
                        })
                        # Merge sbuild artifacts into build result for publishing
                        build_result.artifacts.extend(sbuild_result.artifacts)
                    else:
                        # Binary build failed - show diagnostic info
                        activity("build", "ERROR: no binaries found; check:")
                        if sbuild_result.stdout_log_path:
                            activity("build", f"  stdout: {sbuild_result.stdout_log_path}")
                        if sbuild_result.stderr_log_path:
                            activity("build", f"  stderr: {sbuild_result.stderr_log_path}")
                        if sbuild_result.primary_log_path:
                            activity("build", f"  primary log: {sbuild_result.primary_log_path}")
                        else:
                            activity("build", "  primary log: not found")
                    
                        activity("build", f"Binary build failed: {sbuild_result.validation_message}")
                        run.log_event({
                            "event": "build.binary_failed",
                            "builder": "sbuild",
                            "exit_code": sbuild_result.exit_code,
                            "validation_message": sbuild_result.validation_message,
                            "searched_dirs": sbuild_result.searched_dirs,
                            "output": sbuild_result.output[:2000] if sbuild_result.output else "",
                        })
                    
                        # Binary build failure with sbuild is now fatal if no debs found
                        run.write_summary(
                            status="failed",
                            error=f"Binary build failed: {sbuild_result.validation_message}",
                            exit_code=EXIT_BUILD_FAILED,
                        )
                        return EXIT_BUILD_FAILED

            if use_builder == Builder.DPKG:
                activity("build", "Building binary package with dpkg-buildpackage")
                binary_result = build_binary(build_result.dsc_file, build_output, resolved_ubuntu)
                if binary_result.success:
                    activity("build", "Binary package built successfully (dpkg)")
                    for artifact in binary_result.artifacts:
                        activity("build", f"  {artifact.name}")
                    run.log_event({
                        "event": "build.binary_complete",
                        "builder": "dpkg",
                        "artifacts": [str(a) for a in binary_result.artifacts],
                    })
                else:
                    activity("build", f"Binary build failed: {binary_result.output}")
                    # Binary build failure is not fatal
                    run.log_event({"event": "build.binary_failed", "builder": "dpkg", "output": binary_result.output})

        # =========================================================================
        # PHASE: verify
        # =========================================================================
        activity("verify", "Verifying build artifacts")

        if build_result.dsc_file and build_result.dsc_file.exists():
            activity("verify", f"Source: {build_result.dsc_file.name}")
        if build_result.changes_file and build_result.changes_file.exists():
            activity("verify", f"Changes: {build_result.changes_file.name}")

        host_arch = get_host_arch()

        # Publish artifacts to local APT repository
        if build_result.artifacts:
            activity("verify", "Publishing artifacts to local APT repository")
        
            # Show artifact paths before publishing (no debug suffix)
            for art in build_result.artifacts:
                activity("verify", f"  artifact to publish: {art}")
        
            # Count binary artifacts for verification
            deb_artifacts = [a for a in build_result.artifacts if a.suffix in {".deb", ".udeb", ".ddeb"}]
        
            publish_result = localrepo.publish_artifacts(
                artifact_paths=build_result.artifacts,
                repo_root=local_repo,
                arch=host_arch,
            )
        
            if publish_result.success:
                # Count published debs
                published_debs = [p for p in publish_result.published_paths if p.suffix in {".deb", ".udeb", ".ddeb"}]
                activity("verify", f"Published binaries: {len(published_debs)} debs")
                activity("verify", f"Published {len(publish_result.published_paths)} files to local repo")
                run.log_event({
                    "event": "verify.publish",
                    "published": [str(p) for p in publish_result.published_paths],
                    "deb_count": len(published_debs),
                })

                # Skip index regeneration if coordinator handles it (e.g., build-all mode)
                if not skip_repo_regen:
                    binary_index_result, _ = _refresh_local_repo_indexes(local_repo, host_arch, run)

                    # Verify that published debs are reflected in index
                    if (
                        binary_index_result.success
                        and len(published_debs) > 0
                        and binary_index_result.package_count == 0
                    ):
                        activity("verify", "WARNING: Published debs but Packages index is empty!")
                        run.log_event({
                            "event": "verify.index_mismatch",
                            "published_debs": len(published_debs),
                            "index_packages": binary_index_result.package_count,
                        })
            else:
                activity("verify", f"Warning: Failed to publish artifacts: {publish_result.error}")
                run.log_event({"event": "verify.publish_failed", "error": publish_result.error})
                if not skip_repo_regen:
                    _refresh_local_repo_indexes(local_repo, host_arch, run)

        else:
            activity("verify", "No build artifacts to publish; ensuring local repo metadata exists")
            if not skip_repo_regen:
                _refresh_local_repo_indexes(local_repo, host_arch, run)

        activity("verify", "Verification complete")

        # =========================================================================
        # PHASE: provenance
        # =========================================================================
        # Update provenance with final details and write to disk
        provenance.verification.result = "verified" if signature_verified else "skipped"
        if signature_warning:
            provenance.verification.result = "not_applicable"

        # Write provenance file
        try:
            provenance_path = write_provenance(provenance, run.run_path)
            activity("provenance", f"Written to: {provenance_path}")
            run.log_event({
                "event": "provenance.written",
                "path": str(provenance_path),
            })
        except Exception as e:
            activity("provenance", f"Warning: Failed to write provenance: {e}")
            run.log_event({"event": "provenance.write_failed", "error": str(e)})

        # =========================================================================
        # PHASE: report
        # =========================================================================
        activity("report", "Build Summary")
        activity("report", f"  Package: {pkg_name}")
        activity("report", f"  Version: {new_version}")
        activity("report", f"  Build type: {build_type.value}")
        activity("report", f"  Upstream resolution: {resolution_source.value}")
        activity("report", f"  Workspace: {workspace}")

        if upload and build_result.changes_file:
            activity("report", "Upload commands:")
            activity("report", f"  dput ppa:ubuntu-openstack-dev/proposed {build_result.changes_file}")

        run.write_summary(
            status="success",
            package=pkg_name,
            version=new_version,
            build_type=build_type.value,
            build_order=build_order,
            upload_order=upload_order,
            signature_verified=signature_verified,
            artifacts=[str(a) for a in build_result.artifacts],
            provenance=summarize_provenance(provenance),
            exit_code=EXIT_SUCCESS,
        )

        activity("report", f"Package {pkg_idx}/{len(plan_result.build_order)} complete: {pkg_name}")
        activity("report", f"Logs: {run.run_path}")
    
    # All packages built successfully
    activity("build", f"Successfully built all {len(plan_result.build_order)} package(s)")
    return EXIT_SUCCESS
