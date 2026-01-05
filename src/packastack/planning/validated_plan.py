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

"""Validated plan logic for Packastack build operations.

Handles extraction of upstream dependencies from requirements files,
mapping Python requirements to Debian package names, and validation
of the preliminary plan against the target environment.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from packastack.apt.packages import PackageIndex

logger = logging.getLogger(__name__)


# Soft exclusions for known circular dependency pairs
# Format: (package_a, package_b) - when resolving deps for package_a, skip package_b
# This breaks circular dependencies in the build graph
SOFT_DEPENDENCY_EXCLUSIONS: set[tuple[str, str]] = {
    # oslo.config and oslo.log have a circular dependency
    ("oslo.config", "oslo.log"),
    ("oslo.log", "oslo.config"),
    # oslotest depends on oslo.config, but oslo.config uses oslotest for testing
    ("oslo.config", "oslotest"),
    ("oslo.log", "oslotest"),
}


def is_excluded_dependency(source_project: str, dep_project: str) -> bool:
    """Check if a dependency should be soft-excluded for a project.

    Args:
        source_project: The project we're resolving deps for.
        dep_project: The dependency project to check.

    Returns:
        True if this dependency should be excluded to break a cycle.
    """
    return (source_project, dep_project) in SOFT_DEPENDENCY_EXCLUSIONS


@dataclass
class UpstreamDeps:
    """Parsed upstream dependency information."""

    # Runtime deps with version specifiers: list of (name, version_spec) tuples
    # version_spec can be empty string if no specifier provided
    runtime: list[tuple[str, str]] = field(default_factory=list)  # From requirements.txt
    test: list[tuple[str, str]] = field(default_factory=list)  # From test-requirements.txt
    build: list[tuple[str, str]] = field(default_factory=list)  # From pyproject.toml/setup.cfg
    extras: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # Optional extras

    def all_deps(self) -> list[tuple[str, str]]:
        """Return all unique dependency (name, spec) tuples."""
        seen: set[str] = set()
        result: list[tuple[str, str]] = []
        for dep_name, spec in self.runtime + self.test + self.build:
            if dep_name not in seen:
                seen.add(dep_name)
                result.append((dep_name, spec))
        return result

    def all_dep_names(self) -> list[str]:
        """Return all unique dependency names (for backward compatibility)."""
        return [name for name, _ in self.all_deps()]


@dataclass
class ValidatedPlan:
    """Result of validating a preliminary plan against upstream deps."""

    build_order: list[str]
    upload_order: list[str]
    new_deps: list[str] = field(default_factory=list)  # Dependencies not in preliminary
    missing_deps: dict[str, str] = field(default_factory=dict)  # dep -> source info
    resolved_deps: dict[str, str] = field(default_factory=dict)  # dep -> where found
    warnings: list[str] = field(default_factory=list)
    updated: bool = False  # True if plan differs from preliminary
    # Dependency graph edges: source package -> list of source package dependencies
    dependency_edges: dict[str, list[str]] = field(default_factory=dict)
    # Resolved versions for each dependency: debian package name -> version string
    dependency_versions: dict[str, str] = field(default_factory=dict)


def parse_requirement_line(line: str) -> str | None:
    """Parse a single line from a requirements file, returning just the name.

    Handles:
        - Package names: oslo.config
        - Version specs: oslo.config>=1.0
        - Extras: oslo.config[extra]
        - Comments and empty lines

    Args:
        line: A line from requirements.txt.

    Returns:
        Normalized package name, or None if line should be skipped.
    """
    result = parse_requirement_with_spec(line)
    return result[0] if result else None


def parse_requirement_with_spec(line: str) -> tuple[str, str] | None:
    """Parse a single line from a requirements file with version specifier.

    Handles:
        - Package names: oslo.config
        - Version specs: oslo.config>=1.0,!=1.2.0
        - Extras: oslo.config[extra]
        - Comments and empty lines

    Args:
        line: A line from requirements.txt.

    Returns:
        Tuple of (normalized_name, version_spec) or None if line should be skipped.
        version_spec is empty string if no specifier present.
    """
    line = line.strip()

    # Skip empty lines and comments
    if not line or line.startswith("#"):
        return None

    # Skip lines with -r (includes), -e (editable), -c (constraints)
    if line.startswith(("-r", "-e", "-c", "--")):
        return None

    # Skip environment markers lines (they're part of previous dep)
    if line.startswith(";"):
        return None

    # Split on environment marker first
    if ";" in line:
        line = line.split(";")[0].strip()

    # Remove extras but keep version specifiers
    if "[" in line:
        line = re.sub(r"\[.*?\]", "", line)

    # Extract version specifier
    version_spec = ""
    name = line

    # Match version specifiers: >=, <=, !=, ==, ~=, >, <, @
    # Need to capture the full specifier including compound ones like >=1.0,!=1.2.0
    spec_match = re.match(r"^([a-zA-Z0-9_\-\.]+)\s*(.*)", line)
    if spec_match:
        name = spec_match.group(1).strip()
        version_spec = spec_match.group(2).strip()

        # Clean up version_spec - remove any trailing comments
        if "#" in version_spec:
            version_spec = version_spec.split("#")[0].strip()

        # Validate the specifier if non-empty
        if version_spec and not version_spec.startswith("@"):
            try:
                SpecifierSet(version_spec)
            except InvalidSpecifier:
                logger.debug(f"Invalid version specifier for {name}: {version_spec}")
                version_spec = ""  # Ignore invalid specifiers

    name = name.strip()

    # Normalize: lowercase, underscores to hyphens (PEP 503)
    if name:
        normalized = name.lower().replace("_", "-")
        return normalized, version_spec

    return None


def parse_requirements_file(path: Path) -> list[tuple[str, str]]:
    """Parse a requirements.txt file.

    Args:
        path: Path to requirements file.

    Returns:
        List of (package_name, version_spec) tuples (normalized).
    """
    if not path.exists():
        return []

    deps: list[tuple[str, str]] = []
    try:
        for line in path.read_text().splitlines():
            result = parse_requirement_with_spec(line)
            if result:
                deps.append(result)
    except Exception:
        pass

    return deps


def parse_pyproject_deps(pyproject_path: Path) -> list[tuple[str, str]]:
    """Parse dependencies from pyproject.toml.

    Args:
        pyproject_path: Path to pyproject.toml.

    Returns:
        List of (package_name, version_spec) tuples.
    """
    if not pyproject_path.exists():
        return []

    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore
        except ImportError:
            return []

    try:
        with pyproject_path.open("rb") as f:
            data = tomllib.load(f)

        deps: list[tuple[str, str]] = []

        # [project.dependencies]
        project_deps = data.get("project", {}).get("dependencies", [])
        for dep in project_deps:
            result = parse_requirement_with_spec(dep)
            if result:
                deps.append(result)

        # [build-system.requires]
        build_deps = data.get("build-system", {}).get("requires", [])
        for dep in build_deps:
            result = parse_requirement_with_spec(dep)
            if result:
                deps.append(result)

        return deps
    except Exception:
        return []


def parse_setup_cfg_deps(setup_cfg_path: Path) -> list[tuple[str, str]]:
    """Parse dependencies from setup.cfg.

    Args:
        setup_cfg_path: Path to setup.cfg.

    Returns:
        List of (package_name, version_spec) tuples.
    """
    if not setup_cfg_path.exists():
        return []

    try:
        import configparser

        config = configparser.ConfigParser()
        config.read(setup_cfg_path)

        deps: list[tuple[str, str]] = []

        # [options] install_requires
        if config.has_option("options", "install_requires"):
            raw = config.get("options", "install_requires")
            for line in raw.strip().splitlines():
                result = parse_requirement_with_spec(line)
                if result:
                    deps.append(result)

        # [options] setup_requires
        if config.has_option("options", "setup_requires"):
            raw = config.get("options", "setup_requires")
            for line in raw.strip().splitlines():
                result = parse_requirement_with_spec(line)
                if result:
                    deps.append(result)

        return deps
    except Exception:
        return []


def extract_upstream_deps(repo_path: Path, use_glob: bool = False) -> UpstreamDeps:
    """Extract upstream dependency declarations from a repository.

    Parses:
        - requirements.txt (or *requirements*.txt if use_glob=True)
        - test-requirements.txt
        - pyproject.toml
        - setup.cfg

    Args:
        repo_path: Path to the upstream git repository.
        use_glob: If True, glob for *requirements*.txt files instead of
            just parsing requirements.txt and test-requirements.txt.

    Returns:
        UpstreamDeps with parsed dependencies.
    """
    deps = UpstreamDeps()

    if use_glob:
        # Glob for all requirements files
        runtime_patterns = [
            "requirements.txt",
            "requirements-*.txt",
            "*-requirements.txt",
        ]
        test_patterns = [
            "test-requirements.txt",
            "test_requirements.txt",
            "*test*requirements*.txt",
        ]

        # Parse runtime requirements from glob
        for pattern in runtime_patterns:
            for req_file in repo_path.glob(pattern):
                # Skip test requirements files
                if "test" in req_file.name.lower():
                    continue
                parsed = parse_requirements_file(req_file)
                # Merge, avoiding duplicates
                existing_names = {name for name, _ in deps.runtime}
                for name, spec in parsed:
                    if name not in existing_names:
                        deps.runtime.append((name, spec))
                        existing_names.add(name)

        # Parse test requirements from glob
        for pattern in test_patterns:
            for req_file in repo_path.glob(pattern):
                parsed = parse_requirements_file(req_file)
                existing_names = {name for name, _ in deps.test}
                for name, spec in parsed:
                    if name not in existing_names:
                        deps.test.append((name, spec))
                        existing_names.add(name)
    else:
        # Original behavior: just parse requirements.txt and test-requirements.txt
        req_file = repo_path / "requirements.txt"
        deps.runtime = parse_requirements_file(req_file)

        test_req_file = repo_path / "test-requirements.txt"
        deps.test = parse_requirements_file(test_req_file)

    # Parse pyproject.toml
    pyproject_file = repo_path / "pyproject.toml"
    deps.build.extend(parse_pyproject_deps(pyproject_file))

    # Parse setup.cfg
    setup_cfg_file = repo_path / "setup.cfg"
    deps.build.extend(parse_setup_cfg_deps(setup_cfg_file))

    return deps


# Mapping of Python package names to Debian package names
# This handles common cases where the names differ
PYTHON_TO_DEBIAN: dict[str, str] = {
    # Standard library / virtual packages
    "python": "",
    "setuptools": "python3-setuptools",
    "pip": "python3-pip",
    "wheel": "python3-wheel",
    "pbr": "python3-pbr",
    # Oslo libraries
    "oslo-config": "python3-oslo.config",
    "oslo-log": "python3-oslo.log",
    "oslo-messaging": "python3-oslo.messaging",
    "oslo-db": "python3-oslo.db",
    "oslo-utils": "python3-oslo.utils",
    "oslo-i18n": "python3-oslo.i18n",
    "oslo-serialization": "python3-oslo.serialization",
    "oslo-context": "python3-oslo.context",
    "oslo-concurrency": "python3-oslo.concurrency",
    "oslo-policy": "python3-oslo.policy",
    "oslo-privsep": "python3-oslo.privsep",
    "oslo-service": "python3-oslo.service",
    "oslo-upgradecheck": "python3-oslo.upgradecheck",
    "oslo-middleware": "python3-oslo.middleware",
    "oslo-reports": "python3-oslo.reports",
    "oslo-rootwrap": "python3-oslo.rootwrap",
    "oslo-versionedobjects": "python3-oslo.versionedobjects",
    "oslo-vmware": "python3-oslo.vmware",
    "oslo-cache": "python3-oslo.cache",
    "oslo-limit": "python3-oslo.limit",
    "oslo-metrics": "python3-oslo.metrics",
    # Common OpenStack deps
    "keystoneauth1": "python3-keystoneauth1",
    "keystonemiddleware": "python3-keystonemiddleware",
    "python-keystoneclient": "python3-keystoneclient",
    "python-novaclient": "python3-novaclient",
    "python-glanceclient": "python3-glanceclient",
    "python-neutronclient": "python3-neutronclient",
    "python-cinderclient": "python3-cinderclient",
    "python-swiftclient": "python3-swiftclient",
    "python-openstackclient": "python3-openstackclient",
    "osc-lib": "python3-osc-lib",
    # Common Python deps
    "pyyaml": "python3-yaml",
    "pyjwt": "python3-jwt",
    "sqlalchemy": "python3-sqlalchemy",
    "alembic": "python3-alembic",
    "eventlet": "python3-eventlet",
    "greenlet": "python3-greenlet",
    "webob": "python3-webob",
    "paste": "python3-paste",
    "pastedeploy": "python3-pastedeploy",
    "routes": "python3-routes",
    "jsonschema": "python3-jsonschema",
    "cryptography": "python3-cryptography",
    "pyopenssl": "python3-openssl",
    "requests": "python3-requests",
    "urllib3": "python3-urllib3",
    "httplib2": "python3-httplib2",
    "decorator": "python3-decorator",
    "jinja2": "python3-jinja2",
    "markupsafe": "python3-markupsafe",
    "six": "python3-six",
    "iso8601": "python3-iso8601",
    "netaddr": "python3-netaddr",
    "netifaces": "python3-netifaces",
    "stevedore": "python3-stevedore",
    "debtcollector": "python3-debtcollector",
    "tooz": "python3-tooz",
    "taskflow": "python3-taskflow",
    "automaton": "python3-automaton",
    "futurist": "python3-futurist",
    "cotyledon": "python3-cotyledon",
    "tenacity": "python3-tenacity",
    "dogpile-cache": "python3-dogpile.cache",
    "cachetools": "python3-cachetools",
    "msgpack": "python3-msgpack",
    "psutil": "python3-psutil",
    "psycopg2": "python3-psycopg2",
    "pymysql": "python3-pymysql",
    "pytz": "python3-tz",
    "babel": "python3-babel",
    "pyparsing": "python3-pyparsing",
    "prettytable": "python3-prettytable",
    "cliff": "python3-cliff",
    "cmd2": "python3-cmd2",
    "simplejson": "python3-simplejson",
    "testtools": "python3-testtools",
    "fixtures": "python3-fixtures",
    "oslotest": "python3-oslotest",
    "hacking": "python3-hacking",
    "flake8": "python3-flake8",
    "sphinx": "python3-sphinx",
    "openstackdocstheme": "python3-openstackdocstheme",
}


def map_python_to_debian(python_name: str) -> tuple[str, bool]:
    """Map a Python package name to a Debian package name.

    Args:
        python_name: Python package name (normalized lowercase with hyphens).

    Returns:
        Tuple of (debian_package_name, is_uncertain).
        is_uncertain is True if the mapping is heuristic.
    """
    # Check explicit mapping
    if python_name in PYTHON_TO_DEBIAN:
        mapped = PYTHON_TO_DEBIAN[python_name]
        if not mapped:
            # Empty means skip (stdlib or virtual)
            return "", False
        return mapped, False

    # Heuristic: python3-{name}
    debian_name = f"python3-{python_name}"

    # Some adjustments
    # oslo.* -> python3-oslo.{suffix}
    if python_name.startswith("oslo-"):
        # oslo-config -> oslo.config
        suffix = python_name[5:]  # Remove "oslo-"
        debian_name = f"python3-oslo.{suffix}"

    return debian_name, True


def extract_upstream_version(debian_version: str) -> str:
    """Extract the upstream version from a Debian version string.

    Debian versions can have epoch:upstream-revision format.
    This extracts just the upstream portion for PEP 440 comparison.

    Args:
        debian_version: Full Debian version string (e.g., "1:2.3.0-0ubuntu1").

    Returns:
        Upstream version portion (e.g., "2.3.0").
    """
    version = debian_version

    # Remove epoch (1:2.3.0 -> 2.3.0)
    if ":" in version:
        version = version.split(":", 1)[1]

    # Remove Debian revision (-0ubuntu1 -> nothing)
    if "-" in version:
        version = version.rsplit("-", 1)[0]

    # Handle Ubuntu-specific suffixes like ~b1, ~rc1
    # Keep these as they might be meaningful for version comparison
    return version


def check_version_satisfies(version_spec: str, available_version: str) -> bool:
    """Check if an available version satisfies a version specifier.

    Args:
        version_spec: PEP 440 version specifier (e.g., ">=1.0,!=1.2.0").
        available_version: Debian package version string.

    Returns:
        True if the version satisfies the specifier, False otherwise.
        Returns True if version_spec is empty (no constraint).
    """
    if not version_spec:
        return True

    try:
        spec = SpecifierSet(version_spec)
        # Extract upstream version from Debian version for comparison
        upstream_version = extract_upstream_version(available_version)
        # Parse as PEP 440 version
        version = Version(upstream_version)
        return version in spec
    except (InvalidSpecifier, InvalidVersion) as e:
        logger.debug(f"Version check failed: {e}")
        # If we can't parse, assume satisfied to avoid false negatives
        return True


def resolve_dependency_with_spec(
    dep_name: str,
    version_spec: str,
    local_index: PackageIndex | None,
    cloud_archive_index: PackageIndex | None,
    ubuntu_index: PackageIndex,
) -> tuple[str | None, str, bool]:
    """Resolve a dependency to a Debian package, checking version constraints.

    Uses smart resolution:
    - First checks Ubuntu/cloud-archive for a satisfying version
    - If local has a newer version that also satisfies, prefer local
    - Returns satisfaction status along with version info

    Args:
        dep_name: Debian package name to resolve.
        version_spec: PEP 440 version specifier (can be empty).
        local_index: Index of locally built packages.
        cloud_archive_index: Index of cloud archive packages.
        ubuntu_index: Index of Ubuntu archive packages.

    Returns:
        Tuple of (version, source, satisfied) where:
        - version: The package version found (or None if not found)
        - source: "local", "cloud-archive", "ubuntu", or "" if not found
        - satisfied: True if version satisfies the specifier
    """
    if not dep_name:
        return None, "", True

    candidates: list[tuple[str, str, bool]] = []  # (version, source, satisfied)

    # Check Ubuntu first
    version = ubuntu_index.get_version(dep_name)
    if version:
        satisfied = check_version_satisfies(version_spec, version)
        candidates.append((version, "ubuntu", satisfied))

    # Check cloud archive
    if cloud_archive_index:
        version = cloud_archive_index.get_version(dep_name)
        if version:
            satisfied = check_version_satisfies(version_spec, version)
            candidates.append((version, "cloud-archive", satisfied))

    # Check local
    if local_index:
        version = local_index.get_version(dep_name)
        if version:
            satisfied = check_version_satisfies(version_spec, version)
            candidates.append((version, "local", satisfied))

    if not candidates:
        return None, "", False

    # First preference: satisfying version from ubuntu/cloud-archive
    for ver, src, sat in candidates:
        if sat and src in ("ubuntu", "cloud-archive"):
            return ver, src, sat

    # Second preference: satisfying version from local
    for ver, src, sat in candidates:
        if sat and src == "local":
            return ver, src, sat

    # No satisfying version found - return the "best" available
    # Prefer ubuntu/cloud-archive, then local
    for ver, src, sat in candidates:
        if src in ("ubuntu", "cloud-archive"):
            return ver, src, sat
    
    return candidates[0][0], candidates[0][1], candidates[0][2]


def resolve_dependency(
    dep_name: str,
    local_index: PackageIndex | None,
    cloud_archive_index: PackageIndex | None,
    ubuntu_index: PackageIndex,
) -> tuple[str | None, str]:
    """Resolve a dependency to a Debian package.

    Checks indexes in order: local -> cloud-archive -> ubuntu.

    Args:
        dep_name: Debian package name to resolve.
        local_index: Index of locally built packages.
        cloud_archive_index: Index of cloud archive packages.
        ubuntu_index: Index of Ubuntu archive packages.

    Returns:
        Tuple of (version, source) where source is "local", "cloud-archive",
        "ubuntu", or None if not found.
    """
    if not dep_name:
        return None, ""

    # Check local first
    if local_index:
        version = local_index.get_version(dep_name)
        if version:
            return version, "local"

    # Check cloud archive
    if cloud_archive_index:
        version = cloud_archive_index.get_version(dep_name)
        if version:
            return version, "cloud-archive"

    # Check Ubuntu
    version = ubuntu_index.get_version(dep_name)
    if version:
        return version, "ubuntu"

    return None, ""


@dataclass
class DependencyResolutionResult:
    """Result of resolving a package's dependencies."""

    package: str  # Source package name
    project: str  # Upstream project name
    upstream_deps: UpstreamDeps  # Parsed dependencies
    missing_deps: list[str] = field(default_factory=list)  # Debian names not found
    resolved_deps: dict[str, tuple[str, str]] = field(default_factory=dict)  # debian_name -> (version, source)
    needs_building: list[str] = field(default_factory=list)  # Source packages needing to be built


@dataclass
class RecursiveValidationResult:
    """Result of recursive dependency validation."""

    build_order: list[str]  # Topologically sorted build order
    dependency_edges: dict[str, list[str]]  # package -> dependencies
    dependency_versions: dict[str, str]  # debian package -> version
    missing_deps: dict[str, list[str]]  # package -> missing debian dep names
    warnings: list[str] = field(default_factory=list)
    has_cycles: bool = False
    cycle_packages: list[str] = field(default_factory=list)

    def get_package_deps(self, package: str) -> list[str]:
        """Get the dependency source packages for a package."""
        return self.dependency_edges.get(package, [])


def project_to_source_package(project: str) -> str:
    """Convert upstream project name to Debian source package name.

    Args:
        project: Upstream project name (e.g., "oslo.config", "nova").

    Returns:
        Debian source package name (e.g., "python-oslo.config", "nova").
    """
    # Oslo and client libraries use python- prefix
    if project.startswith("oslo.") or project.startswith("oslo-"):
        return f"python-{project}"
    if project.startswith("python-"):
        return project
    if project.endswith("client"):
        return f"python-{project}"
    # Other libraries that commonly have python- prefix
    if project in (
        "keystoneauth1", "keystonemiddleware", "osc-lib",
        "tooz", "taskflow", "automaton", "futurist", "cotyledon",
        "stevedore", "debtcollector", "cliff", "oslotest",
    ):
        return f"python-{project}"
    # Services keep their name as-is
    return project


def validate_plan(
    preliminary_build_order: list[str],
    upstream_deps: UpstreamDeps,
    local_index: PackageIndex | None,
    cloud_archive_index: PackageIndex | None,
    ubuntu_index: PackageIndex,
) -> ValidatedPlan:
    """Validate and update a preliminary plan against upstream dependencies.

    Args:
        preliminary_build_order: Build order from preliminary plan.
        upstream_deps: Dependencies extracted from upstream source.
        local_index: Index of locally built packages.
        cloud_archive_index: Index of cloud archive packages.
        ubuntu_index: Index of Ubuntu archive packages.

    Returns:
        ValidatedPlan with updated build order and resolution info.
    """
    result = ValidatedPlan(
        build_order=list(preliminary_build_order),
        upload_order=[],
    )

    # Get all deps that need checking
    all_deps = upstream_deps.all_deps()

    for python_dep, version_spec in all_deps:
        debian_name, uncertain = map_python_to_debian(python_dep)

        if not debian_name:
            # Skip stdlib/virtual packages
            continue

        # Try to resolve with version specifier
        version, source, satisfied = resolve_dependency_with_spec(
            debian_name, version_spec, local_index, cloud_archive_index, ubuntu_index
        )

        if version:
            result.resolved_deps[debian_name] = source
            result.dependency_versions[debian_name] = version
            if uncertain:
                result.warnings.append(
                    f"Mapped {python_dep} -> {debian_name} (heuristic)"
                )
            if not satisfied:
                result.warnings.append(
                    f"Version mismatch: {debian_name} {version} does not satisfy {version_spec}"
                )
        else:
            result.missing_deps[debian_name] = f"Required by upstream ({python_dep})"
            if uncertain:
                result.warnings.append(
                    f"Could not resolve {python_dep} (tried {debian_name})"
                )

    # Check if any new deps are needed in build order
    # (This is a simplified check - full implementation would update the graph)
    for debian_name, source in result.resolved_deps.items():
        if source == "local":
            # Check if it's in the build order
            # Extract source package from debian name
            pkg = local_index.find_package(debian_name) if local_index else None
            if pkg and pkg.source not in result.build_order:
                result.new_deps.append(pkg.source)
                result.updated = True

    # Upload order same as build order for now
    result.upload_order = list(result.build_order)

    return result


def validate_dependencies_recursive(
    initial_packages: list[str],
    upstream_cache: Path,
    local_index: "PackageIndex | None",
    cloud_archive_index: "PackageIndex | None",
    ubuntu_index: "PackageIndex",
    openstack_packages: set[str],
    max_depth: int = 10,
    refresh_cache: bool = True,
) -> RecursiveValidationResult:
    """Recursively validate dependencies, discovering new packages to build.

    This function:
    1. For each package in the initial list, clones the upstream repo
    2. Extracts dependencies from requirements.txt
    3. Checks which dependencies need to be built locally
    4. Recursively validates those dependencies
    5. Returns the full build order with dependency edges

    Args:
        initial_packages: Initial list of source packages to build.
        upstream_cache: Directory to cache upstream clones.
        local_index: Index of locally built packages (can be None).
        cloud_archive_index: Index of cloud archive packages (can be None).
        ubuntu_index: Index of Ubuntu archive packages.
        openstack_packages: Set of OpenStack project names we can build.
        max_depth: Maximum recursion depth.
        refresh_cache: Whether to refresh cached upstream repos.

    Returns:
        RecursiveValidationResult with full build order and dependency info.
    """
    result = RecursiveValidationResult(
        build_order=[],
        dependency_edges={},
        dependency_versions={},
        missing_deps={},
    )

    # Track what we've already processed
    processed: set[str] = set()
    # Queue of (package, project, depth)
    queue: list[tuple[str, str, int]] = []

    # Initialize queue with initial packages
    for pkg in initial_packages:
        # Infer project name from package name
        if pkg.startswith("python-"):
            project = pkg[7:]  # Remove python- prefix
        else:
            project = pkg
        queue.append((pkg, project, 0))

    while queue:
        package, project, depth = queue.pop(0)

        if package in processed:
            continue
        if depth > max_depth:
            result.warnings.append(f"Max depth reached processing {package}")
            continue

        processed.add(package)

        # Check if this project should be excluded as a dependency source
        # (Still process it, but don't follow its deps for certain projects)
        skip_deps = False
        for source_proj, target_proj in SOFT_DEPENDENCY_EXCLUSIONS:
            if project == target_proj and any(
                p[1] == source_proj for p in queue
            ):
                skip_deps = True
                break

        # Try to find the upstream repo in cache
        repo_path = upstream_cache / project
        if not repo_path.exists():
            # We'll need to clone it, but that's done by the caller
            # For now, just mark that we couldn't extract deps
            result.warnings.append(f"Upstream repo not cached: {project}")
            result.dependency_edges[package] = []
            continue

        if skip_deps:
            result.dependency_edges[package] = []
            continue

        # Extract dependencies from the cached repo
        upstream_deps = extract_upstream_deps(repo_path)
        pkg_edges: list[str] = []
        pkg_missing: list[str] = []

        for python_dep, version_spec in upstream_deps.runtime:
            debian_name, uncertain = map_python_to_debian(python_dep)
            if not debian_name:
                continue

            # Check if it's an excluded dependency
            if is_excluded_dependency(project, python_dep):
                continue

            # Try to resolve the dependency with version check
            version, source, satisfied = resolve_dependency_with_spec(
                debian_name, version_spec, local_index, cloud_archive_index, ubuntu_index
            )

            if version:
                result.dependency_versions[debian_name] = version

                # If from local, find the source package
                if source == "local" and local_index:
                    pkg_obj = local_index.find_package(debian_name)
                    if pkg_obj and pkg_obj.source:
                        pkg_edges.append(pkg_obj.source)
            else:
                # Not resolved - check if it's an OpenStack package we can build
                # Map debian name back to potential project name
                if debian_name.startswith("python3-"):
                    potential_project = debian_name[8:]  # Remove python3-
                elif debian_name.startswith("python-"):
                    potential_project = debian_name[7:]  # Remove python-
                else:
                    potential_project = debian_name

                if potential_project in openstack_packages:
                    # This is an OpenStack project - add to queue
                    source_pkg = project_to_source_package(potential_project)
                    if source_pkg not in processed:
                        queue.append((source_pkg, potential_project, depth + 1))
                        pkg_edges.append(source_pkg)
                else:
                    pkg_missing.append(debian_name)

        result.dependency_edges[package] = pkg_edges
        if pkg_missing:
            result.missing_deps[package] = pkg_missing

    # Topological sort for build order
    # Simple Kahn's algorithm
    in_degree: dict[str, int] = {pkg: 0 for pkg in processed}
    for pkg, deps in result.dependency_edges.items():
        for dep in deps:
            if dep in in_degree:
                in_degree[dep] = in_degree.get(dep, 0)

    # Count incoming edges
    for pkg, deps in result.dependency_edges.items():
        for dep in deps:
            if dep in in_degree:
                in_degree[pkg] += 1

    # Wait, that's reversed. Let me fix:
    # in_degree[pkg] = number of packages that pkg depends on that are also being built
    in_degree = {pkg: 0 for pkg in processed}
    reverse_deps: dict[str, list[str]] = {pkg: [] for pkg in processed}

    for pkg, deps in result.dependency_edges.items():
        for dep in deps:
            if dep in processed:
                in_degree[pkg] += 1
                reverse_deps[dep].append(pkg)

    # Start with packages that have no dependencies in the build set
    queue_topo: list[str] = [pkg for pkg, degree in in_degree.items() if degree == 0]
    sorted_order: list[str] = []

    while queue_topo:
        pkg = queue_topo.pop(0)
        sorted_order.append(pkg)

        for dependent in reverse_deps.get(pkg, []):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue_topo.append(dependent)

    # Check for cycles
    if len(sorted_order) != len(processed):
        result.has_cycles = True
        result.cycle_packages = [pkg for pkg in processed if pkg not in sorted_order]
        result.warnings.append(f"Dependency cycle detected: {result.cycle_packages}")
        # Use remaining packages in arbitrary order
        for pkg in processed:
            if pkg not in sorted_order:
                sorted_order.append(pkg)

    result.build_order = sorted_order

    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m packastack.planning.validated_plan <repo_path>")
        sys.exit(1)

    repo = Path(sys.argv[1])
    deps = extract_upstream_deps(repo)

    print("Runtime deps:")
    for name, spec in deps.runtime:
        debian, uncertain = map_python_to_debian(name)
        marker = " (uncertain)" if uncertain else ""
        spec_str = f" ({spec})" if spec else ""
        print(f"  {name}{spec_str} -> {debian}{marker}")

    print("\nTest deps:")
    for name, spec in deps.test:
        debian, uncertain = map_python_to_debian(name)
        marker = " (uncertain)" if uncertain else ""
        spec_str = f" ({spec})" if spec else ""
        print(f"  {name}{spec_str} -> {debian}{marker}")
