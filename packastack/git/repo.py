# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Git repository management with retry logic."""

import logging
from pathlib import Path

from git import Repo
from git.exc import GitCommandError
from tenacity import retry, stop_after_attempt, wait_exponential

from packastack.constants import (
    DEFAULT_REMOTE,
    MAX_RETRY_ATTEMPTS,
    PRISTINE_TAR_BRANCH,
    RETRY_MAX_WAIT_SECONDS,
    RETRY_MIN_WAIT_SECONDS,
    RETRY_MULTIPLIER,
)
from packastack.exceptions import RepositoryError


class RepoManager:
    """Manages Git repository operations with retry logic for network operations."""

    def __init__(
        self, path: str | Path | None = None, url: str | None = None
    ):
        """
        Initialize repository manager.

        Args:
            path: Local path to repository (str or Path)
            url: Remote URL to clone from

        Raises:
            ValueError: If neither path nor url is provided
        """
        if path is None and url is None:
            raise ValueError("Either path or url must be provided")

        self.path = Path(path) if path else None
        self.url = url
        self.repo: Repo | None = None
        self._logger = logging.getLogger(__name__)

        # Try to open if path is provided and exists
        if self.path and self.path.exists():
            try:
                self.open()
            except RepositoryError:
                # If opening fails, repo might not be initialized yet
                pass

    @retry(
        stop=stop_after_attempt(MAX_RETRY_ATTEMPTS),
        wait=wait_exponential(
            multiplier=RETRY_MULTIPLIER,
            min=RETRY_MIN_WAIT_SECONDS,
            max=RETRY_MAX_WAIT_SECONDS,
        ),
        reraise=True,
    )
    def clone(self) -> None:
        """
        Clone repository from URL to destination.

        Raises:
            ValueError: If url is not set
            RepositoryError: If clone fails
        """
        if not self.url:
            self._logger.error("Attempted clone without url set")
            raise ValueError("url must be set to clone")
        if not self.path:
            raise ValueError("path must be set to clone")

        try:
            self._logger.info("Cloning repo %s into %s", self.url, self.path)
            self.repo = Repo.clone_from(self.url, self.path)
        except GitCommandError as e:
            raise RepositoryError(f"Failed to clone {self.url} to {self.path}: {e}")

    def open(self) -> None:
        """
        Open existing repository.

        Raises:
            RepositoryError: If repository cannot be opened
        """
        if not self.path:
            raise RepositoryError("Repository path not set")
        try:
            self._logger.debug("Opening repository at %s", self.path)
            self.repo = Repo(self.path)
        except Exception as e:
            self._logger.error("Failed to open repository at %s: %s", self.path, e)
            raise RepositoryError(f"Failed to open repository at {self.path}: {e}")

    @retry(
        stop=stop_after_attempt(MAX_RETRY_ATTEMPTS),
        wait=wait_exponential(
            multiplier=RETRY_MULTIPLIER,
            min=RETRY_MIN_WAIT_SECONDS,
            max=RETRY_MAX_WAIT_SECONDS,
        ),
        reraise=True,
    )
    def fetch(self, remote: str = DEFAULT_REMOTE) -> None:
        """
        Fetch from remote.

        Args:
            remote: Remote name to fetch from

        Raises:
            RepositoryError: If fetch fails
        """
        if not self.repo:
            self._logger.error("Fetch called but repository not opened")
            raise RepositoryError("Repository not opened")

        try:
            self._logger.info("Fetching from remote %s", remote)
            self.repo.remotes[remote].fetch()
        except GitCommandError as e:
            raise RepositoryError(f"Failed to fetch from {remote}: {e}")
        except IndexError:
            raise RepositoryError(f"Remote {remote} not found")

    @retry(
        stop=stop_after_attempt(MAX_RETRY_ATTEMPTS),
        wait=wait_exponential(
            multiplier=RETRY_MULTIPLIER,
            min=RETRY_MIN_WAIT_SECONDS,
            max=RETRY_MAX_WAIT_SECONDS,
        ),
        reraise=True,
    )
    def pull(self, remote: str = DEFAULT_REMOTE, branch: str | None = None) -> None:
        """
        Pull changes from remote.

        Args:
            remote: Remote name to pull from
            branch: Branch name to pull (defaults to current branch)

        Raises:
            RepositoryError: If pull fails
        """
        if not self.repo:
            self._logger.error("Pull called but repository not opened")
            raise RepositoryError("Repository not opened")

        try:
            if branch:
                self.repo.remotes[remote].pull(branch)
            else:
                self.repo.remotes[remote].pull()
        except GitCommandError as e:
            self._logger.error("Failed to pull from %s: %s", remote, e)
            raise RepositoryError(f"Failed to pull from {remote}: {e}")
        except IndexError:
            raise RepositoryError(f"Remote {remote} not found")

    def checkout(self, ref: str) -> None:
        """
        Checkout branch or ref.

        Args:
            ref: Branch name, tag, or commit to checkout

        Raises:
            RepositoryError: If checkout fails
        """
        if not self.repo:
            self._logger.error("Checkout called but repository not opened")
            raise RepositoryError("Repository not opened")

        try:
            self._logger.info("Checking out %s", ref)
            self.repo.git.checkout(ref)
        except GitCommandError as e:
            self._logger.error("Failed to checkout %s: %s", ref, e)
            raise RepositoryError(f"Failed to checkout {ref}: {e}")

    def create_branch(self, name: str, start_point: str | None = None) -> None:
        """
        Create new branch.

        Args:
            name: Branch name
            start_point: Starting point for branch (commit, tag, branch)

        Raises:
            RepositoryError: If branch creation fails
        """
        if not self.repo:
            self._logger.error("Create branch called but repository not opened")
            raise RepositoryError("Repository not opened")

        try:
            if start_point:
                self._logger.debug("Creating branch %s at %s", name, start_point)
                self.repo.create_head(name, start_point)
            else:
                self._logger.debug("Creating branch %s", name)
                self.repo.create_head(name)
        except GitCommandError as e:
            self._logger.error("Failed to create branch %s: %s", name, e)
            raise RepositoryError(f"Failed to create branch {name}: {e}")

    def branch_exists(self, name: str, remote: bool = False) -> bool:
        """
        Check if branch exists.

        Args:
            name: Branch name
            remote: Check remote branches

        Returns:
            True if branch exists
        """
        if not self.repo:
            self._logger.error("Branch existence check when repository not opened")
            raise RepositoryError("Repository not opened")

        if remote:
            return any(
                name in ref.name for ref in self.repo.remotes[DEFAULT_REMOTE].refs
            )
        else:
            return any(head.name == name for head in self.repo.heads)

    @retry(
        stop=stop_after_attempt(MAX_RETRY_ATTEMPTS),
        wait=wait_exponential(
            multiplier=RETRY_MULTIPLIER,
            min=RETRY_MIN_WAIT_SECONDS,
            max=RETRY_MAX_WAIT_SECONDS,
        ),
        reraise=True,
    )
    def push(self, remote: str = DEFAULT_REMOTE, refspec: str | None = None) -> None:
        """
        Push to remote.

        Args:
            remote: Remote name to push to
            refspec: Refspec to push (e.g., 'main:main')

        Raises:
            RepositoryError: If push fails
        """
        if not self.repo:
            self._logger.error("Push called but repository not opened")
            raise RepositoryError("Repository not opened")

        try:
            if refspec:
                self._logger.info("Pushing %s to %s", refspec, remote)
                self.repo.remotes[remote].push(refspec)
            else:
                self._logger.info("Pushing to remote %s", remote)
                self.repo.remotes[remote].push()
        except GitCommandError as e:
            raise RepositoryError(f"Failed to push to {remote}: {e}")
        except IndexError:
            raise RepositoryError(f"Remote {remote} not found")

    def list_branches(self, remote: bool = False) -> list[str]:
        """
        List branches.

        Args:
            remote: List remote branches instead of local

        Returns:
            List of branch names

        Raises:
            RepositoryError: If repository not opened
        """
        if not self.repo:
            self._logger.error("List branches called but repository not opened")
            raise RepositoryError("Repository not opened")

        if remote:
            return [ref.name for ref in self.repo.remotes[DEFAULT_REMOTE].refs]
        else:
            return [head.name for head in self.repo.heads]

    def track_remote_branches(self) -> None:
        """
        Track all remote branches locally (equivalent to bash loop).

        Creates local tracking branches for all remote branches that don't
        already have local counterparts, excluding symbolic refs.

        Raises:
            RepositoryError: If repository not opened or tracking fails
        """
        if not self.repo:
            self._logger.error("track_remote_branches called but repository not opened")
            raise RepositoryError("Repository not opened")

        try:
            # Get all remote refs
            self._logger.debug("Tracking remote branches for %s", DEFAULT_REMOTE)
            remote = self.repo.remotes[DEFAULT_REMOTE]
            local_branches = {head.name for head in self.repo.heads}

            for ref in remote.refs:
                # Skip symbolic refs (like HEAD -> main)
                if ref.name == f"{DEFAULT_REMOTE}/HEAD":
                    continue

                # Extract branch name (remove 'origin/' prefix)
                branch_name = ref.name.split("/", 1)[1]

                # Skip if already tracked locally
                if branch_name in local_branches:
                    continue

                # Create tracking branch
                try:
                    self._logger.info("Creating tracking branch %s", branch_name)
                    self.repo.create_head(branch_name, ref).set_tracking_branch(ref)
                except GitCommandError as e:
                    # Log but don't fail if one branch fails to track
                    self._logger.warning("Failed to track %s: %s", branch_name, e)

        except Exception as e:
            self._logger.error("Failed to track remote branches: %s", e)
            raise RepositoryError(f"Failed to track remote branches: {e}")

    def checkout_important_branches(self) -> None:
        """
        Checkout pristine-tar and upstream branches if they exist.

        This ensures these important branches are available locally.

        Raises:
            RepositoryError: If repository not opened
        """
        if not self.repo:
            raise RepositoryError("Repository not opened")

        # Store original branch
        original_branch = self.get_current_branch()
        self._logger.debug(
            "Checkout important branches; original branch %s",
            original_branch,
        )

        # Try to checkout pristine-tar
        try:
            if self.branch_exists(PRISTINE_TAR_BRANCH, remote=True):
                self.checkout(PRISTINE_TAR_BRANCH)
        except RepositoryError:
            # Branch doesn't exist, that's okay
            pass

        # Try to checkout any upstream branch
        try:
            remote_branches = self.list_branches(remote=True)
            upstream_branches = [
                b for b in remote_branches if "/upstream" in b or b.endswith("upstream")
            ]
            if upstream_branches:
                # Checkout the first upstream branch found
                branch_name = upstream_branches[0].split("/", 1)[1]
                self.checkout(branch_name)
        except RepositoryError:
            # No upstream branches, that's okay
            pass

        # Return back to the original branch. If this fails, we silently
        # ignore the error to avoid raising exceptions from cleanup operations
        # such as returning to the original branch. The important branches
        # have already been attempted to be checked out.
        try:
            self.checkout(original_branch)
        except RepositoryError:
            # Ignore errors when attempting to return to original branch
            pass

    def get_current_branch(self) -> str:
        """
        Get current branch name.

        Returns:
            Current branch name

        Raises:
            RepositoryError: If repository not opened or in detached HEAD
        """
        if not self.repo:
            self._logger.error("get_current_branch called but repository not opened")
            raise RepositoryError("Repository not opened")

        try:
            branch = self.repo.active_branch.name
            self._logger.debug("Current branch is %s", branch)
            return branch
        except (TypeError, AttributeError):
            raise RepositoryError("Repository is in detached HEAD state")

    def list_tags(self) -> list[str]:
        """
        List all tags.

        Returns:
            List of tag names

        Raises:
            RepositoryError: If repository not opened
        """
        if not self.repo:
            self._logger.error("list_tags called but repository not opened")
            raise RepositoryError("Repository not opened")

        return [tag.name for tag in self.repo.tags]

    def get_head_tags(self) -> list[str]:
        """
        Get tags pointing at HEAD.

        Returns:
            List of tag names at HEAD

        Raises:
            RepositoryError: If repository not opened
        """
        if not self.repo:
            self._logger.error("get_head_tags called but repository not opened")
            raise RepositoryError("Repository not opened")

        head_commit = self.repo.head.commit
        return [tag.name for tag in self.repo.tags if tag.commit == head_commit]

    def git_describe(self, long: bool = False) -> str:
        """
        Run git describe.

        Args:
            long: Use long format

        Returns:
            Output of git describe

        Raises:
            RepositoryError: If git describe fails
        """
        if not self.repo:
            self._logger.error("git_describe called but repository not opened")
            raise RepositoryError("Repository not opened")

        try:
            if long:
                self._logger.debug("Running git describe --long --tags")
                return self.repo.git.describe("--long", "--tags")
            else:
                self._logger.debug("Running git describe --tags")
                return self.repo.git.describe("--tags")
        except GitCommandError as e:
            raise RepositoryError(f"git describe failed: {e}")

    def get_remote_url(self, remote: str = DEFAULT_REMOTE) -> str:
        """
        Get URL of remote.

        Args:
            remote: Remote name

        Returns:
            Remote URL

        Raises:
            RepositoryError: If repository not opened or remote not found
        """
        if not self.repo:
            self._logger.error("get_remote_url called but repository not opened")
            raise RepositoryError("Repository not opened")

        try:
            url = list(self.repo.remotes[remote].urls)[0]
            self._logger.debug("Remote %s URL is %s", remote, url)
            return url
        except (IndexError, KeyError):
            raise RepositoryError(f"Remote {remote} not found")

    def set_remote_url(self, url: str, remote: str = DEFAULT_REMOTE) -> None:
        """
        Set URL of remote.

        Args:
            url: New remote URL
            remote: Remote name

        Raises:
            RepositoryError: If repository not opened or remote not found
        """
        if not self.repo:
            self._logger.error("set_remote_url called but repository not opened")
            raise RepositoryError("Repository not opened")

        try:
            self._logger.info("Setting remote %s url to %s", remote, url)
            self.repo.remotes[remote].set_url(url)
        except (IndexError, KeyError):
            raise RepositoryError(f"Remote {remote} not found")

    def commit(self, message: str, files: list[Path|str]) -> None:
        """
        Commit changes to specified files.

        Args:
            message: Commit message
            files: List of file paths to commit

        Raises:
            RepositoryError: If repository not opened or commit fails
        """
        if not self.repo:
            self._logger.error("commit called but repository not opened")
            raise RepositoryError("Repository not opened")

        config = self.repo.config_reader()
        user_name = config.get_value("user", "name", None)
        user_email = config.get_value("user", "email", None)

        if not user_name or not user_email:
            self._logger.error("Git user.name and user.email not set; cannot commit")
            raise RepositoryError("Git user.name and user.email must be set to commit")

        # Let's make sure to add the developer sign off.
        message = f"{message}\n\nSigned-off-by: {user_name} <{user_email}>"

        try:
            self._logger.info("Committing files %s with message: %s", files, message)
            self.repo.index.add(files)
            self.repo.index.commit(message)
        except GitCommandError as e:
            raise RepositoryError(f"Failed to commit changes: {e}")
