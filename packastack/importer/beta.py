# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Beta tarball importer."""

from packastack.importer.release import ReleaseImporter
from packastack.package.version import VersionConverter


class BetaImporter(ReleaseImporter):
    """Importer for beta tarballs.

    Uses same download logic as ReleaseImporter but with different
    version conversion.
    """

    def convert_version(self, upstream_version: str) -> str:
        """
        Convert beta version to Debian format.

        Args:
            upstream_version: Upstream version (e.g., 12.0.0.0b0)

        Returns:
            Debian version string (e.g., 12.0.0~b0)
        """
        return VersionConverter.convert_beta_version(upstream_version)
