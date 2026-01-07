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

"""Dependency sync report generation.

Generates human-readable and machine-readable reports about dependency
synchronization results, including version bumps, new dependencies,
and unresolved packages.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from packastack.debpkg.dep_sync import SyncResult, VersionBump
    from packastack.planning.build_manifest import BuildManifest


@dataclass
class DependencySatisfactionSummary:
    """Aggregated view of dependency satisfaction for a package build."""

    package: str
    policy: str
    total: int
    satisfied: int
    outdated: int
    missing: int
    overridden: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    missing_deps: list[str] = field(default_factory=list)
    outdated_deps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "package": self.package,
            "policy": self.policy,
            "total": self.total,
            "satisfied": self.satisfied,
            "outdated": self.outdated,
            "missing": self.missing,
            "overridden": self.overridden,
            "by_source": dict(self.by_source),
            "missing_deps": list(self.missing_deps),
            "outdated_deps": list(self.outdated_deps),
        }


@dataclass
class DependencySyncReport:
    """Report summarizing dependency synchronization results."""

    source_package: str
    timestamp: str = ""
    # Version bumps
    version_bumps: list[dict] = field(default_factory=list)
    # New dependencies to add
    additions: list[dict] = field(default_factory=list)
    # Unresolved dependencies
    unresolved: list[str] = field(default_factory=list)
    # Warnings
    warnings: list[str] = field(default_factory=list)
    # Stats
    packages_from_manifest: int = 0
    packages_from_lts: int = 0
    packages_from_upstream_spec: int = 0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "source_package": self.source_package,
            "timestamp": self.timestamp,
            "version_bumps": self.version_bumps,
            "additions": self.additions,
            "unresolved": self.unresolved,
            "warnings": self.warnings,
            "stats": {
                "packages_from_manifest": self.packages_from_manifest,
                "packages_from_lts": self.packages_from_lts,
                "packages_from_upstream_spec": self.packages_from_upstream_spec,
            },
        }


def create_sync_report(
    source_package: str,
    sync_result: SyncResult,
) -> DependencySyncReport:
    """Create a report from a SyncResult.

    Args:
        source_package: Name of the source package being synced.
        sync_result: The sync result to report on.

    Returns:
        DependencySyncReport with all data populated.
    """
    report = DependencySyncReport(source_package=source_package)

    # Convert version bumps
    for bump in sync_result.version_bumps:
        report.version_bumps.append({
            "debian_package": bump.debian_package,
            "python_package": bump.python_package,
            "old_version": bump.old_version,
            "new_version": bump.new_version,
            "source": bump.source,
        })

    # Convert additions
    for dep in sync_result.additions:
        report.additions.append({
            "name": dep.name,
            "version": dep.version,
            "relation": dep.relation,
        })

    # Copy unresolved and warnings
    report.unresolved = list(sync_result.unresolved)
    report.warnings = list(sync_result.warnings)

    # Calculate stats
    report.packages_from_manifest = len(sync_result.from_manifest)
    report.packages_from_lts = len(sync_result.from_lts)

    # Count upstream spec sourced packages
    upstream_spec_count = sum(
        1 for bump in sync_result.version_bumps if bump.source == "upstream_spec"
    )
    report.packages_from_upstream_spec = upstream_spec_count

    return report


def render_satisfaction_text(summary: DependencySatisfactionSummary) -> str:
    """Render a dependency satisfaction summary as text."""

    lines = []
    lines.append(f"Dependency Satisfaction: {summary.package}")
    lines.append("=" * 60)
    lines.append(f"Policy: {summary.policy}")
    lines.append(
        f"Satisfied: {summary.satisfied}/{summary.total} | Outdated: {summary.outdated} | Missing: {summary.missing}"
    )
    if summary.overridden:
        lines.append(f"Policy overrides applied: {summary.overridden}")

    if summary.by_source:
        lines.append("Sources:")
        for src, count in sorted(summary.by_source.items()):
            lines.append(f"  {src}: {count}")

    if summary.outdated_deps:
        lines.append("")
        lines.append("Outdated (needs newer version):")
        for dep in sorted(summary.outdated_deps):
            lines.append(f"  {dep}")

    if summary.missing_deps:
        lines.append("")
        lines.append("Missing (not found in any index):")
        for dep in sorted(summary.missing_deps):
            lines.append(f"  {dep}")

    return "\n".join(lines)


def render_satisfaction_json(summary: DependencySatisfactionSummary) -> str:
    """Render a dependency satisfaction summary as JSON."""

    return json.dumps(summary.to_dict(), indent=2)


def save_satisfaction_report(
    summary: DependencySatisfactionSummary,
    output_dir: Path,
) -> list[Path]:
    """Save dependency satisfaction report in text and JSON formats."""

    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = f"dep-satisfaction-{summary.package}"
    text_path = output_dir / f"{base_name}.txt"
    json_path = output_dir / f"{base_name}.json"

    text_path.write_text(render_satisfaction_text(summary))
    json_path.write_text(render_satisfaction_json(summary))

    return [text_path, json_path]


def render_sync_report_text(report: DependencySyncReport) -> str:
    """Render a sync report as human-readable text.

    Args:
        report: The report to render.

    Returns:
        Formatted text report.
    """
    lines = []
    lines.append(f"Dependency Sync Report: {report.source_package}")
    lines.append("=" * 60)
    lines.append(f"Generated: {report.timestamp}")
    lines.append("")

    # Version bumps
    if report.version_bumps:
        lines.append("Version Bumps:")
        lines.append("-" * 40)
        for bump in report.version_bumps:
            old = bump["old_version"] or "(none)"
            lines.append(
                f"  {bump['debian_package']}: {old} -> {bump['new_version']} "
                f"(from {bump['source']})"
            )
        lines.append("")

    # New dependencies
    if report.additions:
        lines.append("New Dependencies:")
        lines.append("-" * 40)
        for dep in report.additions:
            if dep["version"]:
                lines.append(f"  {dep['name']} ({dep['relation']} {dep['version']})")
            else:
                lines.append(f"  {dep['name']}")
        lines.append("")

    # Unresolved
    if report.unresolved:
        lines.append("Unresolved Dependencies:")
        lines.append("-" * 40)
        for pkg in report.unresolved:
            lines.append(f"  {pkg}")
        lines.append("")

    # Warnings
    if report.warnings:
        lines.append("Warnings:")
        lines.append("-" * 40)
        for warning in report.warnings:
            lines.append(f"  ! {warning}")
        lines.append("")

    # Stats
    lines.append("Statistics:")
    lines.append("-" * 40)
    lines.append(f"  From build manifest: {report.packages_from_manifest}")
    lines.append(f"  From LTS archive: {report.packages_from_lts}")
    lines.append(f"  From upstream specs: {report.packages_from_upstream_spec}")
    lines.append(f"  Version bumps: {len(report.version_bumps)}")
    lines.append(f"  New dependencies: {len(report.additions)}")
    lines.append(f"  Unresolved: {len(report.unresolved)}")

    return "\n".join(lines)


def render_sync_report_json(report: DependencySyncReport) -> str:
    """Render a sync report as JSON.

    Args:
        report: The report to render.

    Returns:
        JSON string.
    """
    return json.dumps(report.to_dict(), indent=2)


def save_sync_report(
    report: DependencySyncReport,
    output_dir: Path,
    formats: list[str] | None = None,
) -> list[Path]:
    """Save a sync report to files.

    Args:
        report: The report to save.
        output_dir: Directory to save reports in.
        formats: List of formats to save ("text", "json"). Defaults to both.

    Returns:
        List of paths to saved files.
    """
    if formats is None:
        formats = ["text", "json"]

    output_dir.mkdir(parents=True, exist_ok=True)
    saved_files = []

    base_name = f"dep-sync-{report.source_package}"

    if "text" in formats:
        text_path = output_dir / f"{base_name}.txt"
        text_path.write_text(render_sync_report_text(report))
        saved_files.append(text_path)

    if "json" in formats:
        json_path = output_dir / f"{base_name}.json"
        json_path.write_text(render_sync_report_json(report))
        saved_files.append(json_path)

    return saved_files


@dataclass
class ManifestReport:
    """Report summarizing a build manifest."""

    series: str
    cycle_stage: str
    timestamp: str = ""
    packages: list[dict] = field(default_factory=list)
    build_order: list[str] = field(default_factory=list)
    # Stats by build type
    release_count: int = 0
    milestone_count: int = 0
    snapshot_count: int = 0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "series": self.series,
            "cycle_stage": self.cycle_stage,
            "timestamp": self.timestamp,
            "packages": self.packages,
            "build_order": self.build_order,
            "stats": {
                "release_count": self.release_count,
                "milestone_count": self.milestone_count,
                "snapshot_count": self.snapshot_count,
                "total_packages": len(self.packages),
            },
        }


def create_manifest_report(manifest: BuildManifest) -> ManifestReport:
    """Create a report from a BuildManifest.

    Args:
        manifest: The manifest to report on.

    Returns:
        ManifestReport with all data populated.
    """
    from packastack.planning.type_selection import BuildType

    report = ManifestReport(
        series=manifest.series,
        cycle_stage=manifest.cycle_stage.value,
        build_order=list(manifest.build_order),
    )

    for name, pkg in manifest.packages.items():
        report.packages.append({
            "source_package": pkg.source_package,
            "deliverable": pkg.deliverable,
            "upstream_version": pkg.upstream_version,
            "full_version": pkg.full_version,
            "build_type": pkg.build_type.value,
            "version_source": pkg.version_source,
        })

        # Count by type
        if pkg.build_type == BuildType.RELEASE:
            report.release_count += 1
        elif pkg.build_type == BuildType.MILESTONE:
            report.milestone_count += 1
        else:
            report.snapshot_count += 1

    return report


def render_manifest_report_text(report: ManifestReport) -> str:
    """Render a manifest report as human-readable text.

    Args:
        report: The report to render.

    Returns:
        Formatted text report.
    """
    lines = []
    lines.append(f"Build Manifest Report: {report.series}")
    lines.append("=" * 60)
    lines.append(f"Generated: {report.timestamp}")
    lines.append(f"Cycle Stage: {report.cycle_stage}")
    lines.append("")

    # Build order
    lines.append("Build Order:")
    lines.append("-" * 40)
    for i, pkg in enumerate(report.build_order, 1):
        lines.append(f"  {i:3}. {pkg}")
    lines.append("")

    # Package details
    lines.append("Package Details:")
    lines.append("-" * 40)
    for pkg in report.packages:
        build_type = pkg["build_type"].upper()
        lines.append(
            f"  {pkg['source_package']}: {pkg['full_version']} [{build_type}]"
        )
    lines.append("")

    # Stats
    lines.append("Statistics:")
    lines.append("-" * 40)
    lines.append(f"  Total packages: {len(report.packages)}")
    lines.append(f"  Release builds: {report.release_count}")
    lines.append(f"  Milestone builds: {report.milestone_count}")
    lines.append(f"  Snapshot builds: {report.snapshot_count}")

    return "\n".join(lines)


def render_manifest_report_json(report: ManifestReport) -> str:
    """Render a manifest report as JSON.

    Args:
        report: The report to render.

    Returns:
        JSON string.
    """
    return json.dumps(report.to_dict(), indent=2)


def save_manifest_report(
    report: ManifestReport,
    output_dir: Path,
    formats: list[str] | None = None,
) -> list[Path]:
    """Save a manifest report to files.

    Args:
        report: The report to save.
        output_dir: Directory to save reports in.
        formats: List of formats to save ("text", "json"). Defaults to both.

    Returns:
        List of paths to saved files.
    """
    if formats is None:
        formats = ["text", "json"]

    output_dir.mkdir(parents=True, exist_ok=True)
    saved_files = []

    base_name = f"manifest-{report.series}"

    if "text" in formats:
        text_path = output_dir / f"{base_name}.txt"
        text_path.write_text(render_manifest_report_text(report))
        saved_files.append(text_path)

    if "json" in formats:
        json_path = output_dir / f"{base_name}.json"
        json_path.write_text(render_manifest_report_json(report))
        saved_files.append(json_path)

    return saved_files
