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

"""Upstreams registry loader and resolver for PackaStack.

This module implements the upstreams registry system that defines:
- Where upstream source lives
- How releases are discovered
- How tarballs are obtained
- How verification is performed

The registry uses a defaults-based model where most OpenStack projects
follow standard OpenDev patterns, and only deviating projects need
explicit entries.
"""

from __future__ import annotations

import importlib.resources
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


# Registry schema version
REGISTRY_VERSION = 2

# OpenDev base URLs for URL derivation
OPENDEV_GIT_BASE = "https://opendev.org/openstack"
OPENSTACK_TARBALLS_BASE = "https://tarballs.opendev.org/openstack"


class ResolutionSource(Enum):
    """How a project's upstream configuration was resolved."""

    REGISTRY_EXPLICIT = "registry_explicit"
    REGISTRY_DEFAULTS = "registry_defaults"
    LEGACY_OPENSTACK_RELEASES = "legacy_openstack_releases"


class ReleaseSourceType(Enum):
    """Type of release discovery mechanism."""

    OPENSTACK_RELEASES = "openstack_releases"
    GIT_TAGS = "git_tags"
    PYPI = "pypi"
    PINNED = "pinned"


class TarballMethod(Enum):
    """Method for obtaining upstream tarballs."""

    OFFICIAL = "official"
    GITHUB_RELEASE = "github_release"
    PYPI = "pypi"
    GIT_ARCHIVE = "git_archive"


class SignatureMode(Enum):
    """Verification policy for upstream sources."""

    AUTO = "auto"
    REQUIRED_DETACHED = "required_detached"
    GIT_TAG = "git_tag"
    GIT_COMMIT = "git_commit"
    NONE = "none"


class WatchMode(Enum):
    """Expected debian/watch file mode."""

    PYPI = "pypi"
    GITHUB_RELEASE = "github_release"
    GIT_TAGS = "git_tags"
    OPENSTACK_TARBALL = "openstack_tarball"
    CUSTOM = "custom"


@dataclass
class UpstreamConfig:
    """Upstream source configuration."""

    type: str = "git"
    host: str = "opendev"
    url: str = ""
    default_branch: str = "master"


@dataclass
class Provenance:
    canonical: str
    aliases: list[str]
    inferred: bool


@dataclass
class ReleaseSourceConfig:
    """Release discovery configuration."""

    type: ReleaseSourceType = ReleaseSourceType.OPENSTACK_RELEASES
    deliverable: str = ""
    tag_regex: str = ""
    project: str = ""  # PyPI project name
    ref: str = ""  # For pinned
    strict: bool = True


@dataclass
class TarballConfig:
    """Tarball acquisition configuration."""

    prefer: list[TarballMethod] = field(
        default_factory=lambda: [TarballMethod.OFFICIAL]
    )


@dataclass
class SignaturesConfig:
    """Signature verification configuration."""

    mode: SignatureMode = SignatureMode.AUTO


@dataclass
class RequirementsConfig:
    """Requirements extraction configuration."""

    files: list[str] = field(
        default_factory=lambda: ["pyproject.toml", "requirements.txt", "setup.cfg"]
    )
    optional_files: list[str] = field(default_factory=list)
    include_optional_extras: bool = False


@dataclass
class WatchExpectConfig:
    """Expected debian/watch configuration."""

    mode: WatchMode = WatchMode.OPENSTACK_TARBALL
    base_url: str = ""


@dataclass
class WatchConfig:
    """Watch file expectation configuration."""

    expect: WatchExpectConfig = field(default_factory=WatchExpectConfig)


@dataclass
class UbuntuHints:
    """Ubuntu packaging hints (advisory only)."""

    source_hint: str = ""
    binaries_hint: list[str] = field(default_factory=list)


@dataclass
class ProjectConfig:
    """Complete configuration for a project."""

    project_key: str
    common_names: list[str] = field(default_factory=list)
    retired: bool = False
    ubuntu: UbuntuHints = field(default_factory=UbuntuHints)
    upstream: UpstreamConfig = field(default_factory=UpstreamConfig)
    release_source: ReleaseSourceConfig = field(default_factory=ReleaseSourceConfig)
    tarball: TarballConfig = field(default_factory=TarballConfig)
    signatures: SignaturesConfig = field(default_factory=SignaturesConfig)
    requirements: RequirementsConfig = field(default_factory=RequirementsConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    provenance: Provenance = field(
        default_factory=lambda: Provenance(canonical="", aliases=[], inferred=False)
    )


@dataclass
class ResolvedUpstream:
    """Result of resolving a project's upstream configuration."""

    project: str
    config: ProjectConfig
    resolution_source: ResolutionSource
    overrides_applied: list[str] = field(default_factory=list)


@dataclass
class RegistryLoadResult:
    """Result of loading the registry."""

    version: int
    defaults: dict[str, Any]
    projects: dict[str, dict[str, Any]]
    override_applied: bool = False
    override_path: str = ""
    warnings: list[str] = field(default_factory=list)


class RegistryError(Exception):
    """Error loading or validating the registry."""

    pass


class RegistryVersionMismatchError(RegistryError):
    """Registry version mismatch between base and override."""

    pass


class ProjectNotFoundError(RegistryError):
    """Project not found in registry and not OpenStack-governed."""

    pass


def get_canonical_registry_path() -> Path:
    """Get path to the canonical in-tree registry.

    Returns:
        Path to the upstreams.yaml file in the package data directory.
    """
    # Use importlib.resources to find the data file
    try:
        files = importlib.resources.files("packastack.data")
        return Path(files.joinpath("upstreams.yaml"))
    except (TypeError, FileNotFoundError, AttributeError, ModuleNotFoundError):
        # Fallback for development
        return Path(__file__).parent.parent / "data" / "upstreams.yaml"


def get_override_registry_path() -> Path:
    """Get path to the user's override registry.

    Returns:
        Path to the user's override upstreams.yaml file.
    """
    return Path.home() / ".config" / "packastack" / "upstreams.yaml"


def _parse_upstream_config(data: dict[str, Any]) -> UpstreamConfig:
    """Parse upstream configuration from dict."""
    return UpstreamConfig(
        type=data.get("type", "git"),
        host=data.get("host", "opendev"),
        url=data.get("url", ""),
        default_branch=data.get("default_branch", "master"),
    )


def _parse_release_source_config(data: dict[str, Any]) -> ReleaseSourceConfig:
    """Parse release source configuration from dict."""
    type_str = data.get("type", "openstack_releases")
    try:
        release_type = ReleaseSourceType(type_str)
    except ValueError:
        release_type = ReleaseSourceType.OPENSTACK_RELEASES

    return ReleaseSourceConfig(
        type=release_type,
        deliverable=data.get("deliverable", ""),
        tag_regex=data.get("tag_regex", ""),
        project=data.get("project", ""),
        ref=data.get("ref", ""),
        strict=data.get("strict", True),
    )


def _parse_tarball_config(data: dict[str, Any]) -> TarballConfig:
    """Parse tarball configuration from dict."""
    prefer_list = data.get("prefer", ["official"])
    methods = []
    for method in prefer_list:
        try:
            methods.append(TarballMethod(method))
        except ValueError:
            pass  # Skip unknown methods
    if not methods:
        methods = [TarballMethod.OFFICIAL]
    return TarballConfig(prefer=methods)


def _parse_signatures_config(data: dict[str, Any]) -> SignaturesConfig:
    """Parse signatures configuration from dict."""
    mode_str = data.get("mode", "auto")
    try:
        mode = SignatureMode(mode_str)
    except ValueError:
        mode = SignatureMode.AUTO
    return SignaturesConfig(mode=mode)


def _parse_requirements_config(data: dict[str, Any]) -> RequirementsConfig:
    """Parse requirements configuration from dict."""
    return RequirementsConfig(
        files=data.get("files", ["pyproject.toml", "requirements.txt", "setup.cfg"]),
        optional_files=data.get("optional_files", []),
        include_optional_extras=data.get("include_optional_extras", False),
    )


def _parse_watch_config(data: dict[str, Any]) -> WatchConfig:
    """Parse watch configuration from dict."""
    expect_data = data.get("expect", {})
    mode_str = expect_data.get("mode", "openstack_tarball")
    try:
        mode = WatchMode(mode_str)
    except ValueError:
        mode = WatchMode.OPENSTACK_TARBALL

    return WatchConfig(
        expect=WatchExpectConfig(
            mode=mode,
            base_url=expect_data.get("base_url", ""),
        )
    )


def _parse_ubuntu_hints(data: dict[str, Any]) -> UbuntuHints:
    """Parse ubuntu hints from dict."""
    return UbuntuHints(
        source_hint=data.get("source_hint", ""),
        binaries_hint=data.get("binaries_hint", []),
    )


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge two dicts with override taking precedence.

    - Scalar fields: override replaces base
    - Mapping fields: override replaces keys present in override
    - List fields: override replaces the list entirely
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def load_registry(
    canonical_path: Path | None = None,
    override_path: Path | None = None,
) -> RegistryLoadResult:
    """Load the upstreams registry with optional override.

    Args:
        canonical_path: Path to canonical registry (defaults to in-tree).
        override_path: Path to override registry (defaults to user config).

    Returns:
        RegistryLoadResult with merged configuration.

    Raises:
        RegistryError: If registry cannot be loaded or validated.
        RegistryVersionMismatchError: If versions don't match.
    """
    if canonical_path is None:
        canonical_path = get_canonical_registry_path()
    if override_path is None:
        override_path = get_override_registry_path()

    warnings: list[str] = []

    # Load canonical registry
    if not canonical_path.exists():
        raise RegistryError(f"Canonical registry not found: {canonical_path}")

    try:
        with open(canonical_path) as f:
            base_data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise RegistryError(f"Failed to parse canonical registry: {e}") from e

    version = base_data.get("version")
    if version is None:
        raise RegistryError("Registry missing required 'version' field")
    if version != REGISTRY_VERSION:
        raise RegistryError(
            f"Unsupported registry version {version}, expected {REGISTRY_VERSION}"
        )

    defaults = base_data.get("defaults", {})
    projects = base_data.get("projects", {})

    # Load and merge override if present
    override_applied = False
    override_path_str = ""

    if override_path.exists():
        try:
            with open(override_path) as f:
                override_data = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            warnings.append(f"Failed to parse override registry: {e}")
            override_data = {}

        if override_data:
            override_version = override_data.get("version")
            if override_version is not None and override_version != version:
                raise RegistryVersionMismatchError(
                    f"Override registry version {override_version} does not match "
                    f"canonical version {version}"
                )

            # Merge defaults (shallow merge)
            if "defaults" in override_data:
                defaults = _merge_dicts(defaults, override_data["defaults"])

            # Merge projects
            if "projects" in override_data:
                for proj_key, proj_data in override_data["projects"].items():
                    if proj_key in projects:
                        projects[proj_key] = _merge_dicts(projects[proj_key], proj_data)
                    else:
                        projects[proj_key] = proj_data

            override_applied = True
            override_path_str = str(override_path)

    return RegistryLoadResult(
        version=version,
        defaults=defaults,
        projects=projects,
        override_applied=override_applied,
        override_path=override_path_str,
        warnings=warnings,
    )


def _apply_defaults_to_project(
    project_key: str,
    project_data: dict[str, Any],
    defaults: dict[str, Any],
) -> ProjectConfig:
    """Apply defaults to a project configuration.

    Args:
        project_key: The project's canonical key.
        project_data: Project-specific configuration.
        defaults: Default configuration to apply.

    Returns:
        Complete ProjectConfig with defaults applied.
    """
    # Merge defaults with project data
    merged = _merge_dicts(defaults, project_data)

    # Parse common_names and aliases (aliases mirror common_names today)
    common_names = merged.get("common_names", [project_key]) or [project_key]
    aliases = merged.get("aliases", common_names)

    # Parse upstream config
    upstream_data = merged.get("upstream", {})
    upstream = _parse_upstream_config(upstream_data)

    # Canonical upstream ID
    canonical_upstream = merged.get("canonical", "")
    inferred_canonical = False
    if not canonical_upstream:
        canonical_upstream = f"openstack/{project_key}"
        inferred_canonical = True

    # Derive URL if not explicitly set and host is opendev
    if not upstream.url and upstream.host == "opendev":
        upstream.url = f"{OPENDEV_GIT_BASE}/{project_key}.git"

    # Parse release source config
    release_data = merged.get("release_source", {})
    release_source = _parse_release_source_config(release_data)

    # Default deliverable to project key if openstack_releases
    if (
        release_source.type == ReleaseSourceType.OPENSTACK_RELEASES
        and not release_source.deliverable
    ):
        release_source.deliverable = project_key

    # Parse other configs
    tarball = _parse_tarball_config(merged.get("tarball", {}))
    signatures = _parse_signatures_config(merged.get("signatures", {}))
    requirements = _parse_requirements_config(merged.get("requirements", {}))
    watch = _parse_watch_config(merged.get("watch", {}))
    ubuntu = _parse_ubuntu_hints(merged.get("ubuntu", {}))
    # Retired flag
    retired = bool(merged.get("retired", False))

    provenance = Provenance(
        canonical=canonical_upstream,
        aliases=aliases,
        inferred=inferred_canonical,
    )

    return ProjectConfig(
        project_key=project_key,
        common_names=common_names,
        retired=retired,
        ubuntu=ubuntu,
        upstream=upstream,
        release_source=release_source,
        tarball=tarball,
        signatures=signatures,
        requirements=requirements,
        watch=watch,
        provenance=provenance,
    )


class UpstreamsRegistry:
    """Registry for upstream project configurations.

    Provides resolution of project configurations with defaults and overrides.
    """

    def __init__(
        self,
        canonical_path: Path | None = None,
        override_path: Path | None = None,
    ):
        """Initialize the registry.

        Args:
            canonical_path: Path to canonical registry.
            override_path: Path to override registry.
        """
        self._load_result = load_registry(canonical_path, override_path)
        self._resolved_cache: dict[str, ResolvedUpstream] = {}

    @property
    def version(self) -> int:
        """Registry version."""
        return self._load_result.version

    @property
    def override_applied(self) -> bool:
        """Whether an override was applied."""
        return self._load_result.override_applied

    @property
    def override_path(self) -> str:
        """Path to override file if applied."""
        return self._load_result.override_path

    @property
    def warnings(self) -> list[str]:
        """Warnings from loading."""
        return self._load_result.warnings

    def has_explicit_entry(self, project: str) -> bool:
        """Check if a project has an explicit registry entry.

        Args:
            project: Project name or common name.

        Returns:
            True if the project is explicitly listed in the registry.
        """
        # Direct key match
        if project in self._load_result.projects:
            return True

        # Check common_names
        for proj_key, proj_data in self._load_result.projects.items():
            common_names = proj_data.get("common_names", [])
            if project in common_names:
                return True

        return False

    def find_projects(self, query: str, allow_prefix: bool = False) -> list[str]:
        """Find explicit projects matching a query or prefix.

        Args:
            query: Project name/common name to match.
            allow_prefix: If True, include prefix matches in addition to exact.

        Returns:
            Sorted list of canonical project keys that match.
        """
        matches: set[str] = set()
        for proj_key, proj_data in self._load_result.projects.items():
            names = [proj_key]
            names.extend(proj_data.get("common_names", []))
            for name in names:
                if name == query or (allow_prefix and name.startswith(query)):
                    matches.add(proj_key)
                    break
        return sorted(matches)

    def _find_project_key(self, project: str) -> str | None:
        """Find the canonical project key for a name.

        Args:
            project: Project name or common name.

        Returns:
            Canonical project key if found, None otherwise.
        """
        # Direct key match
        if project in self._load_result.projects:
            return project

        # Check common_names
        for proj_key, proj_data in self._load_result.projects.items():
            common_names = proj_data.get("common_names", [])
            if project in common_names:
                return proj_key

        return None

    def resolve(
        self,
        project: str,
        openstack_governed: bool = False,
    ) -> ResolvedUpstream:
        """Resolve upstream configuration for a project.

        Args:
            project: Project name (canonical or common name).
            openstack_governed: Whether the project is in openstack/releases.

        Returns:
            ResolvedUpstream with complete configuration.

        Raises:
            ProjectNotFoundError: If project not in registry and not governed.
        """
        # Check cache
        cache_key = f"{project}:{openstack_governed}"
        if cache_key in self._resolved_cache:
            return self._resolved_cache[cache_key]

        # Find explicit entry
        project_key = self._find_project_key(project)

        if project_key is not None:
            # Explicit registry entry
            project_data = self._load_result.projects[project_key]
            config = _apply_defaults_to_project(
                project_key, project_data, self._load_result.defaults
            )
            result = ResolvedUpstream(
                project=project_key,
                config=config,
                resolution_source=ResolutionSource.REGISTRY_EXPLICIT,
            )
        elif openstack_governed:
            # Use defaults for OpenStack-governed project
            config = _apply_defaults_to_project(
                project, {}, self._load_result.defaults
            )
            result = ResolvedUpstream(
                project=project,
                config=config,
                resolution_source=ResolutionSource.REGISTRY_DEFAULTS,
            )
        else:
            # Not in registry and not governed - fail
            raise ProjectNotFoundError(
                f"Project '{project}' is not in the upstreams registry and is not "
                "governed by openstack/releases. Add an explicit entry to the "
                "registry or verify the project name."
            )

        self._resolved_cache[cache_key] = result
        return result

    def get_all_explicit_projects(self) -> list[str]:
        """Get all explicitly listed project keys.

        Returns:
            List of project keys in the registry.
        """
        return list(self._load_result.projects.keys())

    def is_retired(self, project: str) -> bool:
        """Check if a project is marked as retired in the registry.

        Args:
            project: Project name or common name.

        Returns:
            True if the project is explicitly marked as retired.
        """
        project_key = self._find_project_key(project)
        if project_key is None:
            return False

        project_data = self._load_result.projects.get(project_key, {})
        return project_data.get("retired", False)

    def get_retired_projects(self) -> list[str]:
        """Get all projects marked as retired in the registry.

        Returns:
            List of project keys that are retired.
        """
        retired = []
        for proj_key, proj_data in self._load_result.projects.items():
            if proj_data.get("retired", False):
                retired.append(proj_key)
        return retired

    def list_projects(self) -> list[str]:
        """List all project keys in the registry.

        Returns:
            Sorted list of all project keys.
        """
        return sorted(self._load_result.projects.keys())


def derive_git_url(project: str, host: str = "opendev") -> str:
    """Derive git URL for a project based on host.

    Args:
        project: Project name.
        host: Host type (opendev, github, gitlab).

    Returns:
        Git clone URL.
    """
    if host == "opendev":
        return f"{OPENDEV_GIT_BASE}/{project}.git"
    # For other hosts, URL must be explicit in registry
    return ""


def derive_tarball_url(project: str, version: str) -> str:
    """Derive official OpenStack tarball URL.

    Args:
        project: Project name.
        version: Version string.

    Returns:
        Tarball URL.
    """
    return f"{OPENSTACK_TARBALLS_BASE}/{project}/{project}-{version}.tar.gz"


def derive_signature_url(tarball_url: str) -> str:
    """Derive signature URL from tarball URL.

    Args:
        tarball_url: URL of the tarball.

    Returns:
        URL of the detached signature.
    """
    return f"{tarball_url}.asc"
