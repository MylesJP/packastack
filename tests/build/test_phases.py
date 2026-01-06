# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for build phases module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from packastack.build.errors import (
    EXIT_REGISTRY_ERROR,
    EXIT_RETIRED_PROJECT,
)
from packastack.build.phases import (
    RegistryResolutionResult,
    RetirementCheckResult,
    check_retirement_status,
    resolve_upstream_registry,
)
from packastack.build.types import PhaseResult


class TestRetirementCheckResult:
    """Tests for RetirementCheckResult dataclass."""

    def test_default_values(self):
        """Test that RetirementCheckResult has sensible defaults."""
        result = RetirementCheckResult()
        assert result.is_retired is False
        assert result.is_possibly_retired is False
        assert result.upstream_project is None
        assert result.source == ""
        assert result.description == ""

    def test_with_retired_values(self):
        """Test RetirementCheckResult with retired project data."""
        result = RetirementCheckResult(
            is_retired=True,
            upstream_project="nova",
            source="openstack/releases",
            description="Project EOL in Zed",
        )
        assert result.is_retired is True
        assert result.upstream_project == "nova"


class TestRegistryResolutionResult:
    """Tests for RegistryResolutionResult dataclass."""

    def test_default_values(self):
        """Test that RegistryResolutionResult has sensible defaults."""
        result = RegistryResolutionResult()
        assert result.registry is None
        assert result.resolved is None
        assert result.project_key == ""
        assert result.is_openstack_governed is False


class TestCheckRetirementStatus:
    """Tests for check_retirement_status function."""

    def test_skips_check_when_include_retired_true(self, tmp_path):
        """Test that retirement check is skipped when include_retired is True."""
        run = MagicMock()
        
        phase_result, retirement_result = check_retirement_status(
            pkg_name="python-oslo.config",
            package="oslo.config",
            project_config_path=tmp_path / "project-config",
            releases_repo=tmp_path / "releases",
            openstack_target="2025.1",
            include_retired=True,  # Skip check
            offline=False,
            run=run,
        )
        
        assert phase_result.success is True
        assert retirement_result.is_retired is False
        # Should not log any events
        run.log_event.assert_not_called()

    def test_skips_check_when_no_project_config_path(self, tmp_path):
        """Test that retirement check is skipped when project_config_path is None."""
        run = MagicMock()
        
        phase_result, retirement_result = check_retirement_status(
            pkg_name="python-oslo.config",
            package="oslo.config",
            project_config_path=None,
            releases_repo=tmp_path / "releases",
            openstack_target="2025.1",
            include_retired=False,
            offline=False,
            run=run,
        )
        
        assert phase_result.success is True
        assert retirement_result.is_retired is False

    @patch("packastack.build.phases._clone_or_update_project_config")
    @patch("packastack.build.phases.activity_spinner")
    def test_clones_project_config_when_missing(self, mock_spinner, mock_clone, tmp_path):
        """Test that project-config is cloned when missing and not offline."""
        run = MagicMock()
        project_config_path = tmp_path / "project-config"
        
        # Simulate directory creation during clone
        def create_dir(*args, **kwargs):
            project_config_path.mkdir(parents=True, exist_ok=True)
        mock_clone.side_effect = create_dir
        mock_spinner.return_value.__enter__ = MagicMock()
        mock_spinner.return_value.__exit__ = MagicMock()
        
        with patch("packastack.build.phases.RetirementChecker") as mock_checker_class:
            mock_checker = MagicMock()
            mock_checker.check_retirement.return_value = MagicMock(
                status=MagicMock(value="not_retired")  # Not retired
            )
            mock_checker_class.return_value = mock_checker
            
            check_retirement_status(
                pkg_name="python-oslo.config",
                package="oslo.config",
                project_config_path=project_config_path,
                releases_repo=tmp_path / "releases",
                openstack_target="2025.1",
                include_retired=False,
                offline=False,
                run=run,
            )
        
        mock_clone.assert_called_once()

    def test_skips_clone_when_offline(self, tmp_path):
        """Test that project-config is not cloned when offline mode is enabled."""
        run = MagicMock()
        project_config_path = tmp_path / "project-config"
        # Path does not exist and we're offline
        
        with patch("packastack.build.phases._clone_or_update_project_config") as mock_clone:
            phase_result, _ = check_retirement_status(
                pkg_name="python-oslo.config",
                package="oslo.config",
                project_config_path=project_config_path,
                releases_repo=tmp_path / "releases",
                openstack_target="2025.1",
                include_retired=False,
                offline=True,  # Offline mode
                run=run,
            )
            
            mock_clone.assert_not_called()
            # Should return OK since we can't check
            assert phase_result.success is True

    @patch("packastack.build.phases.RetirementChecker")
    def test_returns_failure_for_retired_project(self, mock_checker_class, tmp_path):
        """Test that retired projects return failure with proper exit code."""
        from packastack.upstream.retirement import RetirementStatus
        
        run = MagicMock()
        project_config_path = tmp_path / "project-config"
        project_config_path.mkdir(parents=True, exist_ok=True)
        
        mock_checker = MagicMock()
        mock_retirement_info = MagicMock()
        mock_retirement_info.status = RetirementStatus.RETIRED
        mock_retirement_info.upstream_project = "nova"
        mock_retirement_info.source = "project-config/gerrit/projects.yaml"
        mock_retirement_info.description = "Retired in Zed"
        mock_checker.check_retirement.return_value = mock_retirement_info
        mock_checker_class.return_value = mock_checker
        
        phase_result, retirement_result = check_retirement_status(
            pkg_name="python-nova",
            package="nova",
            project_config_path=project_config_path,
            releases_repo=tmp_path / "releases",
            openstack_target="2025.1",
            include_retired=False,
            offline=False,
            run=run,
        )
        
        assert phase_result.success is False
        assert phase_result.exit_code == EXIT_RETIRED_PROJECT
        assert retirement_result.is_retired is True
        assert retirement_result.upstream_project == "nova"
        assert retirement_result.source == "project-config/gerrit/projects.yaml"
        
        # Should log event
        run.log_event.assert_called()
        events = [call[0][0] for call in run.log_event.call_args_list]
        retired_events = [e for e in events if e.get("event") == "policy.retired_project"]
        assert len(retired_events) == 1
        
        # Should write summary
        run.write_summary.assert_called_once()

    @patch("packastack.build.phases.RetirementChecker")
    def test_warns_for_possibly_retired_project(self, mock_checker_class, tmp_path):
        """Test that possibly retired projects return success with warning."""
        from packastack.upstream.retirement import RetirementStatus
        
        run = MagicMock()
        project_config_path = tmp_path / "project-config"
        project_config_path.mkdir(parents=True, exist_ok=True)
        
        mock_checker = MagicMock()
        mock_retirement_info = MagicMock()
        mock_retirement_info.status = RetirementStatus.POSSIBLY_RETIRED
        mock_retirement_info.source = "openstack/releases"
        mock_checker.check_retirement.return_value = mock_retirement_info
        mock_checker_class.return_value = mock_checker
        
        phase_result, retirement_result = check_retirement_status(
            pkg_name="python-oslo.config",
            package="oslo.config",
            project_config_path=project_config_path,
            releases_repo=tmp_path / "releases",
            openstack_target="2025.1",
            include_retired=False,
            offline=False,
            run=run,
        )
        
        # Should still succeed (just a warning)
        assert phase_result.success is True
        assert retirement_result.is_possibly_retired is True
        assert retirement_result.source == "openstack/releases"
        
        # Should log warning event
        run.log_event.assert_called()

    @patch("packastack.build.phases.RetirementChecker")
    def test_strips_python_prefix_for_deliverable(self, mock_checker_class, tmp_path):
        """Test that python- prefix is stripped when looking up deliverable."""
        from packastack.upstream.retirement import RetirementStatus
        
        run = MagicMock()
        project_config_path = tmp_path / "project-config"
        project_config_path.mkdir(parents=True, exist_ok=True)
        
        mock_checker = MagicMock()
        mock_retirement_info = MagicMock()
        mock_retirement_info.status = MagicMock()  # Not retired
        mock_retirement_info.status.value = "not_retired"
        mock_retirement_info.status.__eq__ = lambda self, other: False
        mock_checker.check_retirement.return_value = mock_retirement_info
        mock_checker_class.return_value = mock_checker
        
        check_retirement_status(
            pkg_name="python-oslo.config",
            package="oslo.config",
            project_config_path=project_config_path,
            releases_repo=tmp_path / "releases",
            openstack_target="2025.1",
            include_retired=False,
            offline=False,
            run=run,
        )
        
        # Check that the deliverable was passed without python- prefix
        mock_checker.check_retirement.assert_called_once_with(
            "python-oslo.config", "oslo.config"
        )


class TestResolveUpstreamRegistry:
    """Tests for resolve_upstream_registry function."""

    @patch("packastack.upstream.releases.load_openstack_packages")
    @patch("packastack.upstream.registry.UpstreamsRegistry")
    def test_loads_registry_successfully(self, mock_registry_class, mock_load_pkgs, tmp_path):
        """Test successful registry loading."""
        run = MagicMock()
        
        mock_registry = MagicMock()
        mock_registry.version = "1.0"
        mock_registry.override_applied = False
        mock_registry.override_path = None
        mock_registry.warnings = []
        
        mock_resolved = MagicMock()
        mock_resolved.project = "oslo.config"
        mock_resolved.resolution_source = MagicMock(value="explicit")
        mock_resolved.config.upstream.host = "github.com"
        mock_resolved.config.upstream.url = "https://github.com/openstack/oslo.config"
        mock_resolved.config.tarball.prefer = []
        mock_resolved.config.signatures.mode = MagicMock(value="optional")
        
        mock_registry.resolve.return_value = mock_resolved
        mock_registry_class.return_value = mock_registry
        mock_load_pkgs.return_value = {}
        
        phase_result, registry_result = resolve_upstream_registry(
            package="oslo.config",
            pkg_name="python-oslo.config",
            releases_repo=tmp_path / "releases",
            openstack_target="2025.1",
            run=run,
        )
        
        assert phase_result.success is True
        assert registry_result.registry == mock_registry
        assert registry_result.resolved == mock_resolved
        assert registry_result.project_key == "oslo.config"

    @patch("packastack.upstream.releases.load_openstack_packages")
    @patch("packastack.upstream.registry.UpstreamsRegistry")
    def test_detects_openstack_governed_package(self, mock_registry_class, mock_load_pkgs, tmp_path):
        """Test that OpenStack-governed packages are detected."""
        run = MagicMock()
        
        mock_registry = MagicMock()
        mock_registry.version = "1.0"
        mock_registry.override_applied = False
        mock_registry.warnings = []
        
        mock_resolved = MagicMock()
        mock_resolved.project = "oslo.config"
        mock_resolved.resolution_source = MagicMock(value="explicit")
        mock_resolved.config.upstream.host = "github.com"
        mock_resolved.config.upstream.url = "https://github.com/openstack/oslo.config"
        mock_resolved.config.tarball.prefer = []
        mock_resolved.config.signatures.mode = MagicMock(value="optional")
        
        mock_registry.resolve.return_value = mock_resolved
        mock_registry_class.return_value = mock_registry
        
        # Package is in openstack/releases
        mock_load_pkgs.return_value = {"python-oslo.config": "oslo.config"}
        
        phase_result, registry_result = resolve_upstream_registry(
            package="oslo.config",
            pkg_name="python-oslo.config",
            releases_repo=tmp_path / "releases",
            openstack_target="2025.1",
            run=run,
        )
        
        assert registry_result.is_openstack_governed is True
        # Registry should have been called with openstack_governed=True
        mock_registry.resolve.assert_called_once_with("oslo.config", openstack_governed=True)

    @patch("packastack.upstream.registry.UpstreamsRegistry")
    def test_handles_registry_error(self, mock_registry_class, tmp_path):
        """Test that registry errors are handled properly."""
        run = MagicMock()
        
        mock_registry_class.side_effect = Exception("Failed to load registry")
        
        phase_result, registry_result = resolve_upstream_registry(
            package="oslo.config",
            pkg_name="python-oslo.config",
            releases_repo=tmp_path / "releases",
            openstack_target="2025.1",
            run=run,
        )
        
        assert phase_result.success is False
        assert phase_result.exit_code == EXIT_REGISTRY_ERROR
        assert registry_result is None
        
        run.write_summary.assert_called_once()

    @patch("packastack.upstream.releases.load_openstack_packages")
    @patch("packastack.upstream.registry.UpstreamsRegistry")
    def test_handles_project_not_found(self, mock_registry_class, mock_load_pkgs, tmp_path):
        """Test that ProjectNotFoundError is handled properly."""
        from packastack.upstream.registry import ProjectNotFoundError
        
        run = MagicMock()
        
        mock_registry = MagicMock()
        mock_registry.version = "1.0"
        mock_registry.override_applied = False
        mock_registry.warnings = []
        mock_registry.resolve.side_effect = ProjectNotFoundError("unknown-project")
        mock_registry_class.return_value = mock_registry
        mock_load_pkgs.return_value = {}
        
        phase_result, registry_result = resolve_upstream_registry(
            package="unknown-project",
            pkg_name="python-unknown-project",
            releases_repo=tmp_path / "releases",
            openstack_target="2025.1",
            run=run,
        )
        
        assert phase_result.success is False
        assert phase_result.exit_code == EXIT_REGISTRY_ERROR
        assert registry_result is None

    @patch("packastack.upstream.releases.load_openstack_packages")
    @patch("packastack.upstream.registry.UpstreamsRegistry")
    def test_logs_registry_warnings(self, mock_registry_class, mock_load_pkgs, tmp_path):
        """Test that registry warnings are logged."""
        run = MagicMock()
        
        mock_registry = MagicMock()
        mock_registry.version = "1.0"
        mock_registry.override_applied = True
        mock_registry.override_path = "/tmp/override.yaml"
        mock_registry.warnings = ["Deprecated field 'foo'", "Unknown option 'bar'"]
        
        mock_resolved = MagicMock()
        mock_resolved.project = "oslo.config"
        mock_resolved.resolution_source = MagicMock(value="explicit")
        mock_resolved.config.upstream.host = "github.com"
        mock_resolved.config.upstream.url = "https://github.com/openstack/oslo.config"
        mock_resolved.config.tarball.prefer = []
        mock_resolved.config.signatures.mode = MagicMock(value="optional")
        
        mock_registry.resolve.return_value = mock_resolved
        mock_registry_class.return_value = mock_registry
        mock_load_pkgs.return_value = {}
        
        resolve_upstream_registry(
            package="oslo.config",
            pkg_name="python-oslo.config",
            releases_repo=tmp_path / "releases",
            openstack_target="2025.1",
            run=run,
        )
        
        # Verify registry.loaded event was logged with override info
        events = [call[0][0] for call in run.log_event.call_args_list]
        loaded_events = [e for e in events if e.get("event") == "registry.loaded"]
        assert len(loaded_events) == 1
        assert loaded_events[0]["override_applied"] is True
