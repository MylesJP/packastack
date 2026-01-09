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

"""Sbuild artifact and log collector.

Discovers and collects sbuild output artifacts (.deb, .changes, .buildinfo)
and log files from various candidate directories, supporting user and global
sbuild configuration.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from packastack.build.sbuildrc import (
    CandidateDirectories,
    discover_candidate_directories,
    parse_sbuild_output_for_paths,
)

logger = logging.getLogger(__name__)


# Artifact file extensions to collect
BINARY_EXTENSIONS = {".deb", ".udeb", ".ddeb"}
METADATA_EXTENSIONS = {".changes", ".buildinfo"}
ALL_ARTIFACT_EXTENSIONS = BINARY_EXTENSIONS | METADATA_EXTENSIONS

# Log file patterns
LOG_EXTENSIONS = {".log", ".build"}


@dataclass
class CollectedFile:
    """Information about a collected file."""

    source_path: Path
    copied_path: Path
    sha256: str
    size: int
    mtime: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "source_path": str(self.source_path),
            "copied_path": str(self.copied_path),
            "sha256": self.sha256,
            "size": self.size,
            "mtime": self.mtime,
        }


@dataclass
class CollectionResult:
    """Result of artifact/log collection."""

    success: bool
    binaries: list[CollectedFile] = field(default_factory=list)
    metadata: list[CollectedFile] = field(default_factory=list)
    logs: list[CollectedFile] = field(default_factory=list)
    searched_dirs: list[str] = field(default_factory=list)
    validation_message: str = ""

    @property
    def deb_count(self) -> int:
        """Count of .deb and .udeb files."""
        return sum(1 for f in self.binaries if f.source_path.suffix in {".deb", ".udeb"})

    @property
    def changes_count(self) -> int:
        """Count of .changes files."""
        return sum(1 for f in self.metadata if f.source_path.suffix == ".changes")

    @property
    def buildinfo_count(self) -> int:
        """Count of .buildinfo files."""
        return sum(1 for f in self.metadata if f.source_path.suffix == ".buildinfo")

    @property
    def all_artifacts(self) -> list[CollectedFile]:
        """All collected artifacts (binaries + metadata)."""
        return self.binaries + self.metadata

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "success": self.success,
            "validation_message": self.validation_message,
            "searched_dirs": self.searched_dirs,
            "counts": {
                "debs": self.deb_count,
                "changes": self.changes_count,
                "buildinfo": self.buildinfo_count,
                "logs": len(self.logs),
            },
            "binaries": [f.to_dict() for f in self.binaries],
            "metadata": [f.to_dict() for f in self.metadata],
            "logs": [f.to_dict() for f in self.logs],
        }


@dataclass
class ArtifactReport:
    """Complete report of sbuild artifact collection."""

    sbuild_command: list[str]
    sbuild_exit_code: int
    start_timestamp: str
    end_timestamp: str
    candidate_dirs: list[str]
    collection: CollectionResult
    stdout_path: str
    stderr_path: str
    primary_log_path: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "sbuild_command": self.sbuild_command,
            "sbuild_exit_code": self.sbuild_exit_code,
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "candidate_dirs": self.candidate_dirs,
            "collection": self.collection.to_dict(),
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
            "primary_log_path": self.primary_log_path,
        }

    def write_json(self, path: Path) -> None:
        """Write report to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))


def compute_sha256(path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def copy_file_with_checksum(source: Path, dest_dir: Path) -> CollectedFile:
    """Copy a file to destination directory and compute checksum.

    Args:
        source: Source file path.
        dest_dir: Destination directory.

    Returns:
        CollectedFile with source, destination, and checksum info.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / source.name

    # If source and dest are already the same file, no copy needed
    if dest.exists() and dest.samefile(source):
        return CollectedFile(
            source_path=source.resolve(),
            copied_path=dest.resolve(),
            sha256=compute_sha256(dest),
            size=dest.stat().st_size,
            mtime=source.stat().st_mtime,
        )

    # Handle potential name collision
    if dest.exists():
        # Add timestamp to avoid overwriting
        stem = source.stem
        suffix = source.suffix
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        dest = dest_dir / f"{stem}_{timestamp}{suffix}"

    shutil.copy2(source, dest)

    return CollectedFile(
        source_path=source.resolve(),
        copied_path=dest.resolve(),
        sha256=compute_sha256(dest),
        size=dest.stat().st_size,
        mtime=source.stat().st_mtime,
    )


def matches_package(
    filename: str,
    source_package: str | None = None,
    version: str | None = None,
) -> bool:
    """Check if a filename matches the expected package name/version.

    Handles common Debian/Ubuntu naming patterns:
    - Exact source package prefix match
    - Python package patterns: python-X (source) -> python3-X (binary)
    - Documentation packages: python-X -> python-X-doc
    - Version-based matching as fallback when version is provided

    Args:
        filename: Name of the file to check.
        source_package: Expected source package name (optional).
        version: Expected package version (optional).

    Returns:
        True if the filename matches, False otherwise.
    """
    if not source_package:
        return True

    # Debian package naming: <name>_<version>_<arch>.<ext>
    # or <name>_<version>.<ext> for source-related files
    name_lower = filename.lower()
    pkg_lower = source_package.lower()

    # Generate candidate prefixes to match against
    # This handles common binary package naming patterns
    candidates = [pkg_lower]
    
    # Handle underscore/hyphen variants
    if "-" in pkg_lower:
        candidates.append(pkg_lower.replace("-", "_"))
    if "_" in pkg_lower:
        candidates.append(pkg_lower.replace("_", "-"))
    
    # Handle Python package naming: python-X -> python3-X
    # Also handle python-X-Y -> python3-X-Y patterns
    if pkg_lower.startswith("python-"):
        base = pkg_lower[7:]  # Remove "python-" prefix
        candidates.append(f"python3-{base}")
        candidates.append(f"python3_{base}")
    
    # Check if any candidate prefix matches
    name_matches = any(name_lower.startswith(c) for c in candidates)
    
    # If version is specified, check for it
    if version:
        # Remove epoch for filename matching
        clean_version = version.split(":")[-1] if ":" in version else version
        version_matches = clean_version in filename
        
        # If we have version info, we can be more lenient:
        # - If name matches AND version matches -> definitely a match
        # - If name doesn't match but version matches -> likely a related binary package
        #   (e.g., python-X source produces libX binary)
        # For now, require name match OR version match (if version provided)
        if name_matches:
            return version_matches
        else:
            # Version match alone is acceptable for related binaries
            # but only if the version string is specific enough (contains full version)
            return version_matches
    
    return name_matches


def find_artifacts_in_directory(
    directory: Path,
    source_package: str | None = None,
    version: str | None = None,
    start_time: float | None = None,
    extensions: set[str] | None = None,
) -> list[Path]:
    """Find artifact files in a directory.

    Uses package name/version matching if provided, otherwise falls back to
    timestamp filtering.

    Args:
        directory: Directory to search.
        source_package: Source package name for filtering (exclusive filter).
        version: Package version for filtering.
        start_time: Start time (epoch) to filter by mtime (only used if no source_package).
        extensions: Set of file extensions to match.

    Returns:
        List of matching file paths.
    """
    if not directory.exists() or not directory.is_dir():
        return []

    if extensions is None:
        extensions = ALL_ARTIFACT_EXTENSIONS

    matches = []

    try:
        for path in directory.iterdir():
            if not path.is_file():
                continue

            if path.suffix not in extensions:
                continue

            # If source_package is provided, use it as exclusive filter
            if source_package:
                if matches_package(path.name, source_package, version):
                    matches.append(path)
                # Skip to next file if package doesn't match (don't fall through)
                continue

            # No source_package: use timestamp filtering if available
            if start_time is not None:
                try:
                    if path.stat().st_mtime >= start_time:
                        matches.append(path)
                except OSError:
                    continue
            else:
                # No filtering criteria at all, include all
                matches.append(path)

    except OSError as e:
        logger.debug("Error scanning directory %s: %s", directory, e)

    return matches


def find_logs_in_directory(
    directory: Path,
    source_package: str | None = None,
    start_time: float | None = None,
) -> list[Path]:
    """Find log files in a directory.

    Args:
        directory: Directory to search.
        source_package: Source package name for filtering (exclusive filter).
        start_time: Start time (epoch) to filter by mtime (only used if no source_package).

    Returns:
        List of matching log file paths.
    """
    if not directory.exists() or not directory.is_dir():
        return []

    matches = []

    try:
        for path in directory.iterdir():
            if not path.is_file():
                continue

            if path.suffix not in LOG_EXTENSIONS:
                continue

            # If source_package is provided, use it as exclusive filter
            if source_package:
                if source_package.lower() in path.name.lower():
                    matches.append(path)
                # Skip to next file if package doesn't match (don't fall through)
                continue

            # No source_package: use timestamp filtering if available
            if start_time is not None:
                try:
                    if path.stat().st_mtime >= start_time:
                        matches.append(path)
                except OSError:
                    continue
            else:
                matches.append(path)

    except OSError as e:
        logger.debug("Error scanning log directory %s: %s", directory, e)

    return matches


def collect_artifacts(
    dest_dir: Path,
    candidates: CandidateDirectories,
    source_package: str | None = None,
    version: str | None = None,
    start_time: float | None = None,
    sbuild_output: str | None = None,
) -> CollectionResult:
    """Collect sbuild artifacts from candidate directories.

    Searches all candidate build directories for artifacts matching the
    package name/version or timestamp criteria, copies them to the
    destination directory, and validates that binaries were found.

    Args:
        dest_dir: Directory to copy artifacts to.
        candidates: Candidate directories to search.
        source_package: Source package name for filtering.
        version: Package version for filtering.
        start_time: Build start time for timestamp filtering.
        sbuild_output: Sbuild stdout/stderr for path hints.

    Returns:
        CollectionResult with collected artifacts and validation status.
    """
    result = CollectionResult(success=False)

    # Add any directories discovered from sbuild output
    if sbuild_output:
        output_paths = parse_sbuild_output_for_paths(sbuild_output)
        if output_paths.build_dir:
            candidates.add_build_dir(output_paths.build_dir, "sbuild output hint")
        if output_paths.log_dir:
            candidates.add_log_dir(output_paths.log_dir, "sbuild output hint")

    # Track searched directories
    searched_build_dirs: list[str] = []
    searched_log_dirs: list[str] = []

    # Collect seen files to avoid duplicates
    seen_artifact_paths: set[Path] = set()
    seen_log_paths: set[Path] = set()

    # Search build directories for artifacts
    for build_dir in candidates.build_dirs:
        if not build_dir.exists():
            continue

        searched_build_dirs.append(str(build_dir))

        # Find binary artifacts (.deb, .udeb, .ddeb)
        for artifact_path in find_artifacts_in_directory(
            build_dir,
            source_package=source_package,
            version=version,
            start_time=start_time,
            extensions=BINARY_EXTENSIONS,
        ):
            if artifact_path.resolve() in seen_artifact_paths:
                continue
            seen_artifact_paths.add(artifact_path.resolve())

            try:
                collected = copy_file_with_checksum(artifact_path, dest_dir)
                result.binaries.append(collected)
                logger.debug("Collected binary: %s -> %s", artifact_path, collected.copied_path)
            except OSError as e:
                logger.warning("Failed to collect artifact %s: %s", artifact_path, e)

        # Find metadata artifacts (.changes, .buildinfo)
        for artifact_path in find_artifacts_in_directory(
            build_dir,
            source_package=source_package,
            version=version,
            start_time=start_time,
            extensions=METADATA_EXTENSIONS,
        ):
            if artifact_path.resolve() in seen_artifact_paths:
                continue
            seen_artifact_paths.add(artifact_path.resolve())

            try:
                collected = copy_file_with_checksum(artifact_path, dest_dir)
                result.metadata.append(collected)
                logger.debug("Collected metadata: %s -> %s", artifact_path, collected.copied_path)
            except OSError as e:
                logger.warning("Failed to collect artifact %s: %s", artifact_path, e)

    # Search log directories for logs
    for log_dir in candidates.log_dirs:
        if not log_dir.exists():
            continue

        searched_log_dirs.append(str(log_dir))

        for log_path in find_logs_in_directory(
            log_dir,
            source_package=source_package,
            start_time=start_time,
        ):
            if log_path.resolve() in seen_log_paths:
                continue
            seen_log_paths.add(log_path.resolve())

            try:
                collected = copy_file_with_checksum(log_path, dest_dir)
                result.logs.append(collected)
                logger.debug("Collected log: %s -> %s", log_path, collected.copied_path)
            except OSError as e:
                logger.warning("Failed to collect log %s: %s", log_path, e)

    # Record all searched directories
    result.searched_dirs = searched_build_dirs + searched_log_dirs

    # Validate that we found binaries
    if result.deb_count > 0:
        result.success = True
        result.validation_message = (
            f"Collected {result.deb_count} binary package(s), "
            f"{result.changes_count} changes file(s), "
            f"{result.buildinfo_count} buildinfo file(s)"
        )
    else:
        result.success = False
        result.validation_message = (
            f"No binary packages (.deb/.udeb) found. "
            f"Searched {len(searched_build_dirs)} directories: "
            f"{', '.join(searched_build_dirs[:3])}..."
            if len(searched_build_dirs) > 3
            else f"No binary packages (.deb/.udeb) found. "
            f"Searched directories: {', '.join(searched_build_dirs) or 'none'}"
        )

    return result


def create_primary_log_symlink(
    logs: list[CollectedFile],
    dest_dir: Path,
    link_name: str = "sbuild.log",
) -> Path | None:
    """Create a symlink to the primary sbuild log file.

    Selects the largest or most recent log file as the primary.

    Args:
        logs: List of collected log files.
        dest_dir: Directory to create the symlink in.
        link_name: Name for the symlink.

    Returns:
        Path to the primary log (symlink or copy), or None if no logs.
    """
    if not logs:
        return None

    # Find the primary log (largest file, as it's likely the main build log)
    primary = max(logs, key=lambda f: f.size)
    link_path = dest_dir / link_name

    # If the primary log has a different name, create a copy with the stable name
    if primary.copied_path.name != link_name:
        try:
            shutil.copy2(primary.copied_path, link_path)
            return link_path
        except OSError:
            return primary.copied_path

    return primary.copied_path
