# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Configuration management for PackaStack."""

import json
from pathlib import Path


class PackastackConfig:
    """Manages PackaStack configuration stored in user's home directory."""

    def __init__(self, config_path: Path | None = None):
        """Initialize configuration manager.

        Args:
            config_path: Path to config file (default: ./packastack/config.json)
        """
        if config_path is None:
            config_path = Path.cwd() / "packastack" / "config.json"
        self.config_path = config_path
        self._data = self._load()

    def _load(self) -> dict:
        """Load configuration from disk."""
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self) -> None:
        """Save configuration to disk."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8"
        )

    def get_packaging_round(self) -> str | None:
        """Get current packaging round name."""
        return self._data.get("packaging_round")

    def set_packaging_round(self, round_name: str) -> None:
        """Set packaging round name."""
        self._data["packaging_round"] = round_name
        self._save()

    def get_ubuntu_release(self) -> str | None:
        """Get Ubuntu release name."""
        return self._data.get("ubuntu_release")

    def set_ubuntu_release(self, release_name: str) -> None:
        """Set Ubuntu release name."""
        self._data["ubuntu_release"] = release_name
        self._save()

    def get_lp_bug(self, milestone: str) -> str | None:
        """Get LP bug number for a milestone.

        Args:
            milestone: Milestone name
                (e.g., 'milestone-2', 'milestone-3', 'rc1', 'final')

        Returns:
            Bug number as string, or None if not set
        """
        bugs = self._data.get("lp_bugs", {})
        return bugs.get(milestone)

    def set_lp_bug(self, milestone: str, bug_number: str) -> None:
        """Set LP bug number for a milestone.

        Args:
            milestone: Milestone name
                (e.g., 'milestone-2', 'milestone-3', 'rc1', 'final')
            bug_number: Bug number (without 'LP: #' prefix)
        """
        if "lp_bugs" not in self._data:
            self._data["lp_bugs"] = {}
        self._data["lp_bugs"][milestone] = bug_number
        self._save()

    def get_all_lp_bugs(self) -> dict[str, str]:
        """Get all LP bug numbers."""
        return self._data.get("lp_bugs", {})

    def is_configured(self) -> bool:
        """Check if packaging round and at least one bug are configured."""
        return bool(
            self.get_packaging_round()
            and self.get_ubuntu_release()
            and self._data.get("lp_bugs")
        )

    def clear(self) -> None:
        """Clear all configuration."""
        self._data = {}
        self._save()
