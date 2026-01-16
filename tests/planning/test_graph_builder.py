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

"""Tests for graph builder module."""

from pathlib import Path
from unittest.mock import MagicMock

from packastack.apt.packages import BinaryPackage, PackageIndex
from packastack.planning.graph import DependencyGraph
from packastack.planning.graph_builder import (
    GraphBuildResult,
    build_graph_from_control,
    build_graph_from_index,
    filter_graph_to_packages,
    merge_graphs,
)


class TestGraphBuildResult:
    """Tests for GraphBuildResult dataclass."""

    def test_default_values(self):
        """Test default values of GraphBuildResult."""
        result = GraphBuildResult(graph=DependencyGraph())
        assert isinstance(result.graph, DependencyGraph)
        assert result.binary_to_source == {}
        assert result.missing_deps == {}
        assert result.mir_candidates == {}
        assert result.excluded_edges == []
        assert result.warnings == []


class TestBuildGraphFromControl:
    """Tests for build_graph_from_control function."""

    def test_empty_packages(self, tmp_path: Path):
        """Test with empty package list."""
        result = build_graph_from_control([], tmp_path)
        assert len(result.graph.nodes) == 0

    def test_missing_control_file(self, tmp_path: Path):
        """Test with missing d/control file."""
        result = build_graph_from_control(["nova"], tmp_path)
        assert "nova" in result.graph.nodes
        assert len(result.warnings) > 0
        assert "not found" in result.warnings[0]

    def test_single_package(self, tmp_path: Path):
        """Test with single package."""
        pkg_dir = tmp_path / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").write_text(
            """Source: nova
Maintainer: Test <test@example.com>
Build-Depends: debhelper-compat (= 13)

Package: nova
Architecture: all
Depends: python3
Description: Test package
"""
        )

        result = build_graph_from_control(["nova"], tmp_path)
        assert "nova" in result.graph.nodes
        assert result.binary_to_source.get("nova") == "nova"

    def test_dependency_between_packages(self, tmp_path: Path):
        """Test dependency edge is created between packages."""
        # Create oslo.config
        oslo_dir = tmp_path / "oslo.config" / "debian"
        oslo_dir.mkdir(parents=True)
        (oslo_dir / "control").write_text(
            """Source: oslo.config
Maintainer: Test <test@example.com>
Build-Depends: debhelper-compat (= 13)

Package: python3-oslo.config
Architecture: all
Depends: python3
Description: Oslo config
"""
        )

        # Create nova that depends on oslo.config
        nova_dir = tmp_path / "nova" / "debian"
        nova_dir.mkdir(parents=True)
        (nova_dir / "control").write_text(
            """Source: nova
Maintainer: Test <test@example.com>
Build-Depends: debhelper-compat (= 13), python3-oslo.config

Package: nova
Architecture: all
Depends: python3
Description: Nova compute
"""
        )

        result = build_graph_from_control(
            ["nova", "oslo.config"], tmp_path
        )

        assert "nova" in result.graph.nodes
        assert "oslo.config" in result.graph.nodes
        # nova should depend on oslo.config
        assert "oslo.config" in result.graph.get_dependencies("nova")

    def test_missing_dependency_reported(self, tmp_path: Path):
        """Test missing build dependency is reported."""
        pkg_dir = tmp_path / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").write_text(
            """Source: nova
Build-Depends: python3-missing

Package: nova
Architecture: all
"""
        )

        result = build_graph_from_control(["nova"], tmp_path)

        assert result.missing_deps == {"nova": ["python3-missing"]}

    def test_archive_dependency_not_missing(self, tmp_path: Path):
        """Test archive dependency is not reported as missing."""
        pkg_dir = tmp_path / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").write_text(
            """Source: nova
Build-Depends: python3-found

Package: nova
Architecture: all
"""
        )

        pkg_index = PackageIndex()
        pkg_index.add_package(
            BinaryPackage(
                name="python3-found",
                version="1.0",
                architecture="all",
                source="found",
            ),
            component="main",
            pocket="release",
        )

        result = build_graph_from_control(["nova"], tmp_path, package_index=pkg_index)

        assert result.missing_deps == {}
        assert len(result.graph.get_dependencies("nova")) == 0

    def test_soft_dependency_exclusion(self, tmp_path: Path):
        """Test soft dependency exclusions prevent edge creation."""
        oslo_log_dir = tmp_path / "python-oslo.log" / "debian"
        oslo_log_dir.mkdir(parents=True)
        (oslo_log_dir / "control").write_text(
            """Source: python-oslo.log
Package: python3-oslo.log
Architecture: all
"""
        )

        oslo_cfg_dir = tmp_path / "python-oslo.config" / "debian"
        oslo_cfg_dir.mkdir(parents=True)
        (oslo_cfg_dir / "control").write_text(
            """Source: python-oslo.config
Build-Depends: python3-oslo.log

Package: python3-oslo.config
Architecture: all
"""
        )

        result = build_graph_from_control(
            ["python-oslo.config", "python-oslo.log"],
            tmp_path,
        )

        assert "python-oslo.log" not in result.graph.get_dependencies("python-oslo.config")
        assert ("python-oslo.config", "python-oslo.log") in result.excluded_edges


class TestBuildGraphFromIndex:
    """Tests for build_graph_from_index function."""

    def test_empty_packages(self):
        """Test with empty package list."""
        mock_index = MagicMock()
        result = build_graph_from_index([], mock_index)
        assert len(result.graph.nodes) == 0

    def test_source_not_found(self):
        """Test when source package not in index."""
        mock_index = MagicMock()
        mock_index.get_binaries_for_source.return_value = []

        result = build_graph_from_index(["nova"], mock_index)
        assert len(result.warnings) > 0
        assert "not found" in result.warnings[0]

    def test_single_package(self):
        """Test with single package in index."""
        mock_index = MagicMock()
        mock_index.get_binaries_for_source.return_value = ["python3-nova"]
        mock_pkg = MagicMock()
        mock_pkg.depends = []
        mock_pkg.pre_depends = []
        mock_index.find_package.return_value = mock_pkg

        result = build_graph_from_index(["nova"], mock_index)
        assert "nova" in result.graph.nodes
        assert result.binary_to_source.get("python3-nova") == "nova"

    def test_transitive_dependencies(self):
        """Test that transitive dependencies are discovered."""
        pkg_index = PackageIndex()
        pkg_index.add_package(
            BinaryPackage(
                name="alpha-bin",
                version="1.0",
                architecture="all",
                source="alpha",
                depends=["beta-bin"],
            ),
            component="main",
            pocket="release",
        )
        pkg_index.add_package(
            BinaryPackage(
                name="beta-bin",
                version="1.0",
                architecture="all",
                source="beta",
                depends=["gamma-bin"],
            ),
            component="main",
            pocket="release",
        )
        pkg_index.add_package(
            BinaryPackage(
                name="gamma-bin",
                version="1.0",
                architecture="all",
                source="gamma",
            ),
            component="main",
            pocket="release",
        )

        result = build_graph_from_index(
            ["alpha"],
            pkg_index,
            openstack_packages={"alpha", "beta", "gamma"},
        )

        assert "alpha" in result.graph.nodes
        assert "beta" in result.graph.nodes
        assert "gamma" in result.graph.nodes
        assert "beta" in result.graph.get_dependencies("alpha")
        assert "gamma" in result.graph.get_dependencies("beta")

    def test_skip_optional_dependencies(self):
        """Test optional dependencies are skipped when configured."""
        pkg_index = PackageIndex()
        pkg_index.add_package(
            BinaryPackage(
                name="alpha-bin",
                version="1.0",
                architecture="all",
                source="alpha",
                depends=["python3-sphinx"],
            ),
            component="main",
            pocket="release",
        )
        pkg_index.add_package(
            BinaryPackage(
                name="python3-sphinx",
                version="7.0",
                architecture="all",
                source="sphinx",
            ),
            component="main",
            pocket="release",
        )

        result = build_graph_from_index(
            ["alpha"],
            pkg_index,
            openstack_packages={"alpha", "sphinx"},
            skip_optional_deps=True,
        )

        assert "alpha" in result.graph.nodes
        assert "sphinx" not in result.graph.nodes
        assert len(result.graph.get_dependencies("alpha")) == 0

    def test_records_excluded_edges(self):
        """Test that excluded edges are recorded."""
        pkg_index = PackageIndex()
        pkg_index.add_package(
            BinaryPackage(
                name="python3-oslo.config",
                version="9.0.0",
                architecture="all",
                source="python-oslo.config",
                depends=["python3-oslo.log"],
            ),
            component="main",
            pocket="release",
        )
        pkg_index.add_package(
            BinaryPackage(
                name="python3-oslo.log",
                version="5.0.0",
                architecture="all",
                source="python-oslo.log",
            ),
            component="main",
            pocket="release",
        )

        result = build_graph_from_index(
            ["python-oslo.config"],
            pkg_index,
            openstack_packages={"python-oslo.config", "python-oslo.log"},
        )

        assert "python-oslo.log" not in result.graph.get_dependencies("python-oslo.config")
        assert ("python-oslo.config", "python-oslo.log") in result.excluded_edges


class TestMergeGraphs:
    """Tests for merge_graphs function."""

    def test_merge_empty_graphs(self):
        """Test merging empty graphs."""
        result = merge_graphs([DependencyGraph(), DependencyGraph()])
        assert len(result.nodes) == 0

    def test_merge_single_graph(self):
        """Test merging a single graph."""
        g = DependencyGraph()
        g.add_node("nova", needs_rebuild=True)
        g.add_node("glance")
        g.add_edge("nova", "glance")

        result = merge_graphs([g])
        assert "nova" in result.nodes
        assert "glance" in result.nodes
        assert "glance" in result.get_dependencies("nova")

    def test_merge_overlapping_graphs(self):
        """Test merging graphs with overlapping nodes."""
        g1 = DependencyGraph()
        g1.add_node("nova", version="1.0.0", needs_rebuild=True)
        g1.add_edge("nova", "oslo.config")

        g2 = DependencyGraph()
        g2.add_node("nova")  # Same node, different properties
        g2.add_node("glance")
        g2.add_edge("nova", "glance")

        result = merge_graphs([g1, g2])

        # Should have all nodes
        assert "nova" in result.nodes
        assert "glance" in result.nodes
        assert "oslo.config" in result.nodes

        # Nova should have merged properties
        assert result.nodes["nova"].needs_rebuild is True
        assert result.nodes["nova"].version == "1.0.0"

        # Should have both edges
        nova_deps = result.get_dependencies("nova")
        assert "oslo.config" in nova_deps
        assert "glance" in nova_deps

    def test_merge_disjoint_graphs(self):
        """Test merging completely disjoint graphs."""
        g1 = DependencyGraph()
        g1.add_node("nova")

        g2 = DependencyGraph()
        g2.add_node("glance")

        result = merge_graphs([g1, g2])
        assert "nova" in result.nodes
        assert "glance" in result.nodes


class TestFilterGraphToPackages:
    """Tests for filter_graph_to_packages function."""

    def test_filter_empty_set(self):
        """Test filtering to empty set."""
        g = DependencyGraph()
        g.add_node("nova")
        g.add_node("glance")

        result = filter_graph_to_packages(g, set())
        assert len(result.nodes) == 0

    def test_filter_all_packages(self):
        """Test filtering to all packages."""
        g = DependencyGraph()
        g.add_node("nova")
        g.add_node("glance")
        g.add_edge("nova", "glance")

        result = filter_graph_to_packages(g, {"nova", "glance"})
        assert "nova" in result.nodes
        assert "glance" in result.nodes
        assert "glance" in result.get_dependencies("nova")

    def test_filter_subset(self):
        """Test filtering to subset of packages."""
        g = DependencyGraph()
        g.add_node("nova")
        g.add_node("glance")
        g.add_node("cinder")
        g.add_edge("nova", "glance")
        g.add_edge("nova", "cinder")
        g.add_edge("glance", "cinder")

        result = filter_graph_to_packages(g, {"nova", "glance"})

        assert "nova" in result.nodes
        assert "glance" in result.nodes
        assert "cinder" not in result.nodes

        # Edge nova->glance should remain
        assert "glance" in result.get_dependencies("nova")
        # Edge nova->cinder should be removed (cinder not in filter)
        assert "cinder" not in result.get_dependencies("nova")

    def test_filter_preserves_node_properties(self):
        """Test that filtering preserves node properties."""
        g = DependencyGraph()
        g.add_node("nova", version="1.0.0", needs_rebuild=True)
        g.nodes["nova"].rebuild_reason = "test reason"

        result = filter_graph_to_packages(g, {"nova"})

        assert result.nodes["nova"].version == "1.0.0"
        assert result.nodes["nova"].needs_rebuild is True
