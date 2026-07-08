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

    def test_relative_setup_fstab_resolved_against_etc_schroot(self) -> None:
        text = "[chroot]\nsetup.fstab=custom.fstab\nprofile=sbuild\n"
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

        schroot._sudo_set_fstab_key(conf, Path("/etc/schroot/new.fstab"))
        # schroot resolves setup.fstab relative to /etc/schroot, so only
        # the filename may be written.
        assert "setup.fstab=new.fstab" in written[str(conf)]
        assert "/old/path" not in written[str(conf)]
        assert "setup.fstab=/etc/schroot" not in written[str(conf)]

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

        schroot._sudo_set_fstab_key(conf, Path("/etc/schroot/new.fstab"))
        lines = written[str(conf)].splitlines()
        profile_idx = next(i for i, ln in enumerate(lines) if "profile=" in ln)
        assert lines[profile_idx + 1] == "setup.fstab=new.fstab"

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

        schroot._sudo_set_fstab_key(conf, Path("/etc/schroot/new.fstab"))
        assert written[str(conf)].rstrip().endswith("setup.fstab=new.fstab")


class TestCombineProcessOutput:
    def test_combines_stderr_and_stdout_tail(self) -> None:
        result = SimpleNamespace(
            stderr="E: Error running debootstrap at line 438\n",
            stdout="I: Retrieving InRelease\nE: Invalid Release file, no entry\n",
        )
        combined = schroot._combine_process_output(result)
        assert "Error running debootstrap" in combined
        assert "Invalid Release file" in combined

    def test_stdout_truncated_to_tail(self) -> None:
        lines = [f"line {i}" for i in range(100)]
        result = SimpleNamespace(stderr="", stdout="\n".join(lines))
        combined = schroot._combine_process_output(result)
        assert "line 99" in combined
        assert "line 0" not in combined

    def test_fallback_message_when_both_empty(self) -> None:
        result = SimpleNamespace(stderr="", stdout="")
        assert schroot._combine_process_output(result) == "schroot creation failed"


class TestCleanupPartialChroot:
    def test_removes_dir_under_chroots_root(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        chroots_root = tmp_path / "chroots"
        target = chroots_root / "packastack-stonking-amd64"
        target.mkdir(parents=True)
        monkeypatch.setattr(schroot, "_CHROOTS_ROOT", chroots_root)
        monkeypatch.setattr(schroot.os, "geteuid", lambda: 1000)

        commands: list[list[str]] = []
        monkeypatch.setattr(
            schroot.subprocess,
            "run",
            lambda cmd, **kw: commands.append(cmd)
            or SimpleNamespace(returncode=0, stdout="", stderr=""),
        )

        schroot._cleanup_partial_chroot(target)

        assert commands == [["sudo", "rm", "-rf", str(target)]]

    def test_refuses_paths_outside_chroots_root(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(schroot, "_CHROOTS_ROOT", tmp_path / "chroots")

        commands: list[list[str]] = []
        monkeypatch.setattr(
            schroot.subprocess,
            "run",
            lambda cmd, **kw: commands.append(cmd)
            or SimpleNamespace(returncode=0, stdout="", stderr=""),
        )

        outside = tmp_path / "elsewhere" / "dir"
        outside.mkdir(parents=True)
        schroot._cleanup_partial_chroot(outside)

        assert commands == []

    def test_noop_when_dir_missing(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        chroots_root = tmp_path / "chroots"
        chroots_root.mkdir()
        monkeypatch.setattr(schroot, "_CHROOTS_ROOT", chroots_root)

        commands: list[list[str]] = []
        monkeypatch.setattr(
            schroot.subprocess,
            "run",
            lambda cmd, **kw: commands.append(cmd)
            or SimpleNamespace(returncode=0, stdout="", stderr=""),
        )

        schroot._cleanup_partial_chroot(chroots_root / "never-created")

        assert commands == []


class TestRegisterSchrootConfig:
    def test_writes_sbuild_style_config(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(schroot, "_CHROOT_CONF_DIR", tmp_path)

        written: dict[str, str] = {}
        monkeypatch.setattr(
            schroot, "_sudo_write_file",
            lambda p, c: written.update({str(p): c}),
        )

        config = schroot.SchrootConfig(
            series="stonking",
            arch="amd64",
            mirror="http://archive.ubuntu.com/ubuntu",
            components=("main", "universe"),
        )
        target = Path("/var/lib/schroot/chroots/packastack-stonking-amd64")

        schroot._register_schroot_config(
            "packastack-stonking-amd64", config, target
        )

        conf_path = str(tmp_path / "stonking-amd64-packastack")
        assert conf_path in written
        content = written[conf_path]
        assert "[stonking-amd64-packastack]" in content
        assert f"directory={target}" in content
        assert "union-type=overlay" in content
        assert "aliases=packastack-stonking-amd64" in content
        assert "profile=sbuild" in content


class TestCreateSchrootMmdebstrap:
    def _config(self) -> schroot.SchrootConfig:
        return schroot.SchrootConfig(
            series="stonking",
            arch="amd64",
            mirror="http://archive.ubuntu.com/ubuntu",
            components=("main", "universe"),
            extra_repos=("deb http://example.com/repo stonking main",),
        )

    def test_missing_mmdebstrap(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(schroot.shutil, "which", lambda name: None)
        ok, err = schroot._create_schroot_mmdebstrap(
            "packastack-stonking-amd64", self._config(), Path("/tmp/x")
        )
        assert ok is False
        assert "mmdebstrap not found" in err

    def test_success_runs_and_registers(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(
            schroot.shutil, "which", lambda name: f"/usr/bin/{name}"
        )
        monkeypatch.setattr(schroot.os, "geteuid", lambda: 1000)

        commands: list[list[str]] = []
        monkeypatch.setattr(
            schroot.subprocess,
            "run",
            lambda cmd, **kw: commands.append(cmd)
            or SimpleNamespace(returncode=0, stdout="", stderr=""),
        )

        registered: list[str] = []
        monkeypatch.setattr(
            schroot,
            "_register_schroot_config",
            lambda name, config, target: registered.append(name),
        )

        target = tmp_path / "chroot"
        ok, err = schroot._create_schroot_mmdebstrap(
            "packastack-stonking-amd64", self._config(), target
        )

        assert ok is True
        assert err == ""
        cmd = commands[0]
        assert cmd[0] == "sudo"
        assert "mmdebstrap" in cmd
        assert "--variant=buildd" in cmd
        assert "--components=main,universe" in cmd
        assert "stonking" in cmd
        assert "deb http://example.com/repo stonking main" in cmd
        assert registered == ["packastack-stonking-amd64"]

    def test_failure_returns_combined_output(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(
            schroot.shutil, "which", lambda name: f"/usr/bin/{name}"
        )
        monkeypatch.setattr(schroot.os, "geteuid", lambda: 0)
        monkeypatch.setattr(
            schroot.subprocess,
            "run",
            lambda cmd, **kw: SimpleNamespace(
                returncode=1, stdout="E: apt failed\n", stderr="fatal\n"
            ),
        )

        ok, err = schroot._create_schroot_mmdebstrap(
            "packastack-stonking-amd64", self._config(), tmp_path / "chroot"
        )

        assert ok is False
        assert "fatal" in err
        assert "E: apt failed" in err

    def test_registration_failure_reported(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(
            schroot.shutil, "which", lambda name: f"/usr/bin/{name}"
        )
        monkeypatch.setattr(schroot.os, "geteuid", lambda: 0)
        monkeypatch.setattr(
            schroot.subprocess,
            "run",
            lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""),
        )

        import subprocess as sp

        def boom(name: str, config: Any, target: Path) -> None:
            raise sp.CalledProcessError(1, "sudo tee")

        monkeypatch.setattr(schroot, "_register_schroot_config", boom)

        ok, err = schroot._create_schroot_mmdebstrap(
            "packastack-stonking-amd64", self._config(), tmp_path / "chroot"
        )

        assert ok is False
        assert "registration failed" in err


class TestCreateSchrootFallback:
    def _config(self) -> schroot.SchrootConfig:
        return schroot.SchrootConfig(
            series="stonking",
            arch="amd64",
            mirror="http://archive.ubuntu.com/ubuntu",
            components=("main", "universe"),
        )

    def test_sbuild_failure_falls_back_to_mmdebstrap(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(
            schroot.shutil, "which", lambda name: f"/usr/bin/{name}"
        )
        monkeypatch.setattr(schroot.os, "geteuid", lambda: 0)
        monkeypatch.setattr(
            schroot.subprocess,
            "run",
            lambda cmd, **kw: SimpleNamespace(
                returncode=1,
                stdout="E: Invalid Release file, no entry\n",
                stderr="E: Error running debootstrap\n",
            ),
        )

        cleanups: list[Path] = []
        monkeypatch.setattr(
            schroot, "_cleanup_partial_chroot", lambda p: cleanups.append(p)
        )
        monkeypatch.setattr(
            schroot,
            "_create_schroot_mmdebstrap",
            lambda name, config, target: (True, ""),
        )

        ok, err = schroot._create_schroot("packastack-stonking-amd64", self._config())

        assert ok is True
        assert err == ""
        # Partial dir from the failed sbuild-createchroot attempt is removed
        assert len(cleanups) == 1

    def test_both_tools_fail_reports_both_errors(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(
            schroot.shutil, "which", lambda name: f"/usr/bin/{name}"
        )
        monkeypatch.setattr(schroot.os, "geteuid", lambda: 0)
        monkeypatch.setattr(
            schroot.subprocess,
            "run",
            lambda cmd, **kw: SimpleNamespace(
                returncode=1,
                stdout="E: Invalid Release file, no entry\n",
                stderr="E: Error running debootstrap\n",
            ),
        )

        cleanups: list[Path] = []
        monkeypatch.setattr(
            schroot, "_cleanup_partial_chroot", lambda p: cleanups.append(p)
        )
        monkeypatch.setattr(
            schroot,
            "_create_schroot_mmdebstrap",
            lambda name, config, target: (False, "mmdebstrap not found"),
        )

        ok, err = schroot._create_schroot("packastack-stonking-amd64", self._config())

        assert ok is False
        # Both the debootstrap detail (from stdout!) and the fallback error
        assert "Invalid Release file" in err
        assert "Error running debootstrap" in err
        assert "mmdebstrap not found" in err
        assert len(cleanups) == 2

    def test_missing_sbuild_createchroot_uses_fallback(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(
            schroot.shutil,
            "which",
            lambda name: None if name == "sbuild-createchroot" else f"/usr/bin/{name}",
        )
        monkeypatch.setattr(
            schroot,
            "_create_schroot_mmdebstrap",
            lambda name, config, target: (True, ""),
        )

        ok, err = schroot._create_schroot("packastack-stonking-amd64", self._config())

        assert ok is True
        assert err == ""


class TestConfigureRepoMountFstabName:
    def test_no_stuttering_prefix_for_alias_names(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        conf_dir = tmp_path / "chroot.d"
        conf_dir.mkdir()
        monkeypatch.setattr(schroot, "_CHROOT_CONF_DIR", conf_dir)

        repo = tmp_path / "repo"
        repo.mkdir()
        base_fstab = tmp_path / "base.fstab"
        base_fstab.write_text("/proc /proc none rw,bind 0 0\n")

        conf = conf_dir / "stonking-amd64-packastack"
        conf.write_text(
            "[stonking-amd64-packastack]\nprofile=sbuild\n"
            "aliases=packastack-stonking-amd64\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(schroot, "_fstab_has_repo_mount", lambda *a: False)
        monkeypatch.setattr(schroot, "_get_profile_fstab_path", lambda _: base_fstab)

        written: dict[str, str] = {}
        monkeypatch.setattr(
            schroot, "_sudo_write_file",
            lambda p, c: written.update({str(p): c}),
        )
        fstab_keys: list[Path] = []
        monkeypatch.setattr(
            schroot, "_sudo_set_fstab_key",
            lambda conf_path, fstab: fstab_keys.append(fstab),
        )

        assert configure_repo_mount("packastack-stonking-amd64", repo) is True

        assert "/etc/schroot/packastack-stonking-amd64.fstab" in written
        assert fstab_keys == [Path("/etc/schroot/packastack-stonking-amd64.fstab")]
