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

"""Report renderers for watch file resolution and uscan results.

Generates dedicated JSON and HTML reports showing debian/watch parsing
status, uscan execution outcomes, and version comparisons.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from packastack.planning.type_selection import TypeSelectionReport


@dataclass
class WatchResolutionEntry:
    """Single entry in the watch resolution report."""

    source_package: str
    deliverable: str
    watch_parsed: bool
    watch_mode: str
    uscan_attempted: bool
    uscan_status: str
    uscan_error: str
    packaged_version: str
    upstream_version: str
    newer_available: bool
    download_url: str
    authority: str
    chosen_type: str
    reason_code: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "source_package": self.source_package,
            "deliverable": self.deliverable,
            "watch_parsed": self.watch_parsed,
            "watch_mode": self.watch_mode,
            "uscan_attempted": self.uscan_attempted,
            "uscan_status": self.uscan_status,
            "uscan_error": self.uscan_error,
            "packaged_version": self.packaged_version,
            "upstream_version": self.upstream_version,
            "newer_available": self.newer_available,
            "download_url": self.download_url,
            "authority": self.authority,
            "chosen_type": self.chosen_type,
            "reason_code": self.reason_code,
        }


@dataclass
class WatchResolutionReport:
    """Complete watch resolution report."""

    run_id: str
    target: str
    ubuntu_series: str
    generated_at_utc: str
    entries: list[WatchResolutionEntry] = field(default_factory=list)

    # Summary counts
    total_packages: int = 0
    watch_parsed_count: int = 0
    uscan_attempted_count: int = 0
    uscan_success_count: int = 0
    newer_available_count: int = 0
    uscan_error_count: int = 0

    # Counts by watch mode
    counts_by_mode: dict[str, int] = field(default_factory=dict)

    # Counts by uscan status
    counts_by_uscan_status: dict[str, int] = field(default_factory=dict)

    def add_entry(self, entry: WatchResolutionEntry) -> None:
        """Add an entry and update counts."""
        self.entries.append(entry)
        self.total_packages += 1

        if entry.watch_parsed:
            self.watch_parsed_count += 1

        if entry.uscan_attempted:
            self.uscan_attempted_count += 1
            if entry.uscan_status == "success" or entry.uscan_status in ("up_to_date", "newer_available"):
                self.uscan_success_count += 1
            if entry.uscan_error:
                self.uscan_error_count += 1

        if entry.newer_available:
            self.newer_available_count += 1

        # Count by mode
        mode = entry.watch_mode or "unknown"
        self.counts_by_mode[mode] = self.counts_by_mode.get(mode, 0) + 1

        # Count by uscan status
        if entry.uscan_attempted:
            status = entry.uscan_status or "unknown"
            self.counts_by_uscan_status[status] = self.counts_by_uscan_status.get(status, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "run_id": self.run_id,
            "target": self.target,
            "ubuntu_series": self.ubuntu_series,
            "generated_at_utc": self.generated_at_utc,
            "summary": {
                "total_packages": self.total_packages,
                "watch_parsed": self.watch_parsed_count,
                "uscan_attempted": self.uscan_attempted_count,
                "uscan_success": self.uscan_success_count,
                "newer_available": self.newer_available_count,
                "uscan_errors": self.uscan_error_count,
            },
            "counts_by_mode": self.counts_by_mode,
            "counts_by_uscan_status": self.counts_by_uscan_status,
            "entries": [e.to_dict() for e in self.entries],
        }


def build_watch_resolution_report(
    type_report: "TypeSelectionReport",
) -> WatchResolutionReport:
    """Build a watch resolution report from a type selection report.

    Args:
        type_report: The type selection report to extract watch info from.

    Returns:
        WatchResolutionReport with watch/uscan details.
    """
    report = WatchResolutionReport(
        run_id=type_report.run_id,
        target=type_report.target,
        ubuntu_series=type_report.ubuntu_series,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
    )

    for pkg in type_report.packages:
        # Extract watch info
        watch_parsed = False
        watch_mode = "unknown"
        uscan_attempted = False
        uscan_status = ""
        uscan_error = ""
        packaged_version = ""
        upstream_version = ""
        newer_available = False
        download_url = ""
        authority = "none"

        if pkg.watch_info:
            watch_parsed = pkg.watch_info.parsed
            watch_mode = pkg.watch_info.mode
            uscan_attempted = pkg.watch_info.uscan_attempted
            uscan_status = pkg.watch_info.uscan_status
            uscan_error = pkg.watch_info.uscan_error
            packaged_version = pkg.watch_info.packaged_version
            upstream_version = pkg.watch_info.upstream_version
            newer_available = pkg.watch_info.newer_available

        if pkg.upstream_resolution:
            authority = pkg.upstream_resolution.authority.value if hasattr(pkg.upstream_resolution.authority, 'value') else str(pkg.upstream_resolution.authority)
            download_url = pkg.upstream_resolution.download_url

        entry = WatchResolutionEntry(
            source_package=pkg.source_package,
            deliverable=pkg.deliverable,
            watch_parsed=watch_parsed,
            watch_mode=watch_mode,
            uscan_attempted=uscan_attempted,
            uscan_status=uscan_status,
            uscan_error=uscan_error,
            packaged_version=packaged_version,
            upstream_version=upstream_version,
            newer_available=newer_available,
            download_url=download_url,
            authority=authority,
            chosen_type=pkg.chosen_type.value,
            reason_code=pkg.reason_code.value,
        )
        report.add_entry(entry)

    return report


def render_json(report: WatchResolutionReport, output_path: Path) -> Path:
    """Render watch resolution report as JSON.

    Args:
        report: The watch resolution report.
        output_path: Path to write the JSON file.

    Returns:
        Path to the written file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report.to_dict(), indent=2))
    return output_path


def render_html(report: WatchResolutionReport, output_path: Path) -> Path:
    """Render watch resolution report as self-contained HTML.

    Args:
        report: The watch resolution report.
        output_path: Path to write the HTML file.

    Returns:
        Path to the written file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def esc(s: str) -> str:
        return html.escape(str(s))

    # Build summary cards
    summary_html = f"""
    <div class="summary-cards">
        <div class="card">
            <div class="card-title">Total Packages</div>
            <div class="card-value">{report.total_packages}</div>
        </div>
        <div class="card card-parsed">
            <div class="card-title">Watch Parsed</div>
            <div class="card-value">{report.watch_parsed_count}</div>
        </div>
        <div class="card card-uscan">
            <div class="card-title">Uscan Run</div>
            <div class="card-value">{report.uscan_attempted_count}</div>
        </div>
        <div class="card card-success">
            <div class="card-title">Uscan Success</div>
            <div class="card-value">{report.uscan_success_count}</div>
        </div>
        <div class="card card-newer">
            <div class="card-title">Newer Available</div>
            <div class="card-value">{report.newer_available_count}</div>
        </div>
        <div class="card card-error">
            <div class="card-title">Uscan Errors</div>
            <div class="card-value">{report.uscan_error_count}</div>
        </div>
    </div>
    """

    # Build mode breakdown
    mode_rows = []
    for mode, count in sorted(report.counts_by_mode.items(), key=lambda x: -x[1]):
        mode_rows.append(f"<tr><td>{esc(mode)}</td><td>{count}</td></tr>")
    mode_table = f"""
    <table class="breakdown-table">
        <thead><tr><th>Watch Mode</th><th>Count</th></tr></thead>
        <tbody>{''.join(mode_rows)}</tbody>
    </table>
    """ if mode_rows else "<p>No data</p>"

    # Build uscan status breakdown
    status_rows = []
    for status, count in sorted(report.counts_by_uscan_status.items(), key=lambda x: -x[1]):
        status_rows.append(f"<tr><td>{esc(status)}</td><td>{count}</td></tr>")
    status_table = f"""
    <table class="breakdown-table">
        <thead><tr><th>Uscan Status</th><th>Count</th></tr></thead>
        <tbody>{''.join(status_rows)}</tbody>
    </table>
    """ if status_rows else "<p>No uscan runs</p>"

    # Build main table rows
    table_rows = []
    for entry in report.entries:
        newer_badge = '<span class="badge badge-new">NEW</span>' if entry.newer_available else ''
        error_badge = '<span class="badge badge-error">ERR</span>' if entry.uscan_error else ''

        row_class = ""
        if entry.newer_available:
            row_class = "has-newer"
        elif entry.uscan_error:
            row_class = "has-error"

        table_rows.append(f"""
        <tr class="{row_class}">
            <td>{esc(entry.source_package)}</td>
            <td>{esc(entry.deliverable)}</td>
            <td>{"✓" if entry.watch_parsed else "✗"}</td>
            <td>{esc(entry.watch_mode)}</td>
            <td>{"✓" if entry.uscan_attempted else "-"}</td>
            <td>{esc(entry.uscan_status) or "-"} {error_badge}</td>
            <td>{esc(entry.packaged_version) or "-"}</td>
            <td>{esc(entry.upstream_version) or "-"} {newer_badge}</td>
            <td>{esc(entry.authority)}</td>
            <td>{esc(entry.chosen_type)}</td>
            <td>
                <details>
                    <summary>Details</summary>
                    <p>Reason: {esc(entry.reason_code)}</p>
                    {f'<p>Error: {esc(entry.uscan_error)}</p>' if entry.uscan_error else ''}
                    {f'<p>URL: <a href="{esc(entry.download_url)}">{esc(entry.download_url[:60])}...</a></p>' if entry.download_url else ''}
                </details>
            </td>
        </tr>
        """)

    main_table = f"""
    <table class="main-table">
        <thead>
            <tr>
                <th>Source Package</th>
                <th>Deliverable</th>
                <th>Watch</th>
                <th>Mode</th>
                <th>Uscan</th>
                <th>Status</th>
                <th>Packaged</th>
                <th>Upstream</th>
                <th>Authority</th>
                <th>Type</th>
                <th>Details</th>
            </tr>
        </thead>
        <tbody>
            {''.join(table_rows)}
        </tbody>
    </table>
    """

    # Complete HTML document
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Watch Resolution Report: {esc(report.run_id)}</title>
    <style>
        :root {{
            --color-success: #28a745;
            --color-warning: #ffc107;
            --color-error: #dc3545;
            --color-info: #17a2b8;
            --color-bg: #f8f9fa;
            --color-border: #dee2e6;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            margin: 0;
            padding: 20px;
            background: var(--color-bg);
            color: #212529;
        }}
        .container {{
            max-width: 1600px;
            margin: 0 auto;
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        h1 {{ margin-top: 0; border-bottom: 2px solid var(--color-border); padding-bottom: 10px; }}
        h2 {{ margin-top: 30px; color: #495057; }}
        h3 {{ margin-top: 20px; color: #6c757d; }}
        .header-info {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            background: var(--color-bg);
            padding: 15px;
            border-radius: 6px;
            margin-bottom: 20px;
        }}
        .header-item {{ }}
        .header-label {{ font-weight: bold; color: #6c757d; font-size: 0.85em; }}
        .header-value {{ font-size: 1.1em; }}
        .summary-cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 15px;
            margin: 20px 0;
        }}
        .card {{
            background: var(--color-bg);
            padding: 15px;
            border-radius: 8px;
            text-align: center;
        }}
        .card-title {{ font-size: 0.85em; color: #6c757d; margin-bottom: 5px; }}
        .card-value {{ font-size: 1.8em; font-weight: bold; }}
        .card-parsed {{ border-left: 4px solid var(--color-info); }}
        .card-uscan {{ border-left: 4px solid #6c757d; }}
        .card-success {{ border-left: 4px solid var(--color-success); }}
        .card-success .card-value {{ color: var(--color-success); }}
        .card-newer {{ border-left: 4px solid var(--color-warning); }}
        .card-newer .card-value {{ color: #856404; }}
        .card-error {{ border-left: 4px solid var(--color-error); }}
        .card-error .card-value {{ color: var(--color-error); }}
        .breakdown-section {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin: 20px 0;
        }}
        .breakdown-table {{
            width: 100%;
            border-collapse: collapse;
        }}
        .breakdown-table th, .breakdown-table td {{
            padding: 8px 12px;
            text-align: left;
            border-bottom: 1px solid var(--color-border);
        }}
        .breakdown-table th {{ background: var(--color-bg); font-weight: 600; }}
        .badge {{
            display: inline-block;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 0.75em;
            font-weight: bold;
            margin-left: 5px;
        }}
        .badge-new {{ background: var(--color-warning); color: #212529; }}
        .badge-error {{ background: var(--color-error); color: white; }}
        .main-table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            font-size: 0.85em;
        }}
        .main-table th, .main-table td {{
            padding: 8px 6px;
            text-align: left;
            border-bottom: 1px solid var(--color-border);
        }}
        .main-table th {{
            background: #343a40;
            color: white;
            position: sticky;
            top: 0;
        }}
        .main-table tbody tr:hover {{ background: #f1f3f5; }}
        .has-newer {{ background: #fff3cd; }}
        .has-error {{ background: #f8d7da; }}
        details {{ cursor: pointer; }}
        details summary {{ color: #007bff; }}
        details p {{ margin: 5px 0; font-size: 0.9em; color: #6c757d; }}
        .footer {{
            margin-top: 30px;
            padding-top: 15px;
            border-top: 1px solid var(--color-border);
            color: #6c757d;
            font-size: 0.85em;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Watch Resolution Report</h1>

        <div class="header-info">
            <div class="header-item">
                <div class="header-label">Run ID</div>
                <div class="header-value">{esc(report.run_id)}</div>
            </div>
            <div class="header-item">
                <div class="header-label">Target</div>
                <div class="header-value">{esc(report.target)}</div>
            </div>
            <div class="header-item">
                <div class="header-label">Ubuntu Series</div>
                <div class="header-value">{esc(report.ubuntu_series)}</div>
            </div>
            <div class="header-item">
                <div class="header-label">Generated (UTC)</div>
                <div class="header-value">{esc(report.generated_at_utc)}</div>
            </div>
        </div>

        <h2>Summary</h2>
        {summary_html}

        <h2>Breakdown</h2>
        <div class="breakdown-section">
            <div>
                <h3>By Watch Mode</h3>
                {mode_table}
            </div>
            <div>
                <h3>By Uscan Status</h3>
                {status_table}
            </div>
        </div>

        <h2>All Packages</h2>
        {main_table}

        <div class="footer">
            Generated by PackaStack • {esc(report.generated_at_utc)}
        </div>
    </div>
</body>
</html>
"""

    output_path.write_text(html_content)
    return output_path


def write_watch_resolution_reports(
    type_report: "TypeSelectionReport",
    reports_dir: Path,
) -> dict[str, Path]:
    """Write both JSON and HTML watch resolution reports.

    Args:
        type_report: The type selection report to extract watch info from.
        reports_dir: Directory to write reports.

    Returns:
        Dict with 'json' and 'html' keys mapping to the written paths.
    """
    watch_report = build_watch_resolution_report(type_report)
    json_path = render_json(watch_report, reports_dir / "watch-resolution.json")
    html_path = render_html(watch_report, reports_dir / "watch-resolution.html")
    return {"json": json_path, "html": html_path}
