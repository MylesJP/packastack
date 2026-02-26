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

"""HTTP client for OpenAI-compatible chat completion APIs.

Provides a thin wrapper around ``requests`` to call any model exposed
via the OpenAI ``/v1/chat/completions`` endpoint (OpenAI, OpenRouter,
Ollama, vLLM, Together, Groq, etc.).  All errors are caught and
returned as ``AIResponse(success=False, ...)`` so that AI features are
non-fatal.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass
class AIResponse:
    """Result of an AI API call."""

    success: bool
    content: str = ""
    error: str = ""
    tokens_used: int = 0


def get_api_key(cfg: dict[str, Any]) -> str | None:
    """Get API key from environment variables, falling back to config.

    Precedence:
        1. ``PACKASTACK_AI_API_KEY`` (project-specific)
        2. ``OPENAI_API_KEY`` (common convention)
        3. ``ANTHROPIC_API_KEY`` (backward compatibility)
        4. ``cfg["ai"]["api_key"]`` (config file)

    Args:
        cfg: Packastack configuration dictionary.

    Returns:
        API key string or None if not configured.
    """
    return (
        os.environ.get("PACKASTACK_AI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or cfg.get("ai", {}).get("api_key")
    )


def get_base_url(cfg: dict[str, Any]) -> str:
    """Get the API base URL from environment or config.

    Precedence:
        1. ``PACKASTACK_AI_BASE_URL`` environment variable
        2. ``cfg["ai"]["base_url"]`` config value
        3. ``DEFAULT_BASE_URL`` constant

    Args:
        cfg: Packastack configuration dictionary.

    Returns:
        Base URL string (without trailing slash).
    """
    url = (
        os.environ.get("PACKASTACK_AI_BASE_URL")
        or cfg.get("ai", {}).get("base_url")
        or DEFAULT_BASE_URL
    )
    return url.rstrip("/")


def is_ai_available(cfg: dict[str, Any]) -> bool:
    """Check if AI features are available (API key configured).

    Args:
        cfg: Packastack configuration dictionary.

    Returns:
        True if an API key is set.
    """
    return bool(get_api_key(cfg))


def call_ai(
    system_prompt: str,
    user_message: str,
    cfg: dict[str, Any],
) -> AIResponse:
    """Call an AI model via the OpenAI-compatible chat completions API.

    Makes a single POST request to ``{base_url}/chat/completions``
    with the given system prompt and user message.  All errors
    (network, auth, timeout) are caught and returned as
    ``AIResponse(success=False, ...)``.

    Args:
        system_prompt: System instructions for the model.
        user_message: User message containing the context to diagnose.
        cfg: Packastack configuration dictionary (for model, timeout, key).

    Returns:
        AIResponse with the model's text response on success.
    """
    api_key = get_api_key(cfg)
    if not api_key:
        return AIResponse(success=False, error="No API key configured")

    ai_cfg = cfg.get("ai", {})
    model = ai_cfg.get("model", "anthropic/claude-sonnet-4-5-20250929")
    max_tokens = ai_cfg.get("max_tokens", 8192)
    timeout = ai_cfg.get("timeout", 120)
    base_url = get_base_url(cfg)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }

    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except requests.Timeout:
        return AIResponse(success=False, error="API request timed out")
    except requests.ConnectionError as exc:
        return AIResponse(success=False, error=f"Connection error: {exc}")
    except requests.RequestException as exc:
        return AIResponse(success=False, error=f"Request failed: {exc}")

    if resp.status_code == 429:
        return AIResponse(success=False, error="Rate limited by API")

    if resp.status_code != 200:
        try:
            body = resp.json()
            msg = body.get("error", {}).get("message", resp.text[:200])
        except Exception:
            msg = resp.text[:200]
        return AIResponse(
            success=False,
            error=f"API error {resp.status_code}: {msg}",
        )

    try:
        body = resp.json()
        choices = body.get("choices", [])
        content = choices[0].get("message", {}).get("content", "") if choices else ""
        usage = body.get("usage", {})
        tokens = usage.get("prompt_tokens", 0) + usage.get(
            "completion_tokens", 0
        )
        return AIResponse(success=True, content=content, tokens_used=tokens)
    except (KeyError, ValueError, TypeError) as exc:
        return AIResponse(
            success=False, error=f"Failed to parse API response: {exc}"
        )
