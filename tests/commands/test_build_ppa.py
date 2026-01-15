"""Tests for PPA upload workflow in build command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from packastack.core.context import BuildRequest
from packastack.commands import build

def _call_run_build(run: MagicMock, **kwargs) -> int:
    """Helper to call _run_build with a BuildRequest."""
    default_request = {
        "package": "nova",
        "target": "devel",
        "ubuntu_series": "noble",
        "cloud_archive": "",
        "build_type_str": "release",
        "milestone": "",
        "force": False,
        "offline": False,
        "include_retired": False,
        "yes": False,
        "binary": False,
        "builder": "sbuild",
        "build_deps": True,
        "min_version_policy": "enforce",
        "dep_report": True,
        "fail_on_cloud_archive_required": False,
        "fail_on_mir_required": False,
        "update_control_min_versions": False,
        "normalize_to_prev_lts_floor": False,
        "dry_run_control_edit": False,
        "no_cleanup": False,
        "no_spinner": True,
        "validate_plan_only": False,
        "plan_upload": False,
        "upload": False,
        "ppa_upload": False,
        "workspace_ref": lambda w: None,
    }
    default_request.update(kwargs)
    request = BuildRequest(**default_request)
    return build._run_build(run=run, request=request)

class TestPpaUpload:
    @pytest.fixture
    def mock_run(self):
        run = MagicMock()
        run.run_id = "test-run"
        return run
    
    @pytest.fixture
    def mock_context_setup(self):
        with patch("packastack.commands.build.load_config") as mock_conf, \
             patch("packastack.commands.build.resolve_paths") as mock_paths, \
             patch("packastack.commands.plan.run_plan_for_package") as mock_plan, \
             patch("packastack.build.single_build.setup_build_context") as mock_setup:
            
            # Setup mock config
            mock_conf.return_value = {
                "defaults": {"upload_ppa": "my/ppa"}
            }
            
            # Setup mock plan
            # run_plan_for_package returns (plan_result, plan_exit_code)
            mock_plan_result = MagicMock()
            mock_plan_result.build_order = ["nova"]
            mock_plan.return_value = (mock_plan_result, 0)
            
            # Setup mock ctx
            mock_ctx = MagicMock()
            mock_ctx.pkg_name = "nova"
            mock_ctx.pkg_repo = Path("/tmp/workspace/nova")
            mock_ctx.resolved_ubuntu = "noble"
            mock_ctx.workspace = Path("/tmp/workspace")
            mock_setup.return_value = (MagicMock(success=True), mock_ctx)
            
            yield mock_conf, mock_paths, mock_plan, mock_setup

    def test_ppa_upload_workflow(self, mock_run, mock_context_setup):
        """Test the PPA modify-commit-rebuild-upload-reset flow."""
        _, _, _, _ = mock_context_setup

        with patch("packastack.build.single_build.build_single_package") as mock_build, \
             patch("packastack.commands.build.update_changelog") as mock_changelog, \
             patch("packastack.commands.build.git_commit") as mock_commit, \
             patch("packastack.commands.build.subprocess.run") as mock_subprocess, \
             patch("packastack.commands.build._upload_to_ppa") as mock_upload:

            # Setup build outcomes
            # First build: normal success
            outcome1 = MagicMock()
            outcome1.success = True
            outcome1.artifacts = [Path("nova.dsc"), Path("nova.changes")]
            outcome1.new_version = "1.0.0"
            outcome1.build_type = "release"
            outcome1.signature_verified = True
            
            # Second build (PPA): success
            outcome2 = MagicMock()
            outcome2.success = True
            outcome2.artifacts = [Path("nova_ppa1.dsc"), Path("nova_ppa1.changes")]
            outcome2.new_version = "1.0.0~ppa1"
            outcome2.build_type = "release"
            outcome2.signature_verified = True
            
            # Configure side_effect to return different outcomes
            mock_build.side_effect = [outcome1, outcome2]

            # Run build with ppa_upload=True
            result = _call_run_build(mock_run, package="nova", ppa_upload=True)

            assert result == 0
            
            # Verify Changelog update
            mock_changelog.assert_called_once()
            call_args = mock_changelog.call_args
            assert call_args.kwargs["version"] == "1.0.0~ppa1"
            assert "Automated PPA build" in call_args.kwargs["changes"]

            # Verify Commit
            mock_commit.assert_called_once()
            assert "PPA build 1.0.0~ppa1" in mock_commit.call_args[0][1]

            # Verify Rebuild
            assert mock_build.call_count == 2
            # Check re-entry context
            # second call should have happened (side_effect exhausted)

            # Verify Upload
            mock_upload.assert_called_once()
            uploaded_file = mock_upload.call_args[0][0]
            assert str(uploaded_file) == "nova_ppa1.changes"

            # Verify Reset
            mock_subprocess.assert_called_once()
            assert mock_subprocess.call_args[0][0] == ["git", "reset", "--hard", "HEAD^"]

    def test_ppa_upload_not_configured(self, mock_run, mock_context_setup):
        """Test warning when PPA upload is requested but not configured."""
        mock_conf, _, _, _ = mock_context_setup
        
        # Override config to remove upload_ppa
        mock_conf.return_value = {"defaults": {}}

        with patch("packastack.build.single_build.build_single_package") as mock_build, \
             patch("packastack.commands.build.update_changelog") as mock_changelog, \
             patch("packastack.commands.build.git_commit") as mock_commit, \
             patch("packastack.commands.build.subprocess.run") as mock_subprocess, \
             patch("packastack.commands.build._upload_to_ppa") as mock_upload:

            outcome1 = MagicMock()
            outcome1.success = True
            outcome1.artifacts = [Path("nova.dsc"), Path("nova.changes")]
            outcome1.new_version = "1.0.0"
            outcome1.build_type = "release"
            mock_build.return_value = outcome1

            result = _call_run_build(mock_run, package="nova", ppa_upload=True)

            assert result == 0
            
            # Should NOT attempt workflows
            mock_changelog.assert_not_called()
            mock_commit.assert_not_called()
            # Only called once for the main build
            assert mock_build.call_count == 1
            mock_upload.assert_not_called()
            mock_subprocess.assert_not_called()

class TestUploadToPpa:
    """Test the internal _upload_to_ppa helper function."""

    @pytest.mark.parametrize("ppa_input, expected_target", [
        ("my/ppa", "ppa:my/ppa"),
        ("ppa:my/ppa", "ppa:my/ppa"),
    ])
    def test_dput_command_formation(self, ppa_input, expected_target):
        mock_run = MagicMock()
        changes_file = MagicMock(spec=Path)
        changes_file.exists.return_value = True
        changes_file.name = "pkg.changes"
        changes_file.__str__.return_value = "/tmp/pkg.changes"

        with patch("packastack.commands.build.subprocess.run") as mock_sub:
            mock_sub.return_value.returncode = 0
            
            build._upload_to_ppa(changes_file, ppa_input, mock_run)
            
            expected_cmd = ["dput", expected_target, "/tmp/pkg.changes"]
            mock_sub.assert_called_once()
            args, _ = mock_sub.call_args
            assert args[0] == expected_cmd
