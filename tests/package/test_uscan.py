# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from packastack.exceptions import DebianError
from packastack.package.uscan import Uscan, UscanResult, WatchEntry


def make_watch(tmp_path: Path, body: str) -> Path:
    """Helper to create a temporary watch file."""
    watch = tmp_path / "watch"
    watch.write_text(body, encoding="utf-8")
    return watch


def make_changelog(tmp_path: Path, version: str, name: str = "pkg") -> Path:
    """Create a minimal Debian changelog with the provided version."""

    changelog = tmp_path / "changelog"
    changelog.write_text(
        f"{name} ({version}) unstable; urgency=medium\n\n  * test entry\n\n -- Dev <dev@example.com>  Tue, 01 Jan 2025 00:00:00 +0000\n",
        encoding="utf-8",
    )
    return changelog


def fake_response(html: str) -> SimpleNamespace:
    """Return a minimal response-like object for _http_get patching."""
    return SimpleNamespace(text=html)


def test_scan_identifies_latest_release(tmp_path: Path, monkeypatch):
    """Ensure the newest match is returned when multiple versions exist."""
    html = """
    <a href="pkg-1.0.0.tar.gz">1.0.0</a>
    <a href="pkg-2.1.0.tar.gz">2.1.0</a>
    <a href="pkg-2.0.0.tar.gz">2.0.0</a>
    """
    watch = make_watch(
        tmp_path,
        "version=4\nhttp://example.com pkg-(?P<version>[0-9\\.]+)\\.tar\\.gz\n",
    )
    monkeypatch.setattr(Uscan, "_http_get", lambda self, url: fake_response(html))

    result = Uscan(watch).scan()

    assert {m.version for m in result.matches} == {"1.0.0", "2.0.0", "2.1.0"}
    assert result.latest.version == "2.1.0"
    assert result.latest.url.endswith("pkg-2.1.0.tar.gz")


def test_scan_applies_mangles(tmp_path: Path, monkeypatch):
    """uversionmangle, downloadurlmangle, and filenamemangle are applied in order."""
    html = '<a href="downloads/pkg-2.0rc1.tar.gz">Release</a>'
    watch = make_watch(
        tmp_path,
        (
            "version=4\n"
            "opts=\"uversionmangle=s/rc/~rc/;s/0/~0/,"
            "downloadurlmangle=s/\\.tar\\.gz/.zip/,"
            "filenamemangle=s/.+\\/pkg-(.*)\\.tar\\.gz/pkg-$1.orig.tar.gz/\" "
            "http://example.com pkg-(?P<version>[0-9\\.]+rc[0-9]+)\\.tar\\.gz\n"
        ),
    )
    monkeypatch.setattr(Uscan, "_http_get", lambda self, url: fake_response(html))

    result = Uscan(watch).scan()
    assert len(result.matches) == 1

    match = result.matches[0]
    assert match.version == "2.~0~rc1"
    assert match.url.endswith(".zip")
    assert match.filename == "pkg-2.0rc1.tar.gz"


def test_perl_style_backreference_conversion(tmp_path: Path, monkeypatch):
    """Perl replacement backreferences ($1) should be converted for mangles."""

    html = '<a href="pkg-1.2.tar.gz">Release</a>'
    watch = make_watch(
        tmp_path,
        (
            "version=4\n"
            "opts=filenamemangle=s/pkg-(\\d+\\.\\d+)\\.tar\\.gz/pkg-$1-src.tar.gz/ "
            "http://example.com pkg-(?P<version>\\d+\\.\\d+)\\.tar\\.gz\n"
        ),
    )
    monkeypatch.setattr(Uscan, "_http_get", lambda self, url: fake_response(html))

    result = Uscan(watch).scan()
    assert result.matches[0].filename == "pkg-1.2-src.tar.gz"


def test_scan_prefers_anchor_links_for_html(tmp_path: Path, monkeypatch):
    """Apache listings should use href targets rather than raw HTML text."""

    html = (
        "<a href=\"oslo_middleware-7.0.0.tar.gz.asc\">sig</a>"
        "<a href=\"oslo_middleware-7.0.0.tar.gz\">oslo_middleware-7.0.0.tar.gz</a>"
    )

    watch = make_watch(
        tmp_path,
        (
            "version=4\n"
            "http://example.com "
            "oslo_middleware-(?P<version>.+)\\.tar\\.gz\n"
        ),
    )

    monkeypatch.setattr(Uscan, "_http_get", lambda self, url: fake_response(html))

    result = Uscan(watch).scan()

    assert [m.version for m in result.matches] == ["7.0.0"]
    assert result.matches[0].url.endswith("oslo_middleware-7.0.0.tar.gz")


def test_scan_collects_pgp_signatures(tmp_path: Path, monkeypatch):
    """Signature URLs should be attached when pgpsigurlmangle matches a link."""

    html = (
        "<a href=\"pkg-4.2.tar.gz.asc\">sig</a>"
        "<a href=\"pkg-4.2.tar.gz\">tarball</a>"
    )

    watch = make_watch(
        tmp_path,
        (
            "version=4\n"
            "opts=pgpsigurlmangle=s/$/.asc/ "
            "http://example.com/pkg pkg-(?P<version>\\d+\\.\\d+)\\.tar\\.gz\n"
        ),
    )

    monkeypatch.setattr(Uscan, "_http_get", lambda self, url: fake_response(html))

    result = Uscan(watch).scan()

    assert result.matches[0].signature_url == "http://example.com/pkg-4.2.tar.gz.asc"
    assert result.signatures == ["http://example.com/pkg-4.2.tar.gz.asc"]


def test_scan_ignores_missing_pgp_signatures(tmp_path: Path, monkeypatch):
    """pgpsigurlmangle should not fabricate signatures that are not present."""

    html = "<a href=\"pkg-4.2.tar.gz\">tarball</a>"

    watch = make_watch(
        tmp_path,
        (
            "version=4\n"
            "opts=pgpsigurlmangle=s/$/.asc/ "
            "http://example.com/pkg pkg-(?P<version>\\d+\\.\\d+)\\.tar\\.gz\n"
        ),
    )

    monkeypatch.setattr(Uscan, "_http_get", lambda self, url: fake_response(html))

    result = Uscan(watch).scan()

    assert result.matches[0].signature_url is None
    assert result.signatures == []


def test_watch_without_version_raises(tmp_path: Path):
    """Watch files must declare a supported version."""
    watch = make_watch(
        tmp_path,
        "opts=uversionmangle=s/rc/~rc/ http://example.invalid pkg-(\\d+)\\.tar\\.gz\n",
    )

    scanner = Uscan(watch)
    with pytest.raises(DebianError, match="missing version"):
        _ = scanner.entries


def test_unsupported_watch_version(tmp_path: Path):
    """Reject watch versions older than 3."""
    watch = make_watch(
        tmp_path, "version=2\nhttp://example.com pkg-(\\d+)\\.tar\\.gz\n"
    )
    with pytest.raises(DebianError, match="unsupported watch file version"):
        Uscan(watch).entries


def test_supports_watch_version_three(tmp_path: Path, monkeypatch):
    """Version 3 watch files are supported."""

    html = '<a href="pkg-1.2.tar.gz">tarball</a>'
    watch = make_watch(
        tmp_path, "version=3\nhttp://example.com pkg-(?P<version>\\d+\\.\\d+)\\.tar\\.gz\n"
    )
    monkeypatch.setattr(Uscan, "_http_get", lambda self, url: fake_response(html))

    result = Uscan(watch).scan()

    assert result.matches[0].version == "1.2"


def test_single_field_watch_entry_with_opts(tmp_path: Path, monkeypatch):
    """Watch entries with only opts and a combined URL+pattern should parse."""

    html = (
        "<a href=\"gnocchi-5.0.0.tar.gz\">stable</a>"
        "<a href=\"gnocchi-5.0.0rc1.tar.gz\">rc</a>"
    )

    watch = make_watch(
        tmp_path,
        (
            "version=3\n"
            "opts=uversionmangle=s/(rc|a|b|c)/~$1/ \\\n"
            "https://pypi.debian.net/gnocchi/gnocchi-(.+)\\.(?:zip|tgz|tbz|txz|(?:tar\\.(?:gz|bz2|xz)))\n"
        ),
    )

    monkeypatch.setattr(Uscan, "_http_get", lambda self, url: fake_response(html))

    scanner = Uscan(watch)
    entries = scanner.entries
    assert entries[0].url == "https://pypi.debian.net/gnocchi/"

    result = scanner.scan()
    assert {m.version for m in result.matches} == {"5.0.0", "5.0.0~rc1"}


def test_uversionmangle_backreference_is_substituted(tmp_path: Path, monkeypatch):
    """Perl-style backreferences should expand instead of remaining literal."""

    html = '<a href="pkg-1.0.0rc1.tar.gz">release</a>'
    watch = make_watch(
        tmp_path,
        (
            "version=4\n"
            "opts=uversionmangle=s/(rc|a|b|c)/~$1/ "
            "http://example.com pkg-(?P<version>\\d+\\.\\d+rc\\d+)\\.tar\\.gz\n"
        ),
    )

    monkeypatch.setattr(Uscan, "_http_get", lambda self, url: fake_response(html))

    result = Uscan(watch).scan()
    assert result.matches[0].version == "1.0.0~rc1"


def test_invalid_regex(tmp_path: Path):
    """Invalid regex patterns should surface as DebianError."""
    watch = make_watch(
        tmp_path,
        "version=4\nhttp://example.com pkg-(?P<version>[0-9]+(.tar.gz\n",
    )
    with pytest.raises(DebianError, match="invalid regex"):
        Uscan(watch).entries


def test_missing_version_group_raises(tmp_path: Path, monkeypatch):
    """A regex without a version capture should raise when scanning."""
    watch = make_watch(
        tmp_path,
        "version=4\nhttp://example.com pkg-[0-9]+\\.tar\\.gz\n",
    )
    monkeypatch.setattr(
        Uscan, "_http_get", lambda self, url: fake_response("pkg-1.tar.gz")
    )

    with pytest.raises(DebianError, match="version via a named"):
        Uscan(watch).scan()


def test_apply_single_mangle_errors_on_bad_expression():
    """Ensure malformed mangles raise DebianError."""
    with pytest.raises(DebianError, match="invalid mangle expression"):
        Uscan._apply_single_mangle("value", "s/")


def test_apply_single_mangle_returns_original_for_non_sub():
    """Non-substitution mangles should be ignored."""
    assert Uscan._apply_single_mangle("value", "noop") == "value"


def test_split_unescaped_handles_escapes():
    """Validate delimiter splitting honours escapes."""
    parts = Uscan._split_unescaped(r"a\\/b/c", "/")
    assert parts == [r"a\\", "b", "c"]


def test_latest_none_when_no_matches():
    """UscanResult.latest should be None when empty."""
    assert UscanResult(matches=[]).latest is None


def test_watch_file_missing(tmp_path: Path):
    """Missing watch files raise DebianError."""
    with pytest.raises(DebianError, match="watch file not found"):
        Uscan(tmp_path / "missing")


def test_no_entries_raises(tmp_path: Path):
    """Watch files with no usable entries are rejected."""
    watch = make_watch(tmp_path, "version=4\n# comment only\n")
    with pytest.raises(DebianError, match="no usable entries"):
        Uscan(watch).entries


def test_continuation_and_comments(tmp_path: Path, monkeypatch):
    """Line continuations and comments are handled when parsing."""
    html = '<a href="pkg-3.0.0.tar.gz">v3</a>'
    watch = make_watch(
        tmp_path,
        (
            "version=4\n"
            "# comment line\n"
            "http://example.com \\\n"
            "   pkg-(?P<version>\\d+\\.\\d+\\.\\d+)\\.tar\\.gz\n"
        ),
    )
    monkeypatch.setattr(Uscan, "_http_get", lambda self, url: fake_response(html))
    result = Uscan(watch).scan()
    assert result.latest.version == "3.0.0"


def test_relative_download_url_and_filename_fallback(tmp_path: Path, monkeypatch):
    """Relative URLs join base URL and fall back when filename is blank."""
    html = '<a href="files/pkg-1.tar.gz">link</a>'
    watch = make_watch(
        tmp_path,
        (
            "version=4\n"
            "opts=filenamemangle=s/.+// "
            "http://example.com/releases/ files/pkg-(\\d+)\\.tar\\.gz\n"
        ),
    )
    monkeypatch.setattr(Uscan, "_http_get", lambda self, url: fake_response(html))
    result = Uscan(watch).scan()
    match = result.matches[0]
    assert match.url == "http://example.com/releases/files/pkg-1.tar.gz"
    assert match.filename == "pkg-1.tar.gz"


def test_package_placeholder_substitution(tmp_path: Path, monkeypatch):
    """@PACKAGE@ tokens should resolve using debian/changelog when present."""

    make_changelog(tmp_path, version="1.0-1", name="placeholder")
    html = '<a href="placeholder-2.tar.gz">link</a>'
    watch = make_watch(
        tmp_path,
        (
            "version=4\n"
            "http://example.com @PACKAGE@-(?P<version>\\d+)\\.tar\\.gz\n"
        ),
    )
    monkeypatch.setattr(Uscan, "_http_get", lambda self, url: fake_response(html))

    result = Uscan(watch).scan()
    assert result.matches[0].url == "http://example.com/placeholder-2.tar.gz"


def test_perl_regex_escape_handling(tmp_path: Path, monkeypatch):
    """Perl-style \Q..\E escapes should be converted for Python regex."""

    html = '<a href="pkg-special+chars-3.tar.gz">release</a>'
    watch = make_watch(
        tmp_path,
        (
            "version=4\n"
            "http://example.com pkg-\\Qspecial+chars\\E-(?P<version>\\d+)\\.tar\\.gz\n"
        ),
    )
    monkeypatch.setattr(Uscan, "_http_get", lambda self, url: fake_response(html))

    result = Uscan(watch).scan()
    assert result.matches[0].version == "3"


def test_parse_opts_variations():
    """Ensure opts parsing covers empty and flag-only cases."""
    assert Uscan._parse_opts("opts=") == {}
    assert Uscan._parse_opts("opts=flag") == {"flag": ""}
    assert Uscan._parse_opts("opts=,flag") == {"flag": ""}
    parsed = Uscan._parse_opts('opts="key=val,other"')
    assert parsed["key"] == "val"
    assert parsed["other"] == ""


def test_http_get_uses_session(tmp_path: Path):
    """_http_get should delegate to the configured session."""
    watch = make_watch(
        tmp_path,
        "version=4\nhttp://example.com pkg-(\\d+)\\.tar\\.gz\n",
    )

    class FakeResponse:
        def __init__(self, text: str = ""):
            self.text = text

        def raise_for_status(self):
            return None

    class FakeSession:
        def __init__(self):
            self.headers = {}

        def get(self, url, timeout):
            return FakeResponse("pkg-1.tar.gz")

    scanner = Uscan(watch, session=FakeSession())
    response = scanner._http_get("http://example.com")
    assert isinstance(response, FakeResponse)


def test_invalid_watch_entry(tmp_path: Path):
    """Watch entries missing URL/pattern raise DebianError."""
    watch = make_watch(
        tmp_path,
        "version=4\nopts=uversionmangle=s/rc/~rc/\n",
    )
    with pytest.raises(DebianError, match="invalid watch entry"):
        Uscan(watch).entries


def test_entries_cache_path(tmp_path: Path):
    """entries should return cached results without re-parsing."""
    watch = make_watch(
        tmp_path,
        "version=4\nhttp://example.com pkg-(\\d+)\\.tar\\.gz\n",
    )
    scanner = Uscan(watch)
    cached = [WatchEntry(url="u", pattern=re.compile("x"))]
    scanner._entries = cached
    assert scanner.entries is cached


def test_no_update_needed_when_packaged_is_newer(tmp_path: Path, monkeypatch):
    """When the packaged version is newer or equal, no update is needed."""

    make_changelog(tmp_path, "2.0-1")
    html = '<a href="pkg-2.0.tar.gz">tarball</a>'
    watch = make_watch(
        tmp_path,
        "version=4\nhttp://example.com pkg-(?P<version>\\d+\\.\\d+)\\.tar\\.gz\n",
    )
    monkeypatch.setattr(Uscan, "_http_get", lambda self, url: fake_response(html))

    result = Uscan(watch).scan()

    assert result.packaged_version == "2.0-1"
    assert result.needs_update is False


def test_needs_update_when_upstream_is_newer(tmp_path: Path, monkeypatch):
    """A newer upstream version indicates an update is needed."""

    make_changelog(tmp_path, "1.0-1")
    html = '<a href="pkg-1.1.tar.gz">tarball</a>'
    watch = make_watch(
        tmp_path,
        "version=4\nhttp://example.com pkg-(?P<version>\\d+\\.\\d+)\\.tar\\.gz\n",
    )
    monkeypatch.setattr(Uscan, "_http_get", lambda self, url: fake_response(html))

    result = Uscan(watch).scan()

    assert result.packaged_version == "1.0-1"
    assert result.needs_update is True


def test_absolute_download_url_passthrough(tmp_path: Path, monkeypatch):
    """Absolute URLs skip urljoin branch."""
    html = '<a href="http://example.com/pkg-2.tar.gz">link</a>'
    watch = make_watch(
        tmp_path,
        "version=4\nhttp://example.com http://example.com/pkg-(?P<version>\\d+)\\.tar\\.gz\n",
    )
    monkeypatch.setattr(Uscan, "_http_get", lambda self, url: fake_response(html))
    result = Uscan(watch).scan()
    assert result.matches[0].url == "http://example.com/pkg-2.tar.gz"


def test_extract_version_first_group():
    """Version extraction should fall back to the first group when unnamed."""
    match = re.search(r"pkg-(\d+)\.tar\.gz", "pkg-42.tar.gz")
    assert match is not None
    assert Uscan._extract_version(match) == "42"
