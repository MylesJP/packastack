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
        with pytest.raises(ValueError, match=r"only.*allowed"):
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


class TestTargetExprValidation:
    """Test TargetExpr validation edge cases."""

    def test_empty_identifier_raises(self) -> None:
        """Constructing a TargetExpr with an empty identifier raises."""
        from packastack.target.resolution import TargetExpr

        with pytest.raises(ValueError, match="must not be empty"):
            TargetExpr(
                raw_input="x",
                scope=None,
                match_mode=MatchMode.EXACT,
                identifier="",
            )

    def test_parse_scope_with_empty_body_raises(self) -> None:
        """A scope prefix with no body raises."""
        with pytest.raises(ValueError, match="body cannot be empty"):
            parse_target_expr("source:")


def _identity(
    source: str,
    canonical: str = "",
    deliverable: str | None = None,
    governed: bool = False,
    aliases: list[str] | None = None,
) -> TargetIdentity:
    return TargetIdentity(
        source_package=source,
        canonical_upstream=canonical or f"openstack/{source}",
        deliverable_name=deliverable,
        governed_by_openstack=governed,
        kind=TargetKind.UNKNOWN,
        aliases=aliases or [],
    )


class TestResolverMatching:
    """Test resolver matching tiers directly."""

    def test_exact_match_on_deliverable(self) -> None:
        """Tier 3: exact deliverable name for governed projects."""
        resolver = TargetResolver()
        universe = [
            _identity("python-glance", deliverable="glance-deliv", governed=True),
        ]
        matches = resolver._resolve_exact("glance-deliv", universe)
        assert len(matches) == 1

    def test_exact_match_on_alias(self) -> None:
        """Tier 4: exact alias match."""
        resolver = TargetResolver()
        universe = [_identity("python-foo", aliases=["foo-alias"])]
        matches = resolver._resolve_exact("foo-alias", universe)
        assert len(matches) == 1

    def test_contains_resolution(self) -> None:
        """Contains expressions match and sort by source package."""
        resolver = TargetResolver()
        universe = [
            _identity("python-novaclient"),
            _identity("python-glanceclient"),
            _identity("nova"),
        ]
        matches = resolver._resolve_contains("client", universe)
        assert [m.source_package for m in matches] == [
            "python-glanceclient",
            "python-novaclient",
        ]

    def test_matches_scope_variants(self) -> None:
        """Scope matching covers every scope value."""
        resolver = TargetResolver()
        with_deliverable = _identity("glance", deliverable="glance", governed=True)
        without_deliverable = _identity("gnocchi")

        for scope in (Scope.SOURCE, Scope.CANONICAL, Scope.UPSTREAM, Scope.REPO):
            assert resolver._matches_scope(with_deliverable, scope)
            assert resolver._matches_scope(without_deliverable, scope)

        assert resolver._matches_scope(with_deliverable, Scope.DELIVERABLE)
        assert not resolver._matches_scope(without_deliverable, Scope.DELIVERABLE)

    def test_infer_kind_plugin(self) -> None:
        """Plugin projects are classified as PLUGIN."""
        resolver = TargetResolver()
        assert resolver._infer_kind("networking-plugin") == TargetKind.PLUGIN


class TestResolverUniverse:
    """Test search universe construction."""

    def test_universe_from_releases_repo(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Universe loads identities from openstack/releases metadata."""
        monkeypatch.setattr(
            "packastack.upstream.releases.load_openstack_packages",
            lambda repo, target: {
                "python-oslo.config": "oslo.config",
                "nova": "nova",
            },
        )
        resolver = TargetResolver(
            releases_repo=tmp_path, openstack_target="dalmatian"
        )
        universe = resolver._get_search_universe(None)

        by_source = {i.source_package: i for i in universe}
        assert by_source["python-oslo.config"].kind == TargetKind.LIBRARY
        assert by_source["nova"].kind == TargetKind.SERVICE
        assert by_source["nova"].origin == OriginSource.OPENSTACK_RELEASES

    def test_universe_releases_load_failure_ignored(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failure loading releases metadata yields an empty universe."""

        def boom(repo, target):
            raise OSError("disk error")

        monkeypatch.setattr(
            "packastack.upstream.releases.load_openstack_packages", boom
        )
        resolver = TargetResolver(
            releases_repo=tmp_path, openstack_target="dalmatian"
        )
        assert resolver._get_search_universe(None) == []

    def test_universe_scope_filters_releases(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scope filter applies to identities from releases metadata."""
        monkeypatch.setattr(
            "packastack.upstream.releases.load_openstack_packages",
            lambda repo, target: {"nova": "nova"},
        )
        resolver = TargetResolver(
            releases_repo=tmp_path, openstack_target="dalmatian"
        )
        universe = resolver._get_search_universe(Scope.DELIVERABLE)
        assert len(universe) == 1

    def test_registry_resolve_failure_skipped(self) -> None:
        """Projects that fail registry resolution are skipped."""

        class FakeRegistry:
            def list_projects(self):
                return ["broken"]

            def resolve(self, key, openstack_governed=False):
                raise KeyError(key)

        resolver = TargetResolver(registry=FakeRegistry())
        assert resolver._get_search_universe(None) == []

    def test_resolve_contains_via_resolver(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end contains resolution returns ambiguous candidates."""
        monkeypatch.setattr(
            "packastack.upstream.releases.load_openstack_packages",
            lambda repo, target: {
                "python-novaclient": "python-novaclient",
                "python-glanceclient": "python-glanceclient",
            },
        )
        resolver = TargetResolver(
            releases_repo=tmp_path, openstack_target="dalmatian"
        )
        expr = parse_target_expr("~client")

        result = resolver.resolve(expr)
        assert result.is_ambiguous
        assert len(result.candidates) == 2

        result_all = resolver.resolve(expr, all_matches=True)
        assert not result_all.is_ambiguous
        assert len(result_all.candidates) == 2
