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

"""External tool validation for Packastack build operations.

Validates presence of required build tools (git, gbp, dch, dpkg-source)
and optionally sbuild for binary builds.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ToolCheck:
    """Result of checking for required external tools."""

    tools: dict[str, Path | None] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    def is_complete(self) -> bool:
        """Return True if all required tools are available."""
        return len(self.missing) == 0

    def get_path(self, tool: str) -> Path | None:
        """Get the path to a tool, or None if not found."""
        return self.tools.get(tool)


# Required tools for source package building
REQUIRED_TOOLS = [
    "git",
    "gbp",
    "dch",
    "dpkg-source",
]

# Optional tools that may be required based on options
OPTIONAL_TOOLS = [
    "sbuild",
    "gpg",
]

# Installation instructions per tool
INSTALL_INSTRUCTIONS: dict[str, str] = {
    "git": "apt install git",
    "gbp": "apt install git-buildpackage",
    "dch": "apt install devscripts",
    "dpkg-source": "apt install dpkg-dev",
    "sbuild": "apt install sbuild && sbuild-adduser $USER",
    "gpg": "apt install gnupg",
}

# Package names for apt install command
TOOL_PACKAGES: dict[str, str] = {
    "git": "git",
    "gbp": "git-buildpackage",
    "dch": "devscripts",
    "dpkg-source": "dpkg-dev",
    "sbuild": "sbuild",
    "gpg": "gnupg",
}


def find_tool(name: str) -> Path | None:
    """Find an executable tool in PATH.

    Args:
        name: Name of the tool to find.

    Returns:
        Path to the tool if found, None otherwise.
    """
    path = shutil.which(name)
    if path:
        return Path(path)
    return None


def check_required_tools(need_sbuild: bool = False, need_gpg: bool = False) -> ToolCheck:
    """Check for required external build tools.

    Args:
        need_sbuild: If True, also check for sbuild (for binary builds).
        need_gpg: If True, also check for gpg (for signature verification).

    Returns:
        ToolCheck with available tools and list of missing tools.
    """
    result = ToolCheck()

    # Check required tools
    for tool in REQUIRED_TOOLS:
        path = find_tool(tool)
        result.tools[tool] = path
        if path is None:
            result.missing.append(tool)

    # Check optional tools if requested
    if need_sbuild:
        path = find_tool("sbuild")
        result.tools["sbuild"] = path
        if path is None:
            result.missing.append("sbuild")

    if need_gpg:
        path = find_tool("gpg")
        result.tools["gpg"] = path
        if path is None:
            result.missing.append("gpg")

    return result


def get_missing_tools_message(missing: list[str]) -> str:
    """Generate a user-friendly message for installing missing tools.

    Args:
        missing: List of missing tool names.

    Returns:
        Multi-line string with installation instructions.
    """
    if not missing:
        return ""

    lines = ["The following required tools are missing:"]
    for tool in missing:
        instruction = INSTALL_INSTRUCTIONS.get(tool, f"Install {tool}")
        lines.append(f"  - {tool}: {instruction}")

    # Generate combined apt install command
    packages = [TOOL_PACKAGES.get(t, t) for t in missing if t in TOOL_PACKAGES]
    if packages:
        lines.append("")
        lines.append("Quick install:")
        lines.append(f"  sudo apt install {' '.join(packages)}")

    return "\n".join(lines)


def validate_tools_for_build(binary: bool = False) -> tuple[bool, str]:
    """Validate that all required tools are available for building.

    Args:
        binary: If True, also require sbuild for binary builds.

    Returns:
        Tuple of (success, message). If success is False, message contains
        installation instructions.
    """
    check = check_required_tools(need_sbuild=binary, need_gpg=True)

    if check.is_complete():
        return True, "All required tools available"

    message = get_missing_tools_message(check.missing)
    return False, message


if __name__ == "__main__":  # pragma: no cover - manual smoke test only
    # Quick test of tool detection
    check = check_required_tools(need_sbuild=True, need_gpg=True)
    print("Tool availability:")
    for tool, path in check.tools.items():
        status = str(path) if path else "NOT FOUND"
        print(f"  {tool}: {status}")

    if check.missing:
        print()
        print(get_missing_tools_message(check.missing))
