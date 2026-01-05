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

"""Tests for packastack.build.mode module."""

from __future__ import annotations

import pytest

from packastack.build.mode import Builder, BuildMode


class TestBuilder:
    """Tests for Builder enum."""

    def test_sbuild_value(self) -> None:
        """Test SBUILD has expected value."""
        assert Builder.SBUILD.value == "sbuild"

    def test_dpkg_value(self) -> None:
        """Test DPKG has expected value."""
        assert Builder.DPKG.value == "dpkg"


class TestBuildMode:
    """Tests for BuildMode dataclass."""

    def test_default_values(self) -> None:
        """Test default values for BuildMode."""
        mode = BuildMode()
        assert mode.sources is True
        assert mode.binaries is True
        assert mode.builder == Builder.SBUILD
        assert mode.arch == "amd64"

    def test_custom_values(self) -> None:
        """Test custom values for BuildMode."""
        mode = BuildMode(
            sources=True,
            binaries=False,
            builder=Builder.DPKG,
            arch="arm64",
        )
        assert mode.sources is True
        assert mode.binaries is False
        assert mode.builder == Builder.DPKG
        assert mode.arch == "arm64"

    def test_to_dict(self) -> None:
        """Test to_dict method."""
        mode = BuildMode(sources=True, binaries=True)
        d = mode.to_dict()
        assert d["sources"] is True
        assert d["binaries"] is True
        assert d["builder"] == "sbuild"
        assert d["arch"] == "amd64"

    def test_to_dict_with_dpkg(self) -> None:
        """Test to_dict with dpkg builder."""
        mode = BuildMode(builder=Builder.DPKG)
        d = mode.to_dict()
        assert d["builder"] == "dpkg"

    def test_source_only_classmethod(self) -> None:
        """Test source_only classmethod."""
        mode = BuildMode.source_only()
        assert mode.sources is True
        assert mode.binaries is False
        # Source-only builds don't need sbuild, so builder is DPKG
        assert mode.builder == Builder.DPKG
        assert mode.arch == "amd64"

    def test_full_build_classmethod(self) -> None:
        """Test full_build classmethod with defaults."""
        mode = BuildMode.full_build()
        assert mode.sources is True
        assert mode.binaries is True
        assert mode.builder == Builder.SBUILD
        assert mode.arch == "amd64"

    def test_full_build_with_dpkg(self) -> None:
        """Test full_build classmethod with dpkg builder."""
        mode = BuildMode.full_build(builder=Builder.DPKG)
        assert mode.builder == Builder.DPKG

    def test_full_build_with_custom_arch(self) -> None:
        """Test full_build classmethod with custom architecture."""
        mode = BuildMode.full_build(arch="arm64")
        assert mode.arch == "arm64"

    def test_str_default_full_build(self) -> None:
        """__str__ returns combined description for source+binary builds."""
        assert str(BuildMode()) == "source + binary (sbuild)"

    def test_str_source_only(self) -> None:
        """__str__ only mentions source when binaries are disabled."""
        mode = BuildMode(sources=True, binaries=False)
        assert str(mode) == "source"

    def test_str_binary_only(self) -> None:
        """__str__ only mentions binary when sources are disabled."""
        mode = BuildMode(sources=False, binaries=True)
        assert str(mode) == "binary (sbuild)"

    def test_str_none(self) -> None:
        """__str__ returns 'none' when both flags are false."""
        mode = BuildMode(sources=False, binaries=False)
        assert str(mode) == "none"
