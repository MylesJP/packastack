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

"""Fetch managed package lists from ubuntu-cloud-archive pkg-scripts repository.

The Ubuntu Cloud Archive team maintains authoritative lists of packages they manage:
- current-projects: Core OpenStack services (nova, neutron, etc.)
- dependencies: Python libraries and clients (oslo.*, python-*client, etc.)

These lists are fetched during `packastack init` and `packastack refresh` and
stored locally for use by build commands.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from packastack.core.run import RunContext

# Base URL for raw file access from Launchpad git
PKG_SCRIPTS_BASE_URL = (
    "https://git.launchpad.net/~ubuntu-cloud-archive/+git/pkg-scripts/plain"
)

# Files containing package lists
PACKAGE_LIST_FILES = ["current-projects", "dependencies"]

# Default filename for the cached amalgamated list
MANAGED_PACKAGES_FILENAME = "managed-packages.txt"


def fetch_package_list(url: str, timeout: int = 30) -> list[str]:
    """Fetch a package list from a URL.

    Args:
        url: URL to fetch.
        timeout: Request timeout in seconds.

    Returns:
        List of package names (one per line, comments/blanks stripped).

    Raises:
        urllib.error.URLError: On network errors.
    """
    with urllib.request.urlopen(url, timeout=timeout) as response:
        content = response.read().decode("utf-8")

    packages = []
    for line in content.splitlines():
        line = line.strip()
        # Skip empty lines and comments
        if line and not line.startswith("#"):
            packages.append(line)

    return packages


def fetch_managed_packages(
    base_url: str = PKG_SCRIPTS_BASE_URL,
    timeout: int = 30,
) -> tuple[list[str], list[str]]:
    """Fetch all managed package lists and combine them.

    Fetches current-projects and dependencies files from the
    ubuntu-cloud-archive pkg-scripts repository.

    Args:
        base_url: Base URL for package list files.
        timeout: Request timeout in seconds.

    Returns:
        Tuple of (packages, errors).
        packages: Combined sorted list of unique package names.
        errors: List of error messages for failed fetches.
    """
    all_packages: set[str] = set()
    errors: list[str] = []

    for filename in PACKAGE_LIST_FILES:
        url = f"{base_url}/{filename}"
        try:
            packages = fetch_package_list(url, timeout=timeout)
            all_packages.update(packages)
        except Exception as e:
            errors.append(f"Failed to fetch {filename}: {e}")

    return sorted(all_packages), errors


def save_managed_packages(
    packages: list[str],
    cache_dir: Path,
    filename: str = MANAGED_PACKAGES_FILENAME,
) -> Path:
    """Save managed packages list to cache directory.

    Args:
        packages: List of package names.
        cache_dir: Directory to save the file in.
        filename: Name of the file to create.

    Returns:
        Path to the saved file.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    file_path = cache_dir / filename
    content = "\n".join(packages) + "\n" if packages else ""
    file_path.write_text(content)
    return file_path


def load_managed_packages(
    cache_dir: Path,
    filename: str = MANAGED_PACKAGES_FILENAME,
) -> list[str]:
    """Load managed packages list from cache.

    Args:
        cache_dir: Directory containing the cached file.
        filename: Name of the file to load.

    Returns:
        List of package names, or empty list if file doesn't exist.
    """
    file_path = cache_dir / filename
    if not file_path.exists():
        return []

    packages = []
    for line in file_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            packages.append(line)

    return packages


def refresh_managed_packages(
    cache_dir: Path,
    run: RunContext | None = None,
    offline: bool = False,
) -> tuple[list[str], list[str]]:
    """Fetch and save managed packages list.

    This is the main entry point for updating the managed packages cache.
    Called by `packastack init` and `packastack refresh`.

    Args:
        cache_dir: Cache directory (usually ~/.cache/packastack).
        run: Optional RunContext for logging.
        offline: If True, skip network fetch and use cached data.

    Returns:
        Tuple of (packages, errors).
    """
    from packastack.core.run import activity

    if offline:
        activity("pkg-scripts", "Skipping managed packages update (offline mode)")
        packages = load_managed_packages(cache_dir)
        if run:
            run.log_event({
                "event": "pkg_scripts.skipped",
                "reason": "offline",
                "cached_count": len(packages),
            })
        return packages, []

    activity("pkg-scripts", "Fetching managed packages from ubuntu-cloud-archive...")

    packages, errors = fetch_managed_packages()

    if errors:
        for err in errors:
            activity("pkg-scripts", f"Warning: {err}")
        if run:
            run.log_event({
                "event": "pkg_scripts.fetch_errors",
                "errors": errors,
            })

    if packages:
        file_path = save_managed_packages(packages, cache_dir)
        activity("pkg-scripts", f"Saved {len(packages)} managed packages to {file_path}")
        if run:
            run.log_event({
                "event": "pkg_scripts.saved",
                "count": len(packages),
                "path": str(file_path),
            })
    elif not errors:
        activity("pkg-scripts", "Warning: No packages fetched (empty response)")

    return packages, errors


def get_managed_packages_path(cache_dir: Path) -> Path:
    """Get the path to the managed packages file.

    Args:
        cache_dir: Cache directory (usually ~/.cache/packastack).

    Returns:
        Path to the managed packages file.
    """
    return cache_dir / MANAGED_PACKAGES_FILENAME
