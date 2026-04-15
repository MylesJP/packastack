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

"""Tests for packastack.ai.contracts parsers."""

from __future__ import annotations

import pytest

from packastack.ai.contracts import (
    DiagnosisPayload,
    DispatchPayload,
    GuidancePayload,
    PatchPayload,
    UnknownContractError,
    known_contracts,
    parse_by_contract,
    parse_diagnosis,
    parse_dispatch,
    parse_guidance,
    parse_patch,
)


class TestParsePatch:
    def test_full_response(self) -> None:
        content = (
            "DIAGNOSIS: missing import\n"
            "ACTION: PATCH\n"
            "EXPLANATION: add missing import\n"
            "PATCH_FILENAME: fix.patch\n"
            "--- BEGIN PATCH ---\n"
            "diff --git a/x b/x\n"
            "--- END PATCH ---\n"
        )
        payload = parse_patch(content)
        assert payload.action == "PATCH"
        assert payload.patch_filename == "fix.patch"
        assert "diff --git" in payload.patch_content
        assert payload.patch_content.endswith("\n")
        assert payload.has_patch

    def test_no_patch_response(self) -> None:
        payload = parse_patch(
            "DIAGNOSIS: nothing to do\nACTION: NO_PATCH\nEXPLANATION: skip\n"
        )
        assert payload.action == "NO_PATCH"
        assert not payload.has_patch
        assert payload.explanation == "skip"

    def test_explanation_falls_back_to_diagnosis(self) -> None:
        payload = parse_patch("DIAGNOSIS: the thing\nACTION: NO_PATCH\n")
        assert payload.explanation == "the thing"

    def test_explanation_falls_back_to_raw(self) -> None:
        payload = parse_patch("just some freeform text")
        assert payload.explanation.startswith("just some freeform")

    def test_handles_refresh_action(self) -> None:
        payload = parse_patch(
            "ACTION: REFRESH\nPATCH_FILENAME: x.patch\n"
            "--- BEGIN PATCH ---\nbody\n--- END PATCH ---\n"
        )
        assert payload.action == "REFRESH"
        assert payload.patch_content.strip() == "body"

    def test_patch_block_without_end_delimiter(self) -> None:
        payload = parse_patch(
            "ACTION: PATCH\n"
            "PATCH_FILENAME: x.patch\n"
            "--- BEGIN PATCH ---\n"
            "body without closing delimiter\n"
        )
        assert payload.action == "PATCH"
        assert payload.patch_content == ""


class TestParseDiagnosis:
    def test_can_drop_yes(self) -> None:
        payload = parse_diagnosis(
            "DIAGNOSIS: merged upstream\n"
            "CAN_DROP: YES\n"
            "EXPLANATION: commit abc\n"
        )
        assert payload.can_drop is True
        assert payload.explanation == "commit abc"

    def test_can_drop_no(self) -> None:
        payload = parse_diagnosis("CAN_DROP: NO\nDIAGNOSIS: still needed\n")
        assert payload.can_drop is False
        assert payload.explanation == "still needed"

    def test_explanation_fallback(self) -> None:
        payload = parse_diagnosis("something unparseable")
        assert payload.can_drop is False
        assert payload.explanation.startswith("something unparseable")


class TestParseDispatch:
    def test_parses_skill_and_reason(self) -> None:
        payload = parse_dispatch("SKILL: python-compat\nREASON: distutils removed\n")
        assert payload.skill == "python-compat"
        assert payload.reason == "distutils removed"
        assert payload.confidence == 0.0
        assert payload.evidence == []
        assert payload.fallback_skills == []
        assert payload.extra_files_needed == []

    def test_missing_skill(self) -> None:
        payload = parse_dispatch("REASON: nothing to pick\n")
        assert payload.skill == ""

    def test_ignores_unrelated_lines(self) -> None:
        payload = parse_dispatch(
            "some chatter\nSKILL: a\nmore chatter\nREASON: because\n"
        )
        assert payload.skill == "a"
        assert payload.reason == "because"

    def test_parses_confidence_float(self) -> None:
        payload = parse_dispatch(
            "SKILL: build-patch\nREASON: ok\nCONFIDENCE: 0.85\n"
        )
        assert payload.confidence == 0.85

    def test_parses_confidence_percent(self) -> None:
        payload = parse_dispatch(
            "SKILL: build-patch\nREASON: ok\nCONFIDENCE: 85%\n"
        )
        assert payload.confidence == 0.85

    def test_parses_confidence_bare_integer_clamps(self) -> None:
        """A bare ``85`` (no %) is treated as out-of-range and clamped."""
        payload = parse_dispatch(
            "SKILL: build-patch\nREASON: ok\nCONFIDENCE: 85\n"
        )
        assert payload.confidence == 1.0

    def test_parses_confidence_with_trailing_note(self) -> None:
        payload = parse_dispatch(
            "SKILL: build-patch\nREASON: ok\nCONFIDENCE: 0.9 (high)\n"
        )
        assert payload.confidence == 0.9

    def test_invalid_confidence_becomes_zero(self) -> None:
        payload = parse_dispatch(
            "SKILL: build-patch\nREASON: ok\nCONFIDENCE: high\n"
        )
        assert payload.confidence == 0.0

    def test_empty_confidence_becomes_zero(self) -> None:
        payload = parse_dispatch(
            "SKILL: build-patch\nREASON: ok\nCONFIDENCE:\n"
        )
        assert payload.confidence == 0.0

    def test_negative_confidence_clamped_to_zero(self) -> None:
        payload = parse_dispatch(
            "SKILL: build-patch\nREASON: ok\nCONFIDENCE: -0.4\n"
        )
        assert payload.confidence == 0.0

    def test_confidence_above_one_is_clamped(self) -> None:
        """A 1.5 fraction (not percent) is clamped to 1.0, not divided."""
        payload = parse_dispatch(
            "SKILL: build-patch\nREASON: ok\nCONFIDENCE: 1.5\n"
        )
        assert payload.confidence == 1.0

    def test_parses_multiple_evidence_lines(self) -> None:
        payload = parse_dispatch(
            "SKILL: python-compat\n"
            "EVIDENCE: ModuleNotFoundError: No module named 'distutils'\n"
            "EVIDENCE: at /tmp/setup.py:12\n"
            "REASON: python bump\n"
        )
        assert payload.evidence == [
            "ModuleNotFoundError: No module named 'distutils'",
            "at /tmp/setup.py:12",
        ]

    def test_skips_empty_evidence_values(self) -> None:
        payload = parse_dispatch(
            "SKILL: build-patch\nEVIDENCE:\nEVIDENCE: real line\n"
        )
        assert payload.evidence == ["real line"]

    def test_parses_fallback_skills(self) -> None:
        payload = parse_dispatch(
            "SKILL: python-compat\n"
            "FALLBACK: build-patch\n"
            "FALLBACK: library-sync-advisor\n"
        )
        assert payload.fallback_skills == ["build-patch", "library-sync-advisor"]

    def test_parses_extra_files_needed(self) -> None:
        payload = parse_dispatch(
            "SKILL: build-patch\n"
            "EXTRA_FILES: debian/patches/series\n"
            "EXTRA_FILES: setup.cfg\n"
        )
        assert payload.extra_files_needed == [
            "debian/patches/series",
            "setup.cfg",
        ]

    def test_skips_empty_fallback_and_extra(self) -> None:
        payload = parse_dispatch(
            "SKILL: build-patch\nFALLBACK:\nEXTRA_FILES:\n"
        )
        assert payload.fallback_skills == []
        assert payload.extra_files_needed == []


class TestParseGuidance:
    def test_parses_explanation(self) -> None:
        payload = parse_guidance("EXPLANATION: needs MIR for libfoo\n")
        assert payload.explanation == "needs MIR for libfoo"

    def test_falls_back_to_diagnosis(self) -> None:
        payload = parse_guidance("DIAGNOSIS: advisory\n")
        assert payload.explanation == "advisory"

    def test_falls_back_to_raw(self) -> None:
        payload = parse_guidance("freeform text")
        assert payload.explanation.startswith("freeform text")


class TestParseByContract:
    def test_dispatches_to_patch(self) -> None:
        result = parse_by_contract("patch", "ACTION: NO_PATCH\n")
        assert isinstance(result, PatchPayload)

    def test_dispatches_to_diagnosis(self) -> None:
        result = parse_by_contract("diagnosis", "CAN_DROP: YES\n")
        assert isinstance(result, DiagnosisPayload)
        assert result.can_drop

    def test_dispatches_to_dispatch(self) -> None:
        result = parse_by_contract("dispatch", "SKILL: build-patch\n")
        assert isinstance(result, DispatchPayload)
        assert result.skill == "build-patch"

    def test_dispatches_to_guidance(self) -> None:
        result = parse_by_contract("guidance", "EXPLANATION: hi\n")
        assert isinstance(result, GuidancePayload)

    def test_unknown_contract(self) -> None:
        with pytest.raises(UnknownContractError):
            parse_by_contract("not-a-contract", "whatever")

    def test_known_contracts_sorted(self) -> None:
        contracts = known_contracts()
        assert contracts == sorted(contracts)
        assert set(contracts) == {"patch", "diagnosis", "dispatch", "guidance"}
