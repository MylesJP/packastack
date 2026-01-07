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

"""Target expression parsing and resolution for PackaStack.

This module implements a deterministic, shell-safe target resolution system
with support for exact, prefix, and contains matching, scoped expressions,
and canonical upstream identifiers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from packastack.upstream.registry import UpstreamsRegistry


class MatchMode(Enum):
    """Target expression match mode."""

    EXACT = "exact"
    PREFIX = "prefix"
    CONTAINS = "contains"
    GLOB = "glob"


class Scope(Enum):
    """Target expression scope."""

    SOURCE = "source"
    CANONICAL = "canonical"
    UPSTREAM = "upstream"
    DELIVERABLE = "deliverable"
    REPO = "repo"


class TargetKind(Enum):
    """Kind of target package."""

    SERVICE = "service"
    LIBRARY = "library"
    CLIENT = "client"
    PLUGIN = "plugin"
    UNKNOWN = "unknown"


class OriginSource(Enum):
    """Source of target identity information."""

    UPSTREAMS_YAML = "upstreams.yaml"
    OPENSTACK_RELEASES = "openstack/releases"
    WATCH = "watch"
    VCS = "vcs"
    HEURISTIC = "heuristic"


@dataclass
class TargetExpr:
    """Parsed target expression."""

    raw_input: str
    scope: Scope | None
    match_mode: MatchMode
    identifier: str

    def __post_init__(self) -> None:
        """Validate identifier."""
        if not self.identifier:
            raise ValueError("Identifier must not be empty")
        if not re.match(r"^[A-Za-z0-9._+/-]+$", self.identifier):
            raise ValueError(
                f"Invalid identifier '{self.identifier}': "
                "only [A-Za-z0-9._+-/] allowed"
            )


@dataclass
class TargetIdentity:
    """Complete identity information for a resolved target."""

    source_package: str
    canonical_upstream: str
    deliverable_name: str | None
    governed_by_openstack: bool
    kind: TargetKind
    aliases: list[str] = field(default_factory=list)
    origin: OriginSource = OriginSource.HEURISTIC


@dataclass
class ResolutionResult:
    """Result of target resolution."""

    expr: TargetExpr
    identity: TargetIdentity | None
    candidates: list[TargetIdentity] = field(default_factory=list)
    is_ambiguous: bool = False
    shell_expansion_warning: bool = False


def parse_target_expr(raw: str) -> TargetExpr:
    """Parse a target expression string.

    Grammar:
        TargetExpr := [<scope>:]<body>
        <scope> := source|canonical|upstream|deliverable|repo
        <body> := ^<ident> | ~<ident> | <ident>* | <ident>

    Args:
        raw: Raw target expression string

    Returns:
        Parsed TargetExpr

    Raises:
        ValueError: If expression is invalid
    """
    if not raw:
        raise ValueError("Target expression cannot be empty")

    scope: Scope | None = None
    body = raw

    # Check for scope prefix
    if ":" in raw:
        scope_str, body = raw.split(":", 1)
        try:
            scope = Scope(scope_str.lower())
        except ValueError:
            raise ValueError(
                f"Invalid scope '{scope_str}'. "
                f"Valid scopes: {', '.join(s.value for s in Scope)}"
            ) from None

    if not body:
        raise ValueError("Target expression body cannot be empty")

    # Determine match mode
    match_mode: MatchMode
    identifier: str

    if body.startswith("^"):
        match_mode = MatchMode.PREFIX
        identifier = body[1:]
    elif body.startswith("~"):
        match_mode = MatchMode.CONTAINS
        identifier = body[1:]
    elif body.endswith("*"):
        match_mode = MatchMode.GLOB
        identifier = body[:-1]
    else:
        match_mode = MatchMode.EXACT
        identifier = body

    if not identifier:
        raise ValueError(f"Empty identifier in target expression: {raw}")

    return TargetExpr(
        raw_input=raw,
        scope=scope,
        match_mode=match_mode,
        identifier=identifier,
    )


def detect_shell_expansion(targets: list[str]) -> bool:
    """Detect if multiple targets appear to be from shell glob expansion.

    Args:
        targets: List of raw target strings

    Returns:
        True if shell expansion is suspected
    """
    if len(targets) < 2:
        return False

    # Check if any contain special markers (^, ~, scope:)
    for t in targets:
        if "^" in t or "~" in t or ":" in t:
            return False

    # Check for common prefix (lowercase comparison)
    normalized = [t.lower() for t in targets]
    if not normalized:
        return False

    # Find common prefix
    prefix = normalized[0]
    for t in normalized[1:]:
        while prefix and not t.startswith(prefix):
            prefix = prefix[:-1]

    # If common prefix is at least 3 chars, likely shell expansion
    return len(prefix) >= 3


class TargetResolver:
    """Resolves target expressions to TargetIdentity objects."""

    def __init__(
        self,
        registry: UpstreamsRegistry | None = None,
        local_repo: Path | None = None,
        releases_repo: Path | None = None,
        openstack_target: str = "",
    ):
        """Initialize resolver.

        Args:
            registry: Upstreams registry for canonical IDs
            local_repo: Path to local packaging repository
            releases_repo: Path to openstack/releases repository
            openstack_target: OpenStack series target
        """
        self.registry = registry
        self.local_repo = local_repo
        self.releases_repo = releases_repo
        self.openstack_target = openstack_target

    def resolve(
        self,
        expr: TargetExpr,
        all_matches: bool = False,
    ) -> ResolutionResult:
        """Resolve a target expression.

        Resolution tiers (stops at first tier with exactly one match):
          1. Exact downstream source package
          2. Exact canonical_upstream
          3. Exact deliverable/common name (OpenStack governed)
          4. Exact alias
          5. Prefix match (if mode allows)
          6. Contains match (if mode allows)

        Args:
            expr: Parsed target expression
            all_matches: Allow multiple matches for prefix/contains

        Returns:
            ResolutionResult with identity or candidates
        """
        candidates: list[TargetIdentity] = []

        # Apply scope filter if present
        universe = self._get_search_universe(expr.scope)

        # Tier 1-4: Exact matches
        if expr.match_mode == MatchMode.EXACT:
            candidates = self._resolve_exact(expr.identifier, universe)

        # Tier 5: Prefix matches
        elif expr.match_mode in (MatchMode.PREFIX, MatchMode.GLOB):
            candidates = self._resolve_prefix(expr.identifier, universe)

        # Tier 6: Contains matches
        elif expr.match_mode == MatchMode.CONTAINS:
            candidates = self._resolve_contains(expr.identifier, universe)

        # Determine result
        if len(candidates) == 0:
            return ResolutionResult(expr=expr, identity=None)
        elif len(candidates) == 1:
            return ResolutionResult(expr=expr, identity=candidates[0])
        else:
            # Multiple matches
            if all_matches:
                return ResolutionResult(
                    expr=expr,
                    identity=None,
                    candidates=candidates,
                    is_ambiguous=False,
                )
            else:
                return ResolutionResult(
                    expr=expr,
                    identity=None,
                    candidates=candidates,
                    is_ambiguous=True,
                )

    def _get_search_universe(self, scope: Scope | None) -> list[TargetIdentity]:
        """Build the search universe based on scope.

        Args:
            scope: Optional scope to restrict search

        Returns:
            List of all possible TargetIdentity objects
        """
        universe: list[TargetIdentity] = []
        seen_projects: set[str] = set()

        # Load from registry
        if self.registry:
            for project_key in self.registry.list_projects():
                try:
                    resolved = self.registry.resolve(project_key, openstack_governed=False)
                    config = resolved.config

                    # Extract canonical from provenance
                    canonical = config.provenance.canonical or f"openstack/{project_key}"
                    
                    # Determine if governed by OpenStack
                    governed = (
                        config.release_source.type.value == "openstack_releases"
                    )

                    # Determine deliverable name
                    deliverable = config.release_source.deliverable if governed else None

                    # Infer kind from project name or config
                    kind = self._infer_kind(project_key)

                    # Source package hint or default
                    source_pkg = config.ubuntu.source_hint or project_key

                    identity = TargetIdentity(
                        source_package=source_pkg,
                        canonical_upstream=canonical,
                        deliverable_name=deliverable,
                        governed_by_openstack=governed,
                        kind=kind,
                        aliases=config.common_names or [project_key],
                        origin=OriginSource.UPSTREAMS_YAML,
                    )

                    # Scope filter
                    if scope is None or self._matches_scope(identity, scope):
                        universe.append(identity)
                        seen_projects.add(deliverable or project_key)

                except Exception:
                    # Skip projects that fail to resolve
                    continue

        # Load from openstack/releases if not in registry
        if self.releases_repo and self.openstack_target:
            from packastack.upstream.releases import load_openstack_packages

            try:
                packages = load_openstack_packages(self.releases_repo, self.openstack_target)
                
                for source_pkg, project in packages.items():
                    # Skip if already loaded from registry
                    if project in seen_projects:
                        continue

                    # Determine kind from source package name
                    if source_pkg.startswith("python-"):
                        kind = TargetKind.LIBRARY
                    else:
                        kind = TargetKind.SERVICE

                    identity = TargetIdentity(
                        source_package=source_pkg,
                        canonical_upstream=f"openstack/{project}",
                        deliverable_name=project,
                        governed_by_openstack=True,
                        kind=kind,
                        aliases=[project],
                        origin=OriginSource.OPENSTACK_RELEASES,
                    )

                    # Scope filter
                    if scope is None or self._matches_scope(identity, scope):
                        universe.append(identity)
                        seen_projects.add(project)

            except Exception:
                # If we can't load from releases, continue with what we have
                pass

        # TODO: Load from local repo discovery

        return universe

    def _matches_scope(self, identity: TargetIdentity, scope: Scope) -> bool:
        """Check if identity matches scope.

        Args:
            identity: Target identity
            scope: Scope to match

        Returns:
            True if identity matches scope
        """
        if scope == Scope.SOURCE:
            return True  # All have source packages
        elif scope == Scope.CANONICAL:
            return True  # All have canonical IDs
        elif scope == Scope.UPSTREAM:
            return True  # All have upstream
        elif scope == Scope.DELIVERABLE:
            return identity.deliverable_name is not None
        elif scope == Scope.REPO:
            return True  # All have repos
        return True

    def _resolve_exact(
        self,
        identifier: str,
        universe: list[TargetIdentity],
    ) -> list[TargetIdentity]:
        """Resolve exact matches through all tiers.

        Args:
            identifier: Identifier to match
            universe: Search universe

        Returns:
            List of matching identities
        """
        ident_lower = identifier.lower()
        matches: list[TargetIdentity] = []

        for identity in universe:
            # Tier 1: Exact source package
            if identity.source_package.lower() == ident_lower:
                matches.append(identity)
                continue

            # Tier 2: Exact canonical upstream
            if identity.canonical_upstream.lower() == ident_lower:
                matches.append(identity)
                continue

            # Tier 3: Exact deliverable (if governed)
            if identity.governed_by_openstack and identity.deliverable_name:
                if identity.deliverable_name.lower() == ident_lower:
                    matches.append(identity)
                    continue

            # Tier 4: Exact alias
            if any(alias.lower() == ident_lower for alias in identity.aliases):
                matches.append(identity)
                continue

        return matches

    def _resolve_prefix(
        self,
        prefix: str,
        universe: list[TargetIdentity],
    ) -> list[TargetIdentity]:
        """Resolve prefix matches.

        Args:
            prefix: Prefix to match
            universe: Search universe

        Returns:
            List of matching identities
        """
        prefix_lower = prefix.lower()
        matches: list[TargetIdentity] = []

        for identity in universe:
            if (
                identity.source_package.lower().startswith(prefix_lower)
                or identity.canonical_upstream.lower().startswith(prefix_lower)
                or any(alias.lower().startswith(prefix_lower) for alias in identity.aliases)
            ):
                matches.append(identity)

        return sorted(matches, key=lambda x: x.source_package)

    def _resolve_contains(
        self,
        token: str,
        universe: list[TargetIdentity],
    ) -> list[TargetIdentity]:
        """Resolve contains matches.

        Args:
            token: Token to search for
            universe: Search universe

        Returns:
            List of matching identities
        """
        token_lower = token.lower()
        matches: list[TargetIdentity] = []

        for identity in universe:
            if (
                token_lower in identity.source_package.lower()
                or token_lower in identity.canonical_upstream.lower()
                or any(token_lower in alias.lower() for alias in identity.aliases)
            ):
                matches.append(identity)

        return sorted(matches, key=lambda x: x.source_package)

    def _infer_kind(self, project_key: str) -> TargetKind:
        """Infer target kind from project key.

        Args:
            project_key: Project key

        Returns:
            Inferred TargetKind
        """
        key_lower = project_key.lower()

        if "client" in key_lower or key_lower.endswith("client"):
            return TargetKind.CLIENT
        elif key_lower.startswith("python-") or key_lower.startswith("oslo.") or "lib" in key_lower:
            return TargetKind.LIBRARY
        elif any(
            svc in key_lower
            for svc in ["nova", "glance", "neutron", "cinder", "keystone", "swift"]
        ):
            return TargetKind.SERVICE
        elif "plugin" in key_lower or "driver" in key_lower:
            return TargetKind.PLUGIN

        return TargetKind.UNKNOWN
