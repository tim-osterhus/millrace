"""Shared path token and rendering helpers for research-family modules."""

from __future__ import annotations

from pathlib import Path


def _normalize_path_token(value: str | Path | None) -> str:
    if value is None:
        return ""
    if isinstance(value, Path):
        return value.as_posix()
    stripped = value.strip()
    if not stripped:
        return ""
    return Path(stripped).as_posix()


def _normalize_path_sequence(values: list[str | Path]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = _normalize_path_token(value)
        if not token or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return tuple(deduped)


def _relative_path(path: Path, *, relative_to: Path) -> str:
    try:
        return path.relative_to(relative_to).as_posix()
    except ValueError:
        return path.as_posix()


def _path_token(path: Path, *, relative_to: Path | None = None) -> str:
    candidate = path
    if relative_to is not None:
        try:
            candidate = path.relative_to(relative_to)
        except ValueError:
            pass
    return candidate.as_posix()


def _resolve_path_token(path_token: str | Path, *, relative_to: Path) -> Path:
    candidate = Path(path_token)
    if candidate.is_absolute():
        return candidate
    return relative_to / candidate
