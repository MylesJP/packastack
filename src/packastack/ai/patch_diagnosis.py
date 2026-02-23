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

"""AI-powered patch failure diagnosis.

Analyses quilt/gbp patch application failures by sending the patch
content and error output to Claude, which determines whether the patch
has been upstreamed and can be safely dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packastack.ai.client import AIResponse, call_ai, is_ai_available
from packastack.ai.prompts import PATCH_DIAGNOSIS_SYSTEM, build_patch_context

if TYPE_CHECKING:
    from packastack.debpkg.gbp import PatchHealthReport


@dataclass
class PatchDiagnosisResult:
    """Result of AI patch failure diagnosis."""

    diagnosed: bool
    can_drop: bool = False
    patch_name: str = ""
    explanation: str = ""
    error: str = ""


def _parse_patch_response(response: AIResponse) -> PatchDiagnosisResult:
    """Parse the structured AI response into a PatchDiagnosisResult.

    Args:
        response: Successful AIResponse from call_ai.

    Returns:
        Parsed PatchDiagnosisResult.
    """
    content = response.content
    result = PatchDiagnosisResult(diagnosed=True)

    for line in content.splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("CAN_DROP:"):
            value = line_stripped.split(":", 1)[1].strip().upper()
            result.can_drop = value == "YES"
        elif line_stripped.startswith("EXPLANATION:"):
            result.explanation = line_stripped.split(":", 1)[1].strip()
        elif line_stripped.startswith("DIAGNOSIS:"):
            # Use diagnosis as explanation if no separate explanation found
            diag = line_stripped.split(":", 1)[1].strip()
            if not result.explanation:
                result.explanation = diag

    # If structured parsing found nothing, use the full response
    if not result.explanation:
        result.explanation = content[:500]

    return result


def diagnose_patch_failure(
    patch_name: str,
    patch_content: str,
    pq_output: str,
    pkg_name: str,
    version: str,
    ubuntu_series: str,
    cfg: dict[str, Any],
) -> PatchDiagnosisResult:
    """Ask AI to diagnose a patch application failure.

    Sends the patch content, error output, and package context to Claude
    to determine why the patch failed and whether it can be dropped.

    Args:
        patch_name: Name of the failing patch file.
        patch_content: Full content of the patch.
        pq_output: Output from gbp pq import showing the failure.
        pkg_name: Debian package name.
        version: Upstream version being imported.
        ubuntu_series: Target Ubuntu series.
        cfg: Packastack configuration dictionary.

    Returns:
        PatchDiagnosisResult with diagnosis details.
    """
    if not is_ai_available(cfg):
        return PatchDiagnosisResult(
            diagnosed=False, error="AI not available (no API key)"
        )

    user_message = build_patch_context(
        patch_name=patch_name,
        patch_content=patch_content,
        error_output=pq_output,
        pkg_name=pkg_name,
        version=version,
        ubuntu_series=ubuntu_series,
    )

    response = call_ai(PATCH_DIAGNOSIS_SYSTEM, user_message, cfg)

    if not response.success:
        return PatchDiagnosisResult(
            diagnosed=False,
            error=f"AI call failed: {response.error}",
        )

    result = _parse_patch_response(response)
    result.patch_name = patch_name
    return result


@dataclass
class AutoDropResult:
    """Result of attempting to auto-drop upstreamed patches."""

    dropped: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    all_dropped: bool = False


def auto_drop_upstreamed_patches(
    pkg_repo: Path,
    upstreamed_reports: list[PatchHealthReport],
    pkg_name: str,
    version: str,
    ubuntu_series: str,
    cfg: dict[str, Any],
) -> AutoDropResult:
    """Attempt to auto-drop patches that AI confirms are upstreamed.

    For each patch in *upstreamed_reports*:

    1. Read the patch content from ``debian/patches/``.
    2. Ask the AI whether it can be safely dropped.
    3. If confirmed: remove the patch file and series entry, add a
       ``d/changelog`` entry, and commit.

    All AI and I/O errors are caught so that this function never
    breaks the normal build flow.

    Args:
        pkg_repo: Path to the packaging repository.
        upstreamed_reports: Reports from ``check_upstreamed_patches()``.
        pkg_name: Debian source package name.
        version: Package version string.
        ubuntu_series: Target Ubuntu series.
        cfg: Packastack configuration dictionary.

    Returns:
        AutoDropResult listing which patches were dropped or skipped.
    """
    result = AutoDropResult()

    if not is_ai_available(cfg):
        result.errors.append("AI not available (no API key)")
        return result

    for report in upstreamed_reports:
        try:
            patch_path = pkg_repo / "debian" / "patches" / report.patch_name
            patch_content = ""
            if patch_path.exists():
                patch_content = patch_path.read_text(
                    encoding="utf-8", errors="replace"
                )

            diagnosis = diagnose_patch_failure(
                patch_name=report.patch_name,
                patch_content=patch_content,
                pq_output="git apply --check --reverse succeeded (patch is in upstream)",
                pkg_name=pkg_name,
                version=version,
                ubuntu_series=ubuntu_series,
                cfg=cfg,
            )

            if not diagnosis.diagnosed:
                result.errors.append(
                    f"{report.patch_name}: AI diagnosis failed: {diagnosis.error}"
                )
                continue

            if not diagnosis.can_drop:
                result.skipped.append(report.patch_name)
                continue

            # AI confirmed safe to drop -- remove the patch
            from packastack.debpkg.gbp import drop_patch

            drop = drop_patch(pkg_repo, report.patch_name)
            if not drop.success:
                result.errors.append(
                    f"{report.patch_name}: drop failed: {drop.error}"
                )
                continue

            # Commit the removal
            from packastack.build.git_helpers import git_commit

            commit_result = git_commit(
                pkg_repo,
                f"d/patches: drop upstreamed {report.patch_name}",
                files=["debian/patches"],
            )
            if commit_result.returncode != 0:
                result.errors.append(
                    f"{report.patch_name}: git commit failed: {commit_result.stderr}"
                )
                continue

            result.dropped.append(report.patch_name)

        except Exception as exc:
            result.errors.append(f"{report.patch_name}: unexpected error: {exc}")

    result.all_dropped = (
        len(result.dropped) == len(upstreamed_reports)
        and len(result.dropped) > 0
    )
    return result
