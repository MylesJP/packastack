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

"""Cycle edge suggestions based on upstream requirements provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from packastack.planning.validated_plan import (
    extract_upstream_deps,
    map_python_to_debian,
    project_to_source_package,
)
from packastack.upstream.tarball_cache import find_source_dir, get_cached_extraction

if TYPE_CHECKING:
    from packastack.apt.packages import PackageIndex


REQUIREMENTS_FILES = (
    "requirements.txt",
    "pyproject.toml",
    "setup.cfg",
)


@dataclass
class CycleEdgeSuggestion:
    """Suggestion for excluding a cycle edge based on upstream requirements."""

    source: str
    dependency: str
    upstream_project: str
    upstream_version: str
    requirements_source: str
    requirements_path: str
    requirements_files: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        """Convert to a dict for event logging."""
        return {
            "source": self.source,
            "dependency": self.dependency,
            "upstream_project": self.upstream_project,
            "upstream_version": self.upstream_version,
            "requirements_source": self.requirements_source,
            "requirements_path": self.requirements_path,
            "requirements_files": list(self.requirements_files),
            "reason": self.reason,
        }


def suggest_cycle_edge_exclusions(
    edges: list[tuple[str, str]],
    packaging_repos: dict[str, Path] | None,
    upstream_versions: dict[str, str] | None,
    source_to_project: dict[str, str] | None,
    package_index: PackageIndex | None,
    upstream_cache_base: Path | None,
) -> list[CycleEdgeSuggestion]:
    """Suggest dependency edges to exclude based on upstream requirements.

    Args:
        edges: Cycle edges as (source, dependency) pairs.
        packaging_repos: Optional mapping of source package -> repo path.
        upstream_versions: Optional mapping of source package -> upstream version.
        source_to_project: Optional mapping of source package -> upstream project name.
        package_index: Package index for binary->source mapping.
        upstream_cache_base: Path to cached upstream tarball extractions.

    Returns:
        List of suggestions for edges to exclude.
    """
    suggestions: list[CycleEdgeSuggestion] = []
    if not edges:
        return suggestions

    edges_by_source: dict[str, list[str]] = {}
    for source, dependency in sorted(set(edges)):
        edges_by_source.setdefault(source, []).append(dependency)

    upstream_versions = upstream_versions or {}
    source_to_project = source_to_project or {}

    for source in sorted(edges_by_source):
        upstream_project = source_to_project.get(source, source)
        upstream_version = upstream_versions.get(source, "")
        repo_path, repo_source, repo_files = _resolve_requirements_repo(
            source=source,
            packaging_repos=packaging_repos,
            upstream_project=upstream_project,
            upstream_version=upstream_version,
            upstream_cache_base=upstream_cache_base,
        )
        if repo_path is None:
            continue

        upstream_deps = extract_upstream_deps(repo_path)
        runtime_deps = list(upstream_deps.runtime) + list(upstream_deps.build)
        if not runtime_deps:
            continue

        upstream_sources = _map_upstream_deps_to_sources(runtime_deps, package_index)

        for dependency in sorted(edges_by_source[source]):
            if dependency in upstream_sources:
                continue
            suggestions.append(
                CycleEdgeSuggestion(
                    source=source,
                    dependency=dependency,
                    upstream_project=upstream_project,
                    upstream_version=upstream_version,
                    requirements_source=repo_source,
                    requirements_path=str(repo_path),
                    requirements_files=repo_files,
                    reason=f"{dependency} not found in upstream requirements",
                )
            )

    return suggestions


def _resolve_requirements_repo(
    source: str,
    packaging_repos: dict[str, Path] | None,
    upstream_project: str,
    upstream_version: str,
    upstream_cache_base: Path | None,
) -> tuple[Path | None, str, list[str]]:
    repo_path = None
    repo_source = ""
    repo_files: list[str] = []

    if packaging_repos:
        pkg_repo = packaging_repos.get(source)
        if pkg_repo and pkg_repo.exists():
            repo_files = _list_requirement_files(pkg_repo)
            if repo_files:
                return pkg_repo, "packaging_repo", repo_files

    if upstream_cache_base and upstream_project and upstream_version:
        cached = get_cached_extraction(
            upstream_project,
            upstream_version,
            cache_base=upstream_cache_base,
        )
        if cached:
            source_dir = find_source_dir(cached) or cached
            repo_files = _list_requirement_files(source_dir)
            if repo_files:
                repo_path = source_dir
                repo_source = "tarball_cache"

    if repo_path is None:
        return None, "", []

    return repo_path, repo_source, repo_files


def _list_requirement_files(repo_path: Path) -> list[str]:
    return [name for name in REQUIREMENTS_FILES if (repo_path / name).exists()]


def _map_upstream_deps_to_sources(
    deps: list[tuple[str, str]],
    package_index: PackageIndex | None,
) -> set[str]:
    sources: set[str] = set()
    for python_dep, _ in deps:
        sources.update(_python_dep_to_sources(python_dep, package_index))
    return sources


def _python_dep_to_sources(
    python_dep: str,
    package_index: PackageIndex | None,
) -> set[str]:
    debian_name, _ = map_python_to_debian(python_dep)
    candidates: set[str] = set()

    if debian_name:
        if package_index:
            pkg = package_index.find_package(debian_name)
            if pkg and pkg.source:
                candidates.add(pkg.source)
        if debian_name.startswith("python3-"):
            candidates.add(f"python-{debian_name[8:]}")
        candidates.add(debian_name)

    candidates.add(project_to_source_package(python_dep))
    return {candidate for candidate in candidates if candidate}
