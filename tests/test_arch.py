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

"""Tests for packastack.arch module."""

from __future__ import annotations

from unittest import mock

import pytest

from packastack import arch


class TestMachineToDebArch:
    """Tests for MACHINE_TO_DEB_ARCH mapping."""

    def test_x86_64_maps_to_amd64(self) -> None:
        assert arch.MACHINE_TO_DEB_ARCH["x86_64"] == "amd64"

    def test_aarch64_maps_to_arm64(self) -> None:
        assert arch.MACHINE_TO_DEB_ARCH["aarch64"] == "arm64"

    def test_armv7l_maps_to_armhf(self) -> None:
        assert arch.MACHINE_TO_DEB_ARCH["armv7l"] == "armhf"

    def test_i686_maps_to_i386(self) -> None:
        assert arch.MACHINE_TO_DEB_ARCH["i686"] == "i386"

    def test_ppc64le_maps_to_ppc64el(self) -> None:
        assert arch.MACHINE_TO_DEB_ARCH["ppc64le"] == "ppc64el"

    def test_s390x_maps_to_s390x(self) -> None:
        assert arch.MACHINE_TO_DEB_ARCH["s390x"] == "s390x"

    def test_riscv64_maps_to_riscv64(self) -> None:
        assert arch.MACHINE_TO_DEB_ARCH["riscv64"] == "riscv64"


class TestGetHostArch:
    """Tests for get_host_arch function."""

    def test_returns_amd64_for_x86_64(self) -> None:
        with mock.patch("platform.machine", return_value="x86_64"):
            assert arch.get_host_arch() == "amd64"

    def test_returns_arm64_for_aarch64(self) -> None:
        with mock.patch("platform.machine", return_value="aarch64"):
            assert arch.get_host_arch() == "arm64"

    def test_raises_for_unknown_architecture(self) -> None:
        with mock.patch("platform.machine", return_value="unknown_arch"):
            with pytest.raises(ValueError) as exc_info:
                arch.get_host_arch()

        assert "unknown_arch" in str(exc_info.value)


class TestResolveArches:
    """Tests for resolve_arches function."""

    def test_replaces_host_with_actual_arch(self) -> None:
        with mock.patch("platform.machine", return_value="x86_64"):
            result = arch.resolve_arches(["host", "all"])

        assert result == ["amd64", "all"]

    def test_preserves_non_host_arches(self) -> None:
        with mock.patch("platform.machine", return_value="x86_64"):
            result = arch.resolve_arches(["arm64", "all"])

        assert result == ["arm64", "all"]

    def test_handles_multiple_host_entries(self) -> None:
        with mock.patch("platform.machine", return_value="aarch64"):
            result = arch.resolve_arches(["host", "host"])

        assert result == ["arm64", "arm64"]

    def test_empty_list(self) -> None:
        result = arch.resolve_arches([])
        assert result == []

    def test_case_insensitive_host(self) -> None:
        with mock.patch("platform.machine", return_value="x86_64"):
            result = arch.resolve_arches(["HOST", "Host", "all"])

        assert result == ["amd64", "amd64", "all"]
