from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import packastack.build.schroot as schroot


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
