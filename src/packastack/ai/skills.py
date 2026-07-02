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

"""Skill loader for AI prompts.

Skills live under ``src/packastack/skills/<skill-name>/SKILL.md`` as
markdown files with a YAML frontmatter block. They contain no Python
and work with any OpenAI-compatible chat model, so new AI tasks can be
added by dropping a file into that directory — no code changes.

Set ``PACKASTACK_SKILLS_DIR`` to a directory of skills to override the
packaged ones (useful for local iteration without reinstalling).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_PACKAGED_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
_FRONTMATTER_DELIM = "---"


class SkillNotFoundError(LookupError):
    """Raised when a requested skill cannot be located."""


class SkillFormatError(ValueError):
    """Raised when a SKILL.md file is malformed."""


@dataclass
class Skill:
    """A loaded skill: prompt plus metadata."""

    name: str
    description: str
    system_prompt: str
    output_contract: str = ""
    version: int = 1
    when_to_use: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def _skills_root() -> Path:
    """Return the directory to load skills from.

    Honors the ``PACKASTACK_SKILLS_DIR`` environment variable for
    local development; otherwise falls back to the directory shipped
    inside the installed package.
    """
    override = os.environ.get("PACKASTACK_SKILLS_DIR")
    if override:
        return Path(override)
    return _PACKAGED_SKILLS_DIR


def _parse_skill_file(path: Path) -> Skill:
    """Parse a SKILL.md file into a :class:`Skill`.

    Args:
        path: Absolute path to ``SKILL.md``.

    Returns:
        Parsed skill.

    Raises:
        SkillFormatError: If the frontmatter is missing or malformed.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    if not lines or lines[0].strip() != _FRONTMATTER_DELIM:
        raise SkillFormatError(
            f"{path}: missing opening '---' frontmatter delimiter"
        )

    close_idx: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == _FRONTMATTER_DELIM:
            close_idx = idx
            break

    if close_idx is None:
        raise SkillFormatError(
            f"{path}: missing closing '---' frontmatter delimiter"
        )

    frontmatter_text = "\n".join(lines[1:close_idx])
    try:
        metadata = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError as exc:
        raise SkillFormatError(f"{path}: invalid YAML frontmatter: {exc}") from exc

    if not isinstance(metadata, dict):
        raise SkillFormatError(
            f"{path}: frontmatter must be a YAML mapping"
        )

    name = metadata.get("name")
    description = metadata.get("description")
    if not name or not description:
        raise SkillFormatError(
            f"{path}: frontmatter must set 'name' and 'description'"
        )

    body = "\n".join(lines[close_idx + 1 :]).lstrip("\n")
    if not body.strip():
        raise SkillFormatError(f"{path}: skill body (system prompt) is empty")

    return Skill(
        name=str(name),
        description=str(description),
        system_prompt=body,
        output_contract=str(metadata.get("output_contract", "")),
        version=int(metadata.get("version", 1)),
        when_to_use=str(metadata.get("when_to_use", "")),
        metadata=metadata,
    )


def load_skill(name: str) -> Skill:
    """Load a skill by name.

    Args:
        name: Skill directory name (e.g. ``"patch-refresh"``).

    Returns:
        The parsed :class:`Skill`.

    Raises:
        SkillNotFoundError: If no matching skill directory exists.
        SkillFormatError: If the skill's ``SKILL.md`` is malformed.
    """
    skill_file = _skills_root() / name / "SKILL.md"
    if not skill_file.is_file():
        raise SkillNotFoundError(f"Skill not found: {name} (looked at {skill_file})")
    return _parse_skill_file(skill_file)


def list_skills() -> list[str]:
    """Return the names of every discoverable skill, sorted.

    Only directories that contain a readable ``SKILL.md`` are returned.
    """
    root = _skills_root()
    if not root.is_dir():
        return []
    return sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and (entry / "SKILL.md").is_file()
    )
