# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Git-buildpackage (gbp) command wrapper."""

import logging
import re
import subprocess
from pathlib import Path

from packastack.exceptions import DebianError
from packastack.git.repo import RepoManager


class GitBuildPackage:
    """Manages git-buildpackage operations."""

    def __init__(self, repo_path: str | Path):
        """
        Initialize git-buildpackage manager.

        Args:
            repo_path: Path to repository root (str or Path)

        Raises:
            DebianError: If repo_path doesn't exist
        """
        self.repo_path = Path(repo_path)
        self._logger = logging.getLogger(__name__)
        if not self.repo_path.exists():
            raise DebianError(f"Repository path not found: {repo_path}")

    def import_orig(
        self,
        tarball_path: str | Path,
        merge_mode: str = "merge",
        interactive: bool = False,
    ) -> None:
        """
        Import original tarball using gbp import-orig.

        Args:
            tarball_path: Path to tarball to import (str or Path)
            merge_mode: Merge mode (default: 'merge')
            interactive: Run interactively (default: False)

        Raises:
            DebianError: If import fails
        """
        tarball = Path(tarball_path)
        if not tarball.exists():
            raise DebianError(f"Tarball not found: {tarball_path}")

        cmd = ["gbp", "import-orig"]

        if merge_mode:
            cmd.extend(["--merge-mode", merge_mode])

        if not interactive:
            cmd.append("--no-interactive")

        cmd.append(str(tarball))

        try:
            # Ensure that this is run from the master branch
            repo = RepoManager(self.repo_path)
            orig_branch = repo.get_current_branch()
            if orig_branch != "master":
                repo.checkout("master")

            self._logger.info("Running gbp import-orig with %s", tarball)
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            # Log output for debugging
            if result.stdout:
                self._logger.debug(result.stdout)

            if orig_branch != "master":
                repo.checkout(orig_branch)
        except subprocess.CalledProcessError as e:
            error_msg = f"gbp import-orig failed: {e}"
            if e.stderr:
                error_msg += f"\n{e.stderr}"
            self._logger.error("gbp import-orig failed: %s", e)
            raise DebianError(error_msg)
        except FileNotFoundError:
            self._logger.error("gbp command not found - is git-buildpackage installed?")
            raise DebianError("gbp command not found - is git-buildpackage installed?")

    def update_gbp_conf(self, upstream_branch: str) -> None:
        """
        Update debian/gbp.conf with upstream branch name.

        Args:
            upstream_branch: Name of upstream branch
        Returns:
            True if gbp.conf was modified, False otherwise

        Raises:
            DebianError: If update fails
        """
        gbp_conf_path = self.repo_path / "debian" / "gbp.conf"
        contents_changed = False

        # Read existing content or create new
        self._logger.debug("Updating gbp.conf at %s", gbp_conf_path)
        if gbp_conf_path.exists():
            try:
                content = gbp_conf_path.read_text()
            except Exception as e:
                self._logger.error("Failed to read gbp.conf: %s", e)
                raise DebianError(f"Failed to read gbp.conf: {e}")
        else:
            # Create basic gbp.conf structure
            content = "[DEFAULT]\n"
            contents_changed = True

        # Update or add upstream-branch setting
        if re.search(r"^upstream-branch\s*=", content, re.MULTILINE):
            # Replace existing setting
            new_content = re.sub(
                r"^upstream-branch\s*=.*$",
                f"upstream-branch = {upstream_branch}",
                content,
                flags=re.MULTILINE,
            )

            if new_content != content:
                contents_changed = True

            content = new_content
        else:
            # Add new setting
            if "[DEFAULT]" in content:
                content = content.replace(
                    "[DEFAULT]",
                    f"[DEFAULT]\nupstream-branch = {upstream_branch}",
                )
            else:
                content = f"[DEFAULT]\nupstream-branch = {upstream_branch}\n" + content
            contents_changed = True

        # Write updated content
        try:
            gbp_conf_path.parent.mkdir(parents=True, exist_ok=True)
            gbp_conf_path.write_text(content)
        except Exception as e:
            self._logger.error("Failed to write gbp.conf: %s", e)
            raise DebianError(f"Failed to write gbp.conf: {e}")

        return contents_changed
