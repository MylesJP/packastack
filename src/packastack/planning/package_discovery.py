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

"""Package discovery for build-all mode.

Discovers all ubuntu-openstack-dev packaging repositories from Launchpad API
or from local cache for offline operation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING

from packastack.upstream.registry import RegistryError, UpstreamsRegistry

if TYPE_CHECKING:
    from collections.abc import Sequence

try:
    from launchpadlib.launchpad import Launchpad
except ImportError:
    Launchpad = None  # type: ignore[assignment, misc]

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


# Launchpad API endpoints
LAUNCHPAD_API_BASE = "https://api.launchpad.net/devel/"
UBUNTU_OPENSTACK_DEV_TEAM = "~ubuntu-openstack-dev"

# Patterns for filtering out non-package repositories
EXCLUDED_REPO_PATTERNS = [
    r"^\..*",  # Hidden directories
    r"^_.*",  # Internal/meta directories
    r".*-charm$",  # Juju charms (not Debian packages)
    r".*-operator$",  # Kubernetes operators
    r"^charm-.*",  # Charm prefix pattern
    r"^meta-.*",  # Meta repositories
    r"^tools$",  # Tooling repos
    r"^scripts$",  # Script repos
]

# Known repositories that are not source packages
EXCLUDED_REPOS = frozenset({
    "packaging-guide",
    "upload-queue",
    "ubuntu-openstack-tools",
    "openstack-mojo-specs",
    "openstack-charms",
    "release-tools",
    "bot-control",
    "ubuntu",  # Meta repo
})


@dataclass
class DiscoveryResult:
    """Result of package discovery."""

    packages: list[str]
    """List of source package names discovered."""

    total_repos: int = 0
    """Total number of repositories found before filtering."""

    filtered_repos: dict[str, str] = field(default_factory=dict)
    """Repos that were filtered out, with reason."""

    errors: list[str] = field(default_factory=list)
    """Errors encountered during discovery."""

    source: str = "unknown"
    """Source of discovery: 'launchpad', 'cache', 'file', 'explicit'."""

    missing_upstream: list[str] = field(default_factory=list)
    """Packages with no entry in openstack/releases AND not in upstream registry."""

    missing_packaging: list[str] = field(default_factory=list)
    """Libraries/services in openstack/releases without a packaging repo."""


def _is_excluded_repo(name: str) -> tuple[bool, str]:
    """Check if a repository name should be excluded.

    Args:
        name: Repository/directory name.

    Returns:
        Tuple of (excluded, reason).
    """
    if name in EXCLUDED_REPOS:
        return True, "known non-package repo"

    for pattern in EXCLUDED_REPO_PATTERNS:
        if re.match(pattern, name):
            return True, f"matches exclusion pattern: {pattern}"

    return False, ""


def _is_valid_packaging_repo(path: Path) -> bool:
    """Check if a directory is a valid Debian packaging repository.

    A valid repo must have a debian/control file.

    Args:
        path: Path to check.

    Returns:
        True if this looks like a packaging repo.
    """
    return (path / "debian" / "control").exists()


def discover_packages_from_launchpad(
    timeout: int = 30,
    releases_repo: Path | None = None,
    cache_file: Path | None = None,
) -> DiscoveryResult:
    """Discover packages from Launchpad by enumerating ubuntu-openstack-dev repos.

    Queries Launchpad for all git repositories owned by the ubuntu-openstack-dev
    team, extracts package names from paths matching '+source/{pkg}', then
    cross-references with openstack/releases and upstream registry.

    Args:
        timeout: Request timeout in seconds (unused with launchpadlib).
        releases_repo: Path to openstack/releases repo for cross-referencing.
        cache_file: Optional path to cache discovered repos (JSON file).

    Returns:
        DiscoveryResult with discovered packages and cross-reference warnings.
    """
    result = DiscoveryResult(packages=[], source="launchpad")

    # Try to load from cache if it exists and is recent
    if cache_file and cache_file.exists():
        try:
            cache_data = json.loads(cache_file.read_text())
            cached_packages = cache_data.get("packages", [])
            if cached_packages:
                result.packages = cached_packages
                result.total_repos = cache_data.get("total_repos", len(cached_packages))
                result.filtered_repos = cache_data.get("filtered_repos", {})
                # Still need to do cross-referencing
                _cross_reference_packages(result, releases_repo)
                return result
        except (json.JSONDecodeError, KeyError, OSError):
            pass  # Cache invalid, proceed with API query

    if Launchpad is None:
        result.errors.append("launchpadlib library not available")
        return result

    try:
        # Anonymous login for read-only access
        lp = Launchpad.login_anonymously(
            'packastack',
            'production',
            version='devel',
        )
    except Exception as e:
        result.errors.append(f"Launchpad login failed: {e}")
        return result

    team_name = 'ubuntu-openstack-dev'
    try:
        team = lp.people[team_name]
    except Exception as e:
        result.errors.append(f"Failed to get team {team_name}: {e}")
        return result

    # Enumerate all git repositories owned by the team
    discovered_packages: list[str] = []
    try:
        # getRepositories with target= returns all repos owned by the team
        repos = lp.git_repositories.getRepositories(target=team)
        result.total_repos = 0

        from datetime import datetime, timedelta
        two_years_ago = datetime.now(UTC) - timedelta(days=730)  # 2 years

        for repo in repos:
            result.total_repos += 1
            # Extract package name from repo path
            # Expected format: ~ubuntu-openstack-dev/ubuntu/+source/{package}
            repo_name = getattr(repo, 'name', '')
            repo_path = getattr(repo, 'git_https_url', '') or ''

            # Parse the path to get package name
            pkg_name = _extract_package_from_repo(repo_name, repo_path)
            if not pkg_name:
                result.filtered_repos[repo_name or repo_path] = "not a +source package repo"
                continue

            # Check if repo hasn't been modified in 2 years (consider retired)
            date_last_modified = getattr(repo, 'date_last_modified', None)
            if date_last_modified and date_last_modified < two_years_ago:
                result.filtered_repos[pkg_name] = f"not modified since {date_last_modified.strftime('%Y-%m-%d')} (>2 years, likely retired)"
                continue

            # Check exclusion patterns
            excluded, reason = _is_excluded_repo(pkg_name)
            if excluded:
                result.filtered_repos[pkg_name] = reason
                continue

            discovered_packages.append(pkg_name)

    except Exception as e:
        result.errors.append(f"Failed to enumerate repositories: {e}")
        return result

    result.packages = sorted(set(discovered_packages))

    # Cache the discovered repos for future use
    if cache_file:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_data = {
                "packages": result.packages,
                "total_repos": result.total_repos,
                "filtered_repos": result.filtered_repos,
            }
            cache_file.write_text(json.dumps(cache_data, indent=2))
        except OSError:
            pass  # Caching is best-effort

    # Cross-reference with openstack/releases and upstream registry
    _cross_reference_packages(result, releases_repo)

    return result


def _extract_package_from_repo(repo_name: str, repo_path: str) -> str | None:
    """Extract package name from a Launchpad repository.

    Only returns package names for repos matching the +source/{package} pattern.

    Args:
        repo_name: Repository name attribute.
        repo_path: Repository URL or path.

    Returns:
        Package name or None if not a source package repo.
    """
    # Try to parse from git URL: https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source/nova
    if "+source/" in repo_path:
        match = re.search(r"\+source/([^/]+)(?:\.git)?$", repo_path)
        if match:
            return match.group(1)

    return None


def _get_upstreams_registry() -> UpstreamsRegistry | None:
    """Load upstream registry for cross-reference checks."""
    try:
        return UpstreamsRegistry()
    except RegistryError:
        return None


def _cross_reference_packages(
    result: DiscoveryResult,
    releases_repo: Path | None,
) -> None:
    """Cross-reference discovered packages with releases and upstream registry.

    Updates result.missing_upstream and result.missing_packaging in place.

    Args:
        result: DiscoveryResult to update.
        releases_repo: Path to openstack/releases repo.
    """
    if not releases_repo or not releases_repo.exists():
        return

    registry = _get_upstreams_registry()

    # Load releases libraries and services
    releases_libs_services = get_releases_libraries_and_services(releases_repo)

    discovered_set = set(result.packages)

    # Find packages without upstream definition
    # A package is "missing upstream" if it's not in releases AND not in upstream registry
    # For libraries, the source package has python- prefix (e.g., python-oslo.log -> oslo.log)
    releases_all = _get_all_releases_packages(releases_repo)
    for pkg in result.packages:
        # Check both the package name and without python- prefix
        pkg_base = pkg.removeprefix("python-") if pkg.startswith("python-") else pkg
        in_releases = pkg in releases_all or pkg_base in releases_all
        in_upstreams = False
        if registry:
            in_upstreams = registry.has_explicit_entry(pkg) or registry.has_explicit_entry(pkg_base)
        if not in_releases and not in_upstreams:
            result.missing_upstream.append(pkg)

    # Find releases libs/services without packaging repo
    for deliverable in releases_libs_services:
        # Check both the deliverable name and python- prefixed version
        has_packaging = (
            deliverable in discovered_set or
            f"python-{deliverable}" in discovered_set
        )
        if not has_packaging:
            result.missing_packaging.append(deliverable)

    result.missing_upstream = sorted(result.missing_upstream)
    result.missing_packaging = sorted(result.missing_packaging)


def _get_packages_from_releases_repo(releases_repo: Path) -> list[str]:
    """Extract package names from openstack/releases deliverables.

    Scans the deliverables directory for YAML files and extracts
    package names.

    Args:
        releases_repo: Path to openstack/releases repository.

    Returns:
        List of package names.
    """
    packages: set[str] = set()
    deliverables_dir = releases_repo / "deliverables"

    if not deliverables_dir.exists():
        return []

    # Scan all series directories
    for series_dir in deliverables_dir.iterdir():
        if not series_dir.is_dir():
            continue
        for yaml_file in series_dir.glob("*.yaml"):
            # The filename (without .yaml) is typically the deliverable name
            pkg_name = yaml_file.stem
            packages.add(pkg_name)

    return sorted(packages)


def _get_known_openstack_packages() -> list[str]:
    """Return a list of known OpenStack packages.

    This is a fallback when the releases repo isn't available.
    """
    return [
        # Core services
        "nova", "glance", "cinder", "neutron", "keystone", "swift",
        "heat", "horizon", "barbican", "designate", "ironic", "magnum",
        "manila", "mistral", "murano", "octavia", "sahara", "senlin",
        "trove", "zaqar", "placement", "aodh", "ceilometer", "gnocchi",
        # Oslo libraries
        "oslo.config", "oslo.messaging", "oslo.db", "oslo.log",
        "oslo.policy", "oslo.utils", "oslo.i18n", "oslo.context",
        "oslo.serialization", "oslo.concurrency", "oslo.middleware",
        "oslo.service", "oslo.versionedobjects", "oslo.privsep",
        "oslo.rootwrap", "oslo.cache", "oslo.reports", "oslo.upgradecheck",
        # Clients
        "python-novaclient", "python-glanceclient", "python-cinderclient",
        "python-neutronclient", "python-keystoneclient", "python-swiftclient",
        "python-heatclient", "python-openstackclient", "python-barbicanclient",
        "python-designateclient", "python-ironicclient", "python-magnumclient",
        "python-manilaclient", "python-mistralclient", "python-muranoclient",
        "python-octaviaclient", "python-saharaclient", "python-senlinclient",
        "python-troveclient", "python-zaqarclient", "python-aodhclient",
        "python-ceilometerclient", "python-gnocchiclient",
        # Other common packages
        "osc-lib", "keystoneauth1", "keystonemiddleware",
        "python-openstacksdk", "tempest", "stevedore", "taskflow",
        "tooz", "cotyledon", "futurist", "automaton", "cursive",
    ]


def get_releases_libraries_and_services(releases_repo: Path) -> set[str]:
    """Get deliverable names for libraries and services from openstack/releases.

    Scans all series directories in deliverables/ and returns names of
    deliverables with type 'library' or 'service'.

    Args:
        releases_repo: Path to openstack/releases repository.

    Returns:
        Set of deliverable names (not source package names).
    """
    if yaml is None:
        return set()

    deliverables_dir = releases_repo / "deliverables"
    if not deliverables_dir.exists():
        return set()

    libs_services: set[str] = set()

    # Scan the most recent series (highest numbered or alphabetically last)
    series_dirs = sorted(
        [d for d in deliverables_dir.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))],
        reverse=True,
    )

    if not series_dirs:
        return set()

    # Use the most recent series
    latest_series = series_dirs[0]

    for yaml_file in latest_series.glob("*.yaml"):
        try:
            with yaml_file.open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if not data or not isinstance(data, dict):
                    continue

                proj_type = data.get("type", "")
                if proj_type in ("library", "service"):
                    libs_services.add(yaml_file.stem)

        except (OSError, yaml.YAMLError):
            continue

    return libs_services


def _get_all_releases_packages(releases_repo: Path) -> set[str]:
    """Get all package names from openstack/releases.

    Returns both deliverable names and their python- prefixed versions
    for libraries.

    Args:
        releases_repo: Path to openstack/releases repository.

    Returns:
        Set of source package names that can be matched against discovered packages.
    """
    if yaml is None:
        return set()

    deliverables_dir = releases_repo / "deliverables"
    if not deliverables_dir.exists():
        return set()

    packages: set[str] = set()

    # Scan the most recent series
    series_dirs = sorted(
        [d for d in deliverables_dir.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))],
        reverse=True,
    )

    if not series_dirs:
        return set()

    latest_series = series_dirs[0]

    for yaml_file in latest_series.glob("*.yaml"):
        deliverable = yaml_file.stem
        packages.add(deliverable)

        try:
            with yaml_file.open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if data and isinstance(data, dict):
                    proj_type = data.get("type", "")
                    # Libraries are typically packaged as python-{name}
                    if proj_type == "library":
                        packages.add(f"python-{deliverable}")

        except (OSError, yaml.YAMLError):
            continue

    return packages


def discover_packages_from_cache(
    cache_dir: Path,
    require_control: bool = True,
) -> DiscoveryResult:
    """Discover packages from local git clone cache.

    Enumerates directories in the cache that look like packaging repositories.

    Args:
        cache_dir: Path to the packaging repos cache directory.
        require_control: If True, only include repos with debian/control.

    Returns:
        DiscoveryResult with discovered packages.
    """
    result = DiscoveryResult(packages=[], source="cache")

    if not cache_dir.exists():
        result.errors.append(f"Cache directory does not exist: {cache_dir}")
        return result

    if not cache_dir.is_dir():
        result.errors.append(f"Cache path is not a directory: {cache_dir}")
        return result

    all_entries = sorted(cache_dir.iterdir())
    result.total_repos = sum(1 for e in all_entries if e.is_dir())

    for entry in all_entries:
        if not entry.is_dir():
            continue

        name = entry.name

        # Check exclusion patterns
        excluded, reason = _is_excluded_repo(name)
        if excluded:
            result.filtered_repos[name] = reason
            continue

        # Check for debian/control if required
        if require_control and not _is_valid_packaging_repo(entry):
            result.filtered_repos[name] = "missing debian/control"
            continue

        result.packages.append(name)

    return result


def discover_packages_from_list(
    packages: Sequence[str],
    cache_dir: Path | None = None,
    validate: bool = True,
) -> DiscoveryResult:
    """Create discovery result from an explicit package list.

    Args:
        packages: List of package names.
        cache_dir: Optional cache directory to validate against.
        validate: If True and cache_dir provided, validate packages exist.

    Returns:
        DiscoveryResult with the provided packages.
    """
    result = DiscoveryResult(
        packages=[],
        total_repos=len(packages),
        source="explicit",
    )

    for pkg in packages:
        excluded, reason = _is_excluded_repo(pkg)
        if excluded:
            result.filtered_repos[pkg] = reason
            continue

        if validate and cache_dir:
            pkg_path = cache_dir / pkg
            if not pkg_path.exists():
                result.filtered_repos[pkg] = "not found in cache"
                continue
            if not _is_valid_packaging_repo(pkg_path):
                result.filtered_repos[pkg] = "missing debian/control"
                continue

        result.packages.append(pkg)

    return result


def read_packages_from_file(file_path: Path) -> list[str]:
    """Read package names from a file (one per line).

    Empty lines and lines starting with # are ignored.

    Args:
        file_path: Path to the file.

    Returns:
        List of package names.
    """
    if not file_path.exists():
        return []

    packages = []
    for line in file_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            packages.append(line)

    return packages


def discover_packages(
    cache_dir: Path | None = None,
    packages_file: Path | None = None,
    explicit_packages: Sequence[str] | None = None,
    offline: bool = False,
    releases_repo: Path | None = None,
    launchpad_cache_file: Path | None = None,
) -> DiscoveryResult:
    """Discover packages using the best available method.

    Priority:
    1. Explicit package list if provided
    2. Packages file if provided
    3. Launchpad API (unless offline)
    4. Local cache (fallback or offline mode)

    Args:
        cache_dir: Path to local packaging repos cache.
        packages_file: Optional path to a file with package names.
        explicit_packages: Optional explicit list of packages.
        offline: If True, only use local cache.
        releases_repo: Path to openstack/releases repo for package names.
        launchpad_cache_file: Optional path to cache Launchpad discovery results.

    Returns:
        DiscoveryResult with discovered packages.
    """
    # Explicit list takes priority
    if explicit_packages:
        return discover_packages_from_list(
            packages=explicit_packages,
            cache_dir=cache_dir,
            validate=cache_dir is not None,
        )

    # Packages file next
    if packages_file:
        packages = read_packages_from_file(packages_file)
        result = discover_packages_from_list(
            packages=packages,
            cache_dir=cache_dir,
            validate=cache_dir is not None,
        )
        result.source = "file"
        return result

    # Online: try Launchpad API
    if not offline:
        result = discover_packages_from_launchpad(
            releases_repo=releases_repo,
            cache_file=launchpad_cache_file,
        )
        if result.packages or not result.errors:
            return result
        # Fall through to cache on error

    # Offline or API failed: use cache
    if cache_dir:
        return discover_packages_from_cache(cache_dir)

    # No discovery method available
    return DiscoveryResult(
        packages=[],
        errors=["No discovery method available: offline mode with no cache"],
        source="none",
    )


def filter_by_managed_packages(
    packages: list[str],
    managed_packages: list[str] | None,
) -> tuple[list[str], list[str]]:
    """Filter packages to include only those in the managed packages list.

    If managed_packages is empty or None, returns all packages unchanged.

    Args:
        packages: List of discovered package names.
        managed_packages: List of packages the team manages (from config).

    Returns:
        Tuple of (filtered_packages, skipped_packages).
        filtered_packages: Packages that are in the managed list.
        skipped_packages: Packages that were filtered out.
    """
    if not managed_packages:
        return packages, []

    managed_set = set(managed_packages)
    filtered = []
    skipped = []

    for pkg in packages:
        if pkg in managed_set:
            filtered.append(pkg)
        else:
            skipped.append(pkg)

    return filtered, skipped
