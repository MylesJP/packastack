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

"""Build command subpackage.

This package contains the refactored build command implementation,
split into phases and utility modules for maintainability.

Note: This module has been moved to packastack.build.
This file re-exports for backward compatibility.
"""

# Re-export from packastack.build for backward compatibility
from packastack.build.errors import (
    EXIT_ALL_BUILD_FAILED,
    EXIT_BUILD_FAILED,
    EXIT_CONFIG_ERROR,
    EXIT_CYCLE_DETECTED,
    EXIT_DISCOVERY_FAILED,
    EXIT_FETCH_FAILED,
    EXIT_GRAPH_ERROR,
    EXIT_MISSING_PACKAGES,
    EXIT_PATCH_FAILED,
    EXIT_POLICY_BLOCKED,
    EXIT_REGISTRY_ERROR,
    EXIT_RESUME_ERROR,
    EXIT_RETIRED_PROJECT,
    EXIT_SUCCESS,
    EXIT_TOOL_MISSING,
    phase_error,
    phase_warning,
)
from packastack.build.git_helpers import (
    GitCommitError,
    ensure_no_merge_paths,
    extract_upstream_version,
    get_git_author_env,
    git_commit,
    maybe_disable_gpg_sign,
    maybe_enable_sphinxdoc,
    no_gpg_sign_enabled,
)
from packastack.build.phases import (
    RegistryResolutionResult,
    RetirementCheckResult,
    check_retirement_status,
    resolve_upstream_registry,
)
from packastack.build.tarball import (
    # Backwards compatibility aliases
    _download_github_release_tarball,
    _download_pypi_tarball,
    _fetch_release_tarball,
    _run_uscan,
    download_github_release_tarball,
    download_pypi_tarball,
    fetch_release_tarball,
    run_uscan,
)
from packastack.build.types import (
    BuildInputs,
    BuildOutcome,
    PhaseResult,
    RegistryResolution,
    ResolvedTargets,
    TarballAcquisitionResult,
    WorkspacePaths,
)

__all__ = [
    "EXIT_ALL_BUILD_FAILED",
    "EXIT_BUILD_FAILED",
    "EXIT_CONFIG_ERROR",
    "EXIT_CYCLE_DETECTED",
    "EXIT_DISCOVERY_FAILED",
    "EXIT_FETCH_FAILED",
    "EXIT_GRAPH_ERROR",
    "EXIT_MISSING_PACKAGES",
    "EXIT_PATCH_FAILED",
    "EXIT_POLICY_BLOCKED",
    "EXIT_REGISTRY_ERROR",
    "EXIT_RESUME_ERROR",
    "EXIT_RETIRED_PROJECT",
    # Exit codes
    "EXIT_SUCCESS",
    "EXIT_TOOL_MISSING",
    # Types
    "BuildInputs",
    "BuildOutcome",
    "PhaseResult",
    "RegistryResolution",
    "RegistryResolutionResult",
    "ResolvedTargets",
    # Phase functions
    "RetirementCheckResult",
    "TarballAcquisitionResult",
    "WorkspacePaths",
    "check_retirement_status",
    # Tarball helpers
    "download_github_release_tarball",
    "download_pypi_tarball",
    # Git helpers
    "ensure_no_merge_paths",
    "fetch_release_tarball",
    "get_git_author_env",
    "maybe_disable_gpg_sign",
    "maybe_enable_sphinxdoc",
    "no_gpg_sign_enabled",
    # Error helpers
    "phase_error",
    "phase_warning",
    "resolve_upstream_registry",
    "run_uscan",
]
