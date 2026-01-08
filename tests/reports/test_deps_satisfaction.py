from pathlib import Path

from packastack.reports.deps_satisfaction import (
    render_dependency_satisfaction_html,
    write_dependency_satisfaction_reports,
)


def sample_report():
    return {
        "run_id": "123",
        "target": {"source_package": "python-foo"},
        "openstack_target": "caracal",
        "ubuntu_series": "resolute",
        "current_lts": "flamingo",
        "dependencies": {
            "build": [
                {
                    "name": "python3-foo",
                    "relation": ">=",
                    "version": "1.0",
                    "dev": {"found": True, "version": "1.0", "component": "main", "satisfied": True, "reason": "ok"},
                    "prev_lts": {"found": True, "version": "1.0", "component": "main", "satisfied": True, "reason": "ok"},
                    "cloud_archive_required": False,
                    "mir_warning": False,
                }
            ],
            "runtime": [],
        },
        "summary": {
            "build_deps_total": 1,
            "build_deps_dev_satisfied": 1,
            "build_deps_current_lts_satisfied": 1,
            "runtime_deps_total": 0,
            "runtime_deps_dev_satisfied": 0,
            "runtime_deps_current_lts_satisfied": 0,
            "cloud_archive_required_count": 0,
            "mir_warning_count": 0,
        },
    }


def test_render_dependency_satisfaction_html_includes_counts():
    html = render_dependency_satisfaction_html(sample_report())
    assert "Dependency Satisfaction" in html
    assert "Cloud-archive" in html


def test_write_dependency_satisfaction_reports(tmp_path: Path):
    saved = write_dependency_satisfaction_reports(sample_report(), tmp_path)
    assert saved["json"].exists()
    assert saved["html"].exists()
    assert "python3-foo" in saved["html"].read_text()
