# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

import re
from pathlib import Path

import pytest

from packastack.exceptions import DebianError
from packastack.package.source import DebianSourcePackage
from packastack.package.uscan import WatchEntry


def test_get_watch_urls(monkeypatch, tmp_path: Path):
    """Ensure URLs are sourced from parsed watch entries."""
    pkg_path = tmp_path / "pkg"
    debian_dir = pkg_path / "debian"
    debian_dir.mkdir(parents=True)
    (debian_dir / "watch").write_text(
        "version=4\nhttp://example.com pkg-(?P<version>\\d+)\\.tar\\.gz\n",
        encoding="utf-8",
    )

    fake_entries = [
        WatchEntry(url="http://example.com/releases", pattern=re.compile("x"))
    ]

    class FakeUscan:
        def __init__(self, watch_path: Path):
            assert watch_path == debian_dir / "watch"
            self.entries = fake_entries

    monkeypatch.setattr("packastack.package.source.Uscan", FakeUscan)

    package = DebianSourcePackage(pkg_path)
    assert package.control_file == debian_dir / "control"
    assert package.changelog == debian_dir / "changelog"
    assert package.source_package_name == "pkg"
    assert package.install_files == []
    assert package.get_watch_urls() == ["http://example.com/releases"]


def test_get_watch_urls_missing_file(tmp_path: Path):
    """Missing watch files surface a DebianError."""
    package = DebianSourcePackage(tmp_path / "pkg")
    with pytest.raises(DebianError, match="Watch file not found"):
        package.get_watch_urls()
