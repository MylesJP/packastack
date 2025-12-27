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
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from packastack.config import load_config
from packastack.graph import DependencyGraph, PlanResult
from packastack.packages import (
    PackageIndex,
    load_package_index,
)
from packastack.paths import resolve_paths
from packastack.releases import (
    find_projects_by_prefix,
    get_current_development_series,
    is_snapshot_eligible,
    load_openstack_packages,
    load_project_releases,
    project_to_package_name,
)
from packastack.run import RunContext, activity
from packastack.series import resolve_series
from packastack.spinner import activity_spinner

if TYPE_CHECKING:
    from packastack.run import RunContext as RunContextType

# Exit codes per spec
EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR = 1
EXIT_MISSING_PACKAGES = 5
EXIT_CYCLE_DETECTED = 6


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


def _resolve_package_targets(
    common_name: str,
    local_repo: Path,
    releases_repo: Path,
    openstack_target: str,
    use_local: bool,
    run: RunContextType,
) -> list[str]:
    """Resolve a common name to a list of source package names.

    Args:
        common_name: Package name or prefix (e.g., "nova", "oslo").
        local_repo: Path to local apt repo with package sources.
        releases_repo: Path to openstack/releases repository.
        openstack_target: OpenStack series target.
        use_local: Whether to search local repo first.
        run: RunContext for logging.

    Returns:
        List of resolved source package names.
    """
    matches: list[str] = []

    # First, check local apt repo for exact match or prefix
    if use_local and local_repo.exists():
        for pkg_dir in local_repo.iterdir():
            if not pkg_dir.is_dir():
                continue
            name = pkg_dir.name
            is_name_match = (
                name == common_name
                or name.startswith(f"{common_name}-")
                or name.startswith(f"python3-{common_name}")
            )
            if is_name_match and (pkg_dir / "debian" / "control").exists():
                matches.append(name)
                run.log_event({"event": "resolve.local_match", "name": name})

    # If no local matches, check openstack/releases
    if not matches:
        # Try exact match
        proj = load_project_releases(releases_repo, openstack_target, common_name)
        if proj:
            # Map OpenStack project name to Ubuntu package name
            pkg_name = project_to_package_name(common_name, local_repo)
            matches.append(pkg_name)
            run.log_event({
                "event": "resolve.releases_match",
                "project": common_name,
                "package": pkg_name,
            })
        else:
            # Try as prefix
            prefix_matches = find_projects_by_prefix(releases_repo, openstack_target, common_name)
            for m in prefix_matches:
                pkg_name = project_to_package_name(m, local_repo)
                matches.append(pkg_name)
                run.log_event({
                    "event": "resolve.prefix_match",
                    "prefix": common_name,
                    "project": m,
                    "package": pkg_name,
                })

    return matches


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


# Known soft/optional dependencies that should be excluded from the dependency graph.
# These are cases where packages have runtime-optional dependencies that would create
# cycles in the build order. The key is the source package name, and the value is a
# set of source package names that should NOT be treated as dependencies.
#
# Example: oslo.config optionally imports oslo.log at runtime, but oslo.log depends
# on oslo.config. The Python code handles this with a try/except import, but the
# Debian package still lists it as a dependency.
SOFT_DEPENDENCY_EXCLUSIONS: dict[str, set[str]] = {
    "python-oslo.config": {"python-oslo.log"},
}


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
    graph = DependencyGraph()
    mir_candidates: dict[str, list[str]] = {}
    processed: set[str] = set()
    to_process = list(targets)

    # Load mapping of Ubuntu source packages to OpenStack projects
    openstack_packages: dict[str, str] = {}
    if releases_repo and openstack_series:
        openstack_packages = load_openstack_packages(releases_repo, openstack_series)

    while to_process:
        source_name = to_process.pop(0)
        if source_name in processed:
            continue
        processed.add(source_name)

        # Get all binary packages for this source from Ubuntu index
        binary_names = ubuntu_index.get_binaries_for_source(source_name)
        if not binary_names:
            # Source package not found in Ubuntu index
            run.log_event({"event": "graph.source_not_found", "source": source_name})
            continue

        # Add node for this source package
        # Mark as needing rebuild if it's an OpenStack package
        # (either in local repo or found via releases repo)
        needs_rebuild = (
            source_name in openstack_packages
            or (local_repo / source_name / "debian" / "control").exists()
        )
        graph.add_node(source_name, needs_rebuild=needs_rebuild, version="")

        # Process runtime dependencies from all binary packages
        pkg_mir: list[str] = []
        for binary_name in binary_names:
            binary_pkg = ubuntu_index.find_package(binary_name)
            if not binary_pkg:
                continue

            # Process each dependency
            for dep_str in binary_pkg.depends + binary_pkg.pre_depends:
                # Parse dependency name (strip version constraints and alternatives)
                dep_name = dep_str.split()[0].split("(")[0].split("|")[0].strip()
                if not dep_name:
                    continue

                # Check MIR status
                mir_comp = _check_mir_candidates(dep_name, ubuntu_index, run)
                if mir_comp:
                    pkg_mir.append(f"{dep_name} ({mir_comp})")

                # Find the source package that provides this dependency
                dep_pkg = ubuntu_index.find_package(dep_name)
                if dep_pkg and dep_pkg.source and dep_pkg.source != source_name:
                    dep_source = dep_pkg.source

                    # Check if this is a known soft/optional dependency to exclude
                    excluded_deps = SOFT_DEPENDENCY_EXCLUSIONS.get(source_name, set())
                    if dep_source in excluded_deps:
                        run.log_event({
                            "event": "graph.soft_dep_excluded",
                            "source": source_name,
                            "dep": dep_source,
                        })
                        continue

                    # Only add edge if dependency is also an OpenStack package
                    # Check releases repo first, fall back to local repo for packages
                    # that may not follow standard naming (e.g., gnocchi)
                    is_openstack = (
                        dep_source in openstack_packages
                        or (local_repo / dep_source / "debian" / "control").exists()
                    )
                    if is_openstack:
                        graph.add_edge(source_name, dep_source)
                        if dep_source not in processed:
                            to_process.append(dep_source)

        if pkg_mir:
            mir_candidates[source_name] = pkg_mir

    return graph, mir_candidates


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


def plan(
    package: str = typer.Argument(..., help="Package name or prefix to plan"),
    ubuntu_series: str = typer.Option("devel", help="Ubuntu series target"),
    target: str = typer.Option("devel", "--target", help="OpenStack series target"),
    plan_only: bool = typer.Option(False, "--plan", help="Show plan without executing"),
    plan_upload: bool = typer.Option(False, "--plan-upload", help="Show plan with upload order"),
    upload: bool = typer.Option(False, "--upload", help="Mark packages for upload"),
    force: bool = typer.Option(False, "--force", help="Proceed despite warnings"),
    offline: bool = typer.Option(False, "--offline", help="Run in offline mode"),
    skip_local: bool = typer.Option(False, "--skip-local", help="Skip local apt repo search"),
    pretty: bool = typer.Option(False, "--pretty", help="Print the dependency graph"),
) -> None:
    """Plan package builds and determine build order.

    Analyzes dependencies, detects MIR candidates, finds missing packages,
    and produces a build order for the specified package(s).

    Exit codes:
      0 - Success
      1 - Configuration error
      5 - Missing packages detected
      6 - Dependency cycle detected
    """
    with RunContext("plan") as run:
        cfg = load_config()
        paths = resolve_paths(cfg)

        # Phase: resolve
        with activity_spinner("resolve", f"Resolving build target: {package}"):
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

        # Resolve package targets
        local_repo = paths["local_apt_repo"]
        with activity_spinner("resolve", f"Finding packages matching: {package}"):
            targets = _resolve_package_targets(
                package,
                local_repo,
                releases_repo,
                openstack_target,
                use_local=not skip_local,
                run=run,
            )

        if not targets:
            activity("resolve", f"No packages found matching: {package}")
            run.log_event({"event": "resolve.no_matches", "query": package})
            run.write_summary(
                status="failed",
                error=f"No packages found matching: {package}",
                exit_code=EXIT_CONFIG_ERROR,
            )
            sys.exit(EXIT_CONFIG_ERROR)

        if len(targets) > 1 and not force:
            activity("resolve", f"Multiple matches found: {', '.join(targets)}")
            activity("resolve", "Use --force to proceed with all, or specify exact name")
            run.log_event({"event": "resolve.multiple_matches", "matches": targets})
            run.write_summary(
                status="failed",
                error="Multiple package matches require --force",
                exit_code=EXIT_CONFIG_ERROR,
            )
            sys.exit(EXIT_CONFIG_ERROR)

        for t in targets:
            activity("resolve", f"Target: {t}")
        run.log_event({"event": "resolve.targets", "targets": targets})

        # Phase: policy
        with activity_spinner("policy", "Checking snapshot eligibility"):
            policy_issues: list[str] = []
            preferred_versions: dict[str, str] = {}
            for t in targets:
                eligible, reason, preferred_version = is_snapshot_eligible(
                    releases_repo, openstack_target, t
                )
                if not eligible:
                    policy_issues.append(f"{t}: {reason}")
                    run.log_event({"event": "policy.blocked", "package": t, "reason": reason})
                    if preferred_version:
                        preferred_versions[t] = preferred_version
                        run.log_event({
                            "event": "policy.preferred_version",
                            "package": t,
                            "version": preferred_version,
                        })
                elif "Warning" in reason:
                    # Snapshot allowed but with warning
                    activity("policy", f"Warning: {t}: {reason}")
                    run.log_event({"event": "policy.warning", "package": t, "reason": reason})

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
        )

    sys.exit(exit_code)
