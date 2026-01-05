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

"""Clean command for Packastack.

Removes cached data including:
- Cached upstream tarballs and extractions
- Git repository caches
- Build workspaces
"""

from __future__ import annotations

import shutil
from pathlib import Path

import typer

from packastack.core.config import load_config
from packastack.upstream.tarball_cache import (
    DEFAULT_CACHE_DIR,
    cleanup_expired_cache,
    get_cache_size,
    list_cached_projects,
)

# Size threshold for warnings (10GB)
SIZE_WARNING_THRESHOLD = 10 * 1024 * 1024 * 1024


def format_size(size_bytes: int) -> str:
    """Format a size in bytes as a human-readable string."""
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"
    elif size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes} bytes"


def activity(phase: str, message: str) -> None:
    """Print a status message."""
    typer.echo(f"[{phase}] {message}")


def clean(
    all_caches: bool = typer.Option(False, "-a", "--all", help="Remove all caches (tarballs, workspaces, apt repo)"),
    tarballs: bool = typer.Option(False, "--tarballs", help="Remove cached tarballs and extractions"),
    workspaces: bool = typer.Option(False, "--workspaces", help="Remove temporary build workspaces"),
    apt_repo: bool = typer.Option(False, "--apt-repo", help="Remove local APT repository cache"),
    expired: bool = typer.Option(False, "--expired", help="Remove only expired cache entries"),
    dry_run: bool = typer.Option(False, "-n", "--dry-run", help="Show what would be removed without removing"),
    force: bool = typer.Option(False, "-f", "--force", help="Skip confirmation prompts"),
    max_age: int = typer.Option(14, "--max-age", help="Maximum age in days for cache entries"),
) -> None:
    """Clean up cached data and temporary files.

    By default, shows what would be cleaned without making changes.
    Use --dry-run to preview changes, or specific options to remove caches.

    Examples:
        packastack clean --dry-run          # Preview what would be removed
        packastack clean --expired          # Remove only expired entries
        packastack clean --tarballs         # Remove tarball extraction cache
        packastack clean --all              # Remove all caches
    """
    cfg = load_config()
    paths = cfg.get("paths", {})
    tarball_cache_dir = Path(paths.get("upstream_tarballs", DEFAULT_CACHE_DIR))

    # Determine what to clean
    clean_tarballs = all_caches or tarballs
    clean_workspaces = all_caches or workspaces
    clean_apt_repo = all_caches or apt_repo
    clean_expired_only = expired and not (all_caches or tarballs or workspaces or apt_repo)

    # If nothing specified, show status
    if not any([clean_tarballs, clean_workspaces, clean_apt_repo, clean_expired_only]):
        _show_cache_status(paths)
        return

    # Calculate sizes before cleaning
    tarball_cache_size = get_cache_size(tarball_cache_dir)
    workspace_dir = Path(paths.get("build_root", Path.home() / ".cache" / "packastack" / "build"))
    workspace_size = _get_dir_size(workspace_dir)
    apt_repo_dir = Path(paths.get("local_apt_repo", Path.home() / ".cache" / "packastack" / "apt-repo"))
    apt_repo_size = _get_dir_size(apt_repo_dir)

    total_size = 0
    items_to_remove: list[tuple[str, Path, int]] = []

    # Collect tarball cache items
    if clean_tarballs:
        if tarball_cache_dir.exists():
            items_to_remove.append(("Tarball cache", tarball_cache_dir, tarball_cache_size))
            total_size += tarball_cache_size

    # Collect workspace items
    if clean_workspaces and workspace_dir.exists():
        items_to_remove.append(("Build workspaces", workspace_dir, workspace_size))
        total_size += workspace_size

    # Collect apt repo items
    if clean_apt_repo and apt_repo_dir.exists():
        items_to_remove.append(("Local APT repo", apt_repo_dir, apt_repo_size))
        total_size += apt_repo_size

    # Handle expired-only mode
    if clean_expired_only:
        activity("clean", "Checking for expired cache entries...")
        cached = list_cached_projects(tarball_cache_dir)
        expired_entries = [
            (p, v, m) for p, v, m in cached
            if m is None or m.is_expired(max_age)
        ]

        if not expired_entries:
            activity("clean", "No expired cache entries found")
            return

        activity("clean", f"Found {len(expired_entries)} expired entries")
        for proj, ver, meta in expired_entries[:10]:
            activity("clean", f"  {proj}/{ver}")
        if len(expired_entries) > 10:
            activity("clean", f"  ... and {len(expired_entries) - 10} more")

        if dry_run:
            activity("clean", "(dry-run) Would remove expired entries")
            return

        if not force:
            confirm = typer.confirm(f"Remove {len(expired_entries)} expired entries?")
            if not confirm:
                activity("clean", "Aborted")
                return

        removed = cleanup_expired_cache(tarball_cache_dir, max_age)
        activity("clean", f"Removed {len(removed)} expired entries")
        return

    # No items to remove
    if not items_to_remove:
        activity("clean", "Nothing to clean")
        return

    # Show what will be removed
    activity("clean", "Items to remove:")
    for name, path, size in items_to_remove:
        activity("clean", f"  {name}: {path} ({format_size(size)})")

    activity("clean", f"Total size: {format_size(total_size)}")

    # Size warning
    if total_size >= SIZE_WARNING_THRESHOLD:
        typer.secho(
            f"\n⚠️  Warning: About to remove {format_size(total_size)} of data!",
            fg=typer.colors.YELLOW,
            bold=True,
        )

    if dry_run:
        activity("clean", "(dry-run) No files removed")
        return

    # Confirm
    if not force:
        confirm = typer.confirm(f"Remove {len(items_to_remove)} item(s)?")
        if not confirm:
            activity("clean", "Aborted")
            return

    # Remove items
    for name, path, size in items_to_remove:
        try:
            if path.exists():
                shutil.rmtree(path)
                activity("clean", f"Removed: {name} ({format_size(size)})")
        except OSError as e:
            activity("clean", f"Error removing {name}: {e}")

    activity("clean", f"Cleaned {format_size(total_size)}")


def _show_cache_status(paths: dict) -> None:
    """Show current cache status."""
    activity("status", "Cache status:")

    # Tarball cache
    tarball_cache_dir = Path(paths.get("upstream_tarballs", DEFAULT_CACHE_DIR))
    tarball_size = get_cache_size(tarball_cache_dir)
    cached_projects = list_cached_projects(tarball_cache_dir)
    activity("status", f"  Tarball cache: {format_size(tarball_size)} ({len(cached_projects)} entries)")

    # Count expired
    expired_count = sum(1 for _, _, m in cached_projects if m is None or m.is_expired())
    if expired_count > 0:
        activity("status", f"    ({expired_count} expired)")

    # Workspaces
    workspace_dir = Path(paths.get("build_root", Path.home() / ".cache" / "packastack" / "build"))
    if workspace_dir.exists():
        workspace_size = _get_dir_size(workspace_dir)
        workspace_count = sum(1 for p in workspace_dir.iterdir() if p.is_dir())
        activity("status", f"  Build workspaces: {format_size(workspace_size)} ({workspace_count} entries)")
    else:
        activity("status", "  Build workspaces: (none)")

    # Local APT repo
    apt_repo_dir = Path(paths.get("local_apt_repo", Path.home() / ".cache" / "packastack" / "apt-repo"))
    if apt_repo_dir.exists():
        apt_repo_size = _get_dir_size(apt_repo_dir)
        activity("status", f"  Local APT repo: {format_size(apt_repo_size)}")
    else:
        apt_repo_size = 0
        activity("status", "  Local APT repo: (none)")

    total = tarball_size + (_get_dir_size(workspace_dir) if workspace_dir.exists() else 0) + apt_repo_size
    activity("status", f"  Total: {format_size(total)}")

    if total >= SIZE_WARNING_THRESHOLD:
        typer.secho(
            f"\n⚠️  Cache size exceeds 10GB. Consider running: packastack clean --all",
            fg=typer.colors.YELLOW,
        )

    typer.echo("\nUse 'packastack clean --help' for cleanup options.")


def _get_dir_size(path: Path) -> int:
    """Get total size of a directory in bytes."""
    if not path.exists():
        return 0

    total = 0
    try:
        for file_path in path.rglob("*"):
            if file_path.is_file():
                try:
                    total += file_path.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total
