# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for packastack.debpkg.version module."""

from __future__ import annotations

import pytest

from packastack.debpkg.version import (
    ParsedVersion,
    compare_versions,
    extract_upstream_version,
    format_version_constraint,
    normalize_upstream_version,
    parse_debian_version,
    strip_epoch,
    upstream_version_newer,
    version_satisfies_constraint,
    versions_equal_upstream,
)


class TestParseDebianVersion:
    """Tests for parse_debian_version function."""

    def test_simple_version(self) -> None:
        """Test parsing a simple version."""
        parsed = parse_debian_version("1.0.0")
        assert parsed.epoch == 0
        assert parsed.upstream == "1.0.0"
        assert parsed.debian_revision == ""

    def test_version_with_revision(self) -> None:
        """Test parsing version with Debian revision."""
        parsed = parse_debian_version("1.0.0-1")
        assert parsed.epoch == 0
        assert parsed.upstream == "1.0.0"
        assert parsed.debian_revision == "1"

    def test_version_with_ubuntu_revision(self) -> None:
        """Test parsing version with Ubuntu revision."""
        parsed = parse_debian_version("29.0.0-0ubuntu1")
        assert parsed.epoch == 0
        assert parsed.upstream == "29.0.0"
        assert parsed.debian_revision == "0ubuntu1"

    def test_version_with_epoch(self) -> None:
        """Test parsing version with epoch."""
        parsed = parse_debian_version("1:29.0.0-0ubuntu1")
        assert parsed.epoch == 1
        assert parsed.upstream == "29.0.0"
        assert parsed.debian_revision == "0ubuntu1"

    def test_version_with_tilde(self) -> None:
        """Test parsing version with tilde (pre-release)."""
        parsed = parse_debian_version("1.0.0~b1-1")
        assert parsed.epoch == 0
        assert parsed.upstream == "1.0.0~b1"
        assert parsed.debian_revision == "1"

    def test_version_multiple_hyphens(self) -> None:
        """Test parsing version with multiple hyphens in upstream."""
        parsed = parse_debian_version("2024-01-01-1ubuntu1")
        assert parsed.epoch == 0
        assert parsed.upstream == "2024-01-01"
        assert parsed.debian_revision == "1ubuntu1"

    def test_upstream_only_property(self) -> None:
        """Test the upstream_only property."""
        parsed = parse_debian_version("1:29.0.0-0ubuntu1")
        assert parsed.upstream_only == "29.0.0"

    def test_invalid_epoch_defaults_to_zero(self) -> None:
        """Test that invalid epoch value defaults to 0."""
        # Invalid epoch like 'abc:' should default to 0
        parsed = parse_debian_version("abc:1.0.0-1")
        assert parsed.epoch == 0
        assert parsed.upstream == "1.0.0"

    def test_version_str_method(self) -> None:
        """Test __str__ representation of ParsedVersion."""
        parsed = parse_debian_version("1.0.0-1")
        assert str(parsed) == "1.0.0-1"

        parsed_with_epoch = parse_debian_version("1:29.0.0-0ubuntu1")
        assert str(parsed_with_epoch) == "1:29.0.0-0ubuntu1"

        native = parse_debian_version("1.0.0")
        assert str(native) == "1.0.0"


class TestParsedVersionComparison:
    """Tests for ParsedVersion comparison."""

    def test_equal_versions(self) -> None:
        """Test equal versions."""
        v1 = parse_debian_version("1.0.0-1")
        v2 = parse_debian_version("1.0.0-1")
        assert v1 == v2

    def test_less_than(self) -> None:
        """Test less than comparison."""
        v1 = parse_debian_version("1.0.0-1")
        v2 = parse_debian_version("2.0.0-1")
        assert v1 < v2

    def test_greater_than(self) -> None:
        """Test greater than comparison."""
        v1 = parse_debian_version("2.0.0-1")
        v2 = parse_debian_version("1.0.0-1")
        assert v1 > v2

    def test_epoch_precedence(self) -> None:
        """Test that epoch takes precedence."""
        v1 = parse_debian_version("1:1.0.0-1")
        v2 = parse_debian_version("99.0.0-1")
        assert v1 > v2  # Epoch 1 is greater than no epoch

    def test_str_representation(self) -> None:
        """Test string representation."""
        v = parse_debian_version("1:29.0.0-0ubuntu1")
        assert str(v) == "1:29.0.0-0ubuntu1"

    def test_str_no_epoch(self) -> None:
        """Test string representation without epoch."""
        v = parse_debian_version("29.0.0-0ubuntu1")
        assert str(v) == "29.0.0-0ubuntu1"

    def test_hash(self) -> None:
        """Test that versions are hashable."""
        v1 = parse_debian_version("1.0.0-1")
        v2 = parse_debian_version("1.0.0-1")
        assert hash(v1) == hash(v2)
        s = {v1, v2}
        assert len(s) == 1

    def test_not_implemented_for_non_parsed_version(self) -> None:
        """Test comparison with non-ParsedVersion returns NotImplemented."""
        v = parse_debian_version("1.0.0-1")
        assert v.__eq__("1.0.0-1") is NotImplemented
        assert v.__lt__("1.0.0-1") is NotImplemented

    def test_fallback_comparison_without_debian_version(self) -> None:
        """Test fallback comparison when DebianVersion is not available."""
        from unittest.mock import patch
        import packastack.debpkg.version as version_module

        v1 = parse_debian_version("1.0.0-1")
        v2 = parse_debian_version("2.0.0-1")
        v3 = parse_debian_version("1.0.0-1")

        # Temporarily disable DebianVersion
        with patch.object(version_module, "DebianVersion", None):
            assert v1 == v3  # Uses string comparison fallback
            assert v1 < v2   # Uses string comparison fallback


class TestExtractUpstreamVersion:
    """Tests for extract_upstream_version function."""

    def test_simple(self) -> None:
        """Test extracting from simple version."""
        assert extract_upstream_version("1.0.0") == "1.0.0"

    def test_with_revision(self) -> None:
        """Test extracting from version with revision."""
        assert extract_upstream_version("29.0.0-0ubuntu1") == "29.0.0"

    def test_with_epoch(self) -> None:
        """Test extracting from version with epoch."""
        assert extract_upstream_version("1:29.0.0-0ubuntu1") == "29.0.0"

    def test_with_tilde(self) -> None:
        """Test extracting pre-release version."""
        assert extract_upstream_version("1.0.0~b1-1") == "1.0.0~b1"


class TestCompareVersions:
    """Tests for compare_versions function."""

    def test_equal(self) -> None:
        """Test equal versions."""
        assert compare_versions("1.0.0", "1.0.0") == 0

    def test_less_than(self) -> None:
        """Test less than."""
        assert compare_versions("1.0.0", "2.0.0") == -1

    def test_greater_than(self) -> None:
        """Test greater than."""
        assert compare_versions("2.0.0", "1.0.0") == 1

    def test_tilde_sorting(self) -> None:
        """Test tilde versions sort before release."""
        # 1.0.0~b1 should be less than 1.0.0
        assert compare_versions("1.0.0~b1", "1.0.0") == -1

    def test_epoch_precedence(self) -> None:
        """Test epoch takes precedence."""
        assert compare_versions("1:1.0.0", "99.0.0") == 1

    def test_fallback_without_debian_version(self) -> None:
        """Test fallback comparison when DebianVersion is not available."""
        from unittest.mock import patch
        import packastack.debpkg.version as version_module

        # Temporarily disable DebianVersion
        with patch.object(version_module, "DebianVersion", None):
            result = compare_versions("1.0.0", "2.0.0")
            assert result == -1

            result = compare_versions("2.0.0", "1.0.0")
            assert result == 1

            result = compare_versions("1.0.0", "1.0.0")
            assert result == 0


class TestVersionSatisfiesConstraint:
    """Tests for version_satisfies_constraint function."""

    def test_greater_equal_satisfied(self) -> None:
        """Test >= constraint satisfied."""
        assert version_satisfies_constraint("2.0.0", ">= 1.0.0") is True
        assert version_satisfies_constraint("1.0.0", ">= 1.0.0") is True

    def test_greater_equal_not_satisfied(self) -> None:
        """Test >= constraint not satisfied."""
        assert version_satisfies_constraint("0.9.0", ">= 1.0.0") is False

    def test_less_equal_satisfied(self) -> None:
        """Test <= constraint satisfied."""
        assert version_satisfies_constraint("1.0.0", "<= 2.0.0") is True
        assert version_satisfies_constraint("2.0.0", "<= 2.0.0") is True

    def test_less_equal_not_satisfied(self) -> None:
        """Test <= constraint not satisfied."""
        assert version_satisfies_constraint("3.0.0", "<= 2.0.0") is False

    def test_strictly_greater(self) -> None:
        """Test >> constraint."""
        assert version_satisfies_constraint("2.0.0", ">> 1.0.0") is True
        assert version_satisfies_constraint("1.0.0", ">> 1.0.0") is False

    def test_strictly_less(self) -> None:
        """Test << constraint."""
        assert version_satisfies_constraint("0.9.0", "<< 1.0.0") is True
        assert version_satisfies_constraint("1.0.0", "<< 1.0.0") is False

    def test_equality(self) -> None:
        """Test = constraint."""
        assert version_satisfies_constraint("1.0.0", "= 1.0.0") is True
        assert version_satisfies_constraint("1.0.1", "= 1.0.0") is False

    def test_no_operator(self) -> None:
        """Test constraint without operator (equality)."""
        assert version_satisfies_constraint("1.0.0", "1.0.0") is True
        assert version_satisfies_constraint("1.0.1", "1.0.0") is False


class TestVersionsEqualUpstream:
    """Tests for versions_equal_upstream function."""

    def test_equal_same_revision(self) -> None:
        """Test equal versions with same revision."""
        assert versions_equal_upstream("1.0.0-1", "1.0.0-1") is True

    def test_equal_different_revision(self) -> None:
        """Test equal upstream with different revision."""
        assert versions_equal_upstream("1.0.0-1", "1.0.0-2") is True

    def test_equal_with_epoch(self) -> None:
        """Test equal with epoch difference."""
        # Same upstream, different epoch - should still be equal upstream
        assert versions_equal_upstream("1:1.0.0-1", "1.0.0-1") is True

    def test_different_upstream(self) -> None:
        """Test different upstream versions."""
        assert versions_equal_upstream("1.0.0-1", "2.0.0-1") is False


class TestUpstreamVersionNewer:
    """Tests for upstream_version_newer function."""

    def test_newer(self) -> None:
        """Test newer upstream version."""
        assert upstream_version_newer("1.0.0-1", "2.0.0") is True

    def test_not_newer_same(self) -> None:
        """Test same version is not newer."""
        assert upstream_version_newer("1.0.0-1", "1.0.0") is False

    def test_not_newer_older(self) -> None:
        """Test older version is not newer."""
        assert upstream_version_newer("2.0.0-1", "1.0.0") is False

    def test_candidate_with_revision(self) -> None:
        """Test candidate with revision."""
        assert upstream_version_newer("1.0.0-1", "2.0.0-0ubuntu1") is True


class TestFormatVersionConstraint:
    """Tests for format_version_constraint function."""

    def test_default_relation(self) -> None:
        """Test default >= relation."""
        result = format_version_constraint("python3-oslo.config", "9.0.0")
        assert result == "python3-oslo.config (>= 9.0.0)"

    def test_custom_relation(self) -> None:
        """Test custom relation."""
        result = format_version_constraint("python3-nova", "29.0.0", "<<")
        assert result == "python3-nova (<< 29.0.0)"


class TestStripEpoch:
    """Tests for strip_epoch function."""

    def test_with_epoch(self) -> None:
        """Test stripping epoch."""
        assert strip_epoch("1:29.0.0-0ubuntu1") == "29.0.0-0ubuntu1"

    def test_without_epoch(self) -> None:
        """Test version without epoch."""
        assert strip_epoch("29.0.0-0ubuntu1") == "29.0.0-0ubuntu1"

    def test_high_epoch(self) -> None:
        """Test high epoch value."""
        assert strip_epoch("99:1.0.0-1") == "1.0.0-1"


class TestNormalizeUpstreamVersion:
    """Tests for normalize_upstream_version function."""

    def test_whitespace_stripped(self) -> None:
        """Test whitespace is stripped."""
        assert normalize_upstream_version("  1.0.0  ") == "1.0.0"

    def test_empty_becomes_zero(self) -> None:
        """Test empty version becomes 0."""
        assert normalize_upstream_version("") == "0"

    def test_normal_version_unchanged(self) -> None:
        """Test normal version unchanged."""
        assert normalize_upstream_version("29.0.0") == "29.0.0"
