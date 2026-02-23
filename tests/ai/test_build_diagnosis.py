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

"""Tests for packastack.ai.build_diagnosis module."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

from packastack.ai.build_diagnosis import (
    BuildDiagnosisResult,
    PatchValidationResult,
    _parse_build_response,
    _read_file_safe,
    _request_patch_correction,
    apply_ai_patch,
    diagnose_build_failure,
    validate_patch,
)
from packastack.ai.client import AIResponse


@dataclass
class MockSbuildResult:
    """Minimal mock of SbuildResult for testing."""

    success: bool = False
    validation_message: str = ""
    primary_log_path: Path | None = None
    stderr_log_path: Path | None = None
    stdout_log_path: Path | None = None
    collected_artifacts: list = field(default_factory=list)
    collected_logs: list = field(default_factory=list)


class TestParseBuildResponse:
    """Tests for _parse_build_response function."""

    def test_parses_patch_response(self) -> None:
        """Test parsing response with a patch proposal."""
        response = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Python 3.14 removed deprecated ast module usage\n"
                "ACTION: PATCH\n"
                "EXPLANATION: The code uses ast.Str which was removed in Python 3.14\n"
                "PATCH_FILENAME: fix-py314-ast.patch\n"
                "--- BEGIN PATCH ---\n"
                "Description: Fix ast.Str removal in Python 3.14\n"
                "Author: AI <ai@packastack>\n"
                "Forwarded: not-needed\n"
                "---\n"
                "--- a/aodh/evaluator.py\n"
                "+++ b/aodh/evaluator.py\n"
                "@@ -1,3 +1,3 @@\n"
                "-x = ast.Str(value)\n"
                "+x = ast.Constant(value)\n"
                "--- END PATCH ---\n"
            ),
        )
        result = _parse_build_response(response)
        assert result.diagnosed is True
        assert result.needs_patch is True
        assert result.patch_filename == "fix-py314-ast.patch"
        assert "ast.Constant" in result.patch_content
        assert "Description:" in result.patch_content

    def test_parses_no_patch_response(self) -> None:
        """Test parsing response without a patch."""
        response = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Missing build dependency\n"
                "ACTION: NO_PATCH\n"
                "EXPLANATION: Package requires libfoo-dev which is not in Build-Depends"
            ),
        )
        result = _parse_build_response(response)
        assert result.diagnosed is True
        assert result.needs_patch is False
        assert "libfoo-dev" in result.explanation

    def test_incomplete_patch_falls_back(self) -> None:
        """Test that incomplete patch (missing content) falls back to no-patch."""
        response = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Needs fix\n"
                "ACTION: PATCH\n"
                "PATCH_FILENAME: fix.patch\n"
                # Missing BEGIN/END markers
            ),
        )
        result = _parse_build_response(response)
        assert result.needs_patch is False

    def test_missing_filename_falls_back(self) -> None:
        """Test that patch without filename falls back to no-patch."""
        response = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Needs fix\n"
                "ACTION: PATCH\n"
                "--- BEGIN PATCH ---\n"
                "diff content\n"
                "--- END PATCH ---\n"
            ),
        )
        result = _parse_build_response(response)
        assert result.needs_patch is False

    def test_unstructured_response(self) -> None:
        """Test fallback for unstructured response."""
        response = AIResponse(
            success=True,
            content="The build failed because of a missing header file.",
        )
        result = _parse_build_response(response)
        assert result.diagnosed is True
        assert "missing header" in result.explanation


class TestReadFileSafe:
    """Tests for _read_file_safe function."""

    def test_reads_file(self, tmp_path: Path) -> None:
        """Test normal file read."""
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\n")
        result = _read_file_safe(f)
        assert "line1" in result
        assert "line3" in result

    def test_truncates_long_file(self, tmp_path: Path) -> None:
        """Test truncation of long files."""
        f = tmp_path / "long.txt"
        f.write_text("\n".join(f"line {i}" for i in range(500)))
        result = _read_file_safe(f, max_lines=10)
        assert "truncated" in result
        assert "line 0" in result

    def test_reads_full_file_when_max_lines_zero(self, tmp_path: Path) -> None:
        """Test that max_lines=0 returns the entire file."""
        f = tmp_path / "full.txt"
        f.write_text("\n".join(f"line {i}" for i in range(500)))
        result = _read_file_safe(f, max_lines=0)
        assert "line 0" in result
        assert "line 499" in result
        assert "truncated" not in result

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Test returns empty string for nonexistent file."""
        result = _read_file_safe(tmp_path / "nope.txt")
        assert result == ""


class TestDiagnoseBuildFailure:
    """Tests for diagnose_build_failure function."""

    def _cfg_with_key(self) -> dict:
        return {"ai": {"api_key": "test-key", "model": "test", "max_tokens": 100, "timeout": 10}}

    @patch.dict(os.environ, {}, clear=True)
    def test_returns_not_diagnosed_when_no_key(self, tmp_path: Path) -> None:
        """Test returns diagnosed=False when no API key."""
        sbuild = MockSbuildResult()
        result = diagnose_build_failure(
            sbuild_result=sbuild,
            pkg_repo=tmp_path,
            pkg_name="pkg",
            version="1.0",
            ubuntu_series="noble",
            arch="amd64",
            cfg={"ai": {"api_key": None}},
        )
        assert result.diagnosed is False

    def test_returns_not_diagnosed_when_no_logs(self, tmp_path: Path) -> None:
        """Test returns diagnosed=False when no log files available."""
        sbuild = MockSbuildResult()
        result = diagnose_build_failure(
            sbuild_result=sbuild,
            pkg_repo=tmp_path,
            pkg_name="pkg",
            version="1.0",
            ubuntu_series="noble",
            arch="amd64",
            cfg=self._cfg_with_key(),
        )
        assert result.diagnosed is False
        assert "No build log" in result.error

    @patch("packastack.ai.build_diagnosis.call_ai")
    def test_diagnoses_with_patch(self, mock_call, tmp_path: Path) -> None:
        """Test successful diagnosis proposing a patch."""
        # Create log and debian files
        log = tmp_path / "build.log"
        log.write_text("error: ast.Str removed in Python 3.14\n")
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "control").write_text("Build-Depends: python3-all\n")
        (debian / "rules").write_text("#!/usr/bin/make -f\n")

        mock_call.return_value = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Python 3.14 compat\n"
                "ACTION: PATCH\n"
                "EXPLANATION: ast.Str removed\n"
                "PATCH_FILENAME: fix-py314.patch\n"
                "--- BEGIN PATCH ---\n"
                "Description: Fix Python 3.14\nAuthor: AI\nForwarded: no\n"
                "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"
                "--- END PATCH ---\n"
            ),
        )

        sbuild = MockSbuildResult(primary_log_path=log, validation_message="Build failed")
        result = diagnose_build_failure(
            sbuild_result=sbuild,
            pkg_repo=tmp_path,
            pkg_name="aodh",
            version="19.0.0",
            ubuntu_series="plucky",
            arch="amd64",
            cfg=self._cfg_with_key(),
        )
        assert result.diagnosed is True
        assert result.needs_patch is True
        assert result.patch_filename == "fix-py314.patch"

    @patch("packastack.ai.build_diagnosis.call_ai")
    def test_api_failure(self, mock_call, tmp_path: Path) -> None:
        """Test graceful degradation when API fails."""
        log = tmp_path / "build.log"
        log.write_text("error: something\n")

        mock_call.return_value = AIResponse(success=False, error="timeout")
        sbuild = MockSbuildResult(primary_log_path=log)
        result = diagnose_build_failure(
            sbuild_result=sbuild,
            pkg_repo=tmp_path,
            pkg_name="pkg",
            version="1.0",
            ubuntu_series="noble",
            arch="amd64",
            cfg=self._cfg_with_key(),
        )
        assert result.diagnosed is False
        assert "timeout" in result.error


class TestValidatePatch:
    """Tests for validate_patch function."""

    def test_valid_patch(self, tmp_path: Path) -> None:
        """Test that a valid patch passes validation."""
        (tmp_path / "debian" / "patches").mkdir(parents=True)
        with patch("packastack.debpkg.gbp.run_command", return_value=(0, "", "")):
            result = validate_patch(tmp_path, "--- a/x\n+++ b/x\n", "fix.patch")
        assert result.valid is True
        assert result.error == ""

    def test_invalid_patch(self, tmp_path: Path) -> None:
        """Test that an invalid patch fails validation."""
        (tmp_path / "debian" / "patches").mkdir(parents=True)
        with patch(
            "packastack.debpkg.gbp.run_command",
            return_value=(1, "", "error: patch does not apply"),
        ):
            result = validate_patch(tmp_path, "bad diff", "fix.patch")
        assert result.valid is False
        assert "does not apply" in result.error

    def test_cleanup_after_validation(self, tmp_path: Path) -> None:
        """Test that temporary patch file is cleaned up."""
        (tmp_path / "debian" / "patches").mkdir(parents=True)
        with patch("packastack.debpkg.gbp.run_command", return_value=(0, "", "")):
            validate_patch(tmp_path, "content", "temp.patch")
        assert not (tmp_path / "debian" / "patches" / "temp.patch").exists()

    def test_cleanup_on_failure(self, tmp_path: Path) -> None:
        """Test that temporary patch file is cleaned up even on failure."""
        (tmp_path / "debian" / "patches").mkdir(parents=True)
        with patch(
            "packastack.debpkg.gbp.run_command",
            return_value=(1, "", "fail"),
        ):
            validate_patch(tmp_path, "content", "temp.patch")
        assert not (tmp_path / "debian" / "patches" / "temp.patch").exists()

    def test_creates_patches_dir(self, tmp_path: Path) -> None:
        """Test that debian/patches/ is created if missing."""
        with patch("packastack.debpkg.gbp.run_command", return_value=(0, "", "")):
            result = validate_patch(tmp_path, "content", "fix.patch")
        assert result.valid is True
        assert (tmp_path / "debian" / "patches").exists()

    def test_oserror_returns_invalid(self, tmp_path: Path) -> None:
        """Test that OSError during write returns invalid."""
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            result = validate_patch(tmp_path, "content", "fix.patch")
        assert result.valid is False
        assert "File I/O error" in result.error

    def test_fallback_error_when_stderr_empty(self, tmp_path: Path) -> None:
        """Test fallback error message when stderr is empty."""
        (tmp_path / "debian" / "patches").mkdir(parents=True)
        with patch(
            "packastack.debpkg.gbp.run_command",
            return_value=(128, "", ""),
        ):
            result = validate_patch(tmp_path, "content", "fix.patch")
        assert result.valid is False
        assert "exited with code 128" in result.error


class TestRequestPatchCorrection:
    """Tests for _request_patch_correction function."""

    def _cfg_with_key(self) -> dict:
        return {"ai": {"api_key": "test-key", "model": "test", "max_tokens": 100, "timeout": 10}}

    @patch("packastack.ai.build_diagnosis.call_ai")
    def test_returns_corrected_patch(self, mock_call: Any) -> None:
        """Test successful patch correction."""
        mock_call.return_value = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Fixed paths\n"
                "ACTION: PATCH\n"
                "EXPLANATION: Corrected file paths\n"
                "PATCH_FILENAME: fix-v2.patch\n"
                "--- BEGIN PATCH ---\n"
                "corrected diff\n"
                "--- END PATCH ---\n"
            ),
        )
        original = BuildDiagnosisResult(
            diagnosed=True,
            needs_patch=True,
            patch_filename="fix.patch",
            patch_content="bad diff",
            explanation="original diag",
        )
        result = _request_patch_correction(original, "apply error", self._cfg_with_key())
        assert result is not None
        assert result.needs_patch is True
        assert result.patch_filename == "fix-v2.patch"

    @patch.dict(os.environ, {}, clear=True)
    def test_returns_none_when_ai_unavailable(self) -> None:
        """Test returns None when no API key."""
        original = BuildDiagnosisResult(
            diagnosed=True, patch_filename="f.patch", patch_content="diff"
        )
        result = _request_patch_correction(
            original, "error", {"ai": {"api_key": None}}
        )
        assert result is None

    @patch("packastack.ai.build_diagnosis.call_ai")
    def test_returns_none_when_ai_fails(self, mock_call: Any) -> None:
        """Test returns None when API call fails."""
        mock_call.return_value = AIResponse(success=False, error="timeout")
        original = BuildDiagnosisResult(
            diagnosed=True, patch_filename="f.patch", patch_content="diff"
        )
        result = _request_patch_correction(original, "error", self._cfg_with_key())
        assert result is None


class TestApplyAiPatch:
    """Tests for apply_ai_patch function."""

    def test_writes_patch_and_updates_series(self, tmp_path: Path) -> None:
        """Test that validated patch file is written and series is updated."""
        patches_dir = tmp_path / "debian" / "patches"
        patches_dir.mkdir(parents=True)
        series = patches_dir / "series"
        series.write_text("existing-patch.patch\n")

        diagnosis = BuildDiagnosisResult(
            diagnosed=True,
            needs_patch=True,
            patch_filename="fix-py314.patch",
            patch_content="Description: Fix\n--- a/x\n+++ b/x\n",
        )

        with (
            patch("packastack.ai.build_diagnosis.validate_patch") as mock_validate,
            patch("packastack.debpkg.gbp.run_command", return_value=(0, "", "")),
        ):
            mock_validate.return_value = PatchValidationResult(valid=True)
            result = apply_ai_patch(tmp_path, diagnosis)

        assert result is True
        assert (patches_dir / "fix-py314.patch").exists()
        series_content = series.read_text()
        assert "fix-py314.patch" in series_content
        assert "existing-patch.patch" in series_content

    def test_does_not_duplicate_in_series(self, tmp_path: Path) -> None:
        """Test that patch is not added to series if already present."""
        patches_dir = tmp_path / "debian" / "patches"
        patches_dir.mkdir(parents=True)
        series = patches_dir / "series"
        series.write_text("fix-py314.patch\n")

        diagnosis = BuildDiagnosisResult(
            diagnosed=True,
            needs_patch=True,
            patch_filename="fix-py314.patch",
            patch_content="Description: Fix\n",
        )

        with (
            patch("packastack.ai.build_diagnosis.validate_patch") as mock_validate,
            patch("packastack.debpkg.gbp.run_command", return_value=(0, "", "")),
        ):
            mock_validate.return_value = PatchValidationResult(valid=True)
            result = apply_ai_patch(tmp_path, diagnosis)

        assert result is True
        series_content = series.read_text()
        assert series_content.count("fix-py314.patch") == 1

    def test_creates_patches_dir(self, tmp_path: Path) -> None:
        """Test that debian/patches/ is created if missing."""
        diagnosis = BuildDiagnosisResult(
            diagnosed=True,
            needs_patch=True,
            patch_filename="new.patch",
            patch_content="diff content",
        )

        with (
            patch("packastack.ai.build_diagnosis.validate_patch") as mock_validate,
            patch("packastack.debpkg.gbp.run_command", return_value=(0, "", "")),
        ):
            mock_validate.return_value = PatchValidationResult(valid=True)
            result = apply_ai_patch(tmp_path, diagnosis)

        assert result is True
        assert (tmp_path / "debian" / "patches" / "new.patch").exists()

    def test_returns_false_without_filename(self, tmp_path: Path) -> None:
        """Test returns False when no filename in diagnosis."""
        diagnosis = BuildDiagnosisResult(diagnosed=True, needs_patch=True)
        result = apply_ai_patch(tmp_path, diagnosis)
        assert result is False

    def test_returns_false_without_content(self, tmp_path: Path) -> None:
        """Test returns False when no patch content in diagnosis."""
        diagnosis = BuildDiagnosisResult(
            diagnosed=True, needs_patch=True, patch_filename="fix.patch"
        )
        result = apply_ai_patch(tmp_path, diagnosis)
        assert result is False

    def test_rejects_invalid_patch_without_cfg(self, tmp_path: Path) -> None:
        """Test returns False for invalid patch when no cfg for correction."""
        diagnosis = BuildDiagnosisResult(
            diagnosed=True,
            needs_patch=True,
            patch_filename="fix.patch",
            patch_content="bad diff",
        )
        with patch("packastack.ai.build_diagnosis.validate_patch") as mock_validate:
            mock_validate.return_value = PatchValidationResult(
                valid=False, error="does not apply"
            )
            result = apply_ai_patch(tmp_path, diagnosis)
        assert result is False

    def test_correction_attempt_succeeds(self, tmp_path: Path) -> None:
        """Test that correction attempt fixes an invalid patch."""
        diagnosis = BuildDiagnosisResult(
            diagnosed=True,
            needs_patch=True,
            patch_filename="fix.patch",
            patch_content="bad diff",
            explanation="original fix",
        )
        cfg = {"ai": {"api_key": "key", "model": "test", "max_tokens": 100, "timeout": 10}}

        validation_calls = [
            PatchValidationResult(valid=False, error="does not apply"),
            PatchValidationResult(valid=True),
        ]

        with (
            patch(
                "packastack.ai.build_diagnosis.validate_patch",
                side_effect=validation_calls,
            ),
            patch(
                "packastack.ai.build_diagnosis._request_patch_correction",
            ) as mock_correct,
            patch("packastack.debpkg.gbp.run_command", return_value=(0, "", "")),
        ):
            mock_correct.return_value = BuildDiagnosisResult(
                diagnosed=True,
                needs_patch=True,
                patch_filename="fix-v2.patch",
                patch_content="good diff",
            )
            result = apply_ai_patch(tmp_path, diagnosis, cfg=cfg)

        assert result is True
        assert (tmp_path / "debian" / "patches" / "fix-v2.patch").exists()

    def test_correction_attempt_fails(self, tmp_path: Path) -> None:
        """Test returns False when correction also fails validation."""
        diagnosis = BuildDiagnosisResult(
            diagnosed=True,
            needs_patch=True,
            patch_filename="fix.patch",
            patch_content="bad diff",
            explanation="original fix",
        )
        cfg = {"ai": {"api_key": "key", "model": "test", "max_tokens": 100, "timeout": 10}}

        with (
            patch(
                "packastack.ai.build_diagnosis.validate_patch",
                return_value=PatchValidationResult(valid=False, error="still bad"),
            ),
            patch(
                "packastack.ai.build_diagnosis._request_patch_correction",
            ) as mock_correct,
        ):
            mock_correct.return_value = BuildDiagnosisResult(
                diagnosed=True,
                needs_patch=True,
                patch_filename="fix-v2.patch",
                patch_content="still bad diff",
            )
            result = apply_ai_patch(tmp_path, diagnosis, cfg=cfg)

        assert result is False


class TestBuildDiagnosisResult:
    """Tests for BuildDiagnosisResult dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        result = BuildDiagnosisResult(diagnosed=False)
        assert result.diagnosed is False
        assert result.needs_patch is False
        assert result.patch_filename == ""
        assert result.patch_content == ""
        assert result.explanation == ""
        assert result.error == ""
