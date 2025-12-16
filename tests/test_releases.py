# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Tests for packastack.importer.openstack module."""

from unittest.mock import patch

import pytest
import yaml

from packastack.exceptions import ImporterError
from packastack.importer.openstack import (
    get_current_cycle,
    get_deliverable_info,
    get_previous_cycle,
    get_signing_key,
)


@pytest.fixture
def temp_releases_repo(tmp_path):
    """Create a temporary releases repository structure."""
    repo_path = tmp_path / "releases"
    repo_path.mkdir()

    # Create data directory
    data_dir = repo_path / "data"
    data_dir.mkdir()

    # Create deliverables directory
    deliverables_dir = repo_path / "deliverables"
    deliverables_dir.mkdir()

    # Create doc/source directories
    doc_dir = repo_path / "doc" / "source"
    doc_dir.mkdir(parents=True)
    static_dir = doc_dir / "static"
    static_dir.mkdir()

    return repo_path


def test_get_current_cycle_success(temp_releases_repo):
    """Test getting current development cycle."""
    series_data = [
        {"name": "caracal", "status": "released"},
        {"name": "dalmatian", "status": "development"},
    ]

    series_file = temp_releases_repo / "data" / "series_status.yaml"
    with open(series_file, "w") as f:
        yaml.dump(series_data, f)

    cycle = get_current_cycle(str(temp_releases_repo))
    assert cycle == "dalmatian"


def test_get_current_cycle_no_development(temp_releases_repo):
    """Test error when no development cycle found."""
    series_data = [
        {"name": "caracal", "status": "released"},
        {"name": "dalmatian", "status": "released"},
    ]

    series_file = temp_releases_repo / "data" / "series_status.yaml"
    with open(series_file, "w") as f:
        yaml.dump(series_data, f)

    with pytest.raises(ImporterError, match="No development cycle found"):
        get_current_cycle(str(temp_releases_repo))


def test_get_current_cycle_file_not_found(temp_releases_repo):
    """Test error when series_status.yaml not found."""
    with pytest.raises(ImporterError, match="Series status file not found"):
        get_current_cycle(str(temp_releases_repo))


def test_get_previous_cycle_success(temp_releases_repo):
    """Test getting previous released cycle."""
    series_data = [
        {"name": "bobcat", "status": "released"},
        {"name": "caracal", "status": "released"},
        {"name": "dalmatian", "status": "development"},
    ]

    series_file = temp_releases_repo / "data" / "series_status.yaml"
    with open(series_file, "w") as f:
        yaml.dump(series_data, f)

    cycle = get_previous_cycle(str(temp_releases_repo))
    assert cycle == "caracal"


def test_get_previous_cycle_none_found(temp_releases_repo):
    """Test when no previous cycle exists."""
    series_data = [
        {"name": "dalmatian", "status": "development"},
    ]

    series_file = temp_releases_repo / "data" / "series_status.yaml"
    with open(series_file, "w") as f:
        yaml.dump(series_data, f)

    cycle = get_previous_cycle(str(temp_releases_repo))
    assert cycle is None


def test_get_signing_key_success(temp_releases_repo):
    """Test getting signing key."""
    index_content = """
Cryptographic Signatures
========================

Some content here.

* `2024.1 Caracal key`_ present...Cycle key for 2024.1 release
  key 0xABCDEF1234567890`_

"""

    index_file = temp_releases_repo / "doc" / "source" / "index.rst"
    index_file.write_text(index_content)

    key_content = (
        "-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
        "key data here\n"
        "-----END PGP PUBLIC KEY BLOCK-----"
    )
    key_file = (
        temp_releases_repo / "doc" / "source" / "static" / "0xABCDEF1234567890.txt"
    )
    key_file.write_text(key_content)

    key_id, key_data = get_signing_key(str(temp_releases_repo))
    assert key_id == "0xABCDEF1234567890"
    assert key_data == key_content


def test_get_signing_key_not_found(temp_releases_repo):
    """Test error when signing key not found in index."""
    index_content = "No signing key info here"

    index_file = temp_releases_repo / "doc" / "source" / "index.rst"
    index_file.write_text(index_content)

    with pytest.raises(ImporterError, match="Could not find signing key"):
        get_signing_key(str(temp_releases_repo))


def test_get_signing_key_file_not_found(temp_releases_repo):
    """Test error when signing key pattern not found in index."""
    index_content = """
Cryptographic Signatures
========================

* key 0xABCDEF1234567890`_ present...Cycle key

"""

    index_file = temp_releases_repo / "doc" / "source" / "index.rst"
    index_file.write_text(index_content)

    with pytest.raises(ImporterError, match="Could not find signing key"):
        get_signing_key(str(temp_releases_repo))


def test_get_deliverable_info_success(temp_releases_repo):
    """Test getting deliverable info."""
    deliverable_data = {
        "repository-settings": {
            "openstack/nova": {
                "tarball-base": "nova",
            }
        },
        "releases": [
            {"version": "27.0.0"},
            {"version": "27.1.0"},
        ],
    }

    cycle_dir = temp_releases_repo / "deliverables" / "dalmatian"
    cycle_dir.mkdir()
    deliverable_file = cycle_dir / "nova.yaml"
    with open(deliverable_file, "w") as f:
        yaml.dump(deliverable_data, f)

    info = get_deliverable_info(str(temp_releases_repo), "dalmatian", "nova")
    assert info is not None
    assert info["namespace"] == "openstack"
    assert info["project_name"] == "nova"
    assert info["tarball_base"] == "nova"
    assert info["latest_version"] == "27.1.0"
    assert info["repo_path"] == "openstack/nova"


def test_get_deliverable_info_not_found(temp_releases_repo):
    """Test when deliverable file doesn't exist."""
    cycle_dir = temp_releases_repo / "deliverables" / "dalmatian"
    cycle_dir.mkdir()

    info = get_deliverable_info(str(temp_releases_repo), "dalmatian", "nova")
    assert info is None


def test_get_deliverable_info_no_tarball_base(temp_releases_repo):
    """Test deliverable without explicit tarball-base."""
    deliverable_data = {
        "repository-settings": {"openstack/nova": {}},
        "releases": [
            {"version": "27.0.0"},
        ],
    }

    cycle_dir = temp_releases_repo / "deliverables" / "dalmatian"
    cycle_dir.mkdir()
    deliverable_file = cycle_dir / "nova.yaml"
    with open(deliverable_file, "w") as f:
        yaml.dump(deliverable_data, f)

    info = get_deliverable_info(str(temp_releases_repo), "dalmatian", "nova")
    assert info["tarball_base"] == "nova"  # Defaults to project name


def test_get_deliverable_info_no_releases(temp_releases_repo):
    """Test deliverable with no releases."""
    deliverable_data = {
        "repository-settings": {"openstack/nova": {}},
        "releases": [],
    }

    cycle_dir = temp_releases_repo / "deliverables" / "dalmatian"
    cycle_dir.mkdir()
    deliverable_file = cycle_dir / "nova.yaml"
    with open(deliverable_file, "w") as f:
        yaml.dump(deliverable_data, f)

    info = get_deliverable_info(str(temp_releases_repo), "dalmatian", "nova")
    assert info["latest_version"] is None


def test_get_current_cycle_parse_error(temp_releases_repo):
    """Test error when series_status.yaml can't be parsed."""
    series_file = temp_releases_repo / "data" / "series_status.yaml"
    series_file.write_text("invalid: yaml: content: [")

    with pytest.raises(ImporterError, match="Failed to parse series_status.yaml"):
        get_current_cycle(str(temp_releases_repo))


def test_get_previous_cycle_file_not_found(temp_releases_repo):
    """Test error when series_status.yaml not found."""
    with pytest.raises(ImporterError, match="Series status file not found"):
        get_previous_cycle(str(temp_releases_repo))


def test_get_previous_cycle_parse_error(temp_releases_repo):
    """Test error when series_status.yaml can't be parsed."""
    series_file = temp_releases_repo / "data" / "series_status.yaml"
    series_file.write_text("invalid: yaml: content: [")

    with pytest.raises(ImporterError, match="Failed to parse series_status.yaml"):
        get_previous_cycle(str(temp_releases_repo))


def test_get_signing_key_index_not_found(temp_releases_repo):
    """Test error when index.rst not found."""
    with pytest.raises(ImporterError, match="Index file not found"):
        get_signing_key(str(temp_releases_repo))


@patch(
    "packastack.importer.openstack.Path.read_text",
    side_effect=OSError("Permission denied"),
)
def test_get_signing_key_index_read_error(mock_read_text, temp_releases_repo):
    """Test error when index.rst can't be read."""
    index_file = temp_releases_repo / "doc" / "source" / "index.rst"
    index_file.write_text("content")

    with pytest.raises(ImporterError, match="Failed to read index.rst"):
        get_signing_key(str(temp_releases_repo))


def test_get_signing_key_file_missing(temp_releases_repo):
    """Test error when key file doesn't exist."""
    index_content = """
Cryptographic Signatures
========================

* `2024.1 Caracal key`_ present...Cycle key for 2024.1 release
  key 0xABCDEF1234567890`_

"""

    index_file = temp_releases_repo / "doc" / "source" / "index.rst"
    index_file.write_text(index_content)

    with pytest.raises(ImporterError, match="Signing key file not found"):
        get_signing_key(str(temp_releases_repo))


def test_get_signing_key_file_read_error(temp_releases_repo):
    """Test error when key file can't be read."""

    index_content = """
Cryptographic Signatures
========================

* `2024.1 Caracal key`_ present...Cycle key for 2024.1 release
  key 0xABCDEF1234567890`_

"""

    index_file = temp_releases_repo / "doc" / "source" / "index.rst"
    index_file.write_text(index_content)

    key_file = (
        temp_releases_repo / "doc" / "source" / "static" / "0xABCDEF1234567890.txt"
    )
    key_file.write_text("key data")

    # Patch the module-specific Path.read_text using context manager so we can
    # capture the original function for conditional behavior.
    import importlib
    ow = importlib.import_module("packastack.importer.openstack")
    original_read_text = ow.Path.read_text

    def mock_read_text(self):
        if "0xABCDEF1234567890.txt" in str(self):
            raise OSError("Permission denied")
        return original_read_text(self)

    from unittest.mock import patch
    with patch("packastack.importer.openstack.Path.read_text", new=mock_read_text):
        with pytest.raises(ImporterError, match="Failed to read signing key file"):
            get_signing_key(str(temp_releases_repo))



def test_get_deliverable_info_parse_error(temp_releases_repo):
    """Test error when deliverable file can't be parsed."""
    cycle_dir = temp_releases_repo / "deliverables" / "dalmatian"
    cycle_dir.mkdir()
    deliverable_file = cycle_dir / "nova.yaml"
    deliverable_file.write_text("invalid: yaml: content: [")

    with pytest.raises(ImporterError, match="Failed to parse deliverable file"):
        get_deliverable_info(str(temp_releases_repo), "dalmatian", "nova")


def test_get_deliverable_info_no_repos(temp_releases_repo):
    """Test deliverable with no repository-settings."""
    deliverable_data = {
        "repository-settings": {},
        "releases": [],
    }

    cycle_dir = temp_releases_repo / "deliverables" / "dalmatian"
    cycle_dir.mkdir()
    deliverable_file = cycle_dir / "nova.yaml"
    with open(deliverable_file, "w") as f:
        yaml.dump(deliverable_data, f)

    info = get_deliverable_info(str(temp_releases_repo), "dalmatian", "nova")
    assert info is None


def test_get_deliverable_info_single_part_repo(temp_releases_repo):
    """Test deliverable with single-part repository name."""
    deliverable_data = {
        "repository-settings": {"nova": {}},
        "releases": [{"version": "27.0.0"}],
    }

    cycle_dir = temp_releases_repo / "deliverables" / "dalmatian"
    cycle_dir.mkdir()
    deliverable_file = cycle_dir / "nova.yaml"
    with open(deliverable_file, "w") as f:
        yaml.dump(deliverable_data, f)

    info = get_deliverable_info(str(temp_releases_repo), "dalmatian", "nova")
    assert info["namespace"] == "openstack"  # Default
    assert info["project_name"] == "nova"
