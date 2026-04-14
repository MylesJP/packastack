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

"""Output contract parsers for AI skills.

Every skill declares an ``output_contract`` in its frontmatter.  Parsers
here convert the raw text response into a typed payload; callers switch
on the contract name to read the payload safely.

Supported contracts:

* ``patch`` — the skill produced a unified diff (with DEP3 headers).
* ``diagnosis`` — yes/no judgement with explanation (``patch-diagnosis``).
* ``dispatch`` — router output naming the next skill to invoke.
* ``guidance`` — free-form explanation, no automated action.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class UnknownContractError(ValueError):
    """Raised when a skill declares an output_contract we do not recognise."""


@dataclass
class PatchPayload:
    """Payload for ``output_contract: patch``."""

    action: str  # PATCH | NO_PATCH | REFRESH | NO_REFRESH
    patch_filename: str
    patch_content: str
    diagnosis: str
    explanation: str

    @property
    def has_patch(self) -> bool:
        return bool(self.patch_filename and self.patch_content)


@dataclass
class DiagnosisPayload:
    """Payload for ``output_contract: diagnosis`` (yes/no judgement)."""

    can_drop: bool
    diagnosis: str
    explanation: str


@dataclass
class DispatchPayload:
    """Payload for ``output_contract: dispatch`` (router output)."""

    skill: str
    reason: str


@dataclass
class GuidancePayload:
    """Payload for ``output_contract: guidance`` (text-only advice)."""

    diagnosis: str
    explanation: str


@dataclass
class SkillResult:
    """Result of running a skill.

    Attributes:
        success: Whether the AI call completed and was parsed.
        contract: The declared output contract (empty on failure).
        parsed: Contract-specific typed payload, or ``None`` on failure.
        raw: The raw text response from the model.
        error: Error string set when ``success`` is False.
    """

    success: bool
    contract: str = ""
    parsed: Any = None
    raw: str = ""
    error: str = ""


def _header_value(line: str) -> str:
    return line.split(":", 1)[1].strip()


def _extract_block(content: str, begin: str, end: str) -> str:
    begin_idx = content.find(begin)
    if begin_idx == -1:
        return ""
    end_idx = content.find(end, begin_idx + len(begin))
    if end_idx == -1:
        return ""
    return content[begin_idx + len(begin) : end_idx].strip()


def parse_patch(content: str) -> PatchPayload:
    """Parse a ``patch`` contract response."""
    action = ""
    patch_filename = ""
    diagnosis = ""
    explanation = ""

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("ACTION:"):
            action = _header_value(stripped).upper()
        elif stripped.startswith("PATCH_FILENAME:"):
            patch_filename = _header_value(stripped)
        elif stripped.startswith("EXPLANATION:"):
            explanation = _header_value(stripped)
        elif stripped.startswith("DIAGNOSIS:"):
            diagnosis = _header_value(stripped)

    raw_patch = _extract_block(content, "--- BEGIN PATCH ---", "--- END PATCH ---")
    patch_content = f"{raw_patch}\n" if raw_patch else ""

    if not explanation:
        explanation = diagnosis or content[:500]

    return PatchPayload(
        action=action,
        patch_filename=patch_filename,
        patch_content=patch_content,
        diagnosis=diagnosis,
        explanation=explanation,
    )


def parse_diagnosis(content: str) -> DiagnosisPayload:
    """Parse a ``diagnosis`` contract response (yes/no + explanation)."""
    can_drop = False
    diagnosis = ""
    explanation = ""

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("CAN_DROP:"):
            can_drop = _header_value(stripped).upper() == "YES"
        elif stripped.startswith("EXPLANATION:"):
            explanation = _header_value(stripped)
        elif stripped.startswith("DIAGNOSIS:"):
            diagnosis = _header_value(stripped)

    if not explanation:
        explanation = diagnosis or content[:500]

    return DiagnosisPayload(
        can_drop=can_drop,
        diagnosis=diagnosis,
        explanation=explanation,
    )


def parse_dispatch(content: str) -> DispatchPayload:
    """Parse a ``dispatch`` contract response (router output)."""
    skill = ""
    reason = ""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("SKILL:"):
            skill = _header_value(stripped)
        elif stripped.startswith("REASON:"):
            reason = _header_value(stripped)
    return DispatchPayload(skill=skill, reason=reason)


def parse_guidance(content: str) -> GuidancePayload:
    """Parse a ``guidance`` contract response (text-only)."""
    diagnosis = ""
    explanation = ""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("EXPLANATION:"):
            explanation = _header_value(stripped)
        elif stripped.startswith("DIAGNOSIS:"):
            diagnosis = _header_value(stripped)
    if not explanation:
        explanation = diagnosis or content[:500]
    return GuidancePayload(diagnosis=diagnosis, explanation=explanation)


_PARSERS = {
    "patch": parse_patch,
    "diagnosis": parse_diagnosis,
    "dispatch": parse_dispatch,
    "guidance": parse_guidance,
}


def parse_by_contract(contract: str, content: str) -> Any:
    """Dispatch to the parser for *contract*.

    Args:
        contract: The skill's declared ``output_contract``.
        content: Raw text from the AI response.

    Returns:
        A contract-specific payload dataclass.

    Raises:
        UnknownContractError: If the contract name has no registered parser.
    """
    parser = _PARSERS.get(contract)
    if parser is None:
        raise UnknownContractError(
            f"No parser registered for output_contract={contract!r}"
        )
    return parser(content)


def known_contracts() -> list[str]:
    """Return the sorted list of contract names that can be parsed."""
    return sorted(_PARSERS)
