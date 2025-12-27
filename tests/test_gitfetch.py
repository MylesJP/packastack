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

"""Tests for the gitfetch module - Git repository fetching."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from packastack.gitfetch import LAUNCHPAD_BASE_URL, FetchResult, GitFetcher


class TestFetchResult:
    """Tests for the FetchResult dataclass."""

    def test_defaults(self) -> None:
        """Test default values."""
        result = FetchResult(package="nova", path=Path("/tmp/nova"))
        assert result.package == "nova"
        assert result.path == Path("/tmp/nova")
        assert result.cloned is False
        assert result.updated is False
        assert result.branches == []
        assert result.error is None
        assert result.was_locked is False

    def test_with_values(self) -> None:
        """Test result with explicit values."""
        result = FetchResult(
            package="glance",
            path=Path("/tmp/glance"),
            cloned=True,
            branches=["master", "ubuntu/noble"],
        )
        assert result.package == "glance"
        assert result.cloned is True
        assert result.branches == ["master", "ubuntu/noble"]


class TestGitFetcher:
    """Tests for GitFetcher class."""

    def test_default_base_url(self) -> None:
        """Test default base URL is set correctly."""
        fetcher = GitFetcher()
        assert fetcher.base_url == LAUNCHPAD_BASE_URL

    def test_custom_base_url(self) -> None:
        """Test custom base URL."""
        fetcher = GitFetcher(base_url="https://example.com/repos/")
        assert fetcher.base_url == "https://example.com/repos"

    def test_build_url(self) -> None:
        """Test URL building for a package."""
        fetcher = GitFetcher()
        url = fetcher.build_url("nova")
        assert url == f"{LAUNCHPAD_BASE_URL}/nova"

    def test_build_url_custom_base(self) -> None:
        """Test URL building with custom base."""
        fetcher = GitFetcher(base_url="https://git.example.com")
        url = fetcher.build_url("glance")
        assert url == "https://git.example.com/glance"


class TestFindBranchForSeries:
    """Tests for branch selection logic."""

    def test_exact_ubuntu_openstack_match(self) -> None:
        """Test finding ubuntu/<series>-<openstack> branch."""
        fetcher = GitFetcher()
        branches = ["master", "ubuntu/jammy", "ubuntu/jammy-caracal", "stable/caracal"]
        result = fetcher.find_branch_for_series(branches, "jammy", "caracal")
        assert result == "ubuntu/jammy-caracal"

    def test_ubuntu_series_match(self) -> None:
        """Test finding ubuntu/<series> branch when no openstack combo."""
        fetcher = GitFetcher()
        branches = ["master", "ubuntu/noble", "stable/caracal"]
        result = fetcher.find_branch_for_series(branches, "noble", "caracal")
        assert result == "ubuntu/noble"

    def test_stable_openstack_match(self) -> None:
        """Test finding stable/<openstack> branch."""
        fetcher = GitFetcher()
        branches = ["master", "stable/caracal"]
        result = fetcher.find_branch_for_series(branches, "noble", "caracal")
        assert result == "stable/caracal"

    def test_master_fallback(self) -> None:
        """Test falling back to master."""
        fetcher = GitFetcher()
        branches = ["master", "feature-branch"]
        result = fetcher.find_branch_for_series(branches, "noble", "caracal")
        assert result == "master"

    def test_main_fallback(self) -> None:
        """Test falling back to main when no master."""
        fetcher = GitFetcher()
        branches = ["main", "feature-branch"]
        result = fetcher.find_branch_for_series(branches, "noble", "caracal")
        assert result == "main"

    def test_no_match(self) -> None:
        """Test when no matching branch found."""
        fetcher = GitFetcher()
        branches = ["develop", "feature-x"]
        result = fetcher.find_branch_for_series(branches, "noble", "caracal")
        assert result is None


class TestFetchPackageOffline:
    """Tests for offline mode behavior."""

    def test_offline_repo_exists(self, tmp_path: Path) -> None:
        """Test offline mode when repo already exists."""
        # Create a fake git repo
        pkg_dir = tmp_path / "nova"
        git_dir = pkg_dir / ".git"
        git_dir.mkdir(parents=True)

        fetcher = GitFetcher()
        with patch.object(fetcher, "_list_branches", return_value=["master", "ubuntu/noble"]):
            result = fetcher.fetch_package("nova", tmp_path, offline=True)

        assert result.error is None
        assert result.branches == ["master", "ubuntu/noble"]

    def test_offline_repo_missing(self, tmp_path: Path) -> None:
        """Test offline mode when repo doesn't exist."""
        fetcher = GitFetcher()
        result = fetcher.fetch_package("nova", tmp_path, offline=True)

        assert result.error == "Repository not found in offline mode"
        assert result.branches == []


class TestFetchPackageOnline:
    """Tests for online fetch behavior."""

    def test_clone_new_repo(self, tmp_path: Path) -> None:
        """Test cloning a new repository."""
        fetcher = GitFetcher()

        mock_repo = MagicMock()
        mock_repo.remotes.origin.refs = []

        with patch("git.Repo.clone_from", return_value=mock_repo) as mock_clone:
            result = fetcher.fetch_package("nova", tmp_path, offline=False)

        mock_clone.assert_called_once()
        assert result.cloned is True
        assert result.error is None

    def test_update_existing_repo(self, tmp_path: Path) -> None:
        """Test updating an existing repository."""
        # Create a fake git repo
        pkg_dir = tmp_path / "nova"
        git_dir = pkg_dir / ".git"
        git_dir.mkdir(parents=True)

        mock_repo = MagicMock()
        mock_origin = MagicMock()
        mock_repo.remotes.origin = mock_origin
        mock_origin.refs = []

        fetcher = GitFetcher()

        with patch("git.Repo", return_value=mock_repo):
            result = fetcher.fetch_package("nova", tmp_path, offline=False)

        mock_origin.fetch.assert_called_once_with(prune=True)
        assert result.updated is True
        assert result.error is None

    def test_clone_failure(self, tmp_path: Path) -> None:
        """Test handling clone failure."""
        import git

        fetcher = GitFetcher()

        with patch("git.Repo.clone_from", side_effect=git.GitCommandError("clone", "error")):
            result = fetcher.fetch_package("nova", tmp_path, offline=False)

        assert result.error is not None
        assert "Clone failed" in result.error
        assert result.cloned is False

    def test_fetch_failure(self, tmp_path: Path) -> None:
        """Test handling fetch failure on existing repo."""
        import git

        # Create a fake git repo
        pkg_dir = tmp_path / "nova"
        git_dir = pkg_dir / ".git"
        git_dir.mkdir(parents=True)

        mock_repo = MagicMock()
        mock_origin = MagicMock()
        mock_repo.remotes.origin = mock_origin
        mock_origin.fetch.side_effect = git.GitCommandError("fetch", "error")

        fetcher = GitFetcher()

        with patch("git.Repo", return_value=mock_repo):
            result = fetcher.fetch_package("nova", tmp_path, offline=False)

        assert result.error is not None
        assert "Fetch failed" in result.error
        assert result.updated is False


class TestLocking:
    """Tests for file-based locking."""

    def test_lock_file_created(self, tmp_path: Path) -> None:
        """Test that lock file is used during fetch."""
        fetcher = GitFetcher()

        mock_repo = MagicMock()
        mock_repo.remotes.origin.refs = []

        with patch("git.Repo.clone_from", return_value=mock_repo):
            result = fetcher.fetch_package("nova", tmp_path, offline=False)

        assert result.cloned is True
        # Lock file should be cleaned up after fetch
        lock_path = tmp_path / ".nova.lock"
        assert not lock_path.exists()

    def test_lock_timeout(self, tmp_path: Path) -> None:
        """Test lock timeout behavior."""
        import fcntl

        # Create and hold a lock
        lock_path = tmp_path / ".nova.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = lock_path.open("w")
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            # Create fetcher with very short timeout
            fetcher = GitFetcher(lock_timeout=1)
            result = fetcher.fetch_package("nova", tmp_path, offline=False)

            assert result.error is not None
            assert "Timeout" in result.error
            assert result.was_locked is True
        finally:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            lock_fd.close()


class TestFetchAndCheckout:
    """Tests for fetch_and_checkout method."""

    def test_fetch_and_checkout_success(self, tmp_path: Path) -> None:
        """Test successful fetch and checkout."""
        # Create a fake git repo
        pkg_dir = tmp_path / "nova"
        git_dir = pkg_dir / ".git"
        git_dir.mkdir(parents=True)

        mock_repo = MagicMock()
        mock_origin = MagicMock()
        mock_repo.remotes.origin = mock_origin

        # Mock remote refs
        mock_ref = MagicMock()
        mock_ref.name = "origin/ubuntu/noble"
        mock_origin.refs = [mock_ref]

        # Mock heads (no local branches yet)
        mock_repo.heads = []

        fetcher = GitFetcher()

        with patch("git.Repo", return_value=mock_repo):
            result = fetcher.fetch_and_checkout(
                "nova",
                tmp_path,
                ubuntu_series="noble",
                openstack_series="caracal",
                offline=False,
            )

        assert result.updated is True
        assert result.error is None
        # Should have called checkout
        mock_repo.git.checkout.assert_called()

    def test_fetch_and_checkout_offline_missing(self, tmp_path: Path) -> None:
        """Test fetch_and_checkout in offline mode with missing repo."""
        fetcher = GitFetcher()
        result = fetcher.fetch_and_checkout(
            "nova",
            tmp_path,
            ubuntu_series="noble",
            openstack_series="caracal",
            offline=True,
        )

        assert result.error == "Repository not found in offline mode"

    def test_fetch_and_checkout_with_existing_local_branch(self, tmp_path: Path) -> None:
        """Test checkout when local branch already exists."""
        # Create a fake git repo
        pkg_dir = tmp_path / "nova"
        git_dir = pkg_dir / ".git"
        git_dir.mkdir(parents=True)

        mock_repo = MagicMock()
        mock_origin = MagicMock()
        mock_repo.remotes.origin = mock_origin

        # Mock remote refs
        mock_ref = MagicMock()
        mock_ref.name = "origin/ubuntu/noble"
        mock_origin.refs = [mock_ref]

        # Mock heads with existing local branch - needs to support both iteration and dict access
        mock_head = MagicMock()
        mock_head.name = "ubuntu/noble"
        # Create a special mock that acts like a list when iterated but allows dict access
        mock_heads = MagicMock()
        mock_heads.__iter__ = lambda self: iter([mock_head])
        mock_heads.__getitem__ = lambda self, key: mock_head if key == "ubuntu/noble" else None
        mock_repo.heads = mock_heads

        fetcher = GitFetcher()

        with patch("git.Repo", return_value=mock_repo):
            result = fetcher.fetch_and_checkout(
                "nova",
                tmp_path,
                ubuntu_series="noble",
                openstack_series="caracal",
                offline=False,
            )

        assert result.updated is True
        assert result.error is None
        # Should checkout existing local branch
        mock_head.checkout.assert_called_once()

    def test_fetch_and_checkout_error(self, tmp_path: Path) -> None:
        """Test checkout error handling."""
        import git

        # Create a fake git repo
        pkg_dir = tmp_path / "nova"
        git_dir = pkg_dir / ".git"
        git_dir.mkdir(parents=True)

        mock_repo = MagicMock()
        mock_origin = MagicMock()
        mock_repo.remotes.origin = mock_origin

        # Mock remote refs
        mock_ref = MagicMock()
        mock_ref.name = "origin/ubuntu/noble"
        mock_origin.refs = [mock_ref]

        # Mock heads (no local branches)
        mock_repo.heads = []

        # Mock checkout failure
        mock_repo.git.checkout.side_effect = git.GitCommandError("checkout", "error")

        fetcher = GitFetcher()

        with patch("git.Repo", return_value=mock_repo):
            result = fetcher.fetch_and_checkout(
                "nova",
                tmp_path,
                ubuntu_series="noble",
                openstack_series="caracal",
                offline=False,
            )

        assert result.error is not None
        assert "Checkout of ubuntu/noble failed" in result.error


class TestFetchPackageWithBranch:
    """Tests for fetch_package with branch parameter."""

    def test_checkout_branch_after_clone(self, tmp_path: Path) -> None:
        """Test that branch is checked out after clone."""
        mock_repo = MagicMock()
        mock_origin = MagicMock()
        mock_repo.remotes.origin = mock_origin

        # Mock remote refs to return ubuntu/noble branch
        mock_ref = MagicMock()
        mock_ref.name = "origin/ubuntu/noble"
        mock_origin.refs = [mock_ref]

        fetcher = GitFetcher()

        with patch("git.Repo.clone_from", return_value=mock_repo):
            with patch("git.Repo", return_value=mock_repo):
                result = fetcher.fetch_package(
                    "nova", tmp_path, offline=False, branch="ubuntu/noble"
                )

        assert result.cloned is True
        # Should have attempted checkout
        mock_repo.git.checkout.assert_called_once_with("ubuntu/noble")

    def test_checkout_branch_failure(self, tmp_path: Path) -> None:
        """Test handling checkout failure."""
        import git

        mock_repo = MagicMock()
        mock_origin = MagicMock()
        mock_repo.remotes.origin = mock_origin

        # Mock remote refs
        mock_ref = MagicMock()
        mock_ref.name = "origin/ubuntu/noble"
        mock_origin.refs = [mock_ref]

        # Mock checkout failure
        mock_repo.git.checkout.side_effect = git.GitCommandError("checkout", "error")

        fetcher = GitFetcher()

        with patch("git.Repo.clone_from", return_value=mock_repo):
            with patch("git.Repo", return_value=mock_repo):
                result = fetcher.fetch_package(
                    "nova", tmp_path, offline=False, branch="ubuntu/noble"
                )

        assert result.error is not None
        assert "Checkout failed" in result.error
