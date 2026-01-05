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

import json
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
