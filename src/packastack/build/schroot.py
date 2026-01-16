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

"""Schroot helpers for Packastack builds."""

from __future__ import annotations

import os
import random
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from packastack.core.spinner import activity_spinner

# Fun messages to display while waiting for schroot creation
SCHROOT_WAIT_MESSAGES = [
    "Smell that fresh coffee? Go get some - this will take a while",
    "Perfect time for a stretch break",
    "Maybe check on that mash you left on the stir plate",
    "Time to practice your juggling skills",
    "Great opportunity to refill your water bottle",
    "How about a quick game of desk chair spin?",
    "This is a good time to contemplate the meaning of life",
    "Pro tip: watching progress bars doesn't make them faster",
    "Fun fact: a watched pot never boils, but a watched schroot does",
    "Now would be a good time to pet your cat (or dog, we don't judge)",
]


@dataclass
class SchrootResult:
    """Result of ensuring a schroot exists."""

    name: str
    exists: bool
    created: bool = False
    error: str = ""


@dataclass(frozen=True)
class SchrootConfig:
    """Immutable configuration for schroot creation.

    Bundles all parameters needed to create or identify a schroot.
    The offline flag is kept separate as it's a policy concern.

    Attributes:
        series: Ubuntu series codename (e.g., "noble").
        arch: Architecture (e.g., "amd64").
        mirror: Ubuntu archive mirror URL.
        components: Tuple of components (e.g., ("main", "universe")).
        extra_repos: Optional tuple of extra repository lines.
    """

    series: str
    arch: str
    mirror: str
    components: tuple[str, ...]
    extra_repos: tuple[str, ...] = ()

    @classmethod
    def from_lists(
        cls,
        series: str,
        arch: str,
        mirror: str,
        components: list[str],
        extra_repos: list[str] | None = None,
    ) -> SchrootConfig:
        """Create SchrootConfig from list arguments."""
        return cls(
            series=series,
            arch=arch,
            mirror=mirror,
            components=tuple(components),
            extra_repos=tuple(extra_repos) if extra_repos else (),
        )


def get_schroot_name(series: str, arch: str) -> str:
    """Return the Packastack schroot name for a series/arch."""
    return f"packastack-{series}-{arch}"


def schroot_exists(name: str) -> bool:
    """Check if a schroot exists."""
    if shutil.which("schroot") is None:
        return False
    result = subprocess.run(
        ["schroot", "-c", name, "--info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _sudo_credentials_cached() -> bool:
    """Check if sudo credentials are already cached (no password prompt needed)."""
    result = subprocess.run(
        ["sudo", "-n", "true"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _ensure_sudo_cached() -> bool:
    """Prompt for sudo password upfront and cache credentials.

    Returns True if sudo credentials are now cached.
    """
    print("\n[schroot] sudo access required for schroot creation")
    result = subprocess.run(["sudo", "-v"], check=False)
    return result.returncode == 0


def _create_schroot(
    name: str,
    config: SchrootConfig,
) -> tuple[bool, str]:
    if shutil.which("sbuild-createchroot") is None:
        return False, "sbuild-createchroot not found"

    target_dir = Path("/var/lib/schroot/chroots") / name
    cmd = [
        "sbuild-createchroot",
        "--arch",
        config.arch,
        "--chroot-mode=schroot",
        f"--alias={name}",
    ]
    if config.components:
        cmd.append(f"--components={','.join(config.components)}")
    for repo in config.extra_repos:
        cmd.append(f"--extra-repository={repo}")

    cmd.extend([config.series, str(target_dir), config.mirror])

    if os.geteuid() != 0:
        cmd.insert(0, "sudo")

    # Sudo credentials should already be cached by _ensure_sudo_cached()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip() or "schroot creation failed"
        return False, err

    return True, ""


def ensure_schroot(
    config: SchrootConfig,
    offline: bool,
) -> SchrootResult:
    """Ensure the Packastack schroot exists, creating it if missing.

    Args:
        config: SchrootConfig with series, arch, mirror, and components.
        offline: If True, don't attempt to create missing schroot.

    Returns:
        SchrootResult indicating whether schroot exists/was created.
    """
    name = get_schroot_name(config.series, config.arch)

    if schroot_exists(name):
        return SchrootResult(name=name, exists=True)

    if offline:
        return SchrootResult(
            name=name,
            exists=False,
            error="schroot missing; offline mode prevents creation",
        )

    # When running as non-root, check if sudo credentials are cached
    # If not, prompt for password first (outside spinner) then proceed
    needs_sudo = os.geteuid() != 0
    if needs_sudo and not _sudo_credentials_cached() and not _ensure_sudo_cached():
        return SchrootResult(
            name=name,
            exists=False,
            error="sudo authentication failed",
        )

    # Pick a fun message for the wait
    fun_msg = random.choice(SCHROOT_WAIT_MESSAGES)
    spinner_msg = f"Creating schroot: {name} ({fun_msg})"

    with activity_spinner("schroot", spinner_msg):
        ok, err = _create_schroot(
            name=name,
            config=config,
        )

    if not ok:
        return SchrootResult(name=name, exists=False, error=err)

    return SchrootResult(name=name, exists=True, created=True)
