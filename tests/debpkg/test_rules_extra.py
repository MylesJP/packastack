
from packastack.debpkg import rules


def test_add_doctree_cleanup_nonexistent(tmp_path):
    p = tmp_path / "rules"
    assert not rules.add_doctree_cleanup(p)


def test_add_doctree_cleanup_append_installdocs(tmp_path):
    p = tmp_path / "rules"
    content = "override_dh_installdocs:\n\tdh_installdocs\n"
    p.write_text(content, encoding="utf-8")
    changed = rules.add_doctree_cleanup(p)
    assert changed
    out = p.read_text(encoding="utf-8")
    assert ".doctrees" in out


def test_add_doctree_cleanup_append_sphinxdoc(tmp_path):
    p = tmp_path / "rules"
    content = "override_dh_sphinxdoc:\n\tdh_sphinxdoc\n"
    p.write_text(content, encoding="utf-8")
    changed = rules.add_doctree_cleanup(p)
    assert changed
    out = p.read_text(encoding="utf-8")
    assert ".doctrees" in out


def test_add_doctree_cleanup_create_override(tmp_path):
    p = tmp_path / "rules"
    content = "all:\n\techo hi\n"
    p.write_text(content, encoding="utf-8")
    changed = rules.add_doctree_cleanup(p)
    assert changed
    out = p.read_text(encoding="utf-8")
    assert "override_dh_installdocs" in out


def test_ensure_sphinxdoc_addon_replace(tmp_path):
    p = tmp_path / "rules"
    content = "#!/usr/bin/make -f\ndh $@ --with python3\n"
    p.write_text(content, encoding="utf-8")
    changed = rules.ensure_sphinxdoc_addon(p)
    assert changed
    out = p.read_text(encoding="utf-8")
    assert "sphinxdoc" in out


def test_has_override(tmp_path):
    p = tmp_path / "rules"
    content = "override_dh_sphinxdoc:\n\tdh_sphinxdoc\n"
    p.write_text(content, encoding="utf-8")
    assert rules.has_override(p, "dh_sphinxdoc")


def test_add_doctree_cleanup_already_present(tmp_path):
    p = tmp_path / "rules"
    content = "something\nrm -rf debian/*/usr/share/doc/*/.doctrees\n"
    p.write_text(content, encoding="utf-8")
    assert not rules.add_doctree_cleanup(p)


def test_ensure_sphinxdoc_addon_no_change(tmp_path):
    p = tmp_path / "rules"
    content = "#!/usr/bin/make -f\n# nothing to change here\n"
    p.write_text(content, encoding="utf-8")
    assert not rules.ensure_sphinxdoc_addon(p)


def test_has_override_missing(tmp_path):
    p = tmp_path / "rules"
    p.write_text("no overrides here\n", encoding="utf-8")
    assert not rules.has_override(p, "dh_nonexistent")
