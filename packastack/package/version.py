# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Debian version string conversion utilities."""

import re

from packastack.exceptions import DebianError


class VersionConverter:
    """Converts upstream versions to Debian package versions."""

    @staticmethod
    def convert_beta_version(upstream_version: str) -> str:
        """
        Convert beta version to Debian format.

        Examples:
            12.0.0.0b0 -> 12.0.0~b0
            1.2.3.0b1 -> 1.2.3~b1

        Args:
            upstream_version: Upstream beta version

        Returns:
            Debian version string

        Raises:
            DebianError: If version format is invalid
        """
        # Match version like X.Y.Z.0bN (remove .0 before b) or X.Y.ZbN (keep as-is)
        # We need to match the full version before checking for .0b
        # Key insight: X.Y.Z.0bN means there's a 4th component that is 0
        # vs X.Y.ZbN which has only 3 components

        parts_match = re.match(r"^(.+)b(\d+)$", upstream_version)
        if parts_match:
            version_part = parts_match.group(1)
            beta_number = parts_match.group(2)

            # Split by dots to count components
            components = version_part.split(".")

            # If 4+ components and last is '0', remove it (e.g., "12.0.0.0" -> "12.0.0")
            # Otherwise keep as-is (e.g., "12.0.0" stays "12.0.0")
            if len(components) >= 4 and components[-1] == "0":
                # Remove the trailing .0
                base_version = ".".join(components[:-1])
                return f"{base_version}~b{beta_number}"
            else:
                # No .0 to remove or not a 4th component
                return f"{version_part}~b{beta_number}"

        raise DebianError(f"Invalid beta version format: {upstream_version}")

    @staticmethod
    def convert_candidate_version(upstream_version: str) -> str:
        """
        Convert release candidate version to Debian format.

        Examples:
            12.0.0.0rc0 -> 12.0.0~rc0
            1.2.3.0rc1 -> 1.2.3~rc1

        Args:
            upstream_version: Upstream RC version

        Returns:
            Debian version string

        Raises:
            DebianError: If version format is invalid
        """
        # Match version like X.Y.Z.0rcN (remove .0 before rc) or X.Y.ZrcN (keep as-is)
        # We need to match the full version before checking for .0rc
        # Key insight: X.Y.Z.0rcN means there's a 4th component that is 0
        # vs X.Y.ZrcN which has only 3 components

        parts_match = re.match(r"^(.+)rc(\d+)$", upstream_version)
        if parts_match:
            version_part = parts_match.group(1)
            rc_number = parts_match.group(2)

            # Split by dots to count components
            components = version_part.split(".")

            # If 4+ components and last is '0', remove it (e.g., "12.0.0.0" -> "12.0.0")
            # Otherwise keep as-is (e.g., "12.0.0" stays "12.0.0")
            if len(components) >= 4 and components[-1] == "0":
                # Remove the trailing .0
                base_version = ".".join(components[:-1])
                return f"{base_version}~rc{rc_number}"
            else:
                # No .0 to remove or not a 4th component
                return f"{version_part}~rc{rc_number}"

        raise DebianError(f"Invalid candidate version format: {upstream_version}")

    @staticmethod
    def convert_release_version(upstream_version: str) -> str:
        """
        Convert release version to Debian format.

        For release versions, removes trailing .0 only if it's a 4th component

        Examples:
            12.0.0 -> 12.0.0
            1.2.3.0 -> 1.2.3

        Args:
            upstream_version: Upstream release version

        Returns:
            Debian version string
        """
        # Only remove .0 if it's the 4th component (X.Y.Z.0 -> X.Y.Z)
        # but keep it if it's 2nd or 3rd (12.0, 12.0.0)
        parts = upstream_version.split(".")
        if len(parts) == 4 and parts[3] == "0":
            return ".".join(parts[:3])
        return upstream_version

    @staticmethod
    def convert_snapshot_version(
        git_describe: str, existing_version: str | None = None
    ) -> str:
        """
        Convert git-describe output to Debian snapshot version.

        The git-describe format is: <last_tag>-<commits_since>-g<committish>
        Debian format: <last_tag>+<commits_since>-g<committish><counter>-1ubuntu0

        Always append a counter starting at 1. If the version has already
        been imported, increment the counter after the committish.

        Examples:
            12.0.0-5-gabcdef -> 12.0.0+5-gabcdef.1-1ubuntu0
            12.0.0-5-gabcdef (with existing 12.0.0+5-gabcdef.1-1ubuntu0) ->
                12.0.0+5-gabcdef.2-1ubuntu0

        Args:
            git_describe: Output of git describe --long --tags
            existing_version: Existing version if already imported

        Returns:
            Debian snapshot version string

        Raises:
            DebianError: If git-describe format is invalid
        """
        # Parse git-describe output
        # Format: <tag>-<commits>-g<hash>
        match = re.match(r"^(.+?)-(\d+)-g([0-9a-f]+)$", git_describe)
        if not match:
            raise DebianError(f"Invalid git-describe format: {git_describe}")

        tag = match.group(1)
        commits = match.group(2)
        committish = match.group(3)

        # Clean up tag (remove 'v' prefix if present)
        if tag.startswith("v"):
            tag = tag[1:]

        # Convert any remaining beta/rc markers in tag
        if "b" in tag:
            try:
                tag = VersionConverter.convert_beta_version(tag)
            except DebianError:
                pass  # Not a standard beta format, leave as-is
        elif "rc" in tag:
            try:
                tag = VersionConverter.convert_candidate_version(tag)
            except DebianError:
                pass  # Not a standard rc format, leave as-is

        # Always append a counter; start at 1 by default. If an existing
        # version is provided and matches the same tag+commits+g<committish>
        # pattern, increment the existing counter if present.
        counter = "1"
        if existing_version:
            # Extract the pattern from existing version
            existing_pattern = f"{tag}+{commits}-g{committish}"
            if existing_pattern in existing_version:
                # Find counter in existing version, the counter is directly
                # appended after the committish (e.g., ...g<committish>2-)
                pattern_for_regex = re.escape(existing_pattern)
                # Match counter either with a dot (new format) or without (old
                # format) after the committish. We normalize to dot when
                # generating the new version.
                counter_match = re.search(
                    rf"{pattern_for_regex}\.?(\d+)-",
                    existing_version,
                )
                if counter_match:
                    existing_counter = counter_match.group(1)
                    counter = str(int(existing_counter) + 1)
                else:
                    # Pattern in existing_version but no explicit counter; use 1
                    counter = "1"
            else:
                # Pattern not present - still start counter at 1 (new behavior)
                counter = "1"
        # Build debian version
        # Always format the counter with a dot before it so that the counter
        # isn't interpreted as part of the committish.
        debian_version = f"{tag}+{commits}-g{committish}.{counter}-1ubuntu0"

        return debian_version

    @staticmethod
    def detect_version_type(version: str) -> str:
        """
        Detect version type from version string.

        Args:
            version: Version string

        Returns:
            'beta', 'candidate', 'release', or 'unknown'
        """
        # Match beta: either .0b or just b
        if re.search(r"(\.0b|b)\d+", version):
            return "beta"
        # Match rc: either .0rc or just rc
        elif re.search(r"(\.0rc|rc)\d+", version):
            return "candidate"
        elif re.match(r"^\d+(?:\.\d+)*$", version):
            return "release"
        else:
            return "unknown"
