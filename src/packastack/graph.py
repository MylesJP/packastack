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

"""Dependency graph construction and analysis for build planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass
class GraphNode:
    """Represents a node in the dependency graph (a source package)."""

    name: str
    version: str = ""
    needs_rebuild: bool = False
    rebuild_reason: str = ""
    mir_warnings: list[str] = field(default_factory=list)


@dataclass
class DependencyGraph:
    """Directed acyclic graph of package dependencies.

    Nodes are source packages.
    Edges are runtime dependencies (A depends on B means edge A -> B).
    """

    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: dict[str, set[str]] = field(default_factory=dict)  # node -> set of dependencies
    reverse_edges: dict[str, set[str]] = field(default_factory=dict)  # node -> set of dependents

    def add_node(self, name: str, version: str = "", needs_rebuild: bool = False) -> GraphNode:
        """Add a node to the graph or update if exists."""
        if name not in self.nodes:
            self.nodes[name] = GraphNode(name=name, version=version, needs_rebuild=needs_rebuild)
            self.edges[name] = set()
            self.reverse_edges[name] = set()
        else:
            node = self.nodes[name]
            if version:
                node.version = version
            if needs_rebuild:
                node.needs_rebuild = True
        return self.nodes[name]

    def add_edge(self, from_node: str, to_node: str) -> None:
        """Add a dependency edge: from_node depends on to_node."""
        # Ensure both nodes exist
        if from_node not in self.nodes:
            self.add_node(from_node)
        if to_node not in self.nodes:
            self.add_node(to_node)

        self.edges[from_node].add(to_node)
        self.reverse_edges[to_node].add(from_node)

    def get_dependencies(self, node: str) -> set[str]:
        """Get direct dependencies of a node."""
        return self.edges.get(node, set())

    def get_dependents(self, node: str) -> set[str]:
        """Get packages that depend on this node."""
        return self.reverse_edges.get(node, set())

    def detect_cycles(self) -> list[list[str]]:
        """Detect cycles in the graph using DFS.

        Returns:
            List of cycles, where each cycle is a list of node names.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = dict.fromkeys(self.nodes, WHITE)
        parent: dict[str, str | None] = dict.fromkeys(self.nodes)
        cycles: list[list[str]] = []

        def dfs(node: str) -> None:
            color[node] = GRAY
            for neighbor in self.edges.get(node, set()):
                if color[neighbor] == GRAY:
                    # Back edge found - reconstruct cycle
                    cycle = [neighbor]
                    current: str | None = node
                    while current is not None and current != neighbor:
                        cycle.append(current)
                        current = parent.get(current)
                    cycle.append(neighbor)
                    cycle.reverse()
                    cycles.append(cycle)
                elif color[neighbor] == WHITE:
                    parent[neighbor] = node
                    dfs(neighbor)
            color[node] = BLACK

        for node in self.nodes:
            if color[node] == WHITE:
                dfs(node)

        return cycles

    def topological_sort(self) -> list[str]:
        """Return nodes in topological order (dependencies before dependents).

        Raises:
            ValueError: If the graph contains cycles.
        """
        cycles = self.detect_cycles()
        if cycles:
            cycle_str = " -> ".join(cycles[0])
            raise ValueError(f"Dependency cycle detected: {cycle_str}")

        # Kahn's algorithm
        in_degree = {node: len(self.edges.get(node, set())) for node in self.nodes}
        # Wait, that's wrong. in_degree should be how many edges come INTO the node.
        in_degree = dict.fromkeys(self.nodes, 0)
        for deps in self.edges.values():
            for dep in deps:
                in_degree[dep] = in_degree.get(dep, 0) + 1

        # Start with nodes that have no incoming edges (no dependents in our model)
        # Actually, we want build order: dependencies should be built first.
        # So we want nodes with no outgoing edges (no dependencies) to be first.
        # Let's reconsider: if A depends on B, edge is A -> B.
        # Build order: B first, then A.
        # So we need reverse topological order of edges.
        # Let's compute out-degree based topological sort.

        # Alternative: use reverse edges for Kahn's
        in_degree = {node: len(self.edges.get(node, set())) for node in self.nodes}

        queue = [node for node in self.nodes if in_degree[node] == 0]
        result: list[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for dependent in self.reverse_edges.get(node, set()):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(result) != len(self.nodes):
            raise ValueError("Graph has a cycle - topological sort incomplete")

        return result

    def get_rebuild_order(self) -> list[str]:
        """Get the build order for packages that need rebuilding.

        Returns packages in topological order, filtered to only those
        needing rebuild (and their transitive dependents).
        """
        # First, mark all dependents of rebuild-needed packages
        needs_rebuild = {n for n, node in self.nodes.items() if node.needs_rebuild}

        # Propagate rebuild need to dependents
        to_process = list(needs_rebuild)
        while to_process:
            current = to_process.pop()
            for dependent in self.get_dependents(current):
                if dependent not in needs_rebuild:
                    needs_rebuild.add(dependent)
                    self.nodes[dependent].needs_rebuild = True
                    self.nodes[dependent].rebuild_reason = f"depends on {current}"
                    to_process.append(dependent)

        # Get full topological order, filter to rebuild set
        full_order = self.topological_sort()
        return [n for n in full_order if n in needs_rebuild]

    def find_missing_dependencies(
        self,
        known_packages: set[str],
    ) -> dict[str, list[str]]:
        """Find dependencies that are not in the known packages set.

        Args:
            known_packages: Set of package names that are available.

        Returns:
            Dict mapping source package to list of missing dependency names.
        """
        missing: dict[str, list[str]] = {}

        for node_name, deps in self.edges.items():
            node_missing = [d for d in deps if d not in known_packages and d not in self.nodes]
            if node_missing:
                missing[node_name] = node_missing

        return missing


@dataclass
class PlanResult:
    """Result of build plan generation."""

    build_order: list[str] = field(default_factory=list)
    upload_order: list[str] = field(default_factory=list)
    mir_candidates: dict[str, list[str]] = field(default_factory=dict)  # pkg -> [deps in universe]
    missing_packages: dict[str, list[str]] = field(default_factory=dict)  # pkg -> [missing deps]
    cycles: list[list[str]] = field(default_factory=list)

    def has_errors(self) -> bool:
        """Check if there are blocking errors."""
        return bool(self.missing_packages) or bool(self.cycles)


if __name__ == "__main__":
    # Simple test
    g = DependencyGraph()
    g.add_node("nova", needs_rebuild=True)
    g.add_node("oslo.messaging")
    g.add_node("oslo.config")
    g.add_edge("nova", "oslo.messaging")
    g.add_edge("oslo.messaging", "oslo.config")

    print("Topological order:", g.topological_sort())
    print("Rebuild order:", g.get_rebuild_order())
