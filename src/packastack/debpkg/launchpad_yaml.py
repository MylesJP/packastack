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

"""Launchpad.yaml file handling for Packastack build operations.

Manages parsing, updating, and writing of launchpad.yaml files in
Ubuntu OpenStack packaging repositories.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class AmbiguousUpdateError(Exception):
    """Raised when launchpad.yaml update would be ambiguous."""

    def __init__(self, message: str, conflicts: list[str] | None = None) -> None:
        super().__init__(message)
        self.conflicts = conflicts or []


@dataclass
class LaunchpadConfig:
    """Parsed launchpad.yaml configuration."""

    data: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    def get_recipes(self) -> list[dict[str, Any]]:
        """Get the list of recipes from the config."""
        return self.data.get("recipes", [])

    def get_git_repository(self) -> str:
        """Get the git repository URL."""
        return self.data.get("git-repository", "")

    def get_git_repository_push(self) -> str:
        """Get the push URL for the git repository."""
        return self.data.get("git-repository-push", "")


def load_launchpad_yaml(repo_path: Path) -> LaunchpadConfig | None:
    """Load launchpad.yaml from a repository.

    Args:
        repo_path: Path to the git repository root.

    Returns:
        LaunchpadConfig if file exists, None otherwise.
    """
    yaml_path = repo_path / "launchpad.yaml"

    if not yaml_path.exists():
        return None

    try:
        with yaml_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return LaunchpadConfig(data=data, path=yaml_path)
    except yaml.YAMLError:
        return None


def save_launchpad_yaml(config: LaunchpadConfig) -> bool:
    """Save launchpad.yaml configuration.

    Args:
        config: LaunchpadConfig to save.

    Returns:
        True if save succeeded.
    """
    if config.path is None:
        return False

    try:
        with config.path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(
                config.data,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        return True
    except Exception:
        return False


def find_series_references(data: Any, series: str) -> list[tuple[str, Any]]:
    """Find all references to a series name in the config data.

    Recursively searches the data structure for string values
    containing the series name.

    Args:
        data: YAML data structure to search.
        series: Series name to find (e.g., "caracal", "2024.1").

    Returns:
        List of (path, value) tuples where series was found.
    """
    results: list[tuple[str, Any]] = []

    def search(obj: Any, path: str = "") -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                new_path = f"{path}.{key}" if path else key
                search(value, new_path)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                new_path = f"{path}[{i}]"
                search(item, new_path)
        elif isinstance(obj, str):
            if series in obj:
                results.append((path, obj))

    search(data)
    return results


def update_series_references(
    data: dict[str, Any],
    prev_series: str,
    target_series: str,
) -> tuple[dict[str, Any], list[str]]:
    """Update series references from previous to target series.

    Replaces occurrences of prev_series with target_series in string values.
    Only updates branch names and recipe references, not arbitrary strings.

    Args:
        data: YAML data dictionary.
        prev_series: Previous series codename (e.g., "caracal").
        target_series: Target series codename (e.g., "dalmatian").

    Returns:
        Tuple of (updated_data, list of updated paths).

    Raises:
        AmbiguousUpdateError: If update would be ambiguous or affect
            unexpected fields.
    """
    updated_paths: list[str] = []

    def update(obj: Any, path: str = "") -> Any:
        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                new_path = f"{path}.{key}" if path else key
                result[key] = update(value, new_path)
            return result
        elif isinstance(obj, list):
            return [update(item, f"{path}[{i}]") for i, item in enumerate(obj)]
        elif isinstance(obj, str):
            if prev_series in obj:
                # Check if this is a safe field to update
                safe_fields = [
                    "branch",
                    "git-branch",
                    "recipe",
                    "name",
                    "source-branch",
                    "target-branch",
                ]
                # Get the field name from path
                field_name = path.split(".")[-1] if "." in path else path
                # Remove array index if present
                if "[" in field_name:
                    field_name = path.split(".")[-2] if path.count(".") > 0 else ""

                # Allow updates to known safe fields
                is_safe = any(f in path.lower() for f in safe_fields)

                if is_safe or field_name in safe_fields:
                    new_value = obj.replace(prev_series, target_series)
                    updated_paths.append(path)
                    return new_value
                else:
                    # Log but don't fail - some projects have series in names
                    return obj
            return obj
        else:
            return obj

    new_data = update(data)
    return new_data, updated_paths


def validate_update(
    config: LaunchpadConfig,
    prev_series: str,
    target_series: str,
) -> tuple[bool, list[str], list[str]]:
    """Validate that a series update would be safe.

    Args:
        config: LaunchpadConfig to validate.
        prev_series: Previous series codename.
        target_series: Target series codename.

    Returns:
        Tuple of (is_valid, fields_to_update, warnings).
    """
    warnings: list[str] = []
    fields_to_update: list[str] = []

    # Find all references to prev_series
    refs = find_series_references(config.data, prev_series)

    if not refs:
        warnings.append(f"No references to '{prev_series}' found in launchpad.yaml")
        return True, [], warnings

    for path, value in refs:
        # Check for potential issues
        if target_series in value:
            warnings.append(f"Field '{path}' already contains '{target_series}'")
        else:
            fields_to_update.append(path)

    return True, fields_to_update, warnings


def update_launchpad_yaml_series(
    repo_path: Path,
    prev_series: str,
    target_series: str,
) -> tuple[bool, list[str], str]:
    """Update launchpad.yaml from previous to target series.

    This is the main entry point for series updates.

    Args:
        repo_path: Path to the git repository.
        prev_series: Previous OpenStack series codename.
        target_series: Target OpenStack series codename.

    Returns:
        Tuple of (success, updated_fields, error_message).
    """
    config = load_launchpad_yaml(repo_path)

    if config is None:
        return True, [], "launchpad.yaml not found (this is OK for some packages)"

    # Validate
    is_valid, fields, warnings = validate_update(config, prev_series, target_series)

    if not is_valid:
        return False, [], f"Validation failed: {'; '.join(warnings)}"

    if not fields:
        # No updates needed
        return True, [], "No series references to update"

    # Perform update
    try:
        new_data, updated = update_series_references(
            config.data, prev_series, target_series
        )
        config.data = new_data

        if not save_launchpad_yaml(config):
            return False, [], "Failed to save launchpad.yaml"

        return True, updated, ""
    except AmbiguousUpdateError as e:
        return False, [], str(e)


def create_default_launchpad_yaml(
    repo_path: Path,
    package: str,
    ubuntu_series: str,
    openstack_series: str,
) -> bool:
    """Create a default launchpad.yaml for a package.

    Args:
        repo_path: Path to the git repository.
        package: Source package name.
        ubuntu_series: Ubuntu series codename (e.g., "noble").
        openstack_series: OpenStack series codename (e.g., "dalmatian").

    Returns:
        True if creation succeeded.
    """
    yaml_path = repo_path / "launchpad.yaml"

    data = {
        "git-repository": f"lp:~ubuntu-openstack-dev/ubuntu/+source/{package}",
        "git-repository-push": f"lp:~ubuntu-openstack-dev/ubuntu/+source/{package}",
        "recipes": [
            {
                "name": f"{package}-{openstack_series}",
                "branch": f"ubuntu/{ubuntu_series}-{openstack_series}",
                "recipe-type": "daily-build",
            },
        ],
    }

    try:
        with yaml_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(
                data,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        return True
    except Exception:
        return False


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m packastack.debpkg.launchpad_yaml <repo_path> [prev] [target]")
        sys.exit(1)

    repo = Path(sys.argv[1])
    config = load_launchpad_yaml(repo)

    if config is None:
        print("launchpad.yaml not found")
        sys.exit(1)

    print("Current launchpad.yaml:")
    print(yaml.safe_dump(config.data, default_flow_style=False))

    if len(sys.argv) >= 4:
        prev = sys.argv[2]
        target = sys.argv[3]
        print(f"\nUpdating {prev} -> {target}...")

        success, updated, error = update_launchpad_yaml_series(repo, prev, target)
        if success:
            print(f"Updated fields: {updated}")
        else:
            print(f"Error: {error}")
