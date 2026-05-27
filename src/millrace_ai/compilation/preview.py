"""Compiler graph preview API."""

from __future__ import annotations

from pathlib import Path

from millrace_ai.architecture import FrozenGraphPlanePlan
from millrace_ai.assets import (
    discover_stage_kind_definitions,
    load_builtin_workflow_primitives,
    load_graph_loop_definition,
)
from millrace_ai.config import RuntimeConfig

from .graph_materialization import graph_preview_mode_definition, materialize_graph_plane_plan


def preview_graph_loop_plan(
    loop_id: str,
    *,
    config: RuntimeConfig,
    assets_root: Path | None = None,
) -> FrozenGraphPlanePlan:
    """Materialize one discovered graph loop into a non-authoritative plane plan."""

    graph_loop = load_graph_loop_definition(loop_id, assets_root=assets_root)
    stage_kinds = {
        stage_kind.stage_kind_id: stage_kind
        for stage_kind in discover_stage_kind_definitions(assets_root=assets_root)
    }
    workflow_primitives = load_builtin_workflow_primitives(assets_root=assets_root)
    request_context_profiles_by_id = {
        profile.profile_id: profile
        for profile in workflow_primitives.request_context_profiles
    }
    return materialize_graph_plane_plan(
        graph_loop=graph_loop,
        mode=graph_preview_mode_definition(graph_loop),
        config=config,
        stage_kinds=stage_kinds,
        request_context_profiles_by_id=request_context_profiles_by_id,
    )


__all__ = ["preview_graph_loop_plan"]
