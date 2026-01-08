from packastack.commands.explain import _render_text


def test_render_text_basic():
    report = {
        "target": {"source_package": "python-foo", "upstream_project": "foo", "resolution_source": "registry"},
        "type_selection": {"selected": "snapshot", "mode": "auto", "reason": "none"},
        "ubuntu_series": "resolute",
        "current_lts": "focal",
        "summary": {
            "build_deps_total": 2,
            "build_deps_dev_satisfied": 1,
            "build_deps_current_lts_satisfied": 0,
        },
        "cloud_archive_deps": [
            {"name": "python-bar", "relation": ">=", "version": "1.0"}
        ],
        "mir_warning_deps": [],
    }

    txt = _render_text(report)
    assert "Target: python-foo" in txt
    assert "Build type: snapshot" in txt
    assert "cloud-archive" in txt or "Cloud-archive" in txt


def test_format_dependency():
    from packastack.commands.explain import _format_dependency

    assert _format_dependency("pkg", "", "") == "pkg"
    assert _format_dependency("pkg", ">=", "1.2") == "pkg (>= 1.2)"
