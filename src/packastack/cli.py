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

"""CLI application definition for Packastack."""

from __future__ import annotations

from typer import Typer

from packastack.commands.init import init
from packastack.commands.plan import plan
from packastack.commands.refresh import refresh

app: Typer = Typer(
    name="packastack",
    help="A tool for building OpenStack packages for Ubuntu.",
    add_completion=False,
)

# Register commands
app.command(name="init")(init)
app.command(name="plan")(plan)
app.command(name="refresh")(refresh)
