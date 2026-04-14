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

"""Tests for packastack.ai.collectors."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from packastack.ai import collectors
from packastack.ai.collectors import (
    CollectorInputs,
    UnknownCollectorError,
    ai_memory,
    collect,
    debian_control,
    debian_rules,
    failure_header,
    patch_affected_files,
    patch_subject,
    register,
    registered,
    sbuild_log_tail,
    unregister,
    working_tree,
)


def _inputs(tmp_path: Path, **overrides: object) -> CollectorInputs:
    return CollectorInputs(pkg_repo=tmp_path, **overrides)  # type: ignore[arg-type]


class TestRegistry:
    def test_register_and_collect(self, tmp_path: Path) -> None:
        register("custom-test", lambda _i: "hello")
        try:
            assert collect("custom-test", _inputs(tmp_path)) == "hello"
        finally:
            unregister("custom-test")

    def test_register_rejects_duplicate(self, tmp_path: Path) -> None:
        register("dup-test", lambda _i: "x")
        try:
            with pytest.raises(ValueError, match="already registered"):
                register("dup-test", lambda _i: "y")
        finally:
            unregister("dup-test")

    def test_register_replace_overrides(self, tmp_path: Path) -> None:
        register("replace-test", lambda _i: "first")
        try:
            register("replace-test", lambda _i: "second", replace=True)
            assert collect("replace-test", _inputs(tmp_path)) == "second"
        finally:
            unregister("replace-test")

    def test_unregister_missing_is_noop(self) -> None:
        unregister("never-registered")

    def test_collect_unknown_raises(self, tmp_path: Path) -> None:
        with pytest.raises(UnknownCollectorError):
            collect("no-such-collector", _inputs(tmp_path))

    def test_registered_returns_sorted(self) -> None:
        names = registered()
        assert names == sorted(names)
        # Built-ins are registered at import time
        assert "failure_header" in names
        assert "sbuild_log_tail" in names

    def test_register_builtins_idempotent(self) -> None:
        # Calling twice must not raise — module loads this at import.
        collectors._register_builtins()
        collectors._register_builtins()


class TestFailureHeader:
    def test_includes_all_fields(self, tmp_path: Path) -> None:
        block = failure_header(
            _inputs(
                tmp_path,
                pkg_name="nova",
                version="1.2.3",
                ubuntu_series="noble",
                arch="amd64",
                error_msg="boom",
            )
        )
        assert "Package: nova" in block
        assert "Version: 1.2.3" in block
        assert "Distribution: noble" in block
        assert "Architecture: amd64" in block
        assert "Error: boom" in block

    def test_empty_when_no_data(self, tmp_path: Path) -> None:
        assert failure_header(_inputs(tmp_path)) == ""


class TestSbuildLogTail:
    def test_no_sbuild_result(self, tmp_path: Path) -> None:
        assert sbuild_log_tail(_inputs(tmp_path)) == ""

    def test_extracts_failure_section(self, tmp_path: Path) -> None:
        log = tmp_path / "build.log"
        log.write_text(
            "preamble\nE: Failed to build\nmore\n",
            encoding="utf-8",
        )
        sr = SimpleNamespace(
            primary_log_path=log,
            stderr_log_path=None,
            stdout_log_path=None,
        )
        block = sbuild_log_tail(_inputs(tmp_path, sbuild_result=sr))
        assert block.startswith("== sbuild failure log excerpt ==")

    def test_falls_through_missing_paths(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing.log"
        sr = SimpleNamespace(
            primary_log_path=missing,
            stderr_log_path=None,
            stdout_log_path=None,
        )
        assert sbuild_log_tail(_inputs(tmp_path, sbuild_result=sr)) == ""

    def test_empty_excerpt_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = tmp_path / "build.log"
        log.write_text("nothing relevant", encoding="utf-8")
        monkeypatch.setattr(
            collectors, "extract_sbuild_failure_section", lambda *a, **k: ""
        )
        sr = SimpleNamespace(
            primary_log_path=log,
            stderr_log_path=None,
            stdout_log_path=None,
        )
        assert sbuild_log_tail(_inputs(tmp_path, sbuild_result=sr)) == ""


class TestDebianFileCollectors:
    def test_debian_control_reads_file(self, tmp_path: Path) -> None:
        (tmp_path / "debian").mkdir()
        (tmp_path / "debian" / "control").write_text("Source: x\n", encoding="utf-8")
        assert debian_control(_inputs(tmp_path)).startswith("== debian/control ==")

    def test_debian_control_missing_is_empty(self, tmp_path: Path) -> None:
        assert debian_control(_inputs(tmp_path)) == ""

    def test_debian_rules_reads_file(self, tmp_path: Path) -> None:
        (tmp_path / "debian").mkdir()
        (tmp_path / "debian" / "rules").write_text("#!/usr/bin/make -f\n", encoding="utf-8")
        assert debian_rules(_inputs(tmp_path)).startswith("== debian/rules ==")

    def test_debian_rules_missing_is_empty(self, tmp_path: Path) -> None:
        assert debian_rules(_inputs(tmp_path)) == ""

    def test_truncates_when_max_file_lines_set(self, tmp_path: Path) -> None:
        (tmp_path / "debian").mkdir()
        lines = "\n".join(f"line{i}" for i in range(20))
        (tmp_path / "debian" / "control").write_text(lines, encoding="utf-8")
        block = debian_control(
            _inputs(tmp_path, cfg={"ai": {"max_file_lines": 5}})
        )
        assert "truncated" in block


class TestWorkingTree:
    def test_delegates_to_build_diagnosis(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from packastack.ai import build_diagnosis

        calls = {}

        def fake(pkg_repo: Path, max_file_lines: int) -> str:
            calls["pkg_repo"] = pkg_repo
            calls["max_file_lines"] = max_file_lines
            return "== tree =="

        monkeypatch.setattr(
            build_diagnosis, "collect_working_tree_context", fake
        )
        result = working_tree(
            _inputs(tmp_path, cfg={"ai": {"max_file_lines": 7}})
        )
        assert result == "== tree =="
        assert calls == {"pkg_repo": tmp_path, "max_file_lines": 7}


class TestPatchSubject:
    def test_empty_when_no_data(self, tmp_path: Path) -> None:
        assert patch_subject(_inputs(tmp_path)) == ""

    def test_renders_patch_and_error(self, tmp_path: Path) -> None:
        block = patch_subject(
            _inputs(
                tmp_path,
                patch_name="foo.patch",
                patch_content="diff --git a/x b/x\n",
                patch_error="hunk #1 FAILED",
            )
        )
        assert "Failing patch: foo.patch" in block
        assert "diff --git" in block
        assert "gbp pq import error output" in block
        assert "hunk #1 FAILED" in block

    def test_renders_patch_only(self, tmp_path: Path) -> None:
        block = patch_subject(
            _inputs(tmp_path, patch_name="foo.patch", patch_content="body")
        )
        assert "gbp pq import error output" not in block

    def test_renders_error_only(self, tmp_path: Path) -> None:
        block = patch_subject(_inputs(tmp_path, patch_error="hunk failed"))
        assert "Failing patch" not in block
        assert "gbp pq import error output" in block
        assert "hunk failed" in block


class TestPatchAffectedFiles:
    def test_empty_when_no_data(self, tmp_path: Path) -> None:
        assert patch_affected_files(_inputs(tmp_path)) == ""

    def test_lists_missing_and_current(self, tmp_path: Path) -> None:
        block = patch_affected_files(
            _inputs(
                tmp_path,
                missing_files=["a/gone.py"],
                affected_files={"b/here.py": "print('hi')"},
            )
        )
        assert "NO LONGER EXIST" in block
        assert "a/gone.py" in block
        assert "Current source file contents" in block
        assert "b/here.py" in block
        assert "print('hi')" in block

    def test_only_missing(self, tmp_path: Path) -> None:
        block = patch_affected_files(
            _inputs(tmp_path, missing_files=["a.py"])
        )
        assert "a.py" in block
        assert "Current source file contents" not in block

    def test_only_affected(self, tmp_path: Path) -> None:
        block = patch_affected_files(
            _inputs(tmp_path, affected_files={"a.py": "x"})
        )
        assert "NO LONGER EXIST" not in block
        assert "Current source file contents" in block


class TestAiMemory:
    def test_passes_through(self, tmp_path: Path) -> None:
        assert ai_memory(_inputs(tmp_path, ai_memory_context="prior")) == "prior"

    def test_empty_default(self, tmp_path: Path) -> None:
        assert ai_memory(_inputs(tmp_path)) == ""


class TestReadFileSafe:
    def test_handles_os_error(self, tmp_path: Path) -> None:
        # Pointing at a directory causes OSError on read_text
        dir_path = tmp_path / "dir"
        dir_path.mkdir()
        assert collectors._read_file_safe(dir_path) == ""

    def test_no_truncation_under_threshold(self, tmp_path: Path) -> None:
        f = tmp_path / "x"
        f.write_text("a\nb\nc\n", encoding="utf-8")
        assert collectors._read_file_safe(f, max_lines=10) == "a\nb\nc\n"
