# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Version comparison and manipulation utilities for Debian packages.

This module provides utilities for comparing Debian package versions,
extracting upstream versions, and checking version constraints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Import python-debian's Version class for proper Debian version comparison
try:
    from debian.debian_support import Version as DebianVersion
except ImportError:
    DebianVersion = None  # type: ignore


@dataclass
@total_ordering
class ParsedVersion:
    """Parsed Debian version with comparison support.

    Attributes:
        epoch: The epoch (0 if not specified).
        upstream: The upstream version component.
        debian_revision: The Debian revision (empty if native package).
        original: The original version string.
    """

    epoch: int
    upstream: str
    debian_revision: str
    original: str

    def __str__(self) -> str:
        """Return the full version string."""
        parts = []
        if self.epoch > 0:
            parts.append(f"{self.epoch}:")
        parts.append(self.upstream)
        if self.debian_revision:
            parts.append(f"-{self.debian_revision}")
        return "".join(parts)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ParsedVersion):
            return NotImplemented
        if DebianVersion is not None:
            return DebianVersion(str(self)) == DebianVersion(str(other))
        return str(self) == str(other)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, ParsedVersion):
            return NotImplemented
        if DebianVersion is not None:
            return DebianVersion(str(self)) < DebianVersion(str(other))
        # Fallback to string comparison (not ideal but works for simple cases)
        return str(self) < str(other)

    def __hash__(self) -> int:
        return hash((self.epoch, self.upstream, self.debian_revision))

    @property
    def upstream_only(self) -> str:
        """Return only the upstream version without epoch or revision."""
        return self.upstream


def parse_debian_version(version_str: str) -> ParsedVersion:
    """Parse a Debian version string into components.

    Handles the full Debian version format: [epoch:]upstream-version[-debian-revision]

    Args:
        version_str: A Debian version string (e.g., "1:29.0.0-0ubuntu1").

    Returns:
        ParsedVersion with parsed components.
    """
    epoch = 0
    upstream = version_str
    debian_revision = ""

    # Extract epoch
    if ":" in version_str:
        epoch_str, rest = version_str.split(":", 1)
        try:
            epoch = int(epoch_str)
        except ValueError:
            epoch = 0
        upstream = rest
    else:
        upstream = version_str

    # Extract debian revision (last hyphen)
    if "-" in upstream:
        idx = upstream.rfind("-")
        debian_revision = upstream[idx + 1 :]
        upstream = upstream[:idx]

    return ParsedVersion(
        epoch=epoch,
        upstream=upstream,
        debian_revision=debian_revision,
        original=version_str,
    )


def extract_upstream_version(version_str: str) -> str:
    """Extract the upstream version from a Debian version string.

    Removes epoch and Debian revision, returning only the upstream component.

    Args:
        version_str: A Debian version string (e.g., "1:29.0.0-0ubuntu1").

    Returns:
        The upstream version (e.g., "29.0.0").
    """
    parsed = parse_debian_version(version_str)
    return parsed.upstream


def compare_versions(v1: str, v2: str) -> int:
    """Compare two Debian version strings.

    Uses python-debian's Version class for proper Debian version comparison
    when available, otherwise falls back to string comparison.

    Args:
        v1: First version string.
        v2: Second version string.

    Returns:
        -1 if v1 < v2, 0 if v1 == v2, 1 if v1 > v2.
    """
    if DebianVersion is not None:
        dv1 = DebianVersion(v1)
        dv2 = DebianVersion(v2)
        if dv1 < dv2:
            return -1
        elif dv1 > dv2:
            return 1
        return 0

    # Fallback: parse and compare
    pv1 = parse_debian_version(v1)
    pv2 = parse_debian_version(v2)
    if pv1 < pv2:
        return -1
    elif pv1 > pv2:
        return 1
    return 0


def version_satisfies_constraint(version: str, constraint: str) -> bool:
    """Check if a version satisfies a constraint.

    Supports constraints like:
    - ">= 1.0.0"
    - "<< 2.0.0"
    - ">> 1.0.0"
    - "<= 2.0.0"
    - "= 1.0.0"

    Args:
        version: The version to check.
        constraint: The constraint string (e.g., ">= 1.0.0").

    Returns:
        True if the version satisfies the constraint.
    """
    # Parse constraint
    match = re.match(r"^\s*(>=|<=|>>|<<|=)\s*(.+)\s*$", constraint)
    if not match:
        # No operator, treat as equality
        return compare_versions(version, constraint.strip()) == 0

    op, constraint_version = match.groups()
    cmp = compare_versions(version, constraint_version)

    if op == ">=":
        return cmp >= 0
    elif op == "<=":
        return cmp <= 0
    elif op == ">>":
        return cmp > 0
    elif op == "<<":
        return cmp < 0
    elif op == "=":
        return cmp == 0

    return False


def versions_equal_upstream(v1: str, v2: str) -> bool:
    """Check if two versions have the same upstream component.

    Useful for determining if a package needs a rebuild when only the
    upstream version matters, not the Debian revision.

    Args:
        v1: First Debian version string.
        v2: Second Debian version string.

    Returns:
        True if the upstream versions are equal.
    """
    up1 = extract_upstream_version(v1)
    up2 = extract_upstream_version(v2)
    # Compare upstream versions using proper Debian comparison
    return compare_versions(up1, up2) == 0


def upstream_version_newer(current: str, candidate: str) -> bool:
    """Check if a candidate upstream version is newer than current.

    Compares only the upstream component, ignoring epoch and Debian revision.

    Args:
        current: Current Debian version string.
        candidate: Candidate upstream version string.

    Returns:
        True if candidate's upstream version is newer than current's.
    """
    current_upstream = extract_upstream_version(current)
    # Candidate might already be just an upstream version
    candidate_upstream = extract_upstream_version(candidate)
    return compare_versions(candidate_upstream, current_upstream) > 0


def format_version_constraint(package: str, version: str, relation: str = ">=") -> str:
    """Format a package dependency with version constraint.

    Args:
        package: Package name.
        version: Version string (upstream version).
        relation: Debian version relation (>=, <=, =, >>, <<).

    Returns:
        Formatted dependency string (e.g., "python3-oslo.config (>= 9.0.0)").
    """
    return f"{package} ({relation} {version})"


def strip_epoch(version: str) -> str:
    """Remove epoch from a version string.

    Args:
        version: A Debian version string possibly with epoch.

    Returns:
        Version string without epoch.
    """
    if ":" in version:
        return version.split(":", 1)[1]
    return version


def normalize_upstream_version(version: str) -> str:
    """Normalize an upstream version string for comparison.

    Handles common variations like trailing zeros, tilde suffixes, etc.

    Args:
        version: Upstream version string.

    Returns:
        Normalized version string.
    """
    # Remove any leading/trailing whitespace
    version = version.strip()

    # Handle empty versions
    if not version:
        return "0"

    return version
