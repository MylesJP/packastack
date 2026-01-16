# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only
#
# Packastack is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License version 3, as published by the
# Free Software Foundation.
#
# Packastack is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranties of MERCHANTABILITY,
# SATISFACTORY QUALITY, or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# Packastack. If not, see <http://www.gnu.org/licenses/>.

"""Tests for packastack.debpkg.gbpconf module."""

from __future__ import annotations

from pathlib import Path

from packastack.debpkg import gbpconf


class TestGbpConfig:
    """Tests for GbpConfig dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        config = gbpconf.GbpConfig()
        assert config.debian_branch == ""
        assert config.pristine_tar is False
        assert config.export_dir == "../build-area/"
        assert config.sign_tags is True
        assert config.keyid == ""


class TestLoadGbpConf:
    """Tests for load_gbp_conf function."""

    def test_file_not_exists(self, tmp_path: Path) -> None:
        """Test loading non-existent file returns None."""
        config = gbpconf.load_gbp_conf(tmp_path)
        assert config is None

    def test_load_basic_config(self, tmp_path: Path) -> None:
        """Test loading basic gbp.conf."""
        debian_dir = tmp_path / "debian"
        debian_dir.mkdir()
        conf_path = debian_dir / "gbp.conf"
        conf_path.write_text("""\
[DEFAULT]
debian-branch = ubuntu/noble-dalmatian
pristine-tar = False

[buildpackage]
export-dir = ../build-area/
sign-tags = True
keyid = ABC123
""")

        config = gbpconf.load_gbp_conf(tmp_path)
        assert config is not None
        assert config.debian_branch == "ubuntu/noble-dalmatian"
        assert config.pristine_tar is False
        assert config.export_dir == "../build-area/"
        assert config.sign_tags is True
        assert config.keyid == "ABC123"

    def test_load_minimal_config(self, tmp_path: Path) -> None:
        """Test loading minimal gbp.conf."""
        debian_dir = tmp_path / "debian"
        debian_dir.mkdir()
        conf_path = debian_dir / "gbp.conf"
        conf_path.write_text("""\
[DEFAULT]
debian-branch = ubuntu/plucky-epoxy

[buildpackage]
""")

        config = gbpconf.load_gbp_conf(tmp_path)
        assert config is not None
        assert config.debian_branch == "ubuntu/plucky-epoxy"
        assert config.keyid == ""  # Default

    def test_load_without_buildpackage_section(self, tmp_path: Path) -> None:
        """Test loading gbp.conf without buildpackage section."""
        debian_dir = tmp_path / "debian"
        debian_dir.mkdir()
        conf_path = debian_dir / "gbp.conf"
        conf_path.write_text("""\
[DEFAULT]
debian-branch = ubuntu/noble-caracal
pristine-tar = True
""")

        config = gbpconf.load_gbp_conf(tmp_path)
        assert config is not None
        assert config.debian_branch == "ubuntu/noble-caracal"
        assert config.pristine_tar is True
        # Defaults when buildpackage section is missing
        assert config.export_dir == "../build-area/"
        assert config.sign_tags is True
        assert config.keyid == ""

    def test_load_invalid_config(self, tmp_path: Path) -> None:
        """Test loading invalid gbp.conf returns None."""
        debian_dir = tmp_path / "debian"
        debian_dir.mkdir()
        conf_path = debian_dir / "gbp.conf"
        # Write invalid INI content
        conf_path.write_text("This is not valid INI\n[unclosed section")

        config = gbpconf.load_gbp_conf(tmp_path)
        assert config is None


class TestSaveGbpConf:
    """Tests for save_gbp_conf function."""

    def test_save_config(self, tmp_path: Path) -> None:
        """Test saving gbp.conf."""
        debian_dir = tmp_path / "debian"
        debian_dir.mkdir()
        conf_path = debian_dir / "gbp.conf"

        config = gbpconf.create_gbp_conf(
            tmp_path,
            ubuntu_series="noble",
            openstack_series="dalmatian",
            signing_key="DEADBEEF",
        )

        assert gbpconf.save_gbp_conf(config) is True
        assert conf_path.exists()

        content = conf_path.read_text()
        assert "debian-branch = ubuntu/noble-dalmatian" in content
        assert "keyid = DEADBEEF" in content

    def test_save_no_path(self) -> None:
        """Test saving config without path fails."""
        config = gbpconf.GbpConfig()
        assert gbpconf.save_gbp_conf(config) is False

    def test_save_creates_buildpackage_section(self, tmp_path: Path) -> None:
        """Test saving creates buildpackage section if missing."""
        import configparser

        debian_dir = tmp_path / "debian"
        debian_dir.mkdir()
        conf_path = debian_dir / "gbp.conf"

        # Create a config with a fresh parser (no buildpackage section)
        parser = configparser.ConfigParser()
        config = gbpconf.GbpConfig(
            debian_branch="ubuntu/noble-dalmatian",
            keyid="TESTKEY",
            _parser=parser,
            path=conf_path,
        )

        assert gbpconf.save_gbp_conf(config) is True
        assert conf_path.exists()

        # Verify the file was saved correctly
        loaded = gbpconf.load_gbp_conf(tmp_path)
        assert loaded is not None
        assert loaded.debian_branch == "ubuntu/noble-dalmatian"
        assert loaded.keyid == "TESTKEY"


class TestCreateGbpConf:
    """Tests for create_gbp_conf function."""

    def test_create_with_signing_key(self, tmp_path: Path) -> None:
        """Test creating gbp.conf with signing key."""
        config = gbpconf.create_gbp_conf(
            tmp_path,
            ubuntu_series="plucky",
            openstack_series="epoxy",
            signing_key="12345678",
        )

        assert config.debian_branch == "ubuntu/plucky-epoxy"
        assert config.keyid == "12345678"
        assert config.sign_tags is True

    def test_create_without_signing_key(self, tmp_path: Path) -> None:
        """Test creating gbp.conf without signing key."""
        config = gbpconf.create_gbp_conf(
            tmp_path,
            ubuntu_series="noble",
            openstack_series="dalmatian",
        )

        assert config.debian_branch == "ubuntu/noble-dalmatian"
        assert config.keyid == ""
        assert config.sign_tags is False


class TestUpdateGbpConf:
    """Tests for update_gbp_conf function."""

    def test_update_nonexistent_with_series(self, tmp_path: Path) -> None:
        """Test updating non-existent file creates new one."""
        (tmp_path / "debian").mkdir()

        success, updated, _error = gbpconf.update_gbp_conf(
            tmp_path,
            ubuntu_series="noble",
            openstack_series="dalmatian",
            signing_key="KEY123",
        )

        assert success is True
        assert "created new gbp.conf" in updated
        assert (tmp_path / "debian" / "gbp.conf").exists()

    def test_update_nonexistent_no_series(self, tmp_path: Path) -> None:
        """Test updating non-existent file without series does nothing."""
        success, updated, _error = gbpconf.update_gbp_conf(tmp_path)

        assert success is True
        assert updated == []

    def test_update_existing_config(self, tmp_path: Path) -> None:
        """Test updating existing gbp.conf."""
        debian_dir = tmp_path / "debian"
        debian_dir.mkdir()
        (debian_dir / "gbp.conf").write_text("""\
[DEFAULT]
debian-branch = ubuntu/noble-caracal

[buildpackage]
sign-tags = True
""")

        success, updated, _error = gbpconf.update_gbp_conf(
            tmp_path,
            ubuntu_series="noble",
            openstack_series="dalmatian",
            signing_key="NEWKEY",
        )

        assert success is True
        assert any("debian-branch" in u for u in updated)
        assert any("keyid" in u for u in updated)

    def test_update_no_changes_needed(self, tmp_path: Path) -> None:
        """Test when no changes are needed."""
        debian_dir = tmp_path / "debian"
        debian_dir.mkdir()
        (debian_dir / "gbp.conf").write_text("""\
[DEFAULT]
debian-branch = ubuntu/noble-dalmatian

[buildpackage]
keyid = MYKEY
""")

        success, updated, _error = gbpconf.update_gbp_conf(
            tmp_path,
            ubuntu_series="noble",
            openstack_series="dalmatian",
            signing_key="MYKEY",
        )

        assert success is True
        assert updated == []

    def test_update_remove_signing_key(self, tmp_path: Path) -> None:
        """Test removing signing key."""
        debian_dir = tmp_path / "debian"
        debian_dir.mkdir()
        (debian_dir / "gbp.conf").write_text("""\
[DEFAULT]
debian-branch = ubuntu/noble-dalmatian

[buildpackage]
keyid = OLDKEY
""")

        success, updated, _error = gbpconf.update_gbp_conf(
            tmp_path,
            signing_key="",
        )

        assert success is True
        assert any("(removed)" in u for u in updated)

    def test_update_partial_series(self, tmp_path: Path) -> None:
        """Test update with only one series provided does nothing."""
        debian_dir = tmp_path / "debian"
        debian_dir.mkdir()
        (debian_dir / "gbp.conf").write_text("""\
[DEFAULT]
debian-branch = ubuntu/noble-caracal

[buildpackage]
""")

        # Only ubuntu_series, no openstack_series
        success, updated, _error = gbpconf.update_gbp_conf(
            tmp_path,
            ubuntu_series="noble",
        )

        assert success is True
        # No branch update since openstack_series is missing
        assert not any("debian-branch" in u for u in updated)


class TestUpdateGbpConfFromLaunchpadYaml:
    """Tests for update_gbp_conf_from_launchpad_yaml function."""

    def test_no_launchpad_yaml(self, tmp_path: Path) -> None:
        """Test when launchpad.yaml doesn't exist."""
        success, updated, error = gbpconf.update_gbp_conf_from_launchpad_yaml(tmp_path)

        assert success is True
        assert updated == []
        assert "No launchpad.yaml found" in error

    def test_empty_recipes(self, tmp_path: Path) -> None:
        """Test when launchpad.yaml has no recipes."""
        (tmp_path / "launchpad.yaml").write_text("recipes: []\n")

        success, updated, error = gbpconf.update_gbp_conf_from_launchpad_yaml(tmp_path)

        assert success is True
        assert updated == []
        assert "No recipes" in error

    def test_recipe_no_branch(self, tmp_path: Path) -> None:
        """Test when recipe has no branch."""
        (tmp_path / "launchpad.yaml").write_text("""\
recipes:
  - name: nova-dalmatian
""")

        success, updated, error = gbpconf.update_gbp_conf_from_launchpad_yaml(tmp_path)

        assert success is True
        assert updated == []
        assert "No branch" in error

    def test_update_from_launchpad_yaml(self, tmp_path: Path) -> None:
        """Test updating gbp.conf from launchpad.yaml."""
        # Create launchpad.yaml
        (tmp_path / "launchpad.yaml").write_text("""\
recipes:
  - name: nova-dalmatian
    branch: ubuntu/noble-dalmatian
""")

        # Create debian dir
        (tmp_path / "debian").mkdir()

        success, updated, _error = gbpconf.update_gbp_conf_from_launchpad_yaml(
            tmp_path, signing_key="SIGNKEY"
        )

        assert success is True
        assert any("debian-branch" in u for u in updated)
        assert any("keyid" in u for u in updated)

        # Verify file was created
        config = gbpconf.load_gbp_conf(tmp_path)
        assert config is not None
        assert config.debian_branch == "ubuntu/noble-dalmatian"
        assert config.keyid == "SIGNKEY"

    def test_update_existing_from_launchpad_yaml(self, tmp_path: Path) -> None:
        """Test updating existing gbp.conf from launchpad.yaml."""
        # Create launchpad.yaml
        (tmp_path / "launchpad.yaml").write_text("""\
recipes:
  - name: nova-epoxy
    branch: ubuntu/plucky-epoxy
""")

        # Create existing gbp.conf
        debian_dir = tmp_path / "debian"
        debian_dir.mkdir()
        (debian_dir / "gbp.conf").write_text("""\
[DEFAULT]
debian-branch = ubuntu/noble-dalmatian

[buildpackage]
sign-tags = True
""")

        success, updated, _error = gbpconf.update_gbp_conf_from_launchpad_yaml(tmp_path)

        assert success is True
        assert any("debian-branch=ubuntu/plucky-epoxy" in u for u in updated)

        config = gbpconf.load_gbp_conf(tmp_path)
        assert config is not None
        assert config.debian_branch == "ubuntu/plucky-epoxy"

    def test_no_changes_needed(self, tmp_path: Path) -> None:
        """Test when gbp.conf already matches launchpad.yaml."""
        # Create launchpad.yaml
        (tmp_path / "launchpad.yaml").write_text("""\
recipes:
  - name: nova-dalmatian
    branch: ubuntu/noble-dalmatian
""")

        # Create matching gbp.conf
        debian_dir = tmp_path / "debian"
        debian_dir.mkdir()
        (debian_dir / "gbp.conf").write_text("""\
[DEFAULT]
debian-branch = ubuntu/noble-dalmatian

[buildpackage]
""")

        success, updated, error = gbpconf.update_gbp_conf_from_launchpad_yaml(tmp_path)

        assert success is True
        assert updated == []
        assert "No changes needed" in error
