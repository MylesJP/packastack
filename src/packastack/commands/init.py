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

"""Implementation of `packastack init` command.

Creates configuration and cache directories, clones the OpenStack releases
repository, and optionally primes minimal Ubuntu archive metadata.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import git
import typer

from packastack.config import ensure_config_exists, load_config
from packastack.paths import ensure_directories
from packastack.run import RunContext, activity
from packastack.series import resolve_series
from packastack.spinner import activity_spinner

if TYPE_CHECKING:
    from packastack.run import RunContext as RunContextType

OPENSTACK_RELEASES_URL = "https://opendev.org/openstack/releases"


def _clone_or_update_releases(path: Path, run: RunContextType, phase: str = "init") -> None:
    """Clone or update the openstack/releases repository."""
    if path.exists() and (path / ".git").is_dir():
        # Repository exists, fetch and prune
        run.log_event({"event": "openstack_releases.fetch", "path": str(path)})
        with activity_spinner(phase, f"Updating openstack-releases at {path}"):
            try:
                repo = git.Repo(path)
                origin = repo.remotes.origin
                origin.fetch(prune=True)
            except git.GitCommandError as e:  # pragma: no cover
                run.log_event({"event": "openstack_releases.fetch_error", "error": str(e)})
                raise
    else:
        # Clone fresh
        run.log_event({"event": "openstack_releases.clone", "url": OPENSTACK_RELEASES_URL, "path": str(path)})
        with activity_spinner(phase, f"Cloning openstack-releases to {path}"):
            try:
                git.Repo.clone_from(OPENSTACK_RELEASES_URL, path)
            except git.GitCommandError as e:  # pragma: no cover
                run.log_event({"event": "openstack_releases.clone_error", "error": str(e)})
                raise


def _create_ubuntu_archive_files(ubuntu_cache: Path) -> None:
    """Create README.txt and config.json in ubuntu-archive cache."""
    readme_path = ubuntu_cache / "README.txt"
    config_path = ubuntu_cache / "config.json"

    readme_content = """\
Packastack Ubuntu Archive Cache
================================

This directory contains cached Ubuntu archive Packages.gz indexes.

Structure:
  indexes/<series>/<pocket>/<component>/binary-<arch>/Packages.gz
  indexes/<series>/<pocket>/<component>/binary-<arch>/Packages.meta.json
  snapshots/

The Packages.meta.json files contain metadata about each cached index:
  - url: Source URL
  - etag: HTTP ETag for conditional requests
  - last_modified: HTTP Last-Modified header
  - fetched_utc: When the file was fetched
  - sha256: SHA-256 checksum
  - size: File size in bytes

Use `packastack refresh ubuntu-archive` to update these indexes.
"""
    readme_path.write_text(readme_content)

    config_data = {
        "mirror": "http://archive.ubuntu.com/ubuntu",
        "last_refresh": None,
    }
    config_path.write_text(json.dumps(config_data, indent=2))


def init(
    prime: bool = typer.Option(False, help="Prime minimal Ubuntu archive metadata after init"),
) -> None:
    """Initialize Packastack configuration and cache directories.

    Creates all required directories, writes default config.yaml if missing,
    clones or updates the openstack-releases repository, and optionally
    primes minimal Ubuntu archive metadata.
    """
    with RunContext("init") as run:
        steps_completed: list[str] = []

        # Step 1: Ensure config exists
        with activity_spinner("init", "Creating configuration"):
            ensure_config_exists()
            steps_completed.append("config_created")
            run.log_event({"event": "config.ensured"})

        # Step 2: Create cache directories
        with activity_spinner("init", "Creating cache directories"):
            cfg = load_config()
            paths = ensure_directories()
            steps_completed.append("directories_created")
            run.log_event({"event": "directories.created", "paths": {k: str(v) for k, v in paths.items()}})

        # Step 3: Clone or update openstack-releases
        releases_path = paths["openstack_releases_repo"]
        try:
            _clone_or_update_releases(releases_path, run)
            steps_completed.append("openstack_releases_ready")
        except Exception as e:  # pragma: no cover
            activity("init", f"Warning: Could not clone/update openstack-releases: {e}")
            run.log_event({"event": "openstack_releases.warning", "error": str(e)})

        # Step 4: Create ubuntu-archive README and config
        ubuntu_cache = paths["ubuntu_archive_cache"]
        with activity_spinner("init", "Creating ubuntu-archive metadata"):
            _create_ubuntu_archive_files(ubuntu_cache)
            steps_completed.append("ubuntu_archive_files_created")
            run.log_event({"event": "ubuntu_archive.files_created"})

        # Step 5: Resolve devel series
        with activity_spinner("init", "Resolving Ubuntu development series"):
            devel_series = resolve_series("devel")
            run.log_event({"event": "series.resolved", "devel": devel_series})
            steps_completed.append("series_resolved")
        activity("init", f"Development series: {devel_series}")

        # Step 6: Optionally prime minimal metadata
        if prime:  # pragma: no cover - integration test path
            activity("init", "Priming minimal Ubuntu archive metadata")
            run.log_event({"event": "prime.start"})
            # Import refresh logic here to avoid circular imports
            from packastack.commands.refresh import refresh_ubuntu_archive

            try:
                refresh_ubuntu_archive(
                    ubuntu_series=devel_series,
                    pockets=["release", "updates", "security"],
                    components=["main", "universe"],
                    arches=["host", "all"],
                    mirror=cfg.get("mirrors", {}).get("ubuntu_archive", "http://archive.ubuntu.com/ubuntu"),
                    ttl_seconds=0,  # Force fetch
                    force=True,
                    offline=False,
                    run=run,
                )
                steps_completed.append("metadata_primed")
                run.log_event({"event": "prime.complete"})
            except Exception as e:
                activity("init", f"Warning: Could not prime metadata: {e}")
                run.log_event({"event": "prime.warning", "error": str(e)})

        # Write summary
        run.write_summary(
            steps_completed=steps_completed,
            devel_series=devel_series,
            primed=prime,
        )

    sys.exit(0)
