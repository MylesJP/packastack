# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Debian packaging utilities."""

from packastack.package.control import ControlFileParser
from packastack.package.uscan import (
    Uscan,
    UscanResult,
    WatchEntry,
    WatchMatch,
)
from packastack.package.version import VersionConverter

__all__ = [
    "ControlFileParser",
    "VersionConverter",
    "Uscan",
    "UscanResult",
    "WatchEntry",
    "WatchMatch",
]
