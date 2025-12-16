# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Parse debian/control files."""

import re
from pathlib import Path

from packastack.exceptions import DebianError


class ControlFileParser:
    """Parser for debian/control files."""

    def __init__(self, control_path: str):
        """
        Initialize control file parser.

        Args:
            control_path: Path to debian/control file

        Raises:
            DebianError: If file doesn't exist or can't be read
        """
        self.control_path = Path(control_path)
        if not self.control_path.exists():
            raise DebianError(f"Control file not found: {control_path}")

        try:
            self.content = self.control_path.read_text()
        except Exception as e:
            raise DebianError(f"Failed to read control file: {e}")

    def get_source_name(self) -> str:
        """
        Extract source package name from control file.

        Returns:
            Source package name

        Raises:
            DebianError: If Source field not found
        """
        match = re.search(r"^Source:\s*(.+)$", self.content, re.MULTILINE)
        if not match:
            raise DebianError("Source field not found in control file")
        return match.group(1).strip()

    def get_homepage(self) -> str | None:
        """
        Extract homepage URL from control file.

        Returns:
            Homepage URL or None if not found
        """
        match = re.search(r"^Homepage:\s*(.+)$", self.content, re.MULTILINE)
        if match:
            return match.group(1).strip()
        return None

    def get_upstream_project_name(self) -> str | None:
        """
        Extract upstream project name from Homepage URL.

        Parses the Homepage field and extracts the last component of the path,
        which is typically the upstream project name.

        Returns:
            Upstream project name or None if Homepage not found

        Raises:
            DebianError: If Homepage exists but can't be parsed
        """
        homepage = self.get_homepage()
        if not homepage:
            return None

        # Extract project name from URL path
        # Example: https://opendev.org/openstack/nova -> nova
        try:
            path = homepage.rstrip("/").split("/")[-1]
            if path:
                return path
            raise DebianError(
                f"Could not extract project name from Homepage: {homepage}"
            )
        except Exception as e:
            raise DebianError(f"Failed to parse Homepage URL: {e}")
