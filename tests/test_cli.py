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

"""Tests for packastack CLI module."""

from __future__ import annotations

from typer.testing import CliRunner

from packastack.cli import app

runner = CliRunner()


class TestCliHelp:
    """Tests for CLI help output."""

    def test_main_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "packastack" in result.output.lower() or "Usage" in result.output

    def test_init_help(self) -> None:
        result = runner.invoke(app, ["init", "--help"])
        assert result.exit_code == 0
        assert "init" in result.output.lower() or "Initialize" in result.output

    def test_refresh_help(self) -> None:
        result = runner.invoke(app, ["refresh", "--help"])
        assert result.exit_code == 0
        assert "refresh" in result.output.lower() or "Ubuntu" in result.output


class TestCliCommands:
    """Tests for CLI command registration."""

    def test_init_command_registered(self) -> None:
        # Check that init is accessible
        result = runner.invoke(app, ["init", "--help"])
        assert result.exit_code == 0

    def test_refresh_command_registered(self) -> None:
        # Check that refresh is accessible
        result = runner.invoke(app, ["refresh", "--help"])
        assert result.exit_code == 0


class TestCliOptions:
    """Tests for CLI option parsing."""

    def test_refresh_accepts_ubuntu_series(self) -> None:
        result = runner.invoke(app, ["refresh", "--help"])
        assert "--ubuntu-series" in result.output

    def test_refresh_accepts_pockets(self) -> None:
        result = runner.invoke(app, ["refresh", "--help"])
        assert "--pockets" in result.output

    def test_refresh_accepts_components(self) -> None:
        result = runner.invoke(app, ["refresh", "--help"])
        assert "--components" in result.output

    def test_refresh_accepts_arches(self) -> None:
        result = runner.invoke(app, ["refresh", "--help"])
        assert "--arches" in result.output

    def test_refresh_accepts_mirror(self) -> None:
        result = runner.invoke(app, ["refresh", "--help"])
        assert "--mirror" in result.output

    def test_refresh_accepts_ttl(self) -> None:
        result = runner.invoke(app, ["refresh", "--help"])
        assert "--ttl" in result.output

    def test_refresh_accepts_force(self) -> None:
        result = runner.invoke(app, ["refresh", "--help"])
        assert "--force" in result.output

    def test_refresh_accepts_offline(self) -> None:
        result = runner.invoke(app, ["refresh", "--help"])
        assert "--offline" in result.output

    def test_init_accepts_prime(self) -> None:
        result = runner.invoke(app, ["init", "--help"])
        assert "--prime" in result.output
