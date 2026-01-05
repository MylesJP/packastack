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

"""Tests for type selection report renderers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
    WatchInfo,
)
from packastack.reports.type_selection import (
    render_compact_summary,
    render_console_table,
    render_html,
    render_json,
    write_type_selection_reports,
)


@pytest.fixture
def sample_report() -> TypeSelectionReport:
    """Create a sample TypeSelectionReport for testing."""
    report = TypeSelectionReport(
        run_id="test-run-123",
        target="dalmatian",
        ubuntu_series="plucky",
        generated_at_utc="2025-01-01T00:00:00Z",
        type_mode="auto",
        cycle_stage=CycleStage.PRE_FINAL,
    )

    # Add a release result
    report.add_result(
        TypeSelectionResult(
            source_package="nova",
            deliverable="nova",
            release_model="cycle-with-rc",
            deliverable_kind=DeliverableKind.SERVICE,
            kind_confidence=KindConfidence.METADATA,
            has_release_for_cycle=True,
            has_beta_rc_final=True,
            latest_version="26.0.0b1",
            cycle_stage=CycleStage.PRE_FINAL,
            chosen_type=BuildType.RELEASE,
            reason_code=ReasonCode.HAS_RELEASE,
            reason_human="Beta release 26.0.0b1 available",
            package_status=PackageStatus.ACTIVE,
        )
    )

    # Add a milestone result
    report.add_result(
        TypeSelectionResult(
            source_package="glance",
            deliverable="glance",
            release_model="cycle-with-rc",
            deliverable_kind=DeliverableKind.SERVICE,
            kind_confidence=KindConfidence.HEURISTIC,
            has_release_for_cycle=True,
            has_beta_rc_final=False,
            latest_version="26.0.0.0a1",
            cycle_stage=CycleStage.PRE_FINAL,
            chosen_type=BuildType.MILESTONE,
            reason_code=ReasonCode.HAS_MILESTONE_ONLY,
            reason_human="Only milestone release 26.0.0.0a1",
            package_status=PackageStatus.ACTIVE,
        )
    )

    # Add a snapshot result
    report.add_result(
        TypeSelectionResult(
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
            reason_human="Project not in openstack/releases",
            package_status=PackageStatus.NEW,
        )
    )

    return report


@pytest.fixture
def report_with_watch_and_retired() -> TypeSelectionReport:
    """Create a report with watch info and retired packages."""
    report = TypeSelectionReport(
        run_id="test-run-456",
        target="dalmatian",
        ubuntu_series="plucky",
        generated_at_utc="2025-01-02T00:00:00Z",
        type_mode="auto",
        cycle_stage=CycleStage.PRE_FINAL,
    )

    watch_info = WatchInfo(
        parsed=True,
        mode="openstack_tarball",
        uscan_attempted=True,
        uscan_status="newer_available",
        uscan_error="network",
        packaged_version="1.0.0",
        upstream_version="1.1.0",
        newer_available=True,
    )
    upstream_resolution = UpstreamResolution(
        authority=UpstreamAuthority.WATCH,
        watch_used=True,
        uscan_used=True,
        reason="watch",
        upstream_version="1.1.0",
        download_url="https://example.com/nova.tar.gz",
    )
    report.add_result(
        TypeSelectionResult(
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
            package_status=PackageStatus.ACTIVE,
            watch_info=watch_info,
            upstream_resolution=upstream_resolution,
        )
    )
    report.add_result(
        TypeSelectionResult(
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
            reason_human="Retired upstream",
            package_status=PackageStatus.RETIRED,
        )
    )
    report.add_result(
        TypeSelectionResult(
            source_package="defunct-pkg",
            deliverable="defunct-pkg",
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
    )
    report.missing_upstream = ["missing-upstream"]
    report.missing_packaging = ["missing-packaging"]
    report.needs_upstream_mapping = ["needs-mapping"]
    return report


class TestRenderJson:
    """Tests for render_json."""

    def test_writes_json_file(self, tmp_path: Path, sample_report: TypeSelectionReport):
        """Should write a valid JSON file."""
        output_path = tmp_path / "type-selection.json"
        result_path = render_json(sample_report, output_path)

        assert result_path == output_path
        assert output_path.exists()

        data = json.loads(output_path.read_text())
        assert data["run_id"] == "test-run-123"
        assert data["target"] == "dalmatian"

    def test_json_contains_summary(self, tmp_path: Path, sample_report: TypeSelectionReport):
        """Should contain summary counts."""
        output_path = tmp_path / "type-selection.json"
        render_json(sample_report, output_path)

        data = json.loads(output_path.read_text())
        assert "summary" in data
        assert data["summary"]["release"] == 1
        assert data["summary"]["milestone"] == 1
        assert data["summary"]["snapshot"] == 1

    def test_json_contains_packages(self, tmp_path: Path, sample_report: TypeSelectionReport):
        """Should contain package results."""
        output_path = tmp_path / "type-selection.json"
        render_json(sample_report, output_path)

        data = json.loads(output_path.read_text())
        assert "packages" in data
        assert len(data["packages"]) == 3

    def test_creates_parent_directory(self, tmp_path: Path, sample_report: TypeSelectionReport):
        """Should create parent directories if needed."""
        output_path = tmp_path / "nested" / "dir" / "type-selection.json"
        render_json(sample_report, output_path)

        assert output_path.exists()


class TestRenderHtml:
    """Tests for render_html."""

    def test_writes_html_file(self, tmp_path: Path, sample_report: TypeSelectionReport):
        """Should write an HTML file."""
        output_path = tmp_path / "type-selection.html"
        result_path = render_html(sample_report, output_path)

        assert result_path == output_path
        assert output_path.exists()

    def test_html_contains_header(self, tmp_path: Path, sample_report: TypeSelectionReport):
        """Should contain proper HTML structure."""
        output_path = tmp_path / "type-selection.html"
        render_html(sample_report, output_path)

        content = output_path.read_text()
        assert "<!DOCTYPE html>" in content
        assert "<html" in content
        assert "</html>" in content

    def test_html_contains_summary(self, tmp_path: Path, sample_report: TypeSelectionReport):
        """Should contain summary cards."""
        output_path = tmp_path / "type-selection.html"
        render_html(sample_report, output_path)

        content = output_path.read_text()
        assert "Total Packages" in content
        assert "Release" in content
        assert "Milestone" in content
        assert "Snapshot" in content

    def test_html_contains_package_table(self, tmp_path: Path, sample_report: TypeSelectionReport):
        """Should contain packages table."""
        output_path = tmp_path / "type-selection.html"
        render_html(sample_report, output_path)

        content = output_path.read_text()
        assert "nova" in content
        assert "glance" in content
        assert "new-pkg" in content

    def test_html_escapes_content(self, tmp_path: Path):
        """Should escape special HTML characters."""
        report = TypeSelectionReport(
            run_id="test",
            target="test",
            ubuntu_series="test",
            generated_at_utc="2025-01-01T00:00:00Z",
            type_mode="auto",
            cycle_stage=CycleStage.PRE_FINAL,
        )
        report.add_result(
            TypeSelectionResult(
                source_package="pkg<script>alert('xss')</script>",
                deliverable="pkg",
                release_model="",
                deliverable_kind=DeliverableKind.UNKNOWN,
                kind_confidence=KindConfidence.DEFAULT,
                has_release_for_cycle=False,
                has_beta_rc_final=False,
                latest_version="",
                cycle_stage=CycleStage.PRE_FINAL,
                chosen_type=BuildType.SNAPSHOT,
                reason_code=ReasonCode.NO_RELEASE_YET,
                reason_human="Test <script>",
            )
        )

        output_path = tmp_path / "type-selection.html"
        render_html(report, output_path)

        content = output_path.read_text()
        # Should be escaped
        assert "&lt;script&gt;" in content
        assert "<script>alert" not in content

    def test_html_includes_watch_and_retired_sections(
        self, tmp_path: Path, report_with_watch_and_retired: TypeSelectionReport
    ) -> None:
        """Should render watch details and retired sections."""
        output_path = tmp_path / "type-selection.html"
        render_html(report_with_watch_and_retired, output_path)

        content = output_path.read_text()
        assert "Retired Projects" in content
        assert "Needs upstreams.yaml mapping" in content
        assert "Watch mode" in content
        assert "Uscan error" in content
        assert "Cross-Reference Warnings" in content


class TestRenderConsoleTable:
    """Tests for render_console_table."""

    def test_returns_string(self, sample_report: TypeSelectionReport):
        """Should return a string."""
        result = render_console_table(sample_report)
        assert isinstance(result, str)

    def test_contains_header(self, sample_report: TypeSelectionReport):
        """Should contain column headers."""
        result = render_console_table(sample_report)
        assert "source_package" in result
        assert "type" in result
        assert "reason_code" in result

    def test_contains_packages(self, sample_report: TypeSelectionReport):
        """Should contain package names."""
        result = render_console_table(sample_report)
        assert "nova" in result
        assert "glance" in result
        assert "new-pkg" in result

    def test_explain_mode_adds_column(self, sample_report: TypeSelectionReport):
        """Should add reason_human column in explain mode."""
        result_no_explain = render_console_table(sample_report, explain=False)
        result_explain = render_console_table(sample_report, explain=True)

        assert "reason_human" not in result_no_explain
        assert "reason_human" in result_explain
        assert "Beta release" in result_explain

    def test_explain_mode_includes_watch_fields(
        self, report_with_watch_and_retired: TypeSelectionReport
    ) -> None:
        """Should include authority/watch/uscan fields when present."""
        result = render_console_table(report_with_watch_and_retired, explain=True)

        assert "watch" in result
        assert "yes" in result
        assert "newer_avai" in result


class TestRenderCompactSummary:
    """Tests for render_compact_summary."""

    def test_returns_string(self, sample_report: TypeSelectionReport):
        """Should return a string."""
        result = render_compact_summary(sample_report)
        assert isinstance(result, str)

    def test_contains_mode(self, sample_report: TypeSelectionReport):
        """Should show the type selection mode."""
        result = render_compact_summary(sample_report)
        assert "auto" in result

    def test_contains_counts(self, sample_report: TypeSelectionReport):
        """Should show counts by type."""
        result = render_compact_summary(sample_report)
        assert "Release:" in result
        assert "Milestone:" in result
        assert "Snapshot:" in result

    def test_contains_examples(self, sample_report: TypeSelectionReport):
        """Should show example packages."""
        result = render_compact_summary(sample_report)
        assert "nova" in result

    def test_warns_about_new_packages(self, sample_report: TypeSelectionReport):
        """Should warn about new packages."""
        result = render_compact_summary(sample_report)
        assert "New packages" in result

    def test_hints_table_option(self, sample_report: TypeSelectionReport):
        """Should hint about --table option."""
        result = render_compact_summary(sample_report)
        assert "--table" in result

    def test_expands_long_lists(self) -> None:
        """Should show truncation hints for long lists."""
        report = TypeSelectionReport(
            run_id="test",
            target="dalmatian",
            ubuntu_series="plucky",
            generated_at_utc="2025-01-01T00:00:00Z",
            type_mode="auto",
            cycle_stage=CycleStage.PRE_FINAL,
        )
        for i in range(6):
            report.add_result(
                TypeSelectionResult(
                    source_package=f"pkg{i}",
                    deliverable=f"pkg{i}",
                    release_model="",
                    deliverable_kind=DeliverableKind.UNKNOWN,
                    kind_confidence=KindConfidence.DEFAULT,
                    has_release_for_cycle=False,
                    has_beta_rc_final=False,
                    latest_version="",
                    cycle_stage=CycleStage.PRE_FINAL,
                    chosen_type=BuildType.SNAPSHOT,
                    reason_code=ReasonCode.NOT_IN_RELEASES,
                    reason_human="",
                    package_status=PackageStatus.NEW,
                )
            )
        report.defunct_packages = ["defunct1", "defunct2", "defunct3", "defunct4"]
        report.missing_upstream = ["up1", "up2", "up3", "up4"]
        report.missing_packaging = ["pkg1", "pkg2", "pkg3", "pkg4"]

        result = render_compact_summary(report)

        assert "... (1 more packages)" in result
        assert "and 1 more" in result


class TestWriteTypeSelectionReports:
    """Tests for write_type_selection_reports."""

    def test_writes_both_files(self, tmp_path: Path, sample_report: TypeSelectionReport):
        """Should write both JSON and HTML files."""
        reports_dir = tmp_path / "reports"
        result = write_type_selection_reports(sample_report, reports_dir)

        assert "json" in result
        assert "html" in result
        assert result["json"].exists()
        assert result["html"].exists()

    def test_creates_reports_directory(self, tmp_path: Path, sample_report: TypeSelectionReport):
        """Should create reports directory."""
        reports_dir = tmp_path / "nested" / "reports"
        write_type_selection_reports(sample_report, reports_dir)

        assert reports_dir.exists()

    def test_uses_correct_filenames(self, tmp_path: Path, sample_report: TypeSelectionReport):
        """Should use type-selection.{json,html} filenames."""
        reports_dir = tmp_path / "reports"
        result = write_type_selection_reports(sample_report, reports_dir)

        assert result["json"].name == "type-selection.json"
        assert result["html"].name == "type-selection.html"
