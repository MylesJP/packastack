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

"""Tests for packastack.ai.runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from packastack.ai import runner
from packastack.ai.client import AIResponse
from packastack.ai.collectors import CollectorInputs, register, unregister
from packastack.ai.contracts import (
    DispatchPayload,
    PatchPayload,
    SkillResult,
)
from packastack.ai.runner import (
    _expand_templates,
    render_skills_menu,
    run_skill,
)
from packastack.ai.skills import Skill


def _write_skill(root: Path, name: str, frontmatter: str, body: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\n{frontmatter}---\n{body}\n", encoding="utf-8"
    )


def _inputs(tmp_path: Path) -> CollectorInputs:
    return CollectorInputs(pkg_repo=tmp_path, cfg={})


class TestRenderSkillsMenu:
    def test_empty_menu_message(self) -> None:
        assert render_skills_menu([]) == "(no specialist skills installed)"

    def test_renders_skills_sorted(self) -> None:
        skills = [
            Skill(
                name="beta",
                description="B",
                system_prompt="x",
                when_to_use="when B",
            ),
            Skill(name="alpha", description="A", system_prompt="x"),
        ]
        out = render_skills_menu(skills)
        lines = out.splitlines()
        assert lines[0] == "- alpha: A"
        assert lines[1] == "- beta: B — when B"

    def test_default_loads_specialist_skills(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_skill(
            tmp_path,
            "specialist",
            "name: specialist\ndescription: does x\noutput_contract: patch\n",
            "body",
        )
        _write_skill(
            tmp_path,
            "router",
            "name: router\ndescription: routes\noutput_contract: dispatch\n",
            "body",
        )
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))
        out = render_skills_menu()
        assert "- specialist: does x" in out
        assert "router" not in out

    def test_specialist_skills_skips_malformed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_skill(
            tmp_path,
            "ok",
            "name: ok\ndescription: fine\noutput_contract: patch\n",
            "body",
        )
        # Missing required fields — load_skill will raise
        _write_skill(tmp_path, "broken", "name: broken\n", "body")
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))
        names = [s.name for s in runner._specialist_skills()]
        assert names == ["ok"]


class TestExpandTemplates:
    def test_no_placeholder_is_passthrough(self) -> None:
        assert _expand_templates("no templates here") == "no templates here"

    def test_expands_skills_menu(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_skill(
            tmp_path,
            "foo",
            "name: foo\ndescription: F\noutput_contract: patch\n",
            "body",
        )
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))
        out = _expand_templates("before\n{{skills_menu}}\nafter")
        assert "- foo: F" in out
        assert out.startswith("before\n")
        assert out.endswith("\nafter")


class TestRunSkill:
    def test_no_ai_key(self, tmp_path: Path) -> None:
        result = run_skill("patch-diagnosis", _inputs(tmp_path), cfg={})
        assert isinstance(result, SkillResult)
        assert result.success is False
        assert "AI not available" in result.error

    def test_missing_skill(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))
        result = run_skill(
            "does-not-exist",
            _inputs(tmp_path),
            cfg={"ai": {"api_key": "x"}},
        )
        assert result.success is False
        assert "Failed to load skill" in result.error

    def test_bad_requires_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_skill(
            tmp_path,
            "bad",
            "name: bad\ndescription: x\noutput_contract: patch\n"
            "requires_context: not-a-list\n",
            "body",
        )
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))
        result = run_skill(
            "bad", _inputs(tmp_path), cfg={"ai": {"api_key": "x"}}
        )
        assert result.success is False
        assert "assemble context" in result.error
        assert result.contract == "patch"

    def test_unknown_collector(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_skill(
            tmp_path,
            "bad",
            "name: bad\ndescription: x\noutput_contract: patch\n"
            "requires_context:\n  - nope\n",
            "body",
        )
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))
        result = run_skill(
            "bad", _inputs(tmp_path), cfg={"ai": {"api_key": "x"}}
        )
        assert result.success is False
        assert "assemble context" in result.error

    def test_ai_call_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_skill(
            tmp_path,
            "ok",
            "name: ok\ndescription: x\noutput_contract: patch\n",
            "body",
        )
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))
        monkeypatch.setattr(
            runner,
            "call_ai",
            lambda *a, **k: AIResponse(success=False, error="nope", content="partial"),
        )
        result = run_skill(
            "ok", _inputs(tmp_path), cfg={"ai": {"api_key": "x"}}
        )
        assert result.success is False
        assert "AI call failed" in result.error
        assert result.raw == "partial"
        assert result.contract == "patch"

    def test_parse_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_skill(
            tmp_path,
            "ok",
            "name: ok\ndescription: x\noutput_contract: made-up\n",
            "body",
        )
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))
        monkeypatch.setattr(
            runner,
            "call_ai",
            lambda *a, **k: AIResponse(success=True, content="irrelevant"),
        )
        result = run_skill(
            "ok", _inputs(tmp_path), cfg={"ai": {"api_key": "x"}}
        )
        assert result.success is False
        assert "Failed to parse" in result.error
        assert result.raw == "irrelevant"

    def test_success_path_with_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_skill(
            tmp_path,
            "ok",
            "name: ok\ndescription: x\noutput_contract: patch\n"
            "requires_context:\n  - custom-probe\n",
            "body",
        )
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))

        captured: dict[str, str] = {}

        def fake_call_ai(system: str, user: str, _cfg: dict) -> AIResponse:
            captured["system"] = system
            captured["user"] = user
            return AIResponse(success=True, content="ACTION: NO_PATCH\n")

        monkeypatch.setattr(runner, "call_ai", fake_call_ai)

        register("custom-probe", lambda _i: "CONTEXT BLOCK", replace=True)
        try:
            result = run_skill(
                "ok", _inputs(tmp_path), cfg={"ai": {"api_key": "x"}}
            )
        finally:
            unregister("custom-probe")

        assert result.success is True
        assert result.contract == "patch"
        assert isinstance(result.parsed, PatchPayload)
        assert result.parsed.action == "NO_PATCH"
        assert captured["user"] == "CONTEXT BLOCK"
        assert captured["system"].strip() == "body"

    def test_skips_empty_collectors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_skill(
            tmp_path,
            "ok",
            "name: ok\ndescription: x\noutput_contract: patch\n"
            "requires_context:\n  - empty-probe\n  - full-probe\n",
            "body",
        )
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))

        captured: dict[str, str] = {}

        def fake_call_ai(system: str, user: str, _cfg: dict) -> AIResponse:
            captured["user"] = user
            return AIResponse(success=True, content="ACTION: NO_PATCH\n")

        monkeypatch.setattr(runner, "call_ai", fake_call_ai)
        register("empty-probe", lambda _i: "", replace=True)
        register("full-probe", lambda _i: "FULL", replace=True)
        try:
            result = run_skill(
                "ok", _inputs(tmp_path), cfg={"ai": {"api_key": "x"}}
            )
        finally:
            unregister("empty-probe")
            unregister("full-probe")

        assert result.success is True
        assert captured["user"] == "FULL"

    def test_success_dispatch_contract(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_skill(
            tmp_path,
            "router",
            "name: router\ndescription: picks\noutput_contract: dispatch\n",
            "Pick one of these:\n{{skills_menu}}\n",
        )
        _write_skill(
            tmp_path,
            "worker",
            "name: worker\ndescription: does stuff\noutput_contract: patch\n",
            "body",
        )
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))

        captured: dict[str, str] = {}

        def fake_call_ai(system: str, user: str, _cfg: dict) -> AIResponse:
            captured["system"] = system
            return AIResponse(success=True, content="SKILL: worker\nREASON: it fits\n")

        monkeypatch.setattr(runner, "call_ai", fake_call_ai)

        result = run_skill(
            "router", _inputs(tmp_path), cfg={"ai": {"api_key": "x"}}
        )
        assert result.success is True
        assert isinstance(result.parsed, DispatchPayload)
        assert result.parsed.skill == "worker"
        assert "- worker: does stuff" in captured["system"]
        assert "{{skills_menu}}" not in captured["system"]
