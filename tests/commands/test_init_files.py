from pathlib import Path

from packastack.commands.init import _create_ubuntu_archive_files


def test_create_ubuntu_archive_files(tmp_path: Path):
    cache_dir = tmp_path / "ubuntu-archive"
    cache_dir.mkdir()

    _create_ubuntu_archive_files(cache_dir)

    readme = cache_dir / "README.txt"
    config = cache_dir / "config.json"

    assert readme.exists()
    assert config.exists()

    content = readme.read_text()
    assert "Packastack Ubuntu Archive Cache" in content

    cfg = config.read_text()
    assert "mirror" in cfg
