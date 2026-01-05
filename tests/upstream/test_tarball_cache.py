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

"""Tests for packastack.upstream.tarball_cache module."""

from __future__ import annotations

import json
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from packastack.upstream import tarball_cache


class TestCacheMetadata:
    """Tests for CacheMetadata dataclass."""

    def test_to_dict(self) -> None:
        """Test conversion to dictionary."""
        metadata = tarball_cache.CacheMetadata(
            project="glance",
            version="2024.1.0",
            extracted_at="2024-01-15T10:30:00+00:00",
            tarball_path="/tmp/glance-2024.1.0.tar.gz",
            tarball_size=12345678,
        )

        result = metadata.to_dict()

        assert result["project"] == "glance"
        assert result["version"] == "2024.1.0"
        assert result["extracted_at"] == "2024-01-15T10:30:00+00:00"
        assert result["tarball_path"] == "/tmp/glance-2024.1.0.tar.gz"
        assert result["tarball_size"] == 12345678

    def test_from_dict(self) -> None:
        """Test creation from dictionary."""
        data = {
            "project": "nova",
            "version": "2024.2.0",
            "extracted_at": "2024-02-20T14:00:00+00:00",
            "tarball_path": "/tmp/nova-2024.2.0.tar.gz",
            "tarball_size": 98765432,
        }

        metadata = tarball_cache.CacheMetadata.from_dict(data)

        assert metadata.project == "nova"
        assert metadata.version == "2024.2.0"
        assert metadata.extracted_at == "2024-02-20T14:00:00+00:00"
        assert metadata.tarball_path == "/tmp/nova-2024.2.0.tar.gz"
        assert metadata.tarball_size == 98765432

    def test_is_expired_fresh(self) -> None:
        """Test that fresh cache is not expired."""
        now = datetime.now(timezone.utc)
        metadata = tarball_cache.CacheMetadata(
            project="test",
            version="1.0",
            extracted_at=now.isoformat(),
            tarball_path="/tmp/test.tar.gz",
            tarball_size=1000,
        )

        assert metadata.is_expired(max_age_days=14) is False

    def test_is_expired_old(self) -> None:
        """Test that old cache is expired."""
        old_time = datetime.now(timezone.utc) - timedelta(days=30)
        metadata = tarball_cache.CacheMetadata(
            project="test",
            version="1.0",
            extracted_at=old_time.isoformat(),
            tarball_path="/tmp/test.tar.gz",
            tarball_size=1000,
        )

        assert metadata.is_expired(max_age_days=14) is True


class TestGetCacheDir:
    """Tests for get_cache_dir function."""

    def test_default_path(self, tmp_path: Path) -> None:
        """Test cache directory path generation."""
        cache_dir = tarball_cache.get_cache_dir(
            project="glance",
            version="2024.1.0",
            cache_base=tmp_path,
        )

        assert cache_dir == tmp_path / "glance" / "2024.1.0"


class TestCacheMetadataIO:
    """Tests for cache metadata read/write functions."""

    def test_write_and_read_metadata(self, tmp_path: Path) -> None:
        """Test writing and reading cache metadata."""
        cache_dir = tmp_path / "project" / "1.0"
        cache_dir.mkdir(parents=True)

        metadata = tarball_cache.CacheMetadata(
            project="project",
            version="1.0",
            extracted_at=datetime.now(timezone.utc).isoformat(),
            tarball_path="/tmp/test.tar.gz",
            tarball_size=5000,
        )

        # Write
        result = tarball_cache.write_cache_metadata(cache_dir, metadata)
        assert result is True

        # Read
        read_metadata = tarball_cache.read_cache_metadata(cache_dir)
        assert read_metadata is not None
        assert read_metadata.project == "project"
        assert read_metadata.version == "1.0"

    def test_read_nonexistent(self, tmp_path: Path) -> None:
        """Test reading from nonexistent directory."""
        result = tarball_cache.read_cache_metadata(tmp_path / "nonexistent")
        assert result is None

    def test_read_invalid_json(self, tmp_path: Path) -> None:
        """Test reading invalid JSON metadata."""
        cache_dir = tmp_path / "project" / "1.0"
        cache_dir.mkdir(parents=True)
        (cache_dir / tarball_cache.CACHE_METADATA_FILE).write_text("invalid json")

        result = tarball_cache.read_cache_metadata(cache_dir)
        assert result is None


class TestExtractTarball:
    """Tests for extract_tarball function."""

    @pytest.fixture
    def sample_tarball(self, tmp_path: Path) -> Path:
        """Create a sample tarball for testing."""
        # Create source directory
        source_dir = tmp_path / "source" / "project-1.0"
        source_dir.mkdir(parents=True)
        (source_dir / "requirements.txt").write_text("oslo.config>=1.0\n")
        (source_dir / "setup.py").write_text("# setup.py\n")

        # Create tarball
        tarball_path = tmp_path / "project-1.0.tar.gz"
        with tarfile.open(tarball_path, "w:gz") as tar:
            tar.add(source_dir, arcname="project-1.0")

        return tarball_path

    def test_extract_new_tarball(self, sample_tarball: Path, tmp_path: Path) -> None:
        """Test extracting a new tarball."""
        cache_base = tmp_path / "cache"

        result = tarball_cache.extract_tarball(
            tarball_path=sample_tarball,
            project="project",
            version="1.0",
            cache_base=cache_base,
        )

        assert result.success is True
        assert result.extraction_path is not None
        assert result.extraction_path.exists()
        assert result.from_cache is False

        # Check that requirements.txt exists in extracted path
        assert (result.extraction_path / "requirements.txt").exists()

    def test_extract_uses_cache(self, sample_tarball: Path, tmp_path: Path) -> None:
        """Test that second extraction uses cache."""
        cache_base = tmp_path / "cache"

        # First extraction
        result1 = tarball_cache.extract_tarball(
            tarball_path=sample_tarball,
            project="project",
            version="1.0",
            cache_base=cache_base,
        )
        assert result1.success is True
        assert result1.from_cache is False

        # Second extraction
        result2 = tarball_cache.extract_tarball(
            tarball_path=sample_tarball,
            project="project",
            version="1.0",
            cache_base=cache_base,
        )
        assert result2.success is True
        assert result2.from_cache is True
        assert result2.extraction_path == result1.extraction_path

    def test_extract_force_recache(self, sample_tarball: Path, tmp_path: Path) -> None:
        """Test that force=True re-extracts."""
        cache_base = tmp_path / "cache"

        # First extraction
        result1 = tarball_cache.extract_tarball(
            tarball_path=sample_tarball,
            project="project",
            version="1.0",
            cache_base=cache_base,
        )
        assert result1.success is True

        # Second extraction with force
        result2 = tarball_cache.extract_tarball(
            tarball_path=sample_tarball,
            project="project",
            version="1.0",
            cache_base=cache_base,
            force=True,
        )
        assert result2.success is True
        assert result2.from_cache is False

    def test_extract_nonexistent_tarball(self, tmp_path: Path) -> None:
        """Test extracting nonexistent tarball."""
        result = tarball_cache.extract_tarball(
            tarball_path=tmp_path / "nonexistent.tar.gz",
            project="project",
            version="1.0",
            cache_base=tmp_path / "cache",
        )

        assert result.success is False
        assert "not found" in result.error.lower()


class TestCleanupExpiredCache:
    """Tests for cleanup_expired_cache function."""

    def test_cleanup_removes_expired(self, tmp_path: Path) -> None:
        """Test that expired entries are removed."""
        # Create an expired entry
        expired_dir = tmp_path / "project" / "old"
        expired_dir.mkdir(parents=True)
        old_time = datetime.now(timezone.utc) - timedelta(days=30)
        metadata = tarball_cache.CacheMetadata(
            project="project",
            version="old",
            extracted_at=old_time.isoformat(),
            tarball_path="/tmp/old.tar.gz",
            tarball_size=1000,
        )
        tarball_cache.write_cache_metadata(expired_dir, metadata)

        # Create a fresh entry
        fresh_dir = tmp_path / "project" / "new"
        fresh_dir.mkdir(parents=True)
        fresh_metadata = tarball_cache.CacheMetadata(
            project="project",
            version="new",
            extracted_at=datetime.now(timezone.utc).isoformat(),
            tarball_path="/tmp/new.tar.gz",
            tarball_size=1000,
        )
        tarball_cache.write_cache_metadata(fresh_dir, fresh_metadata)

        # Run cleanup
        removed = tarball_cache.cleanup_expired_cache(tmp_path, max_age_days=14)

        assert len(removed) == 1
        assert expired_dir in removed
        assert not expired_dir.exists()
        assert fresh_dir.exists()


class TestGetCacheSize:
    """Tests for get_cache_size function."""

    def test_empty_cache(self, tmp_path: Path) -> None:
        """Test size of empty/nonexistent cache."""
        size = tarball_cache.get_cache_size(tmp_path / "nonexistent")
        assert size == 0

    def test_cache_with_files(self, tmp_path: Path) -> None:
        """Test size calculation with files."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "file1.txt").write_text("hello")  # 5 bytes
        (cache_dir / "file2.txt").write_text("world!")  # 6 bytes

        size = tarball_cache.get_cache_size(tmp_path)
        assert size == 11


class TestListCachedProjects:
    """Tests for list_cached_projects function."""

    def test_empty_cache(self, tmp_path: Path) -> None:
        """Test listing empty cache."""
        result = tarball_cache.list_cached_projects(tmp_path / "nonexistent")
        assert result == []

    def test_list_projects(self, tmp_path: Path) -> None:
        """Test listing cached projects."""
        # Create some entries
        for project, version in [("glance", "1.0"), ("nova", "2.0")]:
            cache_dir = tmp_path / project / version
            cache_dir.mkdir(parents=True)
            metadata = tarball_cache.CacheMetadata(
                project=project,
                version=version,
                extracted_at=datetime.now(timezone.utc).isoformat(),
                tarball_path=f"/tmp/{project}.tar.gz",
                tarball_size=1000,
            )
            tarball_cache.write_cache_metadata(cache_dir, metadata)

        result = tarball_cache.list_cached_projects(tmp_path)

        assert len(result) == 2
        projects = {(p, v) for p, v, _ in result}
        assert ("glance", "1.0") in projects
        assert ("nova", "2.0") in projects
