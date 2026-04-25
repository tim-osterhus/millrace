"""Built-in mode and loop definition loading for Millrace."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from millrace_ai.contracts import LoopConfigDefinition, ModeDefinition, Plane
from millrace_ai.errors import AssetValidationError

ASSETS_ROOT = Path(__file__).resolve().parent

BUILTIN_LOOP_PATHS: dict[str, Path] = {
    "execution.standard": Path("loops/execution/default.json"),
    "execution.skills_pipeline": Path("loops/execution/skills_pipeline.json"),
    "learning.standard": Path("loops/learning/default.json"),
    "planning.standard": Path("loops/planning/default.json"),
    "planning.skills_pipeline": Path("loops/planning/skills_pipeline.json"),
}

BUILTIN_MODE_PATHS: dict[str, Path] = {
    "default_codex": Path("modes/default_codex.json"),
    "default_pi": Path("modes/default_pi.json"),
    "learning_codex": Path("modes/learning_codex.json"),
    "learning_pi": Path("modes/learning_pi.json"),
    "skills_pipeline_codex": Path("modes/skills_pipeline_codex.json"),
}

BUILTIN_MODE_ALIASES: dict[str, str] = {
    "standard_plain": "default_codex",
}

SHIPPED_MODE_IDS: tuple[str, ...] = (
    "default_codex",
    "default_pi",
    "learning_codex",
    "learning_pi",
)
_DEFAULT_MODE_IDS: tuple[str, ...] = ("default_codex", "default_pi")


class ModeAssetError(AssetValidationError):
    """Raised when built-in mode or loop assets cannot be resolved or validated."""


@dataclass(frozen=True)
class ModeBundle:
    mode: ModeDefinition
    execution_loop: LoopConfigDefinition
    planning_loop: LoopConfigDefinition
    learning_loop: LoopConfigDefinition | None = None
    loops_by_plane: dict[Plane, LoopConfigDefinition] | None = None


def load_builtin_mode_bundle(mode_id: str, *, assets_root: Path | None = None) -> ModeBundle:
    root = _resolve_assets_root(assets_root)
    canonical_mode_id = resolve_builtin_mode_id(mode_id)
    mode = load_builtin_mode_definition(canonical_mode_id, assets_root=root)
    loops_by_plane = {
        plane: load_builtin_loop_definition(loop_id, assets_root=root)
        for plane, loop_id in mode.loop_ids_by_plane.items()
    }
    execution_loop = loops_by_plane[Plane.EXECUTION]
    planning_loop = loops_by_plane[Plane.PLANNING]
    learning_loop = loops_by_plane.get(Plane.LEARNING)

    if canonical_mode_id in _DEFAULT_MODE_IDS:
        validate_shipped_mode_same_graph(assets_root=root)

    if execution_loop.plane is not Plane.EXECUTION:
        raise ModeAssetError(
            f"Execution loop {execution_loop.loop_id} must declare plane=execution"
        )
    if planning_loop.plane is not Plane.PLANNING:
        raise ModeAssetError(
            f"Planning loop {planning_loop.loop_id} must declare plane=planning"
        )
    if learning_loop is not None and learning_loop.plane is not Plane.LEARNING:
        raise ModeAssetError(
            f"Learning loop {learning_loop.loop_id} must declare plane=learning"
        )

    return ModeBundle(
        mode=mode,
        execution_loop=execution_loop,
        planning_loop=planning_loop,
        learning_loop=learning_loop,
        loops_by_plane=loops_by_plane,
    )


def load_builtin_mode_definition(
    mode_id: str,
    *,
    assets_root: Path | None = None,
) -> ModeDefinition:
    root = _resolve_assets_root(assets_root)
    canonical_mode_id = resolve_builtin_mode_id(mode_id)
    mode = _load_mode_definition_raw(canonical_mode_id, root)

    if mode.mode_id != canonical_mode_id:
        raise ModeAssetError(
            f"Mode asset id mismatch: expected {canonical_mode_id}, found {mode.mode_id}"
        )

    return mode


def resolve_builtin_mode_id(mode_id: str) -> str:
    return BUILTIN_MODE_ALIASES.get(mode_id, mode_id)


def builtin_mode_alias_target(mode_id: str) -> str | None:
    return BUILTIN_MODE_ALIASES.get(mode_id)


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

    for mode_id in _DEFAULT_MODE_IDS:
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
    "BUILTIN_MODE_ALIASES",
    "BUILTIN_LOOP_PATHS",
    "BUILTIN_MODE_PATHS",
    "ModeAssetError",
    "ModeBundle",
    "SHIPPED_MODE_IDS",
    "builtin_mode_alias_target",
    "load_builtin_loop_definition",
    "load_builtin_mode_bundle",
    "load_builtin_mode_definition",
    "resolve_builtin_mode_id",
    "validate_shipped_mode_same_graph",
]
