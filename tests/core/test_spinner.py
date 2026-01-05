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

"""Tests for packastack.core.spinner module."""

from __future__ import annotations

from unittest import mock

import pytest

from packastack.core import spinner


class TestIsTty:
    """Tests for is_tty function."""

    def test_returns_true_when_stdout_is_tty(self, tty_stdout: None) -> None:
        assert spinner.is_tty() is True

    def test_returns_false_when_stdout_is_not_tty(self, non_tty_stdout: None) -> None:
        assert spinner.is_tty() is False

    def test_returns_false_on_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_stdout = mock.MagicMock()
        mock_stdout.isatty.side_effect = AttributeError("no isatty")
        monkeypatch.setattr("sys.__stdout__", mock_stdout)

        assert spinner.is_tty() is False


class TestActivitySpinner:
    """Tests for activity_spinner context manager."""

    def test_prints_text_when_not_tty(self, non_tty_stdout: None) -> None:
        output = []
        with mock.patch("sys.__stdout__") as mock_stdout:
            mock_stdout.isatty.return_value = False
            mock_stdout.write = lambda x: output.append(x)

            with spinner.activity_spinner("test", "doing work"):
                pass

        full_output = "".join(output)
        assert "[test]" in full_output
        assert "doing work" in full_output

    def test_no_spinner_when_disabled(self, tty_stdout: None) -> None:
        output = []
        with mock.patch("sys.__stdout__") as mock_stdout:
            mock_stdout.isatty.return_value = True
            mock_stdout.write = lambda x: output.append(x)
            mock_stdout.flush = lambda: None

            # When disabled, should just print text
            with spinner.activity_spinner("test", "doing work", disable=True):
                pass

        full_output = "".join(output)
        assert "[test]" in full_output

    def test_yields_control_to_block(self, non_tty_stdout: None) -> None:
        executed = False

        with mock.patch("sys.__stdout__"), spinner.activity_spinner("test", "working"):
            executed = True

        assert executed

    def test_phase_and_description_in_output(self, non_tty_stdout: None) -> None:
        output = []
        with mock.patch("sys.__stdout__") as mock_stdout:
            mock_stdout.isatty.return_value = False
            mock_stdout.write = lambda x: output.append(x)

            with spinner.activity_spinner("refresh", "Fetching packages"):
                pass

        full_output = "".join(output)
        assert "[refresh]" in full_output
        assert "Fetching packages" in full_output
