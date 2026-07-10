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

from packastack.build.collector import CollectionResult
from packastack.build.sbuild import (
    CHROOT_SOURCES_LIST,
    SbuildConfig,
    SbuildResult,
    build_sbuild_command,
    generate_chroot_cleanup_commands,
    generate_chroot_setup_commands,
    generate_proposed_setup_commands,
    get_default_chroot_name,
    parse_pybuild_python_versions,
    run_sbuild,
    write_python_versions_sbuild_config,
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
        assert config.python_versions == []

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

    def test_python_versions_are_validated_and_deduplicated(self, tmp_path: Path) -> None:
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path,
            distribution="noble",
            python_versions=["3.14", "3.15", "3.14"],
        )
        assert config.python_versions == ["3.14", "3.15"]

    def test_rejects_invalid_python_versions(self, tmp_path: Path) -> None:
        import pytest

        with pytest.raises(ValueError, match="must look like"):
            SbuildConfig(
                dsc_path=tmp_path / "pkg.dsc",
                output_dir=tmp_path,
                distribution="noble",
                python_versions=["3.14; rm -rf /"],
            )


class TestGenerateChrootSetupCommands:
    """Tests for generate_chroot_setup_commands function."""

    def test_generates_sources_list_command(self) -> None:
        """Test that sources list command is generated."""
        cmds = generate_chroot_setup_commands()
        sources_cmd = [c for c in cmds if "echo" in c and "deb" in c]
        assert len(sources_cmd) == 1
        assert "[trusted=yes]" in sources_cmd[0]
        assert CHROOT_SOURCES_LIST in sources_cmd[0]

    def test_generates_apt_update_command(self) -> None:
        """Test that apt-get update command is generated."""
        cmds = generate_chroot_setup_commands()
        update_cmd = [c for c in cmds if "apt-get update" in c]
        assert len(update_cmd) == 1

    def test_no_mount_commands(self) -> None:
        """Mount is handled by schroot fstab, not chroot-setup-commands."""
        cmds = generate_chroot_setup_commands()
        assert not any("mount" in c for c in cmds)


class TestGenerateChrootCleanupCommands:
    """Tests for generate_chroot_cleanup_commands function."""

    def test_generates_rm_command(self) -> None:
        """Test that rm command is generated."""
        cmds = generate_chroot_cleanup_commands()
        rm_cmd = [c for c in cmds if "rm" in c]
        assert len(rm_cmd) == 1
        assert CHROOT_SOURCES_LIST in rm_cmd[0]

    def test_no_umount_command(self) -> None:
        """Unmount is handled by schroot session teardown."""
        cmds = generate_chroot_cleanup_commands()
        assert not any("umount" in c for c in cmds)


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
        assert "--nolog" in cmd  # stdout capture requires --nolog
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
        """Test command with chroot name when no distribution is set."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path,
            distribution="",
            chroot_name="noble-amd64-sbuild",
        )
        cmd = build_sbuild_command(config)
        assert "-c" in cmd
        assert "noble-amd64-sbuild" in cmd

    def test_chroot_name_used_alongside_distribution(self, tmp_path: Path) -> None:
        """Test that -c is passed alongside -d when both are set."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path,
            distribution="noble",
            chroot_name="noble-amd64-packastack",
        )
        cmd = build_sbuild_command(config)
        assert "-c" in cmd
        assert "noble-amd64-packastack" in cmd
        assert "-d" in cmd
        assert "noble" in cmd

    def test_with_local_repo(self, tmp_path: Path) -> None:
        """Test command with local repo setup when fstab is configured."""
        repo = tmp_path / "repo"
        repo.mkdir()
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path,
            distribution="noble",
            local_repo_root=repo,
            chroot_name="noble-amd64-packastack",
        )
        with patch("packastack.build.sbuild.configure_repo_mount", return_value=True):
            cmd = build_sbuild_command(config)
        assert "--chroot-setup-commands" in cmd
        assert "--finished-build-commands" in cmd
        # No mount commands — bind-mount is via schroot fstab
        setup_args = []
        for i, arg in enumerate(cmd):
            if arg == "--chroot-setup-commands" and i + 1 < len(cmd):
                setup_args.append(cmd[i + 1])
        assert not any("mount" in s for s in setup_args)

    def test_local_repo_skipped_when_fstab_fails(self, tmp_path: Path) -> None:
        """When configure_repo_mount fails, local repo commands are omitted."""
        from packastack.build.schroot import RepoMountError

        repo = tmp_path / "repo"
        repo.mkdir()
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path,
            distribution="noble",
            local_repo_root=repo,
            chroot_name="noble-amd64-packastack",
            proposed=False,
        )
        with patch(
            "packastack.build.sbuild.configure_repo_mount",
            side_effect=RepoMountError("no config"),
        ):
            cmd = build_sbuild_command(config)
        assert "--chroot-setup-commands" not in cmd

    def test_local_repo_skipped_when_no_chroot_name(self, tmp_path: Path) -> None:
        """Without a chroot_name, local repo setup is skipped."""
        repo = tmp_path / "repo"
        repo.mkdir()
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path,
            distribution="noble",
            local_repo_root=repo,
            proposed=False,
        )
        cmd = build_sbuild_command(config)
        assert "--chroot-setup-commands" not in cmd

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
        # --fail-on=error is passed as a single combined arg to --lintian-opts
        assert "--lintian-opts" in cmd
        idx = cmd.index("--lintian-opts")
        assert cmd[idx + 1] == "--fail-on=error"

    def test_adds_explicit_python_versions_as_build_dependencies(self, tmp_path: Path) -> None:
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path,
            distribution="stonking",
            python_versions=["3.14", "3.15"],
        )
        cmd = build_sbuild_command(config)
        assert "--add-depends=python3.14" in cmd
        assert "--add-depends=python3.15" in cmd
        assert cmd[-1] == str(tmp_path / "pkg.dsc")


class TestProposedPocket:
    """Tests for -proposed pocket injection in the sbuild command."""

    def test_proposed_on_by_default(self, tmp_path: Path) -> None:
        """Default command enables -proposed with the apt update flags."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path,
            distribution="stonking",
        )
        cmd = build_sbuild_command(config)

        setup_args = [
            cmd[i + 1]
            for i, arg in enumerate(cmd)
            if arg == "--chroot-setup-commands" and i + 1 < len(cmd)
        ]
        assert any("stonking-proposed" in arg for arg in setup_args)
        assert "--apt-update" in cmd
        assert "--apt-distupgrade" in cmd
        # DSC must remain the last argument
        assert cmd[-1] == str(tmp_path / "pkg.dsc")

    def test_proposed_enabled(self, tmp_path: Path) -> None:
        """proposed=True enables -proposed with pin and apt flags."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path,
            distribution="stonking",
            proposed=True,
        )
        cmd = build_sbuild_command(config)

        setup_args = [
            cmd[i + 1]
            for i, arg in enumerate(cmd)
            if arg == "--chroot-setup-commands" and i + 1 < len(cmd)
        ]
        assert any("stonking-proposed" in s for s in setup_args)
        assert any("Pin-Priority: 500" in s for s in setup_args)
        assert "--apt-update" in cmd
        assert "--apt-distupgrade" in cmd
        # DSC must remain the last argument
        assert cmd[-1] == str(tmp_path / "pkg.dsc")

    def test_proposed_disabled(self, tmp_path: Path) -> None:
        """proposed=False omits setup commands and apt flags."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path,
            distribution="stonking",
            proposed=False,
        )
        cmd = build_sbuild_command(config)
        assert "--chroot-setup-commands" not in cmd
        assert "--apt-update" not in cmd
        assert "--apt-distupgrade" not in cmd

    def test_proposed_uses_mirror_and_components(self, tmp_path: Path) -> None:
        """Configured mirror and components appear in the sources line."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path,
            distribution="noble",
            proposed=True,
            mirror="http://ca.archive.ubuntu.com/ubuntu",
            components=["main", "universe", "multiverse"],
        )
        cmd = build_sbuild_command(config)
        setup_args = [
            cmd[i + 1]
            for i, arg in enumerate(cmd)
            if arg == "--chroot-setup-commands" and i + 1 < len(cmd)
        ]
        assert any(
            "deb http://ca.archive.ubuntu.com/ubuntu noble-proposed main universe multiverse" in s
            for s in setup_args
        )


class TestGenerateProposedSetupCommands:
    """Tests for generate_proposed_setup_commands function."""

    def test_sources_line_format(self) -> None:
        """Sources command writes the -proposed deb line."""
        cmds = generate_proposed_setup_commands(
            "stonking", "http://archive.ubuntu.com/ubuntu", ["main", "universe"]
        )
        assert (
            'echo "deb http://archive.ubuntu.com/ubuntu stonking-proposed '
            'main universe"' in cmds[0]
        )
        assert "packastack-proposed.list" in cmds[0]

    def test_pin_content(self) -> None:
        """Pin command writes a priority-500 preferences file."""
        cmds = generate_proposed_setup_commands(
            "stonking", "http://archive.ubuntu.com/ubuntu", ["main"]
        )
        assert "Package: *" in cmds[1]
        assert "Pin: release a=stonking-proposed" in cmds[1]
        assert "Pin-Priority: 500" in cmds[1]
        assert "/etc/apt/preferences.d/packastack-proposed" in cmds[1]

    def test_no_percent_escapes(self) -> None:
        """sbuild percent-escapes external commands; none may contain %."""
        cmds = generate_proposed_setup_commands(
            "stonking", "http://archive.ubuntu.com/ubuntu", ["main", "universe"]
        )
        for cmd in cmds:
            assert "%" not in cmd


class TestParsePybuildPythonVersions:
    """Tests for parse_pybuild_python_versions function."""

    def test_two_versions(self) -> None:
        """Detects both versions across pybuild lines."""
        log = (
            "I: pybuild base:385: python3.14 setup.py config\n"
            "some unrelated line\n"
            "I: pybuild pybuild:308: python3.15 -m pytest\n"
        )
        assert parse_pybuild_python_versions(log) == ["3.14", "3.15"]

    def test_single_version(self) -> None:
        """Single supported version yields a one-element list."""
        log = "I: pybuild base:385: python3.14 setup.py build\n"
        assert parse_pybuild_python_versions(log) == ["3.14"]

    def test_deduplicates(self) -> None:
        """Repeated mentions of a version are deduplicated."""
        log = (
            "I: pybuild base:385: python3.14 setup.py config\n"
            "I: pybuild base:385: python3.14 setup.py build\n"
            "I: pybuild base:385: python3.14 setup.py install\n"
        )
        assert parse_pybuild_python_versions(log) == ["3.14"]

    def test_numeric_sort(self) -> None:
        """Versions sort numerically, not lexically (3.9 < 3.14)."""
        log = (
            "I: pybuild base:385: python3.14 setup.py config\n"
            "I: pybuild base:385: python3.9 setup.py config\n"
        )
        assert parse_pybuild_python_versions(log) == ["3.9", "3.14"]

    def test_empty_log(self) -> None:
        """Empty log yields an empty list."""
        assert parse_pybuild_python_versions("") == []

    def test_ignores_non_pybuild_lines(self) -> None:
        """Interpreter mentions on apt/download lines are ignored."""
        log = (
            "Get:42 http://archive.ubuntu.com/ubuntu stonking/main amd64 "
            "python3.14 amd64 3.14.0-1 [512 kB]\n"
            "Setting up python3.15-minimal (3.15.0-1) ...\n"
        )
        assert parse_pybuild_python_versions(log) == []


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

        with (
            patch("packastack.build.sbuild.is_sbuild_available", return_value=True),
            patch("packastack.build.sbuild.subprocess.run", return_value=mock_result),
            patch("packastack.build.sbuild.discover_candidate_directories") as mock_discover,
            patch("packastack.build.sbuild.collect_artifacts") as mock_collect,
        ):
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

    def test_populates_python_versions_tested(self, tmp_path: Path) -> None:
        """Should parse pybuild-exercised Python versions from the output."""
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path / "output",
            distribution="stonking",
            run_log_dir=tmp_path / "logs",
        )

        mock_result = MagicMock()
        mock_result.returncode = 0

        def fake_run(cmd, **kwargs):
            kwargs["stdout"].write(
                "I: pybuild base:385: python3.14 setup.py config\n"
                "I: pybuild pybuild:308: python3.15 -m pytest\n"
            )
            return mock_result

        with (
            patch("packastack.build.sbuild.is_sbuild_available", return_value=True),
            patch("packastack.build.sbuild.subprocess.run", side_effect=fake_run),
            patch("packastack.build.sbuild.discover_candidate_directories") as mock_discover,
            patch("packastack.build.sbuild.collect_artifacts") as mock_collect,
        ):
            from packastack.build.sbuildrc import CandidateDirectories

            mock_discover.return_value = CandidateDirectories()
            mock_collect.return_value = CollectionResult(
                success=True,
                validation_message="",
            )

            result = run_sbuild(config)

        assert result.python_versions_tested == ["3.14", "3.15"]

    def test_passes_explicit_matrix_via_sbuild_config(self, tmp_path: Path) -> None:
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path / "output",
            distribution="stonking",
            run_log_dir=tmp_path / "logs",
            python_versions=["3.14", "3.15"],
        )
        mock_result = MagicMock(returncode=0)

        with (
            patch("packastack.build.sbuild.is_sbuild_available", return_value=True),
            patch("packastack.build.sbuild.subprocess.run", return_value=mock_result) as mock_run,
            patch("packastack.build.sbuild.discover_candidate_directories") as mock_discover,
            patch("packastack.build.sbuild.collect_artifacts") as mock_collect,
        ):
            from packastack.build.sbuildrc import CandidateDirectories

            mock_discover.return_value = CandidateDirectories()
            mock_collect.return_value = CollectionResult(success=True)
            result = run_sbuild(config)

        assert result.success
        assert result.python_versions_tested == ["3.14", "3.15"]
        assert mock_run.call_count == 2
        for version, call in zip(config.python_versions, mock_run.call_args_list, strict=True):
            command = call.args[0]
            assert "--apt-update" in command
            assert "--apt-distupgrade" in command
            assert any("stonking-proposed" in argument for argument in command)
            env = call.kwargs["env"]
            config_path = Path(env["SBUILD_CONFIG"])
            assert config_path.parent.name == f"python{version}"
            assert config_path.read_text() == (
                "# Generated by Packastack; retained with this build's logs.\n"
                f"$build_environment->{{'DEBPYTHON3_SUPPORTED'}} = '{version}';\n"
                f"$build_environment->{{'PYBUILD_VERSIONS'}} = '{version}';\n"
                f"$build_environment->{{'PYTHON'}} = 'python{version}';\n"
            )

    def test_matrix_fails_if_either_python_pass_fails(self, tmp_path: Path) -> None:
        config = SbuildConfig(
            dsc_path=tmp_path / "pkg.dsc",
            output_dir=tmp_path / "output",
            distribution="stonking",
            run_log_dir=tmp_path / "logs",
            python_versions=["3.14", "3.15"],
        )
        artifact = tmp_path / "output" / "python3.14" / "pkg.deb"
        pass_result = SbuildResult(
            success=True,
            output="Python 3.14 tests passed\n",
            artifacts=[artifact],
            exit_code=0,
        )
        fail_result = SbuildResult(
            success=False,
            output="Failures during discovery\nNO TESTS RAN\n",
            exit_code=0,
            validation_message="Test failure was masked by a successful sbuild pipeline",
        )

        with (
            patch("packastack.build.sbuild.is_sbuild_available", return_value=True),
            patch(
                "packastack.build.sbuild._run_sbuild_once",
                side_effect=[pass_result, fail_result],
            ) as mock_pass,
        ):
            result = run_sbuild(config)

        assert mock_pass.call_count == 2
        assert mock_pass.call_args_list[0].args[0].python_versions == ["3.14"]
        assert mock_pass.call_args_list[1].args[0].python_versions == ["3.15"]
        assert not result.success
        assert result.artifacts == [artifact]
        assert result.python_versions_tested == ["3.14"]
        assert "Python 3.15" in result.validation_message
        assert result.python_version_results == {
            "3.14": pass_result,
            "3.15": fail_result,
        }


class TestPythonVersionsSbuildConfig:
    """Tests for per-build sbuild configuration fragments."""

    def test_returns_none_for_an_empty_matrix(self, tmp_path: Path) -> None:
        assert write_python_versions_sbuild_config(tmp_path, []) is None

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

        with (
            patch("packastack.build.sbuild.is_sbuild_available", return_value=True),
            patch("packastack.build.sbuild.subprocess.run", return_value=mock_result),
            patch("packastack.build.sbuild.discover_candidate_directories") as mock_discover,
        ):
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

        collected.logs.append(
            CollectedFile(
                real_log, real_log, "hash", real_log.stat().st_size, real_log.stat().st_mtime
            )
        )

        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch("packastack.build.sbuild.is_sbuild_available", return_value=True),
            patch("packastack.build.sbuild.subprocess.run", return_value=mock_result),
            patch("packastack.build.sbuild.discover_candidate_directories") as mock_discover,
            patch("packastack.build.sbuild.collect_artifacts", return_value=collected),
        ):
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
            CollectedFile(
                real_log, real_log, "hash", real_log.stat().st_size, real_log.stat().st_mtime
            )
        )

        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch("packastack.build.sbuild.is_sbuild_available", return_value=True),
            patch("packastack.build.sbuild.subprocess.run", return_value=mock_result),
            patch("packastack.build.sbuild.discover_candidate_directories") as mock_discover,
            patch("packastack.build.sbuild.collect_artifacts", return_value=collected),
        ):
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

        with (
            patch("packastack.build.sbuild.is_sbuild_available", return_value=True),
            patch("packastack.build.sbuild.subprocess.run", return_value=mock_result),
            patch("packastack.build.sbuild.discover_candidate_directories") as mock_discover,
        ):
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

        with (
            patch("packastack.build.sbuild.is_sbuild_available", return_value=True),
            patch("packastack.build.sbuild.subprocess.run", return_value=mock_result),
            patch("packastack.build.sbuild.discover_candidate_directories") as mock_discover,
        ):
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

        with (
            patch("packastack.build.sbuild.is_sbuild_available", return_value=True),
            patch("packastack.build.sbuild.subprocess.run", return_value=mock_result),
            patch("packastack.build.sbuild.discover_candidate_directories") as mock_discover,
        ):
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

        with (
            patch("packastack.build.sbuild.is_sbuild_available", return_value=True),
            patch("packastack.build.sbuild.subprocess.run") as mock_run,
        ):
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

        with (
            patch("packastack.build.sbuild.is_sbuild_available", return_value=True),
            patch("packastack.build.sbuild.subprocess.run", return_value=mock_result),
            patch("packastack.build.sbuild.discover_candidate_directories") as mock_discover,
        ):
            from packastack.build.sbuildrc import CandidateDirectories

            candidates = CandidateDirectories()
            candidates.add_build_dir(build_dir, "test")
            candidates.add_log_dir(log_dir, "~/.sbuildrc")
            mock_discover.return_value = candidates

            result = run_sbuild(config)

        # Should collect the log file
        assert len(result.collected_logs) >= 1
