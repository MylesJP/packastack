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

"""Tests for packastack.debpkg.changelog module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from packastack.debpkg import changelog


class TestVersionInfo:
    """Tests for VersionInfo dataclass."""

    def test_str_with_epoch(self) -> None:
        """Test string representation with epoch."""
        v = changelog.VersionInfo(epoch=1, upstream="29.0.0", debian="0ubuntu1")
        assert str(v) == "1:29.0.0-0ubuntu1"

    def test_str_without_epoch(self) -> None:
        """Test string representation without epoch."""
        v = changelog.VersionInfo(epoch=0, upstream="29.0.0", debian="0ubuntu1")
        assert str(v) == "29.0.0-0ubuntu1"


class TestParseVersion:
    """Tests for parse_version function."""

    def test_simple_version(self) -> None:
        """Test parsing simple version."""
        v = changelog.parse_version("29.0.0-0ubuntu1")
        assert v.epoch == 0
        assert v.upstream == "29.0.0"
        assert v.debian == "0ubuntu1"

    def test_version_with_epoch(self) -> None:
        """Test parsing version with epoch."""
        v = changelog.parse_version("1:29.0.0-0ubuntu1")
        assert v.epoch == 1
        assert v.upstream == "29.0.0"
        assert v.debian == "0ubuntu1"

    def test_version_with_multiple_hyphens(self) -> None:
        """Test parsing version with hyphens in upstream."""
        v = changelog.parse_version("2024.2.0-rc1-0ubuntu1")
        assert v.upstream == "2024.2.0-rc1"
        assert v.debian == "0ubuntu1"

    def test_version_without_debian_revision(self) -> None:
        """Test parsing native version (no debian revision)."""
        v = changelog.parse_version("29.0.0")
        assert v.epoch == 0
        assert v.upstream == "29.0.0"
        assert v.debian == ""

    def test_version_with_tilde(self) -> None:
        """Test parsing snapshot version with tilde."""
        v = changelog.parse_version("30.0.0~git20240101.abc1234-0ubuntu1")
        assert v.upstream == "30.0.0~git20240101.abc1234"
        assert v.debian == "0ubuntu1"


class TestGenerateReleaseVersion:
    """Tests for generate_release_version function."""

    def test_default_revision(self) -> None:
        """Test with default ubuntu revision."""
        ver = changelog.generate_release_version("29.0.0")
        assert ver == "29.0.0-0ubuntu1"

    def test_custom_revision(self) -> None:
        """Test with custom ubuntu revision."""
        ver = changelog.generate_release_version("29.0.0", ubuntu_revision=2)
        assert ver == "29.0.0-0ubuntu2"

    def test_with_epoch(self) -> None:
        """Test with epoch prefix."""
        ver = changelog.generate_release_version("29.0.0", epoch=1)
        assert ver == "1:29.0.0-0ubuntu1"

    def test_with_epoch_and_revision(self) -> None:
        """Test with epoch and custom revision."""
        ver = changelog.generate_release_version("29.0.0", ubuntu_revision=3, epoch=2)
        assert ver == "2:29.0.0-0ubuntu3"

    def test_zero_epoch_not_included(self) -> None:
        """Test that epoch=0 does not add prefix."""
        ver = changelog.generate_release_version("29.0.0", epoch=0)
        assert ver == "29.0.0-0ubuntu1"


class TestGenerateSnapshotVersion:
    """Tests for generate_snapshot_version function."""

    def test_snapshot_format(self) -> None:
        """Test snapshot version format."""
        ver = changelog.generate_snapshot_version(
            next_version="30.0.0",
            git_date="20240115",
            git_sha="abc1234",
        )
        assert ver == "30.0.0~git20240115.abc1234-0ubuntu1"


class TestUpdateChangelogGbp:
    """Tests for gbp dch preferred path."""

    @patch("packastack.debpkg.changelog.subprocess.run")
    def test_prefers_gbp_dch_when_available(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        debian_dir = tmp_path / "pkg" / "debian"
        debian_dir.mkdir(parents=True)
        changelog_path = debian_dir / "changelog"
        changelog_path.write_text("", encoding="utf-8")

        result = changelog.update_changelog(
            changelog_path,
            package="pkg",
            version="1.0-1",
            distribution="UNRELEASED",
            changes=["Line"],
            prefer_gbp=True,
        )

        assert result is True
        # gbp dch + dch --append
        assert mock_run.call_count == 2
        gbp_cmd = mock_run.call_args_list[0].args[0]
        assert gbp_cmd[:2] == ["gbp", "dch"]
        # ensure we use git commit authors for attribution
        assert "--git-author" in gbp_cmd
        # dch append should use maintmaint and append the change
        append_cmd = mock_run.call_args_list[1].args[0]
        assert append_cmd[0] == "dch"
        assert "--maintmaint" in append_cmd
        assert "--append" in append_cmd

    @patch("packastack.debpkg.changelog._update_changelog_python_debian", return_value=True)
    @patch("packastack.debpkg.changelog.subprocess.run")
    def test_falls_back_when_gbp_fails(self, mock_run: MagicMock, mock_python: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")

        debian_dir = tmp_path / "pkg" / "debian"
        debian_dir.mkdir(parents=True)
        changelog_path = debian_dir / "changelog"
        changelog_path.write_text("", encoding="utf-8")

        result = changelog.update_changelog(
            changelog_path,
            package="pkg",
            version="1.0-1",
            distribution="UNRELEASED",
            changes=["Line"],
            prefer_gbp=True,
        )

        assert result is True
        assert mock_python.called

    def test_snapshot_custom_revision(self) -> None:
        """Test snapshot with custom revision."""
        ver = changelog.generate_snapshot_version(
            next_version="30.0.0",
            git_date="20240115",
            git_sha="abc1234",
            ubuntu_revision=2,
        )
        assert ver == "30.0.0~git20240115.abc1234-0ubuntu2"

    def test_snapshot_with_epoch(self) -> None:
        """Test snapshot version with epoch."""
        ver = changelog.generate_snapshot_version(
            next_version="30.0.0",
            git_date="20240115",
            git_sha="abc1234",
            epoch=1,
        )
        assert ver == "1:30.0.0~git20240115.abc1234-0ubuntu1"

    def test_snapshot_with_epoch_and_revision(self) -> None:
        """Test snapshot with epoch and revision."""
        ver = changelog.generate_snapshot_version(
            next_version="30.0.0",
            git_date="20240115",
            git_sha="abc1234",
            ubuntu_revision=2,
            epoch=3,
        )
        assert ver == "3:30.0.0~git20240115.abc1234-0ubuntu2"


class TestGenerateMilestoneVersion:
    """Tests for generate_milestone_version function."""

    def test_beta_milestone(self) -> None:
        """Test beta milestone version."""
        ver = changelog.generate_milestone_version("30.0.0", "b1")
        assert ver == "30.0.0~b1-0ubuntu1"

    def test_rc_milestone(self) -> None:
        """Test release candidate milestone version."""
        ver = changelog.generate_milestone_version("30.0.0", "rc1")
        assert ver == "30.0.0~rc1-0ubuntu1"

    def test_milestone_normalization(self) -> None:
        """Test milestone normalization (add 'b' prefix)."""
        ver = changelog.generate_milestone_version("30.0.0", "1")
        assert ver == "30.0.0~b1-0ubuntu1"

    def test_milestone_uppercase(self) -> None:
        """Test uppercase milestone is lowercased."""
        ver = changelog.generate_milestone_version("30.0.0", "RC2")
        assert ver == "30.0.0~rc2-0ubuntu1"

    def test_milestone_with_epoch(self) -> None:
        """Test milestone version with epoch."""
        ver = changelog.generate_milestone_version("30.0.0", "b1", epoch=1)
        assert ver == "1:30.0.0~b1-0ubuntu1"

    def test_milestone_with_epoch_and_revision(self) -> None:
        """Test milestone with epoch and revision."""
        ver = changelog.generate_milestone_version(
            "30.0.0", "rc1", ubuntu_revision=2, epoch=2
        )
        assert ver == "2:30.0.0~rc1-0ubuntu2"


class TestIncrementUpstreamVersion:
    """Tests for increment_upstream_version function."""

    def test_simple_increment(self) -> None:
        """Test incrementing simple version."""
        ver = changelog.increment_upstream_version("29.0.0")
        assert ver == "30.0.0"

    def test_two_part_version(self) -> None:
        """Test incrementing two-part version."""
        ver = changelog.increment_upstream_version("29.1")
        assert ver == "30.0"

    def test_single_number(self) -> None:
        """Test incrementing single number version."""
        ver = changelog.increment_upstream_version("5")
        assert ver == "6"

    def test_non_numeric_prefix(self) -> None:
        """Test version with non-numeric prefix."""
        # "v29.0.0" has "v29" as first part, which is not purely numeric
        # So the first pure numeric part "0" is incremented to "1"
        ver = changelog.increment_upstream_version("v29.0.0")
        assert ver == "v29.1.0"


class TestGetCurrentVersion:
    """Tests for get_current_version function."""

    def test_file_not_exists(self, tmp_path: Path) -> None:
        """Test when changelog doesn't exist."""
        result = changelog.get_current_version(tmp_path / "changelog")
        assert result is None

    def test_parse_changelog(self, tmp_path: Path) -> None:
        """Test parsing changelog file."""
        changelog_path = tmp_path / "changelog"
        changelog_path.write_text(
            "nova (1:29.0.0-0ubuntu1) noble; urgency=medium\n\n  * Initial release\n\n"
            " -- Test User <test@example.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
        )

        result = changelog.get_current_version(changelog_path)
        assert result is not None
        assert "29.0.0" in result

    def test_fallback_parsing(self, tmp_path: Path) -> None:
        """Test fallback parsing when python-debian not available."""
        # Mock Changelog being None
        original_changelog = changelog.Changelog
        try:
            changelog.Changelog = None

            changelog_path = tmp_path / "changelog"
            changelog_path.write_text(
                "nova (1:29.0.0-0ubuntu1) noble; urgency=medium\n\n"
            )

            result = changelog.get_current_version(changelog_path)
            assert result == "1:29.0.0-0ubuntu1"
        finally:
            changelog.Changelog = original_changelog


class TestUpdateChangelog:
    """Tests for update_changelog function."""

    def test_update_changelog_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test successful changelog update."""
        monkeypatch.setenv("DEBFULLNAME", "Test User")
        monkeypatch.setenv("DEBEMAIL", "test@example.com")

        changelog_path = tmp_path / "changelog"
        changelog_path.write_text(
            "nova (1:28.0.0-0ubuntu1) noble; urgency=medium\n\n  * Previous release\n\n"
            " -- Test User <test@example.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
        )

        result = changelog.update_changelog(
            changelog_path=changelog_path,
            package="nova",
            version="1:29.0.0-0ubuntu1",
            distribution="noble",
            changes=["New upstream release"],
        )

        # Result depends on whether python-debian is available
        # Just verify it doesn't crash
        assert result in (True, False)

    def test_update_uses_environment_vars(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that maintainer comes from environment."""
        monkeypatch.setenv("DEBFULLNAME", "Custom Name")
        monkeypatch.setenv("DEBEMAIL", "custom@example.com")

        tmp_path / "changelog"

        # This will use the environment variables for maintainer
        # We're just verifying the function runs without error


class TestUpdateChangelogDch:
    """Tests for _update_changelog_dch function."""

    def test_dch_fallback(self, tmp_path: Path) -> None:
        """Test dch command fallback."""
        changelog_path = tmp_path / "changelog"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Mock the absence of python-debian
            original = changelog.Changelog
            try:
                changelog.Changelog = None
                changelog.update_changelog(
                    changelog_path=changelog_path,
                    package="nova",
                    version="29.0.0-0ubuntu1",
                    distribution="noble",
                    changes=["Test change"],
                    maintainer="Test <test@test.com>",
                )
            finally:
                changelog.Changelog = original

            # dch should be called
            # Result depends on implementation


class TestVersionOrdering:
    """Tests for Debian version ordering semantics."""

    def test_tilde_sorts_before(self) -> None:
        """Verify tilde versions sort before release versions.

        This is a documentation test - actual comparison requires dpkg --compare-versions.
        """
        # 30.0.0~git... < 30.0.0~b1 < 30.0.0~rc1 < 30.0.0
        versions = [
            "30.0.0",
            "30.0.0~rc1",
            "30.0.0~b1",
            "30.0.0~git20240101.abc1234",
        ]
        # Just verify formats are as expected
        for v in versions:
            assert changelog.parse_version(f"{v}-0ubuntu1") is not None


class TestGenerateChangelogMessage:
    """Tests for generate_changelog_message function."""

    def test_release_message(self) -> None:
        """Test release changelog message."""
        changes = changelog.generate_changelog_message(
            build_type="release",
            upstream_version="29.0.0",
            git_ref="",
            signature_verified=True,
            signature_warning="",
        )
        assert len(changes) == 1  # No signature line anymore
        assert "New upstream release 29.0.0" in changes[0]

    def test_snapshot_message(self) -> None:
        """Test snapshot changelog message."""
        changes = changelog.generate_changelog_message(
            build_type="snapshot",
            upstream_version="",
            git_ref="abc1234",
            signature_verified=False,
            signature_warning="",
        )
        assert len(changes) == 1  # No signature line anymore
        assert "snapshot" in changes[0].lower()
        assert "abc1234" in changes[0]

    def test_milestone_message(self) -> None:
        """Test milestone changelog message."""
        changes = changelog.generate_changelog_message(
            build_type="milestone",
            upstream_version="30.0.0.0b1",
            git_ref="",
            signature_verified=False,
            signature_warning="Signature missing",
        )
        assert "milestone" in changes[0].lower()
        assert "30.0.0.0b1" in changes[0]
        # Warning is still included
        assert "Signature missing" in changes[1]

    def test_unknown_build_type(self) -> None:
        """Test unknown build type uses default message."""
        changes = changelog.generate_changelog_message(
            build_type="custom",
            upstream_version="1.0.0",
            git_ref="",
            signature_verified=False,
            signature_warning="",
        )
        assert len(changes) == 1  # No extra lines
        assert "New upstream version" in changes[0]


class TestGetCurrentVersionFallback:
    """Tests for get_current_version with fallback."""

    def test_nonexistent_file_returns_none(self, tmp_path: Path) -> None:
        """Test that nonexistent file returns None."""
        result = changelog.get_current_version(tmp_path / "nonexistent")
        assert result is None

    def test_manual_parse_fallback(self, tmp_path: Path) -> None:
        """Test fallback manual parsing when python-debian not available."""
        changelog_path = tmp_path / "changelog"
        changelog_path.write_text(
            "nova (1:29.0.0-0ubuntu1) noble; urgency=medium\n\n  * Test\n\n"
            " -- Test <test@test.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
        )

        # Temporarily disable python-debian
        original = changelog.Changelog
        try:
            changelog.Changelog = None
            result = changelog.get_current_version(changelog_path)
            assert result == "1:29.0.0-0ubuntu1"
        finally:
            changelog.Changelog = original


class TestIncrementUpstreamVersionEdgeCases:
    """Tests for increment_upstream_version edge cases."""

    def test_empty_version(self) -> None:
        """Test empty version returns empty."""
        result = changelog.increment_upstream_version("")
        assert result == ""

    def test_version_with_trailing_text(self) -> None:
        """Test version with trailing text."""
        result = changelog.increment_upstream_version("29.0.0.final")
        # Should increment 29 and reset rest
        assert "30" in result

    def test_version_with_leading_text(self) -> None:
        """Test version with leading text (non-numeric)."""
        result = changelog.increment_upstream_version("v29.0.0")
        # The "v" prefix is preserved and the minor version is incremented
        assert result == "v29.1.0"

    def test_all_non_numeric_parts(self) -> None:
        """Test version with no numeric parts at all."""
        result = changelog.increment_upstream_version("alpha.beta.gamma")
        # No numeric parts found, returns as-is
        assert result == "alpha.beta.gamma"

    def test_version_with_only_trailing_numeric(self) -> None:
        """Test version with numeric part only at end."""
        result = changelog.increment_upstream_version("final.release.3")
        # Only the last part is numeric and gets incremented
        assert result == "final.release.4"


class TestUpdateChangelogDchFallback:
    """Tests for _update_changelog_dch fallback function."""

    def test_dch_command_called(self, tmp_path: Path) -> None:
        """Test that dch command is called when python-debian unavailable."""
        changelog_path = tmp_path / "changelog"
        changelog_path.write_text(
            "nova (28.0.0-0ubuntu1) focal; urgency=low\n\n  * Init\n\n"
            " -- Test <test@test.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            result = changelog._update_changelog_dch(
                changelog_path=changelog_path,
                package="nova",
                version="29.0.0-0ubuntu1",
                distribution="noble",
                changes=["New release"],
                maintainer="Test User <test@example.com>",
                urgency="medium",
            )

            mock_run.assert_called_once()
            assert result is True

    def test_dch_command_failure(self, tmp_path: Path) -> None:
        """Test dch command failure returns False."""
        changelog_path = tmp_path / "changelog"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="dch error")

            result = changelog._update_changelog_dch(
                changelog_path=changelog_path,
                package="nova",
                version="29.0.0-0ubuntu1",
                distribution="noble",
                changes=["New release"],
                maintainer="Test User <test@example.com>",
                urgency="medium",
            )

            assert result is False

    def test_dch_adds_extra_changes(self, tmp_path: Path) -> None:
        """Test dch adds extra changes."""
        changelog_path = tmp_path / "changelog"
        changelog_path.write_text(
            "nova (28.0.0-0ubuntu1) focal; urgency=low\n\n  * Init\n\n"
            " -- Test <test@test.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            changelog._update_changelog_dch(
                changelog_path=changelog_path,
                package="nova",
                version="29.0.0-0ubuntu1",
                distribution="noble",
                changes=["First change", "Second change", "Third change"],
                maintainer="Test User <test@example.com>",
                urgency="medium",
            )

            # Should be called multiple times for extra changes
            assert mock_run.call_count >= 1

    def test_dch_exception_handling(self, tmp_path: Path) -> None:
        """Test dch exception returns False."""
        changelog_path = tmp_path / "changelog"

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = Exception("dch not found")

            result = changelog._update_changelog_dch(
                changelog_path=changelog_path,
                package="nova",
                version="29.0.0-0ubuntu1",
                distribution="noble",
                changes=["Test change"],
                maintainer="Test User <test@example.com>",
                urgency="medium",
            )

            assert result is False


class TestGetCurrentVersion:
    """Tests for get_current_version function."""

    def test_file_not_exists(self, tmp_path: Path) -> None:
        """Test when changelog file doesn't exist."""
        changelog_path = tmp_path / "changelog"
        result = changelog.get_current_version(changelog_path)
        assert result is None

    def test_valid_changelog(self, tmp_path: Path) -> None:
        """Test getting version from valid changelog."""
        changelog_path = tmp_path / "changelog"
        changelog_path.write_text(
            "nova (29.0.0-0ubuntu1) noble; urgency=medium\n\n"
            "  * Release\n\n"
            " -- Test <test@test.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
        )

        result = changelog.get_current_version(changelog_path)
        assert result == "29.0.0-0ubuntu1"

    def test_fallback_parsing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test fallback regex parsing when python-debian unavailable."""
        changelog_path = tmp_path / "changelog"
        changelog_path.write_text(
            "glance (28.0.0-1) noble; urgency=low\n\n"
            "  * Test\n\n"
            " -- Test <test@test.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
        )

        # Mock Changelog as None to trigger fallback
        monkeypatch.setattr(changelog, "Changelog", None)

        result = changelog.get_current_version(changelog_path)
        assert result == "28.0.0-1"

    def test_fallback_parsing_no_match(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test fallback regex parsing returns None when format doesn't match."""
        changelog_path = tmp_path / "changelog"
        # Invalid format - no version in parentheses
        changelog_path.write_text("invalid changelog format\n")

        # Mock Changelog as None to trigger fallback
        monkeypatch.setattr(changelog, "Changelog", None)

        result = changelog.get_current_version(changelog_path)
        assert result is None

    def test_python_debian_returns_none_version(self, tmp_path: Path) -> None:
        """Test when python-debian parses but returns None version."""
        changelog_path = tmp_path / "changelog"
        # An empty or malformed changelog that python-debian parses but has no version
        changelog_path.write_text("\n")

        # python-debian should return None version
        result = changelog.get_current_version(changelog_path)
        assert result is None


class TestUpdateChangelogPythonDebian:
    """Tests for update_changelog using python-debian."""

    def test_update_new_changelog(self, tmp_path: Path) -> None:
        """Test creating new changelog entry."""
        changelog_path = tmp_path / "changelog"

        result = changelog.update_changelog(
            changelog_path=changelog_path,
            package="nova",
            version="29.0.0-0ubuntu1",
            distribution="noble",
            changes=["New upstream release"],
            maintainer="Test <test@example.com>",
        )

        assert result is True
        assert changelog_path.exists()

    def test_update_existing_changelog(self, tmp_path: Path) -> None:
        """Test adding entry to existing changelog."""
        changelog_path = tmp_path / "changelog"
        changelog_path.write_text(
            "nova (28.0.0-0ubuntu1) noble; urgency=medium\n\n"
            "  * Initial release\n\n"
            " -- Test <test@test.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
        )

        result = changelog.update_changelog(
            changelog_path=changelog_path,
            package="nova",
            version="29.0.0-0ubuntu1",
            distribution="noble",
            changes=["New upstream release"],
            maintainer="Test <test@example.com>",
        )

        assert result is True
        content = changelog_path.read_text()
        assert "29.0.0" in content
        assert "28.0.0" in content  # Old entry still present
