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

"""Build module for PackaStack.

Provides functionality for building Debian packages using sbuild,
including artifact collection and sbuild configuration parsing.
"""

# Types for build state management
from packastack.build.types import (
    BuildInputs,
    BuildOutcome,
    PhaseResult,
    RegistryResolution,
    ResolvedTargets,
    TarballAcquisitionResult,
    WorkspacePaths,
)

# Error handling and exit codes
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

# Git helpers
from packastack.build.git_helpers import (
    ensure_no_merge_paths,
    get_git_author_env,
    maybe_disable_gpg_sign,
    maybe_enable_sphinxdoc,
    no_gpg_sign_enabled,
    # Backwards compatibility aliases
    _ensure_no_merge_paths,
    _get_git_author_env,
    _maybe_disable_gpg_sign,
    _maybe_enable_sphinxdoc,
    _no_gpg_sign_enabled,
)

# Tarball acquisition
from packastack.build.tarball import (
    download_github_release_tarball,
    download_pypi_tarball,
    fetch_release_tarball,
    run_uscan,
    # Backwards compatibility aliases
    _download_github_release_tarball,
    _download_pypi_tarball,
    _fetch_release_tarball,
    _run_uscan,
)

# Phase functions
from packastack.build.phases import (
    RetirementCheckResult,
    RegistryResolutionResult,
    check_retirement_status,
    resolve_upstream_registry,
)

__all__ = [
    # Types
    "BuildInputs",
    "BuildOutcome",
    "PhaseResult",
    "RegistryResolution",
    "ResolvedTargets",
    "TarballAcquisitionResult",
    "WorkspacePaths",
    # Error helpers
    "phase_error",
    "phase_warning",
    # Git helpers
    "ensure_no_merge_paths",
    "get_git_author_env",
    "maybe_disable_gpg_sign",
    "maybe_enable_sphinxdoc",
    "no_gpg_sign_enabled",
    # Tarball helpers
    "download_github_release_tarball",
    "download_pypi_tarball",
    "fetch_release_tarball",
    "run_uscan",
    # Phase functions
    "RetirementCheckResult",
    "RegistryResolutionResult",
    "check_retirement_status",
    "resolve_upstream_registry",
    # Exit codes
    "EXIT_SUCCESS",
    "EXIT_CONFIG_ERROR",
    "EXIT_TOOL_MISSING",
    "EXIT_FETCH_FAILED",
    "EXIT_PATCH_FAILED",
    "EXIT_MISSING_PACKAGES",
    "EXIT_CYCLE_DETECTED",
    "EXIT_BUILD_FAILED",
    "EXIT_POLICY_BLOCKED",
    "EXIT_REGISTRY_ERROR",
    "EXIT_RETIRED_PROJECT",
    "EXIT_DISCOVERY_FAILED",
    "EXIT_GRAPH_ERROR",
    "EXIT_ALL_BUILD_FAILED",
    "EXIT_RESUME_ERROR",
]
