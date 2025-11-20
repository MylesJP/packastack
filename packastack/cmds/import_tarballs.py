# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Import command for importing upstream tarballs."""

import fnmatch
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console

from packastack.constants import (
    ERROR_LOG_FILE,
    RELEASES_DIR,
    RELEASES_REPO_URL,
    UPSTREAM_BRANCH_PREFIX,
    UPSTREAM_GIT_REPOS,
)
from packastack.debian.control import ControlFileParser
from packastack.debian.version import VersionConverter
from packastack.exceptions import (
    DebianError,
    ImporterError,
    PackastackError,
)
from packastack.gbp import GitBuildPackage
from packastack.git import RepoManager
from packastack.importer import (
    BetaImporter,
    CandidateImporter,
    ReleaseImporter,
    SnapshotImporter,
    get_current_cycle,
    get_deliverable_info,
    get_previous_cycle,
)
from packastack.launchpad import (
    LaunchpadClient,
    RepositoryManager,
    lpci,
)

# Import type constants
RELEASE = "release"
CANDIDATE = "candidate"
BETA = "beta"
SNAPSHOT = "snapshot"
AUTO = "auto"
IMPORT_TYPES = [
    RELEASE,
    CANDIDATE,
    BETA,
    SNAPSHOT,
]

logger = logging.getLogger(__name__)
console = Console()


class ImportContext:
    """Shared context for import operations."""

    def __init__(self, cycle: str, import_type: str, cleanup_tarballs: bool):
        """Initialize import context."""
        self.cycle = cycle
        self.import_type = import_type
        self.cleanup_tarballs = cleanup_tarballs
        self.releases_lock = threading.Lock()
        self.tarballs_lock = threading.Lock()
        self.successes = []
        self.failures = []
        self.lock = threading.Lock()

    def add_success(self, repo_name: str):
        """Add successful import."""
        with self.lock:
            self.successes.append(repo_name)

    def add_failure(self, repo_name: str, error: str):
        """Add failed import."""
        with self.lock:
            self.failures.append((repo_name, error))


def setup_directories(root: Path | None = None) -> tuple[Path, Path, Path, Path]:
    """
    Create working directories.

    Args:
        root: Root directory to create subdirectories in.
            Defaults to current working directory.

    Returns:
        Tuple of (packaging_dir, upstream_dir, tarballs_dir, logs_dir)
    """
    if root is None:
        root = Path.cwd()

    packaging = root / "packaging"
    upstream = root / "upstream"
    tarballs = root / "tarballs"
    logs = root / "logs"

    packaging.mkdir(parents=True, exist_ok=True)
    upstream.mkdir(parents=True, exist_ok=True)
    tarballs.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    logger.debug("Created working directories under %s", root)

    return packaging, upstream, tarballs, logs


def setup_releases_repo(releases_lock: threading.Lock, upstream_dir: Path) -> Path:
    """
    Clone or update releases repository.

    Args:
        releases_lock: Lock for releases repo operations
        upstream_dir: Base upstream directory where releases repo should be cloned

    Returns:
        Path to releases repository

    Raises:
        RepositoryError: If clone/update fails
    """
    with releases_lock:
        releases_path = upstream_dir / RELEASES_DIR

        if releases_path.exists():
            # Update existing repo
            repo_mgr = RepoManager(path=releases_path)
            repo_mgr.fetch()
            repo_mgr.checkout("master")
            repo_mgr.pull()
        else:
            # Clone new repo
            logger.info(
                "Cloning releases repo %s to %s", RELEASES_REPO_URL, releases_path
            )
            repo_mgr = RepoManager(url=RELEASES_REPO_URL)
            # Set the clone destination path on the manager for operations
            repo_mgr.path = releases_path
            repo_mgr.clone()

        return releases_path


def determine_importer_type(
    import_type: str, upstream_repo_path: Path
) -> tuple[str, bool]:
    """
    Determine which importer to use.

    Args:
        import_type: User-specified type or 'auto'
        upstream_repo_path: Path to upstream repository

    Returns:
        Tuple of (importer_type, explicit_snapshot)
        importer_type: 'release', 'candidate', 'beta', or 'snapshot'
        explicit_snapshot: True if user explicitly requested snapshot

    Raises:
        ImporterError: If auto-detection fails
    """
    if import_type != AUTO:
        return import_type, import_type == SNAPSHOT

    # Auto-detect from HEAD tags
    try:
        repo_mgr = RepoManager(path=upstream_repo_path)
        head_tags = repo_mgr.get_head_tags()

        if not head_tags:
            logger.info("No tags at HEAD for %s; using snapshot importer", upstream_repo_path)
            # No tags at HEAD, use snapshot
            return SNAPSHOT, False

        # Check tag format to determine type
        for tag in head_tags:
            version_type = VersionConverter.detect_version_type(tag)
            if version_type in IMPORT_TYPES:
                logger.info("Auto-detected import type %s for tag %s", version_type, tag)
                return version_type, False

        # Tag exists but type unknown, default to snapshot
        return SNAPSHOT, False

    except Exception as e:
        logger.exception("Failed to auto-detect import type: %s", e)
        raise ImporterError(f"Failed to auto-detect import type: {e}")


def setup_repository(
    repo_name: str, repo_url: str, base_dir: Path
) -> RepoManager:
    """
    Clone or update a repository.

    Args:
        repo_name: Repository name
        repo_url: Repository URL
        base_dir: Base directory for cloning

    Returns:
        RepoManager instance

    Raises:
        RepositoryError: If clone/update fails
    """
    repo_path = base_dir / repo_name
    if repo_path.exists():
        repo_mgr = RepoManager(path=repo_path)
        repo_mgr.fetch()
    else:
        repo_mgr = RepoManager(url=repo_url)
        # Ensure we set the local clone destination before cloning
        repo_mgr.path = repo_path
        repo_mgr.clone()

    return repo_mgr


def parse_packaging_metadata(pkg_repo: RepoManager) -> tuple[str, str, str]:
    """
    Parse debian/control for metadata.

    Args:
        pkg_repo: RepoManager instance for packaging repository

    Returns:
        Tuple of (source_name, homepage, upstream_project_name)

    Raises:
        DebianError: If parsing fails or required fields missing
    """
    control_path = pkg_repo.path / "debian" / "control"
    if not control_path.exists():
        raise DebianError(
            f"debian/control not found in package {pkg_repo.name}"
            f" on branch {pkg_repo.get_current_branch()}"
        )

    parser = ControlFileParser(str(control_path))
    source_name = parser.get_source_name()
    homepage = parser.get_homepage()

    if not homepage:
        raise DebianError(
            f"Homepage not found in {pkg_repo.name} debian/control"
            f" on branch {pkg_repo.get_current_branch()}"
        )

    upstream_project_name = parser.get_upstream_project_name()
    if not upstream_project_name:
        raise DebianError(
            f"Could not extract project name from Homepage in {pkg_repo.name}"
            f" on branch {pkg_repo.get_current_branch()}"
        )

    return source_name, homepage, upstream_project_name


def setup_upstream_repository(
    upstream_project_name: str, homepage: str, upstream_dir: Path
) -> tuple[RepoManager, Path]:
    """
    Clone or update upstream repository.

    Args:
        upstream_project_name: Name of upstream project
        homepage: Upstream repository URL
        upstream_dir: Base directory for upstream repos

    Returns:
        RepoManager instance

    Raises:
        RepositoryError: If clone/update fails
    """
    upstream_repo_path = upstream_dir / upstream_project_name
    remote = UPSTREAM_GIT_REPOS.get(upstream_project_name, homepage)
    if upstream_repo_path.exists():
        upstream_mgr = RepoManager(path=upstream_repo_path)
        # Verify URL matches
        current_url = upstream_mgr.get_remote_url()
        if current_url != remote:
            upstream_mgr.set_remote_url(remote)
        upstream_mgr.fetch()
        upstream_mgr.checkout("master")
        upstream_mgr.pull()
    else:
        upstream_mgr = RepoManager(path=upstream_repo_path, url=remote)
        upstream_mgr.clone()

    return upstream_mgr


def update_gbp_and_ci_files(
    pkg_mgr: RepoManager, upstream_branch: str, cycle: str
) -> None:
    """
    Update debian/gbp.conf and .launchpad.yaml with upstream branch.

    Args:
        pkg_mgr: Package repository manager
        upstream_branch: Name of upstream branch
        cycle: OpenStack cycle name

    Raises:
        DebianError: If gbp.conf update fails
    """
    # Only edit the files on the master branch.
    pkg_mgr.checkout("master")
    commit_msg = []
    files = []
    gbp = GitBuildPackage(pkg_mgr.path)
    if gbp.update_gbp_conf(upstream_branch):
        files.append("debian/gbp.conf")
        commit_msg.append(f"* d/gbp.conf: Update upstream-branch for {cycle}")

    if lpci.update_launchpad_ci_file(pkg_mgr.path, cycle):
        files.append(".launchpad.yaml")
        commit_msg.append(f"* .launchpad.yaml: Update openstack_series for {cycle}")

    if commit_msg:
        pkg_mgr.commit("\n".join(commit_msg), files)
        logger.info("Committed files %s in %s with message: %s", files, pkg_mgr.path, commit_msg)


def create_upstream_branch(
    pkg_mgr: RepoManager,
    upstream_branch: str,
    releases_path: Path,
) -> None:
    """
    Create upstream branch if it doesn't exist.

    Args:
        pkg_mgr: Package repository manager
        upstream_branch: Name of upstream branch to create
        releases_path: Path to releases repository

    Raises:
        RepositoryError: If branch creation fails
    """
    if not pkg_mgr.branch_exists(upstream_branch):
        # Use previous cycle as base if it exists
        previous_cycle = get_previous_cycle(releases_path)
        original_branch = pkg_mgr.get_current_branch()
        if previous_cycle:
            previous_branch = f"{UPSTREAM_BRANCH_PREFIX}-{previous_cycle}"
            if pkg_mgr.branch_exists(previous_branch):
                pkg_mgr.create_branch(upstream_branch, previous_branch)
            else:
                # No previous branch, create from HEAD
                pkg_mgr.create_branch(upstream_branch)
        else:
            pkg_mgr.create_branch(upstream_branch)
        pkg_mgr.checkout(original_branch)


def check_deliverable_exists(
    releases_path: Path,
    cycle: str,
    upstream_project_name: str,
    import_type: str,
    repo_name: str,
) -> bool:
    """
    Check if deliverable exists for release-based imports.

    Args:
        releases_path: Path to releases repository
        cycle: OpenStack cycle name
        upstream_project_name: Name of upstream project
        import_type: Type of import being performed
        repo_name: Repository name for messages

    Returns:
        True if should continue, False if should skip

    Raises:
        ImporterError: If deliverable check fails
    """
    deliverable = get_deliverable_info(
        str(releases_path), cycle, upstream_project_name
    )
    if not deliverable and import_type in [RELEASE, CANDIDATE, BETA]:
        console.print(
            f"[yellow]Warning: No deliverable found for {repo_name}, "
            f"skipping[/yellow]"
        )
        return False
    return True


def create_and_import_tarball(
    importer_type: str,
    explicit_snapshot: bool,
    pkg_repo_path: Path,
    upstream_repo_path: Path,
    tarballs_dir: Path,
    cycle: str,
    releases_path: Path,
    source_name: str,
) -> tuple[str, Path]:
    """
    Create importer, generate tarball, and prepare for import.

    Args:
        importer_type: Type of importer to use
        explicit_snapshot: Whether snapshot was explicitly requested
        pkg_repo_path: Path to packaging repository
        upstream_repo_path: Path to upstream repository
        tarballs_dir: Directory for tarballs
        cycle: OpenStack cycle name
        releases_path: Path to releases repository
        source_name: Debian source package name

    Returns:
        Tuple of (debian_version, renamed_tarball_path)

    Raises:
        ImporterError: If tarball creation/import fails
    """
    importer_cls = {
        RELEASE: ReleaseImporter,
        CANDIDATE: CandidateImporter,
        BETA: BetaImporter,
        SNAPSHOT: SnapshotImporter,
    }[importer_type]

    importer = importer_cls(
        str(pkg_repo_path),
        str(upstream_repo_path),
        str(tarballs_dir),
        cycle,
        str(releases_path),
        explicit_snapshot=explicit_snapshot,
    )

    # Import tarball
    debian_version = importer.import_tarball()
    logger.info("Imported tarball for %s produced debian_version %s", source_name, debian_version)

    # Get upstream version and tarball
    upstream_version = importer.get_version()
    tarball_path = importer.get_tarball(upstream_version)

    # Rename tarball to Debian naming
    renamed_tarball = importer.rename_tarball(
        tarball_path, source_name, debian_version
    )

    return debian_version, renamed_tarball


def cleanup_tarballs(tarball_path: Path) -> None:
    """
    Remove tarball and signature file.

    Args:
        tarball_path: Path to tarball to remove

    Note:
        Failures are silently ignored as cleanup is non-fatal
    """
    try:
        tarball_path.unlink()
        # Also remove signature if exists
        sig_path = tarball_path.with_suffix(tarball_path.suffix + ".asc")
        if sig_path.exists():
            sig_path.unlink()
    except Exception:
        pass  # Non-fatal


def get_launchpad_repositories() -> list:
    """
    Fetch list of repositories from Launchpad.

    Returns:
        List of repository objects from Launchpad

    Raises:
        LaunchpadError: If connection or fetching fails
    """
    lp_client = LaunchpadClient()
    lp_client.connect()
    repo_mgr = RepositoryManager(lp_client)
    return repo_mgr.list_team_repositories()


def filter_repositories(
    repositories: list, patterns: list[str], exclude: bool = False
) -> list:
    """
    Filter the list of repository objects by name using patterns and glob support.

    Args:
        repositories: List of repository objects (with `.name` attribute)
        patterns: List of patterns to match; if a pattern contains glob symbols (*?[])
            it will be treated as a glob; otherwise exact match is used.
        exclude: If True, return repositories that DO NOT match any pattern.

    Returns:
        Filtered list of repository objects
    """
    if not patterns:
        return repositories

    normalized_patterns = [p.casefold() for p in patterns]

    def matches(name: str) -> bool:
        n = name.casefold()
        for pat in normalized_patterns:
            if any(ch in pat for ch in "*?[]"):
                if fnmatch.fnmatchcase(n, pat):
                    return True
            else:
                if n == pat:
                    return True
        return False

    filtered = [r for r in repositories if matches(r.name)]
    if exclude:
        # return those NOT in filtered
        filtered_names = {r.name for r in filtered}
        return [r for r in repositories if r.name not in filtered_names]
    return filtered


def process_repositories(
    repositories: list,
    context: ImportContext,
    packaging_dir: Path,
    upstream_dir: Path,
    tarballs_dir: Path,
    releases_path: Path,
    continue_on_error: bool,
    jobs: int,
) -> None:
    """
    Process repositories sequentially or in parallel.

    Args:
        repositories: List of repositories to process
        context: Shared import context
        packaging_dir: Path to packaging directory
        upstream_dir: Path to upstream directory
        tarballs_dir: Path to tarballs directory
        releases_path: Path to releases repository
        continue_on_error: Whether to continue on error
        jobs: Number of parallel jobs (1 for sequential)

    Raises:
        PackastackError: If processing fails and continue_on_error is False
    """
    if jobs == 1:
        # Sequential processing
        for repo in repositories:
            process_repository(
                repo.name,
                repo.url,
                context,
                packaging_dir,
                upstream_dir,
                tarballs_dir,
                releases_path,
                continue_on_error,
            )
    else:
        # Parallel processing
        with ThreadPoolExecutor(max_workers=jobs) as executor:

            futures = {
                executor.submit(
                    process_repository,
                    repo.name,
                    repo.url,
                    context,
                    packaging_dir,
                    upstream_dir,
                    tarballs_dir,
                    releases_path,
                    continue_on_error,
                ): repo
                for repo in repositories
            }

            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    if not continue_on_error:
                        # Cancel remaining tasks
                        for f in futures:
                            f.cancel()
                        raise


def print_import_summary(
    context: ImportContext, error_log_path: Path, continue_on_error: bool
) -> None:
    """
    Print import summary and log errors.

    Args:
        context: Import context with results
        error_log_path: Path to error log file
        continue_on_error: Whether errors were allowed to continue

    Raises:
        click.ClickException: If there were failures and continue_on_error is False
    """
    console.print("\n[bold]Import Summary[/bold]")
    console.print(f"[green]Successful:[/green] {len(context.successes)}")
    console.print(f"[red]Failed:[/red] {len(context.failures)}")

    if context.failures:
        console.print(f"\n[yellow]Errors logged to:[/yellow] {error_log_path}")
        for repo_name, error in context.failures:
            logging.error(f"{repo_name}: {error}")

    # Raise exception if any failures and not continue-on-error
    if context.failures and not continue_on_error:
        raise click.ClickException(
            f"Import failed for {len(context.failures)} repositories"
        )


def process_repository(
    repo_name: str,
    repo_url: str,
    context: ImportContext,
    packaging_dir: Path,
    upstream_dir: Path,
    tarballs_dir: Path,
    releases_path: Path,
    continue_on_error: bool,
) -> bool:
    """
    Process a single repository.

    Args:
        repo_name: Repository name
        repo_url: Repository URL
        context: Shared import context
        packaging_dir: Path to packaging directory
        upstream_dir: Path to upstream directory
        tarballs_dir: Path to tarballs directory
        releases_path: Path to releases repository
        continue_on_error: Whether to continue on error

    Returns:
        True if successful, False otherwise
    """
    console.print(f"Processing repository: {repo_name}")
    try:
        # 1. Clone/update packaging repo
        pkg_mgr = setup_repository(repo_name, repo_url, packaging_dir)

        # 2. Track remote branches
        pkg_mgr.track_remote_branches()

        # 3. Checkout important branches
        pkg_mgr.checkout_important_branches()

        # 4. Parse debian/control for source name and upstream URL
        source_name, homepage, upstream_project_name = parse_packaging_metadata(
            pkg_mgr
        )

        # 5. Clone/update upstream repo
        upstream_mgr = setup_upstream_repository(
            upstream_project_name, homepage, upstream_dir
        )

        # 6. Create upstream branch if needed
        upstream_branch = f"{UPSTREAM_BRANCH_PREFIX}-{context.cycle}"
        create_upstream_branch(pkg_mgr, upstream_branch, releases_path)

        # 7. Update debian/gbp.conf and launchpad ci files.
        update_gbp_and_ci_files(pkg_mgr, upstream_branch, context.cycle)

        # 8. Check if deliverable exists
        if not check_deliverable_exists(
            releases_path,
            context.cycle,
            upstream_project_name,
            context.import_type,
            repo_name,
        ):
            return False

        # 9. Determine importer type
        importer_type, explicit_snapshot = determine_importer_type(
            context.import_type, upstream_mgr.path
        )

        # 10-13. Create importer and get tarball
        debian_version, renamed_tarball = create_and_import_tarball(
            importer_type,
            explicit_snapshot,
            pkg_mgr.path,
            upstream_mgr.path,
            tarballs_dir,
            context.cycle,
            releases_path,
            source_name,
        )

        # 14. Run gbp import-orig
        gbp = GitBuildPackage(pkg_mgr.path)
        gbp.import_orig(renamed_tarball)

        # 15. Cleanup tarball if requested
        if context.cleanup_tarballs:
            cleanup_tarballs(renamed_tarball)

        context.add_success(repo_name)
        console.print(f"[green]✓[/green] {repo_name}: {debian_version}")
        return True

    except SystemExit as e:
        if e.code == 74:
            # EBADMSG - explicitly requested snapshot but HEAD is tagged
            error_msg = "Explicitly requested snapshot but HEAD is tagged"
            context.add_failure(repo_name, error_msg)
            console.print(f"[red]✗[/red] {repo_name}: {error_msg}")
            if not continue_on_error:
                raise
            return False
        raise

    except PackastackError as e:
        error_msg = str(e)
        context.add_failure(repo_name, error_msg)
        console.print(f"[red]✗[/red] {repo_name}: {error_msg}")
        if not continue_on_error:
            raise
        return False

    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        context.add_failure(repo_name, error_msg)
        console.print(f"[red]✗[/red] {repo_name}: {error_msg}")
        if not continue_on_error:
            raise
        return False


@click.command("import")
@click.pass_context
@click.argument("packages", nargs=-1)
@click.option(
    "--exclude-packages/--include-packages",
    "exclude_packages",
    default=False,
    help="Exclude packages specified instead of including them",
)
@click.option(
    "--type",
    "import_type",
    type=click.Choice(
        [
            AUTO,
            RELEASE,
            CANDIDATE,
            BETA,
            SNAPSHOT,
        ]
    ),
    default=AUTO,
    help="Type of tarball to import",
)
@click.option(
    "--cycle",
    default="current",
    help="OpenStack cycle name (default: current development cycle)",
)
@click.option(
    "--jobs",
    type=int,
    default=1,
    help="Number of parallel jobs (default: 1 for sequential)",
)
@click.option(
    "--continue-on-error/--no-continue-on-error",
    default=False,
    help="Continue processing other repos if one fails",
)
@click.option(
    "--cleanup-tarballs/--no-cleanup-tarballs",
    default=False,
    help="Remove tarballs after successful import",
)
def import_cmd(
    ctx,
    packages: tuple[str, ...],
    exclude_packages: bool,
    import_type: str,
    cycle: str,
    jobs: int,
    continue_on_error: bool,
    cleanup_tarballs: bool,
):
    """Import upstream tarballs into packaging repositories."""
    # CLI-level logging is configured by the parent `cli` group which sets
    # the root value in click context; we can pick up any per-command root
    # if necessary via click.get_current_context().obj
    console.print("[bold]Starting import process...[/bold]")
    logging.getLogger(__name__).info("Starting import process")

    try:
        # Root path is provided as global CLI option and stored in context
        root = ctx.obj.get("root")
        # Setup directories
        packaging_dir, upstream_dir, tarballs_dir, logs_dir = setup_directories(root)
        console.print("Created working directories")
        logging.getLogger(__name__).info("Created working directories in %s", root)

        # Setup releases repo
        console.print("Setting up releases repository...")
        releases_lock = threading.Lock()
        releases_path = setup_releases_repo(releases_lock, upstream_dir)

        # Determine cycle
        if cycle == "current":
            actual_cycle = get_current_cycle(releases_path)
            console.print(f"Current development cycle: [cyan]{actual_cycle}[/cyan]")
        else:
            actual_cycle = cycle
            console.print(f"Using cycle: [cyan]{actual_cycle}[/cyan]")

        # Create import context
        context = ImportContext(actual_cycle, import_type, cleanup_tarballs)

        # Get list of repositories from Launchpad
        console.print("Fetching repository list from Launchpad...")
        logging.getLogger(__name__).info("Fetching launchpad repositories")
        repositories = get_launchpad_repositories()
        console.print(f"Found [cyan]{len(repositories)}[/cyan] repositories")

        # Apply package filtering if packages were specified
        if packages:
            repositories = filter_repositories(
                repositories, list(packages), exclude_packages
            )
            msg = (
                f"Processing [cyan]{len(repositories)}[/cyan] "
                "repositories after filter"
            )
            console.print(msg)

        # Setup error logging with per-run timestamped filename
        # Ensure logs directory exists before configuring logging
        logs_dir.mkdir(parents=True, exist_ok=True)
        base = Path(ERROR_LOG_FILE)
        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        error_log_path = logs_dir / f"{base.stem}-{timestamp}{base.suffix}"
        root_logger = logging.getLogger()
        # Remove any previous error handlers (not CLI packastack handlers)
        for h in list(root_logger.handlers):
            if isinstance(h, logging.FileHandler) and getattr(h, "packastack_error", False):
                root_logger.removeHandler(h)

        fh = logging.FileHandler(error_log_path, encoding="utf-8")
        fh.setLevel(logging.ERROR)
        fh.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        # Mark handler so it can be removed later if needed
        setattr(fh, "packastack_error", True)
        root_logger.addHandler(fh)

        # Process repositories
        process_repositories(
            repositories,
            context,
            packaging_dir,
            upstream_dir,
            tarballs_dir,
            releases_path,
            continue_on_error,
            jobs,
        )

        # Print summary
        print_import_summary(context, error_log_path, continue_on_error)

    except KeyboardInterrupt:
        console.print("\n[yellow]Import interrupted by user[/yellow]")
        raise click.Abort()
    except PackastackError as e:
        raise click.ClickException(str(e))
    except click.ClickException:
        # Re-raise Click exceptions as-is
        raise
    except Exception as e:
        raise click.ClickException(f"Unexpected error: {e}")
