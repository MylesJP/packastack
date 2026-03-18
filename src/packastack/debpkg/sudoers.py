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

"""Fix sudoers files for sudo-rs compatibility.

sudo-rs (the default sudo on Ubuntu Questing+) does not support wildcard (*)
matching in sudoers command arguments.  The traditional OpenStack rootwrap
pattern ships lines like::

    cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap /etc/cinder/rootwrap.conf *

All arguments after the command path must be removed.  In sudoers syntax a
command with no arguments already matches that command invoked with *any*
arguments, so the functional behaviour is preserved.  The config file pinning
(e.g. ``/etc/cinder/rootwrap.conf``) is not a meaningful security boundary
since these are no-login system accounts and the rootwrap configs are owned
by root.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Matches a NOPASSWD sudoers line that has arguments after the command path.
# Captures everything up to and including the command path (group 1), so we
# can replace the full match with just the command — dropping all arguments.
_NOPASSWD_COMMAND_ARGS_RE = re.compile(
    r"^(\s*\S+\s+ALL\s*=\s*\(root\)\s*NOPASSWD:\s*/\S+)"  # user + command
    r"([ \t]+\S+.*)$",  # one or more arguments to strip
    re.MULTILINE,
)


@dataclass
class SudoersFixResult:
    """Result of scanning/fixing sudoers files in a debian directory."""

    files_scanned: int = 0
    files_fixed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def fix_sudoers_args(sudoers_path: Path) -> bool:
    """Remove all arguments after the command path in NOPASSWD lines.

    Args:
        sudoers_path: Path to a ``debian/*_sudoers`` file.

    Returns:
        True if the file was modified.

    Raises:
        OSError: If the file cannot be read or written.
    """
    content = sudoers_path.read_text(encoding="utf-8")

    new_content, count = _NOPASSWD_COMMAND_ARGS_RE.subn(r"\1", content)

    if count == 0:
        return False

    sudoers_path.write_text(new_content, encoding="utf-8")
    return True


def fix_sudoers_in_debian_dir(debian_dir: Path) -> SudoersFixResult:
    """Scan all ``*_sudoers`` files under *debian_dir* and fix arguments.

    Args:
        debian_dir: Path to the ``debian/`` directory of a package.

    Returns:
        A :class:`SudoersFixResult` summarising what was done.
    """
    result = SudoersFixResult()

    if not debian_dir.is_dir():
        return result

    for sudoers_file in sorted(debian_dir.glob("*_sudoers")):
        if not sudoers_file.is_file():
            continue  # pragma: no cover
        result.files_scanned += 1
        try:
            if fix_sudoers_args(sudoers_file):
                result.files_fixed.append(sudoers_file.name)
        except OSError as exc:
            result.errors.append(f"{sudoers_file.name}: {exc}")

    return result
