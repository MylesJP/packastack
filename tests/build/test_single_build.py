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

from packastack.build.single_build import (
    BuildResult,
    FetchResult,
    PhaseResult,
    PrepareResult,
    SingleBuildContext,
    ValidateDepsResult,
)


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
            paths={"cache_root": Path("/tmp/cache")},
        )

        assert ctx.pkg_name == "python-oslo-config"
        assert ctx.package == "oslo.config"
        assert ctx.build_type == BuildType.SNAPSHOT
        assert ctx.binary is True


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
            paths={
                "cache_root": Path("/tmp/cache"),
                "build_root": Path("/tmp/build"),
            },
        )

        phase_result, _fetch_result = fetch_packaging_repo(ctx)

        assert phase_result.success is False
        assert phase_result.exit_code == 3  # EXIT_FETCH_FAILED
        assert "Network error" in phase_result.error
        run.write_summary.assert_called_once()
