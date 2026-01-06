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

"""Build-all report generation.

This module contains report generation for build-all runs,
producing JSON and Markdown summaries of build results.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from packastack.planning.build_all_state import BuildAllState, PackageStatus


def generate_build_all_reports(
    state: "BuildAllState",
    run_dir: Path,
) -> tuple[Path, Path]:
    """Generate build-all summary reports.

    Produces both JSON and Markdown reports summarizing the build-all run,
    including success/failure counts, timing information, and error details.

    Args:
        state: Final build state containing package results.
        run_dir: Run directory where reports will be written.

    Returns:
        Tuple of (json_report_path, md_report_path).
        
    Side Effects:
        - Creates reports/ directory if needed
        - Writes build-all-summary.json
        - Writes build-all-summary.md
    """
    from packastack.planning.build_all_state import PackageStatus
    
    reports_dir = run_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Compute statistics
    total = state.total_packages
    succeeded = len(state.get_success_packages())
    failed = len(state.get_failed_packages())
    skipped = sum(1 for p in state.packages.values() if p.status == PackageStatus.SKIPPED)
    blocked = len(state.get_blocked_packages())

    # Get timing info
    durations = [
        (p.name, p.duration_seconds)
        for p in state.packages.values()
        if p.duration_seconds > 0
    ]
    durations.sort(key=lambda x: x[1], reverse=True)
    top_10_longest = durations[:10]

    total_time = sum(d for _, d in durations)

    # Failures by type
    failures_by_type: dict[str, list[str]] = defaultdict(list)
    for p in state.packages.values():
        if p.status == PackageStatus.FAILED and p.failure_type:
            failures_by_type[p.failure_type.value].append(p.name)

    # Build JSON report
    json_report = {
        "run_id": state.run_id,
        "target": state.target,
        "ubuntu_series": state.ubuntu_series,
        "build_type": state.build_type,
        "started_at": state.started_at,
        "completed_at": state.completed_at,
        "summary": {
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
            "blocked": blocked,
            "total_time_seconds": total_time,
        },
        "failures": {
            name: {
                "type": p.failure_type.value if p.failure_type else "unknown",
                "message": p.failure_message,
                "log": p.log_path,
            }
            for name, p in state.packages.items()
            if p.status == PackageStatus.FAILED
        },
        "failures_by_type": dict(failures_by_type),
        "missing_deps": {
            name: dep.to_dict()
            for name, dep in state.missing_deps.items()
        },
        "cycles": state.cycles,
        "top_10_longest": [
            {"package": name, "duration_seconds": dur}
            for name, dur in top_10_longest
        ],
        "build_order": state.build_order,
    }

    json_path = reports_dir / "build-all-summary.json"
    json_path.write_text(json.dumps(json_report, indent=2))

    # Build Markdown report
    md_lines = _generate_markdown_report(
        state=state,
        total=total,
        succeeded=succeeded,
        failed=failed,
        skipped=skipped,
        blocked=blocked,
        total_time=total_time,
        failures_by_type=failures_by_type,
        top_10_longest=top_10_longest,
    )

    md_path = reports_dir / "build-all-summary.md"
    md_path.write_text("\n".join(md_lines))

    return json_path, md_path


def _generate_markdown_report(
    state: "BuildAllState",
    total: int,
    succeeded: int,
    failed: int,
    skipped: int,
    blocked: int,
    total_time: float,
    failures_by_type: dict[str, list[str]],
    top_10_longest: list[tuple[str, float]],
) -> list[str]:
    """Generate Markdown report lines.
    
    Args:
        state: Build state
        total: Total package count
        succeeded: Successful build count
        failed: Failed build count
        skipped: Skipped package count
        blocked: Blocked package count
        total_time: Total build time in seconds
        failures_by_type: Failures grouped by type
        top_10_longest: Top 10 longest builds
        
    Returns:
        List of Markdown lines
    """
    md_lines = [
        f"# Build-All Summary: {state.run_id}",
        "",
        f"**Target:** {state.target} on {state.ubuntu_series}",
        f"**Build Type:** {state.build_type}",
        f"**Started:** {state.started_at}",
        f"**Completed:** {state.completed_at}",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|--------|-------|",
        f"| Total Packages | {total} |",
        f"| Succeeded | {succeeded} |",
        f"| Failed | {failed} |",
        f"| Skipped | {skipped} |",
        f"| Blocked | {blocked} |",
        f"| Total Build Time | {total_time:.0f}s ({total_time/3600:.1f}h) |",
        "",
    ]

    if failures_by_type:
        md_lines.extend([
            "## Failures by Type",
            "",
        ])
        for ftype, pkgs in sorted(failures_by_type.items()):
            md_lines.append(f"### {ftype} ({len(pkgs)})")
            md_lines.append("")
            for pkg in sorted(pkgs):
                p = state.packages[pkg]
                md_lines.append(f"- **{pkg}**: {p.failure_message}")
                if p.log_path:
                    md_lines.append(f"  - Log: `{p.log_path}`")
            md_lines.append("")

    if state.missing_deps:
        md_lines.extend([
            "## Missing Dependencies",
            "",
            "| Binary Package | Required By | Suggested Action |",
            "|----------------|-------------|------------------|",
        ])
        for name, dep in sorted(state.missing_deps.items()):
            required_by = ", ".join(dep.required_by[:3])
            if len(dep.required_by) > 3:
                required_by += f" (+{len(dep.required_by) - 3} more)"
            md_lines.append(f"| {name} | {required_by} | {dep.suggested_action} |")
        md_lines.append("")

    if state.cycles:
        md_lines.extend([
            "## Dependency Cycles",
            "",
        ])
        for i, cycle in enumerate(state.cycles, 1):
            md_lines.append(f"{i}. {' -> '.join(cycle)}")
        md_lines.append("")

    if top_10_longest:
        md_lines.extend([
            "## Top 10 Longest Builds",
            "",
            "| Package | Duration |",
            "|---------|----------|",
        ])
        for name, dur in top_10_longest:
            mins = int(dur // 60)
            secs = int(dur % 60)
            md_lines.append(f"| {name} | {mins}m {secs}s |")
        md_lines.append("")

    return md_lines
