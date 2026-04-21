"""Shared contract helpers for the additive loop-architecture package."""

from __future__ import annotations

import re

CANONICAL_ID_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
STATUS_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
OVERRIDE_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


def normalize_canonical_id(value: str, *, field_label: str) -> str:
    normalized = value.strip().lower()
    if not CANONICAL_ID_RE.fullmatch(normalized):
        raise ValueError(
            f"{field_label} must match {CANONICAL_ID_RE.pattern!r} and use lowercase canonical tokens"
        )
    return normalized


def normalize_nonempty_text(value: str, *, field_label: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError(f"{field_label} may not be empty")
    return normalized


def normalize_status(value: str, *, field_label: str) -> str:
    normalized = value.strip().upper()
    if not STATUS_RE.fullmatch(normalized):
        raise ValueError(f"{field_label} must match {STATUS_RE.pattern!r}")
    return normalized


def normalize_override_name(value: str, *, field_label: str) -> str:
    normalized = value.strip().lower()
    if not OVERRIDE_RE.fullmatch(normalized):
        raise ValueError(f"{field_label} must match {OVERRIDE_RE.pattern!r}")
    return normalized


def dedupe_preserve_order(values: list[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return tuple(deduped)


__all__ = [
    "CANONICAL_ID_RE",
    "STATUS_RE",
    "OVERRIDE_RE",
    "dedupe_preserve_order",
    "normalize_canonical_id",
    "normalize_nonempty_text",
    "normalize_override_name",
    "normalize_status",
]
