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

"""Provenance recording for PackaStack builds.

This module records detailed provenance information for each build,
enabling auditability and future recreation of builds.
"""

from __future__ import annotations

import datetime
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class UpstreamProvenance:
    """Provenance information about the upstream source."""

    url: str = ""
    ref: str = ""
    sha: str = ""
    branch: str = ""


@dataclass
class ReleaseSourceProvenance:
    """Provenance information about release discovery."""

    type: str = ""
    deliverable: str = ""
    tag_regex: str = ""
    pypi_project: str = ""
    resolved_version: str = ""


@dataclass
class TarballProvenance:
    """Provenance information about tarball acquisition."""

    method: str = ""
    url: str = ""
    path: str = ""
    sha256: str = ""


@dataclass
class VerificationProvenance:
    """Provenance information about verification."""

    mode: str = ""
    result: str = ""  # "verified", "skipped", "failed", "not_applicable"
    signature_url: str = ""
    key_id: str = ""


@dataclass
class WatchMismatchProvenance:
    """Provenance information about debian/watch mismatch."""

    detected: bool = False
    watch_mode: str = ""
    watch_url: str = ""
    registry_mode: str = ""
    message: str = ""


@dataclass
class BuildProvenance:
    """Complete provenance record for a build.

    This captures all information needed to understand and reproduce
    how a build was performed.
    """

    # Identification
    source_package: str = ""
    build_timestamp: str = ""
    run_id: str = ""

    # Registry resolution
    registry_version: int = 0
    resolution_source: str = ""  # registry_explicit, registry_defaults, legacy
    project_key: str = ""

    # Upstream source
    upstream: UpstreamProvenance = field(default_factory=UpstreamProvenance)

    # Release source
    release_source: ReleaseSourceProvenance = field(
        default_factory=ReleaseSourceProvenance
    )

    # Tarball acquisition
    tarball: TarballProvenance = field(default_factory=TarballProvenance)

    # Verification
    verification: VerificationProvenance = field(default_factory=VerificationProvenance)

    # Overrides
    overrides_applied: list[str] = field(default_factory=list)
    registry_override_path: str = ""

    # Watch mismatch
    watch_mismatch: WatchMismatchProvenance = field(
        default_factory=WatchMismatchProvenance
    )

    # Build type
    build_type: str = ""  # release, snapshot, milestone


def create_provenance(
    source_package: str,
    run_id: str = "",
) -> BuildProvenance:
    """Create a new provenance record.

    Args:
        source_package: Name of the source package.
        run_id: Run ID from RunContext.

    Returns:
        New BuildProvenance with timestamp set.
    """
    return BuildProvenance(
        source_package=source_package,
        build_timestamp=datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z"),
        run_id=run_id,
    )


def write_provenance(
    provenance: BuildProvenance,
    run_path: Path,
) -> Path:
    """Write provenance record to disk.

    Args:
        provenance: The provenance record.
        run_path: Path to the run directory.

    Returns:
        Path to the written provenance file.
    """
    provenance_dir = run_path / "provenance"
    provenance_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{provenance.source_package}.yaml"
    provenance_path = provenance_dir / filename

    # Convert to dict, handling nested dataclasses
    data = _to_dict(provenance)

    with open(provenance_path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)

    return provenance_path


def _to_dict(obj: Any) -> Any:
    """Convert dataclass to dict recursively."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _to_dict(v) for k, v in asdict(obj).items()}
    elif isinstance(obj, list):
        return [_to_dict(item) for item in obj]
    elif isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    else:
        return obj


def load_provenance(provenance_path: Path) -> BuildProvenance:
    """Load a provenance record from disk.

    Args:
        provenance_path: Path to the provenance file.

    Returns:
        BuildProvenance loaded from file.

    Raises:
        FileNotFoundError: If provenance file doesn't exist.
        yaml.YAMLError: If file can't be parsed.
    """
    with open(provenance_path) as f:
        data = yaml.safe_load(f)

    return _from_dict(data)


def _from_dict(data: dict[str, Any]) -> BuildProvenance:
    """Create BuildProvenance from dict."""
    upstream = UpstreamProvenance(**data.get("upstream", {}))
    release_source = ReleaseSourceProvenance(**data.get("release_source", {}))
    tarball = TarballProvenance(**data.get("tarball", {}))
    verification = VerificationProvenance(**data.get("verification", {}))
    watch_mismatch = WatchMismatchProvenance(**data.get("watch_mismatch", {}))

    return BuildProvenance(
        source_package=data.get("source_package", ""),
        build_timestamp=data.get("build_timestamp", ""),
        run_id=data.get("run_id", ""),
        registry_version=data.get("registry_version", 0),
        resolution_source=data.get("resolution_source", ""),
        project_key=data.get("project_key", ""),
        upstream=upstream,
        release_source=release_source,
        tarball=tarball,
        verification=verification,
        overrides_applied=data.get("overrides_applied", []),
        registry_override_path=data.get("registry_override_path", ""),
        watch_mismatch=watch_mismatch,
        build_type=data.get("build_type", ""),
    )


def summarize_provenance(provenance: BuildProvenance) -> dict[str, Any]:
    """Create a summary of provenance for inclusion in summary.json.

    Args:
        provenance: The provenance record.

    Returns:
        Summary dict suitable for embedding in summary.json.
    """
    return {
        "source_package": provenance.source_package,
        "resolution_source": provenance.resolution_source,
        "upstream_url": provenance.upstream.url,
        "upstream_ref": provenance.upstream.ref or provenance.upstream.sha,
        "tarball_method": provenance.tarball.method,
        "verification_result": provenance.verification.result,
        "watch_mismatch": provenance.watch_mismatch.detected,
        "build_type": provenance.build_type,
    }
