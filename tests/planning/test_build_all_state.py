# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for packastack.planning.build_all_state module."""

from __future__ import annotations

import json
from pathlib import Path

from packastack.planning.build_all_state import (
    BuildAllState,
    FailureType,
    MissingDependency,
    PackageState,
    PackageStatus,
    create_initial_state,
    load_state,
    save_state,
)


class TestPackageStatus:
    """Tests for PackageStatus enum."""

    def test_enum_values(self) -> None:
        """Test all status values exist."""
        assert PackageStatus.PENDING.value == "pending"
        assert PackageStatus.BUILDING.value == "building"
        assert PackageStatus.SUCCESS.value == "success"
        assert PackageStatus.FAILED.value == "failed"
        assert PackageStatus.SKIPPED.value == "skipped"
        assert PackageStatus.BLOCKED.value == "blocked"


class TestFailureType:
    """Tests for FailureType enum."""

    def test_enum_values(self) -> None:
        """Test all failure type values exist."""
        assert FailureType.FETCH_FAILED.value == "fetch_failed"
        assert FailureType.MISSING_DEP.value == "missing_dep"
        assert FailureType.PATCH_FAILED.value == "patch_failed"
        assert FailureType.BUILD_FAILED.value == "build_failed"
        assert FailureType.CYCLE.value == "cycle"
        assert FailureType.UPSTREAM_FETCH.value == "upstream_fetch"
        assert FailureType.POLICY_BLOCKED.value == "policy_blocked"
        assert FailureType.UNKNOWN.value == "unknown"


class TestPackageState:
    """Tests for PackageState dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        state = PackageState(name="nova")
        assert state.name == "nova"
        assert state.status == PackageStatus.PENDING
        assert state.failure_type is None
        assert state.failure_message == ""
        assert state.log_path == ""
        assert state.attempt == 0

    def test_to_dict(self) -> None:
        """Test serialization to dict."""
        state = PackageState(
            name="nova",
            status=PackageStatus.FAILED,
            failure_type=FailureType.BUILD_FAILED,
            failure_message="sbuild error",
            log_path="/tmp/nova.log",
            attempt=2,
        )
        d = state.to_dict()

        assert d["name"] == "nova"
        assert d["status"] == "failed"
        assert d["failure_type"] == "build_failed"
        assert d["failure_message"] == "sbuild error"
        assert d["log_path"] == "/tmp/nova.log"
        assert d["attempt"] == 2

    def test_from_dict(self) -> None:
        """Test deserialization from dict."""
        d = {
            "name": "glance",
            "status": "success",
            "failure_type": None,
            "failure_message": "",
            "log_path": "/tmp/glance.log",
            "start_time": "2025-01-01T00:00:00",
            "end_time": "2025-01-01T00:05:00",
            "duration_seconds": 300.0,
            "attempt": 1,
        }
        state = PackageState.from_dict(d)

        assert state.name == "glance"
        assert state.status == PackageStatus.SUCCESS
        assert state.failure_type is None
        assert state.duration_seconds == 300.0

    def test_from_dict_unknown_failure_type(self) -> None:
        """Test handling of unknown failure type."""
        d = {
            "name": "test",
            "status": "failed",
            "failure_type": "unknown_type",
        }
        state = PackageState.from_dict(d)
        assert state.failure_type == FailureType.UNKNOWN


class TestMissingDependency:
    """Tests for MissingDependency dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        dep = MissingDependency(binary_name="python3-foo")
        assert dep.binary_name == "python3-foo"
        assert dep.source_package is None
        assert dep.required_by == []
        assert dep.suggested_action == ""

    def test_to_dict(self) -> None:
        """Test serialization."""
        dep = MissingDependency(
            binary_name="python3-foo",
            source_package="python-foo",
            required_by=["nova", "glance"],
            suggested_action="Package needs new packaging",
        )
        d = dep.to_dict()

        assert d["binary_name"] == "python3-foo"
        assert d["source_package"] == "python-foo"
        assert d["required_by"] == ["nova", "glance"]

    def test_from_dict(self) -> None:
        """Test deserialization."""
        d = {
            "binary_name": "python3-bar",
            "source_package": "python-bar",
            "required_by": ["keystone"],
            "suggested_action": "MIR required",
        }
        dep = MissingDependency.from_dict(d)

        assert dep.binary_name == "python3-bar"
        assert dep.source_package == "python-bar"
        assert dep.required_by == ["keystone"]


class TestBuildAllState:
    """Tests for BuildAllState dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        state = BuildAllState(
            run_id="test-run",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
        )
        assert state.run_id == "test-run"
        assert state.packages == {}
        assert state.build_order == []
        assert state.missing_deps == {}
        assert state.cycles == []
        assert state.keep_going is True

    def test_get_pending_packages(self) -> None:
        """Test getting pending packages."""
        state = BuildAllState(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
        )
        state.packages["nova"] = PackageState(name="nova", status=PackageStatus.PENDING)
        state.packages["glance"] = PackageState(name="glance", status=PackageStatus.SUCCESS)
        state.packages["keystone"] = PackageState(name="keystone", status=PackageStatus.PENDING)

        pending = state.get_pending_packages()
        assert sorted(pending) == ["keystone", "nova"]

    def test_get_failed_packages(self) -> None:
        """Test getting failed packages."""
        state = BuildAllState(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
        )
        state.packages["nova"] = PackageState(name="nova", status=PackageStatus.FAILED)
        state.packages["glance"] = PackageState(name="glance", status=PackageStatus.SUCCESS)

        failed = state.get_failed_packages()
        assert failed == ["nova"]

    def test_get_success_packages(self) -> None:
        """Test getting successful packages."""
        state = BuildAllState(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
        )
        state.packages["nova"] = PackageState(name="nova", status=PackageStatus.SUCCESS)
        state.packages["glance"] = PackageState(name="glance", status=PackageStatus.FAILED)

        success = state.get_success_packages()
        assert success == ["nova"]

    def test_get_blocked_packages(self) -> None:
        """Test getting blocked packages."""
        state = BuildAllState(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
        )
        state.packages["nova"] = PackageState(name="nova", status=PackageStatus.BLOCKED)
        state.packages["glance"] = PackageState(name="glance", status=PackageStatus.SUCCESS)

        blocked = state.get_blocked_packages()
        assert blocked == ["nova"]

    def test_is_complete(self) -> None:
        """Test completion check."""
        state = BuildAllState(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
        )
        state.packages["nova"] = PackageState(name="nova", status=PackageStatus.SUCCESS)
        state.packages["glance"] = PackageState(name="glance", status=PackageStatus.FAILED)

        assert state.is_complete() is True

        state.packages["keystone"] = PackageState(name="keystone", status=PackageStatus.PENDING)
        assert state.is_complete() is False

    def test_should_stop_keep_going(self) -> None:
        """Test should_stop with keep-going mode."""
        state = BuildAllState(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
            keep_going=True,
            max_failures=0,
        )
        state.packages["nova"] = PackageState(name="nova", status=PackageStatus.FAILED)

        assert state.should_stop() is False  # No limit, keep going

    def test_should_stop_fail_fast(self) -> None:
        """Test should_stop with fail-fast mode."""
        state = BuildAllState(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
            keep_going=False,
        )
        state.packages["nova"] = PackageState(name="nova", status=PackageStatus.FAILED)

        assert state.should_stop() is True

    def test_should_stop_max_failures(self) -> None:
        """Test should_stop with max failures."""
        state = BuildAllState(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
            keep_going=True,
            max_failures=2,
        )
        state.packages["nova"] = PackageState(name="nova", status=PackageStatus.FAILED)
        assert state.should_stop() is False

        state.packages["glance"] = PackageState(name="glance", status=PackageStatus.FAILED)
        assert state.should_stop() is True

    def test_mark_started(self) -> None:
        """Test marking a package as started."""
        state = BuildAllState(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
        )
        state.packages["nova"] = PackageState(name="nova")

        state.mark_started("nova")

        assert state.packages["nova"].status == PackageStatus.BUILDING
        assert state.packages["nova"].start_time != ""
        assert state.packages["nova"].attempt == 1

    def test_mark_success(self) -> None:
        """Test marking a package as successful."""
        state = BuildAllState(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
        )
        state.packages["nova"] = PackageState(name="nova")
        state.mark_started("nova")

        state.mark_success("nova", "/tmp/nova.log")

        assert state.packages["nova"].status == PackageStatus.SUCCESS
        assert state.packages["nova"].log_path == "/tmp/nova.log"
        assert state.packages["nova"].end_time != ""

    def test_mark_failed(self) -> None:
        """Test marking a package as failed."""
        state = BuildAllState(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
        )
        state.packages["nova"] = PackageState(name="nova")
        state.mark_started("nova")

        state.mark_failed("nova", FailureType.BUILD_FAILED, "sbuild error", "/tmp/nova.log")

        assert state.packages["nova"].status == PackageStatus.FAILED
        assert state.packages["nova"].failure_type == FailureType.BUILD_FAILED
        assert state.packages["nova"].failure_message == "sbuild error"

    def test_mark_skipped(self) -> None:
        """Test marking a package as skipped."""
        state = BuildAllState(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
        )
        state.packages["nova"] = PackageState(name="nova")

        state.mark_skipped("nova", "Previously failed")

        assert state.packages["nova"].status == PackageStatus.SKIPPED
        assert state.packages["nova"].failure_message == "Previously failed"

    def test_mark_blocked(self) -> None:
        """Test marking a package as blocked."""
        state = BuildAllState(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
        )
        state.packages["nova"] = PackageState(name="nova")

        state.mark_blocked("nova", "oslo.config")

        assert state.packages["nova"].status == PackageStatus.BLOCKED
        assert "oslo.config" in state.packages["nova"].failure_message

    def test_add_missing_dep_new(self) -> None:
        """Test adding a new missing dependency."""
        state = BuildAllState(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
        )

        dep = MissingDependency(
            binary_name="python3-foo",
            required_by=["nova"],
        )
        state.add_missing_dep(dep)

        assert "python3-foo" in state.missing_deps
        assert state.missing_deps["python3-foo"].required_by == ["nova"]

    def test_add_missing_dep_merge(self) -> None:
        """Test merging required_by for existing missing dep."""
        state = BuildAllState(
            run_id="test",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
        )

        dep1 = MissingDependency(binary_name="python3-foo", required_by=["nova"])
        dep2 = MissingDependency(binary_name="python3-foo", required_by=["glance"])

        state.add_missing_dep(dep1)
        state.add_missing_dep(dep2)

        assert state.missing_deps["python3-foo"].required_by == ["nova", "glance"]

    def test_to_dict(self) -> None:
        """Test full serialization."""
        state = BuildAllState(
            run_id="test-run-123",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="snapshot",
            keep_going=True,
            max_failures=5,
            parallel=4,
        )
        state.packages["nova"] = PackageState(name="nova", status=PackageStatus.SUCCESS)
        state.build_order = ["oslo.config", "nova"]
        state.cycles = [["a", "b", "a"]]

        d = state.to_dict()

        assert d["run_id"] == "test-run-123"
        assert d["target"] == "dalmatian"
        assert d["build_type"] == "snapshot"
        assert d["parallel"] == 4
        assert "nova" in d["packages"]
        assert d["build_order"] == ["oslo.config", "nova"]
        assert d["cycles"] == [["a", "b", "a"]]

    def test_from_dict(self) -> None:
        """Test full deserialization."""
        d = {
            "run_id": "test-run-456",
            "target": "caracal",
            "ubuntu_series": "jammy",
            "build_type": "release",
            "started_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-01T01:00:00",
            "completed_at": "",
            "packages": {
                "nova": {
                    "name": "nova",
                    "status": "success",
                    "failure_type": None,
                    "failure_message": "",
                    "log_path": "",
                    "start_time": "",
                    "end_time": "",
                    "duration_seconds": 0,
                    "attempt": 1,
                }
            },
            "build_order": ["nova"],
            "missing_deps": {},
            "cycles": [],
            "total_packages": 1,
            "max_failures": 0,
            "keep_going": True,
            "parallel": 1,
        }

        state = BuildAllState.from_dict(d)

        assert state.run_id == "test-run-456"
        assert state.target == "caracal"
        assert state.ubuntu_series == "jammy"
        assert "nova" in state.packages
        assert state.packages["nova"].status == PackageStatus.SUCCESS


class TestSaveAndLoadState:
    """Tests for save_state and load_state functions."""

    def test_save_state(self, tmp_path: Path) -> None:
        """Test saving state to disk."""
        state = BuildAllState(
            run_id="test-save",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
        )
        state.packages["nova"] = PackageState(name="nova", status=PackageStatus.SUCCESS)

        state_file = save_state(state, tmp_path)

        assert state_file.exists()
        assert state_file.name == "build-all.json"

        content = json.loads(state_file.read_text())
        assert content["run_id"] == "test-save"
        assert "nova" in content["packages"]

    def test_load_state(self, tmp_path: Path) -> None:
        """Test loading state from disk."""
        state = BuildAllState(
            run_id="test-load",
            target="caracal",
            ubuntu_series="jammy",
            build_type="snapshot",
        )
        state.packages["glance"] = PackageState(name="glance", status=PackageStatus.FAILED)

        save_state(state, tmp_path)
        loaded = load_state(tmp_path)

        assert loaded is not None
        assert loaded.run_id == "test-load"
        assert loaded.target == "caracal"
        assert "glance" in loaded.packages
        assert loaded.packages["glance"].status == PackageStatus.FAILED

    def test_load_state_missing(self, tmp_path: Path) -> None:
        """Test loading from nonexistent file."""
        loaded = load_state(tmp_path)
        assert loaded is None

    def test_load_state_invalid_json(self, tmp_path: Path) -> None:
        """Test handling of invalid JSON."""
        state_file = tmp_path / "build-all.json"
        state_file.write_text("not valid json")

        loaded = load_state(tmp_path)
        assert loaded is None


class TestCreateInitialState:
    """Tests for create_initial_state function."""

    def test_creates_state(self) -> None:
        """Test creating initial state."""
        packages = ["nova", "glance", "keystone"]
        build_order = ["keystone", "glance", "nova"]

        state = create_initial_state(
            run_id="test-init",
            target="dalmatian",
            ubuntu_series="noble",
            build_type="release",
            packages=packages,
            build_order=build_order,
            max_failures=5,
            keep_going=True,
            parallel=2,
        )

        assert state.run_id == "test-init"
        assert state.target == "dalmatian"
        assert state.ubuntu_series == "noble"
        assert state.build_type == "release"
        assert state.total_packages == 3
        assert state.max_failures == 5
        assert state.keep_going is True
        assert state.parallel == 2
        assert state.build_order == build_order
        assert state.started_at != ""

        # All packages should be pending
        for pkg in packages:
            assert pkg in state.packages
            assert state.packages[pkg].status == PackageStatus.PENDING
