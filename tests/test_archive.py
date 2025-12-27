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

"""Tests for packastack.archive module."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import responses

from packastack import archive


class TestArchiveFetcher:
    """Tests for ArchiveFetcher class."""

    def test_build_url_release_pocket(self) -> None:
        fetcher = archive.ArchiveFetcher()
        url = fetcher.build_url(
            mirror="http://archive.ubuntu.com/ubuntu",
            series="noble",
            pocket="release",
            component="main",
            arch="amd64",
        )
        assert url == "http://archive.ubuntu.com/ubuntu/dists/noble/main/binary-amd64/Packages.gz"

    def test_build_url_updates_pocket(self) -> None:
        fetcher = archive.ArchiveFetcher()
        url = fetcher.build_url(
            mirror="http://archive.ubuntu.com/ubuntu",
            series="noble",
            pocket="updates",
            component="main",
            arch="amd64",
        )
        assert url == "http://archive.ubuntu.com/ubuntu/dists/noble-updates/main/binary-amd64/Packages.gz"

    def test_build_url_security_pocket(self) -> None:
        fetcher = archive.ArchiveFetcher()
        url = fetcher.build_url(
            mirror="http://archive.ubuntu.com/ubuntu",
            series="noble",
            pocket="security",
            component="universe",
            arch="arm64",
        )
        assert url == "http://archive.ubuntu.com/ubuntu/dists/noble-security/universe/binary-arm64/Packages.gz"

    def test_build_url_strips_trailing_slash(self) -> None:
        fetcher = archive.ArchiveFetcher()
        url = fetcher.build_url(
            mirror="http://archive.ubuntu.com/ubuntu/",
            series="noble",
            pocket="release",
            component="main",
            arch="amd64",
        )
        assert "ubuntu//dists" not in url

    @responses.activate
    def test_fetch_index_success(
        self, temp_home: Path, sample_packages_gz: bytes
    ) -> None:
        url = "http://archive.ubuntu.com/ubuntu/dists/noble/main/binary-amd64/Packages.gz"
        responses.add(
            responses.GET,
            url,
            body=sample_packages_gz,
            status=200,
            headers={"ETag": '"abc123"', "Last-Modified": "Thu, 01 Jan 2025 00:00:00 GMT"},
        )

        dest = temp_home / "Packages.gz"
        fetcher = archive.ArchiveFetcher()
        result = fetcher.fetch_index(url, dest)

        assert result.error is None
        assert result.was_cached is False
        assert result.etag == '"abc123"'
        assert result.last_modified == "Thu, 01 Jan 2025 00:00:00 GMT"
        assert dest.exists()
        assert result.size == len(sample_packages_gz)

    @responses.activate
    def test_fetch_index_304_not_modified(
        self, temp_home: Path, sample_packages_gz: bytes
    ) -> None:
        url = "http://archive.ubuntu.com/ubuntu/dists/noble/main/binary-amd64/Packages.gz"
        responses.add(responses.GET, url, status=304)

        dest = temp_home / "Packages.gz"
        dest.write_bytes(sample_packages_gz)

        fetcher = archive.ArchiveFetcher()
        result = fetcher.fetch_index(
            url, dest, etag='"abc123"', last_modified="Thu, 01 Jan 2025 00:00:00 GMT"
        )

        assert result.error is None
        assert result.was_cached is True
        assert result.etag == '"abc123"'

    @responses.activate
    def test_fetch_index_sends_conditional_headers(
        self, temp_home: Path, sample_packages_gz: bytes
    ) -> None:
        url = "http://archive.ubuntu.com/ubuntu/dists/noble/main/binary-amd64/Packages.gz"
        responses.add(responses.GET, url, body=sample_packages_gz, status=200)

        dest = temp_home / "Packages.gz"
        fetcher = archive.ArchiveFetcher()
        fetcher.fetch_index(
            url, dest, etag='"test-etag"', last_modified="Thu, 01 Jan 2025 00:00:00 GMT"
        )

        assert responses.calls[0].request.headers["If-None-Match"] == '"test-etag"'
        assert responses.calls[0].request.headers["If-Modified-Since"] == "Thu, 01 Jan 2025 00:00:00 GMT"

    @responses.activate
    def test_fetch_index_http_error(self, temp_home: Path) -> None:
        url = "http://archive.ubuntu.com/ubuntu/dists/noble/main/binary-amd64/Packages.gz"
        responses.add(responses.GET, url, status=404)

        dest = temp_home / "Packages.gz"
        fetcher = archive.ArchiveFetcher()
        result = fetcher.fetch_index(url, dest)

        assert result.error is not None
        assert "404" in result.error

    def test_fetch_index_offline_mode_file_exists(
        self, temp_home: Path, sample_packages_gz: bytes
    ) -> None:
        dest = temp_home / "Packages.gz"
        dest.write_bytes(sample_packages_gz)

        fetcher = archive.ArchiveFetcher()
        result = fetcher.fetch_index(
            "http://example.com/Packages.gz", dest, offline=True
        )

        assert result.error is None
        assert result.was_cached is True
        assert result.sha256 != ""

    def test_fetch_index_offline_mode_file_missing(self, temp_home: Path) -> None:
        dest = temp_home / "missing" / "Packages.gz"

        fetcher = archive.ArchiveFetcher()
        result = fetcher.fetch_index(
            "http://example.com/Packages.gz", dest, offline=True
        )

        assert result.error is not None
        assert "not found" in result.error.lower()


class TestComputeSha256:
    """Tests for compute_sha256 function."""

    def test_computes_correct_hash(self, temp_home: Path) -> None:
        test_file = temp_home / "test.txt"
        content = b"hello world"
        test_file.write_bytes(content)

        expected = hashlib.sha256(content).hexdigest()
        result = archive.compute_sha256(test_file)

        assert result == expected

    def test_handles_large_file(self, temp_home: Path) -> None:
        test_file = temp_home / "large.bin"
        # Create a file larger than the chunk size (64KB)
        content = b"x" * (100 * 1024)
        test_file.write_bytes(content)

        expected = hashlib.sha256(content).hexdigest()
        result = archive.compute_sha256(test_file)

        assert result == expected


class TestValidateGzip:
    """Tests for validate_gzip function."""

    def test_validates_good_gzip(self, temp_home: Path, sample_packages_gz: bytes) -> None:
        test_file = temp_home / "good.gz"
        test_file.write_bytes(sample_packages_gz)

        assert archive.validate_gzip(test_file) is True

    def test_rejects_corrupt_gzip(self, temp_home: Path) -> None:
        test_file = temp_home / "corrupt.gz"
        test_file.write_bytes(b"not a gzip file")

        assert archive.validate_gzip(test_file) is False

    def test_rejects_truncated_gzip(self, temp_home: Path, sample_packages_gz: bytes) -> None:
        test_file = temp_home / "truncated.gz"
        # Write only half of the gzip data
        test_file.write_bytes(sample_packages_gz[: len(sample_packages_gz) // 2])

        assert archive.validate_gzip(test_file) is False


class TestWriteMetadata:
    """Tests for write_metadata function."""

    def test_writes_metadata_json(self, temp_home: Path) -> None:
        dest = temp_home / "Packages.gz"
        dest.write_bytes(b"test")

        result = archive.FetchResult(
            url="http://example.com/Packages.gz",
            path=dest,
            etag='"abc123"',
            last_modified="Thu, 01 Jan 2025 00:00:00 GMT",
            fetched_utc="2025-01-01T00:00:00",
            sha256="abc123def456",
            size=1234,
        )

        archive.write_metadata(dest, result)

        meta_path = dest.with_suffix(".meta.json")
        assert meta_path.exists()

        meta = json.loads(meta_path.read_text())
        assert meta["url"] == "http://example.com/Packages.gz"
        assert meta["etag"] == '"abc123"'
        assert meta["sha256"] == "abc123def456"
        assert meta["size"] == 1234


class TestLoadMetadata:
    """Tests for load_metadata function."""

    def test_loads_existing_metadata(self, temp_home: Path) -> None:
        dest = temp_home / "Packages.gz"
        meta_path = dest.with_suffix(".meta.json")
        meta_data = {"url": "http://example.com", "etag": '"test"'}
        meta_path.write_text(json.dumps(meta_data))

        result = archive.load_metadata(dest)

        assert result is not None
        assert result["url"] == "http://example.com"
        assert result["etag"] == '"test"'

    def test_returns_none_when_missing(self, temp_home: Path) -> None:
        dest = temp_home / "missing" / "Packages.gz"

        result = archive.load_metadata(dest)

        assert result is None

    def test_returns_none_on_invalid_json(self, temp_home: Path) -> None:
        dest = temp_home / "Packages.gz"
        meta_path = dest.with_suffix(".meta.json")
        meta_path.write_text("not valid json")

        result = archive.load_metadata(dest)

        assert result is None
