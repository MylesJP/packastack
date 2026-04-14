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

"""Context collector registry for AI skills.

A *collector* is a named callable that returns a formatted string
section to include in the user message.  Skills declare which
collectors they need via ``requires_context`` in their frontmatter, so
new skills can reuse existing collectors without any Python edits.

Add a collector with :func:`register`; override a default with the same
name and ``replace=True`` (useful for tests).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packastack.ai.prompts import extract_sbuild_failure_section


class UnknownCollectorError(KeyError):
    """Raised when a skill requests a collector that isn't registered."""


@dataclass
class CollectorInputs:
    """Bundle of inputs shared by every collector.

    Not every field is populated for every call — collectors should
    return an empty string when the data they need is missing.
    """

    pkg_repo: Path
    pkg_name: str = ""
    version: str = ""
    ubuntu_series: str = ""
    arch: str = ""
    cfg: dict[str, Any] = field(default_factory=dict)

    # Optional — populated when available for the current skill invocation
    sbuild_result: Any = None
    patch_name: str = ""
    patch_content: str = ""
    patch_error: str = ""
    affected_files: dict[str, str] = field(default_factory=dict)
    missing_files: list[str] = field(default_factory=list)
    ai_memory_context: str = ""
    error_msg: str = ""


Collector = Callable[[CollectorInputs], str]

_REGISTRY: dict[str, Collector] = {}


def register(name: str, fn: Collector, *, replace: bool = False) -> None:
    """Register a collector under *name*.

    Args:
        name: Identifier used in skill frontmatter (e.g. ``"sbuild_log_tail"``).
        fn: Callable taking :class:`CollectorInputs` and returning a block
            of formatted text (empty string if no data is available).
        replace: If True, overwrite an existing collector with the same
            name.  Guards against accidental duplicates by default.

    Raises:
        ValueError: If *name* is already registered and *replace* is False.
    """
    if not replace and name in _REGISTRY:
        raise ValueError(f"collector already registered: {name}")
    _REGISTRY[name] = fn


def unregister(name: str) -> None:
    """Remove a collector from the registry (mainly for tests)."""
    _REGISTRY.pop(name, None)


def collect(name: str, inputs: CollectorInputs) -> str:
    """Run the named collector against *inputs*.

    Args:
        name: Registered collector name.
        inputs: Shared inputs.

    Returns:
        Formatted section string, or empty string if no data.

    Raises:
        UnknownCollectorError: If *name* is not registered.
    """
    fn = _REGISTRY.get(name)
    if fn is None:
        raise UnknownCollectorError(f"No collector registered for {name!r}")
    return fn(inputs)


def registered() -> list[str]:
    """Return a sorted list of registered collector names."""
    return sorted(_REGISTRY)


# ---------------------------------------------------------------------------
# Built-in collectors
# ---------------------------------------------------------------------------


def _read_file_safe(path: Path, max_lines: int = 0) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if max_lines > 0:
        lines = content.splitlines()
        if len(lines) > max_lines:
            lines = [
                *lines[:max_lines],
                f"... ({len(lines) - max_lines} lines truncated)",
            ]
            return "\n".join(lines)
    return content


def _max_file_lines(inputs: CollectorInputs) -> int:
    return int(inputs.cfg.get("ai", {}).get("max_file_lines", 0))


def _max_log_lines(inputs: CollectorInputs) -> int:
    return int(inputs.cfg.get("ai", {}).get("max_log_lines", 0))


def failure_header(inputs: CollectorInputs) -> str:
    """Package metadata header shared by most build/patch skills."""
    lines = []
    if inputs.pkg_name:
        lines.append(f"Package: {inputs.pkg_name}")
    if inputs.version:
        lines.append(f"Version: {inputs.version}")
    if inputs.ubuntu_series:
        lines.append(f"Distribution: {inputs.ubuntu_series}")
    if inputs.arch:
        lines.append(f"Architecture: {inputs.arch}")
    if inputs.error_msg:
        lines.append(f"Error: {inputs.error_msg}")
    return "\n".join(lines)


def sbuild_log_tail(inputs: CollectorInputs) -> str:
    """Relevant failure section of the sbuild log."""
    sr = inputs.sbuild_result
    if sr is None:
        return ""
    for log_path in (
        getattr(sr, "primary_log_path", None),
        getattr(sr, "stderr_log_path", None),
        getattr(sr, "stdout_log_path", None),
    ):
        if log_path and log_path.exists():
            excerpt = extract_sbuild_failure_section(
                log_path, max_lines=_max_log_lines(inputs)
            )
            if excerpt:
                return f"== sbuild failure log excerpt ==\n{excerpt}"
    return ""


def debian_control(inputs: CollectorInputs) -> str:
    """Contents of ``debian/control``."""
    content = _read_file_safe(
        inputs.pkg_repo / "debian" / "control", max_lines=_max_file_lines(inputs)
    )
    return f"== debian/control ==\n{content}" if content else ""


def debian_rules(inputs: CollectorInputs) -> str:
    """Contents of ``debian/rules``."""
    content = _read_file_safe(
        inputs.pkg_repo / "debian" / "rules", max_lines=_max_file_lines(inputs)
    )
    return f"== debian/rules ==\n{content}" if content else ""


def working_tree(inputs: CollectorInputs) -> str:
    """Git tree listing, debian/ files, and key upstream config files.

    Imported lazily to avoid a circular import with ``build_diagnosis``
    during module initialisation.
    """
    from packastack.ai.build_diagnosis import collect_working_tree_context

    return collect_working_tree_context(
        inputs.pkg_repo, max_file_lines=_max_file_lines(inputs)
    )


def patch_subject(inputs: CollectorInputs) -> str:
    """The failing patch itself plus the gbp pq import error output."""
    if not inputs.patch_content and not inputs.patch_error:
        return ""
    parts = []
    if inputs.patch_name or inputs.patch_content:
        parts.append(f"== Failing patch: {inputs.patch_name} ==")
        parts.append(inputs.patch_content)
    if inputs.patch_error:
        parts.append("")
        parts.append("== gbp pq import error output ==")
        parts.append(inputs.patch_error)
    return "\n".join(parts)


def patch_affected_files(inputs: CollectorInputs) -> str:
    """Current contents of files the failing patch modifies."""
    parts = []
    if inputs.missing_files:
        parts.append("== Files targeted by patch that NO LONGER EXIST ==")
        for fpath in inputs.missing_files:
            parts.append(f"  - {fpath}")
        parts.append(
            "(The patch hunks for these files need retargeting or dropping.)"
        )
    if inputs.affected_files:
        if parts:
            parts.append("")
        parts.append("== Current source file contents ==")
        for fpath, fcontent in inputs.affected_files.items():
            parts.append(f"--- {fpath} ---")
            parts.append(fcontent)
            parts.append("")
    return "\n".join(parts).rstrip()


def ai_memory(inputs: CollectorInputs) -> str:
    """Previous AI attempt history, if the caller supplied it."""
    return inputs.ai_memory_context or ""


def _register_builtins() -> None:
    builtins: list[tuple[str, Collector]] = [
        ("failure_header", failure_header),
        ("sbuild_log_tail", sbuild_log_tail),
        ("debian_control", debian_control),
        ("debian_rules", debian_rules),
        ("working_tree", working_tree),
        ("patch_subject", patch_subject),
        ("patch_affected_files", patch_affected_files),
        ("ai_memory", ai_memory),
    ]
    for name, fn in builtins:
        # replace=True so importing this module twice (e.g. in tests) is safe
        with contextlib.suppress(ValueError):
            register(name, fn)


_register_builtins()
