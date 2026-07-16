# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for the single_build module.

These tests verify the extracted phase functions work correctly in isolation,
demonstrating the testability benefits of the refactoring.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from packastack.build.single_build import (
    BuildResult,
    FetchResult,
    PhaseResult,
    PrepareResult,
    SingleBuildContext,
    ValidateDepsResult,
    _resolve_modify_delete_conflicts,
    _series_commit_name,
    report_dependency_satisfaction,
    resolve_lp_bug_key,
)


class _FakePackage:
    def __init__(self, version: str, component: str = "main"):
        self.version = version
        self.component = component


class _FakePackageIndex:
    def __init__(self, versions: dict[str, str]):
        self.versions = versions

    def find_package(self, name: str) -> _FakePackage | None:
        version = self.versions.get(name)
        if version is None:
            return None
        return _FakePackage(version)

    def get_version(self, name: str) -> str | None:
        return self.versions.get(name)


class TestPhaseResult:
    """Tests for the PhaseResult dataclass."""

    def test_ok_creates_successful_result(self):
        """Test that PhaseResult.ok() creates a successful result."""
        result = PhaseResult.ok()
        assert result.success is True
        assert result.exit_code == 0
        assert result.error == ""

    def test_fail_creates_failed_result(self):
        """Test that PhaseResult.fail() creates a failed result."""
        result = PhaseResult.fail(3, "Clone failed")
        assert result.success is False
        assert result.exit_code == 3
        assert result.error == "Clone failed"

    def test_fail_with_default_error(self):
        """Test that PhaseResult.fail() works without error message."""
        result = PhaseResult.fail(5)
        assert result.success is False
        assert result.exit_code == 5
        assert result.error == ""


class TestFetchResult:
    """Tests for the FetchResult dataclass."""

    def test_default_values(self):
        """Test that FetchResult has sensible defaults."""
        result = FetchResult()
        assert result.pkg_repo is None
        assert result.workspace is None
        assert result.watch_updated is False
        assert result.signing_key_updated is False

    def test_with_values(self):
        """Test FetchResult with explicit values."""
        result = FetchResult(
            pkg_repo=Path("/tmp/repo"),
            workspace=Path("/tmp/workspace"),
            watch_updated=True,
            signing_key_updated=False,
        )
        assert result.pkg_repo == Path("/tmp/repo")
        assert result.workspace == Path("/tmp/workspace")
        assert result.watch_updated is True
        assert result.signing_key_updated is False


class TestPrepareResult:
    """Tests for the PrepareResult dataclass."""

    def test_default_values(self):
        """Test that PrepareResult has sensible defaults."""
        result = PrepareResult()
        assert result.upstream_tarball is None
        assert result.signature_verified is False
        assert result.signature_warning == ""
        assert result.git_sha == ""
        assert result.git_date == ""
        assert result.snapshot_result is None
        assert result.new_version == ""


class TestValidateDepsResult:
    """Tests for the ValidateDepsResult dataclass."""

    def test_default_values(self):
        """Test that ValidateDepsResult has sensible defaults."""
        result = ValidateDepsResult()
        assert result.missing_deps == []
        assert result.buildable_deps == []
        assert result.upstream_repo_path is None

    def test_with_deps(self):
        """Test ValidateDepsResult with dependency lists."""
        result = ValidateDepsResult(
            missing_deps=["python3-oslo-config", "python3-oslo-utils"],
            buildable_deps=["oslo.config", "oslo.utils"],
        )
        assert len(result.missing_deps) == 2
        assert len(result.buildable_deps) == 2


class TestBuildResult:
    """Tests for the BuildResult dataclass."""

    def test_default_values(self):
        """Test that BuildResult has sensible defaults."""
        result = BuildResult()
        assert result.source_success is False
        assert result.binary_success is False
        assert result.artifacts == []
        assert result.dsc_file is None
        assert result.changes_file is None

    def test_with_artifacts(self):
        """Test BuildResult with artifact paths."""
        result = BuildResult(
            source_success=True,
            binary_success=True,
            artifacts=[Path("/tmp/foo.dsc"), Path("/tmp/foo.deb")],
            dsc_file=Path("/tmp/foo.dsc"),
            changes_file=Path("/tmp/foo.changes"),
        )
        assert result.source_success is True
        assert result.binary_success is True
        assert len(result.artifacts) == 2


class TestSingleBuildContext:
    """Tests for the SingleBuildContext dataclass."""

    def test_minimal_context(self):
        """Test creating a minimal context."""
        from packastack.planning.type_selection import BuildType

        run = MagicMock()
        ctx = SingleBuildContext(
            pkg_name="python-oslo-config",
            package="oslo.config",
            run=run,
            target="devel",
            openstack_target="dalmatian",
            ubuntu_series="devel",
            resolved_ubuntu="plucky",
            cloud_archive="",
            build_type=BuildType.SNAPSHOT,
            build_type_str="snapshot",
            binary=True,
            builder="sbuild",
            force=False,
            offline=False,
            skip_repo_regen=False,
            no_spinner=False,
            build_deps=True,
            archive_deps=False,
            min_version_policy="",
            dep_report=False,
            fail_on_cloud_archive_required=False,
            fail_on_mir_required=False,
            update_control_min_versions=False,
            normalize_to_prev_lts_floor=False,
            dry_run_control_edit=False,
            paths={"cache_root": Path("/tmp/cache")},
        )

        assert ctx.pkg_name == "python-oslo-config"
        assert ctx.package == "oslo.config"
        assert ctx.build_type == BuildType.SNAPSHOT
        assert ctx.binary is True


class TestSeriesCommitName:
    """Tests for package commit series display names."""

    def test_capitalizes_short_series_name(self):
        assert _series_commit_name("hibiscus") == "Hibiscus"

    def test_uses_first_word_from_display_name(self):
        assert _series_commit_name("resolute raccoon") == "Resolute"


class TestReportDependencySatisfaction:
    """Tests for dependency satisfaction reporting."""

    def test_commits_control_bump_and_records_changelog_entry(self, tmp_path: Path):
        from packastack.planning.type_selection import BuildType

        pkg_repo = tmp_path / "pkg"
        debian_dir = pkg_repo / "debian"
        debian_dir.mkdir(parents=True)
        (pkg_repo / ".git").mkdir()
        (debian_dir / "control").write_text(
            "\n".join(
                [
                    "Source: python-foo",
                    "Build-Depends: debhelper-compat (= 13), python3-foo",
                    "Build-Depends-Indep: python3-bar",
                    "",
                    "Package: python3-foo",
                    "Architecture: all",
                    "Depends: ${misc:Depends}, ${python3:Depends}",
                    "Description: test package",
                    " test",
                    "",
                ]
            )
        )

        run = MagicMock()
        run.run_id = "20260708-000000"
        run.logs_path = tmp_path / "logs"
        index = _FakePackageIndex(
            {
                "debhelper-compat": "13",
                "python3-foo": "2.4.0-1",
                "python3-bar": "1.8.0-1",
            }
        )
        ctx = SingleBuildContext(
            pkg_name="python-foo",
            package="foo",
            run=run,
            target="devel",
            openstack_target="hibiscus",
            ubuntu_series="devel",
            resolved_ubuntu="resolute",
            cloud_archive="",
            build_type=BuildType.RELEASE,
            build_type_str="release",
            binary=True,
            builder="sbuild",
            force=False,
            offline=False,
            skip_repo_regen=False,
            no_spinner=True,
            build_deps=False,
            archive_deps=False,
            min_version_policy="",
            dep_report=True,
            fail_on_cloud_archive_required=False,
            fail_on_mir_required=False,
            update_control_min_versions=True,
            normalize_to_prev_lts_floor=False,
            dry_run_control_edit=False,
            paths={"cache_root": tmp_path / "cache"},
            pkg_repo=pkg_repo,
            ubuntu_index=index,
            current_lts_codename="resolute raccoon",
            current_lts_index=index,
            upstream_min_versions={},
        )

        with patch("packastack.build.single_build.git_commit") as commit:
            commit.return_value.returncode = 0
            result = report_dependency_satisfaction(ctx)

        assert result.success is True
        expected_msg = "d/control: Bump dependencies for Resolute."
        commit.assert_called_once_with(
            pkg_repo,
            expected_msg,
            files=["debian/control"],
        )
        control_text = (debian_dir / "control").read_text()
        assert "python3-foo (>= 2.4.0-1)" in control_text
        assert "python3-bar (>= 1.8.0-1)" in control_text


class TestResolveLpBugKey:
    """Tests for resolve_lp_bug_key helper."""

    @pytest.mark.parametrize(
        "build_type, is_library, upstream_version, expected",
        [
            # Snapshots always map to milestone2
            ("snapshot", False, "", "milestone2"),
            ("snapshot", True, "", "milestone2"),
            ("snapshot", False, "26.0.0.0b1", "milestone2"),
            # Library releases -> client-lib-release (regardless of version)
            ("release", True, "4.7.0", "client-lib-release"),
            ("release", True, "4.7.0rc1", "client-lib-release"),
            # Service RC releases -> service-rc1
            ("release", False, "26.0.0.0rc1", "service-rc1"),
            ("release", False, "2024.2.0rc2", "service-rc1"),
            # Service final releases -> service-release
            ("release", False, "26.0.0", "service-release"),
            ("release", False, "2024.2.0", "service-release"),
            ("release", False, "", "service-release"),
            # Unknown build types pass through as-is
            ("custom", False, "", "custom"),
        ],
    )
    def test_resolve_lp_bug_key(self, build_type, is_library, upstream_version, expected):
        """Test that build scenarios map to the correct config key."""
        assert resolve_lp_bug_key(build_type, is_library, upstream_version) == expected


class TestFetchPackagingRepo:
    """Tests for fetch_packaging_repo function."""

    @patch("packastack.build.single_build.GitFetcher")
    @patch("packastack.build.single_build.activity_spinner")
    @patch("packastack.build.single_build.activity")
    def test_fetch_failure_returns_error(self, mock_activity, mock_spinner, mock_fetcher_cls):
        """Test that clone failure returns appropriate error."""
        from packastack.build.single_build import fetch_packaging_repo
        from packastack.planning.type_selection import BuildType

        # Setup mocks
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_and_checkout.return_value = MagicMock(
            error="Network error",
            path=None,
        )
        mock_fetcher_cls.return_value = mock_fetcher
        mock_spinner.return_value.__enter__ = MagicMock()
        mock_spinner.return_value.__exit__ = MagicMock()

        run = MagicMock()
        run.run_id = "test-run-123"
        run.add_log_mirror = MagicMock()

        ctx = SingleBuildContext(
            pkg_name="test-package",
            package="test",
            run=run,
            target="devel",
            openstack_target="dalmatian",
            ubuntu_series="devel",
            resolved_ubuntu="plucky",
            cloud_archive="",
            build_type=BuildType.RELEASE,
            build_type_str="release",
            binary=True,
            builder="sbuild",
            force=False,
            offline=False,
            skip_repo_regen=False,
            no_spinner=False,
            build_deps=True,
            archive_deps=False,
            min_version_policy="",
            dep_report=False,
            fail_on_cloud_archive_required=False,
            fail_on_mir_required=False,
            update_control_min_versions=False,
            normalize_to_prev_lts_floor=False,
            dry_run_control_edit=False,
            paths={
                "cache_root": Path("/tmp/cache"),
                "build_root": Path("/tmp/build"),
            },
            cfg={},
        )

        phase_result, _fetch_result = fetch_packaging_repo(ctx)

        assert phase_result.success is False
        assert phase_result.exit_code == 3  # EXIT_FETCH_FAILED
        assert "Network error" in phase_result.error
        run.write_summary.assert_called_once()


# =============================================================================
# AI Patch Diagnosis Tests
# =============================================================================


def _make_ctx_for_patch_test(
    tmp_path: Path,
    *,
    ai_enabled: bool = True,
    has_upstream: bool = True,
) -> SingleBuildContext:
    """Create a minimal SingleBuildContext for _ai_diagnose_patch_failure tests."""
    from packastack.planning.type_selection import BuildType

    run = MagicMock()
    run.log_event = MagicMock()

    pkg_repo = tmp_path / "pkg"
    pkg_repo.mkdir()
    # Create minimal .git structure for exclude-file logic
    (pkg_repo / ".git" / "info").mkdir(parents=True)

    ctx = SingleBuildContext(
        pkg_name="osc-lib",
        package="osc-lib",
        run=run,
        target="devel",
        openstack_target="epoxy",
        ubuntu_series="plucky",
        resolved_ubuntu="plucky",
        cloud_archive="",
        build_type=BuildType.RELEASE,
        build_type_str="release",
        binary=True,
        builder="sbuild",
        force=False,
        offline=False,
        skip_repo_regen=False,
        no_spinner=False,
        build_deps=True,
        archive_deps=False,
        min_version_policy="",
        dep_report=False,
        fail_on_cloud_archive_required=False,
        fail_on_mir_required=False,
        update_control_min_versions=False,
        normalize_to_prev_lts_floor=False,
        dry_run_control_edit=False,
        paths={
            "cache_root": tmp_path / "cache",
            "build_root": tmp_path / "build",
        },
        ai_enabled=ai_enabled,
        cfg={"ai": {"api_key": "test-key", "model": "test", "max_tokens": 100, "timeout": 10}},
        pkg_repo=pkg_repo,
    )

    if has_upstream:
        ctx.upstream = MagicMock()
        ctx.upstream.version = "3.2.0"

    return ctx


def _setup_patches(pkg_repo: Path, patches: dict[str, str]) -> None:
    """Create patch files and a series file in debian/patches."""
    patches_dir = pkg_repo / "debian" / "patches"
    patches_dir.mkdir(parents=True)
    series_lines = []
    for name, content in patches.items():
        (patches_dir / name).write_text(content)
        series_lines.append(name)
    (patches_dir / "series").write_text("\n".join(series_lines) + "\n")


class TestAiDiagnosePatchFailure:
    """Tests for _ai_diagnose_patch_failure function."""

    def test_extracts_patch_name_from_error_and_drops(self, tmp_path: Path) -> None:
        """Test extracts patch name from gbp error output and drops it."""
        from packastack.build.single_build import _ai_diagnose_patch_failure

        ctx = _make_ctx_for_patch_test(tmp_path)
        _setup_patches(ctx.pkg_repo, {"fix-brittle-tests.patch": "diff content"})

        phase = PhaseResult.fail(4, "Patch fix-brittle-tests.patch failed to apply")

        diagnosis_result = MagicMock()
        diagnosis_result.diagnosed = True
        diagnosis_result.can_drop = True
        diagnosis_result.explanation = "Patch was merged upstream"

        drop_result = MagicMock()
        drop_result.success = True

        commit_result = MagicMock()
        commit_result.returncode = 0
        commit_result.stderr = ""

        with (
            patch(
                "packastack.debpkg.gbp.run_command",
                return_value=(0, "", ""),
            ),
            patch(
                "packastack.ai.patch_diagnosis.diagnose_patch_failure",
                return_value=diagnosis_result,
            ) as mock_diagnose,
            patch(
                "packastack.debpkg.gbp.drop_patch",
                return_value=drop_result,
            ) as mock_drop,
            patch(
                "packastack.build.single_build.git_commit",
                return_value=commit_result,
            ),
        ):
            result = _ai_diagnose_patch_failure(ctx, phase)

        assert result is True
        mock_diagnose.assert_called_once()
        assert mock_diagnose.call_args.kwargs["patch_name"] == "fix-brittle-tests.patch"
        mock_drop.assert_called_once_with(ctx.pkg_repo, "fix-brittle-tests.patch")
        ctx.run.log_event.assert_called()

    def test_reverse_apply_failure_tries_mechanical_then_ai(self, tmp_path: Path) -> None:
        """Test non-upstreamed patches try mechanical refresh, then AI."""
        from packastack.build.single_build import _ai_diagnose_patch_failure

        ctx = _make_ctx_for_patch_test(tmp_path)
        _setup_patches(ctx.pkg_repo, {"drop-zun.patch": "diff content"})

        phase = PhaseResult.fail(4, "Patch drop-zun.patch failed to apply")

        mech_result = MagicMock()
        mech_result.refreshed = False
        mech_result.error = "All strategies failed"

        ai_result = MagicMock()
        ai_result.refreshed = False
        ai_result.error = "Cannot refresh"
        ai_result.explanation = ""

        with (
            patch(
                "packastack.debpkg.gbp.run_command",
                return_value=(1, "", "patch does not apply"),
            ),
            patch(
                "packastack.ai.patch_diagnosis.diagnose_patch_failure",
            ) as mock_diagnose,
            patch(
                "packastack.ai.patch_diagnosis.attempt_mechanical_refresh",
                return_value=mech_result,
            ) as mock_mech,
            patch(
                "packastack.ai.patch_diagnosis.refresh_failing_patch",
                return_value=ai_result,
            ) as mock_ai_refresh,
        ):
            result = _ai_diagnose_patch_failure(ctx, phase)

        assert result is False
        mock_diagnose.assert_not_called()
        mock_mech.assert_called_once()
        mock_ai_refresh.assert_called_once()

    def test_reverse_apply_mechanical_refresh_succeeds(self, tmp_path: Path) -> None:
        """Test mechanical refresh succeeds without calling AI."""
        from packastack.build.single_build import _ai_diagnose_patch_failure

        ctx = _make_ctx_for_patch_test(tmp_path)
        _setup_patches(ctx.pkg_repo, {"drop-zun.patch": "old diff"})

        phase = PhaseResult.fail(4, "Patch drop-zun.patch failed to apply")

        mech_result = MagicMock()
        mech_result.refreshed = True
        mech_result.patch_content = "mechanically refreshed diff"
        mech_result.explanation = "Fixed with fuzz"

        with (
            patch(
                "packastack.debpkg.gbp.run_command",
                return_value=(1, "", "patch does not apply"),
            ),
            patch(
                "packastack.ai.patch_diagnosis.attempt_mechanical_refresh",
                return_value=mech_result,
            ),
            patch(
                "packastack.ai.patch_diagnosis.refresh_failing_patch",
            ) as mock_ai_refresh,
        ):
            result = _ai_diagnose_patch_failure(ctx, phase)

        assert result is True
        mock_ai_refresh.assert_not_called()
        patch_path = ctx.pkg_repo / "debian" / "patches" / "drop-zun.patch"
        assert patch_path.read_text() == "mechanically refreshed diff"

    def test_reverse_apply_ai_refresh_succeeds(self, tmp_path: Path) -> None:
        """Test AI refresh fallback writes patch when mechanical fails."""
        from packastack.build.single_build import _ai_diagnose_patch_failure

        ctx = _make_ctx_for_patch_test(tmp_path)
        _setup_patches(ctx.pkg_repo, {"drop-zun.patch": "old diff"})

        phase = PhaseResult.fail(4, "Patch drop-zun.patch failed to apply")

        mech_result = MagicMock()
        mech_result.refreshed = False
        mech_result.error = "All strategies failed"

        ai_result = MagicMock()
        ai_result.refreshed = True
        ai_result.patch_content = "ai refreshed diff"
        ai_result.explanation = "Updated context lines"

        with (
            patch(
                "packastack.debpkg.gbp.run_command",
                return_value=(1, "", "patch does not apply"),
            ),
            patch(
                "packastack.ai.patch_diagnosis.attempt_mechanical_refresh",
                return_value=mech_result,
            ),
            patch(
                "packastack.ai.patch_diagnosis.refresh_failing_patch",
                return_value=ai_result,
            ),
        ):
            result = _ai_diagnose_patch_failure(ctx, phase)

        assert result is True
        patch_path = ctx.pkg_repo / "debian" / "patches" / "drop-zun.patch"
        assert patch_path.read_text() == "ai refreshed diff"
        ctx.run.log_event.assert_called()
        logged = ctx.run.log_event.call_args[0][0]
        assert logged["event"] == "ai.patch_refreshed"

    def test_pc_directory_excluded(self, tmp_path: Path) -> None:
        """Test that .pc is added to .git/info/exclude."""
        from packastack.build.single_build import _ai_diagnose_patch_failure

        ctx = _make_ctx_for_patch_test(tmp_path)
        _setup_patches(ctx.pkg_repo, {"fix.patch": "diff"})

        phase = PhaseResult.fail(4, "Patch fix.patch failed to apply")

        mech_result = MagicMock()
        mech_result.refreshed = True
        mech_result.patch_content = "refreshed"
        mech_result.explanation = "ok"

        with (
            patch(
                "packastack.debpkg.gbp.run_command",
                return_value=(1, "", "does not apply"),
            ),
            patch(
                "packastack.ai.patch_diagnosis.attempt_mechanical_refresh",
                return_value=mech_result,
            ),
        ):
            _ai_diagnose_patch_failure(ctx, phase)

        exclude = ctx.pkg_repo / ".git" / "info" / "exclude"
        assert ".pc" in exclude.read_text().splitlines()

    def test_pc_exclusion_idempotent(self, tmp_path: Path) -> None:
        """Test that .pc is not duplicated in .git/info/exclude."""
        from packastack.build.single_build import _ai_diagnose_patch_failure

        ctx = _make_ctx_for_patch_test(tmp_path)
        _setup_patches(ctx.pkg_repo, {"fix.patch": "diff"})

        # Pre-populate exclude with .pc
        exclude = ctx.pkg_repo / ".git" / "info" / "exclude"
        exclude.write_text(".pc\n")

        phase = PhaseResult.fail(4, "Patch fix.patch failed to apply")

        mech_result = MagicMock()
        mech_result.refreshed = True
        mech_result.patch_content = "refreshed"
        mech_result.explanation = "ok"

        with (
            patch(
                "packastack.debpkg.gbp.run_command",
                return_value=(1, "", "does not apply"),
            ),
            patch(
                "packastack.ai.patch_diagnosis.attempt_mechanical_refresh",
                return_value=mech_result,
            ),
        ):
            _ai_diagnose_patch_failure(ctx, phase)

        lines = exclude.read_text().splitlines()
        assert lines.count(".pc") == 1

    def test_reverse_apply_failure_refresh_write_fails(self, tmp_path: Path) -> None:
        """Test continues when writing refreshed patch file fails."""
        from packastack.build.single_build import _ai_diagnose_patch_failure

        ctx = _make_ctx_for_patch_test(tmp_path)
        _setup_patches(ctx.pkg_repo, {"fix.patch": "diff"})

        # Make the patch file read-only to cause write failure
        patch_path = ctx.pkg_repo / "debian" / "patches" / "fix.patch"
        patch_path.chmod(0o444)
        # Also make the parent directory read-only
        (ctx.pkg_repo / "debian" / "patches").chmod(0o555)

        phase = PhaseResult.fail(4, "Patch fix.patch failed to apply")

        mech_result = MagicMock()
        mech_result.refreshed = True
        mech_result.patch_content = "refreshed diff"
        mech_result.explanation = "Fixed"

        with (
            patch(
                "packastack.debpkg.gbp.run_command",
                return_value=(1, "", "does not apply"),
            ),
            patch(
                "packastack.ai.patch_diagnosis.attempt_mechanical_refresh",
                return_value=mech_result,
            ),
        ):
            result = _ai_diagnose_patch_failure(ctx, phase)

        # Restore permissions for cleanup
        (ctx.pkg_repo / "debian" / "patches").chmod(0o755)
        patch_path.chmod(0o644)

        assert result is False

    def test_falls_back_to_series_file(self, tmp_path: Path) -> None:
        """Test falls back to series file when no patch names in error output."""
        from packastack.build.single_build import _ai_diagnose_patch_failure

        ctx = _make_ctx_for_patch_test(tmp_path)
        _setup_patches(ctx.pkg_repo, {"fix.patch": "diff"})

        # Error with no parseable patch name
        phase = PhaseResult.fail(4, "patch queue import failed")

        diagnosis_result = MagicMock()
        diagnosis_result.diagnosed = True
        diagnosis_result.can_drop = True
        diagnosis_result.explanation = "Upstreamed"

        drop_result = MagicMock()
        drop_result.success = True

        commit_result = MagicMock()
        commit_result.returncode = 0

        with (
            patch(
                "packastack.debpkg.gbp.run_command",
                return_value=(0, "", ""),
            ),
            patch(
                "packastack.ai.patch_diagnosis.diagnose_patch_failure",
                return_value=diagnosis_result,
            ) as mock_diagnose,
            patch(
                "packastack.debpkg.gbp.drop_patch",
                return_value=drop_result,
            ),
            patch(
                "packastack.build.single_build.git_commit",
                return_value=commit_result,
            ),
        ):
            result = _ai_diagnose_patch_failure(ctx, phase)

        assert result is True
        # Should have tried the patch from series file
        assert mock_diagnose.call_args.kwargs["patch_name"] == "fix.patch"

    def test_ai_says_keep_returns_false(self, tmp_path: Path) -> None:
        """Test returns False when AI says patch should be kept."""
        from packastack.build.single_build import _ai_diagnose_patch_failure

        ctx = _make_ctx_for_patch_test(tmp_path)
        _setup_patches(ctx.pkg_repo, {"ubuntu-fix.patch": "diff"})

        phase = PhaseResult.fail(4, "Patch ubuntu-fix.patch failed to apply")

        diagnosis_result = MagicMock()
        diagnosis_result.diagnosed = True
        diagnosis_result.can_drop = False
        diagnosis_result.explanation = "Ubuntu-specific fix still needed"

        with (
            patch(
                "packastack.debpkg.gbp.run_command",
                return_value=(0, "", ""),
            ),
            patch(
                "packastack.ai.patch_diagnosis.diagnose_patch_failure",
                return_value=diagnosis_result,
            ),
        ):
            result = _ai_diagnose_patch_failure(ctx, phase)

        assert result is False

    def test_no_failing_patches_returns_false(self, tmp_path: Path) -> None:
        """Test returns False when no failing patches can be identified."""
        from packastack.build.single_build import _ai_diagnose_patch_failure

        ctx = _make_ctx_for_patch_test(tmp_path)
        # No patches dir at all
        phase = PhaseResult.fail(4, "some unrelated error")

        result = _ai_diagnose_patch_failure(ctx, phase)

        assert result is False

    def test_drop_failure_continues_to_next_patch(self, tmp_path: Path) -> None:
        """Test continues to next patch when drop fails."""
        from packastack.build.single_build import _ai_diagnose_patch_failure

        ctx = _make_ctx_for_patch_test(tmp_path)
        _setup_patches(
            ctx.pkg_repo,
            {
                "fail-drop.patch": "diff1",
                "ok-drop.patch": "diff2",
            },
        )

        phase = PhaseResult.fail(4, "patch queue import failed")

        diagnosis_result = MagicMock()
        diagnosis_result.diagnosed = True
        diagnosis_result.can_drop = True
        diagnosis_result.explanation = "Upstreamed"

        fail_drop = MagicMock(success=False, error="permission denied")
        ok_drop = MagicMock(success=True)

        commit_result = MagicMock(returncode=0, stderr="")

        with (
            patch(
                "packastack.debpkg.gbp.run_command",
                return_value=(0, "", ""),
            ),
            patch(
                "packastack.ai.patch_diagnosis.diagnose_patch_failure",
                return_value=diagnosis_result,
            ),
            patch(
                "packastack.debpkg.gbp.drop_patch",
                side_effect=[fail_drop, ok_drop],
            ),
            patch(
                "packastack.build.single_build.git_commit",
                return_value=commit_result,
            ),
        ):
            result = _ai_diagnose_patch_failure(ctx, phase)

        # Second patch succeeded, so overall True
        assert result is True

    def test_commit_failure_continues_to_next_patch(self, tmp_path: Path) -> None:
        """Test continues when git commit fails after dropping a patch."""
        from packastack.build.single_build import _ai_diagnose_patch_failure

        ctx = _make_ctx_for_patch_test(tmp_path)
        _setup_patches(
            ctx.pkg_repo,
            {
                "patch-a.patch": "diff1",
                "patch-b.patch": "diff2",
            },
        )

        phase = PhaseResult.fail(4, "patch queue import failed")

        diagnosis_result = MagicMock()
        diagnosis_result.diagnosed = True
        diagnosis_result.can_drop = True
        diagnosis_result.explanation = "Upstreamed"

        drop_result = MagicMock(success=True)
        fail_commit = MagicMock(returncode=1, stderr="commit error")
        ok_commit = MagicMock(returncode=0, stderr="")

        with (
            patch(
                "packastack.debpkg.gbp.run_command",
                return_value=(0, "", ""),
            ),
            patch(
                "packastack.ai.patch_diagnosis.diagnose_patch_failure",
                return_value=diagnosis_result,
            ),
            patch(
                "packastack.debpkg.gbp.drop_patch",
                return_value=drop_result,
            ),
            patch(
                "packastack.build.single_build.git_commit",
                side_effect=[fail_commit, ok_commit],
            ),
        ):
            result = _ai_diagnose_patch_failure(ctx, phase)

        # Second patch commit succeeded
        assert result is True

    def test_diagnosis_not_diagnosed_skips_patch(self, tmp_path: Path) -> None:
        """Test skips patch when AI returns diagnosed=False."""
        from packastack.build.single_build import _ai_diagnose_patch_failure

        ctx = _make_ctx_for_patch_test(tmp_path)
        _setup_patches(ctx.pkg_repo, {"fix.patch": "diff"})

        phase = PhaseResult.fail(4, "Patch fix.patch failed to apply")

        diagnosis_result = MagicMock()
        diagnosis_result.diagnosed = False
        diagnosis_result.error = "timeout"

        with (
            patch(
                "packastack.debpkg.gbp.run_command",
                return_value=(0, "", ""),
            ),
            patch(
                "packastack.ai.patch_diagnosis.diagnose_patch_failure",
                return_value=diagnosis_result,
            ),
        ):
            result = _ai_diagnose_patch_failure(ctx, phase)

        assert result is False

    def test_empty_error_output_with_no_series(self, tmp_path: Path) -> None:
        """Test returns False when error is empty and no series file exists."""
        from packastack.build.single_build import _ai_diagnose_patch_failure

        ctx = _make_ctx_for_patch_test(tmp_path)
        # Create debian/patches dir but no series file
        (ctx.pkg_repo / "debian" / "patches").mkdir(parents=True)

        phase = PhaseResult.fail(4, "")

        result = _ai_diagnose_patch_failure(ctx, phase)

        assert result is False

    def test_reads_patch_content_for_ai(self, tmp_path: Path) -> None:
        """Test passes patch file content to AI for diagnosis."""
        from packastack.build.single_build import _ai_diagnose_patch_failure

        ctx = _make_ctx_for_patch_test(tmp_path)
        patch_content = "--- a/setup.cfg\n+++ b/setup.cfg\n@@ -1 +1 @@\n-old\n+new\n"
        _setup_patches(ctx.pkg_repo, {"fix-setup.patch": patch_content})

        phase = PhaseResult.fail(4, "Patch fix-setup.patch failed to apply")

        diagnosis_result = MagicMock()
        diagnosis_result.diagnosed = True
        diagnosis_result.can_drop = False
        diagnosis_result.explanation = "Still needed"

        with (
            patch(
                "packastack.debpkg.gbp.run_command",
                return_value=(0, "", ""),
            ),
            patch(
                "packastack.ai.patch_diagnosis.diagnose_patch_failure",
                return_value=diagnosis_result,
            ) as mock_diagnose,
        ):
            _ai_diagnose_patch_failure(ctx, phase)

        # Verify patch content was passed to AI
        assert mock_diagnose.call_args.kwargs["patch_content"] == patch_content

    def test_version_from_upstream_context(self, tmp_path: Path) -> None:
        """Test uses upstream version when available."""
        from packastack.build.single_build import _ai_diagnose_patch_failure

        ctx = _make_ctx_for_patch_test(tmp_path, has_upstream=True)
        _setup_patches(ctx.pkg_repo, {"fix.patch": "diff"})

        phase = PhaseResult.fail(4, "Patch fix.patch failed to apply")

        diagnosis_result = MagicMock()
        diagnosis_result.diagnosed = True
        diagnosis_result.can_drop = False
        diagnosis_result.explanation = "Needed"

        with (
            patch(
                "packastack.debpkg.gbp.run_command",
                return_value=(0, "", ""),
            ),
            patch(
                "packastack.ai.patch_diagnosis.diagnose_patch_failure",
                return_value=diagnosis_result,
            ) as mock_diagnose,
        ):
            _ai_diagnose_patch_failure(ctx, phase)

        assert mock_diagnose.call_args.kwargs["version"] == "3.2.0"

    def test_version_empty_without_upstream(self, tmp_path: Path) -> None:
        """Test uses empty version when no upstream context."""
        from packastack.build.single_build import _ai_diagnose_patch_failure

        ctx = _make_ctx_for_patch_test(tmp_path, has_upstream=False)
        _setup_patches(ctx.pkg_repo, {"fix.patch": "diff"})

        phase = PhaseResult.fail(4, "Patch fix.patch failed to apply")

        diagnosis_result = MagicMock()
        diagnosis_result.diagnosed = True
        diagnosis_result.can_drop = False
        diagnosis_result.explanation = "Needed"

        with (
            patch(
                "packastack.debpkg.gbp.run_command",
                return_value=(0, "", ""),
            ),
            patch(
                "packastack.ai.patch_diagnosis.diagnose_patch_failure",
                return_value=diagnosis_result,
            ) as mock_diagnose,
        ):
            _ai_diagnose_patch_failure(ctx, phase)

        assert mock_diagnose.call_args.kwargs["version"] == ""

    def test_series_file_skips_comments_and_blanks(self, tmp_path: Path) -> None:
        """Test series file parsing skips comments and blank lines."""
        from packastack.build.single_build import _ai_diagnose_patch_failure

        ctx = _make_ctx_for_patch_test(tmp_path)
        patches_dir = ctx.pkg_repo / "debian" / "patches"
        patches_dir.mkdir(parents=True)
        (patches_dir / "real.patch").write_text("diff")
        (patches_dir / "series").write_text("# comment\n\nreal.patch\n  \n# another comment\n")

        phase = PhaseResult.fail(4, "patch queue import failed")

        diagnosis_result = MagicMock()
        diagnosis_result.diagnosed = True
        diagnosis_result.can_drop = True
        diagnosis_result.explanation = "Upstreamed"

        drop_result = MagicMock(success=True)
        commit_result = MagicMock(returncode=0, stderr="")

        with (
            patch(
                "packastack.debpkg.gbp.run_command",
                return_value=(0, "", ""),
            ),
            patch(
                "packastack.ai.patch_diagnosis.diagnose_patch_failure",
                return_value=diagnosis_result,
            ) as mock_diagnose,
            patch(
                "packastack.debpkg.gbp.drop_patch",
                return_value=drop_result,
            ),
            patch(
                "packastack.build.single_build.git_commit",
                return_value=commit_result,
            ),
        ):
            result = _ai_diagnose_patch_failure(ctx, phase)

        assert result is True
        # Only the real patch should have been diagnosed
        assert mock_diagnose.call_count == 1
        assert mock_diagnose.call_args.kwargs["patch_name"] == "real.patch"


class TestResolveModifyDeleteConflicts:
    """Tests for _resolve_modify_delete_conflicts helper."""

    def test_resolves_unmerged_files(self, tmp_path: Path) -> None:
        """Should git-rm unmerged files and conclude the merge."""
        from packastack.build.single_build import run_command

        pkg_repo = tmp_path / "repo"
        pkg_repo.mkdir()

        # Set up a real git repo with a modify/delete conflict
        run_command(["git", "init", "-b", "master"], cwd=pkg_repo)
        run_command(["git", "config", "user.email", "test@test"], cwd=pkg_repo)
        run_command(["git", "config", "user.name", "Test"], cwd=pkg_repo)

        # Create initial commit with AUTHORS
        (pkg_repo / "AUTHORS").write_text("initial")
        (pkg_repo / "setup.py").write_text("setup()")
        run_command(["git", "add", "."], cwd=pkg_repo)
        run_command(["git", "commit", "-m", "initial"], cwd=pkg_repo)

        # Branch: upstream modifies AUTHORS
        run_command(["git", "checkout", "-b", "upstream"], cwd=pkg_repo)
        (pkg_repo / "AUTHORS").write_text("modified by upstream")
        run_command(["git", "add", "AUTHORS"], cwd=pkg_repo)
        run_command(["git", "commit", "-m", "upstream change"], cwd=pkg_repo)

        # Back to master: delete AUTHORS
        run_command(["git", "checkout", "master"], cwd=pkg_repo)
        run_command(["git", "rm", "AUTHORS"], cwd=pkg_repo)
        run_command(["git", "commit", "-m", "remove AUTHORS"], cwd=pkg_repo)

        # Merge upstream — triggers modify/delete conflict
        merge_rc, _, _ = run_command(["git", "merge", "-Xtheirs", "upstream"], cwd=pkg_repo)
        assert merge_rc != 0  # Conflict expected

        # Now resolve
        success, resolved = _resolve_modify_delete_conflicts(pkg_repo, "test_tag")

        assert success is True
        assert "AUTHORS" in resolved
        assert not (pkg_repo / "AUTHORS").exists()

    def test_returns_false_when_no_unmerged_files(self, tmp_path: Path) -> None:
        """Should return (False, []) when there are no unmerged files."""
        from packastack.build.single_build import run_command

        pkg_repo = tmp_path / "repo"
        pkg_repo.mkdir()
        run_command(["git", "init", "-b", "master"], cwd=pkg_repo)
        run_command(["git", "config", "user.email", "test@test"], cwd=pkg_repo)
        run_command(["git", "config", "user.name", "Test"], cwd=pkg_repo)
        (pkg_repo / "README").write_text("hello")
        run_command(["git", "add", "."], cwd=pkg_repo)
        run_command(["git", "commit", "-m", "initial"], cwd=pkg_repo)

        success, resolved = _resolve_modify_delete_conflicts(pkg_repo, "test_tag")

        assert success is False
        assert resolved == []


class TestFixSudoersArgs:
    """Tests for _fix_sudoers_args helper."""

    def test_fixes_and_commits_sudoers(self, tmp_path: Path) -> None:
        """Should strip all command arguments and commit."""
        from packastack.build.single_build import _fix_sudoers_args, run_command

        pkg_repo = tmp_path / "repo"
        pkg_repo.mkdir()
        debian = pkg_repo / "debian"
        debian.mkdir()
        (debian / "cinder_sudoers").write_text(
            "cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap /etc/cinder/rootwrap.conf *\n"
        )

        # Set up git repo
        run_command(["git", "init", "-b", "master"], cwd=pkg_repo)
        run_command(["git", "config", "user.email", "test@test"], cwd=pkg_repo)
        run_command(["git", "config", "user.name", "Test"], cwd=pkg_repo)
        run_command(["git", "add", "."], cwd=pkg_repo)
        run_command(["git", "commit", "-m", "initial"], cwd=pkg_repo)

        ctx = MagicMock()
        ctx.pkg_repo = pkg_repo

        _fix_sudoers_args(ctx)

        # Verify all arguments were stripped
        content = (debian / "cinder_sudoers").read_text()
        assert content == "cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap\n"

        # Verify a commit was made
        _rc, stdout, _stderr = run_command(["git", "log", "--oneline", "-1"], cwd=pkg_repo)
        assert "sudoers" in stdout.lower()

        # Verify the event was logged
        ctx.run.log_event.assert_called_once()
        event = ctx.run.log_event.call_args[0][0]
        assert event["event"] == "sudoers.fixed"
        assert "cinder_sudoers" in event["files"]

    def test_no_op_when_no_sudoers_files(self, tmp_path: Path) -> None:
        """Should be a no-op when no sudoers files exist."""
        from packastack.build.single_build import _fix_sudoers_args

        pkg_repo = tmp_path / "repo"
        pkg_repo.mkdir()
        (pkg_repo / "debian").mkdir()

        ctx = MagicMock()
        ctx.pkg_repo = pkg_repo

        _fix_sudoers_args(ctx)

        ctx.run.log_event.assert_not_called()

    def test_no_op_when_sudoers_already_clean(self, tmp_path: Path) -> None:
        """Should be a no-op when sudoers files have no arguments."""
        from packastack.build.single_build import _fix_sudoers_args

        pkg_repo = tmp_path / "repo"
        pkg_repo.mkdir()
        debian = pkg_repo / "debian"
        debian.mkdir()
        (debian / "cinder_sudoers").write_text(
            "cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap\n"
        )

        ctx = MagicMock()
        ctx.pkg_repo = pkg_repo

        _fix_sudoers_args(ctx)

        ctx.run.log_event.assert_not_called()


class TestBuildPackagesSbuildProposed:
    """Tests for -proposed wiring and Python-version reporting in build_packages."""

    @staticmethod
    def _make_ctx(tmp_path: Path, offline: bool = False) -> SingleBuildContext:
        from packastack.planning.type_selection import BuildType

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        return SingleBuildContext(
            pkg_name="python-oslo-config",
            package="oslo.config",
            run=MagicMock(),
            target="devel",
            openstack_target="dalmatian",
            ubuntu_series="devel",
            resolved_ubuntu="stonking",
            cloud_archive="",
            build_type=BuildType.RELEASE,
            build_type_str="release",
            binary=True,
            builder="sbuild",
            force=False,
            offline=offline,
            skip_repo_regen=True,
            no_spinner=True,
            build_deps=False,
            archive_deps=True,
            min_version_policy="",
            dep_report=False,
            fail_on_cloud_archive_required=False,
            fail_on_mir_required=False,
            update_control_min_versions=False,
            normalize_to_prev_lts_floor=False,
            dry_run_control_edit=False,
            paths={"cache_root": tmp_path / "cache"},
            ai_enabled=False,
            cfg={
                "mirrors": {"ubuntu_archive": "http://mirror.example.com/ubuntu"},
                "defaults": {"ubuntu_components": ["main", "universe"]},
            },
            workspace=workspace,
            pkg_repo=None,
            schroot_name="packastack-stonking-amd64",
        )

    def _run_build_packages(
        self,
        tmp_path: Path,
        offline: bool = False,
        python_versions: list[str] | None = None,
        sbuild_cfg: dict | None = None,
    ):
        from packastack.build.sbuild import SbuildResult
        from packastack.build.single_build import build_packages

        ctx = self._make_ctx(tmp_path, offline=offline)
        if sbuild_cfg is not None:
            ctx.cfg["sbuild"] = sbuild_cfg

        dsc = tmp_path / "oslo.config_1.0-0ubuntu1.dsc"
        dsc.touch()
        source_result = MagicMock()
        source_result.success = True
        source_result.artifacts = [dsc]
        source_result.dsc_file = dsc
        source_result.changes_file = None

        captured: dict = {}

        def fake_run_sbuild(config, timeout=3600):
            captured["config"] = config
            return SbuildResult(
                success=True,
                artifacts=[],
                collected_artifacts=[],
                exit_code=0,
                python_versions_tested=python_versions or [],
            )

        activities: list[str] = []

        with (
            patch("packastack.debpkg.gbp.build_source", return_value=source_result),
            patch("packastack.build.sbuild.is_sbuild_available", return_value=True),
            patch("packastack.build.sbuild.run_sbuild", side_effect=fake_run_sbuild),
            patch(
                "packastack.build.single_build.activity",
                side_effect=lambda phase, msg: activities.append(msg),
            ),
        ):
            phase_result, build_result = build_packages(ctx, "1.0-0ubuntu1")

        return phase_result, build_result, captured, activities

    def test_proposed_enabled_online(self, tmp_path: Path) -> None:
        """Online builds enable -proposed by default."""
        phase_result, _, captured, _ = self._run_build_packages(
            tmp_path, offline=False, python_versions=["3.14"]
        )

        assert phase_result.success
        config = captured["config"]
        assert config.proposed is True
        assert config.mirror == "http://mirror.example.com/ubuntu"
        assert config.components == ["main", "universe"]

    def test_proposed_can_be_disabled_via_config(self, tmp_path: Path) -> None:
        """An explicit false value disables -proposed for an online build."""
        phase_result, _, captured, _ = self._run_build_packages(
            tmp_path,
            offline=False,
            python_versions=["3.14"],
            sbuild_cfg={"proposed": False},
        )

        assert phase_result.success
        assert captured["config"].proposed is False

    def test_proposed_disabled_offline(self, tmp_path: Path) -> None:
        """Offline builds skip -proposed even though it defaults on."""
        phase_result, _, captured, _ = self._run_build_packages(
            tmp_path,
            offline=True,
            python_versions=["3.14"],
        )

        assert phase_result.success
        assert captured["config"].proposed is False

    def test_reads_explicit_python_matrix_from_sbuild_config(self, tmp_path: Path) -> None:
        """The sbuild config section is passed to the binary build."""
        from packastack.build.sbuild import SbuildResult
        from packastack.build.single_build import build_packages

        ctx = self._make_ctx(tmp_path)
        ctx.cfg["sbuild"] = {"python_versions": ["3.14", "3.15"]}

        dsc = tmp_path / "oslo.config_1.0-0ubuntu1.dsc"
        dsc.touch()
        source_result = MagicMock(success=True, artifacts=[dsc], dsc_file=dsc, changes_file=None)
        captured: dict = {}

        def fake_run_sbuild(config, timeout=3600):
            captured["config"] = config
            return SbuildResult(success=True, exit_code=0)

        with (
            patch("packastack.debpkg.gbp.build_source", return_value=source_result),
            patch("packastack.build.sbuild.is_sbuild_available", return_value=True),
            patch("packastack.build.sbuild.run_sbuild", side_effect=fake_run_sbuild),
        ):
            phase_result, _ = build_packages(ctx, "1.0-0ubuntu1")

        assert phase_result.success
        assert captured["config"].python_versions == ["3.14", "3.15"]

    def test_reports_python_versions(self, tmp_path: Path) -> None:
        """Both-version builds report the tested versions."""
        _, _, _, activities = self._run_build_packages(tmp_path, python_versions=["3.14", "3.15"])

        assert any("sbuild tested Python versions: 3.14, 3.15" in msg for msg in activities)
        assert not any("only Python" in msg for msg in activities)

    def test_single_version_informational_note(self, tmp_path: Path) -> None:
        """Single-version builds emit the informational note."""
        _, _, _, activities = self._run_build_packages(tmp_path, python_versions=["3.14"])

        assert any("only Python 3.14 was tested" in msg for msg in activities)

    def test_no_versions_no_report(self, tmp_path: Path) -> None:
        """No pybuild lines (non-Python package) means no version report."""
        _, _, _, activities = self._run_build_packages(tmp_path, python_versions=[])

        assert not any("sbuild tested" in msg for msg in activities)


class TestSingleBuildOutcomePythonVersions:
    """Tests for python_versions_tested on SingleBuildOutcome."""

    def test_default_empty(self) -> None:
        from packastack.build.single_build import SingleBuildOutcome

        outcome = SingleBuildOutcome(success=True)
        assert outcome.python_versions_tested == []
