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

"""Tests for packastack.exceptions module."""

from __future__ import annotations

from packastack import exceptions


class TestPackastackError:
    """Tests for PackastackError base class."""

    def test_default_message(self) -> None:
        error = exceptions.PackastackError()
        assert error.message == "An error occurred"

    def test_default_exit_code(self) -> None:
        error = exceptions.PackastackError()
        assert error.exit_code == 1

    def test_custom_message(self) -> None:
        error = exceptions.PackastackError(message="Custom error")
        assert error.message == "Custom error"


class TestConfigError:
    """Tests for ConfigError exception."""

    def test_exit_code_is_1(self) -> None:
        error = exceptions.ConfigError(message="Config error")
        assert error.exit_code == 1


class TestPartialRefreshError:
    """Tests for PartialRefreshError exception."""

    def test_exit_code_is_2(self) -> None:
        error = exceptions.PartialRefreshError(message="Partial failure")
        assert error.exit_code == 2


class TestOfflineMissingError:
    """Tests for OfflineMissingError exception."""

    def test_exit_code_is_3(self) -> None:
        error = exceptions.OfflineMissingError(message="Offline missing")
        assert error.exit_code == 3


class TestCorruptCacheError:
    """Tests for CorruptCacheError exception."""

    def test_exit_code_is_4(self) -> None:
        error = exceptions.CorruptCacheError(message="Corrupt cache")
        assert error.exit_code == 4
