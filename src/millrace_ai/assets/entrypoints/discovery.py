"""Entrypoint asset discovery constants and path inference."""

from __future__ import annotations

from pathlib import Path

from millrace_ai.assets.architecture import discover_stage_kind_definitions
from millrace_ai.contracts import Plane

_STAGE_KINDS = discover_stage_kind_definitions()
KNOWN_EXECUTION_STAGES = {
    stage_kind.stage_kind_id for stage_kind in _STAGE_KINDS if stage_kind.plane is Plane.EXECUTION
} | {stage_kind.runtime_stage.value for stage_kind in _STAGE_KINDS if stage_kind.plane is Plane.EXECUTION}
KNOWN_PLANNING_STAGES = {
    stage_kind.stage_kind_id for stage_kind in _STAGE_KINDS if stage_kind.plane is Plane.PLANNING
} | {stage_kind.runtime_stage.value for stage_kind in _STAGE_KINDS if stage_kind.plane is Plane.PLANNING}
KNOWN_LEARNING_STAGES = {
    stage_kind.stage_kind_id for stage_kind in _STAGE_KINDS if stage_kind.plane is Plane.LEARNING
} | {stage_kind.runtime_stage.value for stage_kind in _STAGE_KINDS if stage_kind.plane is Plane.LEARNING}
KNOWN_STAGES = {
    stage_id
    for stage_kind in _STAGE_KINDS
    for stage_id in (stage_kind.stage_kind_id, stage_kind.runtime_stage.value)
}
KNOWN_PLANES = {plane.value for plane in Plane}
KNOWN_ASSET_TYPES = {"entrypoint", "skill"}
_ENTRYPOINT_TARGETS_BY_PATH = {
    Path(stage_kind.default_entrypoint_path).as_posix(): (
        stage_kind.plane.value,
        stage_kind.stage_kind_id,
    )
    for stage_kind in _STAGE_KINDS
}


def infer_entrypoint_path_target(path: Path) -> tuple[str | None, str | None]:
    parts = path.parts
    if "entrypoints" not in parts:
        return None, None

    entrypoints_index = parts.index("entrypoints")
    if entrypoints_index + 1 >= len(parts):
        return None, None

    plane = parts[entrypoints_index + 1]
    if plane not in KNOWN_PLANES:
        return None, None

    stem = path.stem
    if stem in KNOWN_STAGES:
        return plane, stem

    default_entrypoint_key = Path(*parts[entrypoints_index:]).as_posix()
    entrypoint_target = _ENTRYPOINT_TARGETS_BY_PATH.get(default_entrypoint_key)
    if entrypoint_target is not None:
        return entrypoint_target

    stage = next(
        (
            candidate
            for candidate in sorted(KNOWN_STAGES, key=len, reverse=True)
            if stem.endswith(f"-{candidate}") or stem.endswith(f"_{candidate}")
        ),
        None,
    )
    return plane, stage


__all__ = [
    "KNOWN_ASSET_TYPES",
    "KNOWN_EXECUTION_STAGES",
    "KNOWN_LEARNING_STAGES",
    "KNOWN_PLANES",
    "KNOWN_PLANNING_STAGES",
    "KNOWN_STAGES",
    "infer_entrypoint_path_target",
]
