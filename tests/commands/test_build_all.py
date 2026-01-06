# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for packastack.commands.build module (build-all functionality)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from packastack.apt.packages import PackageIndex, BinaryPackage
from packastack.commands.build import (
    EXIT_SUCCESS,
    EXIT_ALL_BUILD_FAILED,
    EXIT_DISCOVERY_FAILED,
    EXIT_RESUME_ERROR,
    EXIT_GRAPH_ERROR,
    OPTIONAL_DEPS_FOR_CYCLE,
    _build_dependency_graph,
    _build_upstream_versions_from_packaging,
    _filter_retired_packages,
    _run_build_all,
    _run_parallel_builds,
    _run_sequential_builds,
    _run_single_build,
    _generate_reports,
    _get_parallel_batches,
    run_build_all,
)
from packastack.core.context import BuildAllRequest
from packastack.planning.build_all_state import (
    BuildAllState,
    FailureType,
    MissingDependency,
    PackageStatus,
    create_initial_state,
)
from packastack.planning.graph import DependencyGraph
from packastack.planning.package_discovery import DiscoveryResult
from packastack.planning.cycle_suggestions import CycleEdgeSuggestion


def _call_run_build_all(
    run,
    target: str = "devel",
    ubuntu_series: str = "devel",
    cloud_archive: str = "",
    release: bool = True,
    snapshot: bool = False,
    milestone: str = "",
    binary: bool = False,
    keep_going: bool = True,
    max_failures: int = 0,
    resume: bool = False,
    resume_run_id: str = "",
    retry_failed: bool = False,
    skip_failed: bool = False,
    parallel: int = 1,
    packages_file: str = "",
    force: bool = False,
    offline: bool = False,
    dry_run: bool = False,
) -> int:
    """Helper to call _run_build_all with a BuildAllRequest.
    
    This bridges the old kwarg-style test calls to the new BuildAllRequest-based API.
    """
    # Convert old-style release/snapshot bools to new build_type string
    if milestone:
        build_type = "milestone"
    elif snapshot:
        build_type = "snapshot"
    elif release:
        build_type = "release"
    else:
        build_type = "auto"

    request = BuildAllRequest(
        target=target,
        ubuntu_series=ubuntu_series,
        cloud_archive=cloud_archive,
        build_type=build_type,
        milestone=milestone,
        binary=binary,
        keep_going=keep_going,
        max_failures=max_failures,
        resume=resume,
        resume_run_id=resume_run_id,
        retry_failed=retry_failed,
        skip_failed=skip_failed,
        parallel=parallel,
        packages_file=packages_file,
        force=force,
        offline=offline,
        dry_run=dry_run,
    )
    from packastack.commands.build import _run_build_all as _actual_run_build_all
    return _actual_run_build_all(run=run, request=request)


class TestBuildDependencyGraph:
    """Tests for _build_dependency_graph function."""

    def test_empty_packages(self, tmp_path: Path) -> None:
        """Test with no packages."""
        graph, missing = _build_dependency_graph(
            packages=[],
            cache_dir=tmp_path,
            pkg_index=PackageIndex(),
        )

        assert len(graph.nodes) == 0
        assert missing == {}

    def test_single_package_no_deps(self, tmp_path: Path) -> None:
        """Test single package without dependencies."""
        # Create package with no build-depends
        pkg_dir = tmp_path / "nova"
        (pkg_dir / "debian").mkdir(parents=True)
        (pkg_dir / "debian" / "control").write_text("""Source: nova
Build-Depends: debhelper-compat (= 13)

Package: nova
Architecture: all
""")

        graph, missing = _build_dependency_graph(
            packages=["nova"],
            cache_dir=tmp_path,
            pkg_index=PackageIndex(),
        )

        assert "nova" in graph.nodes
        assert len(graph.get_dependencies("nova")) == 0

    def test_internal_dependency(self, tmp_path: Path) -> None:
        """Test dependency between our packages."""
        # Create oslo.config
        oslo = tmp_path / "oslo.config"
        (oslo / "debian").mkdir(parents=True)
        (oslo / "debian" / "control").write_text("""Source: oslo.config
Build-Depends: debhelper-compat (= 13)

Package: python3-oslo.config
Architecture: all
""")

        # Create nova depending on oslo.config
        nova = tmp_path / "nova"
        (nova / "debian").mkdir(parents=True)
        (nova / "debian" / "control").write_text("""Source: nova
Build-Depends: debhelper-compat (= 13),
 python3-oslo.config

Package: nova
Architecture: all
""")

        graph, missing = _build_dependency_graph(
            packages=["oslo.config", "nova"],
            cache_dir=tmp_path,
            pkg_index=PackageIndex(),
        )

        assert "nova" in graph.nodes
        assert "oslo.config" in graph.nodes
        # nova depends on oslo.config
        assert "oslo.config" in graph.get_dependencies("nova")

    def test_archive_dependency(self, tmp_path: Path) -> None:
        """Test dependency satisfied by archive."""
        # Create nova with dependency on python3 (in archive)
        nova = tmp_path / "nova"
        (nova / "debian").mkdir(parents=True)
        (nova / "debian" / "control").write_text("""Source: nova
Build-Depends: debhelper-compat (= 13),
 python3

Package: nova
Architecture: all
""")

        pkg_index = PackageIndex()
        pkg_index.add_package(BinaryPackage(
            name="python3",
            version="3.12",
            source="python3-defaults",
            architecture="amd64",
        ), component="main", pocket="release")
        pkg_index.add_package(BinaryPackage(
            name="debhelper-compat",
            version="13",
            source="debhelper",
            architecture="all",
        ), component="main", pocket="release")

        graph, missing = _build_dependency_graph(
            packages=["nova"],
            cache_dir=tmp_path,
            pkg_index=pkg_index,
        )

        # No edge created for archive deps
        assert len(graph.get_dependencies("nova")) == 0
        assert missing == {}

    def test_missing_dependency(self, tmp_path: Path) -> None:
        """Test handling of missing dependency."""
        nova = tmp_path / "nova"
        (nova / "debian").mkdir(parents=True)
        (nova / "debian" / "control").write_text("""Source: nova
Build-Depends: debhelper-compat (= 13),
 python3-nonexistent

Package: nova
Architecture: all
""")

        graph, missing = _build_dependency_graph(
            packages=["nova"],
            cache_dir=tmp_path,
            pkg_index=PackageIndex(),
        )

        assert "nova" in missing
        assert "python3-nonexistent" in missing["nova"]

    def test_skips_optional_cycle_deps(self, tmp_path: Path) -> None:
        """Test that optional deps for cycle breaking are skipped."""
        nova = tmp_path / "nova"
        (nova / "debian").mkdir(parents=True)
        (nova / "debian" / "control").write_text("""Source: nova
Build-Depends: debhelper-compat (= 13),
 python3-sphinx,
 python3-reno

Package: nova
Architecture: all
""")

        graph, missing = _build_dependency_graph(
            packages=["nova"],
            cache_dir=tmp_path,
            pkg_index=PackageIndex(),
        )

        # These should not create edges or be marked missing
        assert len(graph.get_dependencies("nova")) == 0
        assert "nova" not in missing or "python3-sphinx" not in missing.get("nova", [])

    def test_build_upstream_versions_from_packaging(self, tmp_path: Path) -> None:
        """Test extracting upstream versions from debian/changelog."""
        pkg_dir = tmp_path / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "changelog").write_text(
            "nova (1:29.0.0-0ubuntu1) noble; urgency=medium\n\n  * test\n",
            encoding="utf-8",
        )

        versions = _build_upstream_versions_from_packaging(["nova"], tmp_path)

        assert versions["nova"] == "29.0.0"

    def test_build_upstream_versions_skips_missing_version(self, tmp_path: Path) -> None:
        """Should skip entries without a changelog version."""
        pkg_dir = tmp_path / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "changelog").write_text("invalid\n", encoding="utf-8")

        versions = _build_upstream_versions_from_packaging(["nova"], tmp_path)

        assert versions == {}

    def test_build_upstream_versions_skips_empty_upstream(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should skip when upstream version extraction is empty."""
        pkg_dir = tmp_path / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "changelog").write_text(
            "nova (1:29.0.0-0ubuntu1) noble; urgency=medium\n\n  * test\n",
            encoding="utf-8",
        )

        import packastack.build.all_helpers as all_helpers_module

        monkeypatch.setattr(all_helpers_module, "extract_upstream_version", lambda _ver: "")

        versions = _build_upstream_versions_from_packaging(["nova"], tmp_path)

        assert versions == {}


class TestGetParallelBatches:
    """Tests for _get_parallel_batches function."""

    def test_empty_state(self) -> None:
        """Test with empty state."""
        graph = DependencyGraph()
        state = create_initial_state(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
            packages=[],
            build_order=[],
        )

        batches = _get_parallel_batches(graph, state)
        assert batches == []

    def test_no_dependencies(self) -> None:
        """Test packages with no dependencies (all parallel)."""
        graph = DependencyGraph()
        for pkg in ["a", "b", "c"]:
            graph.add_node(pkg)

        state = create_initial_state(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
            packages=["a", "b", "c"],
            build_order=["a", "b", "c"],
        )

        batches = _get_parallel_batches(graph, state)

        # All should be in first batch
        assert len(batches) == 1
        assert sorted(batches[0]) == ["a", "b", "c"]

    def test_linear_dependencies(self) -> None:
        """Test linear dependency chain."""
        graph = DependencyGraph()
        graph.add_node("a")
        graph.add_node("b")
        graph.add_node("c")
        graph.add_edge("c", "b")  # c depends on b
        graph.add_edge("b", "a")  # b depends on a

        state = create_initial_state(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
            packages=["a", "b", "c"],
            build_order=["a", "b", "c"],
        )

        batches = _get_parallel_batches(graph, state)

        # Each should be in separate batch
        assert len(batches) == 3
        assert batches[0] == ["a"]
        assert batches[1] == ["b"]
        assert batches[2] == ["c"]

    def test_fan_out(self) -> None:
        """Test fan-out pattern (one dep, many dependents)."""
        graph = DependencyGraph()
        graph.add_node("base")
        for pkg in ["a", "b", "c"]:
            graph.add_node(pkg)
            graph.add_edge(pkg, "base")  # a, b, c all depend on base

        state = create_initial_state(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
            packages=["base", "a", "b", "c"],
            build_order=["base", "a", "b", "c"],
        )

        batches = _get_parallel_batches(graph, state)

        # First batch: base
        # Second batch: a, b, c (all parallel)
        assert len(batches) == 2
        assert batches[0] == ["base"]
        assert sorted(batches[1]) == ["a", "b", "c"]

    def test_skips_already_built(self) -> None:
        """Test that already-built packages are skipped."""
        graph = DependencyGraph()
        graph.add_node("a")
        graph.add_node("b")
        graph.add_edge("b", "a")

        state = create_initial_state(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
            packages=["a", "b"],
            build_order=["a", "b"],
        )
        # Mark a as already built
        state.packages["a"].status = PackageStatus.SUCCESS

        batches = _get_parallel_batches(graph, state)

        # Only b should be in batches
        assert len(batches) == 1
        assert batches[0] == ["b"]


class TestGenerateReports:
    """Tests for _generate_reports function."""

    def test_generates_json_and_md(self, tmp_path: Path) -> None:
        """Test that both report formats are generated."""
        state = create_initial_state(
            run_id="test-reports",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
            packages=["nova", "glance"],
            build_order=["nova", "glance"],
        )
        state.mark_started("nova")
        state.mark_success("nova", "/tmp/nova.log")
        state.mark_started("glance")
        state.mark_failed("glance", FailureType.BUILD_FAILED, "sbuild error", "/tmp/glance.log")
        state.completed_at = "2025-01-01T01:00:00"

        json_path, md_path = _generate_reports(state, tmp_path)

        assert json_path.exists()
        assert md_path.exists()
        assert json_path.name == "build-all-summary.json"
        assert md_path.name == "build-all-summary.md"

    def test_json_report_content(self, tmp_path: Path) -> None:
        """Test JSON report contains required fields."""
        state = create_initial_state(
            run_id="test-json",
            target="caracal",
            ubuntu_series="jammy",
            build_type="snapshot",
            packages=["nova"],
            build_order=["nova"],
        )
        state.mark_started("nova")
        state.mark_success("nova")
        state.completed_at = "2025-01-01T01:00:00"

        json_path, _ = _generate_reports(state, tmp_path)
        report = json.loads(json_path.read_text())

        assert report["run_id"] == "test-json"
        assert report["target"] == "caracal"
        assert report["ubuntu_series"] == "jammy"
        assert report["build_type"] == "snapshot"
        assert "summary" in report
        assert report["summary"]["total"] == 1
        assert report["summary"]["succeeded"] == 1
        assert "build_order" in report
        assert "failures" in report

    def test_md_report_content(self, tmp_path: Path) -> None:
        """Test Markdown report contains required sections."""
        state = create_initial_state(
            run_id="test-md",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
            packages=["nova", "glance"],
            build_order=["nova", "glance"],
        )
        state.mark_started("nova")
        state.mark_success("nova")
        state.mark_started("glance")
        state.mark_failed("glance", FailureType.PATCH_FAILED, "patch conflict")
        state.completed_at = "2025-01-01T01:00:00"

        _, md_path = _generate_reports(state, tmp_path)
        content = md_path.read_text()

        assert "# Build-All Summary" in content
        assert "dalmatian" in content
        assert "noble" in content
        assert "## Summary" in content
        assert "Succeeded" in content
        assert "Failed" in content
        assert "## Failures by Type" in content
        assert "patch_failed" in content
        assert "glance" in content

    def test_report_includes_missing_deps(self, tmp_path: Path) -> None:
        """Test that missing deps are included in reports."""
        state = create_initial_state(
            run_id="test-deps",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
            packages=["nova"],
            build_order=["nova"],
        )
        state.add_missing_dep(MissingDependency(
            binary_name="python3-foo",
            required_by=["nova", "glance", "keystone", "neutron"],
            suggested_action="Needs packaging",
        ))
        state.completed_at = "2025-01-01T01:00:00"

        json_path, md_path = _generate_reports(state, tmp_path)

        json_report = json.loads(json_path.read_text())
        assert "python3-foo" in json_report["missing_deps"]

        md_content = md_path.read_text()
        assert "## Missing Dependencies" in md_content
        assert "python3-foo" in md_content
        assert "+1 more" in md_content

    def test_report_includes_cycles(self, tmp_path: Path) -> None:
        """Test that cycles are included in reports."""
        state = create_initial_state(
            run_id="test-cycles",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
            packages=[],
            build_order=[],
        )
        state.cycles = [["a", "b", "c", "a"]]
        state.completed_at = "2025-01-01T01:00:00"

        json_path, md_path = _generate_reports(state, tmp_path)

        json_report = json.loads(json_path.read_text())
        assert json_report["cycles"] == [["a", "b", "c", "a"]]

        md_content = md_path.read_text()
        assert "## Dependency Cycles" in md_content
        assert "a -> b -> c -> a" in md_content

    def test_report_includes_top_longest(self, tmp_path: Path) -> None:
        """Test that top longest builds are included."""
        state = create_initial_state(
            run_id="test-timing",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
            packages=["fast", "slow"],
            build_order=["fast", "slow"],
        )
        state.packages["fast"].status = PackageStatus.SUCCESS
        state.packages["fast"].duration_seconds = 60.0
        state.packages["slow"].status = PackageStatus.SUCCESS
        state.packages["slow"].duration_seconds = 600.0
        state.completed_at = "2025-01-01T01:00:00"

        json_path, md_path = _generate_reports(state, tmp_path)

        json_report = json.loads(json_path.read_text())
        assert len(json_report["top_10_longest"]) == 2
        assert json_report["top_10_longest"][0]["package"] == "slow"

        md_content = md_path.read_text()
        assert "## Top 10 Longest Builds" in md_content
        assert "slow" in md_content


class TestOptionalDepsForCycle:
    """Tests for OPTIONAL_DEPS_FOR_CYCLE constant."""

    def test_contains_known_cycle_breakers(self) -> None:
        """Test that known cycle-causing deps are included."""
        assert "python3-sphinx" in OPTIONAL_DEPS_FOR_CYCLE
        assert "python3-reno" in OPTIONAL_DEPS_FOR_CYCLE
        assert "python3-openstackdocstheme" in OPTIONAL_DEPS_FOR_CYCLE


class TestRunBuildAllIndexLoading:
    """Tests for build-all index loading and graph inputs."""

    def test_loads_indexes_with_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ensure build-all calls index loaders with correct arguments."""
        import packastack.commands.build as build_all

        cfg = {
            "defaults": {
                "ubuntu_pockets": ["release"],
                "ubuntu_components": ["main"],
            }
        }
        paths = {
            "cache_root": tmp_path / "cache",
            "runs_root": tmp_path / "runs",
            "build_root": tmp_path / "build",
            "local_apt_repo": tmp_path / "apt-repo",
            "ubuntu_archive_cache": tmp_path / "ubuntu-archive",
            "openstack_releases_repo": tmp_path / "releases",
        }

        calls: dict[str, object] = {}

        monkeypatch.setattr(build_all, "load_config", lambda: cfg)
        monkeypatch.setattr(build_all, "resolve_paths", lambda _cfg: paths)
        monkeypatch.setattr(build_all, "resolve_series", lambda series: series)
        monkeypatch.setattr(build_all, "get_host_arch", lambda: "amd64")

        def fake_discover_packages(**kwargs) -> DiscoveryResult:
            calls["discover"] = kwargs
            return DiscoveryResult(packages=["nova"], total_repos=1, source="explicit")

        def fake_load_package_index(
            cache_root: Path,
            series: str,
            pockets: list[str],
            components: list[str],
        ) -> PackageIndex:
            calls["ubuntu"] = (cache_root, series, list(pockets), list(components))
            return PackageIndex()

        def fake_load_cloud_archive_index(
            cache_root: Path,
            ubuntu_series: str,
            pocket: str,
            components: list[str] | None = None,
        ) -> PackageIndex:
            calls["cloud"] = (cache_root, ubuntu_series, pocket, components)
            return PackageIndex()

        def fake_load_local_repo_index(repo_root: Path, arch: str = "amd64") -> PackageIndex:
            calls["local"] = (repo_root, arch)
            return PackageIndex()

        def fake_merge_package_indexes(*indexes: PackageIndex) -> PackageIndex:
            calls["merge"] = indexes
            return PackageIndex()

        def fake_build_dependency_graph(
            targets: list[str],
            local_repo: Path,
            local_index,
            ubuntu_index,
            run,
            **kwargs,
        ) -> tuple[DependencyGraph, dict[str, list[str]]]:
            calls["graph"] = (targets, local_repo)
            graph = DependencyGraph()
            for pkg in targets:
                graph.add_node(pkg)
            return graph, {}

        monkeypatch.setattr(build_all, "discover_packages", fake_discover_packages)
        monkeypatch.setattr(build_all, "load_package_index", fake_load_package_index)
        monkeypatch.setattr(build_all, "load_cloud_archive_index", fake_load_cloud_archive_index)
        monkeypatch.setattr(build_all, "load_local_repo_index", fake_load_local_repo_index)
        monkeypatch.setattr(build_all, "merge_package_indexes", fake_merge_package_indexes)
        # The actual code imports plan._build_dependency_graph, so patch that module
        import packastack.commands.plan as plan_module
        monkeypatch.setattr(plan_module, "_build_dependency_graph", fake_build_dependency_graph)

        run = SimpleNamespace(run_id="run-1", log_event=lambda *_args, **_kwargs: None, write_summary=lambda **_kwargs: None)

        exit_code = _call_run_build_all(
            run=run,
            target="dalmatian",
            ubuntu_series="noble",
            cloud_archive="caracal",
            release=True,
            snapshot=False,
            milestone="",
            binary=False,
            keep_going=True,
            max_failures=0,
            resume=False,
            resume_run_id="",
            retry_failed=False,
            skip_failed=True,
            parallel=1,
            packages_file="",
            force=False,
            offline=False,
            dry_run=True,
        )

        assert exit_code == EXIT_SUCCESS
        assert calls["discover"]["releases_repo"] == paths["openstack_releases_repo"]
        assert calls["ubuntu"] == (paths["ubuntu_archive_cache"], "noble", ["release"], ["main"])
        assert calls["cloud"] == (paths["ubuntu_archive_cache"], "noble", "caracal", None)
        assert isinstance(calls["merge"], tuple)
        assert not any(isinstance(item, list) for item in calls["merge"])
        assert calls["graph"][1] == paths["local_apt_repo"]


class TestFilterRetiredPackages:
    """Tests for _filter_retired_packages."""

    def test_returns_early_for_empty_packages(self) -> None:
        """Should return early when package list is empty."""
        filtered, retired, possibly = _filter_retired_packages(
            packages=[],
            project_config_path=None,
            releases_repo=None,
            openstack_target="dalmatian",
            offline=False,
            run=SimpleNamespace(),
        )

        assert filtered == []
        assert retired == []
        assert possibly == []

    def test_calls_clone_when_project_config_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should clone project-config when missing and online."""
        import packastack.commands.build as build_all_module

        project_config_path = tmp_path / "project-config"
        calls: dict[str, object] = {}

        def fake_clone(path: Path, _run: object) -> None:
            calls["path"] = path

        monkeypatch.setattr(build_all_module, "_clone_or_update_project_config", fake_clone)

        filtered, retired, possibly = _filter_retired_packages(
            packages=["nova"],
            project_config_path=project_config_path,
            releases_repo=None,
            openstack_target="dalmatian",
            offline=False,
            run=SimpleNamespace(),
        )

        assert calls["path"] == project_config_path
        assert filtered == ["nova"]
        assert retired == []
        assert possibly == []

    def test_filters_retired_packages(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should filter out retired and possibly retired packages."""
        import packastack.build.all_helpers as all_helpers_module

        project_config_path = tmp_path / "project-config"
        project_config_path.mkdir()

        class FakeChecker:
            def __init__(self, **_kwargs: object) -> None:
                pass

            def get_retired_packages(self, _packages: list[str]) -> list[str]:
                return ["a"]

            def get_possibly_retired_packages(self, _packages: list[str]) -> list[str]:
                return ["b"]

        monkeypatch.setattr(all_helpers_module, "RetirementChecker", FakeChecker)

        filtered, retired, possibly = _filter_retired_packages(
            packages=["a", "b", "c"],
            project_config_path=project_config_path,
            releases_repo=None,
            openstack_target="dalmatian",
            offline=False,
            run=SimpleNamespace(),
        )

        assert filtered == ["c"]
        assert retired == ["a"]
        assert possibly == ["b"]

    def test_keeps_all_when_no_retired(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should keep all packages when none are retired."""
        import packastack.build.all_helpers as all_helpers_module

        project_config_path = tmp_path / "project-config"
        project_config_path.mkdir()

        class FakeChecker:
            def __init__(self, **_kwargs: object) -> None:
                pass

            def get_retired_packages(self, _packages: list[str]) -> list[str]:
                return []

            def get_possibly_retired_packages(self, _packages: list[str]) -> list[str]:
                return []

        monkeypatch.setattr(all_helpers_module, "RetirementChecker", FakeChecker)

        filtered, retired, possibly = _filter_retired_packages(
            packages=["a", "b"],
            project_config_path=project_config_path,
            releases_repo=None,
            openstack_target="dalmatian",
            offline=False,
            run=SimpleNamespace(),
        )

        assert filtered == ["a", "b"]
        assert retired == []
        assert possibly == []


class TestRunSingleBuild:
    """Tests for _run_single_build."""

    def test_success_builds_command(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should build command args and return success on zero exit code."""
        captured: dict[str, object] = {}

        def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env")
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        success, failure_type, message, log_path = _run_single_build(
            package="nova",
            target="dalmatian",
            ubuntu_series="noble",
            cloud_archive="caracal",
            build_type="snapshot",
            binary=False,
            force=True,
            run_dir=tmp_path,
        )

        assert success is True
        assert failure_type is None
        assert message == ""
        assert log_path.endswith("build.log")
        cmd = captured["cmd"]
        assert "--cloud-archive" in cmd
        # Build type is passed via --type flag
        assert "--type" in cmd
        idx = cmd.index("--type")
        assert cmd[idx + 1] == "snapshot"
        assert "--no-binary" in cmd
        assert "--force" in cmd
        assert captured["env"]["PACKASTACK_BUILD_DEPTH"] == "10"

    @pytest.mark.parametrize(
        ("returncode", "expected"),
        [
            (3, FailureType.FETCH_FAILED),
            (4, FailureType.PATCH_FAILED),
            (5, FailureType.MISSING_DEP),
            (6, FailureType.CYCLE),
            (7, FailureType.BUILD_FAILED),
            (8, FailureType.POLICY_BLOCKED),
        ],
    )
    def test_failure_maps_exit_code(
        self,
        returncode: int,
        expected: FailureType,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should map exit codes to failure types."""
        def fake_run(_cmd: list[str], **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(returncode=returncode)

        monkeypatch.setattr(subprocess, "run", fake_run)

        success, failure_type, message, _log_path = _run_single_build(
            package="nova",
            target="dalmatian",
            ubuntu_series="noble",
            cloud_archive="",
            build_type="milestone",
            binary=True,
            force=False,
            run_dir=tmp_path,
        )

        assert success is False
        assert failure_type == expected
        assert "Exit code" in message

    def test_timeout_returns_build_failed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return BUILD_FAILED on timeout."""
        def fake_run(_cmd: list[str], **_kwargs: object) -> None:
            raise subprocess.TimeoutExpired(cmd="cmd", timeout=1)

        monkeypatch.setattr(subprocess, "run", fake_run)

        success, failure_type, message, _log_path = _run_single_build(
            package="nova",
            target="dalmatian",
            ubuntu_series="noble",
            cloud_archive="",
            build_type="release",
            binary=True,
            force=False,
            run_dir=tmp_path,
        )

        assert success is False
        assert failure_type == FailureType.BUILD_FAILED
        assert "timed out" in message

    def test_exception_returns_unknown(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return UNKNOWN on unexpected exceptions."""
        def fake_run(_cmd: list[str], **_kwargs: object) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(subprocess, "run", fake_run)

        success, failure_type, message, _log_path = _run_single_build(
            package="nova",
            target="dalmatian",
            ubuntu_series="noble",
            cloud_archive="",
            build_type="release",
            binary=True,
            force=False,
            run_dir=tmp_path,
        )

        assert success is False
        assert failure_type == FailureType.UNKNOWN
        assert message == "boom"


class TestParallelBatchesEdgeCases:
    """Tests for parallel batch computation edge cases."""

    def test_cycles_return_empty_batches(self) -> None:
        """Should stop when no packages are ready due to cycles."""
        graph = DependencyGraph()
        graph.add_edge("a", "b")
        graph.add_edge("b", "a")
        state = create_initial_state(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
            packages=["a", "b"],
            build_order=["a", "b"],
        )

        batches = _get_parallel_batches(graph, state)

        assert batches == []


class TestRunSequentialBuilds:
    """Tests for _run_sequential_builds."""

    def test_runs_builds_and_updates_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should execute pending builds and mark failures."""
        import packastack.commands.build as build_all_module

        state = create_initial_state(
            run_id="run-1",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
            packages=["a", "b", "c"],
            build_order=["a", "b", "c"],
            keep_going=True,
        )
        state.packages["a"].status = PackageStatus.SUCCESS

        def fake_run_single_build(package: str, **_kwargs: object) -> tuple[bool, FailureType | None, str, str]:
            if package == "c":
                return False, FailureType.BUILD_FAILED, "boom", "/tmp/c.log"
            return True, None, "", f"/tmp/{package}.log"

        monkeypatch.setattr(build_all_module, "_run_single_build", fake_run_single_build)
        monkeypatch.setattr(build_all_module, "save_state", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(build_all_module, "activity", lambda *_args, **_kwargs: None)

        exit_code = _run_sequential_builds(
            state=state,
            run_dir=tmp_path,
            state_dir=tmp_path,
            target="dalmatian",
            ubuntu_series="noble",
            cloud_archive="",
            build_type="release",
            binary=True,
            force=False,
            local_repo=tmp_path / "repo",
            run=SimpleNamespace(log_event=lambda *_args, **_kwargs: None),
        )

        assert exit_code == EXIT_ALL_BUILD_FAILED
        assert state.packages["b"].status == PackageStatus.SUCCESS
        assert state.packages["c"].status == PackageStatus.FAILED


class TestRunParallelBuilds:
    """Tests for _run_parallel_builds."""

    def test_parallel_builds_mark_failures(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should run builds in parallel and report failures."""
        import packastack.commands.build as build_all_module

        state = create_initial_state(
            run_id="run-1",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
            packages=["a", "b"],
            build_order=["a", "b"],
            keep_going=True,
            parallel=2,
        )
        graph = DependencyGraph()
        graph.add_node("a")
        graph.add_node("b")

        def fake_run_single_build(package: str, **_kwargs: object) -> tuple[bool, FailureType | None, str, str]:
            if package == "b":
                return False, FailureType.BUILD_FAILED, "boom", "/tmp/b.log"
            return True, None, "", f"/tmp/{package}.log"

        monkeypatch.setattr(build_all_module, "_run_single_build", fake_run_single_build)
        monkeypatch.setattr(build_all_module, "save_state", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(build_all_module, "activity", lambda *_args, **_kwargs: None)

        exit_code = _run_parallel_builds(
            state=state,
            graph=graph,
            run_dir=tmp_path,
            state_dir=tmp_path,
            target="dalmatian",
            ubuntu_series="noble",
            cloud_archive="",
            build_type="release",
            binary=True,
            force=False,
            parallel=2,
            local_repo=tmp_path / "repo",
            run=SimpleNamespace(log_event=lambda *_args, **_kwargs: None),
        )

        assert exit_code == EXIT_ALL_BUILD_FAILED
        assert state.packages["a"].status == PackageStatus.SUCCESS
        assert state.packages["b"].status == PackageStatus.FAILED


class TestRunBuildAllResume:
    """Tests for resume behavior in _run_build_all."""

    def test_resume_missing_state_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return EXIT_RESUME_ERROR when resume ID not found."""
        import packastack.commands.build as build_all_module

        monkeypatch.setattr(build_all_module, "load_config", lambda: {"defaults": {}})
        monkeypatch.setattr(
            build_all_module,
            "resolve_paths",
            lambda _cfg: {"cache_root": tmp_path, "runs_root": tmp_path / "runs"},
        )
        monkeypatch.setattr(build_all_module, "load_state", lambda _path: None)
        monkeypatch.setattr(build_all_module, "activity", lambda *_args, **_kwargs: None)
        run = SimpleNamespace(run_id="run-1", log_event=lambda *_args, **_kwargs: None, write_summary=lambda **_kwargs: None)

        exit_code = _call_run_build_all(
            run=run,
            target="dalmatian",
            ubuntu_series="noble",
            cloud_archive="",
            release=True,
            snapshot=False,
            milestone="",
            binary=False,
            keep_going=True,
            max_failures=0,
            resume=True,
            resume_run_id="missing",
            retry_failed=False,
            skip_failed=True,
            parallel=1,
            packages_file="",
            force=False,
            offline=False,
            dry_run=True,
        )

        assert exit_code == EXIT_RESUME_ERROR

    def test_resume_retry_failed_resets_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should reset failed packages to pending when retrying."""
        import packastack.commands.build as build_all_module

        state = create_initial_state(
            run_id="run-1",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
            packages=["a"],
            build_order=["a"],
        )
        state.mark_failed("a", FailureType.BUILD_FAILED, "boom")

        monkeypatch.setattr(build_all_module, "load_config", lambda: {"defaults": {}})
        monkeypatch.setattr(
            build_all_module,
            "resolve_paths",
            lambda _cfg: {"cache_root": tmp_path, "runs_root": tmp_path / "runs"},
        )
        monkeypatch.setattr(build_all_module, "load_state", lambda _path: state)
        monkeypatch.setattr(build_all_module, "activity", lambda *_args, **_kwargs: None)
        run = SimpleNamespace(run_id="run-1", log_event=lambda *_args, **_kwargs: None, write_summary=lambda **_kwargs: None)

        exit_code = _call_run_build_all(
            run=run,
            target="dalmatian",
            ubuntu_series="noble",
            cloud_archive="",
            release=True,
            snapshot=False,
            milestone="",
            binary=False,
            keep_going=True,
            max_failures=0,
            resume=True,
            resume_run_id="",
            retry_failed=True,
            skip_failed=False,
            parallel=1,
            packages_file="",
            force=False,
            offline=False,
            dry_run=True,
        )

        assert exit_code == EXIT_SUCCESS
        assert state.packages["a"].status == PackageStatus.PENDING
        assert state.packages["a"].failure_message == ""


class TestRunBuildAllDiscovery:
    """Tests for discovery errors in _run_build_all."""

    def test_discovery_errors_without_packages_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return EXIT_DISCOVERY_FAILED when no packages found."""
        import packastack.commands.build as build_all_module

        discovery = DiscoveryResult(
            packages=[],
            total_repos=0,
            errors=["boom"],
            source="explicit",
        )

        monkeypatch.setattr(build_all_module, "load_config", lambda: {"defaults": {}})
        monkeypatch.setattr(
            build_all_module,
            "resolve_paths",
            lambda _cfg: {"cache_root": tmp_path, "runs_root": tmp_path / "runs"},
        )
        monkeypatch.setattr(build_all_module, "resolve_series", lambda series: series)
        monkeypatch.setattr(build_all_module, "discover_packages", lambda **_kwargs: discovery)
        monkeypatch.setattr(build_all_module, "activity", lambda *_args, **_kwargs: None)

        run = SimpleNamespace(run_id="run-1", log_event=lambda *_args, **_kwargs: None, write_summary=lambda **_kwargs: None)

        exit_code = _call_run_build_all(
            run=run,
            target="dalmatian",
            ubuntu_series="noble",
            cloud_archive="",
            release=True,
            snapshot=False,
            milestone="",
            binary=False,
            keep_going=True,
            max_failures=0,
            resume=False,
            resume_run_id="",
            retry_failed=False,
            skip_failed=True,
            parallel=1,
            packages_file="",
            force=False,
            offline=False,
            dry_run=True,
        )

        assert exit_code == EXIT_DISCOVERY_FAILED


class TestRunBuildAllCycles:
    """Tests for cycles and suggestions in _run_build_all."""

    def test_cycles_trigger_suggestions_and_graph_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should report cycles and return graph error."""
        import packastack.commands.build as build_all_module

        cfg = {"defaults": {"ubuntu_pockets": ["release"], "ubuntu_components": ["main"]}}
        paths = {
            "cache_root": tmp_path,
            "runs_root": tmp_path / "runs",
            "build_root": tmp_path / "build",
            "local_apt_repo": tmp_path / "apt-repo",
            "ubuntu_archive_cache": tmp_path / "ubuntu-archive",
            "openstack_releases_repo": tmp_path / "releases",
        }

        def fake_discover_packages(**_kwargs: object) -> DiscoveryResult:
            return DiscoveryResult(packages=["a", "b"], total_repos=2, source="explicit")

        graph = DependencyGraph()
        graph.add_edge("a", "b")
        graph.add_edge("b", "a")

        def fake_build_dependency_graph(**_kwargs: object) -> tuple[DependencyGraph, dict[str, list[str]]]:
            return graph, {}

        suggestions = [
            CycleEdgeSuggestion(
                source="a",
                dependency="b",
                upstream_project="openstack/a",
                upstream_version="1.0.0",
                requirements_source="upstream",
                requirements_path="requirements.txt",
            )
            for _ in range(6)
        ]

        events: list[dict[str, object]] = []
        run = SimpleNamespace(
            run_id="run-1",
            log_event=lambda event: events.append(event),
            write_summary=lambda **_kwargs: None,
        )

        monkeypatch.setattr(build_all_module, "load_config", lambda: cfg)
        monkeypatch.setattr(build_all_module, "resolve_paths", lambda _cfg: paths)
        monkeypatch.setattr(build_all_module, "resolve_series", lambda series: series)
        monkeypatch.setattr(build_all_module, "get_host_arch", lambda: "amd64")
        monkeypatch.setattr(build_all_module, "discover_packages", fake_discover_packages)
        monkeypatch.setattr(build_all_module, "_filter_retired_packages", lambda **_kwargs: (["a", "b"], [], []))
        monkeypatch.setattr(build_all_module, "load_package_index", lambda *_args, **_kwargs: PackageIndex())
        monkeypatch.setattr(build_all_module, "load_cloud_archive_index", lambda *_args, **_kwargs: PackageIndex())
        monkeypatch.setattr(build_all_module, "load_local_repo_index", lambda *_args, **_kwargs: PackageIndex())
        monkeypatch.setattr(build_all_module, "merge_package_indexes", lambda *_args: PackageIndex())
        # Patch plan module's _build_dependency_graph since that's what _run_build_all imports
        import packastack.commands.plan as plan_module
        monkeypatch.setattr(plan_module, "_build_dependency_graph", fake_build_dependency_graph)
        monkeypatch.setattr(build_all_module, "load_openstack_packages", lambda *_args, **_kwargs: {})
        monkeypatch.setattr(build_all_module, "suggest_cycle_edge_exclusions", lambda **_kwargs: suggestions)
        monkeypatch.setattr(build_all_module, "activity", lambda *_args, **_kwargs: None)

        exit_code = _call_run_build_all(
            run=run,
            target="dalmatian",
            ubuntu_series="noble",
            cloud_archive="",
            release=True,
            snapshot=False,
            milestone="",
            binary=False,
            keep_going=True,
            max_failures=0,
            resume=False,
            resume_run_id="",
            retry_failed=False,
            skip_failed=True,
            parallel=1,
            packages_file="",
            force=False,
            offline=False,
            dry_run=False,
        )

        assert exit_code == EXIT_GRAPH_ERROR
        assert any(event["event"] == "build_all.cycle_edges" for event in events)
        assert any(event["event"] == "build_all.cycle_exclusion_suggestions" for event in events)


class TestRunBuildAllMissingDeps:
    """Tests for missing dependency recording in _run_build_all."""

    @pytest.mark.skip(reason="Missing deps logic changed - now uses graph.find_missing_dependencies() instead of _build_dependency_graph return")
    def test_records_missing_deps_and_dry_run_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should add missing deps to state before dry run completes."""
        import packastack.commands.build as build_all_module

        cfg = {"defaults": {"ubuntu_pockets": ["release"], "ubuntu_components": ["main"]}}
        paths = {
            "cache_root": tmp_path,
            "runs_root": tmp_path / "runs",
            "build_root": tmp_path / "build",
            "local_apt_repo": tmp_path / "apt-repo",
            "ubuntu_archive_cache": tmp_path / "ubuntu-archive",
        }

        packages = [f"pkg{i}" for i in range(25)]
        graph = DependencyGraph()
        for pkg in packages:
            graph.add_node(pkg)
        graph.topological_sort = lambda: list(packages)

        missing_deps = {"pkg0": ["python3-missing"]}
        captured: dict[str, BuildAllState] = {}

        def fake_build_dependency_graph(**_kwargs: object) -> tuple[DependencyGraph, dict[str, list[str]]]:
            return graph, missing_deps

        def capture_state(state: BuildAllState, _path: Path) -> None:
            captured["state"] = state

        monkeypatch.setattr(build_all_module, "load_config", lambda: cfg)
        monkeypatch.setattr(build_all_module, "resolve_paths", lambda _cfg: paths)
        monkeypatch.setattr(build_all_module, "resolve_series", lambda series: series)
        monkeypatch.setattr(build_all_module, "get_host_arch", lambda: "amd64")
        monkeypatch.setattr(
            build_all_module,
            "discover_packages",
            lambda **_kwargs: DiscoveryResult(packages=packages, total_repos=len(packages), source="explicit"),
        )
        monkeypatch.setattr(build_all_module, "_filter_retired_packages", lambda **_kwargs: (packages, [], []))
        monkeypatch.setattr(build_all_module, "load_package_index", lambda *_args, **_kwargs: PackageIndex())
        monkeypatch.setattr(build_all_module, "load_local_repo_index", lambda *_args, **_kwargs: PackageIndex())
        monkeypatch.setattr(build_all_module, "merge_package_indexes", lambda *_args: PackageIndex())
        # Patch plan module's _build_dependency_graph since that's what _run_build_all imports
        import packastack.commands.plan as plan_module
        monkeypatch.setattr(plan_module, "_build_dependency_graph", fake_build_dependency_graph)
        monkeypatch.setattr(build_all_module, "save_state", capture_state)
        monkeypatch.setattr(build_all_module, "activity", lambda *_args, **_kwargs: None)

        run = SimpleNamespace(run_id="run-1", log_event=lambda *_args, **_kwargs: None, write_summary=lambda **_kwargs: None)

        exit_code = _call_run_build_all(
            run=run,
            target="dalmatian",
            ubuntu_series="noble",
            cloud_archive="",
            release=True,
            snapshot=False,
            milestone="",
            binary=False,
            keep_going=True,
            max_failures=1,
            resume=False,
            resume_run_id="",
            retry_failed=False,
            skip_failed=True,
            parallel=2,
            packages_file="",
            force=False,
            offline=False,
            dry_run=True,
        )

        assert exit_code == EXIT_SUCCESS
        assert "state" in captured
        assert "python3-missing" in captured["state"].missing_deps


class TestRunBuildAllExecution:
    """Tests for full build execution flow."""

    def test_runs_builds_and_writes_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should execute builds, generate reports, and write summary."""
        import packastack.commands.build as build_all_module

        cfg = {"defaults": {"ubuntu_pockets": ["release"], "ubuntu_components": ["main"]}}
        paths = {
            "cache_root": tmp_path,
            "runs_root": tmp_path / "runs",
            "build_root": tmp_path / "build",
            "local_apt_repo": tmp_path / "apt-repo",
            "ubuntu_archive_cache": tmp_path / "ubuntu-archive",
        }

        packages = ["a", "b"]
        graph = DependencyGraph()
        for pkg in packages:
            graph.add_node(pkg)
        graph.topological_sort = lambda: list(packages)

        def fake_discover_packages(**_kwargs: object) -> DiscoveryResult:
            return DiscoveryResult(packages=packages, total_repos=2, source="explicit")

        def fake_build_dependency_graph(**_kwargs: object) -> tuple[DependencyGraph, dict[str, list[str]]]:
            return graph, {}

        def fake_run_sequential_builds(state: BuildAllState, **_kwargs: object) -> int:
            state.mark_success("a")
            state.mark_failed("b", FailureType.BUILD_FAILED, "boom")
            return EXIT_ALL_BUILD_FAILED

        def fake_generate_reports(state: BuildAllState, run_dir: Path) -> tuple[Path, Path]:
            reports_dir = run_dir / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            json_path = reports_dir / "build-all-summary.json"
            md_path = reports_dir / "build-all-summary.md"
            json_path.write_text("{}")
            md_path.write_text("#")
            return json_path, md_path

        summary: dict[str, object] = {}
        run = SimpleNamespace(
            run_id="run-1",
            log_event=lambda *_args, **_kwargs: None,
            write_summary=lambda **kwargs: summary.update(kwargs),
        )

        monkeypatch.setattr(build_all_module, "load_config", lambda: cfg)
        monkeypatch.setattr(build_all_module, "resolve_paths", lambda _cfg: paths)
        monkeypatch.setattr(build_all_module, "resolve_series", lambda series: series)
        monkeypatch.setattr(build_all_module, "discover_packages", fake_discover_packages)
        monkeypatch.setattr(build_all_module, "_filter_retired_packages", lambda **_kwargs: (packages, [], []))
        monkeypatch.setattr(build_all_module, "load_package_index", lambda *_args, **_kwargs: PackageIndex())
        monkeypatch.setattr(build_all_module, "load_local_repo_index", lambda *_args, **_kwargs: PackageIndex())
        monkeypatch.setattr(build_all_module, "merge_package_indexes", lambda *_args: PackageIndex())
        # Patch plan module's _build_dependency_graph since that's what _run_build_all imports
        import packastack.commands.plan as plan_module
        monkeypatch.setattr(plan_module, "_build_dependency_graph", fake_build_dependency_graph)
        monkeypatch.setattr(build_all_module, "_run_sequential_builds", fake_run_sequential_builds)
        monkeypatch.setattr(build_all_module, "_generate_reports", fake_generate_reports)
        monkeypatch.setattr(build_all_module, "save_state", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(build_all_module, "activity", lambda *_args, **_kwargs: None)

        exit_code = _call_run_build_all(
            run=run,
            target="dalmatian",
            ubuntu_series="noble",
            cloud_archive="",
            release=True,
            snapshot=False,
            milestone="",
            binary=False,
            keep_going=True,
            max_failures=0,
            resume=False,
            resume_run_id="",
            retry_failed=False,
            skip_failed=True,
            parallel=1,
            packages_file="",
            force=False,
            offline=False,
            dry_run=False,
        )

        assert exit_code == EXIT_ALL_BUILD_FAILED
        assert summary["failed"] == 1


class TestRunBuildAllRetired:
    """Tests for retired package exclusions in _run_build_all."""

    def test_logs_retired_exclusions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should log retired and possibly retired exclusions."""
        import packastack.commands.build as build_all_module

        cfg = {"defaults": {"ubuntu_pockets": ["release"], "ubuntu_components": ["main"]}}
        paths = {
            "cache_root": tmp_path,
            "runs_root": tmp_path / "runs",
            "build_root": tmp_path / "build",
            "local_apt_repo": tmp_path / "apt-repo",
            "ubuntu_archive_cache": tmp_path / "ubuntu-archive",
        }

        def fake_discover_packages(**_kwargs: object) -> DiscoveryResult:
            return DiscoveryResult(packages=["a", "b", "c"], total_repos=3, source="explicit")

        events: list[dict[str, object]] = []
        run = SimpleNamespace(run_id="run-1", log_event=lambda event: events.append(event), write_summary=lambda **_kwargs: None)

        monkeypatch.setattr(build_all_module, "load_config", lambda: cfg)
        monkeypatch.setattr(build_all_module, "resolve_paths", lambda _cfg: paths)
        monkeypatch.setattr(build_all_module, "resolve_series", lambda series: series)
        monkeypatch.setattr(build_all_module, "discover_packages", fake_discover_packages)
        monkeypatch.setattr(
            build_all_module,
            "_filter_retired_packages",
            lambda **_kwargs: (["c"], ["a"], ["b"]),
        )
        monkeypatch.setattr(build_all_module, "load_package_index", lambda *_args, **_kwargs: PackageIndex())
        monkeypatch.setattr(build_all_module, "load_local_repo_index", lambda *_args, **_kwargs: PackageIndex())
        monkeypatch.setattr(build_all_module, "merge_package_indexes", lambda *_args: PackageIndex())
        monkeypatch.setattr(
            build_all_module,
            "_build_dependency_graph",
            lambda **_kwargs: (DependencyGraph(), {}),
        )
        monkeypatch.setattr(build_all_module, "activity", lambda *_args, **_kwargs: None)

        exit_code = _call_run_build_all(
            run=run,
            target="dalmatian",
            ubuntu_series="noble",
            cloud_archive="",
            release=True,
            snapshot=False,
            milestone="",
            binary=False,
            keep_going=True,
            max_failures=0,
            resume=False,
            resume_run_id="",
            retry_failed=False,
            skip_failed=True,
            parallel=1,
            packages_file="",
            force=False,
            offline=False,
            dry_run=True,
        )

        assert exit_code == EXIT_SUCCESS
        assert any(event["event"] == "build_all.retired_excluded" for event in events)
        assert any(event["event"] == "build_all.possibly_retired_excluded" for event in events)


class TestRunBuildAllDevelTarget:
    """Tests for devel target resolution."""

    def test_falls_back_to_devel_when_series_unknown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should use 'devel' when current development series is unknown."""
        import packastack.commands.build as build_all_module

        cfg = {"defaults": {"ubuntu_pockets": ["release"], "ubuntu_components": ["main"]}}
        paths = {
            "cache_root": tmp_path,
            "runs_root": tmp_path / "runs",
            "build_root": tmp_path / "build",
            "local_apt_repo": tmp_path / "apt-repo",
            "ubuntu_archive_cache": tmp_path / "ubuntu-archive",
            "openstack_releases_repo": tmp_path / "releases",
        }

        messages: list[str] = []

        monkeypatch.setattr(build_all_module, "load_config", lambda: cfg)
        monkeypatch.setattr(build_all_module, "resolve_paths", lambda _cfg: paths)
        monkeypatch.setattr(build_all_module, "resolve_series", lambda series: series)
        monkeypatch.setattr(build_all_module, "get_current_development_series", lambda _path: None)
        monkeypatch.setattr(
            build_all_module,
            "discover_packages",
            lambda **_kwargs: DiscoveryResult(packages=["a"], total_repos=1, source="explicit"),
        )
        monkeypatch.setattr(
            build_all_module,
            "_build_dependency_graph",
            lambda **_kwargs: (DependencyGraph(), {}),
        )
        monkeypatch.setattr(build_all_module, "load_package_index", lambda *_args, **_kwargs: PackageIndex())
        monkeypatch.setattr(build_all_module, "load_local_repo_index", lambda *_args, **_kwargs: PackageIndex())
        monkeypatch.setattr(build_all_module, "merge_package_indexes", lambda *_args: PackageIndex())
        monkeypatch.setattr(build_all_module, "activity", lambda _scope, msg: messages.append(msg))

        run = SimpleNamespace(run_id="run-1", log_event=lambda *_args, **_kwargs: None, write_summary=lambda **_kwargs: None)

        exit_code = _call_run_build_all(
            run=run,
            target="devel",
            ubuntu_series="noble",
            cloud_archive="",
            release=True,
            snapshot=False,
            milestone="b1",
            binary=False,
            keep_going=True,
            max_failures=0,
            resume=False,
            resume_run_id="",
            retry_failed=False,
            skip_failed=True,
            parallel=0,
            packages_file="",
            force=False,
            offline=False,
            dry_run=True,
        )

        assert exit_code == EXIT_SUCCESS
        assert any("OpenStack devel" in msg for msg in messages)


class TestBuildAllCli:
    """Tests for run_build_all wrapper (called by build --all)."""

    def test_run_build_all_returns_exit_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return the code from _run_build_all."""
        import packastack.commands.build as build_module

        class DummyRun:
            run_id = "run-1"

            def __enter__(self) -> "DummyRun":
                return self

            def __exit__(self, _exc_type, _exc, _tb) -> None:
                return None

        monkeypatch.setattr(build_module, "RunContext", lambda *_args, **_kwargs: DummyRun())
        monkeypatch.setattr(build_module, "_run_build_all", lambda run, request: EXIT_SUCCESS)

        exit_code = run_build_all(
            target="devel",
            ubuntu_series="devel",
            cloud_archive="",
            build_type="release",
            milestone="",
            binary=True,
            keep_going=True,
            max_failures=0,
            resume=False,
            resume_run_id="",
            retry_failed=False,
            skip_failed=True,
            parallel=0,
            packages_file="",
            force=False,
            offline=False,
            dry_run=False,
        )

        assert exit_code == EXIT_SUCCESS
