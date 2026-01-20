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

"""Context objects for Packastack build operations.

This module provides hierarchical context/config dataclasses that bundle
related parameters, replacing functions with excessive parameter counts.

Immutable configs (frozen=True):
- TargetConfig: Target series and archive information
- PolicyConfig: Behavioral flags (force, offline, etc.)
- BuildOptions: Build-specific settings (type, binary, etc.)
- ResumeConfig: Resume settings for build-all
- BuildRequest: CLI inputs before resolution

Mutable context:
- BuildContext: Accumulates state during a build run
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from packastack.planning.type_selection import BuildType

if TYPE_CHECKING:
    from packastack.build.provenance import BuildProvenance
    from packastack.core.run import RunContext


@dataclass(frozen=True)
class PlanRequest:
    """Immutable request for planning operations.

    Contains the minimal set of parameters needed for dependency resolution,
    cycle detection, and build order computation.

    Attributes:
        package: Package name or OpenStack project from CLI.
        target: OpenStack series target (e.g., "devel", "caracal").
        ubuntu_series: Ubuntu series target (e.g., "devel", "noble").
        force: --force flag to override warnings.
        offline: --offline flag for offline mode.
        include_retired: --include-retired flag.
        skip_local: Skip local apt repo search.
        build_type: Optional resolved build type to skip snapshot checks for RELEASE.
    """

    package: str
    target: str = "devel"
    ubuntu_series: str = "devel"
    force: bool = False
    offline: bool = False
    include_retired: bool = False
    skip_local: bool = False
    build_type: str | None = None


@dataclass(frozen=True)
class BuildRequest:
    """Immutable request containing CLI inputs for build operations.

    This captures what the user requested. Resolution produces
    a BuildContext with resolved values.

    Attributes:
        package: Package name or OpenStack project from CLI.
        target: OpenStack series target (e.g., "devel", "caracal").
        ubuntu_series: Ubuntu series target (e.g., "devel", "noble").
        cloud_archive: Cloud archive pocket or empty string.
        build_type_str: Build type as string ("auto", "release", etc.).
        force: --force flag.
        offline: --offline flag.
        include_retired: --include-retired flag.
        yes: --yes flag.
        binary: --binary flag.
        builder: Builder for binary packages.
        build_deps: --build-deps flag.
        no_cleanup: --no-cleanup flag.
        no_spinner: --no-spinner flag.
        validate_plan_only: --validate-plan flag.
        plan_upload: --plan-upload flag.
        upload: --upload flag.
        skip_repo_regen: --skip-repo-regen flag (skip local repo regeneration).
        resume_workspace: Resume a previous workspace in single-package mode.
        resume_run_id: Specific run ID to resume in single-package mode.
        workspace_ref: Callback to set workspace in outer scope.
    """

    package: str
    target: str = "devel"
    ubuntu_series: str = "devel"
    cloud_archive: str = ""
    build_type_str: str = "auto"
    force: bool = False
    offline: bool = False
    include_retired: bool = False
    yes: bool = False
    binary: bool = True
    builder: str = "sbuild"
    build_deps: bool = True
    # Minimum-version enforcement policy for upstream deps: enforce, report, ignore
    min_version_policy: str = "enforce"
    # Write dependency satisfaction report files during build
    dep_report: bool = True
    # Fail build when previous LTS cannot satisfy deps (cloud-archive needed)
    fail_on_cloud_archive_required: bool = False
    # Fail build when dependencies are only in universe (need MIR)
    fail_on_mir_required: bool = False
    # Control-file min-version policy switches
    update_control_min_versions: bool = True
    normalize_to_prev_lts_floor: bool = False
    dry_run_control_edit: bool = False
    no_cleanup: bool = False
    no_spinner: bool = False
    validate_plan_only: bool = False
    plan_upload: bool = False
    upload: bool = False
    skip_repo_regen: bool = False
    ppa_upload: bool = False
    resume_workspace: bool = False
    resume_run_id: str = ""
    workspace_ref: Callable[[Path], None] | None = None

    def to_plan_request(self) -> PlanRequest:
        """Convert BuildRequest to PlanRequest for planning phase."""
        return PlanRequest(
            package=self.package,
            target=self.target,
            ubuntu_series=self.ubuntu_series,
            force=self.force,
            offline=self.offline,
            include_retired=self.include_retired,
            skip_local=False,  # Build always checks local repo
        )


@dataclass(frozen=True)
class BuildAllRequest:
    """Immutable request containing CLI inputs for build-all before resolution.

    Attributes:
        target: OpenStack series target (e.g., "devel", "caracal").
        ubuntu_series: Ubuntu series target (e.g., "devel", "noble").
        cloud_archive: Cloud archive pocket or empty string.
        build_type: Build type string ("auto", "release", "snapshot").
        binary: Build binary packages.
        keep_going: Continue on failure.
        max_failures: Stop after N failures (0=unlimited).
        resume: Resume a previous run.
        resume_run_id: Specific run ID to resume.
        retry_failed: Retry failed packages on resume.
        skip_failed: Skip previously failed packages.
        parallel: Parallel workers (0=auto).
        packages_file: File with package names.
        force: Proceed despite warnings.
        offline: Offline mode.
        dry_run: Show plan without building.
    """

    target: str = "devel"
    ubuntu_series: str = "devel"
    cloud_archive: str = ""
    build_type: str = "auto"
    binary: bool = True
    keep_going: bool = True
    max_failures: int = 0
    resume: bool = False
    resume_run_id: str = ""
    retry_failed: bool = False
    skip_failed: bool = True
    parallel: int = 0
    packages_file: str = ""
    force: bool = False
    offline: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class TargetConfig:
    """Immutable configuration for target series and archive.

    Attributes:
        ubuntu_series: Original Ubuntu series specification (e.g., "devel", "noble").
        openstack_target: OpenStack series name (e.g., "caracal", "devel").
        cloud_archive: Cloud archive pocket name, or empty string if not using CA.
        resolved_ubuntu: Resolved Ubuntu codename (must not be "devel").
    """

    ubuntu_series: str
    openstack_target: str
    cloud_archive: str
    resolved_ubuntu: str

    def __post_init__(self) -> None:
        """Validate that resolved_ubuntu is actually resolved."""
        if self.resolved_ubuntu == "devel":
            raise ValueError(
                "resolved_ubuntu must be a concrete codename, not 'devel'. "
                "Use resolve_series() before constructing TargetConfig."
            )


@dataclass(frozen=True)
class PolicyConfig:
    """Immutable configuration for behavioral policies.

    Attributes:
        force: Proceed despite warnings.
        offline: Run in offline mode (no network requests).
        include_retired: Build retired upstream projects.
        yes: Skip interactive confirmations.
    """

    force: bool = False
    offline: bool = False
    include_retired: bool = False
    yes: bool = False


@dataclass(frozen=True)
class BuildOptions:
    """Immutable configuration for build-specific settings.

    Attributes:
        build_type: Type of build (release, snapshot).
        binary: Whether to build binary packages.
        builder: Builder for binary packages ("sbuild" or "dpkg").
        build_deps: Whether to auto-build missing dependencies.
        no_cleanup: Don't cleanup workspace on success.
        no_spinner: Disable spinner output.
        validate_plan_only: Stop after validating the build plan.
        plan_upload: Show validated plan with upload order.
        upload: Print upload commands after build.
    """

    build_type: BuildType = BuildType.RELEASE
    binary: bool = True
    builder: str = "sbuild"
    build_deps: bool = True
    no_cleanup: bool = False
    no_spinner: bool = False
    validate_plan_only: bool = False
    plan_upload: bool = False
    upload: bool = False

    def __post_init__(self) -> None:
        """Validate build_type consistency."""
        if self.build_type not in (BuildType.RELEASE, BuildType.SNAPSHOT):
            raise ValueError(
                f"build_type must be RELEASE or SNAPSHOT, got {self.build_type.value}"
            )


@dataclass(frozen=True)
class ResumeConfig:
    """Immutable configuration for build-all resume functionality.

    Attributes:
        resume: Whether to resume a previous run.
        resume_run_id: Specific run ID to resume, or empty for most recent.
        retry_failed: Retry previously failed packages.
        skip_failed: Skip previously failed packages.
    """

    resume: bool = False
    resume_run_id: str = ""
    retry_failed: bool = False
    skip_failed: bool = True


@dataclass
class BuildContext:
    """Mutable context that accumulates state during a build run.

    This is the primary context object passed through build operations.
    It composes immutable configs and adds mutable state fields.

    Attributes:
        target: Target series configuration.
        policy: Behavioral policy configuration.
        options: Build options configuration.
        run: RunContext for logging and run directory management.
        paths: Resolved path configuration.
        package: Original package/project name from CLI.
        pkg_name: Resolved source package name.
        workspace: Workspace directory (set during build).
        provenance: Build provenance record (set during build).
        registry_version: Version of upstreams registry used.
        resolution_source: How upstream was resolved (registry_explicit, etc.).
        project_key: Project key used for registry lookup.
    """

    target: TargetConfig
    policy: PolicyConfig
    options: BuildOptions
    run: RunContext
    paths: dict[str, Path]

    # Package identification
    package: str = ""
    pkg_name: str = ""

    # Mutable state accumulated during build
    workspace: Path | None = None
    provenance: BuildProvenance | None = None
    registry_version: int = 0
    resolution_source: str = ""
    project_key: str = ""

    @classmethod
    def from_cli_args(
        cls,
        *,
        # Target args
        ubuntu_series: str,
        openstack_target: str,
        cloud_archive: str,
        resolved_ubuntu: str,
        # Policy args
        force: bool = False,
        offline: bool = False,
        include_retired: bool = False,
        yes: bool = False,
        # Build option args
        build_type: BuildType = BuildType.RELEASE,
        binary: bool = True,
        builder: str = "sbuild",
        build_deps: bool = True,
        no_cleanup: bool = False,
        no_spinner: bool = False,
        validate_plan_only: bool = False,
        plan_upload: bool = False,
        upload: bool = False,
        # Context args
        run: RunContext,
        paths: dict[str, Path],
        package: str = "",
        pkg_name: str = "",
    ) -> BuildContext:
        """Construct BuildContext from CLI arguments.

        This factory method creates all nested frozen configs and assembles
        the mutable context. Use this at CLI entry points.

        Args:
            ubuntu_series: Original Ubuntu series from CLI.
            openstack_target: Resolved OpenStack target series.
            cloud_archive: Cloud archive pocket or empty string.
            resolved_ubuntu: Resolved Ubuntu codename (not "devel").
            force: --force flag.
            offline: --offline flag.
            include_retired: --include-retired flag.
            yes: --yes flag.
            build_type: Resolved BuildType enum.
            binary: --binary flag.
            builder: --builder value.
            build_deps: --build-deps flag.
            no_cleanup: --no-cleanup flag.
            no_spinner: --no-spinner flag.
            validate_plan_only: --validate-plan flag.
            plan_upload: --plan-upload flag.
            upload: --upload flag.
            run: Active RunContext.
            paths: Resolved paths dict.
            package: Original package name from CLI.
            pkg_name: Resolved source package name.

        Returns:
            Fully constructed BuildContext.
        """
        target = TargetConfig(
            ubuntu_series=ubuntu_series,
            openstack_target=openstack_target,
            cloud_archive=cloud_archive,
            resolved_ubuntu=resolved_ubuntu,
        )

        policy = PolicyConfig(
            force=force,
            offline=offline,
            include_retired=include_retired,
            yes=yes,
        )

        options = BuildOptions(
            build_type=build_type,
            binary=binary,
            builder=builder,
            build_deps=build_deps,
            no_cleanup=no_cleanup,
            no_spinner=no_spinner,
            validate_plan_only=validate_plan_only,
            plan_upload=plan_upload,
            upload=upload,
        )

        return cls(
            target=target,
            policy=policy,
            options=options,
            run=run,
            paths=paths,
            package=package,
            pkg_name=pkg_name,
        )


@dataclass
class BuildAllContext:
    """Mutable context for build-all operations.

    Extends BuildContext with build-all specific settings.

    Attributes:
        target: Target series configuration.
        policy: Behavioral policy configuration.
        options: Build options configuration.
        resume: Resume configuration.
        run: RunContext for logging.
        paths: Resolved paths.
        parallel: Number of parallel workers.
        max_failures: Stop after N failures (0=unlimited).
        keep_going: Continue on failure.
        packages_file: Path to file with package names, or empty.
        dry_run: Show plan without building.
    """

    target: TargetConfig
    policy: PolicyConfig
    options: BuildOptions
    resume: ResumeConfig
    run: RunContext
    paths: dict[str, Path]

    parallel: int = 0
    max_failures: int = 0
    keep_going: bool = True
    packages_file: str = ""
    dry_run: bool = False

    @classmethod
    def from_cli_args(
        cls,
        *,
        # Target args
        ubuntu_series: str,
        openstack_target: str,
        cloud_archive: str,
        resolved_ubuntu: str,
        # Policy args
        force: bool = False,
        offline: bool = False,
        # Build option args
        build_type: BuildType = BuildType.RELEASE,
        binary: bool = True,
        # Resume args
        resume: bool = False,
        resume_run_id: str = "",
        retry_failed: bool = False,
        skip_failed: bool = True,
        # Build-all specific args
        parallel: int = 0,
        max_failures: int = 0,
        keep_going: bool = True,
        packages_file: str = "",
        dry_run: bool = False,
        # Context args
        run: RunContext,
        paths: dict[str, Path],
    ) -> BuildAllContext:
        """Construct BuildAllContext from CLI arguments."""
        target = TargetConfig(
            ubuntu_series=ubuntu_series,
            openstack_target=openstack_target,
            cloud_archive=cloud_archive,
            resolved_ubuntu=resolved_ubuntu,
        )

        policy = PolicyConfig(
            force=force,
            offline=offline,
        )

        options = BuildOptions(
            build_type=build_type,
            binary=binary,
        )

        resume_config = ResumeConfig(
            resume=resume,
            resume_run_id=resume_run_id,
            retry_failed=retry_failed,
            skip_failed=skip_failed,
        )

        return cls(
            target=target,
            policy=policy,
            options=options,
            resume=resume_config,
            run=run,
            paths=paths,
            parallel=parallel,
            max_failures=max_failures,
            keep_going=keep_going,
            packages_file=packages_file,
            dry_run=dry_run,
        )
