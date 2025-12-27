"""Tests for the debian module."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from packastack.debian import (
    BinaryStanza,
    ParsedDependency,
    SourcePackage,
    get_changelog_version,
    parse_control,
    parse_dependency_field,
)


class TestParsedDependency:
    """Tests for ParsedDependency dataclass."""

    def test_basic_dependency(self) -> None:
        dep = ParsedDependency(name="python3")
        assert dep.name == "python3"
        assert dep.relation == ""
        assert dep.version == ""
        assert dep.alternatives == []

    def test_versioned_dependency(self) -> None:
        dep = ParsedDependency(name="python3", relation=">=", version="3.10")
        assert dep.name == "python3"
        assert dep.relation == ">="
        assert dep.version == "3.10"

    def test_str_basic(self) -> None:
        dep = ParsedDependency(name="python3")
        assert str(dep) == "python3"

    def test_str_versioned(self) -> None:
        dep = ParsedDependency(name="python3", relation=">=", version="3.10")
        assert str(dep) == "python3 (>= 3.10)"

    def test_str_with_alternatives(self) -> None:
        alt = ParsedDependency(name="python3.11")
        dep = ParsedDependency(name="python3", alternatives=[alt])
        result = str(dep)
        assert "python3" in result
        assert "|" in result


class TestParseDependencyField:
    """Tests for parse_dependency_field function."""

    def test_single_package(self) -> None:
        deps = parse_dependency_field("python3")
        assert len(deps) == 1
        assert deps[0].name == "python3"

    def test_multiple_packages(self) -> None:
        deps = parse_dependency_field("python3, debhelper")
        assert len(deps) == 2
        assert deps[0].name == "python3"
        assert deps[1].name == "debhelper"

    def test_versioned_package(self) -> None:
        deps = parse_dependency_field("python3 (>= 3.10)")
        assert len(deps) == 1
        assert deps[0].name == "python3"
        assert deps[0].relation == ">="
        assert deps[0].version == "3.10"

    def test_strictly_greater(self) -> None:
        deps = parse_dependency_field("python3 (>> 3.9)")
        assert len(deps) == 1
        assert deps[0].relation == ">>"
        assert deps[0].version == "3.9"

    def test_strictly_less(self) -> None:
        deps = parse_dependency_field("python3 (<< 3.12)")
        assert len(deps) == 1
        assert deps[0].relation == "<<"
        assert deps[0].version == "3.12"

    def test_alternatives(self) -> None:
        deps = parse_dependency_field("python3 | python2.7")
        assert len(deps) == 1
        assert deps[0].name == "python3"
        assert len(deps[0].alternatives) == 1
        assert deps[0].alternatives[0].name == "python2.7"

    def test_arch_qualifiers(self) -> None:
        deps = parse_dependency_field("libc6 [amd64]")
        assert len(deps) == 1
        assert deps[0].name == "libc6"
        assert "amd64" in deps[0].arch_qualifiers

    def test_negative_arch_qualifiers(self) -> None:
        deps = parse_dependency_field("libc6 [!i386]")
        assert len(deps) == 1
        assert "!i386" in deps[0].arch_qualifiers

    def test_build_profile(self) -> None:
        # Build profiles are not stripped in current implementation
        deps = parse_dependency_field("python3 <!nocheck>")
        assert len(deps) == 1
        # The build profile is kept as part of the name (not stripped)
        assert "python3" in deps[0].name

    def test_complex_dependency(self) -> None:
        deps = parse_dependency_field(
            "debhelper-compat (= 13), python3 (>= 3.10), sphinx | python3-sphinx"
        )
        assert len(deps) == 3
        assert deps[0].name == "debhelper-compat"
        assert deps[0].version == "13"
        assert deps[1].name == "python3"
        assert deps[2].name == "sphinx"
        assert len(deps[2].alternatives) == 1

    def test_empty_field(self) -> None:
        deps = parse_dependency_field("")
        assert deps == []

    def test_whitespace_handling(self) -> None:
        deps = parse_dependency_field("  python3  ,  debhelper  ")
        assert len(deps) == 2
        assert deps[0].name == "python3"
        assert deps[1].name == "debhelper"


class TestSourcePackage:
    """Tests for SourcePackage dataclass."""

    def test_default_values(self) -> None:
        pkg = SourcePackage(name="test-src")
        assert pkg.name == "test-src"
        assert pkg.section == ""
        assert pkg.build_depends == []
        assert pkg.build_depends_indep == []
        assert pkg.binaries == []

    def test_get_runtime_depends(self) -> None:
        binary = BinaryStanza(
            name="test-bin",
            architecture="any",
            depends=[
                ParsedDependency(name="python3"),
                ParsedDependency(name="libc6"),
            ],
        )
        pkg = SourcePackage(name="test-src", binaries=[binary])
        runtime = pkg.get_runtime_depends()
        assert len(runtime) == 2
        assert any(d.name == "python3" for d in runtime)

    def test_get_all_binary_names(self) -> None:
        pkg = SourcePackage(
            name="test-src",
            binaries=[
                BinaryStanza(name="test-bin1", architecture="any"),
                BinaryStanza(name="test-bin2", architecture="all"),
            ],
        )
        names = pkg.get_all_binary_names()
        assert "test-bin1" in names
        assert "test-bin2" in names


class TestBinaryStanza:
    """Tests for BinaryStanza dataclass."""

    def test_default_values(self) -> None:
        stanza = BinaryStanza(name="test-bin", architecture="any")
        assert stanza.name == "test-bin"
        assert stanza.architecture == "any"
        assert stanza.depends == []
        assert stanza.pre_depends == []
        assert stanza.recommends == []
        assert stanza.suggests == []
        assert stanza.section == ""
        assert stanza.description == ""


class TestParseControl:
    """Tests for parse_control function."""

    def test_minimal_control(self) -> None:
        control_content = """\
Source: test-package
Section: python
Maintainer: Test <test@example.com>

Package: test-package
Architecture: all
Description: A test package
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            control_path = Path(tmpdir) / "control"
            control_path.write_text(control_content)

            pkg = parse_control(control_path)
            assert pkg.name == "test-package"
            assert pkg.section == "python"
            assert len(pkg.binaries) == 1
            assert pkg.binaries[0].name == "test-package"

    def test_with_build_depends(self) -> None:
        control_content = """\
Source: test-package
Section: python
Maintainer: Test <test@example.com>
Build-Depends: debhelper-compat (= 13), python3

Package: test-package
Architecture: all
Description: A test package
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            control_path = Path(tmpdir) / "control"
            control_path.write_text(control_content)

            pkg = parse_control(control_path)
            assert len(pkg.build_depends) == 2
            assert pkg.build_depends[0].name == "debhelper-compat"
            assert pkg.build_depends[1].name == "python3"

    def test_with_build_depends_indep(self) -> None:
        control_content = """\
Source: test-package
Section: python
Maintainer: Test <test@example.com>
Build-Depends: debhelper-compat (= 13)
Build-Depends-Indep: python3-sphinx

Package: test-package
Architecture: all
Description: A test package
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            control_path = Path(tmpdir) / "control"
            control_path.write_text(control_content)

            pkg = parse_control(control_path)
            assert len(pkg.build_depends_indep) == 1
            assert pkg.build_depends_indep[0].name == "python3-sphinx"

    def test_multiple_binaries(self) -> None:
        control_content = """\
Source: nova
Section: net
Maintainer: Test <test@example.com>
Build-Depends: debhelper-compat (= 13)

Package: nova-api
Architecture: all
Depends: python3-nova
Description: Nova API service

Package: nova-compute
Architecture: all
Depends: python3-nova, qemu-kvm
Description: Nova compute service

Package: python3-nova
Architecture: all
Description: Nova Python library
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            control_path = Path(tmpdir) / "control"
            control_path.write_text(control_content)

            pkg = parse_control(control_path)
            assert pkg.name == "nova"
            assert len(pkg.binaries) == 3

            binaries_by_name = {b.name: b for b in pkg.binaries}
            assert "nova-api" in binaries_by_name
            assert "nova-compute" in binaries_by_name
            assert "python3-nova" in binaries_by_name

            # Check dependencies
            nova_compute = binaries_by_name["nova-compute"]
            dep_names = [d.name for d in nova_compute.depends]
            assert "python3-nova" in dep_names
            assert "qemu-kvm" in dep_names

    def test_missing_source_stanza(self) -> None:
        control_content = """\
Package: test-package
Architecture: all
Description: A test package with no source stanza
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            control_path = Path(tmpdir) / "control"
            control_path.write_text(control_content)

            with pytest.raises(ValueError, match="Missing Source field"):
                parse_control(control_path)


class TestGetChangelogVersion:
    """Tests for get_changelog_version function."""

    def test_parses_changelog(self) -> None:
        changelog_content = """\
nova (2:26.0.0-0ubuntu1) plucky; urgency=medium

  * New upstream release for OpenStack 2024.2 (Dalmatian).

 -- Test Maintainer <test@example.com>  Mon, 01 Jan 2024 00:00:00 +0000
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            changelog_path = Path(tmpdir) / "debian" / "changelog"
            changelog_path.parent.mkdir(parents=True)
            changelog_path.write_text(changelog_content)

            version = get_changelog_version(changelog_path)
            assert version == "2:26.0.0-0ubuntu1"

    def test_returns_empty_when_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            changelog_path = Path(tmpdir) / "changelog"
            changelog_path.write_text("invalid content")
            version = get_changelog_version(changelog_path)
            assert version == ""

    def test_parses_simple_version(self) -> None:
        changelog_content = """\
test-package (1.0.0-1) noble; urgency=medium

  * Initial release.

 -- Test <test@example.com>  Mon, 01 Jan 2024 00:00:00 +0000
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            changelog_path = Path(tmpdir) / "debian" / "changelog"
            changelog_path.parent.mkdir(parents=True)
            changelog_path.write_text(changelog_content)

            version = get_changelog_version(changelog_path)
            assert version == "1.0.0-1"
