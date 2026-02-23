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

# Header prepended to config.yaml on first creation to help users
# discover available options.  yaml.safe_dump cannot emit comments,
# so we write them separately.
_CONFIG_HEADER = """\
# PackaStack configuration
# Location: ~/.config/packastack/config.yaml
#
# ── AI-powered build diagnosis ──────────────────────────────────────────
#
# PackaStack can use any OpenAI-compatible AI model to diagnose build
# failures and propose patches.  To enable it, set an API key via an
# environment variable (recommended) or in the "ai" section below.
#
# Environment variables (checked in order):
#   PACKASTACK_AI_API_KEY   - project-specific key (highest priority)
#   OPENAI_API_KEY          - standard OpenAI key
#   ANTHROPIC_API_KEY       - Anthropic key (backward compatibility)
#
# The base_url can be any OpenAI-compatible endpoint.  Examples:
#
#   OpenAI (default):
#     export OPENAI_API_KEY="sk-..."
#     # base_url: https://api.openai.com/v1  (default, no change needed)
#     # model: gpt-4o
#
#   Anthropic (via OpenRouter):
#     export PACKASTACK_AI_API_KEY="sk-or-..."
#     # base_url: https://openrouter.ai/api/v1
#     # model: anthropic/claude-sonnet-4
#
#   Local model (Ollama):
#     # No API key needed for local models - set any non-empty value:
#     export PACKASTACK_AI_API_KEY="local"
#     # base_url: http://localhost:11434/v1
#     # model: llama3
#
# You can also override base_url via environment variable:
#   PACKASTACK_AI_BASE_URL=http://localhost:11434/v1
#
# To disable AI during a build without removing your key:
#   packastack build <pkg> --no-ai
#
# ────────────────────────────────────────────────────────────────────────

"""

DEFAULT_CONFIG: dict[str, Any] = {
    "paths": {
        "cache_root": "~/.cache/packastack",
        "openstack_releases_repo": "~/.cache/packastack/openstack-releases",
        "openstack_project_config": "~/.cache/packastack/openstack-project-config",
        "ubuntu_archive_cache": "~/.cache/packastack/ubuntu-archive",
        "local_apt_repo": "~/.cache/packastack/apt-repo",
        "upstream_tarballs": "~/.cache/packastack/upstream-tarballs",
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
        "upload_ppa": None,  # PPA to auto-upload to (e.g., "mylesjp/gazpacho-devel")
    },
    "mirrors": {
        "ubuntu_archive": "http://archive.ubuntu.com/ubuntu",
        "ubuntu_openstack_git": "https://git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source",
    },
    "git": {
        "launchpad_username": None,  # Set to your Launchpad username for SSH push access
    },
    "launchpad_bugs": {
        # Launchpad bug numbers for changelog entries, keyed by build type.
        # Example: "service-release": 2116155
        #   client-lib-release  - Client library final releases
        #   milestone2          - Milestone 2 builds
        #   service-rc1         - Service release candidates
        #   service-release     - Service final releases
        "client-lib-release": None,
        "milestone2": None,
        "service-rc1": None,
        "service-release": None,
    },
    "behavior": {"offline": False, "snapshot_archive_on_build": True},
    "ai": {
        "api_key": None,  # Fallback; env vars take precedence (see client.py)
        "base_url": "https://api.openai.com/v1",  # Any OpenAI-compatible endpoint
        "model": "gpt-4o",
        "max_tokens": 8192,
        "timeout": 120,
    },
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
        cfg_path.write_text(_CONFIG_HEADER + yaml.safe_dump(DEFAULT_CONFIG))


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
