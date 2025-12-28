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

"""Ubuntu archive fetching and validation utilities."""

from __future__ import annotations

import datetime
import gzip
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    """Result of a Packages.gz fetch operation."""

    url: str
    path: Path
    etag: str | None = None
    last_modified: str | None = None
    fetched_utc: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    sha256: str = ""
    size: int = 0
    was_cached: bool = False
    error: str | None = None


class ArchiveFetcher:
    """Fetcher for Ubuntu archive Packages.gz indexes with HTTP conditional requests."""

    def __init__(self, session: requests.Session | None = None, timeout: int = 30) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout

    def build_url(self, mirror: str, series: str, pocket: str, component: str, arch: str) -> str:
        """Build the URL for a Packages.gz file.

        Ubuntu archive layout:
          - release pocket: dists/{series}/{component}/binary-{arch}/Packages.gz
          - other pockets: dists/{series}-{pocket}/{component}/binary-{arch}/Packages.gz
        """
        dist = series if pocket == "release" else f"{series}-{pocket}"
        return f"{mirror.rstrip('/')}/dists/{dist}/{component}/binary-{arch}/Packages.gz"

    def fetch_index(
        self,
        url: str,
        dest: Path,
        etag: str | None = None,
        last_modified: str | None = None,
        offline: bool = False,
    ) -> FetchResult:
        """Fetch a Packages.gz file, using conditional requests if possible.

        Args:
            url: Full URL to the Packages.gz file.
            dest: Local path to write the file.
            etag: Cached ETag for If-None-Match header.
            last_modified: Cached Last-Modified for If-Modified-Since header.
            offline: If True, do not make network requests.

        Returns:
            FetchResult with metadata about the fetch.
        """
        result = FetchResult(url=url, path=dest)

        if offline:
            # In offline mode, we just verify the file exists.
            if dest.exists():
                result.was_cached = True
                result.sha256 = compute_sha256(dest)
                result.size = dest.stat().st_size
                result.etag = etag
                result.last_modified = last_modified
                return result
            else:
                result.error = "File not found in offline mode"
                return result

        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        try:
            resp = self.session.get(url, headers=headers, timeout=self.timeout, stream=True)
        except requests.RequestException as e:  # pragma: no cover
            result.error = str(e)
            return result

        if resp.status_code == 304:
            # Not modified; use cached copy.
            result.was_cached = True
            result.etag = etag
            result.last_modified = last_modified
            if dest.exists():
                result.sha256 = compute_sha256(dest)
                result.size = dest.stat().st_size
            return result

        if resp.status_code != 200:
            result.error = f"HTTP {resp.status_code}"
            return result

        # Write to disk.
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with dest.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
        except OSError as e:  # pragma: no cover
            result.error = f"Write error: {e}"
            return result

        result.etag = resp.headers.get("ETag")
        result.last_modified = resp.headers.get("Last-Modified")
        result.sha256 = compute_sha256(dest)
        result.size = dest.stat().st_size
        result.fetched_utc = datetime.datetime.utcnow().isoformat()
        return result


def compute_sha256(path: Path) -> str:
    """Compute the SHA-256 hash of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_gzip(path: Path) -> bool:
    """Validate that a file is a valid gzip archive.

    Returns True if valid, False otherwise.
    """
    try:
        with gzip.open(path, "rb") as f:
            # Read through the file to verify integrity.
            while f.read(65536):
                pass
        return True
    except (gzip.BadGzipFile, OSError, EOFError):
        return False


def write_metadata(dest: Path, result: FetchResult) -> None:
    """Write a Packages.meta.json file alongside the Packages.gz."""
    meta_path = dest.with_suffix(".meta.json")
    meta = {
        "url": result.url,
        "etag": result.etag,
        "last_modified": result.last_modified,
        "fetched_utc": result.fetched_utc,
        "sha256": result.sha256,
        "size": result.size,
    }
    meta_path.write_text(json.dumps(meta, indent=2))


def load_metadata(dest: Path) -> dict[str, Any] | None:
    """Load a Packages.meta.json file if it exists."""
    meta_path = dest.with_suffix(".meta.json")
    if not meta_path.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(meta_path.read_text())
        return data
    except Exception:
        return None


# Cloud Archive support
# The Ubuntu Cloud Archive provides newer OpenStack packages for older Ubuntu LTS releases.
# URL pattern: https://ubuntu-cloud.archive.canonical.com/ubuntu/dists/{series}-updates/{pocket}/main/binary-{arch}/Packages.gz
# Where pocket is like "caracal", "dalmatian", or with suffixes like "caracal-proposed", "caracal-updates"

CLOUD_ARCHIVE_BASE_URL = "https://ubuntu-cloud.archive.canonical.com/ubuntu"


def build_cloud_archive_url(
    ubuntu_series: str,
    pocket: str,
    component: str = "main",
    arch: str = "amd64",
) -> str:
    """Build the URL for a Cloud Archive Packages.gz file.

    Cloud Archive layout:
        dists/{ubuntu_series}-updates/{pocket}/main/binary-{arch}/Packages.gz

    Args:
        ubuntu_series: Ubuntu series codename (e.g., "jammy", "noble").
        pocket: OpenStack pocket (e.g., "caracal", "caracal-proposed", "caracal-updates").
        component: Repository component (usually "main").
        arch: Architecture (e.g., "amd64").

    Returns:
        Full URL to the Packages.gz file.

    Examples:
        >>> build_cloud_archive_url("jammy", "caracal")
        'https://ubuntu-cloud.archive.canonical.com/ubuntu/dists/jammy-updates/caracal/main/binary-amd64/Packages.gz'
    """
    dist = f"{ubuntu_series}-updates"
    return f"{CLOUD_ARCHIVE_BASE_URL}/dists/{dist}/{pocket}/{component}/binary-{arch}/Packages.gz"


def parse_cloud_archive_pocket(pocket: str) -> tuple[str, str]:
    """Parse a cloud archive pocket into series and suffix.

    Args:
        pocket: Pocket string like "caracal", "caracal-proposed", "caracal-updates".

    Returns:
        Tuple of (openstack_series, suffix) where suffix is "", "proposed", or "updates".

    Examples:
        >>> parse_cloud_archive_pocket("caracal")
        ('caracal', '')
        >>> parse_cloud_archive_pocket("caracal-proposed")
        ('caracal', 'proposed')
    """
    if "-" in pocket:
        parts = pocket.rsplit("-", 1)
        if parts[1] in ("proposed", "updates"):
            return parts[0], parts[1]
    return pocket, ""


class CloudArchiveFetcher(ArchiveFetcher):
    """Fetcher for Ubuntu Cloud Archive Packages.gz indexes."""

    def __init__(
        self,
        base_url: str = CLOUD_ARCHIVE_BASE_URL,
        session: requests.Session | None = None,
        timeout: int = 30,
    ) -> None:
        super().__init__(session=session, timeout=timeout)
        self.base_url = base_url.rstrip("/")

    def build_url(
        self,
        mirror: str,
        series: str,
        pocket: str,
        component: str,
        arch: str,
    ) -> str:
        """Build URL for Cloud Archive Packages.gz.

        For cloud archive, 'series' is the Ubuntu series (e.g., "jammy")
        and 'pocket' is the OpenStack pocket (e.g., "caracal").
        """
        dist = f"{series}-updates"
        return f"{mirror.rstrip('/')}/dists/{dist}/{pocket}/{component}/binary-{arch}/Packages.gz"

    def fetch_cloud_archive(
        self,
        ubuntu_series: str,
        pocket: str,
        dest: Path,
        component: str = "main",
        arch: str = "amd64",
        etag: str | None = None,
        last_modified: str | None = None,
        offline: bool = False,
    ) -> FetchResult:
        """Fetch a Cloud Archive Packages.gz file.

        Args:
            ubuntu_series: Ubuntu series (e.g., "jammy").
            pocket: OpenStack pocket (e.g., "caracal", "caracal-proposed").
            dest: Local path to write the file.
            component: Repository component.
            arch: Architecture.
            etag: Cached ETag for conditional request.
            last_modified: Cached Last-Modified for conditional request.
            offline: If True, do not make network requests.

        Returns:
            FetchResult with metadata.
        """
        url = self.build_url(self.base_url, ubuntu_series, pocket, component, arch)
        return self.fetch_index(url, dest, etag, last_modified, offline)


if __name__ == "__main__":
    fetcher = ArchiveFetcher()
    url = fetcher.build_url("http://archive.ubuntu.com/ubuntu", "noble", "release", "main", "amd64")
    print(f"Ubuntu Archive URL: {url}")

    ca_fetcher = CloudArchiveFetcher()
    ca_url = ca_fetcher.build_url(CLOUD_ARCHIVE_BASE_URL, "jammy", "caracal", "main", "amd64")
    print(f"Cloud Archive URL: {ca_url}")
