"""Shared support helpers for the research governance surface."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .normalization_helpers import _normalize_optional_text, _normalize_required_text, _normalize_token_sequence
from .path_helpers import _relative_path
from .persistence_helpers import _load_json_object, _sha256_text


GOVERNANCE_REPORT_SCHEMA_VERSION = "1.0"
DEFAULT_PINNED_FAMILY_POLICY_FIELDS = (
    "family_cap_mode",
    "initial_family_max_specs",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_cap(value: object, *, minimum: int = 0) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = minimum
    return max(minimum, normalized)


def _normalize_bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0"}:
            return False
    return default


def _normalize_scalar(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return tuple(_normalize_scalar(item) for item in value)
    if isinstance(value, dict):
        return {
            str(key).strip(): _normalize_scalar(item)
            for key, item in value.items()
            if str(key).strip()
        }
    return value


def _file_sha256_or_none(path: Path) -> str | None:
    if not path.exists():
        return None
    return _sha256_text(path.read_text(encoding="utf-8"))


def _json_scalar_map(values: dict[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for raw_key, raw_value in values.items():
        key = str(raw_key).strip()
        if not key:
            continue
        normalized[key] = _normalize_scalar(raw_value)
    return normalized


def _normalize_datetime_or_none(value: datetime | str | None) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


__all__ = [
    "DEFAULT_PINNED_FAMILY_POLICY_FIELDS",
    "GOVERNANCE_REPORT_SCHEMA_VERSION",
    "_file_sha256_or_none",
    "_json_scalar_map",
    "_load_json_object",
    "_normalize_bool",
    "_normalize_cap",
    "_normalize_datetime_or_none",
    "_normalize_optional_text",
    "_normalize_required_text",
    "_normalize_scalar",
    "_normalize_token_sequence",
    "_relative_path",
    "_utcnow",
]
