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

"""AI memory for retry loops.

Stores attempted patches and their outcomes between build runs so that
subsequent AI calls receive the history of what was tried.  Memory files
are human-readable JSON stored in the run directory and are deleted on
successful build.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

MEMORY_FILENAME = "ai-memory.json"


@dataclass
class AIAttempt:
    """Record of a single AI patch attempt."""

    timestamp: str
    patch_filename: str
    patch_content: str
    build_error: str
    diagnosis: str
    outcome: str  # "build_failed", "patch_invalid", "build_succeeded"


@dataclass
class AIMemory:
    """Accumulated AI retry memory for a package build."""

    package: str
    version: str
    attempts: list[AIAttempt] = field(default_factory=list)

    def add_attempt(
        self,
        patch_filename: str,
        patch_content: str,
        build_error: str,
        diagnosis: str,
        outcome: str,
    ) -> None:
        """Record a new attempt.

        Args:
            patch_filename: Name of the proposed patch file.
            patch_content: Content of the proposed patch.
            build_error: Error output from the build or validation.
            diagnosis: AI's explanation of the issue.
            outcome: Result label (``build_failed``, ``patch_invalid``,
                ``build_succeeded``).
        """
        self.attempts.append(
            AIAttempt(
                timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
                patch_filename=patch_filename,
                patch_content=patch_content,
                build_error=build_error,
                diagnosis=diagnosis,
                outcome=outcome,
            )
        )

    def format_for_prompt(self) -> str:
        """Format memory as context for the AI prompt.

        Returns:
            Human-readable string describing all previous attempts,
            or empty string if there are no attempts.
        """
        if not self.attempts:
            return ""
        parts = [f"== Previous AI attempts for {self.package} {self.version} ==\n"]
        for i, attempt in enumerate(self.attempts, 1):
            parts.append(f"--- Attempt {i} ({attempt.outcome}) ---")
            parts.append(f"Patch: {attempt.patch_filename}")
            parts.append(f"Diagnosis: {attempt.diagnosis}")
            parts.append(f"Build error: {attempt.build_error[:500]}")
            parts.append(f"Patch content:\n{attempt.patch_content[:1000]}")
            parts.append("")
        return "\n".join(parts)


def get_memory_path(run_path: Path) -> Path:
    """Get the path to the AI memory file for a run.

    Args:
        run_path: Path to the run directory.

    Returns:
        Path to ``ai-memory.json`` inside *run_path*.
    """
    return run_path / MEMORY_FILENAME


def save_memory(memory: AIMemory, run_path: Path) -> None:
    """Save AI memory to the run directory.

    Args:
        memory: AIMemory to persist.
        run_path: Path to the run directory.

    Raises:
        OSError: If the file cannot be written (e.g., disk full).
    """
    path = get_memory_path(run_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(memory)
    path.write_text(
        json.dumps(data, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def load_memory(run_path: Path) -> AIMemory | None:
    """Load AI memory from a run directory if it exists.

    Args:
        run_path: Path to the run directory.

    Returns:
        AIMemory if the file exists and is valid, else ``None``.
    """
    path = get_memory_path(run_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        attempts = [AIAttempt(**a) for a in data.get("attempts", [])]
        return AIMemory(
            package=data.get("package", ""),
            version=data.get("version", ""),
            attempts=attempts,
        )
    except (json.JSONDecodeError, KeyError, TypeError, OSError):
        return None


def delete_memory(run_path: Path) -> None:
    """Delete the AI memory file (called on successful build).

    Safe to call when the file does not exist.

    Args:
        run_path: Path to the run directory.
    """
    path = get_memory_path(run_path)
    if path.exists():
        path.unlink(missing_ok=True)


def find_latest_memory(runs_root: Path, package: str) -> AIMemory | None:
    """Find the most recent AI memory for a package across all run dirs.

    Searches run directories in reverse chronological order (newest
    first, since directory names start with timestamps).

    Args:
        runs_root: Root directory containing all run directories.
        package: Package name to search for.

    Returns:
        Most recent AIMemory for the package, or ``None``.
    """
    if not runs_root.exists():
        return None
    for run_dir in sorted(runs_root.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        memory = load_memory(run_dir)
        if memory and memory.package == package:
            return memory
    return None
