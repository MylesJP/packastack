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

"""Sbuild configuration file parser.

Parses $build_dir and $log_dir from Perl-style sbuild configuration files:
- ~/.sbuildrc (user config)
- /etc/sbuild/sbuild.conf (global config)
- /etc/sbuild/sbuild.conf.d/*.conf (global config fragments)

Only simple variable assignments are parsed; complex Perl expressions are
skipped with a warning.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# Regex patterns for Perl variable assignments
# Matches: $build_dir = '/path'; or $build_dir = "/path"; or $build_dir='/path';
PERL_VAR_PATTERN = re.compile(
    r"""
    ^\s*                           # Leading whitespace
    \$(\w+)                        # Variable name (captured)
    \s*=\s*                        # Assignment operator
    ['"]([^'"]+)['"]               # Quoted path value (captured)
    \s*;?                          # Optional semicolon
    \s*(?:\#.*)?$                  # Optional comment
    """,
    re.VERBOSE,
)


@dataclass
class SbuildPaths:
    """Parsed sbuild path configuration.

    Attributes:
        build_dir: Directory where sbuild places build output.
        log_dir: Directory where sbuild places log files.
        source: Description of where these paths were found.
    """

    build_dir: Path | None = None
    log_dir: Path | None = None
    source: str = ""


@dataclass
class CandidateDirectories:
    """Prioritized list of candidate directories to search for artifacts/logs.

    Directories are ordered by priority (highest first).
    """

    build_dirs: list[Path] = field(default_factory=list)
    log_dirs: list[Path] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def add_build_dir(self, path: Path, source: str) -> None:
        """Add a build directory candidate if it doesn't already exist."""
        resolved = path.resolve()
        if resolved not in self.build_dirs:
            self.build_dirs.append(resolved)
            if source not in self.sources:
                self.sources.append(source)

    def add_log_dir(self, path: Path, source: str) -> None:
        """Add a log directory candidate if it doesn't already exist."""
        resolved = path.resolve()
        if resolved not in self.log_dirs:
            self.log_dirs.append(resolved)
            if source not in self.sources:
                self.sources.append(source)


def parse_sbuildrc_content(content: str, source_name: str = "") -> SbuildPaths:
    """Parse sbuild configuration content for build_dir and log_dir.

    Args:
        content: Content of an sbuildrc file.
        source_name: Name of the source file for logging.

    Returns:
        SbuildPaths with any discovered paths.
    """
    result = SbuildPaths(source=source_name)

    for line in content.splitlines():
        match = PERL_VAR_PATTERN.match(line)
        if match:
            var_name = match.group(1)
            var_value = match.group(2)

            # Expand environment variables and ~ in path
            expanded = os.path.expandvars(os.path.expanduser(var_value))

            if var_name == "build_dir":
                result.build_dir = Path(expanded)
                logger.debug("Parsed build_dir=%s from %s", result.build_dir, source_name)
            elif var_name == "log_dir":
                result.log_dir = Path(expanded)
                logger.debug("Parsed log_dir=%s from %s", result.log_dir, source_name)

    return result


def parse_sbuildrc_file(path: Path) -> SbuildPaths:
    """Parse an sbuild configuration file.

    Args:
        path: Path to the sbuildrc file.

    Returns:
        SbuildPaths with any discovered paths.
    """
    if not path.exists():
        logger.debug("sbuildrc file does not exist: %s", path)
        return SbuildPaths()

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        return parse_sbuildrc_content(content, str(path))
    except OSError as e:
        logger.warning("Failed to read sbuildrc file %s: %s", path, e)
        return SbuildPaths()


def get_user_sbuildrc_path() -> Path:
    """Get the path to the user's sbuildrc file."""
    return Path.home() / ".sbuildrc"


def get_global_sbuild_config_paths() -> list[Path]:
    """Get paths to global sbuild configuration files.

    Returns:
        List of paths to check, in order of precedence.
    """
    paths = []

    # Main global config
    main_conf = Path("/etc/sbuild/sbuild.conf")
    if main_conf.exists():
        paths.append(main_conf)

    # Config fragments directory
    conf_d = Path("/etc/sbuild/sbuild.conf.d")
    if conf_d.is_dir():
        # Sort for deterministic order
        for conf_file in sorted(conf_d.glob("*.conf")):
            paths.append(conf_file)

    return paths


def get_default_candidate_dirs() -> list[tuple[Path, str]]:
    """Get default/fallback candidate directories for sbuild output.

    Returns:
        List of (path, source_description) tuples.
    """
    home = Path.home()
    return [
        (Path("/var/lib/sbuild/build"), "sbuild default"),
        (Path("/var/lib/sbuild"), "sbuild default"),
        (Path("/var/log/sbuild"), "sbuild log default"),
        (home / "schroot" / "build", "common user location"),
        (home / "schroot" / "logs", "common user location"),
        (home / "sbuild" / "build", "common user location"),
        (Path("/tmp"), "temporary directory fallback"),
    ]


def discover_candidate_directories(
    packastack_output_dir: Path | None = None,
    packastack_run_log_dir: Path | None = None,
) -> CandidateDirectories:
    """Discover all candidate directories for sbuild artifacts and logs.

    This function builds a prioritized list of directories to search for
    sbuild output, based on:
    1. PackaStack's intended output directories (highest priority)
    2. User sbuildrc configuration (~/.sbuildrc)
    3. Global sbuild configuration (/etc/sbuild/*)
    4. Common fallback locations (lowest priority)

    Args:
        packastack_output_dir: PackaStack's intended artifact output directory.
        packastack_run_log_dir: PackaStack's run log directory.

    Returns:
        CandidateDirectories with prioritized build and log directories.
    """
    candidates = CandidateDirectories()

    # Priority 1: PackaStack's intended directories
    if packastack_output_dir:
        candidates.add_build_dir(packastack_output_dir, "packastack output_dir")
    if packastack_run_log_dir:
        candidates.add_log_dir(packastack_run_log_dir, "packastack run logs")

    # Priority 2: User sbuildrc
    user_rc = get_user_sbuildrc_path()
    user_paths = parse_sbuildrc_file(user_rc)
    if user_paths.build_dir:
        candidates.add_build_dir(user_paths.build_dir, "~/.sbuildrc")
    if user_paths.log_dir:
        candidates.add_log_dir(user_paths.log_dir, "~/.sbuildrc")

    # Priority 3: Global sbuild configuration
    for global_conf in get_global_sbuild_config_paths():
        global_paths = parse_sbuildrc_file(global_conf)
        if global_paths.build_dir:
            candidates.add_build_dir(global_paths.build_dir, str(global_conf))
        if global_paths.log_dir:
            candidates.add_log_dir(global_paths.log_dir, str(global_conf))

    # Priority 4: Common defaults and fallbacks
    for fallback_path, source in get_default_candidate_dirs():
        # Categorize by path name
        path_str = str(fallback_path).lower()
        if "log" in path_str:
            candidates.add_log_dir(fallback_path, source)
        else:
            candidates.add_build_dir(fallback_path, source)

    return candidates


def parse_sbuild_output_for_paths(output: str) -> SbuildPaths:
    """Parse sbuild stdout/stderr for path hints.

    Sbuild may emit lines indicating where it's placing artifacts or logs.
    This function extracts those hints.

    Args:
        output: Combined stdout/stderr from sbuild.

    Returns:
        SbuildPaths with any discovered paths.
    """
    result = SbuildPaths(source="sbuild output")

    # Pattern for log file path mentions
    # Example: "sbuild log file: /path/to/file.log"
    log_patterns = [
        re.compile(r"log\s+file[:\s]+([/\w.-]+\.(?:log|build))", re.IGNORECASE),
        re.compile(r"build\s+log[:\s]+([/\w.-]+)", re.IGNORECASE),
        re.compile(r"Writing build log to ([/\w.-]+)", re.IGNORECASE),
    ]

    # Pattern for build directory mentions
    # Example: "Build directory: /path/to/build"
    build_patterns = [
        re.compile(r"build\s+dir(?:ectory)?[:\s]+([/\w.-]+)", re.IGNORECASE),
        re.compile(r"output\s+dir(?:ectory)?[:\s]+([/\w.-]+)", re.IGNORECASE),
    ]

    for line in output.splitlines():
        for pattern in log_patterns:
            match = pattern.search(line)
            if match:
                path_str = match.group(1)
                # Only process absolute paths
                if path_str.startswith("/"):
                    path = Path(path_str)
                    parent = path.parent
                    if parent != Path() and parent.exists():
                        result.log_dir = parent
                        logger.debug("Found log dir hint from sbuild output: %s", result.log_dir)
                        break

        for pattern in build_patterns:
            match = pattern.search(line)
            if match:
                path_str = match.group(1)
                # Only process absolute paths
                if path_str.startswith("/"):
                    path = Path(path_str)
                    if path.exists() or (path.parent != Path() and path.parent.exists()):
                        result.build_dir = path if path.is_dir() else path.parent
                        logger.debug("Found build dir hint from sbuild output: %s", result.build_dir)
                        break

    return result
