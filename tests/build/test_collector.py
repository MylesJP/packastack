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

"""Tests for sbuild artifact collector module."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from packastack.build.collector import (
    ArtifactReport,
    CollectedFile,
    CollectionResult,
    collect_artifacts,
    compute_sha256,
    copy_file_with_checksum,
    create_primary_log_symlink,
    find_artifacts_in_directory,
    find_logs_in_directory,
    matches_package,
)
from packastack.build.sbuildrc import CandidateDirectories


class TestComputeSha256:
    """Tests for compute_sha256 function."""

    def test_computes_correct_hash(self, tmp_path: Path) -> None:
        """Should compute correct SHA256 hash."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")
        
        result = compute_sha256(test_file)
        # Known SHA256 of "hello world"
        expected = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        assert result == expected

    def test_handles_binary_file(self, tmp_path: Path) -> None:
        """Should handle binary files."""
        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"\x00\x01\x02\x03")
        
        result = compute_sha256(test_file)
        assert len(result) == 64  # SHA256 hex is 64 chars


class TestCopyFileWithChecksum:
    """Tests for copy_file_with_checksum function."""

    def test_copies_file(self, tmp_path: Path) -> None:
        """Should copy file to destination."""
        src = tmp_path / "source" / "file.deb"
        src.parent.mkdir()
        src.write_text("package content")
        
        dest_dir = tmp_path / "dest"
        
        result = copy_file_with_checksum(src, dest_dir)
        
        assert result.copied_path.exists()
        assert result.copied_path.read_text() == "package content"

    def test_computes_checksum(self, tmp_path: Path) -> None:
        """Should compute SHA256 checksum."""
        src = tmp_path / "file.deb"
        src.write_text("content")
        
        dest_dir = tmp_path / "dest"
        
        result = copy_file_with_checksum(src, dest_dir)
        
        assert len(result.sha256) == 64
        assert result.sha256 == compute_sha256(src)

    def test_records_size(self, tmp_path: Path) -> None:
        """Should record file size."""
        src = tmp_path / "file.deb"
        src.write_text("12345")
        
        dest_dir = tmp_path / "dest"
        
        result = copy_file_with_checksum(src, dest_dir)
        
        assert result.size == 5

    def test_creates_dest_dir(self, tmp_path: Path) -> None:
        """Should create destination directory if needed."""
        src = tmp_path / "file.deb"
        src.write_text("content")
        
        dest_dir = tmp_path / "nested" / "dest"
        
        result = copy_file_with_checksum(src, dest_dir)
        
        assert dest_dir.exists()
        assert result.copied_path.exists()

    def test_handles_name_collision(self, tmp_path: Path) -> None:
        """Should handle name collision by adding timestamp."""
        src1 = tmp_path / "src1" / "file.deb"
        src2 = tmp_path / "src2" / "file.deb"
        src1.parent.mkdir()
        src2.parent.mkdir()
        src1.write_text("content1")
        src2.write_text("content2")
        
        dest_dir = tmp_path / "dest"
        
        result1 = copy_file_with_checksum(src1, dest_dir)
        result2 = copy_file_with_checksum(src2, dest_dir)
        
        # Both should exist with different names
        assert result1.copied_path.exists()
        assert result2.copied_path.exists()
        assert result1.copied_path != result2.copied_path


class TestMatchesPackage:
    """Tests for matches_package function."""

    def test_matches_exact_package_name(self) -> None:
        """Should match exact package name prefix."""
        assert matches_package("python-nova_1.0_amd64.deb", "python-nova")
        assert matches_package("python-nova_1.0_amd64.deb", "Python-Nova")  # case-insensitive

    def test_no_match_wrong_package(self) -> None:
        """Should not match wrong package name."""
        assert not matches_package("python-glance_1.0_amd64.deb", "python-nova")

    def test_matches_with_version(self) -> None:
        """Should match package name with version."""
        assert matches_package("python-nova_1.2.3-1ubuntu1_amd64.deb", "python-nova", "1.2.3-1ubuntu1")
        assert matches_package("python-nova_1.2.3-1ubuntu1_amd64.deb", "python-nova", "1.2.3")

    def test_no_match_wrong_version(self) -> None:
        """Should not match wrong version."""
        assert not matches_package("python-nova_1.0_amd64.deb", "python-nova", "2.0")

    def test_matches_without_source_package(self) -> None:
        """Should match any file if no source_package specified."""
        assert matches_package("anything.deb")
        assert matches_package("anything.deb", None)

    def test_handles_hyphen_underscore(self) -> None:
        """Should handle package names with underscores vs hyphens."""
        assert matches_package("python_nova_1.0_amd64.deb", "python-nova")

    def test_matches_python3_binary_from_python_source(self) -> None:
        """Should match python3-X binary packages from python-X source."""
        # Common pattern: python-foo source produces python3-foo binary
        assert matches_package(
            "python3-oslo.i18n_6.7.1-0ubuntu1_all.deb",
            "python-oslo.i18n",
            "6.7.1-0ubuntu1"
        )
        assert matches_package(
            "python3-nova_1.0_amd64.deb",
            "python-nova"
        )
        # Doc packages should still match (they start with source name)
        assert matches_package(
            "python-oslo.i18n-doc_6.7.1-0ubuntu1_all.deb",
            "python-oslo.i18n",
            "6.7.1-0ubuntu1"
        )

    def test_matches_by_version_alone(self) -> None:
        """Should match packages by version when version is specific."""
        # Some source packages produce binaries with different names
        # e.g., python-keystonemiddleware -> keystonemiddleware
        assert matches_package(
            "keystonemiddleware_1.2.3-1_all.deb",
            "python-keystonemiddleware",
            "1.2.3-1"
        )


class TestFindArtifactsInDirectory:
    """Tests for find_artifacts_in_directory function."""

    def test_finds_deb_files(self, tmp_path: Path) -> None:
        """Should find .deb files."""
        (tmp_path / "package1_1.0_amd64.deb").write_text("deb1")
        (tmp_path / "package2_1.0_amd64.deb").write_text("deb2")
        
        result = find_artifacts_in_directory(tmp_path)
        
        assert len(result) == 2
        assert all(p.suffix == ".deb" for p in result)

    def test_finds_udeb_files(self, tmp_path: Path) -> None:
        """Should find .udeb files."""
        (tmp_path / "installer_1.0_amd64.udeb").write_text("udeb")
        
        result = find_artifacts_in_directory(tmp_path)
        
        assert len(result) == 1
        assert result[0].suffix == ".udeb"

    def test_finds_changes_and_buildinfo(self, tmp_path: Path) -> None:
        """Should find .changes and .buildinfo files."""
        (tmp_path / "package_1.0_amd64.changes").write_text("changes")
        (tmp_path / "package_1.0_amd64.buildinfo").write_text("buildinfo")
        
        result = find_artifacts_in_directory(tmp_path)
        
        assert len(result) == 2
        suffixes = {p.suffix for p in result}
        assert ".changes" in suffixes
        assert ".buildinfo" in suffixes

    def test_filters_by_package_name(self, tmp_path: Path) -> None:
        """Should filter by source package name."""
        (tmp_path / "nova_1.0_amd64.deb").write_text("nova")
        (tmp_path / "glance_1.0_amd64.deb").write_text("glance")
        
        result = find_artifacts_in_directory(tmp_path, source_package="nova")
        
        assert len(result) == 1
        assert "nova" in result[0].name

    def test_filters_by_timestamp(self, tmp_path: Path) -> None:
        """Should filter by timestamp."""
        old_file = tmp_path / "old.deb"
        old_file.write_text("old")
        
        start_time = time.time()
        time.sleep(0.1)  # Ensure time difference
        
        new_file = tmp_path / "new.deb"
        new_file.write_text("new")
        
        result = find_artifacts_in_directory(tmp_path, start_time=start_time)
        
        assert len(result) == 1
        assert result[0].name == "new.deb"

    def test_handles_nonexistent_directory(self, tmp_path: Path) -> None:
        """Should handle non-existent directory."""
        result = find_artifacts_in_directory(tmp_path / "nonexistent")
        assert result == []

    def test_ignores_non_artifact_files(self, tmp_path: Path) -> None:
        """Should ignore non-artifact files."""
        (tmp_path / "readme.txt").write_text("readme")
        (tmp_path / "script.py").write_text("python")
        
        result = find_artifacts_in_directory(tmp_path)
        
        assert result == []


class TestFindLogsInDirectory:
    """Tests for find_logs_in_directory function."""

    def test_finds_log_files(self, tmp_path: Path) -> None:
        """Should find .log files."""
        (tmp_path / "sbuild.log").write_text("log content")
        
        result = find_logs_in_directory(tmp_path)
        
        assert len(result) == 1
        assert result[0].suffix == ".log"

    def test_finds_build_files(self, tmp_path: Path) -> None:
        """Should find .build files."""
        (tmp_path / "package_amd64.build").write_text("build log")
        
        result = find_logs_in_directory(tmp_path)
        
        assert len(result) == 1
        assert result[0].suffix == ".build"

    def test_filters_by_package_name(self, tmp_path: Path) -> None:
        """Should filter by package name."""
        (tmp_path / "nova_amd64.build").write_text("nova log")
        (tmp_path / "glance_amd64.build").write_text("glance log")
        
        result = find_logs_in_directory(tmp_path, source_package="nova")
        
        assert len(result) == 1
        assert "nova" in result[0].name


class TestCollectArtifacts:
    """Tests for collect_artifacts function."""

    def test_collects_from_user_build_dir(self, tmp_path: Path) -> None:
        """Should collect artifacts from user-configured build directory."""
        user_build_dir = tmp_path / "user_build"
        user_build_dir.mkdir()
        (user_build_dir / "package_1.0_amd64.deb").write_text("deb content")
        
        dest_dir = tmp_path / "dest"
        candidates = CandidateDirectories()
        candidates.add_build_dir(user_build_dir, "~/.sbuildrc")
        
        result = collect_artifacts(dest_dir, candidates)
        
        assert result.success
        assert result.deb_count == 1
        assert len(result.binaries) == 1

    def test_collects_logs_from_log_dir(self, tmp_path: Path) -> None:
        """Should collect logs from configured log directory."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "package_amd64.build").write_text("log content")
        
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "package_1.0_amd64.deb").write_text("deb")
        
        dest_dir = tmp_path / "dest"
        candidates = CandidateDirectories()
        candidates.add_build_dir(build_dir, "test")
        candidates.add_log_dir(log_dir, "~/.sbuildrc")
        
        result = collect_artifacts(dest_dir, candidates)
        
        assert len(result.logs) == 1

    def test_fails_when_no_binaries(self, tmp_path: Path) -> None:
        """Should fail validation when no binaries found."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        
        dest_dir = tmp_path / "dest"
        candidates = CandidateDirectories()
        candidates.add_build_dir(empty_dir, "test")
        
        result = collect_artifacts(dest_dir, candidates)
        
        assert not result.success
        assert "No binary packages" in result.validation_message

    def test_records_searched_dirs(self, tmp_path: Path) -> None:
        """Should record which directories were searched."""
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        
        dest_dir = tmp_path / "dest"
        candidates = CandidateDirectories()
        candidates.add_build_dir(dir1, "test1")
        candidates.add_build_dir(dir2, "test2")
        
        result = collect_artifacts(dest_dir, candidates)
        
        assert str(dir1) in result.searched_dirs
        assert str(dir2) in result.searched_dirs

    def test_copies_to_dest_dir(self, tmp_path: Path) -> None:
        """Should copy artifacts to destination directory."""
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "test_1.0_amd64.deb").write_text("content")
        
        dest_dir = tmp_path / "dest"
        candidates = CandidateDirectories()
        candidates.add_build_dir(build_dir, "test")
        
        result = collect_artifacts(dest_dir, candidates)
        
        assert result.success
        assert result.binaries[0].copied_path.parent == dest_dir

    def test_filters_by_package_name(self, tmp_path: Path) -> None:
        """Should filter artifacts by source package name."""
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "nova_1.0_amd64.deb").write_text("nova")
        (build_dir / "glance_1.0_amd64.deb").write_text("glance")
        
        dest_dir = tmp_path / "dest"
        candidates = CandidateDirectories()
        candidates.add_build_dir(build_dir, "test")
        
        result = collect_artifacts(dest_dir, candidates, source_package="nova")
        
        assert result.deb_count == 1
        assert "nova" in result.binaries[0].source_path.name

    def test_avoids_duplicate_artifacts(self, tmp_path: Path) -> None:
        """Should not collect the same artifact twice."""
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "test_1.0_amd64.deb").write_text("content")
        
        dest_dir = tmp_path / "dest"
        candidates = CandidateDirectories()
        # Add same directory twice (simulating config overlap)
        candidates.add_build_dir(build_dir, "source1")
        candidates.build_dirs.append(build_dir.resolve())  # Force duplicate
        
        result = collect_artifacts(dest_dir, candidates)
        
        # Should only collect once
        assert result.deb_count == 1


class TestCollectionResult:
    """Tests for CollectionResult dataclass."""

    def test_deb_count(self) -> None:
        """Should count .deb and .udeb files."""
        result = CollectionResult(success=True)
        result.binaries = [
            CollectedFile(Path("a.deb"), Path("a.deb"), "hash", 100, 0),
            CollectedFile(Path("b.udeb"), Path("b.udeb"), "hash", 100, 0),
            CollectedFile(Path("c.ddeb"), Path("c.ddeb"), "hash", 100, 0),
        ]
        
        assert result.deb_count == 2  # .deb and .udeb, not .ddeb

    def test_to_dict(self) -> None:
        """Should convert to dictionary."""
        result = CollectionResult(success=True, validation_message="OK")
        result.searched_dirs = ["/tmp/build"]
        
        d = result.to_dict()
        
        assert d["success"] is True
        assert d["validation_message"] == "OK"
        assert "/tmp/build" in d["searched_dirs"]


class TestArtifactReport:
    """Tests for ArtifactReport dataclass."""

    def test_write_json(self, tmp_path: Path) -> None:
        """Should write report to JSON file."""
        report = ArtifactReport(
            sbuild_command=["sbuild", "test.dsc"],
            sbuild_exit_code=0,
            start_timestamp="2025-01-01T00:00:00Z",
            end_timestamp="2025-01-01T00:10:00Z",
            candidate_dirs=["/tmp/build"],
            collection=CollectionResult(success=True),
            stdout_path="/tmp/stdout.log",
            stderr_path="/tmp/stderr.log",
            primary_log_path="/tmp/sbuild.log",
        )
        
        report_path = tmp_path / "report.json"
        report.write_json(report_path)
        
        assert report_path.exists()
        data = json.loads(report_path.read_text())
        assert data["sbuild_exit_code"] == 0
        assert data["sbuild_command"] == ["sbuild", "test.dsc"]


class TestCreatePrimaryLogSymlink:
    """Tests for create_primary_log_symlink function."""

    def test_creates_copy_with_stable_name(self, tmp_path: Path) -> None:
        """Should create copy with stable name if original differs."""
        log_file = tmp_path / "package_amd64.build"
        log_file.write_text("log content")
        
        logs = [CollectedFile(log_file, log_file, "hash", 100, 0)]
        
        result = create_primary_log_symlink(logs, tmp_path)
        
        assert result is not None
        assert result.name == "sbuild.log"
        assert result.read_text() == "log content"

    def test_returns_none_for_empty_logs(self, tmp_path: Path) -> None:
        """Should return None if no logs provided."""
        result = create_primary_log_symlink([], tmp_path)
        assert result is None

    def test_selects_largest_log(self, tmp_path: Path) -> None:
        """Should select largest log as primary."""
        small_log = tmp_path / "small.log"
        large_log = tmp_path / "large.log"
        small_log.write_text("x")
        large_log.write_text("x" * 1000)
        
        logs = [
            CollectedFile(small_log, small_log, "hash", 1, 0),
            CollectedFile(large_log, large_log, "hash", 1000, 0),
        ]
        
        result = create_primary_log_symlink(logs, tmp_path, "primary.log")
        
        assert result is not None
        assert result.read_text() == "x" * 1000
