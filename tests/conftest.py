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

"""Pytest fixtures and configuration for Packastack tests."""

from __future__ import annotations

import gzip
import tempfile
from collections.abc import Generator
from pathlib import Path
from unittest import mock

import pytest
import responses


@pytest.fixture
def temp_home(monkeypatch: pytest.MonkeyPatch) -> Generator[Path, None, None]:
    """Create a temporary home directory and set HOME/XDG paths."""
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
        # Also patch Path.home() to return our temp home
        monkeypatch.setattr(Path, "home", lambda: home)
        yield home


@pytest.fixture
def mock_config(temp_home: Path) -> Path:
    """Create a minimal config file in the temp home."""
    config_dir = temp_home / ".config" / "packastack"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.yaml"
    config_file.write_text("""
paths:
  cache_root: "~/.cache/packastack"
  openstack_releases_repo: "~/.cache/packastack/openstack-releases"
  ubuntu_archive_cache: "~/.cache/packastack/ubuntu-archive"
  local_apt_repo: "~/.cache/packastack/apt-repo"
  build_root: "~/.cache/packastack/build"
  runs_root: "~/.cache/packastack/runs"

defaults:
  upstream_target: "devel"
  ubuntu_series: "devel"
  ubuntu_pockets: ["release", "updates", "security"]
  ubuntu_components: ["main", "universe"]
  ubuntu_arches: ["host", "all"]
  refresh_ttl: "6h"
  mir_policy: "warn"
  cloud_archive: null

mirrors:
  ubuntu_archive: "http://archive.ubuntu.com/ubuntu"

behavior:
  offline: false
  snapshot_archive_on_build: true
""")
    return config_file


@pytest.fixture
def mock_cache_dirs(temp_home: Path) -> dict[str, Path]:
    """Create all cache directories."""
    cache_root = temp_home / ".cache" / "packastack"
    dirs = {
        "cache_root": cache_root,
        "openstack_releases_repo": cache_root / "openstack-releases",
        "ubuntu_archive_cache": cache_root / "ubuntu-archive",
        "local_apt_repo": cache_root / "apt-repo",
        "build_root": cache_root / "build",
        "runs_root": cache_root / "runs",
    }
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    (cache_root / "ubuntu-archive" / "indexes").mkdir(parents=True, exist_ok=True)
    (cache_root / "ubuntu-archive" / "snapshots").mkdir(parents=True, exist_ok=True)
    return dirs


@pytest.fixture
def sample_packages_gz() -> bytes:
    """Return a valid gzip-compressed Packages file."""
    content = b"""\
Package: python3-nova
Version: 1:29.0.0-0ubuntu1
Architecture: all
Maintainer: Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>
Installed-Size: 12345
Depends: python3
Description: OpenStack Compute - Python libraries
 Nova is the OpenStack project that provides a way to provision compute
 instances (aka virtual servers).

"""
    return gzip.compress(content)


@pytest.fixture
def mock_responses() -> Generator[responses.RequestsMock, None, None]:
    """Activate responses mock for HTTP requests."""
    with responses.RequestsMock() as rsps:
        yield rsps


@pytest.fixture
def non_tty_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock sys.__stdout__.isatty() to return False."""
    mock_stdout = mock.MagicMock()
    mock_stdout.isatty.return_value = False
    monkeypatch.setattr("sys.__stdout__", mock_stdout)


@pytest.fixture
def tty_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock sys.__stdout__.isatty() to return True."""
    mock_stdout = mock.MagicMock()
    mock_stdout.isatty.return_value = True
    mock_stdout.write = lambda x: None
    mock_stdout.flush = lambda: None
    monkeypatch.setattr("sys.__stdout__", mock_stdout)


# =============================================================================
# Test helpers for BuildRequest, ResolvedTarget, and mock registry
# =============================================================================


@pytest.fixture
def make_build_request():
    """Factory fixture for creating BuildRequest objects with defaults.

    Usage:
        def test_example(make_build_request):
            request = make_build_request(package="nova", force=True)
    """
    from packastack.core.context import BuildRequest

    def _make(
        package: str = "nova",
        target: str = "devel",
        ubuntu_series: str = "devel",
        cloud_archive: str = "",
        build_type_str: str = "release",
        milestone: str = "",
        force: bool = False,
        offline: bool = False,
        include_retired: bool = False,
        yes: bool = False,
        binary: bool = False,
        builder: str = "sbuild",
        build_deps: bool = True,
        no_cleanup: bool = False,
        no_spinner: bool = True,
        validate_plan_only: bool = False,
        plan_upload: bool = False,
        upload: bool = False,
        workspace_ref=None,
    ) -> BuildRequest:
        return BuildRequest(
            package=package,
            target=target,
            ubuntu_series=ubuntu_series,
            cloud_archive=cloud_archive,
            build_type_str=build_type_str,
            milestone=milestone,
            force=force,
            offline=offline,
            include_retired=include_retired,
            yes=yes,
            binary=binary,
            builder=builder,
            build_deps=build_deps,
            no_cleanup=no_cleanup,
            no_spinner=no_spinner,
            validate_plan_only=validate_plan_only,
            plan_upload=plan_upload,
            upload=upload,
            workspace_ref=workspace_ref,
        )

    return _make


@pytest.fixture
def make_resolved_target():
    """Factory fixture for creating ResolvedTarget objects.

    Usage:
        def test_example(make_resolved_target):
            target = make_resolved_target("nova")
            targets = [make_resolved_target("nova"), make_resolved_target("glance")]
    """
    from packastack.commands.plan import ResolvedTarget

    def _make(
        source_package: str,
        upstream_project: str | None = None,
        resolution_source: str = "local",
    ) -> ResolvedTarget:
        return ResolvedTarget(
            source_package=source_package,
            upstream_project=upstream_project or source_package,
            resolution_source=resolution_source,
        )

    return _make


@pytest.fixture
def mock_registry():
    """Create a mock UpstreamsRegistry for testing.

    The mock is pre-configured to resolve any project to a valid
    OpenDev-style configuration.

    Usage:
        def test_example(mock_registry):
            result = mock_registry.resolve("nova")
    """
    from unittest.mock import MagicMock

    from packastack.upstream.registry import (
        ProjectConfig,
        ReleaseSourceConfig,
        ReleaseSourceType,
        ResolutionSource,
        ResolvedUpstream,
        SignatureMode,
        SignaturesConfig,
        TarballConfig,
        TarballMethod,
        UpstreamConfig,
        UpstreamsRegistry,
    )

    registry = MagicMock(spec=UpstreamsRegistry)
    registry.version = 1
    registry.override_applied = False
    registry.override_path = ""
    registry.warnings = []

    def _resolve(project: str, **kwargs) -> ResolvedUpstream:
        config = ProjectConfig(
            project_key=project,
            common_names=[project],
            upstream=UpstreamConfig(
                type="git",
                host="opendev",
                url=f"https://opendev.org/openstack/{project}.git",
                default_branch="master",
            ),
            release_source=ReleaseSourceConfig(
                type=ReleaseSourceType.OPENSTACK_RELEASES,
                deliverable=project,
            ),
            tarball=TarballConfig(prefer=[TarballMethod.OFFICIAL]),
            signatures=SignaturesConfig(mode=SignatureMode.AUTO),
        )
        return ResolvedUpstream(
            project=project,
            config=config,
            resolution_source=ResolutionSource.REGISTRY_DEFAULTS,
        )

    registry.resolve.side_effect = _resolve
    registry.find_projects.return_value = []

    return registry
