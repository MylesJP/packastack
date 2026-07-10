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

import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from packastack.apt.localrepo import ensure_repo_initialized
from packastack.build.collector import (
    ArtifactReport,
    CollectedFile,
    collect_artifacts,
    create_primary_log_symlink,
)
from packastack.build.sbuildrc import discover_candidate_directories
from packastack.build.schroot import RepoMountError, configure_repo_mount

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass

# Mount point inside chroot for PackaStack local repo
CHROOT_REPO_MOUNT = "/srv/packastack-apt"
CHROOT_SOURCES_LIST = "/etc/apt/sources.list.d/packastack-local.list"

# Files written into the ephemeral schroot session to enable the Ubuntu
# -proposed pocket (where new Python interpreters and the dual-supported
# python3-defaults stage during a transition).
CHROOT_PROPOSED_SOURCES = "/etc/apt/sources.list.d/packastack-proposed.list"
CHROOT_PROPOSED_PREFERENCES = "/etc/apt/preferences.d/packastack-proposed"

_PYTHON_VERSION_RE = re.compile(r"3\.\d+")


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
    # Python versions pybuild exercised during the build (e.g. ["3.14", "3.15"])
    python_versions_tested: list[str] = field(default_factory=list)
    # Per-interpreter results when an explicit Python matrix was requested.
    python_version_results: dict[str, SbuildResult] = field(default_factory=dict)


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
    # Enable the Ubuntu -proposed pocket inside the ephemeral session so
    # pybuild builds/tests against all supported Python versions during a
    # transition. Requires network (session apt update + dist-upgrade).
    proposed: bool = True
    mirror: str = "http://archive.ubuntu.com/ubuntu"
    components: list[str] = field(default_factory=lambda: ["main", "universe"])
    # Explicit pybuild interpreter matrix.  Each version is installed as an
    # sbuild-only build-dependency and selected with PYBUILD_VERSIONS.
    python_versions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.python_versions = normalize_python_versions(self.python_versions)


def normalize_python_versions(versions: object) -> list[str]:
    """Validate and normalise an explicit Python version matrix.

    Version strings are deliberately narrow: they become both Debian package
    names (``python<version>``) and values passed to ``PYBUILD_VERSIONS``.
    """
    if versions is None:
        return []
    if not isinstance(versions, list):
        raise ValueError("sbuild.python_versions must be a list of strings")

    normalized: list[str] = []
    for version in versions:
        if not isinstance(version, str) or not _PYTHON_VERSION_RE.fullmatch(version):
            raise ValueError("sbuild.python_versions entries must look like '3.14'")
        if version not in normalized:
            normalized.append(version)
    return normalized


def is_sbuild_available() -> bool:
    """Check if sbuild is installed and available."""
    return shutil.which("sbuild") is not None


def generate_chroot_setup_commands() -> list[str]:
    """Generate chroot setup commands for the local APT repository.

    These commands run inside the chroot *after* schroot has processed
    its fstab (which bind-mounts the host repo at ``CHROOT_REPO_MOUNT``).
    They only need to write an apt sources entry and refresh the index.

    Returns:
        List of shell commands to run in the chroot during setup.
    """
    return [
        (f'echo "deb [trusted=yes] file:{CHROOT_REPO_MOUNT} local main" > {CHROOT_SOURCES_LIST}'),
        (
            "apt-get update"
            f" -o Dir::Etc::sourcelist={CHROOT_SOURCES_LIST}"
            " -o Dir::Etc::sourceparts=-"
        ),
    ]


def generate_proposed_setup_commands(
    series: str,
    mirror: str,
    components: list[str],
) -> list[str]:
    """Generate chroot setup commands enabling the -proposed pocket.

    Writes an apt sources entry for ``{series}-proposed`` plus an apt
    preferences pin at priority 500. The pin is load-bearing: the devel
    series -proposed pocket publishes ``NotAutomatic: yes``, which apt
    pins at priority 100 — without the override neither dist-upgrade nor
    build-dependency resolution would pull anything from -proposed.

    The commands run inside the ephemeral schroot session overlay, so the
    on-disk source chroot is never modified.

    Note: sbuild interprets percent-escapes in external commands, so the
    generated commands must not contain ``%`` (no ``printf``).

    Args:
        series: Resolved Ubuntu codename (e.g., "stonking").
        mirror: Ubuntu archive mirror URL.
        components: Archive components (e.g., ["main", "universe"]).

    Returns:
        List of shell commands to run in the chroot during setup.
    """
    pocket = f"{series}-proposed"
    return [
        (f'echo "deb {mirror} {pocket} {" ".join(components)}" > {CHROOT_PROPOSED_SOURCES}'),
        (
            "{ "
            "echo 'Package: *'; "
            f"echo 'Pin: release a={pocket}'; "
            "echo 'Pin-Priority: 500'; "
            f"}} > {CHROOT_PROPOSED_PREFERENCES}"
        ),
    ]


def parse_pybuild_python_versions(log_text: str) -> list[str]:
    """Extract the Python versions pybuild exercised from an sbuild log.

    Scans pybuild informational lines such as::

        I: pybuild base:385: python3.14 setup.py config
        I: pybuild pybuild:308: python3.15 -m pytest

    and returns the unique Python versions they mention. This covers
    configure/build/install/test steps, so "exercised" rather than
    strictly "tested". Non-pybuild lines (e.g. apt download lines that
    mention interpreter package names) are ignored.

    Args:
        log_text: Combined sbuild output.

    Returns:
        Sorted unique versions, e.g. ["3.14", "3.15"].
    """
    versions: set[str] = set()
    for line in log_text.splitlines():
        if not re.match(r"^I: pybuild \S+:\d+:", line):
            continue
        versions.update(re.findall(r"python(3\.\d+)", line))

    return sorted(versions, key=lambda v: tuple(int(p) for p in v.split(".")))


def write_python_versions_sbuild_config(log_dir: Path, python_versions: list[str]) -> Path | None:
    """Write an additive sbuild config fragment for a Python build matrix.

    ``SBUILD_CONFIG`` is read on the host before sbuild enters the chroot. The
    fragment remains beside the build logs so the selected matrix is auditable.
    ``DEBPYTHON3_SUPPORTED`` makes Debian helpers such as ``py3versions`` and
    ``pkgos-dh_auto_test`` see the complete matrix. ``PYBUILD_VERSIONS`` covers
    direct pybuild use, while ``PYTHON`` selects the newest configured version
    for custom stestr commands that bypass the version-aware helpers. An
    existing user-supplied ``SBUILD_CONFIG`` is loaded first and preserved.
    """
    if not python_versions:
        return None

    path = log_dir / "sbuild-python-versions.conf"
    previous_config = os.environ.get("SBUILD_CONFIG")
    lines = ["# Generated by Packastack; retained with this build's logs."]
    if previous_config:
        escaped = previous_config.replace("\\", "\\\\").replace("'", "\\'")
        lines.append(f"do '{escaped}' or die \"failed to load $ENV{{SBUILD_CONFIG}}: $@\";")
    lines.append(
        "$build_environment->{'DEBPYTHON3_SUPPORTED'} = "
        f"'{','.join(python_versions)}';"
    )
    lines.append(f"$build_environment->{{'PYBUILD_VERSIONS'}} = '{' '.join(python_versions)}';")
    lines.append(f"$build_environment->{{'PYTHON'}} = 'python{python_versions[-1]}';")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def generate_chroot_cleanup_commands() -> list[str]:
    """Generate chroot cleanup commands for sbuild.

    Removes the apt sources list entry added during setup.  The
    bind-mount itself is managed by schroot's fstab and unmounted
    automatically when the session ends.

    Returns:
        List of shell commands to run in the chroot during cleanup.
    """
    return [
        f"rm -f {CHROOT_SOURCES_LIST}",
    ]


def build_sbuild_command(config: SbuildConfig) -> list[str]:
    """Build the sbuild command line.

    Args:
        config: Sbuild configuration.

    Returns:
        Complete sbuild command as a list of arguments.
    """
    # Use --nolog so sbuild prints everything to stdout (which we capture)
    # rather than writing to $log_dir.  Without this flag sbuild sends its
    # build log only to the log-file and stdout stays empty, making it
    # impossible for packastack (and the AI diagnosis) to see the output.
    cmd = ["sbuild", "--nolog"]

    # Distribution
    if config.distribution:
        cmd.extend(["-d", config.distribution])

    # Architecture
    if config.arch:
        cmd.extend(["--arch", config.arch])

    # Chroot name — use -c to select the specific chroot.
    # When -d is also given, sbuild uses -d for the distribution label
    # in .changes files and -c to pick which schroot to enter.
    if config.chroot_name:
        cmd.extend(["-c", config.chroot_name])

    # Local repo setup — ensure indexes exist, configure schroot fstab
    # for the bind-mount, and add apt source commands to the chroot.
    if config.local_repo_root and config.local_repo_root.exists():
        ensure_repo_initialized(config.local_repo_root, config.arch)

        repo_ready = False
        if config.chroot_name:
            try:
                repo_ready = configure_repo_mount(config.chroot_name, config.local_repo_root)
            except RepoMountError as exc:
                log.warning("Could not configure repo mount: %s", exc)

        if repo_ready:
            for setup_cmd in generate_chroot_setup_commands():
                cmd.extend(["--chroot-setup-commands", setup_cmd])
            for cleanup_cmd in generate_chroot_cleanup_commands():
                cmd.extend(["--finished-build-commands", cleanup_cmd])

    # -proposed pocket: enable inside the ephemeral session and bring the
    # session current so pybuild sees the transition python3-defaults.
    if config.proposed:
        for setup_cmd in generate_proposed_setup_commands(
            config.distribution, config.mirror, config.components
        ):
            cmd.extend(["--chroot-setup-commands", setup_cmd])
        cmd.extend(["--apt-update", "--apt-distupgrade"])

    # sbuild's manual dependencies are intentionally ephemeral: they install
    # the requested interpreters without changing the source package's
    # debian/control.  PYBUILD_VERSIONS is supplied separately at execution.
    for version in config.python_versions:
        cmd.append(f"--add-depends=python{version}")

    # Extra arguments
    cmd.extend(config.extra_args)

    # Lintian configuration:
    # 1. Only fail on errors, not warnings (--fail-on error)
    #    This ensures lintian warnings don't cause the build to fail
    # 2. Suppress specific tags as configured
    # Note: Each --lintian-opts takes ONE argument which is passed to lintian
    cmd.extend(["--lintian-opts", "--fail-on=error"])
    if config.lintian_suppress_tags:
        for tag in config.lintian_suppress_tags:
            cmd.extend(["--lintian-opts", f"--suppress-tags={tag}"])

    # DSC file (must be last)
    cmd.append(str(config.dsc_path))

    return cmd


def _run_sbuild_once(config: SbuildConfig, timeout: int = 3600) -> SbuildResult:
    """Run one sbuild pass.

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
    # Ensure output directory exists
    config.output_dir.mkdir(parents=True, exist_ok=True)

    # Determine log directory (use run_log_dir if provided, otherwise output_dir)
    log_dir = config.run_log_dir or config.output_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    # Log file paths
    stdout_log = log_dir / "sbuild.stdout.log"
    stderr_log = log_dir / "sbuild.stderr.log"

    cmd = build_sbuild_command(config)
    python_versions_config = write_python_versions_sbuild_config(log_dir, config.python_versions)
    environment = None
    if python_versions_config:
        environment = os.environ.copy()
        environment["SBUILD_CONFIG"] = str(python_versions_config)
    start_time = time.time()
    start_timestamp = datetime.now(UTC).isoformat()

    try:
        # Run sbuild and capture output to files
        with (
            stdout_log.open("w", encoding="utf-8") as stdout_f,
            stderr_log.open("w", encoding="utf-8") as stderr_f,
        ):
            result = subprocess.run(
                cmd,
                cwd=config.output_dir,
                stdout=stdout_f,
                stderr=stderr_f,
                text=True,
                timeout=timeout,
                env=environment,
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

        end_timestamp = datetime.now(UTC).isoformat()

        # Generate artifact report
        report_dir = log_dir
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
            python_versions_tested=(
                list(config.python_versions)
                if overall_success and config.python_versions
                else parse_pybuild_python_versions(combined_output)
            ),
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


def _run_python_sbuild_matrix(config: SbuildConfig, timeout: int) -> SbuildResult:
    """Run an isolated sbuild pass for every explicitly configured interpreter."""
    output_dir = config.output_dir
    log_dir = config.run_log_dir or output_dir
    results: dict[str, SbuildResult] = {}

    for version in config.python_versions:
        pass_name = f"python{version}"
        pass_config = replace(
            config,
            output_dir=output_dir / pass_name,
            run_log_dir=log_dir / pass_name,
            python_versions=[version],
        )
        results[version] = _run_sbuild_once(pass_config, timeout)

    log_dir.mkdir(parents=True, exist_ok=True)
    combined_output = "".join(
        f"\n===== sbuild Python {version} pass =====\n{result.output}"
        for version, result in results.items()
    )
    stdout_log = log_dir / "sbuild.stdout.log"
    stderr_log = log_dir / "sbuild.stderr.log"
    stdout_log.write_text(combined_output, encoding="utf-8")
    stderr_log.write_text("", encoding="utf-8")

    primary_result = next(iter(results.values()))
    failed = [(version, result) for version, result in results.items() if not result.success]
    successful_versions = [
        version for version, result in results.items() if result.success
    ]
    if failed:
        validation_message = "; ".join(
            f"Python {version}: {result.validation_message or 'sbuild failed'}"
            for version, result in failed
        )
        exit_code = failed[0][1].exit_code
    else:
        validation_message = (
            "All configured Python sbuild passes succeeded: "
            f"{', '.join(successful_versions)}"
        )
        exit_code = 0

    return SbuildResult(
        success=not failed,
        output=combined_output,
        # The first configured pass supplies the publishable artifacts. Each
        # pass has a separate output directory, so later validation builds do
        # not overwrite them.
        artifacts=primary_result.artifacts,
        changes_file=primary_result.changes_file,
        chroot_name=primary_result.chroot_name,
        setup_method=primary_result.setup_method,
        local_repo_path=primary_result.local_repo_path,
        exit_code=exit_code,
        collected_artifacts=primary_result.collected_artifacts,
        collected_logs=[
            item for result in results.values() for item in result.collected_logs
        ],
        stdout_log_path=stdout_log,
        stderr_log_path=stderr_log,
        primary_log_path=stdout_log,
        searched_dirs=list(
            dict.fromkeys(
                item for result in results.values() for item in result.searched_dirs
            )
        ),
        validation_message=validation_message,
        report_path=primary_result.report_path,
        command=primary_result.command,
        python_versions_tested=successful_versions,
        python_version_results=results,
    )


def run_sbuild(config: SbuildConfig, timeout: int = 3600) -> SbuildResult:
    """Build once normally, or once per explicitly configured Python version."""
    if not is_sbuild_available():
        return SbuildResult(
            success=False,
            output="sbuild is not installed. Install with: sudo apt install sbuild",
            validation_message="sbuild not available",
        )

    if len(config.python_versions) > 1:
        return _run_python_sbuild_matrix(config, timeout)
    return _run_sbuild_once(config, timeout)


def get_default_chroot_name(distribution: str, arch: str = "amd64") -> str:
    """Get the default sbuild chroot name for a distribution.

    Args:
        distribution: Ubuntu codename (e.g., "noble", "jammy").
        arch: Architecture (e.g., "amd64").

    Returns:
        Chroot name in sbuild format.
    """
    return f"{distribution}-{arch}-sbuild"
