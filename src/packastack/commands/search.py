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

"""Implementation of `packastack search` command.

Search for targets using the same resolution system as build/plan.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import typer

from packastack.core.config import load_config
from packastack.core.paths import resolve_paths
from packastack.core.run import RunContext, activity
from packastack.target.completion import generate_completion_index, save_completion_index
from packastack.target.resolution import (
    MatchMode,
    OriginSource,
    Scope,
    TargetResolver,
    parse_target_expr,
)
from packastack.upstream.registry import UpstreamsRegistry
from packastack.upstream.releases import get_current_development_series

EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR = 1


def search(
    target: str = typer.Argument(..., help="Target expression to search for"),
    scope: str = typer.Option("", "--scope", help="Restrict search to scope (source|canonical|deliverable|repo)"),
    output_format: str = typer.Option("text", "--format", "-f", help="Output format: text|json"),
    openstack_target: str = typer.Option("", "-t", "--target", help="OpenStack series target (auto-detected if not specified)"),
    refresh_cache: bool = typer.Option(False, "--refresh-cache", help="Refresh completion cache"),
) -> None:
    """Search for targets using target expressions.

    Examples:
        packastack search glance             # Exact match
        packastack search ^glance            # Prefix match
        packastack search ~glance            # Contains match
        packastack search canonical:openstack/glance  # Scoped exact
    """
    try:
        with RunContext("search") as run:
            # Load config
            config = load_config()
            paths = resolve_paths(config)

            # Determine OpenStack target if not specified
            if not openstack_target:
                releases_repo = paths.get("openstack_releases_repo")
                if releases_repo:
                    detected = get_current_development_series(releases_repo)
                    if detected:
                        openstack_target = detected
                        activity("search", f"Auto-detected OpenStack series: {openstack_target}")

            # Load registry
            registry: UpstreamsRegistry | None = None
            try:
                registry = UpstreamsRegistry()
            except Exception as e:
                activity("warning", f"Failed to load registry: {e}")

            # Refresh cache if requested
            if refresh_cache and registry:
                activity("search", "Refreshing completion cache...")
                index = generate_completion_index(
                    registry=registry,
                    local_repo=paths.get("local_apt_repo"),
                    releases_repo=paths.get("openstack_releases_repo"),
                    openstack_target=openstack_target,
                )
                save_completion_index(index)
                activity("search", "Completion cache updated")

            # Parse target expression
            try:
                expr = parse_target_expr(target)
            except ValueError as e:
                activity("error", f"Invalid target expression: {e}")
                raise typer.Exit(EXIT_CONFIG_ERROR) from None

            # Override scope if provided
            if scope:
                try:
                    expr.scope = Scope(scope.lower())
                except ValueError:
                    activity(
                        "error",
                        f"Invalid scope '{scope}'. Valid: {', '.join(s.value for s in Scope)}",
                    )
                    raise typer.Exit(EXIT_CONFIG_ERROR) from None

            # Create resolver
            resolver = TargetResolver(
                registry=registry,
                local_repo=paths.get("local_apt_repo"),
                releases_repo=paths.get("openstack_releases_repo"),
                openstack_target=openstack_target,
            )

            # Resolve with all_matches=True to get all candidates
            result = resolver.resolve(expr, all_matches=True)

            # Collect candidates
            candidates = result.candidates if result.candidates else []
            if result.identity:
                candidates = [result.identity]

            # Output results
            if output_format == "json":
                _output_json(candidates)
            else:
                _output_text(expr, candidates, run)
    except Exception as e:
        activity("error", f"Search failed: {e}")
        raise typer.Exit(EXIT_CONFIG_ERROR) from None


def _output_text(expr: Any, candidates: list[Any], run: RunContext) -> None:
    """Output search results in text format.

    Args:
        expr: Parsed target expression
        candidates: List of matching identities
        run: Run context
    """
    activity("search", f"Target expression: {expr.raw_input}")
    activity("search", f"Match mode: {expr.match_mode.value}")
    if expr.scope:
        activity("search", f"Scope: {expr.scope.value}")

    if not candidates:
        activity("search", "No matches found")
        return

    activity("search", f"Found {len(candidates)} match(es):")
    print(file=sys.__stdout__)

    for identity in candidates:
        print(f"  Source Package:     {identity.source_package}", file=sys.__stdout__)
        print(f"  Canonical Upstream: {identity.canonical_upstream}", file=sys.__stdout__)
        if identity.deliverable_name:
            print(f"  Deliverable:        {identity.deliverable_name}", file=sys.__stdout__)
        print(f"  Kind:               {identity.kind.value}", file=sys.__stdout__)
        print(f"  Governed:           {identity.governed_by_openstack}", file=sys.__stdout__)
        print(f"  Origin:             {identity.origin.value}", file=sys.__stdout__)
        if identity.aliases:
            print(f"  Aliases:            {', '.join(identity.aliases)}", file=sys.__stdout__)
        print(file=sys.__stdout__)

    run.log_event({
        "event": "search.complete",
        "target": expr.raw_input,
        "matches": len(candidates),
    })


def _output_json(candidates: list[Any]) -> None:
    """Output search results in JSON format.

    Args:
        candidates: List of matching identities
    """
    output: list[dict[str, Any]] = []

    for identity in candidates:
        output.append({
            "source_package": identity.source_package,
            "canonical_upstream": identity.canonical_upstream,
            "deliverable_name": identity.deliverable_name,
            "governed_by_openstack": identity.governed_by_openstack,
            "kind": identity.kind.value,
            "origin": identity.origin.value,
            "aliases": identity.aliases,
        })

    print(json.dumps(output, indent=2), file=sys.__stdout__)
