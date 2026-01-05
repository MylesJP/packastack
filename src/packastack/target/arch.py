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

"""Architecture detection and mapping utilities."""

from __future__ import annotations

import platform

# Mapping from platform.machine() values to Debian architecture names.
MACHINE_TO_DEB_ARCH: dict[str, str] = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "armhf",
    "armv6l": "armel",
    "i386": "i386",
    "i686": "i386",
    "ppc64le": "ppc64el",
    "s390x": "s390x",
    "riscv64": "riscv64",
}


def get_host_arch() -> str:
    """Return the Debian architecture name for the current host.

    Raises:
        ValueError: If the host architecture is unknown.
    """
    machine = platform.machine()
    deb_arch = MACHINE_TO_DEB_ARCH.get(machine)
    if deb_arch is None:
        raise ValueError(f"Unknown host architecture: {machine}")
    return deb_arch


def resolve_arches(arches: list[str]) -> list[str]:
    """Resolve architecture list, replacing 'host' with actual host arch.

    Args:
        arches: List of architecture names (may include 'host' and 'all').

    Returns:
        List with 'host' replaced by the detected architecture.
    """
    result: list[str] = []
    for arch in arches:
        if arch.lower() == "host":
            result.append(get_host_arch())
        else:
            result.append(arch)
    return result


if __name__ == "__main__":
    print(f"Host arch: {get_host_arch()}")
