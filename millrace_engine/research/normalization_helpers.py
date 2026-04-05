"""Shared normalization primitives for research-family contracts."""

from __future__ import annotations

from pathlib import Path


def _normalize_required_text(value: str, *, field_name: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError(f"{field_name} may not be empty")
    return normalized


def _normalize_optional_text(value: str | Path | None) -> str:
    if value is None:
        return ""
    if isinstance(value, Path):
        value = value.as_posix()
    return " ".join(value.strip().split())


def _normalize_optional_text_or_none(value: str | Path | None) -> str | None:
    normalized = _normalize_optional_text(value)
    return normalized or None


def _normalize_token_sequence(values: list[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = value.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return tuple(deduped)


def _normalize_text_sequence(values: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if not values:
        return ()
    return _normalize_token_sequence([" ".join(str(item).strip().split()) for item in values])
