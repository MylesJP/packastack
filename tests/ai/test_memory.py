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

"""Tests for packastack.ai.memory module."""

from __future__ import annotations

import json
from pathlib import Path

from packastack.ai.memory import (
    MEMORY_FILENAME,
    AIAttempt,
    AIMemory,
    delete_memory,
    find_latest_memory,
    get_memory_path,
    load_memory,
    save_memory,
)


class TestAIAttempt:
    """Tests for AIAttempt dataclass."""

    def test_creation(self) -> None:
        """Test creating an AIAttempt."""
        attempt = AIAttempt(
            timestamp="2025-01-01T00:00:00",
            patch_filename="fix.patch",
            patch_content="diff content",
            build_error="error output",
            diagnosis="test diagnosis",
            outcome="build_failed",
        )
        assert attempt.patch_filename == "fix.patch"
        assert attempt.outcome == "build_failed"


class TestAIMemory:
    """Tests for AIMemory dataclass."""

    def test_add_attempt(self) -> None:
        """Test adding an attempt to memory."""
        memory = AIMemory(package="nova", version="30.0.0")
        memory.add_attempt(
            patch_filename="fix.patch",
            patch_content="diff content",
            build_error="error",
            diagnosis="diagnosis",
            outcome="build_failed",
        )
        assert len(memory.attempts) == 1
        assert memory.attempts[0].patch_filename == "fix.patch"
        assert memory.attempts[0].timestamp  # Should be auto-set

    def test_format_for_prompt_empty(self) -> None:
        """Test format_for_prompt returns empty string when no attempts."""
        memory = AIMemory(package="nova", version="30.0.0")
        assert memory.format_for_prompt() == ""

    def test_format_for_prompt_with_attempts(self) -> None:
        """Test format_for_prompt returns formatted text with attempts."""
        memory = AIMemory(package="nova", version="30.0.0")
        memory.add_attempt(
            patch_filename="fix.patch",
            patch_content="--- a/x\n+++ b/x\n",
            build_error="make: *** Error 2",
            diagnosis="Missing import",
            outcome="build_failed",
        )
        result = memory.format_for_prompt()
        assert "nova" in result
        assert "30.0.0" in result
        assert "Attempt 1" in result
        assert "fix.patch" in result
        assert "build_failed" in result
        assert "Missing import" in result


class TestGetMemoryPath:
    """Tests for get_memory_path function."""

    def test_returns_correct_path(self, tmp_path: Path) -> None:
        """Test that the memory path is inside the run directory."""
        result = get_memory_path(tmp_path)
        assert result == tmp_path / MEMORY_FILENAME
        assert result.name == "ai-memory.json"


class TestSaveMemory:
    """Tests for save_memory function."""

    def test_save_creates_file(self, tmp_path: Path) -> None:
        """Test that save creates a JSON file."""
        memory = AIMemory(package="nova", version="30.0.0")
        memory.add_attempt(
            patch_filename="fix.patch",
            patch_content="diff",
            build_error="error",
            diagnosis="diag",
            outcome="build_failed",
        )
        save_memory(memory, tmp_path)

        path = tmp_path / MEMORY_FILENAME
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["package"] == "nova"
        assert len(data["attempts"]) == 1

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Test that save creates parent directories if needed."""
        run_path = tmp_path / "nested" / "run"
        memory = AIMemory(package="nova", version="1.0")
        save_memory(memory, run_path)
        assert (run_path / MEMORY_FILENAME).exists()

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        """Test that save overwrites an existing memory file."""
        memory1 = AIMemory(package="nova", version="1.0")
        save_memory(memory1, tmp_path)

        memory2 = AIMemory(package="nova", version="2.0")
        memory2.add_attempt(
            patch_filename="fix.patch",
            patch_content="diff",
            build_error="err",
            diagnosis="diag",
            outcome="build_failed",
        )
        save_memory(memory2, tmp_path)

        loaded = load_memory(tmp_path)
        assert loaded is not None
        assert loaded.version == "2.0"
        assert len(loaded.attempts) == 1


class TestLoadMemory:
    """Tests for load_memory function."""

    def test_round_trip(self, tmp_path: Path) -> None:
        """Test save then load returns equivalent memory."""
        memory = AIMemory(package="neutron", version="25.0.0")
        memory.add_attempt(
            patch_filename="fix-py314.patch",
            patch_content="diff content",
            build_error="ImportError",
            diagnosis="Python 3.14 compat",
            outcome="build_failed",
        )
        save_memory(memory, tmp_path)

        loaded = load_memory(tmp_path)
        assert loaded is not None
        assert loaded.package == "neutron"
        assert loaded.version == "25.0.0"
        assert len(loaded.attempts) == 1
        assert loaded.attempts[0].patch_filename == "fix-py314.patch"
        assert loaded.attempts[0].outcome == "build_failed"

    def test_nonexistent_returns_none(self, tmp_path: Path) -> None:
        """Test returns None when file doesn't exist."""
        result = load_memory(tmp_path)
        assert result is None

    def test_invalid_json_returns_none(self, tmp_path: Path) -> None:
        """Test returns None for corrupt JSON."""
        (tmp_path / MEMORY_FILENAME).write_text("not json{{{")
        result = load_memory(tmp_path)
        assert result is None

    def test_missing_fields_returns_none(self, tmp_path: Path) -> None:
        """Test returns None when attempts have missing fields."""
        data = {"package": "nova", "version": "1.0", "attempts": [{"bad": "data"}]}
        (tmp_path / MEMORY_FILENAME).write_text(json.dumps(data))
        result = load_memory(tmp_path)
        assert result is None


class TestDeleteMemory:
    """Tests for delete_memory function."""

    def test_deletes_existing_file(self, tmp_path: Path) -> None:
        """Test that an existing memory file is deleted."""
        memory = AIMemory(package="nova", version="1.0")
        save_memory(memory, tmp_path)
        assert (tmp_path / MEMORY_FILENAME).exists()

        delete_memory(tmp_path)
        assert not (tmp_path / MEMORY_FILENAME).exists()

    def test_nonexistent_is_noop(self, tmp_path: Path) -> None:
        """Test that deleting a nonexistent file is a no-op."""
        delete_memory(tmp_path)  # Should not raise


class TestFindLatestMemory:
    """Tests for find_latest_memory function."""

    def test_finds_most_recent_for_package(self, tmp_path: Path) -> None:
        """Test finds the newest memory for the requested package."""
        # Create two build directories with memory for the same package
        run1 = tmp_path / "20260101-000000"
        run2 = tmp_path / "20260102-000000"
        run1.mkdir()
        run2.mkdir()

        memory1 = AIMemory(package="nova", version="1.0")
        memory1.add_attempt("old.patch", "old", "err", "diag", "build_failed")
        save_memory(memory1, run1)

        memory2 = AIMemory(package="nova", version="2.0")
        memory2.add_attempt("new.patch", "new", "err", "diag", "build_failed")
        save_memory(memory2, run2)

        result = find_latest_memory(tmp_path, "nova")
        assert result is not None
        assert result.version == "2.0"
        assert result.attempts[0].patch_filename == "new.patch"

    def test_returns_none_when_no_match(self, tmp_path: Path) -> None:
        """Test returns None when no memory for the package."""
        run = tmp_path / "20260101-000000"
        run.mkdir()
        save_memory(AIMemory(package="nova", version="1.0"), run)

        result = find_latest_memory(tmp_path, "neutron")
        assert result is None

    def test_returns_none_when_runs_root_missing(self, tmp_path: Path) -> None:
        """Test returns None when runs root doesn't exist."""
        result = find_latest_memory(tmp_path / "nonexistent", "nova")
        assert result is None
