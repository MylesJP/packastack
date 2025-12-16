# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""The debian source package module."""
from pathlib import Path

from packastack.exceptions import DebianError
from packastack.package.uscan import Uscan

DEBIAN_DIR = "debian"


class DebianSourcePackage:
    """Class representing a Debian source package."""

    def __init__(self, path: Path):
        """Initialize the Debian source package.

        Args:
            path (Path): The path to the source package directory.
        """
        self.path = path

    @property
    def debian_dir(self) -> Path:
        """Get the path to the debian directory.

        Returns:
            Path: The path to the debian directory.
        """
        return self.path / DEBIAN_DIR

    @property
    def control_file(self) -> Path:
        """Get the path to the control file.

        Returns:
            Path: The path to the control file.
        """
        return self.debian_dir / "control"

    @property
    def changelog(self) -> Path:
        """Get the path to the changelog file.

        Returns:
            Path: The path to the changelog file.
        """
        return self.debian_dir / "changelog"

    @property
    def source_package_name(self) -> str:
        """Get the name of the source package.

        Returns:
            str: The source package name.
        """
        # Assuming the directory name is the source package name
        return self.path.name

    @property
    def install_files(self) -> list[Path]:
        """Get the paths of any install files.

        Returns:
            list[Path]: The path to the install files.
        """
        return list(self.debian_dir.glob("*.install"))

    @property
    def watch_file(self) -> Path:
        """Get the path to the watch file.

        Returns:
            Path: The path to the watch file.
        """
        return self.debian_dir / "watch"

    def get_watch_urls(self) -> list[str]:
        """Get the URLs from the watch file which can be used for scanning
        for release artifacts.

        Returns:
            list[str]: The URLs defined in the watch file.
        """
        watch_path = self.watch_file
        if not watch_path.exists():
            raise DebianError(f"Watch file not found at {watch_path}")

        scanner = Uscan(watch_path)
        return [entry.url for entry in scanner.entries]
