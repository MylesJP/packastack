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

"""Utilities for patching debian/rules files.

This module provides functions for modifying debian/rules to fix
common lintian warnings and apply best practices.
"""

from __future__ import annotations

import re
from pathlib import Path


def add_doctree_cleanup(rules_path: Path) -> bool:
    """Add cleanup of .doctree files to debian/rules.

    The .doctree files are Sphinx build artifacts that should not be
    included in packages. This causes the lintian warning:
    'package-contains-python-doctree-file'

    Args:
        rules_path: Path to the debian/rules file.

    Returns:
        True if the file was modified.
    """
    if not rules_path.exists():
        return False

    try:
        content = rules_path.read_text(encoding="utf-8")
    except OSError:
        return False

    # Check if doctree cleanup is already present
    if ".doctrees" in content or ".doctree" in content:
        return False

    # The cleanup command to add
    cleanup_cmd = "\trm -rf debian/*/usr/share/doc/*/.doctrees"

    # Check for existing override_dh_sphinxdoc
    if "override_dh_sphinxdoc:" in content:
        # Append cleanup to existing override_dh_sphinxdoc
        # Find the override block and add cleanup at the end
        lines = content.split("\n")
        new_lines = []
        in_sphinxdoc = False
        added = False

        for i, line in enumerate(lines):
            new_lines.append(line)

            if line.strip().startswith("override_dh_sphinxdoc:"):
                in_sphinxdoc = True
            elif in_sphinxdoc:
                # Check if next line is not a continuation (not starting with tab)
                next_idx = i + 1
                if next_idx >= len(lines) or (
                    not lines[next_idx].startswith("\t")
                    and lines[next_idx].strip()
                    and not lines[next_idx].startswith(" ")
                ):
                    # End of override block, insert cleanup before this
                    new_lines.append(cleanup_cmd)
                    in_sphinxdoc = False
                    added = True

        # If we're still in sphinxdoc at end of file, add cleanup
        if in_sphinxdoc and not added:
            new_lines.append(cleanup_cmd)
            added = True

        if added:
            try:
                rules_path.write_text("\n".join(new_lines), encoding="utf-8")
                return True
            except OSError:
                return False

    # Check for existing override_dh_installdocs
    elif "override_dh_installdocs:" in content:
        # Append cleanup to existing override_dh_installdocs
        lines = content.split("\n")
        new_lines = []
        in_installdocs = False
        added = False

        for i, line in enumerate(lines):
            new_lines.append(line)

            if line.strip().startswith("override_dh_installdocs:"):
                in_installdocs = True
            elif in_installdocs:
                next_idx = i + 1
                if next_idx >= len(lines) or (
                    not lines[next_idx].startswith("\t")
                    and lines[next_idx].strip()
                    and not lines[next_idx].startswith(" ")
                ):
                    new_lines.append(cleanup_cmd)
                    in_installdocs = False
                    added = True

        if in_installdocs and not added:
            new_lines.append(cleanup_cmd)
            added = True

        if added:
            try:
                rules_path.write_text("\n".join(new_lines), encoding="utf-8")
                return True
            except OSError:
                return False

    else:
        # No suitable override exists, add override_dh_installdocs
        override_block = """
override_dh_installdocs:
\tdh_installdocs
\trm -rf debian/*/usr/share/doc/*/.doctrees
"""
        content = content.rstrip() + "\n" + override_block

        try:
            rules_path.write_text(content, encoding="utf-8")
            return True
        except OSError:
            return False

    return False


def ensure_sphinxdoc_addon(rules_path: Path) -> bool:
    """Ensure dh runs with sphinxdoc addon.

    The sphinxdoc addon symlinks JavaScript libraries to system copies
    instead of bundling them, avoiding 'embedded-javascript-library' warnings.

    Args:
        rules_path: Path to the debian/rules file.

    Returns:
        True if the file was modified.
    """
    if not rules_path.exists():
        return False

    try:
        content = rules_path.read_text(encoding="utf-8")
    except OSError:
        return False

    if "sphinxdoc" in content:
        return False

    replaced = content
    if "--with python3" in content:
        replaced = content.replace("--with python3", "--with python3,sphinxdoc", 1)
    elif "dh $@" in content:
        replaced = content.replace("dh $@", "dh $@ --with sphinxdoc", 1)
    else:
        return False

    if replaced != content:
        try:
            rules_path.write_text(replaced, encoding="utf-8")
            return True
        except OSError:
            return False

    return False


def has_override(rules_path: Path, override_name: str) -> bool:
    """Check if debian/rules has a specific override target.

    Args:
        rules_path: Path to the debian/rules file.
        override_name: Name of the override (e.g., 'dh_sphinxdoc').

    Returns:
        True if the override exists.
    """
    if not rules_path.exists():
        return False

    try:
        content = rules_path.read_text(encoding="utf-8")
    except OSError:
        return False

    pattern = rf"^override_{override_name}\s*:"
    return bool(re.search(pattern, content, re.MULTILINE))
