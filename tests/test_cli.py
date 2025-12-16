# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Tests for CLI entry point."""

import io
import subprocess
import sys
from unittest.mock import patch

import pytest



def test_cli_main_execution():
    """Test that CLI can be executed as a script."""
    # Test that the cli module can be executed
    result = subprocess.run(
        [sys.executable, "-m", "packastack.cli", "--help"],
        capture_output=True,
        text=True,
    )

    # Should exit successfully and show help
    assert result.returncode == 0
    assert "Packastack" in result.stdout or "OpenStack" in result.stdout


def test_cli_import():
    """Test that CLI can be imported."""
    from packastack.cli import PackastackApp

    app = PackastackApp(stdout=io.StringIO())
    assert app.command_manager.find_command("import")


@patch(
    "packastack.cli._setup_cli_logging",
    side_effect=Exception("boom"),
)
def test_cli_logging_setup_failure(mock_setup, tmp_path):
    """Ensure CLI doesn't crash when _setup_cli_logging raises an exception."""
    from packastack.cli import PackastackApp

    app = PackastackApp(stdout=io.StringIO())
    # Should exit gracefully even though logging setup fails
    assert app.run(["--root", str(tmp_path), "--help"]) == 0


def test_cli_main_block():
    """Test the if __name__ == '__main__' block."""
    # Test that the cli module can be executed directly using -m
    # Direct script execution doesn't work for package-relative imports
    result = subprocess.run(
        [sys.executable, "-m", "packastack.cli", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Packastack" in result.stdout or "OpenStack" in result.stdout
