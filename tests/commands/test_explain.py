from types import SimpleNamespace

from typer.testing import CliRunner

import packastack.commands.explain as explain_module
from packastack.cli import app


runner = CliRunner()


class FakeRun:
    def __init__(self, path):
        self.run_path = path
        self.run_id = "run-1"
        self.events = []
        self.summary = None

    def __enter__(self):
        self.run_path.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def log_event(self, event):
        self.events.append(event)

    def write_summary(self, **kwargs):
        self.summary = kwargs


def test_explain_command_renders_text(monkeypatch, tmp_path):
    # Stub lightweight dependencies so the command can run without network or apt data.
    monkeypatch.setattr(explain_module, "load_config", lambda: {})
    monkeypatch.setattr(explain_module, "resolve_paths", lambda cfg: {
        "openstack_releases_repo": tmp_path / "releases",
        "local_apt_repo": tmp_path / "local",
        "ubuntu_archive_cache": tmp_path / "cache",
        "build_root": tmp_path,
        "cache_root": tmp_path,
    })
    monkeypatch.setattr(explain_module, "resolve_series", lambda series: series)

    class FakeLts:
        def __init__(self, series: str, codename: str | None = None):
            # Some callers historically used codename; set both for compatibility
            self.series = series
            self.codename = codename or series

    monkeypatch.setattr(explain_module, "get_current_lts", lambda: FakeLts("noble"))
    monkeypatch.setattr(explain_module, "get_current_development_series", lambda repo: "caracal")
    monkeypatch.setattr(explain_module, "RunContext", lambda name: FakeRun(tmp_path / "run"))
    monkeypatch.setattr(explain_module, "activity", lambda *args, **kwargs: None)

    class FakePkg:
        def __init__(self, version: str, component: str):
            self.version = version
            self.component = component

    class FakeIndex:
        def __init__(self, mapping):
            self.mapping = mapping

        def find_package(self, name):
            info = self.mapping.get(name)
            if not info:
                return None
            version, comp = info
            return FakePkg(version, comp)

    dev_index = FakeIndex({"foo": ("1.0", "main"), "bar": ("2.0", "universe")})
    prev_index = FakeIndex({"bar": ("2.0", "universe")})

    def fake_load_index(cache, series, pockets, components):
        return dev_index if series == "devel" else prev_index

    monkeypatch.setattr(explain_module, "load_package_index", fake_load_index)

    # Patch TargetResolver.resolve to return a ResolutionResult-like object
    monkeypatch.setattr(
        explain_module.TargetResolver,
        "resolve",
        lambda self, expr, all_matches=True: SimpleNamespace(
            candidates=[SimpleNamespace(source_package="foo", canonical_upstream="foo", origin=SimpleNamespace(value="local"))],
            identity=None,
        ),
    )

    pkg_path = tmp_path / "pkg"
    (pkg_path / "debian").mkdir(parents=True)
    (pkg_path / "debian" / "control").write_text("Source: foo")

    monkeypatch.setattr(
        explain_module,
        "_fetch_packaging_repos",
        lambda packages, dest_dir, ubuntu_series, openstack_series, offline, workers: {"foo": pkg_path},
    )

    class FakeDep:
        def __init__(self, name, relation="", version="", alternatives=None):
            self.name = name
            self.relation = relation
            self.version = version
            self.alternatives = alternatives or []

    class FakeSource:
        def __init__(self):
            self.build_depends = [FakeDep("foo", ">=", "1.0")]
            self.build_depends_indep = [FakeDep("bar")]

        def get_runtime_depends(self):
            return []

    monkeypatch.setattr(explain_module, "parse_control", lambda path: FakeSource())
    monkeypatch.setattr(explain_module, "is_snapshot_eligible", lambda repo, target, pkg: (True, "eligible", "1.2.3"))

    result = runner.invoke(app, ["explain", "foo"])

    assert result.exit_code == 0
    reports_dir = tmp_path / "run" / "reports"
    assert (reports_dir / "explain.json").exists()
    assert (reports_dir / "explain.html").exists()
    # Verify the saved report uses the LTS series (not codename) for previous_lts
    import json

    data = json.loads((reports_dir / "explain.json").read_text())
    assert data.get("previous_lts") == "noble"
    # Cloud archive should be constructed from the LTS series and OpenStack target
    assert data.get("cloud_archive") == "noble-caracal"


def test_explain_emits_resolve_lines(monkeypatch, tmp_path):
    # Verify the CLI emits the resolve lines to stdout
    monkeypatch.setattr(explain_module, "load_config", lambda: {})
    monkeypatch.setattr(explain_module, "resolve_paths", lambda cfg: {
        "openstack_releases_repo": tmp_path / "releases",
        "local_apt_repo": tmp_path / "local",
        "ubuntu_archive_cache": tmp_path / "cache",
        "build_root": tmp_path,
        "cache_root": tmp_path,
    })
    monkeypatch.setattr(explain_module, "resolve_series", lambda series: "resolute")

    class FakeLts:
        def __init__(self, series: str, codename: str | None = None):
            self.series = series
            self.codename = codename or series

    monkeypatch.setattr(explain_module, "get_current_lts", lambda: FakeLts("noble"))
    monkeypatch.setattr(explain_module, "get_current_development_series", lambda repo: "gazpacho")
    monkeypatch.setattr(explain_module, "RunContext", lambda name: FakeRun(tmp_path / "run_lines"))

    # Make activity print to sys.stdout so CliRunner captures it
    import sys

    def capture_activity(phase, description):
        print(f"[{phase}] {description}", file=sys.stdout)

    monkeypatch.setattr(explain_module, "activity", capture_activity)

    monkeypatch.setattr(
        explain_module.TargetResolver,
        "resolve",
        lambda self, expr, all_matches=True: SimpleNamespace(
            candidates=[SimpleNamespace(source_package="foo", canonical_upstream="foo", origin=SimpleNamespace(value="local"))],
            identity=None,
        ),
    )

    pkg_path = tmp_path / "pkg"
    (pkg_path / "debian").mkdir(parents=True)
    (pkg_path / "debian" / "control").write_text("Source: foo")

    monkeypatch.setattr(
        explain_module,
        "_fetch_packaging_repos",
        lambda packages, dest_dir, ubuntu_series, openstack_series, offline, workers: {"foo": pkg_path},
    )

    class FakeSource:
        def __init__(self):
            self.build_depends = []
            self.build_depends_indep = []

        def get_runtime_depends(self):
            return []

    monkeypatch.setattr(explain_module, "parse_control", lambda path: FakeSource())
    monkeypatch.setattr(explain_module, "is_snapshot_eligible", lambda repo, target, pkg: (False, "release", None))

    result = runner.invoke(app, ["explain", "foo"])

    assert result.exit_code == 0
    out = result.output
    assert "[resolve] Ubuntu series: resolute" in out
    assert "[resolve] Previous LTS series: noble" in out
    assert "[resolve] Cloud Archive: noble-gazpacho" in out


def test_explain_rejects_invalid_format(monkeypatch, tmp_path):
    monkeypatch.setattr(explain_module, "load_config", lambda: {})
    monkeypatch.setattr(explain_module, "resolve_paths", lambda cfg: {})
    monkeypatch.setattr(explain_module, "RunContext", lambda name: FakeRun(tmp_path / "run"))
    monkeypatch.setattr(explain_module, "activity", lambda *args, **kwargs: None)

    result = runner.invoke(app, ["explain", "foo", "--format", "yaml"])

    assert result.exit_code == explain_module.EXIT_CONFIG_ERROR


def test_explain_rejects_invalid_show_deps(monkeypatch, tmp_path):
    monkeypatch.setattr(explain_module, "load_config", lambda: {})
    monkeypatch.setattr(explain_module, "resolve_paths", lambda cfg: {})
    monkeypatch.setattr(explain_module, "RunContext", lambda name: FakeRun(tmp_path / "run"))
    monkeypatch.setattr(explain_module, "activity", lambda *args, **kwargs: None)

    result = runner.invoke(app, ["explain", "foo", "--show-deps", "invalid"])

    assert result.exit_code == explain_module.EXIT_CONFIG_ERROR


def test_explain_html_filters_universe(monkeypatch, tmp_path):
    monkeypatch.setattr(explain_module, "load_config", lambda: {})
    monkeypatch.setattr(explain_module, "resolve_paths", lambda cfg: {
        "openstack_releases_repo": tmp_path / "releases",
        "local_apt_repo": tmp_path / "local",
        "ubuntu_archive_cache": tmp_path / "cache",
        "build_root": tmp_path,
        "cache_root": tmp_path,
    })
    monkeypatch.setattr(explain_module, "resolve_series", lambda series: series)
    monkeypatch.setattr(explain_module, "get_current_lts", lambda: None)
    monkeypatch.setattr(explain_module, "get_current_development_series", lambda repo: "caracal")
    monkeypatch.setattr(explain_module, "RunContext", lambda name: FakeRun(tmp_path / "run_html"))
    monkeypatch.setattr(explain_module, "activity", lambda *args, **kwargs: None)

    class FakePkg:
        def __init__(self, version: str, component: str):
            self.version = version
            self.component = component

    class FakeIndex:
        def __init__(self, mapping):
            self.mapping = mapping

        def find_package(self, name):
            info = self.mapping.get(name)
            if not info:
                return None
            version, comp = info
            return FakePkg(version, comp)

    dev_index = FakeIndex({"foo": ("1.0", "main"), "bar": ("2.0", "universe")})

    def fake_load_index(cache, series, pockets, components):
        return dev_index

    monkeypatch.setattr(explain_module, "load_package_index", fake_load_index)

    monkeypatch.setattr(
        explain_module.TargetResolver,
        "resolve",
        lambda self, expr, all_matches=True: SimpleNamespace(
            candidates=[SimpleNamespace(source_package="foo", canonical_upstream="foo", origin=SimpleNamespace(value="local"))],
            identity=None,
        ),
    )

    pkg_path = tmp_path / "pkg"
    (pkg_path / "debian").mkdir(parents=True)
    (pkg_path / "debian" / "control").write_text("Source: foo")

    monkeypatch.setattr(
        explain_module,
        "_fetch_packaging_repos",
        lambda packages, dest_dir, ubuntu_series, openstack_series, offline, workers: {"foo": pkg_path},
    )

    class FakeDep:
        def __init__(self, name, relation="", version="", alternatives=None):
            self.name = name
            self.relation = relation
            self.version = version
            self.alternatives = alternatives or []

    class FakeSource:
        def __init__(self):
            self.build_depends = [FakeDep("foo"), FakeDep("bar")]
            self.build_depends_indep = []

        def get_runtime_depends(self):
            return [FakeDep("bar")]

    monkeypatch.setattr(explain_module, "parse_control", lambda path: FakeSource())
    monkeypatch.setattr(explain_module, "is_snapshot_eligible", lambda repo, target, pkg: (False, "release", None))

    result = runner.invoke(app, [
        "explain",
        "foo",
        "--format",
        "html",
        "--no-include-universe",
        "--show-deps",
        "runtime",
    ])

    assert result.exit_code == 0
    reports_dir = tmp_path / "run_html" / "reports"
    html_path = reports_dir / "explain.html"
    assert html_path.exists()
    html_content = html_path.read_text()
    assert "Build Dependencies" in html_content
    assert "Runtime Dependencies" in html_content


def test_explain_requires_force_for_multiple_matches(monkeypatch, tmp_path):
    monkeypatch.setattr(explain_module, "load_config", lambda: {})
    monkeypatch.setattr(explain_module, "resolve_paths", lambda cfg: {
        "openstack_releases_repo": tmp_path / "releases",
        "local_apt_repo": tmp_path / "local",
        "ubuntu_archive_cache": tmp_path / "cache",
        "build_root": tmp_path,
        "cache_root": tmp_path,
    })
    monkeypatch.setattr(explain_module, "resolve_series", lambda series: series)
    monkeypatch.setattr(explain_module, "get_current_lts", lambda: None)
    monkeypatch.setattr(explain_module, "get_current_development_series", lambda repo: "caracal")
    monkeypatch.setattr(explain_module, "RunContext", lambda name: FakeRun(tmp_path / "run_multi"))
    monkeypatch.setattr(explain_module, "activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(explain_module, "load_package_index", lambda *args, **kwargs: None)

    monkeypatch.setattr(
        explain_module.TargetResolver,
        "resolve",
        lambda self, expr, all_matches=True: SimpleNamespace(
            candidates=[
                SimpleNamespace(source_package="foo", canonical_upstream="foo", origin=SimpleNamespace(value="local")),
                SimpleNamespace(source_package="bar", canonical_upstream="bar", origin=SimpleNamespace(value="local")),
            ],
            identity=None,
        ),
    )

    result = runner.invoke(app, ["explain", "foo"])

    assert result.exit_code == explain_module.EXIT_CONFIG_ERROR


def test_render_text_includes_cloud_and_mir_lists():
    report = {
        "target": {"source_package": "foo", "upstream_project": "foo", "resolution_source": "local"},
        "type_selection": {"selected": "snapshot", "mode": "auto", "reason": "eligible"},
        "summary": {
            "build_deps_dev_satisfied": 1,
            "build_deps_total": 2,
            "build_deps_prev_lts_satisfied": 0,
            "cloud_archive_required_count": 1,
            "mir_warning_count": 1,
        },
        "ubuntu_series": "devel",
        "previous_lts": "jammy",
        "cloud_archive_deps": [
            {"name": "foo", "relation": ">=", "version": "1.0"},
        ],
        "mir_warning_deps": [
            {"name": "bar", "relation": "=", "version": "2.0"},
        ],
    }

    text = explain_module._render_text(report)

    assert "Cloud-archive required deps" in text
    assert "foo (>= 1.0)" in text
    assert "MIR warnings" in text
    assert "bar (= 2.0)" in text
