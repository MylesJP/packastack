"""Tests for the packages module."""

from __future__ import annotations

import gzip
import tempfile
from pathlib import Path

from packastack.packages import (
    BinaryPackage,
    PackageIndex,
    compare_versions,
    iter_packages,
    load_package_index,
    version_satisfies,
)


class TestCompareVersions:
    """Tests for compare_versions function."""

    def test_equal_versions(self) -> None:
        assert compare_versions("1.0.0", "1.0.0") == 0

    def test_greater_version(self) -> None:
        assert compare_versions("2.0.0", "1.0.0") > 0

    def test_lesser_version(self) -> None:
        assert compare_versions("1.0.0", "2.0.0") < 0

    def test_epoch_comparison(self) -> None:
        assert compare_versions("1:1.0.0", "2.0.0") > 0
        assert compare_versions("2.0.0", "1:1.0.0") < 0

    def test_debian_revision(self) -> None:
        assert compare_versions("1.0.0-1", "1.0.0-2") < 0
        assert compare_versions("1.0.0-2", "1.0.0-1") > 0

    def test_ubuntu_suffix(self) -> None:
        assert compare_versions("1.0.0-1ubuntu1", "1.0.0-1") > 0
        assert compare_versions("1.0.0-1ubuntu2", "1.0.0-1ubuntu1") > 0

    def test_tilde_versions(self) -> None:
        # Tilde sorts before anything
        assert compare_versions("1.0.0~beta1", "1.0.0") < 0
        assert compare_versions("1.0.0", "1.0.0~beta1") > 0


class TestVersionSatisfies:
    """Tests for version_satisfies function."""

    def test_no_relation_always_satisfied(self) -> None:
        assert version_satisfies("1.0.0", "", "2.0.0") is True

    def test_no_required_version_always_satisfied(self) -> None:
        assert version_satisfies("1.0.0", ">=", "") is True

    def test_greater_than_or_equal(self) -> None:
        assert version_satisfies("2.0.0", ">=", "1.0.0") is True
        assert version_satisfies("1.0.0", ">=", "1.0.0") is True
        assert version_satisfies("0.9.0", ">=", "1.0.0") is False

    def test_less_than_or_equal(self) -> None:
        assert version_satisfies("0.9.0", "<=", "1.0.0") is True
        assert version_satisfies("1.0.0", "<=", "1.0.0") is True
        assert version_satisfies("2.0.0", "<=", "1.0.0") is False

    def test_equal(self) -> None:
        assert version_satisfies("1.0.0", "=", "1.0.0") is True
        assert version_satisfies("1.0.1", "=", "1.0.0") is False

    def test_strictly_greater(self) -> None:
        assert version_satisfies("2.0.0", ">>", "1.0.0") is True
        assert version_satisfies("1.0.0", ">>", "1.0.0") is False
        assert version_satisfies("0.9.0", ">>", "1.0.0") is False

    def test_strictly_less(self) -> None:
        assert version_satisfies("0.9.0", "<<", "1.0.0") is True
        assert version_satisfies("1.0.0", "<<", "1.0.0") is False
        assert version_satisfies("2.0.0", "<<", "1.0.0") is False

    def test_unknown_relation_returns_true(self) -> None:
        # Unknown relations default to satisfied
        assert version_satisfies("1.0.0", "~=", "2.0.0") is True


class TestBinaryPackage:
    """Tests for BinaryPackage dataclass."""

    def test_default_values(self) -> None:
        pkg = BinaryPackage(name="test", version="1.0.0", architecture="amd64")
        assert pkg.name == "test"
        assert pkg.version == "1.0.0"
        assert pkg.architecture == "amd64"
        assert pkg.source == ""
        assert pkg.depends == []
        assert pkg.provides == []
        assert pkg.component == ""
        assert pkg.pocket == ""

    def test_with_all_fields(self) -> None:
        pkg = BinaryPackage(
            name="test-pkg",
            version="1.2.3-1ubuntu1",
            architecture="amd64",
            source="test-src",
            depends=["libc6", "libssl3"],
            provides=["test-alt"],
            component="main",
            pocket="updates",
        )
        assert pkg.name == "test-pkg"
        assert pkg.architecture == "amd64"
        assert pkg.source == "test-src"


class TestIterPackages:
    """Tests for iter_packages function."""

    def test_parses_packages_gz(self) -> None:
        packages_content = b"""\
Package: test-pkg
Version: 1.0.0
Architecture: amd64
Source: test-src
Depends: libc6
Provides: test-alt
Filename: pool/main/t/test-src/test-pkg_1.0.0_amd64.deb

Package: another-pkg
Version: 2.0.0
Architecture: amd64
Filename: pool/main/a/another/another-pkg_2.0.0_amd64.deb
"""
        with tempfile.NamedTemporaryFile(suffix=".gz", delete=False) as f:
            f.write(gzip.compress(packages_content))
            temp_path = Path(f.name)

        try:
            packages = list(iter_packages(temp_path))
            assert len(packages) == 2

            pkg1 = packages[0]
            assert pkg1.name == "test-pkg"
            assert pkg1.version == "1.0.0"
            assert pkg1.architecture == "amd64"
            assert pkg1.source == "test-src"
            assert "libc6" in pkg1.depends
            assert "test-alt" in pkg1.provides

            pkg2 = packages[1]
            assert pkg2.name == "another-pkg"
            assert pkg2.version == "2.0.0"
        finally:
            temp_path.unlink()

    def test_parses_source_with_version(self) -> None:
        packages_content = b"""\
Package: test-pkg
Version: 1.0.0
Architecture: amd64
Source: test-src (1.0.0-1)
Filename: pool/main/t/test-src/test-pkg_1.0.0_amd64.deb
"""
        with tempfile.NamedTemporaryFile(suffix=".gz", delete=False) as f:
            f.write(gzip.compress(packages_content))
            temp_path = Path(f.name)

        try:
            packages = list(iter_packages(temp_path))
            assert len(packages) == 1
            assert packages[0].source == "test-src"
        finally:
            temp_path.unlink()


class TestPackageIndex:
    """Tests for PackageIndex class."""

    def test_empty_index(self) -> None:
        index = PackageIndex()
        assert index.find_package("test") is None
        assert index.get_version("test") is None

    def test_add_and_find_package(self) -> None:
        index = PackageIndex()
        pkg = BinaryPackage(
            name="test-pkg", version="1.0.0", architecture="amd64", source="test-src"
        )
        index.add_package(pkg, component="main", pocket="release")

        found = index.find_package("test-pkg")
        assert found is not None
        assert found.name == "test-pkg"
        assert found.component == "main"
        assert found.pocket == "release"

    def test_get_version(self) -> None:
        index = PackageIndex()
        pkg = BinaryPackage(name="test-pkg", version="1.2.3", architecture="amd64")
        index.add_package(pkg, component="main", pocket="release")

        assert index.get_version("test-pkg") == "1.2.3"
        assert index.get_version("nonexistent") is None

    def test_keeps_highest_version(self) -> None:
        index = PackageIndex()
        pkg1 = BinaryPackage(name="test-pkg", version="1.0.0", architecture="amd64")
        pkg2 = BinaryPackage(name="test-pkg", version="2.0.0", architecture="amd64")
        pkg3 = BinaryPackage(name="test-pkg", version="1.5.0", architecture="amd64")

        index.add_package(pkg1, component="main", pocket="release")
        index.add_package(pkg2, component="main", pocket="release")
        index.add_package(pkg3, component="main", pocket="release")  # Should not replace 2.0.0

        assert index.get_version("test-pkg") == "2.0.0"

    def test_find_by_provides(self) -> None:
        index = PackageIndex()
        pkg = BinaryPackage(
            name="libssl3", version="3.0.0", architecture="amd64", provides=["libssl"]
        )
        index.add_package(pkg, component="main", pocket="release")

        # Find by actual name
        found = index.find_package("libssl3")
        assert found is not None
        assert found.name == "libssl3"

        # Find by virtual name
        found = index.find_package("libssl")
        assert found is not None
        assert found.name == "libssl3"

    def test_get_binaries_for_source(self) -> None:
        index = PackageIndex()
        pkg1 = BinaryPackage(
            name="nova-api", version="1.0.0", architecture="amd64", source="nova"
        )
        pkg2 = BinaryPackage(
            name="nova-compute", version="1.0.0", architecture="amd64", source="nova"
        )
        pkg3 = BinaryPackage(
            name="glance-api", version="1.0.0", architecture="amd64", source="glance"
        )

        index.add_package(pkg1, component="main", pocket="release")
        index.add_package(pkg2, component="main", pocket="release")
        index.add_package(pkg3, component="main", pocket="release")

        nova_binaries = index.get_binaries_for_source("nova")
        assert "nova-api" in nova_binaries
        assert "nova-compute" in nova_binaries
        assert "glance-api" not in nova_binaries

    def test_get_component(self) -> None:
        index = PackageIndex()
        pkg = BinaryPackage(name="test-pkg", version="1.0.0", architecture="amd64")
        index.add_package(pkg, component="universe", pocket="release")

        assert index.get_component("test-pkg") == "universe"
        assert index.get_component("nonexistent") is None


class TestLoadPackageIndex:
    """Tests for load_package_index function."""

    def test_load_from_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)

            # Create a Packages.gz file in the expected structure
            release_dir = cache_dir / "indexes" / "noble" / "release" / "main" / "binary-amd64"
            release_dir.mkdir(parents=True)

            packages_content = b"""\
Package: test-pkg
Version: 1.0.0
Architecture: amd64
Source: test-src
Filename: pool/main/t/test-src/test-pkg_1.0.0_amd64.deb
"""
            pkg_gz = release_dir / "Packages.gz"
            pkg_gz.write_bytes(gzip.compress(packages_content))

            index = load_package_index(
                cache_dir,
                series="noble",
                pockets=["release"],
                components=["main"],
            )
            found = index.find_package("test-pkg")
            assert found is not None
            assert found.name == "test-pkg"

    def test_load_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            index = load_package_index(
                cache_dir,
                series="noble",
                pockets=["release"],
                components=["main"],
            )
            # Should return empty index without error
            assert index.find_package("anything") is None

    def test_load_multiple_pockets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)

            # Create packages in release pocket
            release_dir = cache_dir / "indexes" / "noble" / "release" / "main" / "binary-amd64"
            release_dir.mkdir(parents=True)
            (release_dir / "Packages.gz").write_bytes(gzip.compress(b"""\
Package: pkg-release
Version: 1.0.0
Architecture: amd64
Filename: pool/main/p/pkg/pkg-release_1.0.0_amd64.deb
"""))

            # Create packages in updates pocket
            updates_dir = cache_dir / "indexes" / "noble" / "updates" / "main" / "binary-amd64"
            updates_dir.mkdir(parents=True)
            (updates_dir / "Packages.gz").write_bytes(gzip.compress(b"""\
Package: pkg-updates
Version: 1.0.1
Architecture: amd64
Filename: pool/main/p/pkg/pkg-updates_1.0.1_amd64.deb
"""))

            index = load_package_index(
                cache_dir,
                series="noble",
                pockets=["release", "updates"],
                components=["main"],
            )
            assert index.find_package("pkg-release") is not None
            assert index.find_package("pkg-updates") is not None


class TestLoadCloudArchiveIndex:
    """Tests for load_cloud_archive_index function."""

    def test_load_empty_directory(self) -> None:
        """Test loading from non-existent directory."""
        from packastack.packages import load_cloud_archive_index

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            index = load_cloud_archive_index(
                cache_dir, ubuntu_series="noble", pocket="caracal"
            )
            assert index.find_package("anything") is None

    def test_load_cloud_archive(self) -> None:
        """Test loading from cloud archive structure."""
        from packastack.packages import load_cloud_archive_index

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)

            # Create cloud archive structure
            ca_dir = (
                cache_dir / "cloud-archive" / "indexes" / "noble" / "caracal" / "main" / "binary-amd64"
            )
            ca_dir.mkdir(parents=True)

            packages_content = b"""\
Package: python3-oslo.config
Version: 9.0.0
Architecture: all
Source: oslo.config
Filename: pool/main/o/oslo.config/python3-oslo.config_9.0.0_all.deb
"""
            (ca_dir / "Packages.gz").write_bytes(gzip.compress(packages_content))

            index = load_cloud_archive_index(
                cache_dir, ubuntu_series="noble", pocket="caracal"
            )
            found = index.find_package("python3-oslo.config")
            assert found is not None
            assert found.name == "python3-oslo.config"
            assert "cloud-archive" in found.pocket

    def test_skip_non_binary_dirs(self) -> None:
        """Test that non-binary-* directories are skipped."""
        from packastack.packages import load_cloud_archive_index

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)

            # Create cloud archive structure with non-binary directory
            ca_dir = cache_dir / "cloud-archive" / "indexes" / "noble" / "caracal" / "main"
            ca_dir.mkdir(parents=True)
            (ca_dir / "source").mkdir()  # Not a binary-* dir

            # Also create a proper binary-amd64 dir
            binary_dir = ca_dir / "binary-amd64"
            binary_dir.mkdir()
            packages_content = b"""\
Package: test-pkg
Version: 1.0.0
Architecture: amd64
Filename: pool/main/t/test/test-pkg_1.0.0_amd64.deb
"""
            (binary_dir / "Packages.gz").write_bytes(gzip.compress(packages_content))

            index = load_cloud_archive_index(
                cache_dir, ubuntu_series="noble", pocket="caracal"
            )
            # Should only have package from binary-amd64
            assert index.find_package("test-pkg") is not None


class TestMergePackageIndexes:
    """Tests for merge_package_indexes function."""

    def test_merge_empty(self) -> None:
        """Test merging with no indexes."""
        from packastack.packages import merge_package_indexes

        merged = merge_package_indexes()
        assert merged.find_package("anything") is None

    def test_merge_single(self) -> None:
        """Test merging single index."""
        from packastack.packages import merge_package_indexes

        index = PackageIndex()
        pkg = BinaryPackage(name="test", version="1.0", architecture="amd64")
        index.add_package(pkg, "main", "release")

        merged = merge_package_indexes(index)
        assert merged.find_package("test") is not None

    def test_merge_multiple(self) -> None:
        """Test merging multiple indexes."""
        from packastack.packages import merge_package_indexes

        index1 = PackageIndex()
        pkg1 = BinaryPackage(name="pkg1", version="1.0", architecture="amd64")
        index1.add_package(pkg1, "main", "release")

        index2 = PackageIndex()
        pkg2 = BinaryPackage(name="pkg2", version="2.0", architecture="amd64")
        index2.add_package(pkg2, "main", "updates")

        merged = merge_package_indexes(index1, index2)
        assert merged.find_package("pkg1") is not None
        assert merged.find_package("pkg2") is not None

    def test_merge_higher_version_wins(self) -> None:
        """Test that higher version wins in merge."""
        from packastack.packages import merge_package_indexes

        index1 = PackageIndex()
        pkg1 = BinaryPackage(name="pkg", version="1.0", architecture="amd64")
        index1.add_package(pkg1, "main", "release")

        index2 = PackageIndex()
        pkg2 = BinaryPackage(name="pkg", version="2.0", architecture="amd64")
        index2.add_package(pkg2, "main", "updates")

        merged = merge_package_indexes(index1, index2)
        assert merged.get_version("pkg") == "2.0"
