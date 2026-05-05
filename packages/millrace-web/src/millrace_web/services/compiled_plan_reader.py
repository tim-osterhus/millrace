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
    return _graphs_from_exports(read_compiled_stage_graph_exports(workspace))


def read_compiled_stage_graph_exports(workspace: WorkspaceRef) -> tuple[CompiledStageGraphExport, ...]:
    paths = workspace_paths(workspace.path)
    plan = load_existing_plan(paths.state_dir / "compiled_plan.json")
    if plan is None:
        return ()
    return export_compiled_stage_graphs(plan)


def _graphs_from_plan(plan: CompiledRunPlan) -> tuple[StageGraphSummary, ...]:
    return _graphs_from_exports(export_compiled_stage_graphs(plan))


def _graphs_from_exports(exports: tuple[CompiledStageGraphExport, ...]) -> tuple[StageGraphSummary, ...]:
    graphs: list[StageGraphSummary] = []
    for graph in exports:
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
                    )
                    for edge in graph.edges
                ),
            )
        )
    return tuple(graphs)


__all__ = [
    "read_compiled_plan_summary",
    "read_compiled_stage_graph_exports",
    "read_stage_graphs",
]
