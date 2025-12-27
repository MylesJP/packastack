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

"""Tests for packastack.duration module."""

from __future__ import annotations

import pytest

from packastack import duration


class TestParseDuration:
    """Tests for parse_duration function."""

    def test_parses_seconds(self) -> None:
        assert duration.parse_duration("30s") == 30
        assert duration.parse_duration("1s") == 1
        assert duration.parse_duration("0s") == 0

    def test_parses_minutes(self) -> None:
        assert duration.parse_duration("30m") == 30 * 60
        assert duration.parse_duration("1m") == 60
        assert duration.parse_duration("90m") == 90 * 60

    def test_parses_hours(self) -> None:
        assert duration.parse_duration("6h") == 6 * 3600
        assert duration.parse_duration("1h") == 3600
        assert duration.parse_duration("24h") == 24 * 3600

    def test_parses_days(self) -> None:
        assert duration.parse_duration("1d") == 86400
        assert duration.parse_duration("7d") == 7 * 86400

    def test_parses_weeks(self) -> None:
        assert duration.parse_duration("1w") == 604800
        assert duration.parse_duration("2w") == 2 * 604800

    def test_case_insensitive(self) -> None:
        assert duration.parse_duration("6H") == 6 * 3600
        assert duration.parse_duration("1D") == 86400
        assert duration.parse_duration("2W") == 2 * 604800

    def test_strips_whitespace(self) -> None:
        assert duration.parse_duration(" 6h ") == 6 * 3600
        assert duration.parse_duration("  1d  ") == 86400

    def test_raises_on_invalid_format(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            duration.parse_duration("invalid")
        assert "Invalid duration format" in str(exc_info.value)

    def test_raises_on_missing_unit(self) -> None:
        with pytest.raises(ValueError):
            duration.parse_duration("30")

    def test_raises_on_missing_number(self) -> None:
        with pytest.raises(ValueError):
            duration.parse_duration("h")

    def test_raises_on_invalid_unit(self) -> None:
        with pytest.raises(ValueError):
            duration.parse_duration("30x")

    def test_raises_on_empty_string(self) -> None:
        with pytest.raises(ValueError):
            duration.parse_duration("")

    def test_raises_on_negative_number(self) -> None:
        with pytest.raises(ValueError):
            duration.parse_duration("-30m")

    def test_raises_on_decimal_number(self) -> None:
        with pytest.raises(ValueError):
            duration.parse_duration("1.5h")
