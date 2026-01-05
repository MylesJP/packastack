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

"""Tests for packastack.core.paths module."""

from __future__ import annotations

from pathlib import Path

from packastack.core import paths


class TestResolvePaths:
    """Tests for resolve_paths function."""

    def test_resolves_tilde_paths(self, temp_home: Path) -> None:
        cfg = {
            "paths": {
                "cache_root": "~/.cache/packastack",
                "test_dir": "~/test",
            }
        }

        resolved = paths.resolve_paths(cfg)

        assert resolved["cache_root"] == (temp_home / ".cache" / "packastack").resolve()
        assert resolved["test_dir"] == (temp_home / "test").resolve()

    def test_handles_absolute_paths(self, temp_home: Path) -> None:
        cfg = {
            "paths": {
                "absolute": "/tmp/packastack-test",
            }
        }

        resolved = paths.resolve_paths(cfg)

        assert resolved["absolute"] == Path("/tmp/packastack-test").resolve()

    def test_returns_path_objects(self, temp_home: Path) -> None:
        cfg = {
            "paths": {
                "test": "~/.cache/test",
            }
        }

        resolved = paths.resolve_paths(cfg)

        assert isinstance(resolved["test"], Path)


class TestEnsureDirectories:
    """Tests for ensure_directories function."""

    def test_creates_all_required_directories(self, temp_home: Path, mock_config: Path) -> None:
        result = paths.ensure_directories()

        # Check all directories were created
        assert result["cache_root"].exists()
        assert result["openstack_releases_repo"].exists()
        assert result["ubuntu_archive_cache"].exists()
        assert result["local_apt_repo"].exists()
        assert result["build_root"].exists()
        assert result["runs_root"].exists()

        # Check subdirectories
        assert (result["ubuntu_archive_cache"] / "indexes").exists()
        assert (result["ubuntu_archive_cache"] / "snapshots").exists()

    def test_idempotent_when_directories_exist(
        self, temp_home: Path, mock_config: Path, mock_cache_dirs: dict[str, Path]
    ) -> None:
        # Directories already exist from fixture
        # Should not raise
        result = paths.ensure_directories()

        assert result["cache_root"].exists()

    def test_accepts_custom_paths_config(self, temp_home: Path, mock_config: Path) -> None:
        custom_paths = {
            "cache_root": str(temp_home / "custom-cache"),
            "openstack_releases_repo": str(temp_home / "custom-cache" / "releases"),
            "ubuntu_archive_cache": str(temp_home / "custom-cache" / "ubuntu"),
            "local_apt_repo": str(temp_home / "custom-cache" / "apt"),
            "build_root": str(temp_home / "custom-cache" / "build"),
            "runs_root": str(temp_home / "custom-cache" / "runs"),
        }

        result = paths.ensure_directories(custom_paths)

        assert result["cache_root"] == (temp_home / "custom-cache").resolve()
        assert (temp_home / "custom-cache").exists()
        # Derived paths should fall back to the custom cache root when omitted.
        assert result["upstream_tarballs"] == (temp_home / "custom-cache" / "upstream-tarballs").resolve()
        assert result["upstream_tarballs"].exists()
