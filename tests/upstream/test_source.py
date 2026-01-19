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

"""Tests for packastack.upstream.source module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from packastack.upstream import source as upstream


class TestBuildType:
    """Tests for BuildType enum."""

    def test_enum_values(self) -> None:
        """Test that all expected build types exist."""
        assert upstream.BuildType.RELEASE.value == "release"
        assert upstream.BuildType.SNAPSHOT.value == "snapshot"
        assert upstream.BuildType.MILESTONE.value == "milestone"


class TestUpstreamSource:
    """Tests for UpstreamSource dataclass."""

    def test_is_release(self) -> None:
        """Test is_release property."""
        source = upstream.UpstreamSource(
            version="29.0.0", build_type=upstream.BuildType.RELEASE
        )
        assert source.is_release is True
        assert source.is_snapshot is False
        assert source.is_milestone is False

    def test_is_snapshot(self) -> None:
        """Test is_snapshot property."""
        source = upstream.UpstreamSource(
            version="30.0.0~git20240101.abc1234",
            build_type=upstream.BuildType.SNAPSHOT,
        )
        assert source.is_release is False
        assert source.is_snapshot is True
        assert source.is_milestone is False

    def test_is_milestone(self) -> None:
        """Test is_milestone property."""
        source = upstream.UpstreamSource(
            version="30.0.0~b1",
            build_type=upstream.BuildType.MILESTONE,
            milestone="b1",
        )
        assert source.is_release is False
        assert source.is_snapshot is False
        assert source.is_milestone is True


class TestTarballResult:
    """Tests for TarballResult dataclass."""

    def test_successful_result(self, tmp_path: Path) -> None:
        """Test successful tarball result."""
        result = upstream.TarballResult(
            success=True,
            path=tmp_path / "foo.tar.gz",
            signature_verified=True,
        )
        assert result.success is True
        assert result.signature_verified is True

    def test_failed_result(self) -> None:
        """Test failed tarball result."""
        result = upstream.TarballResult(
            success=False,
            error="Download failed",
        )
        assert result.success is False
        assert result.error == "Download failed"


class TestBuildTarballUrl:
    """Tests for build_tarball_url function."""

    def test_simple_project(self) -> None:
        """Test URL for simple project name."""
        url = upstream.build_tarball_url("nova", "29.0.0")
        assert url == "https://tarballs.opendev.org/openstack/nova/nova-29.0.0.tar.gz"

    def test_dotted_project(self) -> None:
        """Test URL for dotted project name (oslo.*)."""
        url = upstream.build_tarball_url("oslo.config", "9.4.0")
        assert "oslo.config" in url
        assert "9.4.0" in url
        assert url.endswith(".tar.gz")

    def test_hyphenated_project(self) -> None:
        """Test URL for hyphenated project name (osc-lib)."""
        url = upstream.build_tarball_url("osc-lib", "4.3.0")
        # Directory path should keep hyphens: osc-lib/
        # Filename should normalize hyphens to underscores: osc_lib-4.3.0.tar.gz
        assert url == "https://tarballs.opendev.org/openstack/osc-lib/osc_lib-4.3.0.tar.gz"

    def test_python_prefixed_project(self) -> None:
        """Test URL for python-prefixed project."""
        url = upstream.build_tarball_url("python-openstackclient", "7.1.0")
        # Directory path should keep hyphens: python-openstackclient/
        # Filename should normalize hyphens to underscores: python_openstackclient-7.1.0.tar.gz
        assert url == "https://tarballs.opendev.org/openstack/python-openstackclient/python_openstackclient-7.1.0.tar.gz"

    def test_version_in_filename(self) -> None:
        """Test that version appears in filename."""
        url = upstream.build_tarball_url("neutron", "24.1.0")
        assert "neutron-24.1.0.tar.gz" in url


class TestBuildSignatureUrl:
    """Tests for build_signature_url function."""

    def test_appends_asc(self) -> None:
        """Test that .asc is appended to tarball URL."""
        tarball_url = "https://example.com/foo-1.0.tar.gz"
        sig_url = upstream.build_signature_url(tarball_url)
        assert sig_url == "https://example.com/foo-1.0.tar.gz.asc"


class TestSelectUpstreamSource:
    """Tests for select_upstream_source function."""

    def test_release_build(self, tmp_path: Path) -> None:
        """Test selecting source for release build."""
        with patch("packastack.upstream.releases.load_project_releases") as mock_load:
            mock_proj = MagicMock()
            mock_release = MagicMock()
            mock_release.version = "29.0.0"
            mock_proj.get_latest_release.return_value = mock_release
            mock_load.return_value = mock_proj

            source = upstream.select_upstream_source(
                releases_repo=tmp_path,
                series="2024.2",
                project="nova",
                build_type=upstream.BuildType.RELEASE,
            )

            assert source is not None
            assert source.version == "29.0.0"
            assert source.build_type == upstream.BuildType.RELEASE
            assert "nova-29.0.0.tar.gz" in source.tarball_url

    def test_snapshot_build(self, tmp_path: Path) -> None:
        """Test selecting source for snapshot build."""
        with patch("packastack.upstream.releases.load_project_releases") as mock_load:
            mock_proj = MagicMock()
            mock_load.return_value = mock_proj

            source = upstream.select_upstream_source(
                releases_repo=tmp_path,
                series="2024.2",
                project="nova",
                build_type=upstream.BuildType.SNAPSHOT,
                git_ref="abc1234",
            )

            assert source is not None
            assert source.build_type == upstream.BuildType.SNAPSHOT
            assert source.git_ref == "abc1234"

    def test_snapshot_default_git_ref(self, tmp_path: Path) -> None:
        """Test that snapshot defaults to HEAD if no git_ref."""
        with patch("packastack.upstream.releases.load_project_releases") as mock_load:
            mock_proj = MagicMock()
            mock_load.return_value = mock_proj

            source = upstream.select_upstream_source(
                releases_repo=tmp_path,
                series="2024.2",
                project="nova",
                build_type=upstream.BuildType.SNAPSHOT,
            )

            assert source.git_ref == "HEAD"

    def test_project_not_found(self, tmp_path: Path) -> None:
        """Test when project is not found in releases."""
        with patch("packastack.upstream.releases.load_project_releases") as mock_load:
            mock_load.return_value = None

            source = upstream.select_upstream_source(
                releases_repo=tmp_path,
                series="2024.2",
                project="nonexistent",
                build_type=upstream.BuildType.RELEASE,
            )

            assert source is None

    def test_no_latest_release(self, tmp_path: Path) -> None:
        """Test when project has no releases."""
        with patch("packastack.upstream.releases.load_project_releases") as mock_load:
            mock_proj = MagicMock()
            mock_proj.get_latest_release.return_value = None
            mock_load.return_value = mock_proj

            source = upstream.select_upstream_source(
                releases_repo=tmp_path,
                series="2024.2",
                project="nova",
                build_type=upstream.BuildType.RELEASE,
            )

            assert source is None


class TestDownloadFile:
    """Tests for download_file function."""

    def test_successful_download(self, tmp_path: Path) -> None:
        """Test successful file download."""
        import io

        # Create a real file-like object for the response
        content = b"test content"
        mock_response = io.BytesIO(content)

        # Wrap in context manager support
        class MockURLResponse:
            def __enter__(self):
                return mock_response

            def __exit__(self, *args):
                return False

        with patch.object(upstream.urllib.request, "urlopen") as mock_urlopen:
            mock_urlopen.return_value = MockURLResponse()

            dest = tmp_path / "test.tar.gz"
            success, error = upstream.download_file("http://example.com/test.tar.gz", dest)

            assert success is True
            assert error == ""
            assert dest.exists()
            assert dest.read_bytes() == content

    def test_failed_download(self, tmp_path: Path) -> None:
        """Test failed file download."""
        with patch.object(upstream.urllib.request, "urlopen") as mock_urlopen:
            mock_urlopen.side_effect = Exception("Connection failed")

            dest = tmp_path / "test.tar.gz"
            success, error = upstream.download_file("http://example.com/test.tar.gz", dest)

            assert success is False
            assert "Connection failed" in error

            assert success is False
            assert "Connection failed" in error


class TestVerifySignature:
    """Tests for verify_signature function."""

    def test_gpg_not_found(self, tmp_path: Path) -> None:
        """Test when gpg is not installed."""
        tarball = tmp_path / "test.tar.gz"
        signature = tmp_path / "test.tar.gz.asc"
        tarball.touch()
        signature.touch()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            verified, msg = upstream.verify_signature(tarball, signature)

            assert verified is False
            assert "gpg not found" in msg

    def test_verification_timeout(self, tmp_path: Path) -> None:
        """Test signature verification timeout."""
        tarball = tmp_path / "test.tar.gz"
        signature = tmp_path / "test.tar.gz.asc"
        tarball.touch()
        signature.touch()

        import subprocess as sp

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = sp.TimeoutExpired(cmd=["gpg"], timeout=60)
            verified, msg = upstream.verify_signature(tarball, signature)

            assert verified is False
            assert "timed out" in msg

    def test_successful_verification(self, tmp_path: Path) -> None:
        """Test successful signature verification."""
        tarball = tmp_path / "test.tar.gz"
        signature = tmp_path / "test.tar.gz.asc"
        tarball.touch()
        signature.touch()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            verified, _msg = upstream.verify_signature(tarball, signature)

            assert verified is True

    def test_failed_verification(self, tmp_path: Path) -> None:
        """Test failed signature verification."""
        tarball = tmp_path / "test.tar.gz"
        signature = tmp_path / "test.tar.gz.asc"
        tarball.touch()
        signature.touch()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="Bad signature")
            verified, msg = upstream.verify_signature(tarball, signature)

            assert verified is False
            assert "Bad signature" in msg


class TestDownloadAndVerifyTarball:
    """Tests for download_and_verify_tarball function."""

    def test_no_tarball_url(self, tmp_path: Path) -> None:
        """Test with missing tarball URL."""
        source = upstream.UpstreamSource(version="1.0", build_type=upstream.BuildType.SNAPSHOT)
        result = upstream.download_and_verify_tarball(source, tmp_path)

        assert result.success is False
        assert "No tarball URL" in result.error

    def test_download_failure(self, tmp_path: Path) -> None:
        """Test handling of download failure."""
        source = upstream.UpstreamSource(
            version="1.0",
            tarball_url="http://example.com/foo-1.0.tar.gz",
            build_type=upstream.BuildType.RELEASE,
        )

        with patch.object(upstream, "download_file") as mock_download:
            mock_download.return_value = (False, "Network error")
            result = upstream.download_and_verify_tarball(source, tmp_path)

            assert result.success is False
            assert "Network error" in result.error

    def test_successful_download_without_signature(self, tmp_path: Path) -> None:
        """Test successful download when no signature URL."""
        source = upstream.UpstreamSource(
            version="1.0",
            tarball_url="http://example.com/foo-1.0.tar.gz",
            build_type=upstream.BuildType.RELEASE,
        )

        with patch.object(upstream, "download_file") as mock_download:
            mock_download.return_value = (True, "")
            result = upstream.download_and_verify_tarball(source, tmp_path)

            assert result.success is True
            assert result.signature_verified is False

    def test_successful_download_with_verified_signature(self, tmp_path: Path) -> None:
        """Test successful download with signature verification."""
        source = upstream.UpstreamSource(
            version="1.0",
            tarball_url="http://example.com/foo-1.0.tar.gz",
            signature_url="http://example.com/foo-1.0.tar.gz.asc",
            build_type=upstream.BuildType.RELEASE,
        )

        with patch.object(upstream, "download_file") as mock_download:
            mock_download.return_value = (True, "")
            with patch.object(upstream, "verify_signature") as mock_verify:
                mock_verify.return_value = (True, "OK")
                result = upstream.download_and_verify_tarball(source, tmp_path)

                assert result.success is True
                assert result.signature_verified is True


class TestSelectUpstreamSourceMilestone:
    """Tests for milestone selection in select_upstream_source."""

    def test_milestone_found(self, tmp_path: Path) -> None:
        """Test finding a milestone release."""
        with patch("packastack.upstream.releases.load_project_releases") as mock_load:
            mock_proj = MagicMock()
            mock_b1 = MagicMock()
            mock_b1.version = "30.0.0.0b1"
            mock_b2 = MagicMock()
            mock_b2.version = "30.0.0.0b2"
            mock_proj.releases = [mock_b1, mock_b2]
            mock_load.return_value = mock_proj

            source = upstream.select_upstream_source(
                releases_repo=tmp_path,
                series="2024.2",
                project="nova",
                build_type=upstream.BuildType.MILESTONE,
                milestone="b2",
            )

            assert source is not None
            assert source.version == "30.0.0.0b2"
            assert source.build_type == upstream.BuildType.MILESTONE
            assert source.milestone == "b2"

    def test_milestone_not_found(self, tmp_path: Path) -> None:
        """Test milestone not found returns None."""
        with patch("packastack.upstream.releases.load_project_releases") as mock_load:
            mock_proj = MagicMock()
            mock_b1 = MagicMock()
            mock_b1.version = "30.0.0.0b1"
            mock_proj.releases = [mock_b1]
            mock_load.return_value = mock_proj

            source = upstream.select_upstream_source(
                releases_repo=tmp_path,
                series="2024.2",
                project="nova",
                build_type=upstream.BuildType.MILESTONE,
                milestone="rc1",
            )

            assert source is None


class TestGenerateSnapshotTarball:
    """Tests for generate_snapshot_tarball function."""

    def test_successful_generation(self, tmp_path: Path) -> None:
        """Test successful tarball generation."""
        repo = tmp_path / "repo"
        repo.mkdir()
        output_dir = tmp_path / "output"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = upstream.generate_snapshot_tarball(
                repo_path=repo,
                ref="HEAD",
                package="nova",
                version="30.0.0~git20241227.abc1234",
                output_dir=output_dir,
            )

            assert result.success is True
            assert result.signature_warning == "Snapshot build - no signature verification"
            mock_run.assert_called_once()

    def test_generation_failure(self, tmp_path: Path) -> None:
        """Test tarball generation failure."""
        repo = tmp_path / "repo"
        repo.mkdir()
        output_dir = tmp_path / "output"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="fatal: not a tree")
            result = upstream.generate_snapshot_tarball(
                repo_path=repo,
                ref="nonexistent",
                package="nova",
                version="30.0.0",
                output_dir=output_dir,
            )

            assert result.success is False
            assert "git archive failed" in result.error

    def test_generation_timeout(self, tmp_path: Path) -> None:
        """Test tarball generation timeout."""
        import subprocess as sp

        repo = tmp_path / "repo"
        repo.mkdir()
        output_dir = tmp_path / "output"

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = sp.TimeoutExpired(cmd=["git"], timeout=300)
            result = upstream.generate_snapshot_tarball(
                repo_path=repo,
                ref="HEAD",
                package="nova",
                version="30.0.0",
                output_dir=output_dir,
            )

            assert result.success is False
            assert "timed out" in result.error

    def test_epoch_in_version(self, tmp_path: Path) -> None:
        """Test version with epoch is cleaned."""
        repo = tmp_path / "repo"
        repo.mkdir()
        output_dir = tmp_path / "output"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = upstream.generate_snapshot_tarball(
                repo_path=repo,
                ref="HEAD",
                package="nova",
                version="2:30.0.0",  # With epoch
                output_dir=output_dir,
            )

            assert result.success is True
            # The filename should not contain the epoch
            expected_path = output_dir / "nova_30.0.0.orig.tar.gz"
            assert result.path == expected_path


class TestGetGitSnapshotInfo:
    """Tests for get_git_snapshot_info function."""

    def test_successful_query(self, tmp_path: Path) -> None:
        """Test successful git snapshot info query."""
        with patch("subprocess.run") as mock_run:
            # First call: git rev-parse
            # Second call: git log --format=%ci
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="abc1234567890abcdef\n"),
                MagicMock(returncode=0, stdout="2024-12-27 10:30:00 +0000\n"),
            ]

            short_sha, full_sha, date = upstream.get_git_snapshot_info(tmp_path, "HEAD")

            assert short_sha == "abc1234"
            assert full_sha == "abc1234567890abcdef"
            assert date == "20241227"

    def test_exception_returns_empty(self, tmp_path: Path) -> None:
        """Test that exceptions return empty strings."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = Exception("git error")

            short_sha, full_sha, date = upstream.get_git_snapshot_info(tmp_path, "HEAD")

            assert short_sha == ""
            assert full_sha == ""
            assert date == ""


class TestGitDescribeResult:
    """Tests for GitDescribeResult dataclass."""

    def test_basic_fields(self) -> None:
        """Test basic field access."""
        result = upstream.GitDescribeResult(
            base_version="30.0.0",
            commit_count=123,
            short_sha="abc1234",
            is_exact_tag=False,
        )
        assert result.base_version == "30.0.0"
        assert result.commit_count == 123
        assert result.short_sha == "abc1234"
        assert result.is_exact_tag is False

    def test_exact_tag(self) -> None:
        """Test result for exact tag match."""
        result = upstream.GitDescribeResult(
            base_version="30.0.0",
            commit_count=0,
            short_sha="abc1234",
            is_exact_tag=True,
        )
        assert result.is_exact_tag is True


class TestGetVersionFromGitDescribe:
    """Tests for get_version_from_git_describe function."""

    def test_with_tag_and_commits(self, tmp_path: Path) -> None:
        """Test git describe with tag and commits since."""
        with patch("subprocess.run") as mock_run:
            # git describe --tags --long HEAD
            mock_run.return_value = MagicMock(
                returncode=0, stdout="30.0.0-123-gabc1234\n"
            )

            result = upstream.get_version_from_git_describe(tmp_path, "HEAD")

            assert result is not None
            assert result.base_version == "30.0.0"
            assert result.commit_count == 123
            assert result.short_sha == "abc1234"
            assert result.is_exact_tag is False

    def test_exact_tag_match(self, tmp_path: Path) -> None:
        """Test git describe when HEAD is exactly at a tag."""
        with patch("subprocess.run") as mock_run:
            # git describe --tags --long HEAD returns count=0 for exact match
            mock_run.return_value = MagicMock(
                returncode=0, stdout="30.0.0-0-gabc1234\n"
            )

            result = upstream.get_version_from_git_describe(tmp_path, "HEAD")

            assert result is not None
            assert result.base_version == "30.0.0"
            assert result.commit_count == 0
            assert result.is_exact_tag is True

    def test_no_tags_fallback(self, tmp_path: Path) -> None:
        """Test fallback when no tags exist."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                # git describe fails - no tags
                MagicMock(returncode=128, stdout="", stderr="fatal: No names found"),
                # git rev-list --count HEAD
                MagicMock(returncode=0, stdout="500\n"),
                # git rev-parse --short HEAD
                MagicMock(returncode=0, stdout="def5678\n"),
            ]

            result = upstream.get_version_from_git_describe(tmp_path, "HEAD")

            assert result is not None
            assert result.base_version == "0.0.0"
            assert result.commit_count == 500
            assert result.short_sha == "def5678"
            assert result.is_exact_tag is False

    def test_version_tag_with_v_prefix(self, tmp_path: Path) -> None:
        """Test handling of version tags with v prefix."""
        with patch("subprocess.run") as mock_run:
            # Some projects use v30.0.0 tags
            mock_run.return_value = MagicMock(
                returncode=0, stdout="v30.0.0-50-gxyz7890\n"
            )

            result = upstream.get_version_from_git_describe(tmp_path, "HEAD")

            assert result is not None
            assert result.base_version == "v30.0.0"
            assert result.commit_count == 50
            assert result.short_sha == "xyz7890"

    def test_exception_returns_none(self, tmp_path: Path) -> None:
        """Test that exceptions return None."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = Exception("git error")

            result = upstream.get_version_from_git_describe(tmp_path, "HEAD")

            assert result is None

    def test_timeout_returns_none(self, tmp_path: Path) -> None:
        """Test that timeout returns None."""
        import subprocess

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)

            result = upstream.get_version_from_git_describe(tmp_path, "HEAD")

            assert result is None

    def test_multi_digit_commit_count(self, tmp_path: Path) -> None:
        """Test handling of large commit counts."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="29.0.0-1234-g1234567\n"
            )

            result = upstream.get_version_from_git_describe(tmp_path, "HEAD")

            assert result is not None
            assert result.base_version == "29.0.0"
            assert result.commit_count == 1234
            assert result.short_sha == "1234567"


class TestApplySignaturePolicy:
    """Tests for apply_signature_policy function."""

    def test_release_build_keeps_keys(self, tmp_path: Path) -> None:
        """Test that release builds keep signing keys."""
        debian_dir = tmp_path / "debian"
        upstream_dir = debian_dir / "upstream"
        upstream_dir.mkdir(parents=True)
        (upstream_dir / "signing-key.asc").touch()

        removed = upstream.apply_signature_policy(debian_dir, upstream.BuildType.RELEASE)

        assert removed == []
        assert (upstream_dir / "signing-key.asc").exists()

    def test_milestone_build_keeps_keys(self, tmp_path: Path) -> None:
        """Test that milestone builds keep signing keys."""
        debian_dir = tmp_path / "debian"
        upstream_dir = debian_dir / "upstream"
        upstream_dir.mkdir(parents=True)
        (upstream_dir / "signing-key.asc").touch()

        removed = upstream.apply_signature_policy(debian_dir, upstream.BuildType.MILESTONE)

        assert removed == []
        assert (upstream_dir / "signing-key.asc").exists()

    def test_snapshot_build_removes_keys(self, tmp_path: Path) -> None:
        """Test that snapshot builds remove signing keys."""
        debian_dir = tmp_path / "debian"
        upstream_dir = debian_dir / "upstream"
        upstream_dir.mkdir(parents=True)
        key_file = upstream_dir / "signing-key.asc"
        key_file.touch()

        removed = upstream.apply_signature_policy(debian_dir, upstream.BuildType.SNAPSHOT)

        assert len(removed) == 1
        assert removed[0] == key_file
        assert not key_file.exists()

    def test_snapshot_no_upstream_dir(self, tmp_path: Path) -> None:
        """Test snapshot when no upstream directory exists."""
        debian_dir = tmp_path / "debian"
        debian_dir.mkdir()

        removed = upstream.apply_signature_policy(debian_dir, upstream.BuildType.SNAPSHOT)

        assert removed == []

    def test_snapshot_removes_multiple_patterns(self, tmp_path: Path) -> None:
        """Test snapshot removes files matching multiple patterns."""
        debian_dir = tmp_path / "debian"
        upstream_dir = debian_dir / "upstream"
        upstream_dir.mkdir(parents=True)
        (upstream_dir / "key1.asc").touch()
        (upstream_dir / "key2.sig").touch()
        (upstream_dir / "keyring.gpg").touch()
        (upstream_dir / "README.txt").touch()  # Should not be removed

        removed = upstream.apply_signature_policy(debian_dir, upstream.BuildType.SNAPSHOT)

        assert len(removed) == 3
        assert (upstream_dir / "README.txt").exists()


class TestComputeTarballHash:
    """Tests for compute_tarball_hash function."""

    def test_sha256_hash(self, tmp_path: Path) -> None:
        """Test SHA-256 hash computation."""
        tarball = tmp_path / "test.tar.gz"
        tarball.write_bytes(b"test content")

        hash_value = upstream.compute_tarball_hash(tarball, "sha256")

        # Known hash for "test content"
        import hashlib

        expected = hashlib.sha256(b"test content").hexdigest()
        assert hash_value == expected

    def test_sha512_hash(self, tmp_path: Path) -> None:
        """Test SHA-512 hash computation."""
        tarball = tmp_path / "test.tar.gz"
        tarball.write_bytes(b"test content")

        hash_value = upstream.compute_tarball_hash(tarball, "sha512")

        import hashlib

        expected = hashlib.sha512(b"test content").hexdigest()
        assert hash_value == expected

    def test_md5_hash(self, tmp_path: Path) -> None:
        """Test MD5 hash computation."""
        tarball = tmp_path / "test.tar.gz"
        tarball.write_bytes(b"test content")

        hash_value = upstream.compute_tarball_hash(tarball, "md5")

        import hashlib

        expected = hashlib.md5(b"test content").hexdigest()
        assert hash_value == expected


class TestSnapshotAcquisitionResult:
    """Tests for SnapshotAcquisitionResult dataclass."""

    def test_success_result(self, tmp_path: Path) -> None:
        """Test successful snapshot acquisition result."""
        result = upstream.SnapshotAcquisitionResult(
            success=True,
            repo_path=tmp_path / "nova",
            git_sha="abc1234567890",
            git_sha_short="abc1234",
            git_date="20241227",
            upstream_version="29.0.0~git20241227.abc1234",
        )
        assert result.success is True
        assert result.git_sha == "abc1234567890"
        assert result.git_sha_short == "abc1234"
        assert result.upstream_version == "29.0.0~git20241227.abc1234"

    def test_failure_result(self) -> None:
        """Test failed snapshot acquisition result."""
        result = upstream.SnapshotAcquisitionResult(
            success=False,
            error="Clone failed: repository not found",
        )
        assert result.success is False
        assert result.error == "Clone failed: repository not found"


class TestBuildOpendevUrl:
    """Tests for build_opendev_url function."""

    def test_simple_project(self) -> None:
        """Test URL for simple project name."""
        url = upstream.build_opendev_url("nova")
        assert url == "https://opendev.org/openstack/nova.git"

    def test_oslo_project(self) -> None:
        """Test URL for oslo project."""
        url = upstream.build_opendev_url("oslo.config")
        assert url == "https://opendev.org/openstack/oslo.config.git"

    def test_client_project(self) -> None:
        """Test URL for client project."""
        url = upstream.build_opendev_url("python-novaclient")
        assert url == "https://opendev.org/openstack/python-novaclient.git"


class TestCloneUpstreamRepo:
    """Tests for clone_upstream_repo function."""

    def test_clone_success(self, tmp_path: Path) -> None:
        """Test successful clone."""
        with patch("git.Repo.clone_from") as mock_clone:
            mock_clone.return_value = MagicMock()

            repo_path, cloned, error = upstream.clone_upstream_repo(
                project="nova",
                dest_dir=tmp_path,
            )

            assert repo_path == tmp_path / "nova"
            assert cloned is True
            assert error == ""
            mock_clone.assert_called_once()

    def test_clone_with_branch(self, tmp_path: Path) -> None:
        """Test clone with specific branch."""
        with patch("git.Repo.clone_from") as mock_clone:
            mock_clone.return_value = MagicMock()

            _repo_path, cloned, _error = upstream.clone_upstream_repo(
                project="nova",
                dest_dir=tmp_path,
                branch="stable/2024.2",
            )

            assert cloned is True
            # Check that branch was passed to clone_from
            call_kwargs = mock_clone.call_args[1]
            assert call_kwargs.get("branch") == "stable/2024.2"

    def test_clone_failure(self, tmp_path: Path) -> None:
        """Test clone failure."""
        import git

        with patch("git.Repo.clone_from") as mock_clone:
            mock_clone.side_effect = git.GitCommandError("clone", "failed")

            repo_path, cloned, error = upstream.clone_upstream_repo(
                project="nonexistent",
                dest_dir=tmp_path,
            )

            assert repo_path is None
            assert cloned is False
            assert "Clone failed" in error

    def test_existing_repo_fetch(self, tmp_path: Path) -> None:
        """Test fetching updates for existing repo."""
        # Create fake existing repo
        repo_path = tmp_path / "nova"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()

        mock_repo = MagicMock()
        mock_origin = MagicMock()
        mock_repo.remotes.origin = mock_origin

        with patch("git.Repo") as mock_git_repo:
            mock_git_repo.return_value = mock_repo

            result_path, cloned, error = upstream.clone_upstream_repo(
                project="nova",
                dest_dir=tmp_path,
            )

            assert result_path == repo_path
            assert cloned is False  # Updated, not cloned
            assert error == ""
            mock_origin.fetch.assert_called_once()


class TestAcquireUpstreamSnapshot:
    """Tests for acquire_upstream_snapshot function."""

    def test_successful_acquisition(self, tmp_path: Path) -> None:
        """Test successful snapshot acquisition with git describe."""
        work_dir = tmp_path / "work"
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True)

        with patch.object(upstream, "clone_upstream_repo") as mock_clone, \
             patch.object(upstream, "get_git_snapshot_info") as mock_info, \
             patch.object(upstream, "get_version_from_git_describe") as mock_describe, \
             patch.object(upstream, "generate_snapshot_tarball") as mock_tarball:

            mock_clone.return_value = (work_dir / "nova", True, "")
            mock_info.return_value = ("abc1234", "abc1234567890abcdef", "20241227")
            mock_describe.return_value = upstream.GitDescribeResult(
                base_version="29.0.0",
                commit_count=50,
                short_sha="abc1234",
                is_exact_tag=False,
            )
            mock_tarball.return_value = upstream.TarballResult(
                success=True,
                path=output_dir / "nova_29.0.0+git20241227.50.abc1234.orig.tar.gz",
            )

            request = upstream.SnapshotRequest(
                project="nova",
                base_version="29.0.0",
            )
            result = upstream.acquire_upstream_snapshot(
                request=request,
                work_dir=work_dir,
                output_dir=output_dir,
            )

            assert result.success is True
            assert result.git_sha_short == "abc1234"
            assert result.git_date == "20241227"
            # Snapshot format: base_version+git{date}.{count}.{sha}
            assert result.upstream_version == "29.0.0+git20241227.50.abc1234"
            assert result.cloned is True

    def test_exact_tag_uses_tag_version(self, tmp_path: Path) -> None:
        """Test that exact tag match uses tag version directly."""
        work_dir = tmp_path / "work"
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True)

        with patch.object(upstream, "clone_upstream_repo") as mock_clone, \
             patch.object(upstream, "get_git_snapshot_info") as mock_info, \
             patch.object(upstream, "get_version_from_git_describe") as mock_describe, \
             patch.object(upstream, "generate_snapshot_tarball") as mock_tarball:

            mock_clone.return_value = (work_dir / "nova", True, "")
            mock_info.return_value = ("abc1234", "abc1234567890abcdef", "20241227")
            mock_describe.return_value = upstream.GitDescribeResult(
                base_version="30.0.0",
                commit_count=0,
                short_sha="abc1234",
                is_exact_tag=True,
            )
            mock_tarball.return_value = upstream.TarballResult(success=True, path=output_dir / "test.tar.gz")

            request = upstream.SnapshotRequest(
                project="nova",
                base_version="29.0.0",
            )
            result = upstream.acquire_upstream_snapshot(
                request=request,
                work_dir=work_dir,
                output_dir=output_dir,
            )

            assert result.success is True
            # Exactly at tag - use +git for post-release snapshot
            assert result.upstream_version == "30.0.0+git20241227.abc1234"

    def test_git_describe_fallback(self, tmp_path: Path) -> None:
        """Test fallback to old format when git describe fails."""
        work_dir = tmp_path / "work"
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True)

        with patch.object(upstream, "clone_upstream_repo") as mock_clone, \
             patch.object(upstream, "get_git_snapshot_info") as mock_info, \
             patch.object(upstream, "get_version_from_git_describe") as mock_describe, \
             patch.object(upstream, "generate_snapshot_tarball") as mock_tarball:

            mock_clone.return_value = (work_dir / "nova", True, "")
            mock_info.return_value = ("abc1234", "abc1234567890abcdef", "20241227")
            mock_describe.return_value = None  # git describe failed
            mock_tarball.return_value = upstream.TarballResult(success=True, path=output_dir / "test.tar.gz")

            request = upstream.SnapshotRequest(
                project="nova",
                base_version="29.0.0",
            )
            result = upstream.acquire_upstream_snapshot(
                request=request,
                work_dir=work_dir,
                output_dir=output_dir,
            )

        assert result.success is True
        # Fallback to old format
        assert result.upstream_version == "29.0.0+git20241227.abc1234"

    def test_clone_failure(self, tmp_path: Path) -> None:
        """Test acquisition when clone fails."""
        with patch.object(upstream, "clone_upstream_repo") as mock_clone:
            mock_clone.return_value = (None, False, "Repository not found")

            request = upstream.SnapshotRequest(
                project="nonexistent",
                base_version="1.0.0",
            )
            result = upstream.acquire_upstream_snapshot(
                request=request,
                work_dir=tmp_path,
                output_dir=tmp_path,
            )

            assert result.success is False
            assert "Repository not found" in result.error

    def test_snapshot_info_failure(self, tmp_path: Path) -> None:
        """Test acquisition when getting snapshot info fails."""
        with patch.object(upstream, "clone_upstream_repo") as mock_clone, \
             patch.object(upstream, "get_git_snapshot_info") as mock_info:

            mock_clone.return_value = (tmp_path / "nova", True, "")
            mock_info.return_value = ("", "", "")  # Empty = failure

            request = upstream.SnapshotRequest(
                project="nova",
                base_version="29.0.0",
            )
            result = upstream.acquire_upstream_snapshot(
                request=request,
                work_dir=tmp_path,
                output_dir=tmp_path,
            )

            assert result.success is False
            assert "snapshot info" in result.error.lower()

    def test_tarball_generation_failure(self, tmp_path: Path) -> None:
        """Test acquisition when tarball generation fails."""
        with patch.object(upstream, "clone_upstream_repo") as mock_clone, \
             patch.object(upstream, "get_git_snapshot_info") as mock_info, \
             patch.object(upstream, "get_version_from_git_describe") as mock_describe, \
             patch.object(upstream, "generate_snapshot_tarball") as mock_tarball:

            mock_clone.return_value = (tmp_path / "nova", True, "")
            mock_info.return_value = ("abc1234", "abc1234567890", "20241227")
            mock_describe.return_value = upstream.GitDescribeResult(
                base_version="29.0.0", commit_count=10, short_sha="abc1234", is_exact_tag=False
            )
            mock_tarball.return_value = upstream.TarballResult(
                success=False,
                error="git archive failed",
            )

            request = upstream.SnapshotRequest(
                project="nova",
                base_version="29.0.0",
            )
            result = upstream.acquire_upstream_snapshot(
                request=request,
                work_dir=tmp_path,
                output_dir=tmp_path,
            )

            assert result.success is False
            assert "git archive failed" in result.error

    def test_with_branch(self, tmp_path: Path) -> None:
        """Test acquisition with specific branch."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(upstream, "clone_upstream_repo") as mock_clone, \
             patch.object(upstream, "get_git_snapshot_info") as mock_info, \
             patch.object(upstream, "get_version_from_git_describe") as mock_describe, \
             patch.object(upstream, "generate_snapshot_tarball") as mock_tarball:

            mock_clone.return_value = (tmp_path / "nova", True, "")
            mock_info.return_value = ("abc1234", "abc1234567890", "20241227")
            mock_describe.return_value = upstream.GitDescribeResult(
                base_version="29.0.0", commit_count=10, short_sha="abc1234", is_exact_tag=False
            )
            mock_tarball.return_value = upstream.TarballResult(
                success=True,
                path=output_dir / "tarball.tar.gz",
            )

            request = upstream.SnapshotRequest(
                project="nova",
                base_version="29.0.0",
                branch="stable/2024.2",
            )
            upstream.acquire_upstream_snapshot(
                request=request,
                work_dir=tmp_path,
                output_dir=output_dir,
            )

            # Verify branch was passed to clone
            clone_call = mock_clone.call_args
            assert clone_call[1]["branch"] == "stable/2024.2"

    def test_custom_package_name(self, tmp_path: Path) -> None:
        """Test acquisition with custom package name."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(upstream, "clone_upstream_repo") as mock_clone, \
             patch.object(upstream, "get_git_snapshot_info") as mock_info, \
             patch.object(upstream, "get_version_from_git_describe") as mock_describe, \
             patch.object(upstream, "generate_snapshot_tarball") as mock_tarball:

            mock_clone.return_value = (tmp_path / "nova", True, "")
            mock_info.return_value = ("abc1234", "abc1234567890", "20241227")
            mock_describe.return_value = upstream.GitDescribeResult(
                base_version="29.0.0", commit_count=10, short_sha="abc1234", is_exact_tag=False
            )
            mock_tarball.return_value = upstream.TarballResult(
                success=True,
                path=output_dir / "tarball.tar.gz",
            )

            request = upstream.SnapshotRequest(
                project="nova",
                base_version="29.0.0",
                package_name="python3-nova",
            )
            upstream.acquire_upstream_snapshot(
                request=request,
                work_dir=tmp_path,
                output_dir=output_dir,
            )

            # Verify package_name was passed to tarball generation
            tarball_call = mock_tarball.call_args
            assert tarball_call[1]["package"] == "python3-nova"


class TestCloneUpstreamRepoEdgeCases:
    """Additional edge case tests for clone_upstream_repo."""

    def test_shallow_clone(self, tmp_path: Path) -> None:
        """Test shallow clone option."""
        with patch("git.Repo.clone_from") as mock_clone:
            mock_clone.return_value = MagicMock()

            _repo_path, _cloned, _error = upstream.clone_upstream_repo(
                project="nova",
                dest_dir=tmp_path,
                shallow=True,
            )

            # Verify depth was passed for shallow clone
            call_kwargs = mock_clone.call_args[1]
            assert call_kwargs.get("depth") == 1

    def test_full_clone_no_depth(self, tmp_path: Path) -> None:
        """Test full clone without depth limit."""
        with patch("git.Repo.clone_from") as mock_clone:
            mock_clone.return_value = MagicMock()

            _repo_path, _cloned, _error = upstream.clone_upstream_repo(
                project="nova",
                dest_dir=tmp_path,
                shallow=False,
            )

            # Verify depth was NOT passed for full clone
            call_kwargs = mock_clone.call_args[1]
            assert "depth" not in call_kwargs

    def test_existing_repo_checkout_branch(self, tmp_path: Path) -> None:
        """Test checking out a branch in existing repo."""
        # Create fake existing repo
        repo_path = tmp_path / "nova"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()

        mock_repo = MagicMock()
        mock_origin = MagicMock()
        mock_repo.remotes.origin = mock_origin
        mock_repo.git = MagicMock()

        with patch("git.Repo") as mock_git_repo:
            mock_git_repo.return_value = mock_repo

            _result_path, _cloned, _error = upstream.clone_upstream_repo(
                project="nova",
                dest_dir=tmp_path,
                branch="stable/2024.2",
            )

            # Verify checkout was called
            mock_repo.git.checkout.assert_called()

    def test_existing_repo_fetch_error(self, tmp_path: Path) -> None:
        """Test error during fetch of existing repo."""
        import git as gitpkg

        # Create fake existing repo
        repo_path = tmp_path / "nova"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()

        mock_repo = MagicMock()
        mock_origin = MagicMock()
        mock_origin.fetch.side_effect = gitpkg.GitCommandError("fetch", "failed")
        mock_repo.remotes.origin = mock_origin

        with patch("git.Repo") as mock_git_repo:
            mock_git_repo.return_value = mock_repo

            result_path, _cloned, error = upstream.clone_upstream_repo(
                project="nova",
                dest_dir=tmp_path,
            )

            assert result_path is None
            assert "Fetch failed" in error

    def test_general_exception(self, tmp_path: Path) -> None:
        """Test handling of general exceptions."""
        with patch("git.Repo.clone_from") as mock_clone:
            mock_clone.side_effect = Exception("Unexpected error")

            repo_path, cloned, error = upstream.clone_upstream_repo(
                project="nova",
                dest_dir=tmp_path,
            )

            assert repo_path is None
            assert cloned is False
            assert "Unexpected error" in error


class TestAcquireUpstreamSnapshotEdgeCases:
    """Additional edge case tests for acquire_upstream_snapshot."""

    def test_version_string_format(self, tmp_path: Path) -> None:
        """Test that version string uses git describe format."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(upstream, "clone_upstream_repo") as mock_clone, \
             patch.object(upstream, "get_git_snapshot_info") as mock_info, \
             patch.object(upstream, "get_version_from_git_describe") as mock_describe, \
             patch.object(upstream, "generate_snapshot_tarball") as mock_tarball:

            mock_clone.return_value = (tmp_path / "nova", True, "")
            mock_info.return_value = ("abc1234", "abc1234567890abcdef1234", "20241227")
            mock_describe.return_value = upstream.GitDescribeResult(
                base_version="29.0.0", commit_count=75, short_sha="abc1234", is_exact_tag=False
            )
            mock_tarball.return_value = upstream.TarballResult(
                success=True,
                path=output_dir / "tarball.tar.gz",
            )

            request = upstream.SnapshotRequest(
                project="nova",
                base_version="29.0.0",
            )
            result = upstream.acquire_upstream_snapshot(
                request=request,
                work_dir=tmp_path,
                output_dir=output_dir,
            )

            # Snapshot format: base_version+git{date}.{count}.{sha}
            assert result.upstream_version == "29.0.0+git20241227.75.abc1234"
            assert result.git_sha == "abc1234567890abcdef1234"
            assert result.git_sha_short == "abc1234"

    def test_default_package_name(self, tmp_path: Path) -> None:
        """Test that package_name defaults to project name."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(upstream, "clone_upstream_repo") as mock_clone, \
             patch.object(upstream, "get_git_snapshot_info") as mock_info, \
             patch.object(upstream, "get_version_from_git_describe") as mock_describe, \
             patch.object(upstream, "generate_snapshot_tarball") as mock_tarball:

            mock_clone.return_value = (tmp_path / "keystone", True, "")
            mock_info.return_value = ("def5678", "def5678901234", "20241227")
            mock_describe.return_value = upstream.GitDescribeResult(
                base_version="25.0.0", commit_count=100, short_sha="def5678", is_exact_tag=False
            )
            mock_tarball.return_value = upstream.TarballResult(
                success=True,
                path=output_dir / "tarball.tar.gz",
            )

            request = upstream.SnapshotRequest(
                project="keystone",
                base_version="25.0.0",
            )
            upstream.acquire_upstream_snapshot(
                request=request,
                work_dir=tmp_path,
                output_dir=output_dir,
            )

            # Verify package name defaulted to project
            tarball_call = mock_tarball.call_args
            assert tarball_call[1]["package"] == "keystone"

    def test_custom_git_ref(self, tmp_path: Path) -> None:
        """Test acquisition with custom git ref."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(upstream, "clone_upstream_repo") as mock_clone, \
             patch.object(upstream, "get_git_snapshot_info") as mock_info, \
             patch.object(upstream, "get_version_from_git_describe") as mock_describe, \
             patch.object(upstream, "generate_snapshot_tarball") as mock_tarball:

            mock_clone.return_value = (tmp_path / "nova", True, "")
            mock_info.return_value = ("abc1234", "abc1234567890", "20241227")
            mock_describe.return_value = upstream.GitDescribeResult(
                base_version="29.0.0", commit_count=0, short_sha="abc1234", is_exact_tag=True
            )
            mock_tarball.return_value = upstream.TarballResult(
                success=True,
                path=output_dir / "tarball.tar.gz",
            )

            request = upstream.SnapshotRequest(
                project="nova",
                base_version="29.0.0",
                git_ref="v29.0.0",
            )
            result = upstream.acquire_upstream_snapshot(
                request=request,
                work_dir=tmp_path,
                output_dir=output_dir,
            )

            # Verify git_ref was passed
            info_call = mock_info.call_args
            assert info_call[0][1] == "v29.0.0"  # Second positional arg is ref
            assert result.git_ref == "v29.0.0"

    def test_result_includes_repo_path(self, tmp_path: Path) -> None:
        """Test that result includes the cloned repo path."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(upstream, "clone_upstream_repo") as mock_clone, \
             patch.object(upstream, "get_git_snapshot_info") as mock_info, \
             patch.object(upstream, "get_version_from_git_describe") as mock_describe, \
             patch.object(upstream, "generate_snapshot_tarball") as mock_tarball:

            expected_repo_path = tmp_path / "nova"
            mock_clone.return_value = (expected_repo_path, True, "")
            mock_info.return_value = ("abc1234", "abc1234567890", "20241227")
            mock_describe.return_value = upstream.GitDescribeResult(
                base_version="29.0.0", commit_count=10, short_sha="abc1234", is_exact_tag=False
            )
            mock_tarball.return_value = upstream.TarballResult(
                success=True,
                path=output_dir / "tarball.tar.gz",
            )

            request = upstream.SnapshotRequest(
                project="nova",
                base_version="29.0.0",
            )
            result = upstream.acquire_upstream_snapshot(
                request=request,
                work_dir=tmp_path,
                output_dir=output_dir,
            )

            assert result.repo_path == expected_repo_path


class TestGetGitSnapshotInfoEdgeCases:
    """Additional edge case tests for get_git_snapshot_info."""

    def test_date_parsing_with_timezone(self, tmp_path: Path) -> None:
        """Test date parsing with timezone offset."""
        with patch("subprocess.run") as mock_run:
            # First call for rev-parse
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="abc1234567890abcdef1234567890abcdef123456\n",
            )

            short_sha, full_sha, _date_str = upstream.get_git_snapshot_info(tmp_path, "HEAD")

            assert short_sha == "abc1234"
            assert full_sha == "abc1234567890abcdef1234567890abcdef123456"


class TestBuildOpendevUrlEdgeCases:
    """Additional edge case tests for build_opendev_url."""

    def test_special_characters_in_project(self) -> None:
        """Test URL building with special project names."""
        # oslo.* projects
        url = upstream.build_opendev_url("oslo.messaging")
        assert url == "https://opendev.org/openstack/oslo.messaging.git"

        # Projects with hyphens
        url = upstream.build_opendev_url("neutron-lib")
        assert url == "https://opendev.org/openstack/neutron-lib.git"

    def test_opendev_base_url_constant(self) -> None:
        """Test that OPENDEV_BASE_URL is correctly defined."""
        assert upstream.OPENDEV_BASE_URL == "https://opendev.org/openstack"


class TestGenerateSnapshotTarballEdgeCases:
    """Additional edge case tests for generate_snapshot_tarball."""

    def test_tarball_naming_convention(self, tmp_path: Path) -> None:
        """Test that tarball follows Debian naming convention."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            result = upstream.generate_snapshot_tarball(
                repo_path=repo_path,
                ref="HEAD",
                package="python3-nova",
                version="29.0.0~git20241227.abc1234",
                output_dir=output_dir,
            )

            # Verify the tarball path uses correct naming
            expected_name = "python3-nova_29.0.0~git20241227.abc1234.orig.tar.gz"
            assert result.path.name == expected_name

    def test_version_with_epoch(self, tmp_path: Path) -> None:
        """Test that epoch is stripped from tarball name."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            result = upstream.generate_snapshot_tarball(
                repo_path=repo_path,
                ref="HEAD",
                package="test",
                version="2:1.0.0",
                output_dir=output_dir,
            )

            # Epoch should be stripped from filename
            assert "2:" not in result.path.name
            assert "1.0.0" in result.path.name

class TestUpstreamVersionFormats:
    """Test various upstream version formats."""

    def test_generate_tarball_for_lib_package(self, tmp_path: Path) -> None:
        """Test tarball generation for lib-prefixed package."""
        import subprocess as sp

        upstream_dir = tmp_path / "upstream"
        upstream_dir.mkdir()

        # Initialize git repo with initial branch 'main'
        sp.run(["git", "init", "-b", "main"], cwd=upstream_dir, capture_output=True)
        sp.run(["git", "config", "user.email", "test@test.com"], cwd=upstream_dir, capture_output=True)
        sp.run(["git", "config", "user.name", "Test"], cwd=upstream_dir, capture_output=True)
        # Disable GPG signing for this repo
        sp.run(["git", "config", "commit.gpgsign", "false"], cwd=upstream_dir, capture_output=True)

        (upstream_dir / "setup.py").write_text("# test")
        sp.run(["git", "add", "."], cwd=upstream_dir, capture_output=True)
        # Create initial commit
        commit_result = sp.run(["git", "commit", "-m", "init"], cwd=upstream_dir, capture_output=True)
        assert commit_result.returncode == 0, f"Git commit failed: {commit_result.stderr}"

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        result = upstream.generate_snapshot_tarball(
            repo_path=upstream_dir,
            ref="HEAD",
            package="python-oslo.config",
            version="10.0.0.dev5",
            output_dir=output_dir,
        )

        assert result.success is True
        assert result.path is not None
        # Should use package name in tarball
        assert "python-oslo.config" in result.path.name

    def test_acquire_snapshot_with_special_branch(self, tmp_path: Path) -> None:
        """Test snapshot acquisition with release branch."""
        work_dir = tmp_path / "work"
        output_dir = tmp_path / "output"

        # Clone returns tuple (repo_path, cloned, error)
        mock_repo_path = tmp_path / "repo"
        mock_repo_path.mkdir(parents=True)

        mock_tarball = MagicMock()
        mock_tarball.success = True
        mock_tarball.path = tmp_path / "tarball.tar.gz"

        with (
            patch.object(upstream, "clone_upstream_repo", return_value=(mock_repo_path, True, "")),
            # get_git_snapshot_info returns tuple (short_sha, full_sha, date_str)
            patch.object(upstream, "get_git_snapshot_info", return_value=("abc123", "abc123def456", "20240101")),
            patch.object(upstream, "get_version_from_git_describe", return_value=upstream.GitDescribeResult(
                base_version="29.0.0", commit_count=5, short_sha="abc123", is_exact_tag=False
            )),
            patch.object(upstream, "generate_snapshot_tarball", return_value=mock_tarball),
        ):
            request = upstream.SnapshotRequest(
                project="nova",
                base_version="29.0.0",
                branch="stable/2024.1",  # Release branch
            )
            result = upstream.acquire_upstream_snapshot(
                request=request,
                work_dir=work_dir,
                output_dir=output_dir,
            )

        assert result.success is True
        assert result.cloned is True


class TestBuildOpendevUrlPatterns:
    """Test OpenDev URL building patterns."""

    def test_url_for_client_lib(self) -> None:
        """Test URL for client library."""
        url = upstream.build_opendev_url("python-novaclient")
        assert url == "https://opendev.org/openstack/python-novaclient.git"

    def test_url_for_oslo_lib(self) -> None:
        """Test URL for oslo library."""
        url = upstream.build_opendev_url("oslo.config")
        assert url == "https://opendev.org/openstack/oslo.config.git"

    def test_url_for_neutron_plugin(self) -> None:
        """Test URL for neutron plugin."""
        url = upstream.build_opendev_url("networking-ovn")
        assert url == "https://opendev.org/openstack/networking-ovn.git"
