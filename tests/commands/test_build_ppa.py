"""Tests for PPA upload workflow in build command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from packastack.commands import build
from packastack.core.context import BuildRequest


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
        with (
            patch("packastack.commands.build.load_config") as mock_conf,
            patch("packastack.commands.build.resolve_paths") as mock_paths,
            patch("packastack.commands.plan.run_plan_for_package") as mock_plan,
            patch("packastack.build.single_build.setup_build_context") as mock_setup,
        ):
            mock_conf.return_value = {"defaults": {"upload_ppa": "my/ppa"}}

            mock_plan_result = MagicMock()
            mock_plan_result.build_order = ["nova"]
            mock_plan.return_value = (mock_plan_result, 0)

            mock_ctx = MagicMock()
            mock_ctx.pkg_name = "nova"
            mock_ctx.pkg_repo = Path("/tmp/workspace/nova")
            mock_ctx.resolved_ubuntu = "noble"
            mock_ctx.workspace = Path("/tmp/workspace")
            mock_ctx.update_control_min_versions = True
            mock_ctx.normalize_to_prev_lts_floor = True
            mock_ctx.dry_run_control_edit = False
            mock_setup.return_value = (MagicMock(success=True), mock_ctx)

            yield mock_conf, mock_paths, mock_plan, mock_setup

    def test_ppa_upload_workflow(self, mock_run, mock_context_setup):
        """Test the PPA modify-commit-rebuild-upload-reset flow."""
        _, _, _, mock_setup = mock_context_setup
        mock_ctx = mock_setup.return_value[1]

        with (
            patch("packastack.build.single_build.build_single_package") as mock_build,
            patch("packastack.commands.build._append_ppa_suffix_to_changelog") as mock_bump,
            patch("packastack.commands.build.git_commit") as mock_commit,
            patch("packastack.commands.build.subprocess.run") as mock_subprocess,
            patch("packastack.commands.build._build_ppa_source") as mock_ppa_build,
            patch("packastack.commands.build._ensure_changes_files_present") as mock_stage,
            patch("packastack.commands.build._upload_to_ppa") as mock_upload,
        ):
            outcome1 = MagicMock()
            outcome1.success = True
            outcome1.artifacts = [Path("nova.dsc"), Path("nova.changes")]
            outcome1.new_version = "1.0.0"
            outcome1.build_type = "release"
            outcome1.signature_verified = True

            ppa_artifacts = [
                Path("nova_ppa1.dsc"),
                Path("nova_ppa1_amd64.changes"),
                Path("nova_source.changes"),
                Path("nova_ppa1_source.changes"),
            ]

            mock_build.return_value = outcome1
            mock_ppa_build.return_value = (True, ppa_artifacts, "")

            status_result = MagicMock()
            status_result.stdout = ""
            mock_subprocess.side_effect = [status_result, MagicMock()]
            mock_stage.return_value = True
            mock_bump.return_value = "1.0.0~ppa1"

            result = _call_run_build(mock_run, package="nova", ppa_upload=True)

            assert result == 0

            mock_bump.assert_called_once()
            bump_args = mock_bump.call_args
            assert bump_args.args[1] == "~ppa1"

            mock_commit.assert_called_once()
            assert "PPA build 1.0.0~ppa1" in mock_commit.call_args[0][1]

            assert mock_build.call_count == 1

            mock_upload.assert_called_once()
            uploaded_file = mock_upload.call_args[0][0]
            assert str(uploaded_file) == "nova_ppa1_source.changes"

            assert mock_subprocess.call_count == 2

            calls = mock_subprocess.call_args_list
            status_call = calls[0]
            reset_call = calls[1]

            assert status_call[0][0] == ["git", "status", "--porcelain"]
            assert reset_call[0][0] == ["git", "reset", "--hard", "HEAD^"]

            assert mock_ctx.update_control_min_versions is True
            assert mock_ctx.normalize_to_prev_lts_floor is True
            assert mock_ctx.dry_run_control_edit is False

    def test_ppa_upload_not_configured(self, mock_run, mock_context_setup):
        """Test warning when PPA upload is requested but not configured."""
        mock_conf, _, _, _ = mock_context_setup

        mock_conf.return_value = {"defaults": {}}

        with (
            patch("packastack.build.single_build.build_single_package") as mock_build,
            patch("packastack.commands.build._append_ppa_suffix_to_changelog") as mock_bump,
            patch("packastack.commands.build.git_commit") as mock_commit,
            patch("packastack.commands.build.subprocess.run") as mock_subprocess,
            patch("packastack.commands.build._upload_to_ppa") as mock_upload,
        ):
            outcome1 = MagicMock()
            outcome1.success = True
            outcome1.artifacts = [Path("nova.dsc"), Path("nova.changes")]
            outcome1.new_version = "1.0.0"
            outcome1.build_type = "release"
            mock_build.return_value = outcome1

            result = _call_run_build(mock_run, package="nova", ppa_upload=True)

            assert result == 0

            mock_bump.assert_not_called()
            mock_commit.assert_not_called()
            assert mock_build.call_count == 1
            mock_upload.assert_not_called()
            mock_subprocess.assert_not_called()


class TestUploadToPpa:
    """Test the internal _upload_to_ppa helper function."""

    @pytest.mark.parametrize(
        "ppa_input, expected_target",
        [
            ("my/ppa", "ppa:my/ppa"),
            ("ppa:my/ppa", "ppa:my/ppa"),
        ],
    )
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
