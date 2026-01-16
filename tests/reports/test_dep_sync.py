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

"""Tests for dependency sync report module."""

import json
from pathlib import Path

from packastack.debpkg.control import ParsedDependency
from packastack.debpkg.dep_sync import SyncResult, VersionBump
from packastack.planning.build_manifest import BuildManifest
from packastack.planning.type_selection import BuildType, CycleStage
from packastack.reports.dep_sync import (
    DependencySyncReport,
    ManifestReport,
    create_manifest_report,
    create_sync_report,
    render_manifest_report_json,
    render_manifest_report_text,
    render_sync_report_json,
    render_sync_report_text,
    save_manifest_report,
    save_sync_report,
)


class TestDependencySyncReport:
    """Tests for DependencySyncReport dataclass."""

    def test_default_timestamp(self):
        """Test that timestamp is set automatically."""
        report = DependencySyncReport(source_package="nova")
        assert report.timestamp != ""
        assert "T" in report.timestamp  # ISO format

    def test_to_dict(self):
        """Test conversion to dictionary."""
        report = DependencySyncReport(
            source_package="nova",
            version_bumps=[{"debian_package": "python3-oslo.config"}],
            unresolved=["unknown-pkg"],
        )
        d = report.to_dict()
        assert d["source_package"] == "nova"
        assert len(d["version_bumps"]) == 1
        assert "unknown-pkg" in d["unresolved"]


class TestCreateSyncReport:
    """Tests for create_sync_report function."""

    def test_empty_result(self):
        """Test with empty sync result."""
        sync_result = SyncResult()
        report = create_sync_report("nova", sync_result)

        assert report.source_package == "nova"
        assert report.version_bumps == []
        assert report.additions == []

    def test_with_version_bumps(self):
        """Test with version bumps."""
        sync_result = SyncResult(
            version_bumps=[
                VersionBump(
                    debian_package="python3-oslo.config",
                    python_package="oslo.config",
                    old_version="7.0.0",
                    new_version="8.0.0",
                    source="manifest",
                ),
            ],
            from_manifest=["python3-oslo.config"],
        )
        report = create_sync_report("nova", sync_result)

        assert len(report.version_bumps) == 1
        assert report.version_bumps[0]["debian_package"] == "python3-oslo.config"
        assert report.packages_from_manifest == 1

    def test_with_additions(self):
        """Test with new dependencies."""
        sync_result = SyncResult(
            additions=[
                ParsedDependency(name="python3-new-pkg", relation=">=", version="1.0.0"),
            ],
        )
        report = create_sync_report("nova", sync_result)

        assert len(report.additions) == 1
        assert report.additions[0]["name"] == "python3-new-pkg"


class TestRenderSyncReportText:
    """Tests for render_sync_report_text function."""

    def test_empty_report(self):
        """Test rendering empty report."""
        report = DependencySyncReport(source_package="nova")
        text = render_sync_report_text(report)

        assert "nova" in text
        assert "Dependency Sync Report" in text
        assert "Statistics" in text

    def test_with_version_bumps(self):
        """Test rendering report with version bumps."""
        report = DependencySyncReport(
            source_package="nova",
            version_bumps=[{
                "debian_package": "python3-oslo.config",
                "python_package": "oslo.config",
                "old_version": "7.0.0",
                "new_version": "8.0.0",
                "source": "manifest",
            }],
        )
        text = render_sync_report_text(report)

        assert "Version Bumps" in text
        assert "python3-oslo.config" in text
        assert "7.0.0" in text
        assert "8.0.0" in text

    def test_with_warnings(self):
        """Test rendering report with warnings."""
        report = DependencySyncReport(
            source_package="nova",
            warnings=["Something went wrong"],
        )
        text = render_sync_report_text(report)

        assert "Warnings" in text
        assert "Something went wrong" in text


class TestRenderSyncReportJson:
    """Tests for render_sync_report_json function."""

    def test_valid_json(self):
        """Test that output is valid JSON."""
        report = DependencySyncReport(source_package="nova")
        json_str = render_sync_report_json(report)

        # Should parse without error
        data = json.loads(json_str)
        assert data["source_package"] == "nova"


class TestSaveSyncReport:
    """Tests for save_sync_report function."""

    def test_saves_both_formats(self, tmp_path: Path):
        """Test saving both text and JSON formats."""
        report = DependencySyncReport(source_package="nova")
        saved = save_sync_report(report, tmp_path)

        assert len(saved) == 2
        assert any(p.suffix == ".txt" for p in saved)
        assert any(p.suffix == ".json" for p in saved)
        assert all(p.exists() for p in saved)

    def test_saves_only_requested_format(self, tmp_path: Path):
        """Test saving only requested format."""
        report = DependencySyncReport(source_package="nova")
        saved = save_sync_report(report, tmp_path, formats=["json"])

        assert len(saved) == 1
        assert saved[0].suffix == ".json"

    def test_creates_output_directory(self, tmp_path: Path):
        """Test that output directory is created."""
        output_dir = tmp_path / "reports" / "nested"
        report = DependencySyncReport(source_package="nova")
        save_sync_report(report, output_dir)

        assert output_dir.exists()


class TestManifestReport:
    """Tests for ManifestReport dataclass."""

    def test_default_values(self):
        """Test default values."""
        report = ManifestReport(series="dalmatian", cycle_stage="pre_final")
        assert report.packages == []
        assert report.build_order == []
        assert report.release_count == 0

    def test_to_dict(self):
        """Test conversion to dictionary."""
        report = ManifestReport(
            series="dalmatian",
            cycle_stage="pre_final",
            build_order=["oslo.config", "nova"],
        )
        d = report.to_dict()
        assert d["series"] == "dalmatian"
        assert d["build_order"] == ["oslo.config", "nova"]
        assert "stats" in d


class TestCreateManifestReport:
    """Tests for create_manifest_report function."""

    def test_empty_manifest(self):
        """Test with empty manifest."""
        manifest = BuildManifest(series="dalmatian", cycle_stage=CycleStage.PRE_FINAL)
        report = create_manifest_report(manifest)

        assert report.series == "dalmatian"
        assert report.cycle_stage == "pre_final"
        assert len(report.packages) == 0

    def test_with_packages(self):
        """Test with packages in manifest."""
        manifest = BuildManifest(
            series="dalmatian",
            cycle_stage=CycleStage.PRE_FINAL,
            build_order=["oslo.config", "nova"],
        )
        manifest.add_package(
            source_package="oslo.config",
            deliverable="oslo.config",
            upstream_version="9.0.0",
            debian_revision="0ubuntu1",
            build_type=BuildType.RELEASE,
        )
        manifest.add_package(
            source_package="nova",
            deliverable="nova",
            upstream_version="29.0.0.dev5",
            debian_revision="0ubuntu1",
            build_type=BuildType.SNAPSHOT,
        )

        report = create_manifest_report(manifest)

        assert len(report.packages) == 2
        assert report.build_order == ["oslo.config", "nova"]
        assert report.release_count == 1
        assert report.snapshot_count == 1


class TestRenderManifestReportText:
    """Tests for render_manifest_report_text function."""

    def test_basic_render(self):
        """Test basic text rendering."""
        report = ManifestReport(
            series="dalmatian",
            cycle_stage="pre_final",
            build_order=["nova"],
            packages=[{
                "source_package": "nova",
                "deliverable": "nova",
                "upstream_version": "29.0.0",
                "full_version": "29.0.0-0ubuntu1",
                "build_type": "release",
                "version_source": "openstack/releases",
            }],
            release_count=1,
        )
        text = render_manifest_report_text(report)

        assert "Build Manifest Report" in text
        assert "dalmatian" in text
        assert "nova" in text
        assert "RELEASE" in text


class TestRenderManifestReportJson:
    """Tests for render_manifest_report_json function."""

    def test_valid_json(self):
        """Test that output is valid JSON."""
        report = ManifestReport(series="dalmatian", cycle_stage="pre_final")
        json_str = render_manifest_report_json(report)

        data = json.loads(json_str)
        assert data["series"] == "dalmatian"


class TestSaveManifestReport:
    """Tests for save_manifest_report function."""

    def test_saves_both_formats(self, tmp_path: Path):
        """Test saving both text and JSON formats."""
        report = ManifestReport(series="dalmatian", cycle_stage="pre_final")
        saved = save_manifest_report(report, tmp_path)

        assert len(saved) == 2
        assert all(p.exists() for p in saved)
