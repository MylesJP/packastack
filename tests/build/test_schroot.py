from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import packastack.build.schroot as schroot
from packastack.build.schroot import (
    RepoMountError,
    _find_schroot_config,
    _fstab_has_repo_mount,
    _get_profile_fstab_path,
    configure_repo_mount,
)


def _fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
    """Fake subprocess.run that accepts any keyword arguments."""
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def test_create_schroot_uses_sudo_when_not_root(monkeypatch, tmp_path: Path) -> None:
    """Test that _create_schroot prepends sudo when not running as root."""
    # Fake tools available
    monkeypatch.setattr(schroot.shutil, "which", lambda name: "/usr/bin/" + name)
    # Pretend we are not root
    monkeypatch.setattr(schroot.os, "geteuid", lambda: 1000)

    # Capture the command that would be run
    captured_cmd = []

    def capture_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        captured_cmd.extend(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(schroot.subprocess, "run", capture_run)

    config = schroot.SchrootConfig(
        series="noble",
        arch="amd64",
        mirror="http://archive.ubuntu.com/ubuntu",
        components=["main"],
        extra_repos=[],
    )

    ok, err = schroot._create_schroot(
        name="packastack-noble-amd64",
        config=config,
    )

    assert ok is True
    assert err == ""
    assert captured_cmd[0] == "sudo"
    assert "sbuild-createchroot" in captured_cmd
    assert f"--chroot-suffix={schroot.CHROOT_SUFFIX}" in captured_cmd


def test_create_schroot_uses_packastack_chroot_suffix(monkeypatch) -> None:
    """Test that _create_schroot passes --chroot-suffix to avoid collisions."""
    monkeypatch.setattr(schroot.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(schroot.os, "geteuid", lambda: 0)

    captured_cmd: list[str] = []

    def capture_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        captured_cmd.extend(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(schroot.subprocess, "run", capture_run)

    config = schroot.SchrootConfig(
        series="resolute",
        arch="amd64",
        mirror="http://archive.ubuntu.com/ubuntu",
        components=("main", "universe"),
        extra_repos=(),
    )

    ok, _ = schroot._create_schroot(name="packastack-resolute-amd64", config=config)

    assert ok is True
    assert "--chroot-suffix=-packastack" in captured_cmd
    assert "--alias=packastack-resolute-amd64" in captured_cmd


def test_get_sbuild_chroot_name() -> None:
    """Test that get_sbuild_chroot_name returns the sbuild-registered name."""
    assert schroot.get_sbuild_chroot_name("noble", "amd64") == "noble-amd64-packastack"
    assert schroot.get_sbuild_chroot_name("resolute", "arm64") == "resolute-arm64-packastack"


def test_ensure_schroot_finds_sbuild_registered_name(monkeypatch) -> None:
    """Test that ensure_schroot detects the sbuild-registered chroot name."""
    # Alias does not exist, but sbuild-registered name does
    def fake_exists(name: str) -> bool:
        return name == "resolute-amd64-packastack"

    monkeypatch.setattr(schroot, "schroot_exists", fake_exists)

    config = schroot.SchrootConfig(
        series="resolute",
        arch="amd64",
        mirror="http://archive.ubuntu.com/ubuntu",
        components=("main", "universe"),
        extra_repos=(),
    )

    result = schroot.ensure_schroot(config=config, offline=False)

    assert result.exists is True
    assert result.name == "resolute-amd64-packastack"
    assert result.created is False


def test_sudo_credentials_cached_returns_true_on_success(monkeypatch) -> None:
    """Test _sudo_credentials_cached returns True when sudo -n succeeds."""
    monkeypatch.setattr(
        schroot.subprocess,
        "run",
        lambda cmd, **kwargs: SimpleNamespace(returncode=0),
    )
    assert schroot._sudo_credentials_cached() is True


def test_sudo_credentials_cached_returns_false_on_failure(monkeypatch) -> None:
    """Test _sudo_credentials_cached returns False when sudo -n fails."""
    monkeypatch.setattr(
        schroot.subprocess,
        "run",
        lambda cmd, **kwargs: SimpleNamespace(returncode=1),
    )
    assert schroot._sudo_credentials_cached() is False


def test_ensure_sudo_cached_prompts_user(monkeypatch, capsys) -> None:
    """Test _ensure_sudo_cached prints message and runs sudo -v."""
    called_with = []

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        called_with.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(schroot.subprocess, "run", fake_run)

    result = schroot._ensure_sudo_cached()

    assert result is True
    assert ["sudo", "-v"] in called_with
    captured = capsys.readouterr()
    assert "sudo access required" in captured.out


# ---------------------------------------------------------------------------
# Tests for schroot fstab / repo mount configuration
# ---------------------------------------------------------------------------


class TestFindSchrootConfig:
    def test_finds_by_section_header(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr(schroot, "_CHROOT_CONF_DIR", tmp_path)
        conf = tmp_path / "myschroot-abc123"
        conf.write_text("[noble-amd64-packastack]\nprofile=sbuild\n")
        assert _find_schroot_config("noble-amd64-packastack") == conf

    def test_finds_by_alias(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr(schroot, "_CHROOT_CONF_DIR", tmp_path)
        conf = tmp_path / "myschroot-abc123"
        conf.write_text(
            "[noble-amd64-packastack]\n"
            "aliases=packastack-noble-amd64\n"
            "profile=sbuild\n"
        )
        assert _find_schroot_config("packastack-noble-amd64") == conf

    def test_returns_none_when_missing(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr(schroot, "_CHROOT_CONF_DIR", tmp_path)
        assert _find_schroot_config("nope") is None

    def test_returns_none_when_dir_absent(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr(schroot, "_CHROOT_CONF_DIR", tmp_path / "missing")
        assert _find_schroot_config("nope") is None

    def test_skips_unreadable_files(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr(schroot, "_CHROOT_CONF_DIR", tmp_path)
        d = tmp_path / "subdir"
        d.mkdir()
        assert _find_schroot_config("any") is None


class TestGetProfileFstabPath:
    def test_explicit_setup_fstab(self) -> None:
        text = "[chroot]\nsetup.fstab=/etc/schroot/custom.fstab\nprofile=sbuild\n"
        assert _get_profile_fstab_path(text) == Path("/etc/schroot/custom.fstab")

    def test_profile_fallback(self) -> None:
        text = "[chroot]\nprofile=sbuild\n"
        assert _get_profile_fstab_path(text) == Path("/etc/schroot/sbuild/fstab")

    def test_default_fallback(self) -> None:
        assert _get_profile_fstab_path("") == Path("/etc/schroot/sbuild/fstab")


class TestFstabHasRepoMount:
    def test_returns_true_when_present(self, tmp_path: Path) -> None:
        f = tmp_path / "fstab"
        f.write_text("/home/user/apt-repo /srv/packastack-apt none ro,bind 0 0\n")
        assert _fstab_has_repo_mount(f, "/home/user/apt-repo") is True

    def test_returns_false_when_absent(self, tmp_path: Path) -> None:
        f = tmp_path / "fstab"
        f.write_text("/proc /proc none rw,bind 0 0\n")
        assert _fstab_has_repo_mount(f, "/home/user/apt-repo") is False

    def test_returns_false_for_missing_file(self, tmp_path: Path) -> None:
        assert _fstab_has_repo_mount(tmp_path / "nope", "/any") is False


class TestConfigureRepoMount:
    def _write_chroot_config(
        self, conf_dir: Path, name: str, fstab: Path
    ) -> Path:
        conf = conf_dir / f"{name}-abc123"
        conf.write_text(
            f"[{name}]\nprofile=sbuild\n", encoding="utf-8"
        )
        return conf

    def test_raises_when_config_not_found(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(schroot, "_CHROOT_CONF_DIR", tmp_path)
        with pytest.raises(RepoMountError, match="Cannot find"):
            configure_repo_mount("nope", tmp_path / "repo")

    def test_noop_when_already_configured(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        conf_dir = tmp_path / "chroot.d"
        conf_dir.mkdir()
        monkeypatch.setattr(schroot, "_CHROOT_CONF_DIR", conf_dir)

        repo = tmp_path / "repo"
        repo.mkdir()

        self._write_chroot_config(conf_dir, "test-chroot", tmp_path)

        custom_fstab = Path("/etc/schroot/packastack-test-chroot.fstab")
        monkeypatch.setattr(
            schroot,
            "_fstab_has_repo_mount",
            lambda fpath, rpath: fpath == custom_fstab
            and rpath == str(repo.resolve()),
        )

        assert configure_repo_mount("test-chroot", repo) is True

    def test_raises_when_base_fstab_unreadable(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        conf_dir = tmp_path / "chroot.d"
        conf_dir.mkdir()
        monkeypatch.setattr(schroot, "_CHROOT_CONF_DIR", conf_dir)

        repo = tmp_path / "repo"
        repo.mkdir()

        self._write_chroot_config(conf_dir, "test-chroot", tmp_path)
        monkeypatch.setattr(
            schroot, "_fstab_has_repo_mount", lambda *a: False
        )
        monkeypatch.setattr(
            schroot,
            "_get_profile_fstab_path",
            lambda _: tmp_path / "nonexistent-fstab",
        )

        with pytest.raises(RepoMountError, match="Cannot read base fstab"):
            configure_repo_mount("test-chroot", repo)

    def test_writes_fstab_and_updates_config(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        conf_dir = tmp_path / "chroot.d"
        conf_dir.mkdir()
        monkeypatch.setattr(schroot, "_CHROOT_CONF_DIR", conf_dir)

        repo = tmp_path / "repo"
        repo.mkdir()
        base_fstab = tmp_path / "base.fstab"
        base_fstab.write_text("/proc /proc none rw,bind 0 0\n")

        self._write_chroot_config(conf_dir, "test-chroot", tmp_path)
        monkeypatch.setattr(
            schroot, "_fstab_has_repo_mount", lambda *a: False
        )
        monkeypatch.setattr(
            schroot, "_get_profile_fstab_path", lambda _: base_fstab
        )

        written_files: dict[str, str] = {}

        def fake_sudo_write(path: Path, content: str) -> None:
            written_files[str(path)] = content

        monkeypatch.setattr(schroot, "_sudo_write_file", fake_sudo_write)
        monkeypatch.setattr(schroot, "_sudo_set_fstab_key", lambda *a: None)

        assert configure_repo_mount("test-chroot", repo) is True

        fstab_key = "/etc/schroot/packastack-test-chroot.fstab"
        assert fstab_key in written_files
        content = written_files[fstab_key]
        assert str(repo.resolve()) in content
        assert "/srv/packastack-apt" in content
        assert "ro,bind" in content
        assert schroot.REPO_FSTAB_MARKER in content

    def test_raises_on_sudo_failure(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        conf_dir = tmp_path / "chroot.d"
        conf_dir.mkdir()
        monkeypatch.setattr(schroot, "_CHROOT_CONF_DIR", conf_dir)

        repo = tmp_path / "repo"
        repo.mkdir()
        base_fstab = tmp_path / "base.fstab"
        base_fstab.write_text("/proc /proc none rw,bind 0 0\n")

        self._write_chroot_config(conf_dir, "test-chroot", tmp_path)
        monkeypatch.setattr(
            schroot, "_fstab_has_repo_mount", lambda *a: False
        )
        monkeypatch.setattr(
            schroot, "_get_profile_fstab_path", lambda _: base_fstab
        )

        import subprocess as sp

        monkeypatch.setattr(
            schroot,
            "_sudo_write_file",
            lambda *a: (_ for _ in ()).throw(
                sp.CalledProcessError(1, "sudo tee")
            ),
        )

        with pytest.raises(RepoMountError, match="Failed to write"):
            configure_repo_mount("test-chroot", repo)


class TestSudoSetFstabKey:
    def test_replaces_existing_key(self, tmp_path: Path, monkeypatch: Any) -> None:
        conf = tmp_path / "conf"
        conf.write_text(
            "[chroot]\nprofile=sbuild\nsetup.fstab=/old/path\n"
        )

        written: dict[str, str] = {}
        monkeypatch.setattr(
            schroot, "_sudo_write_file",
            lambda p, c: written.update({str(p): c}),
        )

        schroot._sudo_set_fstab_key(conf, Path("/new/path"))
        assert "setup.fstab=/new/path" in written[str(conf)]
        assert "/old/path" not in written[str(conf)]

    def test_inserts_after_profile_when_absent(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        conf = tmp_path / "conf"
        conf.write_text("[chroot]\nprofile=sbuild\ntype=directory\n")

        written: dict[str, str] = {}
        monkeypatch.setattr(
            schroot, "_sudo_write_file",
            lambda p, c: written.update({str(p): c}),
        )

        schroot._sudo_set_fstab_key(conf, Path("/new/path"))
        lines = written[str(conf)].splitlines()
        profile_idx = next(i for i, ln in enumerate(lines) if "profile=" in ln)
        assert lines[profile_idx + 1] == "setup.fstab=/new/path"

    def test_appends_when_no_profile_line(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        conf = tmp_path / "conf"
        conf.write_text("[chroot]\ntype=directory\n")

        written: dict[str, str] = {}
        monkeypatch.setattr(
            schroot, "_sudo_write_file",
            lambda p, c: written.update({str(p): c}),
        )

        schroot._sudo_set_fstab_key(conf, Path("/new/path"))
        assert written[str(conf)].rstrip().endswith("setup.fstab=/new/path")
