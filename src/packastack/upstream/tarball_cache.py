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

"""Tarball extraction and caching for release dependency validation.

Provides utilities to extract release tarballs to a cache directory
for dependency analysis during release builds.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Default cache location
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "packastack" / "upstream-tarballs"

# Metadata filename
CACHE_METADATA_FILE = ".cache_metadata.json"

# Default expiry in days
DEFAULT_CACHE_EXPIRY_DAYS = 14

# Raw tarball cache directory name (under DEFAULT_CACHE_DIR)
TARBALLS_DIR_NAME = "artifacts"

# Metadata filename for cached tarballs
TARBALL_METADATA_FILE = "tarball.json"


@dataclass(frozen=True)
class TarballCacheEntry:
    """Immutable entry for caching a tarball.

    This bundles all metadata about a tarball to be cached,
    replacing the 11 individual parameters to cache_tarball().

    Attributes:
        project: Project name (e.g., "glance", "nova").
        package_name: Debian source package name.
        version: Version string.
        build_type: Build type ("release", "snapshot", "milestone").
        source_method: How tarball was acquired ("uscan", "official", etc.).
        source_url: URL where tarball was fetched from.
        git_sha: Git commit SHA for snapshots.
        git_date: Git commit date for snapshots (YYYYMMDD).
        git_ref: Git ref used (branch or tag name).
        signature_verified: Whether GPG signature was verified.
        signature_warning: Warning message about signature verification.
    """

    project: str
    package_name: str
    version: str
    build_type: str
    source_method: str = ""
    source_url: str = ""
    git_sha: str = ""
    git_date: str = ""
    git_ref: str = ""
    signature_verified: bool = False
    signature_warning: str = ""


@dataclass
class CacheMetadata:
    """Metadata for a cached tarball extraction."""

    project: str
    version: str
    extracted_at: str  # ISO format timestamp
    tarball_path: str  # Original tarball path
    tarball_size: int  # Size in bytes

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "project": self.project,
            "version": self.version,
            "extracted_at": self.extracted_at,
            "tarball_path": self.tarball_path,
            "tarball_size": self.tarball_size,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CacheMetadata:
        """Create from dictionary."""
        return cls(
            project=data["project"],
            version=data["version"],
            extracted_at=data["extracted_at"],
            tarball_path=data["tarball_path"],
            tarball_size=data["tarball_size"],
        )

    def is_expired(self, max_age_days: int = DEFAULT_CACHE_EXPIRY_DAYS) -> bool:
        """Check if the cache entry has expired."""
        extracted = datetime.fromisoformat(self.extracted_at)
        # Ensure timezone-aware comparison
        if extracted.tzinfo is None:
            extracted = extracted.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        return now - extracted > timedelta(days=max_age_days)


@dataclass
class TarballMetadata:
    """Metadata for a cached tarball artifact."""

    project: str
    package_name: str
    version: str
    build_type: str
    cached_at: str  # ISO format timestamp
    tarball_name: str
    tarball_size: int
    source_method: str = ""
    source_url: str = ""
    git_sha: str = ""
    git_date: str = ""
    git_ref: str = ""
    signature_verified: bool = False
    signature_warning: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "project": self.project,
            "package_name": self.package_name,
            "version": self.version,
            "build_type": self.build_type,
            "cached_at": self.cached_at,
            "tarball_name": self.tarball_name,
            "tarball_size": self.tarball_size,
            "source_method": self.source_method,
            "source_url": self.source_url,
            "git_sha": self.git_sha,
            "git_date": self.git_date,
            "git_ref": self.git_ref,
            "signature_verified": self.signature_verified,
            "signature_warning": self.signature_warning,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TarballMetadata:
        """Create from dictionary."""
        return cls(
            project=data.get("project", ""),
            package_name=data.get("package_name", ""),
            version=data.get("version", ""),
            build_type=data.get("build_type", ""),
            cached_at=data.get("cached_at", ""),
            tarball_name=data.get("tarball_name", ""),
            tarball_size=int(data.get("tarball_size", 0)),
            source_method=data.get("source_method", ""),
            source_url=data.get("source_url", ""),
            git_sha=data.get("git_sha", ""),
            git_date=data.get("git_date", ""),
            git_ref=data.get("git_ref", ""),
            signature_verified=bool(data.get("signature_verified", False)),
            signature_warning=data.get("signature_warning", ""),
        )


@dataclass
class ExtractionResult:
    """Result of extracting a tarball."""

    success: bool
    extraction_path: Path | None
    error: str = ""
    from_cache: bool = False


def get_cache_dir(
    project: str,
    version: str,
    cache_base: Path = DEFAULT_CACHE_DIR,
) -> Path:
    """Get the cache directory for a project/version.

    Args:
        project: Project name (e.g., "glance", "nova").
        version: Version string (e.g., "2024.1.0").
        cache_base: Base cache directory.

    Returns:
        Path to the cache directory for this project/version.
    """
    return cache_base / project / version


def read_cache_metadata(cache_dir: Path) -> CacheMetadata | None:
    """Read cache metadata from a cache directory.

    Args:
        cache_dir: Path to the cache directory.

    Returns:
        CacheMetadata if found and valid, None otherwise.
    """
    metadata_path = cache_dir / CACHE_METADATA_FILE
    if not metadata_path.exists():
        return None

    try:
        with metadata_path.open() as f:
            data = json.load(f)
        return CacheMetadata.from_dict(data)
    except (json.JSONDecodeError, KeyError, OSError) as e:
        logger.debug(f"Failed to read cache metadata: {e}")
        return None


def write_cache_metadata(cache_dir: Path, metadata: CacheMetadata) -> bool:
    """Write cache metadata to a cache directory.

    Args:
        cache_dir: Path to the cache directory.
        metadata: Metadata to write.

    Returns:
        True if successful, False otherwise.
    """
    metadata_path = cache_dir / CACHE_METADATA_FILE
    try:
        with metadata_path.open("w") as f:
            json.dump(metadata.to_dict(), f, indent=2)
        return True
    except OSError as e:
        logger.debug(f"Failed to write cache metadata: {e}")
        return False


def get_cached_extraction(
    project: str,
    version: str,
    cache_base: Path = DEFAULT_CACHE_DIR,
    max_age_days: int = DEFAULT_CACHE_EXPIRY_DAYS,
) -> Path | None:
    """Get a cached extraction if it exists and is not expired.

    Args:
        project: Project name.
        version: Version string.
        cache_base: Base cache directory.
        max_age_days: Maximum age of cache entries in days.

    Returns:
        Path to the cached extraction directory if valid, None otherwise.
    """
    cache_dir = get_cache_dir(project, version, cache_base)
    if not cache_dir.exists():
        return None

    metadata = read_cache_metadata(cache_dir)
    if metadata is None:
        logger.debug(f"No metadata found in cache: {cache_dir}")
        return None

    if metadata.is_expired(max_age_days):
        logger.debug(f"Cache expired for {project} {version}")
        return None

    # Find the extracted source directory (should be the only non-metadata item)
    for item in cache_dir.iterdir():
        if item.is_dir() and item.name != "__pycache__":
            return item

    return None


def find_source_dir(cache_dir: Path) -> Path | None:
    """Find the source directory within an extracted tarball.

    Tarballs typically extract to a directory like `project-version/`.

    Args:
        cache_dir: Path to the cache directory containing the extraction.

    Returns:
        Path to the source directory, or None if not found.
    """
    for item in cache_dir.iterdir():
        if item.is_dir() and item.name != "__pycache__":
            # Check if it looks like a Python source directory
            if (item / "requirements.txt").exists() or (item / "pyproject.toml").exists():
                return item
    return None


def extract_tarball(
    tarball_path: Path,
    project: str,
    version: str,
    cache_base: Path = DEFAULT_CACHE_DIR,
    force: bool = False,
) -> ExtractionResult:
    """Extract a tarball to the cache directory.

    If the cache already contains a valid extraction for this project/version,
    returns the cached version unless force=True.

    Args:
        tarball_path: Path to the tarball file.
        project: Project name.
        version: Version string.
        cache_base: Base cache directory.
        force: If True, re-extract even if cached.

    Returns:
        ExtractionResult with the extraction path or error.
    """
    if not tarball_path.exists():
        return ExtractionResult(
            success=False,
            extraction_path=None,
            error=f"Tarball not found: {tarball_path}",
        )

    cache_dir = get_cache_dir(project, version, cache_base)

    # Check for valid cache unless forcing
    if not force:
        cached = get_cached_extraction(project, version, cache_base)
        if cached:
            logger.debug(f"Using cached extraction: {cached}")
            return ExtractionResult(
                success=True,
                extraction_path=cached,
                from_cache=True,
            )

    # Remove existing cache dir if present
    if cache_dir.exists():
        logger.debug(f"Removing existing cache: {cache_dir}")
        shutil.rmtree(cache_dir)

    # Create cache directory
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Extract tarball
    try:
        with tarfile.open(tarball_path, "r:*") as tar:
            # Security: filter out unsafe members
            def safe_extract_filter(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo | None:
                """Filter for safe extraction (Python 3.12+ style)."""
                # Skip absolute paths
                if member.name.startswith("/"):
                    logger.warning(f"Skipping absolute path: {member.name}")
                    return None
                # Skip path traversal
                if ".." in member.name:
                    logger.warning(f"Skipping path traversal: {member.name}")
                    return None
                return member

            # Use filter if available (Python 3.12+), otherwise manual filtering
            try:
                tar.extractall(path=cache_dir, filter="data")
            except TypeError:
                # Fallback for older Python
                members = [m for m in tar.getmembers() if safe_extract_filter(m, str(cache_dir))]
                tar.extractall(path=cache_dir, members=members)

    except tarfile.TarError as e:
        return ExtractionResult(
            success=False,
            extraction_path=None,
            error=f"Failed to extract tarball: {e}",
        )

    # Find the source directory
    source_dir = find_source_dir(cache_dir)
    if not source_dir:
        return ExtractionResult(
            success=False,
            extraction_path=None,
            error="Could not find source directory in extracted tarball",
        )

    # Write metadata
    metadata = CacheMetadata(
        project=project,
        version=version,
        extracted_at=datetime.now(UTC).isoformat(),
        tarball_path=str(tarball_path),
        tarball_size=tarball_path.stat().st_size,
    )
    write_cache_metadata(cache_dir, metadata)

    # Trigger cleanup of expired entries
    cleanup_expired_cache(cache_base)

    return ExtractionResult(
        success=True,
        extraction_path=source_dir,
        from_cache=False,
    )


def cleanup_expired_cache(
    cache_base: Path = DEFAULT_CACHE_DIR,
    max_age_days: int = DEFAULT_CACHE_EXPIRY_DAYS,
) -> list[Path]:
    """Clean up expired cache entries.

    Args:
        cache_base: Base cache directory.
        max_age_days: Maximum age of cache entries in days.

    Returns:
        List of paths that were removed.
    """
    removed: list[Path] = []

    if not cache_base.exists():
        return removed

    for project_dir in cache_base.iterdir():
        if not project_dir.is_dir():
            continue
        if project_dir.name == TARBALLS_DIR_NAME:
            continue

        for version_dir in project_dir.iterdir():
            if not version_dir.is_dir():
                continue

            metadata = read_cache_metadata(version_dir)
            if metadata is None or metadata.is_expired(max_age_days):
                logger.debug(f"Removing expired cache: {version_dir}")
                try:
                    shutil.rmtree(version_dir)
                    removed.append(version_dir)
                except OSError as e:
                    logger.warning(f"Failed to remove expired cache {version_dir}: {e}")

        # Remove empty project directories
        if project_dir.exists() and not any(project_dir.iterdir()):
            with contextlib.suppress(OSError):
                project_dir.rmdir()

    return removed


def get_cache_size(cache_base: Path = DEFAULT_CACHE_DIR) -> int:
    """Get the total size of the cache in bytes.

    Args:
        cache_base: Base cache directory.

    Returns:
        Total size in bytes.
    """
    if not cache_base.exists():
        return 0

    total = 0
    for path in cache_base.rglob("*"):
        if path.is_file():
            with contextlib.suppress(OSError):
                total += path.stat().st_size
    return total


def list_cached_projects(
    cache_base: Path = DEFAULT_CACHE_DIR,
) -> list[tuple[str, str, CacheMetadata | None]]:
    """List all cached project/version combinations.

    Args:
        cache_base: Base cache directory.

    Returns:
        List of (project, version, metadata) tuples.
    """
    result: list[tuple[str, str, CacheMetadata | None]] = []

    if not cache_base.exists():
        return result

    for project_dir in cache_base.iterdir():
        if not project_dir.is_dir():
            continue
        if project_dir.name == TARBALLS_DIR_NAME:
            continue

        for version_dir in project_dir.iterdir():
            if not version_dir.is_dir():
                continue

            metadata = read_cache_metadata(version_dir)
            result.append((project_dir.name, version_dir.name, metadata))

    return result


def _safe_cache_key(value: str) -> str:
    """Return a filesystem-safe cache key."""
    return value.replace("/", "_")


def get_tarball_cache_root(cache_base: Path = DEFAULT_CACHE_DIR) -> Path:
    """Return the base directory for cached tarball artifacts."""
    return cache_base / TARBALLS_DIR_NAME


def get_tarball_cache_dir(
    project: str,
    version: str,
    cache_base: Path = DEFAULT_CACHE_DIR,
) -> Path:
    """Return the cache directory for a project/version tarball."""
    safe_project = _safe_cache_key(project)
    safe_version = _safe_cache_key(version)
    return get_tarball_cache_root(cache_base) / safe_project / safe_version


def read_tarball_metadata(cache_dir: Path) -> TarballMetadata | None:
    """Read tarball metadata from a cache directory."""
    metadata_path = cache_dir / TARBALL_METADATA_FILE
    if not metadata_path.exists():
        return None

    try:
        with metadata_path.open() as f:
            data = json.load(f)
        return TarballMetadata.from_dict(data)
    except (json.JSONDecodeError, OSError) as e:
        logger.debug(f"Failed to read tarball metadata: {e}")
        return None


def write_tarball_metadata(cache_dir: Path, metadata: TarballMetadata) -> bool:
    """Write tarball metadata to a cache directory."""
    metadata_path = cache_dir / TARBALL_METADATA_FILE
    try:
        with metadata_path.open("w") as f:
            json.dump(metadata.to_dict(), f, indent=2)
        return True
    except OSError as e:
        logger.debug(f"Failed to write tarball metadata: {e}")
        return False


def cache_tarball(
    tarball_path: Path,
    entry: TarballCacheEntry,
    cache_base: Path = DEFAULT_CACHE_DIR,
) -> tuple[Path | None, TarballMetadata | None]:
    """Copy a tarball into the cache and write metadata.

    Args:
        tarball_path: Path to the tarball file to cache.
        entry: TarballCacheEntry with all metadata about the tarball.
        cache_base: Base cache directory.

    Returns:
        Tuple of (cached_path, metadata) or (None, None) on failure.
    """
    if not tarball_path.exists():
        return None, None

    cache_dir = get_tarball_cache_dir(entry.project, entry.version, cache_base)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        dest = cache_dir / tarball_path.name
        shutil.copy2(tarball_path, dest)
    except OSError as e:
        logger.debug(f"Failed to cache tarball: {e}")
        return None, None

    metadata = TarballMetadata(
        project=entry.project,
        package_name=entry.package_name,
        version=entry.version,
        build_type=entry.build_type,
        cached_at=datetime.now(UTC).isoformat(),
        tarball_name=dest.name,
        tarball_size=dest.stat().st_size,
        source_method=entry.source_method,
        source_url=entry.source_url,
        git_sha=entry.git_sha,
        git_date=entry.git_date,
        git_ref=entry.git_ref,
        signature_verified=entry.signature_verified,
        signature_warning=entry.signature_warning,
    )
    write_tarball_metadata(cache_dir, metadata)
    return dest, metadata


def find_cached_tarball(
    project: str,
    version: str | None = None,
    build_type: str | None = None,
    cache_base: Path = DEFAULT_CACHE_DIR,
    allow_latest: bool = False,
) -> tuple[Path | None, TarballMetadata | None]:
    """Find a cached tarball and its metadata."""
    project_dir = get_tarball_cache_root(cache_base) / _safe_cache_key(project)
    if not project_dir.exists():
        return None, None

    def _candidate(cache_dir: Path) -> tuple[Path | None, TarballMetadata | None]:
        meta = read_tarball_metadata(cache_dir)
        if not meta:
            return None, None
        if build_type and meta.build_type != build_type:
            return None, None
        tarball_path = cache_dir / meta.tarball_name
        if not tarball_path.exists():
            return None, None
        return tarball_path, meta

    if version:
        version_dir = project_dir / _safe_cache_key(version)
        if not version_dir.exists():
            return None, None
        return _candidate(version_dir)

    if not allow_latest:
        return None, None

    best_path: Path | None = None
    best_meta: TarballMetadata | None = None
    best_time: datetime | None = None

    for version_dir in project_dir.iterdir():
        if not version_dir.is_dir():
            continue
        tarball_path, meta = _candidate(version_dir)
        if not tarball_path or not meta:
            continue
        try:
            cached_at = datetime.fromisoformat(meta.cached_at)
            if cached_at.tzinfo is None:
                cached_at = cached_at.replace(tzinfo=UTC)
        except ValueError:
            cached_at = datetime.fromtimestamp(tarball_path.stat().st_mtime, tz=UTC)

        if best_time is None or cached_at > best_time:
            best_time = cached_at
            best_path = tarball_path
            best_meta = meta

    return best_path, best_meta
