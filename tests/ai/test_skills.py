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

"""Tests for packastack.ai.skills loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from packastack.ai.skills import (
    Skill,
    SkillFormatError,
    SkillNotFoundError,
    list_skills,
    load_skill,
)


class TestLoadPackagedSkills:
    """Tests for loading the skills shipped inside the package."""

    def test_patch_diagnosis_loads(self) -> None:
        """patch-diagnosis skill loads with expected metadata."""
        skill = load_skill("patch-diagnosis")
        assert isinstance(skill, Skill)
        assert skill.name == "patch-diagnosis"
        assert "CAN_DROP" in skill.system_prompt
        assert skill.output_contract == "diagnosis"
        assert skill.description

    def test_patch_correction_loads(self) -> None:
        """patch-correction skill loads with expected prompt body."""
        skill = load_skill("patch-correction")
        assert "git apply" in skill.system_prompt
        assert "corrected" in skill.system_prompt.lower()
        assert skill.output_contract == "patch"

    def test_patch_refresh_mentions_pyproject_migration(self) -> None:
        """patch-refresh skill preserves setup.cfg → pyproject.toml guidance."""
        skill = load_skill("patch-refresh")
        assert "setup.cfg" in skill.system_prompt
        assert "pyproject.toml" in skill.system_prompt
        assert skill.output_contract == "patch"

    def test_list_skills_includes_patch_skills(self) -> None:
        """All three patch skills are discoverable."""
        names = list_skills()
        assert "patch-diagnosis" in names
        assert "patch-correction" in names
        assert "patch-refresh" in names
        # Results are sorted for stable UX
        assert names == sorted(names)


class TestLoaderErrorHandling:
    """Tests for loader error paths."""

    def test_missing_skill_raises(self) -> None:
        """Unknown skill name raises SkillNotFoundError."""
        with pytest.raises(SkillNotFoundError, match="does-not-exist"):
            load_skill("does-not-exist")


def _write_skill(root: Path, name: str, content: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


class TestOverrideDir:
    """Tests for PACKASTACK_SKILLS_DIR override behaviour."""

    def test_override_loads_from_custom_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Setting PACKASTACK_SKILLS_DIR loads skills from that directory."""
        _write_skill(
            tmp_path,
            "demo",
            "---\n"
            "name: demo\n"
            "description: A demo skill\n"
            "output_contract: patch\n"
            "when_to_use: For demos only\n"
            "version: 2\n"
            "---\n"
            "Do the thing.\n",
        )
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))

        skill = load_skill("demo")
        assert skill.name == "demo"
        assert skill.description == "A demo skill"
        assert skill.output_contract == "patch"
        assert skill.when_to_use == "For demos only"
        assert skill.version == 2
        assert skill.system_prompt.strip() == "Do the thing."
        assert skill.metadata["name"] == "demo"

    def test_override_list_skills(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """list_skills reflects the overridden directory."""
        _write_skill(tmp_path, "alpha", "---\nname: alpha\ndescription: a\n---\nbody\n")
        _write_skill(tmp_path, "beta", "---\nname: beta\ndescription: b\n---\nbody\n")
        # Skip directories without SKILL.md
        (tmp_path / "not-a-skill").mkdir()
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))

        assert list_skills() == ["alpha", "beta"]

    def test_override_missing_dir_returns_empty_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """list_skills returns [] when the override dir doesn't exist."""
        monkeypatch.setenv(
            "PACKASTACK_SKILLS_DIR", str(tmp_path / "missing")
        )
        assert list_skills() == []


class TestFrontmatterParsing:
    """Tests for SKILL.md frontmatter parsing error paths."""

    def test_rejects_missing_opening_delimiter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_skill(tmp_path, "bad", "name: bad\n---\nbody\n")
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))
        with pytest.raises(SkillFormatError, match="opening"):
            load_skill("bad")

    def test_rejects_missing_closing_delimiter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_skill(tmp_path, "bad", "---\nname: bad\ndescription: x\nbody without close\n")
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))
        with pytest.raises(SkillFormatError, match="closing"):
            load_skill("bad")

    def test_rejects_invalid_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_skill(tmp_path, "bad", "---\nname: [unterminated\n---\nbody\n")
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))
        with pytest.raises(SkillFormatError, match="invalid YAML"):
            load_skill("bad")

    def test_rejects_non_mapping_frontmatter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_skill(tmp_path, "bad", "---\n- just\n- a\n- list\n---\nbody\n")
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))
        with pytest.raises(SkillFormatError, match="mapping"):
            load_skill("bad")

    def test_rejects_missing_required_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_skill(tmp_path, "bad", "---\nname: bad\n---\nbody\n")
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))
        with pytest.raises(SkillFormatError, match=r"name.*description"):
            load_skill("bad")

    def test_rejects_empty_body(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_skill(
            tmp_path, "bad", "---\nname: bad\ndescription: x\n---\n\n"
        )
        monkeypatch.setenv("PACKASTACK_SKILLS_DIR", str(tmp_path))
        with pytest.raises(SkillFormatError, match="empty"):
            load_skill("bad")
