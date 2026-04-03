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

"""Tests for packastack.ai.prompts module."""

from __future__ import annotations

from pathlib import Path

from packastack.ai import prompts


class TestBuildPatchContext:
    """Tests for build_patch_context function."""

    def test_includes_all_fields(self) -> None:
        """Test that context includes all provided fields."""
        result = prompts.build_patch_context(
            patch_name="fix-py314.patch",
            patch_content="--- a/setup.py\n+++ b/setup.py\n",
            error_output="CONFLICT in setup.py",
            pkg_name="python-aodh",
            version="19.0.0",
            ubuntu_series="plucky",
        )
        assert "python-aodh" in result
        assert "19.0.0" in result
        assert "plucky" in result
        assert "fix-py314.patch" in result
        assert "CONFLICT in setup.py" in result
        assert "--- a/setup.py" in result

    def test_empty_patch_content(self) -> None:
        """Test with empty patch content."""
        result = prompts.build_patch_context("p.patch", "", "error", "pkg", "1.0", "noble")
        assert "p.patch" in result
        assert "error" in result


class TestBuildSbuildContext:
    """Tests for build_sbuild_context function."""

    def test_includes_all_fields(self) -> None:
        """Test that context includes all provided fields."""
        result = prompts.build_sbuild_context(
            log_excerpt="make: *** [build] Error 2",
            control_content="Build-Depends: python3-all",
            rules_content="#!/usr/bin/make -f",
            pkg_name="aodh",
            version="19.0.0-0ubuntu1",
            ubuntu_series="plucky",
            arch="amd64",
            error_msg="Build failed",
        )
        assert "aodh" in result
        assert "19.0.0-0ubuntu1" in result
        assert "plucky" in result
        assert "amd64" in result
        assert "Build failed" in result
        assert "make: ***" in result
        assert "Build-Depends" in result
        assert "make -f" in result

    def test_empty_log(self) -> None:
        """Test with empty log excerpt."""
        result = prompts.build_sbuild_context("", "ctrl", "rules", "p", "1", "n", "amd64", "err")
        assert "debian/control" in result

    def test_includes_ai_memory_context(self) -> None:
        """Test that ai_memory_context is appended when provided."""
        result = prompts.build_sbuild_context(
            log_excerpt="error output",
            control_content="ctrl",
            rules_content="rules",
            pkg_name="nova",
            version="30.0.0",
            ubuntu_series="plucky",
            arch="amd64",
            error_msg="Build failed",
            ai_memory_context="== Previous AI attempts ==\nAttempt 1: fix.patch failed",
        )
        assert "Previous AI attempts" in result
        assert "fix.patch failed" in result

    def test_omits_memory_when_empty(self) -> None:
        """Test that empty ai_memory_context adds nothing."""
        result = prompts.build_sbuild_context(
            log_excerpt="error",
            control_content="ctrl",
            rules_content="rules",
            pkg_name="nova",
            version="1.0",
            ubuntu_series="noble",
            arch="amd64",
            error_msg="err",
            ai_memory_context="",
        )
        assert "Previous" not in result

    def test_includes_working_tree_context(self) -> None:
        """Test that working_tree_context is appended when provided."""
        result = prompts.build_sbuild_context(
            log_excerpt="error output",
            control_content="ctrl",
            rules_content="rules",
            pkg_name="nova",
            version="30.0.0",
            ubuntu_series="plucky",
            arch="amd64",
            error_msg="Build failed",
            working_tree_context="== File tree ==\ndebian/rules\nsetup.py",
        )
        assert "File tree" in result
        assert "setup.py" in result

    def test_omits_tree_context_when_empty(self) -> None:
        """Test that empty working_tree_context adds nothing."""
        result = prompts.build_sbuild_context(
            log_excerpt="error",
            control_content="ctrl",
            rules_content="rules",
            pkg_name="nova",
            version="1.0",
            ubuntu_series="noble",
            arch="amd64",
            error_msg="err",
            working_tree_context="",
        )
        assert "File tree" not in result


class TestExtractSbuildFailureSection:
    """Tests for extract_sbuild_failure_section function."""

    def test_extracts_around_error_pattern(self, tmp_path: Path) -> None:
        """Test extraction around an error pattern."""
        log = tmp_path / "build.log"
        lines = [f"line {i}: normal output" for i in range(200)]
        lines[100] = "make: *** [override_dh_auto_test] Error 2"
        log.write_text("\n".join(lines))

        result = prompts.extract_sbuild_failure_section(log)
        assert "make: ***" in result
        # Should include lines before the error
        assert "line 50:" in result

    def test_returns_tail_when_no_pattern(self, tmp_path: Path) -> None:
        """Test returns last N lines when no error pattern found."""
        log = tmp_path / "build.log"
        lines = [f"line {i}: all good" for i in range(1000)]
        log.write_text("\n".join(lines))

        result = prompts.extract_sbuild_failure_section(log, max_lines=100)
        result_lines = result.strip().splitlines()
        assert len(result_lines) <= 100
        assert "line 999:" in result

    def test_empty_file(self, tmp_path: Path) -> None:
        """Test with empty file."""
        log = tmp_path / "build.log"
        log.write_text("")
        result = prompts.extract_sbuild_failure_section(log)
        assert result == ""

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Test with nonexistent file."""
        result = prompts.extract_sbuild_failure_section(tmp_path / "nope.log")
        assert result == ""

    def test_includes_tail_section(self, tmp_path: Path) -> None:
        """Test that tail (sbuild summary) is included when error is early."""
        log = tmp_path / "build.log"
        lines = [f"line {i}: output" for i in range(1000)]
        lines[50] = "error: something broke"
        lines[990] = "Build summary line"
        log.write_text("\n".join(lines))

        result = prompts.extract_sbuild_failure_section(log)
        assert "error: something broke" in result
        assert "Build summary line" in result

    def test_respects_max_lines(self, tmp_path: Path) -> None:
        """Test that output is capped at max_lines."""
        log = tmp_path / "build.log"
        lines = [f"line {i}: output" for i in range(2000)]
        lines[0] = "error: first line error"
        log.write_text("\n".join(lines))

        result = prompts.extract_sbuild_failure_section(log, max_lines=50)
        result_lines = result.strip().splitlines()
        assert len(result_lines) <= 50

    def test_detects_python_errors(self, tmp_path: Path) -> None:
        """Test detection of Python error patterns."""
        log = tmp_path / "build.log"
        lines = ["normal" for _ in range(100)]
        lines[50] = "ModuleNotFoundError: No module named 'removed_lib'"
        log.write_text("\n".join(lines))

        result = prompts.extract_sbuild_failure_section(log)
        assert "ModuleNotFoundError" in result

    def test_returns_full_log_when_max_lines_zero(self, tmp_path: Path) -> None:
        """Test that max_lines=0 returns the entire log file."""
        log = tmp_path / "build.log"
        lines = [f"line {i}: output" for i in range(2000)]
        lines[50] = "error: something broke"
        log.write_text("\n".join(lines))

        result = prompts.extract_sbuild_failure_section(log, max_lines=0)
        assert "line 0:" in result
        assert "line 1999:" in result
        assert "error: something broke" in result
        assert "truncated" not in result


class TestPromptConstants:
    """Tests for prompt constant strings."""

    def test_patch_system_prompt_is_nonempty(self) -> None:
        """Test PATCH_DIAGNOSIS_SYSTEM is defined."""
        assert len(prompts.PATCH_DIAGNOSIS_SYSTEM) > 50
        assert "CAN_DROP" in prompts.PATCH_DIAGNOSIS_SYSTEM

    def test_build_system_prompt_is_nonempty(self) -> None:
        """Test BUILD_DIAGNOSIS_SYSTEM is defined."""
        assert len(prompts.BUILD_DIAGNOSIS_SYSTEM) > 50
        assert "QUILT_PATCH" in prompts.BUILD_DIAGNOSIS_SYSTEM
        assert "NO_PATCH" in prompts.BUILD_DIAGNOSIS_SYSTEM
        assert "DEP3" in prompts.BUILD_DIAGNOSIS_SYSTEM
        # AI must not be allowed to propose debian/ file edits
        assert "ACTION: DEBIAN_EDIT" not in prompts.BUILD_DIAGNOSIS_SYSTEM

    def test_patch_correction_prompt_is_nonempty(self) -> None:
        """Test PATCH_CORRECTION_SYSTEM is defined."""
        assert len(prompts.PATCH_CORRECTION_SYSTEM) > 50
        assert "git apply" in prompts.PATCH_CORRECTION_SYSTEM
        assert "corrected" in prompts.PATCH_CORRECTION_SYSTEM.lower()

    def test_patch_refresh_mentions_pyproject_migration(self) -> None:
        """Test PATCH_REFRESH_SYSTEM mentions setup.cfg → pyproject.toml."""
        assert "setup.cfg" in prompts.PATCH_REFRESH_SYSTEM
        assert "pyproject.toml" in prompts.PATCH_REFRESH_SYSTEM


class TestBuildPatchRefreshContext:
    """Tests for build_patch_refresh_context function."""

    def test_includes_missing_files_section(self) -> None:
        """Test missing files are listed in the context."""
        result = prompts.build_patch_refresh_context(
            patch_name="fix.patch",
            patch_content="diff content",
            error_output="error",
            affected_files={"pyproject.toml": "[project]\nname = test\n"},
            pkg_name="pkg",
            version="1.0",
            missing_files=["setup.cfg"],
        )

        assert "NO LONGER EXIST" in result
        assert "setup.cfg" in result
        assert "[project]" in result

    def test_no_missing_files_section_when_none(self) -> None:
        """Test no missing files section when all files exist."""
        result = prompts.build_patch_refresh_context(
            patch_name="fix.patch",
            patch_content="diff content",
            error_output="error",
            affected_files={"setup.cfg": "[metadata]\n"},
            pkg_name="pkg",
            version="1.0",
        )

        assert "NO LONGER EXIST" not in result

    def test_no_missing_files_section_when_empty_list(self) -> None:
        """Test no missing files section when empty list."""
        result = prompts.build_patch_refresh_context(
            patch_name="fix.patch",
            patch_content="diff content",
            error_output="error",
            affected_files={},
            pkg_name="pkg",
            version="1.0",
            missing_files=[],
        )

        assert "NO LONGER EXIST" not in result

    def test_includes_working_tree_context(self) -> None:
        """Test that working_tree_context is appended when provided."""
        result = prompts.build_patch_refresh_context(
            patch_name="fix.patch",
            patch_content="diff content",
            error_output="error",
            affected_files={},
            pkg_name="pkg",
            version="1.0",
            working_tree_context="== File tree ==\ndebian/rules\nsetup.py",
        )

        assert "File tree" in result
        assert "setup.py" in result

    def test_omits_tree_context_when_empty(self) -> None:
        """Test that empty working_tree_context adds nothing."""
        result = prompts.build_patch_refresh_context(
            patch_name="fix.patch",
            patch_content="diff content",
            error_output="error",
            affected_files={},
            pkg_name="pkg",
            version="1.0",
            working_tree_context="",
        )

        assert "File tree" not in result
