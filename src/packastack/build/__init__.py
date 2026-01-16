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
    log_phase_event,
    phase_error,
    phase_warning,
)

# Git helpers
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

# Local repo helpers
from packastack.build.localrepo_helpers import (
    _refresh_local_repo_indexes,
    refresh_local_repo_indexes,
)

# Phase functions
from packastack.build.phases import (
    PackageIndexes,
    PolicyCheckResult,
    RegistryResolutionResult,
    RetirementCheckResult,
    SchrootSetupResult,
    ToolCheckResult,
    check_policy,
    check_retirement_status,
    check_tools,
    ensure_schroot_ready,
    load_package_indexes,
    resolve_upstream_registry,
)

# Single build phases
from packastack.build.single_build import (
    BuildResult as SingleBuildResult,
)
from packastack.build.single_build import (
    FetchResult,
    PrepareResult,
    SetupInputs,
    SingleBuildContext,
    SingleBuildOutcome,
    ValidateDepsResult,
    build_packages,
    build_single_package,
    fetch_packaging_repo,
    import_and_patch,
    prepare_upstream_source,
    setup_build_context,
    validate_and_build_deps,
    verify_and_publish,
)
from packastack.build.single_build import (
    PhaseResult as SinglePhaseResult,
)

# Tarball acquisition
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
    "FetchResult",
    # Phase functions
    "PackageIndexes",
    "PhaseResult",
    "PolicyCheckResult",
    "PrepareResult",
    "RegistryResolution",
    "RegistryResolutionResult",
    "ResolvedTargets",
    "RetirementCheckResult",
    "SchrootSetupResult",
    # Single build phases
    "SetupInputs",
    "SingleBuildContext",
    "SingleBuildOutcome",
    "SingleBuildResult",
    "SinglePhaseResult",
    "TarballAcquisitionResult",
    "ToolCheckResult",
    "ValidateDepsResult",
    "WorkspacePaths",
    "build_packages",
    "build_single_package",
    "check_policy",
    "check_retirement_status",
    "check_tools",
    # Tarball helpers
    "download_github_release_tarball",
    "download_pypi_tarball",
    # Git helpers
    "ensure_no_merge_paths",
    "ensure_schroot_ready",
    "fetch_packaging_repo",
    "fetch_release_tarball",
    "get_git_author_env",
    "git_commit",
    "import_and_patch",
    "load_package_indexes",
    # Error helpers
    "log_phase_event",
    "maybe_disable_gpg_sign",
    "maybe_enable_sphinxdoc",
    "no_gpg_sign_enabled",
    "phase_error",
    "phase_warning",
    "prepare_upstream_source",
    # Local repo helpers
    "refresh_local_repo_indexes",
    "resolve_upstream_registry",
    "run_uscan",
    "setup_build_context",
    "validate_and_build_deps",
    "verify_and_publish",
]

