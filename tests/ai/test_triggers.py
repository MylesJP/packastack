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

"""Tests for packastack.ai.triggers."""

from __future__ import annotations

from packastack.ai.skills import Skill
from packastack.ai.triggers import match_triggers


def _skill(name: str, patterns: object) -> Skill:
    return Skill(
        name=name,
        description=f"{name} skill",
        system_prompt="body",
        output_contract="patch",
        metadata={"triggers": {"log_patterns": patterns}},
    )


class TestMatchTriggers:
    def test_empty_log_returns_none(self) -> None:
        assert match_triggers([_skill("a", [r"boom"])], "") is None

    def test_first_matching_skill_wins(self) -> None:
        skills = [
            _skill("first", [r"no-match"]),
            _skill("second", [r"boom"]),
            _skill("third", [r"boom"]),
        ]
        assert match_triggers(skills, "boom happened") == "second"

    def test_no_matches_returns_none(self) -> None:
        skills = [_skill("a", [r"xyz"])]
        assert match_triggers(skills, "unrelated text") is None

    def test_skill_without_triggers_metadata(self) -> None:
        skill = Skill(
            name="no-triggers",
            description="",
            system_prompt="body",
            metadata={},
        )
        assert match_triggers([skill], "anything") is None

    def test_triggers_not_a_dict(self) -> None:
        skill = Skill(
            name="bad",
            description="",
            system_prompt="body",
            metadata={"triggers": "not-a-dict"},
        )
        assert match_triggers([skill], "anything") is None

    def test_log_patterns_not_a_list(self) -> None:
        skill = _skill("bad", "not-a-list")
        assert match_triggers([skill], "anything") is None

    def test_skips_non_string_patterns(self) -> None:
        skill = _skill("mixed", [123, r"boom"])
        assert match_triggers([skill], "boom") == "mixed"

    def test_skips_invalid_regex(self) -> None:
        # An invalid regex is skipped; the valid one still matches.
        skill = _skill("mixed", [r"[unterminated", r"boom"])
        assert match_triggers([skill], "boom") == "mixed"

    def test_all_patterns_invalid_returns_none(self) -> None:
        skill = _skill("bad", [r"[unterminated"])
        assert match_triggers([skill], "boom") is None

    def test_empty_skill_list(self) -> None:
        assert match_triggers([], "boom") is None
