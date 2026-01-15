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

import contextlib
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import typer

# Core packastack imports
# Build module imports
from packastack.build import (
    EXIT_ALL_BUILD_FAILED,
    EXIT_BUILD_FAILED,
    EXIT_CONFIG_ERROR,
    EXIT_DISCOVERY_FAILED,
    EXIT_GRAPH_ERROR,
    EXIT_RESUME_ERROR,
    EXIT_SUCCESS,
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
    ensure_no_merge_paths,
    git_commit,
)
from packastack.build.provenance import summarize_provenance
from packastack.build.type_resolution import (
    build_type_from_string,
    resolve_build_type_auto,
    resolve_build_type_from_cli,
)
from packastack.commands.init import _clone_or_update_project_config
from packastack.core.config import load_config
from packastack.core.context import BuildAllRequest, BuildRequest
from packastack.core.paths import resolve_paths
from packastack.core.run import RunContext, activity
from packastack.planning.build_all_state import (
    BuildAllState,
    PackageState,
)
from packastack.planning.graph_builder import OPTIONAL_BUILD_DEPS
from packastack.reports.plan_graph import render_waves
from packastack.target.series import resolve_series
from packastack.upstream.releases import (
    get_current_development_series,
)

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
_ensure_no_merge_paths = ensure_no_merge_paths
OPTIONAL_DEPS_FOR_CYCLE = OPTIONAL_BUILD_DEPS
_run_sequential_builds = _run_sequential_builds
_EXPORTED_CONSTANTS = (
    EXIT_ALL_BUILD_FAILED,
    EXIT_DISCOVERY_FAILED,
    EXIT_GRAPH_ERROR,
    EXIT_RESUME_ERROR,
)


def _find_most_recent_workspace(build_root: Path, package: str) -> Path | None:
    """Find the most recent workspace directory for a package.

    Searches for workspace directories matching pattern:
        {build_root}/*/package/

    Returns the most recent one based on directory modification time.

    Args:
        build_root: Root directory containing build workspaces
        package: Package name to search for

    Returns:
        Path to most recent workspace, or None if not found
    """
    matching = build_root.glob(f"*/{package}")

    if not matching:
        return None

    # Filter to directories only and sort by modification time (most recent first)
    dirs = [p for p in matching if p.is_dir()]
    if not dirs:
        return None

    # Sort by modification time, most recent first
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return dirs[0]


def _parse_changes_files(changes_text: str) -> list[str]:
    files: list[str] = []
    in_files = False
    for line in changes_text.splitlines():
        if line.startswith("Files:"):
            in_files = True
            continue
        if in_files:
            if not line.strip():
                break
            if not line[0].isspace():
                break
            parts = line.split()
            if parts:
                files.append(parts[-1])
    return files


def _append_ppa_suffix_to_changelog(changelog_path: Path, suffix: str) -> str:
    text = changelog_path.read_text()
    lines = text.splitlines()
    if not lines:
        raise ValueError("changelog is empty")

    header = lines[0]
    import re

    match = re.match(r"^(\S+)\s+\(([^)]+)\)\s+([^;]+);(.*)$", header)
    if not match:
        raise ValueError("unexpected changelog header format")

    package, version, distribution, remainder = match.groups()
    if not version.endswith(suffix):
        version = f"{version}{suffix}"

    lines[0] = f"{package} ({version}) {distribution};{remainder}"
    changelog_path.write_text("\n".join(lines) + "\n")
    return version


def _ensure_changes_files_present(
    changes_file: Path,
    artifacts: list[Path],
    run: RunContextType,
) -> bool:
    try:
        changes_text = changes_file.read_text()
    except OSError as exc:
        activity("error", f"Failed to read changes file: {changes_file} ({exc})")
        run.log_event({
            "event": "ppa.changes_read_failed",
            "changes_file": str(changes_file),
            "error": str(exc),
        })
        return False

    required_files = _parse_changes_files(changes_text)
    if not required_files:
        return True

    artifact_map = {a.name: a for a in artifacts}
    changes_dir = changes_file.parent
    missing: list[str] = []

    for filename in required_files:
        target = changes_dir / filename
        if target.exists():
            continue
        source = artifact_map.get(filename)
        if source and source.exists():
            shutil.copy2(source, target)
            continue
        missing.append(filename)

    if missing:
        activity("error", f"Missing files for upload: {', '.join(missing)}")
        run.log_event({
            "event": "ppa.missing_upload_files",
            "changes_file": str(changes_file),
            "missing": missing,
        })
        return False

    return True


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


def _build_ppa_source(
    repo_path: Path,
    output_dir: Path,
    run: RunContextType,
) -> tuple[bool, list[Path], str]:
    from packastack.debpkg.gbp import run_command

    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "gbp",
        "buildpackage",
        "-S",
        "-us",
        "-uc",
        "-d",
        f"--git-export-dir={output_dir}",
        "--git-export=WC",
    ]
    returncode, stdout, stderr = run_command(cmd, cwd=repo_path)
    output = stdout + stderr
    if returncode != 0:
        activity("error", f"PPA source build failed: {output}")
        run.log_event({
            "event": "ppa.source_build_failed",
            "returncode": returncode,
            "output": output,
        })
        return False, [], output

    artifacts: list[Path] = []
    for artifact in output_dir.iterdir():
        if not artifact.is_file():
            continue
        name = artifact.name
        is_artifact = (
            artifact.suffix in {".dsc", ".changes", ".buildinfo"}
            or name.endswith(".tar.gz")
            or name.endswith(".tar.xz")
        )
        if is_artifact:
            artifacts.append(artifact)

    return True, artifacts, output


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
        help="Fail build if any dependency requires Cloud Archive (current LTS unsatisfied)",
    ),
    fail_on_mir_required: bool = typer.Option(
        False,
        "--fail-on-mir-required",
        help="Fail build if any dependency is only available from universe (MIR needed)",
    ),
    update_control_min_versions: bool = typer.Option(
        True,
        "--update-control-min-versions/--no-update-control-min-versions",
        help="Update debian/control minimum versions using current LTS floor when compatible",
    ),
    normalize_to_prev_lts_floor: bool = typer.Option(
        False,
        "--normalize-to-prev-lts-floor",
        help="Allow lowering constraints to current LTS floor when safe (>= upstream min)",
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
    ppa_upload: bool = typer.Option(False, "--ppa-upload", help="Upload to configured PPA on success"),
    # --all mode options
    all_packages: bool = typer.Option(False, "-a", "--all", help="Build all discovered packages in dependency order"),
    keep_going: bool = typer.Option(True, "--keep-going/--fail-fast", help="Continue on failure (default: keep-going) [--all only]"),
    max_failures: int = typer.Option(0, "--max-failures", help="Stop after N failures (0=unlimited) [--all only]"),
    resume: bool = typer.Option(False, "--resume", help="Resume a previous run (all mode) or workspace (single mode)"),
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
            ppa_upload=ppa_upload,
            resume_workspace=resume,
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
    ppa_upload: bool = False,
    resume_workspace: bool = False,
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
                ppa_upload=ppa_upload,
                resume_workspace=resume_workspace,
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
                with contextlib.suppress(Exception):
                    shutil.rmtree(workspace)
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


def _upload_to_ppa(changes_file: Path, ppa: str, run: RunContextType) -> bool:
    """Upload a source package to a PPA using dput.

    Args:
        changes_file: Path to the .changes file.
        ppa: PPA specification (e.g., "mylesjp/gazpacho-devel").
        run: RunContext for logging.

    Returns:
        True if upload succeeded, False otherwise.
    """
    import subprocess

    if not changes_file.exists():
        activity("warn", f"Changes file not found: {changes_file}")
        return False

    # Handle ppa: prefix
    target_ppa = ppa
    if not target_ppa.startswith("ppa:"):
        target_ppa = f"ppa:{ppa}"

    # Build dput command
    cmd = ["dput", target_ppa, str(changes_file)]

    activity("report", f"Uploading {changes_file.name} to {target_ppa}...")
    activity("report", f"  Command: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode == 0:
            activity("report", f"Successfully uploaded {changes_file.name} to {target_ppa}")
            run.log_event({"event": "build.ppa_upload_success", "ppa": target_ppa, "changes_file": str(changes_file)})
            return True
        else:
            activity("warn", f"PPA upload to {target_ppa} failed with exit code {result.returncode}")
            if result.stdout:
                activity("warn", f"  stdout: {result.stdout}")
            if result.stderr:
                activity("warn", f"  stderr: {result.stderr}")
            run.log_event({
                "event": "build.ppa_upload_failed",
                "ppa": ppa,
                "exit_code": result.returncode,
                "error": result.stderr or result.stdout,
            })
            return False
    except FileNotFoundError:
        activity("warn", "dput is not installed. Install with: sudo apt install dput")
        return False
    except subprocess.TimeoutExpired:
        activity("warn", "PPA upload timed out (300 seconds)")
        return False
    except Exception as e:
        activity("warn", f"PPA upload failed: {e}")
        run.log_event({"event": "build.ppa_upload_error", "error": str(e)})
        return False


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

    # Parse CLI build type options
    parsed_type_str, milestone_from_cli = _resolve_build_type_from_cli(
        request.build_type_str, request.milestone
    )

    # Resolve build type early (especially for auto)
    resolved_build_type_str: str | None = None
    if parsed_type_str == "auto":
        # Need to resolve auto before planning
        releases_repo = paths["openstack_releases_repo"]
        resolve_series(request.ubuntu_series)

        # Resolve OpenStack target
        if request.target == "devel":
            openstack_target = get_current_development_series(releases_repo) or request.target
        else:
            openstack_target = request.target

        # Infer deliverable name from package
        deliverable = request.package
        if request.package.startswith("python-"):
            deliverable = request.package[7:]

        build_type_resolved, _milestone_resolved, _reason = _resolve_build_type_auto(
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
    from dataclasses import replace
    # If the CLI asked for auto, leave plan_request.build_type as 'auto'
    # so each package can decide individually. Only override when the
    # user explicitly requested a non-auto build type.
    if parsed_type_str != "auto":
        plan_request = replace(plan_request, build_type=resolved_build_type_str)

    # Show spinners only when allowed and running in a real TTY to avoid
    # polluting CI logs. Honor the `no_spinner` flag as well.
    verbose_for_plan = True
    try:
        verbose_for_plan = (not request.no_spinner) and sys.__stdout__.isatty()
    except Exception:
        verbose_for_plan = False

    plan_result, plan_exit_code = run_plan_for_package(
        request=plan_request,
        run=run,
        cfg=cfg,
        paths=paths,
        verbose_output=verbose_for_plan,
    )

    # When build_deps=False, explicit package builds must NOT expand to dependencies
    # This prevents the parallel builder from endlessly recursing when subprocesses
    # compute their own plans.
    if not request.build_deps and plan_result.build_order:
        # Filter the build order to include only the requested package
        # The requested package name might be an alias (e.g. openstack-dashboard -> horizon),
        # so we check if request.package is in the list, or just trust the topological sort's tail.
        # But for exactness, we match the requested package if it exists in the build order.
        target_in_plan = next((p for p in plan_result.build_order if p == request.package), None)

        # If the requested name isn't exactly in the plan (aliasing), we assume the
        # user wants to build the single package that resulted from the plan logic.
        # Since we ran plan_for_package(request.package), the result should focus on it.
        # We take the *last* element as it's topologically the target.
        if not target_in_plan and plan_result.build_order:
            target_in_plan = plan_result.build_order[-1]

        if target_in_plan:
            plan_result = replace(plan_result, build_order=[target_in_plan])
            # Also clear the graph so parallel builder logic isn't triggered
            plan_result = replace(plan_result, plan_graph=None)

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

    # Print plan waves before building (helps users understand parallel batches)
    if getattr(plan_result, "plan_graph", None) is not None:
        try:
            waves_output = render_waves(plan_result.plan_graph)
            print(f"\n{waves_output}", file=sys.__stdout__, flush=True)
        except Exception:
            # Best-effort: ignore render errors
            pass

    activity("build", f"Building {len(plan_result.build_order)} package(s) in dependency order")

    # If multiple packages are present, use state-aware parallel build runner
    if len(plan_result.build_order) > 1 and getattr(plan_result, "plan_graph", None) is not None:
        from packastack.planning.graph import DependencyGraph
        from packastack.planning.type_selection import get_default_parallel_workers

        parallel_workers = get_default_parallel_workers()
        if parallel_workers <= 0:
            parallel_workers = 1

        # Initialize state for tracking build progress
        state = BuildAllState(
            run_id=run.run_id,
            target=request.target,
            ubuntu_series=request.ubuntu_series,
            build_type=resolved_build_type_str,
            started_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            build_order=plan_result.build_order,
            total_packages=len(plan_result.build_order),
            keep_going=True,
            parallel=parallel_workers,
        )

        # Add packages to state
        for pkg in plan_result.build_order:
            state.packages[pkg] = PackageState(name=pkg)

        # Build dependency graph from plan_graph
        graph = DependencyGraph()
        for node_name in plan_result.plan_graph.nodes:
            graph.add_node(node_name)
        for edge in plan_result.plan_graph.edges:
            graph.add_edge(edge.from_node, edge.to_node)

        # Use state-aware parallel build runner
        # Pass through 'auto' when CLI requested auto so each package can decide
        build_type_arg = resolved_build_type_str if parsed_type_str != "auto" else "auto"

        exit_code = _run_parallel_builds(
            state=state,
            graph=graph,
            run_dir=run.run_path,
            state_dir=run.run_path,
            target=request.target,
            ubuntu_series=request.ubuntu_series,
            cloud_archive=request.cloud_archive,
            build_type=build_type_arg,
            binary=request.binary,
            force=request.force,
            parallel=parallel_workers,
            local_repo=paths.get("local_apt_repo"),
            run=run,
            ppa_upload=request.ppa_upload,
        )

        return exit_code

    for pkg_idx, pkg_name in enumerate(plan_result.build_order, 1):
        activity("build", f"[{pkg_idx}/{len(plan_result.build_order)}] Building: {pkg_name}")

        # Handle --resume: find and use existing workspace
        resume_workspace_path: Path | None = None
        if request.resume_workspace:
            build_root = paths.get("build_root", paths["cache_root"] / "build")
            resume_workspace_path = _find_most_recent_workspace(build_root, pkg_name)

            if resume_workspace_path:
                activity("resume", f"Resuming from existing workspace: {resume_workspace_path}")
                run.log_event({
                    "event": "resume.workspace_found",
                    "workspace": str(resume_workspace_path),
                    "package": pkg_name,
                })
            else:
                activity("resume", f"No existing workspace found for {pkg_name}, starting fresh build")
                run.log_event({
                    "event": "resume.no_workspace",
                    "package": pkg_name,
                })

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
            # Preserve 'auto' when the CLI requested auto so per-package
            # selection can occur during setup. Otherwise pass the resolved
            # build type determined earlier.
            resolved_build_type_str=(parsed_type_str if parsed_type_str == "auto" else resolved_build_type_str),
            milestone_from_cli=milestone_from_cli,
            paths=paths,
            cfg=cfg,
            run=run,
            resume_workspace_path=resume_workspace_path,
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

        # Upload to PPA if configured
        if request.ppa_upload and outcome.artifacts:
            changes_files = [a for a in outcome.artifacts if a.suffix == ".changes"]
            if changes_files:
                upload_ppa = cfg.get("defaults", {}).get("upload_ppa")
                if upload_ppa:
                    activity("ppa", f"Preparing PPA upload to {upload_ppa}")

                    # Ensure workspace is clean before PPA bump
                    status_result = subprocess.run(
                        ["git", "status", "--porcelain"],
                        cwd=ctx.pkg_repo,
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    if status_result.stdout.strip():
                        activity("ppa", "Cleaning workspace before PPA upload")
                        subprocess.run(
                            ["git", "reset", "--hard", "HEAD"],
                            cwd=ctx.pkg_repo,
                            check=True,
                            capture_output=True,
                        )
                        subprocess.run(
                            ["git", "clean", "-fd"],
                            cwd=ctx.pkg_repo,
                            check=True,
                            capture_output=True,
                        )

                    # 1. Modify changelog
                    changelog_path = ctx.pkg_repo / "debian" / "changelog"
                    ppa_version = _append_ppa_suffix_to_changelog(
                        changelog_path,
                        "~ppa1",
                    )
                    activity("ppa", f"Bumping version to {ppa_version}")

                    # 2. Commit
                    git_commit(
                        ctx.pkg_repo,
                        f"PPA build {ppa_version}",
                        files=["debian/changelog"],
                    )

                    # 3. Rebuild
                    activity("ppa", "Building PPA source package...")
                    ppa_output_dir = ctx.workspace / "build-output"
                    ppa_success, ppa_artifacts, _ppa_output = _build_ppa_source(
                        ctx.pkg_repo,
                        ppa_output_dir,
                        run,
                    )

                    if ppa_success and ppa_artifacts:
                        ppa_changes_files = [
                            a for a in ppa_artifacts if a.suffix == ".changes"
                        ]
                        if ppa_changes_files:
                            source_changes = next(
                                (
                                    a
                                    for a in ppa_changes_files
                                    if "ppa1" in a.name
                                    and (a.name.endswith("_source.changes") or a.name.endswith(".source.changes"))
                                ),
                                next(
                                    (
                                        a
                                        for a in ppa_changes_files
                                        if a.name.endswith("_source.changes")
                                        or a.name.endswith(".source.changes")
                                    ),
                                    ppa_changes_files[0],
                                ),
                            )
                            upload_ok = _ensure_changes_files_present(
                                source_changes,
                                [*ppa_artifacts, *(outcome.artifacts or [])],
                                run,
                            )
                            if upload_ok:
                                _upload_to_ppa(source_changes, upload_ppa, run)
                    else:
                        activity("error", "PPA source build failed")

                    # 4. Reset
                    activity("ppa", "Resetting workspace state")
                    subprocess.run(
                        ["git", "reset", "--hard", "HEAD^"],
                        cwd=ctx.pkg_repo,
                        check=True,
                        capture_output=True,
                    )
                else:
                    activity(
                        "warn",
                        "PPA upload requested but 'upload_ppa' not configured in ~/.packastack/config.yaml",
                    )
                    activity(
                        "warn",
                        "Set 'defaults.upload_ppa' to enable (e.g., 'mylesjp/gazpacho-devel')",
                    )

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

