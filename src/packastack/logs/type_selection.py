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

"""Report renderers for type selection results.

Generates JSON, HTML, and console table output from TypeSelectionReport.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from packastack.planning.type_selection import TypeSelectionReport


def render_json(report: TypeSelectionReport, output_path: Path) -> Path:
    """Render type selection report as JSON.

    Args:
        report: The type selection report.
        output_path: Path to write the JSON file.

    Returns:
        Path to the written file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report.to_dict(), indent=2))
    return output_path


def render_html(report: TypeSelectionReport, output_path: Path) -> Path:
    """Render type selection report as self-contained HTML.

    Args:
        report: The type selection report.
        output_path: Path to write the HTML file.

    Returns:
        Path to the written file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Escape helper
    def esc(s: str) -> str:
        return html.escape(str(s))

    # Build summary cards
    total = len(report.packages)
    retired_count = getattr(report, 'count_retired', 0)
    summary_html = f"""
    <div class="summary-cards">
        <div class="card">
            <div class="card-title">Total Packages</div>
            <div class="card-value">{total}</div>
        </div>
        <div class="card card-release">
            <div class="card-title">Release</div>
            <div class="card-value">{report.count_release}</div>
        </div>
        <div class="card card-snapshot">
            <div class="card-title">Snapshot</div>
            <div class="card-value">{report.count_snapshot}</div>
        </div>
        <div class="card card-retired">
            <div class="card-title">Retired</div>
            <div class="card-value">{retired_count}</div>
        </div>
    </div>
    """

    # Build reason breakdown
    reason_rows = []
    for reason, count in sorted(report.counts_by_reason.items(), key=lambda x: -x[1]):
        reason_rows.append(f"<tr><td>{esc(reason)}</td><td>{count}</td></tr>")
    reason_table = f"""
    <table class="breakdown-table">
        <thead><tr><th>Reason Code</th><th>Count</th></tr></thead>
        <tbody>{''.join(reason_rows)}</tbody>
    </table>
    """ if reason_rows else "<p>No data</p>"

    # Build cycle stage breakdown
    stage_rows = []
    for stage, count in sorted(report.counts_by_stage.items(), key=lambda x: -x[1]):
        stage_rows.append(f"<tr><td>{esc(stage)}</td><td>{count}</td></tr>")
    stage_table = f"""
    <table class="breakdown-table">
        <thead><tr><th>Cycle Stage</th><th>Count</th></tr></thead>
        <tbody>{''.join(stage_rows)}</tbody>
    </table>
    """ if stage_rows else "<p>No data</p>"

    # New/defunct packages section
    new_defunct_html = ""
    if report.new_packages or report.defunct_packages:
        new_list = ", ".join(esc(p) for p in report.new_packages) if report.new_packages else "None"
        defunct_list = ", ".join(esc(p) for p in report.defunct_packages) if report.defunct_packages else "None"
        new_defunct_html = f"""
        <div class="alert-section">
            <h3>⚠️ Package Status Changes</h3>
            <div class="alert new-packages">
                <strong>New packages</strong> (in local cache but not in releases): {new_list}
            </div>
            <div class="alert defunct-packages">
                <strong>Defunct packages</strong> (in releases but not in local cache): {defunct_list}
            </div>
        </div>
        """

    # Cross-reference warnings section
    crossref_html = ""
    if report.missing_upstream or report.missing_packaging:
        missing_upstream_list = ", ".join(esc(p) for p in report.missing_upstream) if report.missing_upstream else "None"
        missing_packaging_list = ", ".join(esc(p) for p in report.missing_packaging) if report.missing_packaging else "None"
        crossref_html = f"""
        <div class="alert-section">
            <h3>⚠️ Cross-Reference Warnings</h3>
            <div class="alert missing-upstream">
                <strong>No upstream definition</strong> (not in releases or upstreams.yaml): {missing_upstream_list}
            </div>
            <div class="alert missing-packaging">
                <strong>Missing packaging repo</strong> (library/service in releases without packaging): {missing_packaging_list}
            </div>
        </div>
        """

    # Retired packages section
    retired_html = ""
    retired_packages = getattr(report, 'retired_packages', set())
    needs_mapping = getattr(report, 'needs_upstream_mapping', set())
    if retired_packages or needs_mapping:
        retired_list = ", ".join(esc(p) for p in sorted(retired_packages)) if retired_packages else "None"
        mapping_list = ", ".join(esc(p) for p in sorted(needs_mapping)) if needs_mapping else "None"
        retired_html = f"""
        <div class="alert-section">
            <h3>🚫 Retired Projects</h3>
            <div class="alert retired-packages">
                <strong>Retired upstream</strong> (excluded from plan): {retired_list}
            </div>
            <div class="alert needs-mapping">
                <strong>Needs upstreams.yaml mapping</strong> (could not determine upstream project): {mapping_list}
            </div>
        </div>
        """

    # Build main table rows
    table_rows = []
    for pkg in report.packages:
        type_class = f"type-{pkg.chosen_type.value}"
        status_badge = ""
        row_class = ""
        if pkg.package_status.value == "new":
            status_badge = '<span class="badge badge-new">NEW</span>'
        elif pkg.package_status.value == "defunct":
            status_badge = '<span class="badge badge-defunct">DEFUNCT</span>'
        elif pkg.package_status.value == "retired":
            status_badge = '<span class="badge badge-retired">RETIRED</span>'
            row_class = " row-retired"
            type_class = "type-retired"

        # Watch info columns
        authority = "-"
        watch_status = "-"
        uscan_status = "-"
        upstream_ver = esc(pkg.latest_version) or "-"

        if pkg.upstream_resolution:
            authority = esc(pkg.upstream_resolution.authority.value) if hasattr(pkg.upstream_resolution.authority, 'value') else esc(str(pkg.upstream_resolution.authority))
            if pkg.upstream_resolution.upstream_version:
                upstream_ver = esc(pkg.upstream_resolution.upstream_version)

        if pkg.watch_info:
            watch_status = "✓" if pkg.watch_info.parsed else "✗"
            if pkg.watch_info.uscan_attempted:
                uscan_status = esc(pkg.watch_info.uscan_status) if pkg.watch_info.uscan_status else "-"
                if pkg.watch_info.newer_available:
                    uscan_status += ' <span class="badge badge-new">NEW</span>'

        # Details section with watch info
        details_content = f"<p>{esc(pkg.reason_human)}</p>"
        details_content += f"<p>Version: {esc(pkg.latest_version) or 'N/A'}</p>"

        if pkg.upstream_resolution:
            details_content += f"<p>Authority: {authority}</p>"
            if pkg.upstream_resolution.download_url:
                details_content += f"<p>URL: <a href='{esc(pkg.upstream_resolution.download_url)}'>{esc(pkg.upstream_resolution.download_url[:50])}...</a></p>"

        if pkg.watch_info:
            details_content += f"<p>Watch mode: {esc(pkg.watch_info.mode)}</p>"
            if pkg.watch_info.uscan_error:
                details_content += f"<p>Uscan error: {esc(pkg.watch_info.uscan_error)}</p>"
            if pkg.watch_info.packaged_version:
                details_content += f"<p>Packaged: {esc(pkg.watch_info.packaged_version)}</p>"
            if pkg.watch_info.upstream_version:
                details_content += f"<p>Upstream: {esc(pkg.watch_info.upstream_version)}</p>"

        table_rows.append(f"""
        <tr class="{type_class}{row_class}">
            <td>{esc(pkg.source_package)} {status_badge}</td>
            <td>{esc(pkg.deliverable)}</td>
            <td>{esc(pkg.release_model) or "-"}</td>
            <td>{esc(pkg.deliverable_kind.value)}</td>
            <td>{"✓" if pkg.has_release_for_cycle else "✗"}</td>
            <td>{esc(pkg.cycle_stage.value)}</td>
            <td><strong>{esc(pkg.chosen_type.value)}</strong></td>
            <td>{esc(pkg.reason_code.value)}</td>
            <td>{authority}</td>
            <td>{watch_status}</td>
            <td>{uscan_status}</td>
            <td>{upstream_ver}</td>
            <td>
                <details>
                    <summary>Details</summary>
                    {details_content}
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
                <th>Release Model</th>
                <th>Kind</th>
                <th>Has Release</th>
                <th>Cycle Stage</th>
                <th>Chosen Type</th>
                <th>Reason Code</th>
                <th>Authority</th>
                <th>Watch</th>
                <th>Uscan</th>
                <th>Upstream Ver</th>
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
    <title>Type Selection Report: {esc(report.run_id)}</title>
    <style>
        :root {{
            --color-release: #28a745;
            --color-snapshot: #17a2b8;
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
            max-width: 1400px;
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
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin: 20px 0;
        }}
        .card {{
            background: var(--color-bg);
            padding: 20px;
            border-radius: 8px;
            text-align: center;
        }}
        .card-title {{ font-size: 0.9em; color: #6c757d; margin-bottom: 5px; }}
        .card-value {{ font-size: 2em; font-weight: bold; }}
        .card-release {{ border-left: 4px solid var(--color-release); }}
        .card-release .card-value {{ color: var(--color-release); }}
        .card-snapshot {{ border-left: 4px solid var(--color-snapshot); }}
        .card-snapshot .card-value {{ color: var(--color-snapshot); }}
        .card-retired {{ border-left: 4px solid #6c757d; }}
        .card-retired .card-value {{ color: #6c757d; }}
        .breakdown-section {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
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
        .alert-section {{ margin: 20px 0; }}
        .alert {{
            padding: 12px 16px;
            border-radius: 6px;
            margin: 10px 0;
        }}
        .new-packages {{ background: #d4edda; border: 1px solid #c3e6cb; }}
        .defunct-packages {{ background: #f8d7da; border: 1px solid #f5c6cb; }}
        .badge {{
            display: inline-block;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 0.75em;
            font-weight: bold;
            margin-left: 5px;
        }}
        .badge-new {{ background: #28a745; color: white; }}
        .badge-defunct {{ background: #dc3545; color: white; }}
        .badge-retired {{ background: #6c757d; color: white; }}
        .retired-packages {{ background: #e9ecef; border: 1px solid #ced4da; }}
        .needs-mapping {{ background: #fff3cd; border: 1px solid #ffc107; }}
        .row-retired {{ opacity: 0.6; }}
        .row-retired td {{ color: #6c757d; }}
        .type-retired td:nth-child(7) {{ color: #6c757d; }}
        .main-table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            font-size: 0.9em;
        }}
        .main-table th, .main-table td {{
            padding: 10px 8px;
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
        .type-release td:nth-child(7) {{ color: var(--color-release); }}
        .type-snapshot td:nth-child(7) {{ color: var(--color-snapshot); }}
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
        <h1>Type Selection Report</h1>

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
                <div class="header-label">Type Mode</div>
                <div class="header-value">{esc(report.type_mode)}</div>
            </div>
            <div class="header-item">
                <div class="header-label">Cycle Stage</div>
                <div class="header-value">{esc(report.cycle_stage.value)}</div>
            </div>
            <div class="header-item">
                <div class="header-label">Generated (UTC)</div>
                <div class="header-value">{esc(report.generated_at_utc)}</div>
            </div>
        </div>

        <h2>Summary</h2>
        {summary_html}

        {new_defunct_html}

        {crossref_html}

        {retired_html}

        <h2>Breakdown</h2>
        <div class="breakdown-section">
            <div>
                <h3>By Reason Code</h3>
                {reason_table}
            </div>
            <div>
                <h3>By Cycle Stage</h3>
                {stage_table}
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


def render_console_table(report: TypeSelectionReport, explain: bool = False) -> str:
    """Render type selection report as a fixed-width ASCII table.

    Args:
        report: The type selection report.
        explain: If True, include watch info and reason_human columns.

    Returns:
        String containing the formatted table.
    """
    # Column widths
    cols = [
        ("source_package", 28),
        ("deliverable", 22),
        ("release_model", 20),
        ("kind", 12),
        ("has_rel", 7),
        ("stage", 11),
        ("type", 10),
        ("reason_code", 22),
    ]

    if explain:
        cols.extend([
            ("authority", 10),
            ("watch", 6),
            ("uscan", 10),
            ("up_ver", 12),
            ("reason_human", 35),
        ])

    # Header
    header = " | ".join(name.ljust(width) for name, width in cols)
    separator = "-+-".join("-" * width for _, width in cols)

    lines = [header, separator]

    for pkg in report.packages:
        row_data = [
            pkg.source_package[:28],
            pkg.deliverable[:22],
            (pkg.release_model or "-")[:20],
            pkg.deliverable_kind.value[:12],
            "yes" if pkg.has_release_for_cycle else "no",
            pkg.cycle_stage.value[:11],
            pkg.chosen_type.value[:10],
            pkg.reason_code.value[:22],
        ]
        if explain:
            authority = "-"
            watch = "-"
            uscan = "-"
            up_ver = pkg.latest_version[:12] if pkg.latest_version else "-"

            if pkg.upstream_resolution:
                authority = pkg.upstream_resolution.authority.value[:10] if hasattr(pkg.upstream_resolution.authority, 'value') else str(pkg.upstream_resolution.authority)[:10]
                if pkg.upstream_resolution.upstream_version:
                    up_ver = pkg.upstream_resolution.upstream_version[:12]

            if pkg.watch_info:
                watch = "yes" if pkg.watch_info.parsed else "no"
                if pkg.watch_info.uscan_attempted:
                    uscan = pkg.watch_info.uscan_status[:10] if pkg.watch_info.uscan_status else "-"

            row_data.extend([
                authority,
                watch,
                uscan,
                up_ver,
                (pkg.reason_human or "-")[:35],
            ])

        row = " | ".join(
            data.ljust(width) for data, (_, width) in zip(row_data, cols, strict=False)
        )
        lines.append(row)

    return "\n".join(lines)


def render_compact_summary(report: TypeSelectionReport) -> str:
    """Render a compact summary for console output.

    Args:
        report: The type selection report.

    Returns:
        String for console output.
    """
    lines = [
        f"[plan] Type selection ({report.type_mode}):",
        f"[plan]   Cycle stage: {report.cycle_stage.value}",
        f"[plan]   Total: {len(report.packages)} packages",
        f"[plan]   Release: {report.count_release}, Snapshot: {report.count_snapshot}",
    ]

    # Show a few examples
    examples = report.packages[:5]
    for pkg in examples:
        lines.append(f"[plan]   {pkg.source_package}: {pkg.chosen_type.value} ({pkg.reason_code.value})")

    if len(report.packages) > 5:
        lines.append(f"[plan]   ... ({len(report.packages) - 5} more packages)")

    # Warn about new/defunct
    if report.new_packages:
        lines.append(f"[plan] ⚠️  New packages (not in releases): {len(report.new_packages)}")
        for pkg in report.new_packages[:3]:
            lines.append(f"[plan]     - {pkg}")
        if len(report.new_packages) > 3:
            lines.append(f"[plan]     ... and {len(report.new_packages) - 3} more")

    if report.defunct_packages:
        lines.append(f"[plan] ⚠️  Defunct packages (in releases but not local): {len(report.defunct_packages)}")
        for pkg in report.defunct_packages[:3]:
            lines.append(f"[plan]     - {pkg}")
        if len(report.defunct_packages) > 3:
            lines.append(f"[plan]     ... and {len(report.defunct_packages) - 3} more")

    # Warn about cross-reference issues
    if report.missing_upstream:
        lines.append(f"[plan] ⚠️  No upstream definition: {len(report.missing_upstream)}")
        for pkg in report.missing_upstream[:3]:
            lines.append(f"[plan]     - {pkg}")
        if len(report.missing_upstream) > 3:
            lines.append(f"[plan]     ... and {len(report.missing_upstream) - 3} more")

    if report.missing_packaging:
        lines.append(f"[plan] ⚠️  Missing packaging repo: {len(report.missing_packaging)}")
        for pkg in report.missing_packaging[:3]:
            lines.append(f"[plan]     - {pkg}")
        if len(report.missing_packaging) > 3:
            lines.append(f"[plan]     ... and {len(report.missing_packaging) - 3} more")

    lines.append("[plan] Use --table for full details")

    return "\n".join(lines)


def write_type_selection_reports(
    report: TypeSelectionReport,
    reports_dir: Path,
) -> dict[str, Path]:
    """Write both JSON and HTML type selection reports.

    Args:
        report: The type selection report.
        reports_dir: Directory to write reports.

    Returns:
        Dict with 'json' and 'html' keys mapping to the written paths.
    """
    json_path = render_json(report, reports_dir / "type-selection.json")
    html_path = render_html(report, reports_dir / "type-selection.html")
    return {"json": json_path, "html": html_path}
