# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for upstream retirement detection."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from packastack.upstream.retirement import (
    MappingConfidence,
    RetirementChecker,
    RetirementInfo,
    RetirementStatus,
    check_retirement,
    find_last_seen_series,
    get_series_order,
    load_project_config,
    map_package_to_upstream,
)


def _write_projects_yaml(root: Path, content: str) -> Path:
    projects_yaml = root / "gerrit" / "projects.yaml"
    projects_yaml.parent.mkdir(parents=True, exist_ok=True)
    projects_yaml.write_text(content)
    return projects_yaml


class TestLoadProjectConfig:
    """Tests for load_project_config."""

    def test_missing_file_returns_error(self, tmp_path: Path) -> None:
        """Should return load_error when projects.yaml is missing."""
        data = load_project_config(tmp_path)

        assert data.load_error
        assert data.projects == {}

    def test_invalid_yaml_returns_error(self, tmp_path: Path) -> None:
        """Should return load_error for invalid YAML."""
        _write_projects_yaml(tmp_path, "invalid: [")

        data = load_project_config(tmp_path)

        assert "YAML parse error" in data.load_error

    def test_non_list_yaml_returns_error(self, tmp_path: Path) -> None:
        """Should return load_error for non-list YAML."""
        _write_projects_yaml(tmp_path, "project: openstack/nova")

        data = load_project_config(tmp_path)

        assert "Expected list format" in data.load_error

    def test_parses_entries(self, tmp_path: Path) -> None:
        """Should parse project entries and detect retirement."""
        _write_projects_yaml(
            tmp_path,
            "- project: openstack/glance\n  description: \"RETIRED: archived\"\n"
            "- project: openstack/nova\n  description: \"Active\"\n",
        )

        data = load_project_config(tmp_path)

        assert not data.load_error
        glance = data.find_project("openstack/glance")
        nova = data.find_project("openstack/nova")
        assert glance is not None and glance.is_retired
        assert nova is not None and not nova.is_retired


class TestMapPackageToUpstream:
    """Tests for map_package_to_upstream."""

    def test_registry_opendev_url(self) -> None:
        """Should parse opendev URLs from registry entries."""
        class FakeRegistry:
            def has_explicit_entry(self, _pkg: str) -> bool:
                return True

            def resolve(self, _pkg: str, openstack_governed: bool = True) -> object:
                config = SimpleNamespace(upstream=SimpleNamespace(url="https://opendev.org/openstack/glance.git"))
                return SimpleNamespace(project="glance", config=config)

        upstream, confidence = map_package_to_upstream("glance", registry=FakeRegistry())

        assert upstream == "openstack/glance"
        assert confidence == MappingConfidence.HIGH

    def test_registry_github_url(self) -> None:
        """Should parse GitHub URLs from registry entries."""
        class FakeRegistry:
            def has_explicit_entry(self, _pkg: str) -> bool:
                return True

            def resolve(self, _pkg: str, openstack_governed: bool = True) -> object:
                config = SimpleNamespace(upstream=SimpleNamespace(url="https://github.com/gnocchixyz/gnocchi.git"))
                return SimpleNamespace(project="gnocchi", config=config)

        upstream, confidence = map_package_to_upstream("gnocchi", registry=FakeRegistry())

        assert upstream == "github:gnocchixyz/gnocchi"
        assert confidence == MappingConfidence.HIGH

    def test_registry_project_fallback(self) -> None:
        """Should fall back to openstack/<project> when URL missing."""
        class FakeRegistry:
            def has_explicit_entry(self, _pkg: str) -> bool:
                return True

            def resolve(self, _pkg: str, openstack_governed: bool = True) -> object:
                config = SimpleNamespace(upstream=SimpleNamespace(url=""))
                return SimpleNamespace(project="nova", config=config)

        upstream, confidence = map_package_to_upstream("nova", registry=FakeRegistry())

        assert upstream == "openstack/nova"
        assert confidence == MappingConfidence.HIGH

    def test_releases_deliverable_fallback(self) -> None:
        """Should map to openstack/<name> for known deliverables."""
        upstream, confidence = map_package_to_upstream(
            "nova",
            registry=None,
            releases_deliverables={"nova"},
        )

        assert upstream == "openstack/nova"
        assert confidence == MappingConfidence.MEDIUM

    @pytest.mark.parametrize(
        ("source_package", "expected", "confidence"),
        [
            ("python-novaclient", "openstack/python-novaclient", MappingConfidence.MEDIUM),
            ("oslo.config", "openstack/oslo.config", MappingConfidence.MEDIUM),
            ("manila-dashboard", "openstack/manila-dashboard", MappingConfidence.MEDIUM),
            ("foo-tempest-plugin", "openstack/foo-tempest-plugin", MappingConfidence.MEDIUM),
            ("mystery", "openstack/mystery", MappingConfidence.LOW),
        ],
    )
    def test_heuristic_fallbacks(
        self,
        source_package: str,
        expected: str,
        confidence: MappingConfidence,
    ) -> None:
        """Should apply heuristic mapping for common patterns."""
        upstream, conf = map_package_to_upstream(source_package, registry=None, releases_deliverables=None)

        assert upstream == expected
        assert conf == confidence


class TestReleasesInference:
    """Tests for releases inference helpers."""

    def test_get_series_order_sorts_known_first(self, tmp_path: Path) -> None:
        """Should sort known series and place unknown last."""
        deliverables = tmp_path / "deliverables"
        for name in ["caracal", "zed", "unknown"]:
            (deliverables / name).mkdir(parents=True)

        order = get_series_order(tmp_path)

        assert order[0] == "zed"
        assert order[1] == "caracal"
        assert order[-1] == "unknown"

    def test_find_last_seen_series(self, tmp_path: Path) -> None:
        """Should find last seen series and cycles since."""
        deliverables = tmp_path / "deliverables"
        for name in ["zed", "antelope", "bobcat", "caracal", "dalmatian"]:
            (deliverables / name).mkdir(parents=True)
        (deliverables / "zed" / "nova.yaml").write_text("name: nova\n")

        last_seen, cycles_since = find_last_seen_series("nova", tmp_path, "dalmatian")

        assert last_seen == "zed"
        assert cycles_since >= 3

    def test_find_last_seen_unknown_target(self, tmp_path: Path) -> None:
        """Should return empty values when target series is unknown."""
        last_seen, cycles_since = find_last_seen_series("nova", tmp_path, "unknown")

        assert last_seen == ""
        assert cycles_since == -1


class TestCheckRetirement:
    """Tests for check_retirement."""

    def test_retired_from_project_config(self, tmp_path: Path) -> None:
        """Should mark retired when project-config says RETIRED."""
        _write_projects_yaml(
            tmp_path,
            "- project: openstack/glance\n  description: \"RETIRED: archived\"\n",
        )

        info = check_retirement(
            source_package="glance",
            project_config_path=tmp_path,
            releases_path=None,
            target_series="dalmatian",
        )

        assert info.status == RetirementStatus.RETIRED
        assert info.authoritative
        assert info.source == "project-config"

    def test_possible_retirement_overrides_active(self, tmp_path: Path) -> None:
        """Should mark possibly retired when releases inference is stale."""
        _write_projects_yaml(
            tmp_path,
            "- project: openstack/murano\n  description: \"Active\"\n",
        )
        releases = tmp_path / "releases"
        deliverables = releases / "deliverables"
        for name in ["yoga", "zed", "antelope", "bobcat", "caracal", "dalmatian"]:
            (deliverables / name).mkdir(parents=True)
        (deliverables / "yoga" / "murano.yaml").write_text("name: murano\n")

        info = check_retirement(
            source_package="murano",
            project_config_path=tmp_path,
            releases_path=releases,
            target_series="dalmatian",
        )

        assert info.status == RetirementStatus.POSSIBLY_RETIRED
        assert info.source == "releases-inference"


class TestRetirementChecker:
    """Tests for RetirementChecker."""

    def test_properties_with_loaded_project_config(self, tmp_path: Path) -> None:
        """Should report project-config load status."""
        _write_projects_yaml(tmp_path, "- project: openstack/nova\n  description: Active\n")

        checker = RetirementChecker(project_config_path=tmp_path)

        assert checker.project_config_loaded
        assert checker.project_config_error == ""

    def test_check_batch_and_filters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return batch results and filter by status."""
        import packastack.upstream.retirement as retirement_module

        results = {
            "a": RetirementInfo(status=RetirementStatus.RETIRED),
            "b": RetirementInfo(status=RetirementStatus.POSSIBLY_RETIRED),
            "c": RetirementInfo(status=RetirementStatus.ACTIVE),
        }

        def fake_check_retirement(source_package: str, **_kwargs: object) -> RetirementInfo:
            return results[source_package]

        monkeypatch.setattr(retirement_module, "check_retirement", fake_check_retirement)

        checker = RetirementChecker(project_config_path=None, releases_path=None, target_series="dalmatian")

        batch = checker.check_batch(["a", "b", "c"])
        assert batch["a"].status == RetirementStatus.RETIRED
        assert checker.get_retired_packages(["a", "b", "c"]) == ["a"]
        assert checker.get_possibly_retired_packages(["a", "b", "c"]) == ["b"]
