"""Deployed baseline manifest models and persistence helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

import millrace_ai

from .paths import WorkspacePaths, _RUNTIME_ASSET_DIRS, workspace_paths


class _BaselineModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BaselineManifestEntry(_BaselineModel):
    relative_path: str
    asset_family: str
    original_sha256: str


class BaselineManifest(_BaselineModel):
    schema_version: str = "1.0"
    manifest_id: str
    seed_package_version: str
    entries: tuple[BaselineManifestEntry, ...]

    def entry_for(self, relative_path: str) -> BaselineManifestEntry:
        for entry in self.entries:
            if entry.relative_path == relative_path:
                return entry
        raise KeyError(relative_path)


def build_baseline_manifest(target: WorkspacePaths | Path | str) -> BaselineManifest:
    """Build a manifest from the currently deployed managed runtime assets."""

    paths = target if isinstance(target, WorkspacePaths) else workspace_paths(target)
    entries = tuple(_iter_manifest_entries(paths))
    manifest_id = _manifest_id_for_entries(
        schema_version="1.0",
        seed_package_version=millrace_ai.__version__,
        entries=entries,
    )
    return BaselineManifest(
        manifest_id=manifest_id,
        seed_package_version=millrace_ai.__version__,
        entries=entries,
    )


def load_baseline_manifest(target: WorkspacePaths | Path | str) -> BaselineManifest:
    paths = target if isinstance(target, WorkspacePaths) else workspace_paths(target)
    return BaselineManifest.model_validate_json(paths.baseline_manifest_file.read_text(encoding="utf-8"))


def write_baseline_manifest(
    target: WorkspacePaths | Path | str,
    manifest: BaselineManifest,
) -> Path:
    paths = target if isinstance(target, WorkspacePaths) else workspace_paths(target)
    paths.baseline_manifest_file.parent.mkdir(parents=True, exist_ok=True)
    paths.baseline_manifest_file.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return paths.baseline_manifest_file


def _iter_manifest_entries(paths: WorkspacePaths) -> tuple[BaselineManifestEntry, ...]:
    entries: list[BaselineManifestEntry] = []
    for asset_family in _RUNTIME_ASSET_DIRS:
        family_root = paths.runtime_root / asset_family
        if not family_root.exists():
            continue
        for file_path in sorted(path for path in family_root.rglob("*") if path.is_file()):
            relative_within_family = file_path.relative_to(family_root)
            if any(part.startswith(".") for part in relative_within_family.parts):
                continue
            relative_path = Path(asset_family, relative_within_family).as_posix()
            entries.append(
                BaselineManifestEntry(
                    relative_path=relative_path,
                    asset_family=asset_family,
                    original_sha256=_sha256_file(file_path),
                )
            )
    return tuple(sorted(entries, key=lambda entry: entry.relative_path))


def _manifest_id_for_entries(
    *,
    schema_version: str,
    seed_package_version: str,
    entries: tuple[BaselineManifestEntry, ...],
) -> str:
    canonical_payload = {
        "schema_version": schema_version,
        "seed_package_version": seed_package_version,
        "entries": [entry.model_dump(mode="json") for entry in entries],
    }
    encoded = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(file_path: Path) -> str:
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


__all__ = [
    "BaselineManifest",
    "BaselineManifestEntry",
    "build_baseline_manifest",
    "load_baseline_manifest",
    "write_baseline_manifest",
]
