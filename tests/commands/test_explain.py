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
        def __init__(self, codename: str):
            self.codename = codename

    monkeypatch.setattr(explain_module, "get_previous_lts", lambda: FakeLts("jammy"))
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

    monkeypatch.setattr(
        explain_module,
        "_resolve_package_targets",
        lambda package, local_repo, releases_repo, registry, openstack_target, use_local, run, allow_prefix: [
            SimpleNamespace(source_package="foo", upstream_project="foo", resolution_source="local")
        ],
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


def test_explain_rejects_invalid_format(monkeypatch, tmp_path):
    monkeypatch.setattr(explain_module, "load_config", lambda: {})
    monkeypatch.setattr(explain_module, "resolve_paths", lambda cfg: {})
    monkeypatch.setattr(explain_module, "RunContext", lambda name: FakeRun(tmp_path / "run"))
    monkeypatch.setattr(explain_module, "activity", lambda *args, **kwargs: None)

    result = runner.invoke(app, ["explain", "foo", "--format", "yaml"])

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
    monkeypatch.setattr(explain_module, "get_previous_lts", lambda: None)
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
        explain_module,
        "_resolve_package_targets",
        lambda package, local_repo, releases_repo, registry, openstack_target, use_local, run, allow_prefix: [
            SimpleNamespace(source_package="foo", upstream_project="foo", resolution_source="local")
        ],
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
