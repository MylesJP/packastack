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

"""Skill orchestration.

``run_skill()`` is the single entry point for invoking an AI skill.
It loads the skill, expands any ``{{skills_menu}}`` placeholder in the
system prompt, assembles the user message from the collectors named in
``requires_context``, calls the AI, and parses the response according
to the skill's ``output_contract``.

All AI and configuration errors are caught and returned as
``SkillResult(success=False, ...)`` so callers don't have to wrap the
call in try/except.
"""

from __future__ import annotations

from typing import Any

from packastack.ai.client import call_ai, is_ai_available
from packastack.ai.collectors import CollectorInputs, collect
from packastack.ai.contracts import SkillResult, parse_by_contract
from packastack.ai.skills import Skill, list_skills, load_skill

_SKILLS_MENU_PLACEHOLDER = "{{skills_menu}}"


def _specialist_skills() -> list[Skill]:
    """Return every installed skill whose contract is not ``dispatch``.

    Routers are excluded from their own menus so they can never pick
    themselves or a peer router.
    """
    out: list[Skill] = []
    for name in list_skills():
        try:
            skill = load_skill(name)
        except Exception:
            # A malformed skill should not break the router.  Skip it.
            continue
        if skill.output_contract != "dispatch":
            out.append(skill)
    return out


def render_skills_menu(skills: list[Skill] | None = None) -> str:
    """Render the skills menu inserted by ``{{skills_menu}}``.

    Args:
        skills: Skills to include.  Defaults to every non-dispatch skill.

    Returns:
        Bulleted list, one entry per skill, suitable for inlining into a
        router prompt.  Returns a trailing newline so the surrounding
        prompt stays tidy.
    """
    if skills is None:
        skills = _specialist_skills()
    if not skills:
        return "(no specialist skills installed)"
    lines = []
    for skill in sorted(skills, key=lambda s: s.name):
        summary = skill.description
        if skill.when_to_use:
            summary = f"{summary} — {skill.when_to_use}"
        lines.append(f"- {skill.name}: {summary}")
    return "\n".join(lines)


def _expand_templates(system_prompt: str) -> str:
    if _SKILLS_MENU_PLACEHOLDER not in system_prompt:
        return system_prompt
    return system_prompt.replace(_SKILLS_MENU_PLACEHOLDER, render_skills_menu())


def _assemble_context(
    skill: Skill, inputs: CollectorInputs
) -> str:
    """Assemble the user message by running each requested collector."""
    requires = skill.metadata.get("requires_context") or []
    if not isinstance(requires, list):
        raise ValueError(
            f"Skill {skill.name}: requires_context must be a list, got "
            f"{type(requires).__name__}"
        )

    blocks: list[str] = []
    for collector_name in requires:
        block = collect(str(collector_name), inputs)
        if block:
            blocks.append(block)
    return "\n\n".join(blocks)


def run_skill(
    name: str,
    inputs: CollectorInputs,
    cfg: dict[str, Any],
) -> SkillResult:
    """Load, run, and parse a skill in one call.

    Args:
        name: Skill name (directory under ``src/packastack/skills``).
        inputs: Collector inputs used to assemble the user message.
        cfg: Packastack configuration dict (used for ``ai.*`` settings).

    Returns:
        :class:`SkillResult`.  ``success=False`` on any failure —
        missing API key, AI error, parser error, malformed skill —
        with details in the ``error`` field.
    """
    if not is_ai_available(cfg):
        return SkillResult(success=False, error="AI not available (no API key)")

    try:
        skill = load_skill(name)
    except Exception as exc:
        return SkillResult(success=False, error=f"Failed to load skill {name!r}: {exc}")

    try:
        system_prompt = _expand_templates(skill.system_prompt)
        user_message = _assemble_context(skill, inputs)
    except Exception as exc:
        return SkillResult(
            success=False,
            contract=skill.output_contract,
            error=f"Failed to assemble context for {name!r}: {exc}",
        )

    response = call_ai(system_prompt, user_message, cfg)
    if not response.success:
        return SkillResult(
            success=False,
            contract=skill.output_contract,
            error=f"AI call failed: {response.error}",
            raw=response.content,
        )

    try:
        payload = parse_by_contract(skill.output_contract, response.content)
    except Exception as exc:
        return SkillResult(
            success=False,
            contract=skill.output_contract,
            raw=response.content,
            error=f"Failed to parse {skill.output_contract!r} response: {exc}",
        )

    return SkillResult(
        success=True,
        contract=skill.output_contract,
        parsed=payload,
        raw=response.content,
    )
