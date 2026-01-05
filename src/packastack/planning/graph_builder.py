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

"""Shared dependency graph building utilities.

Provides unified graph building functionality that can be used by both
the plan and build commands, supporting both package index and d/control
based approaches.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from packastack.debpkg.control import parse_control
from packastack.planning.graph import DependencyGraph

if TYPE_CHECKING:
    from packastack.apt.packages import PackageIndex

logger = logging.getLogger(__name__)


# Known soft/optional dependency exclusions keyed by source package name.
# These are excluded from the dependency graph to break cycles.
SOFT_DEPENDENCY_EXCLUSIONS: dict[str, set[str]] = {
    # oslo.config optionally imports oslo.log at runtime
    "python-oslo.config": {"python-oslo.log"},
    # networking-bagpipe/bgpvpn have packaging-only circular deps
    "networking-bagpipe": {"networking-bgpvpn"},
}

# Optional build deps that are excluded to prevent cycles.
OPTIONAL_BUILD_DEPS: set[str] = {
    "python3-hacking",
    "python3-openstackdocstheme",
    "python3-oslotest",
    "python3-reno",
    "python3-sphinx",
    "python3-sphinx-rtd-theme",
}


@dataclass
class GraphBuildResult:
    """Result of building a dependency graph."""

    graph: DependencyGraph
    # Binary package name -> source package name
    binary_to_source: dict[str, str] = field(default_factory=dict)
    # Source package -> list of missing dependency names
    missing_deps: dict[str, list[str]] = field(default_factory=dict)
    # Source package -> list of MIR (main inclusion) candidates
    mir_candidates: dict[str, list[str]] = field(default_factory=dict)
    # List of excluded dependency edges (source, dependency)
    excluded_edges: list[tuple[str, str]] = field(default_factory=list)
    # Warnings generated during graph building
    warnings: list[str] = field(default_factory=list)


def build_graph_from_control(
    packages: list[str],
    packaging_repos_path: Path,
    package_index: PackageIndex | None = None,
) -> GraphBuildResult:
    """Build dependency graph by parsing debian/control files.

    This approach reads the actual d/control files from packaging repos
    to build the graph. It's more accurate for local builds but requires
    the packaging repos to be available.

    Args:
        packages: List of source package names.
        packaging_repos_path: Path to directory containing packaging repos.
        package_index: Optional package index for resolving binary->source.

    Returns:
        GraphBuildResult with the dependency graph.
    """
    result = GraphBuildResult(graph=DependencyGraph())
    missing_deps: dict[str, list[str]] = defaultdict(list)

    # Build binary->source mapping from our packages
    for pkg in packages:
        result.graph.add_node(pkg, needs_rebuild=True)

        control_path = packaging_repos_path / pkg / "debian" / "control"
        if not control_path.exists():
            result.warnings.append(f"debian/control not found for {pkg}")
            continue

        try:
            source = parse_control(control_path)
            for binary in source.binaries:
                result.binary_to_source[binary.name] = pkg
                for provides in binary.provides:
                    result.binary_to_source[provides] = pkg
        except (ValueError, OSError) as e:
            result.warnings.append(f"Error parsing d/control for {pkg}: {e}")
            continue

    # Now build edges from Build-Depends
    package_set = set(packages)
    for pkg in packages:
        control_path = packaging_repos_path / pkg / "debian" / "control"
        if not control_path.exists():
            continue

        try:
            source = parse_control(control_path)
        except (ValueError, OSError):
            continue

        all_build_deps = source.build_depends + source.build_depends_indep

        for dep in all_build_deps:
            dep_name = dep.name

            # Skip optional deps that cause cycles
            if dep_name in OPTIONAL_BUILD_DEPS:
                continue
            dep_source = result.binary_to_source.get(dep_name)
            if dep_source is None and dep_name.startswith("python3-"):
                python_source = f"python-{dep_name[8:]}"
                if python_source in package_set:
                    dep_source = python_source
            found_in_index = False

            if dep_source is None and package_index:
                dep_pkg = package_index.find_package(dep_name)
                if dep_pkg:
                    found_in_index = True
                    if isinstance(dep_pkg.source, str) and dep_pkg.source in package_set:
                        dep_source = dep_pkg.source

            if dep_source and dep_source != pkg:
                excluded = SOFT_DEPENDENCY_EXCLUSIONS.get(pkg, set())
                if dep_source in excluded:
                    result.excluded_edges.append((pkg, dep_source))
                    continue
                result.graph.add_edge(pkg, dep_source)
            elif dep_source is None and not found_in_index:
                missing_deps[pkg].append(dep_name)

    result.missing_deps = dict(missing_deps)
    return result


def build_graph_from_index(
    packages: list[str],
    package_index: PackageIndex,
    openstack_packages: set[str] | None = None,
    skip_optional_deps: bool = False,
) -> GraphBuildResult:
    """Build dependency graph using package index data.

    This approach uses Packages.gz data to determine dependencies,
    without needing access to git repos. It's faster but may be less
    accurate for packages not yet published.

    Args:
        packages: List of source package names.
        package_index: Package index with dependency information.
        openstack_packages: Optional set of known OpenStack source packages.
        skip_optional_deps: If True, skip optional dependencies that can
            introduce cycles.

    Returns:
        GraphBuildResult with the dependency graph.
    """
    result = GraphBuildResult(graph=DependencyGraph())
    processed: set[str] = set()
    package_set = set(packages)

    if openstack_packages is None:
        openstack_packages = package_set

    to_process = list(packages)

    while to_process:
        source_name = to_process.pop(0)
        if source_name in processed:
            continue
        processed.add(source_name)

        # Get all binary packages for this source from index
        binary_names = package_index.get_binaries_for_source(source_name)
        if not binary_names:
            result.warnings.append(f"Source {source_name} not found in index")
            continue

        # Add node for this source package
        needs_rebuild = source_name in openstack_packages
        result.graph.add_node(source_name, needs_rebuild=needs_rebuild)

        # Build binary->source mapping
        for binary_name in binary_names:
            result.binary_to_source[binary_name] = source_name

        # Process runtime dependencies from all binary packages
        pkg_mir: list[str] = []
        for binary_name in binary_names:
            binary_pkg = package_index.find_package(binary_name)
            if not binary_pkg:
                continue

            # Process each dependency
            for dep_str in binary_pkg.depends + binary_pkg.pre_depends:
                # Parse dependency name (strip version constraints and alternatives)
                dep_name = dep_str.split()[0].split("(")[0].split("|")[0].strip()
                if not dep_name:
                    continue

                # Skip optional deps that cause cycles
                if skip_optional_deps and dep_name in OPTIONAL_BUILD_DEPS:
                    continue

                component = package_index.get_component(dep_name)
                if isinstance(component, str) and component and component != "main":
                    entry = f"{dep_name} ({component})"
                    if entry not in pkg_mir:
                        pkg_mir.append(entry)

                # Find the source package that provides this dependency
                dep_pkg = package_index.find_package(dep_name)
                dep_source = ""
                if dep_pkg and isinstance(dep_pkg.source, str):
                    dep_source = dep_pkg.source

                if dep_source and dep_source != source_name:
                    excluded = SOFT_DEPENDENCY_EXCLUSIONS.get(source_name, set())
                    if dep_source in excluded:
                        result.excluded_edges.append((source_name, dep_source))
                        continue

                    if dep_source in openstack_packages:
                        result.graph.add_edge(source_name, dep_source)
                        if dep_source not in processed:
                            to_process.append(dep_source)

        if pkg_mir:
            result.mir_candidates[source_name] = pkg_mir

    return result


def merge_graphs(
    graphs: list[DependencyGraph],
) -> DependencyGraph:
    """Merge multiple dependency graphs into one.

    Args:
        graphs: List of DependencyGraph objects to merge.

    Returns:
        A new DependencyGraph containing all nodes and edges.
    """
    merged = DependencyGraph()

    for graph in graphs:
        for name, node in graph.nodes.items():
            existing = merged.nodes.get(name)
            if existing:
                # Merge properties
                if node.needs_rebuild:
                    existing.needs_rebuild = True
                if node.version and not existing.version:
                    existing.version = node.version
            else:
                merged.add_node(
                    name,
                    version=node.version,
                    needs_rebuild=node.needs_rebuild,
                )

        for from_node, deps in graph.edges.items():
            for to_node in deps:
                merged.add_edge(from_node, to_node)

    return merged


def filter_graph_to_packages(
    graph: DependencyGraph,
    packages: set[str],
) -> DependencyGraph:
    """Filter a graph to only include specified packages.

    Args:
        graph: The source graph.
        packages: Set of package names to include.

    Returns:
        A new graph containing only the specified packages and
        edges between them.
    """
    filtered = DependencyGraph()

    for name in packages:
        if name in graph.nodes:
            node = graph.nodes[name]
            filtered.add_node(
                name,
                version=node.version,
                needs_rebuild=node.needs_rebuild,
            )

    for from_node in packages:
        if from_node in graph.edges:
            for to_node in graph.edges[from_node]:
                if to_node in packages:
                    filtered.add_edge(from_node, to_node)

    return filtered
