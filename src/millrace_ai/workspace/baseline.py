"""Deployed baseline manifest models and persistence helpers."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

import millrace_ai
import millrace_ai.workspace.asset_deployment as asset_deployment

from .paths import WorkspacePaths, workspace_paths


class _BaselineModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BaselineManifestEntry(_BaselineModel):
    relative_path: str
    asset_family: str
    original_sha256: str


class BaselineManifest(_BaselineModel):
    schema_version: Literal["1.0"] = "1.0"
    manifest_id: str
    seed_package_version: str
    entries: tuple[BaselineManifestEntry, ...]

    def entry_for(self, relative_path: str) -> BaselineManifestEntry:
        for entry in self.entries:
            if entry.relative_path == relative_path:
                return entry
        raise KeyError(relative_path)


class UpgradeDisposition(str, Enum):
    UNCHANGED = "unchanged"
    SAFE_PACKAGE_UPDATE = "safe_package_update"
    LOCAL_ONLY_MODIFICATION = "local_only_modification"
    ALREADY_CONVERGED = "already_converged"
    LOCALIZED_REMOVED = "localized_removed"
    CONFLICT = "conflict"
    MISSING = "missing"


class BaselineUpgradeEntry(_BaselineModel):
    relative_path: str
    asset_family: str
    disposition: UpgradeDisposition
    original_sha256: str | None = None
    current_sha256: str | None = None
    candidate_sha256: str | None = None


class BaselineUpgradePreview(_BaselineModel):
    applied: bool = False
    baseline_manifest_id: str
    candidate_manifest_id: str
    entries: tuple[BaselineUpgradeEntry, ...]

    @property
    def classifications_by_path(self) -> dict[str, UpgradeDisposition]:
        return {entry.relative_path: entry.disposition for entry in self.entries}

    @property
    def counts_by_disposition(self) -> dict[UpgradeDisposition, int]:
        counts = {disposition: 0 for disposition in UpgradeDisposition}
        for entry in self.entries:
            counts[entry.disposition] += 1
        return counts


def build_baseline_manifest(
    target: WorkspacePaths | Path | str,
    *,
    assets_root: Path | str | None = None,
) -> BaselineManifest:
    """Build a manifest from the managed runtime asset seed source."""

    # Keep the target parameter for symmetry with other workspace helpers.
    _ = target if isinstance(target, WorkspacePaths) else workspace_paths(target)
    source_root = asset_deployment.resolve_asset_source_root(assets_root)
    entries = tuple(_iter_manifest_entries(source_root))
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


def preview_baseline_upgrade(
    target: WorkspacePaths | Path | str,
    *,
    candidate_assets_root: Path | str | None = None,
    localize_removed_paths: tuple[str, ...] = (),
) -> BaselineUpgradePreview:
    paths = target if isinstance(target, WorkspacePaths) else workspace_paths(target)
    baseline_manifest = load_baseline_manifest(paths)
    candidate_manifest = build_baseline_manifest(paths, assets_root=candidate_assets_root)
    original_by_path = {entry.relative_path: entry for entry in baseline_manifest.entries}
    candidate_by_path = {entry.relative_path: entry for entry in candidate_manifest.entries}
    localize_removed_set = _normalize_localize_removed_paths(localize_removed_paths)
    entries: list[BaselineUpgradeEntry] = []
    removed_original_paths: set[str] = set()

    for relative_path in sorted(set(original_by_path) | set(candidate_by_path)):
        original_entry = original_by_path.get(relative_path)
        candidate_entry = candidate_by_path.get(relative_path)
        current_path = paths.runtime_root / relative_path
        current_sha256 = _current_file_sha256(current_path)

        if current_path.exists() and not current_path.is_file():
            disposition = UpgradeDisposition.CONFLICT
        elif original_entry is None:
            assert candidate_entry is not None
            if current_sha256 is None:
                disposition = UpgradeDisposition.MISSING
            elif current_sha256 == candidate_entry.original_sha256:
                disposition = UpgradeDisposition.ALREADY_CONVERGED
            else:
                disposition = UpgradeDisposition.CONFLICT
        elif candidate_entry is None:
            removed_original_paths.add(relative_path)
            if relative_path in localize_removed_set:
                disposition = UpgradeDisposition.LOCALIZED_REMOVED
            else:
                disposition = UpgradeDisposition.CONFLICT
        elif current_sha256 is None:
            disposition = UpgradeDisposition.MISSING
        else:
            original_sha256 = original_entry.original_sha256
            candidate_sha256 = candidate_entry.original_sha256
            if current_sha256 == original_sha256 == candidate_sha256:
                disposition = UpgradeDisposition.UNCHANGED
            elif current_sha256 == original_sha256 and candidate_sha256 != original_sha256:
                disposition = UpgradeDisposition.SAFE_PACKAGE_UPDATE
            elif current_sha256 != original_sha256 and candidate_sha256 == original_sha256:
                disposition = UpgradeDisposition.LOCAL_ONLY_MODIFICATION
            elif current_sha256 == candidate_sha256 and candidate_sha256 != original_sha256:
                disposition = UpgradeDisposition.ALREADY_CONVERGED
            else:
                disposition = UpgradeDisposition.CONFLICT

        asset_family = (
            candidate_entry.asset_family
            if candidate_entry is not None
            else original_entry.asset_family if original_entry is not None else relative_path.split("/", 1)[0]
        )
        entries.append(
            BaselineUpgradeEntry(
                relative_path=relative_path,
                asset_family=asset_family,
                disposition=disposition,
                original_sha256=None if original_entry is None else original_entry.original_sha256,
                current_sha256=current_sha256,
                candidate_sha256=None if candidate_entry is None else candidate_entry.original_sha256,
            )
        )

    invalid_localize_paths = sorted(localize_removed_set - removed_original_paths)
    if invalid_localize_paths:
        joined = ", ".join(invalid_localize_paths)
        raise ValueError(f"localize-removed path is not a removed managed asset: {joined}")

    return BaselineUpgradePreview(
        baseline_manifest_id=baseline_manifest.manifest_id,
        candidate_manifest_id=candidate_manifest.manifest_id,
        entries=tuple(entries),
    )


def apply_baseline_upgrade(
    target: WorkspacePaths | Path | str,
    *,
    candidate_assets_root: Path | str | None = None,
    localize_removed_paths: tuple[str, ...] = (),
) -> BaselineUpgradePreview:
    paths = target if isinstance(target, WorkspacePaths) else workspace_paths(target)
    preview = preview_baseline_upgrade(
        paths,
        candidate_assets_root=candidate_assets_root,
        localize_removed_paths=localize_removed_paths,
    )
    conflicts = tuple(entry.relative_path for entry in preview.entries if entry.disposition is UpgradeDisposition.CONFLICT)
    if conflicts:
        joined = ", ".join(conflicts)
        raise ValueError(f"upgrade conflict(s) detected: {joined}")

    source_root = asset_deployment.resolve_asset_source_root(candidate_assets_root)
    for entry in preview.entries:
        if entry.disposition not in {UpgradeDisposition.SAFE_PACKAGE_UPDATE, UpgradeDisposition.MISSING}:
            continue
        source_file = source_root / entry.relative_path
        if not source_file.is_file():
            raise ValueError(f"candidate managed asset is missing: {entry.relative_path}")
        destination = paths.runtime_root / entry.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source_file.read_bytes())

    write_baseline_manifest(paths, build_baseline_manifest(paths, assets_root=candidate_assets_root))
    return BaselineUpgradePreview(
        applied=True,
        baseline_manifest_id=preview.baseline_manifest_id,
        candidate_manifest_id=preview.candidate_manifest_id,
        entries=preview.entries,
    )


def _iter_manifest_entries(source_root: Path) -> tuple[BaselineManifestEntry, ...]:
    entries: list[BaselineManifestEntry] = []
    for asset_family in asset_deployment.RUNTIME_ASSET_DIRS:
        family_root = source_root / asset_family
        if not family_root.exists():
            continue
        for file_path in sorted(path for path in family_root.rglob("*") if path.is_file()):
            relative_within_family = file_path.relative_to(family_root)
            if asset_deployment.should_skip_runtime_asset_path(relative_within_family):
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


def _current_file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return _sha256_file(path)


def _normalize_localize_removed_paths(paths: tuple[str, ...]) -> set[str]:
    normalized: set[str] = set()
    for value in paths:
        cleaned = value.strip()
        if not cleaned:
            continue
        candidate = Path(cleaned)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"localize-removed path must be workspace-relative: {value}")
        normalized.add(candidate.as_posix())
    return normalized


__all__ = [
    "BaselineManifest",
    "BaselineManifestEntry",
    "BaselineUpgradeEntry",
    "BaselineUpgradePreview",
    "UpgradeDisposition",
    "apply_baseline_upgrade",
    "build_baseline_manifest",
    "load_baseline_manifest",
    "preview_baseline_upgrade",
    "write_baseline_manifest",
]
