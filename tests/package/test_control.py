# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Tests for debian/control file parsing."""

import tempfile
from pathlib import Path

import pytest

from packastack.exceptions import DebianError
from packastack.package.control import ControlFileParser


@pytest.fixture
def sample_control_content():
    """Sample debian/control content."""
    return """Source: python-nova
Section: python
Priority: optional
Maintainer: Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>
Build-Depends: debhelper (>= 10)
Standards-Version: 4.1.3
Homepage: https://opendev.org/openstack/nova

Package: python3-nova
Architecture: all
Depends: ${python3:Depends}, ${misc:Depends}
Description: OpenStack Compute - Python 3 libraries
 OpenStack is a reliable cloud infrastructure.
"""


@pytest.fixture
def control_file(sample_control_content):
    """Create a temporary control file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".control", delete=False) as f:
        f.write(sample_control_content)
        f.flush()
        yield f.name
    Path(f.name).unlink()


def test_parse_source_name(control_file):
    """Test parsing source package name."""
    parser = ControlFileParser(control_file)
    assert parser.get_source_name() == "python-nova"


def test_parse_homepage(control_file):
    """Test parsing homepage URL."""
    parser = ControlFileParser(control_file)
    assert parser.get_homepage() == "https://opendev.org/openstack/nova"


def test_parse_upstream_project_name(control_file):
    """Test extracting upstream project name from homepage."""
    parser = ControlFileParser(control_file)
    assert parser.get_upstream_project_name() == "nova"


def test_control_file_not_found():
    """Test error when control file doesn't exist."""
    with pytest.raises(DebianError, match="Control file not found"):
        ControlFileParser("/nonexistent/path")


def test_control_file_missing_source():
    """Test error when Source field is missing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".control", delete=False) as f:
        f.write("Package: test\n")
        f.flush()

        parser = ControlFileParser(f.name)
        with pytest.raises(DebianError, match="Source field not found"):
            parser.get_source_name()

        Path(f.name).unlink()


def test_control_file_no_homepage():
    """Test handling when Homepage field is missing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".control", delete=False) as f:
        f.write("Source: test-package\n")
        f.flush()

        parser = ControlFileParser(f.name)
        assert parser.get_homepage() is None
        assert parser.get_upstream_project_name() is None

        Path(f.name).unlink()


def test_homepage_with_trailing_slash():
    """Test parsing homepage with trailing slash."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".control", delete=False) as f:
        f.write("Source: test\nHomepage: https://example.org/project/\n")
        f.flush()

        parser = ControlFileParser(f.name)
        assert parser.get_upstream_project_name() == "project"

        Path(f.name).unlink()


def test_control_file_read_error(tmp_path):
    """Test error when control file can't be read."""
    control_file = tmp_path / "control"
    control_file.write_text("Source: test\n")
    control_file.chmod(0o000)  # Make unreadable

    with pytest.raises(DebianError, match="Failed to read control file"):
        ControlFileParser(str(control_file))

    control_file.chmod(0o644)  # Restore permissions for cleanup


def test_homepage_empty_after_strip():
    """Test error when homepage becomes empty after stripping."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".control", delete=False) as f:
        f.write("Source: test\nHomepage: /\n")
        f.flush()

        parser = ControlFileParser(f.name)
        # After rstrip("/") and split, there will be empty components
        # This will result in empty path
        with pytest.raises(DebianError, match="Could not extract project name"):
            parser.get_upstream_project_name()

        Path(f.name).unlink()


def test_homepage_parse_internal_exception():
    """Test exception handling when parsing homepage raises unexpected error."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".control", delete=False) as f:
        f.write("Source: test\nHomepage: https://example.org/project\n")
        f.flush()

        parser = ControlFileParser(f.name)

        # Patch get_homepage to return something that will cause
        # an exception during processing

        def mock_get_homepage():
            # Return an object that looks like a string but raises error on rstrip
            class BadString:
                def rstrip(self, chars):
                    raise ValueError("Unexpected error")

                def __bool__(self):
                    return True

            return BadString()

        parser.get_homepage = mock_get_homepage

        with pytest.raises(DebianError, match="Failed to parse Homepage URL"):
            parser.get_upstream_project_name()

        Path(f.name).unlink()
