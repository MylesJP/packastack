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

"""Ubuntu series resolution utilities."""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)

# Fallback development series (Ubuntu 25.04 Resolute Rhino)
FALLBACK_DEVEL_SERIES = "resolute"


def resolve_series(name: str) -> str:
    """Resolve a series name, handling 'devel' as a special case.

    If name is 'devel', attempt to determine the current development series
    by calling `distro-info --devel`. If that fails, return the hardcoded
    fallback (resolute).

    Args:
        name: Series name or 'devel'.

    Returns:
        Resolved series codename.
    """
    if name.lower() != "devel":
        return name

    try:
        result = subprocess.run(
            ["distro-info", "--devel"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        series = result.stdout.strip()
        if series:
            return series
    except FileNotFoundError:
        logger.warning("distro-info not found; using fallback series '%s'", FALLBACK_DEVEL_SERIES)
    except subprocess.CalledProcessError as e:
        logger.warning("distro-info failed: %s; using fallback series '%s'", e, FALLBACK_DEVEL_SERIES)
    except subprocess.TimeoutExpired:
        logger.warning("distro-info timed out; using fallback series '%s'", FALLBACK_DEVEL_SERIES)

    return FALLBACK_DEVEL_SERIES


if __name__ == "__main__":
    print(resolve_series("devel"))
