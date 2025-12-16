# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Tests for Git repository management."""

from pathlib import Path
from unittest.mock import MagicMock, Mock, PropertyMock, patch

import pytest
from git import Repo
from git.exc import GitCommandError

from packastack.exceptions import RepositoryError
from packastack.git.repo import RepoManager


@pytest.fixture
def mock_repo():
    """Create a mock Git repository."""
    repo = MagicMock(spec=Repo)
    repo.heads = []
    repo.tags = []
    repo.remotes = {"origin": MagicMock()}
    repo.head = MagicMock()
    repo.git = MagicMock()
    return repo


def test_repo_manager_init_with_path():
    """Test RepoManager initialization with path."""
    mgr = RepoManager(path="/tmp/test")
    assert mgr.path == Path("/tmp/test")
    assert mgr.url is None
    assert mgr.repo is None


def test_repo_manager_init_with_url():
    """Test RepoManager initialization with URL."""
    mgr = RepoManager(url="https://github.com/test/repo")
    assert mgr.url == "https://github.com/test/repo"
    assert mgr.path is None
    assert mgr.repo is None


def test_repo_manager_init_no_args():
    """Test RepoManager initialization with no arguments raises error."""
    with pytest.raises(ValueError, match="Either path or url must be provided"):
        RepoManager()


@patch("packastack.git.repo.Repo")
def test_repo_manager_init_opens_existing(mock_repo_class, tmp_path):
    """Test RepoManager opens existing repository."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    mock_instance = MagicMock(spec=Repo)
    mock_repo_class.return_value = mock_instance

    mgr = RepoManager(path=str(repo_path))

    assert mgr.repo == mock_instance
    # Repo should be called with path as a Path (not a string) in simplified code
    call_arg = mock_repo_class.call_args[0][0]
    assert call_arg == repo_path


@patch("packastack.git.repo.Repo")
def test_repo_manager_init_open_fails(mock_repo_class, tmp_path):
    """Test RepoManager when opening existing path fails."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    # Make Repo() raise an exception
    mock_repo_class.side_effect = Exception("Not a git repo")

    mgr = RepoManager(path=str(repo_path))

    # Should not raise, repo should be None
    assert mgr.repo is None
    assert mgr.path == repo_path


@patch("packastack.git.repo.Repo.clone_from")
def test_clone_success(mock_clone, tmp_path):
    """Test successful repository clone."""
    dest = tmp_path / "repo"
    mock_instance = MagicMock(spec=Repo)
    mock_clone.return_value = mock_instance

    mgr = RepoManager(url="https://github.com/test/repo")
    mgr.path = dest
    mgr.clone()

    assert mgr.repo == mock_instance
    assert mgr.path == dest
    mock_clone.assert_called_once_with("https://github.com/test/repo", dest)


def test_clone_no_url():
    """Test clone without URL raises error."""
    mgr = RepoManager(path="/tmp/test")
    with pytest.raises(ValueError, match="url must be set to clone"):
        mgr.clone()


def test_clone_no_path():
    """Clone should require a destination path."""
    mgr = RepoManager(url="https://github.com/test/repo")
    with pytest.raises(ValueError, match="path must be set to clone"):
        mgr.clone()


@patch("packastack.git.repo.Repo.clone_from")
def test_clone_git_error(mock_clone):
    """Test clone with GitCommandError."""
    mock_clone.side_effect = GitCommandError("clone", "error")

    mgr = RepoManager(url="https://github.com/test/repo")
    mgr.path = Path("/tmp/repo")
    with pytest.raises(RepositoryError, match="Failed to clone"):
        mgr.clone()


@patch("packastack.git.repo.Repo")
def test_open_success(mock_repo_class):
    """Test opening existing repository."""
    mock_instance = MagicMock(spec=Repo)
    mock_repo_class.return_value = mock_instance

    mgr = RepoManager(path="/tmp/test")
    mgr.open()

    assert mgr.repo == mock_instance
    assert mgr.path == Path("/tmp/test")


@patch("packastack.git.repo.Repo")
def test_open_error(mock_repo_class):
    """Test open with error."""
    mock_repo_class.side_effect = Exception("error")

    mgr = RepoManager(url="https://github.com/test/repo")
    mgr.path = Path("/tmp/test")
    with pytest.raises(RepositoryError, match="Failed to open repository"):
        mgr.open()


def test_open_without_path():
    """Opening without a configured path should fail."""
    mgr = RepoManager(url="https://github.com/test/repo")
    with pytest.raises(RepositoryError, match="path not set"):
        mgr.open()


def test_fetch_success(mock_repo):
    """Test successful fetch."""
    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    mgr.fetch()

    mock_repo.remotes["origin"].fetch.assert_called_once()


def test_fetch_not_opened():
    """Test fetch without opened repository."""
    mgr = RepoManager(path="/tmp/test")
    with pytest.raises(RepositoryError, match="Repository not opened"):
        mgr.fetch()


def test_fetch_git_error(mock_repo):
    """Test fetch with GitCommandError."""
    mock_repo.remotes["origin"].fetch.side_effect = GitCommandError("fetch", "error")

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    with pytest.raises(RepositoryError, match="Failed to fetch"):
        mgr.fetch()


def test_pull_success(mock_repo):
    """Test successful pull."""
    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    mgr.pull()

    mock_repo.remotes["origin"].pull.assert_called_once()


def test_pull_with_branch(mock_repo):
    """Test pull with specific branch."""
    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    mgr.pull(branch="main")

    mock_repo.remotes["origin"].pull.assert_called_once_with("main")


def test_checkout_success(mock_repo):
    """Test successful checkout."""
    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    mgr.checkout("main")

    mock_repo.git.checkout.assert_called_once_with("main")


def test_checkout_error(mock_repo):
    """Test checkout with error."""
    mock_repo.git.checkout.side_effect = GitCommandError("checkout", "error")

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    with pytest.raises(RepositoryError, match="Failed to checkout"):
        mgr.checkout("main")


def test_create_branch_success(mock_repo):
    """Test successful branch creation."""
    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    mgr.create_branch("feature")

    mock_repo.create_head.assert_called_once_with("feature")


def test_create_branch_with_start_point(mock_repo):
    """Test branch creation with start point."""
    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    mgr.create_branch("feature", "main")

    mock_repo.create_head.assert_called_once_with("feature", "main")


def test_branch_exists_local(mock_repo):
    """Test checking if local branch exists."""
    mock_head = MagicMock()
    mock_head.name = "main"
    mock_repo.heads = [mock_head]

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    assert mgr.branch_exists("main") is True
    assert mgr.branch_exists("feature") is False


def test_branch_exists_remote(mock_repo):
    """Test checking if remote branch exists."""
    mock_ref = MagicMock()
    mock_ref.name = "origin/main"
    mock_repo.remotes["origin"].refs = [mock_ref]

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    assert mgr.branch_exists("main", remote=True) is True


def test_push_success(mock_repo):
    """Test successful push."""
    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    mgr.push()

    mock_repo.remotes["origin"].push.assert_called_once()


def test_push_with_refspec(mock_repo):
    """Test push with refspec."""
    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    mgr.push(refspec="main:main")

    mock_repo.remotes["origin"].push.assert_called_once_with("main:main")


def test_list_branches_local(mock_repo):
    """Test listing local branches."""
    mock_head1 = MagicMock()
    mock_head1.name = "main"
    mock_head2 = MagicMock()
    mock_head2.name = "feature"
    mock_repo.heads = [mock_head1, mock_head2]

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    branches = mgr.list_branches()
    assert branches == ["main", "feature"]


def test_list_branches_remote(mock_repo):
    """Test listing remote branches."""
    mock_ref1 = MagicMock()
    mock_ref1.name = "origin/main"
    mock_ref2 = MagicMock()
    mock_ref2.name = "origin/feature"
    mock_repo.remotes["origin"].refs = [mock_ref1, mock_ref2]

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    branches = mgr.list_branches(remote=True)
    assert branches == ["origin/main", "origin/feature"]


def test_track_remote_branches(mock_repo):
    """Test tracking remote branches."""
    mock_ref1 = MagicMock()
    mock_ref1.name = "origin/main"
    mock_ref2 = MagicMock()
    mock_ref2.name = "origin/feature"
    mock_ref3 = MagicMock()
    mock_ref3.name = "origin/HEAD"

    mock_repo.remotes["origin"].refs = [mock_ref1, mock_ref2, mock_ref3]
    mock_repo.heads = []

    mock_head = MagicMock()
    mock_repo.create_head.return_value = mock_head

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    mgr.track_remote_branches()

    # Should create tracking branches for main and feature, but not HEAD
    assert mock_repo.create_head.call_count == 2


def test_get_current_branch(mock_repo):
    """Test getting current branch."""
    mock_repo.active_branch.name = "main"

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    assert mgr.get_current_branch() == "main"


def test_get_current_branch_detached(mock_repo):
    """Test getting current branch in detached HEAD state."""
    # When accessing active_branch.name in detached HEAD, it raises TypeError
    mock_active_branch = Mock()
    mock_active_branch.name = property(lambda self: (_ for _ in ()).throw(TypeError()))
    type(mock_repo).active_branch = PropertyMock(side_effect=TypeError())

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    with pytest.raises(RepositoryError, match="detached HEAD"):
        mgr.get_current_branch()


def test_list_tags(mock_repo):
    """Test listing tags."""
    mock_tag1 = MagicMock()
    mock_tag1.name = "v1.0"
    mock_tag2 = MagicMock()
    mock_tag2.name = "v2.0"
    mock_repo.tags = [mock_tag1, mock_tag2]

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    tags = mgr.list_tags()
    assert tags == ["v1.0", "v2.0"]


def test_get_head_tags(mock_repo):
    """Test getting tags at HEAD."""
    mock_commit = MagicMock()
    mock_repo.head.commit = mock_commit

    mock_tag1 = MagicMock()
    mock_tag1.name = "v1.0"
    mock_tag1.commit = mock_commit

    mock_tag2 = MagicMock()
    mock_tag2.name = "v2.0"
    mock_tag2.commit = MagicMock()

    mock_repo.tags = [mock_tag1, mock_tag2]

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    tags = mgr.get_head_tags()
    assert tags == ["v1.0"]


def test_git_describe(mock_repo):
    """Test git describe."""
    mock_repo.git.describe.return_value = "v1.0-5-gabcdef"

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    result = mgr.git_describe()
    assert result == "v1.0-5-gabcdef"
    mock_repo.git.describe.assert_called_once_with("--tags")


def test_git_describe_long(mock_repo):
    """Test git describe with long format."""
    mock_repo.git.describe.return_value = "v1.0-5-gabcdef"

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    result = mgr.git_describe(long=True)
    assert result == "v1.0-5-gabcdef"
    mock_repo.git.describe.assert_called_once_with("--long", "--tags")


def test_get_remote_url(mock_repo):
    """Test getting remote URL."""
    mock_repo.remotes["origin"].urls = ["https://github.com/test/repo"]

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    url = mgr.get_remote_url()
    assert url == "https://github.com/test/repo"


def test_set_remote_url(mock_repo):
    """Test setting remote URL."""
    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    mgr.set_remote_url("https://github.com/new/repo")

    mock_repo.remotes["origin"].set_url.assert_called_once_with(
        "https://github.com/new/repo"
    )


def test_pull_not_opened():
    """Test pull without opened repository."""
    mgr = RepoManager(path="/tmp/test")
    with pytest.raises(RepositoryError, match="Repository not opened"):
        mgr.pull()


def test_pull_git_error(mock_repo):
    """Test pull with GitCommandError."""
    mock_repo.remotes["origin"].pull.side_effect = GitCommandError("pull", "error")

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    with pytest.raises(RepositoryError, match="Failed to pull"):
        mgr.pull()


def test_pull_remote_not_found():
    """Test pull with missing remote."""
    mock_repo = MagicMock(spec=Repo)
    mock_remotes = MagicMock()
    mock_remotes.__getitem__.side_effect = IndexError()
    mock_repo.remotes = mock_remotes

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    with pytest.raises(RepositoryError, match="Remote .* not found"):
        mgr.pull()


def test_checkout_not_opened():
    """Test checkout without opened repository."""
    mgr = RepoManager(path="/tmp/test")
    with pytest.raises(RepositoryError, match="Repository not opened"):
        mgr.checkout("main")


def test_create_branch_not_opened():
    """Test create branch without opened repository."""
    mgr = RepoManager(path="/tmp/test")
    with pytest.raises(RepositoryError, match="Repository not opened"):
        mgr.create_branch("feature")


def test_create_branch_error(mock_repo):
    """Test create branch with error."""
    mock_repo.create_head.side_effect = GitCommandError("create_head", "error")

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    with pytest.raises(RepositoryError, match="Failed to create branch"):
        mgr.create_branch("feature")


def test_branch_exists_not_opened():
    """Test branch exists without opened repository."""
    mgr = RepoManager(path="/tmp/test")
    with pytest.raises(RepositoryError, match="Repository not opened"):
        mgr.branch_exists("main")


def test_push_not_opened():
    """Test push without opened repository."""
    mgr = RepoManager(path="/tmp/test")
    with pytest.raises(RepositoryError, match="Repository not opened"):
        mgr.push()


def test_push_git_error(mock_repo):
    """Test push with GitCommandError."""
    mock_repo.remotes["origin"].push.side_effect = GitCommandError("push", "error")

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    with pytest.raises(RepositoryError, match="Failed to push"):
        mgr.push()


def test_push_remote_not_found():
    """Test push with missing remote."""
    mock_repo = MagicMock(spec=Repo)
    mock_remotes = MagicMock()
    mock_remotes.__getitem__.side_effect = IndexError()
    mock_repo.remotes = mock_remotes

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    with pytest.raises(RepositoryError, match="Remote .* not found"):
        mgr.push()


def test_list_branches_not_opened():
    """Test list branches without opened repository."""
    mgr = RepoManager(path="/tmp/test")
    with pytest.raises(RepositoryError, match="Repository not opened"):
        mgr.list_branches()


def test_track_remote_branches_not_opened():
    """Test track remote branches without opened repository."""
    mgr = RepoManager(path="/tmp/test")
    with pytest.raises(RepositoryError, match="Repository not opened"):
        mgr.track_remote_branches()


def test_track_remote_branches_with_existing_local(mock_repo):
    """Test tracking remote branches when local branch exists."""
    mock_ref1 = MagicMock()
    mock_ref1.name = "origin/main"

    mock_head = MagicMock()
    mock_head.name = "main"

    mock_repo.remotes["origin"].refs = [mock_ref1]
    mock_repo.heads = [mock_head]

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    mgr.track_remote_branches()

    # Should not create branch for main since it already exists
    mock_repo.create_head.assert_not_called()


def test_track_remote_branches_error(mock_repo):
    """Test track remote branches with error."""
    mock_ref1 = MagicMock()
    mock_ref1.name = "origin/main"

    mock_repo.remotes["origin"].refs = [mock_ref1]
    mock_repo.heads = []
    mock_repo.create_head.side_effect = GitCommandError("create_head", "error")

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    # Should print warning but not raise
    mgr.track_remote_branches()


def test_checkout_important_branches_not_opened():
    """Test checkout important branches without opened repository."""
    mgr = RepoManager(path="/tmp/test")
    with pytest.raises(RepositoryError, match="Repository not opened"):
        mgr.checkout_important_branches()


def test_checkout_important_branches_pristine_tar(mock_repo):
    """Test checkout important branches with pristine-tar."""
    mock_ref = MagicMock()
    mock_ref.name = "origin/pristine-tar"
    mock_repo.remotes["origin"].refs = [mock_ref]
    mock_repo.heads = []

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    mgr.checkout_important_branches()

    mock_repo.git.checkout.assert_called()


def test_checkout_important_branches_upstream(mock_repo):
    """Test checkout important branches with upstream."""
    mock_ref1 = MagicMock()
    mock_ref1.name = "origin/upstream-dalmatian"
    mock_ref2 = MagicMock()
    mock_ref2.name = "origin/main"

    mock_repo.remotes["origin"].refs = [mock_ref1, mock_ref2]
    mock_repo.heads = []

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    mgr.checkout_important_branches()

    # Should checkout upstream branch
    mock_repo.git.checkout.assert_called()


def test_checkout_important_branches_no_special_branches(mock_repo):
    """Test checkout important branches when none exist."""
    mock_ref = MagicMock()
    mock_ref.name = "origin/main"

    mock_repo.remotes["origin"].refs = [mock_ref]
    mock_repo.heads = []

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    # Should not raise even if no special branches exist
    mgr.checkout_important_branches()


def test_get_current_branch_not_opened():
    """Test get current branch without opened repository."""
    mgr = RepoManager(path="/tmp/test")
    with pytest.raises(RepositoryError, match="Repository not opened"):
        mgr.get_current_branch()


def test_get_current_branch_attribute_error(mock_repo):
    """Test getting current branch with AttributeError."""
    # Simulate AttributeError when accessing active_branch
    del mock_repo.active_branch

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    with pytest.raises(RepositoryError, match="detached HEAD"):
        mgr.get_current_branch()


def test_list_tags_not_opened():
    """Test list tags without opened repository."""
    mgr = RepoManager(path="/tmp/test")
    with pytest.raises(RepositoryError, match="Repository not opened"):
        mgr.list_tags()


def test_get_head_tags_not_opened():
    """Test get head tags without opened repository."""
    mgr = RepoManager(path="/tmp/test")
    with pytest.raises(RepositoryError, match="Repository not opened"):
        mgr.get_head_tags()


def test_git_describe_not_opened():
    """Test git describe without opened repository."""
    mgr = RepoManager(path="/tmp/test")
    with pytest.raises(RepositoryError, match="Repository not opened"):
        mgr.git_describe()


def test_git_describe_error(mock_repo):
    """Test git describe with error."""
    mock_repo.git.describe.side_effect = GitCommandError("describe", "error")

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    with pytest.raises(RepositoryError, match="git describe failed"):
        mgr.git_describe()


def test_get_remote_url_not_opened():
    """Test get remote URL without opened repository."""
    mgr = RepoManager(path="/tmp/test")
    with pytest.raises(RepositoryError, match="Repository not opened"):
        mgr.get_remote_url()


def test_get_remote_url_not_found():
    """Test get remote URL with missing remote."""
    mock_repo = MagicMock(spec=Repo)
    mock_remotes = MagicMock()
    mock_remotes.__getitem__.side_effect = KeyError()
    mock_repo.remotes = mock_remotes

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    with pytest.raises(RepositoryError, match="Remote .* not found"):
        mgr.get_remote_url()


def test_set_remote_url_not_opened():
    """Test set remote URL without opened repository."""
    mgr = RepoManager(path="/tmp/test")
    with pytest.raises(RepositoryError, match="Repository not opened"):
        mgr.set_remote_url("https://github.com/new/repo")


def test_set_remote_url_not_found():
    """Test set remote URL with missing remote."""
    mock_repo = MagicMock(spec=Repo)
    mock_remotes = MagicMock()
    mock_remotes.__getitem__.side_effect = KeyError()
    mock_repo.remotes = mock_remotes

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    with pytest.raises(RepositoryError, match="Remote .* not found"):
        mgr.set_remote_url("https://github.com/new/repo")


def test_track_remote_branches_general_error():
    """Test track remote branches with general exception."""
    mock_repo = MagicMock(spec=Repo)
    mock_remotes = MagicMock()
    mock_remotes.__getitem__.side_effect = Exception("Something went wrong")
    mock_repo.remotes = mock_remotes

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    with pytest.raises(RepositoryError, match="Failed to track remote branches"):
        mgr.track_remote_branches()


def test_checkout_important_branches_checkout_error(mock_repo):
    """Test checkout important branches when checkout fails."""
    mock_ref = MagicMock()
    mock_ref.name = "origin/pristine-tar"
    mock_repo.remotes["origin"].refs = [mock_ref]
    mock_repo.heads = []
    mock_repo.git.checkout.side_effect = GitCommandError("checkout", "error")

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    # Should not raise even if checkout fails
    mgr.checkout_important_branches()


def test_checkout_important_branches_upstream_error(mock_repo):
    """Test checkout important branches when upstream checkout fails."""
    mock_ref1 = MagicMock()
    mock_ref1.name = "origin/upstream-dalmatian"

    mock_repo.remotes["origin"].refs = [mock_ref1]
    mock_repo.heads = []
    mock_repo.git.checkout.side_effect = GitCommandError("checkout", "error")

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    # Should not raise even if checkout fails
    mgr.checkout_important_branches()


def test_fetch_remote_not_found():
    """Test fetch with missing remote."""
    mock_repo = MagicMock(spec=Repo)
    mock_remotes = MagicMock()
    mock_remotes.__getitem__.side_effect = IndexError()
    mock_repo.remotes = mock_remotes

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    with pytest.raises(RepositoryError, match="Remote .* not found"):
        mgr.fetch()


def test_commit_not_opened():
    """Test commit without opened repository."""
    mgr = RepoManager(path="/tmp/test")
    with pytest.raises(
        RepositoryError,
        match="Repository not opened",
    ):
        mgr.commit("msg", ["file1"])


def test_commit_missing_user_config(mock_repo):
    """Test commit when user.name or user.email is missing."""
    # config_reader returns object with get_value
    config = MagicMock()
    config.get_value.side_effect = [None, "email@example.com"]
    mock_repo.config_reader.return_value = config

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    with pytest.raises(
        RepositoryError,
        match="Git user.name and user.email must be set",
    ):
        mgr.commit("msg", ["file1"])


def test_commit_success(mock_repo):
    """Test successful commit adds files and signs off."""
    # Provide user config
    config = MagicMock()
    config.get_value.side_effect = ["Test User", "user@example.com"]
    mock_repo.config_reader.return_value = config

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    mgr.commit("Test message", ["file1", "file2"])

    mock_repo.index.add.assert_called_once_with(["file1", "file2"])
    mock_repo.index.commit.assert_called_once()


def test_commit_git_error(mock_repo):
    """Test commit raises RepositoryError when index.commit fails."""
    config = MagicMock()
    config.get_value.side_effect = ["Test User", "user@example.com"]
    mock_repo.config_reader.return_value = config
    mock_repo.index.add.side_effect = GitCommandError("add", "error")

    mgr = RepoManager(path="/tmp/test")
    mgr.repo = mock_repo

    with pytest.raises(RepositoryError, match="Failed to commit changes"):
        mgr.commit("msg", ["file1"])
