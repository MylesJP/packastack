# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for the build_helpers.tarball module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from packastack.build.tarball import (
    _download_github_release_tarball,
    _download_pypi_tarball,
    _fetch_release_tarball,
    # Backwards compatibility aliases
    _run_uscan,
    download_github_release_tarball,
    download_pypi_tarball,
    fetch_release_tarball,
    run_uscan,
)


class TestRunUscan:
    """Tests for run_uscan function."""

    def test_uscan_success_with_tarball(self, tmp_path: Path):
        """Test successful uscan run finds tarball."""
        # Create a fake tarball
        tarball = tmp_path / "mypackage_1.0.orig.tar.gz"
        tarball.touch()

        with patch(
            "packastack.build.tarball.run_command"
        ) as mock_cmd:
            mock_cmd.return_value = (0, "", "")
            success, path, err = run_uscan(tmp_path, "1.0")

        assert success is True
        assert path == tarball
        assert err == ""

    def test_uscan_success_picks_newest_tarball(self, tmp_path: Path):
        """Test uscan picks the newest tarball when multiple exist."""
        import time

        # Create older tarball
        old_tarball = tmp_path / "mypackage_0.9.orig.tar.gz"
        old_tarball.touch()
        time.sleep(0.01)  # Ensure different mtime

        # Create newer tarball
        new_tarball = tmp_path / "mypackage_1.0.orig.tar.gz"
        new_tarball.touch()

        with patch(
            "packastack.build.tarball.run_command"
        ) as mock_cmd:
            mock_cmd.return_value = (0, "", "")
            success, path, _err = run_uscan(tmp_path)

        assert success is True
        assert path == new_tarball

    def test_uscan_failure_returns_error(self, tmp_path: Path):
        """Test uscan failure returns error message."""
        with patch(
            "packastack.build.tarball.run_command"
        ) as mock_cmd:
            mock_cmd.return_value = (1, "stdout", "uscan failed: no watch file")
            success, path, err = run_uscan(tmp_path)

        assert success is False
        assert path is None
        assert "uscan failed" in err

    def test_uscan_no_tarball_found(self, tmp_path: Path):
        """Test uscan succeeds but no tarball found."""
        with patch(
            "packastack.build.tarball.run_command"
        ) as mock_cmd:
            mock_cmd.return_value = (0, "", "")
            success, path, err = run_uscan(tmp_path)

        assert success is False
        assert path is None
        assert "no tarball found" in err

    def test_uscan_not_installed(self, tmp_path: Path):
        """Test uscan not installed."""
        with patch(
            "packastack.build.tarball.run_command"
        ) as mock_cmd:
            mock_cmd.side_effect = FileNotFoundError("uscan not found")
            success, path, err = run_uscan(tmp_path)

        assert success is False
        assert path is None
        assert "uscan not installed" in err

    def test_uscan_exception_handled(self, tmp_path: Path):
        """Test unexpected exception is handled."""
        with patch(
            "packastack.build.tarball.run_command"
        ) as mock_cmd:
            mock_cmd.side_effect = RuntimeError("unexpected error")
            success, path, err = run_uscan(tmp_path)

        assert success is False
        assert path is None
        assert "unexpected error" in err

    def test_version_parameter_ignored(self, tmp_path: Path):
        """Test version parameter is accepted but ignored."""
        tarball = tmp_path / "mypackage_1.0.orig.tar.gz"
        tarball.touch()

        with patch(
            "packastack.build.tarball.run_command"
        ) as mock_cmd:
            mock_cmd.return_value = (0, "", "")
            # Pass version - should be ignored
            success, _path, _err = run_uscan(tmp_path, version="2.0")

        # Version doesn't affect behavior
        assert success is True


class TestDownloadPypiTarball:
    """Tests for download_pypi_tarball function."""

    def test_successful_download(self, tmp_path: Path):
        """Test successful PyPI tarball download."""
        with patch(
            "packastack.build.tarball.download_file"
        ) as mock_dl:
            mock_dl.return_value = (True, "")
            ok, path, err = download_pypi_tarball("oslo.config", "9.0.0", tmp_path)

        assert ok is True
        assert path == tmp_path / "oslo.config-9.0.0.tar.gz"
        assert err == ""

        # Verify correct URL was constructed
        expected_url = "https://files.pythonhosted.org/packages/source/o/oslo.config/oslo.config-9.0.0.tar.gz"
        mock_dl.assert_called_once()
        actual_url = mock_dl.call_args[0][0]
        assert actual_url == expected_url

    def test_project_with_slash_replaced(self, tmp_path: Path):
        """Test project name with slash is handled."""
        with patch(
            "packastack.build.tarball.download_file"
        ) as mock_dl:
            mock_dl.return_value = (True, "")
            ok, _path, _err = download_pypi_tarball("openstack/nova", "1.0", tmp_path)

        assert ok is True
        # Slash should be replaced with dash
        expected_url = "https://files.pythonhosted.org/packages/source/o/openstack-nova/openstack-nova-1.0.tar.gz"
        actual_url = mock_dl.call_args[0][0]
        assert actual_url == expected_url

    def test_download_failure(self, tmp_path: Path):
        """Test download failure returns error."""
        with patch(
            "packastack.build.tarball.download_file"
        ) as mock_dl:
            mock_dl.return_value = (False, "Connection refused")
            ok, path, err = download_pypi_tarball("mypackage", "1.0", tmp_path)

        assert ok is False
        assert path is None
        assert err == "Connection refused"


class TestDownloadGithubReleaseTarball:
    """Tests for download_github_release_tarball function."""

    def test_successful_download(self, tmp_path: Path):
        """Test successful GitHub release download."""
        with patch(
            "packastack.build.tarball.download_file"
        ) as mock_dl:
            mock_dl.return_value = (True, "")
            ok, path, err = download_github_release_tarball(
                "https://github.com/openstack/nova.git", "v1.0.0", tmp_path
            )

        assert ok is True
        assert path == tmp_path / "v1.0.0.tar.gz"
        assert err == ""

        # Verify .git suffix is stripped
        expected_url = "https://github.com/openstack/nova/archive/refs/tags/v1.0.0.tar.gz"
        actual_url = mock_dl.call_args[0][0]
        assert actual_url == expected_url

    def test_url_without_git_suffix(self, tmp_path: Path):
        """Test URL without .git suffix works."""
        with patch(
            "packastack.build.tarball.download_file"
        ) as mock_dl:
            mock_dl.return_value = (True, "")
            _ok, _path, _err = download_github_release_tarball(
                "https://github.com/openstack/nova", "v2.0.0", tmp_path
            )

        expected_url = "https://github.com/openstack/nova/archive/refs/tags/v2.0.0.tar.gz"
        actual_url = mock_dl.call_args[0][0]
        assert actual_url == expected_url

    def test_download_failure(self, tmp_path: Path):
        """Test download failure returns error."""
        with patch(
            "packastack.build.tarball.download_file"
        ) as mock_dl:
            mock_dl.return_value = (False, "404 Not Found")
            ok, path, err = download_github_release_tarball(
                "https://github.com/openstack/nova", "v1.0.0", tmp_path
            )

        assert ok is False
        assert path is None
        assert err == "404 Not Found"


class TestFetchReleaseTarball:
    """Tests for fetch_release_tarball function."""

    def _make_provenance(self):
        """Create a mock provenance object."""
        provenance = MagicMock()
        provenance.tarball = MagicMock()
        provenance.verification = MagicMock()
        provenance.upstream = MagicMock()
        provenance.release_source = MagicMock()
        return provenance

    def _make_upstream_config(self, prefer=None):
        """Create a mock upstream config."""
        config = MagicMock()
        config.signatures = MagicMock()
        config.signatures.mode = MagicMock()
        config.signatures.mode.value = "warn"
        config.tarball = MagicMock()
        config.tarball.prefer = prefer or []
        config.upstream = MagicMock()
        config.upstream.url = "https://github.com/openstack/test"
        config.release_source = MagicMock()
        config.release_source.project = "test"
        return config

    def test_offline_mode_uses_cache(self, tmp_path: Path):
        """Test offline mode uses cached tarball."""
        provenance = self._make_provenance()
        upstream_config = self._make_upstream_config()
        upstream = MagicMock()
        upstream.version = "1.0.0"

        cached = tmp_path / "cached.tar.gz"
        cached.touch()
        cached_meta = MagicMock()
        cached_meta.source_url = "http://example.com/cached.tar.gz"
        cached_meta.signature_verified = True
        cached_meta.signature_warning = ""

        with patch(
            "packastack.build.tarball.find_cached_tarball"
        ) as mock_cache:
            mock_cache.return_value = (cached, cached_meta)
            with patch("packastack.build.tarball.activity"):
                path, sig_verified, _sig_warn = fetch_release_tarball(
                    upstream=upstream,
                    upstream_config=upstream_config,
                    pkg_repo=tmp_path,
                    workspace=tmp_path,
                    provenance=provenance,
                    offline=True,
                    project_key="test",
                    package_name="python-test",
                    build_type=MagicMock(value="release"),
                    cache_base=tmp_path,
                    force=False,
                    run=MagicMock(),
                )

        assert path == cached
        assert sig_verified is True
        assert provenance.tarball.method == "cache"

    def test_offline_mode_no_cache_fails(self, tmp_path: Path):
        """Test offline mode fails when cache miss."""
        provenance = self._make_provenance()
        upstream_config = self._make_upstream_config()
        upstream = MagicMock()
        upstream.version = "1.0.0"

        with patch(
            "packastack.build.tarball.find_cached_tarball"
        ) as mock_cache:
            mock_cache.return_value = (None, None)
            path, _sig_verified, sig_warn = fetch_release_tarball(
                upstream=upstream,
                upstream_config=upstream_config,
                pkg_repo=tmp_path,
                workspace=tmp_path,
                provenance=provenance,
                offline=True,
                project_key="test",
                package_name="python-test",
                build_type=MagicMock(value="release"),
                cache_base=tmp_path,
                force=False,
                run=MagicMock(),
            )

        assert path is None
        assert "Offline mode missing cached tarball" in sig_warn

    def test_uscan_success(self, tmp_path: Path):
        """Test uscan is tried first and succeeds."""
        provenance = self._make_provenance()
        upstream_config = self._make_upstream_config()
        upstream = MagicMock()
        upstream.version = "1.0.0"

        tarball = tmp_path / "test_1.0.0.orig.tar.gz"
        tarball.touch()

        with patch(
            "packastack.build.tarball.run_uscan"
        ) as mock_uscan:
            mock_uscan.return_value = (True, tarball, "")
            with patch("packastack.build.tarball.activity"):
                with patch(
                    "packastack.build.tarball.cache_tarball"
                ):
                    path, _sig_verified, _sig_warn = fetch_release_tarball(
                        upstream=upstream,
                        upstream_config=upstream_config,
                        pkg_repo=tmp_path,
                        workspace=tmp_path,
                        provenance=provenance,
                        offline=False,
                        project_key="test",
                        package_name="python-test",
                        build_type=MagicMock(value="release"),
                        cache_base=tmp_path,
                        force=False,
                        run=MagicMock(),
                    )

        assert path == tarball
        assert provenance.tarball.method == "uscan"

    def test_official_tarball_fallback(self, tmp_path: Path):
        """Test official tarball is tried when uscan fails."""
        provenance = self._make_provenance()
        upstream_config = self._make_upstream_config()
        upstream = MagicMock()
        upstream.version = "1.0.0"
        upstream.tarball_url = "https://example.com/test-1.0.0.tar.gz"

        tarball = tmp_path / "test-1.0.0.tar.gz"
        tarball.touch()

        tarball_result = MagicMock()
        tarball_result.success = True
        tarball_result.path = tarball
        tarball_result.signature_verified = False
        tarball_result.signature_warning = ""

        with patch(
            "packastack.build.tarball.run_uscan"
        ) as mock_uscan:
            mock_uscan.return_value = (False, None, "no watch file")
            with patch(
                "packastack.build.tarball.download_and_verify_tarball"
            ) as mock_dl:
                mock_dl.return_value = tarball_result
                with patch("packastack.build.tarball.activity"):
                    with patch(
                        "packastack.build.tarball.cache_tarball"
                    ):
                        path, _sig_verified, _sig_warn = fetch_release_tarball(
                            upstream=upstream,
                            upstream_config=upstream_config,
                            pkg_repo=tmp_path,
                            workspace=tmp_path,
                            provenance=provenance,
                            offline=False,
                            project_key="test",
                            package_name="python-test",
                            build_type=MagicMock(value="release"),
                            cache_base=tmp_path,
                            force=False,
                            run=MagicMock(),
                        )

        assert path == tarball
        assert provenance.tarball.method == "official"

    def test_pypi_fallback(self, tmp_path: Path):
        """Test PyPI fallback when uscan and official fail."""
        provenance = self._make_provenance()
        # Create prefer list as mock enum-like objects
        pypi_method = MagicMock()
        pypi_method.value = "pypi"
        upstream_config = self._make_upstream_config(prefer=[pypi_method])
        upstream = MagicMock()
        upstream.version = "1.0.0"
        upstream.tarball_url = None  # No official URL

        tarball = tmp_path / "test-1.0.0.tar.gz"
        tarball.touch()

        with patch(
            "packastack.build.tarball.run_uscan"
        ) as mock_uscan:
            mock_uscan.return_value = (False, None, "no watch file")
            with patch(
                "packastack.build.tarball.download_pypi_tarball"
            ) as mock_pypi:
                mock_pypi.return_value = (True, tarball, "")
                with patch("packastack.build.tarball.activity"):
                    with patch(
                        "packastack.build.tarball.cache_tarball"
                    ):
                        path, _sig_verified, _sig_warn = fetch_release_tarball(
                            upstream=upstream,
                            upstream_config=upstream_config,
                            pkg_repo=tmp_path,
                            workspace=tmp_path,
                            provenance=provenance,
                            offline=False,
                            project_key="test",
                            package_name="python-test",
                            build_type=MagicMock(value="release"),
                            cache_base=tmp_path,
                            force=False,
                            run=MagicMock(),
                        )

        assert path == tarball
        assert provenance.tarball.method == "pypi"

    def test_all_methods_fail(self, tmp_path: Path):
        """Test returns error when all methods fail."""
        provenance = self._make_provenance()
        upstream_config = self._make_upstream_config()
        upstream = MagicMock()
        upstream.version = "1.0.0"
        upstream.tarball_url = None  # No official URL

        with patch(
            "packastack.build.tarball.run_uscan"
        ) as mock_uscan:
            mock_uscan.return_value = (False, None, "uscan failed")
            with patch("packastack.build.tarball.activity"):
                path, _sig_verified, sig_warn = fetch_release_tarball(
                    upstream=upstream,
                    upstream_config=upstream_config,
                    pkg_repo=tmp_path,
                    workspace=tmp_path,
                    provenance=provenance,
                    offline=False,
                    project_key="test",
                    package_name="python-test",
                    build_type=MagicMock(value="release"),
                    cache_base=tmp_path,
                    force=False,
                    run=MagicMock(),
                )

        assert path is None
        assert "No tarball could be fetched" in sig_warn


class TestBackwardsCompatibilityAliases:
    """Tests for backwards compatibility aliases."""

    def test_run_uscan_alias(self):
        """Test _run_uscan is alias for run_uscan."""
        assert _run_uscan is run_uscan

    def test_download_pypi_tarball_alias(self):
        """Test _download_pypi_tarball is alias for download_pypi_tarball."""
        assert _download_pypi_tarball is download_pypi_tarball

    def test_download_github_release_tarball_alias(self):
        """Test _download_github_release_tarball is alias."""
        assert _download_github_release_tarball is download_github_release_tarball

    def test_fetch_release_tarball_alias(self):
        """Test _fetch_release_tarball is alias for fetch_release_tarball."""
        assert _fetch_release_tarball is fetch_release_tarball
