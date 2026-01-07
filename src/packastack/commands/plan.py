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

"""Implementation of `packastack plan` command.

Analyzes packaging metadata, resolves dependencies, determines build order,
detects missing packages and MIR candidates, and produces plan outputs.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
import concurrent.futures

import typer

from packastack.core.config import load_config
from packastack.debpkg.control import ParsedDependency
from packastack.planning.graph import DependencyGraph, PlanResult
from packastack.planning.graph_builder import (
    SOFT_DEPENDENCY_EXCLUSIONS,
    build_graph_from_index,
)
from packastack.planning.cycle_suggestions import suggest_cycle_edge_exclusions
from packastack.apt.packages import (
    PackageIndex,
    load_package_index,
)
from packastack.core.paths import resolve_paths
from packastack.planning.package_discovery import discover_packages
from packastack.planning.type_selection import (
    BuildType,
    TypeSelectionReport,
    WatchConfig,
    get_default_parallel_workers,
    select_build_types_for_packages,
)
from packastack.planning.dependency_satisfaction import evaluate_dependencies
from packastack.reports.type_selection import (
    render_compact_summary,
    render_console_table,
    write_type_selection_reports,
)
from packastack.reports.watch_resolution import (
    write_watch_resolution_reports,
)
from packastack.reports.plan_graph import (
    PlanGraph,
    render_ascii,
    render_dot,
    render_waves,
    render_build_order_list,
    write_plan_graph_reports,
)
from packastack.reports.explain import write_plan_dependency_summary
from packastack.upstream.gitfetch import GitFetcher
from packastack.upstream.releases import (
    find_projects_by_prefix,
    get_current_development_series,
    is_snapshot_eligible,
    load_openstack_packages,
    load_project_releases,
    project_to_package_name,
)
from packastack.upstream.retirement import RetirementChecker, RetirementStatus
from packastack.upstream.registry import ProjectNotFoundError, UpstreamsRegistry
from packastack.commands.init import _clone_or_update_project_config
from packastack.core.run import RunContext, activity
from packastack.target.series import resolve_series
from packastack.target.distro_info import get_previous_lts
from packastack.core.spinner import activity_spinner

if TYPE_CHECKING:
    from packastack.core.run import RunContext as RunContextType

# Exit codes per spec
EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR = 1
EXIT_MISSING_PACKAGES = 5
EXIT_CYCLE_DETECTED = 6


def _fetch_packaging_repos(
    packages: list[str],
    dest_dir: Path,
    ubuntu_series: str,
    openstack_series: str,
    offline: bool,
    workers: int,
) -> dict[str, Path]:
    """Ensure packaging repos exist locally for watch/uscan.

    Returns a map of package -> repo path (only existing paths when offline).
    """
    import sys
    import time
    from rich.progress import Progress, BarColumn, TextColumn, TaskProgressColumn, TimeRemainingColumn
    from rich.console import Console

    fetcher = GitFetcher()
    dest_dir.mkdir(parents=True, exist_ok=True)

    if offline:
        return {pkg: dest_dir / pkg for pkg in packages if (dest_dir / pkg).exists()}

    resolved: dict[str, Path] = {}
    max_workers = max(1, workers or 1)

    # Use Rich progress bar writing to real terminal
    console = Console(file=sys.__stdout__, force_terminal=True)
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeRemainingColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Fetching packaging repos", total=len(packages))

        def _fetch(pkg: str) -> tuple[str, Path | None]:
            pkg_path = dest_dir / pkg
            if pkg_path.exists():
                progress.advance(task)
                return pkg, pkg_path

            result = fetcher.fetch_and_checkout(
                pkg,
                dest_dir,
                ubuntu_series,
                openstack_series,
                offline=False,
            )
            progress.advance(task)
            if result.error:
                return pkg, None
            return pkg, result.path

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for pkg, path in executor.map(_fetch, packages):
                if path and path.exists():
                    resolved[pkg] = path

    # Print summary after progress bar clears
    activity("fetch", f"Fetched {len(resolved)}/{len(packages)} packaging repositories")

    return resolved


def _parse_dep_name(dep_str: str) -> tuple[str, str, str]:
    """Parse a dependency string into (name, relation, version).

    Examples:
        "python3-foo (>= 1.0)" -> ("python3-foo", ">=", "1.0")
        "python3-bar" -> ("python3-bar", "", "")
    """
    # Remove :any or :native qualifiers
    dep_str = re.sub(r":(?:any|native)", "", dep_str.strip())

    # Remove architecture qualifiers [amd64]
    dep_str = re.sub(r"\[[^\]]+\]", "", dep_str).strip()

    # Pattern: name (relation version)
    match = re.match(r"^([a-z0-9][a-z0-9+\-.]+)(?:\s*\(([<>=]+)\s*([^)]+)\))?$", dep_str)
    if not match:
        return (dep_str, "", "")

    return (match.group(1), match.group(2) or "", (match.group(3) or "").strip())


def run_plan_for_package(
    request: "PlanRequest",
    run: RunContextType,
    cfg: dict,
    paths: dict[str, Path],
    verbose_output: bool = True,
) -> tuple[PlanResult, int]:
    """Run planning for a single package and return result.

    This is the core planning logic that can be reused by both
    the plan and build commands.

    Args:
        request: PlanRequest containing planning parameters.
        run: RunContext for logging.
        cfg: Loaded configuration.
        paths: Resolved paths.
        verbose_output: If True, show detailed planning output.
                       If False, only show warnings/errors.

    Returns:
        Tuple of (PlanResult, exit_code).
    """
    # Import here to avoid circular dependency
    from packastack.core.context import PlanRequest

    # Resolve series
    resolved_ubuntu = resolve_series(request.ubuntu_series)
    if verbose_output:
        activity("resolve", f"Ubuntu series: {resolved_ubuntu}")
    run.log_event({"event": "series.ubuntu_resolved", "series": resolved_ubuntu})

    # Resolve OpenStack target
    releases_repo = paths["openstack_releases_repo"]
    if request.target == "devel":
        openstack_target = get_current_development_series(releases_repo) or request.target
    else:
        openstack_target = request.target
    if verbose_output:
        activity("resolve", f"OpenStack target: {openstack_target}")
    run.log_event({"event": "series.openstack_resolved", "target": openstack_target})

    local_repo = paths["local_apt_repo"]
    registry = UpstreamsRegistry()

    # Resolve package targets
    if verbose_output:
        with activity_spinner("resolve", f"Finding packages matching: {request.package}"):
            resolved_targets = _resolve_package_targets(
                request.package,
                local_repo,
                releases_repo,
                registry,
                openstack_target,
                use_local=not request.skip_local,
                run=run,
            )
    else:
        resolved_targets = _resolve_package_targets(
            request.package,
            local_repo,
            releases_repo,
            registry,
            openstack_target,
            use_local=not request.skip_local,
            run=run,
        )

    if not resolved_targets:
        activity("resolve", f"No packages found matching: {request.package}")
        run.log_event({"event": "resolve.no_matches", "query": request.package})
        return PlanResult([], [], {}, {}, []), EXIT_CONFIG_ERROR

    target_names = [t.source_package for t in resolved_targets]
    if len(target_names) > 1 and not request.force:
        activity("resolve", f"Multiple matches found: {', '.join(target_names)}")
        activity("resolve", "Use --force to proceed with all, or specify exact name")
        run.log_event({"event": "resolve.multiple_matches", "matches": target_names})
        return PlanResult([], [], {}, {}, []), EXIT_CONFIG_ERROR

    for t in resolved_targets:
        if verbose_output:
            activity("resolve", f"Target: {t.source_package} ({t.resolution_source})")
    run.log_event({"event": "resolve.targets", "targets": target_names})

    # Retirement policy (registry + project-config)
    retirement_exit = _enforce_retirement(
        targets=resolved_targets,
        include_retired=request.include_retired,
        registry=registry,
        project_config_path=paths.get("openstack_project_config"),
        releases_repo=releases_repo,
        openstack_target=openstack_target,
        offline=request.offline,
        run=run,
    )
    if retirement_exit:
        return PlanResult([], [], {}, {}, []), retirement_exit

    targets = target_names

    # Phase: policy - check snapshot eligibility
    # Skip this check if build_type is explicitly set to RELEASE or MILESTONE
    # (means caller already resolved build type and wants to use release/milestone)
    skip_snapshot_check = request.build_type in ("release", "milestone")
    
    policy_issues = []
    preferred_versions = {}
    if not skip_snapshot_check:
        if verbose_output:
            with activity_spinner("policy", "Checking snapshot eligibility"):
                for t in resolved_targets:
                    if t.resolution_source.startswith("registry"):
                        run.log_event({"event": "policy.skip_snapshot_registry", "package": t.source_package})
                        continue
                    eligible, reason, preferred_version = is_snapshot_eligible(
                        releases_repo, openstack_target, t.source_package
                    )
                    if not eligible:
                        policy_issues.append(f"{t.source_package}: {reason}")
                        run.log_event({"event": "policy.blocked", "package": t.source_package, "reason": reason})
                        if preferred_version:
                            preferred_versions[t.source_package] = preferred_version
                    elif "Warning" in reason:
                        activity("policy", f"Warning: {t.source_package}: {reason}")
                        run.log_event({"event": "policy.warning", "package": t.source_package, "reason": reason})
        else:
            for t in resolved_targets:
                if t.resolution_source.startswith("registry"):
                    run.log_event({"event": "policy.skip_snapshot_registry", "package": t.source_package})
                    continue
                eligible, reason, preferred_version = is_snapshot_eligible(
                    releases_repo, openstack_target, t.source_package
                )
                if not eligible:
                    policy_issues.append(f"{t.source_package}: {reason}")
                    run.log_event({"event": "policy.blocked", "package": t.source_package, "reason": reason})
                    if preferred_version:
                        preferred_versions[t.source_package] = preferred_version

        if policy_issues and not request.force:
            for issue in policy_issues:
                activity("policy", f"Blocked: {issue}")
            activity("policy", "Use --force to override policy checks")
            return PlanResult([], [], {}, {}, []), EXIT_CONFIG_ERROR
        elif policy_issues:
            for issue in policy_issues:
                activity("policy", f"Warning (forced): {issue}")

        if verbose_output:
            activity("policy", "Snapshot eligibility: OK")
    else:
        if verbose_output:
            activity("policy", f"Snapshot eligibility: Skipped (build_type={request.build_type})")

    # Phase: plan - Load Ubuntu package index
    pockets = cfg.get("defaults", {}).get("ubuntu_pockets", ["release", "updates", "security"])
    components = cfg.get("defaults", {}).get("ubuntu_components", ["main", "universe"])
    ubuntu_cache = paths["ubuntu_archive_cache"]
    
    if verbose_output:
        with activity_spinner("plan", "Loading Ubuntu package index"):
            ubuntu_index = load_package_index(ubuntu_cache, resolved_ubuntu, pockets, components)
    else:
        ubuntu_index = load_package_index(ubuntu_cache, resolved_ubuntu, pockets, components)

    # Auto-refresh if index is empty and not in offline mode
    if len(ubuntu_index.sources) == 0 and not request.offline:
        if verbose_output:
            activity("plan", "Package index empty, refreshing from archive")
        
        from packastack.commands.refresh import refresh_ubuntu_archive, RefreshConfig
        
        try:
            refresh_config = RefreshConfig.from_lists(
                ubuntu_series=resolved_ubuntu,
                pockets=pockets,
                components=components,
                arches=["host", "all"],
                mirror=cfg.get("mirrors", {}).get("ubuntu_archive", "http://archive.ubuntu.com/ubuntu"),
                ttl_seconds=0,
                force=True,
                offline=False,
            )
            refresh_ubuntu_archive(refresh_config, run=run)
            
            # Reload index after refresh
            if verbose_output:
                with activity_spinner("plan", "Reloading Ubuntu package index"):
                    ubuntu_index = load_package_index(ubuntu_cache, resolved_ubuntu, pockets, components)
            else:
                ubuntu_index = load_package_index(ubuntu_cache, resolved_ubuntu, pockets, components)
        except Exception as e:
            activity("warn", f"Could not auto-refresh package index: {e}")

    run.log_event({
        "event": "plan.index_loaded",
        "packages": len(ubuntu_index.packages),
        "sources": len(ubuntu_index.sources),
    })

    if verbose_output:
        activity("plan", f"Loaded {len(ubuntu_index.packages)} packages from Ubuntu index")

    # Load local index if available
    local_index: PackageIndex | None = None

    # Build dependency graph
    if verbose_output:
        with activity_spinner("plan", "Building dependency graph"):
            graph, mir_candidates = _build_dependency_graph(
                targets,
                local_repo,
                local_index,
                ubuntu_index,
                run,
                releases_repo=releases_repo,
                offline=request.offline,
                ubuntu_series=resolved_ubuntu,
                openstack_series=openstack_target,
            )
    else:
        graph, mir_candidates = _build_dependency_graph(
            targets,
            local_repo,
            local_index,
            ubuntu_index,
            run,
            releases_repo=releases_repo,
            offline=request.offline,
            ubuntu_series=resolved_ubuntu,
            openstack_series=openstack_target,
        )

    run.log_event({
        "event": "plan.graph_built",
        "nodes": len(graph.nodes),
        "edges": sum(len(e) for e in graph.edges.values()),
    })

    if verbose_output:
        activity("plan", f"Graph: {len(graph.nodes)} packages, {sum(len(e) for e in graph.edges.values())} dependencies")

    # Check for cycles
    cycles = graph.detect_cycles()
    if cycles:
        run.log_event({
            "event": "plan.cycle_edges",
            "edges": graph.get_cycle_edges(),
        })
        for cycle in cycles:
            cycle_str = " -> ".join(cycle)
            activity("plan", f"Cycle detected: {cycle_str}")
        run.log_event({"event": "plan.cycles", "cycles": [[str(n) for n in c] for c in cycles]})

        if not request.force:
            return PlanResult([], [], mir_candidates, {}, cycles), EXIT_CYCLE_DETECTED

    # Find missing packages
    known_packages = set(ubuntu_index.packages.keys()) | set(graph.nodes.keys())
    missing = graph.find_missing_dependencies(known_packages)

    # Get build and upload order
    try:
        build_order = graph.get_rebuild_order()
    except ValueError as e:
        activity("plan", f"Error computing build order: {e}")
        return PlanResult([], [], mir_candidates, missing, cycles), EXIT_CYCLE_DETECTED

    upload_order = build_order

    # Report MIR warnings
    if mir_candidates:
        if verbose_output:
            with activity_spinner("verify", "Checking MIR status"):
                for pkg, deps in mir_candidates.items():
                    for dep in deps:
                        activity("verify", f"MIR warning: {pkg} depends on {dep}")
                        run.log_event({"event": "verify.mir_warning", "package": pkg, "dependency": dep})
            activity("verify", f"MIR candidates: {sum(len(d) for d in mir_candidates.values())} dependencies")
        else:
            for pkg, deps in mir_candidates.items():
                for dep in deps:
                    activity("verify", f"MIR warning: {pkg} depends on {dep}")
    elif verbose_output:
        activity("verify", "No MIR issues detected")

    # Report missing packages
    if missing:
        for pkg, deps in missing.items():
            for dep in deps:
                activity("verify", f"Missing: {pkg} requires {dep}")
                if verbose_output:
                    activity("verify", f"  Suggested: packastack new {dep}")
        run.log_event({"event": "verify.missing", "missing": missing})

    # Build result
    plan_result = PlanResult(
        build_order=build_order,
        upload_order=upload_order,
        mir_candidates=mir_candidates,
        missing_packages=missing,
        cycles=cycles,
    )
    run.log_event({"event": "report.plan_result", "result": str(plan_result)})

    # Determine exit code
    if missing and not request.force:
        exit_code = EXIT_MISSING_PACKAGES
    elif cycles and not request.force:
        exit_code = EXIT_CYCLE_DETECTED
    else:
        exit_code = EXIT_SUCCESS

    return plan_result, exit_code


@dataclass
class ResolvedTarget:
    source_package: str
    upstream_project: str
    resolution_source: str


def _resolve_package_targets(
    common_name: str,
    local_repo: Path,
    releases_repo: Path,
    registry: UpstreamsRegistry | None,
    openstack_target: str,
    use_local: bool,
    run: RunContextType,
    allow_prefix: bool = True,
) -> list[ResolvedTarget]:
    """Resolve a common name to source package targets with registry support.

    Resolution order (deduplicated):
      1) Local apt repo (exact + prefix)
      2) openstack/releases (exact + prefix)
      3) upstreams registry (explicit entries, exact + prefix)
    """

    results: list[ResolvedTarget] = []
    seen: set[str] = set()

    def _add(pkg: str, upstream: str, source: str) -> None:
        if pkg in seen:
            return
        seen.add(pkg)
        results.append(ResolvedTarget(pkg, upstream, source))
        run.log_event({
            "event": "resolve.match",
            "package": pkg,
            "upstream": upstream,
            "source": source,
        })

    # 1) Local apt repo
    if use_local and local_repo.exists():
        for pkg_dir in sorted(local_repo.iterdir()):
            if not pkg_dir.is_dir():
                continue
            name = pkg_dir.name
            is_name_match = (
                name == common_name
                or name.startswith(f"{common_name}-")
                or name.startswith(f"python3-{common_name}")
            )
            if is_name_match and (pkg_dir / "debian" / "control").exists():
                _add(name, upstream=common_name, source="local")

    # 2) openstack/releases exact + prefix
    proj = load_project_releases(releases_repo, openstack_target, common_name)
    if proj:
        pkg_name = project_to_package_name(common_name, local_repo)
        _add(pkg_name, upstream=common_name, source="releases_exact")

    if allow_prefix:
        prefix_matches = find_projects_by_prefix(releases_repo, openstack_target, common_name)
        for m in prefix_matches:
            pkg_name = project_to_package_name(m, local_repo)
            _add(pkg_name, upstream=m, source="releases_prefix")

    # 3) Registry explicit entries (exact + prefix)
    if registry:
        for proj_key in registry.find_projects(common_name, allow_prefix=allow_prefix):
            try:
                resolved = registry.resolve(proj_key, openstack_governed=False)
            except ProjectNotFoundError:
                continue

            pkg_name = resolved.config.ubuntu.source_hint or proj_key
            _add(
                pkg_name,
                upstream=proj_key,
                source=f"registry:{resolved.resolution_source.value}",
            )

    return results


def _enforce_retirement(
    targets: list[ResolvedTarget],
    include_retired: bool,
    registry: UpstreamsRegistry | None,
    project_config_path: Path | None,
    releases_repo: Path,
    openstack_target: str,
    offline: bool,
    run: RunContextType,
) -> int | None:
    """Apply retirement policy for resolved targets.

    Returns an exit code when retirement blocks the operation, otherwise None.
    """

    if include_retired:
        return None

    # Registry-based retirement flags
    if registry:
        for target in targets:
            if registry.is_retired(target.upstream_project):
                activity(
                    "policy",
                    f"Package {target.source_package} is RETIRED (registry: {target.upstream_project})",
                )
                run.log_event({
                    "event": "policy.retired_registry",
                    "package": target.source_package,
                    "upstream": target.upstream_project,
                })
                return EXIT_CONFIG_ERROR

    # OpenStack project-config based retirement
    if project_config_path:
        if not project_config_path.exists() and not offline:
            with activity_spinner("retire", "Cloning openstack/project-config repository"):
                _clone_or_update_project_config(project_config_path, run)

        if project_config_path.exists():
            retirement_checker = RetirementChecker(
                project_config_path=project_config_path,
                releases_path=releases_repo,
                target_series=openstack_target,
            )

            for target in targets:
                deliverable = target.upstream_project
                if target.source_package.startswith("python-"):
                    deliverable = target.source_package[7:]

                retirement_info = retirement_checker.check_retirement(target.source_package, deliverable)
                if retirement_info.status == RetirementStatus.RETIRED:
                    activity(
                        "policy",
                        f"Package {target.source_package} is RETIRED upstream; skipping",
                    )
                    if retirement_info.description:
                        activity("policy", f"  Reason: {retirement_info.description}")
                    run.log_event({
                        "event": "policy.retired_project",
                        "package": target.source_package,
                        "upstream_project": retirement_info.upstream_project,
                        "source": retirement_info.source,
                        "description": retirement_info.description,
                    })
                    return EXIT_CONFIG_ERROR
                if retirement_info.status == RetirementStatus.POSSIBLY_RETIRED:
                    run.log_event({
                        "event": "policy.possibly_retired",
                        "package": target.source_package,
                        "source": retirement_info.source,
                    })

    return None


def _check_mir_candidates(
    dep_name: str,
    ubuntu_index: PackageIndex,
    run: RunContextType,
) -> str | None:
    """Check if a dependency is in a non-main component.

    Returns:
        Component name if not main, None otherwise.
    """
    component = ubuntu_index.get_component(dep_name)
    if component and component != "main":
        run.log_event({"event": "mir.candidate", "package": dep_name, "component": component})
        return component
    return None


def _build_dependency_graph(
    targets: list[str],
    local_repo: Path,
    local_index: PackageIndex | None,
    ubuntu_index: PackageIndex,
    run: RunContextType,
    releases_repo: Path | None = None,
    offline: bool = False,
    ubuntu_series: str = "",
    openstack_series: str = "",
    exclude_packages: set[str] | None = None,
) -> tuple[DependencyGraph, dict[str, list[str]]]:
    """Build dependency graph from target packages using Ubuntu package index.

    This builds the dependency graph directly from Packages.gz data,
    without needing to clone git repos or read debian/control files.

    Args:
        targets: List of source package names to process.
        local_repo: Path to local apt repo with package sources (used for rebuild checks).
        local_index: Optional index of locally built packages.
        ubuntu_index: Index of Ubuntu archive packages (from Packages.gz).
        run: RunContext for logging.
        releases_repo: Path to openstack/releases repository.
        offline: If True, skip network operations (unused in index-based approach).
        ubuntu_series: Ubuntu series codename (unused in index-based approach).
        openstack_series: OpenStack series codename for determining OpenStack packages.

    Returns:
        Tuple of (DependencyGraph, mir_candidates dict).
    """
    openstack_sources: set[str] = set()
    if releases_repo and openstack_series:
        openstack_sources = set(load_openstack_packages(releases_repo, openstack_series).keys())

    local_sources: set[str] = set()
    if local_repo.exists():
        for pkg_dir in local_repo.iterdir():
            if not pkg_dir.is_dir():
                continue
            if (pkg_dir / "debian" / "control").exists():
                local_sources.add(pkg_dir.name)

    openstack_set = openstack_sources | local_sources
    if exclude_packages:
        openstack_set -= exclude_packages

    result = build_graph_from_index(
        packages=targets,
        package_index=ubuntu_index,
        openstack_packages=openstack_set or None,
        skip_optional_deps=False,
    )

    for source, dep in result.excluded_edges:
        run.log_event({
            "event": "graph.soft_dep_excluded",
            "source": source,
            "dep": dep,
        })

    return result.graph, result.mir_candidates


def _write_plan_dependency_summary(
    graph: DependencyGraph,
    ubuntu_index: PackageIndex,
    cache_root: Path,
    pockets: list[str],
    components: list[str],
    reports_dir: Path,
    run: RunContextType,
) -> dict[str, Path] | None:
    """Generate dependency satisfaction summary for plan reports."""

    prev_lts = get_previous_lts()
    prev_codename = prev_lts.codename if prev_lts else ""
    prev_index = None
    if prev_codename:
        prev_index = load_package_index(cache_root, prev_codename, pockets, components)

    packages: list[dict[str, object]] = []
    totals = {
        "total": 0,
        "cloud_archive_required": 0,
        "mir_warnings": 0,
    }

    for node in sorted(graph.nodes):
        deps = [ParsedDependency(name=d) for d in graph.edges.get(node, set())]
        results, summary = evaluate_dependencies(deps, ubuntu_index, prev_index, kind="runtime")
        packages.append({
            "package": node,
            "dependencies": summary.total,
            "dev_satisfied": summary.dev_satisfied,
            "prev_lts_satisfied": summary.prev_lts_satisfied,
            "cloud_archive_required": summary.cloud_archive_required,
            "mir_warnings": summary.mir_warnings,
        })

        totals["total"] += summary.total
        totals["cloud_archive_required"] += summary.cloud_archive_required
        totals["mir_warnings"] += summary.mir_warnings

    summary_payload = {
        "previous_lts": prev_codename,
        "totals": totals,
        "packages": packages,
    }

    try:
        return write_plan_dependency_summary(summary_payload, reports_dir)
    except Exception as exc:  # pragma: no cover - report generation failures are non-fatal
        activity("warn", f"Could not write dependency summary: {exc}")
        run.log_event({"event": "report.plan_dependency_summary_failed", "error": str(exc)})
        return None


def _format_graph(graph: DependencyGraph) -> list[str]:
    """Return a human-readable adjacency list for the graph."""

    lines: list[str] = ["Dependency graph:"]
    for node in sorted(graph.nodes):
        deps = sorted(graph.edges.get(node, set()))
        if deps:
            lines.append(f"{node}: {', '.join(deps)}")
        else:
            lines.append(f"{node}: (no deps)")
    return lines


def _build_upstream_version_map(report: TypeSelectionReport) -> dict[str, str]:
    """Build a source package -> upstream version map from type selection."""
    versions: dict[str, str] = {}
    for result in report.packages:
        version = ""
        if result.upstream_resolution and result.upstream_resolution.upstream_version:
            version = result.upstream_resolution.upstream_version
        elif result.watch_info and result.watch_info.upstream_version:
            version = result.watch_info.upstream_version
        elif result.latest_version:
            version = result.latest_version
        if version:
            versions[result.source_package] = version
    return versions


def _source_package_to_deliverable(source_package: str) -> str:
    """Convert a source package name to an OpenStack deliverable name.

    For libraries, the source package has a 'python-' prefix that needs to
    be stripped to get the upstream deliverable name.

    Examples:
        python-oslo.log -> oslo.log
        python-keystoneclient -> keystoneclient
        nova -> nova
        keystone -> keystone

    Args:
        source_package: Ubuntu source package name.

    Returns:
        OpenStack deliverable name.
    """
    if source_package.startswith("python-"):
        return source_package.removeprefix("python-")
    return source_package


def _plan_all_packages(
    run: "RunContextType",
    paths: dict[str, Path],
    releases_repo: Path,
    cache_dir: Path,
    openstack_target: str,
    resolved_ubuntu: str,
    type_mode: str,
    workers: int,
    table: bool,
    explain_types: bool,
    offline: bool,
    force: bool,
    include_retired: bool = False,
    print_build_order: bool = True,
    build_order_format: str = "waves",
    build_order_focus: str = "",
    print_graph: bool = False,
    graph_format: str = "ascii",
    graph_max_nodes: int = 200,
    graph_focus: str = "",
    graph_depth: int = 2,
    watch_fallback: bool = True,
    watch_check_upstream: bool = True,
    watch_timeout: int = 30,
    watch_max_projects: int = 0,
) -> int:
    """Plan all packages with type selection.

    Discovers all ubuntu-openstack-dev packages, performs type selection
    based on openstack/releases data, generates reports, and outputs
    summary to console.

    Args:
        include_retired: If True, include retired upstream projects in the plan.
            By default (False), retired projects are excluded.

    Returns:
        Exit code (0 for success)
    """
    # Phase: discover
    # When online, use Launchpad API; when offline, use local cache
    # Cache Launchpad results in the run directory for reuse
    launchpad_cache = run.run_path / "launchpad-repos.json" if not offline else None

    with activity_spinner("discover", "Discovering ubuntu-openstack-dev packages"):
        discovery_result = discover_packages(
            cache_dir=cache_dir if offline else None,
            offline=offline,
            releases_repo=releases_repo,
            launchpad_cache_file=launchpad_cache,
        )
        package_names = sorted(discovery_result.packages)
        run.log_event({
            "event": "discover.packages",
            "count": len(package_names),
            "source": discovery_result.source,
            "errors": discovery_result.errors,
            "missing_upstream": discovery_result.missing_upstream,
            "missing_packaging": discovery_result.missing_packaging,
        })

    activity("discover", f"Found {len(package_names)} packages (source: {discovery_result.source})")

    if discovery_result.errors:
        for err in discovery_result.errors[:3]:
            activity("warn", f"  {err}")

    if not package_names:
        activity("error", "No packages discovered")
        run.write_summary(
            status="failed",
            error="No packages discovered",
            exit_code=EXIT_CONFIG_ERROR,
        )
        return EXIT_CONFIG_ERROR

    # Convert package names to (source_package, deliverable) tuples
    # For libraries, strip python- prefix to get the deliverable name
    packages_tuples = [
        (pkg, _source_package_to_deliverable(pkg)) for pkg in package_names
    ]

    # Build watch config (disabled in offline mode)
    watch_config = WatchConfig(
        enabled=not offline,
        fallback_for_not_in_releases=watch_fallback,
        check_upstream=watch_check_upstream and not offline,
        timeout_seconds=watch_timeout,
        max_projects=watch_max_projects,
    )

    # Build packaging repos mapping (for uscan to access debian/watch)
    # Use build_root as the fetch/cache location to match build phase layout.
    packaging_cache = paths.get("build_root", paths["cache_root"] / "build") / "packaging-cache"
    packaging_repos = _fetch_packaging_repos(
        packages=package_names,
        dest_dir=packaging_cache,
        ubuntu_series=resolved_ubuntu,
        openstack_series=openstack_target,
        offline=offline,
        workers=workers,
    )

    # Uscan cache path
    uscan_cache_path = run.run_path / "uscan-cache.json" if not offline else None

    # Create retirement checker if we need to filter retired packages
    retirement_checker: RetirementChecker | None = None
    if not include_retired:
        project_config_path = paths.get("openstack_project_config")
        
        # Clone project-config if missing and not in offline mode
        if project_config_path and not project_config_path.exists() and not offline:
            with activity_spinner("retire", "Cloning openstack/project-config repository"):
                _clone_or_update_project_config(project_config_path, run)
        
        if project_config_path and project_config_path.exists():
            with activity_spinner("retire", "Loading retirement status data"):
                retirement_checker = RetirementChecker(
                    project_config_path=project_config_path,
                    releases_path=releases_repo,
                    target_series=openstack_target,
                )
        elif offline:
            activity("warn", "Project config not found, skipping retirement detection (offline mode)")
        else:
            activity("warn", "Project config clone failed, skipping retirement detection")

    # Phase: type selection
    if package_names:
        from rich.progress import Progress, BarColumn, TextColumn, TaskProgressColumn, TimeRemainingColumn
        from rich.console import Console

        console = Console(file=sys.__stdout__, force_terminal=True)
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeRemainingColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(
                f"Analyzing type selection for {len(package_names)} packages",
                total=len(package_names),
            )

            def _advance(count: int = 1) -> None:
                progress.advance(task, count)

            report = select_build_types_for_packages(
                releases_repo=releases_repo,
                series=openstack_target,
                packages=packages_tuples,
                run_id=run.run_id,
                ubuntu_series=resolved_ubuntu,
                type_mode=type_mode,
                parallel=workers,
                local_packages=set(package_names),
                watch_config=watch_config,
                packaging_repos=packaging_repos,
                uscan_cache_path=uscan_cache_path,
                retirement_checker=retirement_checker,
                progress_callback=_advance,
            )
    else:
        report = select_build_types_for_packages(
            releases_repo=releases_repo,
            series=openstack_target,
            packages=packages_tuples,
            run_id=run.run_id,
            ubuntu_series=resolved_ubuntu,
            type_mode=type_mode,
            parallel=workers,
            local_packages=set(package_names),
            watch_config=watch_config,
            packaging_repos=packaging_repos,
            uscan_cache_path=uscan_cache_path,
            retirement_checker=retirement_checker,
        )

    # Copy cross-reference warnings from discovery to report
    report.missing_upstream = discovery_result.missing_upstream
    report.missing_packaging = discovery_result.missing_packaging
    run.log_event({
        "event": "type_selection.complete",
        "total": report.total_count,
        "by_type": report.counts_by_type,
    })

    activity("analyze", f"Type selection complete: {report.total_count} packages analyzed")

    # Report retired projects summary
    if report.count_retired > 0:
        activity("plan", f"Retired projects: {report.count_retired} (excluded from graph)")
        run.log_event({
            "event": "plan.retired_projects",
            "count": report.count_retired,
            "packages": list(report.retired_packages),
        })
    if report.possibly_retired_packages:
        activity("plan", f"Possibly retired projects: {len(report.possibly_retired_packages)} (excluded from graph)")
        run.log_event({
            "event": "plan.possibly_retired_projects",
            "count": len(report.possibly_retired_packages),
            "packages": list(report.possibly_retired_packages),
        })

    # Report packages needing upstreams.yaml mapping
    if report.needs_upstream_mapping:
        activity("plan", f"Packages needing upstreams.yaml mapping: {len(report.needs_upstream_mapping)}")
        for pkg in sorted(report.needs_upstream_mapping)[:5]:
            activity("plan", f"  - {pkg}")
        if len(report.needs_upstream_mapping) > 5:
            activity("plan", f"  ... and {len(report.needs_upstream_mapping) - 5} more")
        run.log_event({
            "event": "plan.needs_upstream_mapping",
            "count": len(report.needs_upstream_mapping),
            "packages": list(report.needs_upstream_mapping),
        })

    graph_package_names = package_names
    excluded_retired: set[str] = set()
    if not include_retired:
        excluded_retired = set(report.retired_packages) | set(report.possibly_retired_packages)
        if excluded_retired:
            graph_package_names = [pkg for pkg in package_names if pkg not in excluded_retired]
            run.log_event({
                "event": "plan.graph_excluded_retired",
                "count": len(excluded_retired),
                "packages": sorted(excluded_retired),
            })
            activity("plan", f"Excluded {len(excluded_retired)} retired packages from graph")

    # Phase: reports
    reports_dir = run.run_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    with activity_spinner("report", "Generating type selection reports"):
        report_paths = write_type_selection_reports(report, reports_dir)
        run.log_event({
            "event": "reports.written",
            "json_path": str(report_paths["json"]),
            "html_path": str(report_paths["html"]),
        })

    activity("report", f"Reports written to {reports_dir}")

    # Generate watch resolution report if watch info is available
    if any(r.watch_info is not None for r in report.packages):
        with activity_spinner("report", "Generating watch resolution reports"):
            watch_paths = write_watch_resolution_reports(
                type_report=report,
                reports_dir=reports_dir,
            )
            run.log_event({
                "event": "reports.watch_resolution_written",
                "json_path": str(watch_paths["json"]),
                "html_path": str(watch_paths["html"]),
            })
        activity("report", f"Watch resolution report written to {reports_dir}")

    # Phase: graph reports (always generated)
    # Load the ubuntu package index and build a real dependency graph
    cfg = load_config()
    pockets = cfg.get("defaults", {}).get("ubuntu_pockets", ["release", "updates", "security"])
    components = cfg.get("defaults", {}).get("ubuntu_components", ["main", "universe"])
    ubuntu_cache = paths["ubuntu_archive_cache"]
    local_repo = paths["local_apt_repo"]

    with activity_spinner("plan", "Loading Ubuntu package index"):
        ubuntu_index = load_package_index(ubuntu_cache, resolved_ubuntu, pockets, components)
        run.log_event({
            "event": "plan.index_loaded",
            "packages": len(ubuntu_index.packages),
            "sources": len(ubuntu_index.sources),
        })

    activity("plan", f"Loaded {len(ubuntu_index.packages)} packages from Ubuntu index")

    with activity_spinner("plan", "Building dependency graph"):
        dep_graph, mir_candidates = _build_dependency_graph(
            targets=graph_package_names,
            local_repo=local_repo,
            local_index=None,
            ubuntu_index=ubuntu_index,
            run=run,
            releases_repo=releases_repo,
            offline=offline,
            ubuntu_series=resolved_ubuntu,
            openstack_series=openstack_target,
            exclude_packages=excluded_retired,
        )
        cycles = dep_graph.detect_cycles()
        run.log_event({
            "event": "plan.graph_built",
            "nodes": len(dep_graph.nodes),
            "edges": sum(len(e) for e in dep_graph.edges.values()),
            "cycles": len(cycles),
        })

    activity("plan", f"Graph: {len(dep_graph.nodes)} packages, {sum(len(e) for e in dep_graph.edges.values())} dependencies")
    if cycles:
        cycle_edges = dep_graph.get_cycle_edges()
        run.log_event({
            "event": "plan.cycle_edges",
            "edges": cycle_edges,
        })
        activity("warn", f"Detected {len(cycles)} dependency cycle(s)")

        suggestions = suggest_cycle_edge_exclusions(
            edges=cycle_edges,
            packaging_repos=packaging_repos,
            upstream_versions=_build_upstream_version_map(report),
            source_to_project=load_openstack_packages(releases_repo, openstack_target),
            package_index=ubuntu_index,
            upstream_cache_base=paths.get("upstream_tarballs"),
        )
        if suggestions:
            run.log_event({
                "event": "plan.cycle_exclusion_suggestions",
                "suggestions": [suggestion.to_dict() for suggestion in suggestions],
            })
            activity(
                "warn",
                f"Suggested {len(suggestions)} edge exclusion(s) based on upstream requirements",
            )
            for suggestion in suggestions[:5]:
                activity(
                    "warn",
                    f"  Suggest exclude {suggestion.source} -> {suggestion.dependency} ({suggestion.requirements_source})",
                )
            if len(suggestions) > 5:
                activity("warn", f"  ... and {len(suggestions) - 5} more")

    with activity_spinner("report", "Generating plan graph reports"):
        # Create PlanGraph from the dependency graph
        plan_graph = PlanGraph.from_dependency_graph(
            dep_graph=dep_graph,
            run_id=run.run_id,
            target=openstack_target,
            ubuntu_series=resolved_ubuntu,
            type_report=report,
            cycles=cycles,
        )

        graph_paths = write_plan_graph_reports(plan_graph, reports_dir)
        run.log_event({
            "event": "graph_reports.written",
            "json_path": str(graph_paths["json"]),
            "html_path": str(graph_paths["html"]),
        })

    dep_summary_paths = _write_plan_dependency_summary(
        graph=dep_graph,
        ubuntu_index=ubuntu_index,
        cache_root=ubuntu_cache,
        pockets=pockets,
        components=components,
        reports_dir=reports_dir,
        run=run,
    )

    # Print build order to console if requested
    # Support both legacy --print-graph and new --print-build-order flags
    show_build_order = print_build_order or print_graph
    focus = build_order_focus or graph_focus  # Prefer new flag, fall back to legacy
    format_choice = build_order_format if not print_graph else graph_format  # Use legacy if --print-graph

    if show_build_order:
        # Determine format
        if format_choice == "waves":
            waves_output = render_waves(plan_graph, focus=focus or None)
            print(f"\n{waves_output}", file=sys.__stdout__, flush=True)
        elif format_choice == "list":
            list_output = render_build_order_list(plan_graph, focus=focus or None)
            print(f"\n{list_output}", file=sys.__stdout__, flush=True)
        elif format_choice == "dot":
            dot_output = render_dot(
                plan_graph,
                focus=focus or None,
                depth=graph_depth,
                max_nodes=graph_max_nodes,
            )
            print(dot_output, file=sys.__stdout__, flush=True)
        elif format_choice == "ascii":
            # Legacy ascii format
            ascii_output = render_ascii(
                plan_graph,
                focus=focus or None,
                depth=graph_depth,
                max_nodes=graph_max_nodes,
            )
            print(f"\n{ascii_output}", file=sys.__stdout__, flush=True)
        else:
            # Default to waves
            waves_output = render_waves(plan_graph, focus=focus or None)
            print(f"\n{waves_output}", file=sys.__stdout__, flush=True)

    # Console output (use sys.__stdout__ to bypass RunContext redirection)
    if table:
        table_output = render_console_table(report, explain=explain_types)
        print("\n" + table_output, file=sys.__stdout__, flush=True)
    else:
        summary_output = render_compact_summary(report)
        print("\n" + summary_output, file=sys.__stdout__, flush=True)

    # Summary
    run.write_summary(
        status="success",
        exit_code=EXIT_SUCCESS,
        packages_analyzed=report.total_count,
        type_mode=type_mode,
        openstack_target=openstack_target,
        ubuntu_series=resolved_ubuntu,
        counts_by_type=report.counts_by_type,
        counts_by_reason=report.counts_by_reason,
        reports={
            "json": str(report_paths["json"]),
            "html": str(report_paths["html"]),
            "plan_graph_json": str(graph_paths["json"]),
            "plan_graph_html": str(graph_paths["html"]),
            **({"dependency_summary_json": str(dep_summary_paths["json"]), "dependency_summary_html": str(dep_summary_paths["html"])} if dep_summary_paths else {}),
        },
    )

    return EXIT_SUCCESS


def plan(
    package: str = typer.Argument("", help="Package name or prefix to plan (omit for --all)"),
    ubuntu_series: str = typer.Option("devel", "-u", "--ubuntu-series", help="Ubuntu series target"),
    target: str = typer.Option("devel", "-t", "--target", help="OpenStack series target"),
    plan_only: bool = typer.Option(False, "-p", "--plan", help="Show plan without executing"),
    plan_upload: bool = typer.Option(False, "-P", "--plan-upload", help="Show plan with upload order"),
    upload: bool = typer.Option(False, "-U", "--upload", help="Mark packages for upload"),
    force: bool = typer.Option(False, "-f", "--force", help="Proceed despite warnings"),
    offline: bool = typer.Option(False, "-o", "--offline", help="Run in offline mode"),
    skip_local: bool = typer.Option(False, "-s", "--skip-local", help="Skip local apt repo search"),
    pretty: bool = typer.Option(False, "-r", "--pretty", help="Print the dependency graph"),
    all_packages: bool = typer.Option(False, "-a", "--all", help="Plan all discovered packages"),
    type_mode: str = typer.Option("auto", "--type", help="Build type: auto|release|milestone|snapshot"),
    table: bool = typer.Option(False, "--table", help="Print full type selection table"),
    explain_types: bool = typer.Option(False, "--explain-types", help="Explain type selection reasoning"),
    parallel: int = typer.Option(0, "-j", "--parallel", help="Parallel workers (0=auto)"),
    # Graph options
    print_build_order: bool = typer.Option(True, "--print-build-order/--no-print-build-order", help="Print build order to console"),
    build_order_format: str = typer.Option("waves", "--build-order-format", help="Build order format: waves, list, or dot"),
    build_order_focus: str = typer.Option("", "--build-order-focus", help="Focus build order on specific package"),
    # Legacy graph options (for compatibility)
    print_graph: bool = typer.Option(False, "--print-graph", help="Print build-order graph to console (legacy)"),
    graph_format: str = typer.Option("ascii", "--graph-format", help="Graph format: ascii or dot (legacy)"),
    graph_max_nodes: int = typer.Option(200, "--graph-max-nodes", help="Max nodes to show in console graph"),
    graph_focus: str = typer.Option("", "--graph-focus", help="Focus graph on specific package (legacy)"),
    graph_depth: int = typer.Option(2, "--graph-depth", help="Depth for focused subgraph"),
    # Watch/uscan options
    watch_fallback: bool = typer.Option(True, "--watch-fallback/--no-watch-fallback", help="Use debian/watch for packages not in openstack/releases"),
    watch_check_upstream: bool = typer.Option(True, "--watch-check-upstream/--no-watch-check-upstream", help="Run uscan to discover upstream versions"),
    watch_timeout: int = typer.Option(30, "--watch-timeout-seconds", help="Timeout for uscan execution"),
    watch_max_projects: int = typer.Option(0, "--watch-max-projects", help="Max packages to run uscan for (0=unlimited)"),
    # Retirement options
    include_retired: bool = typer.Option(False, "--include-retired", help="Include retired upstream projects in the plan (default: skip)"),
) -> None:
    """Plan package builds and determine build order.

    Analyzes dependencies, detects MIR candidates, finds missing packages,
    and produces a build order for the specified package(s).

    When --all is specified, discovers all ubuntu-openstack-dev packages
    and performs type selection for each based on openstack/releases data.

    Exit codes:
      0 - Success
      1 - Configuration error
      5 - Missing packages detected
      6 - Dependency cycle detected
    """
    with RunContext("plan") as run:
        cfg = load_config()
        paths = resolve_paths(cfg)

        # Validate inputs
        if not package and not all_packages:
            activity("error", "Either specify a package or use --all")
            sys.exit(EXIT_CONFIG_ERROR)

        if package and all_packages:
            activity("error", "Cannot specify both a package and --all")
            sys.exit(EXIT_CONFIG_ERROR)

        # Validate type mode
        valid_type_modes = ("auto", "release", "milestone", "snapshot")
        if type_mode not in valid_type_modes:
            activity("error", f"Invalid --type: {type_mode}. Must be one of: {', '.join(valid_type_modes)}")
            sys.exit(EXIT_CONFIG_ERROR)

        # Determine parallel workers
        workers = parallel if parallel > 0 else get_default_parallel_workers()

        # Phase: resolve
        with activity_spinner("resolve", f"Resolving build target: {package or 'all'}"):
            resolved_ubuntu = resolve_series(ubuntu_series)
            run.log_event({"event": "series.ubuntu_resolved", "series": resolved_ubuntu})

        activity("resolve", f"Ubuntu series: {resolved_ubuntu}")

        # Resolve OpenStack target
        releases_repo = paths["openstack_releases_repo"]
        if target == "devel":
            openstack_target = get_current_development_series(releases_repo) or target
        else:
            openstack_target = target
        activity("resolve", f"OpenStack target: {openstack_target}")
        run.log_event({"event": "series.openstack_resolved", "target": openstack_target})

        local_repo = paths["local_apt_repo"]
        registry = UpstreamsRegistry()
        # Use build_root for cached packaging repos (same location as build command)
        build_root = paths.get("build_root", Path.home() / ".cache" / "packastack" / "build")

        # Handle --all mode: discover all packages and do type selection
        if all_packages:
            exit_code = _plan_all_packages(
                run=run,
                paths=paths,
                releases_repo=releases_repo,
                cache_dir=build_root,  # Use build_root for cached repos
                openstack_target=openstack_target,
                resolved_ubuntu=resolved_ubuntu,
                type_mode=type_mode,
                workers=workers,
                table=table,
                explain_types=explain_types,
                offline=offline,
                force=force,
                include_retired=include_retired,
                print_build_order=print_build_order,
                build_order_format=build_order_format,
                build_order_focus=build_order_focus,
                print_graph=print_graph,
                graph_format=graph_format,
                graph_max_nodes=graph_max_nodes,
                graph_focus=graph_focus,
                graph_depth=graph_depth,
                watch_fallback=watch_fallback,
                watch_check_upstream=watch_check_upstream,
                watch_timeout=watch_timeout,
                watch_max_projects=watch_max_projects,
            )
            sys.exit(exit_code)

        # Single/prefix package mode
        # Resolve package targets
        with activity_spinner("resolve", f"Finding packages matching: {package}"):
            resolved_targets = _resolve_package_targets(
                package,
                local_repo,
                releases_repo,
                registry,
                openstack_target,
                use_local=not skip_local,
                run=run,
            )

        if not resolved_targets:
            activity("resolve", f"No packages found matching: {package}")
            run.log_event({"event": "resolve.no_matches", "query": package})
            run.write_summary(
                status="failed",
                error=f"No packages found matching: {package}",
                exit_code=EXIT_CONFIG_ERROR,
            )
            sys.exit(EXIT_CONFIG_ERROR)

        target_names = [t.source_package for t in resolved_targets]
        if len(target_names) > 1 and not force:
            activity("resolve", f"Multiple matches found: {', '.join(target_names)}")
            activity("resolve", "Use --force to proceed with all, or specify exact name")
            run.log_event({"event": "resolve.multiple_matches", "matches": target_names})
            run.write_summary(
                status="failed",
                error="Multiple package matches require --force",
                exit_code=EXIT_CONFIG_ERROR,
            )
            sys.exit(EXIT_CONFIG_ERROR)

        for t in resolved_targets:
            activity("resolve", f"Target: {t.source_package} ({t.resolution_source})")
        run.log_event({"event": "resolve.targets", "targets": target_names})

        retirement_exit = _enforce_retirement(
            targets=resolved_targets,
            include_retired=include_retired,
            registry=registry,
            project_config_path=paths.get("openstack_project_config"),
            releases_repo=releases_repo,
            openstack_target=openstack_target,
            offline=offline,
            run=run,
        )
        if retirement_exit:
            run.write_summary(
                status="failed",
                error="Target is retired upstream",
                exit_code=retirement_exit,
            )
            sys.exit(retirement_exit)

        targets = target_names

        # Phase: policy
        with activity_spinner("policy", "Checking snapshot eligibility"):
            policy_issues: list[str] = []
            preferred_versions: dict[str, str] = {}
            for t in resolved_targets:
                if t.resolution_source.startswith("registry"):
                    run.log_event({"event": "policy.skip_snapshot_registry", "package": t.source_package})
                    continue
                eligible, reason, preferred_version = is_snapshot_eligible(
                    releases_repo, openstack_target, t.source_package
                )
                if not eligible:
                    policy_issues.append(f"{t.source_package}: {reason}")
                    run.log_event({"event": "policy.blocked", "package": t.source_package, "reason": reason})
                    if preferred_version:
                        preferred_versions[t.source_package] = preferred_version
                        run.log_event({
                            "event": "policy.preferred_version",
                            "package": t.source_package,
                            "version": preferred_version,
                        })
                elif "Warning" in reason:
                    # Snapshot allowed but with warning
                    activity("policy", f"Warning: {t.source_package}: {reason}")
                    run.log_event({"event": "policy.warning", "package": t.source_package, "reason": reason})

        if policy_issues and not force:
            for issue in policy_issues:
                activity("policy", f"Blocked: {issue}")
            activity("policy", "Use --force to override policy checks")
            run.write_summary(
                status="failed",
                error="Snapshot builds blocked by policy",
                policy_issues=policy_issues,
                exit_code=EXIT_CONFIG_ERROR,
            )
            sys.exit(EXIT_CONFIG_ERROR)
        elif policy_issues:
            for issue in policy_issues:
                activity("policy", f"Warning (forced): {issue}")

        activity("policy", "Snapshot eligibility: OK")

        # Phase: plan - Build dependency graph
        with activity_spinner("plan", "Loading Ubuntu package index"):
            pockets = cfg.get("defaults", {}).get("ubuntu_pockets", ["release", "updates", "security"])
            components = cfg.get("defaults", {}).get("ubuntu_components", ["main", "universe"])
            ubuntu_cache = paths["ubuntu_archive_cache"]
            ubuntu_index = load_package_index(ubuntu_cache, resolved_ubuntu, pockets, components)
            run.log_event({
                "event": "plan.index_loaded",
                "packages": len(ubuntu_index.packages),
                "sources": len(ubuntu_index.sources),
            })

        activity("plan", f"Loaded {len(ubuntu_index.packages)} packages from Ubuntu index")

        # Load local index if available (simplified - just use empty for now)
        local_index: PackageIndex | None = None

        with activity_spinner("plan", "Building dependency graph"):
            graph, mir_candidates = _build_dependency_graph(
                targets,
                local_repo,
                local_index,
                ubuntu_index,
                run,
                releases_repo=releases_repo,
                offline=offline,
                ubuntu_series=resolved_ubuntu,
                openstack_series=openstack_target,
            )
            run.log_event({
                "event": "plan.graph_built",
                "nodes": len(graph.nodes),
                "edges": sum(len(e) for e in graph.edges.values()),
            })

        activity("plan", f"Graph: {len(graph.nodes)} packages, {sum(len(e) for e in graph.edges.values())} dependencies")

        if pretty:
            for line in _format_graph(graph):
                print(line, flush=True)

        # Check for cycles
        cycles = graph.detect_cycles()
        if cycles:
            run.log_event({
                "event": "plan.cycle_edges",
                "edges": graph.get_cycle_edges(),
            })
            for cycle in cycles:
                cycle_str = " -> ".join(cycle)
                activity("plan", f"Cycle detected: {cycle_str}")
            run.log_event({"event": "plan.cycles", "cycles": [[str(n) for n in c] for c in cycles]})

            if not force:
                run.write_summary(
                    status="failed",
                    error="Dependency cycles detected",
                    cycles=[[str(n) for n in c] for c in cycles],
                    exit_code=EXIT_CYCLE_DETECTED,
                )
                sys.exit(EXIT_CYCLE_DETECTED)

        # Find missing packages
        known_packages = set(ubuntu_index.packages.keys()) | set(graph.nodes.keys())
        missing = graph.find_missing_dependencies(known_packages)

        # Get build and upload order
        try:
            build_order = graph.get_rebuild_order()
        except ValueError as e:
            activity("plan", f"Error computing build order: {e}")
            run.write_summary(status="failed", error=str(e), exit_code=EXIT_CYCLE_DETECTED)
            sys.exit(EXIT_CYCLE_DETECTED)

        # Upload order: same as build order for now
        upload_order = build_order if upload or plan_upload else []

        # Phase: verify - Report MIR warnings
        with activity_spinner("verify", "Checking MIR status"):
            for pkg, deps in mir_candidates.items():
                for dep in deps:
                    activity("verify", f"MIR warning: {pkg} depends on {dep}")
                    run.log_event({"event": "verify.mir_warning", "package": pkg, "dependency": dep})

        if mir_candidates:
            activity("verify", f"MIR candidates: {sum(len(d) for d in mir_candidates.values())} dependencies")
        else:
            activity("verify", "No MIR issues detected")

        # Report missing packages
        if missing:
            for pkg, deps in missing.items():
                for dep in deps:
                    activity("verify", f"Missing: {pkg} requires {dep}")
                    activity("verify", f"  Suggested: packastack new {dep}")
            run.log_event({"event": "verify.missing", "missing": missing})

        # Phase: graph reports (always generated)
        reports_dir = run.run_path / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        with activity_spinner("report", "Generating plan graph reports"):
            plan_graph = PlanGraph.from_dependency_graph(
                dep_graph=graph,
                run_id=run.run_id,
                target=openstack_target,
                ubuntu_series=resolved_ubuntu,
                type_report=None,  # No type report in single-package mode
                cycles=cycles,
            )

            graph_paths = write_plan_graph_reports(plan_graph, reports_dir)
            run.log_event({
                "event": "graph_reports.written",
                "json_path": str(graph_paths["json"]),
                "html_path": str(graph_paths["html"]),
            })

        dep_summary_paths = _write_plan_dependency_summary(
            graph=graph,
            ubuntu_index=ubuntu_index,
            cache_root=ubuntu_cache,
            pockets=pockets,
            components=components,
            reports_dir=reports_dir,
            run=run,
        )

        # Print graph to console if requested
        if print_graph:
            if plan_graph.node_count > graph_max_nodes and not graph_focus:
                activity("warn", f"Graph has {plan_graph.node_count} nodes, showing summary only")
                print(f"\nBuild order (first 50 of {plan_graph.node_count}):", file=sys.__stdout__, flush=True)
                for i, pkg_id in enumerate(build_order[:50]):
                    print(f"  {i+1:3d}. {pkg_id}", file=sys.__stdout__, flush=True)
                if len(build_order) > 50:
                    print(f"  ... and {len(build_order) - 50} more", file=sys.__stdout__, flush=True)
            else:
                if graph_format == "dot":
                    dot_output = render_dot(
                        plan_graph,
                        focus=graph_focus or None,
                        depth=graph_depth,
                        max_nodes=graph_max_nodes,
                    )
                    print(dot_output, file=sys.__stdout__, flush=True)
                else:
                    ascii_output = render_ascii(
                        plan_graph,
                        focus=graph_focus or None,
                        depth=graph_depth,
                        max_nodes=graph_max_nodes,
                    )
                    print("\n" + ascii_output, file=sys.__stdout__, flush=True)

        # Phase: report
        activity("report", f"Build order: {len(build_order)} source packages")
        for i, pkg in enumerate(build_order, 1):
            activity("report", f"  {i}. {pkg}")

        if plan_upload or upload:
            activity("report", f"Upload order: {len(upload_order)} source packages")
            for i, pkg in enumerate(upload_order, 1):
                activity("report", f"  {i}. {pkg}")

        # Build result for structured access
        plan_result = PlanResult(
            build_order=build_order,
            upload_order=upload_order,
            mir_candidates=mir_candidates,
            missing_packages=missing,
            cycles=cycles,
        )
        run.log_event({"event": "report.plan_result", "result": str(plan_result)})

        # Determine exit code
        if missing and not force:
            exit_code = EXIT_MISSING_PACKAGES
            status = "failed"
        elif cycles and not force:
            exit_code = EXIT_CYCLE_DETECTED
            status = "failed"
        else:
            exit_code = EXIT_SUCCESS
            status = "success"

        run.write_summary(
            status=status,
            exit_code=exit_code,
            resolved_targets=targets,
            ubuntu_series=resolved_ubuntu,
            openstack_target=openstack_target,
            build_order=build_order,
            upload_order=upload_order,
            mir_candidates=mir_candidates,
            missing_packages=missing,
            reports={
                "plan_graph_json": str(graph_paths["json"]),
                "plan_graph_html": str(graph_paths["html"]),
                **({"dependency_summary_json": str(dep_summary_paths["json"]), "dependency_summary_html": str(dep_summary_paths["html"])} if dep_summary_paths else {}),
            },
        )

    sys.exit(exit_code)
