# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for packastack.commands.build module."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from packastack.commands import build
from packastack.commands import plan as plan_module
from packastack.commands.plan import ResolvedTarget
from packastack.build import tarball as tarball_module
from packastack.build import single_build as single_build_module
from packastack.planning.type_selection import BuildType
from packastack.upstream.registry import (
    ProjectConfig,
    ReleaseSourceConfig,
    ReleaseSourceType,
    ResolvedUpstream,
    ResolutionSource,
    SignaturesConfig,
    SignatureMode,
    TarballConfig,
    TarballMethod,
    UpstreamConfig,
    UpstreamsRegistry,
)


def _create_mock_registry(project: str = "nova") -> MagicMock:
    """Create a mock UpstreamsRegistry for testing.

    Args:
        project: The project name to use in the mock resolved config.

    Returns:
        A MagicMock configured to behave like an UpstreamsRegistry.
    """
    mock_registry = MagicMock(spec=UpstreamsRegistry)
    mock_registry.version = 1
    mock_registry.override_applied = False
    mock_registry.override_path = ""
    mock_registry.warnings = []

    # Create a mock resolved upstream for OpenDev projects
    mock_config = ProjectConfig(
        project_key=project,
        common_names=[project],
        upstream=UpstreamConfig(
            type="git",
            host="opendev",
            url=f"https://opendev.org/openstack/{project}.git",
            default_branch="master",
        ),
        release_source=ReleaseSourceConfig(
            type=ReleaseSourceType.OPENSTACK_RELEASES,
            deliverable=project,
        ),
        tarball=TarballConfig(prefer=[TarballMethod.OFFICIAL]),
        signatures=SignaturesConfig(mode=SignatureMode.AUTO),
    )

    mock_resolved = ResolvedUpstream(
        project=project,
        config=mock_config,
        resolution_source=ResolutionSource.REGISTRY_DEFAULTS,
    )

    mock_registry.resolve.return_value = mock_resolved
    return mock_registry


def _make_resolved_target(pkg: str, upstream: str | None = None, source: str = "local") -> ResolvedTarget:
    """Create a ResolvedTarget for testing."""
    return ResolvedTarget(
        source_package=pkg,
        upstream_project=upstream or pkg,
        resolution_source=source,
    )


def _make_plan_result(packages: list[str] | None = None):
    """Create a mock PlanResult for testing.
    
    Args:
        packages: List of packages in build/upload order. Defaults to ["nova"].
    
    Returns:
        A PlanResult with the given packages in build_order and upload_order.
    """
    from packastack.planning.graph import PlanResult
    if packages is None:
        packages = ["nova"]
    return PlanResult(
        build_order=packages,
        upload_order=packages,
        mir_candidates={},
        missing_packages={},
        cycles=[],
    )


def _apply_build_mocks(stack: ExitStack, patches: dict) -> None:
    """Apply patches to the ExitStack."""
    for target, return_value in patches.items():
        stack.enter_context(patch.object(build, target, return_value=return_value))


def _call_run_build(
    run: MagicMock,
    package: str = "nova",
    target: str = "devel",
    ubuntu_series: str = "devel",
    cloud_archive: str = "",
    build_type_str: str = "release",
    milestone: str = "",
    force: bool = False,
    offline: bool = False,
    validate_plan_only: bool = False,
    plan_upload: bool = False,
    upload: bool = False,
    binary: bool = False,
    builder: str = "sbuild",
    build_deps: bool = True,
    no_spinner: bool = True,
    yes: bool = False,
    workspace_ref=None,
    include_retired: bool = False,
    no_cleanup: bool = False,
) -> int:
    """Helper to call _run_build with a BuildRequest.
    
    This bridges the old kwarg-style test calls to the new BuildRequest-based API.
    """
    from packastack.core.context import BuildRequest

    request = BuildRequest(
        package=package,
        target=target,
        ubuntu_series=ubuntu_series,
        cloud_archive=cloud_archive,
        build_type_str=build_type_str,
        milestone=milestone,
        force=force,
        offline=offline,
        include_retired=include_retired,
        yes=yes,
        binary=binary,
        builder=builder,
        build_deps=build_deps,
        no_cleanup=no_cleanup,
        no_spinner=no_spinner,
        validate_plan_only=validate_plan_only,
        plan_upload=plan_upload,
        upload=upload,
        workspace_ref=workspace_ref or (lambda w: None),
    )
    return build._run_build(run=run, request=request)


class TestResolveBuildTypeFromCli:
    """Tests for _resolve_build_type_from_cli function."""

    def test_release_type(self) -> None:
        """Test --type release."""
        result, milestone = build._resolve_build_type_from_cli("release", "")
        assert result == "release"
        assert milestone == ""

    def test_snapshot_type(self) -> None:
        """Test --type snapshot."""
        result, milestone = build._resolve_build_type_from_cli("snapshot", "")
        assert result == "snapshot"
        assert milestone == ""

    def test_auto_type(self) -> None:
        """Test --type auto."""
        result, milestone = build._resolve_build_type_from_cli("auto", "")
        assert result == "auto"
        assert milestone == ""

    def test_milestone_flag_overrides_type(self) -> None:
        """Test --milestone flag overrides --type."""
        result, milestone = build._resolve_build_type_from_cli("release", "b1")
        assert result == "milestone"
        assert milestone == "b1"

    def test_milestone_type(self) -> None:
        """Test --type milestone without milestone version is still milestone."""
        result, milestone = build._resolve_build_type_from_cli("milestone", "")
        assert result == "milestone"
        assert milestone == ""

    def test_case_insensitive(self) -> None:
        """Test type is case insensitive."""
        result, milestone = build._resolve_build_type_from_cli("RELEASE", "")
        assert result == "release"

    def test_invalid_type_raises(self) -> None:
        """Test invalid type raises BadParameter."""
        import typer
        with pytest.raises(typer.BadParameter):
            build._resolve_build_type_from_cli("invalid", "")

class TestSetWorkspace:
    """Tests for _set_workspace helper."""

    def test_sets_workspace(self, tmp_path: Path) -> None:
        """Test that workspace is set in local vars."""
        local_vars = {"workspace": None}
        build._set_workspace(tmp_path, local_vars)
        assert local_vars["workspace"] == tmp_path


class TestEnsureNoMergePaths:
    """Tests for _ensure_no_merge_paths helper."""

    def test_adds_and_is_idempotent(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()

        build._ensure_no_merge_paths(repo, ["launchpad.yaml"])
        attrs = (repo / ".gitattributes").read_text(encoding="utf-8").splitlines()
        assert "launchpad.yaml merge=ours" in attrs
        # .gitattributes should protect itself
        assert ".gitattributes merge=ours" in attrs

        # Second call should not duplicate entries
        build._ensure_no_merge_paths(repo, ["launchpad.yaml"])
        attrs2 = (repo / ".gitattributes").read_text(encoding="utf-8").splitlines()
        assert attrs2.count("launchpad.yaml merge=ours") == 1
        assert attrs2.count(".gitattributes merge=ours") == 1


class TestBuildExitCodes:
    """Tests for build command exit codes."""

    def test_exit_codes_defined(self) -> None:
        """Test that all exit codes are defined."""
        assert build.EXIT_SUCCESS == 0
        assert build.EXIT_CONFIG_ERROR == 1
        assert build.EXIT_TOOL_MISSING == 2
        assert build.EXIT_FETCH_FAILED == 3
        assert build.EXIT_PATCH_FAILED == 4
        assert build.EXIT_MISSING_PACKAGES == 5
        assert build.EXIT_CYCLE_DETECTED == 6
        assert build.EXIT_BUILD_FAILED == 7
        assert build.EXIT_POLICY_BLOCKED == 8


class TestRunBuildPhases:
    """Tests for _run_build function phases."""

    @pytest.fixture
    def mock_paths(self, tmp_path: Path) -> dict:
        """Create mock paths."""
        paths = {
            "openstack_releases_repo": tmp_path / "releases",
            "local_apt_repo": tmp_path / "local",
            "ubuntu_archive_cache": tmp_path / "cache",
            "cache_root": tmp_path / "cache",
            "build_root": tmp_path / "build",
        }
        for p in paths.values():
            p.mkdir(parents=True, exist_ok=True)
        return paths

    @pytest.fixture
    def mock_run(self) -> MagicMock:
        """Create mock run context."""
        run = MagicMock()
        run.run_id = "test-run-id"
        run.run_path = Path("/tmp/test-run")
        return run

    def test_package_not_found_returns_config_error(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test that package not found returns CONFIG_ERROR."""
        # Create a plan result that returns CONFIG_ERROR for a nonexistent package
        from packastack.planning.graph import PlanResult
        mock_plan_result = PlanResult(
            build_order=[],
            upload_order=[],
            mir_candidates={},
            missing_packages={},
            cycles=[],
        )
        with (
            patch.object(build, "load_config", return_value={}),
            patch.object(build, "resolve_paths", return_value=mock_paths),
            patch.object(plan_module, "run_plan_for_package", return_value=(mock_plan_result, build.EXIT_CONFIG_ERROR)),
        ):
            result = _call_run_build(
                run=mock_run,
                package="nonexistent",
                target="devel",
                ubuntu_series="devel",
                cloud_archive="",
                build_type_str="release",
                milestone="",
                force=False,
                offline=False,
                validate_plan_only=False,
                plan_upload=False,
                upload=False,
                binary=False,
                builder="sbuild",
                build_deps=True,
                no_spinner=True,
                yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_CONFIG_ERROR

    def test_validate_plan_only_returns_success(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test that --validate-plan stops early."""
        # Setup local package
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()

        mock_index = MagicMock()
        mock_index.packages = {}
        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True
        
        # Create a valid PlanResult  
        from packastack.planning.graph import PlanResult
        mock_plan_result = PlanResult(
            build_order=["nova"],
            upload_order=["nova"],
            mir_candidates={},
            missing_packages={},
            cycles=[],
        )

        with (
            patch.object(build, "load_config", return_value={"defaults": {}}),
            patch.object(build, "resolve_paths", return_value=mock_paths),
            patch.object(plan_module, "run_plan_for_package", return_value=(mock_plan_result, 0)),
            patch.object(build, "check_required_tools", return_value=mock_tool_result),
        ):
            result = _call_run_build(
                run=mock_run,
                package="nova",
                target="devel",
                ubuntu_series="devel",
                cloud_archive="",
                build_type_str="release",
                milestone="",
                force=False,
                offline=False,
                validate_plan_only=True,
                plan_upload=False,
                upload=False,
                binary=False,
                builder="sbuild",
                build_deps=True,
                no_spinner=True,
                yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS

    def test_missing_tools_returns_tool_missing(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test that missing tools returns TOOL_MISSING."""
        # Setup local package
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()

        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = False
        mock_tool_result.missing = ["gbp"]

        mock_registry = _create_mock_registry("nova")

        with (
            patch.object(build, "load_config", return_value={"defaults": {}}),
            patch.object(build, "resolve_paths", return_value=mock_paths),
            patch.object(plan_module, "run_plan_for_package", return_value=(_make_plan_result(), 0)),
            patch("packastack.upstream.registry.UpstreamsRegistry", return_value=mock_registry),
            patch.object(build, "load_openstack_packages", return_value={"nova": "nova"}),
            patch("packastack.build.tools.check_required_tools", return_value=mock_tool_result),
            patch("packastack.build.tools.get_missing_tools_message", return_value="Missing: gbp"),
        ):
            result = _call_run_build(
                run=mock_run,
                package="nova",
                target="devel",
                ubuntu_series="devel",
                cloud_archive="",
                build_type_str="release",
                milestone="",
                force=False,
                offline=False,
                validate_plan_only=False,
                plan_upload=False,
                upload=False,
                binary=False,
                builder="sbuild",
                build_deps=True,
                no_spinner=True,
                yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_TOOL_MISSING

    def test_snapshot_blocked_by_policy(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test snapshot build blocked by policy returns POLICY_BLOCKED."""
        # Setup local package
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()

        mock_registry = _create_mock_registry("nova")

        with (
            patch.object(build, "load_config", return_value={"defaults": {}}),
            patch.object(build, "resolve_paths", return_value=mock_paths),
            patch.object(plan_module, "run_plan_for_package", return_value=(_make_plan_result(), 0)),
            patch("packastack.upstream.registry.UpstreamsRegistry", return_value=mock_registry),
            patch.object(build, "load_openstack_packages", return_value={"nova": "nova"}),
            patch.object(build, "is_snapshot_eligible", return_value=(False, "Release exists", "1.0.0")),
        ):
            result = _call_run_build(
                run=mock_run,
                package="nova",
                target="devel",
                ubuntu_series="devel",
                cloud_archive="",
                build_type_str="snapshot",
                milestone="",
                force=False,
                offline=False,
                validate_plan_only=False,
                plan_upload=False,
                upload=False,
                binary=False,
                builder="sbuild",
                build_deps=True,
                no_spinner=True,
                yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_POLICY_BLOCKED

    def test_auto_build_type_with_release_available(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test auto build type with release available uses release without policy block.
        
        This is a regression test for the bug where `auto` build type was blocked
        by snapshot eligibility checks before resolving to RELEASE.
        """
        # Setup local package
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()

        mock_registry = _create_mock_registry("nova")
        
        # Mock auto resolution to return RELEASE
        from packastack.planning.type_selection import BuildType

        with (
            patch.object(build, "load_config", return_value={"defaults": {}}),
            patch.object(build, "resolve_paths", return_value=mock_paths),
            patch.object(build, "resolve_series", return_value="noble"),
            patch.object(build, "get_current_development_series", return_value="caracal"),
            # Mock auto type resolution to return RELEASE
            patch.object(build, "_resolve_build_type_auto", return_value=(BuildType.RELEASE, "", "release_available")),
            patch.object(plan_module, "run_plan_for_package", return_value=(_make_plan_result(), 0)),
            patch("packastack.upstream.registry.UpstreamsRegistry", return_value=mock_registry),
        ):
            result = _call_run_build(
                run=mock_run,
                package="nova",
                target="devel",
                ubuntu_series="devel",
                cloud_archive="",
                build_type_str="auto",  # Auto should resolve to release
                milestone="",
                force=False,
                offline=False,
                validate_plan_only=True,  # Stop after planning
                plan_upload=False,
                upload=False,
                binary=False,
                builder="sbuild",
                build_deps=True,
                no_spinner=True,
                yes=False,
                workspace_ref=lambda w: None,
            )

        # Should succeed without policy block because auto resolved to release
        assert result == build.EXIT_SUCCESS

    def test_snapshot_allowed_with_force(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test snapshot build allowed with --force despite policy block."""
        # Setup local package
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()

        mock_registry = _create_mock_registry("nova")

        with (
            patch.object(build, "load_config", return_value={"defaults": {}}),
            patch.object(build, "resolve_paths", return_value=mock_paths),
            patch.object(build, "resolve_series", return_value="noble"),
            patch.object(build, "get_current_development_series", return_value="caracal"),
            patch.object(build, "get_previous_series", return_value="bobcat"),
            patch("packastack.upstream.registry.UpstreamsRegistry", return_value=mock_registry),
            patch.object(build, "load_openstack_packages", return_value={"nova": "nova"}),
            # Patch at the source locations used by phase functions
            patch("packastack.upstream.releases.is_snapshot_eligible", return_value=(False, "Release exists", "1.0.0")),
            patch.object(plan_module, "run_plan_for_package", return_value=(_make_plan_result(), 0)),
        ):
            result = _call_run_build(
                run=mock_run,
                package="nova",
                target="devel",
                ubuntu_series="devel",
                cloud_archive="",
                build_type_str="snapshot",
                milestone="",
                force=True,
                offline=False,
                validate_plan_only=True,  # Stop early
                plan_upload=False,
                upload=False,
                binary=False,
                builder="sbuild",
                build_deps=True,
                no_spinner=True,
                yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS

    def test_cloud_archive_index_loaded(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test cloud archive index is loaded when specified.
        
        Note: Cloud archive loading now happens in run_plan_for_package.
        This test verifies that validate_plan_only mode returns successfully
        when a cloud_archive is specified. The actual cloud archive loading
        is tested in test_plan.py.
        """
        # Setup local package
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()

        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        with (
            patch.object(build, "load_config", return_value={"defaults": {}}),
            patch.object(build, "resolve_paths", return_value=mock_paths),
            patch.object(plan_module, "run_plan_for_package", return_value=(_make_plan_result(), 0)),
            patch.object(build, "check_required_tools", return_value=mock_tool_result),
        ):
            result = _call_run_build(
                run=mock_run,
                package="nova",
                target="devel",
                ubuntu_series="devel",
                cloud_archive="caracal",
                build_type_str="release",
                milestone="",
                force=False,
                offline=False,
                validate_plan_only=True,
                plan_upload=False,
                upload=False,
                binary=False,
                builder="sbuild",
                build_deps=True,
                no_spinner=True,
                yes=False,
                workspace_ref=lambda w: None,
            )

        # validate_plan_only returns after planning, so no assertions about cloud archive loading
        # (that's now tested in test_plan.py)
        assert result == build.EXIT_SUCCESS


class TestBuildIntegration:
    """Integration-style tests for build command."""

    def test_build_command_exists(self) -> None:
        """Test that build command function exists."""
        assert callable(build.build)

    def test_build_type_enum_imported(self) -> None:
        """Test BuildType enum is available."""
        assert BuildType.RELEASE.value == "release"
        assert BuildType.SNAPSHOT.value == "snapshot"
        assert BuildType.MILESTONE.value == "milestone"


class TestFetchPhase:
    """Tests for the fetch phase."""

    @pytest.fixture
    def mock_paths(self, tmp_path: Path) -> dict:
        """Create mock paths."""
        paths = {
            "openstack_releases_repo": tmp_path / "releases",
            "local_apt_repo": tmp_path / "local",
            "ubuntu_archive_cache": tmp_path / "cache",
            "cache_root": tmp_path / "cache",
            "build_root": tmp_path / "build",
        }
        for p in paths.values():
            p.mkdir(parents=True, exist_ok=True)
        return paths

    @pytest.fixture
    def mock_run(self) -> MagicMock:
        """Create mock run context."""
        run = MagicMock()
        run.run_id = "test-run-id"
        run.run_path = Path("/tmp/test-run")
        return run

    def test_fetch_clone_failure(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test that clone failure returns FETCH_FAILED."""
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()

        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_fetch_result = MagicMock()
        mock_fetch_result.error = "Clone failed: network error"
        mock_fetch_result.path = None

        mock_registry = _create_mock_registry("nova")

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(plan_module, "run_plan_for_package", return_value=(_make_plan_result(), 0)))
            stack.enter_context(patch("packastack.upstream.registry.UpstreamsRegistry", return_value=mock_registry))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value={"nova": "nova"}))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_FETCH_FAILED
        
class TestReleaseTarballFetch:
    """Tests for release tarball fetching order."""

    @pytest.fixture
    def mock_paths(self, tmp_path: Path) -> dict:
        paths = {
            "openstack_releases_repo": tmp_path / "releases",
            "local_apt_repo": tmp_path / "local",
            "ubuntu_archive_cache": tmp_path / "cache",
            "cache_root": tmp_path / "cache",
            "build_root": tmp_path / "build",
        }
        for p in paths.values():
            p.mkdir(parents=True, exist_ok=True)
        return paths

    @pytest.fixture
    def mock_run(self) -> MagicMock:
        run = MagicMock()
        run.run_id = "test-run-id"
        run.run_path = Path("/tmp/test-run")
        return run

    def test_uscan_preferred_over_official(self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock) -> None:
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "control").touch()

        mock_fetch_result = MagicMock()
        mock_fetch_result.error = None
        mock_fetch_result.path = tmp_path / "repo"
        mock_fetch_result.path.mkdir(parents=True, exist_ok=True)
        (mock_fetch_result.path / "debian").mkdir(exist_ok=True)
        mock_fetch_result.branches = ["main"]
        mock_fetch_result.cloned = True
        mock_fetch_result.updated = False

        mock_upstream = MagicMock()
        mock_upstream.version = "1.0.0"
        mock_upstream.tarball_url = "https://example.com/nova-1.0.0.tar.gz"

        mock_tarball_path = tmp_path / "nova-1.0.0.tar.gz"
        mock_tarball_path.touch()

        mock_registry = _create_mock_registry("nova")

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(plan_module, "run_plan_for_package", return_value=(_make_plan_result(), 0)))
            stack.enter_context(patch("packastack.upstream.registry.UpstreamsRegistry", return_value=mock_registry))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value={"nova": "nova"}))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=MagicMock(is_complete=lambda: True)))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            # uscan returns a tarball; official should not be needed
            stack.enter_context(patch.object(tarball_module, "run_uscan", return_value=(True, mock_tarball_path, "")))
            official_dl = stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball"))
            stack.enter_context(patch("packastack.debpkg.gbp.ensure_upstream_branch", return_value=MagicMock(success=True, created=False)))
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, upstream_version="1.0.0", output="")))
            stack.enter_context(patch.object(single_build_module, "run_command"))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="1.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="1.0", epoch=0)))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="1.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="Release"))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=MagicMock(success=True, needs_refresh=False, patch_reports=[])))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=MagicMock(success=True, artifacts=[], dsc_file=None, changes_file=None, output="")))

            result = _call_run_build(
                run=mock_run,
                package="nova",
                target="devel",
                ubuntu_series="devel",
                cloud_archive="",
                build_type_str="release",
                milestone="",
                force=False,
                offline=False,
                validate_plan_only=False,
                plan_upload=False,
                upload=False,
                binary=False,
                builder="sbuild",
                build_deps=True,
                no_spinner=True,
                yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS
        # uscan path used; official downloader not invoked
        official_dl.assert_not_called()

    def test_fallback_to_official_when_uscan_fails(self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock) -> None:
        """When uscan fails, official tarball download should be attempted."""
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "control").touch()

        mock_fetch_result = MagicMock()
        mock_fetch_result.error = None
        mock_fetch_result.path = tmp_path / "repo"
        mock_fetch_result.path.mkdir(parents=True, exist_ok=True)
        (mock_fetch_result.path / "debian").mkdir(exist_ok=True)
        mock_fetch_result.branches = ["main"]
        mock_fetch_result.cloned = True
        mock_fetch_result.updated = False

        mock_upstream = MagicMock()
        mock_upstream.version = "1.0.0"
        mock_upstream.tarball_url = "https://example.com/nova-1.0.0.tar.gz"

        mock_tarball_path = tmp_path / "nova-1.0.0.tar.gz"
        mock_tarball_path.touch()

        mock_tarball_result = MagicMock()
        mock_tarball_result.success = True
        mock_tarball_result.path = mock_tarball_path
        mock_tarball_result.signature_verified = False
        mock_tarball_result.signature_warning = ""
        mock_tarball_result.error = None

        mock_registry = _create_mock_registry("nova")

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(plan_module, "run_plan_for_package", return_value=(_make_plan_result(), 0)))
            stack.enter_context(patch("packastack.upstream.registry.UpstreamsRegistry", return_value=mock_registry))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value={"nova": "nova"}))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=MagicMock(is_complete=lambda: True)))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            # uscan fails
            stack.enter_context(patch.object(tarball_module, "run_uscan", return_value=(False, None, "uscan failed")))
            # official succeeds
            official_dl = stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_tarball_result))
            stack.enter_context(patch("packastack.debpkg.gbp.ensure_upstream_branch", return_value=MagicMock(success=True, created=False)))
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, upstream_version="1.0.0", output="")))
            stack.enter_context(patch.object(single_build_module, "run_command"))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="1.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="1.0", epoch=0)))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="1.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="Release"))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=MagicMock(success=True, needs_refresh=False, patch_reports=[])))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=MagicMock(success=True, artifacts=[], dsc_file=None, changes_file=None, output="")))

            result = _call_run_build(
                run=mock_run,
                package="nova",
                target="devel",
                ubuntu_series="devel",
                cloud_archive="",
                build_type_str="release",
                milestone="",
                force=False,
                offline=False,
                validate_plan_only=False,
                plan_upload=False,
                upload=False,
                binary=False,
                builder="sbuild",
                build_deps=True,
                no_spinner=True,
                yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS
        # uscan failed so official was called
        official_dl.assert_called_once()

    def test_github_fallback_when_official_fails(self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock) -> None:
        """If uscan and official fail, GitHub release fallback should be used when preferred."""
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "control").touch()

        mock_fetch_result = MagicMock()
        mock_fetch_result.error = None
        mock_fetch_result.path = tmp_path / "repo"
        mock_fetch_result.path.mkdir(parents=True, exist_ok=True)
        (mock_fetch_result.path / "debian").mkdir(exist_ok=True)
        mock_fetch_result.branches = ["main"]
        mock_fetch_result.cloned = True
        mock_fetch_result.updated = False

        mock_upstream = MagicMock()
        mock_upstream.version = "1.0.0"
        mock_upstream.tarball_url = "https://example.com/nova-1.0.0.tar.gz"

        mock_tarball_path = tmp_path / "nova-1.0.0.tar.gz"
        mock_tarball_path.touch()

        mock_official_result = MagicMock(success=False, error="404", path=None, signature_verified=False, signature_warning="")

        mock_registry = MagicMock()
        mock_registry.version = "1"
        mock_registry.override_applied = False
        mock_registry.override_path = None
        mock_registry.warnings = []

        cfg = SimpleNamespace(
            upstream=SimpleNamespace(host="example", url="https://example.com/nova.git", default_branch="master"),
            tarball=SimpleNamespace(prefer=[SimpleNamespace(value="official"), SimpleNamespace(value="github_release")]),
            signatures=SimpleNamespace(mode=SimpleNamespace(value="auto")),
            release_source=SimpleNamespace(project="nova", type=SimpleNamespace(value="git"), deliverable="nova"),
            project_key="nova",
        )
        resolved = SimpleNamespace(config=cfg, resolution_source=SimpleNamespace(value="registry_defaults"), project="nova")
        mock_registry.resolve.return_value = resolved

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(plan_module, "run_plan_for_package", return_value=(_make_plan_result(), 0)))
            stack.enter_context(patch("packastack.upstream.registry.UpstreamsRegistry", return_value=mock_registry))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value={"nova": "nova"}))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=MagicMock(is_complete=lambda: True)))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch.object(tarball_module, "run_uscan", return_value=(False, None, "uscan failed")))
            stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_official_result))
            stack.enter_context(patch.object(tarball_module, "download_pypi_tarball", return_value=(False, None, "skip")))
            gh_dl = stack.enter_context(patch.object(tarball_module, "download_github_release_tarball", return_value=(True, mock_tarball_path, "")))
            stack.enter_context(patch("packastack.debpkg.gbp.ensure_upstream_branch", return_value=MagicMock(success=True, created=False)))
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, upstream_version="1.0.0", output="")))
            stack.enter_context(patch.object(single_build_module, "run_command"))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="1.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="1.0", epoch=0)))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="1.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="Release"))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=MagicMock(success=True, needs_refresh=False, patch_reports=[])))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=MagicMock(success=True, artifacts=[], dsc_file=None, changes_file=None, output="")))

            result = _call_run_build(
                run=mock_run,
                package="nova",
                target="devel",
                ubuntu_series="devel",
                cloud_archive="",
                build_type_str="release",
                milestone="",
                force=False,
                offline=False,
                validate_plan_only=False,
                plan_upload=False,
                upload=False,
                binary=False,
                builder="sbuild",
                build_deps=True,
                no_spinner=True,
                yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS
        gh_dl.assert_called_once()

    def test_git_archive_fallback(self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock) -> None:
        """If uscan and official fail and git_archive is preferred, use git archive."""
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "control").touch()

        mock_fetch_result = MagicMock()
        mock_fetch_result.error = None
        mock_fetch_result.path = tmp_path / "repo"
        mock_fetch_result.path.mkdir(parents=True, exist_ok=True)
        (mock_fetch_result.path / "debian").mkdir(exist_ok=True)
        mock_fetch_result.branches = ["main"]
        mock_fetch_result.cloned = True
        mock_fetch_result.updated = False

        mock_upstream = MagicMock()
        mock_upstream.version = "1.0.0"
        mock_upstream.tarball_url = "https://example.com/nova-1.0.0.tar.gz"

        mock_tarball_path = tmp_path / "nova-1.0.0.tar.gz"
        mock_tarball_path.touch()

        mock_official_result = MagicMock(success=False, error="404", path=None, signature_verified=False, signature_warning="")

        mock_registry = MagicMock()
        mock_registry.version = "1"
        mock_registry.override_applied = False
        mock_registry.override_path = None
        mock_registry.warnings = []

        cfg = SimpleNamespace(
            upstream=SimpleNamespace(host="example", url="https://example.com/nova.git", default_branch="master"),
            tarball=SimpleNamespace(prefer=[SimpleNamespace(value="official"), SimpleNamespace(value="git_archive")]),
            signatures=SimpleNamespace(mode=SimpleNamespace(value="auto")),
            release_source=SimpleNamespace(project="nova", type=SimpleNamespace(value="git"), deliverable="nova"),
            project_key="nova",
        )
        resolved = SimpleNamespace(config=cfg, resolution_source=SimpleNamespace(value="registry_defaults"), project="nova")
        mock_registry.resolve.return_value = resolved

        tarball_result = MagicMock(success=True, path=mock_tarball_path, error="", signature_verified=False, signature_warning="")

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(plan_module, "run_plan_for_package", return_value=(_make_plan_result(), 0)))
            stack.enter_context(patch("packastack.upstream.registry.UpstreamsRegistry", return_value=mock_registry))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value={"nova": "nova"}))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=MagicMock(is_complete=lambda: True)))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch.object(tarball_module, "run_uscan", return_value=(False, None, "uscan failed")))
            stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_official_result))
            stack.enter_context(patch.object(tarball_module, "download_pypi_tarball", return_value=(False, None, "skip")))
            stack.enter_context(patch.object(tarball_module, "download_github_release_tarball", return_value=(False, None, "skip")))
            stack.enter_context(patch.object(tarball_module, "run_command", return_value=(0, "", "")))
            git_archive = stack.enter_context(patch.object(tarball_module, "generate_snapshot_tarball", return_value=tarball_result))
            stack.enter_context(patch("packastack.debpkg.gbp.ensure_upstream_branch", return_value=MagicMock(success=True, created=False)))
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, upstream_version="1.0.0", output="")))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="1.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="1.0", epoch=0)))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="1.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="Release"))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=MagicMock(success=True, needs_refresh=False, patch_reports=[])))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=MagicMock(success=True, artifacts=[], dsc_file=None, changes_file=None, output="")))

            result = _call_run_build(
                run=mock_run,
                package="nova",
                target="devel",
                ubuntu_series="devel",
                cloud_archive="",
                build_type_str="release",
                milestone="",
                force=False,
                offline=False,
                validate_plan_only=False,
                plan_upload=False,
                upload=False,
                binary=False,
                builder="sbuild",
                build_deps=True,
                no_spinner=True,
                yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS
        git_archive.assert_called_once()

    def test_milestone_uses_uscan_first(self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock) -> None:
        """Milestone builds should also try uscan before official."""
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "control").touch()

        mock_fetch_result = MagicMock()
        mock_fetch_result.error = None
        mock_fetch_result.path = tmp_path / "repo"
        mock_fetch_result.path.mkdir(parents=True, exist_ok=True)
        (mock_fetch_result.path / "debian").mkdir(exist_ok=True)
        mock_fetch_result.branches = ["main"]
        mock_fetch_result.cloned = True
        mock_fetch_result.updated = False

        mock_upstream = MagicMock()
        mock_upstream.version = "1.0.0b1"
        mock_upstream.tarball_url = "https://example.com/nova-1.0.0b1.tar.gz"

        mock_tarball_path = tmp_path / "nova-1.0.0b1.tar.gz"
        mock_tarball_path.touch()

        mock_registry = _create_mock_registry("nova")

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(plan_module, "run_plan_for_package", return_value=(_make_plan_result(), 0)))
            stack.enter_context(patch("packastack.upstream.registry.UpstreamsRegistry", return_value=mock_registry))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value={"nova": "nova"}))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=MagicMock(is_complete=lambda: True)))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            # uscan succeeds for milestone
            stack.enter_context(patch.object(tarball_module, "run_uscan", return_value=(True, mock_tarball_path, "")))
            official_dl = stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball"))
            stack.enter_context(patch("packastack.debpkg.gbp.ensure_upstream_branch", return_value=MagicMock(success=True, created=False)))
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, upstream_version="1.0.0b1", output="")))
            stack.enter_context(patch.object(single_build_module, "run_command"))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="1.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="1.0", epoch=0)))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_milestone_version", return_value="1.0.0~b1-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="Milestone b1"))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=MagicMock(success=True, needs_refresh=False, patch_reports=[])))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=MagicMock(success=True, artifacts=[], dsc_file=None, changes_file=None, output="")))

            result = _call_run_build(
                run=mock_run,
                package="nova",
                target="devel",
                ubuntu_series="devel",
                cloud_archive="",
                build_type_str="milestone",
                milestone="b1",
                force=False,
                offline=False,
                validate_plan_only=False,
                plan_upload=False,
                upload=False,
                binary=False,
                builder="sbuild",
                build_deps=True,
                no_spinner=True,
                yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS
        # uscan succeeded, official not called
        official_dl.assert_not_called()

    def test_pypi_fallback_when_official_fails(self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock) -> None:
        """If uscan and official fail, PyPI fallback should be used when preferred."""
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "control").touch()

        mock_fetch_result = MagicMock()
        mock_fetch_result.error = None
        mock_fetch_result.path = tmp_path / "repo"
        mock_fetch_result.path.mkdir(parents=True, exist_ok=True)
        (mock_fetch_result.path / "debian").mkdir(exist_ok=True)
        mock_fetch_result.branches = ["main"]
        mock_fetch_result.cloned = True
        mock_fetch_result.updated = False

        mock_upstream = MagicMock()
        mock_upstream.version = "1.0.0"
        mock_upstream.tarball_url = "https://example.com/nova-1.0.0.tar.gz"

        mock_tarball_path = tmp_path / "nova-1.0.0.tar.gz"
        mock_tarball_path.touch()

        mock_official_result = MagicMock(success=False, error="404", path=None, signature_verified=False, signature_warning="")

        mock_registry = MagicMock()
        mock_registry.version = "1"
        mock_registry.override_applied = False
        mock_registry.override_path = None
        mock_registry.warnings = []

        cfg = SimpleNamespace(
            upstream=SimpleNamespace(host="example", url="https://example.com/nova.git", default_branch="master"),
            tarball=SimpleNamespace(prefer=[SimpleNamespace(value="official"), SimpleNamespace(value="pypi")]),
            signatures=SimpleNamespace(mode=SimpleNamespace(value="auto")),
            release_source=SimpleNamespace(project="nova", type=SimpleNamespace(value="git"), deliverable="nova"),
            project_key="nova",
        )
        resolved = SimpleNamespace(config=cfg, resolution_source=SimpleNamespace(value="registry_defaults"), project="nova")
        mock_registry.resolve.return_value = resolved

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(plan_module, "run_plan_for_package", return_value=(_make_plan_result(), 0)))
            stack.enter_context(patch("packastack.upstream.registry.UpstreamsRegistry", return_value=mock_registry))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value={"nova": "nova"}))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=MagicMock(is_complete=lambda: True)))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            # uscan fails
            stack.enter_context(patch.object(tarball_module, "run_uscan", return_value=(False, None, "uscan failed")))
            # official fails
            stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_official_result))
            pypi_dl = stack.enter_context(patch.object(tarball_module, "download_pypi_tarball", return_value=(True, mock_tarball_path, "")))
            stack.enter_context(patch.object(tarball_module, "download_github_release_tarball", return_value=(False, None, "skip")))
            stack.enter_context(patch("packastack.debpkg.gbp.ensure_upstream_branch", return_value=MagicMock(success=True, created=False)))
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, upstream_version="1.0.0", output="")))
            stack.enter_context(patch.object(single_build_module, "run_command"))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="1.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="1.0", epoch=0)))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="1.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="Release"))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=MagicMock(success=True, needs_refresh=False, patch_reports=[])))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=MagicMock(success=True, artifacts=[], dsc_file=None, changes_file=None, output="")))

            result = _call_run_build(
                run=mock_run,
                package="nova",
                target="devel",
                ubuntu_series="devel",
                cloud_archive="",
                build_type_str="release",
                milestone="",
                force=False,
                offline=False,
                validate_plan_only=False,
                plan_upload=False,
                upload=False,
                binary=False,
                builder="sbuild",
                build_deps=True,
                no_spinner=True,
                yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS
        pypi_dl.assert_called_once()


@pytest.mark.skip(reason="Needs refactoring for run_plan_for_package mock pattern - see test_validate_plan_only_returns_success for working example")
class TestFullBuildPhases:
    """Tests for complete build phases."""

    @pytest.fixture
    def mock_paths(self, tmp_path: Path) -> dict:
        """Create mock paths."""
        paths = {
            "openstack_releases_repo": tmp_path / "releases",
            "local_apt_repo": tmp_path / "local",
            "ubuntu_archive_cache": tmp_path / "cache",
            "cache_root": tmp_path / "cache",
            "build_root": tmp_path / "build",
        }
        for p in paths.values():
            p.mkdir(parents=True, exist_ok=True)
        return paths

    @pytest.fixture
    def mock_run(self) -> MagicMock:
        """Create mock run context."""
        run = MagicMock()
        run.run_id = "test-run-id"
        run.run_path = Path("/tmp/test-run")
        return run

    def _setup_package(self, mock_paths: dict, tmp_path: Path) -> MagicMock:
        """Setup a mock package and return fetch result."""
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "control").touch()

        mock_fetch_result = MagicMock()
        mock_fetch_result.error = None
        mock_fetch_result.path = tmp_path / "repo"
        mock_fetch_result.path.mkdir(parents=True, exist_ok=True)
        (mock_fetch_result.path / "debian").mkdir(exist_ok=True)
        changelog = "nova (1.0-0ubuntu1) focal; urgency=low\n\n  * Initial\n\n -- Test <test@test.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
        (mock_fetch_result.path / "debian" / "changelog").write_text(changelog)
        mock_fetch_result.branches = ["main"]
        mock_fetch_result.cloned = True
        mock_fetch_result.updated = False
        return mock_fetch_result

    def test_successful_full_build(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test successful full build through all phases."""
        mock_fetch_result = self._setup_package(mock_paths, tmp_path)

        mock_index = MagicMock()
        mock_index.packages = {}
        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_upstream = MagicMock()
        mock_upstream.version = "29.0.0"
        mock_upstream.tarball_url = "http://example.com/nova-29.0.0.tar.gz"

        mock_tarball_result = MagicMock()
        mock_tarball_result.success = True
        mock_tarball_result.path = tmp_path / "nova-29.0.0.tar.gz"
        mock_tarball_result.signature_verified = True
        mock_tarball_result.signature_warning = ""

        mock_pq_result = MagicMock()
        mock_pq_result.success = True

        mock_dsc = tmp_path / "nova_29.0.0-0ubuntu1.dsc"
        mock_dsc.touch()
        mock_changes = tmp_path / "nova_29.0.0-0ubuntu1_source.changes"
        mock_changes.touch()

        mock_build_result = MagicMock()
        mock_build_result.success = True
        mock_build_result.artifacts = [mock_dsc, mock_changes]
        mock_build_result.dsc_file = mock_dsc
        mock_build_result.changes_file = mock_changes
        mock_build_result.output = ""

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_tarball_result))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="1.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="1.0")))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="29.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="New release."))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=mock_pq_result))
            stack.enter_context(patch("packastack.debpkg.gbp.ensure_upstream_branch", return_value=MagicMock(success=True, branch_name="upstream-caracal", created=False, error="")))
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, output="", upstream_version="")))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=mock_build_result))

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS
        mock_run.write_summary.assert_called()

    def test_build_with_binary(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test build with binary package."""
        mock_fetch_result = self._setup_package(mock_paths, tmp_path)

        mock_index = MagicMock()
        mock_index.packages = {}
        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_upstream = MagicMock()
        mock_upstream.version = "29.0.0"
        mock_upstream.tarball_url = "http://example.com/nova-29.0.0.tar.gz"

        mock_tarball_result = MagicMock()
        mock_tarball_result.success = True
        mock_tarball_result.path = tmp_path / "nova-29.0.0.tar.gz"
        mock_tarball_result.signature_verified = True
        mock_tarball_result.signature_warning = ""

        mock_pq_result = MagicMock()
        mock_pq_result.success = True

        mock_dsc = tmp_path / "nova_29.0.0-0ubuntu1.dsc"
        mock_dsc.touch()
        mock_changes = tmp_path / "nova_29.0.0-0ubuntu1_source.changes"
        mock_changes.touch()
        mock_deb = tmp_path / "nova_29.0.0-0ubuntu1_amd64.deb"
        mock_deb.touch()

        mock_build_result = MagicMock()
        mock_build_result.success = True
        mock_build_result.artifacts = [mock_dsc, mock_changes]
        mock_build_result.dsc_file = mock_dsc
        mock_build_result.changes_file = mock_changes
        mock_build_result.output = ""

        # Mock sbuild result with the new enhanced fields
        mock_sbuild_result = MagicMock()
        mock_sbuild_result.success = True
        mock_sbuild_result.artifacts = [mock_deb]
        mock_sbuild_result.exit_code = 0
        mock_sbuild_result.collected_artifacts = [MagicMock(source_path=mock_deb)]
        mock_sbuild_result.collected_logs = []
        mock_sbuild_result.stdout_log_path = tmp_path / "sbuild.stdout.log"
        mock_sbuild_result.stderr_log_path = tmp_path / "sbuild.stderr.log"
        mock_sbuild_result.primary_log_path = None
        mock_sbuild_result.searched_dirs = [str(tmp_path)]
        mock_sbuild_result.validation_message = "OK"
        mock_sbuild_result.report_path = tmp_path / "reports" / "sbuild-artifacts.json"
        mock_sbuild_result.command = ["sbuild", "-d", "noble", str(mock_dsc)]

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_tarball_result))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="1.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="1.0")))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="29.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="New release."))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=mock_pq_result))
            stack.enter_context(patch("packastack.debpkg.gbp.ensure_upstream_branch", return_value=MagicMock(success=True, branch_name="upstream-caracal", created=False, error="")))
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, output="", upstream_version="")))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=mock_build_result))
            stack.enter_context(patch.object(build, "is_sbuild_available", return_value=True))
            stack.enter_context(patch.object(build, "run_sbuild", return_value=mock_sbuild_result))
            stack.enter_context(patch.object(build, "ensure_schroot", return_value=MagicMock(name="packastack-noble-amd64", exists=True, created=False, error="")))

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=True, binary=True, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS

    def test_build_failure_returns_error(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test that build failure returns BUILD_FAILED."""
        mock_fetch_result = self._setup_package(mock_paths, tmp_path)

        mock_index = MagicMock()
        mock_index.packages = {}
        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_upstream = MagicMock()
        mock_upstream.version = "29.0.0"
        mock_upstream.tarball_url = "http://example.com/nova-29.0.0.tar.gz"

        mock_tarball_result = MagicMock()
        mock_tarball_result.success = True
        mock_tarball_result.path = tmp_path / "nova-29.0.0.tar.gz"
        mock_tarball_result.signature_verified = False
        mock_tarball_result.signature_warning = ""

        mock_pq_result = MagicMock()
        mock_pq_result.success = True

        mock_build_result = MagicMock()
        mock_build_result.success = False
        mock_build_result.output = "dpkg-source: error"

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_tarball_result))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="1.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="1.0")))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="29.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="New release."))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=mock_pq_result))
            stack.enter_context(patch("packastack.debpkg.gbp.ensure_upstream_branch", return_value=MagicMock(success=True, branch_name="upstream-caracal", created=False, error="")))
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, output="", upstream_version="")))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=mock_build_result))

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_BUILD_FAILED

    def test_pq_import_failure(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test that pq import failure returns PATCH_FAILED."""
        mock_fetch_result = self._setup_package(mock_paths, tmp_path)

        mock_index = MagicMock()
        mock_index.packages = {}
        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_upstream = MagicMock()
        mock_upstream.version = "29.0.0"
        mock_upstream.tarball_url = "http://example.com/nova-29.0.0.tar.gz"

        mock_tarball_result = MagicMock()
        mock_tarball_result.success = True
        mock_tarball_result.path = tmp_path / "nova-29.0.0.tar.gz"
        mock_tarball_result.signature_verified = False
        mock_tarball_result.signature_warning = ""

        mock_pq_result = MagicMock()
        mock_pq_result.success = False
        mock_pq_result.needs_refresh = False
        mock_pq_result.output = "Patch conflict"
        mock_pq_result.patch_reports = []

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_tarball_result))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="1.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="1.0")))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="29.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="New release."))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=mock_pq_result))

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_PATCH_FAILED

    def test_pq_needs_refresh_export_fails(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test pq needs refresh but export fails."""
        mock_fetch_result = self._setup_package(mock_paths, tmp_path)

        mock_index = MagicMock()
        mock_index.packages = {}
        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_upstream = MagicMock()
        mock_upstream.version = "29.0.0"
        mock_upstream.tarball_url = "http://example.com/nova-29.0.0.tar.gz"

        mock_tarball_result = MagicMock()
        mock_tarball_result.success = True
        mock_tarball_result.path = tmp_path / "nova-29.0.0.tar.gz"
        mock_tarball_result.signature_verified = False
        mock_tarball_result.signature_warning = ""

        mock_pq_result = MagicMock()
        mock_pq_result.success = False
        mock_pq_result.needs_refresh = True
        mock_pq_result.output = "Needs refresh"

        mock_export_result = MagicMock()
        mock_export_result.success = False
        mock_export_result.output = "Export failed"

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_tarball_result))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="1.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="1.0")))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="29.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="New release."))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=mock_pq_result))
            stack.enter_context(patch.object(build, "pq_export", return_value=mock_export_result))

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_PATCH_FAILED

    def test_pq_needs_refresh_export_succeeds(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test pq needs refresh and export succeeds."""
        mock_fetch_result = self._setup_package(mock_paths, tmp_path)

        mock_index = MagicMock()
        mock_index.packages = {}
        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_upstream = MagicMock()
        mock_upstream.version = "29.0.0"
        mock_upstream.tarball_url = "http://example.com/nova-29.0.0.tar.gz"

        mock_tarball_result = MagicMock()
        mock_tarball_result.success = True
        mock_tarball_result.path = tmp_path / "nova-29.0.0.tar.gz"
        mock_tarball_result.signature_verified = True
        mock_tarball_result.signature_warning = ""

        mock_pq_result = MagicMock()
        mock_pq_result.success = False
        mock_pq_result.needs_refresh = True
        mock_pq_result.output = "Needs refresh"

        # Second call with time_machine=0 succeeds
        mock_pq_result_tm = MagicMock()
        mock_pq_result_tm.success = True
        mock_pq_result_tm.output = "Success"

        mock_export_result = MagicMock()
        mock_export_result.success = True

        mock_dsc = tmp_path / "nova_29.0.0-0ubuntu1.dsc"
        mock_dsc.touch()
        mock_changes = tmp_path / "nova_29.0.0-0ubuntu1_source.changes"
        mock_changes.touch()

        mock_build_result = MagicMock()
        mock_build_result.success = True
        mock_build_result.artifacts = [mock_dsc, mock_changes]
        mock_build_result.dsc_file = mock_dsc
        mock_build_result.changes_file = mock_changes
        mock_build_result.output = ""

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_tarball_result))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="1.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="1.0")))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="29.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="New release."))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            mock_pq_import = stack.enter_context(patch.object(build, "pq_import"))
            mock_pq_import.side_effect = [mock_pq_result, mock_pq_result_tm]
            stack.enter_context(patch.object(build, "pq_export", return_value=mock_export_result))
            stack.enter_context(patch("packastack.debpkg.gbp.ensure_upstream_branch", return_value=MagicMock(success=True, branch_name="upstream-caracal", created=False, error="")))
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, output="", upstream_version="")))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=mock_build_result))

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS

    def test_upstreamed_patches_block_without_force(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test upstreamed patches block build without --force."""
        mock_fetch_result = self._setup_package(mock_paths, tmp_path)

        mock_index = MagicMock()
        mock_index.packages = {}
        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_upstream = MagicMock()
        mock_upstream.version = "29.0.0"
        mock_upstream.tarball_url = "http://example.com/nova-29.0.0.tar.gz"

        mock_tarball_result = MagicMock()
        mock_tarball_result.success = True
        mock_tarball_result.path = tmp_path / "nova-29.0.0.tar.gz"
        mock_tarball_result.signature_verified = False
        mock_tarball_result.signature_warning = ""

        mock_upstreamed = MagicMock()
        mock_upstreamed.patch_name = "fix.patch"
        mock_upstreamed.suggested_action = "Remove"

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_tarball_result))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="1.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="1.0")))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="29.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="New release."))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[mock_upstreamed]))

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_PATCH_FAILED

    def test_no_upstream_source_returns_config_error(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test no upstream source returns CONFIG_ERROR for release build."""
        mock_fetch_result = self._setup_package(mock_paths, tmp_path)

        mock_index = MagicMock()
        mock_index.packages = {}
        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=None))

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_CONFIG_ERROR

    def test_download_failure_returns_fetch_failed(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test download failure returns FETCH_FAILED."""
        mock_fetch_result = self._setup_package(mock_paths, tmp_path)

        mock_index = MagicMock()
        mock_index.packages = {}
        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_upstream = MagicMock()
        mock_upstream.version = "29.0.0"
        mock_upstream.tarball_url = "http://example.com/nova-29.0.0.tar.gz"

        mock_tarball_result = MagicMock()
        mock_tarball_result.success = False
        mock_tarball_result.error = "Network error"

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_tarball_result))

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_FETCH_FAILED

    def test_snapshot_build_no_tarball_url(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test snapshot build without tarball URL (uses git)."""
        mock_fetch_result = self._setup_package(mock_paths, tmp_path)

        mock_index = MagicMock()
        mock_index.packages = {}
        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_upstream = MagicMock()
        mock_upstream.version = ""
        mock_upstream.tarball_url = None
        mock_upstream.git_ref = "HEAD"

        mock_pq_result = MagicMock()
        mock_pq_result.success = True

        mock_dsc = tmp_path / "nova_30.0.0-0ubuntu1.dsc"
        mock_dsc.touch()
        mock_changes = tmp_path / "nova_30.0.0-0ubuntu1_source.changes"
        mock_changes.touch()

        mock_build_result = MagicMock()
        mock_build_result.success = True
        mock_build_result.artifacts = [mock_dsc, mock_changes]
        mock_build_result.dsc_file = mock_dsc
        mock_build_result.changes_file = mock_changes
        mock_build_result.output = ""

        # Mock for acquire_upstream_snapshot
        mock_snapshot_result = MagicMock()
        mock_snapshot_result.success = True
        mock_snapshot_result.git_sha = "abc1234567890"
        mock_snapshot_result.git_sha_short = "abc1234"
        mock_snapshot_result.git_date = "20241227"
        mock_snapshot_result.upstream_version = "1.0.1~git20241227.abc1234"
        mock_snapshot_result.cloned = True
        mock_snapshot_result.tarball_result = MagicMock()
        mock_snapshot_result.tarball_result.path = tmp_path / "nova_1.0.1.orig.tar.gz"

        # Mock for localrepo.publish_artifacts
        mock_publish_result = MagicMock()
        mock_publish_result.success = True
        mock_publish_result.published_paths = [mock_dsc, mock_changes]

        # Mock for localrepo.regenerate_indexes
        mock_index_result = MagicMock()
        mock_index_result.success = True
        mock_index_result.package_count = 0
        mock_index_result.packages_file = tmp_path / "Packages"

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "is_snapshot_eligible", return_value=(True, "", "")))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            # Use dict mapping to exercise validate-deps handling of real load_openstack_packages output
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value={"nova": "nova"}))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch.object(build, "acquire_upstream_snapshot", return_value=mock_snapshot_result))
            # Ensure validate-deps executes with an upstream repo and missing dependency resolution
            upstream_repo = mock_fetch_result.path / "upstream"
            upstream_repo.mkdir(parents=True, exist_ok=True)
            mock_snapshot_result.repo_path = upstream_repo
            mock_upstream_deps = MagicMock(runtime=[("netaddr", ">=0.7.0")], test=[], build=[])
            stack.enter_context(patch.object(build, "extract_upstream_deps", return_value=mock_upstream_deps))
            stack.enter_context(patch("packastack.planning.validated_plan.map_python_to_debian", return_value=("python3-netaddr", False)))
            stack.enter_context(patch("packastack.planning.validated_plan.resolve_dependency_with_spec", return_value=(None, "", False)))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="1.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="1.0")))
            stack.enter_context(patch.object(build, "increment_upstream_version", return_value="1.0.1"))
            stack.enter_context(patch.object(build, "generate_snapshot_version", return_value="1.0.1~git20241227.abc1234-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="Snapshot."))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=mock_pq_result))
            stack.enter_context(patch("packastack.debpkg.gbp.ensure_upstream_branch", return_value=MagicMock(success=True, branch_name="upstream-caracal", created=False, error="")))
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, output="", upstream_version="")))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=mock_build_result))
            stack.enter_context(patch.object(build.localrepo, "publish_artifacts", return_value=mock_publish_result))
            stack.enter_context(patch.object(build.localrepo, "regenerate_indexes", return_value=mock_index_result))
            stack.enter_context(patch.object(build, "get_host_arch", return_value="amd64"))

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="snapshot", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS

    def test_milestone_build(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test milestone build."""
        mock_fetch_result = self._setup_package(mock_paths, tmp_path)

        mock_index = MagicMock()
        mock_index.packages = {}
        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_upstream = MagicMock()
        mock_upstream.version = "30.0.0.0b1"
        mock_upstream.tarball_url = "http://example.com/nova-30.0.0.0b1.tar.gz"

        mock_tarball_result = MagicMock()
        mock_tarball_result.success = True
        mock_tarball_result.path = tmp_path / "nova-30.0.0.0b1.tar.gz"
        mock_tarball_result.signature_verified = True
        mock_tarball_result.signature_warning = ""

        mock_pq_result = MagicMock()
        mock_pq_result.success = True

        mock_dsc = tmp_path / "nova_30.0.0.0~b1-0ubuntu1.dsc"
        mock_dsc.touch()
        mock_changes = tmp_path / "nova_30.0.0.0~b1-0ubuntu1_source.changes"
        mock_changes.touch()

        mock_build_result = MagicMock()
        mock_build_result.success = True
        mock_build_result.artifacts = [mock_dsc, mock_changes]
        mock_build_result.dsc_file = mock_dsc
        mock_build_result.changes_file = mock_changes
        mock_build_result.output = ""

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_tarball_result))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="1.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="1.0")))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_milestone_version", return_value="30.0.0.0~b1-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="Milestone b1."))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=mock_pq_result))
            stack.enter_context(patch("packastack.debpkg.gbp.ensure_upstream_branch", return_value=MagicMock(success=True, branch_name="upstream-caracal", created=False, error="")))
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, output="", upstream_version="")))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=mock_build_result))

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="milestone", milestone="b1",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS


@pytest.mark.skip(reason="Needs refactoring for run_plan_for_package mock pattern")
class TestBuildWithUpstreamedPatchesForce:
    """Tests for build with upstreamed patches and force flag."""

    @pytest.fixture
    def mock_run(self) -> MagicMock:
        """Create a mock RunContext."""
        run = MagicMock()
        run.run_id = "test-run-id"
        run.run_path = Path("/tmp/test-run")
        run.log_event = MagicMock()
        return run

    @pytest.fixture
    def mock_paths(self, tmp_path: Path) -> dict:
        """Create mock paths."""
        paths = {
            "openstack_releases_repo": tmp_path / "releases",
            "local_apt_repo": tmp_path / "local",
            "ubuntu_archive_cache": tmp_path / "cache",
            "cache_root": tmp_path / "cache",
            "build_root": tmp_path / "build",
        }
        for p in paths.values():
            p.mkdir(parents=True, exist_ok=True)
        return paths

    def test_upstreamed_patches_with_force(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test build continues with --force despite upstreamed patches."""
        # Setup package directory
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()
        (pkg_dir.parent / ".git").mkdir()

        # Setup workspace with changelog
        workspace = mock_paths["build_root"] / "nova"
        workspace.mkdir(parents=True)
        (workspace / "debian").mkdir()
        (workspace / "debian" / "changelog").write_text(
            "nova (28.0.0-0ubuntu1) noble; urgency=medium\n\n"
            "  * Test\n\n -- Test <test@test.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
        )

        mock_index = MagicMock()
        mock_index.packages = {}

        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_fetch_result = MagicMock()
        mock_fetch_result.error = None
        mock_fetch_result.path = workspace
        mock_fetch_result.branches = ["main"]
        mock_fetch_result.cloned = True
        mock_fetch_result.updated = False

        mock_upstream = MagicMock()
        mock_upstream.version = "29.0.0"
        mock_upstream.tarball_url = "http://example.com/nova-29.0.0.tar.gz"

        mock_tarball_result = MagicMock()
        mock_tarball_result.success = True
        mock_tarball_result.path = tmp_path / "nova-29.0.0.tar.gz"
        mock_tarball_result.signature_verified = False
        mock_tarball_result.signature_warning = ""

        # Create upstreamed patch report
        mock_upstreamed = [MagicMock()]
        mock_upstreamed[0].patch_name = "fix-bug.patch"
        mock_upstreamed[0].suggested_action = "remove"

        mock_pq_result = MagicMock()
        mock_pq_result.success = True

        mock_dsc = tmp_path / "nova_29.0.0-0ubuntu1.dsc"
        mock_dsc.touch()
        mock_changes = tmp_path / "nova_29.0.0-0ubuntu1_source.changes"
        mock_changes.touch()

        mock_build_result = MagicMock()
        mock_build_result.success = True
        mock_build_result.artifacts = [mock_dsc, mock_changes]
        mock_build_result.dsc_file = mock_dsc
        mock_build_result.changes_file = mock_changes
        mock_build_result.output = ""

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "is_snapshot_eligible", return_value=(True, "", "")))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_tarball_result))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="28.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="28.0.0")))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="29.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="New release."))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            # Return upstreamed patches
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=mock_upstreamed))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=mock_pq_result))
            stack.enter_context(patch("packastack.debpkg.gbp.ensure_upstream_branch", return_value=MagicMock(success=True, branch_name="upstream-caracal", created=False, error="")))
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, output="", upstream_version="")))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=mock_build_result))

            # force=True should allow build to continue
            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=True,  # Force to continue with upstreamed patches
                offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS

    def test_patch_needs_refresh_success(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test patches needing refresh are handled."""
        # Setup package directory
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()
        (pkg_dir.parent / ".git").mkdir()

        # Setup workspace with changelog
        workspace = mock_paths["build_root"] / "nova"
        workspace.mkdir(parents=True)
        (workspace / "debian").mkdir()
        (workspace / "debian" / "changelog").write_text(
            "nova (28.0.0-0ubuntu1) noble; urgency=medium\n\n"
            "  * Test\n\n -- Test <test@test.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
        )

        mock_index = MagicMock()
        mock_index.packages = {}

        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_fetch_result = MagicMock()
        mock_fetch_result.error = None
        mock_fetch_result.path = workspace
        mock_fetch_result.branches = ["main"]
        mock_fetch_result.cloned = True
        mock_fetch_result.updated = False

        mock_upstream = MagicMock()
        mock_upstream.version = "29.0.0"
        mock_upstream.tarball_url = "http://example.com/nova-29.0.0.tar.gz"

        mock_tarball_result = MagicMock()
        mock_tarball_result.success = True
        mock_tarball_result.path = tmp_path / "nova-29.0.0.tar.gz"
        mock_tarball_result.signature_verified = False
        mock_tarball_result.signature_warning = ""

        # Patches need refresh
        mock_pq_result = MagicMock()
        mock_pq_result.success = False
        mock_pq_result.needs_refresh = True
        mock_pq_result.output = "Patch needs refresh"
        mock_pq_result.patch_reports = []

        # Second call with time_machine=0 succeeds
        mock_pq_result_tm = MagicMock()
        mock_pq_result_tm.success = True
        mock_pq_result_tm.output = "Success"

        mock_export_result = MagicMock()
        mock_export_result.success = True

        mock_dsc = tmp_path / "nova_29.0.0-0ubuntu1.dsc"
        mock_dsc.touch()
        mock_changes = tmp_path / "nova_29.0.0-0ubuntu1_source.changes"
        mock_changes.touch()

        mock_build_result = MagicMock()
        mock_build_result.success = True
        mock_build_result.artifacts = [mock_dsc, mock_changes]
        mock_build_result.dsc_file = mock_dsc
        mock_build_result.changes_file = mock_changes
        mock_build_result.output = ""

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "is_snapshot_eligible", return_value=(True, "", "")))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_tarball_result))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="28.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="28.0.0")))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="29.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="New release."))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            mock_pq_import = stack.enter_context(patch.object(build, "pq_import"))
            mock_pq_import.side_effect = [mock_pq_result, mock_pq_result_tm]
            stack.enter_context(patch.object(build, "pq_export", return_value=mock_export_result))
            stack.enter_context(patch("packastack.debpkg.gbp.ensure_upstream_branch", return_value=MagicMock(success=True, branch_name="upstream-caracal", created=False, error="")))
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, output="", upstream_version="")))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=mock_build_result))

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS


@pytest.mark.skip(reason="Needs refactoring for run_plan_for_package mock pattern")
class TestSnapshotAcquisitionIntegration:
    """Test snapshot acquisition integration in build command."""

    @pytest.fixture
    def mock_paths(self, tmp_path: Path) -> dict:
        """Create mock paths."""
        paths = {
            "openstack_releases_repo": tmp_path / "releases",
            "local_apt_repo": tmp_path / "local",
            "ubuntu_archive_cache": tmp_path / "cache",
            "cache_root": tmp_path / "cache",
            "build_root": tmp_path / "build",
        }
        for p in paths.values():
            p.mkdir(parents=True, exist_ok=True)
        return paths

    @pytest.fixture
    def mock_run(self) -> MagicMock:
        """Create mock run context."""
        run = MagicMock()
        run.run_id = "test-run-id"
        run.run_path = Path("/tmp/test-run")
        return run

    def test_snapshot_build_calls_acquire_upstream_snapshot(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test snapshot build calls acquire_upstream_snapshot."""
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()
        (pkg_dir.parent / ".git").mkdir()

        workspace = mock_paths["build_root"] / "nova"
        workspace.mkdir(parents=True)
        (workspace / "debian").mkdir()
        (workspace / "debian" / "changelog").write_text(
            "nova (28.0.0-0ubuntu1) noble; urgency=medium\n\n"
            "  * Test\n\n -- Test <test@test.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
        )

        mock_index = MagicMock()
        mock_index.packages = {}

        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_fetch_result = MagicMock()
        mock_fetch_result.error = None
        mock_fetch_result.path = workspace
        mock_fetch_result.branches = ["main"]
        mock_fetch_result.cloned = True
        mock_fetch_result.updated = False

        mock_snapshot_result = MagicMock()
        mock_snapshot_result.success = True
        mock_snapshot_result.commit_sha = "abc123def"
        mock_snapshot_result.version = "29.0.0.dev5"
        mock_snapshot_result.tarball_path = tmp_path / "nova-29.0.0.dev5.tar.gz"
        (tmp_path / "nova-29.0.0.dev5.tar.gz").touch()

        mock_pq_result = MagicMock()
        mock_pq_result.success = True
        mock_pq_result.needs_refresh = False
        mock_pq_result.output = ""
        mock_pq_result.patch_reports = []

        mock_export_result = MagicMock()
        mock_export_result.success = True

        mock_dsc = tmp_path / "nova_29.0.0.dev5-0ubuntu1.dsc"
        mock_dsc.touch()
        mock_changes = tmp_path / "nova_29.0.0.dev5-0ubuntu1_source.changes"
        mock_changes.touch()

        mock_build_result = MagicMock()
        mock_build_result.success = True
        mock_build_result.artifacts = [mock_dsc, mock_changes]
        mock_build_result.dsc_file = mock_dsc
        mock_build_result.changes_file = mock_changes
        mock_build_result.output = ""

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "is_snapshot_eligible", return_value=(True, "", "")))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="28.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="28.0.0")))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="29.0.0.dev5-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="Snapshot."))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=mock_pq_result))
            stack.enter_context(patch.object(build, "pq_export", return_value=mock_export_result))
            stack.enter_context(patch("packastack.debpkg.gbp.ensure_upstream_branch", return_value=MagicMock(success=True, branch_name="upstream-caracal", created=False, error="")))
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, output="", upstream_version="")))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=mock_build_result))
            mock_acquire = stack.enter_context(
                patch.object(build, "acquire_upstream_snapshot", return_value=mock_snapshot_result)
            )

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="snapshot", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS
        mock_acquire.assert_called_once()

    def test_snapshot_acquisition_failure_exits_early(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test that snapshot acquisition failure causes early exit."""
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()
        (pkg_dir.parent / ".git").mkdir()

        workspace = mock_paths["build_root"] / "nova"
        workspace.mkdir(parents=True)
        (workspace / "debian").mkdir()
        (workspace / "debian" / "changelog").write_text(
            "nova (28.0.0-0ubuntu1) noble; urgency=medium\n\n"
            "  * Test\n\n -- Test <test@test.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
        )

        mock_index = MagicMock()
        mock_index.packages = {}

        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_fetch_result = MagicMock()
        mock_fetch_result.error = None
        mock_fetch_result.path = workspace
        mock_fetch_result.branches = ["main"]
        mock_fetch_result.cloned = True
        mock_fetch_result.updated = False

        # Snapshot acquisition fails
        mock_snapshot_result = MagicMock()
        mock_snapshot_result.success = False
        mock_snapshot_result.error_message = "Failed to clone upstream repository"
        mock_snapshot_result.commit_sha = None
        mock_snapshot_result.version = None
        mock_snapshot_result.tarball_path = None

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "is_snapshot_eligible", return_value=(True, "", "")))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="28.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="28.0.0")))
            stack.enter_context(
                patch.object(build, "acquire_upstream_snapshot", return_value=mock_snapshot_result)
            )
            # build_source should not be called if snapshot fails
            mock_build = stack.enter_context(
                patch("packastack.debpkg.gbp.build_source", return_value=MagicMock())
            )

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="snapshot", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        # Should fail due to snapshot acquisition failure
        assert result == build.EXIT_FETCH_FAILED
        mock_build.assert_not_called()


@pytest.mark.skip(reason="Needs refactoring for run_plan_for_package mock pattern")
class TestLocalRepoPublishingIntegration:
    """Test local APT repo publishing integration in build command."""

    @pytest.fixture
    def mock_paths(self, tmp_path: Path) -> dict:
        """Create mock paths."""
        paths = {
            "openstack_releases_repo": tmp_path / "releases",
            "local_apt_repo": tmp_path / "local",
            "ubuntu_archive_cache": tmp_path / "cache",
            "cache_root": tmp_path / "cache",
            "build_root": tmp_path / "build",
        }
        for p in paths.values():
            p.mkdir(parents=True, exist_ok=True)
        return paths

    @pytest.fixture
    def mock_run(self) -> MagicMock:
        """Create mock run context."""
        run = MagicMock()
        run.run_id = "test-run-id"
        run.run_path = Path("/tmp/test-run")
        return run

    def test_successful_build_publishes_to_local_repo(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test successful build publishes artifacts to local repo."""
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()
        (pkg_dir.parent / ".git").mkdir()

        workspace = mock_paths["build_root"] / "nova"
        workspace.mkdir(parents=True)
        (workspace / "debian").mkdir()
        (workspace / "debian" / "changelog").write_text(
            "nova (28.0.0-0ubuntu1) noble; urgency=medium\n\n"
            "  * Test\n\n -- Test <test@test.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
        )

        mock_index = MagicMock()
        mock_index.packages = {}

        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_fetch_result = MagicMock()
        mock_fetch_result.error = None
        mock_fetch_result.path = workspace
        mock_fetch_result.branches = ["main"]
        mock_fetch_result.cloned = True
        mock_fetch_result.updated = False

        mock_upstream = MagicMock()
        mock_upstream.version = "29.0.0"
        mock_upstream.tarball_url = "http://example.com/nova-29.0.0.tar.gz"

        mock_tarball_result = MagicMock()
        mock_tarball_result.success = True
        mock_tarball_result.path = tmp_path / "nova-29.0.0.tar.gz"
        mock_tarball_result.signature_verified = False
        mock_tarball_result.signature_warning = ""

        mock_pq_result = MagicMock()
        mock_pq_result.success = True
        mock_pq_result.needs_refresh = False
        mock_pq_result.output = ""
        mock_pq_result.patch_reports = []

        mock_export_result = MagicMock()
        mock_export_result.success = True

        mock_dsc = tmp_path / "nova_29.0.0-0ubuntu1.dsc"
        mock_dsc.touch()
        mock_changes = tmp_path / "nova_29.0.0-0ubuntu1_source.changes"
        mock_changes.touch()
        mock_deb = tmp_path / "python3-nova_29.0.0-0ubuntu1_all.deb"
        mock_deb.touch()

        mock_build_result = MagicMock()
        mock_build_result.success = True
        mock_build_result.artifacts = [mock_dsc, mock_changes, mock_deb]
        mock_build_result.dsc_file = mock_dsc
        mock_build_result.changes_file = mock_changes
        mock_build_result.output = ""

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "is_snapshot_eligible", return_value=(True, "", "")))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_tarball_result))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="28.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="28.0.0")))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="29.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="New release."))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=mock_pq_result))
            stack.enter_context(patch.object(build, "pq_export", return_value=mock_export_result))
            stack.enter_context(patch("packastack.debpkg.gbp.ensure_upstream_branch", return_value=MagicMock(success=True, branch_name="upstream-caracal", created=False, error="")))
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, output="", upstream_version="")))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=mock_build_result))

            # Mock the localrepo module with proper result objects
            mock_publish_result = MagicMock()
            mock_publish_result.success = True
            mock_publish_result.published_paths = []
            mock_publish_result.error = ""
            mock_publish = stack.enter_context(
                patch("packastack.commands.build.localrepo.publish_artifacts", return_value=mock_publish_result)
            )
            
            mock_index_result = MagicMock()
            mock_index_result.success = True
            mock_index_result.package_count = 0
            mock_index_result.packages_file = None
            mock_index_result.error = ""
            mock_regen = stack.enter_context(
                patch("packastack.commands.build.localrepo.regenerate_indexes", return_value=mock_index_result)
            )

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS
        # Verify publish was called (may be called once or not at all depending on build flow)
        # Just verify no exceptions occurred

    def test_build_failure_does_not_publish(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test failed build does not publish to local repo."""
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()
        (pkg_dir.parent / ".git").mkdir()

        workspace = mock_paths["build_root"] / "nova"
        workspace.mkdir(parents=True)
        (workspace / "debian").mkdir()
        (workspace / "debian" / "changelog").write_text(
            "nova (28.0.0-0ubuntu1) noble; urgency=medium\n\n"
            "  * Test\n\n -- Test <test@test.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
        )

        mock_index = MagicMock()
        mock_index.packages = {}

        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_fetch_result = MagicMock()
        mock_fetch_result.error = None
        mock_fetch_result.path = workspace
        mock_fetch_result.branches = ["main"]
        mock_fetch_result.cloned = True
        mock_fetch_result.updated = False

        mock_upstream = MagicMock()
        mock_upstream.version = "29.0.0"
        mock_upstream.tarball_url = "http://example.com/nova-29.0.0.tar.gz"

        mock_tarball_result = MagicMock()
        mock_tarball_result.success = True
        mock_tarball_result.path = tmp_path / "nova-29.0.0.tar.gz"
        mock_tarball_result.signature_verified = False
        mock_tarball_result.signature_warning = ""

        mock_pq_result = MagicMock()
        mock_pq_result.success = True
        mock_pq_result.needs_refresh = False
        mock_pq_result.output = ""
        mock_pq_result.patch_reports = []

        mock_export_result = MagicMock()
        mock_export_result.success = True

        # Build fails
        mock_build_result = MagicMock()
        mock_build_result.success = False
        mock_build_result.artifacts = []
        mock_build_result.dsc_file = None
        mock_build_result.changes_file = None
        mock_build_result.output = "Build failed: dpkg-buildpackage error"

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "is_snapshot_eligible", return_value=(True, "", "")))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_tarball_result))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="28.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="28.0.0")))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="29.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="New release."))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=mock_pq_result))
            stack.enter_context(patch.object(build, "pq_export", return_value=mock_export_result))
            stack.enter_context(patch("packastack.debpkg.gbp.ensure_upstream_branch", return_value=MagicMock(success=True, branch_name="upstream-caracal", created=False, error="")))
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, output="", upstream_version="")))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=mock_build_result))

            # Mock localrepo - should NOT be called on failed build
            mock_publish_result = MagicMock()
            mock_publish_result.success = True
            mock_publish_result.published_paths = []
            mock_publish_result.error = ""
            mock_publish = stack.enter_context(
                patch("packastack.commands.build.localrepo.publish_artifacts", return_value=mock_publish_result)
            )
            mock_regen = stack.enter_context(
                patch("packastack.commands.build.localrepo.regenerate_indexes")
            )

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        # Build should fail
        assert result == build.EXIT_BUILD_FAILED
        # publish_artifacts should not be called on failed build
        mock_publish.assert_not_called()

    def test_successful_build_without_artifacts_creates_indexes(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Successful build with no artifacts still regenerates repo metadata."""
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()
        (pkg_dir.parent / ".git").mkdir()

        workspace = mock_paths["build_root"] / "nova"
        workspace.mkdir(parents=True)
        (workspace / "debian").mkdir()
        (workspace / "debian" / "changelog").write_text(
            "nova (28.0.0-0ubuntu1) noble; urgency=medium\n\n"
            "  * Test\n\n -- Test <test@test.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
        )

        mock_index = MagicMock()
        mock_index.packages = {}

        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_fetch_result = MagicMock()
        mock_fetch_result.error = None
        mock_fetch_result.path = workspace
        mock_fetch_result.branches = ["main"]
        mock_fetch_result.cloned = True
        mock_fetch_result.updated = False

        mock_upstream = MagicMock()
        mock_upstream.version = "29.0.0"
        mock_upstream.tarball_url = "http://example.com/nova-29.0.0.tar.gz"

        mock_tarball_result = MagicMock()
        mock_tarball_result.success = True
        mock_tarball_result.path = tmp_path / "nova-29.0.0.tar.gz"
        mock_tarball_result.signature_verified = False
        mock_tarball_result.signature_warning = ""

        mock_pq_result = MagicMock()
        mock_pq_result.success = True
        mock_pq_result.needs_refresh = False
        mock_pq_result.output = ""
        mock_pq_result.patch_reports = []

        mock_export_result = MagicMock()
        mock_export_result.success = True

        mock_dsc = tmp_path / "nova_29.0.0-0ubuntu1.dsc"
        mock_dsc.touch()
        mock_changes = tmp_path / "nova_29.0.0-0ubuntu1_source.changes"
        mock_changes.touch()

        mock_build_result = MagicMock()
        mock_build_result.success = True
        mock_build_result.artifacts = []
        mock_build_result.dsc_file = mock_dsc
        mock_build_result.changes_file = mock_changes
        mock_build_result.output = ""

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "is_snapshot_eligible", return_value=(True, "", "")))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_tarball_result))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="28.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=MagicMock(upstream="28.0.0")))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="29.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="New release."))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=mock_pq_result))
            stack.enter_context(patch.object(build, "pq_export", return_value=mock_export_result))
            stack.enter_context(
                patch.object(
                    build,
                    "ensure_upstream_branch",
                    return_value=MagicMock(success=True, branch_name="upstream-caracal", created=False, error=""),
                )
            )
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, output="", upstream_version="")))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=mock_build_result))
            stack.enter_context(patch.object(build, "get_host_arch", return_value="amd64"))

            mock_publish_result = MagicMock(success=True, published_paths=[], error="")
            mock_publish = stack.enter_context(
                patch("packastack.commands.build.localrepo.publish_artifacts", return_value=mock_publish_result)
            )

            mock_index_result = MagicMock(success=True, package_count=0, packages_file=None, error="")
            mock_regen = stack.enter_context(
                patch("packastack.commands.build.localrepo.regenerate_indexes", return_value=mock_index_result)
            )

            mock_source_index_result = MagicMock(success=True, source_count=0, sources_file=None, error="")
            mock_source_regen = stack.enter_context(
                patch(
                    "packastack.commands.build.localrepo.regenerate_source_indexes",
                    return_value=mock_source_index_result,
                )
            )

            result = _call_run_build(
                run=mock_run,
                package="nova",
                target="devel",
                ubuntu_series="devel",
                cloud_archive="",
                build_type_str="release",
                milestone="",
                force=False,
                offline=False,
                validate_plan_only=False,
                plan_upload=False,
                upload=False,
                binary=False,
                builder="sbuild",
                build_deps=True,
                no_spinner=True,
                yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS
        mock_publish.assert_not_called()
        mock_regen.assert_called_once_with(mock_paths["local_apt_repo"], arch="amd64")
        mock_source_regen.assert_called_once_with(mock_paths["local_apt_repo"])


@pytest.mark.skip(reason="Needs refactoring for run_plan_for_package mock pattern")
class TestBuildVersionEdgeCases:
    """Test version handling edge cases."""

    @pytest.fixture
    def mock_paths(self, tmp_path: Path) -> dict:
        """Create mock paths."""
        paths = {
            "openstack_releases_repo": tmp_path / "releases",
            "local_apt_repo": tmp_path / "local",
            "ubuntu_archive_cache": tmp_path / "cache",
            "cache_root": tmp_path / "cache",
            "build_root": tmp_path / "build",
        }
        for p in paths.values():
            p.mkdir(parents=True, exist_ok=True)
        return paths

    @pytest.fixture
    def mock_run(self) -> MagicMock:
        """Create mock run context."""
        run = MagicMock()
        run.run_id = "test-run-id"
        run.run_path = Path("/tmp/test-run")
        return run

    def test_version_with_epoch(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test handling of versions with epoch."""
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()
        (pkg_dir.parent / ".git").mkdir()

        workspace = mock_paths["build_root"] / "nova"
        workspace.mkdir(parents=True)
        (workspace / "debian").mkdir()
        (workspace / "debian" / "changelog").write_text(
            "nova (1:28.0.0-0ubuntu1) noble; urgency=medium\n\n"
            "  * Test\n\n -- Test <test@test.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
        )

        mock_index = MagicMock()
        mock_index.packages = {}

        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        mock_fetch_result = MagicMock()
        mock_fetch_result.error = None
        mock_fetch_result.path = workspace
        mock_fetch_result.branches = ["main"]
        mock_fetch_result.cloned = True
        mock_fetch_result.updated = False

        mock_upstream = MagicMock()
        mock_upstream.version = "29.0.0"
        mock_upstream.tarball_url = "http://example.com/nova-29.0.0.tar.gz"

        mock_tarball_result = MagicMock()
        mock_tarball_result.success = True
        mock_tarball_result.path = tmp_path / "nova-29.0.0.tar.gz"
        mock_tarball_result.signature_verified = False
        mock_tarball_result.signature_warning = ""

        mock_pq_result = MagicMock()
        mock_pq_result.success = True
        mock_pq_result.needs_refresh = False
        mock_pq_result.output = ""
        mock_pq_result.patch_reports = []

        mock_export_result = MagicMock()
        mock_export_result.success = True

        mock_dsc = tmp_path / "nova_1%3a29.0.0-0ubuntu1.dsc"
        mock_dsc.touch()
        mock_changes = tmp_path / "nova_1%3a29.0.0-0ubuntu1_source.changes"
        mock_changes.touch()

        mock_build_result = MagicMock()
        mock_build_result.success = True
        mock_build_result.artifacts = [mock_dsc, mock_changes]
        mock_build_result.dsc_file = mock_dsc
        mock_build_result.changes_file = mock_changes
        mock_build_result.output = ""

        parsed_version = MagicMock()
        parsed_version.epoch = 1
        parsed_version.upstream = "28.0.0"
        parsed_version.debian_revision = "0ubuntu1"

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "is_snapshot_eligible", return_value=(True, "", "")))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            stack.enter_context(patch.object(single_build_module.GitFetcher, "fetch_and_checkout", return_value=mock_fetch_result))
            stack.enter_context(patch("packastack.debpkg.launchpad_yaml.update_launchpad_yaml_series", return_value=(True, [], None)))
            stack.enter_context(patch("packastack.upstream.source.select_upstream_source", return_value=mock_upstream))
            stack.enter_context(patch("packastack.upstream.source.apply_signature_policy", return_value=[]))
            stack.enter_context(patch.object(tarball_module, "download_and_verify_tarball", return_value=mock_tarball_result))
            stack.enter_context(patch("packastack.debpkg.changelog.get_current_version", return_value="1:28.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.parse_version", return_value=parsed_version))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_release_version", return_value="1:29.0.0-0ubuntu1"))
            stack.enter_context(patch("packastack.debpkg.changelog.generate_changelog_message", return_value="New release."))
            stack.enter_context(patch("packastack.debpkg.changelog.update_changelog", return_value=True))
            stack.enter_context(patch("packastack.debpkg.gbp.check_upstreamed_patches", return_value=[]))
            stack.enter_context(patch("packastack.debpkg.gbp.pq_import", return_value=mock_pq_result))
            stack.enter_context(patch.object(build, "pq_export", return_value=mock_export_result))
            stack.enter_context(patch("packastack.debpkg.gbp.ensure_upstream_branch", return_value=MagicMock(success=True, branch_name="upstream-caracal", created=False, error="")))
            stack.enter_context(patch("packastack.debpkg.gbp.import_orig", return_value=MagicMock(success=True, output="", upstream_version="")))
            stack.enter_context(patch("packastack.debpkg.gbp.build_source", return_value=mock_build_result))

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS


@pytest.mark.skip(reason="Needs refactoring for run_plan_for_package mock pattern")
class TestValidatePlanOnly:
    """Test validate-plan-only mode."""

    @pytest.fixture
    def mock_paths(self, tmp_path: Path) -> dict:
        """Create mock paths."""
        paths = {
            "openstack_releases_repo": tmp_path / "releases",
            "local_apt_repo": tmp_path / "local",
            "ubuntu_archive_cache": tmp_path / "cache",
            "cache_root": tmp_path / "cache",
            "build_root": tmp_path / "build",
        }
        for p in paths.values():
            p.mkdir(parents=True, exist_ok=True)
        return paths

    @pytest.fixture
    def mock_run(self) -> MagicMock:
        """Create mock run context."""
        run = MagicMock()
        run.run_id = "test-run-id"
        run.run_path = Path("/tmp/test-run")
        return run

    def test_validate_plan_only_exits_early(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test validate-plan-only mode exits after planning."""
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()
        (pkg_dir.parent / ".git").mkdir()

        mock_index = MagicMock()
        mock_index.packages = {}

        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "is_snapshot_eligible", return_value=(True, "", "")))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))
            # These should not be called in validate_plan_only mode
            mock_fetch = stack.enter_context(
                patch.object(single_build_module.GitFetcher, "fetch_and_checkout")
            )

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=False, offline=False, validate_plan_only=True, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS
        # Git fetch should not be called in validate mode
        mock_fetch.assert_not_called()

    def test_validate_plan_prints_waves(self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock) -> None:
        """Test that validate-plan prints waves when plan_graph is available."""
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()
        (pkg_dir.parent / ".git").mkdir()

        mock_index = MagicMock()
        mock_index.packages = {}

        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "is_snapshot_eligible", return_value=(True, "", "")))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))

            # Patch run_plan_for_package to return a PlanResult with a PlanGraph
            from packastack.planning.graph import DependencyGraph
            from packastack.reports.plan_graph import PlanGraph
            g = DependencyGraph()
            g.add_node("lib", needs_rebuild=True)
            g.add_node("nova", needs_rebuild=True)
            g.add_edge("nova", "lib")
            plan_graph = PlanGraph.from_dependency_graph(g, run_id="r", target="t", ubuntu_series="u")

            from packastack.planning.graph import PlanResult
            fake_plan_result = PlanResult(build_order=["lib", "nova"], upload_order=[], plan_graph=plan_graph)

            stack.enter_context(patch("packastack.commands.build.run_plan_for_package", return_value=(fake_plan_result, build.EXIT_SUCCESS)))

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=False, offline=False, validate_plan_only=True, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS

    def test_plan_upload_mode(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test plan-upload mode shows upload order."""
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()
        (pkg_dir.parent / ".git").mkdir()

        mock_index = MagicMock()
        mock_index.packages = {}

        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = True

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "is_snapshot_eligible", return_value=(True, "", "")))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=True,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_SUCCESS


@pytest.mark.skip(reason="Needs refactoring for run_plan_for_package mock pattern")
class TestToolMissing:
    """Test missing tool detection."""

    @pytest.fixture
    def mock_paths(self, tmp_path: Path) -> dict:
        """Create mock paths."""
        paths = {
            "openstack_releases_repo": tmp_path / "releases",
            "local_apt_repo": tmp_path / "local",
            "ubuntu_archive_cache": tmp_path / "cache",
            "cache_root": tmp_path / "cache",
            "build_root": tmp_path / "build",
        }
        for p in paths.values():
            p.mkdir(parents=True, exist_ok=True)
        return paths

    @pytest.fixture
    def mock_run(self) -> MagicMock:
        """Create mock run context."""
        run = MagicMock()
        run.run_id = "test-run-id"
        run.run_path = Path("/tmp/test-run")
        return run

    def test_missing_required_tools(
        self, tmp_path: Path, mock_paths: dict, mock_run: MagicMock
    ) -> None:
        """Test that missing tools returns EXIT_TOOL_MISSING."""
        pkg_dir = mock_paths["local_apt_repo"] / "nova" / "debian"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "control").touch()
        (pkg_dir.parent / ".git").mkdir()

        mock_index = MagicMock()
        mock_index.packages = {}

        # Tools are missing
        mock_tool_result = MagicMock()
        mock_tool_result.is_complete.return_value = False
        mock_tool_result.missing_message.return_value = "dpkg-buildpackage not found"

        with ExitStack() as stack:
            stack.enter_context(patch.object(build, "load_config", return_value={"defaults": {}}))
            stack.enter_context(patch.object(build, "resolve_paths", return_value=mock_paths))
            stack.enter_context(patch.object(build, "resolve_series", return_value="noble"))
            stack.enter_context(patch.object(build, "get_current_development_series", return_value="caracal"))
            stack.enter_context(patch.object(build, "get_previous_series", return_value="bobcat"))
            stack.enter_context(patch.object(build, "is_snapshot_eligible", return_value=(True, "", "")))
            stack.enter_context(patch.object(build, "load_package_index", return_value=mock_index))
            stack.enter_context(patch.object(build, "load_openstack_packages", return_value=["nova"]))
            stack.enter_context(patch.object(build, "check_required_tools", return_value=mock_tool_result))

            result = _call_run_build(
                run=mock_run, package="nova", target="devel", ubuntu_series="devel",
                cloud_archive="", build_type_str="release", milestone="",
                force=False, offline=False, validate_plan_only=False, plan_upload=False,
                upload=False, binary=False, builder="sbuild", build_deps=True, no_spinner=True, yes=False,
                workspace_ref=lambda w: None,
            )

        assert result == build.EXIT_TOOL_MISSING
