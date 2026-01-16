
from packastack.target.completion import (
    get_completions,
    load_completion_index,
    save_completion_index,
)


def make_sample_index():
    return {
        "generated_at_utc": "2025-01-01T00:00:00Z",
        "source_packages": ["python-foo", "bar"],
        "canonical_ids": ["openstack/foo", "openstack/bar"],
        "deliverables": ["foo", "bar"],
        "aliases": ["pyfoo"],
        "scopes": ["source:", "canonical:", "repo:", "upstream:", "deliverable:"],
    }


def test_get_completions_unscoped():
    idx = make_sample_index()
    # No colon -> suggest scopes and package names
    out = get_completions("py", index=idx)
    assert "source:" in out or "pyfoo" in out
    # Source package completion
    out2 = get_completions("bar", index=idx)
    assert "bar" in out2


def test_get_completions_scoped():
    idx = make_sample_index()
    # scoped source
    out = get_completions("source:py", index=idx)
    assert "source:python-foo" in out
    # scoped canonical
    out2 = get_completions("canonical:openstack/f", index=idx)
    assert any(s.startswith("canonical:") for s in out2)
    # deliverable scoped
    out3 = get_completions("deliverable:fo", index=idx)
    assert "deliverable:foo" in out3


def test_save_and_load_completion_index(tmp_path):
    idx = make_sample_index()
    p = tmp_path / "index.json"
    # save
    save_completion_index(idx, path=p)
    assert p.exists()
    loaded = load_completion_index(path=p)
    assert loaded is not None
    assert loaded.get("source_packages") == idx["source_packages"]

    # invalid json returns None
    bad = p
    bad.write_text("not-json")
    assert load_completion_index(path=bad) is None


def test_get_completion_cache_path_and_missing_index(monkeypatch, tmp_path):
    # Ensure get_completion_cache_path creates directories under home
    from packastack.target.completion import (
        get_completion_cache_path,
        get_completions,
    )

    monkeypatch.setattr("packastack.target.completion.Path.home", lambda: tmp_path)
    p = get_completion_cache_path()
    assert p.parent.exists()

    # If no index is available, get_completions should return empty list
    monkeypatch.setattr("packastack.target.completion.load_completion_index", lambda: None)
    assert get_completions("anything", index=None) == []


def test_scoped_repo_and_upstream():
    idx = make_sample_index()
    out = get_completions("repo:openstack/f", index=idx)
    assert any(s.startswith("repo:") for s in out)
    out2 = get_completions("upstream:openstack/b", index=idx)
    assert any(s.startswith("upstream:") for s in out2)


def test_generate_completion_index_from_registry_and_releases(monkeypatch, tmp_path):
    # Fake registry with minimal config
    class FakeReleaseSource:
        def __init__(self, tval, deliverable=None):
            self.type = type("T", (), {"value": tval})
            self.deliverable = deliverable

    class FakeProvenance:
        def __init__(self, canonical=None):
            self.canonical = canonical

    class FakeUbuntuCfg:
        def __init__(self, source_hint=None):
            self.source_hint = source_hint

    class FakeConfig:
        def __init__(self):
            self.ubuntu = FakeUbuntuCfg(source_hint="python-fake")
            self.provenance = FakeProvenance(canonical="openstack/fake")
            self.release_source = FakeReleaseSource("openstack_releases", deliverable="fake")
            self.common_names = ["pfake"]

    class FakeResolved:
        def __init__(self, cfg):
            self.config = cfg

    class FakeRegistry:
        def list_projects(self):
            return ["fakeproj"]

        def resolve(self, key, openstack_governed=False):
            return FakeResolved(FakeConfig())

    # Monkeypatch the releases loader to supply additional packages
    monkeypatch.setattr("packastack.upstream.releases.load_openstack_packages", lambda repo, tgt: {"python-other": "other"})

    from packastack.target.completion import generate_completion_index

    idx = generate_completion_index(registry=FakeRegistry(), releases_repo=tmp_path, openstack_target="train")
    # Should include entries from the fake registry and releases
    assert "python-fake" in idx["source_packages"]
    assert "openstack/fake" in idx["canonical_ids"]
    assert "fake" in idx["deliverables"]


def test_get_completions_empty_prefix_all_matches():
    idx = make_sample_index()
    # empty incomplete should match everything
    out = get_completions("", index=idx)
    # scopes and at least one package should be present
    assert "source:" in out
    assert any(p in out for p in idx["source_packages"]) or any(c in out for c in idx["canonical_ids"]) or any(d in out for d in idx["deliverables"]) or any(a in out for a in idx["aliases"])

    # scoped empty-ident should return scoped entries
    out2 = get_completions("source:", index=idx)
    assert any(s.startswith("source:") for s in out2)


def test_generate_completion_index_registry_and_releases_errors(monkeypatch, tmp_path):
    class BadRegistry:
        def list_projects(self):
            return ["bad"]

        def resolve(self, key, openstack_governed=False):
            raise RuntimeError("boom")

    # Bad registry should be handled and not raise
    from packastack.target.completion import generate_completion_index

    idx = generate_completion_index(registry=BadRegistry())
    assert isinstance(idx, dict)

    # Bad releases loader should be handled
    monkeypatch.setattr("packastack.upstream.releases.load_openstack_packages", lambda repo, tgt: (_ for _ in ()).throw(RuntimeError("fail")))
    idx2 = generate_completion_index(releases_repo=tmp_path, openstack_target="train")
    assert isinstance(idx2, dict)


def test_scoped_case_insensitive():
    idx = make_sample_index()
    out = get_completions("SoUrCe:py", index=idx)
    assert any(s.startswith("SoUrCe:") for s in out)
    out2 = get_completions("CANONICAL:openstack/f", index=idx)
    assert any(s.startswith("CANONICAL:") for s in out2)


def test_save_and_load_default_path(monkeypatch, tmp_path):
    # Ensure save/load with default path uses get_completion_cache_path
    monkeypatch.setattr("packastack.target.completion.Path.home", lambda: tmp_path)
    from packastack.target.completion import load_completion_index, save_completion_index

    idx = make_sample_index()
    # Save with default path
    save_completion_index(idx, path=None)
    loaded = load_completion_index(path=None)
    assert loaded is not None
    assert loaded.get("source_packages") == idx["source_packages"]
