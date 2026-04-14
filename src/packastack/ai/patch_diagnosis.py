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

import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packastack.ai.client import AIResponse, call_ai, is_ai_available
from packastack.ai.prompts import (
    build_patch_context,
    build_patch_refresh_context,
)
from packastack.ai.skills import load_skill

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

    response = call_ai(load_skill("patch-diagnosis").system_prompt, user_message, cfg)

    if not response.success:
        return PatchDiagnosisResult(
            diagnosed=False,
            error=f"AI call failed: {response.error}",
        )

    result = _parse_patch_response(response)
    result.patch_name = patch_name
    return result


@dataclass
class PatchRefreshResult:
    """Result of an AI patch refresh attempt."""

    refreshed: bool
    patch_name: str = ""
    patch_content: str = ""
    explanation: str = ""
    error: str = ""


def _parse_refresh_response(response: AIResponse, patch_name: str) -> PatchRefreshResult:
    """Parse the structured AI response for a patch refresh.

    Args:
        response: Successful AIResponse from call_ai.
        patch_name: Original patch filename (used as fallback).

    Returns:
        Parsed PatchRefreshResult.
    """
    content = response.content
    result = PatchRefreshResult(refreshed=False, patch_name=patch_name)

    action = ""
    for line in content.splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("ACTION:"):
            action = line_stripped.split(":", 1)[1].strip().upper()
        elif line_stripped.startswith("EXPLANATION:"):
            result.explanation = line_stripped.split(":", 1)[1].strip()
        elif line_stripped.startswith("DIAGNOSIS:"):
            diag = line_stripped.split(":", 1)[1].strip()
            if not result.explanation:
                result.explanation = diag
        elif line_stripped.startswith("PATCH_FILENAME:"):
            result.patch_name = line_stripped.split(":", 1)[1].strip()

    if action == "NO_REFRESH":
        return result

    # Extract patch content between markers
    begin_marker = "--- BEGIN PATCH ---"
    end_marker = "--- END PATCH ---"
    begin_idx = content.find(begin_marker)
    end_idx = content.find(end_marker)
    if begin_idx != -1 and end_idx != -1 and end_idx > begin_idx:
        patch_start = begin_idx + len(begin_marker)
        # Strip leading whitespace but preserve the trailing newline —
        # unified diffs must end with a newline or git will report
        # "corrupt patch".
        raw = content[patch_start:end_idx].strip()
        if raw:
            result.patch_content = raw + "\n"
            result.refreshed = True

    if not result.explanation:
        result.explanation = content[:500]

    return result


def _extract_dep3_header(patch_content: str) -> str:
    """Extract the DEP3 header from a quilt patch.

    Returns everything before the first ``diff --git`` or ``diff -``
    line, which is the DEP3 metadata (From, Date, Subject, Bug,
    Forwarded, etc.) and the optional free-form description.

    Args:
        patch_content: Full content of the patch file.

    Returns:
        The header text including its trailing newline, or an empty
        string if no header is present.
    """
    lines = patch_content.splitlines(keepends=True)
    header_lines: list[str] = []
    for line in lines:
        if line.startswith("diff --git ") or line.startswith("diff -"):
            break
        header_lines.append(line)
    return "".join(header_lines)


def _get_deleted_files(patch_content: str) -> list[str]:
    """Return file paths that the patch deletes, or empty if it does anything else.

    A patch is considered a pure file-deletion patch when every ``diff``
    section has ``deleted file mode`` and ``+++ /dev/null``.  If any
    hunk adds or modifies lines, the patch is not a pure deletion.

    Args:
        patch_content: Full content of the patch file.

    Returns:
        List of file paths being deleted, or empty list if the patch
        is not a pure file-deletion patch.
    """
    deleted: list[str] = []
    in_diff = False
    has_deleted_mode = False
    current_path = ""

    for line in patch_content.splitlines():
        if line.startswith("diff --git "):
            # Finish previous diff section
            if in_diff and has_deleted_mode and current_path:
                deleted.append(current_path)
            elif in_diff and not has_deleted_mode:
                return []  # Non-deletion diff found
            in_diff = True
            has_deleted_mode = False
            current_path = ""
        elif line.startswith("deleted file mode"):
            has_deleted_mode = True
        elif line.startswith("--- a/"):
            current_path = line[6:].strip()
        elif in_diff and not has_deleted_mode and line.startswith("@@ "):
            # A hunk in a non-deletion section
            return []

    # Handle last diff section
    if in_diff and has_deleted_mode and current_path:
        deleted.append(current_path)
    elif in_diff and not has_deleted_mode:
        return []

    return deleted


def _try_file_deletion_refresh(
    patch_name: str,
    original_content: str,
    pkg_repo: Path,
) -> PatchRefreshResult | None:
    """Attempt to refresh a pure file-deletion patch.

    When a patch only deletes files, and those files still exist but
    have different content (e.g. upstream changed a few lines), this
    regenerates the deletion diff from the current file contents.

    Args:
        patch_name: Name of the patch file.
        original_content: Full content of the original patch.
        pkg_repo: Path to the packaging repository.

    Returns:
        A successful PatchRefreshResult if the patch was refreshed,
        or None if this strategy doesn't apply.
    """
    from packastack.debpkg.gbp import run_command

    deleted_files = _get_deleted_files(original_content)
    if not deleted_files:
        return None

    # All targeted files must still exist for this strategy to work
    for fpath in deleted_files:
        if not (pkg_repo / fpath).is_file():
            return None

    # Stage file deletions and capture the diff
    for fpath in deleted_files:
        rc, _, _stderr = run_command(
            ["git", "rm", "--quiet", fpath], cwd=pkg_repo,
        )
        if rc != 0:
            _revert_working_tree(pkg_repo)
            return None

    _, diff_output, _ = run_command(
        ["git", "diff", "--cached", "HEAD"], cwd=pkg_repo,
    )

    # Revert
    _revert_working_tree(pkg_repo)

    if not diff_output.strip():
        return None

    # Rebuild patch with original DEP3 header + new diff body
    header = _extract_dep3_header(original_content)
    refreshed_content = header + diff_output

    # Validate
    from packastack.ai.build_diagnosis import validate_patch

    tmp_name = f".tmp-refresh-{patch_name}"
    validation = validate_patch(pkg_repo, refreshed_content, tmp_name)
    if not validation.valid:
        return None

    return PatchRefreshResult(
        refreshed=True,
        patch_name=patch_name,
        patch_content=refreshed_content,
        explanation=(
            "Mechanically refreshed (file-deletion): "
            "regenerated deletion diff from current upstream file contents"
        ),
    )


def attempt_mechanical_refresh(
    patch_name: str,
    patch_path: Path,
    pkg_repo: Path,
) -> PatchRefreshResult:
    """Try to refresh a patch mechanically without AI involvement.

    When a quilt patch fails strict application (e.g. due to whitespace
    changes or line-number offsets in context lines), this function
    attempts to apply it with progressively relaxed matching, then
    regenerates a clean patch from the actual result.

    Strategies tried in order:

    1. ``git apply --ignore-whitespace`` — handles whitespace-only
       context differences (e.g. indentation changes).
    2. ``git apply --3way`` — builds a fake ancestor from the index
       lines embedded in the patch and performs a three-way merge.
    3. ``patch -p1 -l --fuzz=3`` — GNU patch with loose whitespace
       matching and generous fuzz (up to 3 context lines ignored).

    On success the patch is applied, a clean diff is captured via
    ``git diff``, DEP3 headers from the original are preserved, and
    the working tree is reverted to its previous state.

    Args:
        patch_name: Name of the failing patch file.
        patch_path: Absolute path to the patch file.
        pkg_repo: Path to the packaging repository.

    Returns:
        PatchRefreshResult with refreshed patch content on success.
    """
    from packastack.debpkg.gbp import run_command

    # Read original patch to preserve DEP3 header
    try:
        original = patch_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return PatchRefreshResult(
            refreshed=False,
            patch_name=patch_name,
            error=f"Cannot read patch file: {exc}",
        )

    # First: if strict git apply already passes, the patch doesn't need
    # refreshing (it might fail during gbp-pq only because an earlier
    # patch in the series couldn't be applied).
    strict_rc, _, _ = run_command(
        ["git", "apply", "--check", str(patch_path)],
        cwd=pkg_repo,
    )
    if strict_rc == 0:
        return PatchRefreshResult(
            refreshed=False,
            patch_name=patch_name,
            error="Patch already applies cleanly (issue is in an earlier patch)",
        )

    # Special case: file deletion patches.  When a patch only deletes
    # files that still exist, the content may have changed upstream
    # causing the old deletion hunks to mismatch.  Regenerate the
    # deletion diff from the current file contents.
    deletion_result = _try_file_deletion_refresh(
        patch_name, original, pkg_repo
    )
    if deletion_result is not None:
        return deletion_result

    strategies: list[tuple[str, list[str], list[str]]] = [
        # (name, check_cmd, apply_cmd)
        (
            "git-ignore-ws",
            [
                "git", "apply", "--check", "--ignore-whitespace",
                str(patch_path),
            ],
            ["git", "apply", "--ignore-whitespace", str(patch_path)],
        ),
        (
            "git-3way",
            # --3way --check always exits 0 even with conflicts, so we
            # skip the check and just try applying.
            [],
            ["git", "apply", "--3way", str(patch_path)],
        ),
        (
            "patch-fuzz",
            [
                "patch", "-p1", "--dry-run", "-l", "--fuzz=3",
                "--no-backup-if-mismatch", "--force",
                "--input", str(patch_path),
            ],
            [
                "patch", "-p1", "-l", "--fuzz=3",
                "--no-backup-if-mismatch", "--force",
                "--input", str(patch_path),
            ],
        ),
    ]

    errors: list[str] = []
    for strategy_name, check_cmd, apply_cmd in strategies:
        # Dry-run check (when available)
        if check_cmd:
            rc, _, stderr = run_command(check_cmd, cwd=pkg_repo)
            if rc != 0:
                errors.append(f"{strategy_name}: check failed: {stderr[:200]}")
                continue

        # Apply for real
        rc, _stdout, stderr = run_command(apply_cmd, cwd=pkg_repo)
        if rc != 0:
            _revert_working_tree(pkg_repo)
            errors.append(f"{strategy_name}: apply failed: {stderr[:200]}")
            continue

        # Check for merge conflicts left by --3way
        _rc_status, status_out, _ = run_command(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=pkg_repo,
        )
        if status_out.strip():
            # Unresolved conflicts — revert and try next strategy
            _revert_working_tree(pkg_repo)
            errors.append(
                f"{strategy_name}: merge conflicts in "
                f"{status_out.strip()}"
            )
            continue

        # Stage all changes (including new/deleted files)
        run_command(["git", "add", "-A"], cwd=pkg_repo)

        # Capture clean diff
        _, diff_output, _ = run_command(
            ["git", "diff", "--cached", "HEAD"],
            cwd=pkg_repo,
        )

        # Revert everything
        _revert_working_tree(pkg_repo)

        if not diff_output.strip():
            errors.append(f"{strategy_name}: empty diff after apply")
            continue

        # Rebuild patch: DEP3 header + clean diff body
        header = _extract_dep3_header(original)
        refreshed_content = header + diff_output

        # Final sanity check: does the refreshed patch pass strict
        # git apply --check?  Use a temporary filename so we don't
        # overwrite the original patch file.
        from packastack.ai.build_diagnosis import validate_patch

        tmp_name = f".tmp-refresh-{patch_name}"
        validation = validate_patch(pkg_repo, refreshed_content, tmp_name)
        if not validation.valid:
            errors.append(
                f"{strategy_name}: regenerated patch fails strict check: "
                f"{validation.error}"
            )
            continue

        return PatchRefreshResult(
            refreshed=True,
            patch_name=patch_name,
            patch_content=refreshed_content,
            explanation=(
                f"Mechanically refreshed ({strategy_name}): "
                f"context lines and offsets updated to match "
                f"current upstream source"
            ),
        )

    return PatchRefreshResult(
        refreshed=False,
        patch_name=patch_name,
        error=(
            "All mechanical refresh strategies failed: "
            + "; ".join(errors)
        ),
    )


def _revert_working_tree(pkg_repo: Path) -> None:
    """Reset the working tree to HEAD, removing any staged or unstaged changes.

    Preserves the ``debian/`` directory since it contains packaging
    files (including the patches being processed).

    Args:
        pkg_repo: Path to the git repository.
    """
    from packastack.debpkg.gbp import run_command

    run_command(["git", "reset", "--hard", "HEAD"], cwd=pkg_repo)
    run_command(["git", "clean", "-fd", "-e", "debian/"], cwd=pkg_repo)


def _extract_affected_paths(patch_content: str) -> list[str]:
    """Extract file paths modified by a unified diff patch.

    Parses ``--- a/path`` and ``+++ b/path`` header lines to find
    all files that the patch touches.

    Args:
        patch_content: Full content of the patch file.

    Returns:
        Deduplicated list of file paths (without ``a/`` or ``b/``
        prefix).
    """
    paths: list[str] = []
    for line in patch_content.splitlines():
        for prefix in ("--- a/", "+++ b/"):
            if line.startswith(prefix):
                p = line[len(prefix):].strip()
                if p and p != "/dev/null" and p not in paths:
                    paths.append(p)
    return paths


def refresh_failing_patch(
    patch_name: str,
    patch_content: str,
    pq_output: str,
    pkg_repo: Path,
    pkg_name: str,
    version: str,
    cfg: dict[str, Any],
    max_correction_attempts: int = 1,
) -> PatchRefreshResult:
    """Ask AI to refresh a patch that fails to apply against new upstream.

    Reads the current contents of every file the patch modifies so the
    AI can produce a correct refreshed version. The refreshed patch is
    validated with ``git apply --check`` before being accepted. If
    validation fails, up to *max_correction_attempts* correction rounds
    are tried.

    Args:
        patch_name: Name of the failing patch file.
        patch_content: Full content of the original patch.
        pq_output: Output from gbp pq import showing the failure.
        pkg_repo: Path to the packaging repository.
        pkg_name: Debian package name.
        version: Upstream version being imported.
        cfg: Packastack configuration dictionary.
        max_correction_attempts: Extra correction rounds if the
            refreshed patch fails ``git apply --check``.

    Returns:
        PatchRefreshResult with the refreshed patch content on success.
    """
    if not is_ai_available(cfg):
        return PatchRefreshResult(
            refreshed=False, error="AI not available (no API key)"
        )

    # Collect full working tree context (git tree, debian/, upstream configs)
    from packastack.ai.build_diagnosis import collect_working_tree_context

    ai_cfg = cfg.get("ai", {})
    max_file_lines = ai_cfg.get("max_file_lines", 0)
    tree_context = collect_working_tree_context(pkg_repo, max_file_lines)

    # Collect current contents of files the patch modifies
    affected_paths = _extract_affected_paths(patch_content)
    affected_files: dict[str, str] = {}
    missing_files: list[str] = []
    for fpath in affected_paths:
        full = pkg_repo / fpath
        if full.is_file():
            with contextlib.suppress(OSError):
                affected_files[fpath] = full.read_text(
                    encoding="utf-8", errors="replace"
                )
        else:
            missing_files.append(fpath)

    # Include pyproject.toml when the patch touches setup.cfg or setup.py.
    # Many OpenStack projects have migrated metadata to pyproject.toml, so
    # the AI needs to see it to retarget hunks — whether setup.cfg is
    # missing entirely or just gutted (content moved but file kept).
    _setup_files = {"setup.cfg", "setup.py"}
    if any(f in _setup_files for f in affected_paths):
        pyproject = pkg_repo / "pyproject.toml"
        if pyproject.is_file() and "pyproject.toml" not in affected_files:
            with contextlib.suppress(OSError):
                affected_files["pyproject.toml"] = pyproject.read_text(
                    encoding="utf-8", errors="replace"
                )

    user_message = build_patch_refresh_context(
        patch_name=patch_name,
        patch_content=patch_content,
        error_output=pq_output,
        affected_files=affected_files,
        pkg_name=pkg_name,
        version=version,
        missing_files=missing_files or None,
        working_tree_context=tree_context,
    )

    response = call_ai(load_skill("patch-refresh").system_prompt, user_message, cfg)

    if not response.success:
        return PatchRefreshResult(
            refreshed=False,
            error=f"AI call failed: {response.error}",
        )

    result = _parse_refresh_response(response, patch_name)
    if not result.refreshed:
        return result

    # Validate the refreshed patch applies cleanly
    from packastack.ai.build_diagnosis import validate_patch

    validation = validate_patch(pkg_repo, result.patch_content, result.patch_name)
    if validation.valid:
        return result

    # Attempt correction rounds
    correction_prompt = load_skill("patch-correction").system_prompt

    current_content = result.patch_content
    for _ in range(max_correction_attempts):
        correction_msg = (
            f"Your refreshed patch '{result.patch_name}' failed validation.\n\n"
            f"== git apply --check error ==\n{validation.error}\n\n"
            f"== Your patch ==\n{current_content}\n\n"
            "Please produce a corrected patch that applies cleanly.\n"
        )
        correction_resp = call_ai(correction_prompt, correction_msg, cfg)
        if not correction_resp.success:
            break
        corrected = _parse_refresh_response(correction_resp, result.patch_name)
        if not corrected.refreshed:
            break
        current_content = corrected.patch_content
        validation = validate_patch(pkg_repo, current_content, result.patch_name)
        if validation.valid:
            result.patch_content = current_content
            result.explanation = corrected.explanation or result.explanation
            return result

    # All attempts failed
    result.refreshed = False
    result.error = f"Refreshed patch failed validation: {validation.error}"
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
