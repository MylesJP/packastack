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

"""Completion index generation and management for PackaStack.

This module generates and maintains a local completion cache for fast,
offline tab completion in shell environments.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from packastack.upstream.registry import UpstreamsRegistry


def get_completion_cache_path() -> Path:
    """Get path to completion cache file.

    Returns:
        Path to ~/.cache/packastack/completion/index.json
    """
    cache_dir = Path.home() / ".cache" / "packastack" / "completion"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "index.json"


def generate_completion_index(
    registry: UpstreamsRegistry | None = None,
    local_repo: Path | None = None,
    releases_repo: Path | None = None,
    openstack_target: str = "",
) -> dict[str, Any]:
    """Generate completion index from available sources.

    Args:
        registry: Upstreams registry
        local_repo: Path to local packaging repository
        releases_repo: Path to openstack/releases repository
        openstack_target: OpenStack series target

    Returns:
        Completion index dictionary
    """
    source_packages: set[str] = set()
    canonical_ids: set[str] = set()
    deliverables: set[str] = set()
    aliases: set[str] = set()
    seen_projects: set[str] = set()

    # Load from registry
    if registry:
        for project_key in registry.list_projects():
            try:
                resolved = registry.resolve(project_key, openstack_governed=False)
                config = resolved.config

                # Source package
                source_pkg = config.ubuntu.source_hint or project_key
                source_packages.add(source_pkg)

                # Canonical ID
                canonical = config.provenance.canonical or f"openstack/{project_key}"
                canonical_ids.add(canonical)

                # Deliverable (if governed)
                deliverable_name = None
                if config.release_source.type.value == "openstack_releases":
                    if config.release_source.deliverable:
                        deliverable_name = config.release_source.deliverable
                        deliverables.add(deliverable_name)

                # Aliases
                for alias in config.common_names or []:
                    aliases.add(alias)

                # Track seen projects
                seen_projects.add(deliverable_name or project_key)

            except Exception:
                # Skip projects that fail
                continue

    # Load from openstack/releases if not in registry
    if releases_repo and openstack_target:
        from packastack.upstream.releases import load_openstack_packages

        try:
            packages = load_openstack_packages(releases_repo, openstack_target)

            for source_pkg, project in packages.items():
                # Skip if already loaded from registry
                if project in seen_projects:
                    continue

                source_packages.add(source_pkg)
                canonical_ids.add(f"openstack/{project}")
                deliverables.add(project)
                aliases.add(project)

        except Exception:
            # If we can't load from releases, continue with what we have
            pass

    # TODO: Load from local repo discovery

    scopes = [
        "source:",
        "canonical:",
        "repo:",
        "upstream:",
        "deliverable:",
    ]

    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source_packages": sorted(source_packages),
        "canonical_ids": sorted(canonical_ids),
        "deliverables": sorted(deliverables),
        "aliases": sorted(aliases),
        "scopes": scopes,
    }


def save_completion_index(index: dict[str, Any], path: Path | None = None) -> None:
    """Save completion index to cache.

    Args:
        index: Completion index dictionary
        path: Optional path (defaults to standard cache location)
    """
    if path is None:
        path = get_completion_cache_path()

    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w") as f:
        json.dump(index, f, indent=2)


def load_completion_index(path: Path | None = None) -> dict[str, Any] | None:
    """Load completion index from cache.

    Args:
        path: Optional path (defaults to standard cache location)

    Returns:
        Completion index dictionary or None if not found
    """
    if path is None:
        path = get_completion_cache_path()

    if not path.exists():
        return None

    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def get_completions(
    incomplete: str,
    index: dict[str, Any] | None = None,
) -> list[str]:
    """Get completion suggestions for incomplete input.

    Args:
        incomplete: Partial input string
        index: Completion index (loaded if not provided)

    Returns:
        List of completion suggestions
    """
    if index is None:
        index = load_completion_index()
        if index is None:
            return []

    suggestions: set[str] = set()

    # No scope: suggest scopes, source packages, canonical IDs
    if ":" not in incomplete:
        # Suggest scopes
        for scope in index.get("scopes", []):
            if scope.startswith(incomplete.lower()):
                suggestions.add(scope)

        # Suggest source packages
        for pkg in index.get("source_packages", []):
            if pkg.lower().startswith(incomplete.lower()):
                suggestions.add(pkg)

        # Suggest canonical IDs
        for cid in index.get("canonical_ids", []):
            if cid.lower().startswith(incomplete.lower()):
                suggestions.add(cid)

        # Suggest deliverables
        for deliv in index.get("deliverables", []):
            if deliv.lower().startswith(incomplete.lower()):
                suggestions.add(deliv)

        # Suggest aliases
        for alias in index.get("aliases", []):
            if alias.lower().startswith(incomplete.lower()):
                suggestions.add(alias)

    else:
        # Scoped completion
        scope_part, _, incomplete_ident = incomplete.partition(":")

        if scope_part.lower() == "source":
            for pkg in index.get("source_packages", []):
                if pkg.lower().startswith(incomplete_ident.lower()):
                    suggestions.add(f"{scope_part}:{pkg}")

        elif scope_part.lower() in ("canonical", "repo", "upstream"):
            for cid in index.get("canonical_ids", []):
                if cid.lower().startswith(incomplete_ident.lower()):
                    suggestions.add(f"{scope_part}:{cid}")

        elif scope_part.lower() == "deliverable":
            for deliv in index.get("deliverables", []):
                if deliv.lower().startswith(incomplete_ident.lower()):
                    suggestions.add(f"{scope_part}:{deliv}")

    return sorted(suggestions)
