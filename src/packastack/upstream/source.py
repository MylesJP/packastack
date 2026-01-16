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

"""Upstream source handling for Packastack build operations.

Handles selection and fetching of upstream sources (release tarballs,
snapshots from git, milestones), signature verification, and signature
policy management.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import git

from packastack.planning.type_selection import BuildType

if TYPE_CHECKING:
    pass

# OpenStack release tarball base URL
OPENSTACK_TARBALLS_URL = "https://tarballs.opendev.org"


@dataclass
class UpstreamSource:
    """Information about an upstream source."""

    version: str
    git_ref: str = ""
    tarball_url: str = ""
    signature_url: str = ""
    build_type: BuildType = BuildType.RELEASE
    milestone: str = ""  # e.g., "b1", "rc1"

    @property
    def is_release(self) -> bool:
        return self.build_type == BuildType.RELEASE

    @property
    def is_snapshot(self) -> bool:
        return self.build_type == BuildType.SNAPSHOT

    @property
    def is_milestone(self) -> bool:
        return self.build_type == BuildType.MILESTONE


@dataclass
class TarballResult:
    """Result of downloading and verifying a tarball."""

    success: bool
    path: Path | None = None
    signature_verified: bool = False
    signature_warning: str = ""
    error: str = ""


@dataclass
class SnapshotAcquisitionResult:
    """Result of acquiring an upstream snapshot.

    Contains all information needed for building from a git snapshot,
    including the cloned repository, version metadata, and provenance.
    """

    success: bool
    repo_path: Path | None = None
    tarball_result: TarballResult | None = None
    git_sha: str = ""
    git_sha_short: str = ""
    git_date: str = ""  # YYYYMMDD format
    upstream_version: str = ""  # e.g., "29.0.0~git20241227.abc1234"
    project: str = ""
    git_ref: str = "HEAD"
    cloned: bool = False
    error: str = ""


@dataclass(frozen=True)
class SnapshotRequest:
    """Immutable request for acquiring an upstream snapshot.

    Bundles all parameters needed to identify what snapshot to acquire.
    Path parameters (work_dir, output_dir) are kept separate as they are
    I/O concerns rather than snapshot identity.

    Attributes:
        project: OpenStack project name (e.g., "nova", "keystone").
        base_version: Fallback version if git describe fails.
        branch: Git branch to checkout (e.g., "stable/2024.2"), or None for default.
        git_ref: Git ref for the snapshot (default: HEAD).
        package_name: Package name for tarball (defaults to project name if empty).
    """

    project: str
    base_version: str
    branch: str | None = None
    git_ref: str = "HEAD"
    package_name: str = ""


# OpenDev base URL for upstream OpenStack projects
OPENDEV_BASE_URL = "https://opendev.org/openstack"


def build_tarball_url(project: str, version: str) -> str:
    """Build URL for an official OpenStack release tarball.

    Args:
        project: Project name (e.g., "nova", "oslo.config").
        version: Release version (e.g., "29.0.0").

    Returns:
        Full URL to the tarball.
    """
    # OpenStack tarballs follow pattern: project/project-version.tar.gz
    # For oslo.* projects:
    # - Directory path uses dots: oslo.config/
    # - Tarball filename uses underscores: oslo_config-version.tar.gz
    # For python-* client libraries:
    # - Directory path uses hyphens: python-openstackclient/
    # - Tarball filename uses underscores: python_openstackclient-version.tar.gz

    tarball_name = project
    # Replace dots with underscores (e.g. oslo.config -> oslo_config)
    if "." in project:
         tarball_name = tarball_name.replace(".", "_")

    # Replace hyphens with underscores for python- prefixed projects
    if project.startswith("python-"):
         tarball_name = tarball_name.replace("-", "_")

    path = f"openstack/{project}/{tarball_name}-{version}.tar.gz"

    return urljoin(OPENSTACK_TARBALLS_URL + "/", path)


def build_signature_url(tarball_url: str) -> str:
    """Build URL for a detached signature file.

    Args:
        tarball_url: URL of the tarball.

    Returns:
        URL for the .asc signature file.
    """
    return tarball_url + ".asc"


def select_upstream_source(
    releases_repo: Path,
    series: str,
    project: str,
    build_type: BuildType,
    milestone: str = "",
    git_ref: str = "",
) -> UpstreamSource | None:
    """Select the appropriate upstream source for a build.

    Args:
        releases_repo: Path to openstack/releases repository.
        series: OpenStack series (e.g., "2024.2").
        project: Project name.
        build_type: Type of build (release, snapshot, milestone).
        milestone: Milestone identifier for milestone builds (e.g., "b1", "rc1").
        git_ref: Git ref for snapshot builds (default: HEAD of stable branch).

    Returns:
        UpstreamSource with source information, or None if not found.
    """
    if build_type == BuildType.SNAPSHOT:
        # For snapshots, we don't strictly need the project to be in openstack/releases
        # (unless we need metadata from it, but currently we rely on git)
        return UpstreamSource(
            version="",  # Will be computed later
            git_ref=git_ref or "HEAD",
            build_type=BuildType.SNAPSHOT,
        )

    # Import here to avoid circular imports
    from packastack.upstream.releases import load_project_releases

    proj = load_project_releases(releases_repo, series, project)
    if proj is None:
        return None

    if build_type == BuildType.RELEASE:
        # Use latest release
        latest = proj.get_latest_release()
        if latest is None:
            return None

        tarball_url = build_tarball_url(proj.name, latest.version)
        signature_url = build_signature_url(tarball_url)

        return UpstreamSource(
            version=latest.version,
            tarball_url=tarball_url,
            signature_url=signature_url,
            build_type=BuildType.RELEASE,
        )

    elif build_type == BuildType.MILESTONE:
        # Find the milestone release
        for rel in reversed(proj.releases):
            if milestone.lower() in rel.version.lower():
                tarball_url = build_tarball_url(proj.name, rel.version)
                signature_url = build_signature_url(tarball_url)

                return UpstreamSource(
                    version=rel.version,
                    tarball_url=tarball_url,
                    signature_url=signature_url,
                    build_type=BuildType.MILESTONE,
                    milestone=milestone,
                )
        return None

    return None


def download_file(url: str, dest: Path, timeout: int = 300) -> tuple[bool, str]:
    """Download a file from a URL.

    Args:
        url: URL to download.
        dest: Destination path.
        timeout: Download timeout in seconds.

    Returns:
        Tuple of (success, error_message).
    """
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Download with timeout
        with urllib.request.urlopen(url, timeout=timeout) as response:
            with dest.open("wb") as f:
                shutil.copyfileobj(response, f)

        return True, ""
    except Exception as e:
        return False, str(e)


def verify_signature(
    tarball_path: Path,
    signature_path: Path,
    keyring_path: Path | None = None,
) -> tuple[bool, str]:
    """Verify a detached GPG signature.

    Args:
        tarball_path: Path to the tarball.
        signature_path: Path to the .asc signature file.
        keyring_path: Optional path to a keyring file.

    Returns:
        Tuple of (verified, message).
    """
    cmd = ["gpg", "--verify"]

    if keyring_path and keyring_path.exists():
        cmd.extend(["--keyring", str(keyring_path)])

    cmd.extend([str(signature_path), str(tarball_path)])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return True, "Signature verified"
        else:
            return False, result.stderr
    except subprocess.TimeoutExpired:
        return False, "Signature verification timed out"
    except FileNotFoundError:
        return False, "gpg not found"
    except Exception as e:
        return False, str(e)


def download_and_verify_tarball(
    source: UpstreamSource,
    dest_dir: Path,
    keyring_path: Path | None = None,
) -> TarballResult:
    """Download a tarball and optionally verify its signature.

    Args:
        source: UpstreamSource with URLs.
        dest_dir: Directory to download to.
        keyring_path: Optional path to signing key for verification.

    Returns:
        TarballResult with download status and verification info.
    """
    if not source.tarball_url:
        return TarballResult(success=False, error="No tarball URL provided")

    # Determine filename from URL
    filename = source.tarball_url.split("/")[-1]
    tarball_path = dest_dir / filename
    signature_path = dest_dir / (filename + ".asc")

    # Download tarball
    success, error = download_file(source.tarball_url, tarball_path)
    if not success:
        return TarballResult(success=False, error=f"Failed to download tarball: {error}")

    # Try to download signature
    signature_verified = False
    signature_warning = ""

    if source.signature_url:
        sig_success, sig_error = download_file(source.signature_url, signature_path)
        if sig_success:
            # Verify signature
            verified, msg = verify_signature(tarball_path, signature_path, keyring_path)
            if verified:
                signature_verified = True
            else:
                signature_warning = f"Signature verification failed: {msg}"
        else:
            signature_warning = f"Signature not available: {sig_error}"
    else:
        signature_warning = "No signature URL provided"

    return TarballResult(
        success=True,
        path=tarball_path,
        signature_verified=signature_verified,
        signature_warning=signature_warning,
    )


def generate_snapshot_tarball(
    repo_path: Path,
    ref: str,
    package: str,
    version: str,
    output_dir: Path,
) -> TarballResult:
    """Generate an orig tarball from a git repository snapshot.

    Args:
        repo_path: Path to the upstream git repository.
        ref: Git ref to snapshot (commit, tag, branch).
        package: Package name for the tarball.
        version: Version string for the tarball.
        output_dir: Directory to write the tarball.

    Returns:
        TarballResult with the generated tarball path.
    """
    # Clean version for filename (remove epoch if present)
    clean_version = version.split(":")[-1] if ":" in version else version

    # Tarball name follows Debian convention: package_version.orig.tar.gz
    tarball_name = f"{package}_{clean_version}.orig.tar.gz"
    tarball_path = output_dir / tarball_name

    try:
        output_dir.mkdir(parents=True, exist_ok=True)

        # Use git archive to create tarball
        # The prefix should be package-version/
        prefix = f"{package}-{clean_version}/"

        cmd = [
            "git",
            "archive",
            "--format=tar.gz",
            f"--prefix={prefix}",
            "--output",
            str(tarball_path),
            ref,
        ]

        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            return TarballResult(
                success=False,
                error=f"git archive failed: {result.stderr}",
            )

        return TarballResult(
            success=True,
            path=tarball_path,
            signature_verified=False,
            signature_warning="Snapshot build - no signature verification",
        )

    except subprocess.TimeoutExpired:
        return TarballResult(success=False, error="git archive timed out")
    except Exception as e:
        return TarballResult(success=False, error=str(e))


def get_git_snapshot_info(repo_path: Path, ref: str = "HEAD") -> tuple[str, str, str]:
    """Get snapshot information from a git repository.

    Args:
        repo_path: Path to the git repository.
        ref: Git ref to query.

    Returns:
        Tuple of (short_sha, full_sha, date_string) where date_string is YYYYMMDD.
    """
    try:
        # Get commit hash
        result = subprocess.run(
            ["git", "rev-parse", ref],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        full_sha = result.stdout.strip()
        short_sha = full_sha[:7]

        # Get commit date
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ci", ref],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        date_line = result.stdout.strip()
        # Format: 2024-12-27 10:30:00 +0000
        date_str = date_line.split()[0].replace("-", "")  # YYYYMMDD

        return short_sha, full_sha, date_str

    except Exception:
        return "", "", ""


@dataclass
class GitDescribeResult:
    """Result of git describe for version calculation."""

    base_version: str  # Most recent tag (e.g., "30.0.0")
    commit_count: int  # Commits since tag (e.g., 123)
    short_sha: str  # Short commit SHA (e.g., "abc1234")
    is_exact_tag: bool  # True if HEAD is exactly at a tag


def get_version_from_git_describe(
    repo_path: Path,
    ref: str = "HEAD",
) -> GitDescribeResult | None:
    """Get version information using git describe.

    Uses git describe --tags to find the most recent tag and calculate
    the number of commits since that tag. This is more accurate than
    incrementing version numbers, as it reflects the actual state.

    Output format from git describe:
    - If at tag: "30.0.0"
    - If commits after tag: "30.0.0-123-gabc1234"
    - If no tags: returns None

    Args:
        repo_path: Path to the git repository.
        ref: Git ref to describe (default: HEAD).

    Returns:
        GitDescribeResult with version info, or None if no tags found.
    """
    try:
        # First, try git describe with tags
        result = subprocess.run(
            ["git", "describe", "--tags", "--long", ref],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            # No tags found - fallback to counting all commits
            count_result = subprocess.run(
                ["git", "rev-list", "--count", ref],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30,
            )

            sha_result = subprocess.run(
                ["git", "rev-parse", "--short", ref],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if count_result.returncode == 0 and sha_result.returncode == 0:
                commit_count = int(count_result.stdout.strip())
                short_sha = sha_result.stdout.strip()
                return GitDescribeResult(
                    base_version="0.0.0",
                    commit_count=commit_count,
                    short_sha=short_sha,
                    is_exact_tag=False,
                )
            return None

        describe_output = result.stdout.strip()

        # Parse git describe --long output: "tag-count-ghash"
        # Examples:
        # - "30.0.0-0-gabc1234" (exactly at tag)
        # - "30.0.0-123-gabc1234" (123 commits after tag)
        parts = describe_output.rsplit("-", 2)

        if len(parts) != 3:
            # Unexpected format
            return None

        tag = parts[0]
        commit_count = int(parts[1])
        sha_with_g = parts[2]  # "gabc1234"
        short_sha = sha_with_g[1:] if sha_with_g.startswith("g") else sha_with_g

        return GitDescribeResult(
            base_version=tag,
            commit_count=commit_count,
            short_sha=short_sha,
            is_exact_tag=(commit_count == 0),
        )

    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def apply_signature_policy(debian_dir: Path, build_type: BuildType) -> list[Path]:
    """Apply signature policy based on build type.

    For snapshot builds, remove signing key files as signature verification
    is not applicable.

    Args:
        debian_dir: Path to the debian/ directory.
        build_type: Type of build being performed.

    Returns:
        List of files that were removed.
    """
    removed: list[Path] = []

    if build_type != BuildType.SNAPSHOT:
        # Keep signing keys for release and milestone builds
        return removed

    # Remove signing key files for snapshots
    upstream_dir = debian_dir / "upstream"
    if not upstream_dir.exists():
        return removed

    patterns = ["*.asc", "*.sig", "*.gpg", "signing-key.asc"]

    for pattern in patterns:
        for key_file in upstream_dir.glob(pattern):
            try:
                key_file.unlink()
                removed.append(key_file)
            except OSError:
                pass

    return removed


def compute_tarball_hash(tarball_path: Path, algorithm: str = "sha256") -> str:
    """Compute hash of a tarball.

    Args:
        tarball_path: Path to the tarball.
        algorithm: Hash algorithm (sha256, sha512, md5).

    Returns:
        Hex digest of the hash.
    """
    h = hashlib.new(algorithm)
    with tarball_path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def build_opendev_url(project: str) -> str:
    """Build the git URL for an upstream OpenStack project on OpenDev.

    Args:
        project: Project name (e.g., "nova", "keystone").

    Returns:
        Full git clone URL.
    """
    return f"{OPENDEV_BASE_URL}/{project}.git"


def clone_upstream_repo(
    project: str,
    dest_dir: Path,
    branch: str | None = None,
    shallow: bool = True,
) -> tuple[Path | None, bool, str]:
    """Clone an upstream OpenStack repository from OpenDev.

    Args:
        project: Project name (e.g., "nova").
        dest_dir: Directory to clone into (repo will be at dest_dir/project).
        branch: Optional branch to checkout (e.g., "stable/2024.2").
        shallow: If True, do a shallow clone (--depth 1).

    Returns:
        Tuple of (repo_path, cloned, error_message).
    """
    url = build_opendev_url(project)
    repo_path = dest_dir / project

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)

        if repo_path.exists() and (repo_path / ".git").is_dir():
            # Already cloned, just fetch updates
            try:
                repo = git.Repo(repo_path)
                origin = repo.remotes.origin
                origin.fetch(prune=True)
                if branch:
                    # Try to checkout the branch
                    try:
                        repo.git.checkout(branch)
                    except git.GitCommandError:
                        # Branch might be remote-only
                        repo.git.checkout(f"origin/{branch}", b=branch)
                return repo_path, False, ""
            except git.GitCommandError as e:
                return None, False, f"Fetch failed: {e}"
        else:
            # Clone new repository
            clone_kwargs = {"to_path": repo_path}
            if shallow:
                clone_kwargs["depth"] = 1
            if branch:
                clone_kwargs["branch"] = branch

            git.Repo.clone_from(url, **clone_kwargs)
            return repo_path, True, ""

    except git.GitCommandError as e:
        return None, False, f"Clone failed: {e}"
    except Exception as e:
        return None, False, str(e)


def acquire_upstream_snapshot(
    request: SnapshotRequest,
    work_dir: Path,
    output_dir: Path,
) -> SnapshotAcquisitionResult:
    """Acquire an upstream snapshot for building.

    This function performs the complete workflow for snapshot builds:
    1. Clone the upstream repository from OpenDev
    2. Get git metadata using git describe for accurate version
    3. Generate an orig tarball from the snapshot

    The resulting version string uses git describe for accuracy:
        {tag}+git{YYYYMMDD}.{commits}.{short_sha}

    For example, if the most recent tag is "30.0.0" with 123 commits since,
    and the snapshot is from 2024-12-27 with SHA abc1234:
        30.0.0+git20241227.123.abc1234

    This avoids the problem of guessing the next version number.
    If there are no tags, falls back to:
        0.0.0+git{YYYYMMDD}.{total_commits}.{short_sha}

    Args:
        request: SnapshotRequest with project, version, and git parameters.
        work_dir: Working directory for cloning the repo.
        output_dir: Directory to write the orig tarball.

    Returns:
        SnapshotAcquisitionResult with all snapshot metadata and tarball.
    """
    pkg_name = request.package_name or request.project

    # Step 1: Clone the upstream repository
    repo_path, cloned, error = clone_upstream_repo(
        project=request.project,
        dest_dir=work_dir,
        branch=request.branch,
        shallow=False,  # Need full history for git describe
    )

    if repo_path is None:
        return SnapshotAcquisitionResult(
            success=False,
            project=request.project,
            error=error,
        )

    # Step 2: Get git snapshot info (date and full SHA)
    short_sha, full_sha, date_str = get_git_snapshot_info(repo_path, request.git_ref)

    if not short_sha or not date_str:
        return SnapshotAcquisitionResult(
            success=False,
            repo_path=repo_path,
            project=request.project,
            cloned=cloned,
            error="Failed to get git snapshot info",
        )

    # Step 3: Compute upstream version using git describe for accuracy
    # This gives us the most recent tag plus commits since, avoiding
    # the need to guess the next version number
    describe_result = get_version_from_git_describe(repo_path, request.git_ref)

    if describe_result:
        if describe_result.is_exact_tag:
            # Exactly at a tag - use +git to indicate post-release snapshot
            # For example: 7.2.0+git20240115.abc1234 sorts after 7.2.0
            upstream_version = f"{describe_result.base_version}+git{date_str}.{short_sha}"
        else:
            # Commits after tag: use +git for post-release development
            # For example: 7.2.0+git20240115.5.abc1234 sorts after 7.2.0
            upstream_version = (
                f"{describe_result.base_version}+git{date_str}."
                f"{describe_result.commit_count}.{describe_result.short_sha}"
            )
    else:
        # Fallback to old format if git describe fails
        upstream_version = f"{request.base_version}+git{date_str}.{short_sha}"


    # Step 4: Generate orig tarball
    tarball_result = generate_snapshot_tarball(
        repo_path=repo_path,
        ref=request.git_ref,
        package=pkg_name,
        version=upstream_version,
        output_dir=output_dir,
    )

    if not tarball_result.success:
        return SnapshotAcquisitionResult(
            success=False,
            repo_path=repo_path,
            project=request.project,
            cloned=cloned,
            git_sha=full_sha,
            git_sha_short=short_sha,
            git_date=date_str,
            upstream_version=upstream_version,
            git_ref=request.git_ref,
            error=tarball_result.error or "Failed to generate tarball",
        )

    return SnapshotAcquisitionResult(
        success=True,
        repo_path=repo_path,
        tarball_result=tarball_result,
        git_sha=full_sha,
        git_sha_short=short_sha,
        git_date=date_str,
        upstream_version=upstream_version,
        project=request.project,
        git_ref=request.git_ref,
        cloned=cloned,
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python -m packastack.upstream.source <project> <version>")
        sys.exit(1)

    project = sys.argv[1]
    version = sys.argv[2]

    tarball_url = build_tarball_url(project, version)
    signature_url = build_signature_url(tarball_url)

    print(f"Project: {project}")
    print(f"Version: {version}")
    print(f"Tarball URL: {tarball_url}")
    print(f"Signature URL: {signature_url}")
