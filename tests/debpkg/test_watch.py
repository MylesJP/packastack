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

"""Tests for packastack.debpkg.watch module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from packastack.debpkg import watch


def test_parse_watch_file_missing(tmp_path: Path) -> None:
    """Missing watch file yields UNKNOWN with parse_error."""
    missing = tmp_path / "debian" / "watch"

    result = watch.parse_watch_file(missing)

    assert result.mode is watch.DetectedWatchMode.UNKNOWN
    assert "not found" in result.parse_error


def test_parse_watch_content_detects_openstack_tarball() -> None:
    """Recognises OpenDev tarball URLs and extracts version."""
    content = r"""\
version=4
opts=uversionmangle=s/\.0rc/~rc/ \\
 https://tarballs.opendev.org/openstack/nova nova-(\\d+)\\.tar\\.gz
"""

    result = watch.parse_watch_content(content)

    assert result.mode is watch.DetectedWatchMode.OPENSTACK_TARBALL
    assert result.base_url.startswith("https://tarballs.opendev.org/openstack/")
    assert result.version_pattern == "version=4"


def test_parse_watch_content_empty() -> None:
    """Empty content returns parse_error and UNKNOWN mode."""
    result = watch.parse_watch_content("   \n\n")

    assert result.mode is watch.DetectedWatchMode.UNKNOWN
    assert "Empty" in result.parse_error


def test_check_watch_mismatch_returns_none_on_match() -> None:
    """Matching registry/watch combinations do not warn."""
    result = watch.WatchParseResult(
        mode=watch.DetectedWatchMode.OPENSTACK_TARBALL,
        base_url="https://tarballs.opendev.org/openstack/nova",
    )

    assert watch.check_watch_mismatch("nova", result, "opendev", "https://tarballs.opendev.org") is None


def test_check_watch_mismatch_unknown_host() -> None:
    """Unknown registry hosts are ignored (no warning)."""
    result = watch.WatchParseResult(mode=watch.DetectedWatchMode.GITHUB_RELEASE, base_url="https://example.com")

    assert watch.check_watch_mismatch("nova", result, "unknown", "https://example.com") is None


def test_check_watch_mismatch_reports_warning() -> None:
    """Mismatch produces WatchMismatchWarning with message."""
    result = watch.WatchParseResult(mode=watch.DetectedWatchMode.GITHUB_RELEASE, base_url="https://github.com/foo/bar")

    warning = watch.check_watch_mismatch("nova", result, "opendev", "https://tarballs.opendev.org/openstack/nova")

    assert warning is not None
    assert warning.package == "nova"
    assert "registry expects" in warning.message


def test_format_mismatch_warning_includes_details() -> None:
    """Formatted warning includes registry and watch details."""
    warning = watch.WatchMismatchWarning(
        package="nova",
        watch_mode=watch.DetectedWatchMode.GITHUB_RELEASE,
        watch_url="https://github.com/openstack/nova",
        registry_mode="opendev",
        registry_url="https://tarballs.opendev.org/openstack/nova",
        message="",
    )

    formatted = watch.format_mismatch_warning(warning)

    assert "nova" in formatted
    assert "registry upstream" in formatted
    assert "github_release" in formatted


def test_upgrade_watch_version_missing_file(tmp_path: Path) -> None:
    """upgrade_watch_version returns False when file is absent."""
    missing = tmp_path / "debian" / "watch"

    assert watch.upgrade_watch_version(missing) is False


class TestPgpVerification:
    """Tests for PGP signature verification functions."""

    def test_has_pgp_verification_with_pgpsigurlmangle(self, tmp_path: Path) -> None:
        """Detects pgpsigurlmangle option."""
        watch_file = tmp_path / "watch"
        watch_file.write_text(
            """version=5
opts=pgpsigurlmangle=s/.tar.gz/.tar.gz.asc/ \\
 https://example.com/foo-(.+).tar.gz
"""
        )

        assert watch.has_pgp_verification(watch_file) is True

    def test_has_pgp_verification_with_pgpmode(self, tmp_path: Path) -> None:
        """Detects pgpmode option."""
        watch_file = tmp_path / "watch"
        watch_file.write_text(
            """version=5
opts=pgpmode=mangle \\
 https://example.com/foo-(.+).tar.gz
"""
        )

        assert watch.has_pgp_verification(watch_file) is True

    def test_has_pgp_verification_false_without_options(self, tmp_path: Path) -> None:
        """Returns False when no PGP options present."""
        watch_file = tmp_path / "watch"
        watch_file.write_text(
            """version=5
https://example.com/foo-(.+).tar.gz
"""
        )

        assert watch.has_pgp_verification(watch_file) is False

    def test_has_pgp_verification_missing_file(self, tmp_path: Path) -> None:
        """Returns False for missing file."""
        watch_file = tmp_path / "watch"
        assert watch.has_pgp_verification(watch_file) is False

    def test_has_upstream_signing_key_finds_key(self, tmp_path: Path) -> None:
        """Finds upstream-signing-key.asc."""
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "upstream-signing-key.asc").write_text("-----BEGIN PGP PUBLIC KEY BLOCK-----")

        assert watch.has_upstream_signing_key(debian) is True

    def test_has_upstream_signing_key_finds_nested_key(self, tmp_path: Path) -> None:
        """Finds upstream/signing-key.asc."""
        debian = tmp_path / "debian"
        upstream = debian / "upstream"
        upstream.mkdir(parents=True)
        (upstream / "signing-key.asc").write_text("-----BEGIN PGP PUBLIC KEY BLOCK-----")

        assert watch.has_upstream_signing_key(debian) is True

    def test_has_upstream_signing_key_missing(self, tmp_path: Path) -> None:
        """Returns False when no signing key exists."""
        debian = tmp_path / "debian"
        debian.mkdir()

        assert watch.has_upstream_signing_key(debian) is False

    def test_remove_pgp_options_removes_pgpsigurlmangle(self, tmp_path: Path) -> None:
        """Removes pgpsigurlmangle from opts."""
        watch_file = tmp_path / "watch"
        watch_file.write_text(
            """version=5
opts=uversionmangle=s/rc/~rc/,pgpsigurlmangle=s/.tar.gz/.tar.gz.asc/ \\
 https://example.com/foo-(.+).tar.gz
"""
        )

        result = watch.remove_pgp_options_from_watch(watch_file)

        assert result is True
        content = watch_file.read_text()
        assert "pgpsigurlmangle" not in content
        assert "uversionmangle" in content

    def test_remove_pgp_options_removes_pgpmode(self, tmp_path: Path) -> None:
        """Removes pgpmode from opts."""
        watch_file = tmp_path / "watch"
        watch_file.write_text(
            """version=5
opts=pgpmode=mangle \\
 https://example.com/foo-(.+).tar.gz
"""
        )

        result = watch.remove_pgp_options_from_watch(watch_file)

        assert result is True
        content = watch_file.read_text()
        assert "pgpmode" not in content

    def test_remove_pgp_options_no_change_when_absent(self, tmp_path: Path) -> None:
        """Returns False when no PGP options to remove."""
        watch_file = tmp_path / "watch"
        original = """version=5
opts=uversionmangle=s/rc/~rc/ \\
 https://example.com/foo-(.+).tar.gz
"""
        watch_file.write_text(original)

        result = watch.remove_pgp_options_from_watch(watch_file)

        assert result is False

    def test_ensure_pgp_verification_valid_removes_orphan_pgp(self, tmp_path: Path) -> None:
        """Removes PGP options when no signing key exists."""
        debian = tmp_path / "debian"
        debian.mkdir()
        watch_file = debian / "watch"
        watch_file.write_text(
            """version=5
opts=pgpsigurlmangle=s/.tar.gz/.tar.gz.asc/ \\
 https://example.com/foo-(.+).tar.gz
"""
        )

        modified, msg = watch.ensure_pgp_verification_valid(debian)

        assert modified is True
        assert "Removed PGP options" in msg
        assert "pgpsigurlmangle" not in watch_file.read_text()

    def test_ensure_pgp_verification_valid_keeps_with_key(self, tmp_path: Path) -> None:
        """Keeps PGP options when signing key exists."""
        debian = tmp_path / "debian"
        debian.mkdir()
        watch_file = debian / "watch"
        watch_file.write_text(
            """version=5
opts=pgpsigurlmangle=s/.tar.gz/.tar.gz.asc/ \\
 https://example.com/foo-(.+).tar.gz
"""
        )
        (debian / "upstream-signing-key.asc").write_text("-----BEGIN PGP PUBLIC KEY BLOCK-----")

        modified, msg = watch.ensure_pgp_verification_valid(debian)

        assert modified is False
        assert "valid signing key" in msg
        assert "pgpsigurlmangle" in watch_file.read_text()

    def test_ensure_pgp_verification_valid_no_pgp_options(self, tmp_path: Path) -> None:
        """Returns empty result when no PGP options exist."""
        debian = tmp_path / "debian"
        debian.mkdir()
        watch_file = debian / "watch"
        watch_file.write_text(
            """version=5
https://example.com/foo-(.+).tar.gz
"""
        )

        modified, msg = watch.ensure_pgp_verification_valid(debian)

        assert modified is False
        assert msg == ""


class TestFixMalformedWatchOpts:
    """Tests for fix_malformed_watch_opts function."""

    def test_moves_leaked_option_inside_quotes(self, tmp_path: Path) -> None:
        """Options after the closing quote are moved inside."""
        watch_file = tmp_path / "watch"
        watch_file.write_text(
            'version=4\n'
            'opts="uversionmangle=s/\\.([a-zA-Z])/~$1/;s/%7E/~/;s/\\.0b/~b/;s/\\.0rc/~rc/"'
            ',pgpsigurlmangle=s/$/.asc/ \\\n'
            ' https://tarballs.opendev.org/openstack/heat-dashboard/'
            ' heat_dashboard-(\\d{1,2}\\.\\d.*)\\.tar\\.gz\n'
        )

        result = watch.fix_malformed_watch_opts(watch_file)

        assert result is True
        content = watch_file.read_text()
        # pgpsigurlmangle should now be inside the quotes
        assert ',pgpsigurlmangle=s/$/.asc/"' in content
        # No option should appear after the closing quote
        assert '",pgpsigurlmangle' not in content

    def test_multiple_leaked_options(self, tmp_path: Path) -> None:
        """Multiple leaked options are all moved inside the quotes."""
        watch_file = tmp_path / "watch"
        watch_file.write_text(
            'version=4\n'
            'opts="uversionmangle=s/\\.0rc/~rc/"'
            ',pgpsigurlmangle=s/$/.asc/,pgpmode=auto \\\n'
            ' https://example.com/ foo-(\\d.*)\\.tar\\.gz\n'
        )

        result = watch.fix_malformed_watch_opts(watch_file)

        assert result is True
        content = watch_file.read_text()
        assert ',pgpsigurlmangle=s/$/.asc/,pgpmode=auto"' in content

    def test_no_change_for_correct_quoting(self, tmp_path: Path) -> None:
        """Returns False when opts are correctly quoted."""
        watch_file = tmp_path / "watch"
        watch_file.write_text(
            'version=4\n'
            'opts="uversionmangle=s/\\.0rc/~rc/,pgpsigurlmangle=s/$/.asc/" \\\n'
            ' https://example.com/ foo-(\\d.*)\\.tar\\.gz\n'
        )

        result = watch.fix_malformed_watch_opts(watch_file)

        assert result is False

    def test_no_change_for_unquoted_opts(self, tmp_path: Path) -> None:
        """Returns False for correctly-formed unquoted opts."""
        watch_file = tmp_path / "watch"
        watch_file.write_text(
            "version=4\n"
            "opts=uversionmangle=s/\\.0rc/~rc/,pgpsigurlmangle=s/$/.asc/ \\\n"
            " https://example.com/ foo-(\\d.*)\\.tar\\.gz\n"
        )

        result = watch.fix_malformed_watch_opts(watch_file)

        assert result is False

    def test_no_change_for_missing_file(self, tmp_path: Path) -> None:
        """Returns False when file does not exist."""
        result = watch.fix_malformed_watch_opts(tmp_path / "watch")

        assert result is False

    def test_preserves_rest_of_file(self, tmp_path: Path) -> None:
        """Fix only touches the malformed opts line, not other content."""
        watch_file = tmp_path / "watch"
        url_line = (
            " https://tarballs.opendev.org/openstack/heat-dashboard/"
            " heat_dashboard-(\\d{1,2}\\.\\d.*)\\.tar\\.gz\n"
        )
        watch_file.write_text(
            'version=4\n'
            'opts="uversionmangle=s/\\.0rc/~rc/",pgpsigurlmangle=s/$/.asc/ \\\n'
            + url_line
        )

        watch.fix_malformed_watch_opts(watch_file)

        content = watch_file.read_text()
        assert url_line in content
        assert content.startswith("version=4\n")


class TestRestorePgpOptionsToWatch:
    """Tests for restore_pgp_options_to_watch function."""

    def test_restores_pgpsigurlmangle(self, tmp_path: Path) -> None:
        """Adds pgpsigurlmangle to opts when missing."""
        watch_file = tmp_path / "watch"
        watch_file.write_text(
            "version=4\n"
            "opts=uversionmangle=s/\\.([a-zA-Z])/~$1/;s/%7E/~/;s/\\.0b/~b/;s/\\.0rc/~rc/ \\\n"
            " https://tarballs.opendev.org/openstack/aodh/ aodh-(\\d.*)\\.tar\\.gz\n"
        )

        result = watch.restore_pgp_options_to_watch(watch_file)

        assert result is True
        content = watch_file.read_text()
        assert "pgpsigurlmangle=s/$/.asc/" in content
        assert "uversionmangle" in content

    def test_no_change_when_already_present(self, tmp_path: Path) -> None:
        """Returns False when pgpsigurlmangle already exists."""
        watch_file = tmp_path / "watch"
        watch_file.write_text(
            "version=4\n"
            "opts=uversionmangle=s/\\.0rc/~rc/,pgpsigurlmangle=s/$/.asc/ \\\n"
            " https://tarballs.opendev.org/openstack/aodh/ aodh-(\\d.*)\\.tar\\.gz\n"
        )

        result = watch.restore_pgp_options_to_watch(watch_file)

        assert result is False

    def test_no_change_when_file_missing(self, tmp_path: Path) -> None:
        """Returns False for missing file."""
        watch_file = tmp_path / "watch"

        result = watch.restore_pgp_options_to_watch(watch_file)

        assert result is False

    def test_no_change_without_opts(self, tmp_path: Path) -> None:
        """Returns False when there is no opts= line to modify."""
        watch_file = tmp_path / "watch"
        watch_file.write_text(
            "version=4\n"
            "https://tarballs.opendev.org/openstack/aodh/ aodh-(\\d.*)\\.tar\\.gz\n"
        )

        result = watch.restore_pgp_options_to_watch(watch_file)

        assert result is False

    def test_roundtrip_remove_then_restore(self, tmp_path: Path) -> None:
        """Removing then restoring PGP options produces a valid watch file."""
        original = (
            "version=4\n"
            "opts=uversionmangle=s/\\.0rc/~rc/,pgpsigurlmangle=s/$/.asc/ \\\n"
            " https://tarballs.opendev.org/openstack/aodh/ aodh-(\\d.*)\\.tar\\.gz\n"
        )
        watch_file = tmp_path / "watch"
        watch_file.write_text(original)

        # Remove
        assert watch.remove_pgp_options_from_watch(watch_file) is True
        assert "pgpsigurlmangle" not in watch_file.read_text()

        # Restore
        assert watch.restore_pgp_options_to_watch(watch_file) is True
        content = watch_file.read_text()
        assert "pgpsigurlmangle=s/$/.asc/" in content
        assert "uversionmangle" in content

    def test_restores_inside_quoted_opts(self, tmp_path: Path) -> None:
        """Inserts pgpsigurlmangle before the closing quote for quoted opts."""
        watch_file = tmp_path / "watch"
        watch_file.write_text(
            'version=4\n'
            'opts="uversionmangle=s/\\.([a-zA-Z])/~$1/;s/%7E/~/;s/\\.0b/~b/;s/\\.0rc/~rc/" \\\n'
            ' https://tarballs.opendev.org/openstack/heat-dashboard/'
            ' heat_dashboard-(\\d{1,2}\\.\\d.*)\\.tar\\.gz\n'
        )

        result = watch.restore_pgp_options_to_watch(watch_file)

        assert result is True
        content = watch_file.read_text()
        # Must be inside the quotes, not after
        assert ',pgpsigurlmangle=s/$/.asc/"' in content
        assert '",pgpsigurlmangle' not in content

    def test_roundtrip_quoted_opts(self, tmp_path: Path) -> None:
        """Remove then restore PGP options with quoted opts stays valid."""
        original = (
            'version=4\n'
            'opts="uversionmangle=s/\\.0rc/~rc/,pgpsigurlmangle=s/$/.asc/" \\\n'
            ' https://tarballs.opendev.org/openstack/aodh/ aodh-(\\d.*)\\.tar\\.gz\n'
        )
        watch_file = tmp_path / "watch"
        watch_file.write_text(original)

        # Remove
        assert watch.remove_pgp_options_from_watch(watch_file) is True
        assert "pgpsigurlmangle" not in watch_file.read_text()

        # Restore
        assert watch.restore_pgp_options_to_watch(watch_file) is True
        content = watch_file.read_text()
        assert ',pgpsigurlmangle=s/$/.asc/"' in content
        assert '",pgpsigurlmangle' not in content


class TestParseDehsOutput:
    """Tests for parse_dehs_output function."""

    def test_parse_valid_dehs_newer_available(self) -> None:
        """Parse DEHS output when newer version is available."""
        dehs_xml = """\
<?xml version="1.0" encoding="utf-8"?>
<dehs>
  <package>alembic</package>
  <debian-uversion>1.13.0</debian-uversion>
  <debian-mangled-uversion>1.13.0</debian-mangled-uversion>
  <upstream-version>1.14.0</upstream-version>
  <upstream-url>https://files.pythonhosted.org/packages/source/a/alembic/alembic-1.14.0.tar.gz</upstream-url>
  <status>newer package available</status>
</dehs>
"""
        result = watch.parse_dehs_output(dehs_xml)

        assert result.status == watch.UscanStatus.NEWER_AVAILABLE
        assert result.success is True
        assert result.debian_upstream_version == "1.13.0"
        assert result.upstream_version == "1.14.0"
        assert result.upstream_url == "https://files.pythonhosted.org/packages/source/a/alembic/alembic-1.14.0.tar.gz"
        assert result.newer_available is True

    def test_parse_valid_dehs_up_to_date(self) -> None:
        """Parse DEHS output when package is up to date."""
        dehs_xml = """\
<?xml version="1.0" encoding="utf-8"?>
<dehs>
  <package>nova</package>
  <debian-uversion>2024.2.0</debian-uversion>
  <upstream-version>2024.2.0</upstream-version>
  <status>up to date</status>
</dehs>
"""
        result = watch.parse_dehs_output(dehs_xml)

        assert result.status == watch.UscanStatus.UP_TO_DATE
        assert result.success is True
        assert result.debian_upstream_version == "2024.2.0"
        assert result.upstream_version == "2024.2.0"
        assert result.newer_available is False

    def test_parse_dehs_watch_error(self) -> None:
        """Parse DEHS output when watch file has an error."""
        dehs_xml = """\
<?xml version="1.0" encoding="utf-8"?>
<dehs>
  <package>broken-pkg</package>
  <warnings>uscan warning: No matching hrefs for pattern</warnings>
  <errors>uscan error: Unable to get upstream version info</errors>
</dehs>
"""
        result = watch.parse_dehs_output(dehs_xml)

        assert result.status == watch.UscanStatus.ERROR
        assert result.success is False
        assert "Unable to get upstream version info" in result.error

    def test_parse_empty_string(self) -> None:
        """Parse empty output returns error status."""
        result = watch.parse_dehs_output("")

        assert result.status == watch.UscanStatus.PARSE_ERROR
        assert result.success is False
        assert "Empty" in result.error

    def test_parse_invalid_xml(self) -> None:
        """Parse invalid XML returns error status."""
        result = watch.parse_dehs_output("<not-valid-xml>")

        assert result.status == watch.UscanStatus.PARSE_ERROR
        assert result.error


class TestRunUscanDehs:
    """Tests for run_uscan_dehs function."""

    def test_run_uscan_success(self, tmp_path: Path) -> None:
        """Successful uscan run parses DEHS output."""
        pkg_path = tmp_path / "alembic"
        debian = pkg_path / "debian"
        debian.mkdir(parents=True)
        (debian / "watch").write_text("version=4\nhttps://example.com/foo-(.+).tar.gz")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = """\
<?xml version="1.0" encoding="utf-8"?>
<dehs>
  <package>alembic</package>
  <debian-uversion>1.13.0</debian-uversion>
  <upstream-version>1.14.0</upstream-version>
  <upstream-url>https://example.com/foo-1.14.0.tar.gz</upstream-url>
  <status>newer package available</status>
</dehs>
"""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = watch.run_uscan_dehs(pkg_path, timeout_seconds=30)

        assert result.status == watch.UscanStatus.NEWER_AVAILABLE
        assert result.success is True
        assert result.upstream_version == "1.14.0"

    def test_run_uscan_no_watch_file(self, tmp_path: Path) -> None:
        """Returns error status when no watch file exists."""
        pkg_path = tmp_path / "missing-pkg"
        debian = pkg_path / "debian"
        debian.mkdir(parents=True)
        # No watch file created

        result = watch.run_uscan_dehs(pkg_path, timeout_seconds=30)

        assert result.status == watch.UscanStatus.NO_WATCH
        assert result.success is False
        assert "watch" in result.error.lower()

    def test_run_uscan_timeout(self, tmp_path: Path) -> None:
        """Returns timeout status when uscan times out."""
        import subprocess

        pkg_path = tmp_path / "slow-pkg"
        debian = pkg_path / "debian"
        debian.mkdir(parents=True)
        (debian / "watch").write_text("version=4\nhttps://example.com/foo-(.+).tar.gz")

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("uscan", 30)):
            result = watch.run_uscan_dehs(pkg_path, timeout_seconds=30)

        assert result.status == watch.UscanStatus.TIMEOUT
        assert result.success is False
        assert "timed out" in result.error.lower()

    def test_run_uscan_missing_dir(self, tmp_path: Path) -> None:
        """Returns error status when package directory doesn't exist."""
        result = watch.run_uscan_dehs(tmp_path / "nonexistent", timeout_seconds=30)

        assert result.status == watch.UscanStatus.NO_WATCH
        assert result.success is False
        assert result.error


class TestUscanCache:
    """Tests for uscan cache functions."""

    def test_load_empty_cache(self, tmp_path: Path) -> None:
        """Loading missing cache returns empty dict."""
        cache_path = tmp_path / "uscan-cache.json"

        cache = watch.load_uscan_cache(cache_path)

        assert cache == {}

    def test_save_and_load_cache(self, tmp_path: Path) -> None:
        """Cache can be saved and loaded."""
        cache_path = tmp_path / "uscan-cache.json"

        entry = watch.UscanCacheEntry(
            source_package="nova",
            cached_at_utc="2025-01-15T10:00:00+00:00",
            result=watch.UscanResult(
                success=True,
                status=watch.UscanStatus.UP_TO_DATE,
                debian_upstream_version="2024.2.0",
                upstream_version="2024.2.0",
            ),
        )

        watch.save_uscan_cache({"nova": entry}, cache_path)
        loaded = watch.load_uscan_cache(cache_path)

        assert "nova" in loaded
        assert loaded["nova"].result.status == watch.UscanStatus.UP_TO_DATE
        assert loaded["nova"].result.upstream_version == "2024.2.0"

    def test_load_corrupted_cache(self, tmp_path: Path) -> None:
        """Loading corrupted cache returns empty dict."""
        cache_path = tmp_path / "uscan-cache.json"
        cache_path.write_text("not valid json {{{{")

        cache = watch.load_uscan_cache(cache_path)

        assert cache == {}

    def test_cache_entry_to_dict(self) -> None:
        """Cache entry serializes to dict."""
        entry = watch.UscanCacheEntry(
            source_package="nova",
            cached_at_utc="2025-01-15T10:00:00+00:00",
            result=watch.UscanResult(
                success=True,
                status=watch.UscanStatus.NEWER_AVAILABLE,
                debian_upstream_version="2024.1.0",
                upstream_version="2024.2.0",
                upstream_url="https://example.com/nova-2024.2.0.tar.gz",
                newer_available=True,
            ),
        )

        data = entry.to_dict()

        assert data["source_package"] == "nova"
        assert data["result"]["status"] == "newer_available"
        assert data["result"]["upstream_version"] == "2024.2.0"

    def test_cache_uscan_result_helper(self, tmp_path: Path) -> None:
        """cache_uscan_result helper adds entry to cache."""
        cache: dict[str, watch.UscanCacheEntry] = {}
        result = watch.UscanResult(
            success=True,
            status=watch.UscanStatus.UP_TO_DATE,
            upstream_version="1.0.0",
        )

        watch.cache_uscan_result("test-pkg", result, cache, "/path/to/repo")

        assert "test-pkg" in cache
        assert cache["test-pkg"].result.upstream_version == "1.0.0"
        assert cache["test-pkg"].packaging_repo_path == "/path/to/repo"

    def test_get_cached_uscan_result_found(self) -> None:
        """get_cached_uscan_result returns cached result."""
        result = watch.UscanResult(
            success=True,
            status=watch.UscanStatus.NEWER_AVAILABLE,
            upstream_version="2.0.0",
        )
        cache = {
            "nova": watch.UscanCacheEntry(
                source_package="nova",
                result=result,
                cached_at_utc="2025-01-15T10:00:00+00:00",
            ),
        }

        cached = watch.get_cached_uscan_result("nova", cache)

        assert cached is not None
        assert cached.upstream_version == "2.0.0"

    def test_get_cached_uscan_result_not_found(self) -> None:
        """get_cached_uscan_result returns None when not cached."""
        cache: dict[str, watch.UscanCacheEntry] = {}

        cached = watch.get_cached_uscan_result("nova", cache)

        assert cached is None


class TestUpdateSigningKey:
    """Tests for update_signing_key function."""

    def _make_releases_repo(self, tmp_path: Path, series: str = "gazpacho") -> Path:
        """Create a minimal openstack-releases repo with a signing key."""
        releases_repo = tmp_path / "openstack-releases"
        static_dir = releases_repo / "doc" / "source" / "static"
        static_dir.mkdir(parents=True)

        # Create index.rst with a cycle key entry
        index_rst = releases_repo / "doc" / "source" / "index.rst"
        index_rst.write_text(
            f"* 2025-10-06..present (2026.1/{series.capitalize()} Cycle key):\n"
            "  `key 0xdeadbeef1234567890abcdef`_\n"
        )

        # Create the key file
        key_file = static_dir / "0xdeadbeef1234567890abcdef.txt"
        key_file.write_text("-----BEGIN PGP PUBLIC KEY BLOCK-----\nreleases-repo-key\n")

        return releases_repo

    def test_snapshot_removes_existing_key(self, tmp_path: Path) -> None:
        """Snapshot builds remove the signing key file."""
        pkg_repo = tmp_path / "pkg"
        signing_key = pkg_repo / "debian" / "upstream" / "signing-key.asc"
        signing_key.parent.mkdir(parents=True)
        signing_key.write_text("old key")

        releases_repo = self._make_releases_repo(tmp_path)
        result = watch.update_signing_key(pkg_repo, releases_repo, "gazpacho", is_snapshot=True)

        assert result is True
        assert not signing_key.exists()

    def test_snapshot_no_key_returns_false(self, tmp_path: Path) -> None:
        """Snapshot builds return False when no key to remove."""
        pkg_repo = tmp_path / "pkg"
        (pkg_repo / "debian").mkdir(parents=True)

        releases_repo = self._make_releases_repo(tmp_path)
        result = watch.update_signing_key(pkg_repo, releases_repo, "gazpacho", is_snapshot=True)

        assert result is False

    def test_release_uses_fallback_key_when_available(self, tmp_path: Path, monkeypatch) -> None:
        """Release builds prefer the local fallback key file over openstack-releases."""
        pkg_repo = tmp_path / "pkg"
        (pkg_repo / "debian").mkdir(parents=True)

        # Create fallback key
        fallback_dir = tmp_path / "home" / "openstack-signing-keys"
        fallback_dir.mkdir(parents=True)
        fallback_key = fallback_dir / "gazpacho-signing-key.asc"
        fallback_key.write_text("-----BEGIN PGP PUBLIC KEY BLOCK-----\nfallback-key-with-subkeys\n")

        # Also create a releases repo with a different key
        releases_repo = self._make_releases_repo(tmp_path)

        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        result = watch.update_signing_key(pkg_repo, releases_repo, "gazpacho", is_snapshot=False)

        assert result is True
        signing_key = pkg_repo / "debian" / "upstream" / "signing-key.asc"
        assert signing_key.exists()
        assert "fallback-key-with-subkeys" in signing_key.read_text()

    def test_release_falls_back_to_releases_repo(self, tmp_path: Path, monkeypatch) -> None:
        """Release builds use openstack-releases key when no fallback exists."""
        pkg_repo = tmp_path / "pkg"
        (pkg_repo / "debian").mkdir(parents=True)

        releases_repo = self._make_releases_repo(tmp_path)

        # Point home to a dir without fallback keys
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "empty-home")
        result = watch.update_signing_key(pkg_repo, releases_repo, "gazpacho", is_snapshot=False)

        assert result is True
        signing_key = pkg_repo / "debian" / "upstream" / "signing-key.asc"
        assert signing_key.exists()
        assert "releases-repo-key" in signing_key.read_text()

    def test_release_fallback_key_unchanged_returns_false(self, tmp_path: Path, monkeypatch) -> None:
        """Returns False when fallback key content matches existing signing key."""
        pkg_repo = tmp_path / "pkg"
        signing_key = pkg_repo / "debian" / "upstream" / "signing-key.asc"
        signing_key.parent.mkdir(parents=True)

        key_content = "-----BEGIN PGP PUBLIC KEY BLOCK-----\nsame-key\n"
        signing_key.write_text(key_content)

        # Fallback key has identical content
        fallback_dir = tmp_path / "home" / "openstack-signing-keys"
        fallback_dir.mkdir(parents=True)
        (fallback_dir / "gazpacho-signing-key.asc").write_text(key_content)

        releases_repo = self._make_releases_repo(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        result = watch.update_signing_key(pkg_repo, releases_repo, "gazpacho", is_snapshot=False)

        assert result is False

    def test_release_releases_repo_key_unchanged_returns_false(self, tmp_path: Path, monkeypatch) -> None:
        """Returns False when releases repo key content matches existing signing key."""
        pkg_repo = tmp_path / "pkg"
        signing_key = pkg_repo / "debian" / "upstream" / "signing-key.asc"
        signing_key.parent.mkdir(parents=True)

        key_content = "-----BEGIN PGP PUBLIC KEY BLOCK-----\nreleases-repo-key\n"
        signing_key.write_text(key_content)

        releases_repo = self._make_releases_repo(tmp_path)

        # No fallback key
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "empty-home")
        result = watch.update_signing_key(pkg_repo, releases_repo, "gazpacho", is_snapshot=False)

        assert result is False

    def test_release_no_key_found_returns_false(self, tmp_path: Path, monkeypatch) -> None:
        """Returns False when neither fallback nor releases repo has a key."""
        pkg_repo = tmp_path / "pkg"
        (pkg_repo / "debian").mkdir(parents=True)

        # Empty releases repo
        releases_repo = tmp_path / "openstack-releases"
        index_dir = releases_repo / "doc" / "source"
        index_dir.mkdir(parents=True)
        (index_dir / "index.rst").write_text("no key info here\n")

        monkeypatch.setattr(Path, "home", lambda: tmp_path / "empty-home")
        result = watch.update_signing_key(pkg_repo, releases_repo, "gazpacho", is_snapshot=False)

        assert result is False


class TestIsGpgVerificationFailure:
    """Tests for _is_gpg_verification_failure helper."""

    @pytest.mark.parametrize(
        "output",
        [
            "gpgv: Can't check signature: No public key",
            "some prefix OpenPGP signature did not verify suffix",
            "gpgv: BAD signature from ...",
            "error: no valid OpenPGP data found",
        ],
    )
    def test_detects_gpg_failures(self, output: str) -> None:
        """Recognises known GPG error patterns."""
        assert watch._is_gpg_verification_failure(output) is True

    def test_non_gpg_failure(self) -> None:
        """Returns False for non-GPG errors."""
        assert watch._is_gpg_verification_failure("404 Not Found") is False

    def test_empty_output(self) -> None:
        """Returns False for empty output."""
        assert watch._is_gpg_verification_failure("") is False


class TestRefreshKeyFromKeyserver:
    """Tests for _refresh_key_from_keyserver helper."""

    @patch("packastack.debpkg.watch.subprocess.run")
    def test_successful_refresh(self, mock_run, tmp_path: Path) -> None:
        """Key is exported when gpg commands succeed."""
        signing_key = tmp_path / "debian" / "upstream" / "signing-key.asc"

        # Mock: recv-keys ok, refresh-keys ok, export returns key data
        mock_run.side_effect = [
            MagicMock(returncode=0),  # recv-keys
            MagicMock(returncode=0),  # refresh-keys
            MagicMock(returncode=0, stdout=b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nkey-data\n"),
        ]

        result = watch._refresh_key_from_keyserver(
            "DEADBEEF", "hkps://keys.openpgp.org", signing_key
        )

        assert result is True
        assert signing_key.exists()
        assert b"key-data" in signing_key.read_bytes()

    @patch("packastack.debpkg.watch.subprocess.run")
    def test_export_fails(self, mock_run, tmp_path: Path) -> None:
        """Returns False when gpg export fails."""
        signing_key = tmp_path / "signing-key.asc"

        mock_run.side_effect = [
            MagicMock(returncode=0),  # recv-keys
            MagicMock(returncode=0),  # refresh-keys
            MagicMock(returncode=1, stdout=b""),  # export fails
        ]

        result = watch._refresh_key_from_keyserver(
            "DEADBEEF", "hkps://keys.openpgp.org", signing_key
        )

        assert result is False

    @patch("packastack.debpkg.watch.subprocess.run")
    def test_export_empty_stdout(self, mock_run, tmp_path: Path) -> None:
        """Returns False when gpg export returns empty stdout."""
        signing_key = tmp_path / "signing-key.asc"

        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout=b""),  # empty export
        ]

        result = watch._refresh_key_from_keyserver(
            "DEADBEEF", "hkps://keys.openpgp.org", signing_key
        )

        assert result is False

    @patch("packastack.debpkg.watch.subprocess.run")
    def test_timeout_returns_false(self, mock_run, tmp_path: Path) -> None:
        """Returns False on subprocess timeout."""
        signing_key = tmp_path / "signing-key.asc"
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gpg", timeout=30)

        result = watch._refresh_key_from_keyserver(
            "DEADBEEF", "hkps://keys.openpgp.org", signing_key
        )

        assert result is False

    @patch("packastack.debpkg.watch.subprocess.run")
    def test_gpg_not_found(self, mock_run, tmp_path: Path) -> None:
        """Returns False when gpg is not installed."""
        signing_key = tmp_path / "signing-key.asc"
        mock_run.side_effect = FileNotFoundError("gpg")

        result = watch._refresh_key_from_keyserver(
            "DEADBEEF", "hkps://keys.openpgp.org", signing_key
        )

        assert result is False


class TestUscanVerifyResult:
    """Tests for UscanVerifyResult dataclass."""

    def test_success_defaults(self) -> None:
        """Successful result has expected defaults."""
        r = watch.UscanVerifyResult(success=True)
        assert r.success is True
        assert r.gpg_error is False
        assert r.remediation_attempted is False
        assert r.remediation_succeeded is False
        assert r.error == ""

    def test_failure_with_all_fields(self) -> None:
        """Failure result with all fields set."""
        r = watch.UscanVerifyResult(
            success=False,
            gpg_error=True,
            remediation_attempted=True,
            remediation_succeeded=False,
            error="key missing",
        )
        assert r.success is False
        assert r.gpg_error is True
        assert r.remediation_attempted is True
        assert r.error == "key missing"


class TestVerifySigningKeyWithUscan:
    """Tests for verify_signing_key_with_uscan function."""

    @patch("packastack.debpkg.watch.subprocess.run")
    def test_uscan_succeeds(self, mock_run, tmp_path: Path) -> None:
        """Returns success when uscan passes on first attempt."""
        pkg_repo = tmp_path / "pkg"
        (pkg_repo / "debian" / "upstream").mkdir(parents=True)

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = watch.verify_signing_key_with_uscan(pkg_repo)

        assert result.success is True
        assert result.gpg_error is False
        assert result.remediation_attempted is False

    @patch("packastack.debpkg.watch.subprocess.run")
    def test_uscan_not_installed(self, mock_run, tmp_path: Path) -> None:
        """Returns error when uscan binary is missing."""
        pkg_repo = tmp_path / "pkg"
        (pkg_repo / "debian").mkdir(parents=True)

        mock_run.side_effect = FileNotFoundError("uscan")

        result = watch.verify_signing_key_with_uscan(pkg_repo)

        assert result.success is False
        assert "not installed" in result.error

    @patch("packastack.debpkg.watch.subprocess.run")
    def test_uscan_timeout(self, mock_run, tmp_path: Path) -> None:
        """Returns error when uscan times out."""
        pkg_repo = tmp_path / "pkg"
        (pkg_repo / "debian").mkdir(parents=True)

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="uscan", timeout=120)

        result = watch.verify_signing_key_with_uscan(pkg_repo)

        assert result.success is False
        assert "timed out" in result.error

    @patch("packastack.debpkg.watch.subprocess.run")
    def test_non_gpg_failure(self, mock_run, tmp_path: Path) -> None:
        """Non-GPG failures are reported without remediation."""
        pkg_repo = tmp_path / "pkg"
        (pkg_repo / "debian").mkdir(parents=True)

        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="404 Not Found"
        )

        result = watch.verify_signing_key_with_uscan(pkg_repo)

        assert result.success is False
        assert result.gpg_error is False
        assert result.remediation_attempted is False
        assert "not a GPG issue" in result.error

    @patch("packastack.debpkg.watch._refresh_key_from_keyserver")
    @patch("packastack.debpkg.watch.subprocess.run")
    def test_gpg_failure_remediation_succeeds(
        self, mock_run, mock_refresh, tmp_path: Path
    ) -> None:
        """GPG failure is remediated by keyserver refresh."""
        pkg_repo = tmp_path / "pkg"
        (pkg_repo / "debian" / "upstream").mkdir(parents=True)

        # First uscan: GPG failure; second uscan: success
        mock_run.side_effect = [
            MagicMock(
                returncode=1,
                stdout="",
                stderr="gpgv: Can't check signature: No public key",
            ),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        mock_refresh.return_value = True

        result = watch.verify_signing_key_with_uscan(pkg_repo)

        assert result.success is True
        assert result.gpg_error is True
        assert result.remediation_attempted is True
        assert result.remediation_succeeded is True
        assert result.error == ""
        mock_refresh.assert_called_once()

    @patch("packastack.debpkg.watch._refresh_key_from_keyserver")
    @patch("packastack.debpkg.watch.subprocess.run")
    def test_gpg_failure_refresh_fails(
        self, mock_run, mock_refresh, tmp_path: Path
    ) -> None:
        """Returns failure when keyserver refresh itself fails."""
        pkg_repo = tmp_path / "pkg"
        (pkg_repo / "debian" / "upstream").mkdir(parents=True)

        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="OpenPGP signature did not verify",
        )
        mock_refresh.return_value = False

        result = watch.verify_signing_key_with_uscan(pkg_repo)

        assert result.success is False
        assert result.gpg_error is True
        assert result.remediation_attempted is True
        assert result.remediation_succeeded is False
        assert "keyserver refresh also failed" in result.error

    @patch("packastack.debpkg.watch._refresh_key_from_keyserver")
    @patch("packastack.debpkg.watch.subprocess.run")
    def test_gpg_failure_remediation_still_fails(
        self, mock_run, mock_refresh, tmp_path: Path
    ) -> None:
        """Returns failure when remediation succeeds but uscan still fails."""
        pkg_repo = tmp_path / "pkg"
        (pkg_repo / "debian" / "upstream").mkdir(parents=True)

        # Both uscan runs fail with GPG error
        mock_run.side_effect = [
            MagicMock(
                returncode=1,
                stdout="",
                stderr="BAD signature from key",
            ),
            MagicMock(
                returncode=1,
                stdout="",
                stderr="BAD signature from key still",
            ),
        ]
        mock_refresh.return_value = True

        result = watch.verify_signing_key_with_uscan(pkg_repo)

        assert result.success is False
        assert result.gpg_error is True
        assert result.remediation_attempted is True
        assert result.remediation_succeeded is False
        assert "still failed after keyserver refresh" in result.error

    @patch("packastack.debpkg.watch._refresh_key_from_keyserver")
    @patch("packastack.debpkg.watch.subprocess.run")
    def test_gpg_failure_reverify_timeout(
        self, mock_run, mock_refresh, tmp_path: Path
    ) -> None:
        """Returns failure when re-verification times out."""
        pkg_repo = tmp_path / "pkg"
        (pkg_repo / "debian" / "upstream").mkdir(parents=True)

        mock_run.side_effect = [
            MagicMock(
                returncode=1,
                stdout="",
                stderr="no valid OpenPGP data",
            ),
            subprocess.TimeoutExpired(cmd="uscan", timeout=120),
        ]
        mock_refresh.return_value = True

        result = watch.verify_signing_key_with_uscan(pkg_repo)

        assert result.success is False
        assert "re-verification" in result.error
