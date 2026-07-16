# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for unshare chroot-mode helpers."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from packastack.build import unshare
from packastack.build.schroot import SchrootConfig
from packastack.build.unshare import (
    ChrootModeError,
    UnshareResult,
    _create_unshare_tarball,
    _has_subid_entry,
    check_unshare_prerequisites,
    ensure_unshare_tarball,
    find_unshare_tarball,
    get_sbuild_version,
    get_unshare_cache_dir,
    get_unshare_tarball_path,
    resolve_chroot_mode,
    sbuild_defaults_to_unshare,
)


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch) -> Path:
    """Point the sbuild unshare cache at a temp directory."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path / "sbuild"


def make_config(**overrides) -> SchrootConfig:
    """Build a SchrootConfig with sensible test defaults."""
    values = {
        "series": "noble",
        "arch": "amd64",
        "mirror": "http://archive.ubuntu.com/ubuntu",
        "components": ["main", "universe"],
    }
    values.update(overrides)
    return SchrootConfig.from_lists(**values)


class TestCacheDir:
    """Tests for unshare cache directory resolution."""

    def test_respects_xdg_cache_home(self, cache_dir: Path) -> None:
        assert get_unshare_cache_dir() == cache_dir

    def test_defaults_to_home_cache(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        with patch("packastack.build.unshare.Path.home", return_value=tmp_path):
            assert get_unshare_cache_dir() == tmp_path / ".cache" / "sbuild"

    def test_tarball_path(self, cache_dir: Path) -> None:
        assert get_unshare_tarball_path("noble", "amd64") == cache_dir / "noble-amd64.tar.zst"


class TestFindUnshareTarball:
    """Tests for find_unshare_tarball."""

    def test_none_when_cache_dir_missing(self, cache_dir: Path) -> None:
        assert find_unshare_tarball("noble", "amd64") is None

    def test_none_when_no_match(self, cache_dir: Path) -> None:
        cache_dir.mkdir(parents=True)
        (cache_dir / "jammy-amd64.tar.zst").write_text("tar")
        assert find_unshare_tarball("noble", "amd64") is None

    def test_finds_tarball(self, cache_dir: Path) -> None:
        cache_dir.mkdir(parents=True)
        tarball = cache_dir / "noble-amd64.tar.zst"
        tarball.write_text("tar")
        assert find_unshare_tarball("noble", "amd64") == tarball

    def test_matches_any_compression_suffix(self, cache_dir: Path) -> None:
        cache_dir.mkdir(parents=True)
        tarball = cache_dir / "noble-amd64.tar.xz"
        tarball.write_text("tar")
        assert find_unshare_tarball("noble", "amd64") == tarball

    def test_ignores_directories(self, cache_dir: Path) -> None:
        cache_dir.mkdir(parents=True)
        (cache_dir / "noble-amd64.tar.zst").mkdir()
        assert find_unshare_tarball("noble", "amd64") is None


class TestGetSbuildVersion:
    """Tests for sbuild version detection."""

    def test_none_when_sbuild_missing(self) -> None:
        with patch("packastack.build.unshare.shutil.which", return_value=None):
            assert get_sbuild_version() is None

    def test_none_on_nonzero_exit(self) -> None:
        result = MagicMock(returncode=1, stdout="")
        with (
            patch("packastack.build.unshare.shutil.which", return_value="/usr/bin/sbuild"),
            patch("packastack.build.unshare.subprocess.run", return_value=result),
        ):
            assert get_sbuild_version() is None

    def test_none_when_unparseable(self) -> None:
        result = MagicMock(returncode=0, stdout="sbuild (Debian sbuild) unknown\n")
        with (
            patch("packastack.build.unshare.shutil.which", return_value="/usr/bin/sbuild"),
            patch("packastack.build.unshare.subprocess.run", return_value=result),
        ):
            assert get_sbuild_version() is None

    def test_parses_full_version(self) -> None:
        result = MagicMock(
            returncode=0,
            stdout="sbuild (Debian sbuild) 0.88.3ubuntu2~bpo24.04.1 (17 June 2025)\n",
        )
        with (
            patch("packastack.build.unshare.shutil.which", return_value="/usr/bin/sbuild"),
            patch("packastack.build.unshare.subprocess.run", return_value=result),
        ):
            assert get_sbuild_version() == (0, 88, 3)

    def test_parses_two_part_version(self) -> None:
        result = MagicMock(returncode=0, stdout="sbuild 1.0\n")
        with (
            patch("packastack.build.unshare.shutil.which", return_value="/usr/bin/sbuild"),
            patch("packastack.build.unshare.subprocess.run", return_value=result),
        ):
            assert get_sbuild_version() == (1, 0)


class TestSbuildDefaultsToUnshare:
    """Tests for the unshare-default version check."""

    def test_false_when_version_unknown(self) -> None:
        with patch("packastack.build.unshare.get_sbuild_version", return_value=None):
            assert sbuild_defaults_to_unshare() is False

    def test_false_for_pre_unshare_default(self) -> None:
        with patch("packastack.build.unshare.get_sbuild_version", return_value=(0, 85, 0)):
            assert sbuild_defaults_to_unshare() is False

    def test_true_for_flip_version(self) -> None:
        with patch("packastack.build.unshare.get_sbuild_version", return_value=(0, 88, 3)):
            assert sbuild_defaults_to_unshare() is True

    def test_true_for_newer(self) -> None:
        with patch("packastack.build.unshare.get_sbuild_version", return_value=(1, 0)):
            assert sbuild_defaults_to_unshare() is True


class TestHasSubidEntry:
    """Tests for subordinate-id file parsing."""

    def test_matches_username(self, tmp_path: Path) -> None:
        subid = tmp_path / "subuid"
        subid.write_text("root:100000:65536\nalice:165536:65536\n")
        assert _has_subid_entry(subid, "alice", "1000") is True

    def test_matches_uid(self, tmp_path: Path) -> None:
        subid = tmp_path / "subuid"
        subid.write_text("1000:100000:65536\n")
        assert _has_subid_entry(subid, "alice", "1000") is True

    def test_no_match(self, tmp_path: Path) -> None:
        subid = tmp_path / "subuid"
        subid.write_text("bob:100000:65536\n")
        assert _has_subid_entry(subid, "alice", "1000") is False

    def test_missing_file(self, tmp_path: Path) -> None:
        assert _has_subid_entry(tmp_path / "missing", "alice", "1000") is False


class TestCheckUnsharePrerequisites:
    """Tests for host prerequisite checks."""

    def test_missing_mmdebstrap(self) -> None:
        with patch("packastack.build.unshare.shutil.which", return_value=None):
            error = check_unshare_prerequisites()
        assert "mmdebstrap not found" in error

    def test_missing_subuid_entry(self) -> None:
        with (
            patch("packastack.build.unshare.shutil.which", return_value="/usr/bin/mmdebstrap"),
            patch("packastack.build.unshare._has_subid_entry", return_value=False),
        ):
            error = check_unshare_prerequisites()
        assert "/etc/subuid" in error
        assert "usermod" in error

    def test_missing_subgid_entry(self) -> None:
        with (
            patch("packastack.build.unshare.shutil.which", return_value="/usr/bin/mmdebstrap"),
            patch("packastack.build.unshare._has_subid_entry", side_effect=[True, False]),
        ):
            error = check_unshare_prerequisites()
        assert "/etc/subgid" in error

    def test_all_prerequisites_met(self) -> None:
        with (
            patch("packastack.build.unshare.shutil.which", return_value="/usr/bin/mmdebstrap"),
            patch("packastack.build.unshare._has_subid_entry", return_value=True),
        ):
            assert check_unshare_prerequisites() == ""


class TestResolveChrootMode:
    """Tests for chroot mode auto-detection."""

    def test_explicit_schroot(self) -> None:
        assert resolve_chroot_mode("schroot", "noble", "amd64") == "schroot"

    def test_explicit_unshare(self) -> None:
        assert resolve_chroot_mode("unshare", "noble", "amd64") == "unshare"

    def test_invalid_mode_raises(self) -> None:
        with pytest.raises(ChrootModeError, match=r"Invalid sbuild\.chroot_mode"):
            resolve_chroot_mode("container", "noble", "amd64")

    def test_auto_prefers_existing_tarball(self, cache_dir: Path) -> None:
        cache_dir.mkdir(parents=True)
        (cache_dir / "noble-amd64.tar.zst").write_text("tar")
        assert resolve_chroot_mode("auto", "noble", "amd64") == "unshare"

    def test_auto_uses_existing_schroot(self, cache_dir: Path) -> None:
        with patch("packastack.build.unshare.schroot_exists", return_value=True):
            assert resolve_chroot_mode("auto", "noble", "amd64") == "schroot"

    def test_auto_follows_sbuild_default_when_ready(self, cache_dir: Path) -> None:
        with (
            patch("packastack.build.unshare.schroot_exists", return_value=False),
            patch("packastack.build.unshare.sbuild_defaults_to_unshare", return_value=True),
            patch("packastack.build.unshare.check_unshare_prerequisites", return_value=""),
        ):
            assert resolve_chroot_mode("auto", "noble", "amd64") == "unshare"

    def test_auto_falls_back_when_prerequisites_missing(self, cache_dir: Path) -> None:
        with (
            patch("packastack.build.unshare.schroot_exists", return_value=False),
            patch("packastack.build.unshare.sbuild_defaults_to_unshare", return_value=True),
            patch(
                "packastack.build.unshare.check_unshare_prerequisites",
                return_value="mmdebstrap not found",
            ),
        ):
            assert resolve_chroot_mode("auto", "noble", "amd64") == "schroot"

    def test_auto_falls_back_for_old_sbuild(self, cache_dir: Path) -> None:
        with (
            patch("packastack.build.unshare.schroot_exists", return_value=False),
            patch("packastack.build.unshare.sbuild_defaults_to_unshare", return_value=False),
        ):
            assert resolve_chroot_mode("auto", "noble", "amd64") == "schroot"


class TestCreateUnshareTarball:
    """Tests for tarball creation via mmdebstrap."""

    def test_builds_expected_command(self, cache_dir: Path) -> None:
        tarball = cache_dir / "noble-amd64.tar.zst"
        config = make_config(extra_repos=["deb http://example.com noble main"])
        result = MagicMock(returncode=0)
        with patch("packastack.build.unshare.subprocess.run", return_value=result) as mock_run:
            ok, err = _create_unshare_tarball(config, tarball)
        assert ok is True
        assert err == ""
        assert cache_dir.is_dir()
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == "mmdebstrap"
        assert "--mode=unshare" in cmd
        assert "--arch=amd64" in cmd
        assert "--variant=buildd" in cmd
        assert "--components=main,universe" in cmd
        assert "noble" in cmd
        assert str(tarball) in cmd
        assert "http://archive.ubuntu.com/ubuntu" in cmd
        assert cmd[-1] == "deb http://example.com noble main"
        assert "sudo" not in cmd

    def test_failure_removes_partial_tarball(self, cache_dir: Path) -> None:
        tarball = cache_dir / "noble-amd64.tar.zst"

        def fake_run(cmd, **kwargs):
            tarball.write_text("partial")
            return MagicMock(returncode=1, stdout="E: bootstrap failed\n", stderr="died\n")

        with patch("packastack.build.unshare.subprocess.run", side_effect=fake_run):
            ok, err = _create_unshare_tarball(make_config(), tarball)
        assert ok is False
        assert "died" in err
        assert "E: bootstrap failed" in err
        assert not tarball.exists()


class TestEnsureUnshareTarball:
    """Tests for ensure_unshare_tarball."""

    def test_existing_tarball(self, cache_dir: Path) -> None:
        cache_dir.mkdir(parents=True)
        tarball = cache_dir / "noble-amd64.tar.zst"
        tarball.write_text("tar")
        result = ensure_unshare_tarball(make_config(), offline=False)
        assert result == UnshareResult(name=str(tarball), exists=True)

    def test_offline_prevents_creation(self, cache_dir: Path) -> None:
        result = ensure_unshare_tarball(make_config(), offline=True)
        assert result.exists is False
        assert "offline" in result.error
        assert result.name == str(cache_dir / "noble-amd64.tar.zst")

    def test_prerequisite_failure(self, cache_dir: Path) -> None:
        with patch(
            "packastack.build.unshare.check_unshare_prerequisites",
            return_value="mmdebstrap not found",
        ):
            result = ensure_unshare_tarball(make_config(), offline=False)
        assert result.exists is False
        assert result.error == "mmdebstrap not found"

    def test_creates_tarball(self, cache_dir: Path) -> None:
        with (
            patch("packastack.build.unshare.check_unshare_prerequisites", return_value=""),
            patch.object(
                unshare, "_create_unshare_tarball", return_value=(True, "")
            ) as mock_create,
        ):
            result = ensure_unshare_tarball(make_config(), offline=False)
        assert result.exists is True
        assert result.created is True
        assert result.name == str(cache_dir / "noble-amd64.tar.zst")
        mock_create.assert_called_once()

    def test_creation_failure(self, cache_dir: Path) -> None:
        with (
            patch("packastack.build.unshare.check_unshare_prerequisites", return_value=""),
            patch.object(unshare, "_create_unshare_tarball", return_value=(False, "boom")),
        ):
            result = ensure_unshare_tarball(make_config(), offline=False)
        assert result.exists is False
        assert result.created is False
        assert result.error == "boom"
