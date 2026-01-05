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

from packastack.core.config import load_config


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
    base_paths: dict[str, Any] = dict(cfg.get("paths", {}))

    if paths_cfg is not None:
        base_paths.update(paths_cfg)
        provided_keys = set(paths_cfg.keys())

        cache_root = Path(str(base_paths.get("cache_root", cfg["paths"]["cache_root"]))).expanduser().resolve()
        derived_defaults = {
            "openstack_releases_repo": cache_root / "openstack-releases",
            "ubuntu_archive_cache": cache_root / "ubuntu-archive",
            "local_apt_repo": cache_root / "apt-repo",
            "upstream_tarballs": cache_root / "upstream-tarballs",
            "build_root": cache_root / "build",
            "runs_root": cache_root / "runs",
        }
        for key, default_path in derived_defaults.items():
            if key in provided_keys:
                continue
            base_paths[key] = str(default_path)

    paths = resolve_paths({"paths": base_paths})

    required = [
        paths["cache_root"],
        paths["openstack_releases_repo"],
        paths["ubuntu_archive_cache"] / "indexes",
        paths["ubuntu_archive_cache"] / "snapshots",
        paths["local_apt_repo"],
        paths["upstream_tarballs"],
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
