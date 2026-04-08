"""Shared markdown and frontmatter parsing helpers for research modules."""

from __future__ import annotations

import re
from collections.abc import Callable

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*(?:\n|$)", flags=re.DOTALL)
_HEADING_RE = re.compile(r"^#\s+(?P<title>.+?)\s*$", flags=re.MULTILINE)


def _parse_frontmatter_block(text: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return {}, text

    fields: dict[str, str] = {}
    for line in match.group("body").splitlines():
        stripped = line.strip()
        if not stripped or ":" not in stripped:
            continue
        key, raw_value = stripped.split(":", 1)
        fields[key.strip()] = raw_value.strip()
    return fields, text[match.end() :]


def _split_frontmatter_block(text: str, *, boundary: str = "---") -> tuple[dict[str, str], str]:
    prefix = f"{boundary}\n"
    if not text.startswith(prefix):
        return {}, text
    end = text.find(f"\n{boundary}\n", len(boundary) + 1)
    if end == -1:
        return {}, text

    frontmatter: dict[str, str] = {}
    for raw_line in text[len(boundary) + 1 : end].splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        frontmatter[key.strip()] = value.strip()
    body = text[end + len(f"\n{boundary}\n") :]
    return frontmatter, body


def _parse_simple_frontmatter(text: str, *, boundary: str = "---") -> dict[str, str]:
    frontmatter, _ = _split_frontmatter_block(text, boundary=boundary)
    return frontmatter


def _markdown_section(text: str, heading: str, *, heading_prefix: str = "## ") -> str:
    target = heading.strip().casefold()
    current: list[str] = []
    capture = False
    in_code_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            if capture:
                current.append(line.rstrip())
            continue
        if stripped.startswith(heading_prefix) and not in_code_block:
            if capture and not in_code_block:
                break
            capture = not in_code_block and stripped[len(heading_prefix) :].strip().casefold() == target
            continue
        if capture:
            current.append(line.rstrip())
    return "\n".join(current).strip()


def _extract_heading_title(
    text: str,
    *,
    normalize: Callable[[str], str] | None = None,
) -> str | None:
    match = _HEADING_RE.search(text)
    if match is None:
        return None
    title = match.group("title").strip()
    if not title:
        return None
    return normalize(title) if normalize is not None else title
