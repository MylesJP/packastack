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

"""Path helpers and directory creation for Packastack."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from packastack.config import load_config


def resolve_paths(cfg: Mapping[str, Any]) -> dict[str, Path]:
    """Return resolved Path objects for configured paths."""
    paths: Mapping[str, Any] = cfg.get("paths", {})
    resolved: dict[str, Path] = {}
    for key, val in paths.items():
        resolved[key] = Path(str(val)).expanduser().resolve()
    return resolved


def ensure_directories(paths_cfg: Mapping[str, Any] | None = None) -> dict[str, Path]:
    """Ensure required cache and run directories exist.

    Returns a mapping of keys to Path objects that were created/ensured.
    """
    cfg = load_config()
    paths = resolve_paths(cfg) if paths_cfg is None else resolve_paths({"paths": paths_cfg})

    required = [
        paths["cache_root"],
        paths["openstack_releases_repo"],
        paths["ubuntu_archive_cache"] / "indexes",
        paths["ubuntu_archive_cache"] / "snapshots",
        paths["local_apt_repo"],
        paths["build_root"],
        paths["runs_root"],
    ]

    for p in required:
        p.mkdir(parents=True, exist_ok=True)

    return paths


if __name__ == "__main__":
    resolved = ensure_directories()
    for k, p in resolved.items():
        print(f"{k}: {p}")
