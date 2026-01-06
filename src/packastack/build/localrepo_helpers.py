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

"""Local APT repository helper functions.

This module provides utilities for regenerating local APT repository
indexes after build operations.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from packastack.apt import localrepo
from packastack.core.run import activity

if TYPE_CHECKING:
    from packastack.core.run import RunContext


def refresh_local_repo_indexes(
    local_repo: Path,
    arch: str,
    run: RunContext,
    phase: str = "verify",
) -> tuple[localrepo.IndexResult, localrepo.SourceIndexResult]:
    """Regenerate binary and source indexes for the local APT repository.

    Ensures `Packages`/`Packages.gz` and `Sources`/`Sources.gz` exist even when
    no artifacts were published, avoiding confusing missing-metadata errors.

    Args:
        local_repo: Path to local APT repository.
        arch: Architecture for binary packages (e.g., "amd64").
        run: RunContext for logging events.
        phase: Phase name for activity logging (default: "verify").

    Returns:
        Tuple of (IndexResult, SourceIndexResult) with regeneration results.
    """
    index_result = localrepo.regenerate_indexes(local_repo, arch=arch)
    if index_result.success:
        activity(phase, f"Regenerated Packages index ({index_result.package_count} packages)")
        run.log_event(
            {
                "event": f"{phase}.index",
                "package_count": index_result.package_count,
                "packages_file": str(index_result.packages_file) if index_result.packages_file else None,
            }
        )
    else:
        activity(phase, f"Warning: Failed to regenerate binary indexes: {index_result.error}")
        run.log_event({"event": f"{phase}.index_failed", "error": index_result.error})

    source_index_result = localrepo.regenerate_source_indexes(local_repo)
    if source_index_result.success:
        activity(phase, f"Regenerated Sources index ({source_index_result.source_count} sources)")
        run.log_event(
            {
                "event": f"{phase}.source_index",
                "source_count": source_index_result.source_count,
                "sources_file": str(source_index_result.sources_file)
                if source_index_result.sources_file
                else None,
            }
        )
    else:
        activity(phase, f"Warning: Failed to regenerate source indexes: {source_index_result.error}")
        run.log_event({"event": f"{phase}.source_index_failed", "error": source_index_result.error})

    return index_result, source_index_result


# Backwards compatibility alias
_refresh_local_repo_indexes = refresh_local_repo_indexes
