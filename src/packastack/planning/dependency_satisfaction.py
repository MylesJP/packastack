# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only
"""Dependency satisfaction evaluation across Ubuntu series.

Provides helpers to check whether debian/control dependencies are satisfied
in the target development series and the previous LTS (cloud-archive base).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from packastack.apt.packages import PackageIndex, version_satisfies
from packastack.debpkg.control import ParsedDependency


@dataclass
class SeriesDepStatus:
    """Status of a dependency within a specific Ubuntu series."""

    found: bool
    version: str | None
    component: str
    satisfied: bool
    reason: str


@dataclass
class DependencyCheck:
    """Evaluation result for a single dependency across series."""

    name: str
    relation: str
    version: str
    kind: str  # build or runtime
    dev: SeriesDepStatus
    prev_lts: SeriesDepStatus
    cloud_archive_required: bool
    mir_warning_dev: bool
    mir_warning_prev_lts: bool
    mir_warning: bool

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "relation": self.relation,
            "version": self.version,
            "kind": self.kind,
            "dev": self.dev.__dict__,
            "prev_lts": self.prev_lts.__dict__,
            "cloud_archive_required": self.cloud_archive_required,
            "mir_warning_dev": self.mir_warning_dev,
            "mir_warning_prev_lts": self.mir_warning_prev_lts,
            "mir_warning": self.mir_warning,
        }


@dataclass
class SatisfactionSummary:
    """Aggregated counts for a set of dependencies."""

    total: int = 0
    dev_satisfied: int = 0
    prev_lts_satisfied: int = 0
    cloud_archive_required: int = 0
    mir_warnings: int = 0

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "dev_satisfied": self.dev_satisfied,
            "prev_lts_satisfied": self.prev_lts_satisfied,
            "cloud_archive_required": self.cloud_archive_required,
            "mir_warnings": self.mir_warnings,
        }


def _status_for_dep(dep: ParsedDependency, index: PackageIndex | None) -> SeriesDepStatus:
    """Evaluate a single dependency in one package index."""

    if index is None:
        return SeriesDepStatus(False, None, "unknown", False, "missing")

    pkg = index.find_package(dep.name)
    if pkg is None:
        return SeriesDepStatus(False, None, "unknown", False, "missing")

    component = pkg.component or "unknown"
    version = pkg.version or ""
    relation = dep.relation
    required = dep.version
    satisfied = True
    reason = "ok"

    if relation and required:
        satisfied = version_satisfies(version, relation, required)
        if not satisfied:
            reason = "version_too_low"
    elif not version:
        # Missing version in index despite package present
        satisfied = True
        reason = "ok"

    return SeriesDepStatus(True, version or None, component, satisfied, reason)


def _evaluate_single(
    dep: ParsedDependency,
    index: PackageIndex | None,
) -> SeriesDepStatus:
    """Evaluate dependency with alternatives against a single index."""

    candidates: list[ParsedDependency] = [dep, *list(dep.alternatives)]
    first_found: SeriesDepStatus | None = None
    for candidate in candidates:
        status = _status_for_dep(candidate, index)
        if status.found and status.satisfied:
            return status
        if status.found and first_found is None:
            first_found = status
    return first_found or SeriesDepStatus(False, None, "unknown", False, "missing")


def evaluate_dependencies(
    deps: Iterable[ParsedDependency],
    dev_index: PackageIndex,
    prev_index: PackageIndex | None,
    kind: str = "build",
) -> tuple[list[DependencyCheck], SatisfactionSummary]:
    """Evaluate dependencies across dev and previous LTS indexes."""

    results: list[DependencyCheck] = []
    summary = SatisfactionSummary()

    for dep in deps:
        dev_status = _evaluate_single(dep, dev_index)
        prev_status = _evaluate_single(dep, prev_index)

        cloud_needed = not prev_status.satisfied
        mir_dev = dev_status.found and dev_status.component not in ("main", "")
        mir_prev = prev_status.found and prev_status.component not in ("main", "")
        mir_any = mir_dev or mir_prev

        results.append(
            DependencyCheck(
                name=dep.name,
                relation=dep.relation,
                version=dep.version,
                kind=kind,
                dev=dev_status,
                prev_lts=prev_status,
                cloud_archive_required=cloud_needed,
                mir_warning_dev=mir_dev,
                mir_warning_prev_lts=mir_prev,
                mir_warning=mir_any,
            )
        )

        summary.total += 1
        if dev_status.satisfied:
            summary.dev_satisfied += 1
        if prev_status.satisfied:
            summary.prev_lts_satisfied += 1
        if cloud_needed:
            summary.cloud_archive_required += 1
        if mir_any:
            summary.mir_warnings += 1

    return results, summary
