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

"""Tests for packastack.build.tools module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from packastack.build import tools


class TestToolCheck:
    """Tests for ToolCheck dataclass."""

    def test_is_complete_with_all_tools(self) -> None:
        """Test is_complete returns True when no tools are missing."""
        check = tools.ToolCheck(
            tools={"git": Path("/usr/bin/git"), "gbp": Path("/usr/bin/gbp")},
            missing=[],
        )
        assert check.is_complete() is True

    def test_is_complete_with_missing_tools(self) -> None:
        """Test is_complete returns False when tools are missing."""
        check = tools.ToolCheck(
            tools={"git": Path("/usr/bin/git"), "gbp": None},
            missing=["gbp"],
        )
        assert check.is_complete() is False

    def test_get_path_returns_path(self) -> None:
        """Test get_path returns Path when tool exists."""
        check = tools.ToolCheck(
            tools={"git": Path("/usr/bin/git")},
            missing=[],
        )
        assert check.get_path("git") == Path("/usr/bin/git")

    def test_get_path_returns_none_for_missing(self) -> None:
        """Test get_path returns None for missing tool."""
        check = tools.ToolCheck(
            tools={"git": None},
            missing=["git"],
        )
        assert check.get_path("git") is None

    def test_get_path_returns_none_for_unknown(self) -> None:
        """Test get_path returns None for unknown tool."""
        check = tools.ToolCheck(tools={}, missing=[])
        assert check.get_path("nonexistent") is None


class TestFindTool:
    """Tests for find_tool function."""

    def test_finds_existing_tool(self) -> None:
        """Test finding an existing tool (like python3)."""
        # python3 should always be available in our test environment
        path = tools.find_tool("python3")
        assert path is not None
        assert path.exists()

    def test_returns_none_for_missing_tool(self) -> None:
        """Test returning None for non-existent tool."""
        path = tools.find_tool("definitely-nonexistent-tool-12345")
        assert path is None

    def test_returns_path_object(self) -> None:
        """Test that find_tool returns a Path object."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/git"
            path = tools.find_tool("git")
            assert isinstance(path, Path)
            assert path == Path("/usr/bin/git")


class TestCheckRequiredTools:
    """Tests for check_required_tools function."""

    def test_all_tools_found(self) -> None:
        """Test when all required tools are available."""
        with patch.object(tools, "find_tool") as mock_find:
            mock_find.return_value = Path("/usr/bin/tool")
            check = tools.check_required_tools(need_sbuild=False, need_gpg=False)

            assert check.is_complete()
            assert len(check.missing) == 0
            for tool in tools.REQUIRED_TOOLS:
                assert check.tools[tool] == Path("/usr/bin/tool")

    def test_some_tools_missing(self) -> None:
        """Test when some tools are missing."""
        def mock_find(name: str) -> Path | None:
            if name == "gbp":
                return None
            return Path(f"/usr/bin/{name}")

        with patch.object(tools, "find_tool", side_effect=mock_find):
            check = tools.check_required_tools()

            assert not check.is_complete()
            assert "gbp" in check.missing
            assert check.tools["gbp"] is None
            assert check.tools["git"] == Path("/usr/bin/git")

    def test_sbuild_required(self) -> None:
        """Test that sbuild is checked when need_sbuild is True."""
        with patch.object(tools, "find_tool") as mock_find:
            mock_find.return_value = Path("/usr/bin/tool")
            check = tools.check_required_tools(need_sbuild=True)

            assert "sbuild" in check.tools
            mock_find.assert_any_call("sbuild")

    def test_sbuild_not_required(self) -> None:
        """Test that sbuild is not checked when need_sbuild is False."""
        with patch.object(tools, "find_tool") as mock_find:
            mock_find.return_value = Path("/usr/bin/tool")
            check = tools.check_required_tools(need_sbuild=False)

            assert "sbuild" not in check.tools

    def test_gpg_required(self) -> None:
        """Test that gpg is checked when need_gpg is True."""
        with patch.object(tools, "find_tool") as mock_find:
            mock_find.return_value = Path("/usr/bin/tool")
            check = tools.check_required_tools(need_gpg=True)

            assert "gpg" in check.tools
            mock_find.assert_any_call("gpg")

    def test_gpg_not_required(self) -> None:
        """Test that gpg is not checked when need_gpg is False."""
        with patch.object(tools, "find_tool") as mock_find:
            mock_find.return_value = Path("/usr/bin/tool")
            check = tools.check_required_tools(need_gpg=False)

            assert "gpg" not in check.tools

    def test_missing_sbuild_added_to_missing_list(self) -> None:
        """Test that missing sbuild is added to missing list."""
        def mock_find(name: str) -> Path | None:
            if name == "sbuild":
                return None
            return Path(f"/usr/bin/{name}")

        with patch.object(tools, "find_tool", side_effect=mock_find):
            check = tools.check_required_tools(need_sbuild=True)

            assert "sbuild" in check.missing


class TestGetMissingToolsMessage:
    """Tests for get_missing_tools_message function."""

    def test_empty_list_returns_empty_string(self) -> None:
        """Test that empty list returns empty message."""
        message = tools.get_missing_tools_message([])
        assert message == ""

    def test_single_missing_tool(self) -> None:
        """Test message for single missing tool."""
        message = tools.get_missing_tools_message(["git"])
        assert "git" in message
        assert "apt install git" in message

    def test_multiple_missing_tools(self) -> None:
        """Test message for multiple missing tools."""
        message = tools.get_missing_tools_message(["git", "gbp", "dch"])
        assert "git" in message
        assert "gbp" in message
        assert "dch" in message
        assert "Quick install" in message

    def test_includes_install_instructions(self) -> None:
        """Test that install instructions are included."""
        message = tools.get_missing_tools_message(["gbp"])
        assert "git-buildpackage" in message

    def test_includes_apt_install_command(self) -> None:
        """Test that combined apt install command is included."""
        message = tools.get_missing_tools_message(["git", "gbp"])
        assert "sudo apt install git git-buildpackage" in message

    def test_sbuild_instructions(self) -> None:
        """Test that sbuild includes adduser instruction."""
        message = tools.get_missing_tools_message(["sbuild"])
        assert "sbuild-adduser" in message


class TestValidateToolsForBuild:
    """Tests for validate_tools_for_build function."""

    def test_all_tools_available(self) -> None:
        """Test validation when all tools are available."""
        with patch.object(tools, "check_required_tools") as mock_check:
            mock_check.return_value = tools.ToolCheck(
                tools={"git": Path("/usr/bin/git")},
                missing=[],
            )
            success, message = tools.validate_tools_for_build(binary=False)

            assert success is True
            assert "available" in message

    def test_tools_missing(self) -> None:
        """Test validation when tools are missing."""
        with patch.object(tools, "check_required_tools") as mock_check:
            mock_check.return_value = tools.ToolCheck(
                tools={"git": None},
                missing=["git"],
            )
            success, message = tools.validate_tools_for_build(binary=False)

            assert success is False
            assert "git" in message

    def test_binary_flag_passed_to_check(self) -> None:
        """Test that binary flag is passed to check_required_tools."""
        with patch.object(tools, "check_required_tools") as mock_check:
            mock_check.return_value = tools.ToolCheck(tools={}, missing=[])
            tools.validate_tools_for_build(binary=True)

            mock_check.assert_called_once_with(need_sbuild=True, need_gpg=True)

    def test_gpg_always_checked(self) -> None:
        """Test that gpg is always checked for build validation."""
        with patch.object(tools, "check_required_tools") as mock_check:
            mock_check.return_value = tools.ToolCheck(tools={}, missing=[])
            tools.validate_tools_for_build(binary=False)

            mock_check.assert_called_once_with(need_sbuild=False, need_gpg=True)


class TestConstants:
    """Tests for module-level constants."""

    def test_required_tools_list(self) -> None:
        """Test that required tools list contains expected tools."""
        assert "git" in tools.REQUIRED_TOOLS
        assert "gbp" in tools.REQUIRED_TOOLS
        assert "dch" in tools.REQUIRED_TOOLS
        assert "dpkg-source" in tools.REQUIRED_TOOLS

    def test_optional_tools_list(self) -> None:
        """Test that optional tools list contains expected tools."""
        assert "sbuild" in tools.OPTIONAL_TOOLS
        assert "gpg" in tools.OPTIONAL_TOOLS

    def test_install_instructions_complete(self) -> None:
        """Test that all required tools have install instructions."""
        for tool in tools.REQUIRED_TOOLS:
            assert tool in tools.INSTALL_INSTRUCTIONS

    def test_tool_packages_mapping(self) -> None:
        """Test that tool to package mapping is correct."""
        assert tools.TOOL_PACKAGES["git"] == "git"
        assert tools.TOOL_PACKAGES["gbp"] == "git-buildpackage"
        assert tools.TOOL_PACKAGES["dch"] == "devscripts"
        assert tools.TOOL_PACKAGES["dpkg-source"] == "dpkg-dev"
