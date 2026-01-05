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

"""Tests for the plan command module."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from packastack.cli import app
from packastack.commands.plan import (
    EXIT_CONFIG_ERROR,
    EXIT_CYCLE_DETECTED,
    EXIT_MISSING_PACKAGES,
    EXIT_SUCCESS,
    ResolvedTarget,
    _format_graph,
    _source_package_to_deliverable,
    SOFT_DEPENDENCY_EXCLUSIONS,
    _build_dependency_graph,
    _check_mir_candidates,
    _parse_dep_name,
    _resolve_package_targets,
)
from packastack.planning.graph import DependencyGraph
from packastack.apt.packages import BinaryPackage, PackageIndex

if TYPE_CHECKING:
    pass

runner = CliRunner()


def _make_resolved_target(pkg: str, upstream: str | None = None, source: str = "local") -> ResolvedTarget:
    """Create a ResolvedTarget for testing."""
    return ResolvedTarget(
        source_package=pkg,
        upstream_project=upstream or pkg,
        resolution_source=source,
    )


class TestParseDependencyName:
    """Tests for _parse_dep_name helper."""

    def test_simple_name(self) -> None:
        """Test parsing a simple package name."""
        name, rel, ver = _parse_dep_name("python3-foo")
        assert name == "python3-foo"
        assert rel == ""
        assert ver == ""

    def test_with_version_constraint(self) -> None:
        """Test parsing name with version constraint."""
        name, rel, ver = _parse_dep_name("python3-oslo.config (>= 1.0.0)")
        assert name == "python3-oslo.config"
        assert rel == ">="
        assert ver == "1.0.0"

    def test_with_any_qualifier(self) -> None:
        """Test parsing name with :any qualifier."""
        name, rel, ver = _parse_dep_name("libfoo:any (>= 2.0)")
        assert name == "libfoo"
        assert rel == ">="
        assert ver == "2.0"

    def test_with_native_qualifier(self) -> None:
        """Test parsing name with :native qualifier."""
        name, rel, ver = _parse_dep_name("python3:native")
        assert name == "python3"
        assert rel == ""
        assert ver == ""

    def test_with_arch_qualifier(self) -> None:
        """Test parsing name with architecture qualifier."""
        name, _rel, _ver = _parse_dep_name("libfoo [amd64]")
        assert name == "libfoo"

    def test_equal_version(self) -> None:
        """Test parsing with = version."""
        name, rel, ver = _parse_dep_name("pkg (= 1.0)")
        assert name == "pkg"
        assert rel == "="
        assert ver == "1.0"

    def test_less_than(self) -> None:
        """Test parsing with << version."""
        name, rel, ver = _parse_dep_name("pkg (<< 2.0)")
        assert name == "pkg"
        assert rel == "<<"
        assert ver == "2.0"

    def test_greater_than(self) -> None:
        """Test parsing with >> version."""
        name, rel, ver = _parse_dep_name("pkg (>> 1.0)")
        assert name == "pkg"
        assert rel == ">>"
        assert ver == "1.0"


class TestFormatGraph:
    """Tests for _format_graph helper."""

    def test_formats_adjacency(self) -> None:
        graph = DependencyGraph()
        graph.add_node("nova")
        graph.add_node("oslo-config")
        graph.add_edge("nova", "oslo-config")

        lines = _format_graph(graph)

        assert lines[0] == "Dependency graph:"
        # Order is sorted: nova then oslo-config
        assert "nova: oslo-config" in lines
        assert "oslo-config: (no deps)" in lines


class TestCheckMirCandidates:
    """Tests for _check_mir_candidates helper."""

    def test_main_component_returns_none(self) -> None:
        """Test package in main returns None."""
        index = PackageIndex()
        index.packages["libfoo"] = BinaryPackage(
            name="libfoo",
            version="1.0",
            architecture="amd64",
            component="main",
        )
        index.sources["libfoo"] = ["libfoo"]

        run = MagicMock()
        result = _check_mir_candidates("libfoo", index, run)

        assert result is None
        run.log_event.assert_not_called()

    def test_universe_returns_component(self) -> None:
        """Test package in universe returns component name."""
        index = PackageIndex()
        index.packages["libbar"] = BinaryPackage(
            name="libbar",
            version="1.0",
            architecture="amd64",
            component="universe",
        )

        run = MagicMock()
        result = _check_mir_candidates("libbar", index, run)

        assert result == "universe"
        run.log_event.assert_called_once()

    def test_unknown_package_returns_none(self) -> None:
        """Test unknown package returns None."""
        index = PackageIndex()
        run = MagicMock()

        result = _check_mir_candidates("unknown", index, run)

        assert result is None


class TestResolvePackageTargets:
    """Tests for _resolve_package_targets helper."""

    def test_local_match(self, tmp_path: Path) -> None:
        """Test finding package in local repo."""
        # Set up a local package
        pkg_dir = tmp_path / "local" / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").write_text("Source: nova\n")

        run = MagicMock()
        result = _resolve_package_targets(
            common_name="nova",
            local_repo=tmp_path / "local",
            releases_repo=tmp_path / "releases",
            registry=None,
            openstack_target="2024.2",
            use_local=True,
            run=run,
        )

        assert any(r.source_package == "nova" for r in result)

    def test_prefix_match_local(self, tmp_path: Path) -> None:
        """Test finding packages by prefix in local repo."""
        # Set up multiple oslo packages - use correct naming with prefix match
        for pkg in ["oslo-config", "oslo-log", "oslo-messaging"]:
            pkg_dir = tmp_path / "local" / pkg / "debian"
            pkg_dir.mkdir(parents=True)
            (pkg_dir / "control").write_text(f"Source: {pkg}\n")

        run = MagicMock()
        result = _resolve_package_targets(
            common_name="oslo",
            local_repo=tmp_path / "local",
            releases_repo=tmp_path / "releases",
            registry=None,
            openstack_target="2024.2",
            use_local=True,
            run=run,
        )

        # All oslo packages should match (oslo-config, oslo-log, etc.)
        assert len(result) >= 3

    def test_skip_local(self, tmp_path: Path) -> None:
        """Test skipping local repo search."""
        pkg_dir = tmp_path / "local" / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").write_text("Source: nova\n")

        run = MagicMock()
        result = _resolve_package_targets(
            common_name="nova",
            local_repo=tmp_path / "local",
            releases_repo=tmp_path / "releases",  # Doesn't exist, will fail
            registry=None,
            openstack_target="2024.2",
            use_local=False,
            run=run,
        )

        # Local was skipped, releases doesn't exist
        assert result == []

    @patch("packastack.commands.plan.load_project_releases")
    def test_fallback_to_releases(
        self, mock_load: MagicMock, tmp_path: Path
    ) -> None:
        """Test fallback to openstack/releases."""
        mock_load.return_value = MagicMock()  # Simulate project found

        run = MagicMock()
        result = _resolve_package_targets(
            common_name="keystone",
            local_repo=tmp_path / "local",  # Empty
            releases_repo=tmp_path / "releases",
            registry=None,
            openstack_target="2024.2",
            use_local=True,
            run=run,
        )

        assert any(r.source_package == "keystone" for r in result)


class TestBuildDependencyGraph:
    """Tests for _build_dependency_graph helper - uses Ubuntu package index."""

    def test_builds_graph_from_ubuntu_index(self, tmp_path: Path) -> None:
        """Test building graph from Ubuntu package index (Packages.gz data)."""
        # Create a local control file to mark package as needing rebuild
        pkg_dir = tmp_path / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").write_text("Source: nova\n")

        # Set up Ubuntu index with binary packages
        ubuntu_index = PackageIndex()
        ubuntu_index.packages["python3-nova"] = BinaryPackage(
            name="python3-nova",
            version="1.0.0",
            architecture="all",
            source="nova",
            depends=["python3-oslo.config"],
        )
        ubuntu_index.sources["nova"] = ["python3-nova"]

        run = MagicMock()

        graph, _mir = _build_dependency_graph(
            targets=["nova"],
            local_repo=tmp_path,
            local_index=None,
            ubuntu_index=ubuntu_index,
            run=run,
            offline=True,
        )

        assert "nova" in graph.nodes
        assert graph.nodes["nova"].needs_rebuild is True

    def test_skip_missing_source(self, tmp_path: Path) -> None:
        """Test that packages not in Ubuntu index are skipped."""
        ubuntu_index = PackageIndex()
        run = MagicMock()

        graph, _mir = _build_dependency_graph(
            targets=["nonexistent"],
            local_repo=tmp_path,
            local_index=None,
            ubuntu_index=ubuntu_index,
            run=run,
            offline=True,
        )

        assert "nonexistent" not in graph.nodes

    def test_detects_mir_candidates(self, tmp_path: Path) -> None:
        """Test detection of MIR candidates from Ubuntu index."""
        # Create local control to mark as OpenStack package
        pkg_dir = tmp_path / "myapp" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").write_text("Source: myapp\n")

        ubuntu_index = PackageIndex()
        # Source package with binary that depends on universe package
        ubuntu_index.packages["myapp"] = BinaryPackage(
            name="myapp",
            version="1.0",
            architecture="all",
            source="myapp",
            depends=["universe-lib"],
        )
        ubuntu_index.sources["myapp"] = ["myapp"]

        # The dependency in universe
        ubuntu_index.packages["universe-lib"] = BinaryPackage(
            name="universe-lib",
            version="1.0",
            architecture="all",
            source="universe-lib-src",
            component="universe",
        )

        run = MagicMock()

        _graph, mir = _build_dependency_graph(
            targets=["myapp"],
            local_repo=tmp_path,
            local_index=None,
            ubuntu_index=ubuntu_index,
            run=run,
            offline=True,
        )

        assert "myapp" in mir
        assert any("universe" in dep for dep in mir["myapp"])

    def test_handles_missing_source_in_index(self, tmp_path: Path) -> None:
        """Test that source packages not in index are skipped."""
        # Local control exists but source not in ubuntu_index.sources
        pkg_dir = tmp_path / "unknown" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").write_text("Source: unknown\n")

        ubuntu_index = PackageIndex()
        run = MagicMock()

        graph, _mir = _build_dependency_graph(
            targets=["unknown"],
            local_repo=tmp_path,
            local_index=None,
            ubuntu_index=ubuntu_index,
            run=run,
            offline=True,
        )

        # Package should be skipped since not in index
        assert "unknown" not in graph.nodes

    def test_adds_edge_for_local_source(self, tmp_path: Path) -> None:
        """Test that edges are added for locally available source packages."""
        # Create main package
        pkg_dir = tmp_path / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").write_text("Source: nova\n")

        # Create dependency package
        oslo_dir = tmp_path / "oslo-config" / "debian"
        oslo_dir.mkdir(parents=True)
        (oslo_dir / "control").write_text("Source: oslo-config\n")

        ubuntu_index = PackageIndex()
        # Nova binary depends on oslo-config binary
        ubuntu_index.packages["python3-nova"] = BinaryPackage(
            name="python3-nova",
            version="2024.1",
            architecture="all",
            source="nova",
            depends=["python3-oslo-config"],
        )
        ubuntu_index.sources["nova"] = ["python3-nova"]
        # Oslo-config binary
        ubuntu_index.packages["python3-oslo-config"] = BinaryPackage(
            name="python3-oslo-config",
            version="9.0.0",
            architecture="all",
            source="oslo-config",
        )
        ubuntu_index.sources["oslo-config"] = ["python3-oslo-config"]

        run = MagicMock()

        graph, _mir = _build_dependency_graph(
            targets=["nova"],
            local_repo=tmp_path,
            local_index=None,
            ubuntu_index=ubuntu_index,
            run=run,
            offline=True,
        )

        # Both packages should be in graph
        assert "nova" in graph.nodes
        assert "oslo-config" in graph.nodes
        # Edge from nova to oslo-config
        assert "oslo-config" in graph.edges.get("nova", set())

    def test_adds_edge_for_openstack_package_from_releases(self, tmp_path: Path) -> None:
        """Test that edges are added for OpenStack packages found in releases repo."""
        # Create releases repo with project yaml files
        releases_dir = tmp_path / "releases" / "deliverables" / "2024.2"
        releases_dir.mkdir(parents=True)
        # nova is a service (no prefix)
        (releases_dir / "nova.yaml").write_text("type: service\n")
        # oslo.config is a library (python- prefix)
        (releases_dir / "oslo.config.yaml").write_text("type: library\n")

        # Set up Ubuntu index with binary packages
        ubuntu_index = PackageIndex()
        ubuntu_index.packages["python3-nova"] = BinaryPackage(
            name="python3-nova",
            version="2024.1",
            architecture="all",
            source="nova",
            depends=["python3-oslo.config"],
        )
        ubuntu_index.sources["nova"] = ["python3-nova"]
        # Oslo.config binary - note source is python-oslo.config for libraries
        ubuntu_index.packages["python3-oslo.config"] = BinaryPackage(
            name="python3-oslo.config",
            version="9.0.0",
            architecture="all",
            source="python-oslo.config",
        )
        ubuntu_index.sources["python-oslo.config"] = ["python3-oslo.config"]

        run = MagicMock()

        graph, _mir = _build_dependency_graph(
            targets=["nova"],
            local_repo=tmp_path / "local",  # Empty, no local packages
            local_index=None,
            ubuntu_index=ubuntu_index,
            run=run,
            releases_repo=tmp_path / "releases",
            openstack_series="2024.2",
            offline=True,
        )

        # Both packages should be in graph via releases repo lookup
        assert "nova" in graph.nodes
        assert "python-oslo.config" in graph.nodes
        # Edge from nova to python-oslo.config
        assert "python-oslo.config" in graph.edges.get("nova", set())

    def test_soft_dependency_exclusions_applied(self, tmp_path: Path) -> None:
        """Test that soft dependency exclusions prevent edges from being added."""
        # Create releases repo
        releases_dir = tmp_path / "releases" / "deliverables" / "2024.2"
        releases_dir.mkdir(parents=True)
        (releases_dir / "oslo.config.yaml").write_text("type: library\n")
        (releases_dir / "oslo.log.yaml").write_text("type: library\n")

        ubuntu_index = PackageIndex()
        # oslo.config depends on oslo.log (but this is a soft/optional dep)
        ubuntu_index.packages["python3-oslo.config"] = BinaryPackage(
            name="python3-oslo.config",
            version="9.0.0",
            architecture="all",
            source="python-oslo.config",
            depends=["python3-oslo.log"],
        )
        ubuntu_index.sources["python-oslo.config"] = ["python3-oslo.config"]
        ubuntu_index.packages["python3-oslo.log"] = BinaryPackage(
            name="python3-oslo.log",
            version="5.0.0",
            architecture="all",
            source="python-oslo.log",
            depends=["python3-oslo.config"],  # oslo.log depends on oslo.config
        )
        ubuntu_index.sources["python-oslo.log"] = ["python3-oslo.log"]

        run = MagicMock()

        graph, _mir = _build_dependency_graph(
            targets=["python-oslo.config"],
            local_repo=tmp_path / "local",
            local_index=None,
            ubuntu_index=ubuntu_index,
            run=run,
            releases_repo=tmp_path / "releases",
            openstack_series="2024.2",
            offline=True,
        )

        # oslo.config should be in graph
        assert "python-oslo.config" in graph.nodes
        # oslo.log should NOT be added as a dependency due to exclusion
        assert "python-oslo.log" not in graph.edges.get("python-oslo.config", set())

        # Verify the exclusion was logged
        log_events = [call[0][0] for call in run.log_event.call_args_list]
        assert any(
            e.get("event") == "graph.soft_dep_excluded"
            and e.get("source") == "python-oslo.config"
            and e.get("dep") == "python-oslo.log"
            for e in log_events
        )

    def test_soft_dependency_exclusions_constant(self) -> None:
        """Test that the exclusions constant has expected entries."""
        # Verify oslo.config -> oslo.log exclusion exists
        assert "python-oslo.config" in SOFT_DEPENDENCY_EXCLUSIONS
        assert "python-oslo.log" in SOFT_DEPENDENCY_EXCLUSIONS["python-oslo.config"]


class TestPlanCLI:
    """Integration tests for the plan CLI command."""

    def test_no_match_exits_with_config_error(self, tmp_path: Path) -> None:
        """Test that no matches exits with config error."""
        with patch("packastack.commands.plan.load_config") as mock_cfg:
            mock_cfg.return_value = {"defaults": {}}
            with patch("packastack.commands.plan.resolve_paths") as mock_paths:
                mock_paths.return_value = {
                    "local_apt_repo": tmp_path / "local",
                    "openstack_releases_repo": tmp_path / "releases",
                    "ubuntu_archive_cache": tmp_path / "cache",
                }
                with patch("packastack.commands.plan.resolve_series") as mock_series:
                    mock_series.return_value = "oracular"
                    with patch("packastack.commands.plan.get_current_development_series") as mock_dev:
                        mock_dev.return_value = "2024.2"
                        with patch("packastack.commands.plan._resolve_package_targets") as mock_resolve:
                            mock_resolve.return_value = []

                            result = runner.invoke(app, ["plan", "nonexistent"])

                            assert result.exit_code == EXIT_CONFIG_ERROR

    def test_multiple_matches_without_force(self, tmp_path: Path) -> None:
        """Test that multiple matches require --force."""
        with patch("packastack.commands.plan.load_config") as mock_cfg:
            mock_cfg.return_value = {"defaults": {}}
            with patch("packastack.commands.plan.resolve_paths") as mock_paths:
                mock_paths.return_value = {
                    "local_apt_repo": tmp_path / "local",
                    "openstack_releases_repo": tmp_path / "releases",
                    "ubuntu_archive_cache": tmp_path / "cache",
                }
                with patch("packastack.commands.plan.resolve_series") as mock_series:
                    mock_series.return_value = "oracular"
                    with patch("packastack.commands.plan.get_current_development_series") as mock_dev:
                        mock_dev.return_value = "2024.2"
                        with patch("packastack.commands.plan._resolve_package_targets") as mock_resolve:
                            mock_resolve.return_value = [_make_resolved_target("oslo.config"), _make_resolved_target("oslo.log")]

                            result = runner.invoke(app, ["plan", "oslo"])

                            assert result.exit_code == EXIT_CONFIG_ERROR

    def test_policy_blocked_exits(self, tmp_path: Path) -> None:
        """Test that policy failures exit with config error."""
        with patch("packastack.commands.plan.load_config") as mock_cfg:
            mock_cfg.return_value = {"defaults": {}}
            with patch("packastack.commands.plan.resolve_paths") as mock_paths:
                mock_paths.return_value = {
                    "local_apt_repo": tmp_path / "local",
                    "openstack_releases_repo": tmp_path / "releases",
                    "ubuntu_archive_cache": tmp_path / "cache",
                }
                with patch("packastack.commands.plan.resolve_series") as mock_series:
                    mock_series.return_value = "oracular"
                    with patch("packastack.commands.plan.get_current_development_series") as mock_dev:
                        mock_dev.return_value = "2024.2"
                        with patch("packastack.commands.plan._resolve_package_targets") as mock_resolve:
                            mock_resolve.return_value = [_make_resolved_target("nova")]
                            with patch("packastack.commands.plan.is_snapshot_eligible") as mock_eligible:
                                mock_eligible.return_value = (False, "blocked by policy", "1.0.0")

                                result = runner.invoke(app, ["plan", "nova"])

                                assert result.exit_code == EXIT_CONFIG_ERROR

    def test_policy_blocked_with_force_continues(self, tmp_path: Path) -> None:
        """Test that --force overrides policy failures."""
        # Create local package
        pkg_dir = tmp_path / "local" / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").write_text("""\
Source: nova
Section: python

Package: python3-nova
Architecture: all
Description: Nova
""")

        # Create empty ubuntu cache
        cache_dir = tmp_path / "cache" / "oracular"
        cache_dir.mkdir(parents=True)

        with patch("packastack.commands.plan.load_config") as mock_cfg:
            mock_cfg.return_value = {"defaults": {}}
            with patch("packastack.commands.plan.resolve_paths") as mock_paths:
                mock_paths.return_value = {
                    "local_apt_repo": tmp_path / "local",
                    "openstack_releases_repo": tmp_path / "releases",
                    "ubuntu_archive_cache": tmp_path / "cache",
                }
                with patch("packastack.commands.plan.resolve_series") as mock_series:
                    mock_series.return_value = "oracular"
                    with patch("packastack.commands.plan.get_current_development_series") as mock_dev:
                        mock_dev.return_value = "2024.2"
                        with patch("packastack.commands.plan._resolve_package_targets") as mock_resolve:
                            mock_resolve.return_value = [_make_resolved_target("nova")]
                            with patch("packastack.commands.plan.is_snapshot_eligible") as mock_eligible:
                                mock_eligible.return_value = (False, "blocked", "1.0.0")
                                with patch("packastack.commands.plan.load_package_index") as mock_index:
                                    mock_index.return_value = PackageIndex()
                                    with patch("packastack.commands.plan._build_dependency_graph") as mock_graph:
                                        from packastack.planning.graph import DependencyGraph
                                        g = DependencyGraph()
                                        g.add_node("nova", needs_rebuild=True)
                                        mock_graph.return_value = (g, {})

                                        result = runner.invoke(app, ["plan", "nova", "--force"])

                                        # Should continue past policy with --force
                                        assert result.exit_code == EXIT_SUCCESS

    def test_cycle_detected_exits(self, tmp_path: Path) -> None:
        """Test that cycles exit with cycle detected code."""
        with patch("packastack.commands.plan.load_config") as mock_cfg:
            mock_cfg.return_value = {"defaults": {}}
            with patch("packastack.commands.plan.resolve_paths") as mock_paths:
                mock_paths.return_value = {
                    "local_apt_repo": tmp_path / "local",
                    "openstack_releases_repo": tmp_path / "releases",
                    "ubuntu_archive_cache": tmp_path / "cache",
                }
                with patch("packastack.commands.plan.resolve_series") as mock_series:
                    mock_series.return_value = "oracular"
                    with patch("packastack.commands.plan.get_current_development_series") as mock_dev:
                        mock_dev.return_value = "2024.2"
                        with patch("packastack.commands.plan._resolve_package_targets") as mock_resolve:
                            mock_resolve.return_value = [_make_resolved_target("nova")]
                            with patch("packastack.commands.plan.is_snapshot_eligible") as mock_eligible:
                                mock_eligible.return_value = (True, "ok", None)
                                with patch("packastack.commands.plan.load_package_index") as mock_index:
                                    mock_index.return_value = PackageIndex()
                                    with patch("packastack.commands.plan._build_dependency_graph") as mock_graph:
                                        # Create graph with cycle
                                        g = DependencyGraph()
                                        g.add_edge("A", "B")
                                        g.add_edge("B", "A")
                                        mock_graph.return_value = (g, {})

                                        result = runner.invoke(app, ["plan", "nova"])

                                        assert result.exit_code == EXIT_CYCLE_DETECTED

    def test_missing_packages_exits(self, tmp_path: Path) -> None:
        """Test that missing packages exit with proper code."""
        with patch("packastack.commands.plan.load_config") as mock_cfg:
            mock_cfg.return_value = {"defaults": {}}
            with patch("packastack.commands.plan.resolve_paths") as mock_paths:
                mock_paths.return_value = {
                    "local_apt_repo": tmp_path / "local",
                    "openstack_releases_repo": tmp_path / "releases",
                    "ubuntu_archive_cache": tmp_path / "cache",
                }
                with patch("packastack.commands.plan.resolve_series") as mock_series:
                    mock_series.return_value = "oracular"
                    with patch("packastack.commands.plan.get_current_development_series") as mock_dev:
                        mock_dev.return_value = "2024.2"
                        with patch("packastack.commands.plan._resolve_package_targets") as mock_resolve:
                            mock_resolve.return_value = [_make_resolved_target("nova")]
                            with patch("packastack.commands.plan.is_snapshot_eligible") as mock_eligible:
                                mock_eligible.return_value = (True, "ok", None)
                                with patch("packastack.commands.plan.load_package_index") as mock_index:
                                    # Return empty index so deps are missing
                                    mock_index.return_value = PackageIndex()
                                    with patch("packastack.commands.plan._build_dependency_graph") as mock_graph:
                                        g = DependencyGraph()
                                        g.add_node("nova", needs_rebuild=True)
                                        # Simulate missing dep by mocking find_missing_dependencies
                                        with patch.object(g, "find_missing_dependencies") as mock_find:
                                            mock_find.return_value = {"nova": ["libmissing"]}
                                            mock_graph.return_value = (g, {})

                                            result = runner.invoke(app, ["plan", "nova"])

                                            assert result.exit_code == EXIT_MISSING_PACKAGES

    def test_successful_plan_with_upload(self, tmp_path: Path) -> None:
        """Test successful plan with upload order."""
        with patch("packastack.commands.plan.load_config") as mock_cfg:
            mock_cfg.return_value = {"defaults": {}}
            with patch("packastack.commands.plan.resolve_paths") as mock_paths:
                mock_paths.return_value = {
                    "local_apt_repo": tmp_path / "local",
                    "openstack_releases_repo": tmp_path / "releases",
                    "ubuntu_archive_cache": tmp_path / "cache",
                }
                with patch("packastack.commands.plan.resolve_series") as mock_series:
                    mock_series.return_value = "oracular"
                    with patch("packastack.commands.plan.get_current_development_series") as mock_dev:
                        mock_dev.return_value = "2024.2"
                        with patch("packastack.commands.plan._resolve_package_targets") as mock_resolve:
                            mock_resolve.return_value = [_make_resolved_target("nova")]
                            with patch("packastack.commands.plan.is_snapshot_eligible") as mock_eligible:
                                mock_eligible.return_value = (True, "ok", None)
                                with patch("packastack.commands.plan.load_package_index") as mock_index:
                                    mock_index.return_value = PackageIndex()
                                    with patch("packastack.commands.plan._build_dependency_graph") as mock_graph:
                                        g = DependencyGraph()
                                        g.add_node("nova", needs_rebuild=True)
                                        mock_graph.return_value = (g, {})

                                        result = runner.invoke(app, ["plan", "nova", "--plan-upload"])

                                        assert result.exit_code == EXIT_SUCCESS

    def test_successful_plan_with_mir_warnings(self, tmp_path: Path) -> None:
        """Test successful plan with MIR warnings."""
        with patch("packastack.commands.plan.load_config") as mock_cfg:
            mock_cfg.return_value = {"defaults": {}}
            with patch("packastack.commands.plan.resolve_paths") as mock_paths:
                mock_paths.return_value = {
                    "local_apt_repo": tmp_path / "local",
                    "openstack_releases_repo": tmp_path / "releases",
                    "ubuntu_archive_cache": tmp_path / "cache",
                }
                with patch("packastack.commands.plan.resolve_series") as mock_series:
                    mock_series.return_value = "oracular"
                    with patch("packastack.commands.plan.get_current_development_series") as mock_dev:
                        mock_dev.return_value = "2024.2"
                        with patch("packastack.commands.plan._resolve_package_targets") as mock_resolve:
                            mock_resolve.return_value = [_make_resolved_target("nova")]
                            with patch("packastack.commands.plan.is_snapshot_eligible") as mock_eligible:
                                mock_eligible.return_value = (True, "ok", None)
                                with patch("packastack.commands.plan.load_package_index") as mock_index:
                                    mock_index.return_value = PackageIndex()
                                    with patch("packastack.commands.plan._build_dependency_graph") as mock_graph:
                                        g = DependencyGraph()
                                        g.add_node("nova", needs_rebuild=True)
                                        # Add MIR candidates
                                        mir = {"nova": ["libfoo (universe)"]}
                                        mock_graph.return_value = (g, mir)

                                        result = runner.invoke(app, ["plan", "nova"])

                                        assert result.exit_code == EXIT_SUCCESS


class TestExitCodes:
    """Tests for exit code constants."""

    def test_exit_codes_defined(self) -> None:
        """Test that exit codes are properly defined."""
        assert EXIT_SUCCESS == 0
        assert EXIT_CONFIG_ERROR == 1
        assert EXIT_MISSING_PACKAGES == 5
        assert EXIT_CYCLE_DETECTED == 6


class TestSourcePackageToDeliverable:
    """Tests for _source_package_to_deliverable function."""

    def test_strips_python_prefix(self) -> None:
        """Test that python- prefix is stripped for libraries."""
        assert _source_package_to_deliverable("python-oslo.log") == "oslo.log"
        assert _source_package_to_deliverable("python-keystoneclient") == "keystoneclient"
        assert _source_package_to_deliverable("python-openstackclient") == "openstackclient"

    def test_preserves_non_python_packages(self) -> None:
        """Test that non-python packages are unchanged."""
        assert _source_package_to_deliverable("nova") == "nova"
        assert _source_package_to_deliverable("keystone") == "keystone"
        assert _source_package_to_deliverable("glance") == "glance"

    def test_preserves_pythonN_packages(self) -> None:
        """Test that python3-* packages are unchanged (not libraries)."""
        # python3-* are binary package names, not source package names
        # but if we ever encounter them, they should not be stripped
        assert _source_package_to_deliverable("python3-nova") == "python3-nova"
