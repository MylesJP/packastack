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

"""Git repository fetching for Ubuntu OpenStack packaging sources.

Clones or updates packaging repositories from ubuntu-openstack-dev on Launchpad,
with file-based locking to prevent concurrent clone operations.
"""

from __future__ import annotations

import contextlib
import fcntl
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import git

if TYPE_CHECKING:
    from collections.abc import Sequence

# Default base URL for ubuntu-openstack-dev repositories
LAUNCHPAD_BASE_URL = "https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source"

# Lock timeout in seconds
LOCK_TIMEOUT = 300  # 5 minutes


@dataclass
class FetchResult:
    """Result of a git fetch operation."""

    package: str
    path: Path
    cloned: bool = False
    updated: bool = False
    branches: list[str] = field(default_factory=list)
    error: str | None = None
    was_locked: bool = False


class GitFetcher:
    """Fetches Ubuntu OpenStack packaging repositories from Launchpad.

    Uses file-based locking to prevent concurrent clone/fetch operations
    on the same package.
    """

    def __init__(
        self,
        base_url: str = LAUNCHPAD_BASE_URL,
        lock_timeout: int = LOCK_TIMEOUT,
    ) -> None:
        """Initialize the fetcher.

        Args:
            base_url: Base URL for git repositories.
            lock_timeout: Maximum seconds to wait for a lock.
        """
        self.base_url = base_url.rstrip("/")
        self.lock_timeout = lock_timeout

    def build_url(self, package: str) -> str:
        """Build the git URL for a package.

        Args:
            package: Source package name.

        Returns:
            Full git clone URL.
        """
        return f"{self.base_url}/{package}"

    def _acquire_lock(self, lock_path: Path) -> int | None:
        """Acquire a file lock, waiting up to lock_timeout seconds.

        Args:
            lock_path: Path to the lock file.

        Returns:
            File descriptor if lock acquired, None if timeout.
        """
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = lock_path.open("w")
        start = time.monotonic()

        while True:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd.fileno()
            except BlockingIOError:
                if time.monotonic() - start > self.lock_timeout:
                    fd.close()
                    return None
                time.sleep(0.5)

    def _release_lock(self, lock_path: Path) -> None:
        """Release a file lock by removing the lock file.

        Args:
            lock_path: Path to the lock file.
        """
        with contextlib.suppress(OSError):
            lock_path.unlink(missing_ok=True)

    def fetch_package(
        self,
        package: str,
        dest_dir: Path,
        offline: bool = False,
        branch: str | None = None,
    ) -> FetchResult:
        """Fetch or update a package repository.

        Args:
            package: Source package name.
            dest_dir: Directory where package repos are stored.
            offline: If True, skip network operations.
            branch: Optional branch to checkout after fetch.

        Returns:
            FetchResult with operation details.
        """
        result = FetchResult(package=package, path=dest_dir / package)
        pkg_path = dest_dir / package
        lock_path = dest_dir / f".{package}.lock"

        if offline:
            # In offline mode, just check if repo exists
            if pkg_path.exists() and (pkg_path / ".git").is_dir():
                result.branches = self._list_branches(pkg_path)
            else:
                result.error = "Repository not found in offline mode"
            return result

        # Acquire lock
        fd = self._acquire_lock(lock_path)
        if fd is None:
            result.error = f"Timeout waiting for lock on {package}"
            result.was_locked = True
            return result

        try:
            url = self.build_url(package)

            if pkg_path.exists() and (pkg_path / ".git").is_dir():
                # Update existing repository
                try:
                    repo = git.Repo(pkg_path)
                    origin = repo.remotes.origin
                    origin.fetch(prune=True)
                    result.updated = True
                except git.GitCommandError as e:
                    result.error = f"Fetch failed: {e}"
                    return result
            else:
                # Clone new repository (full clone)
                try:
                    pkg_path.parent.mkdir(parents=True, exist_ok=True)
                    git.Repo.clone_from(url, pkg_path)
                    result.cloned = True
                except git.GitCommandError as e:
                    result.error = f"Clone failed: {e}"
                    return result

            # List branches
            result.branches = self._list_branches(pkg_path)

            # Checkout specific branch if requested
            if branch and branch in result.branches:
                try:
                    repo = git.Repo(pkg_path)
                    repo.git.checkout(branch)
                except git.GitCommandError as e:
                    result.error = f"Checkout failed: {e}"

        finally:
            self._release_lock(lock_path)

        return result

    def _list_branches(self, repo_path: Path) -> list[str]:
        """List all remote branches in a repository.

        Args:
            repo_path: Path to the git repository.

        Returns:
            List of branch names (without 'origin/' prefix).
        """
        try:
            repo = git.Repo(repo_path)
            branches: list[str] = []
            for ref in repo.remotes.origin.refs:
                name = ref.name.replace("origin/", "")
                if name != "HEAD":
                    branches.append(name)
            return sorted(branches)
        except Exception:
            return []

    def find_branch_for_series(
        self,
        branches: Sequence[str],
        ubuntu_series: str,
        openstack_series: str,
    ) -> str | None:
        """Find the best branch for a given Ubuntu/OpenStack series combination.

        Branch naming conventions:
          - ubuntu/<ubuntu_series>  (e.g., ubuntu/noble)
          - ubuntu/<ubuntu_series>-<openstack_series>  (e.g., ubuntu/jammy-caracal)
          - stable/<openstack_series>  (e.g., stable/caracal)

        Args:
            branches: List of available branches.
            ubuntu_series: Ubuntu series codename.
            openstack_series: OpenStack series codename.

        Returns:
            Best matching branch name, or None if no match.
        """
        # Priority order for branch matching
        candidates = [
            f"ubuntu/{ubuntu_series}-{openstack_series}",
            f"ubuntu/{ubuntu_series}",
            f"stable/{openstack_series}",
            "master",
            "main",
        ]

        for candidate in candidates:
            if candidate in branches:
                return candidate

        return None

    def fetch_and_checkout(
        self,
        package: str,
        dest_dir: Path,
        ubuntu_series: str,
        openstack_series: str,
        offline: bool = False,
    ) -> FetchResult:
        """Fetch a package and checkout the appropriate branch.

        Args:
            package: Source package name.
            dest_dir: Directory where package repos are stored.
            ubuntu_series: Ubuntu series codename.
            openstack_series: OpenStack series codename.
            offline: If True, skip network operations.

        Returns:
            FetchResult with operation details.
        """
        result = self.fetch_package(package, dest_dir, offline=offline)

        if result.error:
            return result

        # Find and checkout the best branch
        branch = self.find_branch_for_series(
            result.branches,
            ubuntu_series,
            openstack_series,
        )

        if branch:
            pkg_path = dest_dir / package
            try:
                repo = git.Repo(pkg_path)
                # Checkout the branch, creating local tracking branch if needed
                if branch in [ref.name for ref in repo.heads]:
                    repo.heads[branch].checkout()
                else:
                    # Create local branch tracking remote
                    repo.git.checkout("-b", branch, f"origin/{branch}")
            except git.GitCommandError as e:
                result.error = f"Checkout of {branch} failed: {e}"

        return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m packastack.gitfetch <package> [dest_dir]")
        sys.exit(1)

    package_name = sys.argv[1]
    dest = Path(sys.argv[2]) if len(sys.argv) > 2 else Path()

    fetcher = GitFetcher()
    res = fetcher.fetch_package(package_name, dest)

    if res.error:
        print(f"Error: {res.error}")
        sys.exit(1)

    action = "Cloned" if res.cloned else "Updated" if res.updated else "Found"
    print(f"{action} {package_name} at {res.path}")
    print(f"Branches: {', '.join(res.branches)}")
