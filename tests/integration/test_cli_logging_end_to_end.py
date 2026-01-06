# Integration test demonstrating a minimal CLI run without network calls
import io
from unittest.mock import patch

from packastack.cli import PackastackApp


def run_cli(args):
    stdout = io.StringIO()
    app = PackastackApp(stdout=stdout)
    code = app.run(args)
    return code, stdout.getvalue()


@patch("packastack.cmds.import_tarballs.get_launchpad_repositories", return_value=[])
def test_cli_end_to_end(mock_get_repos, tmp_path):
    """Run CLI with a real releases repo and ensure logs are created.

    This test avoids network calls by ensuring the output/upstream/releases
    repo is a local git repository and patching get_launchpad_repositories.
    """
    # Create required directories under root/output
    root = tmp_path
    output = root / "output"
    packaging = output / "packaging"
    upstream = output / "upstream"
    tarballs = output / "tarballs"
    logs = output / "logs"
    packaging.mkdir(parents=True)
    upstream.mkdir(parents=True)
    tarballs.mkdir(parents=True)
    logs.mkdir(parents=True)

    # Create a minimal releases repository with a data file so get_current_cycle
    # can read it without errors and create a git repo to satisfy RepoManager
    releases = upstream / "releases"
    releases.mkdir(parents=True)
    (releases / "data").mkdir()
    series = releases / "data" / "series_status.yaml"
    series.write_text("[{'name': 'gazpacho', 'status': 'development'}]")

    # Initialize a git repository for releases to avoid RepoManager clone
    import subprocess as _sub
    _sub.run(["git", "init"], cwd=str(releases), check=True)
    (releases / "README").write_text("initial")
    _sub.run(["git", "add", "README"], cwd=str(releases), check=True)
    _sub.run(
        [
            "git",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "init",
        ],
        cwd=str(releases),
        check=True,
    )
    # The init already created a master branch, no need to create it again
    # Create a bare mirror of the releases repo and push to it so fetch/pull works
    bare_releases = upstream / "releases.git"
    bare_releases.mkdir()
    _sub.run(["git", "init", "--bare"], cwd=str(bare_releases), check=True)
    _sub.run(
        ["git", "remote", "add", "origin", f"file://{bare_releases}"],
        cwd=str(releases),
        check=True,
    )
    _sub.run(["git", "push", "origin", "master"], cwd=str(releases), check=True)

    # Patch setup_releases_repo so CLI doesn't try to
    # fetch/pull from remotes
    from unittest.mock import patch as _patch
    with _patch(
        "packastack.cmds.import_tarballs.setup_releases_repo",
        return_value=releases,
    ):
        code, _ = run_cli(["--root", str(root), "import"])
    assert code == 0

    # Basic assertions that logs directory exists and CLI log file is present
    cli_files = list(logs.glob("packastack-*.log"))
    assert len(cli_files) >= 1
