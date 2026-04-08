"""Helpers for the packaged baseline asset bundle."""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import PurePosixPath
from typing import Any, cast

_ASSET_PACKAGE = "millrace_engine.assets"
_MANIFEST_NAME = "manifest.json"

__all__ = [
    "iter_packaged_baseline_directories",
    "iter_packaged_baseline_files",
    "iter_packaged_baseline_family_files",
    "load_packaged_baseline_manifest",
    "packaged_baseline_asset",
    "packaged_baseline_bundle_version",
    "packaged_baseline_file_entry",
]


def _assets_root() -> Traversable:
    return files(_ASSET_PACKAGE)


def _manifest_entries(name: str) -> tuple[dict[str, Any], ...]:
    entries = load_packaged_baseline_manifest().get(name)
    if not isinstance(entries, list):
        raise RuntimeError(f"packaged baseline manifest is missing a {name!r} list")
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError(f"packaged baseline manifest has a non-object {name[:-1]} entry")
        normalized.append(cast(dict[str, Any], entry))
    return tuple(normalized)


@cache
def load_packaged_baseline_manifest() -> dict[str, Any]:
    """Return the cached packaged baseline manifest payload."""

    payload = _assets_root().joinpath(_MANIFEST_NAME).read_text(encoding="utf-8")
    manifest = json.loads(payload)
    if not isinstance(manifest, dict):
        raise RuntimeError("packaged baseline manifest payload must be an object")
    return cast(dict[str, Any], manifest)


def packaged_baseline_bundle_version() -> str:
    """Return the stable bundle version from the packaged baseline manifest."""

    version = load_packaged_baseline_manifest().get("bundle_version")
    if not isinstance(version, str) or not version:
        raise RuntimeError("packaged baseline manifest is missing a bundle version")
    return version


def iter_packaged_baseline_directories() -> tuple[dict[str, Any], ...]:
    """Return directory entries from the packaged baseline manifest."""

    return _manifest_entries("directories")


def iter_packaged_baseline_files() -> tuple[dict[str, Any], ...]:
    """Return file entries from the packaged baseline manifest."""

    return _manifest_entries("files")


@cache
def _packaged_file_entry_index() -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for entry in iter_packaged_baseline_files():
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            raise RuntimeError("packaged baseline file entry is missing a path")
        index[path] = entry
    return index


def packaged_baseline_file_entry(relative_path: str) -> dict[str, Any] | None:
    """Return one packaged baseline file entry by relative path."""

    return _packaged_file_entry_index().get(relative_path)


def iter_packaged_baseline_family_files(family: str) -> tuple[dict[str, Any], ...]:
    """Return packaged file entries for one manifest family."""

    normalized = family.strip()
    if not normalized:
        raise ValueError("family may not be empty")
    return tuple(
        entry
        for entry in iter_packaged_baseline_files()
        if entry.get("family") == normalized
    )


def packaged_baseline_asset(relative_path: str) -> Traversable:
    """Resolve one packaged asset by workspace-relative path."""

    raw_parts = relative_path.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError(f"invalid packaged baseline asset path: {relative_path!r}")
    path = PurePosixPath(relative_path)
    if path.is_absolute() or not path.parts:
        raise ValueError("packaged baseline asset paths must be non-empty and relative")
    return _assets_root().joinpath(*path.parts)
