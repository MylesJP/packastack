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

"""Tests for packastack refresh command."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest import mock

import pytest
import responses

from packastack.commands import refresh as refresh_cmd


class TestRefreshUbuntuArchive:
    """Tests for refresh_ubuntu_archive function."""

    @responses.activate
    def test_fetches_packages_successfully(
        self, temp_home: Path, mock_config: Path, mock_cache_dirs: dict[str, Path], sample_packages_gz: bytes
    ) -> None:
        # Set up mock response for a single package index
        url = "http://archive.ubuntu.com/ubuntu/dists/noble/main/binary-amd64/Packages.gz"
        responses.add(
            responses.GET,
            url,
            body=sample_packages_gz,
            status=200,
            headers={"ETag": '"test-etag"'},
        )

        with mock.patch("platform.machine", return_value="x86_64"):
            exit_code = refresh_cmd.refresh_ubuntu_archive(
                ubuntu_series="noble",
                pockets=["release"],
                components=["main"],
                arches=["host"],
                mirror="http://archive.ubuntu.com/ubuntu",
                ttl_seconds=0,
                force=True,
                offline=False,
                run=None,
            )

        assert exit_code == 0

        # Check file was created
        dest = mock_cache_dirs["ubuntu_archive_cache"] / "indexes" / "noble" / "release" / "main" / "binary-amd64" / "Packages.gz"
        assert dest.exists()

    @responses.activate
    def test_respects_ttl(
        self, temp_home: Path, mock_config: Path, mock_cache_dirs: dict[str, Path], sample_packages_gz: bytes
    ) -> None:
        # Create existing metadata that is within TTL
        dest_dir = mock_cache_dirs["ubuntu_archive_cache"] / "indexes" / "noble" / "release" / "main" / "binary-amd64"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "Packages.gz"
        dest.write_bytes(sample_packages_gz)

        meta = {
            "url": "http://archive.ubuntu.com/ubuntu/dists/noble/main/binary-amd64/Packages.gz",
            "etag": '"old-etag"',
            "fetched_utc": datetime.datetime.utcnow().isoformat(),
            "sha256": "abc123",
            "size": 100,
        }
        (dest_dir / "Packages.meta.json").write_text(json.dumps(meta))

        # No HTTP responses registered - should not make any requests
        with mock.patch("platform.machine", return_value="x86_64"):
            exit_code = refresh_cmd.refresh_ubuntu_archive(
                ubuntu_series="noble",
                pockets=["release"],
                components=["main"],
                arches=["host"],
                mirror="http://archive.ubuntu.com/ubuntu",
                ttl_seconds=3600,  # 1 hour TTL
                force=False,
                offline=False,
                run=None,
            )

        assert exit_code == 0
        # No HTTP calls should have been made
        assert len(responses.calls) == 0

    @responses.activate
    def test_force_ignores_ttl(
        self, temp_home: Path, mock_config: Path, mock_cache_dirs: dict[str, Path], sample_packages_gz: bytes
    ) -> None:
        # Create existing metadata that is within TTL
        dest_dir = mock_cache_dirs["ubuntu_archive_cache"] / "indexes" / "noble" / "release" / "main" / "binary-amd64"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "Packages.gz"
        dest.write_bytes(sample_packages_gz)

        meta = {
            "url": "http://archive.ubuntu.com/ubuntu/dists/noble/main/binary-amd64/Packages.gz",
            "etag": '"old-etag"',
            "fetched_utc": datetime.datetime.utcnow().isoformat(),
            "sha256": "abc123",
            "size": 100,
        }
        (dest_dir / "Packages.meta.json").write_text(json.dumps(meta))

        # Register response - force should trigger a fetch even with valid TTL
        url = "http://archive.ubuntu.com/ubuntu/dists/noble/main/binary-amd64/Packages.gz"
        responses.add(responses.GET, url, body=sample_packages_gz, status=200)

        with mock.patch("platform.machine", return_value="x86_64"):
            exit_code = refresh_cmd.refresh_ubuntu_archive(
                ubuntu_series="noble",
                pockets=["release"],
                components=["main"],
                arches=["host"],
                mirror="http://archive.ubuntu.com/ubuntu",
                ttl_seconds=3600,
                force=True,  # Force fetch
                offline=False,
                run=None,
            )

        assert exit_code == 0
        assert len(responses.calls) == 1

    def test_offline_mode_with_existing_file(
        self, temp_home: Path, mock_config: Path, mock_cache_dirs: dict[str, Path], sample_packages_gz: bytes
    ) -> None:
        # Create existing file
        dest_dir = mock_cache_dirs["ubuntu_archive_cache"] / "indexes" / "noble" / "release" / "main" / "binary-amd64"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "Packages.gz"
        dest.write_bytes(sample_packages_gz)

        with mock.patch("platform.machine", return_value="x86_64"):
            exit_code = refresh_cmd.refresh_ubuntu_archive(
                ubuntu_series="noble",
                pockets=["release"],
                components=["main"],
                arches=["host"],
                mirror="http://archive.ubuntu.com/ubuntu",
                ttl_seconds=0,
                force=True,
                offline=True,
                run=None,
            )

        assert exit_code == 0

    def test_offline_mode_missing_file_returns_exit_3(
        self, temp_home: Path, mock_config: Path, mock_cache_dirs: dict[str, Path]
    ) -> None:
        # No file exists
        with mock.patch("platform.machine", return_value="x86_64"):
            exit_code = refresh_cmd.refresh_ubuntu_archive(
                ubuntu_series="noble",
                pockets=["release"],
                components=["main"],
                arches=["host"],
                mirror="http://archive.ubuntu.com/ubuntu",
                ttl_seconds=0,
                force=True,
                offline=True,
                run=None,
            )

        assert exit_code == 3  # EXIT_OFFLINE_MISSING

    @responses.activate
    def test_partial_failure_returns_exit_2(
        self, temp_home: Path, mock_config: Path, mock_cache_dirs: dict[str, Path], sample_packages_gz: bytes
    ) -> None:
        # One succeeds, one fails
        responses.add(
            responses.GET,
            "http://archive.ubuntu.com/ubuntu/dists/noble/main/binary-amd64/Packages.gz",
            body=sample_packages_gz,
            status=200,
        )
        responses.add(
            responses.GET,
            "http://archive.ubuntu.com/ubuntu/dists/noble/universe/binary-amd64/Packages.gz",
            status=404,
        )

        with mock.patch("platform.machine", return_value="x86_64"):
            exit_code = refresh_cmd.refresh_ubuntu_archive(
                ubuntu_series="noble",
                pockets=["release"],
                components=["main", "universe"],
                arches=["host"],
                mirror="http://archive.ubuntu.com/ubuntu",
                ttl_seconds=0,
                force=True,
                offline=False,
                run=None,
            )

        assert exit_code == 2  # EXIT_PARTIAL_FAILURE

    @responses.activate
    def test_corrupt_gzip_returns_exit_4(
        self, temp_home: Path, mock_config: Path, mock_cache_dirs: dict[str, Path]
    ) -> None:
        # Return invalid gzip data
        responses.add(
            responses.GET,
            "http://archive.ubuntu.com/ubuntu/dists/noble/main/binary-amd64/Packages.gz",
            body=b"not valid gzip data",
            status=200,
        )

        with mock.patch("platform.machine", return_value="x86_64"):
            exit_code = refresh_cmd.refresh_ubuntu_archive(
                ubuntu_series="noble",
                pockets=["release"],
                components=["main"],
                arches=["host"],
                mirror="http://archive.ubuntu.com/ubuntu",
                ttl_seconds=0,
                force=True,
                offline=False,
                run=None,
            )

        assert exit_code == 4  # EXIT_CORRUPT_CACHE


class TestRefreshCommand:
    """Tests for refresh CLI command."""

    @responses.activate
    def test_parses_comma_separated_options(
        self, temp_home: Path, mock_config: Path, mock_cache_dirs: dict[str, Path], sample_packages_gz: bytes, non_tty_stdout: None
    ) -> None:
        # Register responses for all combinations
        # Note: 'all' arch is filtered out since binary-all doesn't exist
        for pocket in ["release", "updates"]:
            pocket_suffix = "" if pocket == "release" else f"-{pocket}"
            for component in ["main", "universe"]:
                for arch in ["amd64"]:  # 'all' is filtered, only host arch fetched
                    url = f"http://archive.ubuntu.com/ubuntu/dists/noble{pocket_suffix}/{component}/binary-{arch}/Packages.gz"
                    responses.add(responses.GET, url, body=sample_packages_gz, status=200)

        with mock.patch("platform.machine", return_value="x86_64"):
            with mock.patch("subprocess.run") as mock_subprocess:
                mock_subprocess.return_value = mock.Mock(stdout="noble\n", returncode=0)
                with pytest.raises(SystemExit) as exc_info:
                    refresh_cmd.refresh(
                        ubuntu_series="noble",
                        pockets="release,updates",
                        components="main,universe",
                        arches="host,all",
                        mirror="http://archive.ubuntu.com/ubuntu",
                        ttl="6h",
                        force=True,
                        offline=False,
                    )

        assert exc_info.value.code == 0
        # Should have made 2 pockets * 2 components * 1 arch (all filtered) = 4 requests
        assert len(responses.calls) == 4

    def test_invalid_ttl_returns_exit_1(
        self, temp_home: Path, mock_config: Path, non_tty_stdout: None
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            refresh_cmd.refresh(
                ubuntu_series="noble",
                pockets="release",
                components="main",
                arches="host",
                mirror="http://archive.ubuntu.com/ubuntu",
                ttl="invalid",
                force=False,
                offline=False,
            )

        assert exc_info.value.code == 1  # EXIT_CONFIG_ERROR

    @responses.activate
    def test_writes_summary_json(
        self, temp_home: Path, mock_config: Path, mock_cache_dirs: dict[str, Path], sample_packages_gz: bytes, non_tty_stdout: None
    ) -> None:
        url = "http://archive.ubuntu.com/ubuntu/dists/noble/main/binary-amd64/Packages.gz"
        responses.add(responses.GET, url, body=sample_packages_gz, status=200)

        with mock.patch("platform.machine", return_value="x86_64"):
            with mock.patch("subprocess.run") as mock_subprocess:
                mock_subprocess.return_value = mock.Mock(stdout="noble\n", returncode=0)
                with pytest.raises(SystemExit):
                    refresh_cmd.refresh(
                        ubuntu_series="noble",
                        pockets="release",
                        components="main",
                        arches="host",
                        mirror="http://archive.ubuntu.com/ubuntu",
                        ttl="6h",
                        force=True,
                        offline=False,
                    )

        runs_dir = mock_cache_dirs["runs_root"]
        run_dirs = list(runs_dir.iterdir())
        assert len(run_dirs) == 1

        summary = json.loads((run_dirs[0] / "summary.json").read_text())
        assert summary["status"] == "success"
        assert summary["series"] == "noble"

    @responses.activate
    def test_resolves_devel_series(
        self, temp_home: Path, mock_config: Path, mock_cache_dirs: dict[str, Path], sample_packages_gz: bytes, non_tty_stdout: None
    ) -> None:
        url = "http://archive.ubuntu.com/ubuntu/dists/resolute/main/binary-amd64/Packages.gz"
        responses.add(responses.GET, url, body=sample_packages_gz, status=200)

        with mock.patch("platform.machine", return_value="x86_64"):
            with mock.patch("subprocess.run") as mock_subprocess:
                mock_subprocess.return_value = mock.Mock(stdout="resolute\n", returncode=0)
                with pytest.raises(SystemExit) as exc_info:
                    refresh_cmd.refresh(
                        ubuntu_series="devel",
                        pockets="release",
                        components="main",
                        arches="host",
                        mirror="http://archive.ubuntu.com/ubuntu",
                        ttl="6h",
                        force=True,
                        offline=False,
                    )

        assert exc_info.value.code == 0
        # Verify the request was made with resolved series
        request_url = responses.calls[0].request.url
        assert request_url is not None
        assert "resolute" in request_url
