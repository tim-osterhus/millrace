"""Runtime effect operation asset loading."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from millrace_ai.architecture import (
    RuntimeEffectOperationDefinition,
    RuntimeEffectStoreDefinition,
    RuntimeEffectValidatorDefinition,
)
from millrace_ai.errors import AssetValidationError

ASSETS_ROOT = Path(__file__).resolve().parent
EFFECT_STORE_REGISTRY_ROOT = Path("registry/effect_stores")
EFFECT_VALIDATOR_REGISTRY_ROOT = Path("registry/effect_validators")
RUNTIME_EFFECT_OPERATION_REGISTRY_ROOT = Path("registry/runtime_effect_operations")

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class EffectOperationAssetError(AssetValidationError):
    """Raised when declarative runtime effect operation assets are invalid."""


def discover_effect_store_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[RuntimeEffectStoreDefinition, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=EFFECT_STORE_REGISTRY_ROOT,
        model=RuntimeEffectStoreDefinition,
        id_attr="store_id",
        asset_kind="runtime effect store",
    )


def discover_effect_validator_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[RuntimeEffectValidatorDefinition, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=EFFECT_VALIDATOR_REGISTRY_ROOT,
        model=RuntimeEffectValidatorDefinition,
        id_attr="validator_id",
        asset_kind="runtime effect validator",
    )


def discover_runtime_effect_operation_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[RuntimeEffectOperationDefinition, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=RUNTIME_EFFECT_OPERATION_REGISTRY_ROOT,
        model=RuntimeEffectOperationDefinition,
        id_attr="operation_id",
        asset_kind="runtime effect operation",
    )


def _discover_definitions(
    *,
    assets_root: Path | None,
    registry_root: Path,
    model: type[_ModelT],
    id_attr: str,
    asset_kind: str,
) -> tuple[_ModelT, ...]:
    root = _resolve_assets_root(assets_root)
    discovered: list[_ModelT] = []
    seen_ids: dict[str, Path] = {}

    for asset_path in _discover_json_paths(root, registry_root):
        definitions = _load_definitions_at_path(asset_path, model=model, asset_kind=asset_kind)
        for definition in definitions:
            primitive_id = str(getattr(definition, id_attr))
            previous_path = seen_ids.get(primitive_id)
            if previous_path is not None:
                raise EffectOperationAssetError(
                    f"Duplicate discovered {asset_kind} id: {primitive_id} "
                    f"({previous_path}, {asset_path})"
                )
            seen_ids[primitive_id] = asset_path
            discovered.append(definition)

    return tuple(sorted(discovered, key=lambda definition: str(getattr(definition, id_attr))))


def _load_definitions_at_path(
    path: Path,
    *,
    model: type[_ModelT],
    asset_kind: str,
) -> tuple[_ModelT, ...]:
    payload = _load_json_asset(path, asset_kind=asset_kind)
    items = _definition_items(payload, asset_kind=asset_kind, path=path)
    definitions: list[_ModelT] = []
    for item in items:
        try:
            definitions.append(model.model_validate(item))
        except ValidationError as exc:
            first_error = exc.errors()[0]["msg"] if exc.errors() else "validation failed"
            raise EffectOperationAssetError(
                f"Invalid {asset_kind} definition in asset: {path} ({first_error})"
            ) from exc
    return tuple(definitions)


def _definition_items(payload: Any, *, asset_kind: str, path: Path) -> tuple[dict[str, Any], ...]:
    if isinstance(payload, dict) and "definitions" in payload:
        definitions = payload["definitions"]
    else:
        definitions = payload

    if isinstance(definitions, dict):
        items = (definitions,)
    elif isinstance(definitions, list):
        items = tuple(definitions)
    else:
        raise EffectOperationAssetError(f"Invalid JSON in {asset_kind} asset: {path}")

    if not all(isinstance(item, dict) for item in items):
        raise EffectOperationAssetError(f"Invalid JSON in {asset_kind} asset: {path}")
    return items


def _discover_json_paths(assets_root: Path, registry_root: Path) -> tuple[Path, ...]:
    root = assets_root / registry_root
    if not root.is_dir():
        return ()
    return tuple(sorted(path for path in root.rglob("*.json") if path.is_file()))


def _load_json_asset(path: Path, *, asset_kind: str) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EffectOperationAssetError(f"Cannot read {asset_kind} asset: {path}") from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise EffectOperationAssetError(f"Invalid JSON in {asset_kind} asset: {path}") from exc


def _resolve_assets_root(assets_root: Path | None) -> Path:
    if assets_root is None:
        return ASSETS_ROOT
    return Path(assets_root)


__all__ = [
    "EFFECT_STORE_REGISTRY_ROOT",
    "EFFECT_VALIDATOR_REGISTRY_ROOT",
    "EffectOperationAssetError",
    "RUNTIME_EFFECT_OPERATION_REGISTRY_ROOT",
    "discover_effect_store_definitions",
    "discover_effect_validator_definitions",
    "discover_runtime_effect_operation_definitions",
]
