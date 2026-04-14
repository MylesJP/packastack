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
    _extract_debian_edits,
    _is_binary_path,
    _parse_build_response,
    _read_file_safe,
    _request_patch_correction,
    _skill_result_to_diagnosis,
    apply_ai_fix,
    apply_ai_patch,
    apply_debian_edits,
    collect_working_tree_context,
    diagnose_build_failure,
    validate_patch,
)
from packastack.ai.client import AIResponse
from packastack.ai.contracts import (
    DiagnosisPayload,
    DispatchPayload,
    GuidancePayload,
    PatchPayload,
    SkillResult,
)


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

    @patch("packastack.ai.build_diagnosis.run_skill")
    @patch("packastack.ai.build_diagnosis.match_triggers")
    def test_diagnoses_with_patch(
        self, mock_triggers: Any, mock_run: Any, tmp_path: Path
    ) -> None:
        """Trigger-matched specialist returns a patch."""
        log = tmp_path / "build.log"
        log.write_text("error: ast.Str removed in Python 3.14\n")

        mock_triggers.return_value = "build-patch"
        mock_run.return_value = SkillResult(
            success=True,
            contract="patch",
            parsed=PatchPayload(
                action="QUILT_PATCH",
                patch_filename="fix-py314.patch",
                patch_content="diff --git a/x b/x\n",
                diagnosis="Python 3.14 compat",
                explanation="ast.Str removed",
            ),
        )

        sbuild = MockSbuildResult(
            primary_log_path=log, validation_message="Build failed"
        )
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
        # Triggered path shouldn't call the router first.
        assert mock_run.call_count == 1
        assert mock_run.call_args.args[0] == "build-patch"

    @patch("packastack.ai.build_diagnosis.run_skill")
    @patch("packastack.ai.build_diagnosis.match_triggers")
    def test_routes_via_router_when_no_trigger(
        self, mock_triggers: Any, mock_run: Any, tmp_path: Path
    ) -> None:
        """When no trigger matches, the router picks a specialist."""
        log = tmp_path / "build.log"
        log.write_text("error: something weird\n")

        mock_triggers.return_value = None

        def fake_run(name: str, *_args: Any, **_kwargs: Any) -> SkillResult:
            if name == "build-doctor":
                return SkillResult(
                    success=True,
                    contract="dispatch",
                    parsed=DispatchPayload(skill="build-patch", reason="fallback"),
                )
            return SkillResult(
                success=True,
                contract="patch",
                parsed=PatchPayload(
                    action="NO_PATCH",
                    patch_filename="",
                    patch_content="",
                    diagnosis="",
                    explanation="needs human",
                ),
            )

        mock_run.side_effect = fake_run
        sbuild = MockSbuildResult(
            primary_log_path=log, validation_message="Build failed"
        )
        result = diagnose_build_failure(
            sbuild_result=sbuild,
            pkg_repo=tmp_path,
            pkg_name="pkg",
            version="1.0",
            ubuntu_series="noble",
            arch="amd64",
            cfg=self._cfg_with_key(),
        )
        assert result.diagnosed is True
        assert result.needs_patch is False
        assert result.explanation == "needs human"
        called = [c.args[0] for c in mock_run.call_args_list]
        assert called == ["build-doctor", "build-patch"]

    @patch("packastack.ai.build_diagnosis.run_skill")
    @patch("packastack.ai.build_diagnosis.match_triggers")
    def test_router_failure_falls_back(
        self, mock_triggers: Any, mock_run: Any, tmp_path: Path
    ) -> None:
        """If the router errors, we still run the fallback specialist."""
        log = tmp_path / "build.log"
        log.write_text("error: boom\n")
        mock_triggers.return_value = None

        def fake_run(name: str, *_args: Any, **_kwargs: Any) -> SkillResult:
            if name == "build-doctor":
                return SkillResult(success=False, error="router broke")
            return SkillResult(
                success=True,
                contract="patch",
                parsed=PatchPayload(
                    action="NO_PATCH",
                    patch_filename="",
                    patch_content="",
                    diagnosis="",
                    explanation="fallback ran",
                ),
            )

        mock_run.side_effect = fake_run
        sbuild = MockSbuildResult(
            primary_log_path=log, validation_message="Build failed"
        )
        result = diagnose_build_failure(
            sbuild_result=sbuild,
            pkg_repo=tmp_path,
            pkg_name="pkg",
            version="1.0",
            ubuntu_series="noble",
            arch="amd64",
            cfg=self._cfg_with_key(),
        )
        assert result.diagnosed is True
        assert result.explanation == "fallback ran"
        called = [c.args[0] for c in mock_run.call_args_list]
        assert called == ["build-doctor", "build-patch"]

    @patch("packastack.ai.build_diagnosis.run_skill")
    @patch("packastack.ai.build_diagnosis.match_triggers")
    def test_router_picks_unknown_skill_falls_back(
        self, mock_triggers: Any, mock_run: Any, tmp_path: Path
    ) -> None:
        """Router picking a skill that isn't installed falls back to build-patch."""
        log = tmp_path / "build.log"
        log.write_text("error: boom\n")
        mock_triggers.return_value = None

        def fake_run(name: str, *_args: Any, **_kwargs: Any) -> SkillResult:
            if name == "build-doctor":
                return SkillResult(
                    success=True,
                    contract="dispatch",
                    parsed=DispatchPayload(skill="not-installed", reason=""),
                )
            return SkillResult(
                success=True,
                contract="patch",
                parsed=PatchPayload(
                    action="NO_PATCH",
                    patch_filename="",
                    patch_content="",
                    diagnosis="",
                    explanation="fallback",
                ),
            )

        mock_run.side_effect = fake_run
        sbuild = MockSbuildResult(
            primary_log_path=log, validation_message="Build failed"
        )
        result = diagnose_build_failure(
            sbuild_result=sbuild,
            pkg_repo=tmp_path,
            pkg_name="pkg",
            version="1.0",
            ubuntu_series="noble",
            arch="amd64",
            cfg=self._cfg_with_key(),
        )
        called = [c.args[0] for c in mock_run.call_args_list]
        assert called == ["build-doctor", "build-patch"]
        assert result.explanation == "fallback"

    @patch("packastack.ai.build_diagnosis.run_skill")
    @patch("packastack.ai.build_diagnosis.match_triggers")
    def test_specialist_failure_surfaces(
        self, mock_triggers: Any, mock_run: Any, tmp_path: Path
    ) -> None:
        """Specialist failures propagate as diagnosed=False."""
        log = tmp_path / "build.log"
        log.write_text("error: boom\n")
        mock_triggers.return_value = "build-patch"
        mock_run.return_value = SkillResult(success=False, error="AI call failed: timeout")

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


class TestSkillResultToDiagnosis:
    def test_patch_payload_with_patch(self) -> None:
        r = _skill_result_to_diagnosis(
            SkillResult(
                success=True,
                contract="patch",
                parsed=PatchPayload(
                    action="PATCH",
                    patch_filename="f.patch",
                    patch_content="body",
                    diagnosis="d",
                    explanation="e",
                ),
            )
        )
        assert r.diagnosed is True
        assert r.needs_patch is True
        assert r.patch_filename == "f.patch"
        assert r.explanation == "e"

    def test_patch_payload_without_patch(self) -> None:
        r = _skill_result_to_diagnosis(
            SkillResult(
                success=True,
                contract="patch",
                parsed=PatchPayload(
                    action="NO_PATCH",
                    patch_filename="",
                    patch_content="",
                    diagnosis="",
                    explanation="manual",
                ),
            )
        )
        assert r.diagnosed is True
        assert r.needs_patch is False
        assert r.explanation == "manual"

    def test_guidance_payload(self) -> None:
        r = _skill_result_to_diagnosis(
            SkillResult(
                success=True,
                contract="guidance",
                parsed=GuidancePayload(diagnosis="", explanation="advice"),
            )
        )
        assert r.diagnosed is True
        assert r.needs_patch is False
        assert r.explanation == "advice"

    def test_other_payload_uses_explanation_or_raw(self) -> None:
        r = _skill_result_to_diagnosis(
            SkillResult(
                success=True,
                contract="diagnosis",
                parsed=DiagnosisPayload(
                    can_drop=False, diagnosis="", explanation="x"
                ),
                raw="raw",
            )
        )
        assert r.explanation == "x"

    def test_failure(self) -> None:
        r = _skill_result_to_diagnosis(
            SkillResult(success=False, error="nope")
        )
        assert r.diagnosed is False
        assert r.error == "nope"

    def test_other_payload_falls_back_to_raw(self) -> None:
        class Shape:
            pass

        r = _skill_result_to_diagnosis(
            SkillResult(
                success=True,
                contract="diagnosis",
                parsed=Shape(),
                raw="raw fallback",
            )
        )
        assert r.explanation == "raw fallback"


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
        assert result.needs_debian_edit is False
        assert result.patch_filename == ""
        assert result.patch_content == ""
        assert result.debian_edits == {}
        assert result.explanation == ""
        assert result.error == ""


class TestParseDebianEditResponse:
    """Tests for _parse_build_response rejecting DEBIAN_EDIT action."""

    def test_debian_edit_response_is_ignored(self) -> None:
        """Test that DEBIAN_EDIT action is silently rejected."""
        response = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Need to switch build system\n"
                "ACTION: DEBIAN_EDIT\n"
                "EXPLANATION: Switch from python_distutils to pybuild\n"
                "--- BEGIN DEBIAN EDIT: debian/rules ---\n"
                "#!/usr/bin/make -f\n"
                "\n"
                "export PYBUILD_NAME=neutron-fwaas-dashboard\n"
                "\n"
                "%:\n"
                "\tdh $@ --buildsystem=pybuild\n"
                "--- END DEBIAN EDIT ---\n"
            ),
        )
        result = _parse_build_response(response)
        assert result.diagnosed is True
        assert result.needs_debian_edit is False
        assert result.needs_patch is False
        assert result.debian_edits == {}
        # The explanation should still be preserved
        assert "pybuild" in result.explanation

    def test_debian_edit_with_multiple_blocks_ignored(self) -> None:
        """Test that multiple debian edit blocks are still rejected."""
        response = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Fix build system and add dependency\n"
                "ACTION: DEBIAN_EDIT\n"
                "EXPLANATION: Multiple changes needed\n"
                "--- BEGIN DEBIAN EDIT: debian/rules ---\n"
                "#!/usr/bin/make -f\n"
                "%:\n"
                "\tdh $@ --buildsystem=pybuild\n"
                "--- END DEBIAN EDIT ---\n"
                "--- BEGIN DEBIAN EDIT: debian/control ---\n"
                "Source: nova\n"
                "Build-Depends: python3-all, python3-setuptools\n"
                "--- END DEBIAN EDIT ---\n"
            ),
        )
        result = _parse_build_response(response)
        assert result.needs_debian_edit is False
        assert result.needs_patch is False
        assert result.debian_edits == {}

    def test_incomplete_debian_edit_falls_back(self) -> None:
        """Test that debian edit without content falls back."""
        response = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Need fix\n"
                "ACTION: DEBIAN_EDIT\n"
                # Missing BEGIN/END markers
            ),
        )
        result = _parse_build_response(response)
        assert result.needs_debian_edit is False

    def test_parses_quilt_patch_action(self) -> None:
        """Test parsing QUILT_PATCH action (new name for PATCH)."""
        response = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Fix source code\n"
                "ACTION: QUILT_PATCH\n"
                "EXPLANATION: Source fix needed\n"
                "PATCH_FILENAME: fix-source.patch\n"
                "--- BEGIN PATCH ---\n"
                "--- a/module.py\n+++ b/module.py\n@@ -1 +1 @@\n-old\n+new\n"
                "--- END PATCH ---\n"
            ),
        )
        result = _parse_build_response(response)
        assert result.needs_patch is True
        assert result.needs_debian_edit is False
        assert result.patch_filename == "fix-source.patch"


class TestExtractDebianEdits:
    """Tests for _extract_debian_edits function."""

    def test_extracts_single_edit(self) -> None:
        """Test extracting a single debian edit block."""
        content = (
            "some preamble\n"
            "--- BEGIN DEBIAN EDIT: debian/rules ---\n"
            "#!/usr/bin/make -f\n"
            "%:\n"
            "\tdh $@\n"
            "--- END DEBIAN EDIT ---\n"
            "some postamble\n"
        )
        edits = _extract_debian_edits(content)
        assert len(edits) == 1
        assert "debian/rules" in edits
        assert "dh $@" in edits["debian/rules"]

    def test_extracts_multiple_edits(self) -> None:
        """Test extracting multiple debian edit blocks."""
        content = (
            "--- BEGIN DEBIAN EDIT: debian/rules ---\n"
            "rules content\n"
            "--- END DEBIAN EDIT ---\n"
            "--- BEGIN DEBIAN EDIT: debian/control ---\n"
            "control content\n"
            "--- END DEBIAN EDIT ---\n"
        )
        edits = _extract_debian_edits(content)
        assert len(edits) == 2
        assert "rules content" in edits["debian/rules"]
        assert "control content" in edits["debian/control"]

    def test_skips_non_debian_paths(self) -> None:
        """Test that paths not under debian/ are skipped."""
        content = (
            "--- BEGIN DEBIAN EDIT: setup.py ---\n"
            "evil content\n"
            "--- END DEBIAN EDIT ---\n"
        )
        edits = _extract_debian_edits(content)
        assert len(edits) == 0

    def test_empty_content_returns_empty(self) -> None:
        """Test that content without markers returns empty dict."""
        edits = _extract_debian_edits("no markers here")
        assert edits == {}

    def test_unterminated_block_returns_empty(self) -> None:
        """Test that unterminated block returns empty dict."""
        content = "--- BEGIN DEBIAN EDIT: debian/rules ---\ncontent without end\n"
        edits = _extract_debian_edits(content)
        assert edits == {}


class TestIsBinaryPath:
    """Tests for _is_binary_path function."""

    def test_binary_extensions(self) -> None:
        """Test common binary extensions are detected."""
        assert _is_binary_path(Path("file.gz")) is True
        assert _is_binary_path(Path("file.xz")) is True
        assert _is_binary_path(Path("file.png")) is True
        assert _is_binary_path(Path("key.gpg")) is True

    def test_text_extensions(self) -> None:
        """Test text extensions are not binary."""
        assert _is_binary_path(Path("file.py")) is False
        assert _is_binary_path(Path("file.txt")) is False
        assert _is_binary_path(Path("Makefile")) is False

    def test_case_insensitive(self) -> None:
        """Test case-insensitive extension check."""
        assert _is_binary_path(Path("file.GZ")) is True
        assert _is_binary_path(Path("file.PNG")) is True


class TestCollectWorkingTreeContext:
    """Tests for collect_working_tree_context function."""

    def test_includes_debian_files(self, tmp_path: Path) -> None:
        """Test that debian/ files are included in context."""
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "rules").write_text("#!/usr/bin/make -f\n%:\n\tdh $@\n")
        (debian / "control").write_text("Source: pkg\nBuild-Depends: debhelper\n")

        with patch("packastack.ai.build_diagnosis._git_ls_tree", return_value=""):
            result = collect_working_tree_context(tmp_path)

        assert "debian/rules" in result
        assert "dh $@" in result
        assert "debian/control" in result
        assert "debhelper" in result

    def test_includes_upstream_config(self, tmp_path: Path) -> None:
        """Test that upstream config files are included when present."""
        (tmp_path / "setup.py").write_text("from setuptools import setup\n")
        (tmp_path / "pyproject.toml").write_text("[build-system]\n")

        with patch("packastack.ai.build_diagnosis._git_ls_tree", return_value=""):
            result = collect_working_tree_context(tmp_path)

        assert "setup.py" in result
        assert "setuptools" in result
        assert "pyproject.toml" in result
        assert "build-system" in result

    def test_skips_missing_upstream_config(self, tmp_path: Path) -> None:
        """Test that missing upstream config files are silently skipped."""
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "rules").write_text("rules")

        with patch("packastack.ai.build_diagnosis._git_ls_tree", return_value=""):
            result = collect_working_tree_context(tmp_path)

        assert "setup.py" not in result
        assert "pyproject.toml" not in result

    def test_skips_binary_files(self, tmp_path: Path) -> None:
        """Test that binary files are marked as omitted."""
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "upstream").mkdir()
        (debian / "upstream" / "signing-key.asc").write_bytes(b"\x00\x01\x02")

        with patch("packastack.ai.build_diagnosis._git_ls_tree", return_value=""):
            result = collect_working_tree_context(tmp_path)

        assert "binary file, omitted" in result

    def test_includes_git_tree_listing(self, tmp_path: Path) -> None:
        """Test that git ls-tree output is included."""
        tree = "debian/rules\ndebian/control\nsetup.py"
        with patch("packastack.ai.build_diagnosis._git_ls_tree", return_value=tree):
            result = collect_working_tree_context(tmp_path)

        assert "== File tree ==" in result
        assert "debian/rules" in result

    def test_includes_patches_subdir(self, tmp_path: Path) -> None:
        """Test that debian/patches/ files are included."""
        patches = tmp_path / "debian" / "patches"
        patches.mkdir(parents=True)
        (patches / "series").write_text("fix.patch\n")
        (patches / "fix.patch").write_text("--- a/x.py\n+++ b/x.py\n")

        with patch("packastack.ai.build_diagnosis._git_ls_tree", return_value=""):
            result = collect_working_tree_context(tmp_path)

        assert "debian/patches/series" in result
        assert "fix.patch" in result

    def test_empty_repo_returns_empty(self, tmp_path: Path) -> None:
        """Test context for repo with no debian/ dir."""
        with patch("packastack.ai.build_diagnosis._git_ls_tree", return_value=""):
            result = collect_working_tree_context(tmp_path)

        assert result == ""


class TestApplyDebianEdits:
    """Tests for apply_debian_edits function."""

    def test_writes_single_file(self, tmp_path: Path) -> None:
        """Test writing a single debian file."""
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "rules").write_text("old content")

        diagnosis = BuildDiagnosisResult(
            diagnosed=True,
            needs_debian_edit=True,
            debian_edits={"debian/rules": "#!/usr/bin/make -f\n%:\n\tdh $@"},
        )

        with patch("packastack.debpkg.gbp.run_command", return_value=(0, "", "")):
            result = apply_debian_edits(tmp_path, diagnosis)

        assert result is True
        assert "dh $@" in (debian / "rules").read_text()

    def test_writes_multiple_files(self, tmp_path: Path) -> None:
        """Test writing multiple debian files."""
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "rules").write_text("old rules")
        (debian / "control").write_text("old control")

        diagnosis = BuildDiagnosisResult(
            diagnosed=True,
            needs_debian_edit=True,
            debian_edits={
                "debian/rules": "new rules",
                "debian/control": "new control",
            },
        )

        with patch("packastack.debpkg.gbp.run_command", return_value=(0, "", "")):
            result = apply_debian_edits(tmp_path, diagnosis)

        assert result is True
        assert "new rules" in (debian / "rules").read_text()
        assert "new control" in (debian / "control").read_text()

    def test_skips_non_debian_paths(self, tmp_path: Path) -> None:
        """Test that non-debian paths are skipped."""
        diagnosis = BuildDiagnosisResult(
            diagnosed=True,
            needs_debian_edit=True,
            debian_edits={"setup.py": "malicious content"},
        )

        with patch("packastack.debpkg.gbp.run_command", return_value=(0, "", "")):
            result = apply_debian_edits(tmp_path, diagnosis)

        assert result is False
        assert not (tmp_path / "setup.py").exists()

    def test_returns_false_with_empty_edits(self, tmp_path: Path) -> None:
        """Test returns False when no edits provided."""
        diagnosis = BuildDiagnosisResult(
            diagnosed=True, needs_debian_edit=True, debian_edits={}
        )
        result = apply_debian_edits(tmp_path, diagnosis)
        assert result is False

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Test that parent directories are created for new files."""
        diagnosis = BuildDiagnosisResult(
            diagnosed=True,
            needs_debian_edit=True,
            debian_edits={"debian/source/format": "3.0 (quilt)"},
        )

        with patch("packastack.debpkg.gbp.run_command", return_value=(0, "", "")):
            result = apply_debian_edits(tmp_path, diagnosis)

        assert result is True
        assert (tmp_path / "debian" / "source" / "format").exists()

    def test_ensures_trailing_newline(self, tmp_path: Path) -> None:
        """Test that files always end with a newline."""
        debian = tmp_path / "debian"
        debian.mkdir()

        diagnosis = BuildDiagnosisResult(
            diagnosed=True,
            needs_debian_edit=True,
            debian_edits={"debian/rules": "no trailing newline"},
        )

        with patch("packastack.debpkg.gbp.run_command", return_value=(0, "", "")):
            apply_debian_edits(tmp_path, diagnosis)

        assert (debian / "rules").read_text().endswith("\n")


class TestApplyAiFix:
    """Tests for apply_ai_fix function."""

    def test_ignores_debian_edit(self, tmp_path: Path) -> None:
        """Test that debian edits are NOT applied (patch-only policy)."""
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "rules").write_text("original")

        diagnosis = BuildDiagnosisResult(
            diagnosed=True,
            needs_debian_edit=True,
            debian_edits={"debian/rules": "new content"},
        )

        result = apply_ai_fix(tmp_path, diagnosis)

        assert result is False
        # File must remain untouched
        assert (debian / "rules").read_text() == "original"

    def test_routes_quilt_patch(self, tmp_path: Path) -> None:
        """Test that quilt patches are routed to apply_ai_patch."""
        diagnosis = BuildDiagnosisResult(
            diagnosed=True,
            needs_patch=True,
            patch_filename="fix.patch",
            patch_content="diff content",
        )

        with (
            patch("packastack.ai.build_diagnosis.validate_patch") as mock_validate,
            patch("packastack.debpkg.gbp.run_command", return_value=(0, "", "")),
        ):
            mock_validate.return_value = PatchValidationResult(valid=True)
            result = apply_ai_fix(tmp_path, diagnosis)

        assert result is True

    def test_returns_false_when_no_fix(self, tmp_path: Path) -> None:
        """Test returns False when neither edit nor patch is proposed."""
        diagnosis = BuildDiagnosisResult(diagnosed=True)
        result = apply_ai_fix(tmp_path, diagnosis)
        assert result is False

    def test_patch_only_when_both_set(self, tmp_path: Path) -> None:
        """Test that only patch is applied when both edit and patch are set."""
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "rules").write_text("original rules")

        diagnosis = BuildDiagnosisResult(
            diagnosed=True,
            needs_debian_edit=True,
            needs_patch=True,
            debian_edits={"debian/rules": "edited content"},
            patch_filename="fix.patch",
            patch_content="diff",
        )

        with (
            patch("packastack.ai.build_diagnosis.validate_patch") as mock_validate,
            patch("packastack.debpkg.gbp.run_command", return_value=(0, "", "")),
        ):
            mock_validate.return_value = PatchValidationResult(valid=True)
            result = apply_ai_fix(tmp_path, diagnosis)

        assert result is True
        # Patch applied, but debian/rules must be untouched
        assert (debian / "rules").read_text() == "original rules"
        assert (tmp_path / "debian" / "patches" / "fix.patch").exists()


class TestDiagnoseBuildFailureIntegration:
    """End-to-end-style tests using run_skill mocked at the client layer."""

    def _cfg_with_key(self) -> dict:
        return {"ai": {"api_key": "test-key", "model": "test", "max_tokens": 100, "timeout": 10}}

    @patch("packastack.ai.runner.call_ai")
    def test_tree_context_reaches_specialist(
        self, mock_call: Any, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """The working-tree collector populates the specialist's user message."""
        log = tmp_path / "build.log"
        log.write_text("error: python_distutils removed\n")
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "control").write_text("Source: neutron-fwaas-dashboard\n")
        (debian / "rules").write_text(
            "#!/usr/bin/make -f\n%:\n\tdh $@ --with python_distutils\n"
        )

        # Force the router→build-patch path without any AI calls for routing.
        monkeypatch.setattr(
            "packastack.ai.build_diagnosis.match_triggers",
            lambda *_a, **_k: "build-patch",
        )
        mock_call.return_value = AIResponse(
            success=True,
            content=(
                "DIAGNOSIS: Switch build system\n"
                "ACTION: QUILT_PATCH\n"
                "EXPLANATION: Patch upstream setup.cfg to fix build\n"
                "PATCH_FILENAME: fix-build.patch\n"
                "--- BEGIN PATCH ---\n"
                "--- a/setup.cfg\n+++ b/setup.cfg\n@@ -1 +1 @@\n-old\n+new\n"
                "--- END PATCH ---\n"
            ),
        )

        sbuild = MockSbuildResult(
            primary_log_path=log, validation_message="Build failed"
        )
        with patch("packastack.ai.build_diagnosis._git_ls_tree", return_value=""):
            result = diagnose_build_failure(
                sbuild_result=sbuild,
                pkg_repo=tmp_path,
                pkg_name="neutron-fwaas-dashboard",
                version="23.0.0",
                ubuntu_series="plucky",
                arch="amd64",
                cfg=self._cfg_with_key(),
            )

        assert result.diagnosed is True
        assert result.needs_patch is True
        assert result.patch_filename == "fix-build.patch"

        user_message = mock_call.call_args.args[1]
        assert "debian/rules" in user_message
        assert "python_distutils" in user_message
