"""Built-in stage-kind asset loading for the additive loop-architecture layer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from millrace_ai.architecture import RegisteredStageKindDefinition
from millrace_ai.errors import AssetValidationError

ASSETS_ROOT = Path(__file__).resolve().parent
STAGE_KIND_REGISTRY_ROOT = Path("registry/stage_kinds")

BUILTIN_STAGE_KIND_PATHS: dict[str, Path] = {
    "builder": Path("registry/stage_kinds/execution/builder.json"),
    "checker": Path("registry/stage_kinds/execution/checker.json"),
    "fixer": Path("registry/stage_kinds/execution/fixer.json"),
    "doublechecker": Path("registry/stage_kinds/execution/doublechecker.json"),
    "updater": Path("registry/stage_kinds/execution/updater.json"),
    "troubleshooter": Path("registry/stage_kinds/execution/troubleshooter.json"),
    "consultant": Path("registry/stage_kinds/execution/consultant.json"),
    "planner": Path("registry/stage_kinds/planning/planner.json"),
    "manager": Path("registry/stage_kinds/planning/manager.json"),
    "mechanic": Path("registry/stage_kinds/planning/mechanic.json"),
    "auditor": Path("registry/stage_kinds/planning/auditor.json"),
    "arbiter": Path("registry/stage_kinds/planning/arbiter.json"),
}

SHIPPED_STAGE_KIND_IDS: tuple[str, ...] = tuple(BUILTIN_STAGE_KIND_PATHS)


class ArchitectureAssetError(AssetValidationError):
    """Raised when built-in architecture assets cannot be resolved or validated."""


def load_builtin_stage_kind_definition(
    stage_kind_id: str,
    *,
    assets_root: Path | None = None,
) -> RegisteredStageKindDefinition:
    root = _resolve_assets_root(assets_root)
    asset_path = _resolve_stage_kind_path(stage_kind_id, root)
    payload = _load_json_asset(asset_path, asset_kind="stage kind")

    try:
        stage_kind = RegisteredStageKindDefinition.model_validate(payload)
    except ValidationError as exc:
        raise ArchitectureAssetError(f"Invalid stage kind definition in asset: {asset_path}") from exc

    if stage_kind.stage_kind_id != stage_kind_id:
        raise ArchitectureAssetError(
            f"Stage kind asset id mismatch: expected {stage_kind_id}, found {stage_kind.stage_kind_id}"
        )

    return stage_kind


def load_builtin_stage_kind_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[RegisteredStageKindDefinition, ...]:
    return tuple(
        load_builtin_stage_kind_definition(stage_kind_id, assets_root=assets_root)
        for stage_kind_id in SHIPPED_STAGE_KIND_IDS
    )


def load_stage_kind_definition(
    stage_kind_id: str,
    *,
    assets_root: Path | None = None,
) -> RegisteredStageKindDefinition:
    root = _resolve_assets_root(assets_root)
    discovered = {
        stage_kind.stage_kind_id: stage_kind
        for stage_kind in discover_stage_kind_definitions(assets_root=root)
    }
    stage_kind = discovered.get(stage_kind_id)
    if stage_kind is None:
        raise ArchitectureAssetError(f"Unknown discovered stage kind id: {stage_kind_id}")
    return stage_kind


def discover_stage_kind_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[RegisteredStageKindDefinition, ...]:
    root = _resolve_assets_root(assets_root)
    discovered: list[RegisteredStageKindDefinition] = []
    seen_ids: set[str] = set()

    for asset_path in _discover_stage_kind_paths(root):
        stage_kind = _load_stage_kind_definition_at_path(asset_path)
        if stage_kind.stage_kind_id in seen_ids:
            raise ArchitectureAssetError(
                f"Duplicate discovered stage kind id: {stage_kind.stage_kind_id}"
            )
        seen_ids.add(stage_kind.stage_kind_id)
        discovered.append(stage_kind)

    return tuple(sorted(discovered, key=lambda stage_kind: stage_kind.stage_kind_id))


def _resolve_assets_root(assets_root: Path | None) -> Path:
    if assets_root is None:
        return ASSETS_ROOT
    return Path(assets_root)


def _discover_stage_kind_paths(assets_root: Path) -> tuple[Path, ...]:
    registry_root = assets_root / STAGE_KIND_REGISTRY_ROOT
    if not registry_root.is_dir():
        return ()
    return tuple(sorted(path for path in registry_root.rglob("*.json") if path.is_file()))


def _resolve_stage_kind_path(stage_kind_id: str, assets_root: Path) -> Path:
    relative_path = BUILTIN_STAGE_KIND_PATHS.get(stage_kind_id)
    if relative_path is None:
        raise ArchitectureAssetError(f"Unknown built-in stage kind id: {stage_kind_id}")
    return assets_root / relative_path


def _load_json_asset(path: Path, *, asset_kind: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ArchitectureAssetError(f"Cannot read {asset_kind} asset: {path}") from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ArchitectureAssetError(f"Invalid JSON in {asset_kind} asset: {path}") from exc

    if not isinstance(payload, dict):
        raise ArchitectureAssetError(f"Invalid JSON in {asset_kind} asset: {path}")

    return payload


def _load_stage_kind_definition_at_path(path: Path) -> RegisteredStageKindDefinition:
    payload = _load_json_asset(path, asset_kind="stage kind")

    try:
        stage_kind = RegisteredStageKindDefinition.model_validate(payload)
    except ValidationError as exc:
        raise ArchitectureAssetError(f"Invalid stage kind definition in asset: {path}") from exc

    return stage_kind


__all__ = [
    "ASSETS_ROOT",
    "ArchitectureAssetError",
    "BUILTIN_STAGE_KIND_PATHS",
    "STAGE_KIND_REGISTRY_ROOT",
    "SHIPPED_STAGE_KIND_IDS",
    "discover_stage_kind_definitions",
    "load_stage_kind_definition",
    "load_builtin_stage_kind_definition",
    "load_builtin_stage_kind_definitions",
]
