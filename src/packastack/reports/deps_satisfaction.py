# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only
"""Dependency satisfaction report rendering helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _badge(text: str, color: str) -> str:
    return f"<span class='badge' style='background:{color}'>{text}</span>"


def _status_cell(status: dict[str, Any]) -> str:
    found = status.get("found")
    comp = status.get("component", "?")
    version = status.get("version") or "—"
    reason = status.get("reason", "")
    satisfied = status.get("satisfied")

    badge = _badge("ok" if satisfied else reason or "missing", "#1f7a8c" if satisfied else "#c44536")
    comp_badge = _badge(comp or "unknown", "#6c757d")
    return f"{version}<br>{comp_badge}<br>{badge}"


def render_dependency_satisfaction_html(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    deps = report.get("dependencies", {})

    def card(label: str, value: Any) -> str:
        return (
            "<div class='card'>"
            f"<div class='card-label'>{label}</div>"
            f"<div class='card-value'>{value}</div>"
            "</div>"
        )

    def table_rows(kind: str) -> str:
        rows = []
        for dep in deps.get(kind, []):
            cloud = "Yes" if dep.get("cloud_archive_required") else "No"
            mir = "Yes" if dep.get("mir_warning") else "No"
            rows.append(
                "<tr>"
                f"<td>{dep.get('name')}</td>"
                f"<td>{(dep.get('relation') or '') + ' ' + (dep.get('version') or '')}</td>"
                f"<td>{_status_cell(dep.get('dev', {}))}</td>"
                f"<td>{_status_cell(dep.get('prev_lts', {}))}</td>"
                f"<td>{cloud}</td>"
                f"<td>{mir}</td>"
                "</tr>"
            )
        return "\n".join(rows) or "<tr><td colspan='6'>No dependencies</td></tr>"

    html = f"""
<!doctype html>
<html>
<head>
<meta charset='utf-8'>
<title>Packastack Dependency Satisfaction</title>
<style>
body {{ font-family: 'Segoe UI', sans-serif; margin: 16px; color: #111; }}
header {{ margin-bottom: 16px; }}
.cards {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 12px 0; }}
.card {{ background: #f7f9fb; border: 1px solid #dfe3e8; border-radius: 8px; padding: 12px 14px; min-width: 140px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }}
.card-label {{ font-size: 12px; color: #555; text-transform: uppercase; letter-spacing: 0.5px; }}
.card-value {{ font-size: 20px; font-weight: 600; color: #0f172a; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; color: #fff; }}
section {{ margin-top: 18px; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
th, td {{ border: 1px solid #e3e6ea; padding: 8px; text-align: left; font-size: 14px; }}
th {{ background: #eef2f7; }}
.small {{ color: #444; font-size: 13px; }}
</style>
</head>
<body>
<header>
  <h2>Dependency Satisfaction</h2>
  <div class='small'>Target source: {report.get('target', {}).get('source_package', '')}</div>
  <div class='small'>Ubuntu series: {report.get('ubuntu_series')} &nbsp;|&nbsp; Previous LTS: {report.get('previous_lts')}</div>
</header>
<div class='cards'>
  {card('Build deps (dev)', f"{summary.get('build_deps_dev_satisfied', 0)}/{summary.get('build_deps_total', 0)}")}
  {card('Build deps (prev LTS)', f"{summary.get('build_deps_prev_lts_satisfied', 0)}/{summary.get('build_deps_total', 0)}")}
  {card('Cloud-archive required', summary.get('cloud_archive_required_count', 0))}
  {card('MIR warnings', summary.get('mir_warning_count', 0))}
</div>
<section>
  <h3>Build Dependencies</h3>
  <table>
    <thead>
      <tr>
        <th>Package</th>
        <th>Constraint</th>
        <th>Ubuntu (dev)</th>
        <th>Previous LTS</th>
        <th>Cloud-archive?</th>
        <th>MIR?</th>
      </tr>
    </thead>
    <tbody>
      {table_rows('build')}
    </tbody>
  </table>
</section>
<section>
  <h3>Runtime Dependencies</h3>
  <table>
    <thead>
      <tr>
        <th>Package</th>
        <th>Constraint</th>
        <th>Ubuntu (dev)</th>
        <th>Previous LTS</th>
        <th>Cloud-archive?</th>
        <th>MIR?</th>
      </tr>
    </thead>
    <tbody>
      {table_rows('runtime')}
    </tbody>
  </table>
</section>
</body>
</html>
"""
    return html


def write_dependency_satisfaction_reports(report: dict[str, Any], reports_dir: Path) -> dict[str, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(report, indent=2)
    html_text = render_dependency_satisfaction_html(report)

    json_path = reports_dir / "deps-satisfaction.json"
    html_path = reports_dir / "deps-satisfaction.html"
    json_path.write_text(json_text)
    html_path.write_text(html_text)

    return {"json": json_path, "html": html_path}
