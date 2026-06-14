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

BUILTIN_STAGE_KIND_PATHS: dict[str, Path] = {}

SHIPPED_STAGE_KIND_IDS: tuple[str, ...] = ()

BUILTIN_STAGE_KIND_ALIASES: dict[str, str] = {
    "builder": "lad_builder",
    "fixer": "lad_fixer",
    "checker": "lad_checker",
    "doublechecker": "lad_doublechecker",
    "updater": "lad_updater",
    "troubleshooter": "lad_troubleshooter",
    "consultant": "lad_consultant",
    "integrator": "lad_integrator",
    "planner": "lad_planner",
    "manager": "lad_manager",
    "mechanic": "lad_mechanic",
    "auditor": "lad_auditor",
    "arbiter": "lad_arbiter",
}


class ArchitectureAssetError(AssetValidationError):
    """Raised when built-in architecture assets cannot be resolved or validated."""


def load_builtin_stage_kind_definition(
    stage_kind_id: str,
    *,
    assets_root: Path | None = None,
) -> RegisteredStageKindDefinition:
    root = _resolve_assets_root(assets_root)
    canonical_stage_kind_id = resolve_stage_kind_id(stage_kind_id)
    asset_path = _resolve_stage_kind_path(canonical_stage_kind_id, root)
    payload = _load_json_asset(asset_path, asset_kind="stage kind")

    try:
        stage_kind = RegisteredStageKindDefinition.model_validate(payload)
    except ValidationError as exc:
        first_error = _format_validation_error(exc)
        raise ArchitectureAssetError(
            f"Invalid stage kind definition in asset: {asset_path} ({first_error})"
        ) from exc

    if stage_kind.stage_kind_id != canonical_stage_kind_id:
        raise ArchitectureAssetError(
            "Stage kind asset id mismatch: "
            f"expected {canonical_stage_kind_id}, found {stage_kind.stage_kind_id}"
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
    canonical_stage_kind_id = resolve_stage_kind_id(stage_kind_id)
    discovered = {
        stage_kind.stage_kind_id: stage_kind
        for stage_kind in discover_stage_kind_definitions(assets_root=root)
    }
    stage_kind = discovered.get(canonical_stage_kind_id)
    if stage_kind is None:
        raise ArchitectureAssetError(f"Unknown discovered stage kind id: {canonical_stage_kind_id}")
    return stage_kind


def stage_kind_asset_relative_path(stage_kind_id: str, *, assets_root: Path | None = None) -> Path:
    root = _resolve_assets_root(assets_root)
    return _resolve_stage_kind_path(resolve_stage_kind_id(stage_kind_id), root).relative_to(root)


def resolve_stage_kind_id(stage_kind_id: str) -> str:
    return BUILTIN_STAGE_KIND_ALIASES.get(stage_kind_id, stage_kind_id)


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
    if relative_path is not None:
        return assets_root / relative_path

    matches: list[Path] = []
    for path in _discover_stage_kind_paths(assets_root):
        payload = _load_json_asset(path, asset_kind="stage kind")
        if payload.get("stage_kind_id") == stage_kind_id:
            matches.append(path)

    if len(matches) > 1:
        joined = ", ".join(str(path) for path in matches)
        raise ArchitectureAssetError(f"Duplicate stage kind id {stage_kind_id}: {joined}")
    if not matches:
        raise ArchitectureAssetError(f"Unknown built-in stage kind id: {stage_kind_id}")
    return matches[0]


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
        first_error = _format_validation_error(exc)
        raise ArchitectureAssetError(
            f"Invalid stage kind definition in asset: {path} ({first_error})"
        ) from exc

    return stage_kind


def _format_validation_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "validation failed"
    first = errors[0]
    loc = ".".join(str(part) for part in first.get("loc", ()))
    message = str(first.get("msg", "validation failed"))
    if not loc:
        return message
    return f"{loc}: {message}"


# ---------------------------------------------------------------------------
# Module-level initialization – populates BUILTIN_STAGE_KIND_PATHS and
# SHIPPED_STAGE_KIND_IDS from the shipped stage-kind JSON assets.
# ---------------------------------------------------------------------------

def _init_builtin_stage_kind_paths() -> None:
    global BUILTIN_STAGE_KIND_PATHS, SHIPPED_STAGE_KIND_IDS
    from millrace_ai.contracts.enums import ExecutionStageName, LearningStageName, PlanningStageName

    _known_stage_ids: set[str] = {
        *(BUILTIN_STAGE_KIND_ALIASES.get(s.value, s.value) for s in ExecutionStageName),
        *(BUILTIN_STAGE_KIND_ALIASES.get(s.value, s.value) for s in PlanningStageName),
        *(s.value for s in LearningStageName),
        "basic_worker",
        "basic_planner",
        "basic_learner",
        "manager_blueprint",
        "contractor_blueprint",
        "evaluator_blueprint",
        "mechanic_blueprint",
        "recon",
    }

    # Maintain the shipped ordering: execution enum order, then planning,
    # then learning (same order as the old hard-coded SHIPPED_STAGE_KIND_IDS).
    _shipped_order: list[str] = [
        *(BUILTIN_STAGE_KIND_ALIASES.get(s.value, s.value) for s in ExecutionStageName),
        "recon",
        *(BUILTIN_STAGE_KIND_ALIASES.get(s.value, s.value) for s in PlanningStageName if s.value != "recon"),
        *(s.value for s in LearningStageName),
    ]

    paths: dict[str, Path] = {}
    for json_path in _discover_stage_kind_paths(ASSETS_ROOT):
        payload = _load_json_asset(json_path, asset_kind="stage kind")
        stage_kind_id = payload.get("stage_kind_id")
        if isinstance(stage_kind_id, str) and stage_kind_id and stage_kind_id in _known_stage_ids:
            paths[stage_kind_id] = json_path.relative_to(ASSETS_ROOT)
    BUILTIN_STAGE_KIND_PATHS = paths
    SHIPPED_STAGE_KIND_IDS = tuple(
        sid for sid in _shipped_order if sid in paths
    )


_init_builtin_stage_kind_paths()

__all__ = [
    "ASSETS_ROOT",
    "ArchitectureAssetError",
    "BUILTIN_STAGE_KIND_ALIASES",
    "BUILTIN_STAGE_KIND_PATHS",
    "STAGE_KIND_REGISTRY_ROOT",
    "SHIPPED_STAGE_KIND_IDS",
    "discover_stage_kind_definitions",
    "load_stage_kind_definition",
    "load_builtin_stage_kind_definition",
    "load_builtin_stage_kind_definitions",
    "resolve_stage_kind_id",
    "stage_kind_asset_relative_path",
]
