# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for git_helpers module."""

import os
from unittest.mock import patch

import pytest

from packastack.build.git_helpers import (
    GitCommitError,
    ensure_no_merge_paths,
    extract_upstream_version,
    get_git_author_env,
    maybe_disable_gpg_sign,
    maybe_enable_sphinxdoc,
    no_gpg_sign_enabled,
)


class TestNoGpgSignEnabled:
    """Tests for no_gpg_sign_enabled function."""

    def test_returns_false_when_env_not_set(self):
        """Test that it returns False when PACKASTACK_NO_GPG_SIGN is not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("PACKASTACK_NO_GPG_SIGN", None)
            assert no_gpg_sign_enabled() is False

    def test_returns_true_for_1(self):
        """Test that it returns True when set to '1'."""
        with patch.dict(os.environ, {"PACKASTACK_NO_GPG_SIGN": "1"}):
            assert no_gpg_sign_enabled() is True

    def test_returns_true_for_true(self):
        """Test that it returns True when set to 'true'."""
        with patch.dict(os.environ, {"PACKASTACK_NO_GPG_SIGN": "true"}):
            assert no_gpg_sign_enabled() is True

    def test_returns_true_for_yes(self):
        """Test that it returns True when set to 'yes'."""
        with patch.dict(os.environ, {"PACKASTACK_NO_GPG_SIGN": "yes"}):
            assert no_gpg_sign_enabled() is True

    def test_returns_true_case_insensitive(self):
        """Test that it's case-insensitive."""
        with patch.dict(os.environ, {"PACKASTACK_NO_GPG_SIGN": "TRUE"}):
            assert no_gpg_sign_enabled() is True

    def test_returns_false_for_other_values(self):
        """Test that it returns False for other values."""
        with patch.dict(os.environ, {"PACKASTACK_NO_GPG_SIGN": "false"}):
            assert no_gpg_sign_enabled() is False


class TestMaybeDisableGpgSign:
    """Tests for maybe_disable_gpg_sign function."""

    def test_injects_no_gpg_sign_when_enabled(self):
        """Test that --no-gpg-sign is injected when env is set."""
        with patch.dict(os.environ, {"PACKASTACK_NO_GPG_SIGN": "1"}):
            cmd = ["git", "commit", "-m", "test message"]
            result = maybe_disable_gpg_sign(cmd)
            assert result == ["git", "commit", "--no-gpg-sign", "-m", "test message"]

    def test_does_not_modify_when_disabled(self):
        """Test that command is unchanged when env is not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("PACKASTACK_NO_GPG_SIGN", None)
            cmd = ["git", "commit", "-m", "test message"]
            result = maybe_disable_gpg_sign(cmd)
            assert result == cmd

    def test_only_affects_git_commit(self):
        """Test that only git commit commands are modified."""
        with patch.dict(os.environ, {"PACKASTACK_NO_GPG_SIGN": "1"}):
            # Non-commit git commands should be unchanged
            cmd = ["git", "push", "origin", "main"]
            result = maybe_disable_gpg_sign(cmd)
            assert result == cmd

    def test_handles_short_commands(self):
        """Test that short commands don't cause errors."""
        with patch.dict(os.environ, {"PACKASTACK_NO_GPG_SIGN": "1"}):
            cmd = ["git"]
            result = maybe_disable_gpg_sign(cmd)
            assert result == cmd

    def test_returns_same_list_when_not_modified(self):
        """Test that the original list is returned when not modified."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("PACKASTACK_NO_GPG_SIGN", None)
            cmd = ["git", "commit", "-m", "test"]
            result = maybe_disable_gpg_sign(cmd)
            assert result is cmd  # Same object, not a copy


class TestGetGitAuthorEnv:
    """Tests for get_git_author_env function."""

    def test_uses_debfullname_and_debemail(self, capsys):
        """Test that DEBFULLNAME and DEBEMAIL are used."""
        with patch.dict(os.environ, {
            "DEBFULLNAME": "Test User",
            "DEBEMAIL": "test@example.com",
        }, clear=True):
            env = get_git_author_env(debug=False)
            assert env["GIT_AUTHOR_NAME"] == "Test User"
            assert env["GIT_COMMITTER_NAME"] == "Test User"
            assert env["GIT_AUTHOR_EMAIL"] == "test@example.com"
            assert env["GIT_COMMITTER_EMAIL"] == "test@example.com"

    def test_falls_back_to_name_and_email(self, capsys):
        """Test fallback to NAME and EMAIL."""
        with patch.dict(os.environ, {
            "NAME": "Fallback User",
            "EMAIL": "fallback@example.com",
        }, clear=True):
            env = get_git_author_env(debug=False)
            assert env["GIT_AUTHOR_NAME"] == "Fallback User"
            assert env["GIT_AUTHOR_EMAIL"] == "fallback@example.com"

    def test_debfullname_takes_precedence(self, capsys):
        """Test that DEBFULLNAME takes precedence over NAME."""
        with patch.dict(os.environ, {
            "DEBFULLNAME": "Debian User",
            "NAME": "Generic User",
        }, clear=True):
            env = get_git_author_env(debug=False)
            assert env["GIT_AUTHOR_NAME"] == "Debian User"

    def test_returns_empty_dict_when_no_env(self, capsys):
        """Test that empty dict is returned when no env vars are set."""
        with patch.dict(os.environ, {}, clear=True):
            for key in ["DEBFULLNAME", "DEBEMAIL", "NAME", "EMAIL"]:
                os.environ.pop(key, None)
            env = get_git_author_env(debug=False)
            assert env == {}

    def test_partial_env_only_sets_available(self, capsys):
        """Test that only available env vars are set."""
        with patch.dict(os.environ, {"DEBFULLNAME": "Test"}, clear=True):
            for key in ["DEBEMAIL", "NAME", "EMAIL"]:
                os.environ.pop(key, None)
            env = get_git_author_env(debug=False)
            assert "GIT_AUTHOR_NAME" in env
            assert "GIT_AUTHOR_EMAIL" not in env


class TestEnsureNoMergePaths:
    """Tests for ensure_no_merge_paths function."""

    def test_creates_gitattributes_if_missing(self, tmp_path):
        """Test that .gitattributes is created if it doesn't exist."""
        result = ensure_no_merge_paths(tmp_path, ["launchpad.yaml"])
        assert result is True
        gitattributes = tmp_path / ".gitattributes"
        assert gitattributes.exists()
        content = gitattributes.read_text()
        assert "launchpad.yaml merge=ours" in content
        assert ".gitattributes merge=ours" in content

    def test_adds_to_existing_gitattributes(self, tmp_path):
        """Test that entries are added to existing .gitattributes."""
        gitattributes = tmp_path / ".gitattributes"
        gitattributes.write_text("*.pyc binary\n")

        result = ensure_no_merge_paths(tmp_path, ["launchpad.yaml"])
        assert result is True
        content = gitattributes.read_text()
        assert "*.pyc binary" in content
        assert "launchpad.yaml merge=ours" in content

    def test_is_idempotent(self, tmp_path):
        """Test that running twice doesn't duplicate entries."""
        ensure_no_merge_paths(tmp_path, ["launchpad.yaml"])
        result = ensure_no_merge_paths(tmp_path, ["launchpad.yaml"])
        assert result is False  # No changes made second time

        gitattributes = tmp_path / ".gitattributes"
        content = gitattributes.read_text()
        # Count occurrences
        assert content.count("launchpad.yaml merge=ours") == 1

    def test_always_protects_gitattributes_itself(self, tmp_path):
        """Test that .gitattributes is always protected."""
        result = ensure_no_merge_paths(tmp_path, [])
        assert result is True
        gitattributes = tmp_path / ".gitattributes"
        content = gitattributes.read_text()
        assert ".gitattributes merge=ours" in content


class TestMaybeEnableSphinxdoc:
    """Tests for maybe_enable_sphinxdoc function."""

    def test_adds_sphinxdoc_to_python3(self, tmp_path):
        """Test that sphinxdoc is added when --with python3 is present."""
        debian_dir = tmp_path / "debian"
        debian_dir.mkdir()
        rules = debian_dir / "rules"
        rules.write_text("#!/usr/bin/make -f\n%:\n\tdh $@ --with python3\n")

        result = maybe_enable_sphinxdoc(tmp_path)
        assert result is True
        content = rules.read_text()
        assert "--with python3,sphinxdoc" in content

    def test_adds_sphinxdoc_to_dh(self, tmp_path):
        """Test that sphinxdoc is added when dh $@ is present."""
        debian_dir = tmp_path / "debian"
        debian_dir.mkdir()
        rules = debian_dir / "rules"
        rules.write_text("#!/usr/bin/make -f\n%:\n\tdh $@\n")

        result = maybe_enable_sphinxdoc(tmp_path)
        assert result is True
        content = rules.read_text()
        assert "dh $@ --with sphinxdoc" in content

    def test_does_not_add_if_already_present(self, tmp_path):
        """Test that sphinxdoc is not added if already present."""
        debian_dir = tmp_path / "debian"
        debian_dir.mkdir()
        rules = debian_dir / "rules"
        rules.write_text("#!/usr/bin/make -f\n%:\n\tdh $@ --with python3,sphinxdoc\n")

        result = maybe_enable_sphinxdoc(tmp_path)
        assert result is False

    def test_returns_false_if_rules_missing(self, tmp_path):
        """Test that False is returned if debian/rules doesn't exist."""
        result = maybe_enable_sphinxdoc(tmp_path)
        assert result is False

    def test_returns_false_if_no_pattern_match(self, tmp_path):
        """Test that False is returned if no pattern matches."""
        debian_dir = tmp_path / "debian"
        debian_dir.mkdir()
        rules = debian_dir / "rules"
        rules.write_text("#!/usr/bin/make -f\ncustom-build:\n\techo custom\n")

        result = maybe_enable_sphinxdoc(tmp_path)
        assert result is False


class TestExtractUpstreamVersion:
    """Tests for extract_upstream_version function."""

    def test_strips_epoch_and_ubuntu_revision(self):
        """Test extracting version from epoch:upstream-debian."""
        assert extract_upstream_version("2:29.0.0-0ubuntu1") == "29.0.0"

    def test_strips_ubuntu_revision_only(self):
        """Test extracting version with no epoch."""
        assert extract_upstream_version("1.2.3-1ubuntu2") == "1.2.3"

    def test_handles_snapshot_version(self):
        """Test extracting version from snapshot with git hash."""
        result = extract_upstream_version("29.0.0+git2024010412345-0ubuntu1~snapshot")
        assert result == "29.0.0+git2024010412345"

    def test_handles_ppa_suffix(self):
        """Test extracting version with ppa suffix."""
        assert extract_upstream_version("1.2.3-1ubuntu2~ppa1") == "1.2.3"

    def test_handles_build_suffix(self):
        """Test extracting version with build suffix."""
        assert extract_upstream_version("1.2.3-0ubuntu1~build1") == "1.2.3"

    def test_returns_version_unchanged_if_no_ubuntu_revision(self):
        """Test that version without Ubuntu revision works."""
        assert extract_upstream_version("1.2.3") == "1.2.3"

    def test_handles_complex_upstream_version(self):
        """Test with complex upstream version containing dots and tildes."""
        assert extract_upstream_version("2:3.14.0~rc1-0ubuntu1") == "3.14.0~rc1"


class TestGitCommitError:
    """Tests for GitCommitError exception."""

    def test_basic_error(self):
        """Test basic error message."""
        error = GitCommitError("Commit failed")
        assert str(error) == "Commit failed"
        assert error.message == "Commit failed"
        assert error.returncode == 1

    def test_error_with_stderr(self):
        """Test error with stderr output."""
        error = GitCommitError("Commit failed", stderr="nothing to commit", returncode=1)
        assert str(error) == "Commit failed: nothing to commit"
        assert error.stderr == "nothing to commit"

    def test_error_with_custom_returncode(self):
        """Test error with custom return code."""
        error = GitCommitError("Commit failed", returncode=128)
        assert error.returncode == 128

    def test_is_exception(self):
        """Test that GitCommitError is an Exception."""
        assert issubclass(GitCommitError, Exception)
        with pytest.raises(GitCommitError):
            raise GitCommitError("Test error")
