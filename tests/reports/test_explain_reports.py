from pathlib import Path

from packastack.reports.explain import write_explain_reports, write_plan_dependency_summary


def test_write_explain_reports(tmp_path: Path) -> None:
    report = {
        "run_id": "test",
        "target": {"source_package": "foo", "upstream_project": "foo", "resolution_source": "local"},
        "openstack_target": "devel",
        "ubuntu_series": "noble",
        "current_lts": "jammy",
        "type_selection": {"mode": "auto", "selected": "snapshot", "reason": "ok"},
        "dependencies": {"build": [], "runtime": []},
        "summary": {
            "build_deps_total": 0,
            "build_deps_dev_satisfied": 0,
            "build_deps_current_lts_satisfied": 0,
            "cloud_archive_required_count": 0,
            "mir_warning_count": 0,
        },
    }

    paths = write_explain_reports(report, tmp_path)
    assert paths["json"].exists()
    assert paths["html"].exists()
    assert "foo" in paths["json"].read_text()


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
