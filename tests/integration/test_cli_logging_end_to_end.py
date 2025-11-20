# Integration test demonstrating a minimal CLI run without network calls
from unittest.mock import patch

from click.testing import CliRunner

from packastack.cli import cli


@patch("packastack.cmds.import_tarballs.get_launchpad_repositories", return_value=[])
def test_cli_end_to_end(mock_get_repos, tmp_path):
    """Run CLI with a real releases repo and ensure logs are created.

    This test avoids network calls by ensuring the upstream/releases repo
    is a local git repository and patching get_launchpad_repositories.
    """
    # Create required directories under root
    root = tmp_path
    packaging = root / "packaging"
    upstream = root / "upstream"
    tarballs = root / "tarballs"
    logs = root / "logs"
    packaging.mkdir()
    upstream.mkdir()
    tarballs.mkdir()
    logs.mkdir()

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
    _sub.run(["git", "-c", "commit.gpgsign=false", "-c", "user.name=Test User", "-c", "user.email=test@example.com", "commit", "-m", "init"], cwd=str(releases), check=True)
    # Ensure a 'master' branch exists and is checked out for tests expecting it
    _sub.run(["git", "branch", "master"], cwd=str(releases), check=True)
    # Create a bare mirror of the releases repo and push to it so fetch/pull works
    bare_releases = upstream / "releases.git"
    bare_releases.mkdir()
    _sub.run(["git", "init", "--bare"], cwd=str(bare_releases), check=True)
    _sub.run(["git", "remote", "add", "origin", f"file://{bare_releases}"], cwd=str(releases), check=True)
    _sub.run(["git", "push", "origin", "master"], cwd=str(releases), check=True)

    # Patch setup_releases_repo so CLI doesn't try to fetch/pull from remotes
    from unittest.mock import patch as _patch
    with _patch("packastack.cmds.import_tarballs.setup_releases_repo", return_value=releases):
        runner = CliRunner()
        result = runner.invoke(cli, ["--root", str(root), "import"])
    assert result.exit_code == 0

    # Basic assertions that logs directory exists and CLI log file is present
    cli_files = list(logs.glob("packastack-*.log"))
    assert len(cli_files) >= 1
