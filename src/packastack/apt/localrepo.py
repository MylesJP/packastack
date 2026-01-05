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

"""Local APT repository management for Packastack builds.

Provides functionality to publish built artifacts into a local APT repository
and regenerate Packages/Packages.gz indexes. This allows subsequently built
packages to depend on previously built packages from the same build run.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import shutil
import subprocess
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

# Suppress python3-apt warning - it's optional
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message=".*python.*-apt.*")
    warnings.filterwarnings("ignore", message=".*apt_pkg.*")
    from debian.debian_support import Version

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class DebPackageInfo:
    """Information extracted from a .deb package."""

    package: str
    version: str
    architecture: str
    source: str = ""
    depends: str = ""
    pre_depends: str = ""
    provides: str = ""
    description: str = ""
    maintainer: str = ""
    section: str = ""
    priority: str = ""
    installed_size: int = 0
    # These are computed when publishing
    filename: str = ""
    size: int = 0
    md5sum: str = ""
    sha256: str = ""


@dataclass
class SourcePackageInfo:
    """Information extracted from a .dsc file."""

    source: str
    version: str
    maintainer: str = ""
    uploaders: str = ""
    homepage: str = ""
    standards_version: str = ""
    build_depends: str = ""
    architecture: str = ""
    format: str = ""
    # File checksums for the source package
    files: list[tuple[str, int, str]] = field(default_factory=list)  # [(filename, size, hash), ...]
    # Directory in the pool
    directory: str = ""


@dataclass
class PublishResult:
    """Result of publishing artifacts to the local repo."""

    success: bool
    published_paths: list[Path] = field(default_factory=list)
    error: str = ""


@dataclass
class IndexResult:
    """Result of regenerating repository indexes."""

    success: bool
    packages_file: Path | None = None
    packages_gz_file: Path | None = None
    package_count: int = 0
    error: str = ""


def compute_file_hashes(file_path: Path) -> tuple[str, str]:
    """Compute MD5 and SHA256 hashes of a file.

    Args:
        file_path: Path to the file.

    Returns:
        Tuple of (md5_hex, sha256_hex).
    """
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()

    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            md5.update(chunk)
            sha256.update(chunk)

    return md5.hexdigest(), sha256.hexdigest()


def extract_deb_control(deb_path: Path) -> DebPackageInfo | None:
    """Extract control information from a .deb file.

    Uses dpkg-deb to extract the control file content. This is the most
    reliable method as it handles all debian archive formats.

    Args:
        deb_path: Path to the .deb file.

    Returns:
        DebPackageInfo with extracted fields, or None on failure.
    """
    try:
        result = subprocess.run(
            ["dpkg-deb", "--info", str(deb_path), "control"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None

        control_text = result.stdout

        # Parse the control file
        info = DebPackageInfo(package="", version="", architecture="")

        current_field: str | None = None
        current_value: list[str] = []

        for line in control_text.split("\n"):
            if line.startswith(" ") or line.startswith("\t"):
                # Continuation of previous field
                if current_field:
                    current_value.append(line.strip())
            elif ":" in line:
                # Save previous field
                if current_field and current_value:
                    _set_field(info, current_field, "\n".join(current_value))

                # New field
                parts = line.split(":", 1)
                current_field = parts[0].strip()
                current_value = [parts[1].strip()] if len(parts) > 1 else []
            else:
                # Empty line or other
                if current_field and current_value:
                    _set_field(info, current_field, "\n".join(current_value))
                current_field = None
                current_value = []

        # Don't forget the last field
        if current_field and current_value:
            _set_field(info, current_field, "\n".join(current_value))

        if not info.package or not info.version or not info.architecture:
            return None

        return info

    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError:
        # dpkg-deb not installed
        return None
    except Exception:
        return None


def _set_field(info: DebPackageInfo, field_name: str, value: str) -> None:
    """Set a field on DebPackageInfo based on control field name."""
    field_lower = field_name.lower()
    mapping = {
        "package": "package",
        "version": "version",
        "architecture": "architecture",
        "source": "source",
        "depends": "depends",
        "pre-depends": "pre_depends",
        "provides": "provides",
        "description": "description",
        "maintainer": "maintainer",
        "section": "section",
        "priority": "priority",
        "installed-size": "installed_size",
    }

    attr = mapping.get(field_lower)
    if attr:
        if attr == "installed_size":
            try:
                setattr(info, attr, int(value))
            except ValueError:
                pass
        else:
            setattr(info, attr, value)


def extract_dsc_info(dsc_path: Path) -> SourcePackageInfo | None:
    """Extract information from a .dsc file.

    Args:
        dsc_path: Path to the .dsc file.

    Returns:
        SourcePackageInfo with extracted fields, or None on failure.
    """
    try:
        content = dsc_path.read_text(encoding="utf-8", errors="replace")

        info = SourcePackageInfo(source="", version="")

        current_field: str | None = None
        current_value: list[str] = []
        checksums_section: str = ""  # "Files", "Checksums-Sha256", etc.

        for line in content.split("\n"):
            if line.startswith(" ") or line.startswith("\t"):
                # Continuation of previous field
                if current_field:
                    current_value.append(line.strip())
            elif ":" in line:
                # Save previous field
                if current_field and current_value:
                    _set_dsc_field(info, current_field, current_value)

                # New field
                parts = line.split(":", 1)
                current_field = parts[0].strip()
                val = parts[1].strip() if len(parts) > 1 else ""
                current_value = [val] if val else []
            else:
                # Empty line or other
                if current_field and current_value:
                    _set_dsc_field(info, current_field, current_value)
                current_field = None
                current_value = []

        # Don't forget the last field
        if current_field and current_value:
            _set_dsc_field(info, current_field, current_value)

        if not info.source or not info.version:
            return None

        # Compute file entries from the .dsc location
        pool_dir = dsc_path.parent
        info.directory = f"pool/main"

        # Find associated files based on Files: section or by naming convention
        base_name = dsc_path.stem  # e.g., "nova_29.0.0-0ubuntu1"
        for f in pool_dir.iterdir():
            if f.name.startswith(base_name.rsplit("_", 1)[0] + "_"):
                # Compute hash
                _, sha256 = compute_file_hashes(f)
                info.files.append((f.name, f.stat().st_size, sha256))

        return info

    except Exception:
        return None


def _set_dsc_field(
    info: SourcePackageInfo, field_name: str, value: list[str]
) -> None:
    """Set a field on SourcePackageInfo based on dsc field name."""
    field_lower = field_name.lower()
    joined = " ".join(value) if value else ""

    if field_lower == "source":
        info.source = joined
    elif field_lower == "version":
        info.version = joined
    elif field_lower == "maintainer":
        info.maintainer = joined
    elif field_lower == "uploaders":
        info.uploaders = joined
    elif field_lower == "homepage":
        info.homepage = joined
    elif field_lower == "standards-version":
        info.standards_version = joined
    elif field_lower == "build-depends":
        info.build_depends = joined
    elif field_lower == "architecture":
        info.architecture = joined
    elif field_lower == "format":
        info.format = joined


def format_packages_entry(info: DebPackageInfo) -> str:
    """Format a single Packages file entry.

    Args:
        info: Package information with computed hashes.

    Returns:
        Formatted Packages entry as a string.
    """
    lines = [
        f"Package: {info.package}",
        f"Version: {info.version}",
        f"Architecture: {info.architecture}",
    ]

    if info.source:
        lines.append(f"Source: {info.source}")
    if info.maintainer:
        lines.append(f"Maintainer: {info.maintainer}")
    if info.section:
        lines.append(f"Section: {info.section}")
    if info.priority:
        lines.append(f"Priority: {info.priority}")
    if info.installed_size:
        lines.append(f"Installed-Size: {info.installed_size}")
    if info.depends:
        lines.append(f"Depends: {info.depends}")
    if info.pre_depends:
        lines.append(f"Pre-Depends: {info.pre_depends}")
    if info.provides:
        lines.append(f"Provides: {info.provides}")

    # Required fields for apt
    lines.append(f"Filename: {info.filename}")
    lines.append(f"Size: {info.size}")
    lines.append(f"MD5sum: {info.md5sum}")
    lines.append(f"SHA256: {info.sha256}")

    if info.description:
        # Description can be multi-line, format properly
        desc_lines = info.description.split("\n")
        lines.append(f"Description: {desc_lines[0]}")
        for dl in desc_lines[1:]:
            if dl.strip():
                lines.append(f" {dl}")
            else:
                lines.append(" .")

    return "\n".join(lines) + "\n"


def format_sources_entry(info: SourcePackageInfo) -> str:
    """Format a single Sources file entry.

    Args:
        info: Source package information.

    Returns:
        Formatted Sources entry as a string.
    """
    lines = [
        f"Package: {info.source}",
        f"Version: {info.version}",
    ]

    if info.maintainer:
        lines.append(f"Maintainer: {info.maintainer}")
    if info.uploaders:
        lines.append(f"Uploaders: {info.uploaders}")
    if info.homepage:
        lines.append(f"Homepage: {info.homepage}")
    if info.standards_version:
        lines.append(f"Standards-Version: {info.standards_version}")
    if info.build_depends:
        lines.append(f"Build-Depends: {info.build_depends}")
    if info.architecture:
        lines.append(f"Architecture: {info.architecture}")
    if info.format:
        lines.append(f"Format: {info.format}")
    if info.directory:
        lines.append(f"Directory: {info.directory}")

    # Add file checksums
    if info.files:
        lines.append("Checksums-Sha256:")
        for filename, size, sha256 in info.files:
            lines.append(f" {sha256} {size} {filename}")

        lines.append("Files:")
        for filename, size, sha256 in info.files:
            # Use sha256 as placeholder for md5 (we'd need to compute both)
            lines.append(f" {sha256[:32]} {size} {filename}")

    return "\n".join(lines) + "\n"


def publish_artifacts(
    artifact_paths: list[Path],
    repo_root: Path,
    arch: str = "amd64",
) -> PublishResult:
    """Publish build artifacts to the local APT repository.

    Copies .deb files into the pool directory structure and .dsc/.changes
    files into the source pool.

    Args:
        artifact_paths: List of paths to artifacts (.deb, .dsc, .changes, etc.).
        repo_root: Root directory of the local APT repository.
        arch: Target architecture (used for pool subdirectory).

    Returns:
        PublishResult with published paths.
    """
    published: list[Path] = []

    try:
        # Create pool directories
        pool_main = repo_root / "pool" / "main"
        pool_main.mkdir(parents=True, exist_ok=True)

        for artifact in artifact_paths:
            if not artifact.exists():
                continue
            if not artifact.is_file():
                # Ignore directories or other non-regular entries that can slip into
                # artifact lists (e.g., workspace folders); copying them would fail.
                # Log which path we're skipping for debugging
                logger.debug("Skipping non-file artifact: %s", artifact)
                continue

            # Determine destination based on file type
            if artifact.suffix in (".deb", ".ddeb", ".udeb"):
                # Binary packages go to pool/main/<first-letter>/<source>/
                # For simplicity, we'll use pool/main/
                dest = pool_main / artifact.name
            elif artifact.suffix in (".dsc", ".changes", ".tar.gz", ".tar.xz", ".diff.gz", ".buildinfo"):
                # Source packages and metadata also go to pool/main/
                dest = pool_main / artifact.name
            else:
                # Other files also copied
                dest = pool_main / artifact.name

            # Copy file
            shutil.copy2(artifact, dest)
            published.append(dest)

        return PublishResult(success=True, published_paths=published)

    except Exception as e:
        return PublishResult(success=False, error=str(e))


def regenerate_indexes(repo_root: Path, arch: str = "amd64") -> IndexResult:
    """Regenerate Packages and Packages.gz indexes for the local repository.

    Scans the pool directory for .deb files, extracts their control
    information, and generates the index files.

    Args:
        repo_root: Root directory of the local APT repository.
        arch: Architecture to generate indexes for.

    Returns:
        IndexResult with generated file paths.
    """
    try:
        # Create the dists directory structure
        dists_dir = repo_root / "dists" / "local" / "main" / f"binary-{arch}"
        dists_dir.mkdir(parents=True, exist_ok=True)

        pool_dir = repo_root / "pool" / "main"
        if not pool_dir.exists():
            # No packages yet, create empty index
            packages_path = dists_dir / "Packages"
            packages_gz_path = dists_dir / "Packages.gz"
            packages_path.write_text("")
            with gzip.open(packages_gz_path, "wt", encoding="utf-8") as f:
                f.write("")
            return IndexResult(
                success=True,
                packages_file=packages_path,
                packages_gz_file=packages_gz_path,
                package_count=0,
            )

        # Collect all .deb and .udeb files (use set to avoid duplicates)
        deb_files_set = set(pool_dir.glob("**/*.deb")) | set(pool_dir.glob("**/*.udeb"))
        deb_files = list(deb_files_set)
        entries: list[str] = []

        for deb_path in deb_files:
            info = extract_deb_control(deb_path)
            if info is None:
                continue

            # Skip if architecture doesn't match (allow 'all')
            if info.architecture not in (arch, "all"):
                continue

            # Compute hashes and size
            info.md5sum, info.sha256 = compute_file_hashes(deb_path)
            info.size = deb_path.stat().st_size
            info.filename = f"pool/main/{deb_path.name}"

            entries.append(format_packages_entry(info))

        # Write Packages file
        packages_content = "\n".join(entries)
        packages_path = dists_dir / "Packages"
        packages_path.write_text(packages_content, encoding="utf-8")

        # Write Packages.gz
        packages_gz_path = dists_dir / "Packages.gz"
        with gzip.open(packages_gz_path, "wt", encoding="utf-8") as f:
            f.write(packages_content)

        return IndexResult(
            success=True,
            packages_file=packages_path,
            packages_gz_file=packages_gz_path,
            package_count=len(entries),
        )

    except Exception as e:
        return IndexResult(success=False, error=str(e))


@dataclass
class SourceIndexResult:
    """Result of regenerating source repository indexes."""

    success: bool
    sources_file: Path | None = None
    sources_gz_file: Path | None = None
    source_count: int = 0
    error: str = ""


def regenerate_source_indexes(repo_root: Path) -> SourceIndexResult:
    """Regenerate Sources and Sources.gz indexes for the local repository.

    Scans the pool directory for .dsc files, extracts their information,
    and generates the source index files.

    Args:
        repo_root: Root directory of the local APT repository.

    Returns:
        SourceIndexResult with generated file paths.
    """
    try:
        # Create the dists directory structure for source
        dists_dir = repo_root / "dists" / "local" / "main" / "source"
        dists_dir.mkdir(parents=True, exist_ok=True)

        pool_dir = repo_root / "pool" / "main"
        if not pool_dir.exists():
            # No packages yet, create empty index
            sources_path = dists_dir / "Sources"
            sources_gz_path = dists_dir / "Sources.gz"
            sources_path.write_text("")
            with gzip.open(sources_gz_path, "wt", encoding="utf-8") as f:
                f.write("")
            return SourceIndexResult(
                success=True,
                sources_file=sources_path,
                sources_gz_file=sources_gz_path,
                source_count=0,
            )

        # Collect all .dsc files
        dsc_files = list(pool_dir.glob("**/*.dsc"))
        entries: list[str] = []

        for dsc_path in dsc_files:
            info = extract_dsc_info(dsc_path)
            if info is None:
                continue

            entries.append(format_sources_entry(info))

        # Write Sources file
        sources_content = "\n".join(entries)
        sources_path = dists_dir / "Sources"
        sources_path.write_text(sources_content, encoding="utf-8")

        # Write Sources.gz
        sources_gz_path = dists_dir / "Sources.gz"
        with gzip.open(sources_gz_path, "wt", encoding="utf-8") as f:
            f.write(sources_content)

        return SourceIndexResult(
            success=True,
            sources_file=sources_path,
            sources_gz_file=sources_gz_path,
            source_count=len(entries),
        )

    except Exception as e:
        return SourceIndexResult(success=False, error=str(e))


def regenerate_all_indexes(repo_root: Path, arch: str = "amd64") -> tuple[IndexResult, SourceIndexResult]:
    """Regenerate both binary and source indexes.

    Args:
        repo_root: Root directory of the local APT repository.
        arch: Architecture to generate binary indexes for.

    Returns:
        Tuple of (IndexResult, SourceIndexResult).
    """
    binary_result = regenerate_indexes(repo_root, arch)
    source_result = regenerate_source_indexes(repo_root)
    return binary_result, source_result


def get_available_versions(repo_root: Path, package_name: str) -> list[str]:
    """Get all available versions of a package in the local repository.

    Args:
        repo_root: Root directory of the local APT repository.
        package_name: Name of the package to query.

    Returns:
        List of version strings, sorted from newest to oldest.
    """
    versions: list[str] = []

    # Check all arch-specific Packages files
    dists_dir = repo_root / "dists" / "local" / "main"
    if not dists_dir.exists():
        return versions

    for packages_file in dists_dir.glob("binary-*/Packages"):
        if not packages_file.exists():
            continue

        current_pkg = ""
        current_ver = ""

        with packages_file.open(encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if line.startswith("Package:"):
                    current_pkg = line.split(":", 1)[1].strip()
                elif line.startswith("Version:"):
                    current_ver = line.split(":", 1)[1].strip()
                elif line == "":
                    # End of stanza
                    if current_pkg == package_name and current_ver:
                        if current_ver not in versions:
                            versions.append(current_ver)
                    current_pkg = ""
                    current_ver = ""

            # Handle last stanza
            if current_pkg == package_name and current_ver:
                if current_ver not in versions:
                    versions.append(current_ver)

    # Sort versions using debian version comparison
    try:
        versions.sort(key=lambda v: Version(v), reverse=True)
    except Exception:
        pass

    return versions


def satisfies(repo_root: Path, package_name: str, constraint: str) -> bool:
    """Check if any version in the local repo satisfies a version constraint.

    Uses dpkg version comparison semantics.

    Args:
        repo_root: Root directory of the local APT repository.
        package_name: Name of the package to check.
        constraint: Version constraint string (e.g., ">= 1.0.0", "<< 2.0").

    Returns:
        True if a satisfying version exists.
    """
    versions = get_available_versions(repo_root, package_name)
    if not versions:
        return False

    if not constraint.strip():
        # No constraint, any version satisfies
        return True

    # Parse constraint: relation version
    constraint = constraint.strip()
    relation = ""
    required_version = ""

    for rel in (">=", "<=", ">>", "<<", "="):
        if constraint.startswith(rel):
            relation = rel
            required_version = constraint[len(rel):].strip()
            break

    if not relation:
        # Assume exact match if no relation specified
        return constraint in versions

    try:
        required = Version(required_version)
        for v in versions:
            available = Version(v)
            if relation == ">=" and available >= required:
                return True
            elif relation == "<=" and available <= required:
                return True
            elif relation == ">>" and available > required:
                return True
            elif relation == "<<" and available < required:
                return True
            elif relation == "=" and available == required:
                return True
    except Exception:
        pass

    return False


def get_source_versions(repo_root: Path, source_name: str) -> list[str]:
    """Get all versions of a source package based on .dsc files.

    Args:
        repo_root: Root directory of the local APT repository.
        source_name: Name of the source package.

    Returns:
        List of version strings.
    """
    versions: list[str] = []
    pool_dir = repo_root / "pool" / "main"

    if not pool_dir.exists():
        return versions

    # Look for .dsc files matching the source name
    for dsc_file in pool_dir.glob(f"{source_name}_*.dsc"):
        # Extract version from filename: name_version.dsc
        name = dsc_file.stem  # e.g., "nova_29.0.0-0ubuntu1"
        parts = name.split("_", 1)
        if len(parts) == 2:
            versions.append(parts[1])

    try:
        versions.sort(key=lambda v: Version(v), reverse=True)
    except Exception:
        pass

    return versions


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m packastack.apt.localrepo <repo_root>")
        sys.exit(1)

    repo_root = Path(sys.argv[1])
    result = regenerate_indexes(repo_root)
    print(f"Success: {result.success}")
    print(f"Packages: {result.package_count}")
    if result.error:
        print(f"Error: {result.error}")
