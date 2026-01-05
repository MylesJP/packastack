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

"""Sbuild wrapper for PackaStack binary builds.

Provides clean-room binary package building via sbuild with automatic
setup of the PackaStack local APT repository inside the chroot.

The local repo is made available to the chroot via bind-mount at
/srv/packastack-apt with a trusted apt sources entry. This allows
packages built earlier in a run to satisfy Build-Depends for later
packages.

This module also handles:
- Capturing sbuild stdout/stderr to log files
- Discovering artifacts from user/global sbuild config directories
- Collecting logs from sbuild's configured log directory
- Generating detailed artifact reports
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from packastack.build.collector import (
    ArtifactReport,
    CollectedFile,
    CollectionResult,
    collect_artifacts,
    create_primary_log_symlink,
)
from packastack.build.sbuildrc import discover_candidate_directories

if TYPE_CHECKING:
    pass

# Mount point inside chroot for PackaStack local repo
CHROOT_REPO_MOUNT = "/srv/packastack-apt"
CHROOT_SOURCES_LIST = "/etc/apt/sources.list.d/packastack-local.list"


@dataclass
class SbuildResult:
    """Result of an sbuild invocation."""

    success: bool
    output: str = ""
    artifacts: list[Path] = field(default_factory=list)
    changes_file: Path | None = None
    chroot_name: str = ""
    setup_method: str = "bind-mount"
    local_repo_path: str = ""
    # New fields for enhanced artifact/log collection
    exit_code: int = -1
    collected_artifacts: list[CollectedFile] = field(default_factory=list)
    collected_logs: list[CollectedFile] = field(default_factory=list)
    stdout_log_path: Path | None = None
    stderr_log_path: Path | None = None
    primary_log_path: Path | None = None
    searched_dirs: list[str] = field(default_factory=list)
    validation_message: str = ""
    report_path: Path | None = None
    command: list[str] = field(default_factory=list)


@dataclass
class SbuildConfig:
    """Configuration for sbuild invocation."""

    dsc_path: Path
    output_dir: Path
    distribution: str
    arch: str = "amd64"
    local_repo_root: Path | None = None
    chroot_name: str = ""
    extra_args: list[str] = field(default_factory=list)
    # New fields for log capture
    run_log_dir: Path | None = None
    source_package: str | None = None
    version: str | None = None
    # Lintian options to suppress expected warnings/errors
    lintian_suppress_tags: list[str] = field(default_factory=list)


def is_sbuild_available() -> bool:
    """Check if sbuild is installed and available."""
    return shutil.which("sbuild") is not None


def generate_chroot_setup_commands(local_repo_root: Path) -> list[str]:
    """Generate chroot setup commands for sbuild.

    These commands:
    1. Create mount point directory
    2. Bind-mount the host's local repo into the chroot
    3. Write apt sources list entry for the local repo
    4. Run apt-get update to refresh package lists

    Args:
        local_repo_root: Host path to the PackaStack local APT repository.

    Returns:
        List of shell commands to run in the chroot during setup.
    """
    repo_path = str(local_repo_root.resolve())

    commands = [
        f"mkdir -p {CHROOT_REPO_MOUNT}",
        f"mount --bind {repo_path} {CHROOT_REPO_MOUNT}",
        f"mount -o remount,ro,bind {CHROOT_REPO_MOUNT}",
        f'echo "deb [trusted=yes] file:{CHROOT_REPO_MOUNT} local main" > {CHROOT_SOURCES_LIST}',
        "apt-get update -o Dir::Etc::sourcelist=" + CHROOT_SOURCES_LIST + " -o Dir::Etc::sourceparts=-",
    ]

    return commands


def generate_chroot_cleanup_commands() -> list[str]:
    """Generate chroot cleanup commands for sbuild.

    These commands:
    1. Remove the apt sources list entry
    2. Unmount the bind-mounted repo

    Returns:
        List of shell commands to run in the chroot during cleanup.
    """
    return [
        f"rm -f {CHROOT_SOURCES_LIST}",
        f"umount {CHROOT_REPO_MOUNT} || true",
    ]


def build_sbuild_command(config: SbuildConfig) -> list[str]:
    """Build the sbuild command line.

    Args:
        config: Sbuild configuration.

    Returns:
        Complete sbuild command as a list of arguments.
    """
    # Note: We no longer use --nolog so sbuild will create its log file
    cmd = ["sbuild"]

    # Distribution
    if config.distribution:
        cmd.extend(["-d", config.distribution])

    # Architecture
    if config.arch:
        cmd.extend(["--arch", config.arch])

    # Chroot name (if specified, otherwise sbuild uses default)
    if config.chroot_name:
        cmd.extend(["-c", config.chroot_name])

    # Local repo setup via chroot-setup-commands
    if config.local_repo_root and config.local_repo_root.exists():
        setup_cmds = generate_chroot_setup_commands(config.local_repo_root)
        for setup_cmd in setup_cmds:
            cmd.extend(["--chroot-setup-commands", setup_cmd])

        cleanup_cmds = generate_chroot_cleanup_commands()
        for cleanup_cmd in cleanup_cmds:
            cmd.extend(["--finished-build-commands", cleanup_cmd])

    # Extra arguments
    cmd.extend(config.extra_args)

    # Lintian tag suppression (e.g., inconsistent-maintainer for local builds)
    # Each --lintian-opts passes one option to lintian
    if config.lintian_suppress_tags:
        for tag in config.lintian_suppress_tags:
            cmd.extend(["--lintian-opts", f"--suppress-tags={tag}"])

    # DSC file (must be last)
    cmd.append(str(config.dsc_path))

    return cmd


def run_sbuild(config: SbuildConfig, timeout: int = 3600) -> SbuildResult:
    """Run sbuild to build binary packages.

    This function:
    1. Executes sbuild and captures stdout/stderr to log files
    2. Discovers candidate directories from user/global sbuild config
    3. Collects artifacts (.deb, .changes, .buildinfo) from those directories
    4. Collects sbuild log files from the configured log directory
    5. Validates that at least one binary package was produced
    6. Generates a detailed artifact report

    Args:
        config: Sbuild configuration.
        timeout: Build timeout in seconds (default: 1 hour).

    Returns:
        SbuildResult with success status, artifact paths, and log locations.
    """
    if not is_sbuild_available():
        return SbuildResult(
            success=False,
            output="sbuild is not installed. Install with: sudo apt install sbuild",
            validation_message="sbuild not available",
        )

    # Ensure output directory exists
    config.output_dir.mkdir(parents=True, exist_ok=True)

    # Determine log directory (use run_log_dir if provided, otherwise output_dir)
    log_dir = config.run_log_dir or config.output_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    # Log file paths
    stdout_log = log_dir / "sbuild.stdout.log"
    stderr_log = log_dir / "sbuild.stderr.log"

    cmd = build_sbuild_command(config)
    start_time = time.time()
    start_timestamp = datetime.now(timezone.utc).isoformat()

    try:
        # Run sbuild and capture output to files
        with stdout_log.open("w", encoding="utf-8") as stdout_f, stderr_log.open(
            "w", encoding="utf-8"
        ) as stderr_f:
            result = subprocess.run(
                cmd,
                cwd=config.output_dir,
                stdout=stdout_f,
                stderr=stderr_f,
                text=True,
                timeout=timeout,
            )

        exit_code = result.returncode
        sbuild_success = exit_code == 0

        # Read back the output for legacy compatibility
        stdout_content = stdout_log.read_text(encoding="utf-8", errors="replace")
        stderr_content = stderr_log.read_text(encoding="utf-8", errors="replace")
        combined_output = stdout_content + stderr_content

        # Discover candidate directories for artifact collection
        candidates = discover_candidate_directories(
            packastack_output_dir=config.output_dir,
            packastack_run_log_dir=log_dir,
        )

        # Collect artifacts from all candidate directories
        collection = collect_artifacts(
            dest_dir=config.output_dir,
            candidates=candidates,
            source_package=config.source_package,
            version=config.version,
            start_time=start_time,
            sbuild_output=combined_output,
        )

        # Create primary log symlink/copy. Use a stable, per-source name so
        # logs from different packages do not overwrite each other. We derive
        # the source package name from the provided `source_package` or the
        # DSC filename (prefix before the first underscore) and always use
        # the `<source>-sbuild.log` naming convention (no backward
        # compatibility fallback to `sbuild.log`).
        pkg_name = (
            config.source_package
            if config.source_package
            else config.dsc_path.stem.split("_", 1)[0]
        )
        link_name = f"{pkg_name}-sbuild.log"
        primary_log = create_primary_log_symlink(collection.logs, log_dir, link_name)

        # Build legacy artifacts list from collected files
        legacy_artifacts: list[Path] = []
        changes_file: Path | None = None

        for cf in collection.all_artifacts:
            legacy_artifacts.append(cf.copied_path)
            if cf.copied_path.suffix == ".changes" and "_source" not in cf.copied_path.name:
                changes_file = cf.copied_path

        # Determine overall success: sbuild must succeed AND we must have binaries
        # (unless sbuild failed, in which case we just report the failure)
        if sbuild_success:
            overall_success = collection.success
            validation_msg = collection.validation_message
        else:
            overall_success = False
            validation_msg = f"sbuild exited with code {exit_code}"

        end_timestamp = datetime.now(timezone.utc).isoformat()

        # Generate artifact report
        report_dir = log_dir.parent / "reports" if log_dir.name == "logs" else log_dir / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "sbuild-artifacts.json"

        report = ArtifactReport(
            sbuild_command=cmd,
            sbuild_exit_code=exit_code,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            candidate_dirs=[str(d) for d in candidates.build_dirs[:5]],
            collection=collection,
            stdout_path=str(stdout_log),
            stderr_path=str(stderr_log),
            primary_log_path=str(primary_log) if primary_log else "",
        )
        report.write_json(report_path)

        return SbuildResult(
            success=overall_success,
            output=combined_output,
            artifacts=legacy_artifacts,
            changes_file=changes_file,
            chroot_name=config.chroot_name or f"{config.distribution}-{config.arch}",
            setup_method="bind-mount" if config.local_repo_root else "none",
            local_repo_path=str(config.local_repo_root) if config.local_repo_root else "",
            exit_code=exit_code,
            collected_artifacts=collection.all_artifacts,
            collected_logs=collection.logs,
            stdout_log_path=stdout_log,
            stderr_log_path=stderr_log,
            primary_log_path=primary_log,
            searched_dirs=collection.searched_dirs,
            validation_message=validation_msg,
            report_path=report_path,
            command=cmd,
        )

    except subprocess.TimeoutExpired:
        return SbuildResult(
            success=False,
            output=f"sbuild timed out after {timeout} seconds",
            validation_message=f"Build timeout after {timeout} seconds",
            stdout_log_path=stdout_log if stdout_log.exists() else None,
            stderr_log_path=stderr_log if stderr_log.exists() else None,
            command=cmd,
        )
    except Exception as e:
        return SbuildResult(
            success=False,
            output=str(e),
            validation_message=f"Exception: {e}",
            stdout_log_path=stdout_log if stdout_log.exists() else None,
            stderr_log_path=stderr_log if stderr_log.exists() else None,
            command=cmd,
        )


def get_default_chroot_name(distribution: str, arch: str = "amd64") -> str:
    """Get the default sbuild chroot name for a distribution.

    Args:
        distribution: Ubuntu codename (e.g., "noble", "jammy").
        arch: Architecture (e.g., "amd64").

    Returns:
        Chroot name in sbuild format.
    """
    return f"{distribution}-{arch}-sbuild"
