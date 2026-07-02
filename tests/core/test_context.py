# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for packastack.core.context module."""

from __future__ import annotations

import pytest

from packastack.core.context import (
    BuildOptions,
    PolicyConfig,
    TargetConfig,
)
from packastack.planning.type_selection import BuildType


class TestTargetConfig:
    """Tests for TargetConfig dataclass."""

    def test_valid_config(self) -> None:
        """Test creating a valid TargetConfig."""
        config = TargetConfig(
            ubuntu_series="noble",
            openstack_target="dalmatian",
            cloud_archive="dalmatian-proposed",
            resolved_ubuntu="noble",
        )
        assert config.ubuntu_series == "noble"
        assert config.resolved_ubuntu == "noble"

    def test_devel_not_allowed(self) -> None:
        """Test that 'devel' is not allowed for resolved_ubuntu."""
        with pytest.raises(ValueError, match="resolved_ubuntu must be a concrete codename"):
            TargetConfig(
                ubuntu_series="devel",
                openstack_target="dalmatian",
                cloud_archive="dalmatian-proposed",
                resolved_ubuntu="devel",
            )


class TestPolicyConfig:
    """Tests for PolicyConfig dataclass."""

    def test_default_values(self) -> None:
        """Test default policy values."""
        policy = PolicyConfig()
        assert policy.force is False
        assert policy.offline is False
        assert policy.include_retired is False
        assert policy.yes is False

    def test_custom_values(self) -> None:
        """Test custom policy values."""
        policy = PolicyConfig(force=True, offline=True)
        assert policy.force is True
        assert policy.offline is True


class TestBuildOptions:
    """Tests for BuildOptions dataclass."""

    def test_default_values(self) -> None:
        """Test default build options."""
        options = BuildOptions()
        assert options.build_type == BuildType.RELEASE
        assert options.binary is True

    def test_snapshot_build_type(self) -> None:
        """Test snapshot build type is accepted."""
        options = BuildOptions(build_type=BuildType.SNAPSHOT)
        assert options.build_type == BuildType.SNAPSHOT


class TestFromCliArgs:
    """Tests for the from_cli_args factory methods."""

    def test_build_context_from_cli_args(self) -> None:
        from unittest.mock import MagicMock

        from packastack.core.context import BuildContext

        run = MagicMock()
        ctx = BuildContext.from_cli_args(
            ubuntu_series="devel",
            openstack_target="gazpacho",
            cloud_archive="",
            resolved_ubuntu="resolute",
            force=True,
            offline=True,
            build_type=BuildType.SNAPSHOT,
            binary=False,
            run=run,
            paths={},
            package="nova",
            pkg_name="nova",
        )

        assert ctx.target.resolved_ubuntu == "resolute"
        assert ctx.policy.force is True
        assert ctx.policy.offline is True
        assert ctx.options.build_type is BuildType.SNAPSHOT
        assert ctx.options.binary is False
        assert ctx.package == "nova"

    def test_build_all_context_from_cli_args(self) -> None:
        from unittest.mock import MagicMock

        from packastack.core.context import BuildAllContext

        run = MagicMock()
        ctx = BuildAllContext.from_cli_args(
            ubuntu_series="devel",
            openstack_target="gazpacho",
            cloud_archive="",
            resolved_ubuntu="resolute",
            resume=True,
            resume_run_id="20260101-000000",
            retry_failed=True,
            skip_failed=False,
            parallel=4,
            max_failures=2,
            keep_going=False,
            packages_file="pkgs.txt",
            dry_run=True,
            run=run,
            paths={},
        )

        assert ctx.resume.resume is True
        assert ctx.resume.resume_run_id == "20260101-000000"
        assert ctx.parallel == 4
        assert ctx.max_failures == 2
        assert ctx.keep_going is False
        assert ctx.packages_file == "pkgs.txt"
        assert ctx.dry_run is True

    def test_build_options_rejects_invalid_type(self) -> None:
        class FakeType:
            value = "bogus"

        with pytest.raises(ValueError, match="build_type must be"):
            BuildOptions(build_type=FakeType())
