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

"""Implementation of `packastack build` command.

The BUILD phase: clones packaging repos, validates the plan against upstream,
handles patches with gbp patch-queue, builds source (and optionally binary)
packages, and produces upload orders.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import typer

# Core packastack imports
from packastack.apt import localrepo
from packastack.apt.packages import (
    PackageIndex,
    load_cloud_archive_index,
    load_local_repo_index,
    load_package_index,
    merge_package_indexes,
)
from packastack.core.config import load_config
from packastack.core.context import BuildAllRequest, BuildRequest
from packastack.core.paths import resolve_paths
from packastack.core.run import RunContext, activity
from packastack.planning.build_all_state import (
    BuildAllState,
    FailureType,
    PackageStatus,
    create_initial_state,
    load_state,
    save_state,
)
from packastack.planning.cycle_suggestions import suggest_cycle_edge_exclusions
from packastack.planning.graph import DependencyGraph
from packastack.planning.graph_builder import OPTIONAL_BUILD_DEPS
from packastack.planning.package_discovery import discover_packages
from packastack.planning.type_selection import get_default_parallel_workers
from packastack.reports.plan_graph import PlanGraph, render_waves
from packastack.target.arch import get_host_arch
from packastack.target.series import resolve_series
from packastack.upstream.releases import (
    get_current_development_series,
    get_previous_series,
    is_snapshot_eligible,
    load_openstack_packages,
)

# Build module imports
from packastack.build import (
    EXIT_ALL_BUILD_FAILED,
    EXIT_BUILD_FAILED,
    EXIT_CONFIG_ERROR,
    EXIT_CYCLE_DETECTED,
    EXIT_DISCOVERY_FAILED,
    EXIT_FETCH_FAILED,
    EXIT_GRAPH_ERROR,
    EXIT_MISSING_PACKAGES,
    EXIT_PATCH_FAILED,
    EXIT_POLICY_BLOCKED,
    EXIT_REGISTRY_ERROR,
    EXIT_RESUME_ERROR,
    EXIT_RETIRED_PROJECT,
    EXIT_SUCCESS,
    EXIT_TOOL_MISSING,
)
from packastack.build.all_helpers import (
    build_dependency_graph,
    build_upstream_versions_from_packaging,
    filter_retired_packages,
    get_parallel_batches,
    run_single_build,
)
from packastack.build.all_runner import (
    _run_build_all,
    _run_parallel_builds,
    _run_sequential_builds,
)
from packastack.build.git_helpers import (
    _ensure_no_merge_paths,
    _get_git_author_env,
    _maybe_disable_gpg_sign,
)
from packastack.build.localrepo_helpers import (
    refresh_local_repo_indexes as _refresh_local_repo_indexes,
)
from packastack.build.provenance import summarize_provenance
from packastack.build.tools import check_required_tools
from packastack.build.type_resolution import (
    build_type_from_string,
    resolve_build_type_auto,
    resolve_build_type_from_cli,
)
from packastack.commands.init import _clone_or_update_project_config

if TYPE_CHECKING:
    from packastack.core.run import RunContext as RunContextType

# =============================================================================
# Backwards-compatibility aliases (tests import these from build.py)
# =============================================================================
_build_type_from_string = build_type_from_string
_resolve_build_type_auto = resolve_build_type_auto
_resolve_build_type_from_cli = resolve_build_type_from_cli
_build_dependency_graph = build_dependency_graph
_build_upstream_versions_from_packaging = build_upstream_versions_from_packaging
_get_parallel_batches = get_parallel_batches
_run_single_build = run_single_build
OPTIONAL_DEPS_FOR_CYCLE = OPTIONAL_BUILD_DEPS


def _filter_retired_packages(
    packages: list[str],
    project_config_path: Path | None,
    releases_repo: Path | None,
    openstack_target: str,
    offline: bool,
    run: RunContext,
) -> tuple[list[str], list[str], list[str]]:
    """Filter retired packages using openstack/project-config and releases inference."""
    return filter_retired_packages(
        packages=packages,
        project_config_path=project_config_path,
        releases_repo=releases_repo,
        openstack_target=openstack_target,
        offline=offline,
        run=run,
        clone_project_config_fn=_clone_or_update_project_config,
    )


def _generate_reports(
    state: BuildAllState,
    run_dir: Path,
) -> tuple[Path, Path]:
    """Generate build-all summary reports."""
    from packastack.build.all_reports import generate_build_all_reports
    return generate_build_all_reports(state, run_dir)


# =============================================================================
# Build-all entry point
# =============================================================================


def run_build_all(
    target: str,
    ubuntu_series: str,
    cloud_archive: str,
    build_type: str,
    milestone: str,
    binary: bool,
    keep_going: bool,
    max_failures: int,
    resume: bool,
    resume_run_id: str,
    retry_failed: bool,
    skip_failed: bool,
    parallel: int,
    packages_file: str,
    force: bool,
    offline: bool,
    dry_run: bool,
) -> int:
    """Run build-all and return exit code (without sys.exit).

    This function is called by the unified build --all command.

    Args:
        target: OpenStack series target (e.g., "devel", "caracal").
        ubuntu_series: Ubuntu series target (e.g., "noble").
        cloud_archive: Cloud archive pocket (e.g., "caracal").
        build_type: Build type: auto, release, snapshot, or milestone.
        milestone: Milestone version (e.g., b1, rc1).
        binary: Whether to build binary packages.
        keep_going: Continue on failure.
        max_failures: Stop after N failures (0=unlimited).
        resume: Resume a previous run.
        resume_run_id: Specific run ID to resume.
        retry_failed: Retry failed packages on resume.
        skip_failed: Skip previously failed on resume.
        parallel: Number of parallel workers (0=auto).
        packages_file: File with package names.
        force: Proceed despite warnings.
        offline: Run in offline mode.
        dry_run: Show plan without building.

    Returns:
        Exit code.
    """
    with RunContext("build-all") as run:
        exit_code = EXIT_SUCCESS

        try:
            request = BuildAllRequest(
                target=target,
                ubuntu_series=ubuntu_series,
                cloud_archive=cloud_archive,
                build_type=build_type,
                milestone=milestone,
                binary=binary,
                keep_going=keep_going,
                max_failures=max_failures,
                resume=resume,
                resume_run_id=resume_run_id,
                retry_failed=retry_failed,
                skip_failed=skip_failed,
                parallel=parallel,
                packages_file=packages_file,
                force=force,
                offline=offline,
                dry_run=dry_run,
            )
            exit_code = _run_build_all(run=run, request=request)
        except Exception as e:
            import traceback
            activity("error", f"Build-all failed: {e}")
            for line in traceback.format_exc().splitlines():
                activity("error", f"  {line}")
            exit_code = EXIT_CONFIG_ERROR

    return exit_code


# =============================================================================
# CLI entry point
# =============================================================================



def build(
    package: str = typer.Argument("", help="Package name or OpenStack project to build (omit for --all)"),
    target: str = typer.Option("devel", "-t", "--target", help="OpenStack series target"),
    ubuntu_series: str = typer.Option("devel", "-u", "--ubuntu-series", help="Ubuntu series target"),
    cloud_archive: str = typer.Option("", "-c", "--cloud-archive", help="Cloud archive pocket (e.g., caracal)"),
    build_type: str = typer.Option("auto", "--type", help="Build type: auto, release, snapshot, or milestone"),
    milestone: str = typer.Option("", "-m", "--milestone", help="Milestone version (e.g., b1, rc1) - implies --type milestone"),
    force: bool = typer.Option(False, "-f", "--force", help="Proceed despite warnings"),
    offline: bool = typer.Option(False, "-o", "--offline", help="Run in offline mode"),
    validate_plan_only: bool = typer.Option(False, "-v", "--validate-plan", help="Stop after validated plan"),
    plan_upload: bool = typer.Option(False, "-p", "--plan-upload", help="Show validated plan with upload order"),
    upload: bool = typer.Option(False, "-U", "--upload", help="Print upload commands"),
    binary: bool = typer.Option(True, "-b/-B", "--binary/--no-binary", help="Build binary packages with sbuild (default: on)"),
    builder: str = typer.Option("sbuild", "-x", "--builder", help="Builder for binary packages: sbuild or dpkg"),
    build_deps: bool = typer.Option(True, "-d/-D", "--build-deps/--no-build-deps", help="Auto-build missing dependencies"),
    min_version_policy: str = typer.Option(
        "enforce",
        "--min-version-policy",
        help="Minimum-version handling for upstream deps: enforce, report, or ignore",
    ),
    fail_on_cloud_archive_required: bool = typer.Option(
        False,
        "--fail-on-cloud-archive-required",
        help="Fail build if any dependency requires Cloud Archive (previous LTS unsatisfied)",
    ),
    fail_on_mir_required: bool = typer.Option(
        False,
        "--fail-on-mir-required",
        help="Fail build if any dependency is only available from universe (MIR needed)",
    ),
    update_control_min_versions: bool = typer.Option(
        True,
        "--update-control-min-versions/--no-update-control-min-versions",
        help="Update debian/control minimum versions using previous LTS floor when compatible",
    ),
    normalize_to_prev_lts_floor: bool = typer.Option(
        False,
        "--normalize-to-prev-lts-floor",
        help="Allow lowering constraints to previous LTS floor when safe (>= upstream min)",
    ),
    dry_run_control_edit: bool = typer.Option(
        False,
        "--dry-run-control-edit",
        help="Plan control min-version edits without modifying debian/control",
    ),
    dep_report: bool = typer.Option(
        True,
        "--dep-report/--no-dep-report",
        help="Write dependency satisfaction report files to the run directory",
    ),
    no_cleanup: bool = typer.Option(False, "-k", "--no-cleanup", help="Don't cleanup workspace on success (keep)"),
    no_spinner: bool = typer.Option(False, "-q", "--no-spinner", help="Disable spinner output (quiet)"),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmations"),
    include_retired: bool = typer.Option(False, "--include-retired", help="Build retired upstream projects (default: refuse)"),
    skip_repo_regen: bool = typer.Option(False, "--skip-repo-regen", hidden=True, help="Skip local repo regeneration (internal use)"),
    # --all mode options
    all_packages: bool = typer.Option(False, "-a", "--all", help="Build all discovered packages in dependency order"),
    keep_going: bool = typer.Option(True, "--keep-going/--fail-fast", help="Continue on failure (default: keep-going) [--all only]"),
    max_failures: int = typer.Option(0, "--max-failures", help="Stop after N failures (0=unlimited) [--all only]"),
    resume: bool = typer.Option(False, "--resume", help="Resume a previous run [--all only]"),
    resume_run_id: str = typer.Option("", "--resume-run-id", help="Specific run ID to resume [--all only]"),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Retry failed packages on resume [--all only]"),
    skip_failed: bool = typer.Option(True, "--skip-failed/--no-skip-failed", help="Skip previously failed on resume [--all only]"),
    parallel: int = typer.Option(0, "-j", "--parallel", help="Parallel workers (0=auto) [--all only]"),
    packages_file: str = typer.Option("", "--packages-file", help="File with package names (one per line) [--all only]"),
    dry_run: bool = typer.Option(False, "-n", "--dry-run", help="Show plan without building [--all only]"),
) -> None:
    """Build OpenStack packages for Ubuntu.

    Clones packaging repositories, validates the plan against upstream,
    applies patches with gbp patch-queue, builds source packages, and
    optionally builds binary packages with sbuild.

    When --all is specified, discovers all ubuntu-openstack-dev packages
    and builds them in topological (dependency) order. Supports parallel
    execution and resuming interrupted runs.

    Exit codes:
      0 - Success
      1 - Configuration error
      2 - Required tools missing / Discovery failed
      3 - Fetch failed / Graph error (cycles)
      4 - Patch application failed / Some builds failed
      5 - Missing packages detected / Resume error
      6 - Dependency cycle detected
      7 - Build failed
      8 - Policy blocked
      9 - Registry error
      10 - Retired project (skipped)
    """
    # Validate inputs
    if not package and not all_packages:
        activity("error", "Either specify a package or use --all")
        sys.exit(EXIT_CONFIG_ERROR)

    if package and all_packages:
        activity("error", "Cannot specify both a package and --all")
        sys.exit(EXIT_CONFIG_ERROR)

    # Route to appropriate implementation
    if all_packages:
        _build_all_mode(
            target=target,
            ubuntu_series=ubuntu_series,
            cloud_archive=cloud_archive,
            build_type=build_type,
            milestone=milestone,
            force=force,
            offline=offline,
            binary=binary,
            keep_going=keep_going,
            max_failures=max_failures,
            resume=resume,
            resume_run_id=resume_run_id,
            retry_failed=retry_failed,
            skip_failed=skip_failed,
            parallel=parallel,
            packages_file=packages_file,
            dry_run=dry_run,
        )
    else:
        # Treat top-level --dry-run as validate-plan for single-package mode
        if dry_run:
            validate_plan_only = True

        _build_single_mode(
            package=package,
            target=target,
            ubuntu_series=ubuntu_series,
            cloud_archive=cloud_archive,
            build_type=build_type,
            milestone=milestone,
            force=force,
            offline=offline,
            validate_plan_only=validate_plan_only,
            plan_upload=plan_upload,
            upload=upload,
            binary=binary,
            builder=builder,
            build_deps=build_deps,
            min_version_policy=min_version_policy,
            fail_on_cloud_archive_required=fail_on_cloud_archive_required,
            fail_on_mir_required=fail_on_mir_required,
            update_control_min_versions=update_control_min_versions,
            normalize_to_prev_lts_floor=normalize_to_prev_lts_floor,
            dry_run_control_edit=dry_run_control_edit,
            dep_report=dep_report,
            no_cleanup=no_cleanup,
            no_spinner=no_spinner,
            yes=yes,
            include_retired=include_retired,
            skip_repo_regen=skip_repo_regen,
        )


def _build_single_mode(
    package: str,
    target: str,
    ubuntu_series: str,
    cloud_archive: str,
    build_type: str,
    milestone: str,
    force: bool,
    offline: bool,
    validate_plan_only: bool,
    plan_upload: bool,
    upload: bool,
    binary: bool,
    builder: str,
    build_deps: bool,
    min_version_policy: str,
    fail_on_cloud_archive_required: bool,
    fail_on_mir_required: bool,
    update_control_min_versions: bool,
    normalize_to_prev_lts_floor: bool,
    dry_run_control_edit: bool,
    dep_report: bool,
    no_cleanup: bool,
    no_spinner: bool,
    yes: bool,
    include_retired: bool,
    skip_repo_regen: bool = False,
) -> None:
    """Build a single package."""
    with RunContext("build") as run:
        exit_code = EXIT_SUCCESS
        workspace: Path | None = None
        cleanup_on_exit = not no_cleanup

        try:
            policy_value = (min_version_policy or "").lower()
            if policy_value not in {"enforce", "report", "ignore"}:
                activity("error", "--min-version-policy must be one of: enforce, report, ignore")
                sys.exit(EXIT_CONFIG_ERROR)

            request = BuildRequest(
                package=package,
                target=target,
                ubuntu_series=ubuntu_series,
                cloud_archive=cloud_archive,
                build_type_str=build_type,
                milestone=milestone,
                force=force,
                offline=offline,
                include_retired=include_retired,
                yes=yes,
                binary=binary,
                builder=builder,
                build_deps=build_deps,
                min_version_policy=policy_value,
                dep_report=dep_report,
                fail_on_cloud_archive_required=fail_on_cloud_archive_required,
                fail_on_mir_required=fail_on_mir_required,
                update_control_min_versions=update_control_min_versions,
                normalize_to_prev_lts_floor=normalize_to_prev_lts_floor,
                dry_run_control_edit=dry_run_control_edit,
                no_cleanup=no_cleanup,
                no_spinner=no_spinner,
                validate_plan_only=validate_plan_only,
                plan_upload=plan_upload,
                upload=upload,
                skip_repo_regen=skip_repo_regen,
                workspace_ref=lambda w: _set_workspace(w, locals()),
            )
            exit_code = _run_build(run=run, request=request)
        except Exception as e:
            import traceback
            activity("report", f"Build failed: {e}")
            activity("report", "Traceback:")
            for line in traceback.format_exc().splitlines():
                activity("report", f"  {line}")
            run.log_event({"event": "build.exception", "error": str(e), "traceback": traceback.format_exc()})
            exit_code = EXIT_BUILD_FAILED
            cleanup_on_exit = False
        finally:
            # Cleanup on success only
            if cleanup_on_exit and exit_code == EXIT_SUCCESS and workspace and workspace.exists():
                activity("report", f"Cleaning up workspace: {workspace}")
                try:
                    shutil.rmtree(workspace)
                except Exception:
                    pass
            elif workspace and workspace.exists():
                activity("report", f"Workspace preserved: {workspace}")

    sys.exit(exit_code)


def _build_all_mode(
    target: str,
    ubuntu_series: str,
    cloud_archive: str,
    build_type: str,
    milestone: str,
    force: bool,
    offline: bool,
    binary: bool,
    keep_going: bool,
    max_failures: int,
    resume: bool,
    resume_run_id: str,
    retry_failed: bool,
    skip_failed: bool,
    parallel: int,
    packages_file: str,
    dry_run: bool,
) -> None:
    """Build all packages in dependency order."""
    exit_code = run_build_all(
        target=target,
        ubuntu_series=ubuntu_series,
        cloud_archive=cloud_archive,
        build_type=build_type,
        milestone=milestone,
        binary=binary,
        keep_going=keep_going,
        max_failures=max_failures,
        resume=resume,
        resume_run_id=resume_run_id,
        retry_failed=retry_failed,
        skip_failed=skip_failed,
        parallel=parallel,
        packages_file=packages_file,
        force=force,
        offline=offline,
        dry_run=dry_run,
    )
    sys.exit(exit_code)


def _set_workspace(w: Path, local_vars: dict) -> None:
    """Helper to set workspace in outer scope."""
    local_vars["workspace"] = w


def _run_build(
    run: RunContextType,
    request: BuildRequest,
) -> int:
    """Main build logic.

    Args:
        run: RunContext for logging and run directory management.
        request: BuildRequest containing CLI inputs before resolution.

    Returns:
        Exit code.
    """
    cfg = load_config()
    paths = resolve_paths(cfg)
    tarball_cache_base = paths.get("upstream_tarballs")
    if tarball_cache_base is None:
        tarball_cache_base = paths["cache_root"] / "upstream-tarballs"

    # =========================================================================
    # PHASE: Resolve build type (before planning to avoid policy blocks)
    # =========================================================================
    from packastack.planning.type_selection import BuildType
    from packastack.upstream.releases import get_current_development_series
    
    # Parse CLI build type options
    parsed_type_str, milestone_from_cli = _resolve_build_type_from_cli(
        request.build_type_str, request.milestone
    )
    
    # Resolve build type early (especially for auto)
    resolved_build_type_str: str | None = None
    if parsed_type_str == "auto":
        # Need to resolve auto before planning
        releases_repo = paths["openstack_releases_repo"]
        resolved_ubuntu = resolve_series(request.ubuntu_series)
        
        # Resolve OpenStack target
        if request.target == "devel":
            openstack_target = get_current_development_series(releases_repo) or request.target
        else:
            openstack_target = request.target
        
        # Infer deliverable name from package
        deliverable = request.package
        if request.package.startswith("python-"):
            deliverable = request.package[7:]
        
        build_type_resolved, milestone_resolved, _reason = _resolve_build_type_auto(
            releases_repo=releases_repo,
            series=openstack_target,
            source_package=request.package,
            deliverable=deliverable,
            offline=request.offline,
            run=run,
        )
        resolved_build_type_str = build_type_resolved.value
        activity("resolve", f"Build type (auto): {resolved_build_type_str}")
    else:
        resolved_build_type_str = parsed_type_str
        activity("resolve", f"Build type: {resolved_build_type_str}")
    
    # =========================================================================
    # PHASE: Planning (reuse plan command logic)
    # =========================================================================
    from packastack.commands.plan import run_plan_for_package
    
    plan_request = request.to_plan_request()
    # Pass resolved build type to planning to skip snapshot checks for release/milestone
    # Also skip local repo since packages haven't been cloned yet
    from dataclasses import replace
    plan_request = replace(plan_request, build_type=resolved_build_type_str, skip_local=True)
    
    plan_result, plan_exit_code = run_plan_for_package(
        request=plan_request,
        run=run,
        cfg=cfg,
        paths=paths,
        verbose_output=True,  # Show spinners and progress during planning
    )
    
    # Handle plan-only modes
    if request.validate_plan_only or request.plan_upload:
        # Prefer waves output when available; fall back to enumerated lists
        if getattr(plan_result, "plan_graph", None) is not None:
            waves_output = render_waves(plan_result.plan_graph)
            print(f"\n{waves_output}", file=sys.__stdout__, flush=True)
        else:
            activity("report", f"Build order: {len(plan_result.build_order)} packages")
            for i, pkg in enumerate(plan_result.build_order, 1):
                activity("report", f"  {i}. {pkg}")

        if request.plan_upload:
            # Upload order remains a simple enumerated list
            activity("report", f"Upload order: {len(plan_result.upload_order)} packages")
            for i, pkg in enumerate(plan_result.upload_order, 1):
                activity("report", f"  {i}. {pkg}")
        
        return plan_exit_code
    
    # Honor plan exit codes unless --force
    if plan_exit_code != EXIT_SUCCESS:
        if request.force:
            activity("warn", "Planning detected issues but continuing due to --force")
        else:
            run.write_summary(
                status="failed",
                error="Planning failed",
                exit_code=plan_exit_code,
            )
            return plan_exit_code
    
    # Get the resolved package names from plan result
    if not plan_result.build_order:
        activity("error", "No packages to build")
        run.write_summary(status="failed", error="No packages in build order", exit_code=EXIT_CONFIG_ERROR)
        return EXIT_CONFIG_ERROR
    
    # Build all packages in dependency order using extracted orchestrator
    from packastack.build.single_build import (
        SetupInputs,
        build_single_package,
        setup_build_context,
    )

    activity("build", f"Building {len(plan_result.build_order)} package(s) in dependency order")
    
    for pkg_idx, pkg_name in enumerate(plan_result.build_order, 1):
        activity("build", f"[{pkg_idx}/{len(plan_result.build_order)}] Building: {pkg_name}")
        
        # Create setup inputs for the orchestrator
        setup_inputs = SetupInputs(
            pkg_name=pkg_name,
            target=request.target,
            ubuntu_series=request.ubuntu_series,
            cloud_archive=request.cloud_archive,
            build_type_str=request.build_type_str,
            milestone=request.milestone,
            binary=request.binary,
            builder=request.builder,
            force=request.force,
            offline=request.offline,
            skip_repo_regen=request.skip_repo_regen,
            no_spinner=request.no_spinner,
            build_deps=request.build_deps,
            min_version_policy=request.min_version_policy,
            dep_report=request.dep_report,
            fail_on_cloud_archive_required=request.fail_on_cloud_archive_required,
            fail_on_mir_required=request.fail_on_mir_required,
            update_control_min_versions=request.update_control_min_versions,
            normalize_to_prev_lts_floor=request.normalize_to_prev_lts_floor,
            dry_run_control_edit=request.dry_run_control_edit,
            include_retired=request.include_retired,
            resolved_build_type_str=resolved_build_type_str,
            milestone_from_cli=milestone_from_cli,
            paths=paths,
            cfg=cfg,
            run=run,
        )
        
        # Run setup phases (retirement, registry, policy, indexes, tools, schroot)
        setup_result, ctx = setup_build_context(setup_inputs)
        if not setup_result.success:
            return setup_result.exit_code

        # Run the package build using the orchestrator
        outcome = build_single_package(ctx, workspace_ref=request.workspace_ref)
        
        if not outcome.success:
            run.write_summary(
                status="failed",
                error=outcome.error,
                exit_code=outcome.exit_code,
            )
            return outcome.exit_code
        
        # Show upload commands if requested
        if request.upload and outcome.artifacts:
            # Find the .changes file
            changes_files = [a for a in outcome.artifacts if a.suffix == ".changes"]
            if changes_files:
                activity("report", "Upload commands:")
                activity("report", f"  dput ppa:ubuntu-openstack-dev/proposed {changes_files[0]}")
        
        # Write final summary
        run.write_summary(
            status="success",
            package=ctx.pkg_name,
            version=outcome.new_version,
            build_type=outcome.build_type,
            build_order=plan_result.build_order,
            upload_order=plan_result.upload_order,
            signature_verified=outcome.signature_verified,
            artifacts=[str(a) for a in outcome.artifacts],
            provenance=summarize_provenance(ctx.provenance) if ctx.provenance else None,
                dependency_reports={k: str(v) for k, v in (ctx.dependency_reports or {}).items()},
            exit_code=EXIT_SUCCESS,
        )

        activity("report", f"Package {pkg_idx}/{len(plan_result.build_order)} complete: {pkg_name}")
        activity("report", f"Logs: {run.run_path}")
    
    # All packages built successfully
    activity("build", f"Successfully built all {len(plan_result.build_order)} package(s)")
    return EXIT_SUCCESS

