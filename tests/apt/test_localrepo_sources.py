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

"""Tests for source index functionality in packastack.apt.localrepo module."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from packastack.apt.localrepo import (
    SourcePackageInfo,
    SourceIndexResult,
    format_sources_entry,
    regenerate_source_indexes,
    regenerate_all_indexes,
)


class TestSourcePackageInfo:
    """Tests for SourcePackageInfo dataclass."""

    def test_basic_info(self) -> None:
        """Test basic source info."""
        info = SourcePackageInfo(source="nova", version="29.0.0-0ubuntu1")
        assert info.source == "nova"
        assert info.version == "29.0.0-0ubuntu1"
        assert info.maintainer == ""
        assert info.build_depends == ""

    def test_full_info(self) -> None:
        """Test full source info."""
        info = SourcePackageInfo(
            source="nova",
            version="29.0.0-0ubuntu1",
            maintainer="Ubuntu Developers",
            build_depends="debhelper, python3",
            architecture="any",
            format="3.0 (quilt)",
        )
        assert info.maintainer == "Ubuntu Developers"
        assert info.build_depends == "debhelper, python3"


class TestSourceIndexResult:
    """Tests for SourceIndexResult dataclass."""

    def test_success_result(self, tmp_path: Path) -> None:
        """Test successful source index result."""
        sources = tmp_path / "Sources"
        result = SourceIndexResult(
            success=True,
            sources_file=sources,
            source_count=5,
        )
        assert result.success is True
        assert result.source_count == 5

    def test_failure_result(self) -> None:
        """Test failure source index result."""
        result = SourceIndexResult(
            success=False,
            error="Permission denied",
        )
        assert result.success is False
        assert result.error == "Permission denied"


class TestFormatSourcesEntry:
    """Tests for format_sources_entry function."""

    def test_minimal_entry(self) -> None:
        """Test formatting minimal source entry."""
        info = SourcePackageInfo(source="nova", version="29.0.0-0ubuntu1")
        entry = format_sources_entry(info)
        assert "Package: nova" in entry
        assert "Version: 29.0.0-0ubuntu1" in entry

    def test_full_entry(self) -> None:
        """Test formatting full source entry."""
        info = SourcePackageInfo(
            source="nova",
            version="29.0.0-0ubuntu1",
            maintainer="Ubuntu Developers",
            build_depends="debhelper, python3",
            architecture="any",
            format="3.0 (quilt)",
            directory="pool/main",
        )
        entry = format_sources_entry(info)
        assert "Package: nova" in entry
        assert "Maintainer: Ubuntu Developers" in entry
        assert "Build-Depends: debhelper, python3" in entry
        assert "Architecture: any" in entry
        assert "Directory: pool/main" in entry

    def test_entry_with_files(self) -> None:
        """Test formatting source entry with files."""
        info = SourcePackageInfo(
            source="nova",
            version="29.0.0-0ubuntu1",
            files=[
                ("nova_29.0.0.orig.tar.gz", 12345, "abc123"),
                ("nova_29.0.0-0ubuntu1.debian.tar.xz", 5678, "def456"),
            ],
        )
        entry = format_sources_entry(info)
        assert "Checksums-Sha256:" in entry
        assert "abc123" in entry
        assert "def456" in entry


class TestRegenerateSourceIndexes:
    """Tests for regenerate_source_indexes function."""

    def test_empty_repo(self, tmp_path: Path) -> None:
        """Test regenerating indexes for empty repo."""
        result = regenerate_source_indexes(tmp_path)
        assert result.success is True
        assert result.source_count == 0

        # Check files were created
        sources_file = tmp_path / "dists" / "local" / "main" / "source" / "Sources"
        sources_gz_file = tmp_path / "dists" / "local" / "main" / "source" / "Sources.gz"
        assert sources_file.exists()
        assert sources_gz_file.exists()

    def test_with_dsc_files(self, tmp_path: Path) -> None:
        """Test regenerating indexes with .dsc files."""
        # Create pool and .dsc file
        pool = tmp_path / "pool" / "main"
        pool.mkdir(parents=True)

        dsc_content = """Format: 3.0 (quilt)
Source: test-pkg
Version: 1.0.0-1
Maintainer: Test <test@example.com>
Architecture: any
"""
        (pool / "test-pkg_1.0.0-1.dsc").write_text(dsc_content)

        result = regenerate_source_indexes(tmp_path)
        assert result.success is True
        assert result.source_count == 1

        # Check content
        sources = result.sources_file.read_text()
        assert "Package: test-pkg" in sources
        assert "Version: 1.0.0-1" in sources


class TestRegenerateAllIndexes:
    """Tests for regenerate_all_indexes function."""

    def test_both_indexes(self, tmp_path: Path) -> None:
        """Test regenerating both binary and source indexes."""
        binary_result, source_result = regenerate_all_indexes(tmp_path)
        assert binary_result.success is True
        assert source_result.success is True
        assert binary_result.package_count == 0
        assert source_result.source_count == 0
