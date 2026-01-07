# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for completion command."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from packastack.cli import app


@pytest.fixture
def runner() -> CliRunner:
    """Create CLI runner."""
    return CliRunner()


class TestCompletionCommand:
    """Test completion command."""

    def test_bash_completion(self, runner: CliRunner) -> None:
        """Test bash completion generation."""
        result = runner.invoke(app, ["completion", "bash"])
        assert result.exit_code == 0
        assert "bash" in result.stdout.lower()
        assert "packastack" in result.stdout

    def test_zsh_completion(self, runner: CliRunner) -> None:
        """Test zsh completion generation."""
        result = runner.invoke(app, ["completion", "zsh"])
        assert result.exit_code == 0
        assert "zsh" in result.stdout.lower()
        assert "packastack" in result.stdout

    def test_fish_completion(self, runner: CliRunner) -> None:
        """Test fish completion generation."""
        result = runner.invoke(app, ["completion", "fish"])
        assert result.exit_code == 0
        assert "fish" in result.stdout.lower()
        assert "packastack" in result.stdout

    def test_invalid_shell(self, runner: CliRunner) -> None:
        """Test invalid shell type."""
        result = runner.invoke(app, ["completion", "invalid"])
        assert result.exit_code == 1

    def test_case_insensitive(self, runner: CliRunner) -> None:
        """Test shell type is case insensitive."""
        result = runner.invoke(app, ["completion", "BASH"])
        assert result.exit_code == 0
        assert "bash" in result.stdout.lower()
