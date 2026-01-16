# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for packastack.debpkg.manpages module."""

from __future__ import annotations

from pathlib import Path

from packastack.debpkg.manpages import (
    add_sphinx_build_dep,
    apply_man_pages_support,
    create_manpages_file,
    detect_sphinx_man_pages,
    get_main_package_name,
    has_man_page_rules,
    has_sphinx_build_dep,
    patch_rules_for_man_pages,
)


class TestDetectSphinxManPages:
    """Tests for detect_sphinx_man_pages function."""

    def test_detects_man_pages_in_doc_source(self, tmp_path: Path) -> None:
        """Test detection of man_pages in doc/source/conf.py."""
        conf_py = tmp_path / "doc" / "source" / "conf.py"
        conf_py.parent.mkdir(parents=True)
        conf_py.write_text(
            """
# Sphinx configuration
project = 'glance'

man_pages = [
    ('cli/glancestatus', 'glance-status', 'Glance Status Utility', ['OpenStack'], 1),
]
"""
        )

        result = detect_sphinx_man_pages(tmp_path)

        assert result.has_man_pages is True
        assert result.conf_py_path == conf_py
        assert result.doc_source_dir == "doc/source"

    def test_detects_man_pages_with_no_spaces(self, tmp_path: Path) -> None:
        """Test detection of man_pages=[."""
        conf_py = tmp_path / "doc" / "source" / "conf.py"
        conf_py.parent.mkdir(parents=True)
        conf_py.write_text(
            """
man_pages=[
    ('cli/status', 'nova-status', 'Nova Status', [], 1),
]
"""
        )

        result = detect_sphinx_man_pages(tmp_path)
        assert result.has_man_pages is True

    def test_no_man_pages_config(self, tmp_path: Path) -> None:
        """Test when conf.py exists but has no man_pages."""
        conf_py = tmp_path / "doc" / "source" / "conf.py"
        conf_py.parent.mkdir(parents=True)
        conf_py.write_text(
            """
project = 'glance'
extensions = ['sphinx.ext.autodoc']
"""
        )

        result = detect_sphinx_man_pages(tmp_path)
        assert result.has_man_pages is False

    def test_no_conf_py(self, tmp_path: Path) -> None:
        """Test when no conf.py exists."""
        result = detect_sphinx_man_pages(tmp_path)
        assert result.has_man_pages is False
        assert result.conf_py_path is None

    def test_docs_folder_variant(self, tmp_path: Path) -> None:
        """Test detection in docs/source instead of doc/source."""
        conf_py = tmp_path / "docs" / "source" / "conf.py"
        conf_py.parent.mkdir(parents=True)
        conf_py.write_text("man_pages = []")

        result = detect_sphinx_man_pages(tmp_path)
        assert result.has_man_pages is True
        assert result.doc_source_dir == "docs/source"


class TestHasSphinxBuildDep:
    """Tests for has_sphinx_build_dep function."""

    def test_has_sphinx_in_build_depends(self, tmp_path: Path) -> None:
        """Test detection of python3-sphinx in Build-Depends."""
        control = tmp_path / "control"
        control.write_text(
            """Source: glance
Build-Depends: debhelper-compat (= 13),
 python3-sphinx,
 python3-all,
"""
        )

        assert has_sphinx_build_dep(control) is True

    def test_has_sphinx_in_build_depends_indep(self, tmp_path: Path) -> None:
        """Test detection of python3-sphinx in Build-Depends-Indep."""
        control = tmp_path / "control"
        control.write_text(
            """Source: glance
Build-Depends: debhelper-compat (= 13),
Build-Depends-Indep: python3-sphinx,
"""
        )

        assert has_sphinx_build_dep(control) is True

    def test_no_sphinx(self, tmp_path: Path) -> None:
        """Test when python3-sphinx is not present."""
        control = tmp_path / "control"
        control.write_text(
            """Source: glance
Build-Depends: debhelper-compat (= 13),
"""
        )

        assert has_sphinx_build_dep(control) is False

    def test_missing_file(self, tmp_path: Path) -> None:
        """Test with missing control file."""
        control = tmp_path / "control"
        assert has_sphinx_build_dep(control) is False


class TestAddSphinxBuildDep:
    """Tests for add_sphinx_build_dep function."""

    def test_adds_to_build_depends_indep(self, tmp_path: Path) -> None:
        """Test adding python3-sphinx to Build-Depends-Indep."""
        control = tmp_path / "control"
        control.write_text(
            """Source: glance
Build-Depends: debhelper-compat (= 13),
Build-Depends-Indep: python3-all,
 python3-pbr,

Package: glance
"""
        )

        result = add_sphinx_build_dep(control)

        assert result is True
        content = control.read_text()
        assert "python3-sphinx" in content

    def test_adds_to_build_depends_when_no_indep(self, tmp_path: Path) -> None:
        """Test adding to Build-Depends when Build-Depends-Indep is absent."""
        control = tmp_path / "control"
        control.write_text(
            """Source: glance
Build-Depends: debhelper-compat (= 13),
 python3-all,

Package: glance
"""
        )

        result = add_sphinx_build_dep(control)

        assert result is True
        content = control.read_text()
        assert "python3-sphinx" in content

    def test_no_change_when_already_present(self, tmp_path: Path) -> None:
        """Test no modification when python3-sphinx already exists."""
        control = tmp_path / "control"
        original = """Source: glance
Build-Depends: debhelper-compat (= 13),
 python3-sphinx,
"""
        control.write_text(original)

        result = add_sphinx_build_dep(control)

        assert result is False
        assert control.read_text() == original


class TestHasManPageRules:
    """Tests for has_man_page_rules function."""

    def test_detects_sphinx_build_man(self, tmp_path: Path) -> None:
        """Test detection of sphinx-build -b man."""
        rules = tmp_path / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@

override_dh_sphinxdoc:
\tdh_sphinxdoc
\tsphinx-build -b man doc/source debian/man
"""
        )

        assert has_man_page_rules(rules) is True

    def test_detects_installman_pattern(self, tmp_path: Path) -> None:
        """Test detection of dh_installman debian/man/."""
        rules = tmp_path / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@

override_dh_installman:
\tdh_installman debian/man/*.1
"""
        )

        assert has_man_page_rules(rules) is True

    def test_no_man_page_rules(self, tmp_path: Path) -> None:
        """Test when no man page rules exist."""
        rules = tmp_path / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@
"""
        )

        assert has_man_page_rules(rules) is False

    def test_missing_file(self, tmp_path: Path) -> None:
        """Test with missing rules file."""
        rules = tmp_path / "rules"
        assert has_man_page_rules(rules) is False


class TestPatchRulesForManPages:
    """Tests for patch_rules_for_man_pages function."""

    def test_adds_override_dh_sphinxdoc(self, tmp_path: Path) -> None:
        """Test adding new override_dh_sphinxdoc."""
        rules = tmp_path / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@
"""
        )

        result = patch_rules_for_man_pages(rules)

        assert result is True
        content = rules.read_text()
        assert "override_dh_sphinxdoc:" in content
        assert "sphinx-build -b man doc/source debian/man" in content
        assert "override_dh_installman:" in content
        assert "dh_installman debian/man/*.1" in content

    def test_appends_to_existing_override(self, tmp_path: Path) -> None:
        """Test appending to existing override_dh_sphinxdoc."""
        rules = tmp_path / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@

override_dh_sphinxdoc:
\tdh_sphinxdoc
"""
        )

        result = patch_rules_for_man_pages(rules)

        assert result is True
        content = rules.read_text()
        assert "sphinx-build -b man doc/source debian/man" in content
        # Should have only one override_dh_sphinxdoc
        assert content.count("override_dh_sphinxdoc:") == 1

    def test_adds_install_guard_when_already_configured(self, tmp_path: Path) -> None:
        """Ensure install guard is added even if build rules exist."""
        rules = tmp_path / "rules"
        original = """#!/usr/bin/make -f
%:
\tdh $@

override_dh_sphinxdoc:
\tdh_sphinxdoc
\tsphinx-build -b man doc/source debian/man
"""
        rules.write_text(original)

        result = patch_rules_for_man_pages(rules)

        assert result is True
        content = rules.read_text()
        assert "override_dh_installman:" in content
        assert "No generated man pages" in content

    def test_custom_doc_source_dir(self, tmp_path: Path) -> None:
        """Test with custom doc source directory."""
        rules = tmp_path / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@
"""
        )

        result = patch_rules_for_man_pages(rules, doc_source_dir="docs")

        assert result is True
        content = rules.read_text()
        assert "sphinx-build -b man docs debian/man" in content


class TestCreateManpagesFile:
    """Tests for create_manpages_file function."""

    def test_creates_new_file(self, tmp_path: Path) -> None:
        """Test creating new .manpages file."""
        result = create_manpages_file(tmp_path, "glance")

        assert result is True
        manpages = tmp_path / "glance.manpages"
        assert manpages.exists()
        assert "debian/man/*.1" in manpages.read_text()

    def test_appends_to_existing_file(self, tmp_path: Path) -> None:
        """Test appending to existing .manpages file."""
        manpages = tmp_path / "glance.manpages"
        manpages.write_text("some/other/man.1\n")

        result = create_manpages_file(tmp_path, "glance")

        assert result is True
        content = manpages.read_text()
        assert "some/other/man.1" in content
        assert "debian/man/*.1" in content

    def test_no_change_when_already_present(self, tmp_path: Path) -> None:
        """Test no modification when pattern already exists."""
        manpages = tmp_path / "glance.manpages"
        manpages.write_text("debian/man/*.1\n")

        result = create_manpages_file(tmp_path, "glance")

        assert result is False


class TestGetMainPackageName:
    """Tests for get_main_package_name function."""

    def test_finds_service_package(self, tmp_path: Path) -> None:
        """Test finding main service package name."""
        control = tmp_path / "control"
        control.write_text(
            """Source: glance

Package: glance
Architecture: all

Package: glance-api
Architecture: all

Package: python3-glance
Architecture: all

Package: glance-doc
Architecture: all
"""
        )

        result = get_main_package_name(control)
        assert result == "glance"

    def test_skips_doc_and_python_packages(self, tmp_path: Path) -> None:
        """Test that -doc and python3- packages are skipped."""
        control = tmp_path / "control"
        control.write_text(
            """Source: oslo.config

Package: python3-oslo.config
Architecture: all

Package: python-oslo.config-doc
Architecture: all

Package: oslo-config-doc
Architecture: all
"""
        )

        # Should fall back to first non-doc package
        result = get_main_package_name(control)
        assert result == "python3-oslo.config"

    def test_missing_file(self, tmp_path: Path) -> None:
        """Test with missing control file."""
        control = tmp_path / "control"
        result = get_main_package_name(control)
        assert result is None


class TestApplyManPagesSupport:
    """Tests for apply_man_pages_support function."""

    def test_full_application(self, tmp_path: Path) -> None:
        """Test full man pages support application."""
        # Create conf.py with man_pages
        conf_py = tmp_path / "doc" / "source" / "conf.py"
        conf_py.parent.mkdir(parents=True)
        conf_py.write_text(
            """
man_pages = [
    ('cli/glancestatus', 'glance-status', 'Glance Status', [], 1),
]
"""
        )

        # Create debian directory with control and rules
        debian = tmp_path / "debian"
        debian.mkdir()

        control = debian / "control"
        control.write_text(
            """Source: glance
Build-Depends: debhelper-compat (= 13),

Package: glance
Architecture: all
"""
        )

        rules = debian / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@
"""
        )

        result = apply_man_pages_support(tmp_path)

        assert result.applied is True
        assert result.control_modified is True
        assert result.rules_modified is True
        assert result.manpages_created is True
        assert "python3-sphinx" in (debian / "control").read_text()
        assert "sphinx-build -b man" in (debian / "rules").read_text()
        assert (debian / "glance.manpages").exists()

    def test_no_application_without_man_pages(self, tmp_path: Path) -> None:
        """Test no changes when no man_pages configured."""
        # Create conf.py without man_pages
        conf_py = tmp_path / "doc" / "source" / "conf.py"
        conf_py.parent.mkdir(parents=True)
        conf_py.write_text("project = 'glance'\n")

        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "control").write_text("Source: glance\n")
        (debian / "rules").write_text("#!/usr/bin/make -f\n")

        result = apply_man_pages_support(tmp_path)

        assert result.applied is False

    def test_partial_application(self, tmp_path: Path) -> None:
        """Test partial application when some pieces already exist."""
        # Create conf.py with man_pages
        conf_py = tmp_path / "doc" / "source" / "conf.py"
        conf_py.parent.mkdir(parents=True)
        conf_py.write_text("man_pages = []\n")

        debian = tmp_path / "debian"
        debian.mkdir()

        # Control already has sphinx
        control = debian / "control"
        control.write_text(
            """Source: glance
Build-Depends: debhelper-compat (= 13),
 python3-sphinx,

Package: glance
Architecture: all
"""
        )

        rules = debian / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@
"""
        )

        result = apply_man_pages_support(tmp_path)

        assert result.applied is True
        assert result.control_modified is False  # Already had sphinx
        assert result.rules_modified is True
        assert result.manpages_created is True
