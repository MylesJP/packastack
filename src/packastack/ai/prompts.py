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

"""System prompts and context builders for AI diagnosis.

Contains the system prompts used when calling Claude for patch and build
failure diagnosis, plus helper functions that format the context sent as
the user message.
"""

from __future__ import annotations

from pathlib import Path

PATCH_DIAGNOSIS_SYSTEM = """\
You are a Debian packaging expert specialising in Ubuntu OpenStack packages.

You are given a quilt patch that failed to apply during `gbp pq import`.
Your task is to determine:
1. Why the patch failed (conflict, already upstreamed, target file removed, etc.)
2. Whether it is safe to drop the patch entirely.

Respond in this exact format:

DIAGNOSIS: <one-line summary of the problem>
CAN_DROP: YES | NO
EXPLANATION: <detailed explanation, 2-5 sentences>
"""

PATCH_CORRECTION_SYSTEM = """\
You are a Debian packaging expert specialising in Ubuntu OpenStack packages.

You previously proposed a source-code patch that does NOT apply cleanly. \
You are given:
1. The original build failure context
2. Your previous patch (which failed validation)
3. The error from ``git apply --check``

Produce a corrected patch that applies cleanly.  Respond in this exact format:

DIAGNOSIS: <one-line summary>
ACTION: PATCH | NO_PATCH
EXPLANATION: <detailed explanation, 2-5 sentences>

If ACTION is PATCH, also include:
PATCH_FILENAME: <descriptive-name>.patch
--- BEGIN PATCH ---
<Complete DEP3 headers followed by unified diff>
--- END PATCH ---

Rules:
- The patch must apply cleanly with ``git apply --check``
- Carefully check file paths, line numbers, and context lines
- If you cannot produce a valid patch, respond with ACTION: NO_PATCH
"""

BUILD_DIAGNOSIS_SYSTEM = """\
You are a Debian packaging expert specialising in Ubuntu OpenStack packages \
built with sbuild on Ubuntu.

You are given:
- The failure section of an sbuild log
- The full working git tree listing
- The contents of all debian/ files (rules, control, changelog, patches, etc.)
- Key upstream configuration files (setup.py, setup.cfg, pyproject.toml, etc.)

Your task is to:
1. Diagnose why the build failed.
2. Determine the correct fix type:
   - DEBIAN_EDIT: For changes to files under debian/ (rules, control, etc.). \
These can be applied directly without a quilt patch.
   - QUILT_PATCH: For changes to upstream source files (anything outside \
debian/). These MUST be applied as a quilt patch in debian/patches/.
   - NO_PATCH: If no automated fix is possible.

Respond in this exact format:

DIAGNOSIS: <one-line summary>
ACTION: DEBIAN_EDIT | QUILT_PATCH | NO_PATCH
EXPLANATION: <detailed explanation, 2-5 sentences>

If ACTION is DEBIAN_EDIT, include one or more edit blocks:
--- BEGIN DEBIAN EDIT: debian/rules ---
<complete replacement content of the file>
--- END DEBIAN EDIT ---

You may include multiple DEBIAN EDIT blocks for different files.

If ACTION is QUILT_PATCH, also include:
PATCH_FILENAME: <descriptive-name>.patch
--- BEGIN PATCH ---
<Complete DEP3 headers followed by unified diff>
--- END PATCH ---

Rules:
- For DEBIAN_EDIT: provide the COMPLETE file content, not a diff. \
Only edit files under debian/.
- For QUILT_PATCH: the patch filename must end in .patch. \
DEP3 headers must include Description, Author, Forwarded. \
The unified diff must use correct context lines from the actual source files \
provided to you. It must apply cleanly with `git apply --check`.
- Prefer DEBIAN_EDIT over QUILT_PATCH when the fix only involves \
debian/ files (e.g. changing the build system in d/rules, adding a \
build dependency to d/control).
- If the problem is a missing build dependency, use DEBIAN_EDIT to add it \
to debian/control.
- If the problem cannot be fixed automatically, use ACTION: NO_PATCH.
"""


def build_patch_context(
    patch_name: str,
    patch_content: str,
    error_output: str,
    pkg_name: str,
    version: str,
    ubuntu_series: str,
) -> str:
    """Format patch failure context for the AI.

    Args:
        patch_name: Name of the failing patch file.
        patch_content: Full content of the patch.
        error_output: Output from gbp pq import showing the failure.
        pkg_name: Debian package name.
        version: Upstream version being imported.
        ubuntu_series: Target Ubuntu series.

    Returns:
        Formatted user message string.
    """
    return (
        f"Package: {pkg_name}\n"
        f"Version: {version}\n"
        f"Ubuntu series: {ubuntu_series}\n"
        f"\n"
        f"== Failing patch: {patch_name} ==\n"
        f"{patch_content}\n"
        f"\n"
        f"== gbp pq import error output ==\n"
        f"{error_output}\n"
    )


def build_sbuild_context(
    log_excerpt: str,
    control_content: str,
    rules_content: str,
    pkg_name: str,
    version: str,
    ubuntu_series: str,
    arch: str,
    error_msg: str,
    ai_memory_context: str = "",
    working_tree_context: str = "",
) -> str:
    """Format sbuild failure context for the AI.

    Args:
        log_excerpt: Extracted failure section from the sbuild log.
        control_content: Contents of debian/control.
        rules_content: Contents of debian/rules.
        pkg_name: Debian package name.
        version: Package version.
        ubuntu_series: Target Ubuntu distribution.
        arch: Build architecture.
        error_msg: Brief error message from SbuildResult.
        ai_memory_context: Optional formatted string of previous AI
            attempts for this package.
        working_tree_context: Optional formatted string containing the
            git tree listing and file contents from
            :func:`~packastack.ai.build_diagnosis.collect_working_tree_context`.

    Returns:
        Formatted user message string.
    """
    base = (
        f"Package: {pkg_name}\n"
        f"Version: {version}\n"
        f"Distribution: {ubuntu_series}\n"
        f"Architecture: {arch}\n"
        f"Error: {error_msg}\n"
        f"\n"
        f"== sbuild failure log excerpt ==\n"
        f"{log_excerpt}\n"
        f"\n"
        f"== debian/control ==\n"
        f"{control_content}\n"
        f"\n"
        f"== debian/rules ==\n"
        f"{rules_content}\n"
    )
    if working_tree_context:
        base += f"\n{working_tree_context}\n"
    if ai_memory_context:
        base += f"\n{ai_memory_context}\n"
    return base


# Patterns that indicate the start of a build error in sbuild logs
_ERROR_PATTERNS = [
    "error:",
    "Error:",
    "FAILED",
    "E: Build failure",
    "dh_auto_test: error",
    "dh_auto_build: error",
    "make: *** [",
    "dpkg-buildpackage: error",
    "ModuleNotFoundError:",
    "ImportError:",
    "SyntaxError:",
    "AttributeError:",
]


def extract_sbuild_failure_section(
    log_path: Path,
    max_lines: int = 0,
) -> str:
    """Extract the relevant failure section from an sbuild log.

    When *max_lines* is ``0`` the full log is returned so the AI can
    see all available context.  When a positive limit is given, the
    function extracts lines around the first error pattern found,
    plus the last 100 lines (sbuild summary), capped at *max_lines*.

    Args:
        log_path: Path to the sbuild log file.
        max_lines: Maximum number of lines to return.  ``0`` means
            no limit (return the full log).

    Returns:
        Extracted log section as a string, or empty string if
        the file cannot be read.
    """
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    if not content:
        return ""

    # Unlimited mode — return full log content
    if max_lines <= 0:
        return content

    lines = content.splitlines()
    total = len(lines)

    # Find the first error line
    error_line_idx: int | None = None
    for idx, line in enumerate(lines):
        if any(pattern in line for pattern in _ERROR_PATTERNS):
            error_line_idx = idx
            break

    if error_line_idx is not None:
        # Extract context around the error: 50 lines before, 250 after
        start = max(0, error_line_idx - 50)
        end = min(total, error_line_idx + 250)
        error_section = lines[start:end]

        # Always include last 100 lines (sbuild summary) if not overlapping
        tail_start = max(end, total - 100)
        tail_section = lines[tail_start:]

        if tail_start > end:
            combined = [*error_section, "\n... (truncated) ...\n", *tail_section]
        else:
            combined = error_section
    else:
        # No pattern found - return the last max_lines
        combined = lines[-max_lines:]

    # Cap at max_lines
    if len(combined) > max_lines:
        combined = combined[:max_lines]

    return "\n".join(combined)
