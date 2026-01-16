# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2026 Canonical Ltd.
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

"""Target expression parsing and deterministic resolution.

This module defines the shell-safe grammar for target expressions, a canonical
``TargetIdentity`` model, and deterministic resolution across exact, prefix, and
contains modes. Scopes restrict the match universe before applying tiers and are
preferable when ambiguity is possible.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum


class TargetParseError(ValueError):
    """Raised when a target expression fails to parse."""


class TargetScope(str, Enum):
    SOURCE = "source"
    CANONICAL = "canonical"
    UPSTREAM = "upstream"
    DELIVERABLE = "deliverable"
    REPO = "repo"


class MatchMode(str, Enum):
    EXACT = "exact"
    PREFIX = "prefix"
    CONTAINS = "contains"
    GLOB = "glob"


@dataclass(frozen=True)
class TargetExpr:
    """Parsed representation of a target expression."""

    raw: str
    scope: TargetScope | None
    body: str
    mode: MatchMode


@dataclass(frozen=True)
class TargetIdentity:
    """Canonical identity for a target candidate."""

    source_package: str
    canonical_upstream: str
    deliverable_name: str | None
    governed_by_openstack: bool
    kind: str
    aliases: tuple[str, ...]
    origin: str

    def all_names(self) -> tuple[str, ...]:
        """Return all names usable for matching (case-insensitive)."""

        names: list[str] = [self.source_package, self.canonical_upstream]
        if self.deliverable_name:
            names.append(self.deliverable_name)
        names.extend(self.aliases)
        return tuple(names)


def parse_target_expr(raw: str) -> TargetExpr:
    """Parse a raw target expression string into a structured TargetExpr.

    Grammar (shell safe):
    - exact: ``<IDENT>``
    - prefix: ``^<IDENT>``
    - contains: ``~<IDENT>``
    - glob-style prefix: ``<IDENT>*`` (must be quoted by caller; treated as prefix)
    - scoped: ``<scope>:<body>`` where scope ∈ {source, canonical, upstream, deliverable, repo}

    IDENT characters: [A-Za-z0-9._+-/]
    Matching is case-insensitive but the original casing is preserved in ``body``.
    """

    if not raw:
        raise TargetParseError("Target expression cannot be empty")

    scope: TargetScope | None = None
    body = raw

    if ":" in raw:
        prefix, remainder = raw.split(":", 1)
        if prefix in TargetScope._value2member_map_:
            scope = TargetScope(prefix)
            body = remainder
        else:
            raise TargetParseError(f"Unknown scope '{prefix}' in target expression")

    if not body:
        raise TargetParseError("Target expression body cannot be empty")

    if not _is_valid_ident_body(body):
        raise TargetParseError(
            "Target expression contains invalid characters; allowed: [A-Za-z0-9._+-/]*"
        )

    if body.startswith("^"):
        mode = MatchMode.PREFIX
        ident = body[1:]
    elif body.startswith("~"):
        mode = MatchMode.CONTAINS
        ident = body[1:]
    elif body.endswith("*"):
        mode = MatchMode.GLOB
        ident = body[:-1]
    else:
        mode = MatchMode.EXACT
        ident = body

    if not ident:
        raise TargetParseError("Target identifier cannot be empty")

    return TargetExpr(raw=raw, scope=scope, body=ident, mode=mode)


def detect_shell_expansion(raw_args: Sequence[str]) -> bool:
    """Detect likely unintended shell glob expansion.

    Heuristic:
    - multiple positional args
    - none contain ^ or ~ or a scope prefix
    - they share a common prefix
    """

    if len(raw_args) < 2:
        return False

    cleaned = [arg for arg in raw_args if arg]
    if any("^" in arg or "~" in arg or ":" in arg for arg in cleaned):
        return False

    prefix = _common_prefix(cleaned)
    return bool(prefix and len(prefix) >= 2)


def resolve_targets(
    expr: TargetExpr,
    identities: Iterable[TargetIdentity],
    allow_all_matches: bool = False,
) -> tuple[list[TargetIdentity], MatchMode]:
    """Resolve a target expression against candidate identities.

    Resolution tiers (stopping at first tier with exactly one match unless
    ``allow_all_matches`` is True and a fuzzy mode is used):
    1) exact match on source_package
    2) exact match on canonical_upstream
    3) exact match on deliverable_name (OpenStack governed)
    4) exact match on aliases
    5) prefix (^ or glob) match
    6) contains (~) match

    Scopes restrict the candidate universe before applying tiers.
    Returns (matches, effective_mode). Raises ValueError on ambiguity when
    not allowed.
    """

    scoped_identities = _apply_scope(expr.scope, identities)
    if not scoped_identities:
        return [], expr.mode

    query = expr.body.lower()
    mode = expr.mode

    def exact(key_fn) -> list[TargetIdentity]:
        return [i for i in scoped_identities if key_fn(i).lower() == query]

    def prefix(key_fn) -> list[TargetIdentity]:
        return [i for i in scoped_identities if key_fn(i).lower().startswith(query)]

    def contains(key_fn) -> list[TargetIdentity]:
        return [i for i in scoped_identities if query in key_fn(i).lower()]

    tiers: list[tuple[MatchMode, list[TargetIdentity]]] = []
    tiers.append((MatchMode.EXACT, exact(lambda i: i.source_package)))
    tiers.append((MatchMode.EXACT, exact(lambda i: i.canonical_upstream)))
    tiers.append((MatchMode.EXACT, [i for i in scoped_identities if i.deliverable_name and i.deliverable_name.lower() == query]))
    tiers.append((MatchMode.EXACT, [i for i in scoped_identities if any(alias.lower() == query for alias in i.aliases)]))

    if mode in (MatchMode.PREFIX, MatchMode.GLOB, MatchMode.EXACT):
        tiers.append((MatchMode.PREFIX, prefix(lambda i: i.source_package)))
        tiers.append((MatchMode.PREFIX, prefix(lambda i: i.canonical_upstream)))
        tiers.append((MatchMode.PREFIX, [i for i in scoped_identities if i.deliverable_name and i.deliverable_name.lower().startswith(query)]))
        tiers.append((MatchMode.PREFIX, [i for i in scoped_identities if any(alias.lower().startswith(query) for alias in i.aliases)]))

    if mode in (MatchMode.CONTAINS, MatchMode.PREFIX, MatchMode.GLOB, MatchMode.EXACT):
        tiers.append((MatchMode.CONTAINS, contains(lambda i: i.source_package)))
        tiers.append((MatchMode.CONTAINS, contains(lambda i: i.canonical_upstream)))
        tiers.append((MatchMode.CONTAINS, [i for i in scoped_identities if i.deliverable_name and query in i.deliverable_name.lower()]))
        tiers.append((MatchMode.CONTAINS, [i for i in scoped_identities if any(query in alias.lower() for alias in i.aliases)]))

    for tier_mode, matches in tiers:
        if not matches:
            continue
        if tier_mode == MatchMode.EXACT:
            return matches, tier_mode
        if len(matches) == 1:
            return matches, tier_mode
        if allow_all_matches:
            return sorted(matches, key=lambda i: i.source_package.lower()), tier_mode
        raise ValueError("Ambiguous match; specify scope or use --all-matches")

    return [], mode


def _apply_scope(scope: TargetScope | None, identities: Iterable[TargetIdentity]) -> list[TargetIdentity]:
    """Filter identities by scope."""

    if scope is None:
        return list(identities)

    scoped: list[TargetIdentity] = []
    for ident in identities:
        if scope == TargetScope.SOURCE:
            scoped.append(ident)
        elif scope in (TargetScope.CANONICAL, TargetScope.REPO, TargetScope.UPSTREAM):
            if ident.canonical_upstream:
                scoped.append(ident)
        elif scope == TargetScope.DELIVERABLE and ident.deliverable_name:
            scoped.append(ident)
    return scoped


def _is_valid_ident_body(value: str) -> bool:
    """Return True if value contains only allowed IDENT characters."""

    for ch in value:
        if not (
            "A" <= ch <= "Z"
            or "a" <= ch <= "z"
            or "0" <= ch <= "9"
            or ch in {".", "_", "+", "-", "/", "^", "~", "*"}
        ):
            return False
    return True


def _common_prefix(values: Sequence[str]) -> str:
    """Compute common prefix for a sequence of strings."""

    if not values:
        return ""
    prefix = values[0]
    for val in values[1:]:
        while not val.startswith(prefix) and prefix:
            prefix = prefix[:-1]
        if not prefix:
            break
    return prefix
