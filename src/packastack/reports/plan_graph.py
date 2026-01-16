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

"""Build-order graph model and renderers for plan command.

Provides:
- PlanGraph: Data model for build dependency graphs
- render_dot(): DOT/Graphviz output
- render_ascii(): ASCII tree/list output
- render_html(): Self-contained HTML visualization
- render_json(): Machine-readable JSON format
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from packastack.planning.graph import DependencyGraph
    from packastack.planning.type_selection import TypeSelectionReport


@dataclass
class GraphNode:
    """A node in the plan graph representing a source package."""

    id: str
    build_type: str = "snapshot"  # release, milestone, snapshot
    status: str = "ok"  # ok, blocked, cycle
    order: int = -1  # Position in topological order (-1 = not computed)
    wave: int = -1  # Build wave number (-1 = not computed)
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    forced_by: list[str] = field(default_factory=list)  # Critical dependencies

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "type": self.build_type,
            "status": self.status,
            "order": self.order,
            "wave": self.wave,
            "forced_by": self.forced_by,
        }


@dataclass
class GraphEdge:
    """An edge in the plan graph representing a build dependency."""

    from_node: str
    to_node: str
    kind: str = "build-depends"

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "from": self.from_node,
            "to": self.to_node,
            "kind": self.kind,
        }


@dataclass
class PlanGraph:
    """Complete build-order dependency graph for plan visualization."""

    run_id: str
    generated_at_utc: str
    target: str  # OpenStack target series
    ubuntu_series: str
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)
    topo_order: list[str] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    waves: dict[int, list[str]] = field(default_factory=dict)  # wave -> [node_ids]

    @property
    def node_count(self) -> int:
        """Number of nodes in the graph."""
        return len(self.nodes)

    @property
    def wave_count(self) -> int:
        """Number of build waves."""
        return len(self.waves)

    @property
    def edge_count(self) -> int:
        """Number of edges in the graph."""
        return len(self.edges)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        # Convert waves dict to sorted list for JSON
        waves_list = [
            {"wave": wave_num, "packages": nodes}
            for wave_num, nodes in sorted(self.waves.items())
        ]

        return {
            "run_id": self.run_id,
            "generated_at_utc": self.generated_at_utc,
            "target": self.target,
            "ubuntu_series": self.ubuntu_series,
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges],
            "topo_order": self.topo_order,
            "waves": waves_list,
            "summary": {
                "node_count": self.node_count,
                "edge_count": self.edge_count,
                "wave_count": self.wave_count,
                "cycles": len(self.cycles),
            },
        }

    @classmethod
    def from_dependency_graph(
        cls,
        dep_graph: DependencyGraph,
        run_id: str,
        target: str,
        ubuntu_series: str,
        type_report: TypeSelectionReport | None = None,
        cycles: list[list[str]] | None = None,
    ) -> PlanGraph:
        """Create a PlanGraph from a DependencyGraph and optional type selection report.

        Args:
            dep_graph: The dependency graph with nodes and edges.
            run_id: Run identifier.
            target: OpenStack target series.
            ubuntu_series: Ubuntu series.
            type_report: Optional type selection report for build type info.
            cycles: Optional list of detected cycles.

        Returns:
            PlanGraph ready for rendering.
        """
        graph = cls(
            run_id=run_id,
            generated_at_utc=datetime.now(UTC).isoformat(),
            target=target,
            ubuntu_series=ubuntu_series,
            cycles=cycles or [],
        )

        # Build type lookup from report
        type_map: dict[str, str] = {}
        if type_report:
            for pkg in type_report.packages:
                type_map[pkg.source_package] = pkg.chosen_type.value

        # Mark nodes in cycles
        cycle_nodes: set[str] = set()
        for cycle in graph.cycles:
            cycle_nodes.update(cycle)

        # Add nodes
        for name, _node in dep_graph.nodes.items():
            build_type = type_map.get(name, "snapshot")
            status = "cycle" if name in cycle_nodes else "ok"
            graph.nodes[name] = GraphNode(
                id=name,
                build_type=build_type,
                status=status,
                dependencies=sorted(dep_graph.edges.get(name, set())),
                dependents=sorted(dep_graph.reverse_edges.get(name, set())),
            )

        # Add edges
        for from_node, deps in dep_graph.edges.items():
            for to_node in sorted(deps):
                graph.edges.append(GraphEdge(from_node=from_node, to_node=to_node))

        # Compute topological order
        try:
            topo = dep_graph.topological_sort()
            graph.topo_order = topo
            for i, name in enumerate(topo):
                if name in graph.nodes:
                    graph.nodes[name].order = i
        except ValueError:
            # Cycles present, can't compute full order
            graph.topo_order = []

        # Compute waves and forced-by relationships
        node_waves = dep_graph.compute_waves_with_cycles()
        forced_by_map = dep_graph.compute_forced_by(node_waves)

        # Update nodes with wave info and build waves dict
        for name, wave in node_waves.items():
            if name in graph.nodes:
                graph.nodes[name].wave = wave
                graph.nodes[name].forced_by = forced_by_map.get(name, [])
                if wave not in graph.waves:
                    graph.waves[wave] = []
                graph.waves[wave].append(name)

        # Sort nodes within each wave alphabetically
        for wave in graph.waves:
            graph.waves[wave].sort()

        return graph

    def get_subgraph(
        self,
        focus: str,
        depth: int = 2,
    ) -> PlanGraph:
        """Extract a subgraph centered on a focus node.

        Includes ancestors (dependencies) and descendants (dependents) up to
        the specified depth.

        Args:
            focus: The package name to focus on.
            depth: Maximum depth to traverse in each direction.

        Returns:
            A new PlanGraph containing only the subgraph.
        """
        if focus not in self.nodes:
            return PlanGraph(
                run_id=self.run_id,
                generated_at_utc=self.generated_at_utc,
                target=self.target,
                ubuntu_series=self.ubuntu_series,
            )

        included: set[str] = {focus}

        # BFS for dependencies (ancestors)
        frontier = [focus]
        for _ in range(depth):
            next_frontier = []
            for node_id in frontier:
                node = self.nodes.get(node_id)
                if node:
                    for dep in node.dependencies:
                        if dep not in included:
                            included.add(dep)
                            next_frontier.append(dep)
            frontier = next_frontier

        # BFS for dependents (descendants)
        frontier = [focus]
        for _ in range(depth):
            next_frontier = []
            for node_id in frontier:
                node = self.nodes.get(node_id)
                if node:
                    for dep in node.dependents:
                        if dep not in included:
                            included.add(dep)
                            next_frontier.append(dep)
            frontier = next_frontier

        # Build subgraph
        subgraph = PlanGraph(
            run_id=self.run_id,
            generated_at_utc=self.generated_at_utc,
            target=self.target,
            ubuntu_series=self.ubuntu_series,
            cycles=[c for c in self.cycles if any(n in included for n in c)],
        )

        for name in included:
            if name in self.nodes:
                subgraph.nodes[name] = self.nodes[name]

        for edge in self.edges:
            if edge.from_node in included and edge.to_node in included:
                subgraph.edges.append(edge)

        # Preserve order for included nodes
        subgraph.topo_order = [n for n in self.topo_order if n in included]

        return subgraph


def render_waves(
    graph: PlanGraph,
    focus: str | None = None,
    max_wave_packages: int = 20,
) -> str:
    """Render plan graph as build waves (parallelizable batches).

    Args:
        graph: The plan graph to render.
        focus: Optional focus node to show only relevant waves.
        max_wave_packages: Max packages to show per wave before wrapping.

    Returns:
        Formatted waves view string.
    """
    if focus and focus in graph.nodes:
        # Filter to waves relevant to focus (ancestors + descendants)
        relevant_nodes = set()

        def add_ancestors(node_id: str) -> None:
            if node_id in relevant_nodes:
                return
            relevant_nodes.add(node_id)
            if node_id in graph.nodes:
                for dep in graph.nodes[node_id].dependencies:
                    add_ancestors(dep)

        def add_descendants(node_id: str) -> None:
            if node_id in relevant_nodes:
                return
            relevant_nodes.add(node_id)
            if node_id in graph.nodes:
                for dep in graph.nodes[node_id].dependents:
                    add_descendants(dep)

        add_ancestors(focus)
        add_descendants(focus)

        # Filter waves
        filtered_waves = {}
        for wave_num, nodes in graph.waves.items():
            filtered = [n for n in nodes if n in relevant_nodes]
            if filtered:
                filtered_waves[wave_num] = filtered
        waves = filtered_waves
    else:
        waves = graph.waves

    lines: list[str] = []
    lines.append("Build waves (parallelizable batches):")

    if not waves:
        lines.append("  (no waves computed - graph may have cycles)")
        return "\n".join(lines)

    total_packages = sum(len(nodes) for nodes in waves.values())

    for wave_num in sorted(waves.keys()):
        nodes = waves[wave_num]
        count = len(nodes)

        # Format package list and annotate with chosen build type if available
        def _annotate(n: str) -> str:
            node = graph.nodes.get(n)
            if node and node.build_type:
                bt = node.build_type.lower()
                if bt == "snapshot":
                    return f"{n} (s)"
                if bt == "milestone":
                    return f"{n} (m)"
                # default to release
                return f"{n} (r)"
            return n

        if count <= max_wave_packages:
            pkg_list = ", ".join(_annotate(n) for n in nodes)
            lines.append(f"  Wave {wave_num} ({count}): {pkg_list}")
        else:
            # Wrap into multiple lines
            lines.append(f"  Wave {wave_num} ({count}):")
            for i in range(0, count, max_wave_packages):
                chunk = nodes[i:i+max_wave_packages]
                lines.append(f"    {', '.join(_annotate(n) for n in chunk)}")

    lines.append("")
    lines.append(f"Total: {len(waves)} waves, {total_packages} packages, {graph.edge_count} dependencies")

    return "\n".join(lines)


def render_build_order_list(
    graph: PlanGraph,
    focus: str | None = None,
    max_forced_by: int = 3,
) -> str:
    """Render build order as a detailed list with wave and forced-by info.

    Args:
        graph: The plan graph to render.
        focus: Optional focus node to show only relevant packages.
        max_forced_by: Maximum forcing dependencies to show.

    Returns:
        Formatted build order list string.
    """
    if focus and focus in graph.nodes:
        # Get relevant nodes (ancestors + descendants)
        relevant_nodes = set()

        def add_ancestors(node_id: str) -> None:
            if node_id in relevant_nodes:
                return
            relevant_nodes.add(node_id)
            if node_id in graph.nodes:
                for dep in graph.nodes[node_id].dependencies:
                    add_ancestors(dep)

        def add_descendants(node_id: str) -> None:
            if node_id in relevant_nodes:
                return
            relevant_nodes.add(node_id)
            if node_id in graph.nodes:
                for dep in graph.nodes[node_id].dependents:
                    add_descendants(dep)

        add_ancestors(focus)
        add_descendants(focus)

        # Filter topo order
        display_order = [n for n in graph.topo_order if n in relevant_nodes]
    else:
        display_order = graph.topo_order

    lines: list[str] = []
    lines.append("Build order (topologically sorted):")
    lines.append("")

    if not display_order:
        lines.append("  (no build order - graph may have cycles)")
        return "\n".join(lines)

    for i, node_id in enumerate(display_order, 1):
        node = graph.nodes.get(node_id)
        if not node:
            continue

        # Main line: index, wave, package name
        lines.append(f"  {i:03d} [wave {node.wave}] {node_id}")

        # Show forced-by if present
        if node.forced_by:
            shown = node.forced_by[:max_forced_by]

            # Format with wave numbers
            forced_strs = []
            for dep in shown:
                dep_node = graph.nodes.get(dep)
                if dep_node and dep_node.wave >= 0:
                    forced_strs.append(f"{dep} [wave {dep_node.wave}]")
                else:
                    forced_strs.append(dep)

            forced_text = ", ".join(forced_strs)

            # Add overflow indicator
            total_deps = len(node.dependencies)
            if len(node.forced_by) > max_forced_by:
                more = len(node.forced_by) - max_forced_by
                forced_text += f" (+{more} more)"
            elif total_deps > len(node.forced_by):
                other = total_deps - len(node.forced_by)
                forced_text += f" (+{other} deps)"

            lines.append(f"      forced-by: {forced_text}")

    return "\n".join(lines)


def render_json(graph: PlanGraph, output_path: Path) -> Path:
    """Render plan graph as JSON.

    Args:
        graph: The plan graph to render.
        output_path: Path to write the JSON file.

    Returns:
        Path to the written file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(graph.to_dict(), indent=2))
    return output_path


def render_dot(
    graph: PlanGraph,
    focus: str | None = None,
    depth: int = 2,
    max_nodes: int = 200,
) -> str:
    """Render plan graph as DOT (Graphviz) format with wave-based ranking.

    Args:
        graph: The plan graph to render.
        focus: Optional focus node for subgraph extraction.
        depth: Depth for subgraph extraction when focus is set.
        max_nodes: Maximum nodes to include (ignored if focus is set).

    Returns:
        DOT format string.
    """
    if focus:
        graph = graph.get_subgraph(focus, depth)
    elif graph.node_count > max_nodes:
        # Truncate to root nodes and their immediate dependencies
        root_nodes = [n for n in graph.nodes.values() if not n.dependents]
        included = {n.id for n in root_nodes[:max_nodes]}
        for node in root_nodes[:max_nodes]:
            included.update(node.dependencies[:5])  # Limit deps per node

    lines = ["digraph packastack_plan {"]
    lines.append('    rankdir=LR;')
    lines.append('    node [shape=box, style=filled];')
    lines.append('')

    # Color mapping for build types
    type_colors = {
        "release": "#90EE90",  # Light green
        "milestone": "#FFE4B5",  # Moccasin
        "snapshot": "#ADD8E6",  # Light blue
    }

    # Node definitions with attributes
    for node in sorted(graph.nodes.values(), key=lambda n: n.id):
        color = type_colors.get(node.build_type, "#D3D3D3")
        style = "filled"
        if node.status == "cycle":
            color = "#FF6B6B"  # Red for cycle
            style = "filled,bold"
        elif node.status == "blocked":
            color = "#FFB347"  # Orange for blocked
            style = "filled,dashed"

        label = html.escape(node.id)
        if node.wave >= 0:
            label += f"\\nwave {node.wave}"
        lines.append(f'    "{node.id}" [label="{label}", fillcolor="{color}", style="{style}"];')

    lines.append('')

    # Add rank constraints based on waves
    if graph.waves:
        for wave_num in sorted(graph.waves.keys()):
            nodes_in_wave = graph.waves[wave_num]
            if len(nodes_in_wave) > 1:
                node_list = '"; "'.join(nodes_in_wave)
                lines.append(f'    {{ rank=same; "{node_list}"; }}')
        lines.append('')

    # Edge definitions (dependency -> dependent)
    for edge in graph.edges:
        lines.append(f'    "{edge.to_node}" -> "{edge.from_node}";')  # Reverse for build order

    lines.append('}')
    return '\n'.join(lines)


def render_ascii(
    graph: PlanGraph,
    focus: str | None = None,
    depth: int = 2,
    max_nodes: int = 200,
    style: str = "list",
) -> str:
    """Render plan graph as ASCII text.

    Args:
        graph: The plan graph to render.
        focus: Optional focus node for subgraph extraction.
        depth: Depth for subgraph extraction when focus is set.
        max_nodes: Maximum nodes to show.
        style: "list" for ordered list with deps, "tree" for forest view.

    Returns:
        ASCII text representation.
    """
    if focus:
        graph = graph.get_subgraph(focus, depth)

    lines: list[str] = []

    # Header
    lines.append(f"Build Order Graph: {graph.node_count} packages, {graph.edge_count} dependencies")
    lines.append(f"Target: {graph.target} / Ubuntu: {graph.ubuntu_series}")
    lines.append("")

    if graph.cycles:
        lines.append("⚠️  Cycles detected:")
        for cycle in graph.cycles[:3]:
            lines.append(f"    {' -> '.join(cycle)}")
        if len(graph.cycles) > 3:
            lines.append(f"    ... and {len(graph.cycles) - 3} more")
        lines.append("")

    # Check if we need to truncate
    truncated = False
    display_nodes = list(graph.nodes.values())
    if len(display_nodes) > max_nodes and not focus:
        truncated = True
        display_nodes = display_nodes[:max_nodes]

    if style == "tree":
        # Forest view: show root nodes with their dependency trees
        root_nodes = [n for n in graph.nodes.values() if not n.dependents]
        lines.append(f"Root packages ({len(root_nodes)}):")
        lines.append("")

        def print_tree(node_id: str, prefix: str, is_last: bool, visited: set) -> None:
            if node_id in visited or node_id not in graph.nodes:
                return
            visited.add(node_id)

            node = graph.nodes[node_id]
            connector = "└── " if is_last else "├── "
            type_marker = {"release": "R", "milestone": "M", "snapshot": "S"}.get(node.build_type, "?")
            status_marker = "" if node.status == "ok" else f" [{node.status}]"
            lines.append(f"{prefix}{connector}[{type_marker}] {node.id}{status_marker}")

            new_prefix = prefix + ("    " if is_last else "│   ")
            deps = sorted(node.dependencies)
            for i, dep in enumerate(deps[:5]):  # Limit to 5 deps per node
                print_tree(dep, new_prefix, i == len(deps[:5]) - 1, visited)
            if len(deps) > 5:
                lines.append(f"{new_prefix}    ... and {len(deps) - 5} more deps")

        visited: set[str] = set()
        for i, node in enumerate(sorted(root_nodes, key=lambda n: n.id)[:20]):
            print_tree(node.id, "", i == len(root_nodes[:20]) - 1, visited)

        if len(root_nodes) > 20:
            lines.append(f"\n... and {len(root_nodes) - 20} more root packages")

    else:
        # List view: ordered list with immediate dependencies
        lines.append("Build order:")
        lines.append("")

        order_list = graph.topo_order or sorted(graph.nodes.keys())

        for i, node_id in enumerate(order_list[:max_nodes]):
            node = graph.nodes.get(node_id)
            if not node:
                continue

            type_marker = {"release": "R", "milestone": "M", "snapshot": "S"}.get(node.build_type, "?")
            status_marker = "" if node.status == "ok" else f" [{node.status}]"
            deps_str = ""
            if node.dependencies:
                deps = sorted(node.dependencies)[:3]
                deps_str = f" <- {', '.join(deps)}"
                if len(node.dependencies) > 3:
                    deps_str += f" (+{len(node.dependencies) - 3})"

            lines.append(f"  {i + 1:3d}. [{type_marker}] {node.id}{status_marker}{deps_str}")

    if truncated:
        lines.append("")
        lines.append(f"⚠️  Output truncated to {max_nodes} nodes. Use --graph-focus or see HTML report for full graph.")

    lines.append("")
    lines.append("Legend: [R]=Release [M]=Milestone [S]=Snapshot")

    return '\n'.join(lines)


def render_html(graph: PlanGraph) -> str:
    """Render plan graph as self-contained HTML with interactive visualization.

    Args:
        graph: The plan graph to render.

    Returns:
        Complete HTML document as string.
    """

    def esc(s: str) -> str:
        return html.escape(str(s))

    # Determine if we need simplified view for large graphs
    simplified = graph.node_count > 400

    # Generate node data for JavaScript
    nodes_json = json.dumps([
        {
            "id": n.id,
            "type": n.build_type,
            "status": n.status,
            "order": n.order,
            "deps": n.dependencies,
            "dependents": n.dependents,
        }
        for n in graph.nodes.values()
    ])

    edges_json = json.dumps([
        {"from": e.from_node, "to": e.to_node}
        for e in graph.edges
    ])

    topo_json = json.dumps(graph.topo_order)

    # Count by type
    type_counts = {"release": 0, "milestone": 0, "snapshot": 0}
    for node in graph.nodes.values():
        if node.build_type in type_counts:
            type_counts[node.build_type] += 1

    # Build the build order list HTML
    order_rows = []
    order_list = graph.topo_order if graph.topo_order else sorted(graph.nodes.keys())
    for i, node_id in enumerate(order_list):
        node = graph.nodes.get(node_id)
        if not node:
            continue
        deps_html = ""
        if node.dependencies:
            deps = [f'<span class="dep-link" data-pkg="{esc(d)}">{esc(d)}</span>' for d in sorted(node.dependencies)[:5]]
            deps_html = ", ".join(deps)
            if len(node.dependencies) > 5:
                deps_html += f" <em>(+{len(node.dependencies) - 5})</em>"

        status_class = f"status-{node.status}" if node.status != "ok" else ""
        type_class = f"type-{node.build_type}"
        order_rows.append(f'''
        <tr class="{type_class} {status_class}" data-pkg="{esc(node.id)}">
            <td class="order-num">{i + 1}</td>
            <td class="pkg-name"><span class="node-link" data-pkg="{esc(node.id)}">{esc(node.id)}</span></td>
            <td class="pkg-type"><span class="type-badge {type_class}">{esc(node.build_type)}</span></td>
            <td class="pkg-deps">{deps_html}</td>
        </tr>''')

    order_table = '\n'.join(order_rows)

    # Cycles warning HTML
    cycles_html = ""
    if graph.cycles:
        cycle_items = []
        for cycle in graph.cycles[:5]:
            cycle_items.append(f"<li>{' → '.join(esc(n) for n in cycle)}</li>")
        if len(graph.cycles) > 5:
            cycle_items.append(f"<li><em>... and {len(graph.cycles) - 5} more cycles</em></li>")
        cycles_html = f'''
        <div class="warning-box">
            <h3>⚠️ Dependency Cycles Detected</h3>
            <ul>{''.join(cycle_items)}</ul>
        </div>
        '''

    # Build waves HTML (swim lanes)
    if graph.waves:
        wave_rows = []
        for wave_num in sorted(graph.waves.keys()):
            nodes = graph.waves[wave_num]
            pills = []
            for node_id in nodes:
                node = graph.nodes.get(node_id)
                if not node:
                    continue
                type_class = f"type-{node.build_type}"
                pills.append(
                    f'<span class="wave-pill {type_class} node-link" data-pkg="{esc(node.id)}">{esc(node.id)}</span>'
                )
            wave_rows.append(f'''
            <div class="wave-lane">
                <div class="wave-label">Wave {wave_num} <span class="wave-count">({len(nodes)})</span></div>
                <div class="wave-packages">{''.join(pills)}</div>
            </div>
            ''')
        wave_lanes = ''.join(wave_rows)
    else:
        wave_lanes = '<div class="wave-empty">(no waves computed - graph may have cycles)</div>'

    waves_html = f'''
        <div class="panel panel-full">
            <div class="panel-header">
                <span>Build Waves ({graph.wave_count} waves)</span>
            </div>
            <div class="panel-body">
                <div class="wave-lanes">
                    {wave_lanes}
                </div>
            </div>
        </div>
    '''

    # SVG dimensions for graph visualization
    min(1200, max(800, graph.node_count * 8))
    svg_height = min(800, max(400, graph.node_count * 4))

    html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Build Order Graph - {esc(graph.run_id)}</title>
    <style>
        :root {{
            --color-release: #90EE90;
            --color-milestone: #FFE4B5;
            --color-snapshot: #ADD8E6;
            --color-cycle: #FF6B6B;
            --color-blocked: #FFB347;
            --color-bg: #f5f5f5;
            --color-border: #ddd;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, sans-serif;
            margin: 0;
            padding: 20px;
            background: var(--color-bg);
            color: #333;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        header {{
            background: #2c3e50;
            color: white;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
        }}
        header h1 {{ margin: 0 0 10px 0; font-size: 1.5em; }}
        .meta {{ display: flex; gap: 20px; flex-wrap: wrap; font-size: 0.9em; opacity: 0.9; }}
        .meta-item {{ display: flex; gap: 5px; }}
        .meta-label {{ font-weight: bold; }}

        .summary-cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }}
        .card {{
            background: white;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .card-title {{ font-size: 0.85em; color: #666; margin-bottom: 5px; }}
        .card-value {{ font-size: 1.8em; font-weight: bold; }}
        .card-release {{ border-left: 4px solid var(--color-release); }}
        .card-milestone {{ border-left: 4px solid var(--color-milestone); }}
        .card-snapshot {{ border-left: 4px solid var(--color-snapshot); }}

        .warning-box {{
            background: #fff3cd;
            border: 1px solid #ffc107;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 20px;
        }}
        .warning-box h3 {{ margin: 0 0 10px 0; color: #856404; }}
        .warning-box ul {{ margin: 0; padding-left: 20px; }}

        .panel-full {{ margin-bottom: 20px; }}
        .panel-full .panel-body {{ max-height: none; }}

        .panels {{
            display: grid;
            grid-template-columns: {'1fr' if simplified else '1fr 1fr'};
            gap: 20px;
        }}
        .panel {{
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        .panel-header {{
            background: #34495e;
            color: white;
            padding: 12px 15px;
            font-weight: bold;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .panel-body {{ padding: 15px; max-height: 600px; overflow-y: auto; }}

        .search-box {{
            width: 100%;
            padding: 8px 12px;
            border: 1px solid var(--color-border);
            border-radius: 4px;
            margin-bottom: 10px;
            font-size: 0.9em;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.85em;
        }}
        th, td {{
            padding: 8px 10px;
            text-align: left;
            border-bottom: 1px solid var(--color-border);
        }}
        th {{ background: #f8f9fa; font-weight: 600; }}
        tr:hover {{ background: #f0f7ff; }}
        tr.highlight {{ background: #fff3cd !important; }}

        .order-num {{ width: 50px; text-align: center; color: #666; }}
        .pkg-name {{ font-family: monospace; }}
        .pkg-type {{ width: 100px; }}
        .pkg-deps {{ font-size: 0.85em; color: #666; }}

        .type-badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 0.8em;
            font-weight: 500;
        }}
        .type-release {{ background: var(--color-release); color: #155724; }}
        .type-milestone {{ background: var(--color-milestone); color: #856404; }}
        .type-snapshot {{ background: var(--color-snapshot); color: #0c5460; }}

        .wave-lanes {{
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        .wave-lane {{
            display: grid;
            grid-template-columns: 120px 1fr;
            gap: 12px;
            align-items: start;
            padding: 8px 10px;
            border: 1px dashed var(--color-border);
            border-radius: 6px;
            background: #fafafa;
        }}
        .wave-lane:nth-child(even) {{
            background: #f1f5f9;
        }}
        .wave-label {{
            font-weight: 600;
            color: #444;
        }}
        .wave-count {{
            font-weight: 400;
            color: #666;
            font-size: 0.85em;
        }}
        .wave-packages {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }}
        .wave-pill {{
            font-family: monospace;
            font-size: 0.8em;
            padding: 2px 6px;
            border-radius: 4px;
            border: 1px solid #d7dce1;
            background: #eef2f7;
            cursor: pointer;
        }}
        .wave-pill.type-release {{ background: var(--color-release); color: #155724; border-color: #9fd59f; }}
        .wave-pill.type-milestone {{ background: var(--color-milestone); color: #856404; border-color: #e8c98b; }}
        .wave-pill.type-snapshot {{ background: var(--color-snapshot); color: #0c5460; border-color: #9bc8da; }}
        .wave-empty {{ color: #666; font-style: italic; }}

        .status-cycle td {{ background: #ffe0e0; }}
        .status-blocked td {{ background: #fff0e0; }}

        .node-link, .dep-link {{
            cursor: pointer;
            color: #007bff;
            text-decoration: underline;
        }}
        .node-link:hover, .dep-link:hover {{ color: #0056b3; }}

        .legend {{
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
            padding: 10px 15px;
            background: #f8f9fa;
            border-top: 1px solid var(--color-border);
            font-size: 0.85em;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 5px;
        }}
        .legend-color {{
            width: 16px;
            height: 16px;
            border-radius: 3px;
            border: 1px solid #999;
        }}

        #graph-svg {{
            width: 100%;
            height: {svg_height}px;
            border: 1px solid var(--color-border);
            border-radius: 4px;
            background: white;
        }}
        .node-circle {{
            cursor: pointer;
            transition: r 0.2s;
        }}
        .node-circle:hover {{ r: 8; }}
        .node-label {{
            font-size: 10px;
            pointer-events: none;
            fill: #333;
        }}
        .edge-line {{
            stroke: #999;
            stroke-opacity: 0.6;
            fill: none;
        }}
        .edge-line.highlight {{
            stroke: #007bff;
            stroke-opacity: 1;
            stroke-width: 2;
        }}

        footer {{
            margin-top: 20px;
            text-align: center;
            font-size: 0.8em;
            color: #666;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📦 Build Order Graph</h1>
            <div class="meta">
                <div class="meta-item"><span class="meta-label">Run ID:</span> {esc(graph.run_id)}</div>
                <div class="meta-item"><span class="meta-label">Target:</span> {esc(graph.target)}</div>
                <div class="meta-item"><span class="meta-label">Ubuntu:</span> {esc(graph.ubuntu_series)}</div>
                <div class="meta-item"><span class="meta-label">Generated:</span> {esc(graph.generated_at_utc)}</div>
            </div>
        </header>

        <div class="summary-cards">
            <div class="card">
                <div class="card-title">Total Packages</div>
                <div class="card-value">{graph.node_count}</div>
            </div>
            <div class="card">
                <div class="card-title">Dependencies</div>
                <div class="card-value">{graph.edge_count}</div>
            </div>
            <div class="card card-release">
                <div class="card-title">Release</div>
                <div class="card-value">{type_counts['release']}</div>
            </div>
            <div class="card card-milestone">
                <div class="card-title">Milestone</div>
                <div class="card-value">{type_counts['milestone']}</div>
            </div>
            <div class="card card-snapshot">
                <div class="card-title">Snapshot</div>
                <div class="card-value">{type_counts['snapshot']}</div>
            </div>
        </div>

        {cycles_html}

        {waves_html}

        <div class="panels">
            <div class="panel">
                <div class="panel-header">
                    <span>Build Order ({len(order_list)} packages)</span>
                </div>
                <div class="panel-body">
                    <input type="text" class="search-box" id="search-input" placeholder="Search packages...">
                    <table id="order-table">
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>Package</th>
                                <th>Type</th>
                                <th>Dependencies</th>
                            </tr>
                        </thead>
                        <tbody>
                            {order_table}
                        </tbody>
                    </table>
                </div>
                <div class="legend">
                    <div class="legend-item"><div class="legend-color" style="background: var(--color-release)"></div> Release</div>
                    <div class="legend-item"><div class="legend-color" style="background: var(--color-milestone)"></div> Milestone</div>
                    <div class="legend-item"><div class="legend-color" style="background: var(--color-snapshot)"></div> Snapshot</div>
                    <div class="legend-item"><div class="legend-color" style="background: var(--color-cycle)"></div> Cycle</div>
                </div>
            </div>

            {'<div class="panel"><div class="panel-header"><span>Dependency Graph</span></div><div class="panel-body"><svg id="graph-svg"></svg></div></div>' if not simplified else ''}
        </div>

        <footer>
            Generated by Packastack | Run: {esc(graph.run_id)}
        </footer>
    </div>

    <script>
        // Graph data
        const nodes = {nodes_json};
        const edges = {edges_json};
        const topoOrder = {topo_json};
        const simplified = {'true' if simplified else 'false'};

        // Search functionality
        const searchInput = document.getElementById('search-input');
        const orderTable = document.getElementById('order-table');

        searchInput.addEventListener('input', function() {{
            const query = this.value.toLowerCase();
            const rows = orderTable.querySelectorAll('tbody tr');
            rows.forEach(row => {{
                const pkg = row.dataset.pkg.toLowerCase();
                row.style.display = pkg.includes(query) ? '' : 'none';
            }});
        }});

        // Click handlers for package links
        document.querySelectorAll('.node-link, .dep-link').forEach(el => {{
            el.addEventListener('click', function() {{
                const pkg = this.dataset.pkg;
                highlightPackage(pkg);
            }});
        }});

        function highlightPackage(pkg) {{
            // Clear previous highlights
            document.querySelectorAll('tr.highlight').forEach(el => el.classList.remove('highlight'));

            // Highlight the row
            const row = document.querySelector(`tr[data-pkg="${{pkg}}"]`);
            if (row) {{
                row.classList.add('highlight');
                row.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
            }}

            // Highlight in SVG if exists
            if (!simplified) {{
                highlightSvgNode(pkg);
            }}
        }}

        // SVG Graph rendering (only for non-simplified view)
        if (!simplified && nodes.length > 0) {{
            const svg = document.getElementById('graph-svg');
            const width = svg.clientWidth || 800;
            const height = svg.clientHeight || 400;

            // Simple force-directed layout
            const nodeMap = {{}};
            nodes.forEach((n, i) => {{
                nodeMap[n.id] = {{
                    ...n,
                    x: Math.random() * (width - 100) + 50,
                    y: Math.random() * (height - 100) + 50,
                    vx: 0,
                    vy: 0
                }};
            }});

            // If we have topo order, use it for initial x positions
            if (topoOrder.length > 0) {{
                const step = (width - 100) / Math.max(1, topoOrder.length - 1);
                topoOrder.forEach((id, i) => {{
                    if (nodeMap[id]) {{
                        nodeMap[id].x = 50 + i * step;
                        nodeMap[id].y = height / 2 + (Math.random() - 0.5) * 100;
                    }}
                }});
            }}

            // Simple force simulation (limited iterations for performance)
            const iterations = Math.min(100, Math.max(20, 500 / nodes.length));
            for (let iter = 0; iter < iterations; iter++) {{
                // Repulsion between nodes
                for (let i = 0; i < nodes.length; i++) {{
                    const n1 = nodeMap[nodes[i].id];
                    for (let j = i + 1; j < nodes.length; j++) {{
                        const n2 = nodeMap[nodes[j].id];
                        const dx = n2.x - n1.x;
                        const dy = n2.y - n1.y;
                        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
                        const force = 500 / (dist * dist);
                        n1.vx -= dx / dist * force;
                        n1.vy -= dy / dist * force;
                        n2.vx += dx / dist * force;
                        n2.vy += dy / dist * force;
                    }}
                }}

                // Attraction along edges
                edges.forEach(e => {{
                    const n1 = nodeMap[e.from];
                    const n2 = nodeMap[e.to];
                    if (n1 && n2) {{
                        const dx = n2.x - n1.x;
                        const dy = n2.y - n1.y;
                        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
                        const force = dist * 0.01;
                        n1.vx += dx * force;
                        n1.vy += dy * force;
                        n2.vx -= dx * force;
                        n2.vy -= dy * force;
                    }}
                }});

                // Apply velocities with damping
                Object.values(nodeMap).forEach(n => {{
                    n.x += n.vx * 0.1;
                    n.y += n.vy * 0.1;
                    n.vx *= 0.9;
                    n.vy *= 0.9;
                    // Keep in bounds
                    n.x = Math.max(20, Math.min(width - 20, n.x));
                    n.y = Math.max(20, Math.min(height - 20, n.y));
                }});
            }}

            // Render SVG
            let svgContent = '';

            // Edges first (behind nodes)
            edges.forEach(e => {{
                const n1 = nodeMap[e.from];
                const n2 = nodeMap[e.to];
                if (n1 && n2) {{
                    svgContent += `<line class="edge-line" data-from="${{e.from}}" data-to="${{e.to}}" x1="${{n1.x}}" y1="${{n1.y}}" x2="${{n2.x}}" y2="${{n2.y}}" />`;
                }}
            }});

            // Nodes
            const typeColors = {{
                'release': '#90EE90',
                'milestone': '#FFE4B5',
                'snapshot': '#ADD8E6'
            }};
            const statusColors = {{
                'cycle': '#FF6B6B',
                'blocked': '#FFB347'
            }};

            Object.values(nodeMap).forEach(n => {{
                const color = n.status !== 'ok' ? statusColors[n.status] : typeColors[n.type] || '#D3D3D3';
                svgContent += `<circle class="node-circle" data-pkg="${{n.id}}" cx="${{n.x}}" cy="${{n.y}}" r="6" fill="${{color}}" stroke="#333" stroke-width="1" />`;
                // Only show labels for nodes with few connections or in small graphs
                if (nodes.length < 50 || n.deps.length === 0 || n.dependents.length === 0) {{
                    svgContent += `<text class="node-label" x="${{n.x + 8}}" y="${{n.y + 3}}">${{n.id}}</text>`;
                }}
            }});

            svg.innerHTML = svgContent;

            // SVG click handlers
            svg.querySelectorAll('.node-circle').forEach(circle => {{
                circle.addEventListener('click', function() {{
                    highlightPackage(this.dataset.pkg);
                }});
            }});

            window.highlightSvgNode = function(pkg) {{
                // Reset all
                svg.querySelectorAll('.edge-line').forEach(l => l.classList.remove('highlight'));
                svg.querySelectorAll('.node-circle').forEach(c => c.setAttribute('r', '6'));

                // Highlight node
                const node = svg.querySelector(`.node-circle[data-pkg="${{pkg}}"]`);
                if (node) {{
                    node.setAttribute('r', '10');
                }}

                // Highlight connected edges
                svg.querySelectorAll(`.edge-line[data-from="${{pkg}}"], .edge-line[data-to="${{pkg}}"]`).forEach(l => {{
                    l.classList.add('highlight');
                }});
            }};
        }}
    </script>
</body>
</html>'''

    return html_content


def write_plan_graph_reports(
    graph: PlanGraph,
    output_dir: Path,
) -> dict[str, Path]:
    """Write all plan graph reports to the output directory.

    Args:
        graph: The plan graph to render.
        output_dir: Directory to write reports to.

    Returns:
        Dictionary mapping report type to file path.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {}

    # JSON report
    json_path = output_dir / "plan-graph.json"
    render_json(graph, json_path)
    paths["json"] = json_path

    # HTML report
    html_path = output_dir / "plan-graph.html"
    html_content = render_html(graph)
    html_path.write_text(html_content)
    paths["html"] = html_path

    return paths
