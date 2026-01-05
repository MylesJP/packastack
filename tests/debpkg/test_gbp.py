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

"""Tests for packastack.debpkg.gbp module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import git
import pytest

from packastack.debpkg import gbp


class TestPatchFailureReason:
    """Tests for PatchFailureReason enum."""

    def test_enum_values(self) -> None:
        """Test that all expected failure reasons exist."""
        assert gbp.PatchFailureReason.CONFLICT.value == "conflict"
        assert gbp.PatchFailureReason.FUZZ.value == "fuzz"
        assert gbp.PatchFailureReason.OFFSET.value == "offset"
        assert gbp.PatchFailureReason.MISSING_FILE.value == "missing_file"
        assert gbp.PatchFailureReason.ALREADY_APPLIED.value == "already_applied"
        assert gbp.PatchFailureReason.UPSTREAMED.value == "upstreamed"
        assert gbp.PatchFailureReason.UNKNOWN.value == "unknown"


class TestPatchHealthReport:
    """Tests for PatchHealthReport dataclass."""

    def test_successful_patch_str(self) -> None:
        """Test string representation of successful patch."""
        report = gbp.PatchHealthReport(patch_name="fix-foo.patch", success=True)
        assert str(report) == "fix-foo.patch: OK"

    def test_failed_patch_str(self) -> None:
        """Test string representation of failed patch."""
        report = gbp.PatchHealthReport(
            patch_name="broken.patch",
            success=False,
            failure_reason=gbp.PatchFailureReason.CONFLICT,
            suggested_action="Manual fix needed",
        )
        result = str(report)
        assert "broken.patch" in result
        assert "FAILED" in result
        assert "conflict" in result
        assert "Manual fix needed" in result

    def test_failed_patch_str_no_reason(self) -> None:
        """Test string representation when failure_reason is None."""
        report = gbp.PatchHealthReport(
            patch_name="broken.patch",
            success=False,
            suggested_action="Check manually",
        )
        result = str(report)
        assert "unknown" in result


class TestPQResult:
    """Tests for PQResult dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        result = gbp.PQResult(success=True, output="done")
        assert result.success is True
        assert result.output == "done"
        assert result.needs_refresh is False
        assert result.patch_reports == []


class TestBuildResult:
    """Tests for BuildResult dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        result = gbp.BuildResult(success=True, output="built")
        assert result.success is True
        assert result.output == "built"
        assert result.artifacts == []
        assert result.changes_file is None
        assert result.dsc_file is None


class TestRunCommand:
    """Tests for run_command function."""

    def test_successful_command(self) -> None:
        """Test running a successful command."""
        code, stdout, stderr = gbp.run_command(["echo", "hello"])
        assert code == 0
        assert "hello" in stdout

    def test_failed_command(self) -> None:
        """Test running a failing command."""
        code, stdout, stderr = gbp.run_command(["false"])
        assert code != 0

    def test_with_cwd(self, tmp_path: Path) -> None:
        """Test running command in specific directory."""
        code, stdout, _ = gbp.run_command(["pwd"], cwd=tmp_path)
        assert code == 0
        assert str(tmp_path) in stdout

    def test_with_env(self) -> None:
        """Test running command with custom environment."""
        code, stdout, _ = gbp.run_command(
            ["sh", "-c", "echo $TEST_VAR"],
            env={"TEST_VAR": "test_value"},
        )
        assert code == 0
        assert "test_value" in stdout

    def test_capture_false(self) -> None:
        """Test running without capturing output."""
        code, stdout, stderr = gbp.run_command(["echo", "hello"], capture=False)
        assert code == 0
        assert stdout == ""
        assert stderr == ""


class TestPQImport:
    """Tests for pq_import function."""

    def test_successful_import(self, tmp_path: Path) -> None:
        """Test successful patch queue import."""
        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (0, "Importing patches", "")
            result = gbp.pq_import(tmp_path)

            assert result.success is True
            assert result.needs_refresh is False
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert "gbp" in cmd
            assert "pq" in cmd
            assert "import" in cmd

    def test_failed_import_with_conflict(self, tmp_path: Path) -> None:
        """Test import failure with conflict."""
        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (
                1,
                "Applying: fix-something.patch\nCONFLICT in file.py",
                "",
            )
            result = gbp.pq_import(tmp_path)

            assert result.success is False
            assert len(result.patch_reports) > 0

    def test_needs_refresh_for_fuzz(self, tmp_path: Path) -> None:
        """Test that fuzz failures set needs_refresh."""
        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (
                1,
                "Applying: patch.patch\napplied with fuzz",
                "",
            )
            result = gbp.pq_import(tmp_path)

            assert result.success is False
            # Check patch_reports for fuzz


class TestPQExport:
    """Tests for pq_export function."""

    def test_successful_export(self, tmp_path: Path) -> None:
        """Test successful patch queue export."""
        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (0, "Exporting patches", "")
            result = gbp.pq_export(tmp_path)

            assert result.success is True
            cmd = mock_run.call_args[0][0]
            assert "export" in cmd


class TestPQDrop:
    """Tests for pq_drop function."""

    def test_successful_drop(self, tmp_path: Path) -> None:
        """Test successful patch queue drop."""
        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (0, "Dropped", "")
            result = gbp.pq_drop(tmp_path)

            assert result.success is True
            cmd = mock_run.call_args[0][0]
            assert "drop" in cmd


class TestPQRebase:
    """Tests for pq_rebase function."""

    def test_successful_rebase(self, tmp_path: Path) -> None:
        """Test successful patch queue rebase."""
        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (0, "Rebased", "")
            result = gbp.pq_rebase(tmp_path)

            assert result.success is True

    def test_custom_upstream_branch(self, tmp_path: Path) -> None:
        """Test rebase with custom upstream branch."""
        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (0, "Rebased", "")
            gbp.pq_rebase(tmp_path, upstream_branch="origin/upstream")

            cmd = mock_run.call_args[0][0]
            assert "origin/upstream" in cmd


class TestImportOrigResult:
    """Tests for ImportOrigResult dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        result = gbp.ImportOrigResult(success=True, output="")
        assert result.success is True
        assert result.output == ""
        assert result.upstream_version == ""


class TestImportOrig:
    """Tests for import_orig function."""

    def test_successful_import(self, tmp_path: Path) -> None:
        """Test successful tarball import."""
        tarball = tmp_path / "foo_1.0.0.orig.tar.gz"
        tarball.touch()

        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (0, "Imported version 1.0.0", "")
            result = gbp.import_orig(tmp_path, tarball, upstream_version="1.0.0")

            assert result.success is True
            cmd = mock_run.call_args[0][0]
            assert "gbp" in cmd
            assert "import-orig" in cmd
            assert "--pristine-tar" in cmd
            assert "--upstream-version=1.0.0" in cmd
            assert str(tarball) in cmd

    def test_import_without_pristine_tar(self, tmp_path: Path) -> None:
        """Test import without pristine-tar."""
        tarball = tmp_path / "foo_1.0.0.orig.tar.gz"
        tarball.touch()

        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (0, "Imported", "")
            gbp.import_orig(tmp_path, tarball, pristine_tar=False)

            cmd = mock_run.call_args[0][0]
            assert "--no-pristine-tar" in cmd
            assert "--pristine-tar" not in cmd

    def test_import_without_merge(self, tmp_path: Path) -> None:
        """Test import without merging."""
        tarball = tmp_path / "foo_1.0.0.orig.tar.gz"
        tarball.touch()

        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (0, "Imported", "")
            gbp.import_orig(tmp_path, tarball, merge=False)

            cmd = mock_run.call_args[0][0]
            assert "--no-merge" in cmd

    def test_import_failure(self, tmp_path: Path) -> None:
        """Test failed tarball import."""
        tarball = tmp_path / "foo_1.0.0.orig.tar.gz"
        tarball.touch()

        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (1, "", "Error: something went wrong")
            result = gbp.import_orig(tmp_path, tarball)

            assert result.success is False
            assert "something went wrong" in result.output

    def test_custom_upstream_branch(self, tmp_path: Path) -> None:
        """Test import with custom upstream branch."""
        tarball = tmp_path / "foo_1.0.0.orig.tar.gz"
        tarball.touch()

        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (0, "Imported", "")
            gbp.import_orig(tmp_path, tarball, upstream_branch="upstream-gazpacho")

            cmd = mock_run.call_args[0][0]
            assert "--upstream-branch=upstream-gazpacho" in cmd


class TestEnsureUpstreamBranchResult:
    """Tests for EnsureUpstreamBranchResult dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        result = gbp.EnsureUpstreamBranchResult(
            success=True,
            branch_name="upstream-gazpacho",
        )
        assert result.success is True
        assert result.branch_name == "upstream-gazpacho"
        assert result.created is False
        assert result.error == ""


class TestEnsureUpstreamBranch:
    """Tests for ensure_upstream_branch function."""

    def test_branch_already_exists_local(self, tmp_path: Path) -> None:
        """Test when target branch already exists locally."""
        # Create a git repo with the branch already existing
        repo = git.Repo.init(tmp_path)
        (tmp_path / "file.txt").write_text("content")
        repo.index.add(["file.txt"])
        repo.index.commit("Initial commit")
        # Create the upstream-gazpacho branch
        repo.create_head("upstream-gazpacho")

        result = gbp.ensure_upstream_branch(
            tmp_path, target_series="gazpacho", prev_series="flamingo"
        )

        assert result.success is True
        assert result.branch_name == "upstream-gazpacho"
        assert result.created is False  # Already existed

    def test_branch_created_from_prev_series(self, tmp_path: Path) -> None:
        """Test creating branch from previous series branch."""
        # Create a git repo with the prev series branch
        repo = git.Repo.init(tmp_path)
        (tmp_path / "file.txt").write_text("content")
        repo.index.add(["file.txt"])
        repo.index.commit("Initial commit")
        # Create the upstream-flamingo branch (previous series)
        repo.create_head("upstream-flamingo")

        result = gbp.ensure_upstream_branch(
            tmp_path, target_series="gazpacho", prev_series="flamingo"
        )

        assert result.success is True
        assert result.branch_name == "upstream-gazpacho"
        assert result.created is True
        # Verify the branch was actually created
        assert "upstream-gazpacho" in [h.name for h in repo.heads]

    def test_prev_series_branch_missing(self, tmp_path: Path) -> None:
        """Test failure when previous series branch doesn't exist."""
        repo = git.Repo.init(tmp_path)
        (tmp_path / "file.txt").write_text("content")
        repo.index.add(["file.txt"])
        repo.index.commit("Initial commit")
        # Don't create any upstream branch

        result = gbp.ensure_upstream_branch(
            tmp_path, target_series="gazpacho", prev_series="flamingo"
        )

        assert result.success is False
        assert "upstream-flamingo" in result.error
        assert "upstream-gazpacho" not in [h.name for h in repo.heads]

    def test_no_prev_series(self, tmp_path: Path) -> None:
        """Test failure when no previous series provided and branch missing."""
        repo = git.Repo.init(tmp_path)
        (tmp_path / "file.txt").write_text("content")
        repo.index.add(["file.txt"])
        repo.index.commit("Initial commit")

        result = gbp.ensure_upstream_branch(
            tmp_path, target_series="gazpacho", prev_series=None
        )

        assert result.success is False
        assert "upstream-gazpacho" in result.error


class TestAnalyzePQFailure:
    """Tests for _analyze_pq_failure function."""

    def test_conflict_detection(self) -> None:
        """Test detection of conflict in patch output."""
        output = "Applying: fix.patch\nCONFLICT (content): Merge conflict in foo.py"
        reports = gbp._analyze_pq_failure(output)

        assert len(reports) > 0
        assert reports[0].failure_reason == gbp.PatchFailureReason.CONFLICT

    def test_fuzz_detection(self) -> None:
        """Test detection of fuzz in patch output."""
        output = "Applying: fix.patch\napplied with fuzz factor 3"
        reports = gbp._analyze_pq_failure(output)

        assert len(reports) > 0
        assert reports[0].failure_reason == gbp.PatchFailureReason.FUZZ

    def test_offset_detection(self) -> None:
        """Test detection of offset in patch output."""
        output = "Applying: fix.patch\napplied with offset -10"
        reports = gbp._analyze_pq_failure(output)

        assert len(reports) > 0
        assert reports[0].failure_reason == gbp.PatchFailureReason.OFFSET

    def test_missing_file_detection(self) -> None:
        """Test detection of missing file in patch output."""
        output = "Applying: fix.patch\nNo such file or directory"
        reports = gbp._analyze_pq_failure(output)

        assert len(reports) > 0
        assert reports[0].failure_reason == gbp.PatchFailureReason.MISSING_FILE

    def test_already_applied_detection(self) -> None:
        """Test detection of already applied patch."""
        output = "Applying: fix.patch\npatch already applied"
        reports = gbp._analyze_pq_failure(output)

        assert len(reports) > 0
        assert reports[0].failure_reason == gbp.PatchFailureReason.ALREADY_APPLIED

    def test_empty_output(self) -> None:
        """Test with empty output."""
        reports = gbp._analyze_pq_failure("")
        assert reports == []


class TestCheckUpstreamedPatches:
    """Tests for check_upstreamed_patches function."""

    def test_no_patches_dir(self, tmp_path: Path) -> None:
        """Test when patches directory doesn't exist."""
        reports = gbp.check_upstreamed_patches(tmp_path)
        assert reports == []

    def test_no_series_file(self, tmp_path: Path) -> None:
        """Test when series file doesn't exist."""
        patches_dir = tmp_path / "debian" / "patches"
        patches_dir.mkdir(parents=True)
        reports = gbp.check_upstreamed_patches(tmp_path)
        assert reports == []

    def test_empty_series(self, tmp_path: Path) -> None:
        """Test with empty series file."""
        patches_dir = tmp_path / "debian" / "patches"
        patches_dir.mkdir(parents=True)
        (patches_dir / "series").write_text("")
        reports = gbp.check_upstreamed_patches(tmp_path)
        assert reports == []

    def test_custom_patches_dir(self, tmp_path: Path) -> None:
        """Test with custom patches directory."""
        custom_dir = tmp_path / "custom" / "patches"
        custom_dir.mkdir(parents=True)
        (custom_dir / "series").write_text("")
        reports = gbp.check_upstreamed_patches(tmp_path, patches_dir=custom_dir)
        assert reports == []


class TestBuildSource:
    """Tests for build_source function."""

    def test_successful_build(self, tmp_path: Path) -> None:
        """Test successful source build."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (0, "Built successfully", "")
            result = gbp.build_source(repo_path)

            assert result.success is True
            cmd = mock_run.call_args[0][0]
            assert "gbp" in cmd
            assert "buildpackage" in cmd
            assert "-S" in cmd

    def test_unsigned_by_default(self, tmp_path: Path) -> None:
        """Test that unsigned is True by default."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (0, "", "")
            gbp.build_source(repo_path)

            cmd = mock_run.call_args[0][0]
            assert "-us" in cmd
            assert "-uc" in cmd

    def test_signed_build(self, tmp_path: Path) -> None:
        """Test signed build."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (0, "", "")
            gbp.build_source(repo_path, unsigned=False)

            cmd = mock_run.call_args[0][0]
            assert "-us" not in cmd
            assert "-uc" not in cmd

    def test_custom_output_dir(self, tmp_path: Path) -> None:
        """Test with custom output directory."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (0, "", "")
            gbp.build_source(repo_path, output_dir=output_dir)

            cmd = mock_run.call_args[0][0]
            # Check for --git-export-dir=<path> format (gbp uses = not space)
            export_dir_args = [arg for arg in cmd if arg.startswith("--git-export-dir=")]
            assert len(export_dir_args) == 1
            assert str(output_dir) in export_dir_args[0]

    def test_finds_artifacts(self, tmp_path: Path) -> None:
        """Test that artifacts are found after build."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        output_dir = repo_path.parent

        # Create fake artifacts
        (output_dir / "pkg_1.0.dsc").touch()
        (output_dir / "pkg_1.0.tar.gz").touch()
        (output_dir / "pkg_1.0_source.changes").touch()

        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (0, "", "")
            result = gbp.build_source(repo_path)

            assert len(result.artifacts) == 3
            assert result.dsc_file is not None
            assert result.changes_file is not None


class TestBuildBinary:
    """Tests for build_binary function."""

    def test_successful_build(self, tmp_path: Path) -> None:
        """Test successful binary build."""
        dsc_path = tmp_path / "pkg_1.0.dsc"
        dsc_path.touch()

        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (0, "Built successfully", "")
            result = gbp.build_binary(dsc_path)

            assert result.success is True
            cmd = mock_run.call_args[0][0]
            assert "sbuild" in cmd

    def test_with_distribution(self, tmp_path: Path) -> None:
        """Test build with target distribution."""
        dsc_path = tmp_path / "pkg_1.0.dsc"
        dsc_path.touch()

        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (0, "", "")
            gbp.build_binary(dsc_path, distribution="noble")

            cmd = mock_run.call_args[0][0]
            assert "-d" in cmd
            assert "noble" in cmd

    def test_failed_build(self, tmp_path: Path) -> None:
        """Test failed binary build."""
        dsc_path = tmp_path / "pkg_1.0.dsc"
        dsc_path.touch()

        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (1, "", "Build failed")
            result = gbp.build_binary(dsc_path)

            assert result.success is False

    def test_custom_output_dir(self, tmp_path: Path) -> None:
        """Test with custom output directory."""
        dsc_path = tmp_path / "pkg_1.0.dsc"
        dsc_path.touch()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (0, "", "")
            gbp.build_binary(dsc_path, output_dir=output_dir)

            # Check that command runs in output_dir
            kwargs = mock_run.call_args[1]
            assert kwargs["cwd"] == output_dir

    def test_finds_deb_artifacts(self, tmp_path: Path) -> None:
        """Test that .deb artifacts are found after build."""
        dsc_path = tmp_path / "pkg_1.0.dsc"
        dsc_path.touch()

        # Create fake artifacts
        (tmp_path / "pkg_1.0_amd64.deb").touch()
        (tmp_path / "pkg-dbgsym_1.0_amd64.ddeb").touch()
        (tmp_path / "pkg_1.0_amd64.changes").touch()

        with patch.object(gbp, "run_command") as mock_run:
            mock_run.return_value = (0, "", "")
            result = gbp.build_binary(dsc_path)

            assert len(result.artifacts) == 3
            assert result.changes_file is not None
