"""Stable façade over routed post-stage mutation helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.contracts import (
    ActiveRunState,
    ExecutionStageName,
    Plane,
    PlanningStageName,
    StageName,
    StageResultEnvelope,
)
from millrace_ai.contracts.stage_metadata import stage_plane
from millrace_ai.router import RouterAction, RouterDecision

from .active_runs import (
    active_run_for_plane,
    snapshot_projected_to_plane,
    snapshot_with_next_stage_for_plane,
)
from .closure_transitions import apply_closure_target_router_decision
from .compiled_plans import CompiledPlanAuthorityError, load_compiled_plan_by_id
from .completion_behavior import active_closure_target, block_on_closure_lineage_drift_if_present
from .error_recovery import clear_runtime_error_context
from .graph_authority import route_stage_result_from_graph
from .graph_authority.stage_mapping import node_plan_by_id
from .handoff_incidents import enqueue_handoff_incident
from .lanes import compiled_plan_fingerprint_for_runtime
from .recon_transitions import apply_recon_router_decision, is_recon_stage_result
from .result_counters import increment_counter_field, increment_route_counters
from .stage_result_persistence import write_plane_status, write_stage_result
from .work_item_transitions import (
    apply_blocked_router_decision,
    apply_handoff_router_decision,
    apply_idle_router_decision,
    mark_active_work_item_blocked,
    mark_active_work_item_blocked_with_recovery,
    mark_active_work_item_complete,
)

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan
    from millrace_ai.runtime.engine import RuntimeEngine


def route_stage_result(engine: RuntimeEngine, stage_result: StageResultEnvelope) -> RouterDecision:
    assert engine.snapshot is not None
    assert engine.counters is not None
    assert engine.compiled_plan is not None

    compiled_plan = compiled_plan_for_stage_result(engine, stage_result)
    return route_stage_result_with_plan(engine, stage_result, compiled_plan=compiled_plan)


def route_stage_result_with_plan(
    engine: RuntimeEngine,
    stage_result: StageResultEnvelope,
    *,
    compiled_plan: CompiledRunPlan,
) -> RouterDecision:
    assert engine.snapshot is not None
    assert engine.counters is not None

    projected_snapshot = snapshot_projected_to_plane(engine.snapshot, stage_result.plane)
    decision = route_stage_result_from_graph(
        compiled_plan,
        projected_snapshot,
        stage_result,
        engine.counters,
    )
    return decision


def compiled_plan_for_stage_result(
    engine: RuntimeEngine,
    stage_result: StageResultEnvelope,
) -> CompiledRunPlan:
    assert engine.snapshot is not None
    assert engine.compiled_plan is not None

    active_run = active_run_for_plane(engine.snapshot, stage_result.plane)
    return compiled_plan_for_active_run(engine, active_run)


def compiled_plan_for_active_run(
    engine: RuntimeEngine,
    active_run: ActiveRunState | None,
) -> CompiledRunPlan:
    assert engine.compiled_plan is not None

    if active_run is None:
        return engine.compiled_plan
    if active_run.compiled_plan_id == engine.compiled_plan.compiled_plan_id:
        _validate_launch_fingerprint(engine.compiled_plan, active_run.compiled_plan_fingerprint)
        return engine.compiled_plan

    compiled_plan = load_compiled_plan_by_id(engine.paths, active_run.compiled_plan_id)
    if compiled_plan is None:
        raise CompiledPlanAuthorityError(
            f"active run launch compiled plan is unavailable: {active_run.compiled_plan_id}",
            stale=True,
        )
    _validate_launch_fingerprint(compiled_plan, active_run.compiled_plan_fingerprint)
    return compiled_plan


def _validate_launch_fingerprint(compiled_plan: CompiledRunPlan, expected_fingerprint: str) -> None:
    actual_fingerprint = compiled_plan_fingerprint_for_runtime(compiled_plan)
    if actual_fingerprint != expected_fingerprint:
        raise CompiledPlanAuthorityError(
            f"active run launch compiled plan fingerprint mismatch: {compiled_plan.compiled_plan_id}",
            stale=False,
        )


def apply_router_decision(
    engine: RuntimeEngine,
    decision: RouterDecision,
    stage_result: StageResultEnvelope,
    *,
    stage_result_path: Path | None = None,
    compiled_plan: CompiledRunPlan | None = None,
) -> tuple[Path, ...]:
    assert engine.snapshot is not None
    assert engine.counters is not None

    if stage_result.stage_kind_id in {
        ExecutionStageName.TROUBLESHOOTER.value,
        PlanningStageName.MECHANIC.value,
    }:
        clear_runtime_error_context(engine.paths)

    if _is_closure_target_result(stage_result):
        apply_closure_target_router_decision(engine, decision, stage_result)
        return ()

    if is_recon_stage_result(stage_result):
        effective_plan = compiled_plan or compiled_plan_for_stage_result(engine, stage_result)
        return apply_recon_router_decision(
            engine,
            decision,
            stage_result,
            stage_result_path=stage_result_path,
            compiled_plan=effective_plan,
        )

    if decision.action is RouterAction.RUN_STAGE:
        next_stage = decision.next_stage
        assert next_stage is not None
        updated = snapshot_with_next_stage_for_plane(
            engine.snapshot,
            plane=stage_result.plane,
            stage=next_stage,
            node_id=decision.next_node_id or next_stage.value,
            stage_kind_id=decision.next_stage_kind_id or next_stage.value,
            now=engine._now(),
            current_failure_class=decision.failure_class,
        )
        engine.snapshot = increment_route_counters(engine, updated, decision, stage_result)
        _write_next_stage_running_status(
            engine,
            decision,
            stage_result,
            compiled_plan=compiled_plan,
        )
        return ()

    if decision.action is RouterAction.IDLE:
        apply_idle_router_decision(engine, stage_result, decision=decision)
        if stage_result.stage is PlanningStageName.MANAGER:
            target = active_closure_target(engine)
            if target is not None:
                block_on_closure_lineage_drift_if_present(engine, target)
        return ()

    if decision.action is RouterAction.HANDOFF:
        return apply_handoff_router_decision(
            engine,
            decision,
            stage_result,
            stage_result_path=stage_result_path,
        )

    if decision.action is RouterAction.BLOCKED:
        apply_blocked_router_decision(
            engine,
            decision,
            stage_result,
            stage_result_path=stage_result_path,
        )
        return ()

    raise ValueError(f"Unsupported router action: {decision.action.value}")


def _is_closure_target_result(stage_result: StageResultEnvelope) -> bool:
    return stage_result.metadata.get("request_kind") == "closure_target"


def _plane_for_stage(stage: StageName) -> Plane:
    return stage_plane(stage)


def _write_next_stage_running_status(
    engine: RuntimeEngine,
    decision: RouterDecision,
    stage_result: StageResultEnvelope,
    *,
    compiled_plan: CompiledRunPlan | None,
) -> None:
    effective_plan = compiled_plan or engine.compiled_plan
    if effective_plan is None or decision.next_node_id is None:
        return
    graph = effective_plan.graphs_by_plane.get(stage_result.plane)
    if graph is None:
        return
    try:
        next_node = node_plan_by_id(graph, decision.next_node_id)
    except ValueError:
        return
    marker = next_node.running_status_marker
    if not marker:
        return
    engine._mark_active_stage_running(
        plane=stage_result.plane,
        stage=decision.next_stage or stage_result.stage,
        running_status_marker=marker,
        run_id=stage_result.run_id,
    )


__all__ = [
    "apply_router_decision",
    "compiled_plan_for_active_run",
    "compiled_plan_for_stage_result",
    "enqueue_handoff_incident",
    "increment_counter_field",
    "increment_route_counters",
    "mark_active_work_item_blocked",
    "mark_active_work_item_blocked_with_recovery",
    "mark_active_work_item_complete",
    "route_stage_result",
    "route_stage_result_with_plan",
    "write_plane_status",
    "write_stage_result",
]
