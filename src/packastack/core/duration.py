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

"""Duration string parsing utilities."""

from __future__ import annotations

import re

# Regex pattern for duration strings: number followed by unit.
DURATION_PATTERN = re.compile(r"^(\d+)\s*([smhdw])$", re.IGNORECASE)

UNIT_SECONDS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}


def parse_duration(value: str) -> int:
    """Parse a duration string into seconds.

    Supported formats:
        30s  -> 30 seconds
        30m  -> 30 minutes
        6h   -> 6 hours
        1d   -> 1 day
        2w   -> 2 weeks

    Args:
        value: Duration string.

    Returns:
        Duration in seconds.

    Raises:
        ValueError: If the format is invalid.
    """
    match = DURATION_PATTERN.match(value.strip())
    if not match:
        raise ValueError(f"Invalid duration format: '{value}'. Expected format like '6h', '30m', '1d'.")

    amount = int(match.group(1))
    unit = match.group(2).lower()
    multiplier = UNIT_SECONDS[unit]
    return amount * multiplier


if __name__ == "__main__":
    for test in ["30s", "30m", "6h", "1d", "2w", "6H", " 10m "]:
        print(f"{test!r} -> {parse_duration(test)} seconds")
