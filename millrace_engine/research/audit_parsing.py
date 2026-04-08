"""Markdown parsing helpers for audit queue and evidence documents."""

from __future__ import annotations

import re

from .audit_models import _normalize_optional_text
from .parser_helpers import _parse_frontmatter_block

_SECTION_HEADING_RE = re.compile(r"^##+\s+(?P<title>.+?)\s*$")
_LIST_MARKER_RE = re.compile(r"^(?:[-*]|\d+\.)\s+")
_WHITESPACE_RE = re.compile(r"\s+")
_COMMAND_SECTION_NAMES = frozenset({"command", "commands", "command evidence", "required commands"})
_SUMMARY_SECTION_NAMES = frozenset({"summary", "summaries", "findings", "decision", "results"})


def _normalize_section_name(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value.strip()).casefold()


def _normalize_section_line(value: str) -> str | None:
    stripped = _LIST_MARKER_RE.sub("", value.strip())
    stripped = stripped.strip("`").strip()
    return _normalize_optional_text(stripped, field_name="section line")


def _extract_section_lines(text: str, *, section_names: frozenset[str]) -> tuple[str, ...]:
    _, body = _parse_frontmatter_block(text)
    collected: list[str] = []
    active_section: str | None = None
    in_code_block = False

    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue

        heading_match = _SECTION_HEADING_RE.match(stripped)
        if heading_match and not in_code_block:
            candidate = _normalize_section_name(heading_match.group("title"))
            active_section = candidate if candidate in section_names else None
            continue

        if in_code_block or active_section is None or not stripped:
            continue

        normalized = _normalize_section_line(raw_line)
        if normalized is not None:
            collected.append(normalized)

    deduped: list[str] = []
    seen: set[str] = set()
    for item in collected:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return tuple(deduped)


__all__ = [
    "_COMMAND_SECTION_NAMES",
    "_SUMMARY_SECTION_NAMES",
    "_extract_section_lines",
]
