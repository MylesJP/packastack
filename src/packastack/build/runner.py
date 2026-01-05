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

"""Package build runner - unified execution for single and batch builds.

This module provides a common interface for building packages, whether invoked
as a single package build or as part of a batch build-all operation. The runner
handles subprocess invocation, result collection, and state management.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class BuildStatus(str, Enum):
    """Status of a package build."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class FailureType(str, Enum):
    """Classification of build failures."""

    UNKNOWN = "unknown"
    CONFIG_ERROR = "config_error"
    TOOL_MISSING = "tool_missing"
    FETCH_FAILED = "fetch_failed"
    PATCH_FAILED = "patch_failed"
    MISSING_DEP = "missing_dep"
    CYCLE = "cycle"
    BUILD_FAILED = "build_failed"
    POLICY_BLOCKED = "policy_blocked"
    REGISTRY_ERROR = "registry_error"
    RETIRED = "retired"
    TIMEOUT = "timeout"

    @classmethod
    def from_exit_code(cls, exit_code: int) -> "FailureType":
        """Map exit code to failure type."""
        mapping = {
            1: cls.CONFIG_ERROR,
            2: cls.TOOL_MISSING,
            3: cls.FETCH_FAILED,
            4: cls.PATCH_FAILED,
            5: cls.MISSING_DEP,
            6: cls.CYCLE,
            7: cls.BUILD_FAILED,
            8: cls.POLICY_BLOCKED,
            9: cls.REGISTRY_ERROR,
            10: cls.RETIRED,
        }
        return mapping.get(exit_code, cls.UNKNOWN)


@dataclass
class BuildResult:
    """Result of building a single package."""

    package: str
    status: BuildStatus
    exit_code: int = 0
    failure_type: FailureType | None = None
    failure_message: str = ""
    log_path: str = ""
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0
    artifacts: list[str] = field(default_factory=list)
    build_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "package": self.package,
            "status": self.status.value,
            "exit_code": self.exit_code,
            "failure_type": self.failure_type.value if self.failure_type else None,
            "failure_message": self.failure_message,
            "log_path": self.log_path,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "artifacts": self.artifacts,
            "build_type": self.build_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BuildResult":
        """Create from dictionary."""
        return cls(
            package=data["package"],
            status=BuildStatus(data["status"]),
            exit_code=data.get("exit_code", 0),
            failure_type=FailureType(data["failure_type"]) if data.get("failure_type") else None,
            failure_message=data.get("failure_message", ""),
            log_path=data.get("log_path", ""),
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at", ""),
            duration_seconds=data.get("duration_seconds", 0.0),
            artifacts=data.get("artifacts", []),
            build_type=data.get("build_type", ""),
        )


@dataclass
class BuildState:
    """Minimal state for tracking build progress.

    Used by both single-package and build-all modes for consistency.
    Enables resume capability and structured result reporting.
    """

    run_id: str
    target: str
    ubuntu_series: str
    build_type: str
    packages: dict[str, BuildResult] = field(default_factory=dict)
    build_order: list[str] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""
    keep_going: bool = True
    max_failures: int = 0
    parallel: int = 1
    cycles: list[list[str]] = field(default_factory=list)

    @property
    def total_packages(self) -> int:
        """Total number of packages in build order."""
        return len(self.build_order)

    def get_pending(self) -> list[str]:
        """Get packages that haven't been built yet."""
        return [
            pkg for pkg in self.build_order
            if self.packages.get(pkg, BuildResult(pkg, BuildStatus.PENDING)).status == BuildStatus.PENDING
        ]

    def get_succeeded(self) -> list[str]:
        """Get packages that built successfully."""
        return [
            pkg for pkg, result in self.packages.items()
            if result.status == BuildStatus.SUCCESS
        ]

    def get_failed(self) -> list[str]:
        """Get packages that failed to build."""
        return [
            pkg for pkg, result in self.packages.items()
            if result.status == BuildStatus.FAILED
        ]

    def get_blocked(self) -> list[str]:
        """Get packages blocked by failed dependencies."""
        return [
            pkg for pkg, result in self.packages.items()
            if result.status == BuildStatus.BLOCKED
        ]

    def mark_started(self, package: str) -> None:
        """Mark a package as started."""
        if package not in self.packages:
            self.packages[package] = BuildResult(package, BuildStatus.PENDING)
        self.packages[package].status = BuildStatus.RUNNING
        self.packages[package].started_at = datetime.now(timezone.utc).isoformat()

    def mark_complete(self, result: BuildResult) -> None:
        """Record a completed build result."""
        self.packages[result.package] = result

    def mark_blocked(self, package: str, reason: str = "") -> None:
        """Mark a package as blocked."""
        if package not in self.packages:
            self.packages[package] = BuildResult(package, BuildStatus.PENDING)
        self.packages[package].status = BuildStatus.BLOCKED
        self.packages[package].failure_message = reason or "Blocked by failed dependency"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "run_id": self.run_id,
            "target": self.target,
            "ubuntu_series": self.ubuntu_series,
            "build_type": self.build_type,
            "packages": {pkg: result.to_dict() for pkg, result in self.packages.items()},
            "build_order": self.build_order,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "keep_going": self.keep_going,
            "max_failures": self.max_failures,
            "parallel": self.parallel,
            "cycles": self.cycles,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BuildState":
        """Create from dictionary."""
        state = cls(
            run_id=data["run_id"],
            target=data["target"],
            ubuntu_series=data["ubuntu_series"],
            build_type=data["build_type"],
            build_order=data.get("build_order", []),
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at", ""),
            keep_going=data.get("keep_going", True),
            max_failures=data.get("max_failures", 0),
            parallel=data.get("parallel", 1),
            cycles=data.get("cycles", []),
        )
        for pkg, result_data in data.get("packages", {}).items():
            state.packages[pkg] = BuildResult.from_dict(result_data)
        return state


def save_build_state(state: BuildState, state_dir: Path) -> None:
    """Save build state to disk."""
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "build-state.json"
    state_file.write_text(json.dumps(state.to_dict(), indent=2))


def load_build_state(state_dir: Path) -> BuildState | None:
    """Load build state from disk."""
    state_file = state_dir / "build-state.json"
    if not state_file.exists():
        return None
    try:
        data = json.loads(state_file.read_text())
        return BuildState.from_dict(data)
    except (json.JSONDecodeError, KeyError):
        return None


def create_build_state(
    run_id: str,
    target: str,
    ubuntu_series: str,
    build_type: str,
    build_order: list[str],
    keep_going: bool = True,
    max_failures: int = 0,
    parallel: int = 1,
) -> BuildState:
    """Create initial build state."""
    state = BuildState(
        run_id=run_id,
        target=target,
        ubuntu_series=ubuntu_series,
        build_type=build_type,
        build_order=build_order,
        started_at=datetime.now(timezone.utc).isoformat(),
        keep_going=keep_going,
        max_failures=max_failures,
        parallel=parallel,
    )
    # Initialize all packages as pending
    for pkg in build_order:
        state.packages[pkg] = BuildResult(pkg, BuildStatus.PENDING)
    return state


@dataclass
class RunnerConfig:
    """Configuration for PackageBuildRunner."""

    target: str
    ubuntu_series: str
    cloud_archive: str = ""
    build_type: str = "auto"
    milestone: str = ""
    binary: bool = True
    force: bool = False
    timeout: int = 3600  # 1 hour default
    skip_repo_regen: bool = True  # Coordinator regenerates per-batch


class PackageBuildRunner:
    """Runs package builds as subprocesses with structured result collection.

    This class provides a unified interface for building packages, whether
    invoked for a single package or as part of a batch. All builds are
    executed as subprocesses for isolation.
    """

    def __init__(self, config: RunnerConfig, run_dir: Path):
        """Initialize the runner.

        Args:
            config: Build configuration.
            run_dir: Directory for logs and state.
        """
        self.config = config
        self.run_dir = run_dir
        self.logs_dir = run_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def build_package(self, package: str) -> BuildResult:
        """Build a single package as a subprocess.

        Args:
            package: Package name to build.

        Returns:
            BuildResult with status, exit code, and metadata.
        """
        started_at = datetime.now(timezone.utc)

        # Build command
        cmd = self._build_command(package)

        # Setup environment
        env = os.environ.copy()
        env["PACKASTACK_BUILD_DEPTH"] = "10"  # Prevent infinite recursion
        env["PACKASTACK_NO_GPG_SIGN"] = "1"  # Don't require GPG signing

        # Setup logging
        log_dir = self.logs_dir / package
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "build.log"

        try:
            with log_file.open("w") as f:
                result = subprocess.run(
                    cmd,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=self.config.timeout,
                )

            completed_at = datetime.now(timezone.utc)
            duration = (completed_at - started_at).total_seconds()

            if result.returncode == 0:
                return BuildResult(
                    package=package,
                    status=BuildStatus.SUCCESS,
                    exit_code=0,
                    log_path=str(log_file),
                    started_at=started_at.isoformat(),
                    completed_at=completed_at.isoformat(),
                    duration_seconds=duration,
                    build_type=self.config.build_type,
                )
            else:
                return BuildResult(
                    package=package,
                    status=BuildStatus.FAILED,
                    exit_code=result.returncode,
                    failure_type=FailureType.from_exit_code(result.returncode),
                    failure_message=f"Build failed with exit code {result.returncode}",
                    log_path=str(log_file),
                    started_at=started_at.isoformat(),
                    completed_at=completed_at.isoformat(),
                    duration_seconds=duration,
                    build_type=self.config.build_type,
                )

        except subprocess.TimeoutExpired:
            completed_at = datetime.now(timezone.utc)
            duration = (completed_at - started_at).total_seconds()
            return BuildResult(
                package=package,
                status=BuildStatus.FAILED,
                exit_code=-1,
                failure_type=FailureType.TIMEOUT,
                failure_message=f"Build timed out after {self.config.timeout}s",
                log_path=str(log_file),
                started_at=started_at.isoformat(),
                completed_at=completed_at.isoformat(),
                duration_seconds=duration,
                build_type=self.config.build_type,
            )

        except Exception as e:
            completed_at = datetime.now(timezone.utc)
            duration = (completed_at - started_at).total_seconds()
            return BuildResult(
                package=package,
                status=BuildStatus.FAILED,
                exit_code=-1,
                failure_type=FailureType.UNKNOWN,
                failure_message=str(e),
                log_path=str(log_file),
                started_at=started_at.isoformat(),
                completed_at=completed_at.isoformat(),
                duration_seconds=duration,
                build_type=self.config.build_type,
            )

    def _build_command(self, package: str) -> list[str]:
        """Build the subprocess command for a package."""
        cmd = [
            sys.executable, "-m", "packastack", "build",
            package,
            "--target", self.config.target,
            "--ubuntu-series", self.config.ubuntu_series,
            "--yes",  # No prompts
            "--no-cleanup",  # Keep workspace for debugging
        ]

        if self.config.cloud_archive:
            cmd.extend(["--cloud-archive", self.config.cloud_archive])

        # Pass build type
        if self.config.build_type == "auto":
            cmd.extend(["--type", "auto"])
        elif self.config.build_type == "snapshot":
            cmd.extend(["--type", "snapshot"])
        elif self.config.build_type == "milestone":
            cmd.extend(["--type", "milestone"])
            if self.config.milestone:
                cmd.extend(["--milestone", self.config.milestone])
        elif self.config.build_type == "release":
            cmd.extend(["--type", "release"])

        if self.config.binary:
            cmd.append("--binary")
        else:
            cmd.append("--no-binary")

        if self.config.force:
            cmd.append("--force")

        if self.config.skip_repo_regen:
            cmd.append("--skip-repo-regen")

        return cmd
