"""Read-only compiled plan readers."""

from __future__ import annotations

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.compilation.graph_exports import export_compiled_stage_graphs
from millrace_ai.compilation.persistence import load_existing_plan
from millrace_ai.contracts.graph_exports import CompiledStageGraphExport
from millrace_ai.paths import workspace_paths

from millrace_web.models import (
    CompiledPlanSummary,
    StageEdgeSummary,
    StageGraphSummary,
    StageNodeSummary,
    WorkspaceRef,
)


def read_compiled_plan_summary(workspace: WorkspaceRef, *, snapshot_plan_id: str | None = None) -> CompiledPlanSummary:
    paths = workspace_paths(workspace.path)
    plan = load_existing_plan(paths.state_dir / "compiled_plan.json")
    if plan is None:
        return CompiledPlanSummary(id=snapshot_plan_id, currentness="missing", mode_id=None)
    return CompiledPlanSummary(id=plan.compiled_plan_id, currentness="current", mode_id=plan.mode_id)


def read_stage_graphs(workspace: WorkspaceRef) -> tuple[StageGraphSummary, ...]:
    return _graphs_from_export_payloads(read_compiled_stage_graph_export_payloads(workspace))


def read_compiled_stage_graph_exports(workspace: WorkspaceRef) -> tuple[CompiledStageGraphExport, ...]:
    paths = workspace_paths(workspace.path)
    plan = load_existing_plan(paths.state_dir / "compiled_plan.json")
    if plan is None:
        return ()
    return export_compiled_stage_graphs(plan)


def read_compiled_stage_graph_export_payloads(workspace: WorkspaceRef) -> tuple[dict[str, object], ...]:
    paths = workspace_paths(workspace.path)
    plan = load_existing_plan(paths.state_dir / "compiled_plan.json")
    if plan is None:
        return ()
    exports = export_compiled_stage_graphs(plan)
    payloads: list[dict[str, object]] = []
    for export in exports:
        payload = export.model_dump(mode="json")
        _enrich_terminal_state_payloads(plan, payload)
        payloads.append(payload)
    return tuple(payloads)


def _graphs_from_plan(plan: CompiledRunPlan) -> tuple[StageGraphSummary, ...]:
    exports = export_compiled_stage_graphs(plan)
    payloads: list[dict[str, object]] = []
    for export in exports:
        payload = export.model_dump(mode="json")
        _enrich_terminal_state_payloads(plan, payload)
        payloads.append(payload)
    return _graphs_from_export_payloads(tuple(payloads))


def _graphs_from_export_payloads(exports: tuple[dict[str, object], ...]) -> tuple[StageGraphSummary, ...]:
    graphs: list[StageGraphSummary] = []
    for graph in exports:
        terminal_states_by_id = {
            str(state["terminal_state_id"]): state
            for state in graph.get("terminal_states", ())
            if isinstance(state, dict) and state.get("terminal_state_id") is not None
        }
        graphs.append(
            StageGraphSummary(
                plane=str(graph["plane"]),
                loop_id=str(graph["loop_id"]),
                nodes=tuple(
                    StageNodeSummary(
                        node_id=str(node["node_id"]),
                        stage_kind_id=str(node["stage_kind_id"]),
                        plane=str(node["plane"]),
                        label=str(node["stage_kind_id"]),
                    )
                    for node in graph.get("nodes", ())
                    if isinstance(node, dict)
                ),
                edges=tuple(
                    _stage_edge_summary(edge, terminal_states_by_id)
                    for edge in graph.get("edges", ())
                    if isinstance(edge, dict)
                ),
            )
        )
    return tuple(graphs)


def _graphs_from_exports(exports: tuple[CompiledStageGraphExport, ...]) -> tuple[StageGraphSummary, ...]:
    graphs: list[StageGraphSummary] = []
    for graph in exports:
        terminal_states_by_id = {
            state.terminal_state_id: state for state in graph.terminal_states
        }
        graphs.append(
            StageGraphSummary(
                plane=graph.plane.value,
                loop_id=graph.loop_id,
                nodes=tuple(
                    StageNodeSummary(
                        node_id=node.node_id,
                        stage_kind_id=node.stage_kind_id,
                        plane=node.plane.value,
                        label=node.stage_kind_id,
                    )
                    for node in graph.nodes
                ),
                edges=tuple(
                    StageEdgeSummary(
                        source_node_id=edge.source_node_id,
                        target_node_id=edge.target_node_id,
                        terminal_state_id=edge.terminal_state_id,
                        outcome=edge.outcome,
                        kind=edge.kind,
                        terminal_action_id=(
                            getattr(
                                terminal_states_by_id[edge.terminal_state_id],
                                "terminal_action_id",
                                None,
                            )
                            if edge.terminal_state_id in terminal_states_by_id
                            else None
                        ),
                        terminal_action_router_consequence=(
                            getattr(
                                terminal_states_by_id[edge.terminal_state_id],
                                "terminal_action_router_consequence",
                                None,
                            )
                            if edge.terminal_state_id in terminal_states_by_id
                            else None
                        ),
                        lifecycle_mutation_plan_id=(
                            getattr(
                                terminal_states_by_id[edge.terminal_state_id],
                                "lifecycle_mutation_plan_id",
                                None,
                            )
                            if edge.terminal_state_id in terminal_states_by_id
                            else None
                        ),
                        lifecycle_action_id=(
                            getattr(
                                terminal_states_by_id[edge.terminal_state_id],
                                "lifecycle_action_id",
                                None,
                            )
                            if edge.terminal_state_id in terminal_states_by_id
                            else None
                        ),
                        terminal_writes_status=(
                            terminal_states_by_id[edge.terminal_state_id].writes_status
                            if edge.terminal_state_id in terminal_states_by_id
                            else None
                        ),
                        terminal_create_incident=(
                            getattr(
                                terminal_states_by_id[edge.terminal_state_id],
                                "create_incident",
                                False,
                            )
                            if edge.terminal_state_id in terminal_states_by_id
                            else False
                        ),
                    )
                    for edge in graph.edges
                ),
            )
        )
    return tuple(graphs)


def _stage_edge_summary(
    edge: dict[str, object],
    terminal_states_by_id: dict[str, dict[str, object]],
) -> StageEdgeSummary:
    terminal_state_id = edge.get("terminal_state_id")
    terminal_state = (
        terminal_states_by_id.get(str(terminal_state_id))
        if terminal_state_id is not None
        else None
    )
    return StageEdgeSummary(
        source_node_id=str(edge["source_node_id"]),
        target_node_id=_optional_str(edge.get("target_node_id")),
        terminal_state_id=_optional_str(terminal_state_id),
        outcome=str(edge["outcome"]),
        kind=str(edge["kind"]),
        terminal_action_id=_optional_str(
            terminal_state.get("terminal_action_id") if terminal_state is not None else None
        ),
        terminal_action_router_consequence=_optional_str(
            terminal_state.get("terminal_action_router_consequence")
            if terminal_state is not None
            else None
        ),
        lifecycle_mutation_plan_id=_optional_str(
            terminal_state.get("lifecycle_mutation_plan_id")
            if terminal_state is not None
            else None
        ),
        lifecycle_action_id=_optional_str(
            terminal_state.get("lifecycle_action_id") if terminal_state is not None else None
        ),
        terminal_writes_status=_optional_str(
            terminal_state.get("writes_status") if terminal_state is not None else None
        ),
        terminal_create_incident=(
            terminal_state.get("create_incident") is True
            if terminal_state is not None
            else False
        ),
    )


def _enrich_terminal_state_payloads(plan: CompiledRunPlan, payload: dict[str, object]) -> None:
    plane = payload.get("plane")
    graph = _graph_for_plane(plan, str(plane))
    terminal_states = {
        state.terminal_state_id: state
        for state in getattr(graph, "terminal_states", ())
    }
    for state_payload in payload.get("terminal_states", ()):
        if not isinstance(state_payload, dict):
            continue
        terminal_state = terminal_states.get(str(state_payload.get("terminal_state_id")))
        if terminal_state is None:
            continue
        terminal_action_id = getattr(terminal_state, "terminal_action_id", None)
        terminal_action = _terminal_action(plan, terminal_action_id)
        lifecycle_plan_id = (
            getattr(terminal_action, "lifecycle_mutation_plan_id", None)
            if terminal_action is not None
            else None
        )
        lifecycle_plan = _lifecycle_plan(plan, lifecycle_plan_id)
        state_payload["terminal_action_id"] = terminal_action_id
        state_payload["terminal_action_router_consequence"] = (
            getattr(terminal_action, "router_consequence", None)
            if terminal_action is not None
            else None
        )
        state_payload["lifecycle_mutation_plan_id"] = lifecycle_plan_id
        state_payload["lifecycle_action_id"] = (
            getattr(lifecycle_plan, "lifecycle_action_id", None)
            if lifecycle_plan is not None
            else None
        )
        state_payload["failure_class"] = (
            getattr(terminal_action, "failure_class", None)
            if terminal_action is not None
            else None
        )
        state_payload["create_incident"] = (
            bool(getattr(terminal_action, "create_incident", False))
            if terminal_action is not None
            else False
        )


def _graph_for_plane(plan: CompiledRunPlan, plane: str):
    graphs_by_plane = getattr(plan, "graphs_by_plane", {})
    for key, graph in getattr(graphs_by_plane, "items", lambda: ())():
        if getattr(key, "value", key) == plane:
            return graph
    if plane == "execution":
        return getattr(plan, "execution_graph", None)
    if plane == "planning":
        return getattr(plan, "planning_graph", None)
    if plane == "learning":
        return getattr(plan, "learning_graph", None)
    return None


def _terminal_action(plan: CompiledRunPlan, terminal_action_id: object):
    if not isinstance(terminal_action_id, str):
        return None
    return getattr(plan, "terminal_actions_by_id", {}).get(terminal_action_id)


def _lifecycle_plan(plan: CompiledRunPlan, lifecycle_plan_id: object):
    if not isinstance(lifecycle_plan_id, str):
        return None
    return getattr(plan, "lifecycle_mutation_plans_by_id", {}).get(lifecycle_plan_id)


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = [
    "read_compiled_plan_summary",
    "read_compiled_stage_graph_export_payloads",
    "read_compiled_stage_graph_exports",
    "read_stage_graphs",
]
