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

"""Configuration utilities for Packastack."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG: dict[str, Any] = {
    "paths": {
        "cache_root": "~/.cache/packastack",
        "openstack_releases_repo": "~/.cache/packastack/openstack-releases",
        "ubuntu_archive_cache": "~/.cache/packastack/ubuntu-archive",
        "local_apt_repo": "~/.cache/packastack/apt-repo",
        "build_root": "~/.cache/packastack/build",
        "runs_root": "~/.cache/packastack/runs",
    },
    "defaults": {
        "upstream_target": "devel",
        "ubuntu_series": "devel",
        "ubuntu_pockets": ["release", "updates", "security"],
        "ubuntu_components": ["main", "universe"],
        "ubuntu_arches": ["host", "all"],
        "refresh_ttl": "6h",
        "mir_policy": "warn",
        "cloud_archive": None,
    },
    "mirrors": {
        "ubuntu_archive": "http://archive.ubuntu.com/ubuntu",
        "ubuntu_openstack_git": "https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source",
    },
    "behavior": {"offline": False, "snapshot_archive_on_build": True},
}


def get_config_path() -> Path:
    """Return the path to the config file."""
    return Path.home() / ".config" / "packastack" / "config.yaml"


def ensure_config_exists() -> None:
    """Create the config file with defaults if it does not exist."""
    cfg_path = get_config_path()
    cfg_dir = cfg_path.parent
    cfg_dir.mkdir(parents=True, exist_ok=True)
    if not cfg_path.exists():
        cfg_path.write_text(yaml.safe_dump(DEFAULT_CONFIG))


def load_config() -> dict[str, Any]:
    """Load configuration from disk and merge with defaults.

    The returned dictionary is a deep-ish merge of DEFAULT_CONFIG and values
    stored in the on-disk config file.
    """
    ensure_config_exists()
    cfg_path = get_config_path()
    try:
        raw = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception:
        raw = {}

    # Simple shallow merge for top-level sections.
    merged: dict[str, Any] = {}
    for key, val in DEFAULT_CONFIG.items():
        if key in raw and isinstance(raw[key], dict):
            merged[key] = {**val, **raw[key]}
        elif isinstance(val, dict):
            # Make a copy of dict values to avoid modifying DEFAULT_CONFIG
            merged[key] = raw.get(key, dict(val))
        else:
            merged[key] = raw.get(key, val)

    # Expand tiled paths into absolute Paths in place for convenience.
    for pkey, pval in merged.get("paths", {}).items():
        try:
            merged["paths"][pkey] = str(Path(pval).expanduser())
        except Exception:  # pragma: no cover
            merged["paths"][pkey] = pval

    return merged


def write_config(data: dict[str, Any]) -> None:
    """Write the provided data as YAML to the config path.

    The caller should pass a complete configuration mapping.
    """
    cfg_path = get_config_path()
    cfg_dir = cfg_path.parent
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(yaml.safe_dump(data))


if __name__ == "__main__":
    # Basic smoke-check
    cfg = load_config()
    print(json.dumps(cfg, indent=2))
