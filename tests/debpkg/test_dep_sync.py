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

"""Tests for dependency sync module."""

from pathlib import Path
from unittest.mock import MagicMock

from packastack.debpkg.control import ParsedDependency
from packastack.debpkg.dep_sync import (
    SyncResult,
    VersionBump,
    apply_version_bumps,
    compute_version_bumps,
    get_lts_version,
    resolve_upstream_version_constraint,
    sync_upstream_deps,
)
from packastack.planning.validated_plan import UpstreamDeps


class TestVersionBump:
    """Tests for VersionBump dataclass."""

    def test_version_bump_creation(self):
        """Test creating a VersionBump."""
        bump = VersionBump(
            debian_package="python3-oslo.config",
            python_package="oslo.config",
            old_version="1.0.0",
            new_version="2.0.0",
            source="manifest",
        )
        assert bump.debian_package == "python3-oslo.config"
        assert bump.python_package == "oslo.config"
        assert bump.old_version == "1.0.0"
        assert bump.new_version == "2.0.0"
        assert bump.source == "manifest"


class TestSyncResult:
    """Tests for SyncResult dataclass."""

    def test_empty_result(self):
        """Test empty SyncResult defaults."""
        result = SyncResult()
        assert result.additions == []
        assert result.version_bumps == []
        assert result.unresolved == []
        assert result.warnings == []
        assert result.from_manifest == []
        assert result.from_lts == []


class TestGetLtsVersion:
    """Tests for get_lts_version function."""

    def test_with_none_index(self):
        """Test with no package index."""
        result = get_lts_version("python3-oslo.config", None)
        assert result is None

    def test_with_index(self):
        """Test with a package index."""
        mock_index = MagicMock()
        mock_index.get_version.return_value = "1:9.0.0-0ubuntu1"

        result = get_lts_version("python3-oslo.config", mock_index)
        assert result == "1:9.0.0-0ubuntu1"

    def test_with_index_not_found(self):
        """Test when package not in index."""
        mock_index = MagicMock()
        mock_index.get_version.side_effect = Exception("Not found")

        result = get_lts_version("python3-nonexistent", mock_index)
        assert result is None


class TestResolveUpstreamVersionConstraint:
    """Tests for resolve_upstream_version_constraint function."""

    def test_from_manifest(self):
        """Test version resolution from manifest."""
        mock_manifest = MagicMock()
        mock_manifest.is_in_manifest.return_value = True
        mock_manifest.get_version.return_value = "9.0.0-0ubuntu1"

        version, source = resolve_upstream_version_constraint(
            python_package="oslo.config",
            version_spec=">=8.0.0",
            debian_package="python3-oslo.config",
            manifest=mock_manifest,
            ubuntu_index=None,
        )

        assert version == "9.0.0"
        assert source == "manifest"

    def test_from_manifest_with_epoch(self):
        """Test version resolution from manifest with epoch."""
        mock_manifest = MagicMock()
        mock_manifest.is_in_manifest.return_value = True
        mock_manifest.get_version.return_value = "1:9.0.0-0ubuntu1"

        version, source = resolve_upstream_version_constraint(
            python_package="keystoneclient",
            version_spec=">=8.0.0",
            debian_package="python3-keystoneclient",
            manifest=mock_manifest,
            ubuntu_index=None,
        )

        assert version == "9.0.0"
        assert source == "manifest"

    def test_from_lts_when_not_in_manifest(self):
        """Test version resolution from LTS when not in manifest."""
        mock_manifest = MagicMock()
        mock_manifest.is_in_manifest.return_value = False

        mock_index = MagicMock()
        mock_index.get_version.return_value = "8.5.0-0ubuntu1"

        version, source = resolve_upstream_version_constraint(
            python_package="oslo.config",
            version_spec=">=8.0.0",
            debian_package="python3-oslo.config",
            manifest=mock_manifest,
            ubuntu_index=mock_index,
        )

        assert version == "8.5.0"
        assert source == "lts"

    def test_from_upstream_spec(self):
        """Test version from upstream specifier when no other source."""
        version, source = resolve_upstream_version_constraint(
            python_package="oslo.config",
            version_spec=">=8.0.0,<10.0.0",
            debian_package="python3-oslo.config",
            manifest=None,
            ubuntu_index=None,
        )

        assert version == "8.0.0"
        assert source == "upstream_spec"

    def test_from_upstream_spec_exact(self):
        """Test version from exact upstream specifier."""
        version, source = resolve_upstream_version_constraint(
            python_package="oslo.config",
            version_spec="==9.0.0",
            debian_package="python3-oslo.config",
            manifest=None,
            ubuntu_index=None,
        )

        assert version == "9.0.0"
        assert source == "upstream_spec"

    def test_no_version_available(self):
        """Test when no version source is available."""
        version, source = resolve_upstream_version_constraint(
            python_package="oslo.config",
            version_spec="",
            debian_package="python3-oslo.config",
            manifest=None,
            ubuntu_index=None,
        )

        assert version == ""
        assert source == "none"


class TestComputeVersionBumps:
    """Tests for compute_version_bumps function."""

    def test_empty_deps(self):
        """Test with empty dependencies."""
        result = compute_version_bumps(
            existing_deps=[],
            upstream_deps=UpstreamDeps(),
        )

        assert result.additions == []
        assert result.version_bumps == []

    def test_new_dependency_addition(self):
        """Test adding a new dependency."""
        upstream_deps = UpstreamDeps(
            runtime=[("oslo.config", ">=8.0.0")],
        )

        result = compute_version_bumps(
            existing_deps=[],
            upstream_deps=upstream_deps,
        )

        assert len(result.additions) == 1
        assert result.additions[0].name == "python3-oslo.config"

    def test_version_bump_when_newer(self):
        """Test version bump when upstream is newer."""
        existing = [
            ParsedDependency(name="python3-oslo.config", relation=">=", version="7.0.0"),
        ]
        upstream_deps = UpstreamDeps(
            runtime=[("oslo-config", ">=8.0.0")],
        )

        result = compute_version_bumps(
            existing_deps=existing,
            upstream_deps=upstream_deps,
        )

        assert len(result.version_bumps) == 1
        assert result.version_bumps[0].debian_package == "python3-oslo.config"
        assert result.version_bumps[0].old_version == "7.0.0"
        assert result.version_bumps[0].new_version == "8.0.0"

    def test_no_bump_when_same_version(self):
        """Test no bump when versions are the same."""
        existing = [
            ParsedDependency(name="python3-oslo.config", relation=">=", version="8.0.0"),
        ]
        upstream_deps = UpstreamDeps(
            runtime=[("oslo-config", ">=8.0.0")],
        )

        result = compute_version_bumps(
            existing_deps=existing,
            upstream_deps=upstream_deps,
        )

        assert len(result.version_bumps) == 0

    def test_tracks_manifest_source(self):
        """Test that manifest sources are tracked."""
        mock_manifest = MagicMock()
        mock_manifest.is_in_manifest.return_value = True
        mock_manifest.get_version.return_value = "9.0.0-0ubuntu1"

        existing = [
            ParsedDependency(name="python3-oslo.config", relation=">=", version="7.0.0"),
        ]
        upstream_deps = UpstreamDeps(
            runtime=[("oslo-config", ">=8.0.0")],
        )

        result = compute_version_bumps(
            existing_deps=existing,
            upstream_deps=upstream_deps,
            manifest=mock_manifest,
        )

        assert "python3-oslo.config" in result.from_manifest

    def test_skips_stdlib(self):
        """Test that stdlib packages are skipped."""
        upstream_deps = UpstreamDeps(
            runtime=[("python", ""), ("setuptools", ">=40.0.0")],
        )

        result = compute_version_bumps(
            existing_deps=[],
            upstream_deps=upstream_deps,
        )

        # python should be skipped (maps to empty)
        # setuptools should be added
        dep_names = [d.name for d in result.additions]
        assert "python3-setuptools" in dep_names


class TestApplyVersionBumps:
    """Tests for apply_version_bumps function."""

    def test_apply_single_bump(self):
        """Test applying a single version bump."""
        deps = [
            ParsedDependency(name="python3-oslo.config", relation=">=", version="7.0.0"),
            ParsedDependency(name="python3-oslo.log"),
        ]
        bumps = [
            VersionBump(
                debian_package="python3-oslo.config",
                python_package="oslo.config",
                old_version="7.0.0",
                new_version="8.0.0",
                source="manifest",
            ),
        ]

        result = apply_version_bumps(deps, bumps)

        assert len(result) == 2
        assert result[0].name == "python3-oslo.config"
        assert result[0].version == "8.0.0"
        assert result[1].name == "python3-oslo.log"
        assert result[1].version == ""

    def test_apply_preserves_arch_qualifiers(self):
        """Test that arch qualifiers are preserved."""
        deps = [
            ParsedDependency(
                name="python3-oslo.config",
                relation=">=",
                version="7.0.0",
                arch_qualifiers=["amd64"],
            ),
        ]
        bumps = [
            VersionBump(
                debian_package="python3-oslo.config",
                python_package="oslo.config",
                old_version="7.0.0",
                new_version="8.0.0",
                source="manifest",
            ),
        ]

        result = apply_version_bumps(deps, bumps)

        assert result[0].arch_qualifiers == ["amd64"]

    def test_apply_no_matching_bump(self):
        """Test when no bump matches."""
        deps = [
            ParsedDependency(name="python3-oslo.config", relation=">=", version="7.0.0"),
        ]
        bumps = [
            VersionBump(
                debian_package="python3-oslo.log",
                python_package="oslo.log",
                old_version="5.0.0",
                new_version="6.0.0",
                source="manifest",
            ),
        ]

        result = apply_version_bumps(deps, bumps)

        assert len(result) == 1
        assert result[0].version == "7.0.0"  # Unchanged


class TestSyncUpstreamDeps:
    """Tests for sync_upstream_deps function."""

    def test_missing_control_file(self, tmp_path: Path):
        """Test with missing debian/control."""
        packaging_repo = tmp_path / "packaging"
        packaging_repo.mkdir()
        upstream_repo = tmp_path / "upstream"
        upstream_repo.mkdir()
        (upstream_repo / "requirements.txt").write_text("oslo.config>=8.0.0\n")

        result = sync_upstream_deps(
            packaging_repo=packaging_repo,
            upstream_repo=upstream_repo,
        )

        assert "debian/control not found" in result.warnings

    def test_basic_sync(self, tmp_path: Path):
        """Test basic dependency sync."""
        # Setup packaging repo with d/control
        packaging_repo = tmp_path / "packaging"
        (packaging_repo / "debian").mkdir(parents=True)
        (packaging_repo / "debian" / "control").write_text(
            """Source: nova
Maintainer: Test <test@example.com>
Build-Depends: debhelper-compat (= 13),
               python3-oslo.config (>= 7.0.0)

Package: nova
Architecture: all
Depends: python3-oslo.config (>= 7.0.0)
Description: Test package
"""
        )

        # Setup upstream repo with requirements
        upstream_repo = tmp_path / "upstream"
        upstream_repo.mkdir()
        (upstream_repo / "requirements.txt").write_text("oslo-config>=8.0.0\n")

        result = sync_upstream_deps(
            packaging_repo=packaging_repo,
            upstream_repo=upstream_repo,
        )

        # Should detect version bump needed
        assert len(result.version_bumps) >= 1
        bump_packages = [b.debian_package for b in result.version_bumps]
        assert "python3-oslo.config" in bump_packages
