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

"""Ubuntu archive Packages.gz parsing utilities using python-debian."""

from __future__ import annotations

import contextlib
import gzip
import warnings
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

# Suppress python3-apt warning - it's optional and not installable via pip
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message=".*python.*-apt.*")
    warnings.filterwarnings("ignore", message=".*apt_pkg.*")
    from debian.deb822 import Packages
    from debian.debian_support import Version

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass
class BinaryPackage:
    """Represents a binary package from the Ubuntu archive."""

    name: str
    version: str
    architecture: str
    source: str = ""
    depends: list[str] = field(default_factory=list)
    pre_depends: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    component: str = ""  # main, universe, etc.
    pocket: str = ""  # release, updates, security


def compare_versions(v1: str, v2: str) -> int:
    """Compare Debian package versions using python-debian.

    Returns:
        -1 if v1 < v2
         0 if v1 == v2
         1 if v1 > v2
    """
    # Skip comparison if either version contains Debian substitution variables
    # (e.g., ${binary:Version}). These can't be compared meaningfully.
    if "${" in v1 or "${" in v2:
        return 0  # Treat as equal when substitution variables are present

    ver1 = Version(v1)
    ver2 = Version(v2)
    if ver1 < ver2:
        return -1
    elif ver1 > ver2:
        return 1
    return 0


def version_satisfies(available: str, relation: str, required: str) -> bool:
    """Check if an available version satisfies a version constraint.

    Args:
        available: Version string of the available package.
        relation: Debian version relation (>=, <=, =, >>, <<).
        required: Required version string.

    Returns:
        True if the available version satisfies the constraint.
    """
    if not relation or not required:
        return True

    # If the required version contains Debian substitution variables
    # (e.g., ${binary:Version}), we can't evaluate it meaningfully.
    # Treat as satisfied since the actual version is unknown.
    if "${" in required:
        return True

    cmp = compare_versions(available, required)

    relation_checks = {
        ">=": cmp >= 0,
        "<=": cmp <= 0,
        "=": cmp == 0,
        ">>": cmp > 0,
        "<<": cmp < 0,
    }
    return relation_checks.get(relation, True)


def iter_packages(packages_gz_path: Path) -> Iterator[BinaryPackage]:
    """Iterate over binary packages from a Packages.gz file."""
    with gzip.open(packages_gz_path, "rt", encoding="utf-8") as f:
        for pkg in Packages.iter_paragraphs(f, use_apt_pkg=False):
            name = pkg.get("Package", "")
            if not name:
                continue

            # Parse source: defaults to package name, may have version suffix
            source = pkg.get("Source", name)
            # Remove version suffix if present: "foo (1.0)" -> "foo"
            if "(" in source:
                source = source.split("(")[0].strip()

            # Parse depends and pre-depends as raw strings
            depends_raw = pkg.get("Depends", "")
            pre_depends_raw = pkg.get("Pre-Depends", "")
            provides_raw = pkg.get("Provides", "")

            # Split by comma, strip whitespace
            depends = [d.strip() for d in depends_raw.split(",") if d.strip()]
            pre_depends = [d.strip() for d in pre_depends_raw.split(",") if d.strip()]
            provides = [p.strip() for p in provides_raw.split(",") if p.strip()]

            yield BinaryPackage(
                name=name,
                version=pkg.get("Version", ""),
                architecture=pkg.get("Architecture", ""),
                source=source,
                depends=depends,
                pre_depends=pre_depends,
                provides=provides,
            )


@dataclass
class PackageIndex:
    """In-memory index of binary packages from Ubuntu archive cache."""

    packages: dict[str, BinaryPackage] = field(default_factory=dict)
    sources: dict[str, list[str]] = field(default_factory=dict)  # source -> binary names
    provides: dict[str, list[str]] = field(default_factory=dict)  # virtual -> real names

    def add_package(self, pkg: BinaryPackage, component: str, pocket: str) -> None:
        """Add a package to the index."""
        pkg.component = component
        pkg.pocket = pocket

        # Only keep highest version of each package
        existing = self.packages.get(pkg.name)
        if existing and compare_versions(pkg.version, existing.version) <= 0:
            return

        self.packages[pkg.name] = pkg

        # Index by source
        if pkg.source not in self.sources:
            self.sources[pkg.source] = []
        if pkg.name not in self.sources[pkg.source]:
            self.sources[pkg.source].append(pkg.name)

        # Index provides
        for virtual in pkg.provides:
            # Handle versioned provides: "foo (= 1.0)" -> "foo"
            vname = virtual.split("(")[0].strip()
            if vname not in self.provides:
                self.provides[vname] = []
            if pkg.name not in self.provides[vname]:
                self.provides[vname].append(pkg.name)

    def find_package(self, name: str) -> BinaryPackage | None:
        """Find a package by name, checking real packages and provides."""
        if name in self.packages:
            return self.packages[name]
        # Check if it's a virtual package
        if self.provides.get(name):
            return self.packages.get(self.provides[name][0])
        return None

    def get_component(self, name: str) -> str | None:
        """Get the component (main, universe, etc.) for a package."""
        pkg = self.find_package(name)
        return pkg.component if pkg else None

    def get_version(self, name: str) -> str | None:
        """Get the version of a package."""
        pkg = self.find_package(name)
        return pkg.version if pkg else None

    def get_binaries_for_source(self, source: str) -> list[str]:
        """Get all binary package names produced by a source package."""
        return self.sources.get(source, [])


def load_package_index(
    cache_root: Path,
    series: str,
    pockets: Sequence[str],
    components: Sequence[str],
) -> PackageIndex:
    """Load package index from Ubuntu archive cache.

    Args:
        cache_root: Path to ubuntu-archive cache (e.g., ~/.cache/packastack/ubuntu-archive)
        series: Ubuntu series codename (e.g., "noble")
        pockets: List of pockets to load (e.g., ["release", "updates", "security"])
        components: List of components (e.g., ["main", "universe"])

    Returns:
        PackageIndex with all packages loaded.
    """
    index = PackageIndex()
    indexes_dir = cache_root / "indexes"

    for pocket in pockets:
        for component in components:
            # Find all architecture directories
            component_dir = indexes_dir / series / pocket / component
            if not component_dir.exists():
                continue

            for arch_dir in component_dir.iterdir():
                if not arch_dir.is_dir() or not arch_dir.name.startswith("binary-"):
                    continue

                packages_gz = arch_dir / "Packages.gz"
                if not packages_gz.exists():
                    continue

                for pkg in iter_packages(packages_gz):
                    index.add_package(pkg, component, pocket)

    return index


def load_cloud_archive_index(
    cache_root: Path,
    ubuntu_series: str,
    pocket: str,
    components: Sequence[str] | None = None,
) -> PackageIndex:
    """Load package index from Ubuntu Cloud Archive cache.

    Args:
        cache_root: Path to cache root (e.g., ~/.cache/packastack)
        ubuntu_series: Ubuntu series codename (e.g., "jammy", "noble")
        pocket: OpenStack pocket (e.g., "caracal", "caracal-proposed")
        components: List of components (default: ["main"])

    Returns:
        PackageIndex with all packages from the cloud archive.
    """
    if components is None:
        components = ["main"]

    index = PackageIndex()

    # Cloud archive cache structure:
    # cloud-archive/{ubuntu_series}/{pocket}/{component}/binary-{arch}/Packages.gz
    ca_cache_dir = cache_root / "cloud-archive" / "indexes" / ubuntu_series

    for component in components:
        pocket_dir = ca_cache_dir / pocket / component
        if not pocket_dir.exists():
            continue

        for arch_dir in pocket_dir.iterdir():
            if not arch_dir.is_dir() or not arch_dir.name.startswith("binary-"):
                continue

            packages_gz = arch_dir / "Packages.gz"
            if not packages_gz.exists():
                continue

            for pkg in iter_packages(packages_gz):
                # Mark as from cloud-archive
                pkg.pocket = f"cloud-archive:{pocket}"
                index.add_package(pkg, component, f"cloud-archive:{pocket}")

    return index


def load_local_repo_index(repo_root: Path, arch: str = "amd64") -> PackageIndex:
    """Load package index from a local APT repository.

    Args:
        repo_root: Root directory of the local APT repository.
        arch: Architecture to load (default: amd64).

    Returns:
        PackageIndex with packages from the local repo.
    """
    index = PackageIndex()

    # Local repos use dists/local/main/binary-{arch}/Packages.gz
    packages_gz = repo_root / "dists" / "local" / "main" / f"binary-{arch}" / "Packages.gz"

    if packages_gz.exists():
        for pkg in iter_packages(packages_gz):
            pkg.pocket = "local"
            index.add_package(pkg, "main", "local")

    return index


def merge_package_indexes(*indexes: PackageIndex) -> PackageIndex:
    """Merge multiple PackageIndex instances.

    Later indexes take precedence for packages with the same name,
    but only if the version is higher.

    Args:
        *indexes: PackageIndex instances to merge.

    Returns:
        Merged PackageIndex.
    """
    merged = PackageIndex()

    for index in indexes:
        for pkg in index.packages.values():
            merged.add_package(pkg, pkg.component, pkg.pocket)

    return merged


def apply_ubuntu_source_fallbacks(
    ubuntu_index: PackageIndex,
    resolved_targets: list,
    run: object | None = None,
) -> None:
    """Apply deterministic Ubuntu source-name fallbacks to resolved targets.

    For any resolved target whose `source_package` is not present in
    `ubuntu_index.sources`, try a sequence of candidate source names and
    substitute the first match. The candidate order is:

    1. base name with any leading python3-/python- prefix stripped
    2. `python3-<base>`
    3. `python-<base>`
    4. the canonical upstream project name from the resolved target

    The function mutates `resolved_targets` in-place and will add a
    "+ub-fallback" marker to the target's `resolution_source` when a
    substitution occurs. If `run` is provided it will be used to emit
    structured events using `run.log_event(...)`.
    """
    # If ubuntu_index is None or has no sources, perform a limited
    # normalization pass: convert python- / python3- prefixed source
    # package names to an upstream_project (deliverable) by stripping
    # the prefix. This helps policy checks that expect the OpenStack
    # project name rather than a distribution source name.
    if not resolved_targets:
        return

    has_index = bool(ubuntu_index and getattr(ubuntu_index, "sources", None))

    if not has_index:
        # Limited normalization only
        for t in resolved_targets:
            try:
                src = t.source_package
            except Exception:
                continue

            if isinstance(src, str) and (src.startswith("python3-") or src.startswith("python-")):
                base = src.removeprefix("python3-").removeprefix("python-")
                upl = getattr(t, "upstream_project", None)
                # If upstream_project is missing or obviously a python-prefixed name,
                # normalize it to the base deliverable name.
                if not upl or (isinstance(upl, str) and (upl.startswith("python3-") or upl.startswith("python-") or upl == src)):
                    with contextlib.suppress(Exception):
                        t.upstream_project = base
                    if run is not None and hasattr(run, "log_event"):
                        run.log_event({
                            "event": "plan.ub_fallback_upstream_normalized",
                            "original": src,
                            "normalized": base,
                        })
        return

    for t in resolved_targets:
        try:
            original = t.source_package
        except Exception:
            continue

        if original in ubuntu_index.sources:
            continue

        # Derive base name by stripping common python prefixes
        base = original
        if base.startswith("python3-"):
            base = base.removeprefix("python3-")
        elif base.startswith("python-"):
            base = base.removeprefix("python-")

        candidates = [base, f"python3-{base}", f"python-{base}"]
        # Finally, try the canonical upstream project name if present
        upstream = getattr(t, "upstream_project", None)
        if upstream and isinstance(upstream, str):
            # Normalize 'openstack/<project>' to just the project name
            if "/" in upstream:
                upstream = upstream.split("/")[-1]
            candidates.append(upstream)

        tried = []
        chosen = None
        for c in candidates:
            tried.append(c)
            if c in ubuntu_index.sources:
                chosen = c
                break

        if chosen:
            t.source_package = chosen
            # Mark resolution source to indicate the substitution
            with contextlib.suppress(Exception):
                t.resolution_source = f"{getattr(t, 'resolution_source', '')}+ub-fallback"
            # If upstream_project appears to be a python-prefixed name, normalize
            # it to the deliverable base so downstream policy checks use the
            # canonical upstream project identifier.
            try:
                upl = getattr(t, "upstream_project", None)
                if isinstance(upl, str) and (upl.startswith("python3-") or upl.startswith("python-")):
                    t.upstream_project = upl.removeprefix("python3-").removeprefix("python-")
            except Exception:
                pass
            if run is not None and hasattr(run, "log_event"):
                run.log_event({
                    "event": "plan.ub_fallback_applied",
                    "original": original,
                    "chosen": chosen,
                    "tried": tried,
                })
            # Emit a human-friendly activity if available
            try:
                from packastack.core.run import activity

                activity("plan", f"Adjusted target {original} -> {chosen} using Ubuntu fallback")
            except Exception:
                pass
        else:
            if run is not None and hasattr(run, "log_event"):
                run.log_event({
                    "event": "plan.ub_fallback_not_found",
                    "original": original,
                    "tried": tried,
                })


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        for pkg in iter_packages(path):
            print(f"{pkg.name} {pkg.version} [{pkg.source}]")
