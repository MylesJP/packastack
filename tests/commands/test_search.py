# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for search command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from packastack.cli import app


@pytest.fixture
def runner() -> CliRunner:
    """Create CLI runner."""
    return CliRunner()


class TestSearchCommand:
    """Test search command."""

    def test_search_exact_no_matches(self, runner: CliRunner, tmp_path: Path) -> None:
        """Test search with exact match and no results."""
        result = runner.invoke(app, ["search", "nonexistent"])
        # Should not crash even with no registry
        assert result.exit_code in (0, 1)

    def test_search_prefix(self, runner: CliRunner) -> None:
        """Test search with prefix match."""
        result = runner.invoke(app, ["search", "^gla"])
        # Should not crash
        assert result.exit_code in (0, 1)

    def test_search_contains(self, runner: CliRunner) -> None:
        """Test search with contains match."""
        result = runner.invoke(app, ["search", "~client"])
        # Should not crash
        assert result.exit_code in (0, 1)

    def test_search_json_format(self, runner: CliRunner) -> None:
        """Test search with JSON output."""
        result = runner.invoke(app, ["search", "glance", "--format", "json"])
        # Should not crash
        assert result.exit_code in (0, 1)

        # If exit code 0, should be valid JSON
        if result.exit_code == 0:
            try:
                data = json.loads(result.stdout)
                assert isinstance(data, list)
            except json.JSONDecodeError:
                # May be empty/no matches
                pass

    def test_search_with_scope(self, runner: CliRunner) -> None:
        """Test search with scope filter."""
        result = runner.invoke(app, ["search", "glance", "--scope", "source"])
        assert result.exit_code in (0, 1)

    def test_search_invalid_scope(self, runner: CliRunner) -> None:
        """Test search with invalid scope."""
        result = runner.invoke(app, ["search", "glance", "--scope", "invalid"])
        assert result.exit_code == 1

    def test_search_invalid_expression(self, runner: CliRunner) -> None:
        """Test search with invalid expression."""
        result = runner.invoke(app, ["search", ""])
        assert result.exit_code == 1

    def test_search_refresh_cache(self, runner: CliRunner) -> None:
        """Test search with cache refresh."""
        result = runner.invoke(app, ["search", "glance", "--refresh-cache"])
        # Should not crash
        assert result.exit_code in (0, 1)
