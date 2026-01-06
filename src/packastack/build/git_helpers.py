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

"""Git-related helper functions for build operations.

This module provides utilities for git commit operations, including:
- GPG signing control for automation environments
- Git author environment configuration from Debian maintainer variables
- .gitattributes management for merge conflict prevention
- debian/rules sphinxdoc addon enablement
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from packastack.debpkg.gbp import CommandResult


def no_gpg_sign_enabled() -> bool:
    """Check if git commit signing should be disabled for automation.

    Returns:
        True if PACKASTACK_NO_GPG_SIGN environment variable is set to
        "1", "true", or "yes" (case-insensitive).
    """
    return os.environ.get("PACKASTACK_NO_GPG_SIGN", "").lower() in {"1", "true", "yes"}


def maybe_disable_gpg_sign(cmd: list[str]) -> list[str]:
    """Inject --no-gpg-sign into git commit command when opt-out flag is set.

    This function modifies git commit commands to include --no-gpg-sign
    when the PACKASTACK_NO_GPG_SIGN environment variable is set, allowing
    automated builds to run without requiring GPG key access.

    Args:
        cmd: The git command as a list of strings.

    Returns:
        The modified command with --no-gpg-sign injected after "commit"
        if applicable, otherwise the original command unchanged.

    Example:
        >>> os.environ["PACKASTACK_NO_GPG_SIGN"] = "1"
        >>> maybe_disable_gpg_sign(["git", "commit", "-m", "test"])
        ['git', 'commit', '--no-gpg-sign', '-m', 'test']
    """
    if no_gpg_sign_enabled() and len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "commit":
        return [cmd[0], cmd[1], "--no-gpg-sign", *cmd[2:]]
    return cmd


def get_git_author_env(*, debug: bool = True) -> dict[str, str]:
    """Get environment variables for git commit author based on Debian maintainer info.

    This ensures that git commits made by packastack are attributed to the
    correct user (from Debian maintainer environment) rather than the system's
    git config.

    The function checks the following environment variables in order:
    - DEBFULLNAME / NAME for author name
    - DEBEMAIL / EMAIL for author email

    Args:
        debug: If True, print debug information to stderr (default: True).

    Returns:
        Dict with GIT_AUTHOR_NAME, GIT_AUTHOR_EMAIL, GIT_COMMITTER_NAME,
        and GIT_COMMITTER_EMAIL if the corresponding Debian variables are set,
        otherwise an empty dict.

    Example:
        >>> os.environ["DEBFULLNAME"] = "John Doe"
        >>> os.environ["DEBEMAIL"] = "john@example.com"
        >>> get_git_author_env(debug=False)
        {'GIT_AUTHOR_NAME': 'John Doe', 'GIT_COMMITTER_NAME': 'John Doe',
         'GIT_AUTHOR_EMAIL': 'john@example.com', 'GIT_COMMITTER_EMAIL': 'john@example.com'}
    """
    env: dict[str, str] = {}

    name = os.environ.get("DEBFULLNAME") or os.environ.get("NAME")
    email = os.environ.get("DEBEMAIL") or os.environ.get("EMAIL")

    if debug:
        # Debug logging to stderr so it shows up in build output
        print(f"[git-author-debug] DEBFULLNAME={os.environ.get('DEBFULLNAME')}", file=sys.stderr)
        print(f"[git-author-debug] DEBEMAIL={os.environ.get('DEBEMAIL')}", file=sys.stderr)
        print(f"[git-author-debug] NAME={os.environ.get('NAME')}", file=sys.stderr)
        print(f"[git-author-debug] EMAIL={os.environ.get('EMAIL')}", file=sys.stderr)
        print(f"[git-author-debug] Resolved name={name}, email={email}", file=sys.stderr)

    if name:
        env["GIT_AUTHOR_NAME"] = name
        env["GIT_COMMITTER_NAME"] = name
    if email:
        env["GIT_AUTHOR_EMAIL"] = email
        env["GIT_COMMITTER_EMAIL"] = email

    if debug:
        print(f"[git-author-debug] Setting git env: {env}", file=sys.stderr)

    return env


def ensure_no_merge_paths(repo_path: Path, paths: list[str]) -> bool:
    """Ensure specified paths use merge=ours strategy in .gitattributes.

    This protects packaging-only files (e.g., launchpad.yaml, .gitattributes)
    from being deleted when merging upstream content that lacks them.

    The function:
    1. Reads existing .gitattributes if present
    2. Adds "path merge=ours" entries for any missing paths
    3. Always protects .gitattributes itself
    4. Writes the updated file if changes were made

    Args:
        repo_path: Path to the git repository.
        paths: List of file paths to protect with merge=ours.

    Returns:
        True if .gitattributes was modified, False otherwise.

    Example:
        >>> ensure_no_merge_paths(Path("/path/to/repo"), ["launchpad.yaml"])
        True  # If .gitattributes was modified
    """
    gitattributes = repo_path / ".gitattributes"
    existing: list[str] = []

    if gitattributes.exists():
        existing = gitattributes.read_text(encoding="utf-8").splitlines()

    existing_set = {line.strip() for line in existing if line.strip()}
    updated = False

    # Always protect the .gitattributes file itself to avoid it being
    # overwritten or removed during merges.
    full_paths = list(paths) + [".gitattributes"]

    for path in full_paths:
        entry = f"{path} merge=ours"
        if entry not in existing_set:
            existing.append(entry)
            existing_set.add(entry)
            updated = True

    if updated:
        gitattributes.write_text("\n".join(existing) + "\n", encoding="utf-8")

    return updated


def maybe_enable_sphinxdoc(pkg_repo: Path) -> bool:
    """Ensure dh runs with sphinxdoc addon to avoid embedded doc assets warnings.

    This function modifies debian/rules to add the sphinxdoc addon to dh
    if it's not already present. This prevents lintian warnings about
    embedded documentation assets.

    Args:
        pkg_repo: Path to the packaging repository.

    Returns:
        True if debian/rules was modified, False otherwise.

    Example:
        >>> maybe_enable_sphinxdoc(Path("/path/to/pkg"))
        True  # If debian/rules was modified
    """
    rules_path = pkg_repo / "debian" / "rules"
    if not rules_path.exists():
        return False

    try:
        content = rules_path.read_text()
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
            rules_path.write_text(replaced)
            return True
        except OSError:
            return False

    return False


# Backwards compatibility aliases (private names used in build.py)
_no_gpg_sign_enabled = no_gpg_sign_enabled
_maybe_disable_gpg_sign = maybe_disable_gpg_sign
_get_git_author_env = get_git_author_env
_ensure_no_merge_paths = ensure_no_merge_paths
_maybe_enable_sphinxdoc = maybe_enable_sphinxdoc
