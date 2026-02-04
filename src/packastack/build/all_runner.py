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

"""Build-all execution and orchestration.

This module provides the main execution logic for building all packages
in dependency order, including sequential and parallel execution modes.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from packastack.apt.packages import (
    PackageIndex,
    load_cloud_archive_index,
    load_local_repo_index,
    load_package_index,
    merge_package_indexes,
)
from packastack.build.all_helpers import (
    build_upstream_versions_from_packaging,
    get_parallel_batches,
    run_single_build,
)
from packastack.build.all_reports import generate_build_all_reports
from packastack.build.errors import (
    EXIT_ALL_BUILD_FAILED,
    EXIT_DISCOVERY_FAILED,
    EXIT_GRAPH_ERROR,
    EXIT_RESUME_ERROR,
    EXIT_SUCCESS,
)
from packastack.build.localrepo_helpers import refresh_local_repo_indexes
from packastack.core.config import load_config
from packastack.core.context import BuildAllRequest
from packastack.core.paths import resolve_paths
from packastack.core.run import RunContext, activity
from packastack.planning.build_all_state import (
    BuildAllState,
    FailureType,
    PackageStatus,
    create_initial_state,
    load_state,
    save_state,
)
from packastack.planning.cycle_suggestions import suggest_cycle_edge_exclusions
from packastack.planning.graph import DependencyGraph
from packastack.planning.package_discovery import (
    discover_packages,
    filter_by_managed_packages,
)
from packastack.planning.type_selection import get_default_parallel_workers
from packastack.reports.plan_graph import PlanGraph, render_waves
from packastack.target.arch import get_host_arch
from packastack.target.series import resolve_series
from packastack.upstream.releases import (
    get_current_development_series,
    load_openstack_packages,
)

if TYPE_CHECKING:
    pass


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
    # Unpack request for local use
    target = request.target
    ubuntu_series = request.ubuntu_series
    cloud_archive = request.cloud_archive
    build_type = request.build_type
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
        resume_state_dir = runs_root / resume_run_id / "state" if resume_run_id else state_dir

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
    local_repo = paths.get("local_apt_repo", paths["cache_root"] / "apt-repo")
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

        # Filter by managed_packages from cached file (fetched by init/refresh)
        from packastack.upstream.pkg_scripts import load_managed_packages

        cache_root = paths.get("cache_root", Path.home() / ".cache" / "packastack")
        managed_packages = load_managed_packages(cache_root)
        if managed_packages:
            managed_filtered, skipped = filter_by_managed_packages(
                discovery.packages, managed_packages
            )
            if skipped:
                activity("all", f"Filtered to managed packages: {len(managed_filtered)} of {len(discovery.packages)}")
                activity("all", f"  Skipped (not in managed_packages): {len(skipped)}")
                run.log_event({
                    "event": "build_all.managed_packages_filtered",
                    "managed_count": len(managed_filtered),
                    "skipped_count": len(skipped),
                    "skipped": skipped[:20],  # Only log first 20 to avoid huge logs
                })
                discovery.packages = managed_filtered

        # Load package indexes for dependency resolution
        activity("all", "Loading package indexes...")

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
                upstream_versions=build_upstream_versions_from_packaging(discovery.packages, build_root),
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

    if parallel > 1:
        _run_parallel_builds(
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
        _run_sequential_builds(
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
    state.completed_at = datetime.now(UTC).isoformat()
    save_state(state, state_dir)

    # Generate reports
    activity("all", "Generating reports...")
    json_report, md_report = generate_build_all_reports(state, run_dir)
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
    """Run builds sequentially in topological order.

    Args:
        state: Build state tracking progress.
        run_dir: Run directory for logs.
        state_dir: State persistence directory.
        target: OpenStack target series.
        ubuntu_series: Resolved Ubuntu series.
        cloud_archive: Cloud archive pocket.
        build_type: Build type string.
        binary: Whether to build binary packages.
        force: Force build despite warnings.
        local_repo: Path to local APT repository.
        run: RunContext for logging.

    Returns:
        Exit code.
    """
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        Progress,
        TaskProgressColumn,
        TextColumn,
        TimeRemainingColumn,
    )

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

            activity("all", f"[{i}/{total}] Building: {pkg}")

            state.mark_started(pkg)
            save_state(state, state_dir)

            success, failure_type, message, log_path = run_single_build(
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
                refresh_local_repo_indexes(local_repo, host_arch, run, phase="all")
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
    ppa_upload: bool = False,
) -> int:
    """Run builds in parallel, respecting dependencies.

    Args:
        state: Build state tracking progress.
        graph: Dependency graph for batch computation.
        run_dir: Run directory for logs.
        state_dir: State persistence directory.
        target: OpenStack target series.
        ubuntu_series: Resolved Ubuntu series.
        cloud_archive: Cloud archive pocket.
        build_type: Build type string.
        binary: Whether to build binary packages.
        force: Force build despite warnings.
        parallel: Number of parallel workers.
        local_repo: Path to local APT repository.
        run: RunContext for logging.
        ppa_upload: Whether to upload to PPA after build.

    Returns:
        Exit code.
    """
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        Progress,
        TaskProgressColumn,
        TextColumn,
        TimeRemainingColumn,
    )

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
                if ppa_upload and log_path:
                    log_text = ""
                    try:
                        log_text = Path(log_path).read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        log_text = ""

                    if "Successfully uploaded" in log_text:
                        activity("all", f"[ppa]   {pkg}: upload complete")
                    elif "PPA upload failed" in log_text or "PPA Rebuild failed" in log_text:
                        activity("all", f"[ppa]   {pkg}: upload failed (see log)")
                    else:
                        activity("all", f"[ppa]   {pkg}: no upload detected (see log)")
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
        batches = get_parallel_batches(graph, state)

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
                        run_single_build,
                        package=pkg,
                        target=target,
                        ubuntu_series=ubuntu_series,
                        cloud_archive=cloud_archive,
                        build_type=build_type,
                        binary=binary,
                        force=force,
                        run_dir=run_dir,
                        ppa_upload=ppa_upload,
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
            refresh_local_repo_indexes(local_repo, host_arch, run, phase="all")

            # Check failure policy after each batch
            if state.should_stop():
                activity("all", f"Stopping: failure limit reached ({len(failed_set)} failures)")
                break

            activity("all", f"Batch {batch_num} complete: {built} ok, {len(failed_set)} fail total")

    return EXIT_SUCCESS if not failed_set else EXIT_ALL_BUILD_FAILED


def _filter_retired_packages(
    packages: list[str],
    project_config_path: Path | None,
    releases_repo: Path | None,
    openstack_target: str,
    offline: bool,
    run: RunContext,
) -> tuple[list[str], list[str], list[str]]:
    """Filter retired packages using openstack/project-config and releases inference.

    Args:
        packages: List of package names to filter.
        project_config_path: Path to project-config clone.
        releases_repo: Path to releases repo clone.
        openstack_target: Target OpenStack series.
        offline: Whether running in offline mode.
        run: RunContext for logging.

    Returns:
        Tuple of (filtered_packages, retired, possibly_retired).
    """
    from packastack.build.all_helpers import filter_retired_packages
    from packastack.commands.init import _clone_or_update_project_config

    return filter_retired_packages(
        packages=packages,
        project_config_path=project_config_path,
        releases_repo=releases_repo,
        openstack_target=openstack_target,
        offline=offline,
        run=run,
        clone_project_config_fn=_clone_or_update_project_config,
    )
