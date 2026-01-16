# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for packastack.planning.package_discovery module."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from packastack.planning.package_discovery import (
    EXCLUDED_REPOS,
    DiscoveryResult,
    _extract_package_from_repo,
    _get_all_releases_packages,
    _get_known_openstack_packages,
    _get_packages_from_releases_repo,
    _get_upstreams_registry,
    _is_excluded_repo,
    _is_valid_packaging_repo,
    discover_packages,
    discover_packages_from_cache,
    discover_packages_from_launchpad,
    discover_packages_from_list,
    get_releases_libraries_and_services,
    read_packages_from_file,
)


class TestIsExcludedRepo:
    """Tests for _is_excluded_repo function."""

    def test_excluded_known_repos(self) -> None:
        """Test that known non-package repos are excluded."""
        for name in EXCLUDED_REPOS:
            excluded, reason = _is_excluded_repo(name)
            assert excluded is True
            assert "known non-package repo" in reason

    def test_excluded_charm_suffix(self) -> None:
        """Test that charm repos are excluded."""
        excluded, reason = _is_excluded_repo("nova-charm")
        assert excluded is True
        assert "pattern" in reason

    def test_excluded_operator_suffix(self) -> None:
        """Test that operator repos are excluded."""
        excluded, reason = _is_excluded_repo("keystone-operator")
        assert excluded is True
        assert "pattern" in reason

    def test_excluded_hidden_dirs(self) -> None:
        """Test that hidden directories are excluded."""
        excluded, _reason = _is_excluded_repo(".git")
        assert excluded is True

    def test_not_excluded_normal_package(self) -> None:
        """Test that normal packages are not excluded."""
        excluded, reason = _is_excluded_repo("nova")
        assert excluded is False
        assert reason == ""

    def test_not_excluded_python_package(self) -> None:
        """Test that python packages are not excluded."""
        excluded, _reason = _is_excluded_repo("python-oslo.config")
        assert excluded is False


class TestIsValidPackagingRepo:
    """Tests for _is_valid_packaging_repo function."""

    def test_valid_with_control(self, tmp_path: Path) -> None:
        """Test repo with debian/control is valid."""
        (tmp_path / "debian").mkdir()
        (tmp_path / "debian" / "control").write_text("Source: test\n")

        assert _is_valid_packaging_repo(tmp_path) is True

    def test_invalid_without_control(self, tmp_path: Path) -> None:
        """Test repo without debian/control is invalid."""
        assert _is_valid_packaging_repo(tmp_path) is False

    def test_invalid_with_empty_debian_dir(self, tmp_path: Path) -> None:
        """Test repo with empty debian dir is invalid."""
        (tmp_path / "debian").mkdir()
        assert _is_valid_packaging_repo(tmp_path) is False


class TestDiscoverPackagesFromCache:
    """Tests for discover_packages_from_cache function."""

    def test_empty_cache(self, tmp_path: Path) -> None:
        """Test discovery from empty cache."""
        result = discover_packages_from_cache(tmp_path)

        assert result.packages == []
        assert result.total_repos == 0
        assert result.source == "cache"

    def test_discovers_valid_packages(self, tmp_path: Path) -> None:
        """Test discovery finds valid packaging repos."""
        # Create valid repos
        for name in ["nova", "glance", "keystone"]:
            repo = tmp_path / name
            (repo / "debian").mkdir(parents=True)
            (repo / "debian" / "control").write_text(f"Source: {name}\n")

        result = discover_packages_from_cache(tmp_path)

        assert sorted(result.packages) == ["glance", "keystone", "nova"]
        assert result.total_repos == 3
        assert result.source == "cache"

    def test_filters_excluded_repos(self, tmp_path: Path) -> None:
        """Test that excluded repos are filtered."""
        # Create a valid repo
        nova = tmp_path / "nova"
        (nova / "debian").mkdir(parents=True)
        (nova / "debian" / "control").write_text("Source: nova\n")

        # Create an excluded repo
        charm = tmp_path / "nova-charm"
        (charm / "debian").mkdir(parents=True)
        (charm / "debian" / "control").write_text("Source: nova-charm\n")

        result = discover_packages_from_cache(tmp_path)

        assert result.packages == ["nova"]
        assert "nova-charm" in result.filtered_repos

    def test_filters_repos_without_control(self, tmp_path: Path) -> None:
        """Test that repos without debian/control are filtered."""
        # Create a valid repo
        nova = tmp_path / "nova"
        (nova / "debian").mkdir(parents=True)
        (nova / "debian" / "control").write_text("Source: nova\n")

        # Create an incomplete repo
        glance = tmp_path / "glance"
        glance.mkdir()

        result = discover_packages_from_cache(tmp_path)

        assert result.packages == ["nova"]
        assert "glance" in result.filtered_repos
        assert "missing debian/control" in result.filtered_repos["glance"]

    def test_missing_cache_dir(self, tmp_path: Path) -> None:
        """Test error when cache directory doesn't exist."""
        result = discover_packages_from_cache(tmp_path / "nonexistent")

        assert result.packages == []
        assert len(result.errors) == 1
        assert "does not exist" in result.errors[0]

    def test_cache_not_a_directory(self, tmp_path: Path) -> None:
        """Test error when cache path is a file."""
        file_path = tmp_path / "file"
        file_path.write_text("not a dir")

        result = discover_packages_from_cache(file_path)

        assert result.packages == []
        assert len(result.errors) == 1
        assert "not a directory" in result.errors[0]

    def test_ignores_non_directory_entries(self, tmp_path: Path) -> None:
        """Test that non-directory entries are ignored."""
        (tmp_path / "nova").mkdir()
        (tmp_path / "nova" / "debian").mkdir()
        (tmp_path / "nova" / "debian" / "control").write_text("Source: nova\n")
        (tmp_path / "README.txt").write_text("not a repo")

        result = discover_packages_from_cache(tmp_path)

        assert result.packages == ["nova"]
        assert "README.txt" not in result.filtered_repos


class TestDiscoverPackagesFromList:
    """Tests for discover_packages_from_list function."""

    def test_explicit_list(self) -> None:
        """Test discovery from explicit list."""
        packages = ["nova", "glance", "keystone"]
        result = discover_packages_from_list(packages)

        assert result.packages == packages
        assert result.total_repos == 3
        assert result.source == "explicit"

    def test_filters_excluded_from_list(self) -> None:
        """Test that excluded packages are filtered from list."""
        packages = ["nova", "nova-charm", "packaging-guide"]
        result = discover_packages_from_list(packages)

        assert result.packages == ["nova"]
        assert "nova-charm" in result.filtered_repos
        assert "packaging-guide" in result.filtered_repos

    def test_validates_against_cache(self, tmp_path: Path) -> None:
        """Test validation against cache directory."""
        # Create only nova in cache
        nova = tmp_path / "nova"
        (nova / "debian").mkdir(parents=True)
        (nova / "debian" / "control").write_text("Source: nova\n")

        packages = ["nova", "glance"]
        result = discover_packages_from_list(packages, cache_dir=tmp_path, validate=True)

        assert result.packages == ["nova"]
        assert "glance" in result.filtered_repos
        assert "not found" in result.filtered_repos["glance"]

    def test_filters_missing_control_in_cache(self, tmp_path: Path) -> None:
        """Test missing debian/control when validating against cache."""
        (tmp_path / "nova").mkdir()

        result = discover_packages_from_list(
            packages=["nova"],
            cache_dir=tmp_path,
            validate=True,
        )

        assert result.packages == []
        assert "nova" in result.filtered_repos
        assert "missing debian/control" in result.filtered_repos["nova"]


class TestReadPackagesFromFile:
    """Tests for read_packages_from_file function."""

    def test_reads_packages(self, tmp_path: Path) -> None:
        """Test reading packages from file."""
        pkg_file = tmp_path / "packages.txt"
        pkg_file.write_text("nova\nglance\nkeystone\n")

        packages = read_packages_from_file(pkg_file)
        assert packages == ["nova", "glance", "keystone"]

    def test_ignores_comments(self, tmp_path: Path) -> None:
        """Test that comments are ignored."""
        pkg_file = tmp_path / "packages.txt"
        pkg_file.write_text("# This is a comment\nnova\n# Another comment\nglance\n")

        packages = read_packages_from_file(pkg_file)
        assert packages == ["nova", "glance"]

    def test_ignores_empty_lines(self, tmp_path: Path) -> None:
        """Test that empty lines are ignored."""
        pkg_file = tmp_path / "packages.txt"
        pkg_file.write_text("nova\n\n\nglance\n  \n")

        packages = read_packages_from_file(pkg_file)
        assert packages == ["nova", "glance"]

    def test_missing_file(self, tmp_path: Path) -> None:
        """Test handling of missing file."""
        packages = read_packages_from_file(tmp_path / "missing.txt")
        assert packages == []


class TestDiscoverPackagesFromLaunchpad:
    """Tests for discover_packages_from_launchpad function."""

    def test_error_without_launchpadlib(self) -> None:
        """Test error when launchpadlib is not available."""
        with patch("packastack.planning.package_discovery.Launchpad", None):
            result = discover_packages_from_launchpad()

        # Should return error when launchpadlib is not available
        assert result.packages == []
        assert "launchpadlib library not available" in result.errors[0]

    def test_filters_excluded_packages(self) -> None:
        """Test that excluded packages are filtered from results."""
        with patch("packastack.planning.package_discovery.Launchpad", None):
            result = discover_packages_from_launchpad()

        # When launchpadlib unavailable, no packages discovered
        assert result.packages == []

    def test_handles_launchpad_connection_error(self) -> None:
        """Test handling of launchpadlib connection errors."""
        mock_login = MagicMock(side_effect=Exception("Connection failed"))

        with patch("packastack.planning.package_discovery.Launchpad") as mock_Launchpad:
            mock_Launchpad.login_anonymously = mock_login
            result = discover_packages_from_launchpad()

        # Should return error when login fails
        assert result.packages == []
        assert "Connection failed" in result.errors[0]

    def test_successful_discovery_with_mocked_launchpad(self, tmp_path: Path) -> None:
        """Test successful discovery with mocked team repos enumeration."""
        recent = datetime.now(UTC)

        # Create mock repos with +source pattern URLs
        mock_nova_repo = MagicMock()
        mock_nova_repo.name = "nova"
        mock_nova_repo.git_https_url = "https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source/nova"
        mock_nova_repo.date_last_modified = recent

        mock_glance_repo = MagicMock()
        mock_glance_repo.name = "glance"
        mock_glance_repo.git_https_url = "https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source/glance"
        mock_glance_repo.date_last_modified = recent

        mock_charm_repo = MagicMock()
        mock_charm_repo.name = "nova-charm"
        mock_charm_repo.git_https_url = "https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source/nova-charm"
        mock_charm_repo.date_last_modified = recent

        mock_lp = MagicMock()
        mock_team = MagicMock()
        mock_lp.people.__getitem__.return_value = mock_team
        mock_lp.git_repositories.getRepositories.return_value = [
            mock_nova_repo, mock_glance_repo, mock_charm_repo
        ]

        with patch("packastack.planning.package_discovery.Launchpad") as mock_Launchpad:
            mock_Launchpad.login_anonymously.return_value = mock_lp
            result = discover_packages_from_launchpad()

        assert "nova" in result.packages
        assert "glance" in result.packages
        # Charm should be filtered out
        assert "nova-charm" not in result.packages
        assert "nova-charm" in result.filtered_repos
        assert result.source == "launchpad"

    def test_caches_launchpad_results(self, tmp_path: Path) -> None:
        """Test that Launchpad results are cached to file."""
        cache_file = tmp_path / "launchpad-repos.json"
        recent = datetime.now(UTC)

        # Create mock repos
        mock_nova_repo = MagicMock()
        mock_nova_repo.name = "nova"
        mock_nova_repo.git_https_url = "https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source/nova"
        mock_nova_repo.date_last_modified = recent

        mock_lp = MagicMock()
        mock_team = MagicMock()
        mock_lp.people.__getitem__.return_value = mock_team
        mock_lp.git_repositories.getRepositories.return_value = [mock_nova_repo]

        with patch("packastack.planning.package_discovery.Launchpad") as mock_Launchpad:
            mock_Launchpad.login_anonymously.return_value = mock_lp
            result = discover_packages_from_launchpad(cache_file=cache_file)

        assert "nova" in result.packages
        assert cache_file.exists()

        # Second call should use cache, not hit Launchpad
        with patch("packastack.planning.package_discovery.Launchpad") as mock_Launchpad:
            mock_Launchpad.login_anonymously.side_effect = Exception("Should not be called")
            result2 = discover_packages_from_launchpad(cache_file=cache_file)

        assert "nova" in result2.packages

    def test_invalid_cache_file_falls_back(self, tmp_path: Path) -> None:
        """Test invalid cache file is ignored."""
        cache_file = tmp_path / "launchpad-repos.json"
        cache_file.write_text("{not-json")

        with patch("packastack.planning.package_discovery.Launchpad", None):
            result = discover_packages_from_launchpad(cache_file=cache_file)

        assert result.packages == []
        assert "launchpadlib library not available" in result.errors[0]

    def test_empty_cache_file_falls_back(self, tmp_path: Path) -> None:
        """Test empty cache does not short-circuit discovery."""
        cache_file = tmp_path / "launchpad-repos.json"
        cache_file.write_text(json.dumps({"packages": [], "total_repos": 0, "filtered_repos": {}}))

        with patch("packastack.planning.package_discovery.Launchpad", None):
            result = discover_packages_from_launchpad(cache_file=cache_file)

        assert result.packages == []
        assert "launchpadlib library not available" in result.errors[0]

    def test_team_fetch_failure(self) -> None:
        """Test failure to fetch Launchpad team."""
        mock_lp = MagicMock()
        mock_lp.people.__getitem__.side_effect = Exception("team missing")

        with patch("packastack.planning.package_discovery.Launchpad") as mock_Launchpad:
            mock_Launchpad.login_anonymously.return_value = mock_lp
            result = discover_packages_from_launchpad()

        assert result.packages == []
        assert "team missing" in result.errors[0]

    def test_repo_enumeration_failure(self) -> None:
        """Test failure while enumerating repositories."""
        mock_lp = MagicMock()
        mock_team = MagicMock()
        mock_lp.people.__getitem__.return_value = mock_team
        mock_lp.git_repositories.getRepositories.side_effect = Exception("enumeration failed")

        with patch("packastack.planning.package_discovery.Launchpad") as mock_Launchpad:
            mock_Launchpad.login_anonymously.return_value = mock_lp
            result = discover_packages_from_launchpad()

        assert result.packages == []
        assert "enumeration failed" in result.errors[0]

    def test_cache_write_failure_is_ignored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test cache write errors are ignored."""
        cache_file = tmp_path / "launchpad-repos.json"
        recent = datetime.now(UTC)

        mock_repo = MagicMock()
        mock_repo.name = "nova"
        mock_repo.git_https_url = "https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source/nova"
        mock_repo.date_last_modified = recent

        mock_lp = MagicMock()
        mock_team = MagicMock()
        mock_lp.people.__getitem__.return_value = mock_team
        mock_lp.git_repositories.getRepositories.return_value = [mock_repo]

        original_write_text = Path.write_text

        def raise_on_cache_write(path: Path, data: str, *args: object, **kwargs: object) -> int:
            if path == cache_file:
                raise OSError("write failed")
            return original_write_text(path, data, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", raise_on_cache_write)

        with patch("packastack.planning.package_discovery.Launchpad") as mock_Launchpad:
            mock_Launchpad.login_anonymously.return_value = mock_lp
            result = discover_packages_from_launchpad(cache_file=cache_file)

        assert "nova" in result.packages

    def test_discovers_packages_from_repo_urls(self) -> None:
        """Test package extraction from various repo URL formats."""
        recent = datetime.now(UTC)

        # Create mock repos with different path formats
        mock_valid_repo = MagicMock()
        mock_valid_repo.name = "keystone"
        mock_valid_repo.git_https_url = "https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source/keystone"
        mock_valid_repo.date_last_modified = recent

        mock_invalid_repo = MagicMock()
        mock_invalid_repo.name = "some-other-repo"
        mock_invalid_repo.git_https_url = "https://git.launchpad.net/~ubuntu-openstack-dev/some-other-repo"
        mock_invalid_repo.date_last_modified = recent

        mock_lp = MagicMock()
        mock_team = MagicMock()
        mock_lp.people.__getitem__.return_value = mock_team
        mock_lp.git_repositories.getRepositories.return_value = [
            mock_valid_repo, mock_invalid_repo
        ]

        with patch("packastack.planning.package_discovery.Launchpad") as mock_Launchpad:
            mock_Launchpad.login_anonymously.return_value = mock_lp
            result = discover_packages_from_launchpad()

        # Only +source repos should be included
        assert "keystone" in result.packages
        assert "some-other-repo" not in result.packages


class TestExtractPackageFromRepo:
    """Tests for _extract_package_from_repo helper."""

    def test_returns_none_for_unmatched_source_path(self) -> None:
        """Test +source path without package returns None."""
        repo_path = "https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source/"
        assert _extract_package_from_repo("repo", repo_path) is None


class TestUpstreamsRegistryHelper:
    """Tests for _get_upstreams_registry helper."""

    def test_returns_none_on_registry_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test RegistryError yields None."""
        from packastack import planning

        def raise_error() -> None:
            raise planning.package_discovery.RegistryError("boom")

        monkeypatch.setattr(planning.package_discovery, "UpstreamsRegistry", raise_error)
        assert _get_upstreams_registry() is None


class TestDiscoverPackages:
    """Tests for discover_packages function."""

    def test_explicit_packages_priority(self, tmp_path: Path) -> None:
        """Test that explicit packages take priority."""
        result = discover_packages(
            explicit_packages=["nova", "glance"],
            offline=True,
        )

        assert result.packages == ["nova", "glance"]
        assert result.source == "explicit"

    def test_packages_file_priority(self, tmp_path: Path) -> None:
        """Test that packages file takes priority over discovery."""
        pkg_file = tmp_path / "packages.txt"
        pkg_file.write_text("nova\nglance\n")

        result = discover_packages(
            packages_file=pkg_file,
            offline=True,
        )

        assert result.packages == ["nova", "glance"]
        assert result.source == "file"

    def test_offline_uses_cache(self, tmp_path: Path) -> None:
        """Test that offline mode uses cache."""
        # Create valid repos in cache
        for name in ["nova", "glance"]:
            repo = tmp_path / name
            (repo / "debian").mkdir(parents=True)
            (repo / "debian" / "control").write_text(f"Source: {name}\n")

        result = discover_packages(
            cache_dir=tmp_path,
            offline=True,
        )

        assert sorted(result.packages) == ["glance", "nova"]
        assert result.source == "cache"

    def test_falls_back_to_cache_on_launchpad_error(self, tmp_path: Path) -> None:
        """Test fallback to cache when launchpad fails."""
        repo = tmp_path / "nova"
        (repo / "debian").mkdir(parents=True)
        (repo / "debian" / "control").write_text("Source: nova\n")

        failure = DiscoveryResult(packages=[], errors=["boom"], source="launchpad")
        with patch(
            "packastack.planning.package_discovery.discover_packages_from_launchpad",
            return_value=failure,
        ):
            result = discover_packages(cache_dir=tmp_path, offline=False)

        assert result.source == "cache"
        assert result.packages == ["nova"]

    def test_no_discovery_method(self) -> None:
        """Test error when no discovery method available."""
        result = discover_packages(offline=True)

        assert result.packages == []
        assert "No discovery method available" in result.errors[0]
        assert result.source == "none"


class TestGetReleasesLibrariesAndServices:
    """Tests for get_releases_libraries_and_services function."""

    def test_filters_to_libraries_and_services(self, tmp_path: Path) -> None:
        """Test that only libraries and services are returned."""
        # Create mock releases directory
        deliverables = tmp_path / "deliverables" / "zed"
        deliverables.mkdir(parents=True)

        # Library deliverable
        lib_yaml = deliverables / "oslo.config.yaml"
        lib_yaml.write_text("type: library\n")

        # Service deliverable
        svc_yaml = deliverables / "nova.yaml"
        svc_yaml.write_text("type: service\n")

        # Other type deliverable (should be excluded)
        other_yaml = deliverables / "puppet-nova.yaml"
        other_yaml.write_text("type: puppet-module\n")

        libs_and_services = get_releases_libraries_and_services(tmp_path)

        assert "oslo.config" in libs_and_services
        assert "nova" in libs_and_services
        assert "puppet-nova" not in libs_and_services

    def test_returns_empty_when_no_deliverables(self, tmp_path: Path) -> None:
        """Test returns empty set when no deliverables exist."""
        result = get_releases_libraries_and_services(tmp_path)
        assert result == set()

    def test_returns_empty_with_no_series_dirs(self, tmp_path: Path) -> None:
        """Test returns empty when no visible series dirs exist."""
        (tmp_path / "deliverables").mkdir()
        (tmp_path / "deliverables" / "_hidden").mkdir()

        result = get_releases_libraries_and_services(tmp_path)
        assert result == set()

    def test_skips_invalid_yaml(self, tmp_path: Path) -> None:
        """Test invalid yaml is ignored."""
        deliverables = tmp_path / "deliverables" / "zed"
        deliverables.mkdir(parents=True)
        (deliverables / "nova.yaml").write_text(": [")

        result = get_releases_libraries_and_services(tmp_path)
        assert result == set()

    def test_skips_non_mapping_yaml(self, tmp_path: Path) -> None:
        """Test non-mapping yaml is ignored."""
        deliverables = tmp_path / "deliverables" / "zed"
        deliverables.mkdir(parents=True)
        (deliverables / "nova.yaml").write_text("- item\n")

        result = get_releases_libraries_and_services(tmp_path)
        assert result == set()

    def test_yaml_disabled_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test yaml missing returns empty set."""
        from packastack.planning import package_discovery

        monkeypatch.setattr(package_discovery, "yaml", None)
        result = get_releases_libraries_and_services(tmp_path)
        assert result == set()


class TestReleasesPackageHelpers:
    """Tests for releases package helper functions."""

    def test_get_packages_from_releases_repo(self, tmp_path: Path) -> None:
        """Test extracting package names across series."""
        deliverables_a = tmp_path / "deliverables" / "zed"
        deliverables_a.mkdir(parents=True)
        (deliverables_a / "nova.yaml").write_text("type: service\n")

        deliverables_b = tmp_path / "deliverables" / "2024.2"
        deliverables_b.mkdir(parents=True)
        (deliverables_b / "keystone.yaml").write_text("type: service\n")

        packages = _get_packages_from_releases_repo(tmp_path)
        assert "nova" in packages
        assert "keystone" in packages

    def test_get_known_openstack_packages(self) -> None:
        """Test known package fallback contains expected values."""
        packages = _get_known_openstack_packages()
        assert "nova" in packages

    def test_all_releases_packages_includes_library_prefix(self, tmp_path: Path) -> None:
        """Test library deliverables include python- prefixed name."""
        deliverables = tmp_path / "deliverables" / "zed"
        deliverables.mkdir(parents=True)
        (deliverables / "oslo.config.yaml").write_text("type: library\n")
        (deliverables / "broken.yaml").write_text(": [")
        (deliverables / "list.yaml").write_text("- item\n")

        packages = _get_all_releases_packages(tmp_path)
        assert "oslo.config" in packages
        assert "python-oslo.config" in packages

    def test_all_releases_packages_yaml_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test yaml missing returns empty set."""
        from packastack.planning import package_discovery

        monkeypatch.setattr(package_discovery, "yaml", None)
        assert _get_all_releases_packages(tmp_path) == set()

    def test_all_releases_packages_no_series_dirs(self, tmp_path: Path) -> None:
        """Test returns empty when no series dirs exist."""
        (tmp_path / "deliverables").mkdir()
        (tmp_path / "deliverables" / ".hidden").mkdir()

        assert _get_all_releases_packages(tmp_path) == set()


class TestCrossReferencePackages:
    """Tests for _cross_reference_packages function."""

    def test_identifies_missing_upstream(self, tmp_path: Path) -> None:
        """Test that packages without upstream registry entries are identified."""
        from packastack.planning.package_discovery import (
            DiscoveryResult,
            _cross_reference_packages,
        )

        # Create a mock releases repo with some deliverables
        deliverables = tmp_path / "deliverables" / "zed"
        deliverables.mkdir(parents=True)
        (deliverables / "keystone.yaml").write_text("type: service\n")
        (deliverables / "nova.yaml").write_text("type: service\n")

        result = DiscoveryResult(
            packages=["keystone", "unknown-package", "nova"],
            source="launchpad",
        )

        # Cross-reference - unknown-package is not in releases or registry
        with patch("packastack.planning.package_discovery._get_upstreams_registry", return_value=None):
            _cross_reference_packages(result, tmp_path)

        # unknown-package has no upstream entry (not in releases)
        assert "unknown-package" in result.missing_upstream

    def test_registry_common_name_resolution(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test registry common names prevent missing-upstream flags."""
        from packastack.planning import package_discovery

        class StubRegistry:
            def __init__(self, known: set[str]):
                self.known = known
                self.calls: list[str] = []

            def has_explicit_entry(self, project: str) -> bool:
                self.calls.append(project)
                return project in self.known

        registry = StubRegistry({"gnocchi", "project-alias"})
        monkeypatch.setattr(package_discovery, "_get_upstreams_registry", lambda: registry)

        result = DiscoveryResult(
            packages=["python-gnocchi", "project-alias", "missing"],
            source="launchpad",
        )

        package_discovery._cross_reference_packages(result, tmp_path)

        assert "missing" in result.missing_upstream
        assert "python-gnocchi" not in result.missing_upstream
        assert "project-alias" not in result.missing_upstream

    def test_identifies_missing_packaging(self, tmp_path: Path) -> None:
        """Test that releases entries without packaging repos are identified."""
        from packastack.planning.package_discovery import (
            DiscoveryResult,
            _cross_reference_packages,
        )

        # Create releases with more deliverables than we have packages for
        deliverables = tmp_path / "deliverables" / "zed"
        deliverables.mkdir(parents=True)
        (deliverables / "keystone.yaml").write_text("type: service\n")
        (deliverables / "nova.yaml").write_text("type: service\n")
        (deliverables / "glance.yaml").write_text("type: service\n")
        (deliverables / "cinder.yaml").write_text("type: service\n")

        result = DiscoveryResult(
            packages=["keystone", "nova"],
            source="launchpad",
        )

        _cross_reference_packages(result, tmp_path)

        # glance and cinder are in releases but not discovered
        assert "glance" in result.missing_packaging
        assert "cinder" in result.missing_packaging
        assert "keystone" not in result.missing_packaging
        assert "nova" not in result.missing_packaging

    def test_empty_inputs(self, tmp_path: Path) -> None:
        """Test with empty inputs."""
        from packastack.planning.package_discovery import (
            DiscoveryResult,
            _cross_reference_packages,
        )

        result = DiscoveryResult(packages=[], source="launchpad")
        _cross_reference_packages(result, tmp_path)

        assert result.missing_upstream == []
        assert result.missing_packaging == []

    def test_no_releases_repo(self) -> None:
        """Test with no releases repo path."""
        from packastack.planning.package_discovery import (
            DiscoveryResult,
            _cross_reference_packages,
        )

        result = DiscoveryResult(packages=["keystone"], source="launchpad")
        _cross_reference_packages(result, None)

        # Should not error, lists remain empty
        assert result.missing_upstream == []
        assert result.missing_packaging == []
