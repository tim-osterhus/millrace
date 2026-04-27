"""Entrypoint asset discovery constants and path inference."""

from __future__ import annotations

from pathlib import Path

from millrace_ai.contracts import Plane
from millrace_ai.contracts.stage_metadata import known_stage_values, known_stage_values_for_plane

KNOWN_EXECUTION_STAGES = known_stage_values_for_plane(Plane.EXECUTION)
KNOWN_PLANNING_STAGES = known_stage_values_for_plane(Plane.PLANNING)
KNOWN_LEARNING_STAGES = known_stage_values_for_plane(Plane.LEARNING)
KNOWN_STAGES = known_stage_values()
KNOWN_PLANES = {plane.value for plane in Plane}
KNOWN_ASSET_TYPES = {"entrypoint", "skill"}


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
