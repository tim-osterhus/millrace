"""Idempotency and equivalence helpers for runtime-effect operation runners."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel

from .types import OperationErrorFactory


def normalized_model_payload(
    document: BaseModel,
    *,
    exclude: set[str] | None = None,
) -> str:
    return json.dumps(
        document.model_dump(mode="json", exclude=exclude),
        sort_keys=True,
        separators=(",", ":"),
    )


def normalized_markdown_content(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")


def normalized_markdown_sha256(content: str) -> str:
    normalized = normalized_markdown_content(content).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def read_markdown_checksum(
    path: Path,
    *,
    failure_class: str,
    error_factory: OperationErrorFactory,
) -> str:
    try:
        checksum = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise error_factory(
            failure_class,
            f"markdown checksum cannot be read: {exc}",
        ) from exc
    if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
        raise error_factory(failure_class, f"markdown checksum is invalid: {path.name}")
    return checksum


def ensure_contains_all(
    actual: tuple[str, ...],
    expected: tuple[str, ...],
    *,
    field_name: str,
    missing_label: str = "item(s)",
) -> None:
    missing = [item for item in expected if item not in actual]
    if missing:
        raise ValueError(f"{field_name} missing {missing_label}: {', '.join(missing)}")


def unique_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


__all__ = [
    "ensure_contains_all",
    "normalized_markdown_content",
    "normalized_markdown_sha256",
    "normalized_model_payload",
    "read_markdown_checksum",
    "unique_tuple",
]
