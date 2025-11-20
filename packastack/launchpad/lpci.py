# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

import logging
import re
from pathlib import Path

from packastack.exceptions import LaunchpadError

logger = logging.getLogger(__name__)


def update_launchpad_ci_file(pkg_repo_path: Path, cycle: str) -> bool:
    """Update the Launchpad CI configuration file in the given packaging repository.

    Args:
        pkg_repo_path: Path to the packaging repository.
        cycle: The cycle to use for the CI configuration.
    Returns:
        True if the .launchpad.yaml file was updated, False otherwise
    Raises:
        LaunchpadError: If the CI configuration file is not found.
    """
    # Use module-level logger
    ci_file_path = pkg_repo_path / ".launchpad.yaml"
    if not ci_file_path.exists():
        raise LaunchpadError(
            f"Launchpad CI configuration file not found at {ci_file_path}"
        )

    with ci_file_path.open("r", encoding="utf-8") as f:
        content = f.read()
    logger.debug("Read CI file at %s", ci_file_path)

    # Update the cycle in the CI configuration file
    updated_content = re.sub(
        r'openstack_series=".*?"', f'openstack_series="{cycle}"', content, re.MULTILINE
    )

    changed = updated_content != content
    if changed:
        with ci_file_path.open("w", encoding="utf-8") as f:
            f.write(updated_content)
        logger.info("Updated CI file %s to set openstack_series=%s", ci_file_path, cycle)
    else:
        logger.debug("No change required for CI file %s (openstack_series already %s)", ci_file_path, cycle)

    # Return True if any change occurred, False otherwise
    return changed
