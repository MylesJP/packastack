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

from packastack.ai.client import AIResponse
from packastack.ai.patch_diagnosis import (
    AutoDropResult,
    PatchDiagnosisResult,
    _parse_patch_response,
    auto_drop_upstreamed_patches,
    diagnose_patch_failure,
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
