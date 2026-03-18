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

"""AI-powered build failure diagnosis.

Analyses sbuild failure logs by sending log excerpts, debian/control,
and debian/rules to Claude, which proposes either a source patch (with
DEP3 headers) or a text explanation of the failure.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packastack.ai.client import AIResponse, call_ai, is_ai_available
from packastack.ai.prompts import (
    BUILD_DIAGNOSIS_SYSTEM,
    PATCH_CORRECTION_SYSTEM,
    build_sbuild_context,
    extract_sbuild_failure_section,
)

if TYPE_CHECKING:
    from packastack.build.sbuild import SbuildResult


@dataclass
class BuildDiagnosisResult:
    """Result of AI build failure diagnosis."""

    diagnosed: bool
    needs_patch: bool = False
    needs_debian_edit: bool = False
    patch_filename: str = ""
    patch_content: str = ""
    debian_edits: dict[str, str] = field(default_factory=dict)
    explanation: str = ""
    error: str = ""


def _parse_build_response(response: AIResponse) -> BuildDiagnosisResult:
    """Parse the structured AI response into a BuildDiagnosisResult.

    Expected format from the AI:

        DIAGNOSIS: <one-line summary>
        ACTION: DEBIAN_EDIT | QUILT_PATCH | PATCH | NO_PATCH
        EXPLANATION: <multi-line explanation>

        If ACTION is DEBIAN_EDIT:
        --- BEGIN DEBIAN EDIT: debian/rules ---
        <full replacement content>
        --- END DEBIAN EDIT ---

        If ACTION is QUILT_PATCH (or PATCH for backward compat):
        PATCH_FILENAME: <name>.patch
        --- BEGIN PATCH ---
        <patch content>
        --- END PATCH ---

    Args:
        response: Successful AIResponse from call_ai.

    Returns:
        Parsed BuildDiagnosisResult.
    """
    content = response.content
    result = BuildDiagnosisResult(diagnosed=True)

    lines = content.splitlines()
    for line in lines:
        line_stripped = line.strip()
        if line_stripped.startswith("ACTION:"):
            value = line_stripped.split(":", 1)[1].strip().upper()
            if value == "DEBIAN_EDIT":
                # AI is not permitted to edit debian/ files directly.
                # Treat this as "no automated fix" — the explanation
                # will still describe what the maintainer should do.
                pass
            elif value in ("PATCH", "QUILT_PATCH"):
                result.needs_patch = True
        elif line_stripped.startswith("EXPLANATION:"):
            result.explanation = line_stripped.split(":", 1)[1].strip()
        elif line_stripped.startswith("DIAGNOSIS:"):
            diag = line_stripped.split(":", 1)[1].strip()
            if not result.explanation:
                result.explanation = diag
        elif line_stripped.startswith("PATCH_FILENAME:"):
            result.patch_filename = line_stripped.split(":", 1)[1].strip()

    # Extract debian edit blocks
    if result.needs_debian_edit:
        result.debian_edits = _extract_debian_edits(content)
        if not result.debian_edits:
            result.needs_debian_edit = False
            if not result.explanation:
                result.explanation = (
                    "AI suggested debian edits but the response was incomplete"
                )

    # Extract patch content between markers
    if result.needs_patch:
        begin_marker = "--- BEGIN PATCH ---"
        end_marker = "--- END PATCH ---"
        begin_idx = content.find(begin_marker)
        end_idx = content.find(end_marker)
        if begin_idx != -1 and end_idx != -1 and end_idx > begin_idx:
            patch_start = begin_idx + len(begin_marker)
            result.patch_content = content[patch_start:end_idx].strip()

        # Validate we have required fields
        if not result.patch_filename or not result.patch_content:
            result.needs_patch = False
            if not result.explanation:
                result.explanation = (
                    "AI suggested a patch but the response was incomplete"
                )

    # Fallback: use full response if no structured fields found
    if not result.explanation:
        result.explanation = content[:500]

    return result


def _extract_debian_edits(content: str) -> dict[str, str]:
    """Extract debian file edits from structured AI response.

    Looks for blocks delimited by::

        --- BEGIN DEBIAN EDIT: <path> ---
        <content>
        --- END DEBIAN EDIT ---

    All paths must start with ``debian/``.

    Args:
        content: Full AI response text.

    Returns:
        Dict mapping relative paths (e.g. ``"debian/rules"``) to their
        new file content.
    """
    edits: dict[str, str] = {}
    begin_prefix = "--- BEGIN DEBIAN EDIT:"
    end_marker = "--- END DEBIAN EDIT ---"

    search_start = 0
    while True:
        begin_idx = content.find(begin_prefix, search_start)
        if begin_idx == -1:
            break
        # Extract the file path from the marker line
        marker_end = content.find("---", begin_idx + len(begin_prefix))
        if marker_end == -1:
            break
        file_path = content[begin_idx + len(begin_prefix) : marker_end].strip()

        # Validate path is under debian/
        if not file_path.startswith("debian/"):
            search_start = marker_end + 3
            continue

        # Find the content start (after the marker line's newline)
        content_start = content.find("\n", marker_end)
        if content_start == -1:
            break
        content_start += 1  # skip the newline

        end_idx = content.find(end_marker, content_start)
        if end_idx == -1:
            break

        # Strip one leading/trailing newline but preserve internal formatting
        file_content = content[content_start:end_idx]
        if file_content.startswith("\n"):
            file_content = file_content[1:]
        if file_content.endswith("\n"):
            file_content = file_content[:-1]

        edits[file_path] = file_content
        search_start = end_idx + len(end_marker)

    return edits


def _read_file_safe(path: Path, max_lines: int = 0) -> str:
    """Read a file safely, returning empty string on error.

    Args:
        path: File to read.
        max_lines: Maximum number of lines to return.  ``0`` means
            no limit (return the entire file).

    Returns:
        File content as string, optionally truncated to *max_lines*.
    """
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        if max_lines > 0:
            lines = content.splitlines()
            if len(lines) > max_lines:
                lines = [*lines[:max_lines], f"... ({len(lines) - max_lines} lines truncated)"]
                return "\n".join(lines)
        return content
    except OSError:
        return ""


def collect_working_tree_context(
    pkg_repo: Path,
    max_file_lines: int = 0,
) -> str:
    """Collect the working git tree listing and key file contents.

    Provides the AI with enough context to produce correct patches by
    including:

    1. A complete file listing from ``git ls-tree``
    2. Full contents of all ``debian/`` files (rules, control,
       changelog header, patches/series, etc.)
    3. Key upstream configuration files (setup.py, setup.cfg,
       pyproject.toml, tox.ini) when present

    Args:
        pkg_repo: Path to the packaging repository.
        max_file_lines: Maximum lines per file.  ``0`` means no limit.

    Returns:
        Formatted string with tree listing and file contents.
    """
    sections: list[str] = []

    # 1. Git tree listing
    tree_listing = _git_ls_tree(pkg_repo)
    if tree_listing:
        sections.append(f"== File tree ==\n{tree_listing}")

    # 2. All debian/ files (these are what the AI can directly edit)
    debian_dir = pkg_repo / "debian"
    if debian_dir.is_dir():
        for dfile in sorted(debian_dir.rglob("*")):
            if not dfile.is_file():
                continue
            rel = dfile.relative_to(pkg_repo)
            # Skip binary files and large patch files
            if _is_binary_path(dfile):
                sections.append(f"== {rel} ==\n(binary file, omitted)")
                continue
            content = _read_file_safe(dfile, max_lines=max_file_lines)
            if content:
                sections.append(f"== {rel} ==\n{content}")

    # 3. Key upstream config files (when they exist in the tree)
    for upstream_file in [
        "setup.py",
        "setup.cfg",
        "pyproject.toml",
        "tox.ini",
        "Makefile",
    ]:
        fpath = pkg_repo / upstream_file
        if fpath.is_file():
            content = _read_file_safe(fpath, max_lines=max_file_lines)
            if content:
                sections.append(f"== {upstream_file} ==\n{content}")

    return "\n\n".join(sections)


def _git_ls_tree(pkg_repo: Path) -> str:
    """Run ``git ls-tree -r --name-only HEAD`` and return the output.

    Args:
        pkg_repo: Path to the git repository.

    Returns:
        Newline-separated file listing, or empty string on error.
    """
    try:
        result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "HEAD"],
            cwd=pkg_repo,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return ""


def _is_binary_path(path: Path) -> bool:
    """Check if a file is likely binary based on extension.

    Args:
        path: File path to check.

    Returns:
        True if the file has a known binary extension.
    """
    binary_suffixes = {
        ".gz", ".xz", ".bz2", ".zip", ".tar",
        ".png", ".jpg", ".gif", ".ico",
        ".pyc", ".so", ".o", ".a",
        ".gpg", ".asc", ".der",
    }
    return path.suffix.lower() in binary_suffixes


def diagnose_build_failure(
    sbuild_result: SbuildResult,
    pkg_repo: Path,
    pkg_name: str,
    version: str,
    ubuntu_series: str,
    arch: str,
    cfg: dict[str, Any],
    ai_memory_context: str = "",
) -> BuildDiagnosisResult:
    """Ask AI to diagnose an sbuild failure and optionally propose a fix.

    Extracts the relevant failure section from the sbuild log, collects
    the full working tree context (debian/ files, key upstream configs),
    and sends everything to the AI.  The AI may propose either a direct
    debian/ file edit or a quilt patch for upstream source changes.

    Args:
        sbuild_result: Result from run_sbuild() containing log paths.
        pkg_repo: Path to the packaging repository.
        pkg_name: Debian package name.
        version: Package version.
        ubuntu_series: Target Ubuntu distribution.
        arch: Build architecture.
        cfg: Packastack configuration dictionary.
        ai_memory_context: Optional formatted string of previous AI
            attempts for this package (from AI memory).

    Returns:
        BuildDiagnosisResult with diagnosis and optional fix.
    """
    if not is_ai_available(cfg):
        return BuildDiagnosisResult(
            diagnosed=False, error="AI not available (no API key)"
        )

    # Configurable truncation limits (0 = unlimited)
    ai_cfg = cfg.get("ai", {})
    max_log_lines = ai_cfg.get("max_log_lines", 0)
    max_file_lines = ai_cfg.get("max_file_lines", 0)

    # Extract failure section from sbuild log
    log_excerpt = ""
    for log_path in [
        sbuild_result.primary_log_path,
        sbuild_result.stderr_log_path,
        sbuild_result.stdout_log_path,
    ]:
        if log_path and log_path.exists():
            log_excerpt = extract_sbuild_failure_section(
                log_path, max_lines=max_log_lines
            )
            if log_excerpt:
                break

    if not log_excerpt:
        return BuildDiagnosisResult(
            diagnosed=False, error="No build log available for analysis"
        )

    # Read debian packaging files for context
    control_content = _read_file_safe(
        pkg_repo / "debian" / "control", max_lines=max_file_lines
    )
    rules_content = _read_file_safe(
        pkg_repo / "debian" / "rules", max_lines=max_file_lines
    )

    # Collect full working tree context
    tree_context = collect_working_tree_context(pkg_repo, max_file_lines)

    user_message = build_sbuild_context(
        log_excerpt=log_excerpt,
        control_content=control_content,
        rules_content=rules_content,
        pkg_name=pkg_name,
        version=version,
        ubuntu_series=ubuntu_series,
        arch=arch,
        error_msg=sbuild_result.validation_message,
        ai_memory_context=ai_memory_context,
        working_tree_context=tree_context,
    )

    response = call_ai(BUILD_DIAGNOSIS_SYSTEM, user_message, cfg)

    if not response.success:
        return BuildDiagnosisResult(
            diagnosed=False,
            error=f"AI call failed: {response.error}",
        )

    return _parse_build_response(response)


@dataclass
class PatchValidationResult:
    """Result of validating whether a patch applies cleanly."""

    valid: bool
    error: str = ""


def validate_patch(
    pkg_repo: Path,
    patch_content: str,
    patch_filename: str,
) -> PatchValidationResult:
    """Validate that a patch applies cleanly to the repository.

    Writes the patch to a temporary file in ``debian/patches/`` and
    runs ``git apply --check`` to verify it would apply without errors.
    The temporary file is removed after the check.

    Args:
        pkg_repo: Path to the packaging repository.
        patch_content: Content of the unified diff patch.
        patch_filename: Filename for the patch.

    Returns:
        PatchValidationResult indicating whether the patch is valid.
    """
    from packastack.debpkg.gbp import run_command

    patches_dir = pkg_repo / "debian" / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)
    patch_file = patches_dir / patch_filename

    try:
        patch_file.write_text(patch_content + "\n", encoding="utf-8")

        rc, stdout, stderr = run_command(
            ["git", "apply", "--check", str(patch_file)],
            cwd=pkg_repo,
        )

        if rc == 0:
            return PatchValidationResult(valid=True)
        return PatchValidationResult(
            valid=False,
            error=stderr or stdout or f"git apply --check exited with code {rc}",
        )
    except OSError as exc:
        return PatchValidationResult(valid=False, error=f"File I/O error: {exc}")
    finally:
        if patch_file.exists():
            patch_file.unlink(missing_ok=True)


def _request_patch_correction(
    original_diagnosis: BuildDiagnosisResult,
    validation_error: str,
    cfg: dict[str, Any],
) -> BuildDiagnosisResult | None:
    """Ask AI to correct a patch that failed validation.

    Args:
        original_diagnosis: The original diagnosis with the invalid patch.
        validation_error: Error message from ``git apply --check``.
        cfg: Packastack configuration dictionary.

    Returns:
        New BuildDiagnosisResult with corrected patch, or ``None`` on failure.
    """
    if not is_ai_available(cfg):
        return None

    user_message = (
        f"Your previous patch '{original_diagnosis.patch_filename}' "
        f"failed validation.\n\n"
        f"== git apply --check error ==\n{validation_error}\n\n"
        f"== Original patch ==\n{original_diagnosis.patch_content}\n\n"
        f"== Original diagnosis ==\n{original_diagnosis.explanation}\n\n"
        "Please produce a corrected patch that applies cleanly.\n"
    )

    response = call_ai(PATCH_CORRECTION_SYSTEM, user_message, cfg)
    if not response.success:
        return None
    return _parse_build_response(response)


def apply_ai_patch(
    pkg_repo: Path,
    diagnosis: BuildDiagnosisResult,
    cfg: dict[str, Any] | None = None,
    max_correction_attempts: int = 1,
) -> bool:
    """Apply an AI-generated patch to the packaging repository.

    First validates the patch applies cleanly with ``git apply --check``.
    If validation fails and *cfg* is provided, asks the AI for a
    corrected version (up to *max_correction_attempts* times).  Only
    writes the patch file, updates the series, and commits when the
    patch passes validation.

    Args:
        pkg_repo: Path to the packaging repository.
        diagnosis: BuildDiagnosisResult containing the patch.
        cfg: Optional config dict for AI correction calls.
        max_correction_attempts: Maximum correction retries (default 1).

    Returns:
        True if the patch was applied and committed successfully.
    """
    if not diagnosis.patch_filename or not diagnosis.patch_content:
        return False

    current_content = diagnosis.patch_content
    current_filename = diagnosis.patch_filename

    # Validate before committing
    validation = validate_patch(pkg_repo, current_content, current_filename)

    # If invalid, attempt AI-powered correction
    attempts = 0
    while not validation.valid and cfg and attempts < max_correction_attempts:
        corrected = _request_patch_correction(
            original_diagnosis=BuildDiagnosisResult(
                diagnosed=True,
                needs_patch=True,
                patch_filename=current_filename,
                patch_content=current_content,
                explanation=diagnosis.explanation,
            ),
            validation_error=validation.error,
            cfg=cfg,
        )
        if corrected and corrected.needs_patch and corrected.patch_content:
            current_content = corrected.patch_content
            current_filename = corrected.patch_filename or current_filename
            validation = validate_patch(pkg_repo, current_content, current_filename)
        else:
            break
        attempts += 1

    if not validation.valid:
        return False

    # Write the validated patch and commit
    patches_dir = pkg_repo / "debian" / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)

    patch_file = patches_dir / current_filename
    series_file = patches_dir / "series"

    try:
        patch_file.write_text(current_content + "\n", encoding="utf-8")

        # Read existing series content to avoid duplicates
        existing_series = ""
        if series_file.exists():
            existing_series = series_file.read_text(encoding="utf-8")

        if current_filename not in existing_series:
            # Ensure newline before appending
            separator = "" if existing_series.endswith("\n") or not existing_series else "\n"
            with series_file.open("a", encoding="utf-8") as f:
                f.write(f"{separator}{current_filename}\n")

        # Stage the new files for the source build
        from packastack.debpkg.gbp import run_command

        run_command(["git", "add", str(patch_file), str(series_file)], cwd=pkg_repo)
        run_command(
            [
                "git",
                "commit",
                "-m",
                f"d/patches: add AI-generated {current_filename}",
            ],
            cwd=pkg_repo,
        )

        return True
    except OSError:
        return False


def apply_debian_edits(
    pkg_repo: Path,
    diagnosis: BuildDiagnosisResult,
) -> bool:
    """Apply direct debian/ file edits from an AI diagnosis.

    Writes each file listed in ``diagnosis.debian_edits`` directly into
    the packaging repository and commits the changes.  Only files under
    ``debian/`` are accepted; any other paths are silently skipped.

    Args:
        pkg_repo: Path to the packaging repository.
        diagnosis: BuildDiagnosisResult containing ``debian_edits``.

    Returns:
        True if at least one file was written and committed.
    """
    if not diagnosis.debian_edits:
        return False

    written_files: list[str] = []
    for rel_path, content in diagnosis.debian_edits.items():
        # Safety: only allow debian/ files
        if not rel_path.startswith("debian/"):
            continue
        target = pkg_repo / rel_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Ensure file ends with a newline
            if not content.endswith("\n"):
                content += "\n"
            target.write_text(content, encoding="utf-8")
            written_files.append(str(target))
        except OSError:
            continue

    if not written_files:
        return False

    try:
        from packastack.debpkg.gbp import run_command

        run_command(["git", "add", *written_files], cwd=pkg_repo)

        edited = ", ".join(
            diagnosis.debian_edits.keys()
        )
        run_command(
            [
                "git",
                "commit",
                "-m",
                f"d/: AI-applied edit to {edited}",
            ],
            cwd=pkg_repo,
        )
        return True
    except OSError:
        return False


def apply_ai_fix(
    pkg_repo: Path,
    diagnosis: BuildDiagnosisResult,
    cfg: dict[str, Any] | None = None,
    max_correction_attempts: int = 1,
) -> bool:
    """Apply an AI-proposed fix, choosing the right strategy automatically.

    If the diagnosis contains debian edits (``needs_debian_edit``), applies
    them directly.  If it contains a quilt patch (``needs_patch``), uses
    :func:`apply_ai_patch`.  This is the preferred entry point for callers.

    Args:
        pkg_repo: Path to the packaging repository.
        diagnosis: BuildDiagnosisResult from :func:`diagnose_build_failure`.
        cfg: Optional config dict for AI correction calls.
        max_correction_attempts: Maximum correction retries for patches.

    Returns:
        True if the fix was applied and committed successfully.
    """
    # AI is restricted to patch-only changes.  Debian file edits
    # (rules, control, etc.) are left to the human maintainer.
    if diagnosis.needs_patch:
        return apply_ai_patch(pkg_repo, diagnosis, cfg, max_correction_attempts)
    return False
