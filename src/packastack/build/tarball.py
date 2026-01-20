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

"""Tarball acquisition utilities for the build command.

This module provides functions for acquiring upstream tarballs via various
methods: uscan, official URLs, PyPI, GitHub releases, and git archive.

The main entry point is `fetch_release_tarball()` which implements a
uscan-first strategy with fallback to other methods based on registry
preferences.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from packastack.core.run import activity
from packastack.debpkg.gbp import run_command
from packastack.upstream.source import (
    download_and_verify_tarball,
    download_file,
    generate_snapshot_tarball,
)
from packastack.upstream.tarball_cache import (
    TarballCacheEntry,
    cache_tarball,
    find_cached_tarball,
)

if TYPE_CHECKING:
    from packastack.build.provenance import BuildProvenance
    from packastack.planning.type_selection import BuildType
    from packastack.upstream.source import UpstreamSource


def run_uscan(repo_path: Path, version: str | None = None) -> tuple[bool, Path | None, str]:
    """Run uscan to fetch the upstream tarball.

    Uses debian/watch file to download and optionally verify the upstream
    tarball. This is the preferred method when a watch file is available.

    Args:
        repo_path: Path to the repository containing debian/watch
        version: Ignored, kept for compatibility

    Returns:
        Tuple of (success, tarball_path, error_message).
        On success: (True, path_to_tarball, "")
        On failure: (False, None, error_description)
    """
    uscan_cmd = [
        "uscan",
        "--download",
        "--rename",
    ]

    try:
        exit_code, stdout, stderr = run_command(uscan_cmd, cwd=repo_path)
        if exit_code != 0:
            output = stdout + stderr
            return False, None, output

        # Find the newest tarball produced by uscan in repo root
        candidates = sorted(
            (p for p in repo_path.glob("*.tar.*") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return True, candidates[0], ""
        return False, None, "uscan completed but no tarball found"
    except FileNotFoundError:
        return False, None, "uscan not installed"
    except Exception as e:
        return False, None, str(e)


def download_pypi_tarball(project: str, version: str, dest_dir: Path) -> tuple[bool, Path | None, str]:
    """Download PyPI sdist tarball using the simple URL pattern.

    Constructs the PyPI URL from project name and version, then downloads
    the .tar.gz sdist package.

    Args:
        project: PyPI project name (may contain "/" which is replaced with "-")
        version: Version string to download
        dest_dir: Directory to save the downloaded tarball

    Returns:
        Tuple of (success, tarball_path, error_message).
    """
    proj = project.replace("/", "-")
    proj_lower = proj.lower()
    first = proj_lower[0]
    url = f"https://files.pythonhosted.org/packages/source/{first}/{proj_lower}/{proj}-{version}.tar.gz"
    filename = url.split("/")[-1]
    dest = dest_dir / filename
    ok, err = download_file(url, dest)
    return ok, dest if ok else None, err


def download_github_release_tarball(
    upstream_url: str, version: str, dest_dir: Path
) -> tuple[bool, Path | None, str]:
    """Download GitHub release/tag archive from upstream git URL.

    Constructs the GitHub archive URL from the upstream repository URL
    and version tag, then downloads the tarball.

    Args:
        upstream_url: Git URL for the repository (may end in .git)
        version: Tag/version to download
        dest_dir: Directory to save the downloaded tarball

    Returns:
        Tuple of (success, tarball_path, error_message).
    """
    url = upstream_url
    if url.endswith(".git"):
        url = url[:-4]
    tar_url = f"{url}/archive/refs/tags/{version}.tar.gz"
    filename = tar_url.split("/")[-1]
    dest = dest_dir / filename
    ok, err = download_file(tar_url, dest)
    return ok, dest if ok else None, err


def fetch_release_tarball(
    upstream: UpstreamSource | None,
    upstream_config,
    pkg_repo: Path,
    workspace: Path,
    provenance: BuildProvenance,
    offline: bool,
    project_key: str,
    package_name: str,
    build_type: BuildType,
    cache_base: Path,
    force: bool,
    run,
) -> tuple[Path | None, bool, str]:
    """Fetch release tarball with uscan-first strategy.

    Implements the tarball acquisition strategy with fallback:
    1. uscan in packaging repo (uses debian/watch)
    2. Official tarball URL (OpenDev/releases.openstack.org)
    3. Fallback methods from registry tarball preferences:
       - pypi: Download from PyPI
       - github_release: Download from GitHub releases
       - git_archive: Clone and create archive

    Updates provenance with the acquisition method and any verification results.

    Args:
        upstream: Resolved upstream source (version, tarball_url, etc.)
        upstream_config: Registry configuration for the package
        pkg_repo: Path to the packaging repository
        workspace: Working directory for downloads
        provenance: BuildProvenance to update with acquisition details
        offline: If True, only use cached tarballs
        project_key: Project identifier for caching
        package_name: Debian package name
        build_type: Build type for cache organization
        cache_base: Base directory for tarball cache
        force: Currently unused, reserved for future use
        run: RunContext for logging (currently unused, uses activity())

    Returns:
        Tuple of (tarball_path, signature_verified, signature_warning).
        On failure: (None, False, error_description)

    Side Effects:
        - Updates provenance.tarball.* fields
        - Updates provenance.verification.* fields
        - Caches successfully acquired tarballs
        - Logs activity for each acquisition attempt
    """

    def record(
        method: str,
        path: Path | None,
        url: str = "",
        sig_verified: bool = False,
        sig_warning: str = "",
    ):
        """Update provenance with tarball acquisition details."""
        provenance.tarball.method = method
        if url:
            provenance.tarball.url = url
        if path:
            provenance.tarball.path = str(path)
        if sig_warning:
            provenance.verification.result = "not_applicable"
        elif sig_verified:
            provenance.verification.result = "verified"
        else:
            provenance.verification.result = "not_applicable"

    # Offline mode: only use cached tarballs
    if offline:
        if not upstream or not upstream.version:
            return None, False, "Offline mode requires a cached tarball"
        cached_path, cached_meta = find_cached_tarball(
            project=project_key,
            version=upstream.version,
            build_type=build_type.value,
            cache_base=cache_base,
        )
        if cached_path and cached_meta:
            activity("prepare", f"Using cached tarball: {cached_path.name}")
            record(
                "cache",
                cached_path,
                cached_meta.source_url,
                cached_meta.signature_verified,
                cached_meta.signature_warning,
            )
            provenance.verification.mode = upstream_config.signatures.mode.value
            return cached_path, cached_meta.signature_verified, cached_meta.signature_warning
        return None, False, f"Offline mode missing cached tarball for {project_key} {upstream.version}"

    # 1) uscan - preferred method when watch file is available
    success, path, err = run_uscan(pkg_repo, upstream.version if upstream else None)
    if success and path:
        activity("prepare", f"Fetched tarball via uscan: {path.name}")
        activity("prepare", "Tarball selected: uscan")
        record("uscan", path)
        provenance.verification.mode = upstream_config.signatures.mode.value
        provenance.verification.result = "not_applicable"
        if upstream and upstream.version:
            cache_tarball(
                tarball_path=path,
                entry=TarballCacheEntry(
                    project=project_key,
                    package_name=package_name,
                    version=upstream.version,
                    build_type=build_type.value,
                    source_method="uscan",
                ),
                cache_base=cache_base,
            )
        return path, False, ""
    elif err:
        activity("prepare", f"uscan not used: {err}")

    last_error = ""

    # 2) Official tarball (if available)
    if upstream and upstream.tarball_url:
        activity("prepare", f"Downloading official tarball: {upstream.tarball_url}")
        tarball_result = download_and_verify_tarball(upstream, workspace)
        if tarball_result.success:
            activity("prepare", "Tarball selected: official")
            record(
                "official",
                tarball_result.path,
                upstream.tarball_url,
                tarball_result.signature_verified,
                tarball_result.signature_warning,
            )
            provenance.upstream.ref = upstream.version
            provenance.release_source.resolved_version = upstream.version
            provenance.verification.mode = upstream_config.signatures.mode.value
            if tarball_result.signature_verified:
                activity("prepare", "Upstream signature verified")
            elif tarball_result.signature_warning:
                activity("prepare", f"Signature warning: {tarball_result.signature_warning}")
            if tarball_result.path:
                cache_tarball(
                    tarball_path=tarball_result.path,
                    entry=TarballCacheEntry(
                        project=project_key,
                        package_name=package_name,
                        version=upstream.version,
                        build_type=build_type.value,
                        source_method="official",
                        source_url=upstream.tarball_url,
                        signature_verified=tarball_result.signature_verified,
                        signature_warning=tarball_result.signature_warning,
                    ),
                    cache_base=cache_base,
                )
            return (
                tarball_result.path,
                tarball_result.signature_verified,
                tarball_result.signature_warning,
            )
        activity("prepare", f"Official tarball download failed: {tarball_result.error}")
        last_error = tarball_result.error or "official download failed"

    # 3) Registry fallback methods
    methods = getattr(upstream_config.tarball, "prefer", []) or []
    for method in methods:
        name = method.value if hasattr(method, "value") else str(method)
        if name == "official":
            continue  # already tried

        if name == "pypi":
            project = (
                upstream_config.release_source.project
                or upstream_config.project_key
                if hasattr(upstream_config, "project_key")
                else ""
            )
            version = upstream.version if upstream else ""
            ok, path, err = download_pypi_tarball(project, version, workspace)
            if ok and path:
                activity("prepare", f"Fetched PyPI tarball: {path.name}")
                activity("prepare", "Tarball selected: pypi")
                record("pypi", path)
                provenance.verification.mode = upstream_config.signatures.mode.value
                provenance.verification.result = "not_applicable"
                if upstream and upstream.version:
                    cache_tarball(
                        tarball_path=path,
                        entry=TarballCacheEntry(
                            project=project_key,
                            package_name=package_name,
                            version=upstream.version,
                            build_type=build_type.value,
                            source_method="pypi",
                        ),
                        cache_base=cache_base,
                    )
                return path, False, ""
            activity("prepare", f"PyPI tarball download failed: {err}")
            last_error = err or last_error

        elif name == "github_release":
            url = upstream_config.upstream.url
            version = upstream.version if upstream else ""
            ok, path, err = download_github_release_tarball(url, version, workspace)
            if ok and path:
                activity("prepare", f"Fetched GitHub release archive: {path.name}")
                activity("prepare", "Tarball selected: github_release")
                record("github_release", path, url)
                provenance.verification.mode = upstream_config.signatures.mode.value
                provenance.verification.result = "not_applicable"
                if upstream and upstream.version:
                    cache_tarball(
                        tarball_path=path,
                        entry=TarballCacheEntry(
                            project=project_key,
                            package_name=package_name,
                            version=upstream.version,
                            build_type=build_type.value,
                            source_method="github_release",
                            source_url=url,
                        ),
                        cache_base=cache_base,
                    )
                return path, False, ""
            activity("prepare", f"GitHub release download failed: {err}")
            last_error = err or last_error

        elif name == "git_archive":
            project = upstream_config.release_source.project or getattr(
                upstream_config, "project_key", ""
            )
            ref = (
                upstream.version
                if upstream
                else upstream_config.upstream.default_branch or "HEAD"
            )
            repo_dir = workspace / "git-archive"
            if repo_dir.exists():
                shutil.rmtree(repo_dir)

            clone_cmd = [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                ref,
                upstream_config.upstream.url,
                str(repo_dir),
            ]
            clone_exit_code, clone_stdout, clone_stderr = run_command(clone_cmd)
            if clone_exit_code != 0:
                err = clone_stderr or clone_stdout or "git clone failed"
                activity("prepare", f"git archive clone failed: {err}")
                last_error = err or last_error
                continue

            tar_result = generate_snapshot_tarball(
                repo_path=repo_dir,
                ref=ref,
                package=project,
                version=ref,
                output_dir=workspace,
            )
            if tar_result.success and tar_result.path:
                activity("prepare", f"Fetched git archive: {tar_result.path.name}")
                activity("prepare", "Tarball selected: git_archive")
                record("git_archive", tar_result.path)
                provenance.verification.mode = "none"
                provenance.verification.result = "not_applicable"
                if upstream and upstream.version:
                    cache_tarball(
                        tarball_path=tar_result.path,
                        entry=TarballCacheEntry(
                            project=project_key,
                            package_name=package_name,
                            version=upstream.version,
                            build_type=build_type.value,
                            source_method="git_archive",
                            source_url=upstream_config.upstream.url,
                        ),
                        cache_base=cache_base,
                    )
                return tar_result.path, False, ""

            activity("prepare", f"git archive failed: {tar_result.error}")
            last_error = tar_result.error or last_error

    return None, False, last_error or "No tarball could be fetched"


# =============================================================================
# Backwards compatibility aliases (prefixed versions for gradual migration)
# =============================================================================

# These aliases allow build.py to import the prefixed names during migration
_run_uscan = run_uscan
_download_pypi_tarball = download_pypi_tarball
_download_github_release_tarball = download_github_release_tarball
_fetch_release_tarball = fetch_release_tarball
