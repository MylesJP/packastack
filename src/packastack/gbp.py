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

"""Git-buildpackage (gbp) wrapper for Packastack build operations.

Provides functions for gbp patch-queue operations, source package building,
and optional binary building via sbuild.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


class PatchFailureReason(Enum):
    """Classification of patch application failures."""

    CONFLICT = "conflict"
    FUZZ = "fuzz"
    OFFSET = "offset"
    MISSING_FILE = "missing_file"
    ALREADY_APPLIED = "already_applied"
    UPSTREAMED = "upstreamed"
    UNKNOWN = "unknown"


@dataclass
class PatchHealthReport:
    """Report on the health of a patch after attempted application."""

    patch_name: str
    success: bool
    failure_reason: PatchFailureReason | None = None
    files_affected: list[str] = field(default_factory=list)
    suggested_action: str = ""
    output: str = ""

    def __str__(self) -> str:
        if self.success:
            return f"{self.patch_name}: OK"
        reason = self.failure_reason.value if self.failure_reason else "unknown"
        return f"{self.patch_name}: FAILED ({reason}) - {self.suggested_action}"


@dataclass
class PQResult:
    """Result of a gbp patch-queue operation."""

    success: bool
    output: str
    needs_refresh: bool = False
    patch_reports: list[PatchHealthReport] = field(default_factory=list)


@dataclass
class BuildResult:
    """Result of a package build operation."""

    success: bool
    output: str
    artifacts: list[Path] = field(default_factory=list)
    changes_file: Path | None = None
    dsc_file: Path | None = None


def run_command(
    cmd: Sequence[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture: bool = True,
) -> tuple[int, str, str]:
    """Run a command and return exit code, stdout, stderr.

    Args:
        cmd: Command and arguments to run.
        cwd: Working directory for the command.
        env: Environment variables (merged with current env).
        capture: If True, capture output; otherwise inherit stdio.

    Returns:
        Tuple of (exit_code, stdout, stderr).
    """
    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    if capture:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=run_env,
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout, result.stderr
    else:
        result = subprocess.run(cmd, cwd=cwd, env=run_env)
        return result.returncode, "", ""


def pq_import(repo_path: Path, time_machine: int | None = None) -> PQResult:
    """Import patches using gbp pq import.

    This applies debian/patches to create the patch-queue branch.

    Args:
        repo_path: Path to the git repository.
        time_machine: If set, use --time-machine=N to accept patches with offset/fuzz.

    Returns:
        PQResult with success status and any issues detected.
    """
    cmd = ["gbp", "pq", "import", "--force"]
    if time_machine is not None:
        cmd.append(f"--time-machine={time_machine}")
    returncode, stdout, stderr = run_command(cmd, cwd=repo_path)

    output = stdout + stderr
    success = returncode == 0
    needs_refresh = False
    patch_reports: list[PatchHealthReport] = []

    if not success:
        # Analyze failure
        patch_reports = _analyze_pq_failure(output)
        # Check if it's just offset/fuzz that can be refreshed
        if all(
            r.failure_reason in (PatchFailureReason.OFFSET, PatchFailureReason.FUZZ)
            for r in patch_reports
            if not r.success
        ):
            needs_refresh = True

    return PQResult(
        success=success,
        output=output,
        needs_refresh=needs_refresh,
        patch_reports=patch_reports,
    )


def pq_export(repo_path: Path) -> PQResult:
    """Export/refresh patches using gbp pq export.

    This regenerates debian/patches from the patch-queue branch.

    Args:
        repo_path: Path to the git repository.

    Returns:
        PQResult with success status.
    """
    cmd = ["gbp", "pq", "export"]
    returncode, stdout, stderr = run_command(cmd, cwd=repo_path)

    output = stdout + stderr
    return PQResult(success=returncode == 0, output=output)


def pq_drop(repo_path: Path) -> PQResult:
    """Drop the patch-queue branch.

    Args:
        repo_path: Path to the git repository.

    Returns:
        PQResult with success status.
    """
    cmd = ["gbp", "pq", "drop"]
    returncode, stdout, stderr = run_command(cmd, cwd=repo_path)

    output = stdout + stderr
    return PQResult(success=returncode == 0, output=output)


def pq_rebase(repo_path: Path, upstream_branch: str = "upstream") -> PQResult:
    """Rebase patch-queue onto upstream.

    Args:
        repo_path: Path to the git repository.
        upstream_branch: Name of the upstream branch.

    Returns:
        PQResult with success status.
    """
    cmd = ["gbp", "pq", "rebase", "--upstream-tag", upstream_branch]
    returncode, stdout, stderr = run_command(cmd, cwd=repo_path)

    output = stdout + stderr
    return PQResult(success=returncode == 0, output=output)


def _analyze_pq_failure(output: str) -> list[PatchHealthReport]:
    """Analyze gbp pq output to classify patch failures.

    Args:
        output: Combined stdout/stderr from gbp pq.

    Returns:
        List of PatchHealthReport for each problematic patch.
    """
    reports: list[PatchHealthReport] = []
    lines = output.split("\n")

    current_patch = ""
    for line in lines:
        # Detect patch being applied
        if "Applying:" in line or "applying:" in line.lower():
            parts = line.split(":", 1)
            if len(parts) > 1:
                current_patch = parts[1].strip()

        # Detect failure types
        if current_patch:
            if "CONFLICT" in line or "conflict" in line:
                reports.append(
                    PatchHealthReport(
                        patch_name=current_patch,
                        success=False,
                        failure_reason=PatchFailureReason.CONFLICT,
                        suggested_action="Manual conflict resolution required",
                        output=line,
                    )
                )
            elif "fuzz" in line.lower():
                reports.append(
                    PatchHealthReport(
                        patch_name=current_patch,
                        success=False,
                        failure_reason=PatchFailureReason.FUZZ,
                        suggested_action="Refresh patch with gbp pq export",
                        output=line,
                    )
                )
            elif "offset" in line.lower():
                reports.append(
                    PatchHealthReport(
                        patch_name=current_patch,
                        success=False,
                        failure_reason=PatchFailureReason.OFFSET,
                        suggested_action="Refresh patch with gbp pq export",
                        output=line,
                    )
                )
            elif "No such file" in line or "does not exist" in line.lower():
                reports.append(
                    PatchHealthReport(
                        patch_name=current_patch,
                        success=False,
                        failure_reason=PatchFailureReason.MISSING_FILE,
                        suggested_action="File removed upstream; drop or update patch",
                        output=line,
                    )
                )
            elif "already applied" in line.lower() or "previously applied" in line.lower():
                reports.append(
                    PatchHealthReport(
                        patch_name=current_patch,
                        success=False,
                        failure_reason=PatchFailureReason.ALREADY_APPLIED,
                        suggested_action="Patch may be upstreamed; consider dropping",
                        output=line,
                    )
                )

    return reports


def check_upstreamed_patches(
    repo_path: Path,
    patches_dir: Path | None = None,
    upstream_ref: str = "upstream",
) -> list[PatchHealthReport]:
    """Check if any patches appear to be upstreamed.

    Compares patch content against upstream diff to detect patches
    that may have been incorporated upstream.

    Args:
        repo_path: Path to the git repository.
        patches_dir: Path to debian/patches (default: repo_path/debian/patches).
        upstream_ref: Git ref for upstream comparison.

    Returns:
        List of PatchHealthReport for patches that appear upstreamed.
    """
    if patches_dir is None:
        patches_dir = repo_path / "debian" / "patches"

    if not patches_dir.exists():
        return []

    series_file = patches_dir / "series"
    if not series_file.exists():
        return []

    reports: list[PatchHealthReport] = []

    # Get list of patches from series
    patches = [
        line.strip()
        for line in series_file.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    # For each patch, check if its changes exist in upstream
    for patch_name in patches:
        patch_file = patches_dir / patch_name
        if not patch_file.exists():
            continue

        # Simple heuristic: check if patch applies cleanly to upstream
        # If it fails with "already applied", it's likely upstreamed
        # This is a simplified check; full implementation would parse the diff
        cmd = [
            "git",
            "apply",
            "--check",
            "--reverse",
            str(patch_file),
        ]
        returncode, _, stderr = run_command(cmd, cwd=repo_path)

        if returncode == 0:
            # Patch can be reverse-applied, suggesting it's in upstream
            reports.append(
                PatchHealthReport(
                    patch_name=patch_name,
                    success=False,
                    failure_reason=PatchFailureReason.UPSTREAMED,
                    suggested_action="Patch appears to be in upstream; consider dropping",
                )
            )

    return reports


def build_source(
    repo_path: Path,
    output_dir: Path | None = None,
    unsigned: bool = True,
) -> BuildResult:
    """Build source package using gbp buildpackage.

    Args:
        repo_path: Path to the git repository.
        output_dir: Directory for build artifacts (default: parent of repo).
        unsigned: If True, don't sign the package (-us -uc).

    Returns:
        BuildResult with success status and artifact paths.
    """
    if output_dir is None:
        output_dir = repo_path.parent

    cmd = ["gbp", "buildpackage", "-S"]
    if unsigned:
        cmd.extend(["-us", "-uc"])

    # Export working copy and set output directory
    cmd.extend(["--git-export=WC", "--git-export-dir", str(output_dir)])

    returncode, stdout, stderr = run_command(cmd, cwd=repo_path)
    output = stdout + stderr
    success = returncode == 0

    # Find artifacts
    artifacts: list[Path] = []
    dsc_file: Path | None = None
    changes_file: Path | None = None

    if success and output_dir.exists():
        for f in output_dir.iterdir():
            # Check file extensions (note: .tar.gz has suffix .gz in Python)
            name = f.name
            is_artifact = (
                f.suffix == ".dsc"
                or f.suffix == ".changes"
                or f.suffix == ".buildinfo"
                or name.endswith(".tar.gz")
                or name.endswith(".tar.xz")
            )
            if is_artifact:
                artifacts.append(f)
                if f.suffix == ".dsc":
                    dsc_file = f
                elif f.suffix == ".changes" and "_source" in f.name:
                    changes_file = f

    return BuildResult(
        success=success,
        output=output,
        artifacts=artifacts,
        dsc_file=dsc_file,
        changes_file=changes_file,
    )


def build_binary(
    dsc_path: Path,
    output_dir: Path | None = None,
    distribution: str | None = None,
) -> BuildResult:
    """Build binary package using sbuild.

    Args:
        dsc_path: Path to the .dsc file.
        output_dir: Directory for build artifacts.
        distribution: Target distribution (e.g., "noble").

    Returns:
        BuildResult with success status and artifact paths.
    """
    if output_dir is None:
        output_dir = dsc_path.parent

    cmd = ["sbuild", "--nolog", str(dsc_path)]

    if distribution:
        cmd.extend(["-d", distribution])

    # Run in output directory
    returncode, stdout, stderr = run_command(cmd, cwd=output_dir)
    output = stdout + stderr
    success = returncode == 0

    # Find artifacts
    artifacts: list[Path] = []
    changes_file: Path | None = None

    if success and output_dir.exists():
        # Find .deb files and .changes
        for f in output_dir.iterdir():
            if f.suffix in (".deb", ".ddeb", ".changes", ".buildinfo"):
                artifacts.append(f)
                if f.suffix == ".changes" and "_source" not in f.name:
                    changes_file = f

    return BuildResult(
        success=success,
        output=output,
        artifacts=artifacts,
        changes_file=changes_file,
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m packastack.gbp <repo_path> [command]")
        print("Commands: import, export, drop, build-source")
        sys.exit(1)

    repo = Path(sys.argv[1])
    command = sys.argv[2] if len(sys.argv) > 2 else "import"

    if command == "import":
        result = pq_import(repo)
        print(f"Success: {result.success}")
        if result.patch_reports:
            for report in result.patch_reports:
                print(f"  {report}")
    elif command == "export":
        result = pq_export(repo)
        print(f"Success: {result.success}")
    elif command == "drop":
        result = pq_drop(repo)
        print(f"Success: {result.success}")
    elif command == "build-source":
        result = build_source(repo)
        print(f"Success: {result.success}")
        if result.artifacts:
            print("Artifacts:")
            for a in result.artifacts:
                print(f"  {a}")
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
