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

"""Tests for packastack.debpkg.launchpad_yaml module."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from packastack.debpkg import launchpad_yaml


class TestAmbiguousUpdateError:
    """Tests for AmbiguousUpdateError exception."""

    def test_message(self) -> None:
        """Test exception message."""
        error = launchpad_yaml.AmbiguousUpdateError("Test message")
        assert str(error) == "Test message"

    def test_conflicts(self) -> None:
        """Test conflicts attribute."""
        error = launchpad_yaml.AmbiguousUpdateError("Test", conflicts=["a", "b"])
        assert error.conflicts == ["a", "b"]

    def test_default_conflicts(self) -> None:
        """Test default empty conflicts."""
        error = launchpad_yaml.AmbiguousUpdateError("Test")
        assert error.conflicts == []


class TestLaunchpadConfig:
    """Tests for LaunchpadConfig dataclass."""

    def test_get_recipes_empty(self) -> None:
        """Test get_recipes with no recipes."""
        config = launchpad_yaml.LaunchpadConfig(data={})
        assert config.get_recipes() == []

    def test_get_recipes(self) -> None:
        """Test get_recipes with recipes."""
        config = launchpad_yaml.LaunchpadConfig(
            data={"recipes": [{"name": "test"}]}
        )
        assert config.get_recipes() == [{"name": "test"}]

    def test_get_git_repository(self) -> None:
        """Test get_git_repository."""
        config = launchpad_yaml.LaunchpadConfig(
            data={"git-repository": "lp:~test/+git/pkg"}
        )
        assert config.get_git_repository() == "lp:~test/+git/pkg"

    def test_get_git_repository_empty(self) -> None:
        """Test get_git_repository when not set."""
        config = launchpad_yaml.LaunchpadConfig(data={})
        assert config.get_git_repository() == ""

    def test_get_git_repository_push(self) -> None:
        """Test get_git_repository_push."""
        config = launchpad_yaml.LaunchpadConfig(
            data={"git-repository-push": "lp:~test/+git/pkg"}
        )
        assert config.get_git_repository_push() == "lp:~test/+git/pkg"


class TestLoadLaunchpadYaml:
    """Tests for load_launchpad_yaml function."""

    def test_file_not_exists(self, tmp_path: Path) -> None:
        """Test loading when file doesn't exist."""
        result = launchpad_yaml.load_launchpad_yaml(tmp_path)
        assert result is None

    def test_valid_yaml(self, tmp_path: Path) -> None:
        """Test loading valid YAML file."""
        yaml_path = tmp_path / "launchpad.yaml"
        yaml_path.write_text(
            "git-repository: lp:~test/+git/pkg\n"
            "recipes:\n"
            "  - name: test-recipe\n"
            "    branch: ubuntu/noble\n"
        )

        result = launchpad_yaml.load_launchpad_yaml(tmp_path)
        assert result is not None
        assert result.get_git_repository() == "lp:~test/+git/pkg"
        assert result.path == yaml_path

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        """Test loading invalid YAML file."""
        yaml_path = tmp_path / "launchpad.yaml"
        yaml_path.write_text("{ invalid yaml [")

        result = launchpad_yaml.load_launchpad_yaml(tmp_path)
        assert result is None

    def test_empty_yaml(self, tmp_path: Path) -> None:
        """Test loading empty YAML file."""
        yaml_path = tmp_path / "launchpad.yaml"
        yaml_path.write_text("")

        result = launchpad_yaml.load_launchpad_yaml(tmp_path)
        assert result is not None
        assert result.data == {}


class TestSaveLaunchpadYaml:
    """Tests for save_launchpad_yaml function."""

    def test_save_success(self, tmp_path: Path) -> None:
        """Test successful save."""
        yaml_path = tmp_path / "launchpad.yaml"
        config = launchpad_yaml.LaunchpadConfig(
            data={"git-repository": "lp:test"},
            path=yaml_path,
        )

        result = launchpad_yaml.save_launchpad_yaml(config)
        assert result is True
        assert yaml_path.exists()

        # Verify content
        loaded = yaml.safe_load(yaml_path.read_text())
        assert loaded["git-repository"] == "lp:test"

    def test_save_no_path(self) -> None:
        """Test save when path is None."""
        config = launchpad_yaml.LaunchpadConfig(data={})
        result = launchpad_yaml.save_launchpad_yaml(config)
        assert result is False


class TestFindSeriesReferences:
    """Tests for find_series_references function."""

    def test_simple_string(self) -> None:
        """Test finding series in simple string."""
        data = {"branch": "ubuntu/noble-caracal"}
        refs = launchpad_yaml.find_series_references(data, "caracal")

        assert len(refs) == 1
        assert refs[0][0] == "branch"
        assert "caracal" in refs[0][1]

    def test_nested_data(self) -> None:
        """Test finding series in nested structure."""
        data = {
            "recipes": [
                {"name": "pkg-caracal", "branch": "ubuntu/noble-caracal"}
            ]
        }
        refs = launchpad_yaml.find_series_references(data, "caracal")

        assert len(refs) == 2

    def test_no_matches(self) -> None:
        """Test when no series found."""
        data = {"branch": "ubuntu/noble"}
        refs = launchpad_yaml.find_series_references(data, "caracal")

        assert len(refs) == 0

    def test_empty_data(self) -> None:
        """Test with empty data."""
        refs = launchpad_yaml.find_series_references({}, "caracal")
        assert refs == []


class TestUpdateSeriesReferences:
    """Tests for update_series_references function."""

    def test_update_branch(self) -> None:
        """Test updating branch references."""
        data = {"branch": "ubuntu/noble-caracal"}
        new_data, updated = launchpad_yaml.update_series_references(
            data, "caracal", "dalmatian"
        )

        assert "dalmatian" in new_data["branch"]
        assert "caracal" not in new_data["branch"]
        assert len(updated) > 0

    def test_update_recipe_names(self) -> None:
        """Test updating recipe name references."""
        data = {
            "recipes": [
                {"name": "pkg-caracal", "branch": "ubuntu/noble-caracal"}
            ]
        }
        new_data, updated = launchpad_yaml.update_series_references(
            data, "caracal", "dalmatian"
        )

        assert "dalmatian" in new_data["recipes"][0]["name"]
        assert "dalmatian" in new_data["recipes"][0]["branch"]

    def test_no_updates_needed(self) -> None:
        """Test when no updates are needed."""
        data = {"branch": "ubuntu/noble"}
        new_data, updated = launchpad_yaml.update_series_references(
            data, "caracal", "dalmatian"
        )

        assert new_data == data
        assert len(updated) == 0


class TestValidateUpdate:
    """Tests for validate_update function."""

    def test_no_references(self, tmp_path: Path) -> None:
        """Test validation when no references found."""
        config = launchpad_yaml.LaunchpadConfig(
            data={"branch": "ubuntu/noble"},
            path=tmp_path / "launchpad.yaml",
        )

        is_valid, fields, warnings = launchpad_yaml.validate_update(
            config, "caracal", "dalmatian"
        )

        assert is_valid is True
        assert fields == []
        assert any("No references" in w for w in warnings)

    def test_has_references(self, tmp_path: Path) -> None:
        """Test validation with references to update."""
        config = launchpad_yaml.LaunchpadConfig(
            data={"branch": "ubuntu/noble-caracal"},
            path=tmp_path / "launchpad.yaml",
        )

        is_valid, fields, warnings = launchpad_yaml.validate_update(
            config, "caracal", "dalmatian"
        )

        assert is_valid is True
        assert len(fields) > 0


class TestUpdateLaunchpadYamlSeries:
    """Tests for update_launchpad_yaml_series function."""

    def test_no_yaml_file(self, tmp_path: Path) -> None:
        """Test when launchpad.yaml doesn't exist."""
        success, updated, error = launchpad_yaml.update_launchpad_yaml_series(
            tmp_path, "caracal", "dalmatian"
        )

        assert success is True
        assert updated == []
        assert "not found" in error

    def test_successful_update(self, tmp_path: Path) -> None:
        """Test successful series update."""
        yaml_path = tmp_path / "launchpad.yaml"
        yaml_path.write_text(
            "git-repository: lp:~test/+git/pkg\n"
            "recipes:\n"
            "  - name: pkg-caracal\n"
            "    branch: ubuntu/noble-caracal\n"
        )

        success, updated, error = launchpad_yaml.update_launchpad_yaml_series(
            tmp_path, "caracal", "dalmatian"
        )

        assert success is True
        assert len(updated) > 0

        # Verify file was updated
        content = yaml_path.read_text()
        assert "dalmatian" in content

    def test_no_updates_needed(self, tmp_path: Path) -> None:
        """Test when no updates are needed."""
        yaml_path = tmp_path / "launchpad.yaml"
        yaml_path.write_text("git-repository: lp:~test/+git/pkg\n")

        success, updated, error = launchpad_yaml.update_launchpad_yaml_series(
            tmp_path, "caracal", "dalmatian"
        )

        assert success is True
        assert updated == []


class TestCreateDefaultLaunchpadYaml:
    """Tests for create_default_launchpad_yaml function."""

    def test_create_success(self, tmp_path: Path) -> None:
        """Test successful creation."""
        result = launchpad_yaml.create_default_launchpad_yaml(
            repo_path=tmp_path,
            package="python-nova",
            ubuntu_series="noble",
            openstack_series="dalmatian",
        )

        assert result is True
        yaml_path = tmp_path / "launchpad.yaml"
        assert yaml_path.exists()

        # Verify content
        data = yaml.safe_load(yaml_path.read_text())
        assert "python-nova" in data["git-repository"]
        assert data["recipes"][0]["name"] == "python-nova-dalmatian"
        assert "dalmatian" in data["recipes"][0]["branch"]

    def test_default_recipe_type(self, tmp_path: Path) -> None:
        """Test that default recipe type is daily-build."""
        launchpad_yaml.create_default_launchpad_yaml(
            tmp_path, "pkg", "noble", "caracal"
        )

        yaml_path = tmp_path / "launchpad.yaml"
        data = yaml.safe_load(yaml_path.read_text())
        assert data["recipes"][0]["recipe-type"] == "daily-build"


class TestUpdateSeriesReferencesEdgeCases:
    """Edge case tests for update_series_references."""

    def test_non_safe_field_not_updated(self) -> None:
        """Test that non-safe fields are not updated."""
        data = {
            "description": "This is the caracal version",
        }
        new_data, updated = launchpad_yaml.update_series_references(
            data, "caracal", "dalmatian"
        )

        # description is not a safe field, should not be updated
        assert new_data["description"] == "This is the caracal version"
        assert len(updated) == 0

    def test_array_index_field_name_extraction(self) -> None:
        """Test field name extraction with array indices."""
        data = {
            "recipes": [
                {"source-branch": "ubuntu/caracal"}
            ]
        }
        new_data, updated = launchpad_yaml.update_series_references(
            data, "caracal", "dalmatian"
        )

        # source-branch is a safe field
        assert "dalmatian" in new_data["recipes"][0]["source-branch"]
        assert len(updated) > 0

    def test_non_string_values_preserved(self) -> None:
        """Test that non-string values are preserved unchanged."""
        data = {
            "count": 42,
            "enabled": True,
            "branch": "caracal",
        }
        new_data, updated = launchpad_yaml.update_series_references(
            data, "caracal", "dalmatian"
        )

        assert new_data["count"] == 42
        assert new_data["enabled"] is True


class TestValidateUpdateEdgeCases:
    """Edge case tests for validate_update."""

    def test_validates_with_ambiguous_references(self, tmp_path: Path) -> None:
        """Test validation with references in multiple locations."""
        config = launchpad_yaml.LaunchpadConfig(
            data={
                "recipes": [
                    {"name": "pkg-caracal", "branch": "ubuntu/caracal"},
                    {"name": "pkg-caracal-2", "target-branch": "caracal-staging"},
                ]
            },
            path=tmp_path / "launchpad.yaml",
        )

        is_valid, fields, warnings = launchpad_yaml.validate_update(
            config, "caracal", "dalmatian"
        )

        # Should identify multiple fields to update
        assert is_valid is True
        assert len(fields) >= 2


class TestUpdateLaunchpadYamlSeriesEdgeCases:
    """Edge case tests for update_launchpad_yaml_series."""

    def test_validation_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test when validation fails."""
        yaml_path = tmp_path / "launchpad.yaml"
        yaml_path.write_text("branch: ubuntu/caracal\n")

        # Mock validate_update to return False
        def mock_validate(*args: object, **kwargs: object) -> tuple[bool, list[str], list[str]]:
            return False, [], ["Validation failed"]

        monkeypatch.setattr(launchpad_yaml, "validate_update", mock_validate)

        success, updated, error = launchpad_yaml.update_launchpad_yaml_series(
            tmp_path, "caracal", "dalmatian"
        )

        assert success is False
        assert "Validation failed" in error

    def test_save_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test when save fails."""
        yaml_path = tmp_path / "launchpad.yaml"
        yaml_path.write_text("branch: ubuntu/caracal\n")

        # Mock save_launchpad_yaml to return False
        def mock_save(*args: object, **kwargs: object) -> bool:
            return False

        monkeypatch.setattr(launchpad_yaml, "save_launchpad_yaml", mock_save)

        success, updated, error = launchpad_yaml.update_launchpad_yaml_series(
            tmp_path, "caracal", "dalmatian"
        )

        assert success is False
        assert "Failed to save" in error
