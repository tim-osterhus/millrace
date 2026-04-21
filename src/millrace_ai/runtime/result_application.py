"""Stable façade over routed post-stage mutation helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from millrace_ai.contracts import (
    ExecutionStageName,
    Plane,
    PlanningStageName,
    StageResultEnvelope,
)
from millrace_ai.router import RouterAction, RouterDecision, next_execution_step, next_planning_step

from .closure_transitions import apply_closure_target_router_decision
from .error_recovery import clear_runtime_error_context
from .handoff_incidents import enqueue_handoff_incident
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
    from millrace_ai.runtime.engine import RuntimeEngine


def route_stage_result(engine: RuntimeEngine, stage_result: StageResultEnvelope) -> RouterDecision:
    assert engine.snapshot is not None
    assert engine.counters is not None
    if stage_result.plane is Plane.EXECUTION:
        return next_execution_step(
            engine.snapshot,
            stage_result,
            engine.counters,
            max_fix_cycles=engine.config.recovery.max_fix_cycles if engine.config else 2,
            max_troubleshoot_attempts_before_consult=(
                engine.config.recovery.max_troubleshoot_attempts_before_consult if engine.config else 2
            ),
        )
    return next_planning_step(
        engine.snapshot,
        stage_result,
        engine.counters,
        max_mechanic_attempts=engine.config.recovery.max_mechanic_attempts if engine.config else 2,
    )


def apply_router_decision(engine: RuntimeEngine, decision: RouterDecision, stage_result: StageResultEnvelope) -> None:
    assert engine.snapshot is not None
    assert engine.counters is not None

    if stage_result.stage in {ExecutionStageName.TROUBLESHOOTER, PlanningStageName.MECHANIC}:
        clear_runtime_error_context(engine.paths)

    if _is_closure_target_result(stage_result):
        apply_closure_target_router_decision(engine, decision, stage_result)
        return

    if decision.action is RouterAction.RUN_STAGE:
        next_stage = decision.next_stage
        assert next_stage is not None
        updated = engine.snapshot.model_copy(
            update={
                "active_plane": Plane.EXECUTION if isinstance(next_stage, ExecutionStageName) else Plane.PLANNING,
                "active_stage": next_stage,
                "active_since": engine._now(),
                "current_failure_class": decision.failure_class,
                "updated_at": engine._now(),
            }
        )
        engine.snapshot = increment_route_counters(engine, updated, decision, stage_result)
        return

    if decision.action is RouterAction.IDLE:
        apply_idle_router_decision(engine, stage_result)
        return

    if decision.action is RouterAction.HANDOFF:
        apply_handoff_router_decision(engine, decision, stage_result)
        return

    if decision.action is RouterAction.BLOCKED:
        apply_blocked_router_decision(engine, decision, stage_result)
        return

    raise ValueError(f"Unsupported router action: {decision.action.value}")


def _is_closure_target_result(stage_result: StageResultEnvelope) -> bool:
    return stage_result.metadata.get("request_kind") == "closure_target"


__all__ = [
    "apply_router_decision",
    "enqueue_handoff_incident",
    "increment_counter_field",
    "increment_route_counters",
    "mark_active_work_item_blocked",
    "mark_active_work_item_blocked_with_recovery",
    "mark_active_work_item_complete",
    "route_stage_result",
    "write_plane_status",
    "write_stage_result",
]
