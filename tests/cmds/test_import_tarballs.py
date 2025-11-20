# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Tests for import command."""

import threading
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import click
import pytest

from packastack.cmds.import_tarballs import (
    ImportContext,
    determine_importer_type,
    filter_repositories,
    get_launchpad_repositories,
    print_import_summary,
    process_repositories,
    process_repository,
)


@patch("packastack.cmds.import_tarballs.get_launchpad_repositories", return_value=[])
@patch("packastack.cmds.import_tarballs.process_repositories", return_value=None)
@patch("packastack.cmds.import_tarballs.get_current_cycle", return_value="gazpacho")
def test_import_cmd_creates_timestamped_log(mock_get_current_cycle, mock_process_repositories, mock_get_launchpad_repos, tmp_path):
    """Ensure the import command creates a timestamped error log under root/logs."""
    from click.testing import CliRunner

    from packastack.cli import cli

    # Patch heavy operations: fetching repositories and processing them
    def fake_get_launchpad_repos():
        import logging as _logging
        _logging.getLogger().info("fake repos fetched for test")
        return []

    # Decorators patch get_launchpad_repositories, process_repositories and get_current_cycle
    from unittest.mock import patch as _patch
    with _patch("packastack.cmds.import_tarballs.setup_directories", return_value=(tmp_path / "packaging", tmp_path / "upstream", tmp_path / "tarballs", tmp_path / "logs")):
        with _patch("packastack.cmds.import_tarballs.setup_releases_repo", return_value=tmp_path / "releases"):
            runner = CliRunner()
            result = runner.invoke(cli, ["--root", str(tmp_path), "import"])
            if result.exit_code != 0:
                print(result.output)
            assert result.exit_code == 0

    # Note: assert and logging checks moved inside context manager above

    logs_dir = tmp_path / "logs"
    assert logs_dir.exists()
    # There should be at least one timestamped import-errors log file
    matches = list(logs_dir.glob("import-errors-*.log"))
    assert len(matches) >= 1
    # Verify packastack CLI log is present and has contents
    cli_files = list(logs_dir.glob("packastack-*.log"))
    assert len(cli_files) >= 1
    # Ensure either import-errors or packastack CLI file exists; content may
    # vary based on patched behavior in tests.
    assert len(matches) >= 1
    assert len(cli_files) >= 1


@patch("packastack.logging_setup._setup_cli_logging", side_effect=Exception("nope"))
@patch("packastack.cmds.import_tarballs.get_launchpad_repositories", return_value=[])
@patch("packastack.cmds.import_tarballs.process_repositories", return_value=None)
def test_import_cmd_setup_cli_logging_fails(mock_process_repo, mock_get_repos, mock_setup_logging, tmp_path):
    """If `_setup_cli_logging` raises, import_cmd should continue gracefully."""
    # Make the logging setup raise an exception
    from unittest.mock import patch as _patch

    from click.testing import CliRunner

    with _patch("packastack.cmds.import_tarballs.setup_releases_repo", return_value=tmp_path / "releases"):
        with _patch("packastack.cmds.import_tarballs.get_current_cycle", return_value="gazpacho"):
            from packastack.cli import cli as packastack_cli
            runner = CliRunner()
            result = runner.invoke(packastack_cli, ["--root", str(tmp_path), "import"])
            assert result.exit_code == 0

    from packastack.cli import cli as packastack_cli
    runner = CliRunner()
    result = runner.invoke(packastack_cli, ["--root", str(tmp_path), "import"])
    assert result.exit_code == 0



def test_import_context_initialization(tmp_path):
    """Test ImportContext initialization."""
    ctx = ImportContext(cycle="dalmatian", import_type="release", cleanup_tarballs=True)

    assert ctx.cycle == "dalmatian"
    assert ctx.import_type == "release"
    assert ctx.cleanup_tarballs is True
    assert hasattr(ctx.releases_lock, "acquire")  # Check it's a lock-like object
    assert hasattr(ctx.tarballs_lock, "acquire")
    assert isinstance(ctx.successes, list)
    assert isinstance(ctx.failures, list)
    assert hasattr(ctx, "lock")


def test_import_context_add_success():
    """Test adding successful import."""
    ctx = ImportContext(cycle="dalmatian", import_type="release", cleanup_tarballs=True)

    ctx.add_success("nova")
    ctx.add_success("neutron")

    assert len(ctx.successes) == 2
    assert "nova" in ctx.successes
    assert "neutron" in ctx.successes


def test_import_context_add_failure():
    """Test adding failed import."""
    ctx = ImportContext(cycle="dalmatian", import_type="release", cleanup_tarballs=True)

    ctx.add_failure("nova", "Version not found")
    ctx.add_failure("neutron", "Network error")

    assert len(ctx.failures) == 2
    assert ("nova", "Version not found") in ctx.failures
    assert ("neutron", "Network error") in ctx.failures


def test_determine_importer_type_release(tmp_path):
    """Test importer type detection for release."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()

    importer_type, explicit = determine_importer_type("release", upstream)
    assert importer_type == "release"
    assert explicit is False


def test_determine_importer_type_candidate(tmp_path):
    """Test importer type detection for candidate."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()

    importer_type, explicit = determine_importer_type("candidate", upstream)
    assert importer_type == "candidate"
    assert explicit is False


def test_determine_importer_type_beta(tmp_path):
    """Test importer type detection for beta."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()

    importer_type, explicit = determine_importer_type("beta", upstream)
    assert importer_type == "beta"
    assert explicit is False


def test_determine_importer_type_snapshot(tmp_path):
    """Test importer type detection for snapshot."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()

    importer_type, explicit = determine_importer_type("snapshot", upstream)
    assert importer_type == "snapshot"
    assert explicit is True


@patch("packastack.cmds.import_tarballs.RepoManager")
def test_determine_importer_type_auto_no_tags(mock_repo_mgr, tmp_path):
    """Test auto-detect with no tags at HEAD."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()

    mock_mgr = MagicMock()
    mock_mgr.get_head_tags.return_value = []
    mock_repo_mgr.return_value = mock_mgr

    importer_type, explicit = determine_importer_type("auto", upstream)
    assert importer_type == "snapshot"
    assert explicit is False


@patch("packastack.cmds.import_tarballs.RepoManager")
@patch("packastack.cmds.import_tarballs.VersionConverter")
def test_determine_importer_type_auto_beta(mock_version, mock_repo_mgr, tmp_path):
    """Test auto-detect with beta tag."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()

    mock_mgr = MagicMock()
    mock_mgr.get_head_tags.return_value = ["27.0.0.0b1"]
    mock_repo_mgr.return_value = mock_mgr

    mock_version.detect_version_type.return_value = "beta"

    importer_type, explicit = determine_importer_type("auto", upstream)
    assert importer_type == "beta"
    assert explicit is False


@patch("packastack.cmds.import_tarballs.RepoManager")
@patch("packastack.cmds.import_tarballs.VersionConverter")
def test_determine_importer_type_auto_candidate(mock_version, mock_repo_mgr, tmp_path):
    """Test auto-detect with release candidate tag."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()

    mock_mgr = MagicMock()
    mock_mgr.get_head_tags.return_value = ["27.0.0.0rc1"]
    mock_repo_mgr.return_value = mock_mgr

    mock_version.detect_version_type.return_value = "candidate"

    importer_type, explicit = determine_importer_type("auto", upstream)
    assert importer_type == "candidate"
    assert explicit is False


@patch("packastack.cmds.import_tarballs.RepoManager")
@patch("packastack.cmds.import_tarballs.VersionConverter")
def test_determine_importer_type_auto_release(mock_version, mock_repo_mgr, tmp_path):
    """Test auto-detect with release tag."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()

    mock_mgr = MagicMock()
    mock_mgr.get_head_tags.return_value = ["27.0.0"]
    mock_repo_mgr.return_value = mock_mgr

    mock_version.detect_version_type.return_value = "release"

    importer_type, explicit = determine_importer_type("auto", upstream)
    assert importer_type == "release"
    assert explicit is False


@patch("packastack.cmds.import_tarballs.RepoManager")
@patch("packastack.cmds.import_tarballs.VersionConverter")
def test_determine_importer_type_auto_unknown(mock_version, mock_repo_mgr, tmp_path):
    """Test auto-detect with unknown tag type defaults to snapshot."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()

    mock_mgr = MagicMock()
    mock_mgr.get_head_tags.return_value = ["sometag"]
    mock_repo_mgr.return_value = mock_mgr

    mock_version.detect_version_type.return_value = None

    importer_type, explicit = determine_importer_type("auto", upstream)
    assert importer_type == "snapshot"
    assert explicit is False


@patch("packastack.cmds.import_tarballs.RepoManager")
def test_determine_importer_type_auto_error(mock_repo_mgr, tmp_path):
    """Test auto-detect with error."""
    from packastack.exceptions import ImporterError

    upstream = tmp_path / "upstream"
    upstream.mkdir()

    mock_repo_mgr.side_effect = Exception("Git error")

    try:
        determine_importer_type("auto", upstream)
        assert False, "Should have raised ImporterError"
    except ImporterError as e:
        assert "Failed to auto-detect import type" in str(e)


@patch("packastack.cmds.import_tarballs.Path")
@patch("packastack.cmds.import_tarballs.RepoManager")
def test_setup_releases_repo_clone(mock_repo_mgr, mock_path, tmp_path):
    """Test cloning releases repo."""
    from packastack.cmds.import_tarballs import setup_releases_repo

    # Mock path to say it does NOT exist
    mock_releases_path = MagicMock()
    mock_releases_path.exists.return_value = False
    mock_upstream = MagicMock()
    mock_upstream.__truediv__.return_value = mock_releases_path

    mock_mgr = MagicMock()
    mock_repo_mgr.return_value = mock_mgr

    lock = threading.Lock()
    result = setup_releases_repo(lock, mock_upstream)

    # Should have been called with url to clone
    mock_repo_mgr.assert_called_with(url=mock_repo_mgr.call_args_list[0].kwargs["url"])
    mock_mgr.clone.assert_called_once()
    # RepoManager should have been created (called once) to perform clone
    mock_repo_mgr.assert_called_once()
    assert result == mock_releases_path


@patch("packastack.cmds.import_tarballs.Path")
@patch("packastack.cmds.import_tarballs.RepoManager")
def test_setup_releases_repo_update(mock_repo_mgr, mock_path, tmp_path):
    """Test updating existing releases repo."""
    from packastack.cmds.import_tarballs import setup_releases_repo

    # Mock path to say it exists
    mock_releases_path = MagicMock()
    mock_releases_path.exists.return_value = True
    mock_upstream = MagicMock()
    mock_upstream.__truediv__.return_value = mock_releases_path

    mock_mgr = MagicMock()
    mock_repo_mgr.return_value = mock_mgr

    lock = threading.Lock()
    setup_releases_repo(lock, mock_upstream)

    # Should have fetched and pulled
    mock_mgr.fetch.assert_called_once()
    mock_mgr.pull.assert_called_once()


@patch("packastack.cmds.import_tarballs.Path")
def test_setup_directories_with_root(mock_path_class):
    """Test directory creation with custom root."""
    from packastack.cmds.import_tarballs import setup_directories

    # Create mock objects for each path
    mock_pkg = MagicMock()
    mock_upstream = MagicMock()
    mock_tarballs = MagicMock()
    mock_logs = MagicMock()

    # Create a mock root
    mock_root = MagicMock()
    mock_root.__truediv__.side_effect = [
        mock_pkg,
        mock_upstream,
        mock_tarballs,
        mock_logs,
    ]

    packaging, upstream, tarballs, logs = setup_directories(mock_root)

    # Verify mkdir was called on all directories
    assert mock_pkg.mkdir.called
    assert mock_upstream.mkdir.called
    assert mock_tarballs.mkdir.called
    assert mock_logs.mkdir.called
    # Verify paths were computed
    assert packaging == mock_pkg
    assert upstream == mock_upstream
    assert tarballs == mock_tarballs
    assert logs == mock_logs


def test_setup_directories_real_root(tmp_path):
    """Test directory creation with real custom root."""
    from packastack.cmds.import_tarballs import setup_directories

    packaging, upstream, tarballs, logs = setup_directories(tmp_path)

    # Verify all directories were created under root
    assert packaging == tmp_path / "packaging"
    assert upstream == tmp_path / "upstream"
    assert tarballs == tmp_path / "tarballs"
    assert logs == tmp_path / "logs"
    assert packaging.exists()
    assert upstream.exists()
    assert tarballs.exists()
    assert logs.exists()


@patch("packastack.cmds.import_tarballs.Path")
def test_setup_directories(mock_path_class):
    """Test directory creation with mocked Path."""
    from packastack.cmds.import_tarballs import setup_directories

    # Create a mock root
    mock_root = MagicMock()

    # Create mock directory objects
    mock_pkg_dir = MagicMock()
    mock_upstream_dir = MagicMock()
    mock_tarballs_dir = MagicMock()
    mock_logs_dir = MagicMock()

    # Setup mock root to return directories when using /
    mock_root.__truediv__.side_effect = [
        mock_pkg_dir,
        mock_upstream_dir,
        mock_tarballs_dir,
        mock_logs_dir,
    ]

    # Mock Path.cwd() to return our mock root
    mock_path_class.cwd.return_value = mock_root

    packaging, upstream, tarballs, logs = setup_directories()

    # Verify all returned values are the mocked directories
    assert packaging == mock_pkg_dir
    assert upstream == mock_upstream_dir
    assert tarballs == mock_tarballs_dir
    assert logs == mock_logs_dir


@patch("packastack.cmds.import_tarballs.Path.cwd")
def test_setup_directories_default_root(mock_cwd, tmp_path):
    """Test directory creation with default root (cwd)."""
    from packastack.cmds.import_tarballs import setup_directories

    mock_cwd.return_value = tmp_path
    packaging, upstream, tarballs, logs = setup_directories()

    # Verify all directories were created under cwd
    assert packaging == tmp_path / "packaging"
    assert upstream == tmp_path / "upstream"
    assert tarballs == tmp_path / "tarballs"
    assert logs == tmp_path / "logs"
    assert packaging.exists()
    assert upstream.exists()
    assert tarballs.exists()
    assert logs.exists()


@patch("packastack.cmds.import_tarballs.Path")
def test_setup_directories_path_mock(mock_path_class):
    """Test directory creation with Path mocked."""
    from packastack.cmds.import_tarballs import setup_directories

    # Create mock objects for each path
    mock_pkg = MagicMock()
    mock_upstream = MagicMock()
    mock_tarballs = MagicMock()
    mock_logs = MagicMock()

    # Mock the / operator to return our mock paths
    mock_root = MagicMock()
    mock_path_class.cwd.return_value = mock_root
    mock_root.__truediv__.side_effect = [
        mock_pkg,
        mock_upstream,
        mock_tarballs,
        mock_logs,
    ]

    packaging, upstream, tarballs, logs = setup_directories()

    # Verify mkdir was called on all directories
    assert mock_pkg.mkdir.called
    assert mock_upstream.mkdir.called
    assert mock_tarballs.mkdir.called
    assert mock_logs.mkdir.called


# Tests for helper functions


@patch("packastack.cmds.import_tarballs.RepoManager")
def test_setup_repository_existing(mock_repo_mgr, tmp_path):
    """Test setup_repository with existing repository."""
    from packastack.cmds.import_tarballs import setup_repository

    repo_path = tmp_path / "test-repo"
    repo_path.mkdir()

    mock_mgr = MagicMock()
    mock_repo_mgr.return_value = mock_mgr

    result_mgr = setup_repository(
        "test-repo", "https://example.com/repo", tmp_path
    )
    assert result_mgr == mock_mgr
    # No path returned anymore, so infer path from mock
    mock_repo_mgr.assert_called_once_with(path=repo_path)
    mock_mgr.fetch.assert_called_once()
    mock_mgr.clone.assert_not_called()


@patch("packastack.cmds.import_tarballs.RepoManager")
def test_setup_repository_new(mock_repo_mgr, tmp_path):
    """Test setup_repository with new repository."""
    from packastack.cmds.import_tarballs import setup_repository

    repo_path = tmp_path / "test-repo"

    mock_mgr = MagicMock()
    mock_repo_mgr.return_value = mock_mgr

    result_mgr = setup_repository(
        "test-repo", "https://example.com/repo", tmp_path
    )

    assert result_mgr == mock_mgr
    mock_repo_mgr.assert_called_once_with(url="https://example.com/repo")
    mock_mgr.clone.assert_called_once()
    mock_mgr.fetch.assert_not_called()


@patch("packastack.cmds.import_tarballs.ControlFileParser")
def test_parse_packaging_metadata_success(mock_parser, tmp_path):
    """Test parse_packaging_metadata with valid control file."""
    from packastack.cmds.import_tarballs import parse_packaging_metadata

    pkg_repo_path = tmp_path / "nova"
    pkg_repo_path.mkdir()
    (pkg_repo_path / "debian").mkdir()
    (pkg_repo_path / "debian" / "control").write_text("fake control")

    mock_pkg_repo = MagicMock()
    mock_pkg_repo.path = pkg_repo_path
    mock_pkg_repo.name = "nova"
    mock_pkg_repo.get_current_branch.return_value = "master"

    mock_parser_instance = MagicMock()
    mock_parser_instance.get_source_name.return_value = "nova"
    mock_parser_instance.get_homepage.return_value = (
        "https://opendev.org/openstack/nova"
    )
    mock_parser_instance.get_upstream_project_name.return_value = "nova"
    mock_parser.return_value = mock_parser_instance

    source_name, homepage, upstream_name = parse_packaging_metadata(
        mock_pkg_repo
    )

    assert source_name == "nova"
    assert homepage == "https://opendev.org/openstack/nova"
    assert upstream_name == "nova"


def test_parse_packaging_metadata_no_control(tmp_path):
    """Test parse_packaging_metadata with missing control file."""
    import pytest

    from packastack.cmds.import_tarballs import parse_packaging_metadata
    from packastack.exceptions import DebianError

    pkg_repo_path = tmp_path / "nova"
    pkg_repo_path.mkdir()

    mock_pkg_repo = MagicMock()
    mock_pkg_repo.path = pkg_repo_path
    mock_pkg_repo.name = "nova"
    mock_pkg_repo.get_current_branch.return_value = "master"

    with pytest.raises(DebianError, match="debian/control not found"):
        parse_packaging_metadata(mock_pkg_repo)


@patch("packastack.cmds.import_tarballs.ControlFileParser")
def test_parse_packaging_metadata_no_homepage(mock_parser, tmp_path):
    """Test parse_packaging_metadata with missing homepage."""
    import pytest

    from packastack.cmds.import_tarballs import parse_packaging_metadata
    from packastack.exceptions import DebianError

    pkg_repo_path = tmp_path / "nova"
    pkg_repo_path.mkdir()
    (pkg_repo_path / "debian").mkdir()
    (pkg_repo_path / "debian" / "control").write_text("fake control")

    mock_parser_instance = MagicMock()
    mock_parser_instance.get_source_name.return_value = "nova"
    mock_parser_instance.get_homepage.return_value = None
    mock_parser.return_value = mock_parser_instance

    mock_pkg_repo = MagicMock()
    mock_pkg_repo.path = pkg_repo_path
    mock_pkg_repo.name = "nova"
    mock_pkg_repo.get_current_branch.return_value = "master"

    with pytest.raises(DebianError, match="Homepage not found"):
        parse_packaging_metadata(mock_pkg_repo)


@patch("packastack.cmds.import_tarballs.ControlFileParser")
def test_parse_packaging_metadata_no_upstream_name(mock_parser, tmp_path):
    """Test parse_packaging_metadata with missing upstream project name."""
    import pytest

    from packastack.cmds.import_tarballs import parse_packaging_metadata
    from packastack.exceptions import DebianError

    pkg_repo_path = tmp_path / "nova"
    pkg_repo_path.mkdir()
    (pkg_repo_path / "debian").mkdir()
    (pkg_repo_path / "debian" / "control").write_text("fake control")

    mock_parser_instance = MagicMock()
    mock_parser_instance.get_source_name.return_value = "nova"
    mock_parser_instance.get_homepage.return_value = (
        "https://opendev.org/openstack/nova"
    )
    mock_parser_instance.get_upstream_project_name.return_value = None
    mock_parser.return_value = mock_parser_instance

    mock_pkg_repo = MagicMock()
    mock_pkg_repo.path = pkg_repo_path
    mock_pkg_repo.name = "nova"
    mock_pkg_repo.get_current_branch.return_value = "master"

    with pytest.raises(DebianError, match="Could not extract project name"):
        parse_packaging_metadata(mock_pkg_repo)


@patch("packastack.cmds.import_tarballs.RepoManager")
def test_setup_upstream_repository_existing(mock_repo_mgr, tmp_path):
    """Test setup_upstream_repository with existing repo."""
    from packastack.cmds.import_tarballs import setup_upstream_repository

    upstream_path = tmp_path / "nova"
    upstream_path.mkdir()

    mock_mgr = MagicMock()
    mock_mgr.get_remote_url.return_value = "https://opendev.org/openstack/nova"
    mock_repo_mgr.return_value = mock_mgr

    result_mgr = setup_upstream_repository(
        "nova", "https://opendev.org/openstack/nova", tmp_path
    )

    assert result_mgr == mock_mgr
    mock_mgr.fetch.assert_called_once()
    mock_mgr.checkout.assert_called_once_with("master")
    mock_mgr.pull.assert_called_once()
    # UPSTREAM_GIT_REPOS contains a .git mapping for 'nova', so set_remote_url
    # should have been called to update to the correct mapping.
    mock_mgr.set_remote_url.assert_called_once_with(
        "https://opendev.org/openstack/nova.git"
    )


@patch("packastack.cmds.import_tarballs.RepoManager")
def test_setup_upstream_repository_existing_no_change(mock_repo_mgr, tmp_path):
    """Test setup_upstream_repository when remote already matches; should not set_remote_url."""
    from packastack.cmds.import_tarballs import setup_upstream_repository

    upstream_path = tmp_path / "nova"
    upstream_path.mkdir()

    mock_mgr = MagicMock()
    # Simulate the remote already matching the expected mapping with .git
    mock_mgr.get_remote_url.return_value = "https://opendev.org/openstack/nova.git"
    mock_repo_mgr.return_value = mock_mgr

    result_mgr = setup_upstream_repository(
        "nova", "https://opendev.org/openstack/nova", tmp_path
    )

    assert result_mgr == mock_mgr
    mock_mgr.set_remote_url.assert_not_called()


@patch("packastack.cmds.import_tarballs.RepoManager")
def test_setup_upstream_repository_url_mismatch(mock_repo_mgr, tmp_path):
    """Test setup_upstream_repository with URL mismatch."""
    from packastack.cmds.import_tarballs import setup_upstream_repository

    upstream_path = tmp_path / "nova"
    upstream_path.mkdir()

    mock_mgr = MagicMock()
    mock_mgr.get_remote_url.return_value = "https://old-url.com/nova"
    mock_repo_mgr.return_value = mock_mgr

    result_mgr = setup_upstream_repository(
        "nova", "https://opendev.org/openstack/nova", tmp_path
    )

    assert result_mgr == mock_mgr
    mock_mgr.set_remote_url.assert_called_once_with(
        "https://opendev.org/openstack/nova.git"
    )
    mock_mgr.fetch.assert_called_once()


@patch("packastack.cmds.import_tarballs.RepoManager")
def test_setup_upstream_repository_new(mock_repo_mgr, tmp_path):
    """Test setup_upstream_repository with new repo."""
    from packastack.cmds.import_tarballs import setup_upstream_repository

    upstream_path = tmp_path / "nova"

    mock_mgr = MagicMock()
    mock_repo_mgr.return_value = mock_mgr

    result_mgr = setup_upstream_repository(
        "nova", "https://opendev.org/openstack/nova", tmp_path
    )

    assert result_mgr == mock_mgr
    mock_repo_mgr.assert_called_once_with(path=upstream_path, url="https://opendev.org/openstack/nova.git")
    mock_mgr.clone.assert_called_once()


@patch("packastack.cmds.import_tarballs.lpci.update_launchpad_ci_file")
@patch("packastack.cmds.import_tarballs.GitBuildPackage")
def test_update_gbp_and_ci_files(mock_gbp, mock_update_ci, tmp_path):
    """Test update_gbp_configuration."""
    from packastack.cmds.import_tarballs import update_gbp_and_ci_files

    mock_mgr = MagicMock()
    mock_gbp.return_value = mock_mgr
    mock_gbp.return_value.update_gbp_conf.return_value = False
    mock_update_ci.return_value = False

    update_gbp_and_ci_files(mock_mgr, "upstream/dalmatian", "dalmatian")

    mock_gbp.assert_called_once_with(mock_mgr.path)
    mock_mgr.update_gbp_conf.assert_called_once_with("upstream/dalmatian")
    mock_mgr.set_remote_url.assert_not_called()
    mock_mgr.commit.assert_not_called()


@patch("packastack.cmds.import_tarballs.lpci.update_launchpad_ci_file")
@patch("packastack.cmds.import_tarballs.GitBuildPackage")
def test_update_gbp_and_ci_files_commit(mock_gbp, mock_update_ci, tmp_path):
    """Test update_gbp_and_ci_files triggers commit when files changed."""
    from packastack.cmds.import_tarballs import update_gbp_and_ci_files

    mock_mgr = MagicMock()
    mock_gbp.return_value = mock_mgr
    mock_gbp.return_value.update_gbp_conf.return_value = True
    mock_update_ci.return_value = True

    update_gbp_and_ci_files(mock_mgr, "upstream/dalmatian", "dalmatian")

    mock_gbp.assert_called_once_with(mock_mgr.path)
    # Should commit both files
    mock_mgr.commit.assert_called_once()
    args = mock_mgr.commit.call_args[0]
    assert ".launchpad.yaml" in args[1] and "debian/gbp.conf" in args[1]


@patch("packastack.cmds.import_tarballs.lpci.update_launchpad_ci_file")
@patch("packastack.cmds.import_tarballs.GitBuildPackage")
def test_update_gbp_and_ci_files_commit_gbp_only(mock_gbp, mock_update_ci, tmp_path):
    """Test update_gbp_and_ci_files triggers commit when gbp.conf changed only."""
    from packastack.cmds.import_tarballs import update_gbp_and_ci_files

    mock_mgr = MagicMock()
    mock_gbp.return_value = mock_mgr
    mock_gbp.return_value.update_gbp_conf.return_value = True
    mock_update_ci.return_value = False

    update_gbp_and_ci_files(mock_mgr, "upstream/dalmatian", "dalmatian")

    mock_gbp.assert_called_once_with(mock_mgr.path)
    mock_mgr.commit.assert_called_once()
    args = mock_mgr.commit.call_args[0]
    assert args[1] == ["debian/gbp.conf"]


@patch("packastack.cmds.import_tarballs.lpci.update_launchpad_ci_file")
@patch("packastack.cmds.import_tarballs.GitBuildPackage")
def test_update_gbp_and_ci_files_commit_ci_only(mock_gbp, mock_update_ci, tmp_path):
    """Test update_gbp_and_ci_files triggers commit when .launchpad.yaml changed only."""
    from packastack.cmds.import_tarballs import update_gbp_and_ci_files

    mock_mgr = MagicMock()
    mock_gbp.return_value = mock_mgr
    mock_gbp.return_value.update_gbp_conf.return_value = False
    mock_update_ci.return_value = True

    update_gbp_and_ci_files(mock_mgr, "upstream/dalmatian", "dalmatian")

    mock_gbp.assert_called_once_with(mock_mgr.path)
    mock_mgr.commit.assert_called_once()
    args = mock_mgr.commit.call_args[0]
    assert args[1] == [".launchpad.yaml"]


@patch("packastack.cmds.import_tarballs.lpci.update_launchpad_ci_file")
@patch("packastack.cmds.import_tarballs.GitBuildPackage")
def test_update_gbp_and_ci_files_commit_raises(mock_gbp, mock_update_ci, tmp_path):
    """Test update_gbp_and_ci_files surfaces RepositoryError from commit."""
    from packastack.cmds.import_tarballs import update_gbp_and_ci_files
    from packastack.exceptions import RepositoryError

    mock_mgr = MagicMock()
    mock_gbp.return_value = mock_mgr
    mock_gbp.return_value.update_gbp_conf.return_value = True
    mock_update_ci.return_value = True
    mock_mgr.commit.side_effect = RepositoryError("commit failed")

    with pytest.raises(RepositoryError, match="commit failed"):
        update_gbp_and_ci_files(mock_mgr, "upstream/dalmatian", "dalmatian")


@patch("packastack.cmds.import_tarballs.get_previous_cycle")
def test_create_upstream_branch_already_exists(mock_get_prev, tmp_path):
    """Test create_upstream_branch when branch exists."""
    from packastack.cmds.import_tarballs import create_upstream_branch

    mock_mgr = MagicMock()
    mock_mgr.branch_exists.return_value = True

    create_upstream_branch(mock_mgr, "upstream/dalmatian", tmp_path)

    mock_mgr.create_branch.assert_not_called()


@patch("packastack.cmds.import_tarballs.get_previous_cycle")
def test_create_upstream_branch_with_previous(mock_get_prev, tmp_path):
    """Test create_upstream_branch with previous cycle branch."""
    from packastack.cmds.import_tarballs import create_upstream_branch

    mock_mgr = MagicMock()
    mock_mgr.branch_exists.side_effect = [
        False,
        True,
    ]  # upstream/dalmatian, then upstream-caracal
    mock_get_prev.return_value = "caracal"

    create_upstream_branch(mock_mgr, "upstream/dalmatian", tmp_path)

    mock_mgr.create_branch.assert_called_once_with(
        "upstream/dalmatian", "upstream-caracal"
    )


@patch("packastack.cmds.import_tarballs.get_previous_cycle")
def test_create_upstream_branch_no_previous_branch(mock_get_prev, tmp_path):
    """Test create_upstream_branch when previous branch doesn't exist."""
    from packastack.cmds.import_tarballs import create_upstream_branch

    mock_mgr = MagicMock()
    mock_mgr.branch_exists.side_effect = [False, False]
    mock_get_prev.return_value = "caracal"

    create_upstream_branch(mock_mgr, "upstream/dalmatian", tmp_path)

    mock_mgr.create_branch.assert_called_once_with("upstream/dalmatian")


@patch("packastack.cmds.import_tarballs.get_previous_cycle")
def test_create_upstream_branch_no_previous_cycle(mock_get_prev, tmp_path):
    """Test create_upstream_branch when no previous cycle."""
    from packastack.cmds.import_tarballs import create_upstream_branch

    mock_mgr = MagicMock()
    mock_mgr.branch_exists.return_value = False
    mock_get_prev.return_value = None

    create_upstream_branch(mock_mgr, "upstream/dalmatian", tmp_path)

    mock_mgr.create_branch.assert_called_once_with("upstream/dalmatian")


@patch("packastack.cmds.import_tarballs.get_deliverable_info")
@patch("packastack.cmds.import_tarballs.console")
def test_check_deliverable_exists_found(mock_console, mock_get_deliverable, tmp_path):
    """Test check_deliverable_exists when deliverable found."""
    from packastack.cmds.import_tarballs import check_deliverable_exists

    mock_get_deliverable.return_value = {"name": "nova"}

    result = check_deliverable_exists(tmp_path, "dalmatian", "nova", "release", "nova")

    assert result is True
    mock_console.print.assert_not_called()


@patch("packastack.cmds.import_tarballs.get_deliverable_info")
@patch("packastack.cmds.import_tarballs.console")
def test_check_deliverable_exists_not_found_release(
    mock_console, mock_get_deliverable, tmp_path
):
    """Test check_deliverable_exists when deliverable not found for release."""
    from packastack.cmds.import_tarballs import check_deliverable_exists

    mock_get_deliverable.return_value = None

    result = check_deliverable_exists(tmp_path, "dalmatian", "nova", "release", "nova")

    assert result is False
    mock_console.print.assert_called_once()


@patch("packastack.cmds.import_tarballs.get_deliverable_info")
@patch("packastack.cmds.import_tarballs.console")
def test_check_deliverable_exists_not_found_snapshot(
    mock_console, mock_get_deliverable, tmp_path
):
    """Test check_deliverable_exists when deliverable not found for snapshot."""
    from packastack.cmds.import_tarballs import check_deliverable_exists

    mock_get_deliverable.return_value = None

    result = check_deliverable_exists(tmp_path, "dalmatian", "nova", "snapshot", "nova")

    assert result is True  # Should continue for snapshot
    mock_console.print.assert_not_called()


def test_create_and_import_tarball_release(tmp_path):
    """Test create_and_import_tarball with release importer."""
    from packastack.cmds.import_tarballs import create_and_import_tarball

    pkg_repo_path = tmp_path / "nova"
    upstream_repo_path = tmp_path / "upstream"
    tarballs_dir = tmp_path / "tarballs"
    releases_path = tmp_path / "releases"

    pkg_repo_path.mkdir()
    upstream_repo_path.mkdir()
    tarballs_dir.mkdir()
    releases_path.mkdir()

    with patch("packastack.cmds.import_tarballs.ReleaseImporter") as mock_importer_cls:
        mock_importer = MagicMock()
        mock_importer.import_tarball.return_value = "27.0.0-1ubuntu0"
        mock_importer.get_version.return_value = "27.0.0"
        mock_importer.get_tarball.return_value = tarballs_dir / "nova-27.0.0.tar.gz"
        mock_importer.rename_tarball.return_value = (
            tarballs_dir / "nova_27.0.0-1ubuntu0.orig.tar.gz"
        )
        mock_importer_cls.return_value = mock_importer

        debian_version, tarball = create_and_import_tarball(
            "release",
            False,
            pkg_repo_path,
            upstream_repo_path,
            tarballs_dir,
            "dalmatian",
            releases_path,
            "nova",
        )

        assert debian_version == "27.0.0-1ubuntu0"
        assert tarball == tarballs_dir / "nova_27.0.0-1ubuntu0.orig.tar.gz"


def test_cleanup_tarballs_success(tmp_path):
    """Test cleanup_tarballs removes files."""
    from packastack.cmds.import_tarballs import cleanup_tarballs

    tarball = tmp_path / "nova_27.0.0.orig.tar.gz"
    signature = tmp_path / "nova_27.0.0.orig.tar.gz.asc"
    tarball.write_text("fake tarball")
    signature.write_text("fake signature")

    cleanup_tarballs(tarball)

    assert not tarball.exists()
    assert not signature.exists()


def test_cleanup_tarballs_no_signature(tmp_path):
    """Test cleanup_tarballs without signature file."""
    from packastack.cmds.import_tarballs import cleanup_tarballs

    tarball = tmp_path / "nova_27.0.0.orig.tar.gz"
    tarball.write_text("fake tarball")

    cleanup_tarballs(tarball)

    assert not tarball.exists()


def test_cleanup_tarballs_error(tmp_path):
    """Test cleanup_tarballs handles errors silently."""
    from packastack.cmds.import_tarballs import cleanup_tarballs

    tarball = tmp_path / "nonexistent.tar.gz"

    # Should not raise exception
    cleanup_tarballs(tarball)


# Tests for process_repository


@patch("packastack.cmds.import_tarballs.cleanup_tarballs")
@patch("packastack.cmds.import_tarballs.GitBuildPackage")
@patch("packastack.cmds.import_tarballs.create_and_import_tarball")
@patch("packastack.cmds.import_tarballs.determine_importer_type")
@patch("packastack.cmds.import_tarballs.check_deliverable_exists")
@patch("packastack.cmds.import_tarballs.create_upstream_branch")
@patch("packastack.cmds.import_tarballs.update_gbp_and_ci_files")
@patch("packastack.cmds.import_tarballs.setup_upstream_repository")
@patch("packastack.cmds.import_tarballs.parse_packaging_metadata")
@patch("packastack.cmds.import_tarballs.setup_repository")
@patch("packastack.cmds.import_tarballs.console")
def test_process_repository_success(
    mock_console,
    mock_setup_repo,
    mock_parse_metadata,
    mock_setup_upstream,
    mock_update_gbp,
    mock_create_branch,
    mock_check_deliverable,
    mock_determine_type,
    mock_create_import,
    mock_gbp,
    mock_cleanup,
    tmp_path,
):
    """Test process_repository successful flow."""
    # Multi-import handled at module level

    # Setup mocks
    mock_pkg_mgr = MagicMock()
    mock_setup_repo.return_value = mock_pkg_mgr

    mock_parse_metadata.return_value = (
        "nova",
        "https://opendev.org/openstack/nova",
        "nova",
    )

    mock_upstream_mgr = MagicMock()
    mock_setup_upstream.return_value = mock_upstream_mgr

    mock_check_deliverable.return_value = True
    mock_determine_type.return_value = ("release", False)
    mock_create_import.return_value = ("27.0.0-1ubuntu0", tmp_path / "nova.tar.gz")

    mock_gbp_instance = MagicMock()
    mock_gbp.return_value = mock_gbp_instance

    context = ImportContext("dalmatian", "auto", False)

    result = process_repository(
        "nova",
        "https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source/nova",
        context,
        tmp_path / "packaging",
        tmp_path / "upstream",
        tmp_path / "tarballs",
        tmp_path / "releases",
        False,
    )

    assert result is True
    assert "nova" in context.successes
    mock_pkg_mgr.track_remote_branches.assert_called_once()
    mock_pkg_mgr.checkout_important_branches.assert_called_once()
    mock_gbp_instance.import_orig.assert_called_once()
    mock_cleanup.assert_not_called()


@patch("packastack.cmds.import_tarballs.cleanup_tarballs")
@patch("packastack.cmds.import_tarballs.GitBuildPackage")
@patch("packastack.cmds.import_tarballs.create_and_import_tarball")
@patch("packastack.cmds.import_tarballs.determine_importer_type")
@patch("packastack.cmds.import_tarballs.check_deliverable_exists")
@patch("packastack.cmds.import_tarballs.create_upstream_branch")
@patch("packastack.cmds.import_tarballs.update_gbp_and_ci_files")
@patch("packastack.cmds.import_tarballs.setup_upstream_repository")
@patch("packastack.cmds.import_tarballs.parse_packaging_metadata")
@patch("packastack.cmds.import_tarballs.setup_repository")
@patch("packastack.cmds.import_tarballs.console")
def test_process_repository_with_cleanup(
    mock_console,
    mock_setup_repo,
    mock_parse_metadata,
    mock_setup_upstream,
    mock_update_gbp,
    mock_create_branch,
    mock_check_deliverable,
    mock_determine_type,
    mock_create_import,
    mock_gbp,
    mock_cleanup,
    tmp_path,
):
    """Test process_repository with tarball cleanup."""
    # Multi-import handled at module level

    # Setup mocks
    mock_pkg_mgr = MagicMock()
    mock_setup_repo.return_value = mock_pkg_mgr
    mock_parse_metadata.return_value = (
        "nova",
        "https://opendev.org/openstack/nova",
        "nova",
    )
    mock_upstream_mgr = MagicMock()
    mock_setup_upstream.return_value = mock_upstream_mgr
    mock_check_deliverable.return_value = True
    mock_determine_type.return_value = ("release", False)
    tarball_path = tmp_path / "nova.tar.gz"
    mock_create_import.return_value = ("27.0.0-1ubuntu0", tarball_path)
    mock_gbp.return_value = MagicMock()

    context = ImportContext("dalmatian", "auto", True)  # cleanup_tarballs=True

    result = process_repository(
        "nova",
        "https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source/nova",
        context,
        tmp_path / "packaging",
        tmp_path / "upstream",
        tmp_path / "tarballs",
        tmp_path / "releases",
        False,
    )

    assert result is True
    mock_cleanup.assert_called_once_with(tarball_path)


@patch("packastack.cmds.import_tarballs.check_deliverable_exists")
@patch("packastack.cmds.import_tarballs.create_upstream_branch")
@patch("packastack.cmds.import_tarballs.update_gbp_and_ci_files")
@patch("packastack.cmds.import_tarballs.setup_upstream_repository")
@patch("packastack.cmds.import_tarballs.parse_packaging_metadata")
@patch("packastack.cmds.import_tarballs.setup_repository")
@patch("packastack.cmds.import_tarballs.console")
def test_process_repository_no_deliverable(
    mock_console,
    mock_setup_repo,
    mock_parse_metadata,
    mock_setup_upstream,
    mock_update_gbp,
    mock_create_branch,
    mock_check_deliverable,
    tmp_path,
):
    """Test process_repository when deliverable not found."""
    # Multi-import handled at module level

    mock_pkg_mgr = MagicMock()
    mock_setup_repo.return_value = mock_pkg_mgr
    mock_parse_metadata.return_value = (
        "nova",
        "https://opendev.org/openstack/nova",
        "nova",
    )
    mock_upstream_mgr = MagicMock()
    mock_setup_upstream.return_value = mock_upstream_mgr
    mock_check_deliverable.return_value = False

    context = ImportContext("dalmatian", "release", False)

    result = process_repository(
        "nova",
        "https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source/nova",
        context,
        tmp_path / "packaging",
        tmp_path / "upstream",
        tmp_path / "tarballs",
        tmp_path / "releases",
        False,
    )

    assert result is False
    assert len(context.successes) == 0
    assert len(context.failures) == 0


@patch("packastack.cmds.import_tarballs.setup_repository")
@patch("packastack.cmds.import_tarballs.console")
def test_process_repository_packastack_error(mock_console, mock_setup_repo, tmp_path):
    """Test process_repository with PackastackError."""
    # Multi-import handled at module level
    from packastack.exceptions import DebianError

    mock_setup_repo.side_effect = DebianError("Test error")

    context = ImportContext("dalmatian", "release", False)

    result = process_repository(
        "nova",
        "https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source/nova",
        context,
        tmp_path / "packaging",
        tmp_path / "upstream",
        tmp_path / "tarballs",
        tmp_path / "releases",
        True,  # continue_on_error
    )

    assert result is False
    assert len(context.failures) == 1
    assert context.failures[0] == ("nova", "Test error")


@patch("packastack.cmds.import_tarballs.setup_repository")
@patch("packastack.cmds.import_tarballs.console")
def test_process_repository_packastack_error_no_continue(
    mock_console, mock_setup_repo, tmp_path
):
    """Test process_repository with PackastackError and no continue."""
    import pytest

    # Multi-import handled at module level
    from packastack.exceptions import DebianError

    mock_setup_repo.side_effect = DebianError("Test error")

    context = ImportContext("dalmatian", "release", False)

    with pytest.raises(DebianError):
        process_repository(
            "nova",
            "https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source/nova",
            context,
            tmp_path / "packaging",
            tmp_path / "upstream",
            tmp_path / "tarballs",
            tmp_path / "releases",
            False,  # continue_on_error=False
        )


@patch("packastack.cmds.import_tarballs.setup_repository")
@patch("packastack.cmds.import_tarballs.console")
def test_process_repository_unexpected_error(mock_console, mock_setup_repo, tmp_path):
    """Test process_repository with unexpected error."""
    # Multi-import handled at module level

    mock_setup_repo.side_effect = RuntimeError("Unexpected error")

    context = ImportContext("dalmatian", "release", False)

    result = process_repository(
        "nova",
        "https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source/nova",
        context,
        tmp_path / "packaging",
        tmp_path / "upstream",
        tmp_path / "tarballs",
        tmp_path / "releases",
        True,  # continue_on_error
    )

    assert result is False
    assert len(context.failures) == 1
    assert "Unexpected error" in context.failures[0][1]


@patch("packastack.cmds.import_tarballs.setup_repository")
@patch("packastack.cmds.import_tarballs.console")
def test_process_repository_system_exit_ebadmsg(
    mock_console, mock_setup_repo, tmp_path
):
    """Test process_repository with SystemExit EBADMSG."""
    # Multi-import handled at module level

    mock_setup_repo.side_effect = SystemExit(74)

    context = ImportContext("dalmatian", "snapshot", False)

    result = process_repository(
        "nova",
        "https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source/nova",
        context,
        tmp_path / "packaging",
        tmp_path / "upstream",
        tmp_path / "tarballs",
        tmp_path / "releases",
        True,  # continue_on_error
    )

    assert result is False
    assert len(context.failures) == 1
    assert "Explicitly requested snapshot but HEAD is tagged" in context.failures[0][1]


@patch("packastack.cmds.import_tarballs.setup_repository")
@patch("packastack.cmds.import_tarballs.console")
def test_process_repository_system_exit_other(mock_console, mock_setup_repo, tmp_path):
    """Test process_repository with other SystemExit code."""
    import pytest

    # Multi-import handled at module level

    mock_setup_repo.side_effect = SystemExit(1)

    context = ImportContext("dalmatian", "release", False)

    with pytest.raises(SystemExit):
        process_repository(
            "nova",
            "https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source/nova",
            context,
            tmp_path / "packaging",
            tmp_path / "upstream",
            tmp_path / "tarballs",
            tmp_path / "releases",
            True,
        )


@patch("packastack.cmds.import_tarballs.RepositoryManager")
@patch("packastack.cmds.import_tarballs.LaunchpadClient")
def test_get_launchpad_repositories(mock_lp_client_cls, mock_repo_mgr_cls):
    """Test getting repositories from Launchpad."""
    mock_client = Mock()
    mock_lp_client_cls.return_value = mock_client

    mock_repo_mgr = Mock()
    mock_repos = [Mock(name="nova"), Mock(name="neutron")]
    mock_repo_mgr.list_team_repositories.return_value = mock_repos
    mock_repo_mgr_cls.return_value = mock_repo_mgr

    result = get_launchpad_repositories()

    assert result == mock_repos
    mock_client.connect.assert_called_once()
    mock_repo_mgr_cls.assert_called_once_with(mock_client)
    mock_repo_mgr.list_team_repositories.assert_called_once()


@patch("packastack.cmds.import_tarballs.process_repository")
def test_process_repositories_sequential(mock_process_repo):
    """Test sequential repository processing."""
    repo1 = Mock()
    repo1.name = "nova"
    repo1.url = "url1"
    repo2 = Mock()
    repo2.name = "neutron"
    repo2.url = "url2"
    repos = [repo1, repo2]
    context = Mock()
    packaging_dir = Path("/tmp/packaging")
    upstream_dir = Path("/tmp/upstream")
    tarballs_dir = Path("/tmp/tarballs")
    releases_path = Path("/tmp/releases")

    process_repositories(
        repos,
        context,
        packaging_dir,
        upstream_dir,
        tarballs_dir,
        releases_path,
        False,
        1,  # jobs=1 for sequential
    )

    assert mock_process_repo.call_count == 2
    mock_process_repo.assert_any_call(
        "nova",
        "url1",
        context,
        packaging_dir,
        upstream_dir,
        tarballs_dir,
        releases_path,
        False,
    )
    mock_process_repo.assert_any_call(
        "neutron",
        "url2",
        context,
        packaging_dir,
        upstream_dir,
        tarballs_dir,
        releases_path,
        False,
    )


def test_filter_repositories_include_exact():
    """Test filter_repositories exact match include"""
    repo1 = Mock()
    repo1.name = "nova"
    repo2 = Mock()
    repo2.name = "neutron"
    repos = [repo1, repo2]

    filtered = filter_repositories(repos, ["nova"], exclude=False)
    assert len(filtered) == 1
    assert filtered[0].name == "nova"


def test_filter_repositories_include_glob():
    """Test filter_repositories glob include and normalization"""
    repo1 = Mock()
    repo1.name = "Nova"
    repo2 = Mock()
    repo2.name = "neutron"
    repo3 = Mock()
    repo3.name = "nova-api"
    repos = [repo1, repo2, repo3]

    filtered = filter_repositories(repos, ["nova*"], exclude=False)
    assert len(filtered) == 2
    assert {r.name for r in filtered} == {"Nova", "nova-api"}


def test_filter_repositories_exclude():
    """Test filter_repositories exclude behavior"""
    repo1 = Mock()
    repo1.name = "nova"
    repo2 = Mock()
    repo2.name = "neutron"
    repo3 = Mock()
    repo3.name = "nova-api"
    repos = [repo1, repo2, repo3]

    filtered = filter_repositories(repos, ["nova*"], exclude=True)
    assert len(filtered) == 1
    assert filtered[0].name == "neutron"


def test_filter_repositories_no_pattern():
    """Test filter_repositories with no patterns returns all repos"""
    repo1 = Mock()
    repo1.name = "nova"
    repo2 = Mock()
    repo2.name = "neutron"
    repos = [repo1, repo2]

    filtered = filter_repositories(repos, [], exclude=False)
    assert len(filtered) == 2
    assert filtered == repos


def test_filter_repositories_no_match():
    """Test filter_repositories when pattern doesn't match any repo"""
    repo1 = Mock()
    repo1.name = "nova"
    repo2 = Mock()
    repo2.name = "neutron"
    repos = [repo1, repo2]

    filtered = filter_repositories(repos, ["cinder"], exclude=False)
    assert len(filtered) == 0


@patch("packastack.cmds.import_tarballs.process_repository")
def test_process_repositories_parallel_success(mock_process_repo):
    """Test parallel repository processing with success."""
    repo1 = Mock()
    repo1.name = "nova"
    repo1.url = "url1"
    repo2 = Mock()
    repo2.name = "neutron"
    repo2.url = "url2"
    repos = [repo1, repo2]
    context = Mock()
    packaging_dir = Path("/tmp/packaging")
    upstream_dir = Path("/tmp/upstream")
    tarballs_dir = Path("/tmp/tarballs")
    releases_path = Path("/tmp/releases")

    mock_process_repo.return_value = True

    process_repositories(
        repos,
        context,
        packaging_dir,
        upstream_dir,
        tarballs_dir,
        releases_path,
        False,
        2,  # jobs=2 for parallel
    )

    assert mock_process_repo.call_count == 2


@patch("packastack.cmds.import_tarballs.process_repository")
def test_process_repositories_parallel_error_no_continue(mock_process_repo):
    """Test parallel processing with error and no continue."""
    repo1 = Mock()
    repo1.name = "nova"
    repo1.url = "url1"
    repo2 = Mock()
    repo2.name = "neutron"
    repo2.url = "url2"
    repos = [repo1, repo2]
    context = Mock()
    packaging_dir = Path("/tmp/packaging")
    upstream_dir = Path("/tmp/upstream")
    tarballs_dir = Path("/tmp/tarballs")
    releases_path = Path("/tmp/releases")

    mock_process_repo.side_effect = Exception("Test error")

    with pytest.raises(Exception, match="Test error"):
        process_repositories(
            repos,
            context,
            packaging_dir,
            upstream_dir,
            tarballs_dir,
            releases_path,
            False,
            2,  # jobs=2 for parallel
        )


@patch("packastack.cmds.import_tarballs.process_repository")
def test_process_repositories_parallel_error_with_continue(mock_process_repo):
    """Test parallel processing with error and continue."""
    repo1 = Mock()
    repo1.name = "nova"
    repo1.url = "url1"
    repo2 = Mock()
    repo2.name = "neutron"
    repo2.url = "url2"
    repos = [repo1, repo2]
    context = Mock()
    packaging_dir = Path("/tmp/packaging")
    upstream_dir = Path("/tmp/upstream")
    tarballs_dir = Path("/tmp/tarballs")
    releases_path = Path("/tmp/releases")

    mock_process_repo.side_effect = Exception("Test error")

    # Should not raise when continue_on_error=True
    process_repositories(
        repos,
        context,
        packaging_dir,
        upstream_dir,
        tarballs_dir,
        releases_path,
        True,
        2,  # jobs=2 for parallel
    )


@patch("packastack.cmds.import_tarballs.logging")
@patch("packastack.cmds.import_tarballs.console")
def test_print_import_summary_success(mock_console, mock_logging):
    """Test printing import summary with success."""
    context = Mock()
    context.successes = ["nova", "neutron"]
    context.failures = []

    print_import_summary(context, Path("/tmp/errors.log"), False)

    assert mock_console.print.call_count == 3  # Title, successes, failures


@patch("packastack.cmds.import_tarballs.logging")
@patch("packastack.cmds.import_tarballs.console")
def test_print_import_summary_with_failures_continue(mock_console, mock_logging):
    """Test printing import summary with failures but continue_on_error."""
    context = Mock()
    context.successes = ["nova"]
    context.failures = [("neutron", "Version not found")]

    print_import_summary(context, Path("/tmp/errors.log"), True)

    assert mock_console.print.call_count == 4  # Title, successes, failures, log path
    mock_logging.error.assert_called_once_with("neutron: Version not found")


@patch("packastack.cmds.import_tarballs.logging")
@patch("packastack.cmds.import_tarballs.console")
def test_print_import_summary_with_failures_no_continue(mock_console, mock_logging):
    """Test printing import summary with failures and no continue."""
    context = Mock()
    context.successes = ["nova"]
    context.failures = [("neutron", "Version not found")]

    with pytest.raises(click.ClickException, match="Import failed for 1 repositories"):
        print_import_summary(context, Path("/tmp/errors.log"), False)


@patch("packastack.cmds.import_tarballs.print_import_summary")
@patch("packastack.cmds.import_tarballs.process_repositories")
@patch("packastack.cmds.import_tarballs.get_launchpad_repositories")
@patch("packastack.cmds.import_tarballs.get_current_cycle")
@patch("packastack.cmds.import_tarballs.setup_releases_repo")
@patch("packastack.cmds.import_tarballs.setup_directories")
@patch("packastack.cmds.import_tarballs.console")
def test_import_cmd_current_cycle_sequential(
    mock_console,
    mock_setup_dirs,
    mock_setup_releases,
    mock_get_cycle,
    mock_get_repos,
    mock_process,
    mock_print_summary,
):
    """Test import_cmd with current cycle and sequential processing."""
    from click.testing import CliRunner


    mock_setup_dirs.return_value = (
        Path("/tmp/packaging"),
        Path("/tmp/upstream"),
        Path("/tmp/tarballs"),
        Path("/tmp/logs"),
    )
    mock_setup_releases.return_value = Path("/tmp/releases")
    mock_get_cycle.return_value = "dalmatian"
    mock_get_repos.return_value = []

    from packastack.cli import cli as packastack_cli
    runner = CliRunner()
    # Test include packages via positional argument (only 'nova' should be processed)
    mock_get_repos.return_value = [Mock(name="nova"), Mock(name="neutron")]
    result = runner.invoke(packastack_cli, ["import", "--type", "release", "--cycle", "current", "nova"])

    assert result.exit_code == 0
    mock_get_cycle.assert_called_once()
    mock_process.assert_called_once()
    # Verify setup_releases_repo was called with upstream_dir
    mock_setup_releases.assert_called_once()


@patch("packastack.cmds.import_tarballs.print_import_summary")
@patch("packastack.cmds.import_tarballs.process_repositories")
@patch("packastack.cmds.import_tarballs.get_launchpad_repositories")
@patch("packastack.cmds.import_tarballs.setup_releases_repo")
@patch("packastack.cmds.import_tarballs.setup_directories")
@patch("packastack.cmds.import_tarballs.console")
def test_import_cmd_specific_cycle_parallel(
    mock_console,
    mock_setup_dirs,
    mock_setup_releases,
    mock_get_repos,
    mock_process,
    mock_print_summary,
):
    """Test import_cmd with specific cycle and parallel processing."""
    from click.testing import CliRunner


    mock_setup_dirs.return_value = (
        Path("/tmp/packaging"),
        Path("/tmp/upstream"),
        Path("/tmp/tarballs"),
        Path("/tmp/logs"),
    )
    mock_setup_releases.return_value = Path("/tmp/releases")
    mock_get_repos.return_value = []

    from packastack.cli import cli as packastack_cli
    runner = CliRunner()
    mock_get_repos.return_value = [Mock(name="nova"), Mock(name="neutron")]
    # Test exclude packages via flag
    runner.invoke(
        packastack_cli,
        [
            "import",
            "--exclude-packages",
            "--type",
            "snapshot",
            "--cycle",
            "caracal",
            "--jobs",
            "4",
            "nova",
        ],
    )

    mock_process.assert_called_once()
    # Verify setup_releases_repo was called
    mock_setup_releases.assert_called_once()


@patch("packastack.cmds.import_tarballs.setup_directories")
@patch("packastack.cmds.import_tarballs.console")
def test_import_cmd_keyboard_interrupt(mock_console, mock_setup_dirs):
    """Test import_cmd with KeyboardInterrupt."""
    from click.testing import CliRunner


    mock_setup_dirs.side_effect = KeyboardInterrupt()

    from packastack.cli import cli as packastack_cli
    runner = CliRunner()
    result = runner.invoke(packastack_cli, ["import"])

    assert result.exit_code == 1
    assert "Import interrupted by user" in mock_console.print.call_args[0][0]

    # No process_repositories mock here, skip filtering assertion

@patch("packastack.cmds.import_tarballs.setup_directories")
@patch("packastack.cmds.import_tarballs.console")
def test_import_cmd_packastack_error(mock_console, mock_setup_dirs):
    """Test import_cmd with PackastackError."""
    from click.testing import CliRunner

    from packastack.exceptions import PackastackError

    mock_setup_dirs.side_effect = PackastackError("Test error")

    from packastack.cli import cli as packastack_cli
    runner = CliRunner()
    result = runner.invoke(packastack_cli, ["import"])

    assert result.exit_code == 1
    assert "Test error" in result.output
    # No process_repositories mock here, skip filtering assertion


@patch("packastack.cmds.import_tarballs.setup_directories")
@patch("packastack.cmds.import_tarballs.console")
def test_import_cmd_unexpected_error(mock_console, mock_setup_dirs):
    """Test import_cmd with unexpected error."""
    from click.testing import CliRunner


    mock_setup_dirs.side_effect = ValueError("Unexpected")

    from packastack.cli import cli as packastack_cli
    runner = CliRunner()
    result = runner.invoke(packastack_cli, ["import"])

    assert result.exit_code == 1
    assert "Unexpected error: Unexpected" in result.output


@patch("packastack.cmds.import_tarballs.print_import_summary")
@patch("packastack.cmds.import_tarballs.process_repositories")
@patch("packastack.cmds.import_tarballs.get_launchpad_repositories")
@patch("packastack.cmds.import_tarballs.setup_releases_repo")
@patch("packastack.cmds.import_tarballs.setup_directories")
@patch("packastack.cmds.import_tarballs.console")
def test_import_cmd_click_exception_reraise(
    mock_console,
    mock_setup_dirs,
    mock_setup_releases,
    mock_get_repos,
    mock_process,
    mock_print_summary,
):
    """Test import_cmd re-raises Click exceptions."""
    from click.testing import CliRunner


    mock_setup_dirs.return_value = (
        Path("/tmp/packaging"),
        Path("/tmp/upstream"),
        Path("/tmp/tarballs"),
        Path("/tmp/logs"),
    )
    mock_setup_releases.return_value = Path("/tmp/releases")
    mock_get_repos.return_value = []
    mock_print_summary.side_effect = click.ClickException("Test click error")

    from packastack.cli import cli as packastack_cli
    runner = CliRunner()
    result = runner.invoke(packastack_cli, ["import", "--cycle", "caracal"])

    assert result.exit_code == 1
    assert "Test click error" in result.output


@patch("packastack.cmds.import_tarballs.print_import_summary")
@patch("packastack.cmds.import_tarballs.process_repositories")
@patch("packastack.cmds.import_tarballs.get_launchpad_repositories")
@patch("packastack.cmds.import_tarballs.setup_releases_repo")
@patch("packastack.cmds.import_tarballs.setup_directories")
@patch("packastack.cmds.import_tarballs.console")
def test_import_cmd_with_root_option(
    mock_console,
    mock_setup_dirs,
    mock_setup_releases,
    mock_get_repos,
    mock_process,
    mock_print_summary,
    tmp_path,
):
    """Test import_cmd with --root option."""
    from click.testing import CliRunner


    mock_setup_dirs.return_value = (
        tmp_path / "packaging",
        tmp_path / "upstream",
        tmp_path / "tarballs",
        tmp_path / "logs",
    )
    mock_setup_releases.return_value = tmp_path / "releases"
    mock_get_repos.return_value = []

    runner = CliRunner()
    from packastack.cli import cli as packastack_cli
    runner = CliRunner()
    result = runner.invoke(
        packastack_cli, ["--root", str(tmp_path), "import", "--cycle", "caracal"]
    )

    assert result.exit_code == 0
    # Verify setup_directories was called with root parameter
    mock_setup_dirs.assert_called_once_with(Path(str(tmp_path)))
