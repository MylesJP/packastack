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

import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from packastack.core.context import BuildRequest

from packastack.core.config import load_config
from packastack.planning.graph import DependencyGraph
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
)
from packastack.core.run import RunContext, activity
from packastack.target.series import resolve_series
from packastack.apt import localrepo
from packastack.target.arch import get_host_arch
from packastack.build.provenance import (
    summarize_provenance,
)
from packastack.commands.init import _clone_or_update_project_config
from packastack.build.tools import check_required_tools

# Build helpers (refactored modules)
from packastack.build import (
    # Exit codes - re-exported for backwards compatibility
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
from packastack.build.git_helpers import (
    _ensure_no_merge_paths,
    _get_git_author_env,
    _maybe_disable_gpg_sign,
)
from packastack.build.type_resolution import (
    build_type_from_string,
    resolve_build_type_auto,
    resolve_build_type_from_cli,
)
# Private aliases for backwards compatibility
_build_type_from_string = build_type_from_string
_resolve_build_type_auto = resolve_build_type_auto
_resolve_build_type_from_cli = resolve_build_type_from_cli

from packastack.build.all_helpers import (
    build_dependency_graph,
    build_upstream_versions_from_packaging,
    filter_retired_packages,
    get_parallel_batches,
    run_single_build,
)

# Build-all imports
import concurrent.futures
import contextlib
import threading
from datetime import datetime

from packastack.core.context import BuildAllRequest
from packastack.planning.cycle_suggestions import suggest_cycle_edge_exclusions
from packastack.planning.build_all_state import (
    BuildAllState,
    FailureType,
    PackageStatus,
    create_initial_state,
    load_state,
    save_state,
)
from packastack.planning.graph_builder import OPTIONAL_BUILD_DEPS
from packastack.planning.package_discovery import (
    discover_packages,
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

# Delegate to packastack.build.all_helpers
_build_dependency_graph = build_dependency_graph
_build_upstream_versions_from_packaging = build_upstream_versions_from_packaging
_get_parallel_batches = get_parallel_batches


def _filter_retired_packages(
    packages: list[str],
    project_config_path: Path | None,
    releases_repo: Path | None,
    openstack_target: str,
    offline: bool,
    run: RunContext,
) -> tuple[list[str], list[str], list[str]]:
    """Filter retired packages using openstack/project-config and releases inference."""
    return filter_retired_packages(
        packages=packages,
        project_config_path=project_config_path,
        releases_repo=releases_repo,
        openstack_target=openstack_target,
        offline=offline,
        run=run,
        clone_project_config_fn=_clone_or_update_project_config,
    )


# Alias for backward compatibility - implementation moved to all_helpers
_run_single_build = run_single_build


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


# Import from extracted module - re-exported for backwards compatibility
from packastack.build.localrepo_helpers import (
    refresh_local_repo_indexes as _refresh_local_repo_indexes,
)


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
    
    # Build all packages in dependency order using extracted orchestrator
    from packastack.build.single_build import (
        SetupInputs,
        build_single_package,
        setup_build_context,
    )

    activity("build", f"Building {len(plan_result.build_order)} package(s) in dependency order")
    
    for pkg_idx, pkg_name in enumerate(plan_result.build_order, 1):
        activity("build", f"[{pkg_idx}/{len(plan_result.build_order)}] Building: {pkg_name}")
        
        # Create setup inputs for the orchestrator
        setup_inputs = SetupInputs(
            pkg_name=pkg_name,
            target=request.target,
            ubuntu_series=request.ubuntu_series,
            cloud_archive=request.cloud_archive,
            build_type_str=request.build_type_str,
            milestone=request.milestone,
            binary=request.binary,
            builder=request.builder,
            force=request.force,
            offline=request.offline,
            use_gbp_dch=request.use_gbp_dch,
            skip_repo_regen=request.skip_repo_regen,
            no_spinner=request.no_spinner,
            build_deps=request.build_deps,
            include_retired=request.include_retired,
            resolved_build_type_str=resolved_build_type_str,
            milestone_from_cli=milestone_from_cli,
            paths=paths,
            cfg=cfg,
            run=run,
        )
        
        # Run setup phases (retirement, registry, policy, indexes, tools, schroot)
        setup_result, ctx = setup_build_context(setup_inputs)
        if not setup_result.success:
            return setup_result.exit_code

        # Run the package build using the orchestrator
        outcome = build_single_package(ctx, workspace_ref=request.workspace_ref)
        
        if not outcome.success:
            run.write_summary(
                status="failed",
                error=outcome.error,
                exit_code=outcome.exit_code,
            )
            return outcome.exit_code
        
        # Show upload commands if requested
        if request.upload and outcome.artifacts:
            # Find the .changes file
            changes_files = [a for a in outcome.artifacts if a.suffix == ".changes"]
            if changes_files:
                activity("report", "Upload commands:")
                activity("report", f"  dput ppa:ubuntu-openstack-dev/proposed {changes_files[0]}")
        
        # Write final summary
        run.write_summary(
            status="success",
            package=ctx.pkg_name,
            version=outcome.new_version,
            build_type=outcome.build_type,
            build_order=plan_result.build_order,
            upload_order=plan_result.upload_order,
            signature_verified=outcome.signature_verified,
            artifacts=[str(a) for a in outcome.artifacts],
            provenance=summarize_provenance(ctx.provenance) if ctx.provenance else None,
            exit_code=EXIT_SUCCESS,
        )

        activity("report", f"Package {pkg_idx}/{len(plan_result.build_order)} complete: {pkg_name}")
        activity("report", f"Logs: {run.run_path}")
    
    # All packages built successfully
    activity("build", f"Successfully built all {len(plan_result.build_order)} package(s)")
    return EXIT_SUCCESS

