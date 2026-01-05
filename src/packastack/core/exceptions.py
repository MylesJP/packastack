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

"""Packastack-specific exception types with associated exit codes."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PackastackError(Exception):
    """Base class for Packastack errors with an exit code."""

    message: str = "An error occurred"
    exit_code: int = field(default=1)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.message} (exit {self.exit_code})"


@dataclass
class ConfigError(PackastackError):
    exit_code: int = field(default=1)


@dataclass
class PartialRefreshError(PackastackError):
    exit_code: int = field(default=2)


@dataclass
class OfflineMissingError(PackastackError):
    exit_code: int = field(default=3)


@dataclass
class CorruptCacheError(PackastackError):
    exit_code: int = field(default=4)


@dataclass
class MissingPackageError(PackastackError):
    """Error raised when required packages are not available."""

    exit_code: int = field(default=5)
    missing_packages: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class CycleDetectedError(PackastackError):
    """Error raised when a dependency cycle is detected."""

    exit_code: int = field(default=6)
    cycles: list[list[str]] = field(default_factory=list)
