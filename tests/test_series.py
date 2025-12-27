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

"""Tests for packastack.series module."""

from __future__ import annotations

import subprocess
from unittest import mock

from packastack import series


class TestResolveSeries:
    """Tests for resolve_series function."""

    def test_returns_non_devel_series_unchanged(self) -> None:
        assert series.resolve_series("noble") == "noble"
        assert series.resolve_series("jammy") == "jammy"
        assert series.resolve_series("focal") == "focal"

    def test_resolves_devel_using_distro_info(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                stdout="resolute\n",
                returncode=0,
            )

            result = series.resolve_series("devel")

        assert result == "resolute"
        mock_run.assert_called_once_with(
            ["distro-info", "--devel"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )

    def test_devel_case_insensitive(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(stdout="resolute\n", returncode=0)

            assert series.resolve_series("DEVEL") == "resolute"
            assert series.resolve_series("Devel") == "resolute"

    def test_fallback_when_distro_info_not_found(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()

            result = series.resolve_series("devel")

        assert result == series.FALLBACK_DEVEL_SERIES

    def test_fallback_when_distro_info_fails(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "distro-info")

            result = series.resolve_series("devel")

        assert result == series.FALLBACK_DEVEL_SERIES

    def test_fallback_when_distro_info_times_out(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("distro-info", 5)

            result = series.resolve_series("devel")

        assert result == series.FALLBACK_DEVEL_SERIES

    def test_fallback_when_distro_info_returns_empty(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(stdout="", returncode=0)

            result = series.resolve_series("devel")

        assert result == series.FALLBACK_DEVEL_SERIES

    def test_fallback_series_is_resolute(self) -> None:
        assert series.FALLBACK_DEVEL_SERIES == "resolute"
