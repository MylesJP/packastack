# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only
"""Control-file minimum-version policy helpers.

Implements the policy of choosing the previous LTS version floor when compatible
with upstream minimum requirements, without regressing existing constraints
unless explicitly normalized.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from packastack.apt.packages import compare_versions
from packastack.debpkg.control import ParsedDependency


@dataclass
class MinVersionDecision:
    name: str
    upstream_min_required: str | None
    prev_lts_version: str | None
    existing_constraint: str | None
    chosen_min_version: str | None
    action: str
    reason_code: str
    cloud_archive_required: bool


def _cmp(v1: str | None, v2: str | None) -> int:
    if v1 is None and v2 is None:
        return 0
    if v1 is None:
        return -1
    if v2 is None:
        return 1
    return compare_versions(v1, v2)


def decide_min_version(
    name: str,
    existing_version: str | None,
    upstream_min: str | None,
    prev_lts_version: str | None,
    normalize: bool = False,
) -> MinVersionDecision:
    """Decide the control minimum version for a dependency."""

    cloud_archive_required = False
    if upstream_min is None:
        return MinVersionDecision(
            name=name,
            upstream_min_required=None,
            prev_lts_version=prev_lts_version,
            existing_constraint=existing_version,
            chosen_min_version=existing_version,
            action="unchanged",
            reason_code="no_upstream_min",
            cloud_archive_required=cloud_archive_required,
        )

    # Determine baseline candidate from previous LTS
    baseline = upstream_min
    if prev_lts_version and _cmp(prev_lts_version, upstream_min) >= 0:
        baseline = prev_lts_version
    else:
        cloud_archive_required = True

    chosen = baseline
    action = "added" if not existing_version else "unchanged"
    reason = "baseline_applied"

    if existing_version:
        cmp_existing = _cmp(existing_version, baseline)
        if cmp_existing > 0:
            if normalize:
                chosen = baseline
                action = "lowered"
                reason = "normalized_to_baseline"
            else:
                chosen = existing_version
                action = "kept"
                reason = "existing_higher"
        elif cmp_existing < 0:
            chosen = baseline
            action = "raised"
            reason = "raised_to_baseline"
        else:
            chosen = existing_version
            action = "unchanged"
            reason = "unchanged"

    return MinVersionDecision(
        name=name,
        upstream_min_required=upstream_min,
        prev_lts_version=prev_lts_version,
        existing_constraint=existing_version,
        chosen_min_version=chosen,
        action=action,
        reason_code=reason,
        cloud_archive_required=cloud_archive_required,
    )


def apply_min_version_policy(
    existing: Iterable[ParsedDependency],
    upstream_mins: dict[str, str],
    prev_lts_versions: dict[str, str],
    normalize: bool = False,
    dry_run: bool = False,
) -> tuple[list[ParsedDependency], list[MinVersionDecision]]:
    """Apply minimum-version policy to a list of ParsedDependency objects."""

    updated: list[ParsedDependency] = []
    decisions: list[MinVersionDecision] = []

    for dep in sorted(existing, key=lambda d: d.name):
        upstream_min = upstream_mins.get(dep.name)
        prev_ver = prev_lts_versions.get(dep.name)
        existing_version = dep.version or None

        decision = decide_min_version(
            name=dep.name,
            existing_version=existing_version,
            upstream_min=upstream_min,
            prev_lts_version=prev_ver,
            normalize=normalize,
        )
        decisions.append(decision)

        if dry_run:
            updated.append(dep)
            continue

        if decision.chosen_min_version and (decision.action in {"added", "raised", "lowered", "kept"}):
            updated.append(
                ParsedDependency(
                    name=dep.name,
                    relation=">=",
                    version=decision.chosen_min_version,
                    arch_qualifiers=dep.arch_qualifiers,
                    alternatives=dep.alternatives,
                )
            )
        else:
            updated.append(dep)

    return updated, decisions


def decisions_to_report(decisions: list[MinVersionDecision]) -> dict:
    """Convert decisions to a JSON-serializable report."""

    items = []
    for d in decisions:
        items.append(
            {
                "name": d.name,
                "upstream_min_required": d.upstream_min_required,
                "prev_lts_version": d.prev_lts_version,
                "existing_constraint": d.existing_constraint,
                "chosen_min_version": d.chosen_min_version,
                "action": d.action,
                "reason_code": d.reason_code,
                "cloud_archive_required": d.cloud_archive_required,
            }
        )

    return {
        "decisions": items,
        "cloud_archive_required": sum(1 for d in decisions if d.cloud_archive_required),
        "raised": sum(1 for d in decisions if d.action == "raised"),
        "lowered": sum(1 for d in decisions if d.action == "lowered"),
        "unchanged": sum(1 for d in decisions if d.action == "unchanged"),
    }
