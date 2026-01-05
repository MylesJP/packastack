# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for packastack.debpkg.rules module."""

from __future__ import annotations

from pathlib import Path

import pytest

from packastack.debpkg.rules import (
    add_doctree_cleanup,
    ensure_sphinxdoc_addon,
    has_override,
)


class TestAddDoctreeCleanup:
    """Tests for add_doctree_cleanup function."""

    def test_adds_cleanup_to_existing_sphinxdoc_override(self, tmp_path: Path) -> None:
        """Test adding cleanup to existing override_dh_sphinxdoc."""
        rules = tmp_path / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@

override_dh_sphinxdoc:
\tdh_sphinxdoc
"""
        )

        result = add_doctree_cleanup(rules)

        assert result is True
        content = rules.read_text()
        assert ".doctrees" in content
        assert "rm -rf debian/*/usr/share/doc/*/.doctrees" in content

    def test_adds_cleanup_to_existing_installdocs_override(self, tmp_path: Path) -> None:
        """Test adding cleanup to existing override_dh_installdocs."""
        rules = tmp_path / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@

override_dh_installdocs:
\tdh_installdocs
"""
        )

        result = add_doctree_cleanup(rules)

        assert result is True
        content = rules.read_text()
        assert ".doctrees" in content

    def test_creates_new_installdocs_override(self, tmp_path: Path) -> None:
        """Test creating new override_dh_installdocs when none exists."""
        rules = tmp_path / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@
"""
        )

        result = add_doctree_cleanup(rules)

        assert result is True
        content = rules.read_text()
        assert "override_dh_installdocs:" in content
        assert ".doctrees" in content

    def test_no_change_when_already_present(self, tmp_path: Path) -> None:
        """Test no modification when .doctrees cleanup already exists."""
        rules = tmp_path / "rules"
        original = """#!/usr/bin/make -f
%:
\tdh $@

override_dh_installdocs:
\tdh_installdocs
\trm -rf debian/*/usr/share/doc/*/.doctrees
"""
        rules.write_text(original)

        result = add_doctree_cleanup(rules)

        assert result is False

    def test_missing_file(self, tmp_path: Path) -> None:
        """Test with missing rules file."""
        rules = tmp_path / "rules"
        result = add_doctree_cleanup(rules)
        assert result is False

    def test_read_error_returns_false(self, tmp_path: Path) -> None:
        """Test returns False when file cannot be read."""
        from unittest.mock import patch, MagicMock
        import os

        rules = tmp_path / "rules"
        rules.write_text("test content")

        # Make file unreadable
        original_mode = rules.stat().st_mode
        try:
            rules.chmod(0o000)
            # Some systems (CI, root) can still read, so mock instead
            with patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
                result = add_doctree_cleanup(rules)
            assert result is False
        finally:
            rules.chmod(original_mode)

    def test_write_error_returns_false(self, tmp_path: Path) -> None:
        """Test returns False when file cannot be written."""
        from unittest.mock import patch

        rules = tmp_path / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@
"""
        )

        with patch.object(Path, "write_text", side_effect=OSError("Disk full")):
            result = add_doctree_cleanup(rules)
        assert result is False

    def test_sphinxdoc_override_at_end_of_file(self, tmp_path: Path) -> None:
        """Test adding cleanup when sphinxdoc override is at end of file."""
        rules = tmp_path / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@

override_dh_sphinxdoc:
\tdh_sphinxdoc"""
        )

        result = add_doctree_cleanup(rules)

        assert result is True
        content = rules.read_text()
        assert ".doctrees" in content

    def test_installdocs_override_at_end_of_file(self, tmp_path: Path) -> None:
        """Test adding cleanup when installdocs override is at end of file."""
        rules = tmp_path / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@

override_dh_installdocs:
\tdh_installdocs"""
        )

        result = add_doctree_cleanup(rules)

        assert result is True
        content = rules.read_text()
        assert ".doctrees" in content


class TestEnsureSphinxdocAddon:
    """Tests for ensure_sphinxdoc_addon function."""

    def test_adds_to_with_python3(self, tmp_path: Path) -> None:
        """Test adding sphinxdoc to --with python3."""
        rules = tmp_path / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@ --with python3
"""
        )

        result = ensure_sphinxdoc_addon(rules)

        assert result is True
        content = rules.read_text()
        assert "--with python3,sphinxdoc" in content

    def test_adds_to_dh_call(self, tmp_path: Path) -> None:
        """Test adding sphinxdoc to bare dh $@ call."""
        rules = tmp_path / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@
"""
        )

        result = ensure_sphinxdoc_addon(rules)

        assert result is True
        content = rules.read_text()
        assert "dh $@ --with sphinxdoc" in content

    def test_no_change_when_already_present(self, tmp_path: Path) -> None:
        """Test no modification when sphinxdoc already exists."""
        rules = tmp_path / "rules"
        original = """#!/usr/bin/make -f
%:
\tdh $@ --with python3,sphinxdoc
"""
        rules.write_text(original)

        result = ensure_sphinxdoc_addon(rules)

        assert result is False

    def test_missing_file(self, tmp_path: Path) -> None:
        """Test with missing rules file."""
        rules = tmp_path / "rules"
        result = ensure_sphinxdoc_addon(rules)
        assert result is False

    def test_read_error_returns_false(self, tmp_path: Path) -> None:
        """Test returns False when file cannot be read."""
        from unittest.mock import patch

        rules = tmp_path / "rules"
        rules.write_text("test content")

        with patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
            result = ensure_sphinxdoc_addon(rules)
        assert result is False

    def test_write_error_returns_false(self, tmp_path: Path) -> None:
        """Test returns False when file cannot be written."""
        from unittest.mock import patch

        rules = tmp_path / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@
"""
        )

        with patch.object(Path, "write_text", side_effect=OSError("Disk full")):
            result = ensure_sphinxdoc_addon(rules)
        assert result is False

    def test_no_dh_call_returns_false(self, tmp_path: Path) -> None:
        """Test returns False when no dh call to modify."""
        rules = tmp_path / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tmake build
"""
        )

        result = ensure_sphinxdoc_addon(rules)
        assert result is False


class TestHasOverride:
    """Tests for has_override function."""

    def test_finds_existing_override(self, tmp_path: Path) -> None:
        """Test finding an existing override."""
        rules = tmp_path / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@

override_dh_sphinxdoc:
\tdh_sphinxdoc
"""
        )

        assert has_override(rules, "dh_sphinxdoc") is True

    def test_returns_false_when_not_found(self, tmp_path: Path) -> None:
        """Test returns False when override doesn't exist."""
        rules = tmp_path / "rules"
        rules.write_text(
            """#!/usr/bin/make -f
%:
\tdh $@
"""
        )

        assert has_override(rules, "dh_sphinxdoc") is False

    def test_missing_file(self, tmp_path: Path) -> None:
        """Test with missing rules file."""
        rules = tmp_path / "rules"
        assert has_override(rules, "dh_sphinxdoc") is False

    def test_read_error_returns_false(self, tmp_path: Path) -> None:
        """Test returns False when file cannot be read."""
        from unittest.mock import patch

        rules = tmp_path / "rules"
        rules.write_text("test content")

        with patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
            result = has_override(rules, "dh_sphinxdoc")
        assert result is False
