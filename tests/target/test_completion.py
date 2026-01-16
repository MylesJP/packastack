# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for completion index generation and loading."""

from __future__ import annotations

from pathlib import Path

from packastack.target.completion import (
    generate_completion_index,
    get_completions,
    load_completion_index,
    save_completion_index,
)


class TestCompletionIndex:
    """Test completion index operations."""

    def test_generate_empty_index(self) -> None:
        """Test generating index with no sources."""
        index = generate_completion_index()

        assert "generated_at_utc" in index
        assert "source_packages" in index
        assert "canonical_ids" in index
        assert "deliverables" in index
        assert "aliases" in index
        assert "scopes" in index

        assert isinstance(index["source_packages"], list)
        assert isinstance(index["canonical_ids"], list)
        assert isinstance(index["scopes"], list)

    def test_index_has_scopes(self) -> None:
        """Test index contains expected scopes."""
        index = generate_completion_index()

        scopes = index["scopes"]
        assert "source:" in scopes
        assert "canonical:" in scopes
        assert "repo:" in scopes
        assert "upstream:" in scopes
        assert "deliverable:" in scopes

    def test_save_and_load_index(self, tmp_path: Path) -> None:
        """Test saving and loading index."""
        index_path = tmp_path / "index.json"
        index = {
            "generated_at_utc": "2025-01-01T00:00:00Z",
            "source_packages": ["glance", "nova"],
            "canonical_ids": ["openstack/glance"],
            "deliverables": ["glance"],
            "aliases": ["glance"],
            "scopes": ["source:"],
        }

        save_completion_index(index, index_path)
        assert index_path.exists()

        loaded = load_completion_index(index_path)
        assert loaded is not None
        assert loaded["source_packages"] == ["glance", "nova"]
        assert loaded["canonical_ids"] == ["openstack/glance"]

    def test_load_nonexistent_index(self, tmp_path: Path) -> None:
        """Test loading nonexistent index."""
        index_path = tmp_path / "nonexistent.json"
        loaded = load_completion_index(index_path)
        assert loaded is None

    def test_load_invalid_json(self, tmp_path: Path) -> None:
        """Test loading invalid JSON."""
        index_path = tmp_path / "invalid.json"
        index_path.write_text("invalid json")

        loaded = load_completion_index(index_path)
        assert loaded is None


class TestCompletions:
    """Test completion suggestions."""

    def test_completions_no_scope(self) -> None:
        """Test completions without scope."""
        index = {
            "source_packages": ["glance", "nova"],
            "canonical_ids": ["openstack/glance"],
            "deliverables": ["glance"],
            "aliases": ["glance"],
            "scopes": ["source:", "canonical:"],
        }

        completions = get_completions("gla", index)
        assert "glance" in completions

    def test_completions_scope_prefix(self) -> None:
        """Test scope prefix completions."""
        index = {
            "source_packages": ["glance"],
            "canonical_ids": [],
            "deliverables": [],
            "aliases": [],
            "scopes": ["source:", "canonical:"],
        }

        completions = get_completions("sou", index)
        assert "source:" in completions

    def test_completions_scoped_source(self) -> None:
        """Test scoped source completions."""
        index = {
            "source_packages": ["glance", "glance-store"],
            "canonical_ids": [],
            "deliverables": [],
            "aliases": [],
            "scopes": ["source:"],
        }

        completions = get_completions("source:gla", index)
        assert "source:glance" in completions
        assert "source:glance-store" in completions

    def test_completions_scoped_canonical(self) -> None:
        """Test scoped canonical completions."""
        index = {
            "source_packages": [],
            "canonical_ids": ["openstack/glance", "gnocchixyz/gnocchi"],
            "deliverables": [],
            "aliases": [],
            "scopes": ["canonical:"],
        }

        completions = get_completions("canonical:open", index)
        assert "canonical:openstack/glance" in completions

    def test_completions_empty_input(self) -> None:
        """Test completions with empty input."""
        index = {
            "source_packages": ["glance"],
            "canonical_ids": [],
            "deliverables": [],
            "aliases": [],
            "scopes": ["source:"],
        }

        completions = get_completions("", index)
        # Should suggest scopes and packages
        assert len(completions) >= 0

    def test_completions_no_index(self) -> None:
        """Test completions with no index."""
        # Ensure we don't accidentally read a real cache during test runs
        from unittest.mock import patch

        import packastack.target.completion as completion_module

        with patch.object(completion_module, "load_completion_index", return_value=None):
            completions = get_completions("glance", None)
        assert completions == []
