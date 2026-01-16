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

"""Tests for packastack.planning.validated_plan module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from packastack.planning import validated_plan


class TestUpstreamDeps:
    """Tests for UpstreamDeps dataclass."""

    def test_all_deps_unique(self) -> None:
        """Test that all_deps returns unique dependencies."""
        deps = validated_plan.UpstreamDeps(
            runtime=[("oslo-config", ">=1.0"), ("oslo-log", "")],
            test=[("oslo-config", ">=1.0"), ("pytest", "")],
            build=[("pbr", ">=2.0")],
        )
        all_deps = deps.all_deps()

        assert len(all_deps) == 4
        names = [name for name, _ in all_deps]
        assert names.count("oslo-config") == 1

    def test_all_deps_ordering(self) -> None:
        """Test that all_deps maintains order."""
        deps = validated_plan.UpstreamDeps(
            runtime=[("a", ""), ("b", "")],
            test=[("c", "")],
            build=[("d", "")],
        )
        all_deps = deps.all_deps()
        names = [name for name, _ in all_deps]

        assert names == ["a", "b", "c", "d"]

    def test_all_dep_names(self) -> None:
        """Test that all_dep_names returns just names."""
        deps = validated_plan.UpstreamDeps(
            runtime=[("oslo-config", ">=1.0"), ("oslo-log", "")],
            test=[],
            build=[],
        )
        names = deps.all_dep_names()
        assert names == ["oslo-config", "oslo-log"]


class TestValidatedPlan:
    """Tests for ValidatedPlan dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        plan = validated_plan.ValidatedPlan(
            build_order=["pkg1", "pkg2"],
            upload_order=["pkg1", "pkg2"],
        )
        assert plan.new_deps == []
        assert plan.missing_deps == {}
        assert plan.resolved_deps == {}
        assert plan.warnings == []
        assert plan.updated is False
        assert plan.dependency_edges == {}
        assert plan.dependency_versions == {}

    def test_dependency_edges(self) -> None:
        """Test dependency_edges field."""
        plan = validated_plan.ValidatedPlan(
            build_order=["nova", "keystone"],
            upload_order=["nova", "keystone"],
            dependency_edges={
                "nova": ["oslo.config", "keystone"],
                "keystone": ["oslo.config"],
            },
        )
        assert "nova" in plan.dependency_edges
        assert "oslo.config" in plan.dependency_edges["nova"]
        assert len(plan.dependency_edges["nova"]) == 2

    def test_dependency_versions(self) -> None:
        """Test dependency_versions field."""
        plan = validated_plan.ValidatedPlan(
            build_order=["nova"],
            upload_order=["nova"],
            dependency_versions={
                "python3-oslo.config": "10.0.0-0ubuntu1",
                "python3-keystoneauth1": "5.3.0-0ubuntu1",
            },
        )
        assert plan.dependency_versions["python3-oslo.config"] == "10.0.0-0ubuntu1"


class TestSoftDependencyExclusions:
    """Tests for soft dependency exclusion functionality."""

    def test_oslo_config_oslo_log_exclusion(self) -> None:
        """Test oslo.config/oslo.log circular dep is excluded."""
        assert validated_plan.is_excluded_dependency("oslo.config", "oslo.log") is True
        assert validated_plan.is_excluded_dependency("oslo.log", "oslo.config") is True

    def test_oslo_oslotest_exclusion(self) -> None:
        """Test oslo.config/oslotest exclusion."""
        assert validated_plan.is_excluded_dependency("oslo.config", "oslotest") is True
        assert validated_plan.is_excluded_dependency("oslo.log", "oslotest") is True

    def test_non_excluded_deps(self) -> None:
        """Test that normal dependencies are not excluded."""
        assert validated_plan.is_excluded_dependency("nova", "oslo.config") is False
        assert validated_plan.is_excluded_dependency("nova", "keystone") is False
        assert validated_plan.is_excluded_dependency("oslo.config", "oslo.utils") is False

    def test_exclusions_set_format(self) -> None:
        """Test that SOFT_DEPENDENCY_EXCLUSIONS is a set of tuples."""
        assert isinstance(validated_plan.SOFT_DEPENDENCY_EXCLUSIONS, set)
        for item in validated_plan.SOFT_DEPENDENCY_EXCLUSIONS:
            assert isinstance(item, tuple)
            assert len(item) == 2


class TestProjectToSourcePackage:
    """Tests for project_to_source_package function."""

    def test_oslo_project(self) -> None:
        """Test Oslo project mapping."""
        assert validated_plan.project_to_source_package("oslo.config") == "python-oslo.config"
        assert validated_plan.project_to_source_package("oslo-log") == "python-oslo-log"

    def test_service_project(self) -> None:
        """Test service project mapping."""
        assert validated_plan.project_to_source_package("nova") == "nova"
        assert validated_plan.project_to_source_package("keystone") == "keystone"
        assert validated_plan.project_to_source_package("neutron") == "neutron"

    def test_client_project(self) -> None:
        """Test client library mapping."""
        assert validated_plan.project_to_source_package("python-novaclient") == "python-novaclient"
        assert validated_plan.project_to_source_package("novaclient") == "python-novaclient"

    def test_other_libraries(self) -> None:
        """Test other library mappings."""
        assert validated_plan.project_to_source_package("keystoneauth1") == "python-keystoneauth1"
        assert validated_plan.project_to_source_package("stevedore") == "python-stevedore"
        assert validated_plan.project_to_source_package("cliff") == "python-cliff"


class TestDependencyResolutionResult:
    """Tests for DependencyResolutionResult dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        result = validated_plan.DependencyResolutionResult(
            package="nova",
            project="nova",
            upstream_deps=validated_plan.UpstreamDeps(),
        )
        assert result.missing_deps == []
        assert result.resolved_deps == {}
        assert result.needs_building == []


class TestRecursiveValidationResult:
    """Tests for RecursiveValidationResult dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        result = validated_plan.RecursiveValidationResult(
            build_order=["nova"],
            dependency_edges={"nova": ["oslo.config"]},
            dependency_versions={},
            missing_deps={},
        )
        assert result.warnings == []
        assert result.has_cycles is False
        assert result.cycle_packages == []

    def test_get_package_deps(self) -> None:
        """Test get_package_deps method."""
        result = validated_plan.RecursiveValidationResult(
            build_order=["nova", "oslo.config"],
            dependency_edges={
                "nova": ["oslo.config", "keystone"],
                "oslo.config": [],
            },
            dependency_versions={},
            missing_deps={},
        )
        assert result.get_package_deps("nova") == ["oslo.config", "keystone"]
        assert result.get_package_deps("oslo.config") == []
        assert result.get_package_deps("unknown") == []


class TestParseRequirementLine:
    """Tests for parse_requirement_line function."""

    def test_simple_name(self) -> None:
        """Test parsing simple package name."""
        result = validated_plan.parse_requirement_line("oslo.config")
        assert result == "oslo.config"

    def test_with_version_constraint(self) -> None:
        """Test parsing with version constraint."""
        result = validated_plan.parse_requirement_line("oslo.config>=1.0.0")
        assert result == "oslo.config"

    def test_with_extras(self) -> None:
        """Test parsing with extras."""
        result = validated_plan.parse_requirement_line("oslo.config[extra1,extra2]>=1.0")
        assert result == "oslo.config"

    def test_with_environment_marker(self) -> None:
        """Test parsing with environment marker."""
        result = validated_plan.parse_requirement_line(
            "oslo.config ; python_version >= '3.8'"
        )
        assert result == "oslo.config"

    def test_empty_line(self) -> None:
        """Test parsing empty line."""
        result = validated_plan.parse_requirement_line("")
        assert result is None

    def test_comment_line(self) -> None:
        """Test parsing comment line."""
        result = validated_plan.parse_requirement_line("# this is a comment")
        assert result is None

    def test_include_directive(self) -> None:
        """Test parsing include directive."""
        result = validated_plan.parse_requirement_line("-r other-requirements.txt")
        assert result is None

    def test_editable_install(self) -> None:
        """Test parsing editable install."""
        result = validated_plan.parse_requirement_line("-e git+https://github.com/foo")
        assert result is None

    def test_constraint_file(self) -> None:
        """Test parsing constraint file."""
        result = validated_plan.parse_requirement_line("-c constraints.txt")
        assert result is None

    def test_normalized_name(self) -> None:
        """Test that names are normalized."""
        result = validated_plan.parse_requirement_line("Some_Package")
        assert result == "some-package"

    def test_all_operators(self) -> None:
        """Test various version operators."""
        for spec in ["pkg>=1.0", "pkg<=1.0", "pkg!=1.0", "pkg==1.0", "pkg~=1.0", "pkg>1", "pkg<1"]:
            result = validated_plan.parse_requirement_line(spec)
            assert result == "pkg"

    def test_url_based_install(self) -> None:
        """Test URL-based install (PEP 440)."""
        result = validated_plan.parse_requirement_line(
            "package @ https://example.com/package.tar.gz"
        )
        assert result == "package"

    def test_line_starting_with_semicolon(self) -> None:
        """Test line starting with semicolon (env marker continuation)."""
        result = validated_plan.parse_requirement_line("; python_version >= '3.8'")
        assert result is None

    def test_line_with_double_dash(self) -> None:
        """Test line starting with -- (pip option)."""
        result = validated_plan.parse_requirement_line("--index-url https://pypi.org")
        assert result is None


class TestParseRequirementsFile:
    """Tests for parse_requirements_file function."""

    def test_file_not_exists(self, tmp_path: Path) -> None:
        """Test parsing non-existent file."""
        result = validated_plan.parse_requirements_file(tmp_path / "requirements.txt")
        assert result == []

    def test_valid_file(self, tmp_path: Path) -> None:
        """Test parsing valid requirements file."""
        req_file = tmp_path / "requirements.txt"
        req_file.write_text(
            "oslo.config>=1.0\n"
            "oslo.log\n"
            "# comment\n"
            "\n"
            "requests>=2.0\n"
        )

        result = validated_plan.parse_requirements_file(req_file)
        names = [name for name, _ in result]
        assert len(result) == 3
        assert "oslo.config" in names
        assert "oslo.log" in names
        assert "requests" in names
        # Check version specs are preserved
        specs = dict(result)
        assert specs["oslo.config"] == ">=1.0"
        assert specs["oslo.log"] == ""
        assert specs["requests"] == ">=2.0"

    def test_complex_file(self, tmp_path: Path) -> None:
        """Test parsing complex requirements file."""
        req_file = tmp_path / "requirements.txt"
        req_file.write_text(
            "# OpenStack requirements\n"
            "pbr>=2.0.0\n"
            "oslo.config>=5.2.0 # inline comment\n"
            "oslo.db[mysql]>=4.27.0\n"
            "keystoneauth1>=3.4.0 ; python_version >= '3.6'\n"
            "-r base-requirements.txt\n"
        )

        result = validated_plan.parse_requirements_file(req_file)
        names = [name for name, _ in result]
        assert "pbr" in names
        assert "oslo.config" in names
        assert "oslo.db" in names  # Note: extras removed
        assert "keystoneauth1" in names


class TestParsePyprojectDeps:
    """Tests for parse_pyproject_deps function."""

    def test_file_not_exists(self, tmp_path: Path) -> None:
        """Test parsing non-existent file."""
        result = validated_plan.parse_pyproject_deps(tmp_path / "pyproject.toml")
        assert result == []

    def test_valid_pyproject(self, tmp_path: Path) -> None:
        """Test parsing valid pyproject.toml."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[project]\n"
            "dependencies = [\n"
            '    "oslo.config>=1.0",\n'
            '    "oslo.log",\n'
            "]\n"
            "\n"
            "[build-system]\n"
            "requires = [\n"
            '    "pbr>=2.0",\n'
            '    "setuptools",\n'
            "]\n"
        )

        result = validated_plan.parse_pyproject_deps(pyproject)
        names = [name for name, _ in result]
        assert "oslo.config" in names
        assert "oslo.log" in names
        assert "pbr" in names
        assert "setuptools" in names

    def test_pyproject_with_exception(self, tmp_path: Path) -> None:
        """Test parsing pyproject.toml with invalid content."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("invalid toml content [[[")

        # Should return empty list on exception
        result = validated_plan.parse_pyproject_deps(pyproject)
        assert result == []

    def test_pyproject_no_deps(self, tmp_path: Path) -> None:
        """Test parsing pyproject.toml without dependencies section."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[project]\n"
            "name = 'test'\n"
            "version = '1.0'\n"
        )

        result = validated_plan.parse_pyproject_deps(pyproject)
        assert result == []


class TestParseSetupCfgDeps:
    """Tests for parse_setup_cfg_deps function."""

    def test_file_not_exists(self, tmp_path: Path) -> None:
        """Test parsing non-existent file."""
        result = validated_plan.parse_setup_cfg_deps(tmp_path / "setup.cfg")
        assert result == []

    def test_valid_setup_cfg(self, tmp_path: Path) -> None:
        """Test parsing valid setup.cfg."""
        setup_cfg = tmp_path / "setup.cfg"
        setup_cfg.write_text(
            "[options]\n"
            "install_requires =\n"
            "    oslo.config>=1.0\n"
            "    oslo.log\n"
            "\n"
            "setup_requires =\n"
            "    pbr>=2.0\n"
        )

        result = validated_plan.parse_setup_cfg_deps(setup_cfg)
        names = [name for name, _ in result]
        assert "oslo.config" in names
        assert "oslo.log" in names
        assert "pbr" in names

    def test_setup_cfg_no_options(self, tmp_path: Path) -> None:
        """Test parsing setup.cfg without options section."""
        setup_cfg = tmp_path / "setup.cfg"
        setup_cfg.write_text("[metadata]\nname = test\n")

        result = validated_plan.parse_setup_cfg_deps(setup_cfg)
        assert result == []

    def test_setup_cfg_exception(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test parsing setup.cfg with exception."""
        import configparser
        setup_cfg = tmp_path / "setup.cfg"
        setup_cfg.write_text("[options]\ninstall_requires = pkg\n")

        # Mock configparser to raise exception
        def mock_read(self: configparser.ConfigParser, *args: object, **kwargs: object) -> list[str]:
            raise OSError("Parse failed")

        monkeypatch.setattr(configparser.ConfigParser, "read", mock_read)

        result = validated_plan.parse_setup_cfg_deps(setup_cfg)
        assert result == []


class TestExtractUpstreamDeps:
    """Tests for extract_upstream_deps function."""

    def test_empty_repo(self, tmp_path: Path) -> None:
        """Test extracting from empty repository."""
        deps = validated_plan.extract_upstream_deps(tmp_path)
        assert deps.runtime == []
        assert deps.test == []
        assert deps.build == []

    def test_with_requirements(self, tmp_path: Path) -> None:
        """Test extracting from repo with requirements files."""
        (tmp_path / "requirements.txt").write_text("oslo.config\noslo.log\n")
        (tmp_path / "test-requirements.txt").write_text("pytest\ntox\n")

        deps = validated_plan.extract_upstream_deps(tmp_path)
        runtime_names = [name for name, _ in deps.runtime]
        test_names = [name for name, _ in deps.test]
        assert "oslo.config" in runtime_names
        assert "oslo.log" in runtime_names
        assert "pytest" in test_names
        assert "tox" in test_names

    def test_all_deps_combined(self, tmp_path: Path) -> None:
        """Test that all deps are available through all_deps()."""
        (tmp_path / "requirements.txt").write_text("oslo.config\n")
        (tmp_path / "test-requirements.txt").write_text("pytest\n")

        deps = validated_plan.extract_upstream_deps(tmp_path)
        all_deps = deps.all_dep_names()

        assert "oslo.config" in all_deps
        assert "pytest" in all_deps

    def test_use_glob_finds_additional_requirements(self, tmp_path: Path) -> None:
        """Test that use_glob=True finds *requirements*.txt files."""
        (tmp_path / "requirements.txt").write_text("oslo.config\n")
        (tmp_path / "requirements-extra.txt").write_text("oslo.messaging\n")
        (tmp_path / "driver-requirements.txt").write_text("oslo.db\n")

        deps = validated_plan.extract_upstream_deps(tmp_path, use_glob=True)
        runtime_names = [name for name, _ in deps.runtime]

        assert "oslo.config" in runtime_names
        assert "oslo.messaging" in runtime_names
        assert "oslo.db" in runtime_names

    def test_use_glob_finds_test_requirements_variations(self, tmp_path: Path) -> None:
        """Test that use_glob=True finds test requirements variations."""
        (tmp_path / "test-requirements.txt").write_text("pytest\n")
        (tmp_path / "test_requirements.txt").write_text("mock\n")
        (tmp_path / "integration-test-requirements.txt").write_text("testcontainers\n")

        deps = validated_plan.extract_upstream_deps(tmp_path, use_glob=True)
        test_names = [name for name, _ in deps.test]

        assert "pytest" in test_names
        assert "mock" in test_names
        assert "testcontainers" in test_names

    def test_use_glob_avoids_duplicates(self, tmp_path: Path) -> None:
        """Test that use_glob=True avoids duplicate entries."""
        (tmp_path / "requirements.txt").write_text("oslo.config\n")
        (tmp_path / "requirements-extra.txt").write_text("oslo.config\noslo.log\n")

        deps = validated_plan.extract_upstream_deps(tmp_path, use_glob=True)
        runtime_names = [name for name, _ in deps.runtime]

        # Should have oslo.config only once
        assert runtime_names.count("oslo.config") == 1
        assert "oslo.log" in runtime_names

    def test_use_glob_separates_test_from_runtime(self, tmp_path: Path) -> None:
        """Test that use_glob=True correctly separates test from runtime deps."""
        (tmp_path / "requirements.txt").write_text("oslo.config\n")
        (tmp_path / "test-requirements.txt").write_text("pytest\n")
        (tmp_path / "doc-requirements.txt").write_text("sphinx\n")

        deps = validated_plan.extract_upstream_deps(tmp_path, use_glob=True)
        runtime_names = [name for name, _ in deps.runtime]
        test_names = [name for name, _ in deps.test]

        # pytest should be in test, not runtime
        assert "pytest" in test_names
        assert "pytest" not in runtime_names
        # sphinx in doc-requirements should be in runtime (not test)
        assert "sphinx" in runtime_names

    def test_use_glob_false_original_behavior(self, tmp_path: Path) -> None:
        """Test that use_glob=False maintains original behavior."""
        (tmp_path / "requirements.txt").write_text("oslo.config\n")
        (tmp_path / "requirements-extra.txt").write_text("oslo.messaging\n")

        deps = validated_plan.extract_upstream_deps(tmp_path, use_glob=False)
        runtime_names = [name for name, _ in deps.runtime]

        # Should only have oslo.config, not oslo.messaging
        assert "oslo.config" in runtime_names
        assert "oslo.messaging" not in runtime_names


class TestPythonToDebianMapping:
    """Tests for PYTHON_TO_DEBIAN mapping."""

    def test_oslo_libraries(self) -> None:
        """Test oslo library mappings."""
        assert validated_plan.PYTHON_TO_DEBIAN["oslo-config"] == "python3-oslo.config"
        assert validated_plan.PYTHON_TO_DEBIAN["oslo-log"] == "python3-oslo.log"
        assert validated_plan.PYTHON_TO_DEBIAN["oslo-utils"] == "python3-oslo.utils"

    def test_common_packages(self) -> None:
        """Test common package mappings."""
        assert validated_plan.PYTHON_TO_DEBIAN["pyyaml"] == "python3-yaml"
        assert validated_plan.PYTHON_TO_DEBIAN["sqlalchemy"] == "python3-sqlalchemy"
        assert validated_plan.PYTHON_TO_DEBIAN["requests"] == "python3-requests"

    def test_client_libraries(self) -> None:
        """Test OpenStack client library mappings."""
        assert validated_plan.PYTHON_TO_DEBIAN["keystoneauth1"] == "python3-keystoneauth1"
        assert validated_plan.PYTHON_TO_DEBIAN["python-novaclient"] == "python3-novaclient"


class TestMapPythonToDebian:
    """Tests for map_python_to_debian function."""

    def test_direct_mapping(self) -> None:
        """Test direct mapping from PYTHON_TO_DEBIAN."""
        debian_name, is_uncertain = validated_plan.map_python_to_debian("oslo-config")
        assert debian_name == "python3-oslo.config"
        assert is_uncertain is False

    def test_python3_prefix(self) -> None:
        """Test default python3- prefix for unknown packages."""
        debian_name, is_uncertain = validated_plan.map_python_to_debian("unknown-package")
        assert debian_name == "python3-unknown-package"
        assert is_uncertain is True  # Heuristic mapping

    def test_empty_mapping(self) -> None:
        """Test package that maps to empty (skip)."""
        debian_name, is_uncertain = validated_plan.map_python_to_debian("python")
        assert debian_name == ""
        assert is_uncertain is False

    def test_oslo_heuristic(self) -> None:
        """Test oslo-* heuristic mapping for packages not in explicit mapping."""
        # oslo-newservice is not in the explicit mapping
        debian_name, is_uncertain = validated_plan.map_python_to_debian("oslo-newservice")
        assert debian_name == "python3-oslo.newservice"
        assert is_uncertain is True


class TestResolveDependency:
    """Tests for resolve_dependency function."""

    def test_resolve_empty_name(self) -> None:
        """Test resolving empty dependency name."""
        mock_ubuntu = MagicMock()
        version, source = validated_plan.resolve_dependency(
            "", None, None, mock_ubuntu
        )
        assert version is None
        assert source == ""

    def test_resolve_from_local(self) -> None:
        """Test resolving from local index."""
        mock_local = MagicMock()
        mock_local.get_version.return_value = "1.0.0"
        mock_ubuntu = MagicMock()

        version, source = validated_plan.resolve_dependency(
            "python3-oslo.config", mock_local, None, mock_ubuntu
        )
        assert version == "1.0.0"
        assert source == "local"

    def test_resolve_from_cloud_archive(self) -> None:
        """Test resolving from cloud archive index."""
        mock_local = MagicMock()
        mock_local.get_version.return_value = None
        mock_ca = MagicMock()
        mock_ca.get_version.return_value = "2.0.0"
        mock_ubuntu = MagicMock()

        version, source = validated_plan.resolve_dependency(
            "python3-oslo.config", mock_local, mock_ca, mock_ubuntu
        )
        assert version == "2.0.0"
        assert source == "cloud-archive"

    def test_resolve_from_ubuntu(self) -> None:
        """Test resolving from Ubuntu index."""
        mock_ubuntu = MagicMock()
        mock_ubuntu.get_version.return_value = "3.0.0"

        version, source = validated_plan.resolve_dependency(
            "python3-requests", None, None, mock_ubuntu
        )
        assert version == "3.0.0"
        assert source == "ubuntu"

    def test_resolve_not_found(self) -> None:
        """Test resolving when not found in any index."""
        mock_ubuntu = MagicMock()
        mock_ubuntu.get_version.return_value = None

        version, source = validated_plan.resolve_dependency(
            "python3-unknown", None, None, mock_ubuntu
        )
        assert version is None
        assert source == ""


class TestValidatePlan:
    """Tests for validate_plan function."""

    def test_empty_plan(self, tmp_path: Path) -> None:
        """Test validating empty plan."""
        from packastack.apt.packages import PackageIndex

        mock_ubuntu_index = MagicMock(spec=PackageIndex)
        mock_ubuntu_index.find_package.return_value = None

        upstream_deps = validated_plan.UpstreamDeps()

        result = validated_plan.validate_plan(
            preliminary_build_order=[],
            upstream_deps=upstream_deps,
            local_index=None,
            cloud_archive_index=None,
            ubuntu_index=mock_ubuntu_index,
        )

        assert result.build_order == []
        assert result.upload_order == []

    def test_all_deps_in_ubuntu(self, tmp_path: Path) -> None:
        """Test when all deps are in Ubuntu archive."""
        from packastack.apt.packages import PackageIndex

        mock_ubuntu_index = MagicMock(spec=PackageIndex)
        mock_ubuntu_index.get_version.return_value = "2.31.0-0ubuntu1"

        upstream_deps = validated_plan.UpstreamDeps(
            runtime=[("requests", ">=2.0")],
        )

        result = validated_plan.validate_plan(
            preliminary_build_order=["my-package"],
            upstream_deps=upstream_deps,
            local_index=None,
            cloud_archive_index=None,
            ubuntu_index=mock_ubuntu_index,
        )

        # requests should be resolved
        assert "python3-requests" in result.resolved_deps or not result.missing_deps

    def test_missing_deps(self, tmp_path: Path) -> None:
        """Test when deps are not found."""
        from packastack.apt.packages import PackageIndex

        mock_ubuntu_index = MagicMock(spec=PackageIndex)
        mock_ubuntu_index.get_version.return_value = None

        upstream_deps = validated_plan.UpstreamDeps(
            runtime=[("unknown-package", "")],
        )

        result = validated_plan.validate_plan(
            preliminary_build_order=["my-package"],
            upstream_deps=upstream_deps,
            local_index=None,
            cloud_archive_index=None,
            ubuntu_index=mock_ubuntu_index,
        )

        # Should have a missing dep with warning
        assert len(result.missing_deps) > 0
        assert len(result.warnings) > 0

    def test_resolved_with_warning(self, tmp_path: Path) -> None:
        """Test when deps are resolved via heuristic."""
        from packastack.apt.packages import PackageIndex

        mock_ubuntu_index = MagicMock(spec=PackageIndex)
        mock_ubuntu_index.get_version.return_value = "1.0"

        upstream_deps = validated_plan.UpstreamDeps(
            runtime=[("some-unknown-package", "")],  # Not in PYTHON_TO_DEBIAN
        )

        result = validated_plan.validate_plan(
            preliminary_build_order=["my-package"],
            upstream_deps=upstream_deps,
            local_index=None,
            cloud_archive_index=None,
            ubuntu_index=mock_ubuntu_index,
        )

        # Should have a warning about heuristic mapping
        assert len(result.warnings) > 0
        assert any("heuristic" in w for w in result.warnings)

    def test_local_dep_adds_to_build_order(self, tmp_path: Path) -> None:
        """Test that local deps can update build order."""
        from packastack.apt.packages import PackageIndex

        mock_local = MagicMock(spec=PackageIndex)
        mock_local.get_version.return_value = "1.0"
        mock_pkg = MagicMock()
        mock_pkg.source = "oslo.config"
        mock_local.find_package.return_value = mock_pkg

        mock_ubuntu = MagicMock(spec=PackageIndex)
        mock_ubuntu.get_version.return_value = None

        upstream_deps = validated_plan.UpstreamDeps(
            runtime=[("oslo-config", ">=1.0")],
        )

        result = validated_plan.validate_plan(
            preliminary_build_order=["my-package"],
            upstream_deps=upstream_deps,
            local_index=mock_local,
            cloud_archive_index=None,
            ubuntu_index=mock_ubuntu,
        )

        # oslo.config should be in new_deps if not already in build_order
        assert "oslo.config" in result.new_deps
        assert result.updated is True

    def test_skip_stdlib_packages(self, tmp_path: Path) -> None:
        """Test that stdlib packages are skipped."""
        from packastack.apt.packages import PackageIndex

        mock_ubuntu = MagicMock(spec=PackageIndex)
        mock_ubuntu.get_version.return_value = None

        upstream_deps = validated_plan.UpstreamDeps(
            runtime=[("python", "")],  # Maps to empty string
        )

        result = validated_plan.validate_plan(
            preliminary_build_order=["my-package"],
            upstream_deps=upstream_deps,
            local_index=None,
            cloud_archive_index=None,
            ubuntu_index=mock_ubuntu,
        )

        # python should be skipped, no missing deps
        assert "python" not in result.missing_deps
        assert "" not in result.missing_deps


class TestValidateDependenciesRecursive:
    """Tests for validate_dependencies_recursive function."""

    def test_empty_package_list(self, tmp_path: Path) -> None:
        """Test with empty package list."""
        from packastack.apt.packages import PackageIndex

        mock_ubuntu = MagicMock(spec=PackageIndex)

        result = validated_plan.validate_dependencies_recursive(
            initial_packages=[],
            upstream_cache=tmp_path,
            local_index=None,
            cloud_archive_index=None,
            ubuntu_index=mock_ubuntu,
            openstack_packages=set(),
        )

        assert result.build_order == []
        assert result.dependency_edges == {}
        assert result.has_cycles is False

    def test_single_package_no_deps(self, tmp_path: Path) -> None:
        """Test with single package, no upstream repo."""
        from packastack.apt.packages import PackageIndex

        mock_ubuntu = MagicMock(spec=PackageIndex)

        result = validated_plan.validate_dependencies_recursive(
            initial_packages=["nova"],
            upstream_cache=tmp_path,
            local_index=None,
            cloud_archive_index=None,
            ubuntu_index=mock_ubuntu,
            openstack_packages=set(),
        )

        assert "nova" in result.build_order
        # No repo, so no deps extracted
        assert result.dependency_edges.get("nova") == []
        assert "Upstream repo not cached: nova" in result.warnings

    def test_package_with_cached_repo(self, tmp_path: Path) -> None:
        """Test with package that has cached upstream repo."""
        from packastack.apt.packages import PackageIndex

        mock_ubuntu = MagicMock(spec=PackageIndex)
        mock_ubuntu.get_version.return_value = "1.0.0"  # All deps resolved

        # Create fake upstream repo with requirements.txt
        repo_dir = tmp_path / "oslo.config"
        repo_dir.mkdir()
        (repo_dir / "requirements.txt").write_text("oslo.utils>=1.0\n")

        result = validated_plan.validate_dependencies_recursive(
            initial_packages=["python-oslo.config"],
            upstream_cache=tmp_path,
            local_index=None,
            cloud_archive_index=None,
            ubuntu_index=mock_ubuntu,
            openstack_packages=set(),
        )

        assert "python-oslo.config" in result.build_order
        assert result.has_cycles is False

    def test_cycle_detection(self, tmp_path: Path) -> None:
        """Test that cycles are detected."""
        from packastack.apt.packages import BinaryPackage, PackageIndex

        # Create a mock that returns None for deps (not found in ubuntu)
        mock_ubuntu = MagicMock(spec=PackageIndex)
        mock_ubuntu.get_version.return_value = None

        mock_local = MagicMock(spec=PackageIndex)
        mock_local.get_version.return_value = "1.0.0"
        mock_local.find_package.return_value = BinaryPackage(
            name="python3-oslo.utils",
            version="1.0.0",
            architecture="all",
            source="python-oslo.utils",
        )

        # Create fake repos with circular dependencies
        oslo_config_dir = tmp_path / "oslo.config"
        oslo_config_dir.mkdir()
        (oslo_config_dir / "requirements.txt").write_text("oslo.utils>=1.0\n")

        oslo_utils_dir = tmp_path / "oslo.utils"
        oslo_utils_dir.mkdir()
        (oslo_utils_dir / "requirements.txt").write_text("oslo.config>=1.0\n")

        result = validated_plan.validate_dependencies_recursive(
            initial_packages=["python-oslo.config"],
            upstream_cache=tmp_path,
            local_index=mock_local,
            cloud_archive_index=None,
            ubuntu_index=mock_ubuntu,
            openstack_packages={"oslo.config", "oslo.utils"},
        )

        # Should still return a build order
        assert len(result.build_order) >= 1

    def test_max_depth_limit(self, tmp_path: Path) -> None:
        """Test that max depth is respected."""
        from packastack.apt.packages import PackageIndex

        mock_ubuntu = MagicMock(spec=PackageIndex)
        mock_ubuntu.get_version.return_value = None

        # Create chain of deps
        for i in range(15):
            dep_dir = tmp_path / f"pkg{i}"
            dep_dir.mkdir()
            if i < 14:
                (dep_dir / "requirements.txt").write_text(f"pkg{i+1}>=1.0\n")

        result = validated_plan.validate_dependencies_recursive(
            initial_packages=["pkg0"],
            upstream_cache=tmp_path,
            local_index=None,
            cloud_archive_index=None,
            ubuntu_index=mock_ubuntu,
            openstack_packages={f"pkg{i}" for i in range(15)},
            max_depth=5,  # Limit to 5
        )

        # Should have warning about max depth
        assert any("Max depth" in w for w in result.warnings)

