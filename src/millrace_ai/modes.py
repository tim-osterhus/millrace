"""Built-in mode and loop definition loading for Millrace."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from millrace_ai.contracts import LoopConfigDefinition, ModeDefinition, Plane
from millrace_ai.errors import AssetValidationError

ASSETS_ROOT = Path(__file__).resolve().parent / "assets"

BUILTIN_LOOP_PATHS: dict[str, Path] = {
    "execution.standard": Path("loops/execution/default.json"),
    "planning.standard": Path("loops/planning/default.json"),
}

BUILTIN_MODE_PATHS: dict[str, Path] = {
    "standard_plain": Path("modes/standard_plain.json"),
}

SHIPPED_MODE_IDS: tuple[str, ...] = ("standard_plain",)


class ModeAssetError(AssetValidationError):
    """Raised when built-in mode or loop assets cannot be resolved or validated."""


@dataclass(frozen=True)
class ModeBundle:
    mode: ModeDefinition
    execution_loop: LoopConfigDefinition
    planning_loop: LoopConfigDefinition


def load_builtin_mode_bundle(mode_id: str, *, assets_root: Path | None = None) -> ModeBundle:
    root = _resolve_assets_root(assets_root)
    mode = load_builtin_mode_definition(mode_id, assets_root=root)
    execution_loop = load_builtin_loop_definition(mode.execution_loop_id, assets_root=root)
    planning_loop = load_builtin_loop_definition(mode.planning_loop_id, assets_root=root)

    if mode_id in SHIPPED_MODE_IDS:
        validate_shipped_mode_same_graph(assets_root=root)

    if execution_loop.plane is not Plane.EXECUTION:
        raise ModeAssetError(
            f"Execution loop {execution_loop.loop_id} must declare plane=execution"
        )
    if planning_loop.plane is not Plane.PLANNING:
        raise ModeAssetError(
            f"Planning loop {planning_loop.loop_id} must declare plane=planning"
        )

    return ModeBundle(mode=mode, execution_loop=execution_loop, planning_loop=planning_loop)


def load_builtin_mode_definition(
    mode_id: str,
    *,
    assets_root: Path | None = None,
) -> ModeDefinition:
    root = _resolve_assets_root(assets_root)
    mode = _load_mode_definition_raw(mode_id, root)

    if mode.mode_id != mode_id:
        raise ModeAssetError(f"Mode asset id mismatch: expected {mode_id}, found {mode.mode_id}")

    return mode


def load_builtin_loop_definition(
    loop_id: str,
    *,
    assets_root: Path | None = None,
) -> LoopConfigDefinition:
    root = _resolve_assets_root(assets_root)
    loop_path = _resolve_loop_path(loop_id, root)
    payload = _load_json_asset(loop_path, asset_kind="loop")

    try:
        loop = LoopConfigDefinition.model_validate(payload)
    except ValidationError as exc:
        raise ModeAssetError(f"Invalid loop definition in asset: {loop_path}") from exc

    if loop.loop_id != loop_id:
        raise ModeAssetError(f"Loop asset id mismatch: expected {loop_id}, found {loop.loop_id}")

    return loop


def validate_shipped_mode_same_graph(*, assets_root: Path | None = None) -> tuple[str, str]:
    root = _resolve_assets_root(assets_root)
    selected_graph: tuple[str, str] | None = None

    for mode_id in SHIPPED_MODE_IDS:
        mode = _load_mode_definition_raw(mode_id, root)
        graph = (mode.execution_loop_id, mode.planning_loop_id)
        if selected_graph is None:
            selected_graph = graph
            continue
        if graph != selected_graph:
            raise ModeAssetError(
                "Shipped modes must reference the same execution and planning loops"
            )

    if selected_graph is None:
        raise ModeAssetError("No shipped modes configured")

    return selected_graph


def _load_mode_definition_raw(mode_id: str, assets_root: Path) -> ModeDefinition:
    mode_path = _resolve_mode_path(mode_id, assets_root)
    payload = _load_json_asset(mode_path, asset_kind="mode")

    try:
        return ModeDefinition.model_validate(payload)
    except ValidationError as exc:
        raise ModeAssetError(f"Invalid mode definition in asset: {mode_path}") from exc


def _resolve_assets_root(assets_root: Path | None) -> Path:
    if assets_root is None:
        return ASSETS_ROOT
    return Path(assets_root)


def _resolve_mode_path(mode_id: str, assets_root: Path) -> Path:
    relative_path = BUILTIN_MODE_PATHS.get(mode_id)
    if relative_path is None:
        raise ModeAssetError(f"Unknown built-in mode id: {mode_id}")
    return assets_root / relative_path


def _resolve_loop_path(loop_id: str, assets_root: Path) -> Path:
    relative_path = BUILTIN_LOOP_PATHS.get(loop_id)
    if relative_path is None:
        raise ModeAssetError(f"Unknown built-in loop id: {loop_id}")
    return assets_root / relative_path


def _load_json_asset(path: Path, *, asset_kind: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ModeAssetError(f"Cannot read {asset_kind} asset: {path}") from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModeAssetError(f"Invalid JSON in {asset_kind} asset: {path}") from exc

    if not isinstance(payload, dict):
        raise ModeAssetError(f"Invalid JSON in {asset_kind} asset: {path}")

    return payload


__all__ = [
    "ASSETS_ROOT",
    "BUILTIN_LOOP_PATHS",
    "BUILTIN_MODE_PATHS",
    "ModeAssetError",
    "ModeBundle",
    "SHIPPED_MODE_IDS",
    "load_builtin_loop_definition",
    "load_builtin_mode_bundle",
    "load_builtin_mode_definition",
    "validate_shipped_mode_same_graph",
]
