# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Tests for base importer."""

from unittest.mock import patch

import pytest

from packastack.exceptions import ImporterError
from packastack.importer.base import BaseImporter


class ConcreteImporter(BaseImporter):
    """Concrete implementation for testing."""

    def get_version(self):
        return "1.0.0"

    def get_tarball(self, version):
        return self.tarballs_dir / f"test-{version}.tar.gz"

    def convert_version(self, upstream_version):
        return upstream_version


@pytest.fixture
def importer_paths(tmp_path):
    """Create temporary paths for importer."""
    packaging = tmp_path / "packaging"
    upstream = tmp_path / "upstream"
    tarballs = tmp_path / "tarballs"
    releases = tmp_path / "releases"

    packaging.mkdir()
    upstream.mkdir()
    releases.mkdir()

    return packaging, upstream, tarballs, releases


def test_base_importer_init(importer_paths):
    """Test BaseImporter initialization."""
    packaging, upstream, tarballs, releases = importer_paths

    importer = ConcreteImporter(
        str(packaging),
        str(upstream),
        str(tarballs),
        "dalmatian",
        str(releases),
    )

    assert importer.packaging_repo_path == packaging
    assert importer.upstream_repo_path == upstream
    assert importer.tarballs_dir == tarballs
    assert importer.cycle == "dalmatian"
    assert importer.releases_repo_path == releases
    assert tarballs.exists()  # Should be created


def test_base_importer_init_packaging_not_found(tmp_path):
    """Test BaseImporter with nonexistent packaging repo."""
    with pytest.raises(ImporterError, match="Packaging repo not found"):
        ConcreteImporter(
            "/nonexistent",
            str(tmp_path),
            str(tmp_path / "tarballs"),
            "dalmatian",
            str(tmp_path),
        )


def test_base_importer_init_upstream_not_found(tmp_path):
    """Test BaseImporter with nonexistent upstream repo."""
    packaging = tmp_path / "packaging"
    packaging.mkdir()

    with pytest.raises(ImporterError, match="Upstream repo not found"):
        ConcreteImporter(
            str(packaging),
            "/nonexistent",
            str(tmp_path / "tarballs"),
            "dalmatian",
            str(tmp_path),
        )


def test_base_importer_init_releases_not_found(tmp_path):
    """Test BaseImporter with nonexistent releases repo."""
    packaging = tmp_path / "packaging"
    upstream = tmp_path / "upstream"
    packaging.mkdir()
    upstream.mkdir()

    with pytest.raises(ImporterError, match="Releases repo not found"):
        ConcreteImporter(
            str(packaging),
            str(upstream),
            str(tmp_path / "tarballs"),
            "dalmatian",
            "/nonexistent",
        )


def test_save_gpg_key(importer_paths):
    """Test saving GPG key."""
    packaging, upstream, tarballs, releases = importer_paths

    importer = ConcreteImporter(
        str(packaging),
        str(upstream),
        str(tarballs),
        "dalmatian",
        str(releases),
    )

    key_content = (
        "-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
        "key data\n"
        "-----END PGP PUBLIC KEY BLOCK-----"
    )
    importer.save_gpg_key(key_content)

    key_file = packaging / "debian" / "upstream" / "signing-key.asc"
    assert key_file.exists()
    assert key_file.read_text() == key_content


def test_rename_tarball(importer_paths):
    """Test renaming tarball."""
    packaging, upstream, tarballs, releases = importer_paths
    tarballs.mkdir(exist_ok=True)

    importer = ConcreteImporter(
        str(packaging),
        str(upstream),
        str(tarballs),
        "dalmatian",
        str(releases),
    )

    # Create source tarball
    source = tarballs / "nova-1.0.0.tar.gz"
    source.write_text("fake tarball")

    # Rename it
    renamed = importer.rename_tarball(source, "nova", "1.0.0")

    assert renamed == tarballs / "nova_1.0.0.orig.tar.gz"
    assert renamed.exists()
    assert not source.exists()


def test_rename_tarball_already_correct_name(importer_paths):
    """Test renaming tarball that already has correct name."""
    packaging, upstream, tarballs, releases = importer_paths
    tarballs.mkdir(exist_ok=True)

    importer = ConcreteImporter(
        str(packaging),
        str(upstream),
        str(tarballs),
        "dalmatian",
        str(releases),
    )

    # Create source tarball with correct name
    source = tarballs / "nova_1.0.0.orig.tar.gz"
    source.write_text("fake tarball")

    # "Rename" it (should be no-op)
    renamed = importer.rename_tarball(source, "nova", "1.0.0")

    assert renamed == source
    assert renamed.exists()


def test_import_tarball(importer_paths):
    """Test complete import workflow."""
    packaging, upstream, tarballs, releases = importer_paths

    importer = ConcreteImporter(
        str(packaging),
        str(upstream),
        str(tarballs),
        "dalmatian",
        str(releases),
    )

    debian_version = importer.import_tarball()

    assert debian_version == "1.0.0"  # From ConcreteImporter


@patch("packastack.importer.base.Path.write_text", side_effect=OSError("Permission denied"))
def test_save_gpg_key_error(mock_write_text, importer_paths):
    """Test saving GPG key with error."""

    packaging, upstream, tarballs, releases = importer_paths

    importer = ConcreteImporter(
        str(packaging),
        str(upstream),
        str(tarballs),
        "dalmatian",
        str(releases),
    )

    def mock_write_text(self, content):
        raise OSError("Permission denied")

    # Patched via decorator to raise OSError when write_text is called

    with pytest.raises(ImporterError, match="Failed to save GPG key"):
        importer.save_gpg_key("key content")


@patch("packastack.importer.base.Path.rename", side_effect=OSError("Permission denied"))
def test_rename_tarball_error(mock_rename, importer_paths):
    """Test renaming tarball with error."""

    packaging, upstream, tarballs, releases = importer_paths
    tarballs.mkdir(exist_ok=True)

    importer = ConcreteImporter(
        str(packaging),
        str(upstream),
        str(tarballs),
        "dalmatian",
        str(releases),
    )

    # Create source tarball
    source = tarballs / "nova-1.0.0.tar.gz"
    source.write_text("fake tarball")

    def mock_rename(self, target):
        raise OSError("Permission denied")

    # Patched via decorator to raise OSError when rename is called

    with pytest.raises(ImporterError, match="Failed to rename tarball"):
        importer.rename_tarball(source, "nova", "1.0.0")
