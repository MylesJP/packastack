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

"""Build mode configuration for Packastack.

Defines the BuildMode dataclass that controls whether source/binary packages
are produced and which builder tool is used (sbuild or dpkg-buildpackage).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Builder(Enum):
    """Builder tool selection."""

    SBUILD = "sbuild"
    DPKG = "dpkg"


@dataclass
class BuildMode:
    """Build mode configuration.

    Controls what type of packages are built and which builder is used.

    Attributes:
        sources: Whether to produce source packages (.dsc, .orig.tar.*, etc.)
        binaries: Whether to produce binary packages (.deb)
        builder: Which builder tool to use (sbuild or dpkg-buildpackage)
        arch: Target architecture for binary builds
    """

    sources: bool = True
    binaries: bool = True
    builder: Builder = Builder.SBUILD
    arch: str = "amd64"

    @classmethod
    def source_only(cls, arch: str = "amd64") -> BuildMode:
        """Create a source-only build mode."""
        return cls(sources=True, binaries=False, builder=Builder.DPKG, arch=arch)

    @classmethod
    def full_build(cls, builder: Builder = Builder.SBUILD, arch: str = "amd64") -> BuildMode:
        """Create a full source + binary build mode."""
        return cls(sources=True, binaries=True, builder=builder, arch=arch)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging/summary."""
        return {
            "sources": self.sources,
            "binaries": self.binaries,
            "builder": self.builder.value,
            "arch": self.arch,
        }

    def __str__(self) -> str:
        parts = []
        if self.sources:
            parts.append("source")
        if self.binaries:
            parts.append(f"binary ({self.builder.value})")
        return " + ".join(parts) or "none"
