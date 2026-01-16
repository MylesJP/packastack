# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only
"""Explain command report renderers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def render_explain_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2)


def _badge(text: str, color: str) -> str:
    return f"<span class='badge' style='background:{color}'>{text}</span>"


def _status_cell(status: dict[str, Any]) -> str:
    status.get("found")
    comp = status.get("component", "?")
    version = status.get("version") or "—"
    reason = status.get("reason", "")
    satisfied = status.get("satisfied")

    badge = _badge("ok" if satisfied else reason or "missing", "#1f7a8c" if satisfied else "#c44536")
    comp_badge = _badge(comp or "unknown", "#6c757d")
    return f"{version}<br>{comp_badge}<br>{badge}"


def render_explain_html(report: dict[str, Any]) -> str:
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
<title>Packastack Explain Report</title>
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
  <h2>packastack explain</h2>
  <div class='small'>Target: {report.get('target', {})}</div>
  <div class='small'>Ubuntu series: {report.get('ubuntu_series')} &nbsp;|&nbsp; Current LTS: {report.get('current_lts')}</div>
</header>
<div class='cards'>
  {card('Build deps (dev)', f"{summary.get('build_deps_dev_satisfied', 0)}/{summary.get('build_deps_total', 0)}")}
  {card('Build deps (current LTS)', f"{summary.get('build_deps_current_lts_satisfied', 0)}/{summary.get('build_deps_total', 0)}")}
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
        <th>Current LTS</th>
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
        <th>Current LTS</th>
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


def write_explain_reports(report: dict[str, Any], reports_dir: Path) -> dict[str, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_text = render_explain_json(report)
    html_text = render_explain_html(report)

    json_path = reports_dir / "explain.json"
    html_path = reports_dir / "explain.html"
    json_path.write_text(json_text)
    html_path.write_text(html_text)

    return {"json": json_path, "html": html_path}


def render_plan_dependency_html(summary: dict[str, Any]) -> str:
    packages = summary.get("packages", [])
    totals = summary.get("totals", {})
    current_lts = summary.get("current_lts", "")

    rows = []
    for pkg in packages:
        rows.append(
            "<tr>"
            f"<td>{pkg.get('package')}</td>"
            f"<td>{pkg.get('dependencies')}</td>"
            f"<td>{pkg.get('dev_satisfied')}</td>"
            f"<td>{pkg.get('current_lts_satisfied')}</td>"
            f"<td>{pkg.get('cloud_archive_required')}</td>"
            f"<td>{pkg.get('mir_warnings')}</td>"
            "</tr>"
        )

    if not rows:
        rows.append("<tr><td colspan='6'>No packages</td></tr>")

    return f"""
<!doctype html>
<html><head><meta charset='utf-8'>
<title>Plan Dependency Summary</title>
<style>
body {{ font-family: 'Segoe UI', sans-serif; margin: 16px; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
th, td {{ border: 1px solid #e3e6ea; padding: 8px; text-align: left; }}
th {{ background: #eef2f7; }}
.cards {{ display:flex; gap:12px; flex-wrap: wrap; margin: 10px 0; }}
.card {{ padding: 10px 12px; background: #f7f9fb; border: 1px solid #dfe3e8; border-radius: 8px; }}
.card .label {{ font-size: 12px; color: #666; text-transform: uppercase; }}
.card .value {{ font-size: 18px; font-weight: 600; }}
</style></head>
<body>
<h2>Plan Dependency Summary</h2>
<div>Current LTS: {current_lts or 'unknown'}</div>
<div class='cards'>
  <div class='card'><div class='label'>Dependencies</div><div class='value'>{totals.get('total',0)}</div></div>
  <div class='card'><div class='label'>Cloud-archive required</div><div class='value'>{totals.get('cloud_archive_required',0)}</div></div>
  <div class='card'><div class='label'>MIR warnings</div><div class='value'>{totals.get('mir_warnings',0)}</div></div>
</div>
<table>
  <thead>
    <tr><th>Package</th><th>Deps</th><th>Dev satisfied</th><th>Current LTS satisfied</th><th>Cloud-archive</th><th>MIR</th></tr>
  </thead>
  <tbody>
    {''.join(rows)}
  </tbody>
</table>
</body></html>
"""


def write_plan_dependency_summary(summary: dict[str, Any], reports_dir: Path) -> dict[str, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / "plan-dependencies.json"
    html_path = reports_dir / "plan-dependencies.html"
    json_path.write_text(json.dumps(summary, indent=2))
    html_path.write_text(render_plan_dependency_html(summary))
    return {"json": json_path, "html": html_path}
