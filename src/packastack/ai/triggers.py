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

"""Cheap trigger-based skill selection.

Skills may declare regex patterns under their ``triggers.log_patterns``
frontmatter key.  When a failure log matches one of a skill's
patterns, that skill can be invoked directly, bypassing the router's
AI classifier and saving a round-trip.

Matching is in-order: the first skill (alphabetical by name) that
matches wins.  Authors should write narrow, high-signal patterns so
that only the intended specialist triggers.
"""

from __future__ import annotations

import re

from packastack.ai.skills import Skill


def _compile_patterns(skill: Skill) -> list[re.Pattern[str]]:
    triggers = skill.metadata.get("triggers")
    if not isinstance(triggers, dict):
        return []
    patterns = triggers.get("log_patterns")
    if not isinstance(patterns, list):
        return []
    compiled: list[re.Pattern[str]] = []
    for pat in patterns:
        if not isinstance(pat, str):
            continue
        try:
            compiled.append(re.compile(pat))
        except re.error:
            # Skip malformed patterns so one bad skill can't break triage.
            continue
    return compiled


def match_triggers(skills: list[Skill], log_text: str) -> str | None:
    """Return the first matching skill's name, or None.

    Args:
        skills: Candidate skills to test (normally the specialist skills
            returned by :func:`packastack.ai.runner._specialist_skills`).
        log_text: Text to search with each skill's patterns — typically
            an sbuild log tail.

    Returns:
        Name of the first skill that matches, or ``None`` if nothing
        matches.  Skills are evaluated in the order given.
    """
    if not log_text:
        return None
    for skill in skills:
        for pattern in _compile_patterns(skill):
            if pattern.search(log_text):
                return skill.name
    return None
