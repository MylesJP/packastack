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

"""Tests for packastack init command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from packastack.commands import init as init_cmd


class TestCloneOrUpdateReleases:
    """Tests for _clone_or_update_releases function."""

    def test_clones_new_repository(
        self, temp_home: Path, mock_config: Path, mock_cache_dirs: dict[str, Path]
    ) -> None:
        releases_path = mock_cache_dirs["openstack_releases_repo"]
        # Remove the directory to simulate fresh clone
        releases_path.rmdir()

        mock_run = mock.MagicMock()

        with mock.patch("git.Repo.clone_from") as mock_clone:
            init_cmd._clone_or_update_releases(releases_path, mock_run)

        mock_clone.assert_called_once_with(init_cmd.OPENSTACK_RELEASES_URL, releases_path)

    def test_fetches_existing_repository(
        self, temp_home: Path, mock_config: Path, mock_cache_dirs: dict[str, Path]
    ) -> None:
        releases_path = mock_cache_dirs["openstack_releases_repo"]
        # Create .git directory to simulate existing repo
        (releases_path / ".git").mkdir(parents=True, exist_ok=True)

        mock_run = mock.MagicMock()
        mock_repo = mock.MagicMock()
        mock_origin = mock.MagicMock()
        mock_repo.remotes.origin = mock_origin

        with mock.patch("git.Repo", return_value=mock_repo):
            init_cmd._clone_or_update_releases(releases_path, mock_run)

        mock_origin.fetch.assert_called_once_with(prune=True)


class TestCreateUbuntuArchiveFiles:
    """Tests for _create_ubuntu_archive_files function."""

    def test_creates_readme(self, temp_home: Path) -> None:
        ubuntu_cache = temp_home / "ubuntu-archive"
        ubuntu_cache.mkdir(parents=True)

        init_cmd._create_ubuntu_archive_files(ubuntu_cache)

        readme = ubuntu_cache / "README.txt"
        assert readme.exists()
        content = readme.read_text()
        assert "Packastack" in content
        assert "indexes" in content

    def test_creates_config_json(self, temp_home: Path) -> None:
        ubuntu_cache = temp_home / "ubuntu-archive"
        ubuntu_cache.mkdir(parents=True)

        init_cmd._create_ubuntu_archive_files(ubuntu_cache)

        config_file = ubuntu_cache / "config.json"
        assert config_file.exists()
        config_data = json.loads(config_file.read_text())
        assert "mirror" in config_data
        assert "last_refresh" in config_data


class TestInitCommand:
    """Tests for init command."""

    def test_creates_config_file(
        self, temp_home: Path, non_tty_stdout: None
    ) -> None:
        config_file = temp_home / ".config" / "packastack" / "config.yaml"
        assert not config_file.exists()

        with mock.patch("git.Repo.clone_from"):
            with mock.patch("subprocess.run") as mock_subprocess:
                mock_subprocess.return_value = mock.Mock(stdout="resolute\n", returncode=0)
                with pytest.raises(SystemExit) as exc_info:
                    init_cmd.init(prime=False)

        assert exc_info.value.code == 0
        assert config_file.exists()

    def test_creates_cache_directories(
        self, temp_home: Path, non_tty_stdout: None
    ) -> None:
        cache_root = temp_home / ".cache" / "packastack"

        with mock.patch("git.Repo.clone_from"):
            with mock.patch("subprocess.run") as mock_subprocess:
                mock_subprocess.return_value = mock.Mock(stdout="resolute\n", returncode=0)
                with pytest.raises(SystemExit):
                    init_cmd.init(prime=False)

        assert cache_root.exists()
        assert (cache_root / "ubuntu-archive" / "indexes").exists()
        assert (cache_root / "ubuntu-archive" / "snapshots").exists()
        assert (cache_root / "runs").exists()

    def test_creates_summary_json(
        self, temp_home: Path, non_tty_stdout: None
    ) -> None:
        with mock.patch("git.Repo.clone_from"):
            with mock.patch("subprocess.run") as mock_subprocess:
                mock_subprocess.return_value = mock.Mock(stdout="resolute\n", returncode=0)
                with pytest.raises(SystemExit):
                    init_cmd.init(prime=False)

        runs_dir = temp_home / ".cache" / "packastack" / "runs"
        run_dirs = list(runs_dir.iterdir())
        assert len(run_dirs) == 1

        summary_file = run_dirs[0] / "summary.json"
        assert summary_file.exists()
        summary = json.loads(summary_file.read_text())
        assert summary["status"] == "success"
        assert "steps_completed" in summary

    def test_does_not_overwrite_existing_config(
        self, temp_home: Path, mock_config: Path, non_tty_stdout: None
    ) -> None:
        original_content = mock_config.read_text()
        mock_config.write_text(original_content + "\n# my custom comment\n")
        mock_config.read_text()

        with mock.patch("git.Repo.clone_from"):
            with mock.patch("subprocess.run") as mock_subprocess:
                mock_subprocess.return_value = mock.Mock(stdout="resolute\n", returncode=0)
                with pytest.raises(SystemExit):
                    init_cmd.init(prime=False)

        # Config should not be overwritten
        assert "my custom comment" in mock_config.read_text()

    def test_exits_with_code_0_on_success(
        self, temp_home: Path, non_tty_stdout: None
    ) -> None:
        with mock.patch("git.Repo.clone_from"):
            with mock.patch("subprocess.run") as mock_subprocess:
                mock_subprocess.return_value = mock.Mock(stdout="resolute\n", returncode=0)
                with pytest.raises(SystemExit) as exc_info:
                    init_cmd.init(prime=False)

        assert exc_info.value.code == 0

    def test_resolves_devel_series(
        self, temp_home: Path, non_tty_stdout: None
    ) -> None:
        with mock.patch("git.Repo.clone_from"):
            with mock.patch("subprocess.run") as mock_subprocess:
                mock_subprocess.return_value = mock.Mock(stdout="resolute\n", returncode=0)
                with pytest.raises(SystemExit):
                    init_cmd.init(prime=False)

        # Check that series was resolved in summary
        runs_dir = temp_home / ".cache" / "packastack" / "runs"
        run_dirs = list(runs_dir.iterdir())
        summary = json.loads((run_dirs[0] / "summary.json").read_text())
        assert summary["devel_series"] == "resolute"
