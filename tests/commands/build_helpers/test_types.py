# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for build command types module."""

from pathlib import Path

import pytest

from packastack.commands.build_helpers.types import (
    BuildInputs,
    BuildOutcome,
    PhaseResult,
    ResolvedTargets,
    TarballAcquisitionResult,
    WorkspacePaths,
)


class TestResolvedTargets:
    """Tests for ResolvedTargets dataclass."""

    def test_basic_construction(self):
        """Test basic construction with required fields."""
        targets = ResolvedTargets(
            openstack_series="caracal",
            ubuntu_series="noble",
        )
        assert targets.openstack_series == "caracal"
        assert targets.ubuntu_series == "noble"
        assert targets.prev_series is None
        assert targets.is_development is False

    def test_full_construction(self):
        """Test construction with all fields."""
        targets = ResolvedTargets(
            openstack_series="devel",
            ubuntu_series="oracular",
            prev_series="caracal",
            is_development=True,
        )
        assert targets.openstack_series == "devel"
        assert targets.ubuntu_series == "oracular"
        assert targets.prev_series == "caracal"
        assert targets.is_development is True


class TestWorkspacePaths:
    """Tests for WorkspacePaths dataclass."""

    def test_basic_construction(self):
        """Test basic construction with required fields."""
        paths = WorkspacePaths(
            workspace=Path("/tmp/build"),
            pkg_repo=Path("/tmp/build/packaging"),
            build_output=Path("/tmp/build/output"),
        )
        assert paths.workspace == Path("/tmp/build")
        assert paths.pkg_repo == Path("/tmp/build/packaging")
        assert paths.build_output == Path("/tmp/build/output")
        assert paths.upstream_work_dir is None
        assert paths.local_repo is None

    def test_full_construction(self):
        """Test construction with all fields."""
        paths = WorkspacePaths(
            workspace=Path("/tmp/build"),
            pkg_repo=Path("/tmp/build/packaging"),
            build_output=Path("/tmp/build/output"),
            upstream_work_dir=Path("/tmp/build/upstream"),
            local_repo=Path("/var/cache/apt-repo"),
        )
        assert paths.upstream_work_dir == Path("/tmp/build/upstream")
        assert paths.local_repo == Path("/var/cache/apt-repo")


class TestPhaseResult:
    """Tests for PhaseResult dataclass."""

    def test_ok_factory(self):
        """Test ok() factory method."""
        result = PhaseResult.ok("Phase completed successfully")
        assert result.success is True
        assert result.exit_code == 0
        assert result.message == "Phase completed successfully"
        assert result.data == {}

    def test_ok_with_data(self):
        """Test ok() factory method with additional data."""
        result = PhaseResult.ok(
            "Fetched tarball",
            tarball_path="/tmp/foo.tar.gz",
            version="1.2.3",
        )
        assert result.success is True
        assert result.data["tarball_path"] == "/tmp/foo.tar.gz"
        assert result.data["version"] == "1.2.3"

    def test_fail_factory(self):
        """Test fail() factory method."""
        result = PhaseResult.fail(3, "Fetch failed: connection refused")
        assert result.success is False
        assert result.exit_code == 3
        assert result.message == "Fetch failed: connection refused"
        assert result.data == {}

    def test_fail_with_data(self):
        """Test fail() factory method with additional data."""
        result = PhaseResult.fail(
            7,
            "Build failed",
            builder="sbuild",
            log_path="/tmp/build.log",
        )
        assert result.success is False
        assert result.exit_code == 7
        assert result.data["builder"] == "sbuild"
        assert result.data["log_path"] == "/tmp/build.log"


class TestTarballAcquisitionResult:
    """Tests for TarballAcquisitionResult dataclass."""

    def test_success_result(self):
        """Test successful tarball acquisition result."""
        result = TarballAcquisitionResult(
            success=True,
            tarball_path=Path("/tmp/oslo.config-10.0.0.tar.gz"),
            method="official",
            version="10.0.0",
            signature_verified=True,
        )
        assert result.success is True
        assert result.tarball_path == Path("/tmp/oslo.config-10.0.0.tar.gz")
        assert result.method == "official"
        assert result.version == "10.0.0"
        assert result.signature_verified is True
        assert result.error == ""

    def test_failure_result(self):
        """Test failed tarball acquisition result."""
        result = TarballAcquisitionResult(
            success=False,
            error="Connection timeout",
        )
        assert result.success is False
        assert result.tarball_path is None
        assert result.error == "Connection timeout"

    def test_snapshot_result(self):
        """Test snapshot tarball acquisition result."""
        result = TarballAcquisitionResult(
            success=True,
            tarball_path=Path("/tmp/oslo.config-10.0.1~git20240105.abc1234.tar.gz"),
            method="git_archive",
            version="10.0.1~git20240105.abc1234",
            git_sha="abc1234567890",
            git_date="20240105",
        )
        assert result.git_sha == "abc1234567890"
        assert result.git_date == "20240105"
        assert result.signature_verified is False
        assert result.signature_warning == ""


class TestBuildOutcome:
    """Tests for BuildOutcome dataclass."""

    def test_successful_outcome(self):
        """Test successful build outcome."""
        outcome = BuildOutcome(
            success=True,
            exit_code=0,
            package="python-oslo.config",
            version="10.0.0-0ubuntu1",
            build_type="release",
            artifacts=[
                Path("/tmp/python-oslo.config_10.0.0-0ubuntu1.dsc"),
                Path("/tmp/python-oslo.config_10.0.0-0ubuntu1_amd64.deb"),
            ],
        )
        assert outcome.success is True
        assert outcome.exit_code == 0
        assert len(outcome.artifacts) == 2
        assert outcome.error is None
        assert outcome.skipped_reason is None

    def test_failed_factory(self):
        """Test failed() factory method."""
        outcome = BuildOutcome.failed(
            7,
            "dpkg-buildpackage failed",
            package="python-oslo.config",
        )
        assert outcome.success is False
        assert outcome.exit_code == 7
        assert outcome.error == "dpkg-buildpackage failed"
        assert outcome.package == "python-oslo.config"
        assert outcome.artifacts == []

    def test_skipped_factory(self):
        """Test skipped() factory method."""
        outcome = BuildOutcome.skipped(
            10,
            "Upstream project is retired",
            package="python-oslo.concurrency",
        )
        assert outcome.success is False
        assert outcome.exit_code == 10
        assert outcome.skipped_reason == "Upstream project is retired"
        assert outcome.error is None
