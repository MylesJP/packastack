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
