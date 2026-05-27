"""Shared private validation helpers for workflow primitive contracts."""

from __future__ import annotations

from pathlib import PurePosixPath

from pydantic import ValidationInfo

from ..common import normalize_canonical_id, normalize_status


def _canonical(value: str, info: ValidationInfo) -> str:
    return normalize_canonical_id(value, field_label=info.field_name or "canonical id")


def _ensure_sequence(
    value: object,
    *,
    field_label: str,
    allow_empty: bool = False,
) -> tuple[object, ...]:
    if value is None:
        values: tuple[object, ...] = ()
    elif isinstance(value, str):
        values = (value,)
    else:
        try:
            values = tuple(value)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ValueError(f"{field_label} must be a sequence") from exc
    if not values and not allow_empty:
        raise ValueError(f"{field_label} must not be empty")
    return values


def _normalize_unique_id_tuple(
    value: object,
    *,
    field_label: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    raw = _ensure_sequence(value, field_label=field_label, allow_empty=allow_empty)
    normalized = [
        normalize_canonical_id(str(item), field_label=field_label)
        for item in raw
    ]
    return _reject_duplicates(normalized, field_label=field_label)


def _normalize_unique_status_tuple(
    value: object,
    *,
    field_label: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    raw = _ensure_sequence(value, field_label=field_label, allow_empty=allow_empty)
    normalized = [normalize_status(str(item), field_label=field_label) for item in raw]
    return _reject_duplicates(normalized, field_label=field_label)


def _reject_duplicates(values: list[str], *, field_label: str) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {field_label} value: {value}")
        seen.add(value)
        deduped.append(value)
    return tuple(deduped)


def _normalize_runtime_relative_path(value: str, *, field_label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_label} may not be empty")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or path.as_posix() == ".":
        raise ValueError(f"{field_label} must be a safe runtime-relative path")
    return path.as_posix()


def _normalize_artifact_filename(value: str, *, field_label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_label} may not be empty")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or path.as_posix() in {".", ".."}:
        raise ValueError(f"{field_label} must be a safe filename")
    if path.name != path.as_posix() or "/" in normalized or "\\" in normalized:
        raise ValueError(f"{field_label} must be a filename, not a path")
    return path.as_posix()


def _normalize_file_extension(value: str, *, field_label: str) -> str:
    normalized = value.strip().lower()
    if (
        not normalized.startswith(".")
        or normalized == "."
        or "/" in normalized
        or "\\" in normalized
    ):
        raise ValueError(f"{field_label} must be a lowercase file extension such as '.md'")
    return normalized

