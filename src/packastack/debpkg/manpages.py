# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only
#
# Packastack is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License version 3, as published by the
# Free Software Foundation.
#
# Packastack is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranties of MERCHANTABILITY,
# SATISFACTORY QUALITY, or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# Packastack. If not, see <http://www.gnu.org/licenses/>.

"""Automatic man page generation support for Debian packages.

Detects Sphinx man_pages configuration in upstream projects and patches
debian packaging to build and install man pages automatically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ManPagesConfig:
    """Result of detecting man_pages configuration."""

    has_man_pages: bool
    conf_py_path: Path | None = None
    doc_source_dir: str = "doc/source"


def detect_sphinx_man_pages(workspace: Path) -> ManPagesConfig:
    """Detect if a project has Sphinx man_pages configured.

    Looks for doc/source/conf.py and checks if it contains man_pages
    configuration.

    Args:
        workspace: Path to the package workspace root.

    Returns:
        ManPagesConfig with detection results.
    """
    # Check common doc source locations
    doc_source_dirs = [
        "doc/source",
        "docs/source",
        "doc",
        "docs",
    ]

    for doc_dir in doc_source_dirs:
        conf_py = workspace / doc_dir / "conf.py"
        if conf_py.exists():
            content = conf_py.read_text(encoding="utf-8", errors="replace")
            # Look for man_pages = [ or man_pages=[ configuration
            if re.search(r"^\s*man_pages\s*=\s*\[", content, re.MULTILINE):
                return ManPagesConfig(
                    has_man_pages=True,
                    conf_py_path=conf_py,
                    doc_source_dir=doc_dir,
                )

    return ManPagesConfig(has_man_pages=False)


def has_sphinx_build_dep(control_path: Path) -> bool:
    """Check if python3-sphinx is already in Build-Depends.

    Args:
        control_path: Path to debian/control file.

    Returns:
        True if python3-sphinx is already a build dependency.
    """
    if not control_path.exists():
        return False

    content = control_path.read_text(encoding="utf-8")
    # Look for python3-sphinx in Build-Depends or Build-Depends-Indep
    return bool(re.search(r"python3-sphinx", content))


def add_sphinx_build_dep(control_path: Path) -> bool:
    """Add python3-sphinx to Build-Depends-Indep if not already present.

    Args:
        control_path: Path to debian/control file.

    Returns:
        True if the file was modified, False otherwise.
    """
    if not control_path.exists():
        return False

    if has_sphinx_build_dep(control_path):
        return False

    content = control_path.read_text(encoding="utf-8")
    lines = content.split("\n")
    modified = False

    # Find Build-Depends-Indep line and add python3-sphinx
    for i, line in enumerate(lines):
        if line.startswith("Build-Depends-Indep:"):
            # Find end of the field (continuation lines start with space/tab)
            j = i + 1
            while j < len(lines) and lines[j].startswith((" ", "\t")):
                j += 1
            # Insert before the end of the field
            insert_idx = j - 1 if j > i + 1 else i
            if insert_idx == i:
                # Single line, append to it
                lines[i] = lines[i].rstrip(",") + ",\n python3-sphinx,"
            else:
                # Multi-line, add as continuation
                lines.insert(j, " python3-sphinx,")
            modified = True
            break
    else:
        # No Build-Depends-Indep, check Build-Depends and add there
        for i, line in enumerate(lines):
            if line.startswith("Build-Depends:"):
                j = i + 1
                while j < len(lines) and lines[j].startswith((" ", "\t")):
                    j += 1
                lines.insert(j, " python3-sphinx,")
                modified = True
                break

    if modified:
        control_path.write_text("\n".join(lines), encoding="utf-8")

    return modified


def has_man_page_rules(rules_path: Path) -> bool:
    """Check if debian/rules already has man page build logic.

    Args:
        rules_path: Path to debian/rules file.

    Returns:
        True if man page building is already configured.
    """
    if not rules_path.exists():
        return False

    content = rules_path.read_text(encoding="utf-8")
    # Check for sphinx-build -b man or dh_installman with our pattern
    return bool(
        re.search(r"sphinx-build\s+-b\s+man", content)
        or re.search(r"dh_installman\s+debian/man/", content)
    )


def patch_rules_for_man_pages(rules_path: Path, doc_source_dir: str = "doc/source") -> bool:
    """Patch debian/rules to build and install man pages.

    Adds or augments override_dh_sphinxdoc to build man pages and ensures
    override_dh_installman is present with a guard so dh_installman does not
    fail when no man pages are produced.

    Args:
        rules_path: Path to debian/rules file.
        doc_source_dir: Path to Sphinx doc source directory.

    Returns:
        True if the file was modified, False otherwise.
    """
    if not rules_path.exists():
        return False

    content = rules_path.read_text(encoding="utf-8")
    lines = content.rstrip("\n").split("\n")

    # Man page build snippet
    man_build_snippet = f"""
# Build man pages from Sphinx documentation
override_dh_sphinxdoc:
	dh_sphinxdoc
	PYTHONPATH=. sphinx-build -b man {doc_source_dir} debian/man
"""

    man_install_snippet = """
override_dh_installman:
	# Only install if man pages were produced; avoid hard failure when none exist
	if [ -d debian/man ] && ls debian/man/*.1 >/dev/null 2>&1; then \\
		dh_installman debian/man/*.1; \\
	else \\
		echo "No generated man pages to install; skipping dh_installman"; \\
	fi
"""

    modified = False

    # Ensure sphinx-build -b man is present in override_dh_sphinxdoc
    if re.search(r"^override_dh_sphinxdoc\s*:", content, re.MULTILINE):
        new_lines = []
        in_override = False
        added = False
        for line in lines:
            new_lines.append(line)
            if line.startswith("override_dh_sphinxdoc"):
                in_override = True
            elif in_override and not line.startswith("\t") and line.strip():
                # End of override, insert before next target
                if not added and not any("sphinx-build -b man" in l for l in new_lines):
                    new_lines.insert(-1, f"\tPYTHONPATH=. sphinx-build -b man {doc_source_dir} debian/man")
                    modified = True
                    added = True
                in_override = False
        if in_override and not added and not any("sphinx-build -b man" in l for l in new_lines):
            new_lines.append(f"\tPYTHONPATH=. sphinx-build -b man {doc_source_dir} debian/man")
            modified = True
        lines = new_lines
    else:
        lines.append(man_build_snippet)
        modified = True

    # Ensure guarded override_dh_installman exists
    content_check = "\n".join(lines)
    if not re.search(r"^override_dh_installman\s*:", content_check, re.MULTILINE):
        lines.append(man_install_snippet)
        modified = True

    new_content = "\n".join(lines)
    if modified and new_content != content:
        rules_path.write_text(new_content, encoding="utf-8")
        return True

    return False


def create_manpages_file(debian_dir: Path, main_package: str) -> bool:
    """Create debian/<package>.manpages file to install man pages.

    Args:
        debian_dir: Path to debian directory.
        main_package: Name of the main binary package.

    Returns:
        True if file was created, False if it already exists.
    """
    manpages_file = debian_dir / f"{main_package}.manpages"
    if manpages_file.exists():
        content = manpages_file.read_text(encoding="utf-8")
        if "debian/man/*.1" in content:
            return False
        # Append to existing file
        with manpages_file.open("a", encoding="utf-8") as f:
            f.write("\ndebian/man/*.1\n")
        return True

    manpages_file.write_text("debian/man/*.1\n", encoding="utf-8")
    return True


def get_main_package_name(control_path: Path) -> str | None:
    """Get the main (non-doc, non-dbg) binary package name.

    Args:
        control_path: Path to debian/control file.

    Returns:
        Name of the main binary package, or None if not found.
    """
    if not control_path.exists():
        return None

    content = control_path.read_text(encoding="utf-8")

    # Find all Package: lines
    packages = re.findall(r"^Package:\s*(.+)$", content, re.MULTILINE)

    # Filter out -doc, -dbg, python3- packages to find the main one
    for pkg in packages:
        pkg = pkg.strip()
        if pkg.endswith("-doc"):
            continue
        if pkg.endswith("-dbg"):
            continue
        if pkg.startswith("python3-"):
            continue
        if pkg.startswith("python-"):
            continue
        return pkg

    # Fallback: return first non-doc package
    for pkg in packages:
        pkg = pkg.strip()
        if not pkg.endswith("-doc") and not pkg.endswith("-dbg"):
            return pkg

    return packages[0].strip() if packages else None


@dataclass
class ManPagesResult:
    """Result of applying man pages support to a package."""

    applied: bool
    control_modified: bool = False
    rules_modified: bool = False
    manpages_created: bool = False
    changelog_entry: str = ""


def apply_man_pages_support(workspace: Path) -> ManPagesResult:
    """Apply man pages support to a package if applicable.

    Detects if the package has Sphinx man_pages configured, and if so:
    - Adds python3-sphinx to Build-Depends
    - Patches debian/rules to build and install man pages
    - Creates debian/<package>.manpages file

    Args:
        workspace: Path to the package workspace root.

    Returns:
        ManPagesResult with details of what was modified.
    """
    config = detect_sphinx_man_pages(workspace)
    if not config.has_man_pages:
        return ManPagesResult(applied=False)

    debian_dir = workspace / "debian"
    control_path = debian_dir / "control"
    rules_path = debian_dir / "rules"

    # Get main package name for .manpages file
    main_package = get_main_package_name(control_path)
    if not main_package:
        return ManPagesResult(applied=False)

    # Apply changes
    control_modified = add_sphinx_build_dep(control_path)
    rules_modified = patch_rules_for_man_pages(rules_path, config.doc_source_dir)
    manpages_created = create_manpages_file(debian_dir, main_package)

    # Generate changelog entry
    changelog_entries = []
    if control_modified:
        changelog_entries.append("d/control: Add python3-sphinx to Build-Depends for man pages")
    if rules_modified:
        changelog_entries.append("d/rules: Build and install man pages from upstream Sphinx documentation")
    if manpages_created:
        changelog_entries.append(f"d/{main_package}.manpages: Install generated man pages")

    return ManPagesResult(
        applied=control_modified or rules_modified or manpages_created,
        control_modified=control_modified,
        rules_modified=rules_modified,
        manpages_created=manpages_created,
        changelog_entry="\n".join(f"  * {entry}" for entry in changelog_entries),
    )
