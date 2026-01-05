"""Tests for cycle edge suggestions based on upstream requirements."""

from pathlib import Path

import packastack.planning.cycle_suggestions as cycle_suggestions
from packastack.apt.packages import BinaryPackage, PackageIndex
from packastack.planning.cycle_suggestions import (
    CycleEdgeSuggestion,
    suggest_cycle_edge_exclusions,
)


def _write_requirements(repo_path: Path, content: str) -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / "requirements.txt").write_text(content, encoding="utf-8")


def test_suggests_exclusion_when_dep_missing(tmp_path: Path) -> None:
    repo_path = tmp_path / "networking-bagpipe"
    _write_requirements(repo_path, "oslo.config\n")

    suggestions = suggest_cycle_edge_exclusions(
        edges=[("networking-bagpipe", "networking-bgpvpn")],
        packaging_repos={"networking-bagpipe": repo_path},
        upstream_versions={},
        source_to_project={},
        package_index=None,
        upstream_cache_base=None,
    )

    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert suggestion.source == "networking-bagpipe"
    assert suggestion.dependency == "networking-bgpvpn"
    assert suggestion.requirements_source == "packaging_repo"
    assert "requirements.txt" in suggestion.requirements_files


def test_no_suggestion_when_dep_present(tmp_path: Path) -> None:
    repo_path = tmp_path / "networking-bagpipe"
    _write_requirements(repo_path, "networking-bgpvpn\n")

    suggestions = suggest_cycle_edge_exclusions(
        edges=[("networking-bagpipe", "networking-bgpvpn")],
        packaging_repos={"networking-bagpipe": repo_path},
        upstream_versions={},
        source_to_project={},
        package_index=None,
        upstream_cache_base=None,
    )

    assert suggestions == []


def test_no_suggestion_without_requirements(tmp_path: Path) -> None:
    repo_path = tmp_path / "networking-bagpipe"
    repo_path.mkdir(parents=True, exist_ok=True)

    suggestions = suggest_cycle_edge_exclusions(
        edges=[("networking-bagpipe", "networking-bgpvpn")],
        packaging_repos={"networking-bagpipe": repo_path},
        upstream_versions={},
        source_to_project={},
        package_index=None,
        upstream_cache_base=None,
    )

    assert suggestions == []


def test_empty_edges_returns_empty() -> None:
    suggestions = suggest_cycle_edge_exclusions(
        edges=[],
        packaging_repos=None,
        upstream_versions=None,
        source_to_project=None,
        package_index=None,
        upstream_cache_base=None,
    )

    assert suggestions == []


def test_runtime_deps_empty_skips(tmp_path: Path) -> None:
    repo_path = tmp_path / "networking-bagpipe"
    _write_requirements(repo_path, "")

    suggestions = suggest_cycle_edge_exclusions(
        edges=[("networking-bagpipe", "networking-bgpvpn")],
        packaging_repos={"networking-bagpipe": repo_path},
        upstream_versions={},
        source_to_project={},
        package_index=None,
        upstream_cache_base=None,
    )

    assert suggestions == []


def test_tarball_cache_used_when_packaging_missing(tmp_path: Path, monkeypatch) -> None:
    cache_repo = tmp_path / "cached"
    _write_requirements(cache_repo, "oslo.config\n")

    def _fake_get_cached_extraction(project: str, version: str, cache_base: Path) -> Path:
        return cache_repo

    def _fake_find_source_dir(path: Path) -> Path:
        return path

    monkeypatch.setattr(cycle_suggestions, "get_cached_extraction", _fake_get_cached_extraction)
    monkeypatch.setattr(cycle_suggestions, "find_source_dir", _fake_find_source_dir)

    suggestions = suggest_cycle_edge_exclusions(
        edges=[("networking-bagpipe", "networking-bgpvpn")],
        packaging_repos=None,
        upstream_versions={"networking-bagpipe": "1.0.0"},
        source_to_project={"networking-bagpipe": "networking-bagpipe"},
        package_index=None,
        upstream_cache_base=tmp_path,
    )

    assert len(suggestions) == 1
    assert suggestions[0].requirements_source == "tarball_cache"


def test_package_index_mapping_avoids_false_suggestion(tmp_path: Path) -> None:
    repo_path = tmp_path / "nova"
    _write_requirements(repo_path, "oslo.config\n")

    index = PackageIndex()
    index.add_package(
        BinaryPackage(
            name="python3-oslo.config",
            version="1.0",
            architecture="all",
            source="python-oslo.config",
        ),
        component="main",
        pocket="release",
    )

    suggestions = suggest_cycle_edge_exclusions(
        edges=[("nova", "python-oslo.config")],
        packaging_repos={"nova": repo_path},
        upstream_versions={},
        source_to_project={},
        package_index=index,
        upstream_cache_base=None,
    )

    assert suggestions == []


def test_cycle_edge_suggestion_to_dict() -> None:
    suggestion = CycleEdgeSuggestion(
        source="nova",
        dependency="python-oslo.config",
        upstream_project="oslo.config",
        upstream_version="1.2.3",
        requirements_source="packaging_repo",
        requirements_path="/tmp/nova",
        requirements_files=["requirements.txt"],
        reason="missing upstream requirement",
    )

    payload = suggestion.to_dict()
    assert payload["source"] == "nova"
    assert payload["dependency"] == "python-oslo.config"
