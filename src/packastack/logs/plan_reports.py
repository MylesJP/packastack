# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only
"""Plan dependency report renderers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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
