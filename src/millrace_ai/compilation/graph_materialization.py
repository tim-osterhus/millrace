"""Compiler graph-plane materialization."""

from __future__ import annotations

from millrace_ai.architecture import (
    CompiledGraphEntryPlan,
    FrozenGraphPlanePlan,
    GraphLoopDefinition,
    RegisteredStageKindDefinition,
)
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import ModeDefinition, Plane, StageName

from .completion import compile_graph_completion_entry
from .node_materialization import materialize_graph_node_plan, stage_name_for_identifier
from .policies import compile_graph_resume_policies, compile_graph_threshold_policies
from .transitions import compile_graph_transitions


def materialize_graph_plane_plan(
    *,
    graph_loop: GraphLoopDefinition,
    mode: ModeDefinition,
    config: RuntimeConfig,
    stage_kinds: dict[str, RegisteredStageKindDefinition],
) -> FrozenGraphPlanePlan:
    node_plans = tuple(
        materialize_graph_node_plan(
            node=node,
            plane=graph_loop.plane,
            mode=mode,
            config=config,
            stage_kinds=stage_kinds,
        )
        for node in graph_loop.nodes
    )
    node_plan_by_id = {node.node_id: node for node in node_plans}
    return FrozenGraphPlanePlan(
        loop_id=graph_loop.loop_id,
        plane=graph_loop.plane,
        nodes=node_plans,
        entry_nodes=graph_loop.entry_nodes,
        transitions=graph_loop.edges,
        compiled_entries=tuple(
            CompiledGraphEntryPlan(
                entry_key=entry.entry_key,
                node_id=entry.node_id,
                stage_kind_id=node_plan_by_id[entry.node_id].stage_kind_id,
                plane=graph_loop.plane,
            )
            for entry in graph_loop.entry_nodes
        ),
        compiled_completion_entry=compile_graph_completion_entry(
            graph_loop=graph_loop,
            node_plan_by_id=node_plan_by_id,
        ),
        compiled_transitions=compile_graph_transitions(graph_loop.edges),
        compiled_resume_policies=compile_graph_resume_policies(
            graph_loop.dynamic_policies.resume_policies
            if graph_loop.dynamic_policies is not None
            else ()
        ),
        compiled_threshold_policies=compile_graph_threshold_policies(
            graph_loop.dynamic_policies.threshold_policies
            if graph_loop.dynamic_policies is not None
            else (),
            config=config,
        ),
        terminal_states=graph_loop.terminal_states,
        completion_behavior=graph_loop.completion_behavior,
    )


def selected_stages_for_graph_loops(*graph_loops: GraphLoopDefinition) -> set[StageName]:
    selected_stages: set[StageName] = set()
    for graph_loop in graph_loops:
        for node in graph_loop.nodes:
            stage_name = stage_name_for_identifier(node.stage_kind_id)
            if stage_name is not None:
                selected_stages.add(stage_name)
    return selected_stages


def graph_preview_mode_definition(graph_loop: GraphLoopDefinition) -> ModeDefinition:
    loop_ids_by_plane = {
        Plane.EXECUTION: graph_loop.loop_id
        if graph_loop.plane is Plane.EXECUTION
        else "execution.preview",
        Plane.PLANNING: graph_loop.loop_id
        if graph_loop.plane is Plane.PLANNING
        else "planning.preview",
    }
    if graph_loop.plane is Plane.LEARNING:
        loop_ids_by_plane[Plane.LEARNING] = graph_loop.loop_id
    return ModeDefinition(
        mode_id=f"graph_preview.{graph_loop.loop_id}",
        loop_ids_by_plane=loop_ids_by_plane,
    )


def build_graph_source_refs(
    mode_id: str,
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    *,
    has_planning_completion_behavior: bool,
) -> tuple[str, ...]:
    refs = [
        f"mode:{mode_id}",
        *[
            f"graph_loop:{graph.loop_id}"
            for _plane, graph in sorted(graphs_by_plane.items(), key=lambda item: item[0].value)
        ],
    ]
    if has_planning_completion_behavior:
        refs.append(f"graph_completion_behavior:{graphs_by_plane[Plane.PLANNING].loop_id}")
    return tuple(refs)


__all__ = [
    "build_graph_source_refs",
    "graph_preview_mode_definition",
    "materialize_graph_plane_plan",
    "selected_stages_for_graph_loops",
]
