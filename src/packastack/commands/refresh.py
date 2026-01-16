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

"""Implementation of `packastack refresh ubuntu-archive` command.

Fetches and caches Ubuntu archive Packages.gz indexes with support for
TTL, conditional HTTP requests, offline mode, and proper exit codes.
"""

from __future__ import annotations

import datetime
import sys
from dataclasses import dataclass

import requests
import typer

from packastack.apt.archive import (
    ArchiveFetcher,
    load_metadata,
    validate_gzip,
    write_metadata,
)
from packastack.commands.init import _clone_or_update_project_config, _clone_or_update_releases
from packastack.core.config import load_config
from packastack.core.duration import parse_duration
from packastack.core.paths import resolve_paths
from packastack.core.run import RunContext, activity
from packastack.core.spinner import activity_spinner
from packastack.target.arch import resolve_arches
from packastack.target.series import resolve_series

# Exit codes per spec
EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR = 1
EXIT_PARTIAL_FAILURE = 2
EXIT_OFFLINE_MISSING = 3
EXIT_CORRUPT_CACHE = 4


@dataclass(frozen=True)
class RefreshConfig:
    """Immutable configuration for Ubuntu archive refresh.

    Attributes:
        ubuntu_series: Resolved Ubuntu series name (e.g., "noble").
        pockets: List of pockets (release, updates, security).
        components: List of components (main, universe).
        arches: List of architectures (may include 'host', 'all').
        mirror: Mirror URL.
        ttl_seconds: TTL in seconds for cached indexes.
        force: Ignore TTL and force fetch.
        offline: Run in offline mode (no network requests).
    """

    ubuntu_series: str
    pockets: tuple[str, ...]
    components: tuple[str, ...]
    arches: tuple[str, ...]
    mirror: str
    ttl_seconds: int
    force: bool = False
    offline: bool = False

    @classmethod
    def from_lists(
        cls,
        ubuntu_series: str,
        pockets: list[str],
        components: list[str],
        arches: list[str],
        mirror: str,
        ttl_seconds: int,
        force: bool = False,
        offline: bool = False,
    ) -> RefreshConfig:
        """Create RefreshConfig from list arguments (for CLI compatibility)."""
        return cls(
            ubuntu_series=ubuntu_series,
            pockets=tuple(pockets),
            components=tuple(components),
            arches=tuple(arches),
            mirror=mirror,
            ttl_seconds=ttl_seconds,
            force=force,
            offline=offline,
        )


def refresh_ubuntu_archive(
    config: RefreshConfig,
    run: RunContext | None = None,
) -> int:
    """Core refresh logic, callable from init command or CLI.

    Args:
        config: RefreshConfig with all refresh parameters.
        run: Optional RunContext for logging.

    Returns:
        Exit code (0=success, 2=partial failure, 3=offline missing, 4=corrupt).
    """
    cfg = load_config()
    paths = resolve_paths(cfg)
    cache_root = paths["ubuntu_archive_cache"]
    indexes_dir = cache_root / "indexes"

    # Resolve architectures (replace 'host' with actual, filter 'all')
    # Note: 'all' is not a separate binary- directory; arch-independent packages
    # are included in each architecture's Packages.gz (binary-amd64, etc.)
    resolved_arches = [a for a in resolve_arches(list(config.arches)) if a != "all"]

    # Create session for connection pooling
    session = requests.Session()
    fetcher = ArchiveFetcher(session=session)

    successes = 0
    failures = 0
    offline_missing = 0
    corrupt = 0

    now = datetime.datetime.now(datetime.UTC)

    from rich.console import Console
    from rich.progress import (
        BarColumn,
        Progress,
        TaskProgressColumn,
        TextColumn,
        TimeRemainingColumn,
    )

    targets = [
        (pocket, component, arch)
        for pocket in config.pockets
        for component in config.components
        for arch in resolved_arches
    ]

    console = Console(file=sys.__stdout__, force_terminal=True)
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeRemainingColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Refreshing Ubuntu package indexes", total=len(targets))

        for pocket, component, arch in targets:
            progress.update(task, description=f"Fetching {pocket}/{component}/{arch}")

            # Build destination path
            dest_dir = indexes_dir / config.ubuntu_series / pocket / component / f"binary-{arch}"
            dest_path = dest_dir / "Packages.gz"

            url = fetcher.build_url(config.mirror, config.ubuntu_series, pocket, component, arch)

            if run:
                run.log_event({
                    "event": "fetch.start",
                    "url": url,
                    "dest": str(dest_path),
                    "offline": config.offline,
                })

            # Check TTL unless force is set
            existing_meta = load_metadata(dest_path)
            if existing_meta and not config.force:
                try:
                    fetched_utc = datetime.datetime.fromisoformat(existing_meta["fetched_utc"])
                    if fetched_utc.tzinfo is None:
                        fetched_utc = fetched_utc.replace(tzinfo=datetime.UTC)
                    age_seconds = (now - fetched_utc).total_seconds()
                    if age_seconds < config.ttl_seconds:
                        activity("refresh", f"Skipping {pocket}/{component}/{arch} (within TTL)")
                        if run:
                            run.log_event({
                                "event": "fetch.skip_ttl",
                                "url": url,
                                "age_seconds": age_seconds,
                                "ttl_seconds": config.ttl_seconds,
                            })
                        successes += 1
                        progress.advance(task)
                        continue
                except (KeyError, ValueError):
                    pass  # Invalid metadata, proceed with fetch

            # Fetch the index
            result = fetcher.fetch_index(
                url=url,
                dest=dest_path,
                etag=existing_meta.get("etag") if existing_meta else None,
                last_modified=existing_meta.get("last_modified") if existing_meta else None,
                offline=config.offline,
            )

            if result.error:
                if config.offline and "not found" in result.error.lower():
                    activity("refresh", f"Missing in offline mode: {pocket}/{component}/{arch}")
                    if run:
                        run.log_event({"event": "fetch.offline_missing", "url": url, "error": result.error})
                    offline_missing += 1
                else:
                    activity("refresh", f"Failed: {pocket}/{component}/{arch} - {result.error}")
                    if run:
                        run.log_event({"event": "fetch.error", "url": url, "error": result.error})
                    failures += 1
                progress.advance(task)
                continue

            # Validate gzip integrity
            if dest_path.exists() and not validate_gzip(dest_path):
                activity("refresh", f"Corrupt gzip: {pocket}/{component}/{arch}")
                if run:
                    run.log_event({"event": "fetch.corrupt", "url": url, "path": str(dest_path)})
                corrupt += 1
                progress.advance(task)
                continue

            # Write metadata
            write_metadata(dest_path, result)

            status = "cached (304)" if result.was_cached else "fetched"
            activity("refresh", f"{status}: {pocket}/{component}/{arch}")
            if run:
                run.log_event({
                    "event": "fetch.success",
                    "url": url,
                    "was_cached": result.was_cached,
                    "sha256": result.sha256,
                    "size": result.size,
                })
            successes += 1
            progress.advance(task)

    session.close()

    # Determine exit code
    if corrupt > 0:
        return EXIT_CORRUPT_CACHE
    if offline_missing > 0:
        return EXIT_OFFLINE_MISSING
    if failures > 0:
        return EXIT_PARTIAL_FAILURE
    return EXIT_SUCCESS


def refresh(
    ubuntu_series: str = typer.Option("devel", "-u", "--ubuntu-series", help="Ubuntu series to refresh"),
    pockets: str = typer.Option("release,updates,security", "-p", "--pockets", help="Comma-separated pockets"),
    components: str = typer.Option("main,universe", "-c", "--components", help="Comma-separated components"),
    arches: str = typer.Option("host,all", "-a", "--arches", help="Comma-separated arches"),
    mirror: str = typer.Option("http://archive.ubuntu.com/ubuntu", "-m", "--mirror", help="Ubuntu mirror URL"),
    ttl: str = typer.Option("6h", "-T", "--ttl", help="TTL for cached indexes (e.g., 6h, 1d, 30m)"),
    force: bool = typer.Option(False, "-f", "--force", help="Ignore TTL and force fetch"),
    offline: bool = typer.Option(False, "-o", "--offline", help="Run in offline mode (no network requests)"),
) -> None:
    """Refresh Ubuntu archive Packages.gz indexes.

    Fetches package indexes from an Ubuntu mirror with support for conditional
    HTTP requests (ETag/If-Modified-Since), TTL-based caching, and offline mode.

    Exit codes:
      0 - Success
      1 - Configuration/usage error
      2 - Partial refresh failure (online)
      3 - Offline mode with missing required files
      4 - Corrupt cache detected
    """
    with RunContext("refresh") as run:
        # Parse and validate inputs
        try:
            ttl_seconds = parse_duration(ttl)
        except ValueError as e:
            activity("refresh", f"Invalid TTL: {e}")
            run.log_event({"event": "config.error", "error": str(e)})
            run.write_summary(status="failed", error=str(e))
            sys.exit(EXIT_CONFIG_ERROR)

        # Resolve series
        with activity_spinner("refresh", "Resolving Ubuntu series"):
            resolved_series = resolve_series(ubuntu_series)
            run.log_event({"event": "series.resolved", "series": resolved_series})
        activity("refresh", f"Series: {resolved_series}")

        # Update openstack-releases repository (unless offline)
        if not offline:
            cfg = load_config()
            paths = resolve_paths(cfg)
            releases_path = paths["openstack_releases_repo"]
            try:
                _clone_or_update_releases(releases_path, run, phase="refresh")
            except Exception as e:  # pragma: no cover
                activity("refresh", f"Warning: Could not update openstack-releases: {e}")
                run.log_event({"event": "openstack_releases.warning", "error": str(e)})

            # Update openstack-project-config repository
            project_config_path = paths["openstack_project_config"]
            try:
                _clone_or_update_project_config(project_config_path, run, phase="refresh")
            except Exception as e:  # pragma: no cover
                activity("refresh", f"Warning: Could not update openstack-project-config: {e}")
                run.log_event({"event": "openstack_project_config.warning", "error": str(e)})

        # Parse comma-separated lists
        pocket_list = [p.strip() for p in pockets.split(",") if p.strip()]
        component_list = [c.strip() for c in components.split(",") if c.strip()]
        arch_list = [a.strip() for a in arches.split(",") if a.strip()]

        run.log_event({
            "event": "refresh.start",
            "series": resolved_series,
            "pockets": pocket_list,
            "components": component_list,
            "arches": arch_list,
            "mirror": mirror,
            "ttl_seconds": ttl_seconds,
            "force": force,
            "offline": offline,
        })

        # Perform refresh
        refresh_config = RefreshConfig.from_lists(
            ubuntu_series=resolved_series,
            pockets=pocket_list,
            components=component_list,
            arches=arch_list,
            mirror=mirror,
            ttl_seconds=ttl_seconds,
            force=force,
            offline=offline,
        )
        exit_code = refresh_ubuntu_archive(refresh_config, run=run)

        # Write summary
        status_map = {
            EXIT_SUCCESS: "success",
            EXIT_PARTIAL_FAILURE: "partial_failure",
            EXIT_OFFLINE_MISSING: "offline_missing",
            EXIT_CORRUPT_CACHE: "corrupt_cache",
        }
        run.write_summary(
            status=status_map.get(exit_code, "unknown"),
            exit_code=exit_code,
            series=resolved_series,
            pockets=pocket_list,
            components=component_list,
            arches=arch_list,
        )

    sys.exit(exit_code)
