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

"""Debian changelog manipulation for Packastack build operations.

Handles version string generation and changelog updates using python-debian.
"""

from __future__ import annotations

import datetime
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Import python-debian's Changelog class
try:
    from debian.changelog import Changelog, Version
except ImportError:
    Changelog = None  # type: ignore
    Version = None  # type: ignore


@dataclass
class VersionInfo:
    """Parsed Debian version information."""

    epoch: int
    upstream: str
    debian: str

    def __str__(self) -> str:
        if self.epoch:
            return f"{self.epoch}:{self.upstream}-{self.debian}"
        return f"{self.upstream}-{self.debian}"


def parse_version(version_str: str) -> VersionInfo:
    """Parse a Debian version string into components.

    Args:
        version_str: Full version string (e.g., "1:29.0.0-0ubuntu1").

    Returns:
        VersionInfo with parsed components.
    """
    epoch = 0
    upstream = version_str
    debian = ""

    # Extract epoch
    if ":" in version_str:
        epoch_str, rest = version_str.split(":", 1)
        epoch = int(epoch_str)
        upstream = rest
    else:
        upstream = version_str

    # Extract debian revision
    if "-" in upstream:
        # Find the last hyphen (debian revision separator)
        idx = upstream.rfind("-")
        debian = upstream[idx + 1 :]
        upstream = upstream[:idx]

    return VersionInfo(epoch=epoch, upstream=upstream, debian=debian)


def generate_release_version(
    upstream_version: str,
    ubuntu_revision: int = 1,
    epoch: int = 0,
) -> str:
    """Generate version string for a release build.

    Format: [epoch:]<version>-0ubuntu<N>

    Args:
        upstream_version: Upstream version (e.g., "29.0.0").
        ubuntu_revision: Ubuntu package revision number.
        epoch: Debian epoch (prepended as epoch:version if non-zero).

    Returns:
        Full Debian version string.
    """
    version = f"{upstream_version}-0ubuntu{ubuntu_revision}"
    if epoch:
        return f"{epoch}:{version}"
    return version


def generate_snapshot_version(
    next_version: str,
    git_date: str,
    git_sha: str,
    ubuntu_revision: int = 1,
    epoch: int = 0,
) -> str:
    """Generate version string for a snapshot build.

    Format: [epoch:]<next_version>~git<YYYYMMDD>.<sha>-0ubuntu<N>

    Args:
        next_version: Expected next upstream version (e.g., "30.0.0").
        git_date: Commit date in YYYYMMDD format.
        git_sha: Short git SHA (7 characters).
        ubuntu_revision: Ubuntu package revision number.
        epoch: Debian epoch (prepended as epoch:version if non-zero).

    Returns:
        Full Debian version string.
    """
    version = f"{next_version}~git{git_date}.{git_sha}-0ubuntu{ubuntu_revision}"
    if epoch:
        return f"{epoch}:{version}"
    return version


def generate_milestone_version(
    base_version: str,
    milestone: str,
    ubuntu_revision: int = 1,
    epoch: int = 0,
) -> str:
    """Generate version string for a milestone build.

    Format: [epoch:]<version>~<milestone>-0ubuntu<N>
    Where milestone is like "b1", "b2", "rc1", "rc2".

    Args:
        base_version: Base upstream version (e.g., "30.0.0").
        milestone: Milestone identifier (e.g., "b1", "rc1").
        ubuntu_revision: Ubuntu package revision number.
        epoch: Debian epoch (prepended as epoch:version if non-zero).

    Returns:
        Full Debian version string.
    """
    # Normalize milestone format
    milestone = milestone.lower()
    if not milestone.startswith(("b", "rc")):
        milestone = f"b{milestone}"

    version = f"{base_version}~{milestone}-0ubuntu{ubuntu_revision}"
    if epoch:
        return f"{epoch}:{version}"
    return version


def increment_upstream_version(version: str) -> str:
    """Increment the last numeric component of an upstream version.

    Used to estimate the next version for snapshot builds.

    Args:
        version: Current upstream version (e.g., "29.0.0").

    Returns:
        Incremented version (e.g., "30.0.0").
    """
    # Split into parts
    parts = version.split(".")
    if not parts:
        return version

    # Find and increment the first numeric part (major version)
    for i, part in enumerate(parts):
        if part.isdigit():
            parts[i] = str(int(part) + 1)
            # Reset subsequent numeric parts to 0
            for j in range(i + 1, len(parts)):
                if parts[j].isdigit():
                    parts[j] = "0"
            break

    return ".".join(parts)


def get_current_version(changelog_path: Path) -> str | None:
    """Get the current version from debian/changelog.

    Args:
        changelog_path: Path to debian/changelog.

    Returns:
        Current version string, or None if not found.
    """
    if not changelog_path.exists():
        return None

    if Changelog is None:
        # Fallback: parse first line manually
        with changelog_path.open(encoding="utf-8") as f:
            first_line = f.readline()
        match = re.match(r"^[^\s]+\s+\(([^)]+)\)", first_line)
        if match:
            return match.group(1)
        return None

    with changelog_path.open(encoding="utf-8") as f:
        cl = Changelog(f)
        if cl.version:
            return str(cl.version)
    return None


def update_changelog(
    changelog_path: Path,
    package: str,
    version: str,
    distribution: str,
    changes: list[str],
    maintainer: str | None = None,
    urgency: str = "medium",
    prefer_gbp: bool = False,
) -> bool:
    """Update debian/changelog with a new entry.

    Args:
        changelog_path: Path to debian/changelog.
        package: Source package name.
        version: New version string.
        distribution: Target distribution (e.g., "noble", "UNRELEASED").
        changes: List of changelog entry lines.
        maintainer: Maintainer name and email (default: from environment).
        urgency: Package urgency level.

    Returns:
        True if update succeeded.
    """
    def _detect_existing_maintainer() -> str | None:
        # Prefer the maintainer from the current top changelog entry to avoid
        # introducing inconsistent-maintainer lintian errors.
        if not changelog_path.exists():
            return None

        if Changelog:
            try:
                with changelog_path.open(encoding="utf-8") as f:
                    cl = Changelog(f)
                    if cl and cl[0].author:
                        return str(cl[0].author)
            except Exception:
                pass

        # Fallback: parse the first author line manually (" -- Name <email>  date")
        try:
            with changelog_path.open(encoding="utf-8") as f:
                for line in f:
                    if line.startswith(" -- "):
                        author = line[4:].strip()
                        if "  " in author:
                            author = author.split("  ", 1)[0]
                        return author
        except Exception:
            return None

        return None

    # Determine maintainer
    if maintainer is None:
        maintainer = _detect_existing_maintainer()
        if maintainer is None:
            name = os.environ.get("DEBFULLNAME", os.environ.get("NAME", "Packastack"))
            email = os.environ.get("DEBEMAIL", os.environ.get("EMAIL", "packastack@ubuntu.com"))
            maintainer = f"{name} <{email}>"

    # Debug logging
    import sys
    print(f"[update_changelog] maintainer={maintainer}", file=sys.stderr)
    print(f"[update_changelog] prefer_gbp={prefer_gbp}", file=sys.stderr)

    if prefer_gbp and _update_changelog_gbp_dch(
        changelog_path, version, distribution, changes, maintainer, urgency
    ):
        return True

    if Changelog is not None:
        return _update_changelog_python_debian(
            changelog_path, package, version, distribution, changes, maintainer, urgency
        )
    else:
        return _update_changelog_dch(
            changelog_path, package, version, distribution, changes, maintainer, urgency
        )


def _update_changelog_gbp_dch(
    changelog_path: Path,
    version: str,
    distribution: str,
    changes: list[str],
    maintainer: str,
    urgency: str,
) -> bool:
    """Update changelog using gbp dch, appending custom change lines.

    gbp dch creates the stanza and handles version/distribution wiring; we
    then append our provided change lines with dch for consistency.
    """
    import sys

    repo_root = changelog_path.parent.parent

    env = os.environ.copy()
    match = re.match(r"^(.+)\s+<(.+)>$", maintainer)
    if match:
        env["DEBFULLNAME"] = match.group(1)
        env["DEBEMAIL"] = match.group(2)

    # Debug logging
    print(f"[changelog-debug] maintainer={maintainer}", file=sys.stderr)
    print(f"[changelog-debug] DEBFULLNAME in env={env.get('DEBFULLNAME')}", file=sys.stderr)
    print(f"[changelog-debug] DEBEMAIL in env={env.get('DEBEMAIL')}", file=sys.stderr)
    print(f"[changelog-debug] changes to append={changes}", file=sys.stderr)

    try:
        cmd = [
            "gbp",
            "dch",
            "--git-author",
            "--spawn-editor=never",
            "--force-distribution",
            "--distribution",
            distribution,
            "--urgency",
            urgency,
            "--new-version",
            version,
        ]

        result = subprocess.run(
            cmd,
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            return False

        # Add the custom changes (like "New upstream release") using dch
        # Use --maintmaint to use the specified maintainer instead of
        # preserving previous maintainer
        for change in changes:
            append_cmd = [
                "dch",
                "--maintmaint",
                "--append",
                "--",
                change
            ]
            append_result = subprocess.run(
                append_cmd,
                cwd=repo_root,
                env=env,
                capture_output=True,
                text=True,
            )
            if append_result.returncode != 0:
                return False

        return True
    except Exception:
        return False


def _update_changelog_python_debian(
    changelog_path: Path,
    package: str,
    version: str,
    distribution: str,
    changes: list[str],
    maintainer: str,
    urgency: str,
) -> bool:
    """Update changelog using python-debian library."""
    try:
        # Read existing changelog
        if changelog_path.exists():
            with changelog_path.open(encoding="utf-8") as f:
                cl = Changelog(f)
        else:
            cl = Changelog()

        # Create new block
        cl.new_block(
            package=package,
            version=Version(version),
            distributions=distribution,
            urgency=urgency,
            author=maintainer,
            date=datetime.datetime.now(datetime.UTC).strftime("%a, %d %b %Y %H:%M:%S %z"),
        )

        # Add changes
        for change in changes:
            cl.add_change(f"  * {change}")

        # Write back
        with changelog_path.open("w", encoding="utf-8") as f:
            cl.write_to_open_file(f)

        return True
    except Exception:
        return False


def _update_changelog_dch(
    changelog_path: Path,
    package: str,
    version: str,
    distribution: str,
    changes: list[str],
    maintainer: str,
    urgency: str,
) -> bool:
    """Update changelog using dch command."""
    try:
        # Use dch to create new version
        cmd = [
            "dch",
            "--newversion",
            version,
            "--distribution",
            distribution,
            "--urgency",
            urgency,
            "--",
            changes[0] if changes else "New upstream version",
        ]

        env = os.environ.copy()
        # Parse maintainer for dch
        match = re.match(r"^(.+)\s+<(.+)>$", maintainer)
        if match:
            env["DEBFULLNAME"] = match.group(1)
            env["DEBEMAIL"] = match.group(2)

        result = subprocess.run(
            cmd,
            cwd=changelog_path.parent.parent,  # Run from package root
            env=env,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            return False

        # Add additional changes
        for change in changes[1:]:
            subprocess.run(
                ["dch", "--append", "--", change],
                cwd=changelog_path.parent.parent,
                env=env,
                capture_output=True,
            )

        return True
    except Exception:
        return False


def generate_changelog_message(
    build_type: str,
    upstream_version: str,
    git_ref: str = "",
    signature_verified: bool = False,
    signature_warning: str = "",
    lp_bug: int | None = None,
    openstack_series: str | None = None,
) -> list[str]:
    """Generate changelog entry messages for a build.

    Args:
        build_type: Type of build (release, snapshot, milestone).
        upstream_version: Upstream version string.
        git_ref: Git ref for snapshots.
        signature_verified: Whether upstream signature was verified.
        signature_warning: Warning message about signature.
        lp_bug: Optional Launchpad bug number to reference.
        openstack_series: OpenStack series name (e.g., "Gazpacho").

    Returns:
        List of changelog entry lines.
    """
    changes: list[str] = []

    # Build the main changelog message
    lp_ref = f" (LP: #{lp_bug})" if lp_bug else ""
    series_name = f" for OpenStack {openstack_series.capitalize()}" if openstack_series else ""

    if build_type == "release":
        changes.append(f"New upstream release{series_name}.{lp_ref}")
    elif build_type == "snapshot":
        changes.append(f"New upstream snapshot from {git_ref}{series_name}.{lp_ref}")
    elif build_type == "milestone":
        changes.append(f"New upstream milestone {upstream_version}{series_name}.{lp_ref}")
    else:
        changes.append(f"New upstream version {upstream_version}{series_name}.{lp_ref}")

    # Note: We no longer include signature verification status in changelog
    # as it's redundant - the build process validates signatures and users
    # can see verification results in the build logs.
    # Only add notes about signature issues if there's a warning.
    if signature_warning:
        changes.append(f"Note: {signature_warning}")

    return changes


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m packastack.debpkg.changelog <changelog_path>")
        sys.exit(1)

    path = Path(sys.argv[1])
    version = get_current_version(path)
    print(f"Current version: {version}")

    if version:
        parsed = parse_version(version)
        print(f"  Epoch: {parsed.epoch}")
        print(f"  Upstream: {parsed.upstream}")
        print(f"  Debian: {parsed.debian}")

        next_ver = increment_upstream_version(parsed.upstream)
        print(f"  Next upstream: {next_ver}")
