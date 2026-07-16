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

"""Unshare chroot-mode helpers for Packastack builds.

sbuild 0.88 changed its default ``--chroot-mode`` from ``schroot`` to
``unshare``. The two modes are incompatible: schroot uses a root-owned
directory registered under ``/etc/schroot/chroot.d/`` while unshare uses a
plain tarball in ``~/.cache/sbuild/`` owned by the invoking user and built
in rootless user namespaces (no sudo required).

This module manages the unshare-mode build tarball lifecycle and decides
which chroot mode a build should use when the configuration says ``auto``.
"""

from __future__ import annotations

import os
import pwd
import random
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from packastack.build.schroot import (
    SCHROOT_WAIT_MESSAGES,
    SchrootConfig,
    _combine_process_output,
    get_sbuild_chroot_name,
    get_schroot_name,
    schroot_exists,
)
from packastack.core.spinner import activity_spinner

# Valid chroot-mode configuration values.
CHROOT_MODES = ("auto", "schroot", "unshare")

# sbuild release that flipped the default chroot mode to unshare.
UNSHARE_DEFAULT_SBUILD_VERSION = (0, 88)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


class ChrootModeError(Exception):
    """Raised when an invalid chroot mode is configured."""

    pass  # pragma: no cover


@dataclass
class UnshareResult:
    """Result of ensuring an unshare build tarball exists.

    Attributes:
        name: Absolute path to the tarball (usable as sbuild ``--chroot``).
        exists: True if the tarball exists (or was just created).
        created: True if the tarball was created during this call.
        error: Human-readable error when exists is False.
    """

    name: str
    exists: bool
    created: bool = False
    error: str = ""


def get_unshare_cache_dir() -> Path:
    """Return the directory where sbuild looks for unshare tarballs.

    sbuild resolves unshare chroots from ``$XDG_CACHE_HOME/sbuild``
    (defaulting to ``~/.cache/sbuild``).
    """
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
    return base / "sbuild"


def get_unshare_tarball_path(series: str, arch: str) -> Path:
    """Return the canonical tarball path Packastack creates for series/arch."""
    return get_unshare_cache_dir() / f"{series}-{arch}.tar.zst"


def find_unshare_tarball(series: str, arch: str) -> Path | None:
    """Find an existing unshare tarball for series/arch.

    Matches any compression suffix sbuild accepts
    (``{series}-{arch}.tar``, ``.tar.gz``, ``.tar.xz``, ``.tar.zst``, ...).
    """
    cache_dir = get_unshare_cache_dir()
    if not cache_dir.is_dir():
        return None
    matches = sorted(path for path in cache_dir.glob(f"{series}-{arch}.tar*") if path.is_file())
    return matches[0] if matches else None


def get_sbuild_version() -> tuple[int, ...] | None:
    """Return the installed sbuild version as a tuple, or None if unknown."""
    if shutil.which("sbuild") is None:
        return None
    result = subprocess.run(
        ["sbuild", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    match = _VERSION_RE.search(result.stdout)
    if not match:
        return None
    return tuple(int(part) for part in match.groups() if part is not None)


def sbuild_defaults_to_unshare() -> bool:
    """Return True if the installed sbuild defaults to unshare chroot mode."""
    version = get_sbuild_version()
    if version is None:
        return False
    return version[:2] >= UNSHARE_DEFAULT_SBUILD_VERSION


def _has_subid_entry(path: Path, user: str, uid: str) -> bool:
    """Return True if *path* has a subordinate-id entry for the user."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for line in text.splitlines():
        owner = line.split(":", 1)[0].strip()
        if owner in (user, uid):
            return True
    return False


def check_unshare_prerequisites() -> str:
    """Check host prerequisites for unshare-mode builds.

    Returns:
        Empty string when the host is ready, otherwise a human-readable
        description of what is missing.
    """
    if shutil.which("mmdebstrap") is None:
        return "mmdebstrap not found (install with: sudo apt install mmdebstrap)"

    uid = os.getuid()
    user = pwd.getpwuid(uid).pw_name
    for subid_file in (Path("/etc/subuid"), Path("/etc/subgid")):
        if not _has_subid_entry(subid_file, user, str(uid)):
            return (
                f"no entry for user '{user}' in {subid_file} "
                "(required for rootless user namespaces; "
                f"add one with: sudo usermod --add-subuids 100000-165535 "
                f"--add-subgids 100000-165535 {user})"
            )

    return ""


def resolve_chroot_mode(configured: str, series: str, arch: str) -> str:
    """Resolve the effective chroot mode for a build.

    Explicit configuration ("schroot" or "unshare") wins. In "auto" mode the
    decision prefers whichever build environment already exists so users
    never rebuild a working chroot, then falls back to the installed
    sbuild's own default:

    1. An unshare tarball for series/arch exists -> "unshare".
    2. A Packastack schroot for series/arch exists -> "schroot".
    3. sbuild defaults to unshare and the host prerequisites for
       rootless namespaces are met -> "unshare".
    4. Otherwise -> "schroot".

    Raises:
        ChrootModeError: If *configured* is not a recognised mode.
    """
    if configured not in CHROOT_MODES:
        raise ChrootModeError(
            f"Invalid sbuild.chroot_mode '{configured}' (expected one of {CHROOT_MODES})"
        )
    if configured != "auto":
        return configured

    if find_unshare_tarball(series, arch) is not None:
        return "unshare"

    if schroot_exists(get_schroot_name(series, arch)) or schroot_exists(
        get_sbuild_chroot_name(series, arch)
    ):
        return "schroot"

    if sbuild_defaults_to_unshare() and not check_unshare_prerequisites():
        return "unshare"

    return "schroot"


def _create_unshare_tarball(config: SchrootConfig, tarball: Path) -> tuple[bool, str]:
    """Create the unshare build tarball with mmdebstrap.

    Runs entirely unprivileged (``--mode=unshare``). On failure any partial
    tarball is removed so the next attempt starts clean.
    """
    tarball.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "mmdebstrap",
        "--mode=unshare",
        f"--arch={config.arch}",
        "--variant=buildd",
        f"--components={','.join(config.components)}",
        "--include=fakeroot",
        config.series,
        str(tarball),
        config.mirror,
        *config.extra_repos,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        tarball.unlink(missing_ok=True)
        return False, _combine_process_output(result)
    return True, ""


def ensure_unshare_tarball(config: SchrootConfig, offline: bool) -> UnshareResult:
    """Ensure the unshare build tarball exists, creating it if missing.

    Args:
        config: SchrootConfig with series, arch, mirror, and components.
        offline: If True, don't attempt to create a missing tarball.

    Returns:
        UnshareResult indicating whether the tarball exists/was created.
    """
    existing = find_unshare_tarball(config.series, config.arch)
    if existing is not None:
        return UnshareResult(name=str(existing), exists=True)

    tarball = get_unshare_tarball_path(config.series, config.arch)

    if offline:
        return UnshareResult(
            name=str(tarball),
            exists=False,
            error="unshare build tarball missing; offline mode prevents creation",
        )

    prereq_error = check_unshare_prerequisites()
    if prereq_error:
        return UnshareResult(name=str(tarball), exists=False, error=prereq_error)

    fun_msg = random.choice(SCHROOT_WAIT_MESSAGES)
    spinner_msg = f"Creating unshare build tarball: {tarball.name} ({fun_msg})"

    with activity_spinner("unshare", spinner_msg):
        ok, err = _create_unshare_tarball(config, tarball)

    if not ok:
        return UnshareResult(name=str(tarball), exists=False, error=err)

    return UnshareResult(name=str(tarball), exists=True, created=True)
