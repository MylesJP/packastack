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

"""Tests for packastack.apt.localrepo module."""

from __future__ import annotations

import gzip
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from packastack.apt import localrepo


class TestDebPackageInfo:
    """Tests for DebPackageInfo dataclass."""

    def test_basic_info(self) -> None:
        """Test basic package info creation."""
        info = localrepo.DebPackageInfo(
            package="python3-nova",
            version="29.0.0-0ubuntu1",
            architecture="all",
        )
        assert info.package == "python3-nova"
        assert info.version == "29.0.0-0ubuntu1"
        assert info.architecture == "all"
        assert info.depends == ""
        assert info.size == 0
        assert info.md5sum == ""

    def test_full_info(self) -> None:
        """Test package info with all fields."""
        info = localrepo.DebPackageInfo(
            package="nova-api",
            version="29.0.0-0ubuntu1",
            architecture="amd64",
            source="nova",
            depends="python3-nova (= 29.0.0-0ubuntu1)",
            pre_depends="adduser",
            provides="nova-api-server",
            description="Nova API server",
            maintainer="Ubuntu OpenStack <openstack@ubuntu.com>",
            section="net",
            priority="optional",
            installed_size=1024,
            filename="pool/main/nova-api_29.0.0-0ubuntu1_amd64.deb",
            size=512000,
            md5sum="d41d8cd98f00b204e9800998ecf8427e",
            sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        assert info.source == "nova"
        assert info.depends == "python3-nova (= 29.0.0-0ubuntu1)"
        assert info.installed_size == 1024


class TestPublishResult:
    """Tests for PublishResult dataclass."""

    def test_success_result(self, tmp_path: Path) -> None:
        """Test successful publish result."""
        result = localrepo.PublishResult(
            success=True,
            published_paths=[tmp_path / "test.deb"],
        )
        assert result.success is True
        assert len(result.published_paths) == 1
        assert result.error == ""

    def test_failure_result(self) -> None:
        """Test failed publish result."""
        result = localrepo.PublishResult(
            success=False,
            error="Permission denied",
        )
        assert result.success is False
        assert result.error == "Permission denied"


class TestIndexResult:
    """Tests for IndexResult dataclass."""

    def test_success_result(self, tmp_path: Path) -> None:
        """Test successful index result."""
        result = localrepo.IndexResult(
            success=True,
            packages_file=tmp_path / "Packages",
            packages_gz_file=tmp_path / "Packages.gz",
            package_count=5,
        )
        assert result.success is True
        assert result.package_count == 5


class TestComputeFileHashes:
    """Tests for compute_file_hashes function."""

    def test_compute_hashes(self, tmp_path: Path) -> None:
        """Test computing MD5 and SHA256 hashes."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        md5, sha256 = localrepo.compute_file_hashes(test_file)

        # Known hashes for "Hello, World!"
        assert md5 == "65a8e27d8879283831b664bd8b7f0ad4"
        assert sha256 == "dffd6021bb2bd5b0af676290809ec3a53191dd81c7f70a4b28688a362182986f"

    def test_empty_file(self, tmp_path: Path) -> None:
        """Test hashing empty file."""
        test_file = tmp_path / "empty.txt"
        test_file.write_text("")

        md5, sha256 = localrepo.compute_file_hashes(test_file)

        # Known hashes for empty content
        assert md5 == "d41d8cd98f00b204e9800998ecf8427e"
        assert sha256 == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class TestExtractDebControl:
    """Tests for extract_deb_control function."""

    def test_missing_dpkg_deb(self, tmp_path: Path) -> None:
        """Test when dpkg-deb is not available."""
        test_file = tmp_path / "test.deb"
        test_file.write_bytes(b"not a real deb")

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("dpkg-deb not found")
            result = localrepo.extract_deb_control(test_file)

        assert result is None

    def test_successful_extraction(self, tmp_path: Path) -> None:
        """Test successful control file extraction."""
        test_file = tmp_path / "test.deb"
        test_file.write_bytes(b"deb data")

        control_content = """Package: python3-nova
Version: 29.0.0-0ubuntu1
Architecture: all
Maintainer: Ubuntu OpenStack <openstack@ubuntu.com>
Source: nova
Section: python
Priority: optional
Installed-Size: 5000
Depends: python3-oslo.config (>= 8.0.0)
Description: Nova common Python libraries
 This package contains shared Python code for Nova.
"""

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=control_content,
            )
            result = localrepo.extract_deb_control(test_file)

        assert result is not None
        assert result.package == "python3-nova"
        assert result.version == "29.0.0-0ubuntu1"
        assert result.architecture == "all"
        assert result.source == "nova"
        assert result.section == "python"
        assert result.installed_size == 5000
        assert "python3-oslo.config" in result.depends

    def test_failed_extraction(self, tmp_path: Path) -> None:
        """Test failed dpkg-deb command."""
        test_file = tmp_path / "bad.deb"
        test_file.write_bytes(b"invalid")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="not a deb archive",
            )
            result = localrepo.extract_deb_control(test_file)

        assert result is None

    def test_missing_required_fields(self, tmp_path: Path) -> None:
        """Test extraction with missing required fields."""
        test_file = tmp_path / "incomplete.deb"
        test_file.write_bytes(b"deb data")

        # Missing Architecture field
        control_content = """Package: test
Version: 1.0
"""

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=control_content,
            )
            result = localrepo.extract_deb_control(test_file)

        assert result is None


class TestFormatPackagesEntry:
    """Tests for format_packages_entry function."""

    def test_minimal_entry(self) -> None:
        """Test formatting minimal package entry."""
        info = localrepo.DebPackageInfo(
            package="test-pkg",
            version="1.0.0",
            architecture="amd64",
            filename="pool/main/test-pkg_1.0.0_amd64.deb",
            size=1024,
            md5sum="abc123",
            sha256="def456",
        )

        entry = localrepo.format_packages_entry(info)

        assert "Package: test-pkg" in entry
        assert "Version: 1.0.0" in entry
        assert "Architecture: amd64" in entry
        assert "Filename: pool/main/test-pkg_1.0.0_amd64.deb" in entry
        assert "Size: 1024" in entry
        assert "MD5sum: abc123" in entry
        assert "SHA256: def456" in entry

    def test_full_entry(self) -> None:
        """Test formatting full package entry."""
        info = localrepo.DebPackageInfo(
            package="python3-nova",
            version="29.0.0-0ubuntu1",
            architecture="all",
            source="nova",
            depends="python3 (>= 3.10)",
            pre_depends="adduser",
            provides="nova-common",
            maintainer="Ubuntu OpenStack",
            section="python",
            priority="optional",
            installed_size=5000,
            description="Nova Python libraries\nThis is the description.",
            filename="pool/main/python3-nova_29.0.0-0ubuntu1_all.deb",
            size=1000000,
            md5sum="abc123",
            sha256="def456",
        )

        entry = localrepo.format_packages_entry(info)

        assert "Source: nova" in entry
        assert "Maintainer: Ubuntu OpenStack" in entry
        assert "Section: python" in entry
        assert "Priority: optional" in entry
        assert "Installed-Size: 5000" in entry
        assert "Depends: python3 (>= 3.10)" in entry
        assert "Pre-Depends: adduser" in entry
        assert "Provides: nova-common" in entry
        assert "Description: Nova Python libraries" in entry


class TestPublishArtifacts:
    """Tests for publish_artifacts function."""

    def test_publish_deb_files(self, tmp_path: Path) -> None:
        """Test publishing .deb files."""
        repo_root = tmp_path / "repo"
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()

        # Create fake .deb files
        deb1 = artifacts_dir / "test_1.0_amd64.deb"
        deb2 = artifacts_dir / "test-doc_1.0_all.deb"
        deb1.write_bytes(b"deb content 1")
        deb2.write_bytes(b"deb content 2")

        result = localrepo.publish_artifacts(
            artifact_paths=[deb1, deb2],
            repo_root=repo_root,
            arch="amd64",
        )

        assert result.success is True
        assert len(result.published_paths) == 2
        assert (repo_root / "pool" / "main" / "test_1.0_amd64.deb").exists()
        assert (repo_root / "pool" / "main" / "test-doc_1.0_all.deb").exists()

    def test_publish_mixed_artifacts(self, tmp_path: Path) -> None:
        """Test publishing mixed artifact types."""
        repo_root = tmp_path / "repo"
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()

        # Create various artifact types
        deb = artifacts_dir / "test_1.0_amd64.deb"
        dsc = artifacts_dir / "test_1.0.dsc"
        changes = artifacts_dir / "test_1.0_amd64.changes"
        tar = artifacts_dir / "test_1.0.orig.tar.gz"

        for f in [deb, dsc, changes, tar]:
            f.write_bytes(b"content")

        result = localrepo.publish_artifacts(
            artifact_paths=[deb, dsc, changes, tar],
            repo_root=repo_root,
        )

        assert result.success is True
        assert len(result.published_paths) == 4
        pool_main = repo_root / "pool" / "main"
        assert (pool_main / "test_1.0_amd64.deb").exists()
        assert (pool_main / "test_1.0.dsc").exists()
        assert (pool_main / "test_1.0_amd64.changes").exists()
        assert (pool_main / "test_1.0.orig.tar.gz").exists()

    def test_skip_nonexistent_files(self, tmp_path: Path) -> None:
        """Test that nonexistent files are skipped."""
        repo_root = tmp_path / "repo"

        result = localrepo.publish_artifacts(
            artifact_paths=[tmp_path / "nonexistent.deb"],
            repo_root=repo_root,
        )

        assert result.success is True
        assert len(result.published_paths) == 0


class TestRegenerateIndexes:
    """Tests for regenerate_indexes function."""

    def test_empty_repo(self, tmp_path: Path) -> None:
        """Test regenerating indexes for empty repo."""
        repo_root = tmp_path / "repo"

        result = localrepo.regenerate_indexes(repo_root, arch="amd64")

        assert result.success is True
        assert result.package_count == 0
        assert result.packages_file is not None
        assert result.packages_file.exists()
        assert result.packages_gz_file is not None
        assert result.packages_gz_file.exists()

    def test_with_packages(self, tmp_path: Path) -> None:
        """Test regenerating indexes with packages."""
        repo_root = tmp_path / "repo"
        pool_dir = repo_root / "pool" / "main"
        pool_dir.mkdir(parents=True)

        # Create a fake .deb file
        deb_file = pool_dir / "test_1.0_amd64.deb"
        deb_file.write_bytes(b"fake deb content")

        control_content = """Package: test
Version: 1.0
Architecture: amd64
Description: Test package
"""

        with patch.object(localrepo, "extract_deb_control") as mock_extract:
            mock_extract.return_value = localrepo.DebPackageInfo(
                package="test",
                version="1.0",
                architecture="amd64",
                description="Test package",
            )

            result = localrepo.regenerate_indexes(repo_root, arch="amd64")

        assert result.success is True
        assert result.package_count == 1

        # Check Packages file content
        packages_content = result.packages_file.read_text()
        assert "Package: test" in packages_content
        assert "Version: 1.0" in packages_content

        # Check Packages.gz is valid gzip
        with gzip.open(result.packages_gz_file, "rt") as f:
            gz_content = f.read()
        assert gz_content == packages_content

    def test_arch_filtering(self, tmp_path: Path) -> None:
        """Test that packages are filtered by architecture."""
        repo_root = tmp_path / "repo"
        pool_dir = repo_root / "pool" / "main"
        pool_dir.mkdir(parents=True)

        # Create fake .deb files
        amd64_deb = pool_dir / "test_1.0_amd64.deb"
        arm64_deb = pool_dir / "test_1.0_arm64.deb"
        all_deb = pool_dir / "test-doc_1.0_all.deb"
        for f in [amd64_deb, arm64_deb, all_deb]:
            f.write_bytes(b"deb")

        # Mock extract_deb_control to return appropriate architectures
        def mock_extract(path: Path):
            if "amd64" in path.name:
                return localrepo.DebPackageInfo(
                    package="test", version="1.0", architecture="amd64"
                )
            elif "arm64" in path.name:
                return localrepo.DebPackageInfo(
                    package="test", version="1.0", architecture="arm64"
                )
            elif "all" in path.name:
                return localrepo.DebPackageInfo(
                    package="test-doc", version="1.0", architecture="all"
                )
            return None

        with patch.object(localrepo, "extract_deb_control", side_effect=mock_extract):
            result = localrepo.regenerate_indexes(repo_root, arch="amd64")

        # Should have amd64 and all packages, not arm64
        assert result.success is True
        assert result.package_count == 2


class TestGetAvailableVersions:
    """Tests for get_available_versions function."""

    def test_no_versions(self, tmp_path: Path) -> None:
        """Test when no versions exist."""
        repo_root = tmp_path / "repo"
        versions = localrepo.get_available_versions(repo_root, "nonexistent")
        assert versions == []

    def test_multiple_versions(self, tmp_path: Path) -> None:
        """Test getting multiple versions."""
        repo_root = tmp_path / "repo"
        dists_dir = repo_root / "dists" / "local" / "main" / "binary-amd64"
        dists_dir.mkdir(parents=True)

        packages_content = """Package: nova
Version: 29.0.0-0ubuntu1
Architecture: all

Package: nova
Version: 29.0.1-0ubuntu1
Architecture: all

Package: keystone
Version: 25.0.0-0ubuntu1
Architecture: all
"""
        (dists_dir / "Packages").write_text(packages_content)

        versions = localrepo.get_available_versions(repo_root, "nova")

        assert len(versions) == 2
        # Should be sorted newest first
        assert versions[0] == "29.0.1-0ubuntu1"
        assert versions[1] == "29.0.0-0ubuntu1"

    def test_multiple_archs(self, tmp_path: Path) -> None:
        """Test getting versions from multiple arch directories."""
        repo_root = tmp_path / "repo"
        
        for arch in ["amd64", "arm64"]:
            dists_dir = repo_root / "dists" / "local" / "main" / f"binary-{arch}"
            dists_dir.mkdir(parents=True)
            packages_content = f"""Package: test
Version: 1.0-{arch}
Architecture: {arch}

"""
            (dists_dir / "Packages").write_text(packages_content)

        versions = localrepo.get_available_versions(repo_root, "test")

        assert len(versions) == 2
        assert "1.0-amd64" in versions
        assert "1.0-arm64" in versions


class TestSatisfies:
    """Tests for satisfies function."""

    def test_no_constraint(self, tmp_path: Path) -> None:
        """Test that no constraint matches any version."""
        repo_root = tmp_path / "repo"
        dists_dir = repo_root / "dists" / "local" / "main" / "binary-amd64"
        dists_dir.mkdir(parents=True)
        (dists_dir / "Packages").write_text("Package: test\nVersion: 1.0\n\n")

        assert localrepo.satisfies(repo_root, "test", "") is True

    def test_no_versions(self, tmp_path: Path) -> None:
        """Test that missing package doesn't satisfy."""
        repo_root = tmp_path / "repo"
        assert localrepo.satisfies(repo_root, "missing", ">= 1.0") is False

    def test_exact_version(self, tmp_path: Path) -> None:
        """Test exact version matching."""
        repo_root = tmp_path / "repo"
        dists_dir = repo_root / "dists" / "local" / "main" / "binary-amd64"
        dists_dir.mkdir(parents=True)
        (dists_dir / "Packages").write_text("Package: test\nVersion: 1.0\n\n")

        assert localrepo.satisfies(repo_root, "test", "= 1.0") is True
        assert localrepo.satisfies(repo_root, "test", "= 2.0") is False

    def test_version_ranges(self, tmp_path: Path) -> None:
        """Test version range constraints."""
        repo_root = tmp_path / "repo"
        dists_dir = repo_root / "dists" / "local" / "main" / "binary-amd64"
        dists_dir.mkdir(parents=True)
        (dists_dir / "Packages").write_text("Package: test\nVersion: 2.0\n\n")

        assert localrepo.satisfies(repo_root, "test", ">= 1.0") is True
        assert localrepo.satisfies(repo_root, "test", ">= 2.0") is True
        assert localrepo.satisfies(repo_root, "test", ">= 3.0") is False
        assert localrepo.satisfies(repo_root, "test", "<= 2.0") is True
        assert localrepo.satisfies(repo_root, "test", "<= 1.0") is False
        assert localrepo.satisfies(repo_root, "test", ">> 1.0") is True
        assert localrepo.satisfies(repo_root, "test", ">> 2.0") is False
        assert localrepo.satisfies(repo_root, "test", "<< 3.0") is True
        assert localrepo.satisfies(repo_root, "test", "<< 2.0") is False


class TestGetSourceVersions:
    """Tests for get_source_versions function."""

    def test_no_dsc_files(self, tmp_path: Path) -> None:
        """Test when no .dsc files exist."""
        repo_root = tmp_path / "repo"
        versions = localrepo.get_source_versions(repo_root, "nova")
        assert versions == []

    def test_multiple_dsc_files(self, tmp_path: Path) -> None:
        """Test getting versions from .dsc files."""
        repo_root = tmp_path / "repo"
        pool_dir = repo_root / "pool" / "main"
        pool_dir.mkdir(parents=True)

        (pool_dir / "nova_29.0.0-0ubuntu1.dsc").write_text("dsc content")
        (pool_dir / "nova_29.0.1-0ubuntu1.dsc").write_text("dsc content")
        (pool_dir / "keystone_25.0.0-0ubuntu1.dsc").write_text("dsc content")

        versions = localrepo.get_source_versions(repo_root, "nova")

        assert len(versions) == 2
        assert "29.0.1-0ubuntu1" in versions
        assert "29.0.0-0ubuntu1" in versions


class TestSetField:
    """Tests for _set_field helper function."""

    def test_set_standard_fields(self) -> None:
        """Test setting standard control fields."""
        info = localrepo.DebPackageInfo(package="", version="", architecture="")
        
        localrepo._set_field(info, "Package", "test-pkg")
        localrepo._set_field(info, "Version", "1.0.0")
        localrepo._set_field(info, "Architecture", "amd64")
        localrepo._set_field(info, "Source", "test")
        localrepo._set_field(info, "Section", "python")
        localrepo._set_field(info, "Priority", "optional")
        
        assert info.package == "test-pkg"
        assert info.version == "1.0.0"
        assert info.architecture == "amd64"
        assert info.source == "test"
        assert info.section == "python"
        assert info.priority == "optional"

    def test_set_pre_depends(self) -> None:
        """Test setting Pre-Depends field with hyphen."""
        info = localrepo.DebPackageInfo(package="", version="", architecture="")
        localrepo._set_field(info, "Pre-Depends", "adduser, passwd")
        assert info.pre_depends == "adduser, passwd"

    def test_set_installed_size(self) -> None:
        """Test setting Installed-Size as integer."""
        info = localrepo.DebPackageInfo(package="", version="", architecture="")
        localrepo._set_field(info, "Installed-Size", "5000")
        assert info.installed_size == 5000

    def test_set_installed_size_invalid(self) -> None:
        """Test setting Installed-Size with invalid value."""
        info = localrepo.DebPackageInfo(package="", version="", architecture="")
        localrepo._set_field(info, "Installed-Size", "invalid")
        assert info.installed_size == 0  # Should remain default

    def test_set_unknown_field(self) -> None:
        """Test setting unknown field does nothing."""
        info = localrepo.DebPackageInfo(package="test", version="1.0", architecture="amd64")
        localrepo._set_field(info, "Unknown-Field", "some value")
        # Should not raise and info should remain unchanged
        assert info.package == "test"


class TestExtractDebControlEdgeCases:
    """Additional edge case tests for extract_deb_control."""

    def test_timeout_handling(self, tmp_path: Path) -> None:
        """Test handling of subprocess timeout."""
        test_file = tmp_path / "test.deb"
        test_file.write_bytes(b"deb data")

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("dpkg-deb", 30)
            result = localrepo.extract_deb_control(test_file)

        assert result is None

    def test_multiline_description(self, tmp_path: Path) -> None:
        """Test parsing multi-line description field."""
        test_file = tmp_path / "test.deb"
        test_file.write_bytes(b"deb data")

        control_content = """Package: python3-nova
Version: 29.0.0
Architecture: all
Description: Nova common Python libraries
 This is a longer description that spans
 multiple lines and should be preserved
 properly in the output.
"""

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=control_content,
            )
            result = localrepo.extract_deb_control(test_file)

        assert result is not None
        assert "Nova common Python libraries" in result.description
        assert "multiple lines" in result.description

    def test_continuation_lines(self, tmp_path: Path) -> None:
        """Test handling of continuation lines (tab-indented)."""
        test_file = tmp_path / "test.deb"
        test_file.write_bytes(b"deb data")

        control_content = """Package: test
Version: 1.0
Architecture: amd64
Depends: dep1,
\tdep2,
\tdep3
"""

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=control_content,
            )
            result = localrepo.extract_deb_control(test_file)

        assert result is not None
        assert "dep1" in result.depends


class TestPublishArtifactsEdgeCases:
    """Additional edge case tests for publish_artifacts."""

    def test_publish_ddeb_files(self, tmp_path: Path) -> None:
        """Test publishing .ddeb debug symbol files."""
        repo_root = tmp_path / "repo"
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()

        ddeb = artifacts_dir / "test-dbgsym_1.0_amd64.ddeb"
        ddeb.write_bytes(b"ddeb content")

        result = localrepo.publish_artifacts(
            artifact_paths=[ddeb],
            repo_root=repo_root,
        )

        assert result.success is True
        assert len(result.published_paths) == 1
        assert (repo_root / "pool" / "main" / "test-dbgsym_1.0_amd64.ddeb").exists()

    def test_publish_buildinfo_files(self, tmp_path: Path) -> None:
        """Test publishing .buildinfo files."""
        repo_root = tmp_path / "repo"
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()

        buildinfo = artifacts_dir / "test_1.0_amd64.buildinfo"
        buildinfo.write_text("buildinfo content")

        result = localrepo.publish_artifacts(
            artifact_paths=[buildinfo],
            repo_root=repo_root,
        )

        assert result.success is True
        assert (repo_root / "pool" / "main" / "test_1.0_amd64.buildinfo").exists()

    def test_publish_overwrites_existing(self, tmp_path: Path) -> None:
        """Test that publishing overwrites existing files."""
        repo_root = tmp_path / "repo"
        pool_main = repo_root / "pool" / "main"
        pool_main.mkdir(parents=True)
        
        # Create existing file with old content
        existing = pool_main / "test_1.0_amd64.deb"
        existing.write_bytes(b"old content")
        
        # Create new artifact
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        new_deb = artifacts_dir / "test_1.0_amd64.deb"
        new_deb.write_bytes(b"new content")

        result = localrepo.publish_artifacts(
            artifact_paths=[new_deb],
            repo_root=repo_root,
        )

        assert result.success is True
        assert existing.read_bytes() == b"new content"

    def test_publish_permission_error(self, tmp_path: Path) -> None:
        """Test handling of permission errors during publish."""
        repo_root = tmp_path / "repo"
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        
        deb = artifacts_dir / "test_1.0_amd64.deb"
        deb.write_bytes(b"deb content")

        with patch("shutil.copy2") as mock_copy:
            mock_copy.side_effect = PermissionError("Access denied")
            result = localrepo.publish_artifacts(
                artifact_paths=[deb],
                repo_root=repo_root,
            )

        assert result.success is False
        assert "Access denied" in result.error


class TestRegenerateIndexesEdgeCases:
    """Additional edge case tests for regenerate_indexes."""

    def test_nested_pool_structure(self, tmp_path: Path) -> None:
        """Test with nested pool structure (pool/main/n/nova/)."""
        repo_root = tmp_path / "repo"
        nested_dir = repo_root / "pool" / "main" / "n" / "nova"
        nested_dir.mkdir(parents=True)

        deb_file = nested_dir / "nova_29.0.0_all.deb"
        deb_file.write_bytes(b"deb content")

        with patch.object(localrepo, "extract_deb_control") as mock_extract:
            mock_extract.return_value = localrepo.DebPackageInfo(
                package="nova",
                version="29.0.0",
                architecture="all",
            )

            result = localrepo.regenerate_indexes(repo_root, arch="amd64")

        assert result.success is True
        assert result.package_count == 1

    def test_mixed_architectures_in_pool(self, tmp_path: Path) -> None:
        """Test filtering with multiple architectures in pool."""
        repo_root = tmp_path / "repo"
        pool_dir = repo_root / "pool" / "main"
        pool_dir.mkdir(parents=True)

        # Create packages for different architectures
        for arch in ["amd64", "arm64", "all"]:
            deb = pool_dir / f"test_1.0_{arch}.deb"
            deb.write_bytes(b"deb")

        def mock_extract(path: Path):
            if "_amd64" in path.name:
                return localrepo.DebPackageInfo(package="test", version="1.0", architecture="amd64")
            elif "_arm64" in path.name:
                return localrepo.DebPackageInfo(package="test", version="1.0", architecture="arm64")
            elif "_all" in path.name:
                return localrepo.DebPackageInfo(package="test", version="1.0", architecture="all")
            return None

        with patch.object(localrepo, "extract_deb_control", side_effect=mock_extract):
            # Generate for amd64 - should include amd64 and all
            result = localrepo.regenerate_indexes(repo_root, arch="amd64")

        assert result.success is True
        assert result.package_count == 2  # amd64 + all

    def test_extract_failure_skips_package(self, tmp_path: Path) -> None:
        """Test that extraction failure skips the package."""
        repo_root = tmp_path / "repo"
        pool_dir = repo_root / "pool" / "main"
        pool_dir.mkdir(parents=True)

        (pool_dir / "good_1.0_amd64.deb").write_bytes(b"deb")
        (pool_dir / "bad_1.0_amd64.deb").write_bytes(b"corrupt")

        def mock_extract(path: Path):
            if "good" in path.name:
                return localrepo.DebPackageInfo(package="good", version="1.0", architecture="amd64")
            return None  # Extraction failed

        with patch.object(localrepo, "extract_deb_control", side_effect=mock_extract):
            result = localrepo.regenerate_indexes(repo_root, arch="amd64")

        assert result.success is True
        assert result.package_count == 1


class TestSatisfiesEdgeCases:
    """Additional edge case tests for satisfies function."""

    def test_implicit_exact_match(self, tmp_path: Path) -> None:
        """Test constraint without relation operator (implicit exact)."""
        repo_root = tmp_path / "repo"
        dists_dir = repo_root / "dists" / "local" / "main" / "binary-amd64"
        dists_dir.mkdir(parents=True)
        (dists_dir / "Packages").write_text("Package: test\nVersion: 1.0\n\n")

        # Plain version string should match exactly
        assert localrepo.satisfies(repo_root, "test", "1.0") is True
        assert localrepo.satisfies(repo_root, "test", "2.0") is False

    def test_ubuntu_version_comparison(self, tmp_path: Path) -> None:
        """Test with Ubuntu-style version strings."""
        repo_root = tmp_path / "repo"
        dists_dir = repo_root / "dists" / "local" / "main" / "binary-amd64"
        dists_dir.mkdir(parents=True)
        (dists_dir / "Packages").write_text("Package: test\nVersion: 29.0.0-0ubuntu1\n\n")

        assert localrepo.satisfies(repo_root, "test", ">= 29.0.0") is True
        assert localrepo.satisfies(repo_root, "test", ">= 29.0.0-0ubuntu1") is True
        assert localrepo.satisfies(repo_root, "test", ">= 29.0.0-0ubuntu2") is False

    def test_epoch_version_comparison(self, tmp_path: Path) -> None:
        """Test with epoch in version string."""
        repo_root = tmp_path / "repo"
        dists_dir = repo_root / "dists" / "local" / "main" / "binary-amd64"
        dists_dir.mkdir(parents=True)
        (dists_dir / "Packages").write_text("Package: test\nVersion: 2:1.0.0-1\n\n")

        # Epoch 2: is higher than epoch 1:
        assert localrepo.satisfies(repo_root, "test", ">= 1:5.0.0") is True
        assert localrepo.satisfies(repo_root, "test", ">= 2:0.5.0") is True
        assert localrepo.satisfies(repo_root, "test", ">= 3:0.1.0") is False


class TestFormatPackagesEntryEdgeCases:
    """Additional edge case tests for format_packages_entry."""

    def test_empty_description(self) -> None:
        """Test formatting with empty description."""
        info = localrepo.DebPackageInfo(
            package="test",
            version="1.0",
            architecture="amd64",
            filename="pool/main/test_1.0_amd64.deb",
            size=1024,
            md5sum="abc",
            sha256="def",
            description="",
        )

        entry = localrepo.format_packages_entry(info)

        assert "Package: test" in entry
        assert "Description:" not in entry  # Empty description should be omitted

    def test_description_with_empty_lines(self) -> None:
        """Test formatting description with blank lines."""
        info = localrepo.DebPackageInfo(
            package="test",
            version="1.0",
            architecture="amd64",
            filename="pool/main/test_1.0_amd64.deb",
            size=1024,
            md5sum="abc",
            sha256="def",
            description="Short desc\n\nLong description after blank.",
        )

        entry = localrepo.format_packages_entry(info)

        assert "Description: Short desc" in entry
        assert " ." in entry  # Blank line represented as " ."


class TestGetSourceVersions:
    """Test get_source_versions function."""

    def test_get_source_versions_found(self, tmp_path: Path) -> None:
        """Test getting source versions from .dsc files."""
        pool_dir = tmp_path / "pool" / "main"
        pool_dir.mkdir(parents=True)

        # Create mock .dsc files
        (pool_dir / "nova_29.0.0-0ubuntu1.dsc").touch()
        (pool_dir / "nova_28.0.0-0ubuntu1.dsc").touch()
        (pool_dir / "nova_27.0.0-0ubuntu1.dsc").touch()

        versions = localrepo.get_source_versions(tmp_path, "nova")

        assert len(versions) == 3
        assert "29.0.0-0ubuntu1" in versions
        assert "28.0.0-0ubuntu1" in versions
        assert "27.0.0-0ubuntu1" in versions
        # Should be sorted newest to oldest
        assert versions[0] == "29.0.0-0ubuntu1"

    def test_get_source_versions_not_found(self, tmp_path: Path) -> None:
        """Test no versions found for non-existent package."""
        pool_dir = tmp_path / "pool" / "main"
        pool_dir.mkdir(parents=True)

        versions = localrepo.get_source_versions(tmp_path, "nonexistent")

        assert versions == []

    def test_get_source_versions_no_pool(self, tmp_path: Path) -> None:
        """Test when pool directory doesn't exist."""
        versions = localrepo.get_source_versions(tmp_path, "nova")

        assert versions == []

    def test_get_source_versions_underscore_in_version(self, tmp_path: Path) -> None:
        """Test handling of filenames with unusual patterns."""
        pool_dir = tmp_path / "pool" / "main"
        pool_dir.mkdir(parents=True)

        # Files without proper version format
        (pool_dir / "badname.dsc").touch()

        versions = localrepo.get_source_versions(tmp_path, "badname")

        # Should return empty because no underscore separator
        assert versions == []

    def test_get_source_versions_with_epoch(self, tmp_path: Path) -> None:
        """Test getting source versions with epoch."""
        pool_dir = tmp_path / "pool" / "main"
        pool_dir.mkdir(parents=True)

        # Epoch is URL-encoded in filename as %3a
        (pool_dir / "nova_1%3a29.0.0-0ubuntu1.dsc").touch()
        (pool_dir / "nova_29.0.0-0ubuntu1.dsc").touch()

        versions = localrepo.get_source_versions(tmp_path, "nova")

        assert len(versions) == 2


class TestSatisfiesEdgeCases:
    """Additional edge cases for satisfies function."""

    def test_satisfies_greater_than_strict(self, tmp_path: Path) -> None:
        """Test >> (strictly greater) relation."""
        # Create Packages file
        dists_dir = tmp_path / "dists" / "local" / "main" / "binary-amd64"
        dists_dir.mkdir(parents=True)

        packages_content = """Package: libtest
Version: 2.0.0
Architecture: amd64

"""
        (dists_dir / "Packages").write_text(packages_content)

        # 2.0.0 >> 1.0.0 should be true (we have 2.0.0, which is > 1.0.0)
        assert localrepo.satisfies(tmp_path, "libtest", ">> 1.0.0") is True
        # 2.0.0 >> 2.0.0 should be false
        assert localrepo.satisfies(tmp_path, "libtest", ">> 2.0.0") is False
        # 2.0.0 >> 3.0.0 should be false
        assert localrepo.satisfies(tmp_path, "libtest", ">> 3.0.0") is False

    def test_satisfies_less_than_strict(self, tmp_path: Path) -> None:
        """Test << (strictly less) relation."""
        dists_dir = tmp_path / "dists" / "local" / "main" / "binary-amd64"
        dists_dir.mkdir(parents=True)

        packages_content = """Package: libtest
Version: 1.0.0
Architecture: amd64

"""
        (dists_dir / "Packages").write_text(packages_content)

        # 1.0.0 << 2.0.0 should be true
        assert localrepo.satisfies(tmp_path, "libtest", "<< 2.0.0") is True
        # 1.0.0 << 1.0.0 should be false
        assert localrepo.satisfies(tmp_path, "libtest", "<< 1.0.0") is False

    def test_satisfies_less_equal(self, tmp_path: Path) -> None:
        """Test <= relation."""
        dists_dir = tmp_path / "dists" / "local" / "main" / "binary-amd64"
        dists_dir.mkdir(parents=True)

        packages_content = """Package: libtest
Version: 1.0.0
Architecture: amd64

"""
        (dists_dir / "Packages").write_text(packages_content)

        assert localrepo.satisfies(tmp_path, "libtest", "<= 2.0.0") is True
        assert localrepo.satisfies(tmp_path, "libtest", "<= 1.0.0") is True
        assert localrepo.satisfies(tmp_path, "libtest", "<= 0.5.0") is False

    def test_satisfies_exact_match(self, tmp_path: Path) -> None:
        """Test = (exact) relation."""
        dists_dir = tmp_path / "dists" / "local" / "main" / "binary-amd64"
        dists_dir.mkdir(parents=True)

        packages_content = """Package: libtest
Version: 1.5.0-1ubuntu1
Architecture: amd64

"""
        (dists_dir / "Packages").write_text(packages_content)

        assert localrepo.satisfies(tmp_path, "libtest", "= 1.5.0-1ubuntu1") is True
        assert localrepo.satisfies(tmp_path, "libtest", "= 1.5.0") is False
        assert localrepo.satisfies(tmp_path, "libtest", "= 1.5.0-1ubuntu2") is False

    def test_satisfies_no_relation(self, tmp_path: Path) -> None:
        """Test constraint with no relation (exact match assumed)."""
        dists_dir = tmp_path / "dists" / "local" / "main" / "binary-amd64"
        dists_dir.mkdir(parents=True)

        packages_content = """Package: libtest
Version: 1.0.0
Architecture: amd64

"""
        (dists_dir / "Packages").write_text(packages_content)

        # Just version string, should do exact match
        assert localrepo.satisfies(tmp_path, "libtest", "1.0.0") is True
        assert localrepo.satisfies(tmp_path, "libtest", "2.0.0") is False

    def test_satisfies_empty_constraint(self, tmp_path: Path) -> None:
        """Test with empty constraint (any version satisfies)."""
        dists_dir = tmp_path / "dists" / "local" / "main" / "binary-amd64"
        dists_dir.mkdir(parents=True)

        packages_content = """Package: libtest
Version: 1.0.0
Architecture: amd64

"""
        (dists_dir / "Packages").write_text(packages_content)

        assert localrepo.satisfies(tmp_path, "libtest", "") is True
        assert localrepo.satisfies(tmp_path, "libtest", "  ") is True


class TestMultiArchIndexes:
    """Test multi-architecture index handling."""

    def test_regenerate_with_multi_arch(self, tmp_path: Path) -> None:
        """Test regenerating indexes with multiple architectures."""
        pool_dir = tmp_path / "pool" / "main"
        pool_dir.mkdir(parents=True)

        # Create mock .deb files for different architectures
        # Create minimal .deb content (just touch for now, real tests mock dpkg-deb)
        amd64_deb = pool_dir / "test_1.0_amd64.deb"
        arm64_deb = pool_dir / "test_1.0_arm64.deb"
        amd64_deb.touch()
        arm64_deb.touch()

        # Mock extract_deb_control to return architecture-specific info
        original_extract = localrepo.extract_deb_control

        def mock_extract(deb_path: Path) -> localrepo.DebPackageInfo | None:
            if "amd64" in deb_path.name:
                return localrepo.DebPackageInfo(
                    package="test", version="1.0", architecture="amd64"
                )
            elif "arm64" in deb_path.name:
                return localrepo.DebPackageInfo(
                    package="test", version="1.0", architecture="arm64"
                )
            return None

        with patch.object(localrepo, "extract_deb_control", mock_extract):
            result = localrepo.regenerate_indexes(tmp_path, arch="amd64")

        # Should succeed and only include amd64 packages
        assert result.success is True


class TestEnsureRepoInitialized:
    """Tests for ensure_repo_initialized function."""

    def test_creates_empty_index_files(self, tmp_path: Path) -> None:
        """Test that ensure_repo_initialized creates all required index files."""
        result = localrepo.ensure_repo_initialized(tmp_path, arch="amd64")

        assert result is True

        # Check that the directories were created
        binary_amd64 = tmp_path / "dists" / "local" / "main" / "binary-amd64"
        binary_all = tmp_path / "dists" / "local" / "main" / "binary-all"

        assert binary_amd64.is_dir()
        assert binary_all.is_dir()

        # Check that empty Packages files exist
        assert (binary_amd64 / "Packages").exists()
        assert (binary_amd64 / "Packages.gz").exists()
        assert (binary_all / "Packages").exists()
        assert (binary_all / "Packages.gz").exists()

        # Verify Packages files are empty
        assert (binary_amd64 / "Packages").read_text() == ""
        assert (binary_all / "Packages").read_text() == ""

        # Verify Packages.gz decompresses to empty
        with gzip.open(binary_amd64 / "Packages.gz", "rt") as f:
            assert f.read() == ""
        with gzip.open(binary_all / "Packages.gz", "rt") as f:
            assert f.read() == ""

    def test_does_not_overwrite_existing_indexes(self, tmp_path: Path) -> None:
        """Test that ensure_repo_initialized doesn't overwrite existing indexes."""
        # Create a directory structure with existing content
        binary_amd64 = tmp_path / "dists" / "local" / "main" / "binary-amd64"
        binary_amd64.mkdir(parents=True)
        binary_all = tmp_path / "dists" / "local" / "main" / "binary-all"
        binary_all.mkdir(parents=True)

        packages_content = "Package: test\nVersion: 1.0\n"
        (binary_amd64 / "Packages").write_text(packages_content)
        with gzip.open(binary_amd64 / "Packages.gz", "wt") as f:
            f.write(packages_content)
        (binary_all / "Packages").write_text("")
        with gzip.open(binary_all / "Packages.gz", "wt") as f:
            f.write("")

        # Run ensure_repo_initialized
        result = localrepo.ensure_repo_initialized(tmp_path, arch="amd64")

        assert result is True  # Success even when already initialized

        # Verify content wasn't overwritten
        assert (binary_amd64 / "Packages").read_text() == packages_content

    def test_creates_arm64_indexes(self, tmp_path: Path) -> None:
        """Test that ensure_repo_initialized works with arm64 architecture."""
        result = localrepo.ensure_repo_initialized(tmp_path, arch="arm64")

        assert result is True

        # Check that the directories were created for arm64
        binary_arm64 = tmp_path / "dists" / "local" / "main" / "binary-arm64"
        binary_all = tmp_path / "dists" / "local" / "main" / "binary-all"

        assert binary_arm64.is_dir()
        assert binary_all.is_dir()
        assert (binary_arm64 / "Packages").exists()
        assert (binary_arm64 / "Packages.gz").exists()
