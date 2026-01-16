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

"""Tests for the type_selection module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from packastack.debpkg.watch import (
    DetectedWatchMode,
    UscanResult,
    UscanStatus,
    WatchParseResult,
)
from packastack.planning.type_selection import (
    BuildType,
    CycleStage,
    DeliverableKind,
    KindConfidence,
    PackageStatus,
    ReasonCode,
    TypeSelectionReport,
    TypeSelectionResult,
    UpstreamAuthority,
    UpstreamResolution,
    WatchConfig,
    WatchInfo,
    determine_cycle_stage,
    find_new_and_defunct_packages,
    get_default_parallel_workers,
    infer_deliverable_kind,
    select_build_type,
    select_build_types_for_packages,
)
from packastack.upstream.retirement import (
    MappingConfidence,
    RetirementInfo,
    RetirementStatus,
)


class TestGetDefaultParallelWorkers:
    """Tests for get_default_parallel_workers."""

    def test_returns_half_cpu_count(self):
        """Should return half the CPU count."""
        with patch("os.cpu_count", return_value=8):
            result = get_default_parallel_workers()
            assert result == 4

    def test_minimum_of_one(self):
        """Should return minimum of 1 even with low CPU count."""
        with patch("os.cpu_count", return_value=1):
            result = get_default_parallel_workers()
            assert result == 1

    def test_fallback_when_cpu_count_none(self):
        """Should fallback to 4 CPUs when cpu_count returns None."""
        with patch("os.cpu_count", return_value=None):
            result = get_default_parallel_workers()
            assert result == 2  # (4 // 2)


class TestDetermineCycleStage:
    """Tests for determine_cycle_stage."""

    def test_returns_unknown_when_no_repo(self):
        """Should return UNKNOWN when releases_repo is None."""
        result = determine_cycle_stage(None, "dalmatian")
        assert result == CycleStage.UNKNOWN

    def test_returns_unknown_when_repo_not_exists(self, tmp_path: Path):
        """Should return UNKNOWN when releases_repo doesn't exist."""
        fake_path = tmp_path / "nonexistent"
        result = determine_cycle_stage(fake_path, "dalmatian")
        assert result == CycleStage.UNKNOWN

    def test_returns_pre_final_for_development_series(self, tmp_path: Path):
        """Should return PRE_FINAL for series with development status."""
        releases_repo = tmp_path
        with patch(
            "packastack.planning.type_selection.load_series_info"
        ) as mock_load:
            mock_load.return_value = {
                "dalmatian": MagicMock(status="development"),
            }
            result = determine_cycle_stage(releases_repo, "dalmatian")
            assert result == CycleStage.PRE_FINAL

    def test_returns_post_final_for_maintained_series(self, tmp_path: Path):
        """Should return POST_FINAL for series with maintained status."""
        releases_repo = tmp_path
        with patch(
            "packastack.planning.type_selection.load_series_info"
        ) as mock_load:
            mock_load.return_value = {
                "caracal": MagicMock(status="maintained"),
            }
            result = determine_cycle_stage(releases_repo, "caracal")
            assert result == CycleStage.POST_FINAL

    def test_returns_post_final_for_extended_maintenance(self, tmp_path: Path):
        """Should return POST_FINAL for extended maintenance status."""
        releases_repo = tmp_path
        with patch(
            "packastack.planning.type_selection.load_series_info"
        ) as mock_load:
            mock_load.return_value = {
                "antelope": MagicMock(status="extended maintenance"),
            }
            result = determine_cycle_stage(releases_repo, "antelope")
            assert result == CycleStage.POST_FINAL

    def test_returns_unknown_for_unknown_series(self, tmp_path: Path):
        """Should return UNKNOWN for series not in info."""
        releases_repo = tmp_path
        with patch(
            "packastack.planning.type_selection.load_series_info"
        ) as mock_load:
            mock_load.return_value = {}
            result = determine_cycle_stage(releases_repo, "nonexistent")
            assert result == CycleStage.UNKNOWN

    def test_returns_unknown_for_unrecognized_status(self, tmp_path: Path):
        """Should return UNKNOWN for unrecognized status values."""
        releases_repo = tmp_path
        with patch(
            "packastack.planning.type_selection.load_series_info"
        ) as mock_load:
            mock_load.return_value = {
                "weird": MagicMock(status="experimental"),
            }
            result = determine_cycle_stage(releases_repo, "weird")
            assert result == CycleStage.UNKNOWN


class TestInferDeliverableKind:
    """Tests for infer_deliverable_kind."""

    def test_uses_metadata_type_service(self):
        """Should use metadata type field when available - service."""
        project = MagicMock(type="service")
        kind, conf = infer_deliverable_kind(project, "nova", "nova")
        assert kind == DeliverableKind.SERVICE
        assert conf == KindConfidence.METADATA

    def test_uses_metadata_type_library(self):
        """Should use metadata type field when available - library."""
        project = MagicMock(type="library")
        kind, conf = infer_deliverable_kind(project, "oslo-config", "oslo.config")
        assert kind == DeliverableKind.LIBRARY
        assert conf == KindConfidence.METADATA

    def test_uses_metadata_type_client(self):
        """Should use metadata type field when available - client."""
        project = MagicMock(type="client")
        kind, conf = infer_deliverable_kind(project, "python-novaclient", "python-novaclient")
        assert kind == DeliverableKind.CLIENT
        assert conf == KindConfidence.METADATA

    def test_heuristic_client_suffix(self):
        """Should detect client packages by suffix."""
        kind, conf = infer_deliverable_kind(None, "python-novaclient", "python-novaclient")
        assert kind == DeliverableKind.CLIENT
        assert conf == KindConfidence.HEURISTIC

    def test_heuristic_oslo_library(self):
        """Should detect oslo libraries by prefix."""
        kind, conf = infer_deliverable_kind(None, "oslo-config", "oslo.config")
        assert kind == DeliverableKind.LIBRARY
        assert conf == KindConfidence.HEURISTIC

    def test_heuristic_oslo_dash(self):
        """Should detect oslo-* libraries."""
        kind, conf = infer_deliverable_kind(None, "oslo-messaging", "oslo-messaging")
        assert kind == DeliverableKind.LIBRARY
        assert conf == KindConfidence.HEURISTIC

    def test_heuristic_horizon_plugin(self):
        """Should detect horizon plugins."""
        kind, conf = infer_deliverable_kind(None, "manila-ui", "manila-dashboard")
        assert kind == DeliverableKind.HORIZON_PLUGIN
        assert conf == KindConfidence.HEURISTIC

    def test_heuristic_tempest_plugin(self):
        """Should detect tempest plugins."""
        kind, conf = infer_deliverable_kind(None, "nova-tempest-plugin", "nova-tempest-plugin")
        assert kind == DeliverableKind.TEMPEST_PLUGIN
        assert conf == KindConfidence.HEURISTIC

    def test_heuristic_core_service(self):
        """Should detect core services from known list."""
        kind, conf = infer_deliverable_kind(None, "nova", "nova")
        assert kind == DeliverableKind.SERVICE
        assert conf == KindConfidence.HEURISTIC

    def test_fallback_unknown(self):
        """Should fallback to UNKNOWN with DEFAULT confidence."""
        kind, conf = infer_deliverable_kind(None, "some-package", "some-project")
        assert kind == DeliverableKind.UNKNOWN
        assert conf == KindConfidence.DEFAULT

    def test_python_prefix_library(self):
        """Should detect python-* as library (unless client)."""
        kind, conf = infer_deliverable_kind(None, "python-oslo.config", "oslo.config")
        assert kind == DeliverableKind.LIBRARY
        assert conf == KindConfidence.HEURISTIC


class TestSelectBuildType:
    """Tests for select_build_type."""

    def test_force_snapshot_mode(self, tmp_path: Path):
        """Should return SNAPSHOT when force_snapshot is True."""
        result = select_build_type(
            releases_repo=None,
            series="dalmatian",
            source_package="nova",
            deliverable="nova",
            cycle_stage=CycleStage.PRE_FINAL,
            force_snapshot=True,
        )
        assert result.chosen_type == BuildType.SNAPSHOT
        assert result.reason_code == ReasonCode.SNAPSHOT_FORCED

    def test_not_in_releases(self, tmp_path: Path):
        """Should return SNAPSHOT when project not in releases."""
        releases_repo = tmp_path
        with patch(
            "packastack.planning.type_selection.load_project_releases",
            return_value=None,
        ):
            result = select_build_type(
                releases_repo=releases_repo,
                series="dalmatian",
                source_package="new-pkg",
                deliverable="new-pkg",
                cycle_stage=CycleStage.PRE_FINAL,
            )
            assert result.chosen_type == BuildType.SNAPSHOT
            assert result.reason_code == ReasonCode.NOT_IN_RELEASES

    def test_post_final_with_release(self, tmp_path: Path):
        """Should return RELEASE for post-final series with releases."""
        releases_repo = tmp_path
        mock_project = MagicMock()
        mock_project.type = "service"
        mock_project.release_model = "cycle-with-rc"
        mock_project.has_releases.return_value = True
        mock_project.has_beta_rc_or_final.return_value = True
        mock_project.get_latest_version.return_value = "25.0.0"

        with patch(
            "packastack.planning.type_selection.load_project_releases",
            return_value=mock_project,
        ):
            result = select_build_type(
                releases_repo=releases_repo,
                series="caracal",
                source_package="nova",
                deliverable="nova",
                cycle_stage=CycleStage.POST_FINAL,
            )
            assert result.chosen_type == BuildType.RELEASE
            assert result.reason_code == ReasonCode.POST_FINAL_RELEASE

    def test_post_final_no_release_edge_case(self, tmp_path: Path):
        """Should return SNAPSHOT for post-final series without releases (rare)."""
        releases_repo = tmp_path
        mock_project = MagicMock()
        mock_project.type = "other"
        mock_project.release_model = "cycle-trailing"
        mock_project.has_releases.return_value = False
        mock_project.has_beta_rc_or_final.return_value = False
        mock_project.get_latest_version.return_value = None

        with patch(
            "packastack.planning.type_selection.load_project_releases",
            return_value=mock_project,
        ):
            result = select_build_type(
                releases_repo=releases_repo,
                series="caracal",
                source_package="some-pkg",
                deliverable="some-pkg",
                cycle_stage=CycleStage.POST_FINAL,
            )
            assert result.chosen_type == BuildType.SNAPSHOT
            assert result.reason_code == ReasonCode.PRE_FINAL_NO_RELEASE

    def test_pre_final_with_beta_rc_final(self, tmp_path: Path):
        """Should return RELEASE for pre-final series with beta/RC/final."""
        releases_repo = tmp_path
        mock_project = MagicMock()
        mock_project.type = "service"
        mock_project.release_model = "cycle-with-rc"
        mock_project.has_releases.return_value = True
        mock_project.has_beta_rc_or_final.return_value = True
        mock_project.get_latest_version.return_value = "26.1.0"

        with patch(
            "packastack.planning.type_selection.load_project_releases",
            return_value=mock_project,
        ):
            result = select_build_type(
                releases_repo=releases_repo,
                series="dalmatian",
                source_package="nova",
                deliverable="nova",
                cycle_stage=CycleStage.PRE_FINAL,
            )
            assert result.chosen_type == BuildType.RELEASE
            assert result.reason_code == ReasonCode.HAS_RELEASE

    def test_pre_final_beta_rc_classified_as_milestone(self, tmp_path: Path) -> None:
        """Beta/RC with upstream artifact should be classified as MILESTONE."""
        releases_repo = tmp_path
        mock_project = MagicMock()
        mock_project.type = "service"
        mock_project.release_model = "cycle-with-rc"
        mock_project.has_releases.return_value = True
        mock_project.has_beta_rc_or_final.return_value = True
        # latest version is a beta
        mock_project.get_latest_version.return_value = "26.0.0b1"
        # get_latest_release should provide is_beta/is_rc and projects info
        latest_rel = MagicMock()
        latest_rel.is_beta.return_value = True
        latest_rel.is_rc.return_value = False
        latest_rel.is_final.return_value = False
        latest_rel.projects = [{"repo": "openstack/nova", "hash": "abc123"}]
        mock_project.get_latest_release.return_value = latest_rel

        with patch(
            "packastack.planning.type_selection.load_project_releases",
            return_value=mock_project,
        ):
            result = select_build_type(
                releases_repo=releases_repo,
                series="dalmatian",
                source_package="nova",
                deliverable="nova",
                cycle_stage=CycleStage.PRE_FINAL,
            )
            assert result.chosen_type == BuildType.MILESTONE
            assert result.reason_code == ReasonCode.HAS_MILESTONE_ONLY

    def test_pre_final_beta_rc_without_artifact_falls_back(self, tmp_path: Path) -> None:
        """Beta/RC without artifacts should fall back to RELEASE."""
        releases_repo = tmp_path
        mock_project = MagicMock()
        mock_project.type = "service"
        mock_project.release_model = "cycle-with-rc"
        mock_project.has_releases.return_value = True
        mock_project.has_beta_rc_or_final.return_value = True
        mock_project.get_latest_version.return_value = "26.0.0b1"
        latest_rel = MagicMock()
        latest_rel.is_beta.return_value = True
        latest_rel.is_rc.return_value = False
        latest_rel.is_final.return_value = False
        latest_rel.projects = []
        mock_project.get_latest_release.return_value = latest_rel

        with patch(
            "packastack.planning.type_selection.load_project_releases",
            return_value=mock_project,
        ):
            result = select_build_type(
                releases_repo=releases_repo,
                series="dalmatian",
                source_package="nova",
                deliverable="nova",
                cycle_stage=CycleStage.PRE_FINAL,
            )
            assert result.chosen_type == BuildType.RELEASE
            assert result.reason_code == ReasonCode.HAS_RELEASE

    def test_pre_final_milestone_only(self, tmp_path: Path):
        """Should return MILESTONE for pre-final with only milestone releases."""
        releases_repo = tmp_path
        mock_project = MagicMock()
        mock_project.type = "service"
        mock_project.release_model = "cycle-with-rc"
        mock_project.has_releases.return_value = True
        mock_project.has_beta_rc_or_final.return_value = False
        mock_project.get_latest_version.return_value = "26.0.0.0a1"

        with patch(
            "packastack.planning.type_selection.load_project_releases",
            return_value=mock_project,
        ):
            result = select_build_type(
                releases_repo=releases_repo,
                series="dalmatian",
                source_package="nova",
                deliverable="nova",
                cycle_stage=CycleStage.PRE_FINAL,
            )
            assert result.chosen_type == BuildType.MILESTONE
            assert result.reason_code == ReasonCode.HAS_MILESTONE_ONLY

    def test_pre_final_cycle_with_intermediary(self, tmp_path: Path):
        """Should return RELEASE for cycle-with-intermediary with releases."""
        releases_repo = tmp_path
        mock_project = MagicMock()
        mock_project.type = "library"
        mock_project.release_model = "cycle-with-intermediary"
        mock_project.has_releases.return_value = True
        mock_project.has_beta_rc_or_final.return_value = False
        mock_project.get_latest_version.return_value = "3.2.0"

        with patch(
            "packastack.planning.type_selection.load_project_releases",
            return_value=mock_project,
        ):
            result = select_build_type(
                releases_repo=releases_repo,
                series="dalmatian",
                source_package="oslo-config",
                deliverable="oslo.config",
                cycle_stage=CycleStage.PRE_FINAL,
            )
            assert result.chosen_type == BuildType.RELEASE
            assert result.reason_code == ReasonCode.INTERMEDIARY_RELEASE

    def test_pre_final_cycle_trailing(self, tmp_path: Path):
        """Should return RELEASE for cycle-trailing with releases."""
        releases_repo = tmp_path
        mock_project = MagicMock()
        mock_project.type = "service"
        mock_project.release_model = "cycle-trailing"
        mock_project.has_releases.return_value = True
        mock_project.has_beta_rc_or_final.return_value = False
        mock_project.get_latest_version.return_value = "2.0.0"

        with patch(
            "packastack.planning.type_selection.load_project_releases",
            return_value=mock_project,
        ):
            result = select_build_type(
                releases_repo=releases_repo,
                series="dalmatian",
                source_package="trove",
                deliverable="trove",
                cycle_stage=CycleStage.PRE_FINAL,
            )
            assert result.chosen_type == BuildType.RELEASE
            assert result.reason_code == ReasonCode.CYCLE_TRAILING_RELEASE

    def test_pre_final_no_releases(self, tmp_path: Path):
        """Should return SNAPSHOT for pre-final with no releases."""
        releases_repo = tmp_path
        mock_project = MagicMock()
        mock_project.type = "service"
        mock_project.release_model = "cycle-with-rc"
        mock_project.has_releases.return_value = False
        mock_project.has_beta_rc_or_final.return_value = False
        mock_project.get_latest_version.return_value = None

        with patch(
            "packastack.planning.type_selection.load_project_releases",
            return_value=mock_project,
        ):
            result = select_build_type(
                releases_repo=releases_repo,
                series="dalmatian",
                source_package="new-service",
                deliverable="new-service",
                cycle_stage=CycleStage.PRE_FINAL,
            )
            assert result.chosen_type == BuildType.SNAPSHOT
            assert result.reason_code == ReasonCode.NO_RELEASE_YET


class TestTypeSelectionResult:
    """Tests for TypeSelectionResult dataclass."""

    def test_to_dict(self):
        """Should serialize to dictionary."""
        result = TypeSelectionResult(
            source_package="nova",
            deliverable="nova",
            release_model="cycle-with-rc",
            deliverable_kind=DeliverableKind.SERVICE,
            kind_confidence=KindConfidence.METADATA,
            has_release_for_cycle=True,
            has_beta_rc_final=True,
            latest_version="25.0.0",
            cycle_stage=CycleStage.POST_FINAL,
            chosen_type=BuildType.RELEASE,
            reason_code=ReasonCode.POST_FINAL_RELEASE,
            reason_human="Post-final: use release",
            package_status=PackageStatus.ACTIVE,
        )
        d = result.to_dict()
        assert d["source_package"] == "nova"
        assert d["chosen_type"] == "release"
        assert d["reason_code"] == "POST_FINAL_RELEASE"
        assert d["deliverable_kind"] == "service"

    def test_from_dict(self):
        """Should deserialize from dictionary."""
        data = {
            "source_package": "nova",
            "deliverable": "nova",
            "release_model": "cycle-with-rc",
            "deliverable_kind": "service",
            "kind_confidence": "metadata",
            "has_release_for_cycle": True,
            "has_beta_rc_final": True,
            "latest_version": "25.0.0",
            "cycle_stage": "post_final",
            "chosen_type": "release",
            "reason_code": "POST_FINAL_RELEASE",
            "reason_human": "Post-final: use release",
            "package_status": "active",
        }
        result = TypeSelectionResult.from_dict(data)
        assert result.source_package == "nova"
        assert result.chosen_type == BuildType.RELEASE
        assert result.reason_code == ReasonCode.POST_FINAL_RELEASE


class TestTypeSelectionReport:
    """Tests for TypeSelectionReport dataclass."""

    def test_add_result_updates_counts(self):
        """Should update counts when adding results."""
        report = TypeSelectionReport(
            run_id="test",
            target="dalmatian",
            ubuntu_series="plucky",
            generated_at_utc="2025-01-01T00:00:00Z",
            type_mode="auto",
            cycle_stage=CycleStage.PRE_FINAL,
        )

        release_result = TypeSelectionResult(
            source_package="nova",
            deliverable="nova",
            release_model="cycle-with-rc",
            deliverable_kind=DeliverableKind.SERVICE,
            kind_confidence=KindConfidence.METADATA,
            has_release_for_cycle=True,
            has_beta_rc_final=True,
            latest_version="26.0.0",
            cycle_stage=CycleStage.PRE_FINAL,
            chosen_type=BuildType.RELEASE,
            reason_code=ReasonCode.HAS_RELEASE,
            reason_human="Has release",
        )
        report.add_result(release_result)

        assert report.count_release == 1
        assert report.count_milestone == 0
        assert report.count_snapshot == 0
        assert report.total_count == 1
        assert report.counts_by_type == {"release": 1, "milestone": 0, "snapshot": 0}

    def test_add_result_tracks_new_packages(self):
        """Should track new packages."""
        report = TypeSelectionReport(
            run_id="test",
            target="dalmatian",
            ubuntu_series="plucky",
            generated_at_utc="2025-01-01T00:00:00Z",
            type_mode="auto",
            cycle_stage=CycleStage.PRE_FINAL,
        )

        new_result = TypeSelectionResult(
            source_package="new-pkg",
            deliverable="new-pkg",
            release_model="",
            deliverable_kind=DeliverableKind.UNKNOWN,
            kind_confidence=KindConfidence.DEFAULT,
            has_release_for_cycle=False,
            has_beta_rc_final=False,
            latest_version="",
            cycle_stage=CycleStage.PRE_FINAL,
            chosen_type=BuildType.SNAPSHOT,
            reason_code=ReasonCode.NOT_IN_RELEASES,
            reason_human="Not in releases",
            package_status=PackageStatus.NEW,
        )
        report.add_result(new_result)

        assert "new-pkg" in report.new_packages

    def test_add_result_tracks_defunct_and_retired(self):
        """Should track defunct and retired packages."""
        report = TypeSelectionReport(
            run_id="test",
            target="dalmatian",
            ubuntu_series="plucky",
            generated_at_utc="2025-01-01T00:00:00Z",
            type_mode="auto",
            cycle_stage=CycleStage.PRE_FINAL,
        )

        defunct_result = TypeSelectionResult(
            source_package="old-pkg",
            deliverable="old-pkg",
            release_model="",
            deliverable_kind=DeliverableKind.UNKNOWN,
            kind_confidence=KindConfidence.DEFAULT,
            has_release_for_cycle=False,
            has_beta_rc_final=False,
            latest_version="",
            cycle_stage=CycleStage.PRE_FINAL,
            chosen_type=BuildType.SNAPSHOT,
            reason_code=ReasonCode.NOT_IN_RELEASES,
            reason_human="Defunct",
            package_status=PackageStatus.DEFUNCT,
        )
        retired_result = TypeSelectionResult(
            source_package="retired-pkg",
            deliverable="retired-pkg",
            release_model="",
            deliverable_kind=DeliverableKind.UNKNOWN,
            kind_confidence=KindConfidence.DEFAULT,
            has_release_for_cycle=False,
            has_beta_rc_final=False,
            latest_version="",
            cycle_stage=CycleStage.PRE_FINAL,
            chosen_type=BuildType.SNAPSHOT,
            reason_code=ReasonCode.RETIRED_PROJECT,
            reason_human="Retired",
            package_status=PackageStatus.RETIRED,
        )

        report.add_result(defunct_result)
        report.add_result(retired_result)

        assert "old-pkg" in report.defunct_packages
        assert "retired-pkg" in report.retired_packages
        assert report.count_retired == 1

    def test_to_dict(self):
        """Should serialize report to dictionary."""
        report = TypeSelectionReport(
            run_id="test-123",
            target="dalmatian",
            ubuntu_series="plucky",
            generated_at_utc="2025-01-01T00:00:00Z",
            type_mode="auto",
            cycle_stage=CycleStage.PRE_FINAL,
        )
        d = report.to_dict()
        assert d["run_id"] == "test-123"
        assert d["target"] == "dalmatian"
        assert d["cycle_stage"] == "pre_final"
        assert "summary" in d


class TestFindNewAndDefunctPackages:
    """Tests for find_new_and_defunct_packages."""

    def test_returns_empty_when_no_repo(self):
        """Should return empty sets when no releases repo."""
        new, defunct = find_new_and_defunct_packages(None, "dalmatian", {"nova"})
        assert new == set()
        assert defunct == set()

    def test_finds_new_packages(self, tmp_path: Path):
        """Should find packages in local but not in releases."""
        with patch(
            "packastack.planning.type_selection.load_openstack_packages"
        ) as mock_load:
            mock_load.return_value = {"nova": "nova", "glance": "glance"}
            local = {"nova", "glance", "new-pkg"}
            new, _defunct = find_new_and_defunct_packages(tmp_path, "dalmatian", local)
            assert "new-pkg" in new
            assert "nova" not in new

    def test_finds_defunct_packages(self, tmp_path: Path):
        """Should find packages in releases but not local."""
        with patch(
            "packastack.planning.type_selection.load_openstack_packages"
        ) as mock_load:
            mock_load.return_value = {
                "nova": "nova",
                "glance": "glance",
                "old-pkg": "old-pkg",
            }
            local = {"nova", "glance"}
            _new, defunct = find_new_and_defunct_packages(tmp_path, "dalmatian", local)
            assert "old-pkg" in defunct
            assert "nova" not in defunct


class TestSelectBuildTypesForPackages:
    """Tests for select_build_types_for_packages."""

    def test_generates_report(self, tmp_path: Path):
        """Should generate a complete report."""
        with patch(
            "packastack.planning.type_selection.determine_cycle_stage",
            return_value=CycleStage.PRE_FINAL,
        ), patch(
            "packastack.planning.type_selection.load_project_releases",
            return_value=None,
        ):
            packages = [("nova", "nova"), ("glance", "glance")]
            report = select_build_types_for_packages(
                releases_repo=tmp_path,
                series="dalmatian",
                packages=packages,
                run_id="test-run",
                ubuntu_series="plucky",
                type_mode="auto",
            )
            assert report.run_id == "test-run"
            assert report.target == "dalmatian"
            assert len(report.packages) == 2

    def test_force_release_mode(self, tmp_path: Path):
        """Should force RELEASE type when type_mode is 'release'."""
        mock_project = MagicMock()
        mock_project.type = "service"
        mock_project.release_model = "cycle-with-rc"
        mock_project.has_releases.return_value = True
        mock_project.has_beta_rc_or_final.return_value = True
        mock_project.get_latest_version.return_value = "25.0.0"

        with patch(
            "packastack.planning.type_selection.determine_cycle_stage",
            return_value=CycleStage.PRE_FINAL,
        ), patch(
            "packastack.planning.type_selection.load_project_releases",
            return_value=mock_project,
        ):
            packages = [("nova", "nova")]
            report = select_build_types_for_packages(
                releases_repo=tmp_path,
                series="dalmatian",
                packages=packages,
                run_id="test-run",
                ubuntu_series="plucky",
                type_mode="release",
            )
            assert report.packages[0].chosen_type == BuildType.RELEASE

    def test_force_milestone_mode(self, tmp_path: Path):
        """Should force MILESTONE type when type_mode is 'milestone'."""
        mock_project = MagicMock()
        mock_project.type = "service"
        mock_project.release_model = "cycle-with-rc"
        mock_project.has_releases.return_value = True
        mock_project.has_beta_rc_or_final.return_value = True
        mock_project.get_latest_version.return_value = "25.0.0"

        with patch(
            "packastack.planning.type_selection.determine_cycle_stage",
            return_value=CycleStage.PRE_FINAL,
        ), patch(
            "packastack.planning.type_selection.load_project_releases",
            return_value=mock_project,
        ):
            packages = [("nova", "nova")]
            report = select_build_types_for_packages(
                releases_repo=tmp_path,
                series="dalmatian",
                packages=packages,
                run_id="test-run",
                ubuntu_series="plucky",
                type_mode="milestone",
            )
            assert report.packages[0].chosen_type == BuildType.MILESTONE

    def test_force_snapshot_mode(self, tmp_path: Path):
        """Should force SNAPSHOT type when force_snapshot is True."""
        mock_project = MagicMock()
        mock_project.type = "service"
        mock_project.release_model = "cycle-with-rc"
        mock_project.has_releases.return_value = True
        mock_project.has_beta_rc_or_final.return_value = True
        mock_project.get_latest_version.return_value = "25.0.0"

        with patch(
            "packastack.planning.type_selection.determine_cycle_stage",
            return_value=CycleStage.PRE_FINAL,
        ), patch(
            "packastack.planning.type_selection.load_project_releases",
            return_value=mock_project,
        ):
            packages = [("nova", "nova")]
            report = select_build_types_for_packages(
                releases_repo=tmp_path,
                series="dalmatian",
                packages=packages,
                run_id="test-run",
                ubuntu_series="plucky",
                type_mode="snapshot",
                force_snapshot=True,
            )
            assert report.packages[0].chosen_type == BuildType.SNAPSHOT

    def test_force_release_progress_callback(self, tmp_path: Path):
        """Should invoke progress_callback in forced mode."""
        mock_project = MagicMock()
        mock_project.type = "service"
        mock_project.release_model = "cycle-with-rc"
        mock_project.has_releases.return_value = True
        mock_project.has_beta_rc_or_final.return_value = True
        mock_project.get_latest_version.return_value = "25.0.0"

        progress: list[int] = []
        with patch(
            "packastack.planning.type_selection.determine_cycle_stage",
            return_value=CycleStage.PRE_FINAL,
        ), patch(
            "packastack.planning.type_selection.load_project_releases",
            return_value=mock_project,
        ):
            packages = [("nova", "nova")]
            report = select_build_types_for_packages(
                releases_repo=tmp_path,
                series="dalmatian",
                packages=packages,
                run_id="test-run",
                ubuntu_series="plucky",
                type_mode="release",
                progress_callback=lambda n: progress.append(n),
            )
            assert report.packages[0].chosen_type == BuildType.RELEASE
            assert progress == [1]


class TestSerializationHelpers:
    """Tests for serialization helpers in type selection."""

    def test_upstream_resolution_round_trip(self) -> None:
        """Should serialize and deserialize UpstreamResolution."""
        resolution = UpstreamResolution(
            authority=UpstreamAuthority.RELEASES,
            watch_used=True,
            uscan_used=True,
            reason="test",
            upstream_version="1.0.0",
            download_url="https://example.com/pkg.tar.gz",
        )
        data = resolution.to_dict()
        restored = UpstreamResolution.from_dict(data)

        assert restored.authority == UpstreamAuthority.RELEASES
        assert restored.upstream_version == "1.0.0"

    def test_watch_info_round_trip(self) -> None:
        """Should serialize and deserialize WatchInfo."""
        watch_info = WatchInfo(
            parsed=True,
            mode="openstack_tarball",
            uscan_attempted=True,
            uscan_status="success",
            uscan_error="",
            packaged_version="1.0.0",
            upstream_version="1.1.0",
            newer_available=True,
        )
        data = watch_info.to_dict()
        restored = WatchInfo.from_dict(data)

        assert restored.parsed
        assert restored.mode == "openstack_tarball"
        assert restored.newer_available

    def test_type_selection_result_includes_optional_fields(self) -> None:
        """Should include watch/upstream/retirement info in dict."""
        retirement_info = RetirementInfo(
            status=RetirementStatus.RETIRED,
            authoritative=True,
            source="project-config",
            description="RETIRED",
        )
        result = TypeSelectionResult(
            source_package="nova",
            deliverable="nova",
            release_model="cycle-with-rc",
            deliverable_kind=DeliverableKind.SERVICE,
            kind_confidence=KindConfidence.METADATA,
            has_release_for_cycle=True,
            has_beta_rc_final=True,
            latest_version="1.0.0",
            cycle_stage=CycleStage.PRE_FINAL,
            chosen_type=BuildType.RELEASE,
            reason_code=ReasonCode.HAS_RELEASE,
            reason_human="Has release",
            package_status=PackageStatus.RETIRED,
            upstream_resolution=UpstreamResolution(
                authority=UpstreamAuthority.RELEASES,
                upstream_version="1.0.0",
            ),
            watch_info=WatchInfo(parsed=True, mode="openstack_tarball"),
            retirement_info=retirement_info,
        )
        data = result.to_dict()
        restored = TypeSelectionResult.from_dict(data)

        assert "upstream_resolution" in data
        assert "watch_info" in data
        assert "retirement_info" in data
        assert restored.retirement_info is not None
        assert restored.retirement_info.status == RetirementStatus.RETIRED

    def test_report_from_dict_includes_possibly_retired(self) -> None:
        """Should load possibly_retired_packages from dict."""
        report = TypeSelectionReport(
            run_id="test",
            target="dalmatian",
            ubuntu_series="plucky",
            generated_at_utc="2025-01-01T00:00:00Z",
            type_mode="auto",
            cycle_stage=CycleStage.PRE_FINAL,
        )
        data = report.to_dict()
        data["possibly_retired_packages"] = ["maybe-retired"]
        restored = TypeSelectionReport.from_dict(data)

        assert restored.possibly_retired_packages == ["maybe-retired"]


class TestInferDeliverableKindExtras:
    """Tests for additional deliverable kind heuristics."""

    def test_python_library_not_oslo(self) -> None:
        """Should classify python-* as library when not oslo."""
        kind, conf = infer_deliverable_kind(None, "python-requests", "requests")
        assert kind == DeliverableKind.LIBRARY
        assert conf == KindConfidence.HEURISTIC

    def test_horizon_plugin_keyword(self) -> None:
        """Should detect horizon plugin by keyword."""
        kind, conf = infer_deliverable_kind(None, "foo", "horizon-plugin-foo")
        assert kind == DeliverableKind.HORIZON_PLUGIN
        assert conf == KindConfidence.HEURISTIC


class TestSelectBuildTypeWatchInfo:
    """Tests for watch/uscan handling in select_build_type."""

    def test_uses_cached_uscan_result(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should use cached uscan result when available."""
        repo = tmp_path / "nova"
        (repo / "debian").mkdir(parents=True)
        (repo / "debian" / "watch").write_text("version=4")

        mock_project = MagicMock()
        mock_project.type = "service"
        mock_project.release_model = "cycle-with-rc"
        mock_project.has_releases.return_value = True
        mock_project.has_beta_rc_or_final.return_value = True
        mock_project.get_latest_version.return_value = "1.0.0"
        mock_project.get_latest_release.return_value = MagicMock(is_final=lambda: True)

        uscan_result = UscanResult(
            success=True,
            status=UscanStatus.SUCCESS,
            upstream_version="1.1.0",
            upstream_url="https://example.com/nova.tar.gz",
            debian_upstream_version="1.0.0",
            newer_available=True,
        )

        monkeypatch.setattr(
            "packastack.planning.type_selection.load_project_releases",
            lambda *_args, **_kwargs: mock_project,
        )
        monkeypatch.setattr(
            "packastack.debpkg.watch.parse_watch_file",
            lambda _path: WatchParseResult(mode=DetectedWatchMode.OPENSTACK_TARBALL),
        )
        monkeypatch.setattr(
            "packastack.debpkg.watch.get_cached_uscan_result",
            lambda _pkg, _cache: uscan_result,
        )
        monkeypatch.setattr(
            "packastack.debpkg.watch.run_uscan_dehs",
            lambda *_args, **_kwargs: pytest.fail("uscan should not run"),
        )

        result = select_build_type(
            releases_repo=tmp_path,
            series="dalmatian",
            source_package="nova",
            deliverable="nova",
            cycle_stage=CycleStage.PRE_FINAL,
            packaging_repo=repo,
            watch_config=WatchConfig(enabled=True, check_upstream=True),
            uscan_cache={},
        )

        assert result.watch_info is not None
        assert result.watch_info.uscan_attempted
        assert result.upstream_resolution is not None
        assert result.upstream_resolution.authority == UpstreamAuthority.RELEASES

    def test_runs_uscan_for_not_in_releases(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should run uscan and use WATCH authority when not in releases."""
        repo = tmp_path / "custom"
        (repo / "debian").mkdir(parents=True)
        (repo / "debian" / "watch").write_text("version=4")

        uscan_result = UscanResult(
            success=True,
            status=UscanStatus.NEWER_AVAILABLE,
            upstream_version="2.0.0",
            upstream_url="https://example.com/custom.tar.gz",
            debian_upstream_version="1.0.0",
            newer_available=True,
        )

        cache_calls: list[str] = []

        monkeypatch.setattr(
            "packastack.planning.type_selection.load_project_releases",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            "packastack.debpkg.watch.parse_watch_file",
            lambda _path: WatchParseResult(mode=DetectedWatchMode.OPENSTACK_TARBALL),
        )
        monkeypatch.setattr(
            "packastack.debpkg.watch.get_cached_uscan_result",
            lambda _pkg, _cache: None,
        )
        monkeypatch.setattr(
            "packastack.debpkg.watch.run_uscan_dehs",
            lambda *_args, **_kwargs: uscan_result,
        )
        monkeypatch.setattr(
            "packastack.debpkg.watch.cache_uscan_result",
            lambda pkg, *_args, **_kwargs: cache_calls.append(pkg),
        )

        result = select_build_type(
            releases_repo=tmp_path,
            series="dalmatian",
            source_package="custom",
            deliverable="custom",
            cycle_stage=CycleStage.PRE_FINAL,
            packaging_repo=repo,
            watch_config=WatchConfig(enabled=True, check_upstream=True, fallback_for_not_in_releases=True),
            uscan_cache={},
        )

        assert result.reason_code == ReasonCode.NOT_IN_RELEASES
        assert result.watch_info is not None
        assert result.upstream_resolution is not None
        assert result.upstream_resolution.authority == UpstreamAuthority.WATCH
        assert cache_calls == ["custom"]


class TestSelectBuildTypesForPackagesAdvanced:
    """Tests for advanced select_build_types_for_packages behavior."""

    def test_prunes_uscan_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should drop stale uscan cache entries."""
        from packastack.debpkg.watch import UscanCacheEntry

        repo = tmp_path / "nova"
        repo.mkdir()
        cache_path = tmp_path / "uscan.json"

        stale = {
            "gone": UscanCacheEntry(
                source_package="gone",
                result=UscanResult(success=False, status=UscanStatus.ERROR),
                cached_at_utc="",
                packaging_repo_path="",
            ),
            "moved": UscanCacheEntry(
                source_package="moved",
                result=UscanResult(success=False, status=UscanStatus.ERROR),
                cached_at_utc="",
                packaging_repo_path="old-path",
            ),
        }
        saved: dict[str, object] = {}

        monkeypatch.setattr(
            "packastack.debpkg.watch.load_uscan_cache",
            lambda _path: dict(stale),
        )
        monkeypatch.setattr(
            "packastack.debpkg.watch.save_uscan_cache",
            lambda cache, _path: saved.update(cache),
        )
        monkeypatch.setattr(
            "packastack.planning.type_selection.select_build_type",
            lambda **_kwargs: TypeSelectionResult(
                source_package="nova",
                deliverable="nova",
                release_model="",
                deliverable_kind=DeliverableKind.UNKNOWN,
                kind_confidence=KindConfidence.DEFAULT,
                has_release_for_cycle=False,
                has_beta_rc_final=False,
                latest_version="",
                cycle_stage=CycleStage.UNKNOWN,
                chosen_type=BuildType.SNAPSHOT,
                reason_code=ReasonCode.NOT_IN_RELEASES,
                reason_human="",
            ),
        )

        report = select_build_types_for_packages(
            releases_repo=tmp_path,
            series="dalmatian",
            packages=[("nova", "nova")],
            run_id="run-1",
            ubuntu_series="plucky",
            type_mode="auto",
            packaging_repos={"nova": repo},
            uscan_cache_path=cache_path,
            parallel=1,
        )

        assert report.run_id == "run-1"
        assert saved == {}

    def test_forced_mode_respects_retired(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should emit retired results in forced mode."""
        retirement = RetirementInfo(
            status=RetirementStatus.RETIRED,
            authoritative=True,
            description="RETIRED",
        )

        class FakeChecker:
            def check(self, _pkg: str) -> RetirementInfo:
                return retirement

        progress: list[int] = []

        report = select_build_types_for_packages(
            releases_repo=tmp_path,
            series="dalmatian",
            packages=[("retired", "retired")],
            run_id="run-2",
            ubuntu_series="plucky",
            type_mode="release",
            retirement_checker=FakeChecker(),
            progress_callback=lambda n: progress.append(n),
        )

        retired = next(p for p in report.packages if p.source_package == "retired")
        assert retired.package_status == PackageStatus.RETIRED
        assert retired.reason_code == ReasonCode.RETIRED_PROJECT
        assert progress == [1]

    def test_auto_mode_retired_packages(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should add retired packages before processing active ones."""
        retirement = RetirementInfo(
            status=RetirementStatus.RETIRED,
            authoritative=True,
            description="RETIRED",
        )

        class FakeChecker:
            def check(self, pkg: str) -> RetirementInfo:
                if pkg == "retired":
                    return retirement
                return RetirementInfo(status=RetirementStatus.ACTIVE)

        monkeypatch.setattr(
            "packastack.planning.type_selection.select_build_type",
            lambda **_kwargs: TypeSelectionResult(
                source_package="active",
                deliverable="active",
                release_model="",
                deliverable_kind=DeliverableKind.UNKNOWN,
                kind_confidence=KindConfidence.DEFAULT,
                has_release_for_cycle=False,
                has_beta_rc_final=False,
                latest_version="",
                cycle_stage=CycleStage.UNKNOWN,
                chosen_type=BuildType.SNAPSHOT,
                reason_code=ReasonCode.NOT_IN_RELEASES,
                reason_human="",
            ),
        )

        progress: list[int] = []
        report = select_build_types_for_packages(
            releases_repo=tmp_path,
            series="dalmatian",
            packages=[("retired", "retired"), ("active", "active")],
            run_id="run-2",
            ubuntu_series="plucky",
            type_mode="auto",
            retirement_checker=FakeChecker(),
            parallel=1,
            progress_callback=lambda n: progress.append(n),
        )

        retired = next(p for p in report.packages if p.source_package == "retired")
        assert retired.package_status == PackageStatus.RETIRED
        assert retired.reason_code == ReasonCode.RETIRED_PROJECT
        assert len(report.packages) == 2
        assert sum(progress) == 2

    def test_parallel_uscan_limit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should disable uscan for packages beyond the max_projects limit."""
        seen_configs: list[WatchConfig | None] = []

        def fake_worker(item: tuple[object, ...]) -> TypeSelectionResult:
            seen_configs.append(item[8])
            return TypeSelectionResult(
                source_package=item[2],
                deliverable=item[3],
                release_model="",
                deliverable_kind=DeliverableKind.UNKNOWN,
                kind_confidence=KindConfidence.DEFAULT,
                has_release_for_cycle=False,
                has_beta_rc_final=False,
                latest_version="",
                cycle_stage=CycleStage.UNKNOWN,
                chosen_type=BuildType.SNAPSHOT,
                reason_code=ReasonCode.NOT_IN_RELEASES,
                reason_human="",
            )

        monkeypatch.setattr("packastack.planning.type_selection._select_type_worker", fake_worker)

        progress: list[int] = []
        watch_config = WatchConfig(enabled=True, check_upstream=True, max_projects=1)
        report = select_build_types_for_packages(
            releases_repo=tmp_path,
            series="dalmatian",
            packages=[("a", "a"), ("b", "b")],
            run_id="run-3",
            ubuntu_series="plucky",
            type_mode="auto",
            watch_config=watch_config,
            parallel=2,
            progress_callback=lambda n: progress.append(n),
        )

        assert len(report.packages) == 2
        assert seen_configs[0] == watch_config
        assert seen_configs[1] is not None
        assert seen_configs[1].check_upstream is False
        assert sum(progress) == 2

    def test_sequential_uscan_limit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should disable uscan when limit exceeded in sequential mode."""
        seen_configs: list[WatchConfig | None] = []

        def fake_select_build_type(**kwargs: object) -> TypeSelectionResult:
            seen_configs.append(kwargs.get("watch_config"))
            return TypeSelectionResult(
                source_package=kwargs["source_package"],
                deliverable=kwargs["deliverable"],
                release_model="",
                deliverable_kind=DeliverableKind.UNKNOWN,
                kind_confidence=KindConfidence.DEFAULT,
                has_release_for_cycle=False,
                has_beta_rc_final=False,
                latest_version="",
                cycle_stage=CycleStage.UNKNOWN,
                chosen_type=BuildType.SNAPSHOT,
                reason_code=ReasonCode.NOT_IN_RELEASES,
                reason_human="",
            )

        monkeypatch.setattr("packastack.planning.type_selection.select_build_type", fake_select_build_type)

        watch_config = WatchConfig(enabled=True, check_upstream=True, max_projects=1)
        progress: list[int] = []
        report = select_build_types_for_packages(
            releases_repo=tmp_path,
            series="dalmatian",
            packages=[("a", "a"), ("b", "b")],
            run_id="run-4",
            ubuntu_series="plucky",
            type_mode="auto",
            watch_config=watch_config,
            parallel=1,
            progress_callback=lambda n: progress.append(n),
        )

        assert len(report.packages) == 2
        assert seen_configs[0] == watch_config
        assert seen_configs[1] is not None
        assert seen_configs[1].check_upstream is False
        assert sum(progress) == 2

    def test_marks_possibly_retired_and_needs_mapping(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should track possibly retired packages and mapping needs."""
        retirement = RetirementInfo(
            status=RetirementStatus.POSSIBLY_RETIRED,
            mapping_confidence=MappingConfidence.LOW,
        )

        class FakeChecker:
            def check(self, _pkg: str) -> RetirementInfo:
                return retirement

        monkeypatch.setattr(
            "packastack.planning.type_selection.find_new_and_defunct_packages",
            lambda *_args, **_kwargs: ({"new-pkg"}, set()),
        )
        monkeypatch.setattr(
            "packastack.planning.type_selection.select_build_type",
            lambda **_kwargs: TypeSelectionResult(
                source_package="new-pkg",
                deliverable="new-pkg",
                release_model="",
                deliverable_kind=DeliverableKind.UNKNOWN,
                kind_confidence=KindConfidence.DEFAULT,
                has_release_for_cycle=False,
                has_beta_rc_final=False,
                latest_version="",
                cycle_stage=CycleStage.UNKNOWN,
                chosen_type=BuildType.SNAPSHOT,
                reason_code=ReasonCode.NOT_IN_RELEASES,
                reason_human="",
            ),
        )

        report = select_build_types_for_packages(
            releases_repo=tmp_path,
            series="dalmatian",
            packages=[("new-pkg", "new-pkg")],
            run_id="run-6",
            ubuntu_series="plucky",
            type_mode="auto",
            local_packages={"new-pkg"},
            retirement_checker=FakeChecker(),
            parallel=1,
        )

        assert "new-pkg" in report.possibly_retired_packages
        assert "new-pkg" in report.needs_upstream_mapping

    def test_adds_defunct_packages(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should add defunct packages not present in results."""
        monkeypatch.setattr(
            "packastack.planning.type_selection.find_new_and_defunct_packages",
            lambda *_args, **_kwargs: (set(), {"old-pkg"}),
        )
        monkeypatch.setattr(
            "packastack.planning.type_selection.select_build_type",
            lambda **_kwargs: TypeSelectionResult(
                source_package="nova",
                deliverable="nova",
                release_model="",
                deliverable_kind=DeliverableKind.UNKNOWN,
                kind_confidence=KindConfidence.DEFAULT,
                has_release_for_cycle=False,
                has_beta_rc_final=False,
                latest_version="",
                cycle_stage=CycleStage.UNKNOWN,
                chosen_type=BuildType.SNAPSHOT,
                reason_code=ReasonCode.NOT_IN_RELEASES,
                reason_human="",
            ),
        )

        report = select_build_types_for_packages(
            releases_repo=tmp_path,
            series="dalmatian",
            packages=[("nova", "nova")],
            run_id="run-5",
            ubuntu_series="plucky",
            type_mode="auto",
            local_packages={"nova"},
            parallel=1,
        )

        assert "old-pkg" in report.defunct_packages
