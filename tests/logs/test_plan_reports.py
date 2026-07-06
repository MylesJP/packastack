from pathlib import Path

from packastack.logs.plan_reports import (
    render_plan_dependency_html,
    write_plan_dependency_summary,
)


def test_write_plan_dependency_summary(tmp_path: Path) -> None:
    payload = {
        "current_lts": "jammy",
        "totals": {"total": 3, "cloud_archive_required": 1, "mir_warnings": 1},
        "packages": [
            {
                "package": "foo",
                "dependencies": 2,
                "dev_satisfied": 2,
                "current_lts_satisfied": 1,
                "cloud_archive_required": 1,
                "mir_warnings": 0,
            }
        ],
    }

    paths = write_plan_dependency_summary(payload, tmp_path)
    assert paths["json"].exists()
    assert paths["html"].exists()
    html = paths["html"].read_text()
    assert "Plan Dependency Summary" in html
    assert "cloud-archive" in html.lower()


def test_render_plan_dependency_html_no_packages() -> None:
    html = render_plan_dependency_html({"packages": [], "totals": {}})
    assert "No packages" in html
    assert "unknown" in html
