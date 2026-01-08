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

"""Tests for packastack.build.sbuild module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from packastack.build.collector import CollectionResult

from packastack.build.sbuild import (
    CHROOT_REPO_MOUNT,
    CHROOT_SOURCES_LIST,
    SbuildConfig,
    SbuildResult,
    build_sbuild_command,
    generate_chroot_cleanup_commands,
    generate_chroot_setup_commands,
    get_default_chroot_name,
    run_sbuild,
)


class TestSbuildResult:
    """Tests for SbuildResult dataclass."""

    def test_success_result(self) -> None:
        """Test successful result."""
        result = SbuildResult(success=True, output="build log")
        assert result.success is True
        assert result.output == "build log"
        assert result.artifacts == []
        assert result.changes_file is None

    def test_failure_result(self) -> None:
        """Test failure result."""
        result = SbuildResult(success=False, output="error message")
        assert result.success is False
        assert result.output == "error message"

    def test_with_artifacts(self) -> None:
        """Test result with artifacts."""
        artifacts = [Path("/tmp/pkg.deb"), Path("/tmp/pkg.changes")]
        result = SbuildResult(
            success=True,
            artifacts=artifacts,
            changes_file=artifacts[1],
        )
        assert len(result.artifacts) == 2
        assert result.changes_file == artifacts[1]


class TestSbuildConfig:
    """Tests for SbuildConfig dataclass."""

    def test_basic_config(self, tmp_path: Path) -> None:
        """Test basic config with required fields."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path / "output",
            distribution="noble",
        )
        assert config.dsc_path == tmp_path / "pkg.dsc"
        assert config.distribution == "noble"
        assert config.arch == "amd64"

    def test_full_config(self, tmp_path: Path) -> None:
        """Test full config with all fields."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path / "output",
            distribution="jammy",
            arch="arm64",
            local_repo_root=tmp_path / "repo",
            chroot_name="jammy-arm64-sbuild",
            extra_args=["--verbose"],
        )
        assert config.arch == "arm64"
        assert config.chroot_name == "jammy-arm64-sbuild"
        assert config.extra_args == ["--verbose"]


class TestGenerateChrootSetupCommands:
    """Tests for generate_chroot_setup_commands function."""

    def test_generates_mount_command(self, tmp_path: Path) -> None:
        """Test that mount command is generated."""
        cmds = generate_chroot_setup_commands(tmp_path)
        mount_cmd = [c for c in cmds if "mount --bind" in c]
        assert len(mount_cmd) == 1
        assert str(tmp_path) in mount_cmd[0]
        assert CHROOT_REPO_MOUNT in mount_cmd[0]

    def test_generates_mkdir_command(self, tmp_path: Path) -> None:
        """Test that mkdir command is generated."""
        cmds = generate_chroot_setup_commands(tmp_path)
        mkdir_cmd = [c for c in cmds if c.startswith("mkdir")]
        assert len(mkdir_cmd) == 1
        assert CHROOT_REPO_MOUNT in mkdir_cmd[0]

    def test_generates_sources_list_command(self, tmp_path: Path) -> None:
        """Test that sources list command is generated."""
        cmds = generate_chroot_setup_commands(tmp_path)
        sources_cmd = [c for c in cmds if "echo" in c and "deb" in c]
        assert len(sources_cmd) == 1
        assert "[trusted=yes]" in sources_cmd[0]
        assert CHROOT_SOURCES_LIST in sources_cmd[0]

    def test_generates_apt_update_command(self, tmp_path: Path) -> None:
        """Test that apt-get update command is generated."""
        cmds = generate_chroot_setup_commands(tmp_path)
        update_cmd = [c for c in cmds if "apt-get update" in c]
        assert len(update_cmd) == 1


class TestGenerateChrootCleanupCommands:
    """Tests for generate_chroot_cleanup_commands function."""

    def test_generates_rm_command(self) -> None:
        """Test that rm command is generated."""
        cmds = generate_chroot_cleanup_commands()
        rm_cmd = [c for c in cmds if "rm" in c]
        assert len(rm_cmd) == 1
        assert CHROOT_SOURCES_LIST in rm_cmd[0]

    def test_generates_umount_command(self) -> None:
        """Test that umount command is generated."""
        cmds = generate_chroot_cleanup_commands()
        umount_cmd = [c for c in cmds if "umount" in c]
        assert len(umount_cmd) == 1
        assert CHROOT_REPO_MOUNT in umount_cmd[0]


class TestBuildSbuildCommand:
    """Tests for build_sbuild_command function."""

    def test_basic_command(self, tmp_path: Path) -> None:
        """Test basic sbuild command."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path,
            distribution="noble",
        )
        cmd = build_sbuild_command(config)
        assert cmd[0] == "sbuild"
        # Note: --nolog is no longer used so sbuild creates log files
        assert "-d" in cmd
        assert "noble" in cmd
        assert str(tmp_path / "pkg.dsc") in cmd

    def test_with_arch(self, tmp_path: Path) -> None:
        """Test command with architecture."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path,
            distribution="noble",
            arch="arm64",
        )
        cmd = build_sbuild_command(config)
        assert "--arch" in cmd
        assert "arm64" in cmd

    def test_with_chroot_name(self, tmp_path: Path) -> None:
        """Test command with chroot name."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path,
            distribution="noble",
            chroot_name="noble-amd64-sbuild",
        )
        cmd = build_sbuild_command(config)
        assert "-c" in cmd
        assert "noble-amd64-sbuild" in cmd

    def test_with_local_repo(self, tmp_path: Path) -> None:
        """Test command with local repo setup."""
        repo = tmp_path / "repo"
        repo.mkdir()
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path,
            distribution="noble",
            local_repo_root=repo,
        )
        cmd = build_sbuild_command(config)
        assert "--chroot-setup-commands" in cmd
        assert "--finished-build-commands" in cmd

    def test_with_extra_args(self, tmp_path: Path) -> None:
        """Test command with extra arguments."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path,
            distribution="noble",
            extra_args=["--verbose", "--source"],
        )
        cmd = build_sbuild_command(config)
        assert "--verbose" in cmd
        assert "--source" in cmd

    def test_dsc_is_last(self, tmp_path: Path) -> None:
        """Test that dsc file is last argument."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path,
            distribution="noble",
        )
        cmd = build_sbuild_command(config)
        assert cmd[-1] == str(tmp_path / "pkg.dsc")

    def test_lintian_fail_on_error_only(self, tmp_path: Path) -> None:
        """Test that lintian is configured to fail only on errors, not warnings."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path,
            distribution="noble",
        )
        cmd = build_sbuild_command(config)
        # Check that --fail-on error is passed to lintian
        # This ensures warnings don't fail the build
        assert "--lintian-opts" in cmd
        lintian_opts_indices = [i for i, x in enumerate(cmd) if x == "--lintian-opts"]
        # There should be at least 2 --lintian-opts: one for --fail-on, one for error
        assert len(lintian_opts_indices) >= 2
        # Verify --fail-on is followed by error
        for i, idx in enumerate(lintian_opts_indices[:-1]):
            if cmd[idx + 1] == "--fail-on":
                assert cmd[lintian_opts_indices[i + 1] + 1] == "error"
                break
        else:
            # If we didn't find it in the loop, fail
            assert "--fail-on" in cmd, "Expected --fail-on in lintian options"


class TestGetDefaultChrootName:
    """Tests for get_default_chroot_name function."""

    def test_default_arch(self) -> None:
        """Test with default architecture."""
        name = get_default_chroot_name("noble")
        assert name == "noble-amd64-sbuild"

    def test_custom_arch(self) -> None:
        """Test with custom architecture."""
        name = get_default_chroot_name("jammy", "arm64")
        assert name == "jammy-arm64-sbuild"


class TestSbuildResultEnhanced:
    """Tests for enhanced SbuildResult fields."""

    def test_result_with_log_paths(self, tmp_path: Path) -> None:
        """Test result with log file paths."""
        result = SbuildResult(
            success=True,
            stdout_log_path=tmp_path / "sbuild.stdout.log",
            stderr_log_path=tmp_path / "sbuild.stderr.log",
            primary_log_path=tmp_path / "sbuild.log",
        )
        assert result.stdout_log_path == tmp_path / "sbuild.stdout.log"
        assert result.stderr_log_path == tmp_path / "sbuild.stderr.log"
        assert result.primary_log_path == tmp_path / "sbuild.log"

    def test_result_with_exit_code(self) -> None:
        """Test result with exit code."""
        result = SbuildResult(success=False, exit_code=1)
        assert result.exit_code == 1

    def test_result_with_searched_dirs(self) -> None:
        """Test result with searched directories."""
        result = SbuildResult(
            success=True,
            searched_dirs=["/tmp/build", "/var/lib/sbuild"],
        )
        assert len(result.searched_dirs) == 2

    def test_result_with_validation_message(self) -> None:
        """Test result with validation message."""
        result = SbuildResult(
            success=False,
            validation_message="No binary packages found",
        )
        assert "No binary packages" in result.validation_message

    def test_result_with_command(self) -> None:
        """Test result with sbuild command."""
        result = SbuildResult(
            success=True,
            command=["sbuild", "-d", "noble", "pkg.dsc"],
        )
        assert result.command[0] == "sbuild"
        assert "noble" in result.command


class TestSbuildConfigEnhanced:
    """Tests for enhanced SbuildConfig fields."""

    def test_config_with_run_log_dir(self, tmp_path: Path) -> None:
        """Test config with run log directory."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path / "output",
            distribution="noble",
            run_log_dir=tmp_path / "logs",
        )
        assert config.run_log_dir == tmp_path / "logs"

    def test_config_with_source_package(self, tmp_path: Path) -> None:
        """Test config with source package name."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path / "output",
            distribution="noble",
            source_package="python-nova",
            version="1.2.3-1ubuntu1",
        )
        assert config.source_package == "python-nova"
        assert config.version == "1.2.3-1ubuntu1"


class TestRunSbuild:
    """Tests for run_sbuild function with mocking."""

    def test_sbuild_not_available(self, tmp_path: Path) -> None:
        """Should return failure when sbuild is not installed."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path / "output",
            distribution="noble",
        )
        
        with patch("packastack.build.sbuild.is_sbuild_available", return_value=False):
            result = run_sbuild(config)
        
        assert not result.success
        assert "not installed" in result.output

    def test_captures_stdout_stderr_to_files(self, tmp_path: Path) -> None:
        """Should capture stdout/stderr to log files."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path / "output",
            distribution="noble",
            run_log_dir=tmp_path / "logs",
        )
        
        # Create a mock that simulates sbuild writing to stdout/stderr
        mock_result = MagicMock()
        mock_result.returncode = 0
        
        with patch("packastack.build.sbuild.is_sbuild_available", return_value=True), \
             patch("packastack.build.sbuild.subprocess.run", return_value=mock_result), \
             patch("packastack.build.sbuild.discover_candidate_directories") as mock_discover, \
             patch("packastack.build.sbuild.collect_artifacts") as mock_collect:
            
            # Setup mock candidates
            from packastack.build.sbuildrc import CandidateDirectories
            mock_discover.return_value = CandidateDirectories()
            
            # Setup mock collection with no binaries (to test the failure path)
            from packastack.build.collector import CollectionResult
            mock_collect.return_value = CollectionResult(
                success=False,
                validation_message="No binary packages found",
            )
            
            result = run_sbuild(config)
        
        # Log files should be created
        assert result.stdout_log_path is not None
        assert result.stderr_log_path is not None
        assert result.stdout_log_path.exists()
        assert result.stderr_log_path.exists()

    def test_collects_artifacts_from_user_config_dir(self, tmp_path: Path) -> None:
        """Should collect artifacts from user-configured build directory."""
        user_build_dir = tmp_path / "user_build"
        user_build_dir.mkdir()
        (user_build_dir / "pkg_1.0_amd64.deb").write_text("deb content")
        
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path / "output",
            distribution="noble",
            run_log_dir=tmp_path / "logs",
            source_package="pkg",
        )
        
        mock_result = MagicMock()
        mock_result.returncode = 0
        
        with patch("packastack.build.sbuild.is_sbuild_available", return_value=True), \
             patch("packastack.build.sbuild.subprocess.run", return_value=mock_result), \
             patch("packastack.build.sbuild.discover_candidate_directories") as mock_discover:
            
            from packastack.build.sbuildrc import CandidateDirectories
            candidates = CandidateDirectories()
            candidates.add_build_dir(user_build_dir, "~/.sbuildrc")
            mock_discover.return_value = candidates
            
            result = run_sbuild(config)
        
        # Should find and collect the deb from user build dir
        assert result.success
        assert len(result.collected_artifacts) >= 1
        # primary log may not always be present; if it is, it should be named
        # with the source package to avoid being overwritten by subsequent builds
        if result.primary_log_path:
            assert result.primary_log_path.name == "pkg-sbuild.log"

    def test_primary_log_named_with_source_when_logs_present(self, tmp_path: Path) -> None:
        """Primary log should be copied with <source>-sbuild.log when logs exist."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path / "output",
            distribution="noble",
            run_log_dir=tmp_path / "logs",
            source_package="pkg",
        )

        # create a real log file that will be returned as a collected log
        real_log = tmp_path / "logs" / "build.log"
        real_log.parent.mkdir(parents=True, exist_ok=True)
        real_log.write_text("log content")

        collected = CollectionResult(success=True)
        from packastack.build.collector import CollectedFile

        collected.logs.append(CollectedFile(real_log, real_log, "hash", real_log.stat().st_size, real_log.stat().st_mtime))

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("packastack.build.sbuild.is_sbuild_available", return_value=True), \
             patch("packastack.build.sbuild.subprocess.run", return_value=mock_result), \
             patch("packastack.build.sbuild.discover_candidate_directories") as mock_discover, \
             patch("packastack.build.sbuild.collect_artifacts", return_value=collected):

            from packastack.build.sbuildrc import CandidateDirectories
            mock_discover.return_value = CandidateDirectories()

            result = run_sbuild(config)

        assert result.primary_log_path is not None
        assert result.primary_log_path.name == "pkg-sbuild.log"
        assert result.primary_log_path.read_text() == "log content"

    def test_primary_log_derived_from_dsc_when_no_source_package(self, tmp_path: Path) -> None:
        """When source_package is not set, derive name from DSC prefix."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg_1.0.dsc",
            output_dir=tmp_path / "output",
            distribution="noble",
            run_log_dir=tmp_path / "logs",
            source_package=None,
        )

        real_log = tmp_path / "logs" / "build.log"
        real_log.parent.mkdir(parents=True, exist_ok=True)
        real_log.write_text("log content")

        collected = CollectionResult(success=True)
        from packastack.build.collector import CollectedFile

        collected.logs.append(
            CollectedFile(real_log, real_log, "hash", real_log.stat().st_size, real_log.stat().st_mtime)
        )

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("packastack.build.sbuild.is_sbuild_available", return_value=True), \
             patch("packastack.build.sbuild.subprocess.run", return_value=mock_result), \
             patch("packastack.build.sbuild.discover_candidate_directories") as mock_discover, \
             patch("packastack.build.sbuild.collect_artifacts", return_value=collected):

            from packastack.build.sbuildrc import CandidateDirectories
            mock_discover.return_value = CandidateDirectories()

            result = run_sbuild(config)

        assert result.primary_log_path is not None
        assert result.primary_log_path.name == "pkg-sbuild.log"
        assert result.primary_log_path.read_text() == "log content"

    def test_fails_when_no_binaries_found(self, tmp_path: Path) -> None:
        """Should fail when sbuild succeeds but no binaries are found."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path / "output",
            distribution="noble",
            run_log_dir=tmp_path / "logs",
        )
        
        mock_result = MagicMock()
        mock_result.returncode = 0  # sbuild succeeds
        
        with patch("packastack.build.sbuild.is_sbuild_available", return_value=True), \
             patch("packastack.build.sbuild.subprocess.run", return_value=mock_result), \
             patch("packastack.build.sbuild.discover_candidate_directories") as mock_discover:
            
            from packastack.build.sbuildrc import CandidateDirectories
            mock_discover.return_value = CandidateDirectories()
            
            result = run_sbuild(config)
        
        # Should fail validation even though sbuild exited 0
        assert not result.success
        assert "No binary packages" in result.validation_message

    def test_writes_artifact_report(self, tmp_path: Path) -> None:
        """Should write artifact report JSON file."""
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "pkg_1.0_amd64.deb").write_text("deb")
        
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=build_dir,
            distribution="noble",
            run_log_dir=tmp_path / "logs",
        )
        
        mock_result = MagicMock()
        mock_result.returncode = 0
        
        with patch("packastack.build.sbuild.is_sbuild_available", return_value=True), \
             patch("packastack.build.sbuild.subprocess.run", return_value=mock_result), \
             patch("packastack.build.sbuild.discover_candidate_directories") as mock_discover:
            
            from packastack.build.sbuildrc import CandidateDirectories
            candidates = CandidateDirectories()
            candidates.add_build_dir(build_dir, "test")
            mock_discover.return_value = candidates
            
            result = run_sbuild(config)
        
        assert result.report_path is not None
        assert result.report_path.exists()
        
        # Verify report content
        import json
        report_data = json.loads(result.report_path.read_text())
        assert "sbuild_command" in report_data
        assert "sbuild_exit_code" in report_data
        assert "collection" in report_data

    def test_records_sbuild_command(self, tmp_path: Path) -> None:
        """Should record the sbuild command that was executed."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path / "output",
            distribution="noble",
            run_log_dir=tmp_path / "logs",
        )
        
        mock_result = MagicMock()
        mock_result.returncode = 0
        
        with patch("packastack.build.sbuild.is_sbuild_available", return_value=True), \
             patch("packastack.build.sbuild.subprocess.run", return_value=mock_result), \
             patch("packastack.build.sbuild.discover_candidate_directories") as mock_discover:
            
            from packastack.build.sbuildrc import CandidateDirectories
            mock_discover.return_value = CandidateDirectories()
            
            result = run_sbuild(config)
        
        assert len(result.command) > 0
        assert result.command[0] == "sbuild"
        assert "-d" in result.command
        assert "noble" in result.command

    def test_handles_sbuild_timeout(self, tmp_path: Path) -> None:
        """Should handle sbuild timeout gracefully."""
        import subprocess
        
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path / "output",
            distribution="noble",
            run_log_dir=tmp_path / "logs",
        )
        
        with patch("packastack.build.sbuild.is_sbuild_available", return_value=True), \
             patch("packastack.build.sbuild.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="sbuild", timeout=3600)
            
            result = run_sbuild(config, timeout=3600)
        
        assert not result.success
        assert "timed out" in result.output
        assert "timeout" in result.validation_message.lower()

    def test_collects_logs_from_log_dir(self, tmp_path: Path) -> None:
        """Should collect logs from configured log directory."""
        build_dir = tmp_path / "build"
        log_dir = tmp_path / "schroot_logs"
        build_dir.mkdir()
        log_dir.mkdir()
        
        # Create artifacts and logs
        (build_dir / "pkg_1.0_amd64.deb").write_text("deb content")
        (log_dir / "pkg_amd64.build").write_text("sbuild log content")
        
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=build_dir,
            distribution="noble",
            run_log_dir=tmp_path / "run_logs",
            source_package="pkg",
        )
        
        mock_result = MagicMock()
        mock_result.returncode = 0
        
        with patch("packastack.build.sbuild.is_sbuild_available", return_value=True), \
             patch("packastack.build.sbuild.subprocess.run", return_value=mock_result), \
             patch("packastack.build.sbuild.discover_candidate_directories") as mock_discover:
            
            from packastack.build.sbuildrc import CandidateDirectories
            candidates = CandidateDirectories()
            candidates.add_build_dir(build_dir, "test")
            candidates.add_log_dir(log_dir, "~/.sbuildrc")
            mock_discover.return_value = candidates
            
            result = run_sbuild(config)
        
        # Should collect the log file
        assert len(result.collected_logs) >= 1
