# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Tests for version conversion utilities."""

import pytest

from packastack.exceptions import DebianError
from packastack.package.version import VersionConverter


class TestBetaVersionConversion:
    """Tests for beta version conversion."""

    def test_convert_beta_with_leading_zero(self):
        """Test converting beta version with leading zero."""
        result = VersionConverter.convert_beta_version("12.0.0.0b0")
        assert result == "12.0.0~b0"

    def test_convert_beta_without_leading_zero(self):
        """Test converting beta version without leading zero."""
        result = VersionConverter.convert_beta_version("12.0.0b1")
        assert result == "12.0.0~b1"

    def test_convert_beta_multiple_digits(self):
        """Test converting beta version with multiple beta digits."""
        result = VersionConverter.convert_beta_version("1.2.3.0b10")
        assert result == "1.2.3~b10"

    def test_convert_invalid_beta_version(self):
        """Test converting invalid beta version raises error."""
        with pytest.raises(DebianError):
            VersionConverter.convert_beta_version("12.0.0")

    def test_convert_beta_with_extra_components(self):
        """Test converting beta with extra version components."""
        result = VersionConverter.convert_beta_version("1.2.3.4.0b2")
        assert result == "1.2.3.4~b2"


class TestCandidateVersionConversion:
    """Tests for release candidate version conversion."""

    def test_convert_rc_with_leading_zero(self):
        """Test converting RC version with leading zero."""
        result = VersionConverter.convert_candidate_version("12.0.0.0rc0")
        assert result == "12.0.0~rc0"

    def test_convert_rc_without_leading_zero(self):
        """Test converting RC version without leading zero."""
        result = VersionConverter.convert_candidate_version("12.0.0rc1")
        assert result == "12.0.0~rc1"

    def test_convert_rc_multiple_digits(self):
        """Test converting RC version with multiple RC digits."""
        result = VersionConverter.convert_candidate_version("1.2.3.0rc10")
        assert result == "1.2.3~rc10"

    def test_convert_invalid_rc_version(self):
        """Test converting invalid RC version raises error."""
        with pytest.raises(DebianError):
            VersionConverter.convert_candidate_version("12.0.0")


class TestReleaseVersionConversion:
    """Tests for release version conversion."""

    def test_convert_release_without_trailing_zero(self):
        """Test converting release version without trailing zero."""
        result = VersionConverter.convert_release_version("12.0.0")
        assert result == "12.0.0"

    def test_convert_release_with_trailing_zero(self):
        """Test converting release version with trailing zero."""
        result = VersionConverter.convert_release_version("12.0.0.0")
        assert result == "12.0.0"

    def test_convert_release_simple(self):
        """Test converting simple release version."""
        result = VersionConverter.convert_release_version("1.2.3")
        assert result == "1.2.3"


class TestSnapshotVersionConversion:
    """Tests for snapshot version conversion."""

    def test_convert_snapshot_basic(self):
        """Test basic snapshot version conversion."""
        result = VersionConverter.convert_snapshot_version("12.0.0-5-gabcdef")
        assert result == "12.0.0+5-gabcdef.1-1ubuntu0"

    def test_convert_snapshot_with_v_prefix(self):
        """Test snapshot conversion with v prefix in tag."""
        result = VersionConverter.convert_snapshot_version("v12.0.0-5-gabcdef")
        assert result == "12.0.0+5-gabcdef.1-1ubuntu0"

    def test_convert_snapshot_with_existing_version(self):
        """Test snapshot conversion with existing version."""
        result = VersionConverter.convert_snapshot_version(
            "12.0.0-5-gabcdef", existing_version="12.0.0+5-gabcdef-1ubuntu0"
        )
        assert result == "12.0.0+5-gabcdef.1-1ubuntu0"

    def test_convert_snapshot_with_existing_counter(self):
        """Test snapshot conversion incrementing existing counter."""
        result = VersionConverter.convert_snapshot_version(
            "12.0.0-5-gabcdef", existing_version="12.0.0+5-gabcdef.2-1ubuntu0"
        )
        assert result == "12.0.0+5-gabcdef.3-1ubuntu0"

    def test_convert_snapshot_invalid_format(self):
        """Test snapshot conversion with invalid format."""
        with pytest.raises(DebianError):
            VersionConverter.convert_snapshot_version("invalid-format")


class TestVersionTypeDetection:
    """Tests for version type detection."""

    def test_detect_beta_version(self):
        """Test detecting beta version."""
        assert VersionConverter.detect_version_type("12.0.0.0b0") == "beta"
        assert VersionConverter.detect_version_type("1.2.3b1") == "beta"

    def test_detect_candidate_version(self):
        """Test detecting RC version."""
        assert VersionConverter.detect_version_type("12.0.0.0rc0") == "candidate"
        assert VersionConverter.detect_version_type("1.2.3rc1") == "candidate"

    def test_detect_release_version(self):
        """Test detecting release version."""
        assert VersionConverter.detect_version_type("12.0.0") == "release"
        assert VersionConverter.detect_version_type("1.2.3.4") == "release"

    def test_detect_unknown_version(self):
        """Test detecting unknown version."""
        assert VersionConverter.detect_version_type("invalid") == "unknown"
        assert VersionConverter.detect_version_type("v1.2.3-beta") == "unknown"


class TestSnapshotVersionEdgeCases:
    """Tests for edge cases in snapshot version conversion."""

    def test_convert_snapshot_with_beta_tag(self):
        """Test snapshot conversion with beta tag."""
        result = VersionConverter.convert_snapshot_version("12.0.0.0b1-5-gabcdef")
        assert result == "12.0.0~b1+5-gabcdef.1-1ubuntu0"

    def test_convert_snapshot_with_rc_tag(self):
        """Test snapshot conversion with RC tag."""
        result = VersionConverter.convert_snapshot_version("12.0.0.0rc1-5-gabcdef")
        assert result == "12.0.0~rc1+5-gabcdef.1-1ubuntu0"

    def test_convert_snapshot_with_invalid_beta(self):
        """Test snapshot conversion with invalid beta tag."""
        result = VersionConverter.convert_snapshot_version("12.0.0b-5-gabcdef")
        # Should leave tag as-is if not standard format
        assert result == "12.0.0b+5-gabcdef.1-1ubuntu0"

    def test_convert_snapshot_with_invalid_rc(self):
        """Test snapshot conversion with invalid RC tag."""
        result = VersionConverter.convert_snapshot_version("12.0.0rc-5-gabcdef")
        # Should leave tag as-is if not standard format
        assert result == "12.0.0rc+5-gabcdef.1-1ubuntu0"

    def test_convert_snapshot_existing_no_counter(self):
        """Test snapshot conversion with existing version but no counter match."""
        result = VersionConverter.convert_snapshot_version(
            "12.0.0-5-gabcdef", existing_version="12.0.0+5-gabcd99-1ubuntu0"
        )
        # Different committish; still append starting counter '1'
        assert result == "12.0.0+5-gabcdef.1-1ubuntu0"

    def test_convert_snapshot_existing_counter_is_none(self):
        """Test snapshot where counter_match group is None."""
        # This tests line 190 - counter_match succeeds but group(1) is None
        # Meaning the pattern matches but there's no counter digit before the dash
        result = VersionConverter.convert_snapshot_version(
            "12.0.0-5-gabcdef", existing_version="12.0.0+5-gabcdef-1ubuntu0"
        )
        # No counter in existing (group(1) is None), so start with "1"
        assert result == "12.0.0+5-gabcdef.1-1ubuntu0"

    def test_convert_snapshot_existing_pattern_no_regex_match(self):
        """Test snapshot where pattern is in version but no regex match."""
        # This tests line 192 - pattern in existing_version but
        # counter_match is None due to malformed existing version
        result = VersionConverter.convert_snapshot_version(
            "12.0.0-5-gabcdef", existing_version="some-12.0.0+5-gabcdefX"
        )
        # Pattern is in version but regex won't match due to format
        assert result == "12.0.0+5-gabcdef.1-1ubuntu0"
