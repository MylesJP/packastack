# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for packastack.upstream.pkg_scripts module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from packastack.upstream.pkg_scripts import (
    MANAGED_PACKAGES_FILENAME,
    PKG_SCRIPTS_BASE_URL,
    fetch_managed_packages,
    fetch_package_list,
    load_managed_packages,
    refresh_managed_packages,
    save_managed_packages,
)


class TestFetchPackageList:
    """Tests for fetch_package_list function."""

    def test_parses_simple_list(self) -> None:
        """Test parsing a simple package list."""
        content = b"nova\nglance\nkeystone\n"

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = content
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            packages = fetch_package_list("http://example.com/packages")

        assert packages == ["nova", "glance", "keystone"]

    def test_strips_whitespace(self) -> None:
        """Test that whitespace is stripped."""
        content = b"  nova  \n  glance\t\nkeystone  \n"

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = content
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            packages = fetch_package_list("http://example.com/packages")

        assert packages == ["nova", "glance", "keystone"]

    def test_skips_empty_lines(self) -> None:
        """Test that empty lines are skipped."""
        content = b"nova\n\n\nglance\n"

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = content
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            packages = fetch_package_list("http://example.com/packages")

        assert packages == ["nova", "glance"]

    def test_skips_comments(self) -> None:
        """Test that comment lines are skipped."""
        content = b"# This is a comment\nnova\n# Another comment\nglance\n"

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = content
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            packages = fetch_package_list("http://example.com/packages")

        assert packages == ["nova", "glance"]


class TestFetchManagedPackages:
    """Tests for fetch_managed_packages function."""

    def test_combines_multiple_files(self) -> None:
        """Test that packages from multiple files are combined."""
        responses = {
            f"{PKG_SCRIPTS_BASE_URL}/current-projects": b"nova\nglance\n",
            f"{PKG_SCRIPTS_BASE_URL}/dependencies": b"python-novaclient\npython-oslo.config\n",
        }

        def mock_urlopen(url: str, timeout: int = 30) -> MagicMock:
            mock_response = MagicMock()
            mock_response.read.return_value = responses.get(url, b"")
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = MagicMock(return_value=False)
            return mock_response

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            packages, errors = fetch_managed_packages()

        assert sorted(packages) == ["glance", "nova", "python-novaclient", "python-oslo.config"]
        assert errors == []

    def test_deduplicates_packages(self) -> None:
        """Test that duplicate packages are deduplicated."""
        responses = {
            f"{PKG_SCRIPTS_BASE_URL}/current-projects": b"nova\nglance\n",
            f"{PKG_SCRIPTS_BASE_URL}/dependencies": b"nova\npython-novaclient\n",
        }

        def mock_urlopen(url: str, timeout: int = 30) -> MagicMock:
            mock_response = MagicMock()
            mock_response.read.return_value = responses.get(url, b"")
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = MagicMock(return_value=False)
            return mock_response

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            packages, errors = fetch_managed_packages()

        # nova should only appear once
        assert packages.count("nova") == 1
        assert sorted(packages) == ["glance", "nova", "python-novaclient"]

    def test_returns_errors_on_failure(self) -> None:
        """Test that fetch errors are returned."""
        def mock_urlopen(url: str, timeout: int = 30) -> None:
            raise Exception("Network error")

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            packages, errors = fetch_managed_packages()

        assert packages == []
        assert len(errors) == 2
        assert "current-projects" in errors[0]
        assert "dependencies" in errors[1]

    def test_partial_failure(self) -> None:
        """Test that partial failures still return successful results."""
        call_count = 0

        def mock_urlopen(url: str, timeout: int = 30) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if "current-projects" in url:
                mock_response = MagicMock()
                mock_response.read.return_value = b"nova\nglance\n"
                mock_response.__enter__ = lambda s: s
                mock_response.__exit__ = MagicMock(return_value=False)
                return mock_response
            raise Exception("Network error")

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            packages, errors = fetch_managed_packages()

        assert sorted(packages) == ["glance", "nova"]
        assert len(errors) == 1
        assert "dependencies" in errors[0]


class TestSaveAndLoadManagedPackages:
    """Tests for save_managed_packages and load_managed_packages functions."""

    def test_save_creates_file(self, tmp_path: Path) -> None:
        """Test that save creates the managed packages file."""
        packages = ["nova", "glance", "keystone"]

        file_path = save_managed_packages(packages, tmp_path)

        assert file_path.exists()
        assert file_path.name == MANAGED_PACKAGES_FILENAME

    def test_save_writes_packages(self, tmp_path: Path) -> None:
        """Test that packages are written correctly."""
        packages = ["nova", "glance", "keystone"]

        file_path = save_managed_packages(packages, tmp_path)
        content = file_path.read_text()

        assert content == "nova\nglance\nkeystone\n"

    def test_save_creates_directory(self, tmp_path: Path) -> None:
        """Test that save creates parent directories."""
        packages = ["nova"]
        nested_dir = tmp_path / "a" / "b" / "c"

        file_path = save_managed_packages(packages, nested_dir)

        assert file_path.exists()
        assert nested_dir.exists()

    def test_save_empty_list(self, tmp_path: Path) -> None:
        """Test saving an empty package list."""
        file_path = save_managed_packages([], tmp_path)

        assert file_path.exists()
        assert file_path.read_text() == ""

    def test_load_returns_packages(self, tmp_path: Path) -> None:
        """Test loading packages from file."""
        packages = ["nova", "glance", "keystone"]
        save_managed_packages(packages, tmp_path)

        loaded = load_managed_packages(tmp_path)

        assert loaded == packages

    def test_load_missing_file(self, tmp_path: Path) -> None:
        """Test loading when file doesn't exist."""
        loaded = load_managed_packages(tmp_path)

        assert loaded == []

    def test_load_skips_empty_lines(self, tmp_path: Path) -> None:
        """Test that empty lines are skipped when loading."""
        file_path = tmp_path / MANAGED_PACKAGES_FILENAME
        file_path.write_text("nova\n\n\nglance\n  \n")

        loaded = load_managed_packages(tmp_path)

        assert loaded == ["nova", "glance"]

    def test_load_skips_comments(self, tmp_path: Path) -> None:
        """Test that comments are skipped when loading."""
        file_path = tmp_path / MANAGED_PACKAGES_FILENAME
        file_path.write_text("# Comment\nnova\n# Another\nglance\n")

        loaded = load_managed_packages(tmp_path)

        assert loaded == ["nova", "glance"]


class TestRefreshManagedPackages:
    """Tests for refresh_managed_packages function."""

    def test_offline_mode_uses_cache(self, tmp_path: Path) -> None:
        """Test that offline mode uses cached data."""
        # Pre-populate cache
        save_managed_packages(["nova", "glance"], tmp_path)

        packages, errors = refresh_managed_packages(tmp_path, offline=True)

        assert packages == ["nova", "glance"]
        assert errors == []

    def test_offline_mode_returns_empty_when_no_cache(self, tmp_path: Path) -> None:
        """Test that offline mode returns empty when no cache exists."""
        packages, errors = refresh_managed_packages(tmp_path, offline=True)

        assert packages == []
        assert errors == []

    def test_online_fetches_and_saves(self, tmp_path: Path) -> None:
        """Test that online mode fetches and saves packages."""
        responses = {
            f"{PKG_SCRIPTS_BASE_URL}/current-projects": b"nova\n",
            f"{PKG_SCRIPTS_BASE_URL}/dependencies": b"python-novaclient\n",
        }

        def mock_urlopen(url: str, timeout: int = 30) -> MagicMock:
            mock_response = MagicMock()
            mock_response.read.return_value = responses.get(url, b"")
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = MagicMock(return_value=False)
            return mock_response

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            packages, errors = refresh_managed_packages(tmp_path)

        assert sorted(packages) == ["nova", "python-novaclient"]
        assert errors == []

        # Verify file was saved
        cached = load_managed_packages(tmp_path)
        assert sorted(cached) == ["nova", "python-novaclient"]

    def test_logs_to_run_context(self, tmp_path: Path) -> None:
        """Test that events are logged to RunContext."""
        responses = {
            f"{PKG_SCRIPTS_BASE_URL}/current-projects": b"nova\n",
            f"{PKG_SCRIPTS_BASE_URL}/dependencies": b"glance\n",
        }

        def mock_urlopen(url: str, timeout: int = 30) -> MagicMock:
            mock_response = MagicMock()
            mock_response.read.return_value = responses.get(url, b"")
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = MagicMock(return_value=False)
            return mock_response

        mock_run = MagicMock()

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            refresh_managed_packages(tmp_path, run=mock_run)

        # Verify log_event was called
        mock_run.log_event.assert_called()
        events = [call[0][0] for call in mock_run.log_event.call_args_list]
        assert any(e.get("event") == "pkg_scripts.saved" for e in events)
