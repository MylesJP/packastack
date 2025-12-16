# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""A lightweight, Python implementation of Debian's ``uscan``.

The real ``uscan`` tool reads a ``debian/watch`` file, fetches upstream
release listings, and identifies the newest available tarball.  The
implementation here mirrors the core behaviour needed for automation:

* Parse version 3+ ``debian/watch`` entries (including ``opts=`` blocks)
* Fetch the upstream page and apply the supplied regex to discover
  candidate release artifacts
* Apply common mangle directives (``uversionmangle``, ``downloadurlmangle``,
  and ``filenamemangle``)
* Compare discovered upstream versions to the packaged version from
  ``debian/changelog`` when present
* Return the discovered matches and the newest upstream version

This module is intentionally conservative and only covers the options we
need today.  It can be extended in the future as new watch-file features
become necessary.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cmp_to_key
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urljoin

import requests
from debian.debian_support import version_compare
from requests import Response, Session
from requests.exceptions import RequestException

from packastack.exceptions import DebianError, NetworkError


class _PackagedInfo(NamedTuple):
    """Lightweight packaged metadata gleaned from debian/changelog."""

    name: str
    version: str | None

DEFAULT_USER_AGENT = "packastack-uscan/0.1"


@dataclass
class WatchEntry:
    """Represents a single ``debian/watch`` stanza."""

    url: str
    pattern: re.Pattern[str]
    uversionmangle: list[str] = field(default_factory=list)
    downloadurlmangle: list[str] = field(default_factory=list)
    filenamemangle: list[str] = field(default_factory=list)
    pgpsigurlmangle: list[str] = field(default_factory=list)


@dataclass
class WatchMatch:
    """Represents a discovered upstream artifact."""

    version: str
    url: str
    filename: str
    signature_url: str | None = None


@dataclass
class UscanResult:
    """Holds the matches from scanning a watch file."""

    matches: list[WatchMatch]
    packaged_version: str | None = None
    needs_update: bool = False

    @property
    def signatures(self) -> list[str]:
        """Return signature URLs discovered alongside matches.

        The ``pgpsigurlmangle`` option can attach optional signature files to
        each upstream artifact.  This helper walks the collected matches and
        returns only the non-empty signature URLs, keeping the ordering of the
        matches intact.  Callers can use this to fetch detached signatures
        without re-parsing the match objects themselves.
        """
        return [match.signature_url for match in self.matches if match.signature_url]

    @property
    def latest(self) -> WatchMatch | None:
        """Return the newest upstream match if any.

        Debian's ``version_compare`` is used to determine ordering, so the
        version sorting matches packaging semantics rather than simple lexical
        ordering.  When no matches are present the property returns ``None`` so
        callers can short-circuit update checks gracefully.
        """
        if not self.matches:
            return None

        def _compare(a: WatchMatch, b: WatchMatch) -> int:
            """Order WatchMatch instances using Debian's version rules."""
            return version_compare(a.version, b.version)

        return max(self.matches, key=cmp_to_key(_compare))


class Uscan:
    """Scan upstream releases described by a ``debian/watch`` file."""

    def __init__(
        self,
        watch_file: str | Path,
        session: Session | None = None,
        timeout: int = 10,
    ):
        """Set up an instance bound to a ``debian/watch`` file.

        The constructor validates that the watch file exists, primes a requests
        session with a default ``User-Agent`` so upstream services can
        distinguish PackaStack accesses, and caches the timeout and lazy state
        used throughout the class.  No parsing or network I/O occurs here; the
        watch file is parsed only when :pyattr:`entries` is first accessed.

        Args:
            watch_file: Path to the ``debian/watch`` file.
            session: Optional requests session to reuse connections.
            timeout: HTTP timeout in seconds.
        """
        self.watch_file = Path(watch_file)
        if not self.watch_file.exists():
            raise DebianError(f"watch file not found: {self.watch_file}")

        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", DEFAULT_USER_AGENT)
        self.timeout = timeout
        self._entries: list[WatchEntry] | None = None
        self._packaged_info: _PackagedInfo | None = None

    @property
    def entries(self) -> list[WatchEntry]:
        """Parsed watch entries.

        The watch file is parsed lazily the first time this property is
        accessed and cached for subsequent calls.  Each stanza is normalised
        and expanded into a :class:`WatchEntry` capturing the URL, regex, and
        any mangle options so later scan operations can iterate efficiently.
        """
        if self._entries is None:
            self._entries = self._parse_watch_file()
        return self._entries

    def scan(self) -> UscanResult:
        """Fetch upstream listings and return discovered artifacts.

        The scan walks each parsed watch entry, fetches the upstream listing,
        extracts candidate filenames and versions, and aggregates them into a
        single :class:`UscanResult`.  When a packaged version is available from
        ``debian/changelog``, the method also determines whether a newer
        upstream release exists and sets ``needs_update`` accordingly.
        """
        matches: list[WatchMatch] = []
        packaged_info = self._read_packaged_info()
        for entry in self.entries:
            matches.extend(self._scan_entry(entry))
        needs_update = False
        if packaged_info.version and matches:
            latest = UscanResult(matches).latest
            if latest and version_compare(latest.version, packaged_info.version) > 0:
                needs_update = True

        return UscanResult(
            matches=matches,
            packaged_version=packaged_info.version,
            needs_update=needs_update,
        )

    def _parse_watch_file(self) -> list[WatchEntry]:
        """Parse the watch file into structured entries.

        The parser enforces watch format version 3 or newer, strips comments
        and empty lines, handles line continuations, and substitutes common
        tokens such as ``@PACKAGE@`` using changelog metadata.  Each resulting
        entry string is then handed to :meth:`_parse_watch_entry` for detailed
        interpretation.  A :class:`DebianError` is raised when mandatory
        metadata is missing or no usable entries are found.
        """
        raw = self.watch_file.read_text(encoding="utf-8")
        lines = [line.strip() for line in raw.splitlines() if line.strip()]

        version_line = next(
            (line for line in lines if line.lower().startswith("version=")), None
        )
        if not version_line:
            raise DebianError("watch file missing version declaration (e.g. version=4)")

        version = version_line.split("=", maxsplit=1)[1].strip()
        try:
            parsed_version = int(version)
        except ValueError as exc:
            raise DebianError(f"unsupported watch file version: {version}") from exc

        if parsed_version < 3:
            raise DebianError(f"unsupported watch file version: {version}")

        entries: list[str] = []
        buffer = ""
        for line in lines:
            if line.lower().startswith("version="):
                continue
            if line.startswith("#"):
                continue

            # Handle line continuations with trailing backslashes
            if line.endswith("\\"):
                buffer += line[:-1].rstrip() + " "
                continue

            buffer += line
            entries.append(buffer.strip())
            buffer = ""

        package_name = self._read_packaged_info().name

        parsed_entries = [
            self._parse_watch_entry(self._substitute_tokens(entry, package_name))
            for entry in entries
            if entry
        ]
        if not parsed_entries:
            raise DebianError("no usable entries found in watch file")
        return parsed_entries

    def _parse_watch_entry(self, entry: str) -> WatchEntry:
        """Parse a single watch entry string.

        The entry is split into whitespace-delimited parts, ``opts=`` are
        parsed into structured mangle directives, and the remaining tokens are
        treated as the upstream URL and the regex pattern to discover
        artifacts.  Perl-style regex fragments are normalised before compiling
        so the pattern behaves as expected under Python's regex engine.
        """
        # Entries are whitespace separated once continuations are resolved.
        parts = re.split(r"\s+", entry.strip())
        opts_block = next((p for p in parts if p.startswith("opts=")), None)
        opts = self._parse_opts(opts_block) if opts_block else {}

        # Remove opts=... token so the remaining two tokens are url + pattern
        without_opts = [p for p in parts if not p.startswith("opts=")]

        # Some watch entries embed the regex directly in the URL, yielding only
        # a single token after opts=.  In that case extract the directory URL
        # and the filename regex from the final path segment.
        url: str
        pattern: str
        if len(without_opts) == 1:
            combined = without_opts[0]
            base, sep, tail = combined.rpartition("/")
            if not sep or not tail:
                raise DebianError(f"invalid watch entry: {entry}")
            url = base + "/"
            pattern = tail
        elif len(without_opts) >= 2:
            url = without_opts[0]
            pattern = without_opts[1]
        else:
            raise DebianError(f"invalid watch entry: {entry}")

        pattern = self._normalize_regex(pattern)

        try:
            compiled_pattern: re.Pattern[str] = re.compile(pattern)
        except re.error as exc:
            raise DebianError(f"invalid regex in watch entry: {exc}") from exc

        return WatchEntry(
            url=url,
            pattern=compiled_pattern,
            uversionmangle=self._mangle_list(opts.get("uversionmangle")),
            downloadurlmangle=self._mangle_list(opts.get("downloadurlmangle")),
            filenamemangle=self._mangle_list(
                opts.get("filenamemangle") or opts.get("dversionmangle")
            ),
            pgpsigurlmangle=self._mangle_list(opts.get("pgpsigurlmangle")),
        )

    @staticmethod
    def _parse_opts(opts_block: str) -> dict[str, str]:
        """Parse the ``opts=`` portion of a watch entry.

        The ``opts`` segment contains comma-separated ``key=value`` pairs or
        bare flags.  Quotes around the whole block are removed to align with
        watch-file conventions.  The function returns a mapping of option names
        to their string values, using an empty string for flag-only entries.
        """
        opts_block = opts_block.removeprefix("opts=").strip()
        if opts_block.startswith('"') and opts_block.endswith('"'):
            opts_block = opts_block[1:-1]
        if not opts_block:
            return {}

        opts: dict[str, str] = {}
        for raw_opt in opts_block.split(","):
            if not raw_opt:
                continue
            if "=" in raw_opt:
                key, value = raw_opt.split("=", maxsplit=1)
                opts[key.strip()] = value.strip()
            else:
                opts[raw_opt.strip()] = ""
        return opts

    @staticmethod
    def _mangle_list(value: str | None) -> list[str]:
        """Split chained mangles into a list.

        uscan allows multiple ``mangle`` expressions separated by semicolons in
        a single option.  This helper converts the raw option string into an
        ordered list while skipping empty segments so that later processing can
        apply the substitutions sequentially.
        """
        if not value:
            return []
        return [part for part in value.split(";") if part]

    def _scan_entry(self, entry: WatchEntry) -> list[WatchMatch]:
        """Scan a single watch entry.

        The method fetches the upstream page, extracts link targets when HTML
        is present, and iterates the compiled watch regex over each target to
        identify artifacts.  For every match it applies mangle rules to derive
        the upstream version, download URL, optional signature URL, and final
        filename.  Duplicate discoveries are deduplicated before returning the
        list of :class:`WatchMatch` objects.
        """
        response = self._http_get(entry.url)
        content = response.text

        targets = self._extract_links(content)
        available_links = set(targets)
        if not targets:
            targets = [content]
            available_links = set()

        matches: list[WatchMatch] = []
        seen: set[tuple[str, str, str, str | None]] = set()
        for target in targets:
            for match in entry.pattern.finditer(target):
                version = self._extract_version(match)
                version = self._apply_mangles(version, entry.uversionmangle)

                matched_url = match.group(0)
                download_url = urljoin(
                    entry.url, self._apply_mangles(matched_url, entry.downloadurlmangle)
                )

                filename = self._apply_mangles(matched_url, entry.filenamemangle)
                if not filename:
                    filename = download_url.rsplit("/", maxsplit=1)[-1]

                signature_url: str | None = None
                if entry.pgpsigurlmangle:
                    sig_target = self._apply_mangles(
                        matched_url, entry.pgpsigurlmangle
                    )
                    if sig_target in available_links:
                        signature_url = urljoin(entry.url, sig_target)

                key = (version, download_url, filename, signature_url)
                if key in seen:
                    continue
                seen.add(key)

                matches.append(
                    WatchMatch(
                        version=version,
                        url=download_url,
                        filename=filename,
                        signature_url=signature_url,
                    )
                )

        return matches

    @staticmethod
    def _extract_links(content: str) -> list[str]:
        """Return href targets from HTML content when present.

        The helper feeds the content through :class:`html.parser.HTMLParser` and
        collects ``href`` attributes from ``<a>`` tags, preserving their order
        of appearance.  It only returns the raw attribute values, leaving any
        subsequent joining or mangle logic to the caller.
        """

        class _AnchorParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.links: list[str] = []
                """Collect ``href`` attribute values encountered while parsing."""

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
                """Record anchor ``href`` targets in the order they appear."""
                if tag.lower() != "a":
                    return
                href = dict(attrs).get("href")
                if href:
                    self.links.append(href)

        parser = _AnchorParser()
        parser.feed(content)
        return parser.links

    def _http_get(self, url: str) -> Response:
        """Wrapper around HTTP GET with consistent error handling.

        All network access flows through this method so error handling is
        uniform.  It invokes ``requests`` with the configured timeout and
        re-raises any request failures as :class:`NetworkError` to keep the
        public API free of transport-specific exceptions.
        """
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            return response
        except RequestException as exc:  # pragma: no cover - exercised in runtime
            raise NetworkError(f"failed to fetch {url}: {exc}") from exc

    @staticmethod
    def _extract_version(match: re.Match[str]) -> str:
        """Best-effort extraction of the upstream version from a regex match.

        The watch regex is expected to capture the upstream version either via
        a named ``(?P<version>...)`` group or the first positional group.  If
        neither exists, the method raises :class:`DebianError` to signal that
        the watch entry is malformed for version extraction.
        """
        if "version" in match.re.groupindex:
            return match.group("version")

        # Fall back to the first capturing group
        if match.groups():
            return match.group(1)

        raise DebianError(
            "watch regex must expose an upstream version via a named "
            "group 'version' or at least one capturing group"
        )

    @staticmethod
    def _apply_mangles(value: str, mangles: Iterable[str]) -> str:
        """Apply sed-style substitution expressions in order.

        Each mangle expression is executed sequentially against the input
        value, allowing complex transformations such as stripping prefixes or
        renaming files.  Expressions that do not start with ``s`` are ignored to
        mirror ``uscan``'s sed-like behaviour.
        """
        result = value
        for mangle in mangles:
            result = Uscan._apply_single_mangle(result, mangle)
        return result

    @staticmethod
    def _apply_single_mangle(value: str, mangle: str) -> str:
        """Apply a single sed-style substitution.

        Mangling follows ``sed`` syntax ``s<delim>pattern<delim>replacement<delim>flags``.
        Delimiters are arbitrary and escaped delimiters are honoured.  The
        function normalises Perl-style regex fragments, respects ``i`` and ``g``
        flags for case-insensitivity and global substitution, and leaves values
        unchanged when the expression is malformed or not a substitution.
        """
        if not mangle.startswith("s"):
            return value

        sep = mangle[1]
        parts = Uscan._split_unescaped(mangle[2:], sep)
        if len(parts) < 2:
            raise DebianError(f"invalid mangle expression: {mangle}")

        pattern = Uscan._normalize_regex(parts[0])
        replacement = Uscan._normalize_regex(parts[1])
        flags = parts[2] if len(parts) > 2 else ""

        re_flags = re.IGNORECASE if "i" in flags else 0
        count = 0 if "g" in flags else 1
        return re.sub(pattern, replacement, value, count=count, flags=re_flags)

    @staticmethod
    def _split_unescaped(value: str, delimiter: str) -> list[str]:
        """Split a string on a delimiter, ignoring escaped delimiters.

        This utility mirrors how ``sed`` parses substitution tokens: the string
        is broken at unescaped delimiter characters while escaped delimiters are
        retained in the resulting segments.  It enables reliable parsing of
        mangle expressions that may include literal delimiter characters.
        """
        parts: list[str] = []
        current: list[str] = []
        escaped = False

        for char in value:
            if escaped:
                current.append(char)
                escaped = False
                continue

            if char == "\\":
                current.append(char)
                escaped = True
                continue

            if char == delimiter:
                parts.append("".join(current))
                current = []
                continue

            current.append(char)

        parts.append("".join(current))
        return parts

    def _read_packaged_info(self) -> _PackagedInfo:
        """Return package name and latest packaged version when available.

        The method caches a small tuple containing the package name (derived
        from the watch directory or the changelog header) and the most recent
        packaged version.  It reads only the first line of ``debian/changelog``
        to avoid the overhead of full parsing while still reflecting the latest
        packaged upload.
        """

        if self._packaged_info is not None:
            return self._packaged_info

        package_name = self.watch_file.parent.name
        packaged_version: str | None = None

        changelog_path = self.watch_file.parent / "changelog"
        if changelog_path.exists():
            lines = changelog_path.read_text(encoding="utf-8").splitlines()
            if lines:
                header_match = re.match(r"^(?P<name>[^\s]+) \((?P<version>[^)]+)\)", lines[0])
                if header_match:
                    package_name = header_match.group("name")
                    packaged_version = header_match.group("version")

        self._packaged_info = _PackagedInfo(name=package_name, version=packaged_version)
        return self._packaged_info

    @staticmethod
    def _substitute_tokens(value: str, package_name: str) -> str:
        """Apply watch variable substitutions.

        Current support mirrors the portions of ``uscan`` needed by PackaStack:
        it replaces ``@PACKAGE@`` with the source package name so watch entries
        can template the upstream URL.  Additional substitutions can be added
        later as new watch features are required.
        """

        substitutions = {
            "@PACKAGE@": package_name,
        }

        for needle, replacement in substitutions.items():
            value = value.replace(needle, replacement)
        return value

    @staticmethod
    def _normalize_regex(pattern: str) -> str:
        """Best-effort conversion of Perl regex escapes to Python.

        ``uscan`` patterns and mangles may contain Perl-specific escapes that
        Python's regex engine does not understand.  This helper translates
        ``\Q...\E`` literal sections into escaped text and converts Perl
        backreferences (``$1`` or ``\1``) into Python's ``\g<1>`` form so compiled
        patterns and replacement strings behave as users expect.
        """

        # Translate \Q...\E to escaped literals
        def _escape_literal(match: re.Match[str]) -> str:
            return re.escape(match.group(1))

        normalized = re.sub(r"\\Q(.*?)\\E", _escape_literal, pattern)
        # Convert Perl-style numeric backreferences ($1 or \1) to Python's
        # ``\g<1>`` form so replacement strings do not leave literal
        # backslashes behind (``\1`` is treated as a literal when doubled).
        normalized = re.sub(r"\$(\d+)", lambda m: f"\\g<{m.group(1)}>", normalized)
        normalized = re.sub(r"\\(\d+)", lambda m: f"\\g<{m.group(1)}>", normalized)
        return normalized
