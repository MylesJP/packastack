# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for packastack.target.distro_info module."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from textwrap import dedent

import pytest

from packastack.target.distro_info import (
    UbuntuRelease,
    get_all_lts_releases,
    get_base_lts_for_mode,
    get_current_lts,
    get_lts_by_codename,
    get_lts_codename_for_mode,
    get_previous_lts,
    get_release_by_codename,
    get_released_lts_releases,
    get_supported_lts_releases,
    is_lts_codename,
    load_ubuntu_releases,
)

# Sample CSV content for testing
SAMPLE_CSV = dedent("""\
    version,codename,series,created,release,eol,eol-server
    20.04 LTS,focal,focal,2019-10-17,2020-04-23,2025-04-23,2030-04-23
    22.04 LTS,jammy,jammy,2021-10-14,2022-04-21,2027-04-21,2032-04-21
    23.10,mantic,mantic,2023-04-20,2023-10-12,2024-07-11,
    24.04 LTS,noble,noble,2023-10-19,2024-04-25,2029-04-25,2034-04-25
    24.10,oracular,oracular,2024-04-25,2024-10-10,2025-07-10,
    25.04,plucky,plucky,2024-10-24,2025-04-17,2026-01-15,
    25.10,questing,questing,2025-04-24,,2026-07-16,
""")


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    """Create a sample ubuntu.csv file for testing."""
    csv_path = tmp_path / "ubuntu.csv"
    csv_path.write_text(SAMPLE_CSV)
    return csv_path


class TestUbuntuRelease:
    """Tests for UbuntuRelease dataclass."""

    def test_is_released_true(self) -> None:
        """Test is_released returns True for past release dates."""
        release = UbuntuRelease(
            version="24.04",
            codename="noble",
            series="noble",
            is_lts=True,
            release_date=date(2024, 4, 25),
        )
        assert release.is_released is True

    def test_is_released_false_no_date(self) -> None:
        """Test is_released returns False when no release date."""
        release = UbuntuRelease(
            version="25.10",
            codename="questing",
            series="questing",
            is_lts=False,
            release_date=None,
        )
        assert release.is_released is False

    def test_is_supported_true(self) -> None:
        """Test is_supported for a supported release."""
        release = UbuntuRelease(
            version="24.04",
            codename="noble",
            series="noble",
            is_lts=True,
            release_date=date(2024, 4, 25),
            eol_server_date=date(2034, 4, 25),
        )
        assert release.is_supported is True

    def test_is_supported_false_eol(self) -> None:
        """Test is_supported for an EOL release."""
        release = UbuntuRelease(
            version="23.10",
            codename="mantic",
            series="mantic",
            is_lts=False,
            release_date=date(2023, 10, 12),
            eol_date=date(2024, 7, 11),
        )
        assert release.is_supported is False


class TestLoadUbuntuReleases:
    """Tests for load_ubuntu_releases function."""

    def test_loads_all_releases(self, csv_file: Path) -> None:
        """Test loading all releases from CSV."""
        # Clear cache to use test file
        load_ubuntu_releases.cache_clear()
        releases = load_ubuntu_releases(csv_file)

        assert len(releases) == 7
        codenames = [r.codename for r in releases]
        assert "focal" in codenames
        assert "jammy" in codenames
        assert "noble" in codenames
        assert "oracular" in codenames

    def test_identifies_lts_releases(self, csv_file: Path) -> None:
        """Test that LTS releases are correctly identified."""
        load_ubuntu_releases.cache_clear()
        releases = load_ubuntu_releases(csv_file)

        lts_codenames = {r.codename for r in releases if r.is_lts}
        assert lts_codenames == {"focal", "jammy", "noble"}

    def test_parses_dates(self, csv_file: Path) -> None:
        """Test that dates are correctly parsed."""
        load_ubuntu_releases.cache_clear()
        releases = load_ubuntu_releases(csv_file)

        noble = next(r for r in releases if r.codename == "noble")
        assert noble.release_date == date(2024, 4, 25)
        assert noble.eol_date == date(2029, 4, 25)
        assert noble.eol_server_date == date(2034, 4, 25)

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """Test that missing file returns empty list."""
        load_ubuntu_releases.cache_clear()
        releases = load_ubuntu_releases(tmp_path / "nonexistent.csv")
        assert releases == []

    def test_sorted_by_release_date(self, csv_file: Path) -> None:
        """Test releases are sorted by release date."""
        load_ubuntu_releases.cache_clear()
        releases = load_ubuntu_releases(csv_file)

        # Filter to released only (have dates)
        released = [r for r in releases if r.release_date]
        for i in range(len(released) - 1):
            assert released[i].release_date <= released[i + 1].release_date


class TestGetAllLtsReleases:
    """Tests for get_all_lts_releases function."""

    def test_returns_only_lts(self, csv_file: Path) -> None:
        """Test that only LTS releases are returned."""
        load_ubuntu_releases.cache_clear()
        lts = get_all_lts_releases(csv_file)

        assert len(lts) == 3
        for release in lts:
            assert release.is_lts is True
            assert release.codename in {"focal", "jammy", "noble"}


class TestGetReleasedLtsReleases:
    """Tests for get_released_lts_releases function."""

    def test_returns_only_released_lts(self, csv_file: Path) -> None:
        """Test that only released LTS releases are returned."""
        load_ubuntu_releases.cache_clear()
        released = get_released_lts_releases(csv_file)

        # All three LTS in sample are released (as of test date 2026-01-01)
        assert len(released) == 3
        for release in released:
            assert release.is_lts is True
            assert release.is_released is True


class TestGetSupportedLtsReleases:
    """Tests for get_supported_lts_releases function."""

    def test_returns_supported_lts(self, csv_file: Path) -> None:
        """Test that only supported LTS releases are returned."""
        load_ubuntu_releases.cache_clear()
        supported = get_supported_lts_releases(csv_file)

        # As of 2026-01-01, focal, jammy, noble are all still supported
        assert len(supported) == 3


class TestGetCurrentLts:
    """Tests for get_current_lts function."""

    def test_returns_most_recent_lts(self, csv_file: Path) -> None:
        """Test that the most recent released LTS is returned."""
        load_ubuntu_releases.cache_clear()
        current = get_current_lts(csv_file)

        assert current is not None
        assert current.codename == "noble"
        assert current.version == "24.04"
        assert current.is_lts is True

    def test_returns_none_when_no_lts(self, tmp_path: Path) -> None:
        """Test returns None when no LTS releases exist."""
        csv_path = tmp_path / "ubuntu.csv"
        csv_path.write_text("version,codename,series,created,release,eol,eol-server\n")
        csv_path.write_text(
            csv_path.read_text() + "24.10,oracular,oracular,2024-04-25,2024-10-10,2025-07-10,\n"
        )

        load_ubuntu_releases.cache_clear()
        current = get_current_lts(csv_path)
        assert current is None


class TestGetPreviousLts:
    """Tests for get_previous_lts function."""

    def test_returns_second_most_recent_lts(self, csv_file: Path) -> None:
        """Test that the previous LTS is returned."""
        load_ubuntu_releases.cache_clear()
        previous = get_previous_lts(csv_file)

        assert previous is not None
        assert previous.codename == "jammy"
        assert previous.version == "22.04"
        assert previous.is_lts is True

    def test_returns_none_when_only_one_lts(self, tmp_path: Path) -> None:
        """Test returns None when only one LTS release exists."""
        csv_path = tmp_path / "ubuntu.csv"
        csv_path.write_text(
            "version,codename,series,created,release,eol,eol-server\n"
            "24.04 LTS,noble,noble,2023-10-19,2024-04-25,2029-04-25,2034-04-25\n"
        )

        load_ubuntu_releases.cache_clear()
        previous = get_previous_lts(csv_path)
        assert previous is None


class TestGetLtsByCodename:
    """Tests for get_lts_by_codename function."""

    def test_finds_lts_by_codename(self, csv_file: Path) -> None:
        """Test finding an LTS release by codename."""
        load_ubuntu_releases.cache_clear()
        jammy = get_lts_by_codename("jammy", csv_file)

        assert jammy is not None
        assert jammy.codename == "jammy"
        assert jammy.version == "22.04"
        assert jammy.is_lts is True

    def test_returns_none_for_non_lts(self, csv_file: Path) -> None:
        """Test returns None for non-LTS codename."""
        load_ubuntu_releases.cache_clear()
        oracular = get_lts_by_codename("oracular", csv_file)
        assert oracular is None

    def test_returns_none_for_unknown(self, csv_file: Path) -> None:
        """Test returns None for unknown codename."""
        load_ubuntu_releases.cache_clear()
        result = get_lts_by_codename("nonexistent", csv_file)
        assert result is None


class TestGetReleaseByCodename:
    """Tests for get_release_by_codename function."""

    def test_finds_any_release(self, csv_file: Path) -> None:
        """Test finding any release by codename."""
        load_ubuntu_releases.cache_clear()
        oracular = get_release_by_codename("oracular", csv_file)

        assert oracular is not None
        assert oracular.codename == "oracular"
        assert oracular.version == "24.10"
        assert oracular.is_lts is False

    def test_returns_none_for_unknown(self, csv_file: Path) -> None:
        """Test returns None for unknown codename."""
        load_ubuntu_releases.cache_clear()
        result = get_release_by_codename("nonexistent", csv_file)
        assert result is None


class TestIsLtsCodename:
    """Tests for is_lts_codename function."""

    def test_true_for_lts(self, csv_file: Path) -> None:
        """Test returns True for LTS codenames."""
        load_ubuntu_releases.cache_clear()
        assert is_lts_codename("noble", csv_file) is True
        assert is_lts_codename("jammy", csv_file) is True
        assert is_lts_codename("focal", csv_file) is True

    def test_false_for_non_lts(self, csv_file: Path) -> None:
        """Test returns False for non-LTS codenames."""
        load_ubuntu_releases.cache_clear()
        assert is_lts_codename("oracular", csv_file) is False
        assert is_lts_codename("mantic", csv_file) is False

    def test_false_for_unknown(self, csv_file: Path) -> None:
        """Test returns False for unknown codenames."""
        load_ubuntu_releases.cache_clear()
        assert is_lts_codename("nonexistent", csv_file) is False


class TestGetBaseLtsForMode:
    """Tests for get_base_lts_for_mode function."""

    def test_cloud_archive_returns_previous_lts(self, csv_file: Path) -> None:
        """Test Cloud Archive mode returns previous LTS."""
        load_ubuntu_releases.cache_clear()
        result = get_base_lts_for_mode(is_cloud_archive=True, csv_path=csv_file)
        assert result is not None
        # Previous LTS should be jammy (22.04)
        assert result.codename == "jammy"

    def test_devel_returns_current_lts(self, csv_file: Path) -> None:
        """Test devel mode returns current LTS."""
        load_ubuntu_releases.cache_clear()
        result = get_base_lts_for_mode(is_cloud_archive=False, csv_path=csv_file)
        assert result is not None
        # Current LTS should be noble (24.04)
        assert result.codename == "noble"


class TestGetLtsCodenameForMode:
    """Tests for get_lts_codename_for_mode function."""

    def test_cloud_archive_returns_previous_codename(self, csv_file: Path) -> None:
        """Test Cloud Archive mode returns previous LTS codename."""
        load_ubuntu_releases.cache_clear()
        result = get_lts_codename_for_mode(is_cloud_archive=True, csv_path=csv_file)
        assert result == "jammy"

    def test_devel_returns_current_codename(self, csv_file: Path) -> None:
        """Test devel mode returns current LTS codename."""
        load_ubuntu_releases.cache_clear()
        result = get_lts_codename_for_mode(is_cloud_archive=False, csv_path=csv_file)
        assert result == "noble"

    def test_empty_when_no_lts_found(self, tmp_path: Path) -> None:
        """Test returns empty string when no LTS found."""
        load_ubuntu_releases.cache_clear()
        # Create empty CSV
        csv_path = tmp_path / "empty.csv"
        csv_path.write_text("version,codename,series,created,release,eol,eol-server\n")
        result = get_lts_codename_for_mode(is_cloud_archive=False, csv_path=csv_path)
        assert result == ""
