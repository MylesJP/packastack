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

"""Tests for packastack.ai.client module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from packastack.ai import client


class TestGetApiKey:
    """Tests for get_api_key function."""

    def test_packastack_env_var_takes_precedence(self) -> None:
        """Test that PACKASTACK_AI_API_KEY takes highest precedence."""
        cfg = {"ai": {"api_key": "config-key"}}
        env = {
            "PACKASTACK_AI_API_KEY": "packastack-key",
            "OPENAI_API_KEY": "openai-key",
            "ANTHROPIC_API_KEY": "anthropic-key",
        }
        with patch.dict("os.environ", env, clear=True):
            assert client.get_api_key(cfg) == "packastack-key"

    def test_openai_env_var_second(self) -> None:
        """Test that OPENAI_API_KEY is used when PACKASTACK var not set."""
        cfg = {"ai": {"api_key": "config-key"}}
        env = {"OPENAI_API_KEY": "openai-key", "ANTHROPIC_API_KEY": "anthropic-key"}
        with patch.dict("os.environ", env, clear=True):
            assert client.get_api_key(cfg) == "openai-key"

    def test_anthropic_env_var_backward_compat(self) -> None:
        """Test that ANTHROPIC_API_KEY is still supported for backward compat."""
        cfg = {"ai": {"api_key": "config-key"}}
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "anthropic-key"}, clear=True):
            assert client.get_api_key(cfg) == "anthropic-key"

    def test_falls_back_to_config(self) -> None:
        """Test fallback to config when no env vars set."""
        cfg = {"ai": {"api_key": "config-key"}}
        with patch.dict("os.environ", {}, clear=True):
            assert client.get_api_key(cfg) == "config-key"

    def test_returns_none_when_not_configured(self) -> None:
        """Test returns None when neither env var nor config is set."""
        cfg = {"ai": {"api_key": None}}
        with patch.dict("os.environ", {}, clear=True):
            assert client.get_api_key(cfg) is None

    def test_returns_none_with_empty_config(self) -> None:
        """Test returns None with empty config dict."""
        with patch.dict("os.environ", {}, clear=True):
            assert client.get_api_key({}) is None


class TestGetBaseUrl:
    """Tests for get_base_url function."""

    def test_env_var_takes_precedence(self) -> None:
        """Test that PACKASTACK_AI_BASE_URL takes precedence."""
        cfg = {"ai": {"base_url": "https://config.example.com/v1"}}
        with patch.dict("os.environ", {"PACKASTACK_AI_BASE_URL": "https://env.example.com/v1"}, clear=True):
            assert client.get_base_url(cfg) == "https://env.example.com/v1"

    def test_falls_back_to_config(self) -> None:
        """Test fallback to config when env var not set."""
        cfg = {"ai": {"base_url": "https://config.example.com/v1"}}
        with patch.dict("os.environ", {}, clear=True):
            assert client.get_base_url(cfg) == "https://config.example.com/v1"

    def test_falls_back_to_default(self) -> None:
        """Test fallback to DEFAULT_BASE_URL when nothing configured."""
        with patch.dict("os.environ", {}, clear=True):
            assert client.get_base_url({}) == client.DEFAULT_BASE_URL

    def test_strips_trailing_slash(self) -> None:
        """Test trailing slash is stripped from URL."""
        cfg = {"ai": {"base_url": "http://localhost:11434/v1/"}}
        with patch.dict("os.environ", {}, clear=True):
            assert client.get_base_url(cfg) == "http://localhost:11434/v1"


class TestIsAiAvailable:
    """Tests for is_ai_available function."""

    def test_available_with_env_var(self) -> None:
        """Test returns True when env var is set."""
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
            assert client.is_ai_available({}) is True

    def test_unavailable_without_key(self) -> None:
        """Test returns False when no key configured."""
        with patch.dict("os.environ", {}, clear=True):
            assert client.is_ai_available({"ai": {"api_key": None}}) is False


class TestCallAi:
    """Tests for call_ai function."""

    def _make_cfg(self) -> dict:
        return {
            "ai": {
                "api_key": "test-key",
                "base_url": "https://api.example.com/v1",
                "model": "gpt-4o",
                "max_tokens": 100,
                "timeout": 10,
            },
        }

    def test_successful_call(self) -> None:
        """Test successful API call parses OpenAI response correctly."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "The build failed due to..."}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 30},
        }

        with patch("packastack.ai.client.requests.post", return_value=mock_resp) as mock_post:
            result = client.call_ai("system", "user msg", self._make_cfg())

            assert result.success is True
            assert result.content == "The build failed due to..."
            assert result.tokens_used == 80
            mock_post.assert_called_once()

            # Verify request structure (OpenAI format)
            call_kwargs = mock_post.call_args
            assert call_kwargs.kwargs["json"]["model"] == "gpt-4o"
            # System prompt is a message, not a top-level field
            messages = call_kwargs.kwargs["json"]["messages"]
            assert messages[0] == {"role": "system", "content": "system"}
            assert messages[1] == {"role": "user", "content": "user msg"}
            assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer test-key"
            # URL includes base_url + /chat/completions
            assert call_kwargs.args[0] == "https://api.example.com/v1/chat/completions"

    def test_no_api_key(self) -> None:
        """Test returns error when no API key."""
        with patch.dict("os.environ", {}, clear=True):
            result = client.call_ai("sys", "msg", {"ai": {"api_key": None}})
            assert result.success is False
            assert "No API key" in result.error

    def test_network_error(self) -> None:
        """Test handles connection errors gracefully."""
        with patch("packastack.ai.client.requests.post", side_effect=requests.ConnectionError("refused")):
            result = client.call_ai("sys", "msg", self._make_cfg())
            assert result.success is False
            assert "Connection error" in result.error

    def test_timeout(self) -> None:
        """Test handles timeout gracefully."""
        with patch("packastack.ai.client.requests.post", side_effect=requests.Timeout()):
            result = client.call_ai("sys", "msg", self._make_cfg())
            assert result.success is False
            assert "timed out" in result.error

    def test_rate_limit(self) -> None:
        """Test handles 429 rate limit response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        with patch("packastack.ai.client.requests.post", return_value=mock_resp):
            result = client.call_ai("sys", "msg", self._make_cfg())
            assert result.success is False
            assert "Rate limited" in result.error

    def test_api_error_with_json_body(self) -> None:
        """Test handles non-200 API error with JSON error body."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"error": {"message": "Invalid request"}}
        with patch("packastack.ai.client.requests.post", return_value=mock_resp):
            result = client.call_ai("sys", "msg", self._make_cfg())
            assert result.success is False
            assert "400" in result.error
            assert "Invalid request" in result.error

    def test_api_error_with_text_body(self) -> None:
        """Test handles non-200 API error with non-JSON body."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.side_effect = ValueError("not json")
        mock_resp.text = "Internal server error"
        with patch("packastack.ai.client.requests.post", return_value=mock_resp):
            result = client.call_ai("sys", "msg", self._make_cfg())
            assert result.success is False
            assert "500" in result.error

    def test_malformed_response(self) -> None:
        """Test handles malformed JSON response body."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"unexpected": "structure"}
        with patch("packastack.ai.client.requests.post", return_value=mock_resp):
            result = client.call_ai("sys", "msg", self._make_cfg())
            # Should succeed but with empty content (no choices found)
            assert result.success is True
            assert result.content == ""

    def test_request_exception(self) -> None:
        """Test handles generic request exception."""
        with patch("packastack.ai.client.requests.post", side_effect=requests.RequestException("generic")):
            result = client.call_ai("sys", "msg", self._make_cfg())
            assert result.success is False
            assert "Request failed" in result.error

    def test_uses_config_values(self) -> None:
        """Test that config model, max_tokens, timeout, base_url are used."""
        cfg = {
            "ai": {
                "api_key": "k",
                "base_url": "http://localhost:11434/v1",
                "model": "llama3",
                "max_tokens": 4096,
                "timeout": 60,
            },
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {},
        }

        with patch("packastack.ai.client.requests.post", return_value=mock_resp) as mock_post:
            client.call_ai("sys", "msg", cfg)
            call_kwargs = mock_post.call_args
            assert call_kwargs.args[0] == "http://localhost:11434/v1/chat/completions"
            assert call_kwargs.kwargs["json"]["model"] == "llama3"
            assert call_kwargs.kwargs["json"]["max_tokens"] == 4096
            assert call_kwargs.kwargs["timeout"] == 60
