# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Snapshot tarball importer for unreleased code."""

import re
import subprocess
from pathlib import Path

from packastack.exceptions import ImporterError
from packastack.git import RepoManager
from packastack.importer.base import BaseImporter
from packastack.package.version import VersionConverter


class SnapshotImporter(BaseImporter):
    """Importer for snapshot tarballs from git."""

    def __init__(self, *args, explicit_snapshot: bool = False, **kwargs):
        """
        Initialize snapshot importer.

        Args:
            explicit_snapshot: True if user explicitly requested snapshot type
            *args, **kwargs: Passed to BaseImporter
        """
        super().__init__(*args, **kwargs)
        self.explicit_snapshot = explicit_snapshot

    def get_version(self) -> str:
        """
        Get version from git describe.

        Checks if HEAD is tagged and raises error if so (should use
        official release importer instead).

        Returns:
            Git describe output

        Raises:
            ImporterError: If HEAD is tagged (exit code 74 if explicit)
        """
        repo_mgr = RepoManager(path=self.upstream_repo_path)

        # Check for tags at HEAD
        head_tags = repo_mgr.get_head_tags()
        if head_tags:
            error_msg = (
                f"HEAD is tagged with {head_tags}. "
                f"Use release/candidate/beta importer instead."
            )
            if self.explicit_snapshot:
                # User explicitly asked for snapshot but HEAD is tagged
                # Exit with EBADMSG (74)
                raise SystemExit(74)
            else:
                # Auto mode - should switch to appropriate importer
                raise ImporterError(error_msg)

        # Get git describe
        try:
            describe = repo_mgr.git_describe(long=True)
            return describe
        except Exception as e:
            raise ImporterError(f"Failed to get git describe: {e}")

    def get_tarball(self, version: str) -> Path:
        """
        Generate snapshot tarball using setup.py sdist or uv build.

        Args:
            version: Git describe output (not used directly)

        Returns:
            Path to generated tarball

        Raises:
            ImporterError: If tarball generation fails
        """
        # Try uv build first, fall back to setup.py sdist
        tarball_path = None

        try:
            # Try uv build --sdist
            result = subprocess.run(
                ["uv", "build", "--sdist"],
                cwd=self.upstream_repo_path,
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0:
                # Find generated tarball in dist/
                dist_dir = self.upstream_repo_path / "dist"
                if dist_dir.exists():
                    tarballs = list(dist_dir.glob("*.tar.gz"))
                    if tarballs:
                        # Get most recent tarball
                        tarball_path = max(tarballs, key=lambda p: p.stat().st_mtime)

        except FileNotFoundError:
            # uv not installed, will try setup.py
            pass

        # Fall back to setup.py sdist
        if not tarball_path:
            try:
                result = subprocess.run(
                    ["python3", "setup.py", "sdist"],
                    cwd=self.upstream_repo_path,
                    capture_output=True,
                    text=True,
                    check=True,
                )

                # Find generated tarball in dist/
                dist_dir = self.upstream_repo_path / "dist"
                if dist_dir.exists():
                    tarballs = list(dist_dir.glob("*.tar.gz"))
                    if tarballs:
                        tarball_path = max(tarballs, key=lambda p: p.stat().st_mtime)

            except subprocess.CalledProcessError as e:
                raise ImporterError(f"Failed to generate tarball: {e.stderr}")
            except FileNotFoundError:
                raise ImporterError(
                    "Neither uv nor python3 found - cannot generate tarball"
                )

        if not tarball_path:
            raise ImporterError("No tarball generated in dist/ directory")

        # Move tarball to tarballs directory
        dest_path = self.tarballs_dir / tarball_path.name
        tarball_path.rename(dest_path)

        return dest_path

    def convert_version(self, upstream_version: str) -> str:
        """
        Convert git-describe output to Debian snapshot version.

        Args:
            upstream_version: Git describe output

        Returns:
            Debian snapshot version

        Raises:
            ImporterError: If conversion fails
        """
        # Check for existing version in tarballs directory
        existing_version = None
        for tarball in self.tarballs_dir.glob("*.orig.tar.gz"):
            # Extract version from filename: package_VERSION.orig.tar.gz
            match = re.match(r".*_(.+)\.orig\.tar\.gz", tarball.name)
            if match:
                existing_version = match.group(1)
                break

        return VersionConverter.convert_snapshot_version(
            upstream_version, existing_version
        )
