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

"""Tests for plan graph reports module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from packastack.planning.graph import DependencyGraph
from packastack.reports.plan_graph import (
    GraphEdge,
    GraphNode,
    PlanGraph,
    render_ascii,
    render_build_order_list,
    render_dot,
    render_html,
    render_json,
    render_waves,
    write_plan_graph_reports,
)


class TestGraphNode:
    """Tests for GraphNode dataclass."""

    def test_defaults(self) -> None:
        """Test default values."""
        node = GraphNode(id="nova")
        assert node.id == "nova"
        assert node.build_type == "snapshot"
        assert node.status == "ok"
        assert node.order == -1
        assert node.dependencies == []
        assert node.dependents == []

    def test_to_dict(self) -> None:
        """Test dictionary conversion."""
        node = GraphNode(
            id="glance",
            build_type="release",
            status="ok",
            order=5,
        )
        d = node.to_dict()
        assert d["id"] == "glance"
        assert d["type"] == "release"
        assert d["status"] == "ok"
        assert d["order"] == 5


class TestGraphEdge:
    """Tests for GraphEdge dataclass."""

    def test_defaults(self) -> None:
        """Test default values."""
        edge = GraphEdge(from_node="nova", to_node="oslo.config")
        assert edge.from_node == "nova"
        assert edge.to_node == "oslo.config"
        assert edge.kind == "build-depends"

    def test_to_dict(self) -> None:
        """Test dictionary conversion."""
        edge = GraphEdge(from_node="a", to_node="b", kind="test")
        d = edge.to_dict()
        assert d["from"] == "a"
        assert d["to"] == "b"
        assert d["kind"] == "test"


class TestPlanGraph:
    """Tests for PlanGraph dataclass."""

    def test_empty_graph(self) -> None:
        """Test empty graph properties."""
        graph = PlanGraph(
            run_id="test-run",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        assert graph.node_count == 0
        assert graph.edge_count == 0

    def test_to_dict(self) -> None:
        """Test dictionary conversion."""
        graph = PlanGraph(
            run_id="test-run",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["nova"] = GraphNode(id="nova", order=0)
        graph.nodes["glance"] = GraphNode(id="glance", order=1)
        graph.edges.append(GraphEdge(from_node="glance", to_node="nova"))
        graph.topo_order = ["nova", "glance"]

        d = graph.to_dict()
        assert d["run_id"] == "test-run"
        assert d["target"] == "gazpacho"
        assert d["ubuntu_series"] == "resolute"
        assert len(d["nodes"]) == 2
        assert len(d["edges"]) == 1
        assert d["topo_order"] == ["nova", "glance"]
        assert d["summary"]["node_count"] == 2
        assert d["summary"]["edge_count"] == 1
        assert d["summary"]["cycles"] == 0

    def test_from_dependency_graph(self) -> None:
        """Test creation from DependencyGraph."""
        dep_graph = DependencyGraph()
        dep_graph.add_node("nova", needs_rebuild=True)
        dep_graph.add_node("oslo.config", needs_rebuild=True)
        dep_graph.add_edge("nova", "oslo.config")

        plan_graph = PlanGraph.from_dependency_graph(
            dep_graph=dep_graph,
            run_id="test",
            target="gazpacho",
            ubuntu_series="resolute",
        )

        assert plan_graph.node_count == 2
        assert plan_graph.edge_count == 1
        assert "nova" in plan_graph.nodes
        assert "oslo.config" in plan_graph.nodes
        # Topo order: oslo.config first (no deps), then nova
        assert plan_graph.topo_order == ["oslo.config", "nova"]

    def test_from_dependency_graph_with_type_report(self) -> None:
        """Test creation with type selection report."""
        dep_graph = DependencyGraph()
        dep_graph.add_node("nova", needs_rebuild=True)
        dep_graph.add_node("oslo.config", needs_rebuild=True)

        # Mock type selection report
        mock_report = MagicMock()
        mock_pkg1 = MagicMock()
        mock_pkg1.source_package = "nova"
        mock_pkg1.chosen_type.value = "release"
        mock_pkg2 = MagicMock()
        mock_pkg2.source_package = "oslo.config"
        mock_pkg2.chosen_type.value = "snapshot"
        mock_report.packages = [mock_pkg1, mock_pkg2]

        plan_graph = PlanGraph.from_dependency_graph(
            dep_graph=dep_graph,
            run_id="test",
            target="gazpacho",
            ubuntu_series="resolute",
            type_report=mock_report,
        )

        assert plan_graph.nodes["nova"].build_type == "release"
        assert plan_graph.nodes["oslo.config"].build_type == "snapshot"

    def test_from_dependency_graph_with_cycles(self) -> None:
        """Test creation with cycle detection."""
        dep_graph = DependencyGraph()
        dep_graph.add_node("a")
        dep_graph.add_node("b")
        # Note: No actual cycle in this graph, but we pass cycle info

        plan_graph = PlanGraph.from_dependency_graph(
            dep_graph=dep_graph,
            run_id="test",
            target="gazpacho",
            ubuntu_series="resolute",
            cycles=[["a", "b", "a"]],
        )

        assert len(plan_graph.cycles) == 1
        assert plan_graph.nodes["a"].status == "cycle"
        assert plan_graph.nodes["b"].status == "cycle"

    def test_from_dependency_graph_with_actual_cycle(self) -> None:
        """Should clear topo order when cycles exist in graph."""
        dep_graph = DependencyGraph()
        dep_graph.add_edge("a", "b")
        dep_graph.add_edge("b", "a")

        plan_graph = PlanGraph.from_dependency_graph(
            dep_graph=dep_graph,
            run_id="test",
            target="gazpacho",
            ubuntu_series="resolute",
        )

        assert plan_graph.topo_order == []

    def test_from_dependency_graph_sets_waves(self) -> None:
        """Should compute waves for nodes."""
        dep_graph = DependencyGraph()
        dep_graph.add_edge("nova", "oslo.config")

        plan_graph = PlanGraph.from_dependency_graph(
            dep_graph=dep_graph,
            run_id="test",
            target="gazpacho",
            ubuntu_series="resolute",
        )

        assert plan_graph.waves
        assert plan_graph.nodes["oslo.config"].wave >= 0

    def test_from_dependency_graph_skips_unknown_nodes(self) -> None:
        """Should ignore unknown nodes from topo and waves."""
        dep_graph = DependencyGraph()
        dep_graph.add_node("a")
        dep_graph.topological_sort = lambda: ["a", "missing"]
        dep_graph.compute_waves_with_cycles = lambda: {"a": 0, "missing": 1}
        dep_graph.compute_forced_by = lambda _waves: {"a": []}

        plan_graph = PlanGraph.from_dependency_graph(
            dep_graph=dep_graph,
            run_id="test",
            target="gazpacho",
            ubuntu_series="resolute",
        )

        assert "a" in plan_graph.nodes
        assert "missing" not in plan_graph.nodes

    def test_get_subgraph(self) -> None:
        """Test subgraph extraction."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )

        # Create a chain: a -> b -> c -> d -> e
        for name in ["a", "b", "c", "d", "e"]:
            graph.nodes[name] = GraphNode(id=name)

        # Set up dependencies
        graph.nodes["a"].dependencies = ["b"]
        graph.nodes["a"].dependents = []
        graph.nodes["b"].dependencies = ["c"]
        graph.nodes["b"].dependents = ["a"]
        graph.nodes["c"].dependencies = ["d"]
        graph.nodes["c"].dependents = ["b"]
        graph.nodes["d"].dependencies = ["e"]
        graph.nodes["d"].dependents = ["c"]
        graph.nodes["e"].dependencies = []
        graph.nodes["e"].dependents = ["d"]

        graph.edges = [
            GraphEdge("a", "b"),
            GraphEdge("b", "c"),
            GraphEdge("c", "d"),
            GraphEdge("d", "e"),
        ]
        graph.topo_order = ["e", "d", "c", "b", "a"]

        # Focus on 'c' with depth 1
        sub = graph.get_subgraph("c", depth=1)

        # Should include b, c, d (1 step in each direction)
        assert "c" in sub.nodes
        assert "b" in sub.nodes
        assert "d" in sub.nodes
        assert "a" not in sub.nodes
        assert "e" not in sub.nodes

    def test_get_subgraph_missing_node(self) -> None:
        """Test subgraph extraction with missing focus node."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["a"] = GraphNode(id="a")

        sub = graph.get_subgraph("nonexistent")
        assert sub.node_count == 0

    def test_get_subgraph_with_missing_dependency(self) -> None:
        """Should skip dependencies not in the graph."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["a"] = GraphNode(id="a", dependencies=["missing"], dependents=["child"])
        graph.nodes["child"] = GraphNode(id="child")

        sub = graph.get_subgraph("a", depth=2)

        assert "missing" not in sub.nodes
        assert "a" in sub.nodes


class TestRenderJson:
    """Tests for render_json function."""

    def test_writes_valid_json(self, tmp_path: Path) -> None:
        """Test that valid JSON is written."""
        graph = PlanGraph(
            run_id="test-run",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["nova"] = GraphNode(id="nova", build_type="release", order=0)

        output_path = tmp_path / "test.json"
        result = render_json(graph, output_path)

        assert result == output_path
        assert output_path.exists()

        data = json.loads(output_path.read_text())
        assert data["run_id"] == "test-run"
        assert data["target"] == "gazpacho"
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["id"] == "nova"
        assert data["nodes"][0]["type"] == "release"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Test that parent directories are created."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )

        output_path = tmp_path / "nested" / "dir" / "test.json"
        render_json(graph, output_path)

        assert output_path.exists()


class TestRenderDot:
    """Tests for render_dot function."""

    def test_basic_output(self) -> None:
        """Test basic DOT output structure."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["nova"] = GraphNode(id="nova", build_type="release")
        graph.nodes["glance"] = GraphNode(id="glance", build_type="snapshot")
        graph.edges.append(GraphEdge("nova", "glance"))

        dot = render_dot(graph)

        assert "digraph packastack_plan {" in dot
        assert '"nova"' in dot
        assert '"glance"' in dot
        # Edge is reversed for build order: glance must be built before nova
        assert '"glance" -> "nova"' in dot
        assert "}" in dot

    def test_node_colors(self) -> None:
        """Test that different build types get different colors."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["release_pkg"] = GraphNode(id="release_pkg", build_type="release")
        graph.nodes["snapshot_pkg"] = GraphNode(id="snapshot_pkg", build_type="snapshot")

        dot = render_dot(graph)

        # Check that each node has a fillcolor attribute
        assert 'fillcolor="#90EE90"' in dot  # release - light green
        assert 'fillcolor="#ADD8E6"' in dot  # snapshot - light blue

    def test_cycle_status(self) -> None:
        """Test that cycle nodes get red color."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["cycle_pkg"] = GraphNode(id="cycle_pkg", status="cycle")

        dot = render_dot(graph)

        assert 'fillcolor="#FF6B6B"' in dot  # cycle - red

    def test_focus_mode(self) -> None:
        """Test focused subgraph extraction."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        # Create nodes with dependencies set
        graph.nodes["a"] = GraphNode(id="a", dependencies=[], dependents=["b"])
        graph.nodes["b"] = GraphNode(id="b", dependencies=["a"], dependents=["c"])
        graph.nodes["c"] = GraphNode(id="c", dependencies=["b"], dependents=[])
        graph.edges = [GraphEdge("b", "a"), GraphEdge("c", "b")]

        # Focus on 'b' with depth 1 - should include a, b, c
        dot = render_dot(graph, focus="b", depth=1)

        assert '"a"' in dot
        assert '"b"' in dot
        assert '"c"' in dot

    def test_blocked_nodes_are_styled(self) -> None:
        """Test blocked nodes get dashed styling."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["blocked"] = GraphNode(id="blocked", status="blocked")

        dot = render_dot(graph)

        assert "dashed" in dot

    def test_truncates_large_graph(self) -> None:
        """Should truncate large graphs without focus."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        for i in range(210):
            graph.nodes[f"pkg{i}"] = GraphNode(id=f"pkg{i}")

        dot = render_dot(graph)

        assert "digraph packastack_plan" in dot

    def test_rank_constraints_for_waves(self) -> None:
        """Should add rank constraints when waves exist."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["a"] = GraphNode(id="a", wave=1)
        graph.nodes["b"] = GraphNode(id="b", wave=1)
        graph.waves = {1: ["a", "b"]}

        dot = render_dot(graph)

        assert "wave 1" in dot
        assert "rank=same" in dot


class TestRenderWaves:
    """Tests for render_waves."""

    def test_renders_waves_with_focus_and_wrapping(self) -> None:
        """Should render waves and wrap long waves."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["base"] = GraphNode(id="base", dependencies=[], dependents=["lib1", "lib2"])
        graph.nodes["lib1"] = GraphNode(id="lib1", dependencies=["base"], dependents=["svc"])
        graph.nodes["lib2"] = GraphNode(id="lib2", dependencies=["base"], dependents=["svc"])
        graph.nodes["svc"] = GraphNode(id="svc", dependencies=["lib1", "lib2"])
        graph.waves = {0: ["base", "lib1", "lib2"], 1: ["svc"]}
        graph.edges = [
            GraphEdge("lib1", "base"),
            GraphEdge("lib2", "base"),
            GraphEdge("svc", "lib1"),
        ]

        output = render_waves(graph, focus="svc", max_wave_packages=1)

        assert "Wave 0" in output
        assert "Wave 1" in output
        assert "base" in output
        assert "svc" in output

    def test_renders_no_waves_message(self) -> None:
        """Should show empty message when no waves are computed."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )

        output = render_waves(graph)

        assert "no waves computed" in output


class TestRenderBuildOrderList:
    """Tests for render_build_order_list."""

    def test_includes_forced_by_details(self) -> None:
        """Should include forced-by details and wave hints."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["a"] = GraphNode(
            id="a",
            dependencies=["b", "c", "d", "e"],
            dependents=[],
            forced_by=["b", "c", "d"],
            wave=2,
        )
        graph.nodes["b"] = GraphNode(id="b", wave=0)
        graph.nodes["c"] = GraphNode(id="c", wave=0)
        graph.nodes["d"] = GraphNode(id="d", wave=1)
        graph.nodes["e"] = GraphNode(id="e", wave=1)
        graph.nodes["f"] = GraphNode(
            id="f",
            dependencies=["b", "c", "d"],
            dependents=[],
            forced_by=["b"],
            wave=3,
        )
        graph.topo_order = ["b", "c", "d", "e", "a", "f"]

        output = render_build_order_list(graph, focus="a", max_forced_by=2)

        assert "forced-by" in output
        assert "(+1 more)" in output
        assert "wave 2" in output

    def test_includes_other_dependency_count(self) -> None:
        """Should include remaining dependency count when forced-by is shorter."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["a"] = GraphNode(
            id="a",
            dependencies=["b", "c", "d"],
            forced_by=["b"],
            wave=2,
        )
        graph.nodes["b"] = GraphNode(id="b", wave=0)
        graph.nodes["c"] = GraphNode(id="c", wave=0)
        graph.nodes["d"] = GraphNode(id="d", wave=1)
        graph.topo_order = ["b", "c", "d", "a"]

        output = render_build_order_list(graph, max_forced_by=3)

        assert "(+2 deps)" in output


class TestRenderAscii:
    """Tests for render_ascii function."""

    def test_header_info(self) -> None:
        """Test that header contains metadata."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["nova"] = GraphNode(id="nova")

        output = render_ascii(graph)

        assert "Build Order Graph:" in output
        assert "1 packages" in output
        assert "gazpacho" in output
        assert "resolute" in output

    def test_legend(self) -> None:
        """Test that legend is included."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )

        output = render_ascii(graph)

        assert "[R]=Release" in output
        assert "[S]=Snapshot" in output

    def test_cycle_warning(self) -> None:
        """Test that cycles are warned about."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
            cycles=[["a", "b", "a"]],
        )

        output = render_ascii(graph)

        assert "Cycles detected" in output
        assert "a -> b -> a" in output

    def test_list_style(self) -> None:
        """Test list style output."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["nova"] = GraphNode(id="nova", build_type="release")
        graph.nodes["glance"] = GraphNode(id="glance", build_type="snapshot")
        graph.topo_order = ["nova", "glance"]

        output = render_ascii(graph, style="list")

        assert "Build order:" in output
        assert "[R] nova" in output
        assert "[S] glance" in output

    def test_list_style_truncates_dependencies(self) -> None:
        """Should show dependency truncation when over limit."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["nova"] = GraphNode(
            id="nova",
            build_type="release",
            dependencies=["a", "b", "c", "d", "e"],
        )

        output = render_ascii(graph, style="list")

        assert "(+2)" in output

    def test_tree_style_truncates_roots_and_deps(self) -> None:
        """Should show truncation for tree view roots and dependencies."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        for i in range(25):
            node_id = f"root{i}"
            deps = [f"dep{j}" for j in range(6)] if i == 0 else []
            graph.nodes[node_id] = GraphNode(id=node_id, dependencies=deps)
            for dep in deps:
                graph.nodes.setdefault(dep, GraphNode(id=dep))

        output = render_ascii(graph, style="tree")

        assert "Root packages" in output
        assert "more root packages" in output
        assert "more deps" in output

    def test_truncation_warning(self) -> None:
        """Test truncation warning for large graphs."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        # Add many nodes
        for i in range(100):
            graph.nodes[f"pkg{i}"] = GraphNode(id=f"pkg{i}")

        output = render_ascii(graph, max_nodes=50)

        assert "truncated" in output.lower() or "50" in output

    def test_stable_ordering(self) -> None:
        """Test that output is deterministic."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["z"] = GraphNode(id="z")
        graph.nodes["a"] = GraphNode(id="a")
        graph.nodes["m"] = GraphNode(id="m")
        graph.topo_order = ["a", "m", "z"]

        output1 = render_ascii(graph)
        output2 = render_ascii(graph)

        assert output1 == output2


class TestRenderHtml:
    """Tests for render_html function."""

    def test_self_contained(self) -> None:
        """Test that HTML is self-contained."""
        graph = PlanGraph(
            run_id="test-run",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["nova"] = GraphNode(id="nova")

        html = render_html(graph)

        # Should be complete HTML document
        assert "<!DOCTYPE html>" in html
        assert "<html" in html
        assert "</html>" in html
        assert "<style>" in html
        assert "</style>" in html
        # No external CDN references
        assert "cdn." not in html.lower()
        assert "http://" not in html.lower() or "https://" not in html.lower()

    def test_contains_metadata(self) -> None:
        """Test that HTML contains run metadata."""
        graph = PlanGraph(
            run_id="test-run-123",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )

        html = render_html(graph)

        assert "test-run-123" in html
        assert "gazpacho" in html
        assert "resolute" in html

    def test_contains_node_labels(self) -> None:
        """Test that HTML contains node labels."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["nova"] = GraphNode(id="nova", build_type="release")
        graph.nodes["glance"] = GraphNode(id="glance", build_type="snapshot")

        html = render_html(graph)

        assert "nova" in html
        assert "glance" in html

    def test_contains_counts(self) -> None:
        """Test that HTML shows node and edge counts."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["a"] = GraphNode(id="a")
        graph.nodes["b"] = GraphNode(id="b")
        graph.edges.append(GraphEdge("a", "b"))

        html = render_html(graph)

        # Should show total packages and dependencies somewhere
        assert ">2<" in html  # node count
        assert ">1<" in html  # edge count

    def test_escapes_special_characters(self) -> None:
        """Test that special characters are escaped."""
        graph = PlanGraph(
            run_id="test<script>alert(1)</script>",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["<pkg>"] = GraphNode(id="<pkg>")

        html = render_html(graph)

        # Ensure script tags are escaped
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html or "\\u003c" in html.lower()

    def test_simplified_view_for_large_graphs(self) -> None:
        """Test that large graphs use simplified view."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        # Add >400 nodes to trigger simplified mode
        for i in range(450):
            graph.nodes[f"pkg{i}"] = GraphNode(id=f"pkg{i}")

        html = render_html(graph)

        # Should set simplified=true in JavaScript
        assert "simplified = true" in html

    def test_includes_wave_lanes(self) -> None:
        """Test that HTML includes wave swim lanes."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["a"] = GraphNode(id="a", build_type="release", wave=0)
        graph.nodes["b"] = GraphNode(id="b", build_type="snapshot", wave=1)
        graph.waves = {0: ["a"], 1: ["b"]}

        html = render_html(graph)

        assert "wave-lane" in html
        assert "Wave 0" in html

    def test_wave_empty_message(self) -> None:
        """Test that HTML shows empty message when no waves exist."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["a"] = GraphNode(id="a", build_type="release")

        html = render_html(graph)

        assert "wave-empty" in html

    def test_html_includes_dependencies_and_cycles(self) -> None:
        """Test dependency list and cycles rendering."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["nova"] = GraphNode(
            id="nova",
            build_type="release",
            dependencies=["a", "b", "c", "d", "e", "f"],
        )
        graph.nodes["other"] = GraphNode(id="other", build_type="unknown")
        graph.topo_order = ["nova"]
        graph.cycles = [[f"a{i}", f"b{i}", f"a{i}"] for i in range(6)]
        graph.waves = {0: ["missing", "nova"]}

        html = render_html(graph)

        assert "(+1)" in html
        assert "more cycles" in html


class TestWritePlanGraphReports:
    """Tests for write_plan_graph_reports function."""

    def test_writes_both_files(self, tmp_path: Path) -> None:
        """Test that both JSON and HTML files are written."""
        graph = PlanGraph(
            run_id="test-run",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["nova"] = GraphNode(id="nova")

        paths = write_plan_graph_reports(graph, tmp_path)

        assert "json" in paths
        assert "html" in paths
        assert paths["json"].exists()
        assert paths["html"].exists()
        assert paths["json"].name == "plan-graph.json"
        assert paths["html"].name == "plan-graph.html"

    def test_json_has_required_keys(self, tmp_path: Path) -> None:
        """Test that JSON output has all required keys."""
        graph = PlanGraph(
            run_id="test-run",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["nova"] = GraphNode(id="nova", order=0)
        graph.edges.append(GraphEdge("nova", "oslo"))
        graph.topo_order = ["oslo", "nova"]

        paths = write_plan_graph_reports(graph, tmp_path)

        data = json.loads(paths["json"].read_text())

        # Required keys per spec
        assert "run_id" in data
        assert "generated_at_utc" in data
        assert "target" in data
        assert "ubuntu_series" in data
        assert "nodes" in data
        assert "edges" in data
        assert "topo_order" in data
        assert "summary" in data
        assert "node_count" in data["summary"]
        assert "edge_count" in data["summary"]
        assert "cycles" in data["summary"]

    def test_html_has_at_least_one_node_label(self, tmp_path: Path) -> None:
        """Test that HTML includes at least one node label."""
        graph = PlanGraph(
            run_id="test",
            generated_at_utc="2026-01-01T00:00:00Z",
            target="gazpacho",
            ubuntu_series="resolute",
        )
        graph.nodes["my-unique-pkg-name"] = GraphNode(id="my-unique-pkg-name")

        paths = write_plan_graph_reports(graph, tmp_path)

        html_content = paths["html"].read_text()
        assert "my-unique-pkg-name" in html_content
