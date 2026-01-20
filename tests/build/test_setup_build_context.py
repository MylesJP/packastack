# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for deliverable name handling in setup_build_context.

These tests verify that the build context setup correctly uses deliverable names
(or pkg_name as fallback) when calling resolve_build_type_auto and is_snapshot_eligible.

This tests the fix for the bug where python-barbicanclient was using "barbicanclient"
instead of "python-barbicanclient" in is_snapshot_eligible calls, causing
"Project barbicanclient not found in series gazpacho" errors.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from packastack.build.single_build import SetupInputs, setup_build_context
from packastack.planning.type_selection import BuildType
from packastack.upstream.registry import ReleaseSourceType


@pytest.fixture
def mock_run():
    """Create a mock RunContext."""
    run = MagicMock()
    run.run_id = "test-run-123"
    run.run_path = "/tmp/test-run"
    run.log_event = MagicMock()
    run.write_summary = MagicMock()
    return run


@pytest.fixture
def base_setup_inputs(tmp_path, mock_run):
    """Create base SetupInputs for testing."""
    return SetupInputs(
        pkg_name="python-barbicanclient",
        target="gazpacho",
        ubuntu_series="resolute",
        cloud_archive="",
        build_type_str="auto",
        binary=False,
        builder="sbuild",
        force=False,
        offline=False,
        skip_repo_regen=False,
        no_spinner=True,
        build_deps=False,
        min_version_policy="enforce",
        dep_report=False,
        include_retired=False,
        fail_on_cloud_archive_required=False,
        fail_on_mir_required=False,
        update_control_min_versions=False,
        normalize_to_prev_lts_floor=False,
        dry_run_control_edit=False,
        resolved_build_type_str="auto",
        paths={
            "cache_root": tmp_path / "cache",
            "openstack_releases_repo": tmp_path / "releases",
            "ubuntu_archive_cache": tmp_path / "ubuntu-cache",
            "local_apt_repo": tmp_path / "local-repo",
        },
        cfg={
            "defaults": {
                "ubuntu_pockets": ["release", "updates"],
                "ubuntu_components": ["main", "universe"],
            },
            "mirrors": {
                "ubuntu_archive": "http://archive.ubuntu.com/ubuntu",
            },
        },
        run=mock_run,
    )


class TestDeliverableNameHandling:
    """Tests for deliverable name parameter passing.

    These tests verify the bug fix for python-barbicanclient where deliverable
    was None and caused "Project barbicanclient not found" errors.
    """

    @patch("packastack.build.type_resolution.resolve_build_type_auto")
    def test_deliverable_fallback_to_pkg_name_when_none(
        self, mock_auto_resolve, base_setup_inputs, tmp_path
    ):
        """Test that pkg_name is used when deliverable is None.

        This is the core bug fix: when upstream_config.release_source.deliverable is None
        (common for python-prefixed packages), we should pass pkg_name instead to
        resolve_build_type_auto.
        """
        # Create upstream_config with None deliverable
        mock_upstream_config = MagicMock()
        mock_upstream_config.release_source.deliverable = None  # This is the test condition
        mock_upstream_config.upstream.url = "https://opendev.org/openstack/python-barbicanclient.git"

        # Mock only what we need to get to the resolve_build_type_auto call
        with patch("packastack.build.phases.check_retirement_status") as mock_retirement, \
             patch("packastack.build.phases.resolve_upstream_registry") as mock_registry, \
             patch("packastack.target.series.resolve_series") as mock_resolve_series, \
             patch("packastack.upstream.releases.get_current_development_series") as mock_get_dev:

            # Setup mocks to get past initial checks
            mock_resolve_series.return_value = "resolute"
            mock_get_dev.return_value = "gazpacho"
            mock_retirement.return_value = (MagicMock(success=True), MagicMock())

            # Registry returns our test config
            mock_resolved = MagicMock()
            mock_resolved.config = mock_upstream_config
            mock_resolved.project = "barbicanclient"
            mock_resolved.resolution_source.value = "registry_defaults"

            mock_registry_info = MagicMock()
            mock_registry_info.resolved = mock_resolved
            mock_registry_info.registry.version = "1.0"
            mock_registry_info.registry.override_applied = False
            mock_registry.return_value = (MagicMock(success=True), mock_registry_info)

            # Make auto-resolve return quickly to stop execution
            mock_auto_resolve.return_value = (BuildType.SNAPSHOT, "", "NOT_IN_RELEASES")

            # Stop after auto-resolve by making next phase fail
            with patch("packastack.upstream.releases.get_previous_series") as mock_prev:
                mock_prev.return_value = None
                with patch("packastack.upstream.releases.is_snapshot_eligible") as mock_eligible:
                    mock_eligible.return_value = (False, "Test stop", None)

                    # Execute
                    _result, _ctx = setup_build_context(base_setup_inputs)

        # Verify resolve_build_type_auto was called with pkg_name (not None)
        assert mock_auto_resolve.called
        call_args = mock_auto_resolve.call_args
        assert call_args.kwargs["deliverable"] == "python-barbicanclient", \
            "deliverable parameter should be pkg_name when release_source.deliverable is None"
        assert call_args.kwargs["source_package"] == "python-barbicanclient"

    @patch("packastack.build.type_resolution.resolve_build_type_auto")
    def test_explicit_deliverable_value_used(
        self, mock_auto_resolve, base_setup_inputs, tmp_path
    ):
        """Test that explicit deliverable value is preserved when provided."""
        # Create upstream_config with explicit deliverable
        mock_upstream_config = MagicMock()
        mock_upstream_config.release_source.deliverable = "python-barbicanclient"
        mock_upstream_config.upstream.url = "https://opendev.org/openstack/python-barbicanclient.git"

        with patch("packastack.build.phases.check_retirement_status") as mock_retirement, \
             patch("packastack.build.phases.resolve_upstream_registry") as mock_registry, \
             patch("packastack.target.series.resolve_series") as mock_resolve_series, \
             patch("packastack.upstream.releases.get_current_development_series") as mock_get_dev:

            mock_resolve_series.return_value = "resolute"
            mock_get_dev.return_value = "gazpacho"
            mock_retirement.return_value = (MagicMock(success=True), MagicMock())

            mock_resolved = MagicMock()
            mock_resolved.config = mock_upstream_config
            mock_resolved.project = "barbicanclient"
            mock_resolved.resolution_source.value = "registry_defaults"

            mock_registry_info = MagicMock()
            mock_registry_info.resolved = mock_resolved
            mock_registry_info.registry.version = "1.0"
            mock_registry_info.registry.override_applied = False
            mock_registry.return_value = (MagicMock(success=True), mock_registry_info)

            mock_auto_resolve.return_value = (BuildType.SNAPSHOT, "", "NOT_IN_RELEASES")

            with patch("packastack.upstream.releases.get_previous_series") as mock_prev:
                mock_prev.return_value = None
                with patch("packastack.upstream.releases.is_snapshot_eligible") as mock_eligible:
                    mock_eligible.return_value = (False, "Test stop", None)

                    _result, _ctx = setup_build_context(base_setup_inputs)

        # Verify the explicit deliverable was used
        call_args = mock_auto_resolve.call_args
        assert call_args.kwargs["deliverable"] == "python-barbicanclient", \
            "explicit deliverable value should be preserved"

    @patch("packastack.upstream.releases.is_snapshot_eligible")
    def test_policy_check_uses_deliverable_or_pkg_name(
        self, mock_eligible, base_setup_inputs, tmp_path
    ):
        """Test that is_snapshot_eligible gets correct project name."""
        # Create upstream_config with None deliverable
        mock_upstream_config = MagicMock()
        mock_upstream_config.release_source.deliverable = None
        mock_upstream_config.upstream.url = "https://opendev.org/openstack/python-barbicanclient.git"

        with patch("packastack.build.phases.check_retirement_status") as mock_retirement, \
             patch("packastack.build.phases.resolve_upstream_registry") as mock_registry, \
             patch("packastack.target.series.resolve_series") as mock_resolve_series, \
             patch("packastack.upstream.releases.get_current_development_series") as mock_get_dev, \
             patch("packastack.build.type_resolution.resolve_build_type_auto") as mock_auto, \
             patch("packastack.upstream.releases.get_previous_series") as mock_prev:

            mock_resolve_series.return_value = "resolute"
            mock_get_dev.return_value = "gazpacho"
            mock_retirement.return_value = (MagicMock(success=True), MagicMock())

            mock_resolved = MagicMock()
            mock_resolved.config = mock_upstream_config
            mock_resolved.project = "barbicanclient"
            mock_resolved.resolution_source.value = "registry_defaults"

            mock_registry_info = MagicMock()
            mock_registry_info.resolved = mock_resolved
            mock_registry_info.registry.version = "1.0"
            mock_registry_info.registry.override_applied = False
            mock_registry.return_value = (MagicMock(success=True), mock_registry_info)

            # Return SNAPSHOT so policy check is executed
            mock_auto.return_value = (BuildType.SNAPSHOT, "", "NOT_IN_RELEASES")
            mock_prev.return_value = "flamingo"

            # Policy check blocks to stop execution
            mock_eligible.return_value = (False, "Test stop", None)

            _result, _ctx = setup_build_context(base_setup_inputs)

        # Verify is_snapshot_eligible was called with correct project name
        assert mock_eligible.called
        call_args = mock_eligible.call_args
        # Third argument is the project name
        assert call_args.args[2] == "python-barbicanclient", \
            "is_snapshot_eligible should be called with pkg_name when deliverable is None"


    def test_policy_check_uses_pkg_name_when_deliverable_is_stripped(
        self, base_setup_inputs, tmp_path
    ):
        """When deliverable is the stripped project name (e.g., 'barbicanclient'),
        but pkg_name has a prefix (e.g., 'python-barbicanclient'), use pkg_name."""
        # This simulates the real bug: registry sets deliverable to the stripped name
        # Create test config where deliverable is the stripped name
        mock_upstream_config = MagicMock()
        mock_release_source = MagicMock()
        # This is the key: deliverable is "barbicanclient" not "python-barbicanclient"
        mock_release_source.deliverable = "barbicanclient"
        mock_release_source.type = ReleaseSourceType.OPENSTACK_RELEASES
        mock_upstream_config.release_source = mock_release_source
        mock_upstream_config.upstream.url = "https://opendev.org/openstack/python-barbicanclient.git"
        mock_upstream_config.build_repos = []

        # Test setup with proper mocking pattern
        with patch("packastack.build.phases.check_retirement_status") as mock_retirement, \
             patch("packastack.build.phases.resolve_upstream_registry") as mock_registry, \
             patch("packastack.target.series.resolve_series") as mock_resolve_series, \
             patch("packastack.upstream.releases.get_current_development_series") as mock_get_dev:

            # Setup mocks to get past initial checks
            mock_resolve_series.return_value = "resolute"
            mock_get_dev.return_value = "gazpacho"
            mock_retirement.return_value = (MagicMock(success=True), MagicMock())

            # Registry returns config with stripped deliverable
            mock_resolved = MagicMock()
            mock_resolved.config = mock_upstream_config
            mock_resolved.project = "barbicanclient"
            mock_resolved.resolution_source.value = "registry_defaults"

            mock_registry_info = MagicMock()
            mock_registry_info.resolved = mock_resolved
            mock_registry_info.registry.version = "1.0"
            mock_registry_info.registry.override_applied = False
            mock_registry.return_value = (MagicMock(success=True), mock_registry_info)

            with patch("packastack.build.type_resolution.resolve_build_type_auto") as mock_auto, \
                 patch("packastack.upstream.releases.load_project_releases") as mock_load_releases:
                # Return SNAPSHOT so policy check is executed
                mock_auto.return_value = (BuildType.SNAPSHOT, "", "NOT_IN_RELEASES")

                # Mock load_project_releases to return None for stripped deliverable "barbicanclient"
                # but return releases for full package name "python-barbicanclient"
                def load_side_effect(repo, target, project_name):
                    if project_name == "barbicanclient":
                        return []  # No releases for stripped name
                    elif project_name == "python-barbicanclient":
                        return [MagicMock()]  # Has releases
                    return []
                mock_load_releases.side_effect = load_side_effect

                with patch("packastack.upstream.releases.get_previous_series") as mock_prev:
                    mock_prev.return_value = "flamingo"
                    with patch("packastack.upstream.releases.is_snapshot_eligible") as mock_eligible:
                        # Policy check blocks to stop execution
                        mock_eligible.return_value = (False, "Test stop", None)

                        _result, _ctx = setup_build_context(base_setup_inputs)

                # Verify is_snapshot_eligible was called with full pkg_name "python-barbicanclient"
                assert mock_eligible.called
                eligible_call_args = mock_eligible.call_args
                assert eligible_call_args.args[2] == "python-barbicanclient", \
                    "is_snapshot_eligible should use pkg_name when deliverable doesn't exist in releases"

                # Verify is_snapshot_eligible was called with full pkg_name, not stripped deliverable
                assert mock_eligible.called
                eligible_call_args = mock_eligible.call_args
                assert eligible_call_args.args[2] == "python-barbicanclient", \
                    "is_snapshot_eligible should use pkg_name when deliverable is stripped name"

    def test_main_package_with_matching_deliverable(self, base_setup_inputs, tmp_path):
        """Test packages where pkg_name equals deliverable (e.g., 'cinder').

        For main OpenStack packages like cinder, nova, keystone, etc., the package
        name matches the deliverable name exactly. This should work without any
        fallback logic.
        """
        # Modify inputs to use 'cinder' instead of 'python-barbicanclient'
        inputs = base_setup_inputs
        inputs.pkg_name = "cinder"

        mock_upstream_config = MagicMock()
        mock_release_source = MagicMock()
        # For main packages, deliverable matches pkg_name
        mock_release_source.deliverable = "cinder"
        mock_release_source.type = ReleaseSourceType.OPENSTACK_RELEASES
        mock_upstream_config.release_source = mock_release_source
        mock_upstream_config.upstream.url = "https://opendev.org/openstack/cinder.git"
        mock_upstream_config.build_repos = []

        with patch("packastack.build.phases.check_retirement_status") as mock_retirement, \
             patch("packastack.build.phases.resolve_upstream_registry") as mock_registry, \
             patch("packastack.target.series.resolve_series") as mock_resolve_series, \
             patch("packastack.upstream.releases.get_current_development_series") as mock_get_dev:

            # Setup mocks to get past initial checks
            mock_resolve_series.return_value = "resolute"
            mock_get_dev.return_value = "gazpacho"
            mock_retirement.return_value = (MagicMock(success=True), MagicMock())

            # Registry returns config with matching deliverable
            mock_resolved = MagicMock()
            mock_resolved.config = mock_upstream_config
            mock_resolved.project = "cinder"
            mock_resolved.resolution_source.value = "registry_defaults"

            mock_registry_info = MagicMock()
            mock_registry_info.resolved = mock_resolved
            mock_registry_info.registry.version = "1.0"
            mock_registry_info.registry.override_applied = False
            mock_registry.return_value = (MagicMock(success=True), mock_registry_info)

            with patch("packastack.build.type_resolution.resolve_build_type_auto") as mock_auto, \
                 patch("packastack.upstream.releases.load_project_releases") as mock_load_releases:
                # Return SNAPSHOT so policy check is executed
                mock_auto.return_value = (BuildType.SNAPSHOT, "", "NOT_IN_RELEASES")

                # Mock load_project_releases to return releases for "cinder"
                def load_side_effect(repo, target, project_name):
                    if project_name == "cinder":
                        return [MagicMock()]  # Has releases
                    return []
                mock_load_releases.side_effect = load_side_effect

                with patch("packastack.upstream.releases.get_previous_series") as mock_prev:
                    mock_prev.return_value = "flamingo"
                    with patch("packastack.upstream.releases.is_snapshot_eligible") as mock_eligible:
                        # Policy check blocks to stop execution
                        mock_eligible.return_value = (False, "Test stop", None)

                        _result, _ctx = setup_build_context(inputs)

                # Verify is_snapshot_eligible was called with "cinder"
                assert mock_eligible.called
                eligible_call_args = mock_eligible.call_args
                assert eligible_call_args.args[2] == "cinder", \
                    "is_snapshot_eligible should use deliverable 'cinder' when it exists in releases"

                # Verify load_project_releases was called with "cinder" and found releases
                assert mock_load_releases.called
                # Should have been called with "cinder" and found it
                load_calls = [call.args[2] for call in mock_load_releases.call_args_list]
                assert "cinder" in load_calls, "Should have checked if 'cinder' exists in releases"


