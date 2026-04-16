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

# Suffix used by sbuild-createchroot to name the schroot config.
# Default sbuild suffix is "-sbuild"; we use a custom one to avoid
# collisions with user-managed schroots.
CHROOT_SUFFIX = "-packastack"

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


def get_sbuild_chroot_name(series: str, arch: str) -> str:
    """Return the chroot name that sbuild-createchroot registers.

    sbuild-createchroot names its chroot config as
    ``{series}-{arch}{CHROOT_SUFFIX}``.
    """
    return f"{series}-{arch}{CHROOT_SUFFIX}"


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
        f"--chroot-suffix={CHROOT_SUFFIX}",
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

    # Also check the sbuild-registered chroot name
    # ({series}-{arch}{CHROOT_SUFFIX}) which sbuild-createchroot creates.
    sbuild_name = get_sbuild_chroot_name(config.series, config.arch)
    if schroot_exists(sbuild_name):
        return SchrootResult(name=sbuild_name, exists=True)

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


# ---------------------------------------------------------------------------
# Schroot fstab configuration for local APT repository bind-mount
# ---------------------------------------------------------------------------

REPO_FSTAB_MARKER = "# PackaStack local apt repo"
_CHROOT_CONF_DIR = Path("/etc/schroot/chroot.d")


class RepoMountError(Exception):
    """Raised when configuring the schroot repo mount fails."""

    pass  # pragma: no cover


def _find_schroot_config(chroot_name: str) -> Path | None:
    """Locate the schroot config file that defines *chroot_name*.

    Scans ``/etc/schroot/chroot.d/`` for a file whose INI section header
    matches ``[chroot_name]`` or whose ``aliases=`` line includes it.
    """
    if not _CHROOT_CONF_DIR.is_dir():
        return None
    for candidate in _CHROOT_CONF_DIR.iterdir():
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if f"[{chroot_name}]" in text:
            return candidate
        for line in text.splitlines():
            if line.strip().startswith("aliases=") and chroot_name in line:
                return candidate
    return None


def _get_profile_fstab_path(config_text: str) -> Path:
    """Return the fstab path implied by a schroot config block.

    Checks ``setup.fstab=`` first, then falls back to the ``profile=``
    directory's ``fstab`` file, and finally the sbuild default.
    """
    for line in config_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("setup.fstab="):
            return Path(stripped.split("=", 1)[1].strip())
        if stripped.startswith("profile="):
            profile = stripped.split("=", 1)[1].strip()
            return Path(f"/etc/schroot/{profile}/fstab")
    return Path("/etc/schroot/sbuild/fstab")


def _fstab_has_repo_mount(fstab_path: Path, repo_path: str) -> bool:
    """Return True if *fstab_path* already bind-mounts *repo_path*."""
    if not fstab_path.is_file():
        return False
    try:
        return repo_path in fstab_path.read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return False


def _sudo_write_file(path: Path, content: str) -> None:
    """Write *content* to *path* via ``sudo tee``."""
    subprocess.run(
        ["sudo", "tee", str(path)],
        input=content.encode("utf-8"),
        stdout=subprocess.DEVNULL,
        check=True,
    )


def _sudo_set_fstab_key(config_path: Path, fstab_path: Path) -> None:
    """Set ``setup.fstab=`` in an existing schroot config file."""
    text = config_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    key = f"setup.fstab={fstab_path}"
    for i, line in enumerate(lines):
        if line.strip().startswith("setup.fstab="):
            lines[i] = key
            _sudo_write_file(config_path, "\n".join(lines) + "\n")
            return

    for i, line in enumerate(lines):
        if line.strip().startswith("profile="):
            lines.insert(i + 1, key)
            _sudo_write_file(config_path, "\n".join(lines) + "\n")
            return

    lines.append(key)
    _sudo_write_file(config_path, "\n".join(lines) + "\n")


def configure_repo_mount(
    chroot_name: str,
    local_repo_root: Path,
    chroot_mount: str = "/srv/packastack-apt",
) -> bool:
    """Ensure the schroot's fstab bind-mounts the local APT repository.

    If the schroot's fstab already contains the correct bind-mount entry
    this is a no-op.  Otherwise a custom fstab is written (extending the
    profile's base fstab) and the schroot config is updated to reference it.

    Requires sudo when the fstab needs to be created or updated.

    Args:
        chroot_name: Name (or alias) of the schroot.
        local_repo_root: Host path to the PackaStack local APT repository.
        chroot_mount: Mount point inside the chroot.

    Returns:
        True if the fstab is (now) correctly configured.

    Raises:
        RepoMountError: When the schroot config cannot be found or the
            fstab cannot be written.
    """
    config_path = _find_schroot_config(chroot_name)
    if config_path is None:
        raise RepoMountError(
            f"Cannot find schroot config for '{chroot_name}' "
            f"in {_CHROOT_CONF_DIR}"
        )

    repo_path = str(local_repo_root.resolve())
    config_text = config_path.read_text(encoding="utf-8", errors="replace")

    base_fstab = _get_profile_fstab_path(config_text)
    custom_fstab = Path(f"/etc/schroot/packastack-{chroot_name}.fstab")

    if _fstab_has_repo_mount(custom_fstab, repo_path):
        return True

    try:
        base_content = base_fstab.read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError as exc:
        raise RepoMountError(
            f"Cannot read base fstab {base_fstab}: {exc}"
        ) from exc

    mount_line = f"{repo_path} {chroot_mount} none ro,bind 0 0"
    custom_content = (
        f"{base_content.rstrip()}\n{REPO_FSTAB_MARKER}\n{mount_line}\n"
    )

    try:
        _sudo_write_file(custom_fstab, custom_content)
        _sudo_set_fstab_key(config_path, custom_fstab)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise RepoMountError(
            f"Failed to write fstab or update schroot config: {exc}"
        ) from exc

    return True
