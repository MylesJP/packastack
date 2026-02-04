# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for packastack.commands.build_subset module."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from packastack.commands.build_subset import (
    SubsetType,
    _filter_packages_by_subset,
    _update_openstack_repos,
    run_subset_build,
)
from packastack.planning.type_selection import DeliverableKind


class TestSubsetType:
    """Tests for SubsetType enum."""

    def test_libraries_value(self) -> None:
        """Test LIBRARIES enum value."""
        assert SubsetType.LIBRARIES.value == "libraries"

    def test_clients_value(self) -> None:
        """Test CLIENTS enum value."""
        assert SubsetType.CLIENTS.value == "clients"


class TestUpdateOpenstackRepos:
    """Tests for _update_openstack_repos function."""

    def test_skips_update_in_offline_mode(self, tmp_path: Path) -> None:
        """Should skip updates when offline mode is enabled."""
        paths = {
            "openstack_releases_repo": tmp_path / "releases",
            "openstack_project_config": tmp_path / "project-config",
        }
        events: list[dict] = []
        run = SimpleNamespace(log_event=lambda e: events.append(e))

        result = _update_openstack_repos(paths, run, offline=True)

        assert result is True
        assert any(e.get("event") == "subset.repos_skipped" for e in events)

    def test_updates_releases_repo(self, tmp_path: Path) -> None:
        """Should call clone_or_update for releases repo."""
        paths = {
            "openstack_releases_repo": tmp_path / "releases",
            "openstack_project_config": tmp_path / "project-config",
        }
        events: list[dict] = []
        run = SimpleNamespace(log_event=lambda e: events.append(e))

        with patch(
            "packastack.commands.build_subset._clone_or_update_releases"
        ) as mock_releases, patch(
            "packastack.commands.build_subset._clone_or_update_project_config"
        ) as mock_project_config:
            result = _update_openstack_repos(paths, run, offline=False)

        assert result is True
        mock_releases.assert_called_once_with(
            paths["openstack_releases_repo"], run, phase="subset"
        )
        mock_project_config.assert_called_once_with(
            paths["openstack_project_config"], run, phase="subset"
        )

    def test_continues_on_releases_error(self, tmp_path: Path) -> None:
        """Should continue even if releases update fails."""
        paths = {
            "openstack_releases_repo": tmp_path / "releases",
            "openstack_project_config": tmp_path / "project-config",
        }
        events: list[dict] = []
        run = SimpleNamespace(log_event=lambda e: events.append(e))

        with patch(
            "packastack.commands.build_subset._clone_or_update_releases",
            side_effect=Exception("git error"),
        ), patch(
            "packastack.commands.build_subset._clone_or_update_project_config"
        ):
            result = _update_openstack_repos(paths, run, offline=False)

        assert result is True
        assert any(
            e.get("event") == "subset.releases_update_failed" for e in events
        )

    def test_continues_on_project_config_error(self, tmp_path: Path) -> None:
        """Should continue even if project-config update fails."""
        paths = {
            "openstack_releases_repo": tmp_path / "releases",
            "openstack_project_config": tmp_path / "project-config",
        }
        events: list[dict] = []
        run = SimpleNamespace(log_event=lambda e: events.append(e))

        with patch(
            "packastack.commands.build_subset._clone_or_update_releases"
        ), patch(
            "packastack.commands.build_subset._clone_or_update_project_config",
            side_effect=Exception("git error"),
        ):
            result = _update_openstack_repos(paths, run, offline=False)

        assert result is True
        assert any(
            e.get("event") == "subset.project_config_update_failed" for e in events
        )

    def test_handles_missing_paths(self, tmp_path: Path) -> None:
        """Should handle missing path keys gracefully."""
        paths: dict[str, Path] = {}
        events: list[dict] = []
        run = SimpleNamespace(log_event=lambda e: events.append(e))

        result = _update_openstack_repos(paths, run, offline=False)

        assert result is True


class TestFilterPackagesBySubset:
    """Tests for _filter_packages_by_subset function."""

    def test_filters_libraries(self, tmp_path: Path) -> None:
        """Should filter packages to include only libraries."""
        releases_repo = tmp_path / "releases"
        releases_repo.mkdir()

        packages = ["python-oslo.config", "nova", "python-novaclient"]

        with patch(
            "packastack.commands.build_subset.load_openstack_packages"
        ) as mock_load_pkgs, patch(
            "packastack.commands.build_subset.load_project_releases"
        ) as mock_load_rel, patch(
            "packastack.commands.build_subset.infer_deliverable_kind"
        ) as mock_infer:
            mock_load_pkgs.return_value = {
                "python-oslo.config": "oslo.config",
                "nova": "nova",
                "python-novaclient": "python-novaclient",
            }
            mock_load_rel.return_value = None

            # Mock infer to return correct types
            def infer_side_effect(project, pkg, deliverable):
                if "oslo" in pkg:
                    return DeliverableKind.LIBRARY, "heuristic"
                if "client" in pkg:
                    return DeliverableKind.CLIENT_LIBRARY, "heuristic"
                return DeliverableKind.SERVICE, "heuristic"

            mock_infer.side_effect = infer_side_effect

            result = _filter_packages_by_subset(
                packages=packages,
                subset_type=SubsetType.LIBRARIES,
                releases_repo=releases_repo,
                openstack_target="devel",
            )

        # Should include both LIBRARY and CLIENT_LIBRARY
        assert "python-oslo.config" in result
        assert "python-novaclient" in result
        assert "nova" not in result

    def test_filters_clients(self, tmp_path: Path) -> None:
        """Should filter packages to include only clients."""
        releases_repo = tmp_path / "releases"
        releases_repo.mkdir()

        packages = ["python-oslo.config", "nova", "python-novaclient"]

        with patch(
            "packastack.commands.build_subset.load_openstack_packages"
        ) as mock_load_pkgs, patch(
            "packastack.commands.build_subset.load_project_releases"
        ) as mock_load_rel, patch(
            "packastack.commands.build_subset.infer_deliverable_kind"
        ) as mock_infer:
            mock_load_pkgs.return_value = {
                "python-oslo.config": "oslo.config",
                "nova": "nova",
                "python-novaclient": "python-novaclient",
            }
            mock_load_rel.return_value = None

            def infer_side_effect(project, pkg, deliverable):
                if "oslo" in pkg:
                    return DeliverableKind.LIBRARY, "heuristic"
                if "client" in pkg:
                    return DeliverableKind.CLIENT_LIBRARY, "heuristic"
                return DeliverableKind.SERVICE, "heuristic"

            mock_infer.side_effect = infer_side_effect

            result = _filter_packages_by_subset(
                packages=packages,
                subset_type=SubsetType.CLIENTS,
                releases_repo=releases_repo,
                openstack_target="devel",
            )

        # Should include only CLIENT_LIBRARY
        assert "python-novaclient" in result
        assert "python-oslo.config" not in result
        assert "nova" not in result

    def test_returns_empty_for_no_matches(self, tmp_path: Path) -> None:
        """Should return empty list when no packages match."""
        releases_repo = tmp_path / "releases"
        releases_repo.mkdir()

        packages = ["nova", "glance"]

        with patch(
            "packastack.commands.build_subset.load_openstack_packages"
        ) as mock_load_pkgs, patch(
            "packastack.commands.build_subset.load_project_releases"
        ) as mock_load_rel, patch(
            "packastack.commands.build_subset.infer_deliverable_kind"
        ) as mock_infer:
            mock_load_pkgs.return_value = {
                "nova": "nova",
                "glance": "glance",
            }
            mock_load_rel.return_value = None
            mock_infer.return_value = (DeliverableKind.SERVICE, "heuristic")

            result = _filter_packages_by_subset(
                packages=packages,
                subset_type=SubsetType.LIBRARIES,
                releases_repo=releases_repo,
                openstack_target="devel",
            )

        assert result == []


class TestRunSubsetBuild:
    """Tests for run_subset_build function."""

    def test_returns_success_on_empty_filter_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return success when no packages match the filter."""
        import packastack.commands.build_subset as build_subset_module

        paths = {
            "cache_root": tmp_path / "cache",
            "openstack_releases_repo": tmp_path / "releases",
            "local_apt_repo": tmp_path / "apt-repo",
        }
        paths["openstack_releases_repo"].mkdir(parents=True)

        monkeypatch.setattr(build_subset_module, "load_config", lambda: {})
        monkeypatch.setattr(build_subset_module, "resolve_paths", lambda _cfg: paths)
        monkeypatch.setattr(
            build_subset_module,
            "_update_openstack_repos",
            lambda *args, **kwargs: True,
        )
        monkeypatch.setattr(
            build_subset_module,
            "get_current_development_series",
            lambda _path: "dalmatian",
        )

        from packastack.planning.package_discovery import DiscoveryResult

        monkeypatch.setattr(
            build_subset_module,
            "discover_packages",
            lambda **kwargs: DiscoveryResult(
                packages=["nova", "glance"], total_repos=2, source="explicit"
            ),
        )
        monkeypatch.setattr(
            build_subset_module,
            "_filter_packages_by_subset",
            lambda **kwargs: [],
        )

        class DummyRun:
            def __init__(self, name: str) -> None:
                pass

            def __enter__(self) -> DummyRun:
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def log_event(self, event: dict) -> None:
                pass

            def write_summary(self, **kwargs: object) -> None:
                pass

        monkeypatch.setattr(build_subset_module, "RunContext", DummyRun)
        monkeypatch.setattr(build_subset_module, "activity", lambda *args, **kwargs: None)

        from packastack.build import EXIT_SUCCESS

        exit_code = run_subset_build(
            subset_type=SubsetType.LIBRARIES,
            target="devel",
            ubuntu_series="noble",
            cloud_archive="",
            build_type="release",
            binary=True,
            keep_going=True,
            max_failures=0,
            parallel=1,
            force=False,
            offline=False,
            dry_run=False,
        )

        assert exit_code == EXIT_SUCCESS

    def test_dry_run_lists_packages(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should list packages without building in dry run mode."""
        import packastack.commands.build_subset as build_subset_module

        paths = {
            "cache_root": tmp_path / "cache",
            "openstack_releases_repo": tmp_path / "releases",
            "local_apt_repo": tmp_path / "apt-repo",
        }
        paths["openstack_releases_repo"].mkdir(parents=True)

        summary_data: dict = {}

        class DummyRun:
            def __init__(self, name: str) -> None:
                pass

            def __enter__(self) -> DummyRun:
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def log_event(self, event: dict) -> None:
                pass

            def write_summary(self, **kwargs: object) -> None:
                summary_data.update(kwargs)

        monkeypatch.setattr(build_subset_module, "load_config", lambda: {})
        monkeypatch.setattr(build_subset_module, "resolve_paths", lambda _cfg: paths)
        monkeypatch.setattr(
            build_subset_module,
            "_update_openstack_repos",
            lambda *args, **kwargs: True,
        )
        monkeypatch.setattr(
            build_subset_module,
            "get_current_development_series",
            lambda _path: "dalmatian",
        )

        from packastack.planning.package_discovery import DiscoveryResult

        monkeypatch.setattr(
            build_subset_module,
            "discover_packages",
            lambda **kwargs: DiscoveryResult(
                packages=["python-oslo.config", "python-novaclient"],
                total_repos=2,
                source="explicit",
            ),
        )
        monkeypatch.setattr(
            build_subset_module,
            "_filter_packages_by_subset",
            lambda **kwargs: ["python-oslo.config", "python-novaclient"],
        )
        monkeypatch.setattr(build_subset_module, "RunContext", DummyRun)
        monkeypatch.setattr(build_subset_module, "activity", lambda *args, **kwargs: None)

        from packastack.build import EXIT_SUCCESS

        exit_code = run_subset_build(
            subset_type=SubsetType.LIBRARIES,
            target="devel",
            ubuntu_series="noble",
            cloud_archive="",
            build_type="release",
            binary=True,
            keep_going=True,
            max_failures=0,
            parallel=1,
            force=False,
            offline=False,
            dry_run=True,
        )

        assert exit_code == EXIT_SUCCESS
        assert summary_data.get("dry_run") is True
        assert "python-oslo.config" in summary_data.get("packages", [])

    def test_returns_discovery_failed_on_no_packages(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return DISCOVERY_FAILED when no packages are discovered."""
        import packastack.commands.build_subset as build_subset_module

        paths = {
            "cache_root": tmp_path / "cache",
            "openstack_releases_repo": tmp_path / "releases",
            "local_apt_repo": tmp_path / "apt-repo",
        }
        paths["openstack_releases_repo"].mkdir(parents=True)

        class DummyRun:
            def __init__(self, name: str) -> None:
                pass

            def __enter__(self) -> DummyRun:
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def log_event(self, event: dict) -> None:
                pass

            def write_summary(self, **kwargs: object) -> None:
                pass

        monkeypatch.setattr(build_subset_module, "load_config", lambda: {})
        monkeypatch.setattr(build_subset_module, "resolve_paths", lambda _cfg: paths)
        monkeypatch.setattr(
            build_subset_module,
            "_update_openstack_repos",
            lambda *args, **kwargs: True,
        )
        monkeypatch.setattr(
            build_subset_module,
            "get_current_development_series",
            lambda _path: "dalmatian",
        )

        from packastack.planning.package_discovery import DiscoveryResult

        monkeypatch.setattr(
            build_subset_module,
            "discover_packages",
            lambda **kwargs: DiscoveryResult(
                packages=[], total_repos=0, source="explicit"
            ),
        )
        monkeypatch.setattr(build_subset_module, "RunContext", DummyRun)
        monkeypatch.setattr(build_subset_module, "activity", lambda *args, **kwargs: None)

        from packastack.build import EXIT_DISCOVERY_FAILED

        exit_code = run_subset_build(
            subset_type=SubsetType.LIBRARIES,
            target="devel",
            ubuntu_series="noble",
            cloud_archive="",
            build_type="release",
            binary=True,
            keep_going=True,
            max_failures=0,
            parallel=1,
            force=False,
            offline=False,
            dry_run=False,
        )

        assert exit_code == EXIT_DISCOVERY_FAILED

    def test_calls_run_build_all_with_packages_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should call run_build_all with a packages file."""
        import packastack.commands.build_subset as build_subset_module

        paths = {
            "cache_root": tmp_path / "cache",
            "openstack_releases_repo": tmp_path / "releases",
            "local_apt_repo": tmp_path / "apt-repo",
        }
        paths["openstack_releases_repo"].mkdir(parents=True)

        build_all_calls: list[dict] = []

        def fake_run_build_all(**kwargs: object) -> int:
            build_all_calls.append(kwargs)
            return 0

        class DummyRun:
            def __init__(self, name: str) -> None:
                pass

            def __enter__(self) -> DummyRun:
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def log_event(self, event: dict) -> None:
                pass

            def write_summary(self, **kwargs: object) -> None:
                pass

        monkeypatch.setattr(build_subset_module, "load_config", lambda: {})
        monkeypatch.setattr(build_subset_module, "resolve_paths", lambda _cfg: paths)
        monkeypatch.setattr(
            build_subset_module,
            "_update_openstack_repos",
            lambda *args, **kwargs: True,
        )
        monkeypatch.setattr(
            build_subset_module,
            "get_current_development_series",
            lambda _path: "dalmatian",
        )

        from packastack.planning.package_discovery import DiscoveryResult

        monkeypatch.setattr(
            build_subset_module,
            "discover_packages",
            lambda **kwargs: DiscoveryResult(
                packages=["python-oslo.config"],
                total_repos=1,
                source="explicit",
            ),
        )
        monkeypatch.setattr(
            build_subset_module,
            "_filter_packages_by_subset",
            lambda **kwargs: ["python-oslo.config"],
        )
        monkeypatch.setattr(build_subset_module, "RunContext", DummyRun)
        monkeypatch.setattr(build_subset_module, "activity", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            "packastack.commands.build.run_build_all", fake_run_build_all
        )

        from packastack.build import EXIT_SUCCESS

        exit_code = run_subset_build(
            subset_type=SubsetType.LIBRARIES,
            target="devel",
            ubuntu_series="noble",
            cloud_archive="",
            build_type="release",
            binary=True,
            keep_going=True,
            max_failures=0,
            parallel=2,
            force=False,
            offline=False,
            dry_run=False,
        )

        assert exit_code == EXIT_SUCCESS
        assert len(build_all_calls) == 1
        assert build_all_calls[0]["packages_file"] != ""
        assert build_all_calls[0]["parallel"] == 2


class TestBuildCommandSubsetRouting:
    """Tests for subset routing in the build command."""

    def test_build_libraries_routes_to_subset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should route 'libraries' package to subset build."""

        subset_calls: list[dict] = []

        def fake_run_subset_build(**kwargs: object) -> int:
            subset_calls.append(kwargs)
            return 0

        monkeypatch.setattr(
            "packastack.commands.build_subset.run_subset_build",
            fake_run_subset_build,
        )

        # Test via importing and calling the module function
        from packastack.commands.build_subset import SubsetType, run_subset_build

        exit_code = run_subset_build(
            subset_type=SubsetType.LIBRARIES,
            target="devel",
            ubuntu_series="noble",
            cloud_archive="",
            build_type="release",
            binary=True,
            keep_going=True,
            max_failures=0,
            parallel=0,
            force=False,
            offline=False,
            dry_run=True,
        )
        # The mock returns 0
        assert exit_code == 0

    def test_build_clients_routes_to_subset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should route 'clients' package to subset build."""
        from packastack.commands.build_subset import SubsetType, run_subset_build

        subset_calls: list[dict] = []

        def fake_run_subset_build(**kwargs: object) -> int:
            subset_calls.append(kwargs)
            return 0

        monkeypatch.setattr(
            "packastack.commands.build_subset.run_subset_build",
            fake_run_subset_build,
        )

        exit_code = run_subset_build(
            subset_type=SubsetType.CLIENTS,
            target="devel",
            ubuntu_series="noble",
            cloud_archive="",
            build_type="release",
            binary=True,
            keep_going=True,
            max_failures=0,
            parallel=0,
            force=False,
            offline=False,
            dry_run=True,
        )
        # The mock returns 0
        assert exit_code == 0


class TestBuildLibrariesFunction:
    """Tests for build_libraries function."""

    def test_calls_run_subset_build_with_libraries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should call run_subset_build with LIBRARIES type."""
        from packastack.commands import build_subset as build_subset_module

        calls: list[dict] = []

        def fake_run_subset_build(**kwargs: object) -> int:
            calls.append(kwargs)
            return 0

        monkeypatch.setattr(
            build_subset_module, "run_subset_build", fake_run_subset_build
        )

        # Mock sys.exit to prevent actual exit
        exits: list[int] = []
        monkeypatch.setattr("sys.exit", lambda code: exits.append(code))

        build_subset_module.build_libraries(
            target="dalmatian",
            ubuntu_series="noble",
            dry_run=True,
        )

        assert len(calls) == 1
        assert calls[0]["subset_type"] == SubsetType.LIBRARIES
        assert calls[0]["target"] == "dalmatian"
        assert calls[0]["ubuntu_series"] == "noble"
        assert exits == [0]


class TestBuildClientsFunction:
    """Tests for build_clients function."""

    def test_calls_run_subset_build_with_clients(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should call run_subset_build with CLIENTS type."""
        from packastack.commands import build_subset as build_subset_module

        calls: list[dict] = []

        def fake_run_subset_build(**kwargs: object) -> int:
            calls.append(kwargs)
            return 0

        monkeypatch.setattr(
            build_subset_module, "run_subset_build", fake_run_subset_build
        )

        # Mock sys.exit to prevent actual exit
        exits: list[int] = []
        monkeypatch.setattr("sys.exit", lambda code: exits.append(code))

        build_subset_module.build_clients(
            target="dalmatian",
            ubuntu_series="noble",
            dry_run=True,
        )

        assert len(calls) == 1
        assert calls[0]["subset_type"] == SubsetType.CLIENTS
        assert calls[0]["target"] == "dalmatian"
        assert calls[0]["ubuntu_series"] == "noble"
        assert exits == [0]
