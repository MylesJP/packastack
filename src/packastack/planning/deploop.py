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

"""Dependency loop for PackaStack builds.

Provides dependency checking and build planning to automatically
build missing Build-Depends before the main package build.

The core purpose is to:
1. Parse upstream requirements.txt/pyproject.toml to determine dependencies
2. Check which dependencies are missing from the archive and local repo
3. Create a build plan (topological order) to build them first
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


@dataclass
class DependencyCheckResult:
    """Result of checking a single dependency."""

    name: str
    version_constraint: str = ""
    available_in_archive: bool = False
    available_in_local: bool = False
    archive_version: str = ""
    local_version: str = ""
    needs_build: bool = False


@dataclass
class DependencyBuildPlan:
    """Plan for building dependencies."""

    to_build: list[str] = field(default_factory=list)
    already_available: list[str] = field(default_factory=list)
    from_archive: list[str] = field(default_factory=list)
    from_local: list[str] = field(default_factory=list)
    check_results: dict[str, DependencyCheckResult] = field(default_factory=dict)


@dataclass
class DependencyBuildResult:
    """Result of building dependencies."""

    success: bool
    built: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def normalize_python_package_name(name: str) -> str:
    """Normalize a Python package name to Debian package format.

    PEP 503 normalization: lowercase, replace [-_.] with single hyphen.
    Then prepend 'python3-' for Debian.

    Args:
        name: Python package name (e.g., "oslo.config", "pbr").

    Returns:
        Debian package name (e.g., "python3-oslo.config", "python3-pbr").
    """
    # PEP 503 normalization
    normalized = re.sub(r"[-_.]+", "-", name.lower())
    return f"python3-{normalized}"


def parse_requirements_txt(path: Path) -> list[tuple[str, str]]:
    """Parse a requirements.txt file.

    Args:
        path: Path to requirements.txt file.

    Returns:
        List of (package_name, version_constraint) tuples.
    """
    if not path.exists():
        return []

    requirements: list[tuple[str, str]] = []
    content = path.read_text()

    for line in content.splitlines():
        line = line.strip()

        # Skip comments and empty lines
        if not line or line.startswith("#"):
            continue

        # Skip -r, -c, -e, -f directives
        if line.startswith(("-r", "-c", "-e", "-f", "--")):
            continue

        # Parse package spec
        # Handle: package, package==1.0, package>=1.0, package>=1.0,<2.0, etc.
        match = re.match(r"^([a-zA-Z0-9_.-]+)\s*(.*)$", line)
        if match:
            name = match.group(1)
            constraint = match.group(2).strip()
            requirements.append((name, constraint))

    return requirements


def parse_pyproject_toml_deps(path: Path) -> list[tuple[str, str]]:
    """Parse dependencies from pyproject.toml.

    Args:
        path: Path to pyproject.toml file.

    Returns:
        List of (package_name, version_constraint) tuples.
    """
    if not path.exists():
        return []

    try:
        import tomllib
    except ImportError:
        # Python < 3.11 fallback
        try:
            import tomli as tomllib  # type: ignore[import-not-found]
        except ImportError:
            logger.warning("tomllib/tomli not available, cannot parse pyproject.toml")
            return []

    try:
        content = path.read_text()
        data = tomllib.loads(content)
    except Exception as e:
        logger.warning(f"Failed to parse pyproject.toml: {e}")
        return []

    requirements: list[tuple[str, str]] = []

    # project.dependencies
    deps = data.get("project", {}).get("dependencies", [])
    for dep in deps:
        match = re.match(r"^([a-zA-Z0-9_.-]+)\s*(.*)$", str(dep))
        if match:
            requirements.append((match.group(1), match.group(2).strip()))

    return requirements


def check_archive_availability(
    package_name: str,
    distribution: str = "",
) -> tuple[bool, str]:
    """Check if a package is available in the Ubuntu archive.

    Args:
        package_name: Debian package name to check.
        distribution: Ubuntu codename (optional, uses default if not provided).

    Returns:
        Tuple of (is_available, version_string).
    """
    cmd = ["apt-cache", "policy", package_name]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            return False, ""

        # Parse apt-cache policy output
        output = result.stdout
        for line in output.splitlines():
            if "Candidate:" in line:
                version = line.split("Candidate:")[-1].strip()
                if version and version != "(none)":
                    return True, version

        return False, ""

    except Exception as e:
        logger.debug(f"Error checking archive for {package_name}: {e}")
        return False, ""


def check_local_repo_availability(
    package_name: str,
    local_repo_root: Path,
) -> tuple[bool, str]:
    """Check if a package is available in the local PackaStack repo.

    Args:
        package_name: Debian package name to check.
        local_repo_root: Path to the local APT repository.

    Returns:
        Tuple of (is_available, version_string).
    """
    packages_file = local_repo_root / "dists" / "local" / "main" / "binary-amd64" / "Packages"

    if not packages_file.exists():
        return False, ""

    try:
        content = packages_file.read_text()

        # Simple parsing of Packages file
        in_package = False
        current_version = ""

        for line in content.splitlines():
            if line.startswith("Package: "):
                in_package = line.split("Package: ")[-1].strip() == package_name
                current_version = ""
            elif in_package and line.startswith("Version: "):
                current_version = line.split("Version: ")[-1].strip()
                return True, current_version

        return False, ""

    except Exception as e:
        logger.debug(f"Error checking local repo for {package_name}: {e}")
        return False, ""


def check_dependencies(
    source_dir: Path,
    local_repo_root: Path | None = None,
    distribution: str = "",
) -> DependencyBuildPlan:
    """Check dependencies for a package source.

    Args:
        source_dir: Path to the package source directory.
        local_repo_root: Path to the PackaStack local APT repo (optional).
        distribution: Ubuntu codename (optional).

    Returns:
        DependencyBuildPlan with categorized dependencies.
    """
    plan = DependencyBuildPlan()

    # Parse requirements from source
    requirements: list[tuple[str, str]] = []

    # Try requirements.txt first
    reqs_file = source_dir / "requirements.txt"
    if reqs_file.exists():
        requirements.extend(parse_requirements_txt(reqs_file))

    # Also check pyproject.toml
    pyproject = source_dir / "pyproject.toml"
    if pyproject.exists():
        requirements.extend(parse_pyproject_toml_deps(pyproject))

    # Deduplicate by package name
    seen: set[str] = set()
    unique_reqs: list[tuple[str, str]] = []
    for name, constraint in requirements:
        if name.lower() not in seen:
            seen.add(name.lower())
            unique_reqs.append((name, constraint))

    # Check each requirement
    for py_name, constraint in unique_reqs:
        deb_name = normalize_python_package_name(py_name)

        result = DependencyCheckResult(
            name=deb_name,
            version_constraint=constraint,
        )

        # Check archive
        archive_avail, archive_ver = check_archive_availability(deb_name, distribution)
        result.available_in_archive = archive_avail
        result.archive_version = archive_ver

        # Check local repo
        if local_repo_root:
            local_avail, local_ver = check_local_repo_availability(deb_name, local_repo_root)
            result.available_in_local = local_avail
            result.local_version = local_ver

        # Determine if it needs building
        if result.available_in_archive:
            result.needs_build = False
            plan.from_archive.append(deb_name)
            plan.already_available.append(deb_name)
        elif result.available_in_local:
            result.needs_build = False
            plan.from_local.append(deb_name)
            plan.already_available.append(deb_name)
        else:
            result.needs_build = True
            plan.to_build.append(py_name)  # Use Python name for building

        plan.check_results[deb_name] = result

    return plan


def compute_topological_order(
    packages: Sequence[str],
    dep_graph: dict[str, list[str]],
) -> list[str]:
    """Compute topological order for building packages.

    Args:
        packages: List of package names to order.
        dep_graph: Mapping of package -> list of dependencies.

    Returns:
        Packages in build order (dependencies first).
    """
    # Simple topological sort using Kahn's algorithm
    # For now, just return packages in original order
    # (full implementation would need to build the dependency graph)
    return list(packages)


def create_build_plan(
    packages: Sequence[str],
    local_repo_root: Path | None = None,
    distribution: str = "",
) -> DependencyBuildPlan:
    """Create a build plan for a list of packages.

    Args:
        packages: Python package names to build.
        local_repo_root: Path to local APT repo.
        distribution: Ubuntu codename.

    Returns:
        DependencyBuildPlan with build order.
    """
    plan = DependencyBuildPlan()

    for py_name in packages:
        deb_name = normalize_python_package_name(py_name)

        result = DependencyCheckResult(name=deb_name)

        # Check availability
        archive_avail, archive_ver = check_archive_availability(deb_name, distribution)
        result.available_in_archive = archive_avail
        result.archive_version = archive_ver

        if local_repo_root:
            local_avail, local_ver = check_local_repo_availability(deb_name, local_repo_root)
            result.available_in_local = local_avail
            result.local_version = local_ver

        if archive_avail or result.available_in_local:
            plan.already_available.append(py_name)
        else:
            plan.to_build.append(py_name)

        plan.check_results[deb_name] = result

    # Order by dependencies (placeholder - just use input order for now)
    plan.to_build = compute_topological_order(plan.to_build, {})

    return plan
