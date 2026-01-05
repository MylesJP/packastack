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

"""TTY-aware spinner for activity indication.

Uses Rich spinners when stdout is a TTY, falls back to plain text otherwise.
The spinner output is written directly to the real terminal (sys.__stdout__)
and never goes into captured log files.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner


def is_tty() -> bool:
    """Return True if stdout is a TTY."""
    try:
        if sys.__stdout__ is None:
            return False  # pragma: no cover
        return sys.__stdout__.isatty()
    except Exception:  # pragma: no cover
        return False


@contextlib.contextmanager
def activity_spinner(phase: str, description: str, disable: bool = False) -> Iterator[None]:
    """Context manager that shows a spinner while the wrapped block runs.

    Args:
        phase: Short phase label (e.g., "init", "refresh").
        description: Human-readable description of current activity.
        disable: Force disable spinner even on TTY.

    When stdout is not a TTY or disable is True, the activity line is printed
    without animation and immediately returned.
    """

    text = f"[{phase}] {description}"

    # Non-TTY or explicitly disabled: print once and return.
    if disable or not is_tty():
        with contextlib.suppress(Exception):  # pragma: no cover
            print(text, file=sys.__stdout__, flush=True)
        yield
        return

    # TTY: show a Rich spinner that clears when done, then print the completed line.
    console = Console(file=sys.__stdout__, force_terminal=True)
    spinner = Spinner("dots", text=text)
    with Live(spinner, console=console, refresh_per_second=12, transient=True):
        yield

    # After spinner clears, print the completed activity on its own line.
    with contextlib.suppress(Exception):  # pragma: no cover
        print(text, file=sys.__stdout__, flush=True)


if __name__ == "__main__":
    import time

    with activity_spinner("demo", "doing some work"):
        time.sleep(2)
