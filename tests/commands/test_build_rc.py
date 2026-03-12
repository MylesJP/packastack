# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for build_rc module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from packastack.commands.build_rc import _filter_packages_with_rc
from packastack.upstream.releases import ProjectRelease, ReleaseVersion


def _make_project(versions: list[str]) -> ProjectRelease:
    """Create a ProjectRelease with the given version strings."""
    return ProjectRelease(
        name="test",
        team="test",
        release_model="cycle-with-rc",
        releases=[ReleaseVersion(version=v) for v in versions],
        branches=[],
        type="service",
    )


class TestFilterPackagesWithRc:
    """Tests for _filter_packages_with_rc."""

    def test_finds_rc1_packages(self, tmp_path: Path) -> None:
        """Packages with RC1 releases are included."""
        releases_repo = tmp_path / "releases"
        releases_repo.mkdir()

        with (
            patch(
                "packastack.commands.build_rc.load_openstack_packages",
                return_value={"nova": "nova", "glance": "glance"},
            ),
            patch(
                "packastack.commands.build_rc.load_project_releases",
                side_effect=lambda _repo, _series, project: {
                    "nova": _make_project(["22.0.0.0rc1"]),
                    "glance": _make_project(["28.0.0.0b1"]),
                }.get(project),
            ),
        ):
            result = _filter_packages_with_rc(
                packages=["nova", "glance"],
                releases_repo=releases_repo,
                openstack_target="gazpacho",
                rc_number=1,
            )

        assert len(result) == 1
        assert result[0] == ("nova", "22.0.0.0rc1")

    def test_finds_any_rc_without_rc_number(self, tmp_path: Path) -> None:
        """Without rc_number, any RC version matches."""
        releases_repo = tmp_path / "releases"
        releases_repo.mkdir()

        with (
            patch(
                "packastack.commands.build_rc.load_openstack_packages",
                return_value={"nova": "nova"},
            ),
            patch(
                "packastack.commands.build_rc.load_project_releases",
                return_value=_make_project(["22.0.0.0rc1", "22.0.0.0rc2"]),
            ),
        ):
            result = _filter_packages_with_rc(
                packages=["nova"],
                releases_repo=releases_repo,
                openstack_target="gazpacho",
                rc_number=None,
            )

        # Should pick the last (latest) RC version
        assert len(result) == 1
        assert result[0] == ("nova", "22.0.0.0rc2")

    def test_filters_rc2_only(self, tmp_path: Path) -> None:
        """When rc_number=2, only RC2 versions match."""
        releases_repo = tmp_path / "releases"
        releases_repo.mkdir()

        with (
            patch(
                "packastack.commands.build_rc.load_openstack_packages",
                return_value={"nova": "nova", "glance": "glance"},
            ),
            patch(
                "packastack.commands.build_rc.load_project_releases",
                side_effect=lambda _repo, _series, project: {
                    "nova": _make_project(["22.0.0.0rc1"]),
                    "glance": _make_project(["28.0.0.0rc2"]),
                }.get(project),
            ),
        ):
            result = _filter_packages_with_rc(
                packages=["nova", "glance"],
                releases_repo=releases_repo,
                openstack_target="gazpacho",
                rc_number=2,
            )

        assert len(result) == 1
        assert result[0] == ("glance", "28.0.0.0rc2")

    def test_skips_packages_without_releases(self, tmp_path: Path) -> None:
        """Packages with no release data are skipped."""
        releases_repo = tmp_path / "releases"
        releases_repo.mkdir()

        with (
            patch(
                "packastack.commands.build_rc.load_openstack_packages",
                return_value={"nova": "nova"},
            ),
            patch(
                "packastack.commands.build_rc.load_project_releases",
                return_value=None,
            ),
        ):
            result = _filter_packages_with_rc(
                packages=["nova"],
                releases_repo=releases_repo,
                openstack_target="gazpacho",
                rc_number=1,
            )

        assert result == []

    def test_skips_final_and_beta_releases(self, tmp_path: Path) -> None:
        """Only RC versions match, not final or beta releases."""
        releases_repo = tmp_path / "releases"
        releases_repo.mkdir()

        with (
            patch(
                "packastack.commands.build_rc.load_openstack_packages",
                return_value={"nova": "nova"},
            ),
            patch(
                "packastack.commands.build_rc.load_project_releases",
                return_value=_make_project(["22.0.0", "22.0.0.0b1"]),
            ),
        ):
            result = _filter_packages_with_rc(
                packages=["nova"],
                releases_repo=releases_repo,
                openstack_target="gazpacho",
                rc_number=1,
            )

        assert result == []

    def test_results_sorted_by_package_name(self, tmp_path: Path) -> None:
        """Results are sorted alphabetically by package name."""
        releases_repo = tmp_path / "releases"
        releases_repo.mkdir()

        with (
            patch(
                "packastack.commands.build_rc.load_openstack_packages",
                return_value={"zaqar": "zaqar", "aodh": "aodh"},
            ),
            patch(
                "packastack.commands.build_rc.load_project_releases",
                return_value=_make_project(["22.0.0.0rc1"]),
            ),
        ):
            result = _filter_packages_with_rc(
                packages=["zaqar", "aodh"],
                releases_repo=releases_repo,
                openstack_target="gazpacho",
            )

        assert [name for name, _ in result] == ["aodh", "zaqar"]
