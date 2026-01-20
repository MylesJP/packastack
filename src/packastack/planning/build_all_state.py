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

"""State management for build-all mode.

Provides persistence for build-all runs to enable resume capability.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any


def _utcnow_iso() -> str:
    """Get current UTC time as ISO format string."""
    return datetime.now(UTC).isoformat()


class PackageStatus(str, Enum):
    """Status of a package in build-all."""

    PENDING = "pending"
    BUILDING = "building"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"  # Blocked by failed dependency


class FailureType(str, Enum):
    """Classification of build failures."""

    FETCH_FAILED = "fetch_failed"
    MISSING_DEP = "missing_dep"
    PATCH_FAILED = "patch_failed"
    BUILD_FAILED = "build_failed"
    CYCLE = "cycle"
    UPSTREAM_FETCH = "upstream_fetch"
    POLICY_BLOCKED = "policy_blocked"
    UNKNOWN = "unknown"


@dataclass
class PackageState:
    """State of a single package in build-all."""

    name: str
    status: PackageStatus = PackageStatus.PENDING
    failure_type: FailureType | None = None
    failure_message: str = ""
    log_path: str = ""
    start_time: str = ""
    end_time: str = ""
    duration_seconds: float = 0.0
    attempt: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "status": self.status.value,
            "failure_type": self.failure_type.value if self.failure_type else None,
            "failure_message": self.failure_message,
            "log_path": self.log_path,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": self.duration_seconds,
            "attempt": self.attempt,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PackageState:
        """Create from dictionary."""
        failure_type = None
        if data.get("failure_type"):
            try:
                failure_type = FailureType(data["failure_type"])
            except ValueError:
                failure_type = FailureType.UNKNOWN

        return cls(
            name=data["name"],
            status=PackageStatus(data.get("status", "pending")),
            failure_type=failure_type,
            failure_message=data.get("failure_message", ""),
            log_path=data.get("log_path", ""),
            start_time=data.get("start_time", ""),
            end_time=data.get("end_time", ""),
            duration_seconds=data.get("duration_seconds", 0.0),
            attempt=data.get("attempt", 0),
        )


@dataclass
class MissingDependency:
    """A missing dependency encountered during build-all."""

    binary_name: str
    """The binary package name that is missing."""

    source_package: str | None = None
    """The source package that provides it, if known."""

    required_by: list[str] = field(default_factory=list)
    """Packages that require this dependency."""

    suggested_action: str = ""
    """Suggested action to resolve."""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "binary_name": self.binary_name,
            "source_package": self.source_package,
            "required_by": self.required_by,
            "suggested_action": self.suggested_action,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MissingDependency:
        """Create from dictionary."""
        return cls(
            binary_name=data["binary_name"],
            source_package=data.get("source_package"),
            required_by=data.get("required_by", []),
            suggested_action=data.get("suggested_action", ""),
        )


@dataclass
class BuildAllState:
    """Complete state of a build-all run."""

    run_id: str
    """Unique identifier for this run."""

    target: str
    """OpenStack target series."""

    ubuntu_series: str
    """Ubuntu series."""

    build_type: str
    """Build type: release or snapshot."""

    started_at: str = ""
    """ISO timestamp of run start."""

    updated_at: str = ""
    """ISO timestamp of last update."""

    completed_at: str = ""
    """ISO timestamp of completion (empty if in progress)."""

    packages: dict[str, PackageState] = field(default_factory=dict)
    """Per-package state."""

    build_order: list[str] = field(default_factory=list)
    """Ordered list of packages to build."""

    missing_deps: dict[str, MissingDependency] = field(default_factory=dict)
    """Missing dependencies discovered."""

    cycles: list[list[str]] = field(default_factory=list)
    """Dependency cycles detected."""

    total_packages: int = 0
    """Total packages in the run."""

    max_failures: int = 0
    """Maximum failures allowed (0 = unlimited)."""

    keep_going: bool = True
    """Continue on failure."""

    parallel: int = 1
    """Number of parallel builds."""

    def get_pending_packages(self) -> list[str]:
        """Get packages that are pending build."""
        return [
            name for name, state in self.packages.items()
            if state.status == PackageStatus.PENDING
        ]

    def get_failed_packages(self) -> list[str]:
        """Get packages that failed."""
        return [
            name for name, state in self.packages.items()
            if state.status == PackageStatus.FAILED
        ]

    def get_success_packages(self) -> list[str]:
        """Get packages that succeeded."""
        return [
            name for name, state in self.packages.items()
            if state.status == PackageStatus.SUCCESS
        ]

    def get_blocked_packages(self) -> list[str]:
        """Get packages blocked by failed dependencies."""
        return [
            name for name, state in self.packages.items()
            if state.status == PackageStatus.BLOCKED
        ]

    def get_failure_count(self) -> int:
        """Get count of failed packages."""
        return sum(
            1 for state in self.packages.values()
            if state.status == PackageStatus.FAILED
        )

    def is_complete(self) -> bool:
        """Check if all packages are processed."""
        return all(
            state.status in (PackageStatus.SUCCESS, PackageStatus.FAILED, PackageStatus.SKIPPED, PackageStatus.BLOCKED)
            for state in self.packages.values()
        )

    def should_stop(self) -> bool:
        """Check if we should stop due to failure policy."""
        if not self.keep_going:
            return self.get_failure_count() > 0
        if self.max_failures > 0:
            return self.get_failure_count() >= self.max_failures
        return False

    def mark_started(self, package: str) -> None:
        """Mark a package as started."""
        if package in self.packages:
            self.packages[package].status = PackageStatus.BUILDING
            self.packages[package].start_time = _utcnow_iso()
            self.packages[package].attempt += 1
        self.updated_at = _utcnow_iso()

    def mark_success(self, package: str, log_path: str = "") -> None:
        """Mark a package as successfully built."""
        if package in self.packages:
            state = self.packages[package]
            state.status = PackageStatus.SUCCESS
            state.end_time = _utcnow_iso()
            state.log_path = log_path
            if state.start_time:
                start = datetime.fromisoformat(state.start_time)
                end = datetime.fromisoformat(state.end_time)
                state.duration_seconds = (end - start).total_seconds()
        self.updated_at = _utcnow_iso()

    def mark_failed(
        self,
        package: str,
        failure_type: FailureType,
        message: str = "",
        log_path: str = "",
    ) -> None:
        """Mark a package as failed."""
        if package in self.packages:
            state = self.packages[package]
            state.status = PackageStatus.FAILED
            state.failure_type = failure_type
            state.failure_message = message
            state.end_time = _utcnow_iso()
            state.log_path = log_path
            if state.start_time:
                start = datetime.fromisoformat(state.start_time)
                end = datetime.fromisoformat(state.end_time)
                state.duration_seconds = (end - start).total_seconds()
        self.updated_at = _utcnow_iso()

    def mark_skipped(self, package: str, reason: str = "") -> None:
        """Mark a package as skipped."""
        if package in self.packages:
            self.packages[package].status = PackageStatus.SKIPPED
            self.packages[package].failure_message = reason
        self.updated_at = _utcnow_iso()

    def mark_blocked(self, package: str, blocked_by: str) -> None:
        """Mark a package as blocked by a failed dependency."""
        if package in self.packages:
            self.packages[package].status = PackageStatus.BLOCKED
            self.packages[package].failure_message = f"blocked by failed: {blocked_by}"
        self.updated_at = _utcnow_iso()

    def add_missing_dep(self, dep: MissingDependency) -> None:
        """Add a missing dependency."""
        if dep.binary_name in self.missing_deps:
            # Merge required_by lists
            existing = self.missing_deps[dep.binary_name]
            for pkg in dep.required_by:
                if pkg not in existing.required_by:
                    existing.required_by.append(pkg)
        else:
            self.missing_deps[dep.binary_name] = dep

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "run_id": self.run_id,
            "target": self.target,
            "ubuntu_series": self.ubuntu_series,
            "build_type": self.build_type,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "packages": {name: state.to_dict() for name, state in self.packages.items()},
            "build_order": self.build_order,
            "missing_deps": {name: dep.to_dict() for name, dep in self.missing_deps.items()},
            "cycles": self.cycles,
            "total_packages": self.total_packages,
            "max_failures": self.max_failures,
            "keep_going": self.keep_going,
            "parallel": self.parallel,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BuildAllState:
        """Create from dictionary."""
        state = cls(
            run_id=data["run_id"],
            target=data["target"],
            ubuntu_series=data["ubuntu_series"],
            build_type=data.get("build_type", "release"),
            started_at=data.get("started_at", ""),
            updated_at=data.get("updated_at", ""),
            completed_at=data.get("completed_at", ""),
            build_order=data.get("build_order", []),
            cycles=data.get("cycles", []),
            total_packages=data.get("total_packages", 0),
            max_failures=data.get("max_failures", 0),
            keep_going=data.get("keep_going", True),
            parallel=data.get("parallel", 1),
        )

        for name, pkg_data in data.get("packages", {}).items():
            state.packages[name] = PackageState.from_dict(pkg_data)

        for name, dep_data in data.get("missing_deps", {}).items():
            state.missing_deps[name] = MissingDependency.from_dict(dep_data)

        return state


def save_state(state: BuildAllState, state_dir: Path) -> Path:
    """Save build-all state to disk.

    Args:
        state: The state to save.
        state_dir: Directory to save state in.

    Returns:
        Path to the saved state file.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "build-all.json"
    state_file.write_text(json.dumps(state.to_dict(), indent=2))
    return state_file


def load_state(state_dir: Path) -> BuildAllState | None:
    """Load build-all state from disk.

    Args:
        state_dir: Directory containing state file.

    Returns:
        Loaded state, or None if not found.
    """
    state_file = state_dir / "build-all.json"
    if not state_file.exists():
        return None

    try:
        data = json.loads(state_file.read_text())
        return BuildAllState.from_dict(data)
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def create_initial_state(
    run_id: str,
    target: str,
    ubuntu_series: str,
    build_type: str,
    packages: list[str],
    build_order: list[str],
    max_failures: int = 0,
    keep_going: bool = True,
    parallel: int = 1,
) -> BuildAllState:
    """Create initial state for a new build-all run.

    Args:
        run_id: Unique run identifier.
        target: OpenStack target series.
        ubuntu_series: Ubuntu series.
        build_type: Build type (release/snapshot).
        packages: All packages to build.
        build_order: Topologically sorted build order.
        max_failures: Maximum failures before stopping.
        keep_going: Continue on failure.
        parallel: Number of parallel builds.

    Returns:
        New BuildAllState.
    """
    state = BuildAllState(
        run_id=run_id,
        target=target,
        ubuntu_series=ubuntu_series,
        build_type=build_type,
        started_at=_utcnow_iso(),
        updated_at=_utcnow_iso(),
        build_order=build_order,
        total_packages=len(packages),
        max_failures=max_failures,
        keep_going=keep_going,
        parallel=parallel,
    )

    for pkg in packages:
        state.packages[pkg] = PackageState(name=pkg)

    return state
