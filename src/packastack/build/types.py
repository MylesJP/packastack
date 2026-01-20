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

"""Type definitions for build command phases.

This module provides dataclasses that structure the data passed between
build phases, reducing parameter counts and clarifying phase boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from packastack.build.provenance import BuildProvenance
    from packastack.core.context import BuildRequest
    from packastack.core.run import RunContext
    from packastack.upstream.registry import ResolvedUpstream, UpstreamsRegistry


@dataclass
class ResolvedTargets:
    """Resolved target series information.

    Attributes:
        openstack_series: Resolved OpenStack series name (e.g., "caracal").
        ubuntu_series: Resolved Ubuntu codename (e.g., "noble").
        prev_series: Previous OpenStack series for branch inheritance.
        is_development: Whether the target is the development series.
    """

    openstack_series: str
    ubuntu_series: str
    prev_series: str | None = None
    is_development: bool = False


@dataclass
class WorkspacePaths:
    """Paths used during a single package build.

    Attributes:
        workspace: Root workspace directory for this build.
        pkg_repo: Path to the cloned packaging repository.
        build_output: Directory for build artifacts.
        upstream_work_dir: Directory for upstream clone/tarball extraction.
        local_repo: Path to local APT repository.
    """

    workspace: Path
    pkg_repo: Path
    build_output: Path
    upstream_work_dir: Path | None = None
    local_repo: Path | None = None


@dataclass
class BuildInputs:
    """Consolidated inputs for a build operation.

    This bundles the request, run context, configuration, and resolved
    targets into a single object that can be passed through phases.

    Attributes:
        request: Original BuildRequest from CLI.
        run: RunContext for logging and run directory management.
        cfg: Loaded configuration dictionary.
        paths: Resolved path configuration.
        targets: Resolved target series information.
        package_name: Source package name (e.g., "python-oslo.config").
        project_name: Upstream project name (e.g., "oslo.config").
    """

    request: BuildRequest
    run: RunContext
    cfg: dict[str, Any]
    paths: dict[str, Path]
    targets: ResolvedTargets
    package_name: str = ""
    project_name: str = ""


@dataclass
class PhaseResult:
    """Result of a build phase execution.

    Phases return this to indicate success/failure and provide
    data for subsequent phases.

    Attributes:
        success: Whether the phase completed successfully.
        exit_code: Exit code if phase failed (0 for success).
        message: Human-readable status message.
        data: Optional phase-specific data for subsequent phases.
    """

    success: bool
    exit_code: int = 0
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, message: str = "", **data: Any) -> PhaseResult:
        """Create a successful phase result."""
        return cls(success=True, exit_code=0, message=message, data=data)

    @classmethod
    def fail(cls, exit_code: int, message: str, **data: Any) -> PhaseResult:
        """Create a failed phase result."""
        return cls(success=False, exit_code=exit_code, message=message, data=data)


@dataclass
class TarballAcquisitionResult:
    """Result of tarball acquisition phase.

    Attributes:
        success: Whether a tarball was acquired.
        tarball_path: Path to the acquired tarball.
        method: How the tarball was acquired (uscan, official, pypi, etc.).
        version: Upstream version of the tarball.
        signature_verified: Whether the tarball signature was verified.
        signature_warning: Warning message about signature verification.
        git_sha: Git SHA for snapshot builds.
        git_date: Git date for snapshot builds (YYYYMMDD format).
        error: Error message if acquisition failed.
    """

    success: bool
    tarball_path: Path | None = None
    method: str = ""
    version: str = ""
    signature_verified: bool = False
    signature_warning: str = ""
    git_sha: str = ""
    git_date: str = ""
    error: str = ""


@dataclass
class RegistryResolution:
    """Result of registry resolution phase.

    Attributes:
        registry: The loaded UpstreamsRegistry.
        resolved: The resolved upstream configuration.
        project_key: Resolved project key in the registry.
        is_openstack_governed: Whether the project is in openstack/releases.
    """

    registry: UpstreamsRegistry
    resolved: ResolvedUpstream
    project_key: str
    is_openstack_governed: bool = False


@dataclass
class BuildOutcome:
    """Final outcome of a complete build.

    Attributes:
        success: Whether the build completed successfully.
        exit_code: Final exit code.
        package: Source package name.
        version: Built version string.
        build_type: Build type used (release, snapshot).
        artifacts: List of produced artifact paths.
        provenance: Build provenance record.
        error: Error message if build failed.
        skipped_reason: Reason if build was skipped (e.g., retired).
    """

    success: bool
    exit_code: int
    package: str = ""
    version: str = ""
    build_type: str = ""
    artifacts: list[Path] = field(default_factory=list)
    provenance: BuildProvenance | None = None
    error: str | None = None
    skipped_reason: str | None = None

    @classmethod
    def failed(cls, exit_code: int, error: str, package: str = "") -> BuildOutcome:
        """Create a failed build outcome."""
        return cls(
            success=False,
            exit_code=exit_code,
            package=package,
            error=error,
        )

    @classmethod
    def skipped(cls, exit_code: int, reason: str, package: str = "") -> BuildOutcome:
        """Create a skipped build outcome."""
        return cls(
            success=False,
            exit_code=exit_code,
            package=package,
            skipped_reason=reason,
        )
