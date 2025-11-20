# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Tests for GitBuildPackage."""

import subprocess
from unittest.mock import MagicMock, Mock, patch

import pytest

from packastack.exceptions import DebianError
from packastack.gbp import GitBuildPackage


@pytest.fixture
def temp_repo(tmp_path):
    """Create a temporary repository."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    debian_dir = repo_path / "debian"
    debian_dir.mkdir()
    # Initialize a git repository so RepoManager can open it during tests
    import subprocess as _sub
    _sub.run(["git", "init"], cwd=str(repo_path), check=True)
    # Create initial commit so master exists
    (repo_path / "README").write_text("initial")
    _sub.run(["git", "add", "README"], cwd=str(repo_path), check=True)
    # Disable gpg signing in tests to avoid failures where the environment
    # enables signing but no key is available.
    _sub.run([
        "git",
        "-c",
        "user.name=Test User",
        "-c",
        "user.email=test@example.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "init",
    ], cwd=str(repo_path), check=True)
    # Ensure a 'master' branch exists for tests that expect it
    _sub.run(["git", "branch", "master"], cwd=str(repo_path), check=True)
    return repo_path


def test_git_build_package_init(temp_repo):
    """Test GitBuildPackage initialization."""
    mgr = GitBuildPackage(str(temp_repo))
    assert mgr.repo_path == temp_repo


def test_git_build_package_init_nonexistent():
    """Test GitBuildPackage initialization with nonexistent path."""
    with pytest.raises(DebianError, match="Repository path not found"):
        GitBuildPackage("/nonexistent/path")


@patch("subprocess.run")
def test_import_orig_success(mock_run, temp_repo, tmp_path):
    """Test successful gbp import-orig."""
    mock_run.return_value = Mock(returncode=0, stdout="Success", stderr="")

    tarball = tmp_path / "test_1.0.orig.tar.gz"
    tarball.write_text("fake tarball")

    mgr = GitBuildPackage(str(temp_repo))
    mgr.import_orig(str(tarball), merge_mode="merge")

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert args[0] == "gbp"
    assert args[1] == "import-orig"
    assert "--merge-mode" in args
    assert "merge" in args
    assert "--no-interactive" in args
    assert str(tarball) in args


@patch("subprocess.run")
def test_import_orig_interactive(mock_run, temp_repo, tmp_path):
    """Test gbp import-orig with interactive mode."""
    mock_run.return_value = Mock(returncode=0, stdout="Success", stderr="")

    tarball = tmp_path / "test_1.0.orig.tar.gz"
    tarball.write_text("fake tarball")

    mgr = GitBuildPackage(str(temp_repo))
    mgr.import_orig(str(tarball), merge_mode="merge", interactive=True)

    args = mock_run.call_args[0][0]
    assert "--no-interactive" not in args


@patch("subprocess.run")
def test_import_orig_custom_merge_mode(mock_run, temp_repo, tmp_path):
    """Test gbp import-orig with custom merge mode."""
    mock_run.return_value = Mock(returncode=0, stdout="Success", stderr="")

    tarball = tmp_path / "test_1.0.orig.tar.gz"
    tarball.write_text("fake tarball")

    mgr = GitBuildPackage(str(temp_repo))
    mgr.import_orig(str(tarball), merge_mode="merge")

    args = mock_run.call_args[0][0]
    assert "merge" in args


def test_import_orig_tarball_not_found(temp_repo):
    """Test gbp import-orig with nonexistent tarball."""
    mgr = GitBuildPackage(str(temp_repo))

    with pytest.raises(DebianError, match="Tarball not found"):
        mgr.import_orig("/nonexistent/tarball.tar.gz")


@patch("subprocess.run")
def test_import_orig_command_error(mock_run, temp_repo, tmp_path):
    """Test gbp import-orig with command error."""
    mock_run.side_effect = subprocess.CalledProcessError(
        1, ["gbp", "import-orig"], stderr="error"
    )

    tarball = tmp_path / "test_1.0.orig.tar.gz"
    tarball.write_text("fake tarball")

    mgr = GitBuildPackage(str(temp_repo))

    with pytest.raises(DebianError, match="gbp import-orig failed"):
        mgr.import_orig(str(tarball), merge_mode="merge")


@patch("subprocess.run")
def test_import_orig_gbp_not_found(mock_run, temp_repo, tmp_path):
    """Test gbp import-orig when gbp not installed."""
    mock_run.side_effect = FileNotFoundError()

    tarball = tmp_path / "test_1.0.orig.tar.gz"
    tarball.write_text("fake tarball")

    mgr = GitBuildPackage(str(temp_repo))

    with pytest.raises(DebianError, match="gbp command not found"):
        mgr.import_orig(str(tarball), merge_mode="merge")


@patch("subprocess.run")
def test_import_orig_no_merge_mode(mock_run, temp_repo, tmp_path):
    """Test gbp import-orig with no merge mode."""
    mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

    tarball = tmp_path / "test_1.0.orig.tar.gz"
    tarball.write_text("fake tarball")

    mgr = GitBuildPackage(str(temp_repo))
    mgr.import_orig(str(tarball), merge_mode=None)

    args = mock_run.call_args[0][0]
    assert "--merge-mode" not in args


@patch("subprocess.run")
def test_import_orig_no_stdout(mock_run, temp_repo, tmp_path):
    """Test gbp import-orig with no stdout."""
    mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

    tarball = tmp_path / "test_1.0.orig.tar.gz"
    tarball.write_text("fake tarball")

    mgr = GitBuildPackage(str(temp_repo))
    mgr.import_orig(str(tarball), merge_mode="merge")

    # Should not raise, just no output


@patch("subprocess.run")
def test_import_orig_no_stderr(mock_run, temp_repo, tmp_path):
    """Test gbp import-orig error with no stderr."""
    mock_run.side_effect = subprocess.CalledProcessError(1, ["gbp"], stderr=None)

    tarball = tmp_path / "test_1.0.orig.tar.gz"
    tarball.write_text("fake tarball")

    mgr = GitBuildPackage(str(temp_repo))

    with pytest.raises(DebianError, match="gbp import-orig failed"):
        mgr.import_orig(str(tarball), merge_mode="merge")


def test_update_gbp_conf_new_file(temp_repo):
    """Test updating gbp.conf when file doesn't exist."""
    mgr = GitBuildPackage(str(temp_repo))
    mgr.update_gbp_conf("upstream-dalmatian")

    gbp_conf = temp_repo / "debian" / "gbp.conf"
    assert gbp_conf.exists()

    content = gbp_conf.read_text()
    assert "[DEFAULT]" in content
    assert "upstream-branch = upstream-dalmatian" in content


def test_update_gbp_conf_existing_file(temp_repo):
    """Test updating gbp.conf when file exists."""
    gbp_conf = temp_repo / "debian" / "gbp.conf"
    gbp_conf.write_text("[DEFAULT]\nupstream-branch = upstream-caracal\n")

    mgr = GitBuildPackage(str(temp_repo))
    mgr.update_gbp_conf("upstream-dalmatian")

    content = gbp_conf.read_text()
    assert "upstream-branch = upstream-dalmatian" in content
    assert "upstream-caracal" not in content


def test_update_gbp_conf_add_to_existing(temp_repo):
    """Test adding upstream-branch to existing gbp.conf without it."""
    gbp_conf = temp_repo / "debian" / "gbp.conf"
    gbp_conf.write_text("[DEFAULT]\ndebian-branch = debian/master\n")

    mgr = GitBuildPackage(str(temp_repo))
    mgr.update_gbp_conf("upstream-dalmatian")

    content = gbp_conf.read_text()
    assert "upstream-branch = upstream-dalmatian" in content
    assert "debian-branch = debian/master" in content


def test_update_gbp_conf_read_error(temp_repo):
    """Test error when gbp.conf can't be read."""
    gbp_conf = temp_repo / "debian" / "gbp.conf"
    gbp_conf.write_text("[DEFAULT]\n")
    gbp_conf.chmod(0o000)  # Make unreadable

    mgr = GitBuildPackage(str(temp_repo))
    with pytest.raises(DebianError, match="Failed to read gbp.conf"):
        mgr.update_gbp_conf("upstream-dalmatian")

    gbp_conf.chmod(0o644)  # Restore permissions for cleanup


@patch("packastack.gbp.buildpackage.Path.write_text", side_effect=OSError("Permission denied"))
def test_update_gbp_conf_write_error(mock_write_text, temp_repo):
    """Test error when gbp.conf can't be written."""
    mgr = GitBuildPackage(str(temp_repo))

    with pytest.raises(DebianError, match="Failed to write gbp.conf"):
        mgr.update_gbp_conf("upstream-dalmatian")


def test_update_gbp_conf_no_default_section(temp_repo):
    """Test updating gbp.conf when no DEFAULT section exists."""
    gbp_conf = temp_repo / "debian" / "gbp.conf"
    gbp_conf.write_text("# Some comment\\n")

    mgr = GitBuildPackage(str(temp_repo))
    mgr.update_gbp_conf("upstream-dalmatian")

    content = gbp_conf.read_text()
    assert "upstream-branch = upstream-dalmatian" in content
    assert "[DEFAULT]" in content


def test_update_gbp_conf_no_change(temp_repo):
    """Test that updating to the same value results in no change."""
    gbp_conf = temp_repo / "debian" / "gbp.conf"
    gbp_conf.write_text("[DEFAULT]\nupstream-branch = upstream-dalmatian\n")

    mgr = GitBuildPackage(str(temp_repo))
    changed = mgr.update_gbp_conf("upstream-dalmatian")

    assert changed is False
    assert "upstream-branch = upstream-dalmatian" in gbp_conf.read_text()


def test_import_orig_switches_branch(tmp_path):
    """Test that import_orig switches to master and back when starting on another branch."""
    import subprocess as _sub
    from unittest.mock import Mock

    from packastack.gbp import GitBuildPackage

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _sub.run(["git", "init"], cwd=str(repo_path), check=True)
    (repo_path / "README").write_text("initial")
    _sub.run(["git", "add", "README"], cwd=str(repo_path), check=True)
    # Commit with GPG disabled
    _sub.run([
        "git",
        "-c",
        "user.name=Test User",
        "-c",
        "user.email=test@example.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "init",
    ], cwd=str(repo_path), check=True)
    # Ensure master exists and create a feature branch
    _sub.run(["git", "branch", "master"], cwd=str(repo_path), check=True)
    _sub.run(["git", "checkout", "-b", "feature-branch"], cwd=str(repo_path), check=True)

    tarball = tmp_path / "test_1.0.orig.tar.gz"
    tarball.write_text("fake tarball")

    mgr = GitBuildPackage(str(repo_path))
    # Patch subprocess.run used by import_orig
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0, stdout="Success", stderr="")
        mgr.import_orig(str(tarball), merge_mode="merge")

    # Ensure we're back on the original branch
    from packastack.git.repo import RepoManager
    repo = RepoManager(path=repo_path)
    assert repo.get_current_branch() == "feature-branch"


def test_import_orig_no_switch_when_on_master(tmp_path):
    """Ensure import_orig doesn't switch branches when already on master."""
    import subprocess as _sub
    from unittest.mock import Mock

    from packastack.gbp import GitBuildPackage
    from packastack.git.repo import RepoManager

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _sub.run(["git", "init"], cwd=str(repo_path), check=True)
    (repo_path / "README").write_text("initial")
    _sub.run(["git", "add", "README"], cwd=str(repo_path), check=True)
    # Commit with GPG disabled
    _sub.run([
        "git",
        "-c",
        "user.name=Test User",
        "-c",
        "user.email=test@example.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "init",
    ], cwd=str(repo_path), check=True)
    # Ensure master exists and check it out
    _sub.run(["git", "branch", "master"], cwd=str(repo_path), check=True)
    _sub.run(["git", "checkout", "master"], cwd=str(repo_path), check=True)

    tarball = tmp_path / "test_1.0.orig.tar.gz"
    tarball.write_text("fake tarball")

    mgr = GitBuildPackage(str(repo_path))
    # Patch subprocess.run used by import_orig
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0, stdout="Success", stderr="")
        mgr.import_orig(str(tarball), merge_mode="merge")

    repo = RepoManager(path=repo_path)
    assert repo.get_current_branch() == "master"


@patch("packastack.gbp.buildpackage.RepoManager")
@patch("subprocess.run")
def test_import_orig_mocked_repo_no_switch(mock_run, mock_repo_mgr, tmp_path):
    """Test import_orig doesn't switch branches when RepoManager reports 'master'."""
    mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
    mock_mgr = MagicMock()
    mock_mgr.get_current_branch.return_value = "master"
    mock_repo_mgr.return_value = mock_mgr

    # Create a minimal repo path
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    mgr = GitBuildPackage(str(repo_path))
    tarball = tmp_path / "test_1.0.orig.tar.gz"
    tarball.write_text("fake tarball")

    mgr.import_orig(str(tarball), merge_mode="merge")

    # Should not try to checkout master
    mock_mgr.checkout.assert_not_called()


@patch("packastack.gbp.buildpackage.RepoManager")
@patch("subprocess.run")
def test_import_orig_mocked_repo_switch(mock_run, mock_repo_mgr, tmp_path):
    """Test import_orig switches to master when starting on another branch."""
    mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
    mock_mgr = MagicMock()
    mock_mgr.get_current_branch.return_value = "feature-branch"
    mock_repo_mgr.return_value = mock_mgr

    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    mgr = GitBuildPackage(str(repo_path))
    tarball = tmp_path / "test_1.0.orig.tar.gz"
    tarball.write_text("fake tarball")

    mgr.import_orig(str(tarball), merge_mode="merge")

    # Should checkout to master and back to feature-branch
    mock_mgr.checkout.assert_any_call("master")
    mock_mgr.checkout.assert_any_call("feature-branch")
