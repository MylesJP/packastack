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

"""Tests for packastack.planning.deploop module."""

from __future__ import annotations

from pathlib import Path

import pytest

from packastack.planning.deploop import (
    DependencyBuildPlan,
    DependencyBuildResult,
    DependencyCheckResult,
    normalize_python_package_name,
    parse_requirements_txt,
    parse_pyproject_toml_deps,
)


class TestDependencyCheckResult:
    """Tests for DependencyCheckResult dataclass."""

    def test_basic_result(self) -> None:
        """Test basic check result."""
        result = DependencyCheckResult(name="python3-pbr")
        assert result.name == "python3-pbr"
        assert result.version_constraint == ""
        assert result.available_in_archive is False
        assert result.available_in_local is False
        assert result.needs_build is False

    def test_full_result(self) -> None:
        """Test full check result."""
        result = DependencyCheckResult(
            name="python3-oslo-config",
            version_constraint=">=1.0.0",
            available_in_archive=True,
            archive_version="1.5.0-1",
            needs_build=False,
        )
        assert result.available_in_archive is True
        assert result.archive_version == "1.5.0-1"


class TestDependencyBuildPlan:
    """Tests for DependencyBuildPlan dataclass."""

    def test_empty_plan(self) -> None:
        """Test empty plan."""
        plan = DependencyBuildPlan()
        assert plan.to_build == []
        assert plan.already_available == []
        assert plan.from_archive == []
        assert plan.from_local == []
        assert plan.check_results == {}

    def test_plan_with_data(self) -> None:
        """Test plan with populated data."""
        plan = DependencyBuildPlan(
            to_build=["oslo.config"],
            already_available=["pbr"],
            from_archive=["pbr"],
        )
        assert "oslo.config" in plan.to_build
        assert "pbr" in plan.already_available


class TestDependencyBuildResult:
    """Tests for DependencyBuildResult dataclass."""

    def test_success_result(self) -> None:
        """Test successful build result."""
        result = DependencyBuildResult(
            success=True,
            built=["oslo.config", "oslo.utils"],
        )
        assert result.success is True
        assert len(result.built) == 2

    def test_failure_result(self) -> None:
        """Test failed build result."""
        result = DependencyBuildResult(
            success=False,
            failed=["oslo.db"],
            errors={"oslo.db": "missing dependency"},
        )
        assert result.success is False
        assert "oslo.db" in result.failed


class TestNormalizePythonPackageName:
    """Tests for normalize_python_package_name function."""

    def test_simple_name(self) -> None:
        """Test simple package name."""
        assert normalize_python_package_name("pbr") == "python3-pbr"

    def test_name_with_dots(self) -> None:
        """Test name with dots (oslo.config style)."""
        assert normalize_python_package_name("oslo.config") == "python3-oslo-config"

    def test_name_with_underscores(self) -> None:
        """Test name with underscores."""
        assert normalize_python_package_name("keystoneauth1") == "python3-keystoneauth1"

    def test_name_with_hyphens(self) -> None:
        """Test name with hyphens."""
        assert normalize_python_package_name("os-client-config") == "python3-os-client-config"

    def test_mixed_separators(self) -> None:
        """Test name with mixed separators."""
        assert normalize_python_package_name("oslo.log") == "python3-oslo-log"

    def test_uppercase(self) -> None:
        """Test uppercase names are lowercased."""
        assert normalize_python_package_name("PyYAML") == "python3-pyyaml"

    def test_consecutive_separators(self) -> None:
        """Test consecutive separators are collapsed."""
        assert normalize_python_package_name("some..package") == "python3-some-package"


class TestParseRequirementsTxt:
    """Tests for parse_requirements_txt function."""

    def test_empty_file(self, tmp_path: Path) -> None:
        """Test parsing empty file."""
        reqs = tmp_path / "requirements.txt"
        reqs.write_text("")
        result = parse_requirements_txt(reqs)
        assert result == []

    def test_simple_requirements(self, tmp_path: Path) -> None:
        """Test parsing simple requirements."""
        reqs = tmp_path / "requirements.txt"
        reqs.write_text("pbr\noslo.config\n")
        result = parse_requirements_txt(reqs)
        assert ("pbr", "") in result
        assert ("oslo.config", "") in result

    def test_versioned_requirements(self, tmp_path: Path) -> None:
        """Test parsing versioned requirements."""
        reqs = tmp_path / "requirements.txt"
        reqs.write_text("pbr>=1.0\noslo.config>=1.0,<2.0\n")
        result = parse_requirements_txt(reqs)
        assert ("pbr", ">=1.0") in result
        assert ("oslo.config", ">=1.0,<2.0") in result

    def test_skips_comments(self, tmp_path: Path) -> None:
        """Test that comments are skipped."""
        reqs = tmp_path / "requirements.txt"
        reqs.write_text("# this is a comment\npbr\n# another comment\n")
        result = parse_requirements_txt(reqs)
        assert len(result) == 1
        assert ("pbr", "") in result

    def test_skips_empty_lines(self, tmp_path: Path) -> None:
        """Test that empty lines are skipped."""
        reqs = tmp_path / "requirements.txt"
        reqs.write_text("pbr\n\n\noslo.config\n")
        result = parse_requirements_txt(reqs)
        assert len(result) == 2

    def test_skips_directives(self, tmp_path: Path) -> None:
        """Test that -r, -c, -e directives are skipped."""
        reqs = tmp_path / "requirements.txt"
        reqs.write_text("-r base.txt\n-c constraints.txt\n-e git+...\npbr\n")
        result = parse_requirements_txt(reqs)
        assert len(result) == 1
        assert ("pbr", "") in result

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Test handling of nonexistent file."""
        result = parse_requirements_txt(tmp_path / "nonexistent.txt")
        assert result == []


class TestParsePyprojectTomlDeps:
    """Tests for parse_pyproject_toml_deps function."""

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Test handling of nonexistent file."""
        result = parse_pyproject_toml_deps(tmp_path / "nonexistent.toml")
        assert result == []

    def test_empty_dependencies(self, tmp_path: Path) -> None:
        """Test parsing file with no dependencies."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "test"\n')
        result = parse_pyproject_toml_deps(pyproject)
        assert result == []

    def test_simple_dependencies(self, tmp_path: Path) -> None:
        """Test parsing simple dependencies."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('''
[project]
name = "test"
dependencies = ["pbr", "oslo.config"]
''')
        result = parse_pyproject_toml_deps(pyproject)
        assert ("pbr", "") in result
        assert ("oslo.config", "") in result

    def test_versioned_dependencies(self, tmp_path: Path) -> None:
        """Test parsing versioned dependencies."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('''
[project]
name = "test"
dependencies = ["pbr>=1.0", "oslo.config>=2.0,<3.0"]
''')
        result = parse_pyproject_toml_deps(pyproject)
        assert ("pbr", ">=1.0") in result
        assert ("oslo.config", ">=2.0,<3.0") in result
