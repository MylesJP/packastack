# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Ubuntu distribution information utilities.

This module provides utilities for parsing Ubuntu release information from
/usr/share/distro-info/ubuntu.csv and querying LTS release details.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Iterator

# Default path to Ubuntu distro info CSV
UBUNTU_DISTRO_INFO_PATH = Path("/usr/share/distro-info/ubuntu.csv")


@dataclass(frozen=True)
class UbuntuRelease:
    """Information about an Ubuntu release.

    Attributes:
        version: The version number (e.g., "24.04").
        codename: The release codename (e.g., "noble").
        series: The series name, same as codename.
        is_lts: Whether this is an LTS release.
        release_date: The release date, if known.
        eol_date: The end-of-life date, if known.
        eol_server_date: The server EOL date (for LTS), if known.
    """

    version: str
    codename: str
    series: str
    is_lts: bool
    release_date: date | None = None
    eol_date: date | None = None
    eol_server_date: date | None = None

    @property
    def is_released(self) -> bool:
        """Check if the release has been released."""
        if self.release_date is None:
            return False
        return date.today() >= self.release_date

    @property
    def is_supported(self) -> bool:
        """Check if the release is still supported (not EOL)."""
        if not self.is_released:
            return False
        # Use server EOL for LTS, regular EOL otherwise
        eol = self.eol_server_date if self.is_lts else self.eol_date
        if eol is None:
            return True  # Assume supported if no EOL date
        return date.today() < eol


def _parse_date(date_str: str) -> date | None:
    """Parse a date string from the CSV file.

    Args:
        date_str: Date string in YYYY-MM-DD format, or empty.

    Returns:
        Parsed date or None if empty/invalid.
    """
    if not date_str or date_str.strip() == "":
        return None
    try:
        parts = date_str.split("-")
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        pass
    return None


def _parse_ubuntu_csv(csv_path: Path) -> Iterator[UbuntuRelease]:
    """Parse the Ubuntu distro-info CSV file.

    Args:
        csv_path: Path to ubuntu.csv file.

    Yields:
        UbuntuRelease objects for each release in the file.
    """
    if not csv_path.exists():
        return

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            version = row.get("version", "")
            codename = row.get("codename", "")
            series = row.get("series", codename)

            # LTS releases have "LTS" in the version string
            is_lts = "LTS" in version

            # Clean version (remove " LTS" suffix if present)
            clean_version = version.replace(" LTS", "").strip()

            yield UbuntuRelease(
                version=clean_version,
                codename=codename,
                series=series,
                is_lts=is_lts,
                release_date=_parse_date(row.get("release", "")),
                eol_date=_parse_date(row.get("eol", "")),
                eol_server_date=_parse_date(row.get("eol-server", "")),
            )


@lru_cache(maxsize=1)
def load_ubuntu_releases(csv_path: Path | None = None) -> list[UbuntuRelease]:
    """Load all Ubuntu releases from the distro-info CSV.

    Args:
        csv_path: Optional path to ubuntu.csv. Defaults to system path.

    Returns:
        List of UbuntuRelease objects, ordered by release date (oldest first).
    """
    path = csv_path or UBUNTU_DISTRO_INFO_PATH
    releases = list(_parse_ubuntu_csv(path))
    # Sort by release date (None dates at the end)
    releases.sort(key=lambda r: r.release_date or date.max)
    return releases


def get_all_lts_releases(csv_path: Path | None = None) -> list[UbuntuRelease]:
    """Get all LTS releases.

    Args:
        csv_path: Optional path to ubuntu.csv.

    Returns:
        List of LTS releases, ordered by release date (oldest first).
    """
    return [r for r in load_ubuntu_releases(csv_path) if r.is_lts]


def get_released_lts_releases(csv_path: Path | None = None) -> list[UbuntuRelease]:
    """Get all released LTS releases.

    Args:
        csv_path: Optional path to ubuntu.csv.

    Returns:
        List of released LTS releases, ordered by release date (oldest first).
    """
    return [r for r in get_all_lts_releases(csv_path) if r.is_released]


def get_supported_lts_releases(csv_path: Path | None = None) -> list[UbuntuRelease]:
    """Get all currently supported LTS releases.

    Args:
        csv_path: Optional path to ubuntu.csv.

    Returns:
        List of supported LTS releases, ordered by release date (oldest first).
    """
    return [r for r in get_all_lts_releases(csv_path) if r.is_supported]


def get_current_lts(csv_path: Path | None = None) -> UbuntuRelease | None:
    """Get the current (most recent released) LTS release.

    Args:
        csv_path: Optional path to ubuntu.csv.

    Returns:
        The most recent released LTS release, or None if not found.
    """
    released = get_released_lts_releases(csv_path)
    return released[-1] if released else None


def get_previous_lts(csv_path: Path | None = None) -> UbuntuRelease | None:
    """Get the previous LTS release (one before current).

    This is typically the base for Ubuntu Cloud Archive builds.

    Args:
        csv_path: Optional path to ubuntu.csv.

    Returns:
        The second most recent released LTS release, or None if not found.
    """
    released = get_released_lts_releases(csv_path)
    if len(released) >= 2:
        return released[-2]
    return None


def get_lts_by_codename(codename: str, csv_path: Path | None = None) -> UbuntuRelease | None:
    """Get an LTS release by its codename.

    Args:
        codename: The Ubuntu codename (e.g., "jammy", "noble").
        csv_path: Optional path to ubuntu.csv.

    Returns:
        The matching LTS release, or None if not found or not LTS.
    """
    for release in get_all_lts_releases(csv_path):
        if release.codename == codename:
            return release
    return None


def get_release_by_codename(codename: str, csv_path: Path | None = None) -> UbuntuRelease | None:
    """Get any release by its codename.

    Args:
        codename: The Ubuntu codename (e.g., "jammy", "noble", "oracular").
        csv_path: Optional path to ubuntu.csv.

    Returns:
        The matching release, or None if not found.
    """
    for release in load_ubuntu_releases(csv_path):
        if release.codename == codename:
            return release
    return None


def is_lts_codename(codename: str, csv_path: Path | None = None) -> bool:
    """Check if a codename corresponds to an LTS release.

    Args:
        codename: The Ubuntu codename to check.
        csv_path: Optional path to ubuntu.csv.

    Returns:
        True if the codename is an LTS release.
    """
    release = get_release_by_codename(codename, csv_path)
    return release is not None and release.is_lts


def get_base_lts_for_mode(
    is_cloud_archive: bool,
    csv_path: Path | None = None,
) -> UbuntuRelease | None:
    """Get the appropriate base LTS for Cloud Archive or devel mode.

    For Cloud Archive builds, we use the previous LTS as the base (e.g., jammy
    for building Dalmatian Cloud Archive on 22.04).

    For devel builds, we use the current LTS as the base (e.g., noble for
    building packages targeting the development series).

    Args:
        is_cloud_archive: True if building for Ubuntu Cloud Archive.
        csv_path: Optional path to ubuntu.csv.

    Returns:
        The appropriate LTS release for version checking, or None.
    """
    if is_cloud_archive:
        return get_previous_lts(csv_path)
    return get_current_lts(csv_path)


def get_lts_codename_for_mode(
    is_cloud_archive: bool,
    csv_path: Path | None = None,
) -> str:
    """Get the codename of the appropriate base LTS.

    Args:
        is_cloud_archive: True if building for Ubuntu Cloud Archive.
        csv_path: Optional path to ubuntu.csv.

    Returns:
        The LTS codename (e.g., "jammy", "noble"), or empty string if not found.
    """
    lts = get_base_lts_for_mode(is_cloud_archive, csv_path)
    return lts.codename if lts else ""

