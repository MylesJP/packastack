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

"""Tests for build manifest module."""


from packastack.planning.build_manifest import (
    BuildManifest,
    PackageVersion,
    compute_build_order,
    create_manifest,
    resolve_version_for_package,
)
from packastack.planning.graph import DependencyGraph
from packastack.planning.type_selection import (
    BuildType,
    CycleStage,
    DeliverableKind,
    KindConfidence,
    PackageStatus,
    ReasonCode,
    TypeSelectionResult,
)


class TestPackageVersion:
    """Tests for PackageVersion dataclass."""

    def test_full_version_no_epoch(self):
        """Full version without epoch."""
        pv = PackageVersion(
            source_package="nova",
            deliverable="nova",
            upstream_version="29.0.0",
            debian_revision="0ubuntu1",
            epoch=0,
            build_type=BuildType.RELEASE,
        )
        assert pv.full_version == "29.0.0-0ubuntu1"

    def test_full_version_with_epoch(self):
        """Full version with epoch."""
        pv = PackageVersion(
            source_package="python-keystoneclient",
            deliverable="python-keystoneclient",
            upstream_version="5.2.0",
            debian_revision="0ubuntu1",
            epoch=1,
            build_type=BuildType.RELEASE,
        )
        assert pv.full_version == "1:5.2.0-0ubuntu1"

    def test_full_version_explicit(self):
        """Explicit full version takes precedence."""
        pv = PackageVersion(
            source_package="nova",
            deliverable="nova",
            upstream_version="29.0.0",
            debian_revision="0ubuntu1",
            epoch=0,
            build_type=BuildType.RELEASE,
            full_version="29.0.0~b1-0ubuntu1",
        )
        assert pv.full_version == "29.0.0~b1-0ubuntu1"

    def test_computed_default_true(self):
        """Computed defaults to True."""
        pv = PackageVersion(
            source_package="nova",
            deliverable="nova",
            upstream_version="29.0.0",
            debian_revision="0ubuntu1",
            epoch=0,
            build_type=BuildType.RELEASE,
        )
        assert pv.computed is True


class TestBuildManifest:
    """Tests for BuildManifest dataclass."""

    def test_empty_manifest(self):
        """Empty manifest has no packages."""
        manifest = BuildManifest(series="dalmatian", cycle_stage=CycleStage.PRE_FINAL)
        assert manifest.packages == {}
        assert manifest.build_order == []

    def test_get_version_not_found(self):
        """get_version returns None for missing package."""
        manifest = BuildManifest(series="dalmatian", cycle_stage=CycleStage.PRE_FINAL)
        assert manifest.get_version("nonexistent") is None

    def test_get_version_found(self):
        """get_version returns version for existing package."""
        manifest = BuildManifest(series="dalmatian", cycle_stage=CycleStage.PRE_FINAL)
        manifest.add_package(
            source_package="nova",
            deliverable="nova",
            upstream_version="29.0.0",
            debian_revision="0ubuntu1",
            build_type=BuildType.RELEASE,
        )
        assert manifest.get_version("nova") == "29.0.0-0ubuntu1"

    def test_get_upstream_version(self):
        """get_upstream_version returns upstream version."""
        manifest = BuildManifest(series="dalmatian", cycle_stage=CycleStage.PRE_FINAL)
        manifest.add_package(
            source_package="nova",
            deliverable="nova",
            upstream_version="29.0.0",
            debian_revision="0ubuntu1",
            build_type=BuildType.RELEASE,
        )
        assert manifest.get_upstream_version("nova") == "29.0.0"
        assert manifest.get_upstream_version("nonexistent") is None

    def test_get_build_type(self):
        """get_build_type returns build type."""
        manifest = BuildManifest(series="dalmatian", cycle_stage=CycleStage.PRE_FINAL)
        manifest.add_package(
            source_package="nova",
            deliverable="nova",
            upstream_version="29.0.0",
            debian_revision="0ubuntu1",
            build_type=BuildType.RELEASE,
        )
        assert manifest.get_build_type("nova") == BuildType.RELEASE
        assert manifest.get_build_type("nonexistent") is None

    def test_is_in_manifest(self):
        """is_in_manifest checks package presence."""
        manifest = BuildManifest(series="dalmatian", cycle_stage=CycleStage.PRE_FINAL)
        manifest.add_package(
            source_package="nova",
            deliverable="nova",
            upstream_version="29.0.0",
            debian_revision="0ubuntu1",
            build_type=BuildType.RELEASE,
        )
        assert manifest.is_in_manifest("nova") is True
        assert manifest.is_in_manifest("nonexistent") is False

    def test_add_package_with_epoch(self):
        """add_package handles epoch correctly."""
        manifest = BuildManifest(series="dalmatian", cycle_stage=CycleStage.PRE_FINAL)
        pkg = manifest.add_package(
            source_package="keystoneclient",
            deliverable="python-keystoneclient",
            upstream_version="5.2.0",
            debian_revision="0ubuntu1",
            build_type=BuildType.RELEASE,
            epoch=1,
        )
        assert pkg.epoch == 1
        assert pkg.full_version == "1:5.2.0-0ubuntu1"

    def test_to_dict(self):
        """to_dict serializes manifest correctly."""
        manifest = BuildManifest(series="dalmatian", cycle_stage=CycleStage.PRE_FINAL)
        manifest.add_package(
            source_package="nova",
            deliverable="nova",
            upstream_version="29.0.0",
            debian_revision="0ubuntu1",
            build_type=BuildType.RELEASE,
            version_source="openstack/releases",
        )
        manifest.build_order = ["nova"]
        manifest.warnings = ["test warning"]

        d = manifest.to_dict()
        assert d["series"] == "dalmatian"
        assert d["cycle_stage"] == "pre_final"
        assert "nova" in d["packages"]
        assert d["packages"]["nova"]["upstream_version"] == "29.0.0"
        assert d["packages"]["nova"]["build_type"] == "release"
        assert d["build_order"] == ["nova"]
        assert d["warnings"] == ["test warning"]


class TestComputeBuildOrder:
    """Tests for compute_build_order function."""

    def test_empty_packages(self):
        """Empty package list returns empty order."""
        graph = DependencyGraph()
        order = compute_build_order([], graph)
        assert order == []

    def test_single_package(self):
        """Single package returns that package."""
        graph = DependencyGraph()
        graph.add_node("nova")
        order = compute_build_order(["nova"], graph)
        assert order == ["nova"]

    def test_dependencies_come_first(self):
        """Dependencies appear before dependents."""
        graph = DependencyGraph()
        graph.add_node("nova")
        graph.add_node("oslo.config")
        graph.add_node("oslo.messaging")
        # nova depends on oslo.messaging, oslo.messaging depends on oslo.config
        graph.add_edge("nova", "oslo.messaging")
        graph.add_edge("oslo.messaging", "oslo.config")

        order = compute_build_order(["nova", "oslo.config", "oslo.messaging"], graph)
        # oslo.config should come before oslo.messaging, which should come before nova
        assert order.index("oslo.config") < order.index("oslo.messaging")
        assert order.index("oslo.messaging") < order.index("nova")

    def test_filters_to_requested_packages(self):
        """Only requested packages are in result."""
        graph = DependencyGraph()
        graph.add_node("nova")
        graph.add_node("glance")
        graph.add_node("oslo.config")

        order = compute_build_order(["nova", "oslo.config"], graph)
        assert "glance" not in order
        assert "nova" in order
        assert "oslo.config" in order

    def test_cycle_falls_back(self):
        """Cycle detection falls back to original order."""
        graph = DependencyGraph()
        graph.add_node("a")
        graph.add_node("b")
        graph.add_edge("a", "b")
        graph.add_edge("b", "a")  # Creates cycle

        # Should not raise, falls back to input order
        order = compute_build_order(["a", "b"], graph)
        assert set(order) == {"a", "b"}


class TestResolveVersionForPackage:
    """Tests for resolve_version_for_package function."""

    def test_snapshot_returns_placeholder(self):
        """Snapshot type returns placeholder version."""
        version, _revision, _epoch, source = resolve_version_for_package(
            source_package="nova",
            build_type=BuildType.SNAPSHOT,
            releases_repo=None,
            series="dalmatian",
            deliverable="nova",
        )
        assert source == "git-snapshot"
        # Snapshot versions are computed at build time
        assert version == "0.0.0"

    def test_release_without_repo(self):
        """Release without releases repo returns placeholder."""
        version, _revision, _epoch, source = resolve_version_for_package(
            source_package="nova",
            build_type=BuildType.RELEASE,
            releases_repo=None,
            series="dalmatian",
            deliverable="nova",
        )
        assert version == "0.0.0"
        assert source == "placeholder"

    def test_release_with_repo(self, tmp_path):
        """Release with releases repo loads version from releases."""
        from unittest.mock import MagicMock, patch

        releases_repo = tmp_path / "releases"
        releases_repo.mkdir()

        mock_release = MagicMock()
        mock_release.version = "29.1.0"

        with patch("packastack.upstream.releases.load_project_releases", return_value=[mock_release]):
            version, _revision, _epoch, source = resolve_version_for_package(
                source_package="nova",
                build_type=BuildType.RELEASE,
                releases_repo=releases_repo,
                series="dalmatian",
                deliverable="nova",
            )
        assert version == "29.1.0"
        assert source == "openstack/releases"

    def test_release_with_repo_no_releases(self, tmp_path):
        """Release with releases repo but no releases returns placeholder."""
        from unittest.mock import patch

        releases_repo = tmp_path / "releases"
        releases_repo.mkdir()

        with patch("packastack.upstream.releases.load_project_releases", return_value=[]):
            version, _revision, _epoch, source = resolve_version_for_package(
                source_package="nova",
                build_type=BuildType.RELEASE,
                releases_repo=releases_repo,
                series="dalmatian",
                deliverable="nova",
            )
        assert version == "0.0.0"
        assert source == "placeholder"

    def test_milestone_without_repo(self):
        """Milestone without releases repo returns placeholder."""
        version, _revision, _epoch, source = resolve_version_for_package(
            source_package="nova",
            build_type=BuildType.MILESTONE,
            releases_repo=None,
            series="dalmatian",
            deliverable="nova",
        )
        assert version == "0.0.0"
        assert source == "placeholder"

    def test_milestone_with_repo(self, tmp_path):
        """Milestone with releases repo loads version from releases."""
        from unittest.mock import MagicMock, patch

        releases_repo = tmp_path / "releases"
        releases_repo.mkdir()

        mock_release = MagicMock()
        mock_release.version = "30.0.0b1"

        with patch("packastack.upstream.releases.load_project_releases", return_value=[mock_release]):
            version, _revision, _epoch, source = resolve_version_for_package(
                source_package="nova",
                build_type=BuildType.MILESTONE,
                releases_repo=releases_repo,
                series="dalmatian",
                deliverable="nova",
            )
        assert version == "30.0.0b1"
        assert source == "openstack/releases"


class TestCreateManifest:
    """Tests for create_manifest function."""

    def _make_type_selection(
        self,
        source_package: str,
        deliverable: str = "",
        build_type: BuildType = BuildType.SNAPSHOT,
    ) -> TypeSelectionResult:
        """Helper to create a TypeSelectionResult."""
        return TypeSelectionResult(
            source_package=source_package,
            deliverable=deliverable or source_package,
            release_model="cycle-with-rc",
            deliverable_kind=DeliverableKind.SERVICE,
            kind_confidence=KindConfidence.HEURISTIC,
            has_release_for_cycle=False,
            has_beta_rc_final=False,
            latest_version="",
            cycle_stage=CycleStage.PRE_FINAL,
            chosen_type=build_type,
            reason_code=ReasonCode.NO_RELEASE_YET,
            reason_human="No release yet",
            package_status=PackageStatus.ACTIVE,
        )

    def test_create_empty_manifest(self):
        """Create manifest with no packages."""
        graph = DependencyGraph()
        manifest = create_manifest(
            packages=[],
            series="dalmatian",
            cycle_stage=CycleStage.PRE_FINAL,
            type_selections={},
            dependency_graph=graph,
        )
        assert manifest.series == "dalmatian"
        assert manifest.cycle_stage == CycleStage.PRE_FINAL
        assert manifest.packages == {}

    def test_create_manifest_single_package(self):
        """Create manifest with single package."""
        graph = DependencyGraph()
        graph.add_node("nova")

        type_selections = {
            "nova": self._make_type_selection("nova", build_type=BuildType.SNAPSHOT),
        }

        manifest = create_manifest(
            packages=["nova"],
            series="dalmatian",
            cycle_stage=CycleStage.PRE_FINAL,
            type_selections=type_selections,
            dependency_graph=graph,
        )

        assert "nova" in manifest.packages
        assert manifest.packages["nova"].build_type == BuildType.SNAPSHOT
        assert manifest.build_order == ["nova"]

    def test_create_manifest_with_dependencies(self):
        """Create manifest respects dependency order."""
        graph = DependencyGraph()
        graph.add_node("nova")
        graph.add_node("oslo.config")
        graph.add_edge("nova", "oslo.config")

        type_selections = {
            "nova": self._make_type_selection("nova"),
            "oslo.config": self._make_type_selection("oslo.config"),
        }

        manifest = create_manifest(
            packages=["nova", "oslo.config"],
            series="dalmatian",
            cycle_stage=CycleStage.PRE_FINAL,
            type_selections=type_selections,
            dependency_graph=graph,
        )

        # oslo.config should come before nova in build order
        assert manifest.build_order.index("oslo.config") < manifest.build_order.index("nova")

    def test_create_manifest_stores_dependency_edges(self):
        """Create manifest stores dependency edges."""
        graph = DependencyGraph()
        graph.add_node("nova")
        graph.add_node("oslo.config")
        graph.add_node("oslo.messaging")
        graph.add_edge("nova", "oslo.config")
        graph.add_edge("nova", "oslo.messaging")

        type_selections = {
            "nova": self._make_type_selection("nova"),
            "oslo.config": self._make_type_selection("oslo.config"),
            "oslo.messaging": self._make_type_selection("oslo.messaging"),
        }

        manifest = create_manifest(
            packages=["nova", "oslo.config", "oslo.messaging"],
            series="dalmatian",
            cycle_stage=CycleStage.PRE_FINAL,
            type_selections=type_selections,
            dependency_graph=graph,
        )

        assert "oslo.config" in manifest.dependency_edges["nova"]
        assert "oslo.messaging" in manifest.dependency_edges["nova"]

    def test_create_manifest_missing_type_selection(self):
        """Create manifest handles missing type selection with warning."""
        graph = DependencyGraph()
        graph.add_node("nova")

        manifest = create_manifest(
            packages=["nova"],
            series="dalmatian",
            cycle_stage=CycleStage.PRE_FINAL,
            type_selections={},  # No type selection for nova
            dependency_graph=graph,
        )

        assert "nova" in manifest.packages
        assert manifest.packages["nova"].build_type == BuildType.SNAPSHOT
        assert len(manifest.warnings) > 0
        assert "No type selection for nova" in manifest.warnings[0]

    def test_create_manifest_stores_type_selections(self):
        """Create manifest stores type selections."""
        graph = DependencyGraph()
        graph.add_node("nova")

        type_selections = {
            "nova": self._make_type_selection("nova", build_type=BuildType.RELEASE),
        }

        manifest = create_manifest(
            packages=["nova"],
            series="dalmatian",
            cycle_stage=CycleStage.PRE_FINAL,
            type_selections=type_selections,
            dependency_graph=graph,
        )

        assert "nova" in manifest.type_selections
        assert manifest.type_selections["nova"].chosen_type == BuildType.RELEASE
