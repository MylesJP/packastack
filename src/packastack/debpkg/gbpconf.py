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

"""Debian gbp.conf file handling for Packastack build operations.

Manages parsing, updating, and writing of debian/gbp.conf files in
Ubuntu OpenStack packaging repositories.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GbpConfig:
    """Parsed debian/gbp.conf configuration."""

    debian_branch: str = ""
    pristine_tar: bool = False
    export_dir: str = "../build-area/"
    sign_tags: bool = True
    keyid: str = ""
    # Store raw config for preserving other settings
    _parser: configparser.ConfigParser = field(
        default_factory=configparser.ConfigParser, repr=False
    )
    path: Path | None = None


def load_gbp_conf(repo_path: Path) -> GbpConfig | None:
    """Load debian/gbp.conf from a repository.

    Args:
        repo_path: Path to the git repository root.

    Returns:
        GbpConfig if file exists, None otherwise.
    """
    conf_path = repo_path / "debian" / "gbp.conf"

    if not conf_path.exists():
        return None

    try:
        parser = configparser.ConfigParser()
        parser.read(conf_path)

        config = GbpConfig(path=conf_path, _parser=parser)

        # Parse DEFAULT section
        if parser.has_section("DEFAULT") or "DEFAULT" in parser:
            config.debian_branch = parser.get("DEFAULT", "debian-branch", fallback="")
            config.pristine_tar = parser.getboolean(
                "DEFAULT", "pristine-tar", fallback=False
            )

        # Parse buildpackage section
        if parser.has_section("buildpackage"):
            config.export_dir = parser.get(
                "buildpackage", "export-dir", fallback="../build-area/"
            )
            config.sign_tags = parser.getboolean(
                "buildpackage", "sign-tags", fallback=True
            )
            config.keyid = parser.get("buildpackage", "keyid", fallback="")

        return config
    except (configparser.Error, OSError):
        return None


def save_gbp_conf(config: GbpConfig) -> bool:
    """Save debian/gbp.conf configuration.

    Args:
        config: GbpConfig to save.

    Returns:
        True if save succeeded.
    """
    if config.path is None:
        return False

    try:
        # Update parser with current values
        parser = config._parser

        # Ensure sections exist
        if not parser.has_section("DEFAULT"):
            # DEFAULT is special in configparser
            pass
        if not parser.has_section("buildpackage"):
            parser.add_section("buildpackage")

        # Update values
        parser.set("DEFAULT", "debian-branch", config.debian_branch)
        parser.set("DEFAULT", "pristine-tar", str(config.pristine_tar))

        parser.set("buildpackage", "export-dir", config.export_dir)
        parser.set("buildpackage", "sign-tags", str(config.sign_tags))
        if config.keyid:
            parser.set("buildpackage", "keyid", config.keyid)

        # Write to file
        with config.path.open("w", encoding="utf-8") as f:
            parser.write(f)

        return True
    except (configparser.Error, OSError):
        return False


def create_gbp_conf(
    repo_path: Path,
    ubuntu_series: str,
    openstack_series: str,
    signing_key: str = "",
) -> GbpConfig:
    """Create a new debian/gbp.conf file.

    Args:
        repo_path: Path to the git repository root.
        ubuntu_series: Ubuntu series codename (e.g., "noble").
        openstack_series: OpenStack series codename (e.g., "dalmatian").
        signing_key: GPG key ID for signing (optional).

    Returns:
        New GbpConfig object.
    """
    conf_path = repo_path / "debian" / "gbp.conf"
    conf_path.parent.mkdir(parents=True, exist_ok=True)

    parser = configparser.ConfigParser()
    parser.add_section("buildpackage")

    config = GbpConfig(
        debian_branch=f"ubuntu/{ubuntu_series}-{openstack_series}",
        pristine_tar=False,
        export_dir="../build-area/",
        sign_tags=bool(signing_key),
        keyid=signing_key,
        _parser=parser,
        path=conf_path,
    )

    return config


def update_gbp_conf(
    repo_path: Path,
    ubuntu_series: str | None = None,
    openstack_series: str | None = None,
    signing_key: str | None = None,
) -> tuple[bool, list[str], str]:
    """Update debian/gbp.conf with new settings.

    Args:
        repo_path: Path to the git repository root.
        ubuntu_series: Ubuntu series codename to update to (optional).
        openstack_series: OpenStack series codename to update to (optional).
        signing_key: GPG key ID to set (optional).

    Returns:
        Tuple of (success, updated_fields, error_message).
    """
    config = load_gbp_conf(repo_path)
    updated_fields: list[str] = []

    if config is None:
        # Create new config if series info is provided
        if ubuntu_series and openstack_series:
            config = create_gbp_conf(
                repo_path, ubuntu_series, openstack_series, signing_key or ""
            )
            if save_gbp_conf(config):
                return True, ["created new gbp.conf"], ""
            return False, [], "Failed to create gbp.conf"
        return True, [], "No gbp.conf found and no series provided"

    # Update debian-branch if series are provided
    if ubuntu_series and openstack_series:
        new_branch = f"ubuntu/{ubuntu_series}-{openstack_series}"
        if config.debian_branch != new_branch:
            config.debian_branch = new_branch
            updated_fields.append(f"debian-branch={new_branch}")

    # Update signing key if provided
    if signing_key is not None:
        if config.keyid != signing_key:
            config.keyid = signing_key
            config.sign_tags = bool(signing_key)
            updated_fields.append(f"keyid={signing_key or '(removed)'}")

    if not updated_fields:
        return True, [], "No changes needed"

    if save_gbp_conf(config):
        return True, updated_fields, ""
    return False, [], "Failed to save gbp.conf"


def update_gbp_conf_from_launchpad_yaml(
    repo_path: Path,
    signing_key: str = "",
) -> tuple[bool, list[str], str]:
    """Update gbp.conf debian-branch from launchpad.yaml recipe branch.

    Reads the branch from the first recipe in launchpad.yaml and updates
    gbp.conf to match.

    Args:
        repo_path: Path to the git repository root.
        signing_key: GPG key ID to set (optional).

    Returns:
        Tuple of (success, updated_fields, error_message).
    """
    # Import here to avoid circular imports
    from packastack.debpkg.launchpad_yaml import load_launchpad_yaml

    lp_config = load_launchpad_yaml(repo_path)
    if lp_config is None:
        return True, [], "No launchpad.yaml found"

    recipes = lp_config.get_recipes()
    if not recipes:
        return True, [], "No recipes in launchpad.yaml"

    # Get branch from first recipe
    branch = recipes[0].get("branch", "")
    if not branch:
        return True, [], "No branch in first recipe"

    # Load or create gbp.conf
    config = load_gbp_conf(repo_path)
    updated_fields: list[str] = []

    if config is None:
        # Create new config
        conf_path = repo_path / "debian" / "gbp.conf"
        conf_path.parent.mkdir(parents=True, exist_ok=True)
        parser = configparser.ConfigParser()
        parser.add_section("buildpackage")
        config = GbpConfig(
            debian_branch=branch,
            pristine_tar=False,
            export_dir="../build-area/",
            sign_tags=bool(signing_key),
            keyid=signing_key,
            _parser=parser,
            path=conf_path,
        )
        updated_fields.append("created new gbp.conf")
        updated_fields.append(f"debian-branch={branch}")
        if signing_key:
            updated_fields.append(f"keyid={signing_key}")
    else:
        # Update existing config
        if config.debian_branch != branch:
            config.debian_branch = branch
            updated_fields.append(f"debian-branch={branch}")

    # Update signing key if provided
    if signing_key and config.keyid != signing_key:
        config.keyid = signing_key
        config.sign_tags = True
        updated_fields.append(f"keyid={signing_key}")

    if not updated_fields:
        return True, [], "No changes needed"

    if save_gbp_conf(config):
        return True, updated_fields, ""
    return False, [], "Failed to save gbp.conf"


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m packastack.debpkg.gbpconf <repo_path>")
        sys.exit(1)

    repo = Path(sys.argv[1])
    config = load_gbp_conf(repo)

    if config:
        print(f"debian-branch: {config.debian_branch}")
        print(f"pristine-tar: {config.pristine_tar}")
        print(f"export-dir: {config.export_dir}")
        print(f"sign-tags: {config.sign_tags}")
        print(f"keyid: {config.keyid}")
    else:
        print("No gbp.conf found")
