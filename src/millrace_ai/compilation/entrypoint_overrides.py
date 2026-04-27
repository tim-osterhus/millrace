"""Compiler validation helpers for entrypoint override paths."""

from __future__ import annotations

from pathlib import Path

from .outcomes import CompilerValidationError


def validate_entrypoint_override(stage_name: str, raw_path: str) -> str:
    normalized = normalize_relative_asset_path(raw_path)
    if (
        normalized is None
        or not normalized.startswith("entrypoints/")
        or not normalized.endswith(".md")
    ):
        raise CompilerValidationError(
            f"Invalid entrypoint override for stage `{stage_name}`: {raw_path}"
        )
    return normalized


def normalize_relative_asset_path(raw_path: str) -> str | None:
    text = raw_path.strip()
    if not text:
        return None

    path = Path(text)
    if path.is_absolute():
        return None

    normalized = path.as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized:
        return None
    if normalized == ".." or normalized.startswith("../") or "/../" in normalized:
        return None

    return normalized


__all__ = ["normalize_relative_asset_path", "validate_entrypoint_override"]
