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

"""Tests for packastack.config module."""

from __future__ import annotations

from pathlib import Path

import yaml

from packastack import config


class TestDefaultConfig:
    """Tests for DEFAULT_CONFIG structure."""

    def test_default_config_has_required_sections(self) -> None:
        assert "paths" in config.DEFAULT_CONFIG
        assert "defaults" in config.DEFAULT_CONFIG
        assert "mirrors" in config.DEFAULT_CONFIG
        assert "behavior" in config.DEFAULT_CONFIG

    def test_default_config_paths_use_tilde(self) -> None:
        paths = config.DEFAULT_CONFIG["paths"]
        assert paths["cache_root"].startswith("~")

    def test_default_config_has_ubuntu_mirror(self) -> None:
        mirrors = config.DEFAULT_CONFIG["mirrors"]
        assert "ubuntu_archive" in mirrors
        assert "archive.ubuntu.com" in mirrors["ubuntu_archive"]


class TestEnsureConfigExists:
    """Tests for ensure_config_exists function."""

    def test_creates_config_directory(self, temp_home: Path) -> None:
        config_dir = temp_home / ".config" / "packastack"
        assert not config_dir.exists()

        config.ensure_config_exists()

        assert config_dir.exists()

    def test_creates_config_file_with_defaults(self, temp_home: Path) -> None:
        config_file = temp_home / ".config" / "packastack" / "config.yaml"
        assert not config_file.exists()

        config.ensure_config_exists()

        assert config_file.exists()
        content = yaml.safe_load(config_file.read_text())
        assert "paths" in content
        assert "defaults" in content

    def test_does_not_overwrite_existing_config(self, mock_config: Path) -> None:
        original_content = mock_config.read_text()
        mock_config.write_text(original_content + "\n# custom comment\n")
        modified_content = mock_config.read_text()

        config.ensure_config_exists()

        assert mock_config.read_text() == modified_content


class TestLoadConfig:
    """Tests for load_config function."""

    def test_loads_default_config_when_no_file(self, temp_home: Path) -> None:
        cfg = config.load_config()

        assert "paths" in cfg
        assert "defaults" in cfg
        assert "mirrors" in cfg

    def test_expands_tilde_in_paths(self, temp_home: Path) -> None:
        cfg = config.load_config()

        paths = cfg["paths"]
        for key, value in paths.items():
            assert "~" not in value, f"Path {key} was not expanded: {value}"

    def test_merges_custom_config_with_defaults(self, temp_home: Path) -> None:
        config_dir = temp_home / ".config" / "packastack"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.yaml"
        config_file.write_text("""
mirrors:
  ubuntu_archive: "http://custom.mirror.example/ubuntu"
""")

        cfg = config.load_config()

        # Custom value should be used
        assert cfg["mirrors"]["ubuntu_archive"] == "http://custom.mirror.example/ubuntu"
        # Defaults should still be present
        assert "paths" in cfg
        assert "cache_root" in cfg["paths"]

    def test_handles_empty_config_file(self, temp_home: Path) -> None:
        config_dir = temp_home / ".config" / "packastack"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.yaml"
        config_file.write_text("")

        cfg = config.load_config()

        # Should fall back to defaults
        assert "paths" in cfg
        assert "mirrors" in cfg

    def test_handles_invalid_yaml(self, temp_home: Path) -> None:
        config_dir = temp_home / ".config" / "packastack"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.yaml"
        config_file.write_text("invalid: yaml: content: [")

        cfg = config.load_config()

        # Should fall back to defaults on parse error
        assert "paths" in cfg


class TestWriteConfig:
    """Tests for write_config function."""

    def test_writes_config_to_file(self, temp_home: Path) -> None:
        test_data = {"custom": "value", "nested": {"key": "data"}}

        config.write_config(test_data)

        config_file = temp_home / ".config" / "packastack" / "config.yaml"
        assert config_file.exists()
        content = yaml.safe_load(config_file.read_text())
        assert content == test_data

    def test_creates_directory_if_missing(self, temp_home: Path) -> None:
        config_dir = temp_home / ".config" / "packastack"
        assert not config_dir.exists()

        config.write_config({"test": "data"})

        assert config_dir.exists()
