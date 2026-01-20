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

"""debian/watch file parser, mismatch detection, and uscan integration.

This module provides heuristic parsing of debian/watch files to detect
the upstream source type and compare it against the registry configuration.
Mismatches are reported as warnings only and never fail the build.

Additionally provides uscan integration for upstream version detection
during the planning phase, using DEHS (Debian External Health Status)
XML output to discover available upstream versions without downloading.
"""

from __future__ import annotations

import json
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any


class DetectedWatchMode(Enum):
    """Detected upstream source type from debian/watch."""

    OPENSTACK_TARBALL = "openstack_tarball"
    PYPI = "pypi"
    GITHUB_RELEASE = "github_release"
    GITHUB_TAGS = "github_tags"
    GITLAB_RELEASE = "gitlab_release"
    GIT_TAGS = "git_tags"
    UNKNOWN = "unknown"


class UscanStatus(Enum):
    """Status of uscan execution."""

    SUCCESS = "success"  # uscan ran successfully
    NEWER_AVAILABLE = "newer_available"  # Upstream has newer version
    UP_TO_DATE = "up_to_date"  # Package is up to date
    NO_WATCH = "no_watch"  # No debian/watch file
    PARSE_ERROR = "parse_error"  # Watch file could not be parsed
    NETWORK_ERROR = "network_error"  # Network request failed
    TIMEOUT = "timeout"  # uscan timed out
    NOT_INSTALLED = "not_installed"  # uscan not available
    ERROR = "error"  # Other error


@dataclass
class WatchParseResult:
    """Result of parsing a debian/watch file."""

    mode: DetectedWatchMode
    base_url: str = ""
    version_pattern: str = ""
    raw_content: str = ""
    parse_error: str = ""


@dataclass
class WatchMismatchWarning:
    """Warning about a mismatch between watch and registry."""

    package: str
    watch_mode: DetectedWatchMode
    watch_url: str
    registry_mode: str
    registry_url: str
    message: str


@dataclass
class UscanResult:
    """Result of running uscan --dehs for version detection.

    This captures the output from uscan's DEHS (Debian External Health Status)
    XML format, which provides structured information about upstream versions
    without actually downloading tarballs.
    """

    success: bool
    status: UscanStatus
    upstream_version: str = ""
    upstream_url: str = ""
    debian_version: str = ""  # Current packaged version from d/changelog
    debian_upstream_version: str = ""  # Upstream portion of debian version
    newer_available: bool = False
    error: str = ""
    dehs_xml: str = ""  # Raw DEHS XML for debugging
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "success": self.success,
            "status": self.status.value,
            "upstream_version": self.upstream_version,
            "upstream_url": self.upstream_url,
            "debian_version": self.debian_version,
            "debian_upstream_version": self.debian_upstream_version,
            "newer_available": self.newer_available,
            "error": self.error,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UscanResult:
        """Create from dictionary."""
        return cls(
            success=data.get("success", False),
            status=UscanStatus(data.get("status", "error")),
            upstream_version=data.get("upstream_version", ""),
            upstream_url=data.get("upstream_url", ""),
            debian_version=data.get("debian_version", ""),
            debian_upstream_version=data.get("debian_upstream_version", ""),
            newer_available=data.get("newer_available", False),
            error=data.get("error", ""),
            warnings=data.get("warnings", []),
        )


@dataclass
class UscanCacheEntry:
    """Cache entry for uscan results."""

    source_package: str
    result: UscanResult
    cached_at_utc: str
    packaging_repo_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "source_package": self.source_package,
            "result": self.result.to_dict(),
            "cached_at_utc": self.cached_at_utc,
            "packaging_repo_path": self.packaging_repo_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UscanCacheEntry:
        """Create from dictionary."""
        return cls(
            source_package=data["source_package"],
            result=UscanResult.from_dict(data["result"]),
            cached_at_utc=data.get("cached_at_utc", ""),
            packaging_repo_path=data.get("packaging_repo_path", ""),
        )


# Patterns for detecting upstream source types
PATTERNS = {
    # OpenStack tarballs: https://tarballs.opendev.org/openstack/...
    "openstack_tarball": [
        re.compile(r"tarballs\.opendev\.org/openstack/", re.IGNORECASE),
        re.compile(r"tarballs\.openstack\.org/", re.IGNORECASE),
    ],
    # PyPI: various pypi watch patterns
    "pypi": [
        re.compile(r"pypi\.debian\.net/", re.IGNORECASE),
        re.compile(r"pypi\.org/", re.IGNORECASE),
        re.compile(r"pypi\.python\.org/", re.IGNORECASE),
        re.compile(r"files\.pythonhosted\.org/", re.IGNORECASE),
    ],
    # GitHub releases
    "github_release": [
        re.compile(r"github\.com/[^/]+/[^/]+/releases", re.IGNORECASE),
        re.compile(r"github\.com/[^/]+/[^/]+/archive/refs/tags", re.IGNORECASE),
    ],
    # GitHub tags (older style)
    "github_tags": [
        re.compile(r"github\.com/[^/]+/[^/]+/tags", re.IGNORECASE),
    ],
    # GitLab
    "gitlab_release": [
        re.compile(r"gitlab\.com/[^/]+/[^/]+/-/archive", re.IGNORECASE),
    ],
}

# Pattern to extract base URL from watch file
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

# Pattern to detect version= lines
VERSION_LINE_PATTERN = re.compile(r"^version\s*=\s*(\d+)", re.MULTILINE | re.IGNORECASE)


def upgrade_watch_version(watch_path: Path) -> bool:
    """Ensure debian/watch declares version=4 without altering rules.

    Returns True if the file was modified, False otherwise.
    """

    if not watch_path.exists():
        return False

    try:
        content = watch_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    # Already at version 4
    if re.search(r"^version\s*=\s*4\b", content, re.IGNORECASE | re.MULTILINE):
        return False

    if VERSION_LINE_PATTERN.search(content):
        updated = VERSION_LINE_PATTERN.sub("version=4", content)
    else:
        # Prepend version line; strip leading blank lines to keep header tight
        updated = "version=4\n" + content.lstrip("\n")

    try:
        watch_path.write_text(updated, encoding="utf-8")
        return True
    except OSError:
        return False


def fix_oslo_watch_pattern(watch_path: Path, project_name: str) -> bool:
    """Update watch file to accept both oslo.* and oslo_* tarball naming.

    OpenStack changed Oslo library tarball naming from dots to underscores.
    This updates watch file patterns to match both formats for compatibility.
    Also updates tarballs.openstack.org to tarballs.opendev.org.

    Note: The directory path on tarballs.opendev.org still uses dots (oslo.i18n/)
    but the tarball filenames use underscores (oslo_i18n-6.7.1.tar.gz).

    Args:
        watch_path: Path to debian/watch file.
        project_name: Project name (e.g., "oslo.config").

    Returns:
        True if watch file was modified, False otherwise.
    """
    if not watch_path.exists():
        return False

    if not project_name.startswith("oslo."):
        return False

    try:
        content = watch_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    # Check if already updated
    flexible_pattern = project_name.replace(".", "[._]")
    already_updated = (
        "[._]" in content and
        "tarballs.opendev.org" in content
    )
    if already_updated:
        return False

    # For watch version 4, we need to update:
    # 1. The domain from openstack.org to opendev.org
    # 2. The filename pattern to accept both . and _ with regex character class
    # Note: The URL directory path keeps dots (that's how opendev.org structures it)

    modified = False
    lines = content.splitlines(keepends=True)
    updated_lines = []

    for line in lines:
        updated_line = line

        # Skip comments and version lines
        if line.strip().startswith("#") or line.strip().startswith("version="):
            updated_lines.append(updated_line)
            continue

        # Fix domain name openstack.org -> opendev.org
        # Also add /openstack/ path if missing (new URL structure)
        if "tarballs.openstack.org" in line:
            updated_line = updated_line.replace("tarballs.openstack.org", "tarballs.opendev.org/openstack")
            modified = True
        elif "tarballs.opendev.org" in line and "/openstack/" not in line:
            # Add /openstack/ path if using opendev.org but missing the path
            updated_line = updated_line.replace("tarballs.opendev.org/", "tarballs.opendev.org/openstack/")
            modified = True

        # Check if this line contains the project name for pattern update
        if project_name in updated_line:
            # Split to find URL and filename pattern
            parts = updated_line.split()
            if len(parts) >= 2:
                # Last part is usually the filename pattern
                filename_pattern = parts[-1]

                # Update filename pattern to accept both . and _
                # Only modify the pattern part, not the URL
                if project_name in filename_pattern:
                    # Replace project.name with project[._]name in the pattern
                    filename_pattern = filename_pattern.replace(project_name, flexible_pattern)
                    parts[-1] = filename_pattern
                    updated_line = " ".join(parts)
                    if not updated_line.endswith("\n") and line.endswith("\n"):
                        updated_line += "\n"
                    modified = True

        updated_lines.append(updated_line)

    if not modified:
        return False

    try:
        watch_path.write_text("".join(updated_lines), encoding="utf-8")
        return True
    except OSError:
        return False


def parse_watch_file(watch_path: Path) -> WatchParseResult:
    """Parse a debian/watch file to detect upstream source type.

    Args:
        watch_path: Path to the debian/watch file.

    Returns:
        WatchParseResult with detected mode and details.
    """
    if not watch_path.exists():
        return WatchParseResult(
            mode=DetectedWatchMode.UNKNOWN,
            parse_error="debian/watch file not found",
        )

    try:
        content = watch_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return WatchParseResult(
            mode=DetectedWatchMode.UNKNOWN,
            parse_error=f"Failed to read watch file: {e}",
        )

    return parse_watch_content(content)


def parse_watch_content(content: str) -> WatchParseResult:
    """Parse debian/watch content to detect upstream source type.

    Args:
        content: Contents of the debian/watch file.

    Returns:
        WatchParseResult with detected mode and details.
    """
    if not content.strip():
        return WatchParseResult(
            mode=DetectedWatchMode.UNKNOWN,
            raw_content=content,
            parse_error="Empty watch file",
        )

    # Extract watch file version
    version_match = VERSION_LINE_PATTERN.search(content)
    version = int(version_match.group(1)) if version_match else 0

    # Find all URLs in the content
    urls = URL_PATTERN.findall(content)
    base_url = urls[0] if urls else ""

    # Detect mode based on content patterns
    detected_mode = DetectedWatchMode.UNKNOWN

    for mode_name, patterns in PATTERNS.items():
        for pattern in patterns:
            if pattern.search(content):
                detected_mode = DetectedWatchMode(mode_name)
                break
        if detected_mode != DetectedWatchMode.UNKNOWN:
            break

    return WatchParseResult(
        mode=detected_mode,
        base_url=base_url,
        version_pattern=f"version={version}" if version else "",
        raw_content=content,
    )


def check_watch_mismatch(
    package: str,
    watch_result: WatchParseResult,
    registry_host: str,
    registry_url: str,
) -> WatchMismatchWarning | None:
    """Check for mismatch between detected watch mode and registry.

    This is a heuristic check and may produce false positives.
    Mismatches are warnings only and never fail the build.

    Args:
        package: Package name.
        watch_result: Parsed watch file result.
        registry_host: Registry upstream host (opendev, github, etc).
        registry_url: Registry upstream URL.

    Returns:
        WatchMismatchWarning if mismatch detected, None otherwise.
    """
    if watch_result.mode == DetectedWatchMode.UNKNOWN:
        # Can't detect watch mode, no mismatch to report
        return None

    # Build expected watch mode from registry
    expected_modes: set[DetectedWatchMode] = set()

    if registry_host == "opendev":
        expected_modes.add(DetectedWatchMode.OPENSTACK_TARBALL)
        # PyPI is also acceptable for OpenDev projects
        expected_modes.add(DetectedWatchMode.PYPI)
    elif registry_host == "github":
        expected_modes.add(DetectedWatchMode.GITHUB_RELEASE)
        expected_modes.add(DetectedWatchMode.GITHUB_TAGS)
        expected_modes.add(DetectedWatchMode.PYPI)
    elif registry_host == "gitlab":
        expected_modes.add(DetectedWatchMode.GITLAB_RELEASE)
        expected_modes.add(DetectedWatchMode.PYPI)
    else:
        # Unknown host, accept anything
        return None

    if watch_result.mode in expected_modes:
        # No mismatch
        return None

    # Generate warning message
    message = (
        f"debian/watch suggests {watch_result.mode.value} but registry "
        f"expects {registry_host}"
    )

    return WatchMismatchWarning(
        package=package,
        watch_mode=watch_result.mode,
        watch_url=watch_result.base_url,
        registry_mode=registry_host,
        registry_url=registry_url,
        message=message,
    )


def format_mismatch_warning(warning: WatchMismatchWarning) -> str:
    """Format a mismatch warning for display.

    Args:
        warning: The mismatch warning.

    Returns:
        Formatted warning string.
    """
    lines = [
        f"debian/watch mismatch (warn) for {warning.package}:",
        f"  registry upstream: {warning.registry_url}",
        f"  debian/watch suggests: {warning.watch_mode.value}",
    ]
    if warning.watch_url:
        lines.append(f"  debian/watch URL: {warning.watch_url}")
    return "\n".join(lines)


# Patterns for PGP signature options in watch files
# Match pgpsigurlmangle=value or pgpmode=value with optional leading comma
PGP_OPTION_PATTERN = re.compile(
    r",?\s*(?:pgpsigurlmangle|pgpmode)\s*=\s*[^,\s\\]+",
    re.IGNORECASE,
)


def has_pgp_verification(watch_path: Path) -> bool:
    """Check if debian/watch has PGP signature verification configured.

    Args:
        watch_path: Path to the debian/watch file.

    Returns:
        True if PGP verification options are present.
    """
    if not watch_path.exists():
        return False

    try:
        content = watch_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    return bool(
        re.search(r"pgpsigurlmangle", content, re.IGNORECASE)
        or re.search(r"pgpmode", content, re.IGNORECASE)
    )


def has_upstream_signing_key(debian_dir: Path) -> bool:
    """Check if upstream signing key exists in debian directory.

    Args:
        debian_dir: Path to the debian directory.

    Returns:
        True if upstream-signing-key.asc or similar exists.
    """
    key_patterns = [
        "upstream-signing-key.asc",
        "upstream-signing-key.pgp",
        "upstream/signing-key.asc",
        "upstream/signing-key.pgp",
    ]

    return any((debian_dir / pattern).exists() for pattern in key_patterns)


def remove_pgp_options_from_watch(watch_path: Path) -> bool:
    """Remove PGP verification options from debian/watch.

    This is used for snapshot builds where we cannot verify signatures.

    Args:
        watch_path: Path to the debian/watch file.

    Returns:
        True if the file was modified.
    """
    if not watch_path.exists():
        return False

    try:
        content = watch_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    original = content

    # Remove pgpsigurlmangle and pgpmode options directly from content
    # Watch files use opts=key=value,key=value format (no parentheses)
    content = PGP_OPTION_PATTERN.sub("", content)

    # Clean up empty opts= declarations that might result
    # Match opts= followed by only whitespace/backslash/newline before URL
    content = re.sub(r"opts\s*=\s*(?=\\|\s*https?://|\s*\n)", "", content)

    # Clean up leading commas in remaining opts
    content = re.sub(r"(opts\s*=\s*),+\s*", r"\1", content)

    # Clean up trailing commas before backslash continuation
    content = re.sub(r",+\s*(\\)", r" \1", content)

    # Clean up multiple spaces
    content = re.sub(r"  +", " ", content)

    if content != original:
        try:
            watch_path.write_text(content, encoding="utf-8")
            return True
        except OSError:
            return False

    return False


def ensure_pgp_verification_valid(debian_dir: Path) -> tuple[bool, str]:
    """Ensure PGP verification in watch file is valid.

    If watch file has PGP options but no signing key exists,
    removes the PGP options to avoid lintian errors.

    Args:
        debian_dir: Path to the debian directory.

    Returns:
        Tuple of (modified, message) where modified indicates if changes were made.
    """
    watch_path = debian_dir / "watch"

    if not has_pgp_verification(watch_path):
        return False, ""

    if has_upstream_signing_key(debian_dir):
        return False, "PGP verification configured with valid signing key"

    # Has PGP options but no key - remove the options
    if remove_pgp_options_from_watch(watch_path):
        return True, "Removed PGP options from watch file (no signing key available)"

    return False, ""


# =============================================================================
# uscan Integration for Upstream Version Detection
# =============================================================================


def parse_dehs_output(dehs_xml: str) -> UscanResult:
    """Parse DEHS XML output from uscan.

    DEHS (Debian External Health Status) is an XML format that uscan uses
    to report upstream version information in a structured way.

    Example DEHS output:
    ```xml
    <dehs>
      <package>alembic</package>
      <debian-uversion>1.13.1</debian-uversion>
      <debian-mangled-uversion>1.13.1</debian-mangled-uversion>
      <upstream-version>1.14.0</upstream-version>
      <upstream-url>https://...</upstream-url>
      <status>newer package available</status>
    </dehs>
    ```

    Args:
        dehs_xml: Raw DEHS XML string from uscan.

    Returns:
        UscanResult with parsed information.
    """
    if not dehs_xml.strip():
        return UscanResult(
            success=False,
            status=UscanStatus.PARSE_ERROR,
            error="Empty DEHS output",
            dehs_xml=dehs_xml,
        )

    try:
        # Parse XML
        root = ET.fromstring(dehs_xml)

        # Extract fields
        _get_xml_text(root, "package", "")
        debian_uversion = _get_xml_text(root, "debian-uversion", "")
        debian_mangled = _get_xml_text(root, "debian-mangled-uversion", "")
        upstream_version = _get_xml_text(root, "upstream-version", "")
        upstream_url = _get_xml_text(root, "upstream-url", "")
        status_text = _get_xml_text(root, "status", "").lower()

        # Collect warnings
        warnings: list[str] = []
        for warn_elem in root.findall(".//warnings"):
            if warn_elem.text:
                warnings.append(warn_elem.text.strip())

        # Check for errors in DEHS output
        errors_elem = root.find("errors")
        if errors_elem is not None and errors_elem.text:
            return UscanResult(
                success=False,
                status=UscanStatus.ERROR,
                error=errors_elem.text.strip(),
                dehs_xml=dehs_xml,
                warnings=warnings,
            )

        # Determine status
        newer_available = "newer" in status_text or upstream_version != debian_uversion
        if "up to date" in status_text or "up-to-date" in status_text:
            uscan_status = UscanStatus.UP_TO_DATE
            newer_available = False
        elif newer_available:
            uscan_status = UscanStatus.NEWER_AVAILABLE
        else:
            uscan_status = UscanStatus.SUCCESS

        return UscanResult(
            success=True,
            status=uscan_status,
            upstream_version=upstream_version,
            upstream_url=upstream_url,
            debian_version="",  # Full debian version not in DEHS
            debian_upstream_version=debian_uversion or debian_mangled,
            newer_available=newer_available,
            dehs_xml=dehs_xml,
            warnings=warnings,
        )

    except ET.ParseError as e:
        return UscanResult(
            success=False,
            status=UscanStatus.PARSE_ERROR,
            error=f"Failed to parse DEHS XML: {e}",
            dehs_xml=dehs_xml,
        )


def _get_xml_text(root: ET.Element, tag: str, default: str = "") -> str:
    """Get text content of an XML element safely."""
    elem = root.find(tag)
    if elem is not None and elem.text:
        return elem.text.strip()
    return default


def run_uscan_dehs(
    packaging_repo: Path,
    timeout_seconds: int = 30,
) -> UscanResult:
    """Run uscan with DEHS output to check upstream version.

    Uses `uscan --dehs --report --safe` to check for upstream versions
    without downloading any files. The --safe flag prevents uscan from
    following redirects to untrusted hosts.

    Args:
        packaging_repo: Path to the packaging repository (containing debian/).
        timeout_seconds: Timeout for uscan execution.

    Returns:
        UscanResult with version information or error details.
    """
    watch_path = packaging_repo / "debian" / "watch"
    if not watch_path.exists():
        return UscanResult(
            success=False,
            status=UscanStatus.NO_WATCH,
            error="No debian/watch file",
        )

    try:
        result = subprocess.run(
            [
                "uscan",
                "--dehs",  # Output DEHS XML format
                "--report",  # Report only, don't download
                "--safe",  # Don't follow redirects to untrusted hosts
                "--no-download",  # Extra safety: don't download anything
            ],
            cwd=packaging_repo,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )

        # uscan returns various exit codes:
        # 0 = up to date
        # 1 = newer version available
        # Other codes indicate errors

        dehs_output = result.stdout
        stderr = result.stderr

        # Check for common errors in stderr
        if "uscan: Unable to determine" in stderr:
            return UscanResult(
                success=False,
                status=UscanStatus.PARSE_ERROR,
                error=f"Watch file parse error: {stderr.strip()}",
                dehs_xml=dehs_output,
            )

        if "Unable to connect" in stderr or "Connection refused" in stderr:
            return UscanResult(
                success=False,
                status=UscanStatus.NETWORK_ERROR,
                error=f"Network error: {stderr.strip()}",
                dehs_xml=dehs_output,
            )

        # Parse DEHS output
        if dehs_output.strip():
            parsed = parse_dehs_output(dehs_output)
            # Preserve stderr warnings even on success
            if stderr.strip() and not parsed.warnings:
                parsed.warnings.append(stderr.strip())
            return parsed

        # No DEHS output but no error either
        if result.returncode == 0:
            return UscanResult(
                success=True,
                status=UscanStatus.UP_TO_DATE,
                error="",
            )

        return UscanResult(
            success=False,
            status=UscanStatus.ERROR,
            error=stderr.strip() or f"uscan exited with code {result.returncode}",
        )

    except FileNotFoundError:
        return UscanResult(
            success=False,
            status=UscanStatus.NOT_INSTALLED,
            error="uscan not installed (devscripts package)",
        )
    except subprocess.TimeoutExpired:
        return UscanResult(
            success=False,
            status=UscanStatus.TIMEOUT,
            error=f"uscan timed out after {timeout_seconds}s",
        )
    except Exception as e:
        return UscanResult(
            success=False,
            status=UscanStatus.ERROR,
            error=str(e),
        )


def load_uscan_cache(cache_path: Path) -> dict[str, UscanCacheEntry]:
    """Load uscan cache from JSON file.

    Args:
        cache_path: Path to the cache JSON file.

    Returns:
        Dictionary mapping source package names to cache entries.
    """
    if not cache_path.exists():
        return {}

    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        cache: dict[str, UscanCacheEntry] = {}
        for pkg_name, entry_data in data.items():
            try:
                cache[pkg_name] = UscanCacheEntry.from_dict(entry_data)
            except (KeyError, ValueError):
                # Skip invalid entries
                continue
        return cache
    except (json.JSONDecodeError, OSError):
        return {}


def save_uscan_cache(cache: dict[str, UscanCacheEntry], cache_path: Path) -> bool:
    """Save uscan cache to JSON file.

    Args:
        cache: Dictionary of cache entries.
        cache_path: Path to the cache JSON file.

    Returns:
        True if save was successful.
    """
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        data = {pkg: entry.to_dict() for pkg, entry in cache.items()}
        cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def get_cached_uscan_result(
    source_package: str,
    cache: dict[str, UscanCacheEntry],
) -> UscanResult | None:
    """Get cached uscan result if available.

    Args:
        source_package: Source package name.
        cache: Loaded cache dictionary.

    Returns:
        Cached UscanResult or None if not cached.
    """
    entry = cache.get(source_package)
    if entry:
        return entry.result
    return None


def cache_uscan_result(
    source_package: str,
    result: UscanResult,
    cache: dict[str, UscanCacheEntry],
    packaging_repo_path: str = "",
) -> None:
    """Add uscan result to cache.

    Args:
        source_package: Source package name.
        result: UscanResult to cache.
        cache: Cache dictionary to update.
        packaging_repo_path: Path to packaging repo for reference.
    """
    cache[source_package] = UscanCacheEntry(
        source_package=source_package,
        result=result,
        cached_at_utc=datetime.now(UTC).isoformat(),
        packaging_repo_path=packaging_repo_path,
    )


def update_signing_key(pkg_repo: Path, releases_repo: Path, series: str, is_snapshot: bool = False) -> bool:
    """Update or remove debian/upstream/signing-key.asc based on build type.

    For snapshot builds, removes the signing key file since snapshots are unsigned.
    For release builds, updates the signing key for the current series.

    Args:
        pkg_repo: Path to the package repository.
        releases_repo: Path to the openstack-releases repository.
        series: OpenStack series name (e.g., "2026.1", "gazpacho").
        is_snapshot: True for snapshot builds (removes key), False for releases (updates key).

    Returns:
        True if signing key was updated or removed, False otherwise.
    """
    signing_key_path = pkg_repo / "debian" / "upstream" / "signing-key.asc"

    # For snapshot builds, remove the signing key if it exists
    if is_snapshot:
        if signing_key_path.exists():
            try:
                signing_key_path.unlink()
                return True
            except OSError:
                return False
        return False

    # For release builds, update the signing key
    index_path = releases_repo / "doc" / "source" / "index.rst"
    if not index_path.exists():
        return False

    try:
        content = index_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    # Parse the index.rst to find the signing key for the series
    # Format is multi-line:
    # * 2025-10-06..present (2026.1/Gazpacho Cycle key):
    #   `key 0x<keyid>`_
    # We look for the "present" line (current key) or the specific series

    # Normalize series for matching (handle both "2026.1" and "gazpacho" forms)
    series_lower = series.lower()

    key_id = None
    lines = content.splitlines()
    for i, line in enumerate(lines):
        # Check if this is a cycle key line for the current series or the "present" (current) key
        if "Cycle key" in line and ("present" in line or series in line or series_lower in line):
            # The key ID is on the next line - look for it
            for j in range(i + 1, min(i + 3, len(lines))):  # Check next 2 lines
                match = re.search(r'`key (0x[0-9a-fA-F]+)`_', lines[j])
                if match:
                    key_id = match.group(1)
                    break
            if key_id:
                break

    if not key_id:
        return False

    # Construct the key file path directly - the naming convention is consistent:
    # Key ID 0x<hex> maps to _static/0x<hex>.txt or static/0x<hex>.txt
    # Note: The RST reference may span multiple lines, so we use direct path construction
    key_file_path = releases_repo / "doc" / "source" / "_static" / f"{key_id}.txt"
    if not key_file_path.exists():
        # Try without underscore prefix
        key_file_path = releases_repo / "doc" / "source" / "static" / f"{key_id}.txt"

    if not key_file_path.exists():
        return False

    # Copy the key file to debian/upstream/signing-key.asc
    signing_key_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        key_content = key_file_path.read_text(encoding="utf-8", errors="replace")
        signing_key_path.write_text(key_content, encoding="utf-8")
        return True
    except OSError:
        return False
