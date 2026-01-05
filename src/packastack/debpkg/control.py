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

"""Debian control file parsing utilities using python-debian."""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

# Suppress python3-apt warning - it's optional and not installable via pip
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message=".*python.*-apt.*")
    warnings.filterwarnings("ignore", message=".*apt_pkg.*")
    from debian.deb822 import Deb822

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass
class ParsedDependency:
    """Represents a single parsed dependency with optional version constraint."""

    name: str
    relation: str = ""  # >=, <=, =, >>, <<, or empty
    version: str = ""
    arch_qualifiers: list[str] = field(default_factory=list)  # [amd64, !i386]
    alternatives: list[ParsedDependency] = field(default_factory=list)

    def __str__(self) -> str:
        base = self.name
        if self.relation and self.version:
            base = f"{self.name} ({self.relation} {self.version})"
        if self.arch_qualifiers:
            base = f"{base} [{' '.join(self.arch_qualifiers)}]"
        if self.alternatives:
            alts = " | ".join(str(a) for a in self.alternatives)
            return f"{base} | {alts}"
        return base


def parse_single_dependency(dep_str: str) -> ParsedDependency:
    """Parse a single dependency specification.

    Examples:
        python3 (>= 3.10)
        libc6 (>= 2.35)
        python3-foo
        python3-bar [amd64]
        python3-baz:any
    """
    dep_str = dep_str.strip()

    # Remove :any or :native qualifiers
    dep_str = re.sub(r":(?:any|native)", "", dep_str)

    # Extract architecture qualifiers [amd64 !i386]
    arch_qualifiers: list[str] = []
    arch_match = re.search(r"\[([^\]]+)\]", dep_str)
    if arch_match:
        arch_qualifiers = arch_match.group(1).split()
        dep_str = dep_str[: arch_match.start()] + dep_str[arch_match.end() :]
        dep_str = dep_str.strip()

    # Pattern: name (relation version)
    pattern = r"^([a-z0-9][a-z0-9+\-.]+)(?:\s*\(([<>=]+)\s*([^)]+)\))?$"
    match = re.match(pattern, dep_str.strip())
    if not match:
        return ParsedDependency(name=dep_str.strip(), arch_qualifiers=arch_qualifiers)

    name = match.group(1)
    relation = match.group(2) or ""
    version = match.group(3) or ""

    return ParsedDependency(
        name=name,
        relation=relation,
        version=version.strip(),
        arch_qualifiers=arch_qualifiers,
    )


def parse_dependency_field(field_value: str) -> list[ParsedDependency]:
    """Parse a full dependency field with alternatives.

    Example:
        python3-foo (>= 1.0) | python3-bar, python3-baz
    """
    if not field_value.strip():
        return []

    deps: list[ParsedDependency] = []

    # Split by comma to get individual dependency groups
    for group in field_value.split(","):
        group = group.strip()
        if not group:
            continue

        # Handle alternatives (|)
        alternatives = [a.strip() for a in group.split("|")]
        if len(alternatives) == 1:
            deps.append(parse_single_dependency(alternatives[0]))
        else:
            # First is primary, rest are alternatives
            primary = parse_single_dependency(alternatives[0])
            primary.alternatives = [parse_single_dependency(a) for a in alternatives[1:]]
            deps.append(primary)

    return deps


@dataclass
class SourcePackage:
    """Represents a Debian source package parsed from debian/control."""

    name: str
    version: str = ""
    maintainer: str = ""
    section: str = ""
    priority: str = ""
    build_depends: list[ParsedDependency] = field(default_factory=list)
    build_depends_indep: list[ParsedDependency] = field(default_factory=list)
    binaries: list[BinaryStanza] = field(default_factory=list)

    def get_runtime_depends(self) -> list[ParsedDependency]:
        """Get all runtime dependencies from all binary packages."""
        deps: list[ParsedDependency] = []
        for binary in self.binaries:
            deps.extend(binary.depends)
            deps.extend(binary.pre_depends)
        return deps

    def get_all_binary_names(self) -> list[str]:
        """Get names of all binary packages produced by this source."""
        return [b.name for b in self.binaries]


@dataclass
class BinaryStanza:
    """Represents a binary package stanza from debian/control."""

    name: str
    architecture: str = ""
    section: str = ""
    priority: str = ""
    depends: list[ParsedDependency] = field(default_factory=list)
    pre_depends: list[ParsedDependency] = field(default_factory=list)
    recommends: list[ParsedDependency] = field(default_factory=list)
    suggests: list[ParsedDependency] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    description: str = ""


def iter_control_paragraphs(control_path: Path) -> Iterator[Deb822]:
    """Iterate over paragraphs in a debian/control file."""
    with control_path.open(encoding="utf-8") as f:
        yield from Deb822.iter_paragraphs(f, use_apt_pkg=False)


def parse_control(control_path: Path) -> SourcePackage:
    """Parse a debian/control file and return a SourcePackage.

    Args:
        control_path: Path to the debian/control file.

    Returns:
        SourcePackage with all stanzas parsed.
    """
    paragraphs = list(iter_control_paragraphs(control_path))
    if not paragraphs:
        raise ValueError(f"Empty or invalid control file: {control_path}")

    # First paragraph is the source stanza
    source_para = paragraphs[0]
    source_name = source_para.get("Source", "")
    if not source_name:
        raise ValueError(f"Missing Source field in control file: {control_path}")

    source = SourcePackage(
        name=source_name,
        maintainer=source_para.get("Maintainer", ""),
        section=source_para.get("Section", ""),
        priority=source_para.get("Priority", ""),
        build_depends=parse_dependency_field(source_para.get("Build-Depends", "")),
        build_depends_indep=parse_dependency_field(source_para.get("Build-Depends-Indep", "")),
    )

    # Remaining paragraphs are binary stanzas
    for para in paragraphs[1:]:
        pkg_name = para.get("Package", "")
        if not pkg_name:
            continue

        provides_raw = para.get("Provides", "")
        provides = [p.strip().split("(")[0].strip() for p in provides_raw.split(",") if p.strip()]

        binary = BinaryStanza(
            name=pkg_name,
            architecture=para.get("Architecture", ""),
            section=para.get("Section", ""),
            priority=para.get("Priority", ""),
            depends=parse_dependency_field(para.get("Depends", "")),
            pre_depends=parse_dependency_field(para.get("Pre-Depends", "")),
            recommends=parse_dependency_field(para.get("Recommends", "")),
            suggests=parse_dependency_field(para.get("Suggests", "")),
            provides=provides,
            description=para.get("Description", ""),
        )
        source.binaries.append(binary)

    return source


def get_changelog_version(changelog_path: Path) -> str:
    """Extract version from debian/changelog.

    Args:
        changelog_path: Path to debian/changelog file.

    Returns:
        Version string (e.g., "1:29.0.0-0ubuntu1")
    """
    with changelog_path.open(encoding="utf-8") as f:
        first_line = f.readline()

    # Format: package (version) distribution; urgency=xxx
    match = re.match(r"^[^\s]+\s+\(([^)]+)\)", first_line)
    if match:
        return match.group(1)
    return ""


def format_dependency_list(deps: list[ParsedDependency]) -> str:
    """Format a list of dependencies as a debian control field value.

    Args:
        deps: List of ParsedDependency objects.

    Returns:
        Formatted string suitable for Depends/Build-Depends field.
    """
    return ",\n ".join(str(d) for d in deps)


def merge_dependencies(
    existing: list[ParsedDependency],
    new_deps: list[ParsedDependency],
    version_overrides: dict[str, str] | None = None,
) -> list[ParsedDependency]:
    """Merge new dependencies into existing ones, preserving order.

    Args:
        existing: Existing dependency list.
        new_deps: New dependencies to add.
        version_overrides: Optional dict of pkg_name -> version to update.

    Returns:
        Merged list with new deps added and versions updated.
    """
    result: list[ParsedDependency] = []
    seen: set[str] = set()

    # First, add existing deps with version overrides applied
    for dep in existing:
        if dep.name in seen:
            continue
        seen.add(dep.name)

        # Apply version override if provided
        if version_overrides and dep.name in version_overrides:
            # Only update if there's no existing constraint or new is higher
            new_version = version_overrides[dep.name]
            if new_version:
                dep = ParsedDependency(
                    name=dep.name,
                    relation=">=",
                    version=new_version,
                    arch_qualifiers=dep.arch_qualifiers,
                    alternatives=dep.alternatives,
                )
        result.append(dep)

    # Then, add new deps that weren't already present
    for dep in new_deps:
        if dep.name in seen:
            continue
        seen.add(dep.name)
        result.append(dep)

    return result


def update_control_dependencies(
    control_path: Path,
    new_deps: list[ParsedDependency],
    version_overrides: dict[str, str] | None = None,
    binary_name: str | None = None,
) -> bool:
    """Update dependencies in a debian/control file.

    Args:
        control_path: Path to debian/control file.
        new_deps: New dependencies to merge.
        version_overrides: Optional version constraints to apply.
        binary_name: Specific binary package to update (None = all python3-* binaries).

    Returns:
        True if file was modified, False otherwise.
    """
    if not control_path.exists():
        return False

    content = control_path.read_text(encoding="utf-8")
    lines = content.split("\n")

    # Parse to get structure
    source = parse_control(control_path)

    # Find which binaries to update
    target_binaries = []
    for binary in source.binaries:
        if binary_name and binary.name != binary_name:
            continue
        if binary.name.startswith("python3-"):
            target_binaries.append(binary)

    if not target_binaries:
        return False

    modified = False

    for binary in target_binaries:
        # Merge dependencies
        merged = merge_dependencies(binary.depends, new_deps, version_overrides)
        if merged == binary.depends:
            continue

        # Find and update the Depends field in the file
        in_binary = False
        binary_start = -1
        depends_start = -1
        depends_end = -1

        for i, line in enumerate(lines):
            if line.startswith("Package:") and binary.name in line:
                in_binary = True
                binary_start = i
            elif in_binary and line.startswith("Package:"):
                in_binary = False
            elif in_binary and line.startswith("Depends:"):
                depends_start = i
                # Find end of depends field (continuation lines start with space)
                j = i + 1
                while j < len(lines) and lines[j].startswith((" ", "\t")):
                    j += 1
                depends_end = j
                break

        if depends_start >= 0 and depends_end >= 0:
            # Replace depends field
            new_depends_line = "Depends: " + format_dependency_list(merged)
            lines[depends_start:depends_end] = [new_depends_line]
            modified = True

    if modified:
        control_path.write_text("\n".join(lines), encoding="utf-8")

    return modified


def fix_priority_extra(control_path: Path) -> bool:
    """Replace deprecated 'Priority: extra' with 'Priority: optional'.

    Since Debian Policy 4.0.1, 'extra' priority is deprecated and
    should be replaced with 'optional'.

    Args:
        control_path: Path to the debian/control file.

    Returns:
        True if any changes were made.
    """
    if not control_path.exists():
        return False

    try:
        content = control_path.read_text(encoding="utf-8")
    except OSError:
        return False

    # Case-insensitive match for 'Priority: extra' (with possible whitespace)
    updated = re.sub(
        r"^(Priority:\s*)extra\s*$",
        r"\1optional",
        content,
        flags=re.MULTILINE | re.IGNORECASE,
    )

    if updated != content:
        try:
            control_path.write_text(updated, encoding="utf-8")
            return True
        except OSError:
            return False

    return False


def ensure_misc_pre_depends(control_path: Path) -> bool:
    """Ensure packages with systemd units have Pre-Depends: ${misc:Pre-Depends}.

    This is required for packages using init-system-helpers to avoid
    the 'missing-dependency-on-init-system-helpers' lintian warning.

    Args:
        control_path: Path to the debian/control file.

    Returns:
        True if any changes were made.
    """
    if not control_path.exists():
        return False

    try:
        content = control_path.read_text(encoding="utf-8")
    except OSError:
        return False

    # Check if ${misc:Pre-Depends} is already present
    if "${misc:Pre-Depends}" in content:
        return False

    lines = content.split("\n")
    modified = False
    i = 0

    while i < len(lines):
        line = lines[i]

        # Find Package: stanzas (binary packages)
        if line.startswith("Package:"):
            # Look for Pre-Depends in this stanza
            has_pre_depends = False
            stanza_end = i + 1

            while stanza_end < len(lines) and not lines[stanza_end].startswith("Package:"):
                if lines[stanza_end].startswith("Pre-Depends:"):
                    has_pre_depends = True
                    # Add ${misc:Pre-Depends} if not present
                    if "${misc:Pre-Depends}" not in lines[stanza_end]:
                        lines[stanza_end] = lines[stanza_end].rstrip()
                        if lines[stanza_end].endswith(","):
                            lines[stanza_end] += " ${misc:Pre-Depends},"
                        else:
                            lines[stanza_end] += ", ${misc:Pre-Depends}"
                        modified = True
                stanza_end += 1

            # If no Pre-Depends exists, add one after Architecture:
            if not has_pre_depends:
                for j in range(i + 1, stanza_end):
                    if lines[j].startswith("Architecture:"):
                        lines.insert(j + 1, "Pre-Depends: ${misc:Pre-Depends}")
                        modified = True
                        break

            i = stanza_end
        else:
            i += 1

    if modified:
        try:
            control_path.write_text("\n".join(lines), encoding="utf-8")
            return True
        except OSError:
            return False

    return False


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        source = parse_control(path)
        print(f"Source: {source.name}")
        print(f"Build-Depends: {len(source.build_depends)}")
        for binary in source.binaries:
            print(f"  Binary: {binary.name}")
            print(f"    Depends: {len(binary.depends)}")
