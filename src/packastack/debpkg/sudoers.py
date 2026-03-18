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

The trailing ``*`` must be removed.  In sudoers syntax a command with no
arguments already matches that command invoked with *any* arguments, so the
functional behaviour is preserved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Matches a NOPASSWD sudoers line ending with a bare wildcard ``*``.
_NOPASSWD_TRAILING_WILDCARD_RE = re.compile(
    r"^(.+NOPASSWD:\s+\S+.*)[ \t]+\*[ \t]*$",
    re.MULTILINE,
)


@dataclass
class SudoersFixResult:
    """Result of scanning/fixing sudoers files in a debian directory."""

    files_scanned: int = 0
    files_fixed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def fix_sudoers_wildcard(sudoers_path: Path) -> bool:
    """Remove trailing wildcard arguments from a single sudoers file.

    Args:
        sudoers_path: Path to a ``debian/*_sudoers`` file.

    Returns:
        True if the file was modified.

    Raises:
        OSError: If the file cannot be read or written.
    """
    content = sudoers_path.read_text(encoding="utf-8")

    new_content, count = _NOPASSWD_TRAILING_WILDCARD_RE.subn(r"\1", content)

    if count == 0:
        return False

    sudoers_path.write_text(new_content, encoding="utf-8")
    return True


def fix_sudoers_in_debian_dir(debian_dir: Path) -> SudoersFixResult:
    """Scan all ``*_sudoers`` files under *debian_dir* and fix wildcards.

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
            if fix_sudoers_wildcard(sudoers_file):
                result.files_fixed.append(sudoers_file.name)
        except OSError as exc:
            result.errors.append(f"{sudoers_file.name}: {exc}")

    return result
