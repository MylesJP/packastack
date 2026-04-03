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

"""Tests for packastack.ai.patch_diagnosis module."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from packastack.ai.client import AIResponse
from packastack.ai.patch_diagnosis import (
    AutoDropResult,
    PatchDiagnosisResult,
    PatchRefreshResult,
    _extract_affected_paths,
    _extract_dep3_header,
    _get_deleted_files,
    _parse_patch_response,
    _parse_refresh_response,
    _revert_working_tree,
    _try_file_deletion_refresh,
    attempt_mechanical_refresh,
    auto_drop_upstreamed_patches,
    diagnose_patch_failure,
    refresh_failing_patch,
)


class TestParsePatchResponse:
    """Tests for _parse_patch_response function."""

    def test_parses_can_drop_yes(self) -> None:
        """Test parsing response that says patch can be dropped."""
        response = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Patch has been incorporated upstream in commit abc123\n"
                "CAN_DROP: YES\n"
                "EXPLANATION: The fix was merged upstream in version 18.0.0"
            ),
        )
        result = _parse_patch_response(response)
        assert result.diagnosed is True
        assert result.can_drop is True
        assert "merged upstream" in result.explanation

    def test_parses_can_drop_no(self) -> None:
        """Test parsing response that says patch cannot be dropped."""
        response = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Patch conflicts with renamed file\n"
                "CAN_DROP: NO\n"
                "EXPLANATION: The target file was renamed upstream"
            ),
        )
        result = _parse_patch_response(response)
        assert result.diagnosed is True
        assert result.can_drop is False
        assert "renamed" in result.explanation

    def test_falls_back_to_full_content(self) -> None:
        """Test fallback when structured fields not found."""
        response = AIResponse(
            success=True,
            content="This patch conflicts because the file was removed upstream.",
        )
        result = _parse_patch_response(response)
        assert result.diagnosed is True
        assert "conflicts" in result.explanation

    def test_uses_diagnosis_as_explanation_fallback(self) -> None:
        """Test DIAGNOSIS field used when EXPLANATION is missing."""
        response = AIResponse(
            success=True,
            content="DIAGNOSIS: Missing target file\nCAN_DROP: YES\n",
        )
        result = _parse_patch_response(response)
        assert "Missing target file" in result.explanation


class TestDiagnosePatchFailure:
    """Tests for diagnose_patch_failure function."""

    def _cfg_with_key(self) -> dict:
        return {"ai": {"api_key": "test-key", "model": "test", "max_tokens": 100, "timeout": 10}}

    @patch.dict(os.environ, {}, clear=True)
    def test_returns_not_diagnosed_when_no_key(self) -> None:
        """Test returns diagnosed=False when no API key."""
        result = diagnose_patch_failure(
            patch_name="fix.patch",
            patch_content="diff",
            pq_output="error",
            pkg_name="pkg",
            version="1.0",
            ubuntu_series="noble",
            cfg={"ai": {"api_key": None}},
        )
        assert result.diagnosed is False
        assert "no api key" in result.error.lower()

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_diagnoses_upstreamed_patch(self, mock_call) -> None:
        """Test successful diagnosis of upstreamed patch."""
        mock_call.return_value = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Patch upstreamed in v18.0\n"
                "CAN_DROP: YES\n"
                "EXPLANATION: This fix was merged upstream"
            ),
        )
        result = diagnose_patch_failure(
            patch_name="fix-py312.patch",
            patch_content="--- a/x\n+++ b/x\n",
            pq_output="already applied",
            pkg_name="python-aodh",
            version="19.0.0",
            ubuntu_series="plucky",
            cfg=self._cfg_with_key(),
        )
        assert result.diagnosed is True
        assert result.can_drop is True
        assert result.patch_name == "fix-py312.patch"

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_api_failure_returns_not_diagnosed(self, mock_call) -> None:
        """Test graceful degradation when API call fails."""
        mock_call.return_value = AIResponse(success=False, error="timeout")
        result = diagnose_patch_failure(
            patch_name="fix.patch",
            patch_content="diff",
            pq_output="error",
            pkg_name="pkg",
            version="1.0",
            ubuntu_series="noble",
            cfg=self._cfg_with_key(),
        )
        assert result.diagnosed is False
        assert "timeout" in result.error


class TestPatchDiagnosisResult:
    """Tests for PatchDiagnosisResult dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        result = PatchDiagnosisResult(diagnosed=False)
        assert result.diagnosed is False
        assert result.can_drop is False
        assert result.patch_name == ""
        assert result.explanation == ""
        assert result.error == ""


class TestAutoDropResult:
    """Tests for AutoDropResult dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        result = AutoDropResult()
        assert result.dropped == []
        assert result.skipped == []
        assert result.errors == []
        assert result.all_dropped is False


@dataclass
class _MockPatchReport:
    """Minimal mock of PatchHealthReport for testing."""

    patch_name: str
    success: bool = False
    failure_reason: str | None = None
    suggested_action: str = ""


class TestAutoDropUpstreamedPatches:
    """Tests for auto_drop_upstreamed_patches function."""

    def _cfg_with_key(self) -> dict:
        return {"ai": {"api_key": "test-key", "model": "test", "max_tokens": 100, "timeout": 10}}

    @patch.dict(os.environ, {}, clear=True)
    def test_returns_error_when_no_key(self, tmp_path: Path) -> None:
        """Test returns error when no API key configured."""
        result = auto_drop_upstreamed_patches(
            pkg_repo=tmp_path,
            upstreamed_reports=[_MockPatchReport(patch_name="fix.patch")],
            pkg_name="pkg",
            version="1.0",
            ubuntu_series="noble",
            cfg={"ai": {"api_key": None}},
        )
        assert result.all_dropped is False
        assert len(result.errors) == 1
        assert "no api key" in result.errors[0].lower()

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_drops_all_confirmed_patches(self, mock_call: MagicMock, tmp_path: Path) -> None:
        """Test drops all patches when AI confirms they are upstreamed."""
        patches_dir = tmp_path / "debian" / "patches"
        patches_dir.mkdir(parents=True)
        (patches_dir / "fix1.patch").write_text("diff1\n")
        (patches_dir / "fix2.patch").write_text("diff2\n")
        (patches_dir / "series").write_text("fix1.patch\nfix2.patch\n")

        mock_call.return_value = AIResponse(
            success=True,
            content="DIAGNOSIS: Upstreamed\nCAN_DROP: YES\nEXPLANATION: Merged upstream",
        )

        reports = [
            _MockPatchReport(patch_name="fix1.patch"),
            _MockPatchReport(patch_name="fix2.patch"),
        ]

        with patch("packastack.build.git_helpers.git_commit") as mock_commit:
            mock_commit.return_value = MagicMock(returncode=0, stderr="")
            result = auto_drop_upstreamed_patches(
                pkg_repo=tmp_path,
                upstreamed_reports=reports,
                pkg_name="pkg",
                version="1.0",
                ubuntu_series="noble",
                cfg=self._cfg_with_key(),
            )

        assert result.all_dropped is True
        assert result.dropped == ["fix1.patch", "fix2.patch"]
        assert result.skipped == []

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_skips_patches_ai_says_no(self, mock_call: MagicMock, tmp_path: Path) -> None:
        """Test skips patches when AI says not safe to drop."""
        patches_dir = tmp_path / "debian" / "patches"
        patches_dir.mkdir(parents=True)
        (patches_dir / "fix.patch").write_text("diff\n")
        (patches_dir / "series").write_text("fix.patch\n")

        mock_call.return_value = AIResponse(
            success=True,
            content="DIAGNOSIS: Still needed\nCAN_DROP: NO\nEXPLANATION: Patch fixes Ubuntu-specific issue",
        )

        result = auto_drop_upstreamed_patches(
            pkg_repo=tmp_path,
            upstreamed_reports=[_MockPatchReport(patch_name="fix.patch")],
            pkg_name="pkg",
            version="1.0",
            ubuntu_series="noble",
            cfg=self._cfg_with_key(),
        )

        assert result.all_dropped is False
        assert result.skipped == ["fix.patch"]
        assert (patches_dir / "fix.patch").exists()

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_handles_ai_call_failure(self, mock_call: MagicMock, tmp_path: Path) -> None:
        """Test handles AI call failure gracefully."""
        patches_dir = tmp_path / "debian" / "patches"
        patches_dir.mkdir(parents=True)
        (patches_dir / "fix.patch").write_text("diff\n")

        mock_call.return_value = AIResponse(success=False, error="timeout")

        result = auto_drop_upstreamed_patches(
            pkg_repo=tmp_path,
            upstreamed_reports=[_MockPatchReport(patch_name="fix.patch")],
            pkg_name="pkg",
            version="1.0",
            ubuntu_series="noble",
            cfg=self._cfg_with_key(),
        )

        assert result.all_dropped is False
        assert len(result.errors) == 1
        assert "fix.patch" in result.errors[0]

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_handles_drop_failure(self, mock_call: MagicMock, tmp_path: Path) -> None:
        """Test handles drop_patch failure gracefully."""
        patches_dir = tmp_path / "debian" / "patches"
        patches_dir.mkdir(parents=True)
        # Don't create the file so drop_patch fails with "not found"

        mock_call.return_value = AIResponse(
            success=True,
            content="DIAGNOSIS: Upstreamed\nCAN_DROP: YES\nEXPLANATION: Merged",
        )

        result = auto_drop_upstreamed_patches(
            pkg_repo=tmp_path,
            upstreamed_reports=[_MockPatchReport(patch_name="missing.patch")],
            pkg_name="pkg",
            version="1.0",
            ubuntu_series="noble",
            cfg=self._cfg_with_key(),
        )

        assert result.all_dropped is False
        assert len(result.errors) == 1
        assert "drop failed" in result.errors[0]

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_all_dropped_false_when_partial(self, mock_call: MagicMock, tmp_path: Path) -> None:
        """Test all_dropped is False when only some patches dropped."""
        patches_dir = tmp_path / "debian" / "patches"
        patches_dir.mkdir(parents=True)
        (patches_dir / "drop-me.patch").write_text("diff\n")
        (patches_dir / "keep-me.patch").write_text("diff\n")
        (patches_dir / "series").write_text("drop-me.patch\nkeep-me.patch\n")

        # First call: drop, second call: keep
        mock_call.side_effect = [
            AIResponse(
                success=True,
                content="DIAGNOSIS: Upstreamed\nCAN_DROP: YES\nEXPLANATION: Merged",
            ),
            AIResponse(
                success=True,
                content="DIAGNOSIS: Still needed\nCAN_DROP: NO\nEXPLANATION: Ubuntu-specific",
            ),
        ]

        reports = [
            _MockPatchReport(patch_name="drop-me.patch"),
            _MockPatchReport(patch_name="keep-me.patch"),
        ]

        with patch("packastack.build.git_helpers.git_commit") as mock_commit:
            mock_commit.return_value = MagicMock(returncode=0, stderr="")
            result = auto_drop_upstreamed_patches(
                pkg_repo=tmp_path,
                upstreamed_reports=reports,
                pkg_name="pkg",
                version="1.0",
                ubuntu_series="noble",
                cfg=self._cfg_with_key(),
            )

        assert result.all_dropped is False
        assert result.dropped == ["drop-me.patch"]
        assert result.skipped == ["keep-me.patch"]

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_handles_unexpected_exception(self, mock_call: MagicMock, tmp_path: Path) -> None:
        """Test handles unexpected exceptions gracefully."""
        mock_call.side_effect = RuntimeError("unexpected")

        result = auto_drop_upstreamed_patches(
            pkg_repo=tmp_path,
            upstreamed_reports=[_MockPatchReport(patch_name="fix.patch")],
            pkg_name="pkg",
            version="1.0",
            ubuntu_series="noble",
            cfg=self._cfg_with_key(),
        )

        assert result.all_dropped is False
        assert len(result.errors) == 1
        assert "unexpected error" in result.errors[0]


# =============================================================================
# Patch Refresh Tests
# =============================================================================


class TestPatchRefreshResult:
    """Tests for PatchRefreshResult dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        result = PatchRefreshResult(refreshed=False)
        assert result.refreshed is False
        assert result.patch_name == ""
        assert result.patch_content == ""
        assert result.explanation == ""
        assert result.error == ""

    def test_refreshed_result(self) -> None:
        """Test populated refresh result."""
        result = PatchRefreshResult(
            refreshed=True,
            patch_name="fix.patch",
            patch_content="diff --git ...",
            explanation="Updated context lines",
        )
        assert result.refreshed is True
        assert result.patch_name == "fix.patch"


class TestParseRefreshResponse:
    """Tests for _parse_refresh_response function."""

    def test_parses_refresh_action(self) -> None:
        """Test parsing a response with ACTION: REFRESH."""
        response = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Context lines changed in setup.cfg\n"
                "ACTION: REFRESH\n"
                "EXPLANATION: Updated context around the hunk\n"
                "PATCH_FILENAME: fix-setup.patch\n"
                "--- BEGIN PATCH ---\n"
                "--- a/setup.cfg\n"
                "+++ b/setup.cfg\n"
                "@@ -1,3 +1,3 @@\n"
                " context\n"
                "-old\n"
                "+new\n"
                " more\n"
                "--- END PATCH ---\n"
            ),
        )
        result = _parse_refresh_response(response, "fix-setup.patch")
        assert result.refreshed is True
        assert result.patch_name == "fix-setup.patch"
        assert "--- a/setup.cfg" in result.patch_content
        assert "Updated context" in result.explanation

    def test_parses_no_refresh_action(self) -> None:
        """Test parsing a response with ACTION: NO_REFRESH."""
        response = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Target code was completely rewritten\n"
                "ACTION: NO_REFRESH\n"
                "EXPLANATION: The function was removed upstream\n"
            ),
        )
        result = _parse_refresh_response(response, "fix.patch")
        assert result.refreshed is False
        assert result.patch_name == "fix.patch"
        assert "removed upstream" in result.explanation

    def test_no_markers_returns_not_refreshed(self) -> None:
        """Test response without patch markers is not refreshed."""
        response = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Needs update\n"
                "ACTION: REFRESH\n"
                "EXPLANATION: I updated the patch\n"
            ),
        )
        result = _parse_refresh_response(response, "fix.patch")
        assert result.refreshed is False

    def test_empty_patch_between_markers(self) -> None:
        """Test empty content between markers is not refreshed."""
        response = AIResponse(
            success=True,
            content=(
                "ACTION: REFRESH\n"
                "--- BEGIN PATCH ---\n"
                "   \n"
                "--- END PATCH ---\n"
            ),
        )
        result = _parse_refresh_response(response, "fix.patch")
        assert result.refreshed is False

    def test_fallback_explanation_from_content(self) -> None:
        """Test falls back to content when no structured fields."""
        response = AIResponse(
            success=True,
            content="Some unstructured response about the patch.\n--- BEGIN PATCH ---\ndiff\n--- END PATCH ---",
        )
        result = _parse_refresh_response(response, "fix.patch")
        assert result.refreshed is True
        assert "unstructured" in result.explanation

    def test_patch_filename_override(self) -> None:
        """Test PATCH_FILENAME field overrides the default name."""
        response = AIResponse(
            success=True,
            content=(
                "ACTION: REFRESH\n"
                "PATCH_FILENAME: renamed.patch\n"
                "--- BEGIN PATCH ---\ndiff content\n--- END PATCH ---"
            ),
        )
        result = _parse_refresh_response(response, "original.patch")
        assert result.patch_name == "renamed.patch"

    def test_patch_content_ends_with_newline(self) -> None:
        """Patch content must end with a newline for git apply."""
        response = AIResponse(
            success=True,
            content=(
                "ACTION: REFRESH\n"
                "--- BEGIN PATCH ---\n"
                "diff --git a/f.py b/f.py\n"
                "--- a/f.py\n"
                "+++ b/f.py\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
                "--- END PATCH ---\n"
            ),
        )
        result = _parse_refresh_response(response, "fix.patch")
        assert result.refreshed is True
        assert result.patch_content.endswith("\n")


class TestExtractAffectedPaths:
    """Tests for _extract_affected_paths function."""

    def test_extracts_paths_from_unified_diff(self) -> None:
        """Test extracts file paths from standard unified diff headers."""
        patch_content = (
            "--- a/setup.cfg\n"
            "+++ b/setup.cfg\n"
            "@@ -1,3 +1,3 @@\n"
            " x\n"
            "-old\n"
            "+new\n"
        )
        paths = _extract_affected_paths(patch_content)
        assert paths == ["setup.cfg"]

    def test_extracts_multiple_files(self) -> None:
        """Test extracts paths from multi-file patch."""
        patch_content = (
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
            "--- a/bar.py\n"
            "+++ b/bar.py\n"
            "@@ -1 +1 @@\n"
            "-c\n"
            "+d\n"
        )
        paths = _extract_affected_paths(patch_content)
        assert paths == ["foo.py", "bar.py"]

    def test_deduplicates_paths(self) -> None:
        """Test paths are deduplicated (--- and +++ for same file)."""
        patch_content = "--- a/same.py\n+++ b/same.py\n"
        paths = _extract_affected_paths(patch_content)
        assert paths == ["same.py"]

    def test_excludes_dev_null(self) -> None:
        """Test /dev/null is excluded (new or deleted file)."""
        patch_content = "--- /dev/null\n+++ b/new_file.py\n"
        paths = _extract_affected_paths(patch_content)
        assert paths == ["new_file.py"]

    def test_empty_patch(self) -> None:
        """Test returns empty list for non-patch content."""
        paths = _extract_affected_paths("not a patch\n")
        assert paths == []


class TestExtractDep3Header:
    """Tests for _extract_dep3_header function."""

    def test_extracts_header_before_diff_git(self) -> None:
        """Test extracts header before 'diff --git' line."""
        patch_content = (
            "From: Author <author@example.com>\n"
            "Date: Mon, 1 Jan 2024\n"
            "Subject: Fix bug\n"
            "\n"
            "Description of the fix.\n"
            "---\n"
            "diff --git a/file.py b/file.py\n"
            "--- a/file.py\n"
            "+++ b/file.py\n"
        )
        header = _extract_dep3_header(patch_content)
        assert "From: Author" in header
        assert "Subject: Fix bug" in header
        assert "Description of the fix." in header
        assert "diff --git" not in header

    def test_no_header(self) -> None:
        """Test returns empty string when patch starts with diff."""
        patch_content = "diff --git a/file.py b/file.py\n--- a/file.py\n"
        header = _extract_dep3_header(patch_content)
        assert header == ""

    def test_preserves_trailing_separator(self) -> None:
        """Test preserves the --- separator line."""
        patch_content = (
            "Subject: Fix\n"
            "---\n"
            " file.py | 1 +\n"
            "diff --git a/file.py b/file.py\n"
        )
        header = _extract_dep3_header(patch_content)
        assert header.endswith("diff --git a/file.py b/file.py\n") is False
        assert "---\n" in header


class TestRevertWorkingTree:
    """Tests for _revert_working_tree function."""

    def test_reverts_modifications(self, tmp_path: Path) -> None:
        """Test reverts file modifications in a real git repo."""
        from packastack.debpkg.gbp import run_command

        repo = tmp_path / "repo"
        repo.mkdir()
        run_command(["git", "init", "-b", "main"], cwd=repo)
        run_command(["git", "config", "user.email", "test@test"], cwd=repo)
        run_command(["git", "config", "user.name", "Test"], cwd=repo)
        (repo / "file.txt").write_text("original\n")
        run_command(["git", "add", "."], cwd=repo)
        run_command(["git", "commit", "-m", "init"], cwd=repo)

        # Modify tracked file and add untracked file
        (repo / "file.txt").write_text("modified\n")
        (repo / "new.txt").write_text("new\n")

        _revert_working_tree(repo)

        assert (repo / "file.txt").read_text() == "original\n"
        assert not (repo / "new.txt").exists()


class TestGetDeletedFiles:
    """Tests for _get_deleted_files function."""

    def test_returns_deleted_file_path(self) -> None:
        patch = (
            "diff --git a/tests/test_foo.py b/tests/test_foo.py\n"
            "deleted file mode 100644\n"
            "index abc1234..0000000\n"
            "--- a/tests/test_foo.py\n"
            "+++ /dev/null\n"
            "@@ -1,3 +0,0 @@\n"
            "-line1\n"
            "-line2\n"
            "-line3\n"
        )
        assert _get_deleted_files(patch) == ["tests/test_foo.py"]

    def test_returns_multiple_deleted_files(self) -> None:
        patch = (
            "diff --git a/a.py b/a.py\n"
            "deleted file mode 100644\n"
            "index abc..000\n"
            "--- a/a.py\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-a\n"
            "diff --git a/b.py b/b.py\n"
            "deleted file mode 100644\n"
            "index def..000\n"
            "--- a/b.py\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-b\n"
        )
        assert _get_deleted_files(patch) == ["a.py", "b.py"]

    def test_returns_empty_for_modification_patch(self) -> None:
        patch = (
            "diff --git a/file.py b/file.py\n"
            "--- a/file.py\n"
            "+++ b/file.py\n"
            "@@ -1,3 +1,3 @@\n"
            " ctx\n"
            "-old\n"
            "+new\n"
            " ctx\n"
        )
        assert _get_deleted_files(patch) == []

    def test_returns_empty_for_mixed_patch(self) -> None:
        """A patch that deletes one file and modifies another is not pure deletion."""
        patch = (
            "diff --git a/a.py b/a.py\n"
            "deleted file mode 100644\n"
            "index abc..000\n"
            "--- a/a.py\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-a\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        assert _get_deleted_files(patch) == []

    def test_returns_empty_for_empty_content(self) -> None:
        assert _get_deleted_files("") == []

    def test_returns_empty_for_no_diff_sections(self) -> None:
        assert _get_deleted_files("Subject: some patch\n---\n") == []


class TestTryFileDeletionRefresh:
    """Tests for _try_file_deletion_refresh function."""

    def _init_repo(self, tmp_path: Path) -> Path:
        from packastack.debpkg.gbp import run_command

        repo = tmp_path / "repo"
        repo.mkdir()
        run_command(["git", "init", "-b", "main"], cwd=repo)
        run_command(["git", "config", "user.email", "test@test"], cwd=repo)
        run_command(["git", "config", "user.name", "Test"], cwd=repo)
        return repo

    def test_refreshes_file_deletion_patch(self, tmp_path: Path) -> None:
        """Regenerates deletion diff when file content has changed."""
        from packastack.debpkg.gbp import run_command

        repo = self._init_repo(tmp_path)

        # Create a file with content that differs from the patch
        (repo / "test_foo.py").write_text("new_line1\nline2\nline3\n")
        run_command(["git", "add", "."], cwd=repo)
        run_command(["git", "commit", "-m", "init"], cwd=repo)

        # Patch tries to delete the old version (different content)
        original_patch = (
            "Subject: Remove test_foo\n"
            "---\n"
            "diff --git a/test_foo.py b/test_foo.py\n"
            "deleted file mode 100644\n"
            "index abc1234..0000000\n"
            "--- a/test_foo.py\n"
            "+++ /dev/null\n"
            "@@ -1,3 +0,0 @@\n"
            "-old_line1\n"
            "-line2\n"
            "-line3\n"
        )

        result = _try_file_deletion_refresh("rm.patch", original_patch, repo)

        assert result is not None
        assert result.refreshed is True
        assert result.patch_name == "rm.patch"
        assert "deleted file mode" in result.patch_content
        assert "-new_line1" in result.patch_content
        # DEP3 header preserved
        assert "Subject: Remove test_foo" in result.patch_content

    def test_returns_none_for_modification_patch(self, tmp_path: Path) -> None:
        repo = self._init_repo(tmp_path)
        (repo / "file.py").write_text("content\n")

        from packastack.debpkg.gbp import run_command

        run_command(["git", "add", "."], cwd=repo)
        run_command(["git", "commit", "-m", "init"], cwd=repo)

        mod_patch = (
            "diff --git a/file.py b/file.py\n"
            "--- a/file.py\n"
            "+++ b/file.py\n"
            "@@ -1 +1 @@\n"
            "-content\n"
            "+changed\n"
        )
        result = _try_file_deletion_refresh("fix.patch", mod_patch, repo)
        assert result is None

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        """Returns None if the file to delete no longer exists."""
        from packastack.debpkg.gbp import run_command

        repo = self._init_repo(tmp_path)
        (repo / "dummy").write_text("x\n")
        run_command(["git", "add", "."], cwd=repo)
        run_command(["git", "commit", "-m", "init"], cwd=repo)

        del_patch = (
            "diff --git a/gone.py b/gone.py\n"
            "deleted file mode 100644\n"
            "index abc..000\n"
            "--- a/gone.py\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-old\n"
        )
        result = _try_file_deletion_refresh("rm.patch", del_patch, repo)
        assert result is None

    def test_reverts_working_tree(self, tmp_path: Path) -> None:
        """Working tree is clean after refresh."""
        from packastack.debpkg.gbp import run_command

        repo = self._init_repo(tmp_path)
        (repo / "test_foo.py").write_text("content\n")
        run_command(["git", "add", "."], cwd=repo)
        run_command(["git", "commit", "-m", "init"], cwd=repo)

        del_patch = (
            "diff --git a/test_foo.py b/test_foo.py\n"
            "deleted file mode 100644\n"
            "index abc..000\n"
            "--- a/test_foo.py\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-old_content\n"
        )
        _try_file_deletion_refresh("rm.patch", del_patch, repo)

        # File should still exist after refresh
        assert (repo / "test_foo.py").exists()
        assert (repo / "test_foo.py").read_text() == "content\n"


class TestAttemptMechanicalRefresh:
    """Tests for attempt_mechanical_refresh function."""

    def _init_repo(self, tmp_path: Path) -> Path:
        """Create a minimal git repo with upstream-like content."""
        from packastack.debpkg.gbp import run_command

        repo = tmp_path / "repo"
        repo.mkdir()
        run_command(["git", "init", "-b", "main"], cwd=repo)
        run_command(["git", "config", "user.email", "test@test"], cwd=repo)
        run_command(["git", "config", "user.name", "Test"], cwd=repo)
        return repo

    def test_returns_false_when_strict_apply_passes(self, tmp_path: Path) -> None:
        """Test skips refresh when strict git apply already succeeds."""
        from packastack.debpkg.gbp import run_command

        repo = self._init_repo(tmp_path)

        # Create a file and commit
        (repo / "file.txt").write_text("line1\nline2\nline3\n")
        run_command(["git", "add", "."], cwd=repo)
        run_command(["git", "commit", "-m", "init"], cwd=repo)

        # Create a patch that applies cleanly with strict matching
        patches_dir = repo / "debian" / "patches"
        patches_dir.mkdir(parents=True)
        patch_content = (
            "Subject: Fix\n"
            "---\n"
            "diff --git a/file.txt b/file.txt\n"
            "--- a/file.txt\n"
            "+++ b/file.txt\n"
            "@@ -1,3 +1,3 @@\n"
            " line1\n"
            "-line2\n"
            "+line2-fixed\n"
            " line3\n"
        )
        patch_file = patches_dir / "fix.patch"
        patch_file.write_text(patch_content)

        result = attempt_mechanical_refresh("fix.patch", patch_file, repo)
        assert result.refreshed is False
        assert "already applies" in result.error.lower()

    def test_refreshes_with_offset(self, tmp_path: Path) -> None:
        """Test refreshes patch that has wrong line offsets."""
        from packastack.debpkg.gbp import run_command

        repo = self._init_repo(tmp_path)

        # Create a file with extra lines at the top (shifting offsets)
        (repo / "file.txt").write_text(
            "new1\nnew2\nnew3\nline1\nline2\nline3\n"
        )
        run_command(["git", "add", "."], cwd=repo)
        run_command(["git", "commit", "-m", "init"], cwd=repo)

        # Create a patch with outdated line numbers (expects line 1, but now it's line 4)
        patches_dir = repo / "debian" / "patches"
        patches_dir.mkdir(parents=True)
        patch_content = (
            "Subject: Fix offset\n"
            "---\n"
            "diff --git a/file.txt b/file.txt\n"
            "--- a/file.txt\n"
            "+++ b/file.txt\n"
            "@@ -1,3 +1,3 @@\n"
            " line1\n"
            "-line2\n"
            "+line2-fixed\n"
            " line3\n"
        )
        patch_file = patches_dir / "fix.patch"
        patch_file.write_text(patch_content)

        result = attempt_mechanical_refresh("fix.patch", patch_file, repo)

        assert result.refreshed is True
        assert result.patch_name == "fix.patch"
        # The refreshed patch should mention the correct line numbers
        assert "line2-fixed" in result.patch_content
        # DEP3 header should be preserved
        assert "Subject: Fix offset" in result.patch_content

    def test_preserves_dep3_header(self, tmp_path: Path) -> None:
        """Test DEP3 headers from original patch are preserved."""
        from packastack.debpkg.gbp import run_command

        repo = self._init_repo(tmp_path)

        (repo / "file.txt").write_text("extra\nline1\nold\nline3\n")
        run_command(["git", "add", "."], cwd=repo)
        run_command(["git", "commit", "-m", "init"], cwd=repo)

        dep3_header = (
            "From: Test Author <test@example.com>\n"
            "Date: Tue, 1 Jan 2024\n"
            "Subject: Important fix\n"
            "\n"
            "Ubuntu-Bug: https://bugs.launchpad.net/bug/123\n"
            "Forwarded: no\n"
            "---\n"
        )
        diff_body = (
            "diff --git a/file.txt b/file.txt\n"
            "--- a/file.txt\n"
            "+++ b/file.txt\n"
            "@@ -1,3 +1,3 @@\n"
            " line1\n"
            "-old\n"
            "+new\n"
            " line3\n"
        )
        patches_dir = repo / "debian" / "patches"
        patches_dir.mkdir(parents=True)
        patch_file = patches_dir / "fix.patch"
        patch_file.write_text(dep3_header + diff_body)

        result = attempt_mechanical_refresh("fix.patch", patch_file, repo)

        assert result.refreshed is True
        assert "From: Test Author" in result.patch_content
        assert "Ubuntu-Bug:" in result.patch_content
        assert "Forwarded: no" in result.patch_content

    def test_returns_false_when_all_strategies_fail(self, tmp_path: Path) -> None:
        """Test returns not-refreshed when no strategy works."""
        from packastack.debpkg.gbp import run_command

        repo = self._init_repo(tmp_path)

        # Create file that doesn't match the patch at all
        (repo / "file.txt").write_text("completely\ndifferent\ncontent\n")
        run_command(["git", "add", "."], cwd=repo)
        run_command(["git", "commit", "-m", "init"], cwd=repo)

        patches_dir = repo / "debian" / "patches"
        patches_dir.mkdir(parents=True)
        patch_content = (
            "diff --git a/file.txt b/file.txt\n"
            "--- a/file.txt\n"
            "+++ b/file.txt\n"
            "@@ -1,3 +1,3 @@\n"
            " alpha\n"
            "-beta\n"
            "+gamma\n"
            " delta\n"
        )
        patch_file = patches_dir / "fix.patch"
        patch_file.write_text(patch_content)

        result = attempt_mechanical_refresh("fix.patch", patch_file, repo)

        assert result.refreshed is False
        assert "failed" in result.error.lower()

    def test_reverts_working_tree_after_refresh(self, tmp_path: Path) -> None:
        """Test working tree is clean after a successful refresh."""
        from packastack.debpkg.gbp import run_command

        repo = self._init_repo(tmp_path)

        (repo / "file.txt").write_text("extra\nline1\nold\nline3\n")
        run_command(["git", "add", "."], cwd=repo)
        run_command(["git", "commit", "-m", "init"], cwd=repo)

        patches_dir = repo / "debian" / "patches"
        patches_dir.mkdir(parents=True)
        patch_content = (
            "diff --git a/file.txt b/file.txt\n"
            "--- a/file.txt\n"
            "+++ b/file.txt\n"
            "@@ -1,3 +1,3 @@\n"
            " line1\n"
            "-old\n"
            "+new\n"
            " line3\n"
        )
        patch_file = patches_dir / "fix.patch"
        patch_file.write_text(patch_content)

        original_content = (repo / "file.txt").read_text()
        attempt_mechanical_refresh("fix.patch", patch_file, repo)

        # Working tree should be unchanged
        assert (repo / "file.txt").read_text() == original_content
        _rc, status_out, _ = run_command(["git", "status", "--porcelain"], cwd=repo)
        # Only untracked debian/patches should remain
        for line in status_out.strip().splitlines():
            assert "debian/" in line or line.strip() == ""

    def test_handles_unreadable_patch_file(self, tmp_path: Path) -> None:
        """Test handles unreadable patch file gracefully."""
        repo = self._init_repo(tmp_path)
        missing_path = repo / "debian" / "patches" / "missing.patch"

        result = attempt_mechanical_refresh("missing.patch", missing_path, repo)

        assert result.refreshed is False
        assert "cannot read" in result.error.lower()

    def test_reverts_working_tree_after_failed_strategy(self, tmp_path: Path) -> None:
        """Test working tree reverts even when a strategy fails mid-apply."""
        from packastack.debpkg.gbp import run_command

        repo = self._init_repo(tmp_path)
        (repo / "file.txt").write_text("original content\n")
        run_command(["git", "add", "."], cwd=repo)
        run_command(["git", "commit", "-m", "init"], cwd=repo)

        original = (repo / "file.txt").read_text()

        patches_dir = repo / "debian" / "patches"
        patches_dir.mkdir(parents=True)
        # A patch that won't apply at all
        patch_content = (
            "diff --git a/nonexistent.py b/nonexistent.py\n"
            "--- a/nonexistent.py\n"
            "+++ b/nonexistent.py\n"
            "@@ -1 +1 @@\n"
            "-old function\n"
            "+new function\n"
        )
        patch_file = patches_dir / "bad.patch"
        patch_file.write_text(patch_content)

        attempt_mechanical_refresh("bad.patch", patch_file, repo)

        assert (repo / "file.txt").read_text() == original

    def test_refreshes_file_deletion_with_changed_content(self, tmp_path: Path) -> None:
        """File deletion patch is refreshed when upstream changed the file."""
        from packastack.debpkg.gbp import run_command

        repo = self._init_repo(tmp_path)

        # File exists with content that differs from what patch expects
        (repo / "test_foo.py").write_text("new_line1\nline2\n")
        run_command(["git", "add", "."], cwd=repo)
        run_command(["git", "commit", "-m", "init"], cwd=repo)

        patches_dir = repo / "debian" / "patches"
        patches_dir.mkdir(parents=True)
        patch_content = (
            "Subject: Skip test\n"
            "---\n"
            "diff --git a/test_foo.py b/test_foo.py\n"
            "deleted file mode 100644\n"
            "index abc1234..0000000\n"
            "--- a/test_foo.py\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-old_line1\n"
            "-line2\n"
        )
        patch_file = patches_dir / "skip.patch"
        patch_file.write_text(patch_content)

        result = attempt_mechanical_refresh("skip.patch", patch_file, repo)

        assert result.refreshed is True
        assert "file-deletion" in result.explanation
        assert "deleted file mode" in result.patch_content
        assert "-new_line1" in result.patch_content
        assert "Subject: Skip test" in result.patch_content


class TestRefreshFailingPatch:
    """Tests for refresh_failing_patch function."""

    @pytest.fixture(autouse=True)
    def _mock_tree_context(self) -> None:
        """Mock collect_working_tree_context for all tests in this class."""
        with patch(
            "packastack.ai.build_diagnosis.collect_working_tree_context",
            return_value="== File tree ==\nmocked tree listing",
        ):
            yield

    def _cfg_with_key(self) -> dict:
        return {"ai": {"api_key": "test-key", "model": "test", "max_tokens": 100, "timeout": 10}}

    @patch.dict(os.environ, {}, clear=True)
    def test_returns_not_refreshed_when_no_key(self, tmp_path: Path) -> None:
        """Test returns error when no API key configured."""
        result = refresh_failing_patch(
            patch_name="fix.patch",
            patch_content="diff",
            pq_output="error",
            pkg_repo=tmp_path,
            pkg_name="pkg",
            version="1.0",
            cfg={"ai": {"api_key": None}},
        )
        assert result.refreshed is False
        assert "no api key" in result.error.lower()

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_successful_refresh(self, mock_call: MagicMock, tmp_path: Path) -> None:
        """Test successful patch refresh with valid output."""
        # Create source file that the patch references
        (tmp_path / "setup.cfg").write_text("[metadata]\nname = pkg\n")

        patch_content = "--- a/setup.cfg\n+++ b/setup.cfg\n@@ -1 +1 @@\n-old\n+new\n"

        mock_call.return_value = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Context lines shifted\n"
                "ACTION: REFRESH\n"
                "EXPLANATION: Updated offsets\n"
                "PATCH_FILENAME: fix.patch\n"
                "--- BEGIN PATCH ---\n"
                "--- a/setup.cfg\n"
                "+++ b/setup.cfg\n"
                "@@ -1,2 +1,2 @@\n"
                " [metadata]\n"
                "-name = pkg\n"
                "+name = newpkg\n"
                "--- END PATCH ---\n"
            ),
        )

        with patch(
            "packastack.ai.build_diagnosis.validate_patch"
        ) as mock_validate:
            mock_validate.return_value = MagicMock(valid=True, error="")
            result = refresh_failing_patch(
                patch_name="fix.patch",
                patch_content=patch_content,
                pq_output="hunk #1 FAILED",
                pkg_repo=tmp_path,
                pkg_name="pkg",
                version="2.0",
                cfg=self._cfg_with_key(),
            )

        assert result.refreshed is True
        assert result.patch_name == "fix.patch"
        assert "--- a/setup.cfg" in result.patch_content
        # Verify AI was called with correct system prompt
        assert mock_call.call_args[0][0] is not None

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_ai_call_failure(self, mock_call: MagicMock, tmp_path: Path) -> None:
        """Test handles AI call failure gracefully."""
        mock_call.return_value = AIResponse(success=False, error="timeout")
        result = refresh_failing_patch(
            patch_name="fix.patch",
            patch_content="diff",
            pq_output="error",
            pkg_repo=tmp_path,
            pkg_name="pkg",
            version="1.0",
            cfg=self._cfg_with_key(),
        )
        assert result.refreshed is False
        assert "timeout" in result.error

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_ai_says_no_refresh(self, mock_call: MagicMock, tmp_path: Path) -> None:
        """Test handles AI response saying refresh not possible."""
        mock_call.return_value = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Code was rewritten\n"
                "ACTION: NO_REFRESH\n"
                "EXPLANATION: The entire function was removed\n"
            ),
        )
        result = refresh_failing_patch(
            patch_name="fix.patch",
            patch_content="diff",
            pq_output="error",
            pkg_repo=tmp_path,
            pkg_name="pkg",
            version="1.0",
            cfg=self._cfg_with_key(),
        )
        assert result.refreshed is False
        assert result.error == ""
        assert "removed" in result.explanation

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_validation_fails_then_correction_succeeds(
        self, mock_call: MagicMock, tmp_path: Path
    ) -> None:
        """Test correction round recovers from initial validation failure."""
        # First call: refresh with bad patch
        # Second call: correction with valid patch
        mock_call.side_effect = [
            AIResponse(
                success=True,
                content=(
                    "ACTION: REFRESH\n"
                    "EXPLANATION: Initial attempt\n"
                    "--- BEGIN PATCH ---\nbad patch\n--- END PATCH ---"
                ),
            ),
            AIResponse(
                success=True,
                content=(
                    "ACTION: REFRESH\n"
                    "EXPLANATION: Fixed patch\n"
                    "--- BEGIN PATCH ---\ngood patch\n--- END PATCH ---"
                ),
            ),
        ]

        validation_fail = MagicMock(valid=False, error="does not apply")
        validation_ok = MagicMock(valid=True, error="")

        with patch(
            "packastack.ai.build_diagnosis.validate_patch",
            side_effect=[validation_fail, validation_ok],
        ):
            result = refresh_failing_patch(
                patch_name="fix.patch",
                patch_content="diff",
                pq_output="error",
                pkg_repo=tmp_path,
                pkg_name="pkg",
                version="1.0",
                cfg=self._cfg_with_key(),
                max_correction_attempts=1,
            )

        assert result.refreshed is True
        assert result.patch_content == "good patch\n"

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_validation_fails_all_attempts(
        self, mock_call: MagicMock, tmp_path: Path
    ) -> None:
        """Test returns not refreshed when all validation attempts fail."""
        mock_call.side_effect = [
            AIResponse(
                success=True,
                content=(
                    "ACTION: REFRESH\n"
                    "--- BEGIN PATCH ---\nbad1\n--- END PATCH ---"
                ),
            ),
            AIResponse(
                success=True,
                content=(
                    "ACTION: REFRESH\n"
                    "--- BEGIN PATCH ---\nbad2\n--- END PATCH ---"
                ),
            ),
        ]

        validation_fail = MagicMock(valid=False, error="does not apply")

        with patch(
            "packastack.ai.build_diagnosis.validate_patch",
            return_value=validation_fail,
        ):
            result = refresh_failing_patch(
                patch_name="fix.patch",
                patch_content="diff",
                pq_output="error",
                pkg_repo=tmp_path,
                pkg_name="pkg",
                version="1.0",
                cfg=self._cfg_with_key(),
                max_correction_attempts=1,
            )

        assert result.refreshed is False
        assert "validation" in result.error.lower()

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_reads_affected_files(self, mock_call: MagicMock, tmp_path: Path) -> None:
        """Test reads current contents of files the patch modifies."""
        (tmp_path / "setup.cfg").write_text("[metadata]\nname = test\n")
        patch_content = "--- a/setup.cfg\n+++ b/setup.cfg\n@@ -1 +1 @@\n-old\n+new\n"

        mock_call.return_value = AIResponse(
            success=True,
            content="ACTION: NO_REFRESH\nEXPLANATION: Cannot refresh\n",
        )

        refresh_failing_patch(
            patch_name="fix.patch",
            patch_content=patch_content,
            pq_output="error",
            pkg_repo=tmp_path,
            pkg_name="pkg",
            version="1.0",
            cfg=self._cfg_with_key(),
        )

        # Verify the AI was called and the user message contains file content
        call_args = mock_call.call_args
        user_msg = call_args[0][1]
        assert "[metadata]" in user_msg
        assert "name = test" in user_msg

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_handles_missing_affected_file(
        self, mock_call: MagicMock, tmp_path: Path
    ) -> None:
        """Test gracefully handles when affected source file doesn't exist."""
        # Don't create the file that the patch references
        patch_content = "--- a/missing.py\n+++ b/missing.py\n@@ -1 +1 @@\n-x\n+y\n"

        mock_call.return_value = AIResponse(
            success=True,
            content="ACTION: NO_REFRESH\nEXPLANATION: Cannot refresh\n",
        )

        result = refresh_failing_patch(
            patch_name="fix.patch",
            patch_content=patch_content,
            pq_output="error",
            pkg_repo=tmp_path,
            pkg_name="pkg",
            version="1.0",
            cfg=self._cfg_with_key(),
        )

        # Should still call AI, just without the file contents
        mock_call.assert_called_once()
        assert result.refreshed is False

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_correction_call_failure_returns_not_refreshed(
        self, mock_call: MagicMock, tmp_path: Path
    ) -> None:
        """Test returns not refreshed when correction API call fails."""
        mock_call.side_effect = [
            AIResponse(
                success=True,
                content=(
                    "ACTION: REFRESH\n"
                    "--- BEGIN PATCH ---\nbad\n--- END PATCH ---"
                ),
            ),
            AIResponse(success=False, error="rate limited"),
        ]

        validation_fail = MagicMock(valid=False, error="does not apply")

        with patch(
            "packastack.ai.build_diagnosis.validate_patch",
            return_value=validation_fail,
        ):
            result = refresh_failing_patch(
                patch_name="fix.patch",
                patch_content="diff",
                pq_output="error",
                pkg_repo=tmp_path,
                pkg_name="pkg",
                version="1.0",
                cfg=self._cfg_with_key(),
                max_correction_attempts=1,
            )

        assert result.refreshed is False

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_correction_no_refresh_returns_not_refreshed(
        self, mock_call: MagicMock, tmp_path: Path
    ) -> None:
        """Test returns not refreshed when correction says NO_REFRESH."""
        mock_call.side_effect = [
            AIResponse(
                success=True,
                content=(
                    "ACTION: REFRESH\n"
                    "--- BEGIN PATCH ---\nbad\n--- END PATCH ---"
                ),
            ),
            AIResponse(
                success=True,
                content="ACTION: NO_REFRESH\nEXPLANATION: Cannot fix\n",
            ),
        ]

        validation_fail = MagicMock(valid=False, error="does not apply")

        with patch(
            "packastack.ai.build_diagnosis.validate_patch",
            return_value=validation_fail,
        ):
            result = refresh_failing_patch(
                patch_name="fix.patch",
                patch_content="diff",
                pq_output="error",
                pkg_repo=tmp_path,
                pkg_name="pkg",
                version="1.0",
                cfg=self._cfg_with_key(),
                max_correction_attempts=1,
            )

        assert result.refreshed is False

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_includes_pyproject_toml_when_setup_cfg_missing(
        self, mock_call: MagicMock, tmp_path: Path
    ) -> None:
        """Test includes pyproject.toml when patch targets missing setup.cfg."""
        # Only pyproject.toml exists (setup.cfg was removed in migration)
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = \"cinder\"\n"
        )
        patch_content = (
            "--- a/setup.cfg\n+++ b/setup.cfg\n"
            "@@ -1,2 +1,3 @@\n [metadata]\n name = cinder\n+version = 1.0\n"
        )

        mock_call.return_value = AIResponse(
            success=True,
            content="ACTION: NO_REFRESH\nEXPLANATION: Cannot refresh\n",
        )

        refresh_failing_patch(
            patch_name="fix.patch",
            patch_content=patch_content,
            pq_output="error: setup.cfg does not exist",
            pkg_repo=tmp_path,
            pkg_name="cinder",
            version="28.0.0",
            cfg=self._cfg_with_key(),
        )

        # AI should receive pyproject.toml contents
        user_msg = mock_call.call_args[0][1]
        assert "[project]" in user_msg
        assert 'name = "cinder"' in user_msg
        # And be told that setup.cfg is missing
        assert "setup.cfg" in user_msg
        assert "NO LONGER EXIST" in user_msg

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_includes_pyproject_toml_even_when_setup_cfg_exists(
        self, mock_call: MagicMock, tmp_path: Path
    ) -> None:
        """Test includes pyproject.toml alongside setup.cfg for migration awareness."""
        (tmp_path / "setup.cfg").write_text("[metadata]\nname = test\n")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = \"test\"\n")
        patch_content = (
            "--- a/setup.cfg\n+++ b/setup.cfg\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )

        mock_call.return_value = AIResponse(
            success=True,
            content="ACTION: NO_REFRESH\nEXPLANATION: Cannot refresh\n",
        )

        refresh_failing_patch(
            patch_name="fix.patch",
            patch_content=patch_content,
            pq_output="error",
            pkg_repo=tmp_path,
            pkg_name="pkg",
            version="1.0",
            cfg=self._cfg_with_key(),
        )

        # AI should receive BOTH setup.cfg and pyproject.toml
        user_msg = mock_call.call_args[0][1]
        assert "[metadata]" in user_msg
        assert "[project]" in user_msg

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_no_pyproject_when_patch_does_not_touch_setup(
        self, mock_call: MagicMock, tmp_path: Path
    ) -> None:
        """Test does not include pyproject.toml when patch doesn't touch setup files."""
        (tmp_path / "requirements.txt").write_text("oslo.config>=1.0\n")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = \"test\"\n")
        patch_content = (
            "--- a/requirements.txt\n+++ b/requirements.txt\n"
            "@@ -1 +1 @@\n-oslo.config>=1.0\n+oslo.config>=2.0\n"
        )

        mock_call.return_value = AIResponse(
            success=True,
            content="ACTION: NO_REFRESH\nEXPLANATION: Cannot refresh\n",
        )

        refresh_failing_patch(
            patch_name="fix.patch",
            patch_content=patch_content,
            pq_output="error",
            pkg_repo=tmp_path,
            pkg_name="pkg",
            version="1.0",
            cfg=self._cfg_with_key(),
        )

        # AI should NOT receive pyproject.toml — patch doesn't touch setup files
        user_msg = mock_call.call_args[0][1]
        assert "[project]" not in user_msg

    @patch("packastack.ai.patch_diagnosis.call_ai")
    def test_includes_working_tree_context(
        self, mock_call: MagicMock, tmp_path: Path
    ) -> None:
        """Test that the AI receives the full working tree context."""
        patch_content = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"

        mock_call.return_value = AIResponse(
            success=True,
            content="ACTION: NO_REFRESH\nEXPLANATION: Cannot refresh\n",
        )

        refresh_failing_patch(
            patch_name="fix.patch",
            patch_content=patch_content,
            pq_output="error",
            pkg_repo=tmp_path,
            pkg_name="pkg",
            version="1.0",
            cfg=self._cfg_with_key(),
        )

        # The autouse fixture returns "== File tree ==\nmocked tree listing"
        user_msg = mock_call.call_args[0][1]
        assert "File tree" in user_msg
        assert "mocked tree listing" in user_msg
