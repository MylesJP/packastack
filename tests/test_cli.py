# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Tests for CLI entry point."""

import subprocess
import sys

import click


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
    from packastack.cli import cli

    # Test that the CLI function exists and is callable
    assert callable(cli)


from unittest.mock import patch


@patch("packastack.cli._setup_cli_logging", side_effect=Exception("boom"))
def test_cli_logging_setup_failure(mock_setup, tmp_path):
    """Ensure CLI doesn't crash when _setup_cli_logging raises an exception."""
    from packastack.cli import cli
    # _setup_cli_logging patched via decorator to raise an exception

    # Calling the Click group via CliRunner with --help won't execute the
    # group callback. Call the underlying callback directly to exercise the
    # exception handling in the CLI group.
    ctx = click.Context(cli)
    # The underlying function should accept (ctx, root) — call it directly while
    # using the context manager to ensure Click's context stack is active.
    with ctx:
        # Call the original wrapped function to avoid the Click decorator wrapper
        # supplying its own context; the wrapped function requires the Context
        # object as the first argument.
        cli.callback.__wrapped__(ctx, tmp_path)


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
