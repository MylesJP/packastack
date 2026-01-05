from __future__ import annotations

from pathlib import Path

from packastack.debpkg.watch import upgrade_watch_version


def test_upgrade_watch_rewrites_version_line(tmp_path: Path) -> None:
    watch = tmp_path / "watch"
    watch.write_text("version=4\nhttps://example.com/src.tar.gz\n", encoding="utf-8")

    changed = upgrade_watch_version(watch)

    assert changed is True
    updated = watch.read_text(encoding="utf-8")
    assert updated.startswith("version=5\n")
    assert "https://example.com/src.tar.gz" in updated


def test_upgrade_watch_no_change_when_already_v5(tmp_path: Path) -> None:
    watch = tmp_path / "watch"
    original = "version=5\n# comment\n"
    watch.write_text(original, encoding="utf-8")

    changed = upgrade_watch_version(watch)

    assert changed is False
    assert watch.read_text(encoding="utf-8") == original


def test_upgrade_watch_adds_version_when_missing(tmp_path: Path) -> None:
    watch = tmp_path / "watch"
    content = "# watch file\nhttps://example.com/src.tar.gz\n"
    watch.write_text(content, encoding="utf-8")

    changed = upgrade_watch_version(watch)

    assert changed is True
    updated = watch.read_text(encoding="utf-8")
    assert updated.startswith("version=5\n")
    assert "https://example.com/src.tar.gz" in updated
