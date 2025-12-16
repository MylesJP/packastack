# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Release tarball importer."""

from pathlib import Path

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from packastack.constants import (
    CONNECT_TIMEOUT,
    MAX_RETRY_ATTEMPTS,
    READ_TIMEOUT,
    RETRY_MAX_WAIT_SECONDS,
    RETRY_MIN_WAIT_SECONDS,
    RETRY_MULTIPLIER,
    TARBALLS_BASE_URL,
)
from packastack.exceptions import ImporterError, NetworkError
from packastack.importer.base import BaseImporter
from packastack.importer.openstack import get_deliverable_info, get_signing_key
from packastack.package.version import VersionConverter


class ReleaseImporter(BaseImporter):
    """Importer for official release tarballs."""

    def get_version(self) -> str:
        """
        Get latest release version from deliverable file.

        Returns:
            Latest release version

        Raises:
            ImporterError: If deliverable not found or no version
        """
        # Extract project name from upstream repo path
        project_name = self.upstream_repo_path.name

        deliverable = get_deliverable_info(
            self.releases_repo_path, self.cycle, project_name
        )

        if not deliverable:
            raise ImporterError(
                f"No deliverable found for {project_name} in {self.cycle}"
            )

        version = deliverable.get("latest_version")
        if not version:
            raise ImporterError(f"No release version found for {project_name}")

        return version

    @retry(
        stop=stop_after_attempt(MAX_RETRY_ATTEMPTS),
        wait=wait_exponential(
            multiplier=RETRY_MULTIPLIER,
            min=RETRY_MIN_WAIT_SECONDS,
            max=RETRY_MAX_WAIT_SECONDS,
        ),
        reraise=True,
    )
    def download_file(self, url: str, dest_path: Path) -> None:
        """
        Download file with retry logic.

        Args:
            url: URL to download
            dest_path: Destination path

        Raises:
            NetworkError: If download fails
        """
        try:
            response = requests.get(
                url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), stream=True
            )
            response.raise_for_status()

            with open(dest_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

        except requests.RequestException as e:
            raise NetworkError(f"Failed to download {url}: {e}")

    def get_tarball(self, version: str) -> Path:
        """
        Download release tarball and signature.

        Args:
            version: Version to download

        Returns:
            Path to downloaded tarball

        Raises:
            ImporterError: If download or verification fails
        """
        # Get deliverable info for tarball naming
        project_name = self.upstream_repo_path.name
        deliverable = get_deliverable_info(
            self.releases_repo_path, self.cycle, project_name
        )

        if not deliverable:
            raise ImporterError(f"No deliverable found for {project_name}")

        namespace = deliverable["namespace"]
        tarball_base = deliverable["tarball_base"]

        # Build tarball URL
        tarball_name = f"{tarball_base}-{version}.tar.gz"
        tarball_url = f"{TARBALLS_BASE_URL}/{namespace}/{tarball_base}/{tarball_name}"
        signature_url = f"{tarball_url}.asc"

        # Download tarball
        tarball_path = self.tarballs_dir / tarball_name
        if not tarball_path.exists():
            self.download_file(tarball_url, tarball_path)

        # Download signature
        signature_path = self.tarballs_dir / f"{tarball_name}.asc"
        if not signature_path.exists():
            self.download_file(signature_url, signature_path)

        # Get and save signing key
        _, key_content = get_signing_key(self.releases_repo_path)
        self.save_gpg_key(key_content)

        return tarball_path

    def convert_version(self, upstream_version: str) -> str:
        """
        Convert release version to Debian format.

        Args:
            upstream_version: Upstream version

        Returns:
            Debian version string
        """
        return VersionConverter.convert_release_version(upstream_version)
