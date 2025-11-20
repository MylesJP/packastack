# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Tests for Launchpad CI file update."""


import pytest

from packastack.exceptions import LaunchpadError
from packastack.launchpad.lpci import update_launchpad_ci_file


def test_update_launchpad_ci_file_missing(tmp_path):
    """Raise LaunchpadError if .launchpad.yaml missing."""
    pkg_repo = tmp_path / "pkg"
    pkg_repo.mkdir()

    with pytest.raises(LaunchpadError):
        update_launchpad_ci_file(pkg_repo, "rocky")


def test_update_launchpad_ci_file_no_change(tmp_path):
    """Return False when file exists but no change required."""
    pkg_repo = tmp_path / "pkg"
    pkg_repo.mkdir()

    ci_file = pkg_repo / ".launchpad.yaml"
    ci_file.write_text('openstack_series="rocky"\n')

    result = update_launchpad_ci_file(pkg_repo, "rocky")
    assert result is False


def test_update_launchpad_ci_file_changed(tmp_path):
    """Return True when file exists and update occurs."""
    pkg_repo = tmp_path / "pkg"
    pkg_repo.mkdir()

    ci_file = pkg_repo / ".launchpad.yaml"
    ci_file.write_text('openstack_series="queens"\n')

    result = update_launchpad_ci_file(pkg_repo, "rocky")
    assert result is True
    assert 'openstack_series="rocky"' in ci_file.read_text()
