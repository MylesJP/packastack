# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for packastack.debpkg.sudoers module."""

from __future__ import annotations

from pathlib import Path

from packastack.debpkg.sudoers import (
    SudoersFixResult,
    fix_sudoers_args,
    fix_sudoers_in_debian_dir,
)


class TestFixSudoersArgs:
    """Tests for fix_sudoers_args function."""

    def test_strips_config_and_wildcard(self, tmp_path: Path) -> None:
        """Removes both config path arg and trailing wildcard."""
        sudoers = tmp_path / "cinder_sudoers"
        sudoers.write_text(
            "cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap /etc/cinder/rootwrap.conf *\n"
        )

        result = fix_sudoers_args(sudoers)

        assert result is True
        assert sudoers.read_text() == (
            "cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap\n"
        )

    def test_strips_config_path_without_wildcard(self, tmp_path: Path) -> None:
        """Removes config path arg even when there's no trailing wildcard."""
        sudoers = tmp_path / "manila_sudoers"
        sudoers.write_text(
            "manila ALL = (root) NOPASSWD: /usr/bin/manila-rootwrap /etc/manila/rootwrap.conf\n"
        )

        result = fix_sudoers_args(sudoers)

        assert result is True
        assert sudoers.read_text() == (
            "manila ALL = (root) NOPASSWD: /usr/bin/manila-rootwrap\n"
        )

    def test_strips_wildcard_only(self, tmp_path: Path) -> None:
        """Removes bare wildcard argument."""
        sudoers = tmp_path / "nova_sudoers"
        sudoers.write_text(
            "nova ALL = (root) NOPASSWD: /usr/bin/nova-rootwrap *\n"
        )

        result = fix_sudoers_args(sudoers)

        assert result is True
        assert sudoers.read_text() == (
            "nova ALL = (root) NOPASSWD: /usr/bin/nova-rootwrap\n"
        )

    def test_no_change_when_no_args(self, tmp_path: Path) -> None:
        """No-op when command already has no arguments."""
        sudoers = tmp_path / "cinder_sudoers"
        original = "cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap\n"
        sudoers.write_text(original)

        result = fix_sudoers_args(sudoers)

        assert result is False
        assert sudoers.read_text() == original

    def test_no_change_for_empty_file(self, tmp_path: Path) -> None:
        sudoers = tmp_path / "empty_sudoers"
        sudoers.write_text("")

        result = fix_sudoers_args(sudoers)

        assert result is False

    def test_preserves_comment_lines(self, tmp_path: Path) -> None:
        sudoers = tmp_path / "neutron_sudoers"
        sudoers.write_text(
            "# Allow neutron user to run rootwrap\n"
            "neutron ALL = (root) NOPASSWD: /usr/bin/neutron-rootwrap /etc/neutron/rootwrap.conf *\n"
        )

        result = fix_sudoers_args(sudoers)

        assert result is True
        assert sudoers.read_text() == (
            "# Allow neutron user to run rootwrap\n"
            "neutron ALL = (root) NOPASSWD: /usr/bin/neutron-rootwrap\n"
        )

    def test_fixes_multiple_lines(self, tmp_path: Path) -> None:
        sudoers = tmp_path / "multi_sudoers"
        sudoers.write_text(
            "cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap /etc/cinder/rootwrap.conf *\n"
            "cinder ALL = (root) NOPASSWD: /usr/bin/privsep-helper *\n"
        )

        result = fix_sudoers_args(sudoers)

        assert result is True
        content = sudoers.read_text()
        assert content == (
            "cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap\n"
            "cinder ALL = (root) NOPASSWD: /usr/bin/privsep-helper\n"
        )

    def test_raises_oserror_on_missing_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent_sudoers"

        import pytest

        with pytest.raises(OSError):
            fix_sudoers_args(missing)

    def test_handles_leading_whitespace(self, tmp_path: Path) -> None:
        sudoers = tmp_path / "indented_sudoers"
        sudoers.write_text(
            "  cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap /etc/cinder/rootwrap.conf *\n"
        )

        result = fix_sudoers_args(sudoers)

        assert result is True
        assert sudoers.read_text() == (
            "  cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap\n"
        )


class TestFixSudoersInDebianDir:
    """Tests for fix_sudoers_in_debian_dir function."""

    def test_fixes_matching_sudoers_files(self, tmp_path: Path) -> None:
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "cinder_sudoers").write_text(
            "cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap /etc/cinder/rootwrap.conf *\n"
        )
        (debian / "nova_sudoers").write_text(
            "nova ALL = (root) NOPASSWD: /usr/bin/nova-rootwrap /etc/nova/rootwrap.conf *\n"
        )

        result = fix_sudoers_in_debian_dir(debian)

        assert result.files_scanned == 2
        assert sorted(result.files_fixed) == ["cinder_sudoers", "nova_sudoers"]
        assert result.errors == []

    def test_fixes_config_arg_without_wildcard(self, tmp_path: Path) -> None:
        """Manila-style: config path but no wildcard should still be fixed."""
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "manila_sudoers").write_text(
            "manila ALL = (root) NOPASSWD: /usr/bin/manila-rootwrap /etc/manila/rootwrap.conf\n"
        )

        result = fix_sudoers_in_debian_dir(debian)

        assert result.files_scanned == 1
        assert result.files_fixed == ["manila_sudoers"]
        content = (debian / "manila_sudoers").read_text()
        assert content == "manila ALL = (root) NOPASSWD: /usr/bin/manila-rootwrap\n"

    def test_skips_clean_sudoers_files(self, tmp_path: Path) -> None:
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "cinder_sudoers").write_text(
            "cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap\n"
        )

        result = fix_sudoers_in_debian_dir(debian)

        assert result.files_scanned == 1
        assert result.files_fixed == []

    def test_returns_empty_for_missing_dir(self, tmp_path: Path) -> None:
        result = fix_sudoers_in_debian_dir(tmp_path / "nonexistent")

        assert result.files_scanned == 0
        assert result.files_fixed == []

    def test_ignores_non_sudoers_files(self, tmp_path: Path) -> None:
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "control").write_text("Source: cinder\n")
        (debian / "rules").write_text("#!/usr/bin/make -f\n")

        result = fix_sudoers_in_debian_dir(debian)

        assert result.files_scanned == 0

    def test_records_oserror_as_error(self, tmp_path: Path) -> None:
        debian = tmp_path / "debian"
        debian.mkdir()
        sudoers = debian / "cinder_sudoers"
        sudoers.write_text(
            "cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap *\n"
        )
        # Make the file unwritable to trigger an OSError on write
        sudoers.chmod(0o444)
        # Also make parent unwritable so we can't recreate it
        debian.chmod(0o555)

        result = fix_sudoers_in_debian_dir(debian)

        # Restore permissions for cleanup
        debian.chmod(0o755)
        sudoers.chmod(0o644)

        assert result.files_scanned == 1
        assert result.files_fixed == []
        assert len(result.errors) == 1
        assert "cinder_sudoers" in result.errors[0]

    def test_mixed_clean_and_dirty_files(self, tmp_path: Path) -> None:
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "cinder_sudoers").write_text(
            "cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap /etc/cinder/rootwrap.conf *\n"
        )
        (debian / "neutron_sudoers").write_text(
            "neutron ALL = (root) NOPASSWD: /usr/bin/neutron-rootwrap\n"
        )

        result = fix_sudoers_in_debian_dir(debian)

        assert result.files_scanned == 2
        assert result.files_fixed == ["cinder_sudoers"]


class TestSudoersFixResult:
    """Tests for SudoersFixResult dataclass."""

    def test_defaults(self) -> None:
        result = SudoersFixResult()

        assert result.files_scanned == 0
        assert result.files_fixed == []
        assert result.errors == []
