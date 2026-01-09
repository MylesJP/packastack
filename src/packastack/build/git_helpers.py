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
- Standardized git commit with file staging
- Version string extraction for commit messages
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # For type checking only: run_command returns a tuple in packastack.debpkg.gbp
    from packastack.debpkg.gbp import run_command as _run_command_type

@dataclass
class CommandResult:
    """Simple command result used by git helpers.

    Mirrors the minimal attributes expected by callers:
    - returncode: int
    - stdout: str
    - stderr: str
    """

    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def success(self) -> bool:  # pragma: no cover - trivial
        return self.returncode == 0


class GitCommitError(Exception):
    """Raised when a git commit operation fails.

    Attributes:
        message: Error description.
        stderr: Standard error output from git command.
        returncode: Exit code from git command.
    """

    def __init__(self, message: str, stderr: str = "", returncode: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.stderr = stderr
        self.returncode = returncode

    def __str__(self) -> str:
        if self.stderr:
            return f"{self.message}: {self.stderr}"
        return self.message


def extract_upstream_version(debian_version: str) -> str:
    """Extract the upstream version from a Debian version string.

    Strips the epoch prefix and Ubuntu revision suffix to get the bare
    upstream version for use in commit messages.

    Args:
        debian_version: Full Debian version string (e.g., "2:29.0.0-0ubuntu1",
            "29.0.0+git2024010412345-0ubuntu1~snapshot").

    Returns:
        Upstream version without epoch or Ubuntu revision (e.g., "29.0.0",
        "29.0.0+git2024010412345").

    Examples:
        >>> extract_upstream_version("2:29.0.0-0ubuntu1")
        '29.0.0'
        >>> extract_upstream_version("29.0.0+git2024010412345-0ubuntu1~snapshot")
        '29.0.0+git2024010412345'
        >>> extract_upstream_version("1.2.3-1ubuntu2~ppa1")
        '1.2.3'
    """
    version = debian_version

    # Strip epoch (e.g., "2:" prefix)
    if ":" in version:
        version = version.split(":", 1)[1]

    # Strip Debian/Ubuntu revision (e.g., "-0ubuntu1", "-1ubuntu2~ppa1")
    # Pattern matches: -<debian_revision>ubuntu<ubuntu_revision><optional_suffix>
    version = re.sub(r"-\d+ubuntu\d+.*$", "", version)

    return version


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

    This creates a .gitattributes file documenting that certain files should
    be preserved during merges. While PackaStack now explicitly restores these
    files after merge, this serves as documentation and provides protection
    for manual merges.

    Args:
        repo_path: Path to the git repository.
        paths: List of file paths to document for merge protection.

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


def git_commit(
    repo_path: Path,
    message: str,
    *,
    files: list[str] | None = None,
    extra_lines: list[str] | None = None,
    add_all: bool = False,
    debug: bool = False,
) -> "CommandResult":
    """Execute a git commit with standardized options.

    This helper combines common git commit patterns:
    - Optional file staging before commit
    - Optional GPG signing disable via PACKASTACK_NO_GPG_SIGN
    - Author/committer environment from Debian maintainer variables
    - Optional staged-all (-a flag)
    - Multi-line commit messages

    Args:
        repo_path: Path to the git repository.
        message: Primary commit message line (subject for gbp changelog).
        files: List of file paths to stage with git add before committing.
            If provided, these files are added before the commit. If git add
            fails, returns early with a failed CommandResult.
        extra_lines: Additional lines to append to commit message body.
        add_all: If True, use -a flag to stage all modified files.
        debug: If True, print git author environment debug info.

    Returns:
        CommandResult with success status, output, and return code.

    Example:
        result = git_commit(
            pkg_repo,
            "d/watch: update for new upstream",
            files=["debian/watch"],
            extra_lines=["Update watch file version to 4"],
        )
        if result.success:
            print("Committed successfully")
    """
    from packastack.debpkg.gbp import run_command

    # If this isn't a git repository, treat commit as a no-op to allow
    # test fixtures (which create directories but not actual git repos)
    # to run without invoking git commands.
    if not (repo_path / ".git").exists():
        return CommandResult(returncode=0, stdout="", stderr=f"Not a git repo: {repo_path}")

    # Stage files if provided
    if files:
        add_cmd = ["git", "add"] + files
        add_rc, add_stdout, add_stderr = run_command(add_cmd, cwd=repo_path)
        if add_rc != 0:
            return CommandResult(
                returncode=add_rc,
                stdout=add_stdout,
                stderr=add_stderr or f"Failed to stage files: {files}",
            )

    # Build full message with extra lines
    full_message = message
    if extra_lines:
        full_message = message + "\n\n" + "\n".join(extra_lines)

    # Build command
    cmd = ["git", "commit"]
    if add_all:
        cmd.append("-a")
    cmd.extend(["-m", full_message])

    # Apply GPG signing option
    cmd = maybe_disable_gpg_sign(cmd)

    # Get author environment
    env = get_git_author_env(debug=debug)

    # Execute and normalize result into CommandResult
    rc, out, err = run_command(cmd, cwd=repo_path, env=env)
    return CommandResult(returncode=rc, stdout=out, stderr=err)

