# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for canonical upstream identifiers in registry."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from packastack.upstream.registry import (
    UpstreamsRegistry,
    load_registry,
)


class TestCanonicalIdentifiers:
    """Test canonical upstream identifier handling."""

    def test_registry_loads_canonical_from_yaml(self) -> None:
        """Test that canonical IDs are loaded from upstreams.yaml."""
        registry = UpstreamsRegistry()

        # Test explicit canonical for gnocchi
        resolved = registry.resolve("gnocchi", openstack_governed=False)
        assert resolved.config.provenance.canonical == "gnocchixyz/gnocchi"
        assert not resolved.config.provenance.inferred

    def test_registry_infers_canonical_for_openstack(self) -> None:
        """Test that canonical IDs are inferred for OpenStack projects."""
        registry = UpstreamsRegistry()

        # Test inferred canonical for OpenStack project (using defaults)
        resolved = registry.resolve("nova", openstack_governed=True)
        assert resolved.config.provenance.canonical == "openstack/nova"
        assert resolved.config.provenance.inferred

    def test_custom_registry_with_canonical(self, tmp_path: Path) -> None:
        """Test loading registry with explicit canonical field."""
        registry_file = tmp_path / "upstreams.yaml"
        registry_file.write_text("""
version: 2
defaults:
  upstream:
    type: git
    host: opendev
projects:
  testproject:
    canonical: myorg/testproject
    common_names: [testproject]
    upstream:
      type: git
      host: github
      url: https://github.com/myorg/testproject.git
""")

        registry = UpstreamsRegistry(canonical_path=registry_file)
        resolved = registry.resolve("testproject", openstack_governed=False)

        assert resolved.config.provenance.canonical == "myorg/testproject"
        assert not resolved.config.provenance.inferred

    def test_list_projects(self) -> None:
        """Test listing all projects in registry."""
        registry = UpstreamsRegistry()
        projects = registry.list_projects()

        assert isinstance(projects, list)
        assert "gnocchi" in projects
        assert "alembic" in projects
        assert "git-review" in projects
        assert "rally" in projects

    def test_retired_projects_have_canonical(self) -> None:
        """Test that retired projects also have canonical IDs."""
        registry = UpstreamsRegistry()

        # alembic is retired
        resolved = registry.resolve("alembic", openstack_governed=False)
        assert resolved.config.retired
        assert resolved.config.provenance.canonical == "sqlalchemy/alembic"

        # rally is retired
        resolved = registry.resolve("rally", openstack_governed=False)
        assert resolved.config.retired
        assert resolved.config.provenance.canonical == "openstack/rally"

    def test_canonical_in_registry_load_result(self, tmp_path: Path) -> None:
        """Test that canonical field is preserved in raw registry data."""
        registry_file = tmp_path / "upstreams.yaml"
        registry_file.write_text("""
version: 2
defaults: {}
projects:
  testproj:
    canonical: org/proj
""")

        result = load_registry(canonical_path=registry_file, override_path=None)
        assert "testproj" in result.projects
        assert result.projects["testproj"]["canonical"] == "org/proj"
