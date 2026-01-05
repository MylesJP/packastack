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

"""Tests for the graph module - dependency graph operations."""

from __future__ import annotations

import pytest

from packastack.planning.graph import DependencyGraph, GraphNode, PlanResult


class TestGraphNode:
    """Tests for the GraphNode dataclass."""

    def test_defaults(self) -> None:
        """Test default values."""
        node = GraphNode(name="test-pkg")
        assert node.name == "test-pkg"
        assert node.version == ""
        assert node.needs_rebuild is False
        assert node.rebuild_reason == ""
        assert node.mir_warnings == []

    def test_with_values(self) -> None:
        """Test node with explicit values."""
        node = GraphNode(
            name="nova",
            version="2:28.0.0-0ubuntu1",
            needs_rebuild=True,
            rebuild_reason="new upstream",
            mir_warnings=["uses libfoo from universe"],
        )
        assert node.name == "nova"
        assert node.version == "2:28.0.0-0ubuntu1"
        assert node.needs_rebuild is True
        assert node.rebuild_reason == "new upstream"
        assert node.mir_warnings == ["uses libfoo from universe"]


class TestDependencyGraph:
    """Tests for DependencyGraph class."""

    def test_empty_graph(self) -> None:
        """Test an empty graph."""
        g = DependencyGraph()
        assert g.nodes == {}
        assert g.edges == {}
        assert g.reverse_edges == {}

    def test_add_node(self) -> None:
        """Test adding a node."""
        g = DependencyGraph()
        node = g.add_node("nova", version="1.0", needs_rebuild=True)

        assert node.name == "nova"
        assert node.version == "1.0"
        assert node.needs_rebuild is True
        assert "nova" in g.nodes
        assert "nova" in g.edges
        assert "nova" in g.reverse_edges
        assert g.edges["nova"] == set()
        assert g.reverse_edges["nova"] == set()

    def test_add_node_update_existing(self) -> None:
        """Test that add_node updates an existing node."""
        g = DependencyGraph()
        g.add_node("nova", version="1.0", needs_rebuild=False)

        # Update with new values
        node = g.add_node("nova", version="2.0", needs_rebuild=True)

        assert node.version == "2.0"
        assert node.needs_rebuild is True

    def test_add_node_preserves_version_if_empty(self) -> None:
        """Test that empty version doesn't overwrite existing."""
        g = DependencyGraph()
        g.add_node("nova", version="1.0")
        g.add_node("nova", version="")

        assert g.nodes["nova"].version == "1.0"

    def test_add_edge(self) -> None:
        """Test adding an edge between nodes."""
        g = DependencyGraph()
        g.add_node("nova")
        g.add_node("oslo.config")

        g.add_edge("nova", "oslo.config")

        assert "oslo.config" in g.edges["nova"]
        assert "nova" in g.reverse_edges["oslo.config"]

    def test_add_edge_creates_nodes(self) -> None:
        """Test that add_edge creates nodes if they don't exist."""
        g = DependencyGraph()
        g.add_edge("nova", "oslo.config")

        assert "nova" in g.nodes
        assert "oslo.config" in g.nodes
        assert "oslo.config" in g.edges["nova"]

    def test_get_dependencies(self) -> None:
        """Test getting direct dependencies."""
        g = DependencyGraph()
        g.add_edge("nova", "oslo.config")
        g.add_edge("nova", "oslo.log")

        deps = g.get_dependencies("nova")

        assert deps == {"oslo.config", "oslo.log"}

    def test_get_dependencies_unknown_node(self) -> None:
        """Test getting dependencies for non-existent node."""
        g = DependencyGraph()
        deps = g.get_dependencies("unknown")
        assert deps == set()

    def test_get_dependents(self) -> None:
        """Test getting packages that depend on a node."""
        g = DependencyGraph()
        g.add_edge("nova", "oslo.config")
        g.add_edge("cinder", "oslo.config")

        dependents = g.get_dependents("oslo.config")

        assert dependents == {"nova", "cinder"}

    def test_get_dependents_unknown_node(self) -> None:
        """Test getting dependents for non-existent node."""
        g = DependencyGraph()
        dependents = g.get_dependents("unknown")
        assert dependents == set()


class TestDetectCycles:
    """Tests for cycle detection."""

    def test_no_cycles(self) -> None:
        """Test graph with no cycles."""
        g = DependencyGraph()
        g.add_edge("A", "B")
        g.add_edge("B", "C")
        g.add_edge("A", "C")

        cycles = g.detect_cycles()

        assert cycles == []

    def test_simple_cycle(self) -> None:
        """Test detecting a simple cycle."""
        g = DependencyGraph()
        g.add_edge("A", "B")
        g.add_edge("B", "C")
        g.add_edge("C", "A")

        cycles = g.detect_cycles()

        assert len(cycles) >= 1
        # The cycle should contain all three nodes
        cycle_set = set(cycles[0])
        assert {"A", "B", "C"} <= cycle_set

    def test_self_loop(self) -> None:
        """Test detecting a self-referential cycle."""
        g = DependencyGraph()
        g.add_edge("A", "A")

        cycles = g.detect_cycles()

        assert len(cycles) >= 1
        assert "A" in cycles[0]

    def test_multiple_cycles(self) -> None:
        """Test graph with multiple independent cycles."""
        g = DependencyGraph()
        # First cycle
        g.add_edge("A", "B")
        g.add_edge("B", "A")
        # Second cycle
        g.add_edge("C", "D")
        g.add_edge("D", "C")

        cycles = g.detect_cycles()

        assert len(cycles) >= 2


class TestTopologicalSort:
    """Tests for topological sorting."""

    def test_simple_chain(self) -> None:
        """Test topological sort of a simple chain."""
        g = DependencyGraph()
        g.add_edge("A", "B")  # A depends on B
        g.add_edge("B", "C")  # B depends on C

        order = g.topological_sort()

        # C must come before B, B must come before A
        assert order.index("C") < order.index("B")
        assert order.index("B") < order.index("A")

    def test_diamond(self) -> None:
        """Test topological sort with diamond dependency pattern."""
        g = DependencyGraph()
        g.add_edge("A", "B")
        g.add_edge("A", "C")
        g.add_edge("B", "D")
        g.add_edge("C", "D")

        order = g.topological_sort()

        # D must come before B and C, both must come before A
        assert order.index("D") < order.index("B")
        assert order.index("D") < order.index("C")
        assert order.index("B") < order.index("A")
        assert order.index("C") < order.index("A")

    def test_empty_graph(self) -> None:
        """Test topological sort of empty graph."""
        g = DependencyGraph()
        order = g.topological_sort()
        assert order == []

    def test_single_node(self) -> None:
        """Test topological sort with single node."""
        g = DependencyGraph()
        g.add_node("A")
        order = g.topological_sort()
        assert order == ["A"]

    def test_multiple_roots(self) -> None:
        """Test graph with multiple root nodes."""
        g = DependencyGraph()
        g.add_edge("A", "C")
        g.add_edge("B", "C")

        order = g.topological_sort()

        assert order.index("C") < order.index("A")
        assert order.index("C") < order.index("B")

    def test_raises_on_cycle(self) -> None:
        """Test that topological sort raises on cycle."""
        g = DependencyGraph()
        g.add_edge("A", "B")
        g.add_edge("B", "A")

        with pytest.raises(ValueError, match="Dependency cycle detected"):
            g.topological_sort()


class TestGetRebuildOrder:
    """Tests for rebuild order calculation."""

    def test_single_package_rebuild(self) -> None:
        """Test rebuild order with single package needing rebuild."""
        g = DependencyGraph()
        g.add_node("A", needs_rebuild=True)
        g.add_node("B", needs_rebuild=False)
        g.add_edge("B", "A")  # B depends on A

        order = g.get_rebuild_order()

        # A needs rebuild, B depends on A so B also needs rebuild
        assert "A" in order
        assert "B" in order
        assert order.index("A") < order.index("B")

    def test_rebuild_propagates_to_dependents(self) -> None:
        """Test that rebuild need propagates transitively."""
        g = DependencyGraph()
        g.add_node("A", needs_rebuild=True)
        g.add_node("B")
        g.add_node("C")
        g.add_edge("B", "A")  # B depends on A
        g.add_edge("C", "B")  # C depends on B

        order = g.get_rebuild_order()

        # All three should need rebuild
        assert set(order) == {"A", "B", "C"}
        # Order: A first, then B, then C
        assert order.index("A") < order.index("B")
        assert order.index("B") < order.index("C")

    def test_rebuild_sets_reason(self) -> None:
        """Test that rebuild reason is set for dependents."""
        g = DependencyGraph()
        g.add_node("oslo.config", needs_rebuild=True)
        g.add_node("nova")
        g.add_edge("nova", "oslo.config")

        g.get_rebuild_order()

        assert g.nodes["nova"].needs_rebuild is True
        assert "oslo.config" in g.nodes["nova"].rebuild_reason

    def test_no_rebuilds_needed(self) -> None:
        """Test with no packages needing rebuild."""
        g = DependencyGraph()
        g.add_node("A")
        g.add_node("B")
        g.add_edge("A", "B")

        order = g.get_rebuild_order()

        assert order == []

    def test_partial_rebuild(self) -> None:
        """Test that unrelated packages are not included."""
        g = DependencyGraph()
        g.add_node("A", needs_rebuild=True)
        g.add_node("B")  # No dependency on A
        g.add_node("C")
        g.add_edge("C", "A")  # C depends on A

        order = g.get_rebuild_order()

        assert set(order) == {"A", "C"}
        assert "B" not in order


class TestFindMissingDependencies:
    """Tests for missing dependency detection."""

    def test_no_missing(self) -> None:
        """Test when all dependencies are known."""
        g = DependencyGraph()
        g.add_edge("A", "B")
        g.add_edge("A", "C")

        missing = g.find_missing_dependencies({"A", "B", "C"})

        assert missing == {}

    def test_find_missing(self) -> None:
        """Test finding missing dependencies.

        Dependencies that are NOT in the graph and NOT in known_packages
        are considered missing. add_edge adds nodes to the graph.
        """
        g = DependencyGraph()
        # Only add nova node, add libfoo as edge target but we need a different
        # approach - the implementation checks if dep is in known OR in graph nodes.
        # So we need to manually set up edges without using add_edge.
        g.add_node("nova")
        g.edges["nova"] = {"libfoo", "oslo.config"}

        # oslo.config is known, libfoo is not known and not in nodes
        missing = g.find_missing_dependencies({"nova", "oslo.config"})

        assert "nova" in missing
        assert "libfoo" in missing["nova"]
        assert "oslo.config" not in missing.get("nova", [])

    def test_missing_external_deps(self) -> None:
        """Test that deps in graph nodes are not considered missing."""
        g = DependencyGraph()
        g.add_node("A")
        g.add_node("B")
        g.add_edge("A", "B")

        # Even though B isn't in known_packages, it's in the graph
        missing = g.find_missing_dependencies({"A"})

        assert missing == {}


class TestPlanResult:
    """Tests for PlanResult dataclass."""

    def test_defaults(self) -> None:
        """Test default values."""
        result = PlanResult()

        assert result.build_order == []
        assert result.upload_order == []
        assert result.mir_candidates == {}
        assert result.missing_packages == {}
        assert result.cycles == []

    def test_has_errors_false(self) -> None:
        """Test has_errors returns False when no errors."""
        result = PlanResult(
            build_order=["A", "B"],
            mir_candidates={"A": ["libfoo"]},
        )

        assert result.has_errors() is False

    def test_has_errors_missing_packages(self) -> None:
        """Test has_errors returns True for missing packages."""
        result = PlanResult(
            missing_packages={"A": ["unknown-dep"]},
        )

        assert result.has_errors() is True

    def test_has_errors_cycles(self) -> None:
        """Test has_errors returns True for cycles."""
        result = PlanResult(
            cycles=[["A", "B", "A"]],
        )

        assert result.has_errors() is True

    def test_has_errors_both(self) -> None:
        """Test has_errors when both errors present."""
        result = PlanResult(
            missing_packages={"A": ["dep"]},
            cycles=[["X", "Y", "X"]],
        )

        assert result.has_errors() is True
