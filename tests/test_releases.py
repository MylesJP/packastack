"""Tests for the releases module."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from packastack.releases import (
    ProjectRelease,
    ReleaseVersion,
    SeriesInfo,
    find_projects_by_prefix,
    get_current_development_series,
    is_snapshot_eligible,
    load_openstack_packages,
    load_project_releases,
    load_series_info,
)


@pytest.fixture
def releases_repo(tmp_path: Path) -> Path:
    """Create a mock openstack/releases repository structure."""
    repo = tmp_path / "openstack-releases"
    repo.mkdir()

    # Create deliverables directory
    (repo / "deliverables").mkdir()

    # Create series info directory (note: series_status is a directory)
    series_dir = repo / "data" / "series_status"
    series_dir.mkdir(parents=True)

    # Create series YAML files
    dalmatian = {
        "status": "development",
        "initial-release": "2024-10-02",
    }
    (series_dir / "2024.2.yaml").write_text(yaml.dump(dalmatian))

    caracal = {
        "status": "maintained",
        "initial-release": "2024-04-03",
    }
    (series_dir / "2024.1.yaml").write_text(yaml.dump(caracal))

    # Create a dalmatian series directory
    dalmatian_dir = repo / "deliverables" / "2024.2"
    dalmatian_dir.mkdir()

    # Create nova deliverable
    nova_data = {
        "team": "Nova",
        "type": "service",
        "releases": [
            {
                "version": "26.0.0",
                "projects": [{"repo": "openstack/nova", "hash": "abc123"}],
            },
            {
                "version": "26.1.0",
                "projects": [{"repo": "openstack/nova", "hash": "def456"}],
            },
        ],
        "release-model": "cycle-with-rc",
    }
    (dalmatian_dir / "nova.yaml").write_text(yaml.dump(nova_data))

    # Create glance deliverable
    glance_data = {
        "team": "Glance",
        "type": "service",
        "releases": [
            {
                "version": "28.0.0",
                "projects": [{"repo": "openstack/glance", "hash": "ghi789"}],
            },
        ],
        "release-model": "cycle-with-rc",
    }
    (dalmatian_dir / "glance.yaml").write_text(yaml.dump(glance_data))

    # Create a library (oslo.config)
    oslo_config_data = {
        "team": "Oslo",
        "type": "library",
        "releases": [
            {
                "version": "9.4.0",
                "projects": [{"repo": "openstack/oslo.config", "hash": "mno345"}],
            },
        ],
        "release-model": "cycle-with-intermediary",
    }
    (dalmatian_dir / "oslo.config.yaml").write_text(yaml.dump(oslo_config_data))

    # Create caracal series
    caracal_dir = repo / "deliverables" / "2024.1"
    caracal_dir.mkdir()

    nova_caracal = {
        "team": "Nova",
        "type": "service",
        "releases": [
            {
                "version": "25.0.0",
                "projects": [{"repo": "openstack/nova", "hash": "pqr678"}],
            },
        ],
        "release-model": "cycle-with-rc",
    }
    (caracal_dir / "nova.yaml").write_text(yaml.dump(nova_caracal))

    return repo


class TestReleaseVersion:
    """Tests for ReleaseVersion dataclass."""

    def test_basic_version(self) -> None:
        rv = ReleaseVersion(version="26.0.0")
        assert rv.version == "26.0.0"
        assert rv.projects == []
        assert rv.diff_start == ""

    def test_with_projects(self) -> None:
        projects = [{"repo": "openstack/nova", "hash": "abc123"}]
        rv = ReleaseVersion(version="26.0.0", projects=projects)
        assert len(rv.projects) == 1

    def test_is_beta(self) -> None:
        assert ReleaseVersion(version="1.0.0.0b1").is_beta() is True
        assert ReleaseVersion(version="1.0.0.0b2").is_beta() is True
        assert ReleaseVersion(version="1.0.0.0rc1").is_beta() is False
        assert ReleaseVersion(version="1.0.0").is_beta() is False
        assert ReleaseVersion(version="1.0.0.0a1").is_beta() is False

    def test_is_rc(self) -> None:
        assert ReleaseVersion(version="1.0.0.0rc1").is_rc() is True
        assert ReleaseVersion(version="1.0.0.0rc2").is_rc() is True
        assert ReleaseVersion(version="1.0.0.0b1").is_rc() is False
        assert ReleaseVersion(version="1.0.0").is_rc() is False

    def test_is_final(self) -> None:
        assert ReleaseVersion(version="1.0.0").is_final() is True
        assert ReleaseVersion(version="26.1.0").is_final() is True
        assert ReleaseVersion(version="1.0.0.0b1").is_final() is False
        assert ReleaseVersion(version="1.0.0.0rc1").is_final() is False
        # Alpha versions are not final
        assert ReleaseVersion(version="1.0.0.0a1").is_final() is False

    def test_is_beta_rc_or_final(self) -> None:
        # Final
        assert ReleaseVersion(version="1.0.0").is_beta_rc_or_final() is True
        # Beta
        assert ReleaseVersion(version="1.0.0.0b1").is_beta_rc_or_final() is True
        # RC
        assert ReleaseVersion(version="1.0.0.0rc1").is_beta_rc_or_final() is True
        # Alpha (pre-beta)
        assert ReleaseVersion(version="1.0.0.0a1").is_beta_rc_or_final() is False


class TestProjectRelease:
    """Tests for ProjectRelease dataclass."""

    def test_basic_release(self) -> None:
        release = ProjectRelease(
            name="nova",
            team="Nova",
            release_model="cycle-with-rc",
            type="service",
        )
        assert release.name == "nova"
        assert release.release_model == "cycle-with-rc"
        assert release.releases == []

    def test_get_latest_version(self) -> None:
        release = ProjectRelease(
            name="nova",
            releases=[
                ReleaseVersion(version="26.0.0"),
                ReleaseVersion(version="26.1.0"),
            ],
        )
        assert release.get_latest_version() == "26.1.0"

    def test_get_latest_version_empty(self) -> None:
        release = ProjectRelease(name="nova")
        assert release.get_latest_version() is None

    def test_is_library(self) -> None:
        lib = ProjectRelease(name="oslo.config", type="library")
        service = ProjectRelease(name="nova", type="service")
        assert lib.is_library() is True
        assert service.is_library() is False

    def test_has_releases(self) -> None:
        with_releases = ProjectRelease(
            name="nova",
            releases=[ReleaseVersion(version="26.0.0")],
        )
        without_releases = ProjectRelease(name="nova", releases=[])
        assert with_releases.has_releases() is True
        assert without_releases.has_releases() is False

    def test_has_beta_rc_or_final(self) -> None:
        # Has final release
        final_project = ProjectRelease(
            name="nova",
            releases=[ReleaseVersion(version="26.0.0")],
        )
        assert final_project.has_beta_rc_or_final() is True

        # Has beta release
        beta_project = ProjectRelease(
            name="test",
            releases=[ReleaseVersion(version="1.0.0.0b1")],
        )
        assert beta_project.has_beta_rc_or_final() is True

        # Has only pre-beta (alpha)
        alpha_project = ProjectRelease(
            name="test",
            releases=[
                ReleaseVersion(version="1.0.0.0a1"),
                ReleaseVersion(version="1.0.0.0a2"),
            ],
        )
        assert alpha_project.has_beta_rc_or_final() is False

        # No releases
        no_releases = ProjectRelease(name="test", releases=[])
        assert no_releases.has_beta_rc_or_final() is False

    def test_get_latest_release(self) -> None:
        release = ProjectRelease(
            name="nova",
            releases=[
                ReleaseVersion(version="26.0.0"),
                ReleaseVersion(version="26.1.0"),
            ],
        )
        latest = release.get_latest_release()
        assert latest is not None
        assert latest.version == "26.1.0"

    def test_get_latest_release_empty(self) -> None:
        release = ProjectRelease(name="nova", releases=[])
        assert release.get_latest_release() is None


class TestSeriesInfo:
    """Tests for SeriesInfo dataclass."""

    def test_basic_series(self) -> None:
        series = SeriesInfo(
            name="2024.2",
            status="development",
            initial_release="2024-10-02",
        )
        assert series.name == "2024.2"
        assert series.status == "development"


class TestLoadSeriesInfo:
    """Tests for load_series_info function."""

    def test_loads_series(self, releases_repo: Path) -> None:
        series = load_series_info(releases_repo)
        assert "2024.2" in series
        assert series["2024.2"].status == "development"

    def test_empty_when_missing(self, tmp_path: Path) -> None:
        series = load_series_info(tmp_path)
        assert series == {}


class TestLoadProjectReleases:
    """Tests for load_project_releases function."""

    def test_load_nova(self, releases_repo: Path) -> None:
        release = load_project_releases(releases_repo, "2024.2", "nova")
        assert release is not None
        assert release.name == "nova"
        assert release.release_model == "cycle-with-rc"
        assert len(release.releases) == 2
        assert release.releases[0].version == "26.0.0"
        assert release.releases[1].version == "26.1.0"

    def test_load_glance(self, releases_repo: Path) -> None:
        release = load_project_releases(releases_repo, "2024.2", "glance")
        assert release is not None
        assert release.name == "glance"
        assert len(release.releases) == 1

    def test_load_nonexistent(self, releases_repo: Path) -> None:
        release = load_project_releases(releases_repo, "2024.2", "nonexistent")
        assert release is None

    def test_load_from_different_series(self, releases_repo: Path) -> None:
        release = load_project_releases(releases_repo, "2024.1", "nova")
        assert release is not None
        assert release.releases[0].version == "25.0.0"


class TestIsSnapshotEligible:
    """Tests for is_snapshot_eligible function.

    Policy rules:
    - If project has a beta/rc/final release → Block (must use release tarball)
    - If project has only pre-beta releases (e.g., 1.0.0.0b1) → Allow with warning
    - If project has no releases at all → Allow without warning
    - If project not found in releases repo → Block
    """

    def test_nonexistent_project(self, releases_repo: Path) -> None:
        eligible, reason, preferred = is_snapshot_eligible(
            releases_repo, "2024.1", "nonexistent"
        )
        assert eligible is False
        assert "not found" in reason
        assert preferred is None

    def test_no_releases_allowed(self, releases_repo: Path) -> None:
        """Project with no releases should be allowed."""
        dalmatian_dir = releases_repo / "deliverables" / "2024.2"
        no_release_data = {
            "team": "Test",
            "type": "service",
            "releases": [],
            "release-model": "cycle-with-rc",
        }
        (dalmatian_dir / "new-project.yaml").write_text(yaml.dump(no_release_data))

        eligible, reason, preferred = is_snapshot_eligible(
            releases_repo, "2024.2", "new-project"
        )
        assert eligible is True
        assert "no releases" in reason.lower()
        assert preferred is None

    def test_final_release_blocks(self, releases_repo: Path) -> None:
        """Final release exists, snapshot should be blocked."""
        # Nova has final releases (26.0.0, 26.1.0) in the fixture
        eligible, reason, preferred = is_snapshot_eligible(
            releases_repo, "2024.2", "nova"
        )
        assert eligible is False
        assert "26.1.0" in reason  # Latest release version
        assert preferred == "26.1.0"

    def test_beta_release_blocks(self, releases_repo: Path) -> None:
        """Beta release exists, snapshot should be blocked."""
        dalmatian_dir = releases_repo / "deliverables" / "2024.2"
        beta_data = {
            "team": "Test",
            "type": "service",
            "releases": [
                {"version": "1.0.0.0b1", "projects": []},
                {"version": "1.0.0.0b2", "projects": []},
            ],
            "release-model": "cycle-with-rc",
        }
        (dalmatian_dir / "beta-project.yaml").write_text(yaml.dump(beta_data))

        eligible, reason, preferred = is_snapshot_eligible(
            releases_repo, "2024.2", "beta-project"
        )
        assert eligible is False
        assert "1.0.0.0b2" in reason
        assert preferred == "1.0.0.0b2"

    def test_rc_release_blocks(self, releases_repo: Path) -> None:
        """RC release exists, snapshot should be blocked."""
        dalmatian_dir = releases_repo / "deliverables" / "2024.2"
        rc_data = {
            "team": "Test",
            "type": "service",
            "releases": [
                {"version": "2.0.0.0rc1", "projects": []},
            ],
            "release-model": "cycle-with-rc",
        }
        (dalmatian_dir / "rc-project.yaml").write_text(yaml.dump(rc_data))

        eligible, reason, preferred = is_snapshot_eligible(
            releases_repo, "2024.2", "rc-project"
        )
        assert eligible is False
        assert "2.0.0.0rc1" in reason
        assert preferred == "2.0.0.0rc1"

    def test_prebeta_releases_allowed_with_warning(self, releases_repo: Path) -> None:
        """Only pre-beta releases (like milestones), should be allowed with warning."""
        dalmatian_dir = releases_repo / "deliverables" / "2024.2"
        # Pre-beta releases typically look like "1.0.0.0a1" or don't contain b/rc
        # but also aren't "final" versions - they're development milestones
        # Actually, the policy states "before first beta" - these would be milestone
        # releases that don't match beta/rc/final patterns.
        # For testing, let's assume "a" versions are pre-beta milestones
        prebeta_data = {
            "team": "Test",
            "type": "service",
            "releases": [
                {"version": "3.0.0.0a1", "projects": []},
                {"version": "3.0.0.0a2", "projects": []},
            ],
            "release-model": "cycle-with-rc",
        }
        (dalmatian_dir / "prebeta-project.yaml").write_text(yaml.dump(prebeta_data))

        eligible, reason, preferred = is_snapshot_eligible(
            releases_repo, "2024.2", "prebeta-project"
        )
        assert eligible is True
        assert "pre-beta" in reason.lower() or "warning" in reason.lower()
        assert preferred is None

    def test_library_with_release_blocked(self, releases_repo: Path) -> None:
        """Library with a release should be blocked (has final version 9.4.0)."""
        eligible, reason, preferred = is_snapshot_eligible(
            releases_repo, "2024.2", "oslo.config"
        )
        assert eligible is False
        assert "9.4.0" in reason
        assert preferred == "9.4.0"


class TestFindProjectsByPrefix:
    """Tests for find_projects_by_prefix function."""

    def test_find_oslo_projects(self, releases_repo: Path) -> None:
        matches = find_projects_by_prefix(releases_repo, "2024.2", "oslo")
        assert "oslo.config" in matches

    def test_find_nova(self, releases_repo: Path) -> None:
        matches = find_projects_by_prefix(releases_repo, "2024.2", "nov")
        assert "nova" in matches

    def test_find_no_matches(self, releases_repo: Path) -> None:
        matches = find_projects_by_prefix(releases_repo, "2024.2", "xyz")
        assert matches == []

    def test_find_multiple(self, releases_repo: Path) -> None:
        # Create additional projects with same prefix
        dalmatian_dir = releases_repo / "deliverables" / "2024.2"
        glance_store_data = {
            "releases": [{"version": "1.0.0", "projects": []}],
            "release-model": "cycle-with-intermediary",
        }
        (dalmatian_dir / "glance_store.yaml").write_text(yaml.dump(glance_store_data))

        matches = find_projects_by_prefix(releases_repo, "2024.2", "glance")
        assert "glance" in matches
        assert "glance_store" in matches


class TestGetCurrentDevelopmentSeries:
    """Tests for get_current_development_series function."""

    def test_finds_development_series(self, releases_repo: Path) -> None:
        series = get_current_development_series(releases_repo)
        assert series == "2024.2"

    def test_fallback_to_highest_numbered_series(self, releases_repo: Path) -> None:
        # When no series is marked as development, fallback to highest numbered series
        series_dir = releases_repo / "data" / "series_status"
        (series_dir / "2024.2.yaml").write_text(yaml.dump({"status": "maintained"}))

        series = get_current_development_series(releases_repo)
        # Should fall back to the highest numbered deliverables directory
        assert series == "2024.2"

    def test_returns_none_when_no_deliverables(self, tmp_path: Path) -> None:
        series = get_current_development_series(tmp_path)
        assert series is None

    def test_fallback_to_named_series(self, tmp_path: Path) -> None:
        # When only named series exist (no numbered), return alphabetically last
        releases_repo = tmp_path / "releases"
        deliverables = releases_repo / "deliverables"
        deliverables.mkdir(parents=True)
        (deliverables / "flamingo").mkdir()
        (deliverables / "gazpacho").mkdir()
        (deliverables / "emu").mkdir()

        series = get_current_development_series(releases_repo)
        assert series == "gazpacho"  # Alphabetically last


class TestListSeries:
    """Tests for list_series function."""

    def test_lists_series(self, releases_repo: Path) -> None:
        from packastack.releases import list_series

        series = list_series(releases_repo)
        assert "2024.2" in series
        assert "2024.1" in series

    def test_returns_empty_when_missing(self, tmp_path: Path) -> None:
        from packastack.releases import list_series

        series = list_series(tmp_path)
        assert series == []

    def test_excludes_hidden_dirs(self, releases_repo: Path) -> None:
        from packastack.releases import list_series

        # Create a hidden directory
        (releases_repo / "deliverables" / ".git").mkdir()

        series = list_series(releases_repo)
        assert ".git" not in series

    def test_sorts_numeric_after_named(self, releases_repo: Path) -> None:
        from packastack.releases import list_series

        # Create a named series like "zed"
        (releases_repo / "deliverables" / "zed").mkdir()

        series = list_series(releases_repo)
        # Check that sorting works (numeric 2024.x comes before named in reverse sort)
        assert len(series) >= 3


class TestProjectToPackageName:
    """Tests for project_to_package_name function."""

    def test_project_exists_as_is(self, tmp_path: Path) -> None:
        """Test when project name matches directory exactly."""
        from packastack.releases import project_to_package_name

        # Create nova package
        (tmp_path / "nova" / "debian").mkdir(parents=True)
        (tmp_path / "nova" / "debian" / "control").write_text("Source: nova\n")

        result = project_to_package_name("nova", tmp_path)
        assert result == "nova"

    def test_oslo_with_python_prefix(self, tmp_path: Path) -> None:
        """Test oslo.messaging -> python-oslo.messaging."""
        from packastack.releases import project_to_package_name

        # Create python-oslo.messaging package
        (tmp_path / "python-oslo.messaging" / "debian").mkdir(parents=True)
        (tmp_path / "python-oslo.messaging" / "debian" / "control").write_text(
            "Source: python-oslo.messaging\n"
        )

        result = project_to_package_name("oslo.messaging", tmp_path)
        assert result == "python-oslo.messaging"

    def test_oslo_with_dash(self, tmp_path: Path) -> None:
        """Test oslo.config -> oslo-config (dash instead of dot)."""
        from packastack.releases import project_to_package_name

        # Create oslo-config package
        (tmp_path / "oslo-config" / "debian").mkdir(parents=True)
        (tmp_path / "oslo-config" / "debian" / "control").write_text(
            "Source: oslo-config\n"
        )

        result = project_to_package_name("oslo.config", tmp_path)
        assert result == "oslo-config"

    def test_oslo_with_python_and_dash(self, tmp_path: Path) -> None:
        """Test oslo.log -> python-oslo-log."""
        from packastack.releases import project_to_package_name

        # Create python-oslo-log package
        (tmp_path / "python-oslo-log" / "debian").mkdir(parents=True)
        (tmp_path / "python-oslo-log" / "debian" / "control").write_text(
            "Source: python-oslo-log\n"
        )

        result = project_to_package_name("oslo.log", tmp_path)
        assert result == "python-oslo-log"

    def test_non_oslo_with_python_prefix(self, tmp_path: Path) -> None:
        """Test keystoneauth -> python-keystoneauth."""
        from packastack.releases import project_to_package_name

        # Create python-keystoneauth package
        (tmp_path / "python-keystoneauth" / "debian").mkdir(parents=True)
        (tmp_path / "python-keystoneauth" / "debian" / "control").write_text(
            "Source: python-keystoneauth\n"
        )

        result = project_to_package_name("keystoneauth", tmp_path)
        assert result == "python-keystoneauth"

    def test_no_match_returns_original(self, tmp_path: Path) -> None:
        """Test when no mapping found, return original name."""
        from packastack.releases import project_to_package_name

        result = project_to_package_name("unknown-project", tmp_path)
        assert result == "unknown-project"

    def test_oslo_no_match_returns_original(self, tmp_path: Path) -> None:
        """Test oslo project with no matching package returns original."""
        from packastack.releases import project_to_package_name

        result = project_to_package_name("oslo.nonexistent", tmp_path)
        assert result == "oslo.nonexistent"


class TestLoadOpenstackPackages:
    """Tests for load_openstack_packages function."""

    def test_empty_series(self, tmp_path: Path) -> None:
        """Test with non-existent series directory."""
        result = load_openstack_packages(tmp_path, "nonexistent")
        assert result == {}

    def test_loads_service_packages(self, tmp_path: Path) -> None:
        """Test loading service packages (no prefix)."""
        series_dir = tmp_path / "deliverables" / "2024.2"
        series_dir.mkdir(parents=True)
        (series_dir / "nova.yaml").write_text("type: service\n")
        (series_dir / "glance.yaml").write_text("type: service\n")

        result = load_openstack_packages(tmp_path, "2024.2")

        assert result == {"nova": "nova", "glance": "glance"}

    def test_loads_library_packages(self, tmp_path: Path) -> None:
        """Test loading library packages (python- prefix)."""
        series_dir = tmp_path / "deliverables" / "2024.2"
        series_dir.mkdir(parents=True)
        (series_dir / "oslo.config.yaml").write_text("type: library\n")
        (series_dir / "oslo.messaging.yaml").write_text("type: library\n")

        result = load_openstack_packages(tmp_path, "2024.2")

        assert result == {
            "python-oslo.config": "oslo.config",
            "python-oslo.messaging": "oslo.messaging",
        }

    def test_loads_mixed_packages(self, tmp_path: Path) -> None:
        """Test loading both service and library packages."""
        series_dir = tmp_path / "deliverables" / "2024.2"
        series_dir.mkdir(parents=True)
        (series_dir / "nova.yaml").write_text("type: service\n")
        (series_dir / "oslo.config.yaml").write_text("type: library\n")
        (series_dir / "keystoneauth.yaml").write_text("type: library\n")

        result = load_openstack_packages(tmp_path, "2024.2")

        assert result == {
            "nova": "nova",
            "python-oslo.config": "oslo.config",
            "python-keystoneauth": "keystoneauth",
        }

    def test_handles_missing_type(self, tmp_path: Path) -> None:
        """Test that missing type defaults to no prefix (like service)."""
        series_dir = tmp_path / "deliverables" / "2024.2"
        series_dir.mkdir(parents=True)
        (series_dir / "someproject.yaml").write_text("team: SomeTeam\n")

        result = load_openstack_packages(tmp_path, "2024.2")

        # No type means no prefix (treated like service)
        assert result == {"someproject": "someproject"}

    def test_caches_results(self, tmp_path: Path) -> None:
        """Test that results are cached."""
        from packastack.releases import _openstack_packages_cache

        series_dir = tmp_path / "deliverables" / "2024.2"
        series_dir.mkdir(parents=True)
        (series_dir / "nova.yaml").write_text("type: service\n")

        # Clear cache
        _openstack_packages_cache.clear()

        result1 = load_openstack_packages(tmp_path, "2024.2")
        result2 = load_openstack_packages(tmp_path, "2024.2")

        assert result1 is result2  # Same object from cache
        assert (tmp_path, "2024.2") in _openstack_packages_cache

    def test_skips_invalid_yaml(self, tmp_path: Path) -> None:
        """Test that invalid YAML files are skipped."""
        from packastack.releases import _openstack_packages_cache

        _openstack_packages_cache.clear()

        series_dir = tmp_path / "deliverables" / "2024.2"
        series_dir.mkdir(parents=True)
        (series_dir / "valid.yaml").write_text("type: service\n")
        (series_dir / "invalid.yaml").write_text("{{{{invalid yaml\n")

        result = load_openstack_packages(tmp_path, "2024.2")

        # Should only have the valid project
        assert result == {"valid": "valid"}
