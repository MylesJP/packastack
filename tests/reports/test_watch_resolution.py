# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for watch resolution report rendering."""

from __future__ import annotations

from pathlib import Path

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
from packastack.reports.watch_resolution import (
    build_watch_resolution_report,
    render_html,
    render_json,
    write_watch_resolution_reports,
)


def _make_result(
    source_package: str,
    watch_info: WatchInfo | None = None,
    upstream_resolution: UpstreamResolution | None = None,
    chosen_type: BuildType = BuildType.RELEASE,
    reason_code: ReasonCode = ReasonCode.HAS_RELEASE,
) -> TypeSelectionResult:
    return TypeSelectionResult(
        source_package=source_package,
        deliverable=source_package,
        release_model="cycle-with-rc",
        deliverable_kind=DeliverableKind.SERVICE,
        kind_confidence=KindConfidence.METADATA,
        has_release_for_cycle=True,
        has_beta_rc_final=True,
        latest_version="1.0.0",
        cycle_stage=CycleStage.PRE_FINAL,
        chosen_type=chosen_type,
        reason_code=reason_code,
        reason_human="test",
        package_status=PackageStatus.ACTIVE,
        watch_info=watch_info,
        upstream_resolution=upstream_resolution,
    )


def _build_type_report() -> TypeSelectionReport:
    report = TypeSelectionReport(
        run_id="run-1",
        target="dalmatian",
        ubuntu_series="noble",
        generated_at_utc="2025-01-01T00:00:00Z",
        type_mode="auto",
        cycle_stage=CycleStage.PRE_FINAL,
    )

    watch_info_ok = WatchInfo(
        parsed=True,
        mode="openstack_tarball",
        uscan_attempted=True,
        uscan_status="newer_available",
        uscan_error="",
        packaged_version="1.0.0",
        upstream_version="1.1.0",
        newer_available=True,
    )
    upstream_ok = UpstreamResolution(
        authority=UpstreamAuthority.RELEASES,
        watch_used=True,
        uscan_used=True,
        reason="release",
        upstream_version="1.1.0",
        download_url="https://example.com/nova.tar.gz",
    )
    report.add_result(_make_result("nova", watch_info_ok, upstream_ok))

    watch_info_err = WatchInfo(
        parsed=False,
        mode="unknown",
        uscan_attempted=True,
        uscan_status="error",
        uscan_error="network",
        packaged_version="",
        upstream_version="",
        newer_available=False,
    )
    upstream_other = UpstreamResolution(
        authority="custom",
        watch_used=False,
        uscan_used=False,
        reason="manual",
        upstream_version="",
        download_url="",
    )
    report.add_result(_make_result("glance", watch_info_err, upstream_other))

    return report


class TestBuildWatchResolutionReport:
    """Tests for build_watch_resolution_report."""

    def test_builds_counts(self) -> None:
        """Should compute summary counts from type selection report."""
        report = _build_type_report()
        watch_report = build_watch_resolution_report(report)

        assert watch_report.total_packages == 2
        assert watch_report.watch_parsed_count == 1
        assert watch_report.uscan_attempted_count == 2
        assert watch_report.uscan_success_count == 1
        assert watch_report.uscan_error_count == 1
        assert watch_report.newer_available_count == 1
        assert watch_report.counts_by_mode["openstack_tarball"] == 1
        assert watch_report.counts_by_uscan_status["newer_available"] == 1


class TestRenderWatchResolutionReports:
    """Tests for render_json/render_html."""

    def test_renders_json_and_html(self, tmp_path: Path) -> None:
        """Should write JSON and HTML outputs."""
        report = _build_type_report()
        watch_report = build_watch_resolution_report(report)

        json_path = render_json(watch_report, tmp_path / "watch.json")
        html_path = render_html(watch_report, tmp_path / "watch.html")

        assert json_path.exists()
        assert html_path.exists()
        html = html_path.read_text()
        assert "Watch Resolution Report" in html
        assert "badge-new" in html
        assert "badge-error" in html


class TestWriteWatchResolutionReports:
    """Tests for write_watch_resolution_reports."""

    def test_writes_report_files(self, tmp_path: Path) -> None:
        """Should write both JSON and HTML reports."""
        report = _build_type_report()
        result = write_watch_resolution_reports(report, tmp_path)

        assert result["json"].exists()
        assert result["html"].exists()
