# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for target expression parsing and resolution."""

from __future__ import annotations

import pytest

from packastack.target.resolution import (
    MatchMode,
    OriginSource,
    Scope,
    TargetIdentity,
    TargetKind,
    TargetResolver,
    detect_shell_expansion,
    parse_target_expr,
)


class TestTargetExprParsing:
    """Test target expression parsing."""

    def test_parse_exact_match(self) -> None:
        """Test parsing exact match expression."""
        expr = parse_target_expr("glance")
        assert expr.raw_input == "glance"
        assert expr.scope is None
        assert expr.match_mode == MatchMode.EXACT
        assert expr.identifier == "glance"

    def test_parse_prefix_match(self) -> None:
        """Test parsing prefix match expression."""
        expr = parse_target_expr("^glance")
        assert expr.raw_input == "^glance"
        assert expr.scope is None
        assert expr.match_mode == MatchMode.PREFIX
        assert expr.identifier == "glance"

    def test_parse_contains_match(self) -> None:
        """Test parsing contains match expression."""
        expr = parse_target_expr("~glance")
        assert expr.raw_input == "~glance"
        assert expr.scope is None
        assert expr.match_mode == MatchMode.CONTAINS
        assert expr.identifier == "glance"

    def test_parse_glob_match(self) -> None:
        """Test parsing glob match expression."""
        expr = parse_target_expr("glance*")
        assert expr.raw_input == "glance*"
        assert expr.scope is None
        assert expr.match_mode == MatchMode.GLOB
        assert expr.identifier == "glance"

    def test_parse_scoped_exact(self) -> None:
        """Test parsing scoped exact match."""
        expr = parse_target_expr("source:glance")
        assert expr.raw_input == "source:glance"
        assert expr.scope == Scope.SOURCE
        assert expr.match_mode == MatchMode.EXACT
        assert expr.identifier == "glance"

    def test_parse_scoped_prefix(self) -> None:
        """Test parsing scoped prefix match."""
        expr = parse_target_expr("canonical:^openstack/glance")
        assert expr.raw_input == "canonical:^openstack/glance"
        assert expr.scope == Scope.CANONICAL
        assert expr.match_mode == MatchMode.PREFIX
        assert expr.identifier == "openstack/glance"

    def test_parse_scoped_contains(self) -> None:
        """Test parsing scoped contains match."""
        expr = parse_target_expr("deliverable:~glance")
        assert expr.raw_input == "deliverable:~glance"
        assert expr.scope == Scope.DELIVERABLE
        assert expr.match_mode == MatchMode.CONTAINS
        assert expr.identifier == "glance"

    def test_parse_canonical_with_slash(self) -> None:
        """Test parsing canonical ID with slash."""
        expr = parse_target_expr("canonical:gnocchixyz/gnocchi")
        assert expr.identifier == "gnocchixyz/gnocchi"

    def test_parse_invalid_scope(self) -> None:
        """Test parsing with invalid scope."""
        with pytest.raises(ValueError, match="Invalid scope"):
            parse_target_expr("invalid:glance")

    def test_parse_empty_expression(self) -> None:
        """Test parsing empty expression."""
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_target_expr("")

    def test_parse_empty_identifier(self) -> None:
        """Test parsing expression with empty identifier."""
        with pytest.raises(ValueError, match="Empty identifier"):
            parse_target_expr("^")

    def test_parse_invalid_characters(self) -> None:
        """Test parsing with invalid characters."""
        with pytest.raises(ValueError, match="only.*allowed"):
            parse_target_expr("glance@ubuntu")


class TestShellExpansionDetection:
    """Test shell expansion detection."""

    def test_no_expansion_single_target(self) -> None:
        """Test single target is not detected as expansion."""
        assert not detect_shell_expansion(["glance"])

    def test_no_expansion_with_markers(self) -> None:
        """Test targets with markers not detected as expansion."""
        assert not detect_shell_expansion(["^glance", "^nova"])

    def test_no_expansion_with_scope(self) -> None:
        """Test scoped targets not detected as expansion."""
        assert not detect_shell_expansion(["source:glance", "source:nova"])

    def test_expansion_detected_common_prefix(self) -> None:
        """Test expansion detected with common prefix."""
        assert detect_shell_expansion(["glance", "glance-store"])

    def test_expansion_detected_multiple_similar(self) -> None:
        """Test expansion detected with multiple similar names."""
        assert detect_shell_expansion(["python-glance", "python-glanceclient"])

    def test_no_expansion_different_names(self) -> None:
        """Test no expansion with different names."""
        assert not detect_shell_expansion(["glance", "nova"])


class TestTargetResolver:
    """Test target resolver."""

    def test_resolver_init_no_registry(self) -> None:
        """Test resolver initialization without registry."""
        resolver = TargetResolver()
        assert resolver.registry is None

    def test_resolve_exact_empty_universe(self) -> None:
        """Test exact resolution with empty universe."""
        resolver = TargetResolver()
        expr = parse_target_expr("glance")
        result = resolver.resolve(expr)

        assert result.expr == expr
        assert result.identity is None
        assert not result.is_ambiguous

    def test_resolve_prefix_empty_universe(self) -> None:
        """Test prefix resolution with empty universe."""
        resolver = TargetResolver()
        expr = parse_target_expr("^glance")
        result = resolver.resolve(expr)

        assert result.expr == expr
        assert result.identity is None
        assert not result.is_ambiguous

    def test_infer_kind_service(self) -> None:
        """Test kind inference for service."""
        resolver = TargetResolver()
        assert resolver._infer_kind("nova") == TargetKind.SERVICE
        assert resolver._infer_kind("glance") == TargetKind.SERVICE

    def test_infer_kind_client(self) -> None:
        """Test kind inference for client."""
        resolver = TargetResolver()
        assert resolver._infer_kind("python-glanceclient") == TargetKind.CLIENT
        assert resolver._infer_kind("novaclient") == TargetKind.CLIENT

    def test_infer_kind_library(self) -> None:
        """Test kind inference for library."""
        resolver = TargetResolver()
        assert resolver._infer_kind("python-oslo.config") == TargetKind.LIBRARY
        assert resolver._infer_kind("oslo.messaging") == TargetKind.LIBRARY

    def test_infer_kind_unknown(self) -> None:
        """Test kind inference for unknown."""
        resolver = TargetResolver()
        assert resolver._infer_kind("somepackage") == TargetKind.UNKNOWN


class TestTargetIdentity:
    """Test TargetIdentity dataclass."""

    def test_identity_creation(self) -> None:
        """Test creating target identity."""
        identity = TargetIdentity(
            source_package="glance",
            canonical_upstream="openstack/glance",
            deliverable_name="glance",
            governed_by_openstack=True,
            kind=TargetKind.SERVICE,
            aliases=["glance"],
            origin=OriginSource.UPSTREAMS_YAML,
        )

        assert identity.source_package == "glance"
        assert identity.canonical_upstream == "openstack/glance"
        assert identity.deliverable_name == "glance"
        assert identity.governed_by_openstack
        assert identity.kind == TargetKind.SERVICE
        assert identity.aliases == ["glance"]
        assert identity.origin == OriginSource.UPSTREAMS_YAML

    def test_identity_defaults(self) -> None:
        """Test identity with default values."""
        identity = TargetIdentity(
            source_package="gnocchi",
            canonical_upstream="gnocchixyz/gnocchi",
            deliverable_name=None,
            governed_by_openstack=False,
            kind=TargetKind.SERVICE,
        )

        assert identity.aliases == []
        assert identity.origin == OriginSource.HEURISTIC
