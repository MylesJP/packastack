# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Import command for importing upstream tarballs."""

import errno
import fnmatch
import logging
import threading
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cliff.command import Command
from oslo_config import cfg
import sys

from packastack.constants import (
    ERROR_LOG_FILE,
    RELEASES_DIR,
    RELEASES_REPO_URL,
    UPSTREAM_BRANCH_PREFIX,
    UPSTREAM_GIT_REPOS,
)
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
from packastack.package.control import ControlFileParser
from packastack.package.version import VersionConverter

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

CLI_OPTS: list[cfg.Opt] = [
    cfg.MultiStrOpt(
        "packages",
        positional=True,
        default=[],
        help="Packages to import (default: all known packages)",
    ),
    cfg.BoolOpt(
        "exclude_packages",
        default=False,
        help="Treat listed packages as exclusions and process all others",
    ),
    cfg.StrOpt(
        "type",
        dest="import_type",
        choices=IMPORT_TYPES + [AUTO],
        default=AUTO,
        help="Type of tarball to import",
    ),
    cfg.StrOpt(
        "cycle",
        default="current",
        help="OpenStack cycle name (default: current development cycle)",
    ),
    cfg.IntOpt(
        "jobs",
        default=1,
        help="Number of parallel jobs (default: 1 for sequential)",
    ),
    cfg.BoolOpt(
        "continue_on_error",
        default=False,
        help="Continue processing other repos if one fails",
    ),
]


class CLICommandError(Exception):
    """Custom command error used for CLI-friendly failures."""


class CLIConsole:
    """Lightweight console wrapper for writing CLI output."""

    def __init__(self, stream=None):
        self.stream = stream or sys.stdout

    def set_stream(self, stream) -> None:
        """Update the target stream."""

        self.stream = stream

    def print(self, message: str = "") -> None:
        """Write a message to the stream."""

        self.stream.write(f"{message}\n")
        self.stream.flush()


console = CLIConsole()


@dataclass(frozen=True)
class RepositorySpec:
    """Lightweight representation of a Launchpad repository entry."""

    name: str
    url: str


class ImportContext:
    """Shared context for import operations."""

    def __init__(self, cycle: str, import_type: str):
        """Initialize import context."""
        self.cycle = cycle
        self.import_type = import_type
        self.releases_lock = threading.Lock()
        self.tarballs_lock = threading.Lock()
        self.successes: list[str] = []
        self.failures: list[tuple[str, str]] = []
        self.lock = threading.Lock()

    def add_success(self, repo_name: str) -> None:
        """Add successful import."""
        with self.lock:
            self.successes.append(repo_name)

    def add_failure(self, repo_name: str, error: str) -> None:
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
        repo_mgr = RepoManager(path=releases_path, url=RELEASES_REPO_URL)

        if releases_path.exists():
            repo_mgr.fetch()
            repo_mgr.checkout("master")
            repo_mgr.pull()
        else:
            logger.info(
                "Cloning releases repo %s to %s", RELEASES_REPO_URL, releases_path
            )
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
            logger.info(
                "No tags at HEAD for %s; using snapshot importer",
                upstream_repo_path,
            )
            # No tags at HEAD, use snapshot
            return SNAPSHOT, False

        # Check tag format to determine type
        for tag in head_tags:
            version_type = VersionConverter.detect_version_type(tag)
            if version_type in IMPORT_TYPES:
                logger.info(
                    "Auto-detected import type %s for tag %s",
                    version_type,
                    tag,
                )
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
    repo_mgr = RepoManager(path=repo_path, url=repo_url)
    if repo_path.exists():
        repo_mgr.fetch()
    else:
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

    parser = ControlFileParser(control_path)
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
        logger.info(
            "Committed files %s in %s with message: %s",
            files,
            pkg_mgr.path,
            commit_msg,
        )


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
    deliverable = get_deliverable_info(releases_path, cycle, upstream_project_name)
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
        pkg_repo_path,
        upstream_repo_path,
        tarballs_dir,
        cycle,
        releases_path,
        explicit_snapshot=explicit_snapshot,
    )

    # Import tarball
    debian_version = importer.import_tarball()
    logger.info(
        "Imported tarball for %s produced debian_version %s",
        source_name,
        debian_version,
    )

    # Get upstream version and tarball
    upstream_version = importer.get_version()
    tarball_path = importer.get_tarball(upstream_version)

    # Rename tarball to Debian naming
    renamed_tarball = importer.rename_tarball(
        tarball_path, source_name, debian_version
    )

    return debian_version, renamed_tarball


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


def to_repository_specs(repositories: Iterable) -> list[RepositorySpec]:
    """Normalize Launchpad repository objects to RepositorySpec instances."""
    specs: list[RepositorySpec] = []
    for repo in repositories:
        name = getattr(repo, "name", None)
        url = getattr(repo, "url", None) or getattr(repo, "git_https_url", None)
        if not name or not url:
            raise ImporterError("Repository entry missing required attributes")
        specs.append(RepositorySpec(name=name, url=url))
    return specs


def filter_repositories(
    repositories: Iterable[RepositorySpec], patterns: list[str], exclude: bool = False
) -> list[RepositorySpec]:
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
    repositories: Iterable[RepositorySpec],
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
    repo_specs = list(repositories)
    if jobs == 1:
        for spec in repo_specs:
            process_repository(
                spec.name,
                spec.url,
                context,
                packaging_dir,
                upstream_dir,
                tarballs_dir,
                releases_path,
                continue_on_error,
            )
        return

    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {
            executor.submit(
                process_repository,
                spec.name,
                spec.url,
                context,
                packaging_dir,
                upstream_dir,
                tarballs_dir,
                releases_path,
                continue_on_error,
            ): spec
            for spec in repo_specs
        }

        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                if not continue_on_error:
                    for pending in futures:
                        pending.cancel()
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
        CLICommandError: If there were failures and continue_on_error is False
    """
    console.print("\nImport Summary")
    console.print(f"Successful: {len(context.successes)}")
    console.print(f"Failed: {len(context.failures)}")

    if context.failures:
        console.print(f"\nErrors logged to: {error_log_path}")
        for repo_name, error in context.failures:
            logging.error(f"{repo_name}: {error}")

    # Raise exception if any failures and not continue-on-error
    if context.failures and not continue_on_error:
        raise CLICommandError(
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
        repo: the repository to process
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

        context.add_success(repo_name)
        console.print(f"[green]✓[/green] {repo_name}: {debian_version}")
        return True

    except SystemExit as e:
        if e.code == errno.EBADMSG:
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


class ImportTarballsCommand(Command):
    """Import upstream tarballs into packaging repositories."""

    cli_opts = CLI_OPTS

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        from packastack.cli import add_opts_to_parser

        add_opts_to_parser(parser, CLI_OPTS)
        return parser

    def take_action(self, parsed_args):
        console.set_stream(self.app.stdout)
        console.print("Starting import process...")
        logging.getLogger(__name__).info("Starting import process")

        try:
            root_value = getattr(parsed_args, "root", None)
            if root_value is None and hasattr(self.app, "options"):
                root_value = getattr(self.app.options, "root", None)

            root = Path(root_value) if root_value else None
            packaging_dir, upstream_dir, tarballs_dir, logs_dir = setup_directories(root)
            console.print("Created working directories")
            logging.getLogger(__name__).info("Created working directories in %s", root)

            console.print("Setting up releases repository...")
            releases_lock = threading.Lock()
            releases_path = setup_releases_repo(releases_lock, upstream_dir)

            if parsed_args.cycle == "current":
                actual_cycle = get_current_cycle(releases_path)
                console.print(f"Current development cycle: {actual_cycle}")
            else:
                actual_cycle = parsed_args.cycle
                console.print(f"Using cycle: {actual_cycle}")

            context = ImportContext(actual_cycle, parsed_args.import_type)

            console.print("Fetching repository list from Launchpad...")
            logging.getLogger(__name__).info("Fetching launchpad repositories")
            repositories = to_repository_specs(get_launchpad_repositories())
            console.print(f"Found {len(repositories)} repositories")

            if parsed_args.packages:
                repositories = filter_repositories(
                    repositories, list(parsed_args.packages), parsed_args.exclude_packages
                )
                msg = f"Processing {len(repositories)} repositories after filter"
                console.print(msg)

            logs_dir.mkdir(parents=True, exist_ok=True)
            base = Path(ERROR_LOG_FILE)
            timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            error_log_path = logs_dir / f"{base.stem}-{timestamp}{base.suffix}"
            root_logger = logging.getLogger()
            for handler in list(root_logger.handlers):
                if isinstance(handler, logging.FileHandler) and getattr(
                    handler, "packastack_error", False
                ):
                    root_logger.removeHandler(handler)

            file_handler = logging.FileHandler(error_log_path, encoding="utf-8")
            file_handler.setLevel(logging.ERROR)
            file_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
                )
            )
            setattr(file_handler, "packastack_error", True)
            root_logger.addHandler(file_handler)

            process_repositories(
                repositories,
                context,
                packaging_dir,
                upstream_dir,
                tarballs_dir,
                releases_path,
                parsed_args.continue_on_error,
                parsed_args.jobs,
            )

            print_import_summary(
                context, error_log_path, parsed_args.continue_on_error
            )

        except KeyboardInterrupt:
            console.print("\nImport interrupted by user")
            raise CLICommandError("Import interrupted by user")
        except PackastackError as exc:
            raise CLICommandError(str(exc))
        except CLICommandError:
            raise
        except Exception as exc:  # pragma: no cover - defensive guard
            raise CLICommandError(f"Unexpected error: {exc}")
