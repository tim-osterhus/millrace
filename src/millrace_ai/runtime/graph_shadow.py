"""Optional legacy-oracle comparison helpers for compiled-graph runtime authority."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pydantic import JsonValue

from millrace_ai.contracts import (
    ExecutionStageName,
    Plane,
    PlanningStageName,
    RecoveryCounters,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.events import write_runtime_event
from millrace_ai.router import RouterDecision, next_execution_step, next_planning_step

from .graph_authority import GraphActivationDecision

if TYPE_CHECKING:
    from millrace_ai.contracts import RuntimeSnapshot
    from millrace_ai.runtime.engine import RuntimeEngine

_ENV_VAR = "MILLRACE_ENABLE_GRAPH_SHADOW_VALIDATION"


def shadow_validation_enabled() -> bool:
    value = os.getenv(_ENV_VAR, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def maybe_report_work_item_activation_mismatch(
    engine: RuntimeEngine,
    *,
    work_item_kind: WorkItemKind,
    graph_decision: GraphActivationDecision,
) -> None:
    if not shadow_validation_enabled():
        return

    legacy_stage = _legacy_entry_stage_for_kind(work_item_kind)
    legacy_plane = _plane_for_stage(legacy_stage)
    if legacy_plane is graph_decision.plane and legacy_stage == graph_decision.stage:
        return

    write_runtime_event(
        engine.paths,
        event_type="compiled_graph_activation_mismatch",
        data={
            "work_item_kind": work_item_kind.value,
            "legacy_plane": legacy_plane.value,
            "legacy_stage": legacy_stage.value,
            "graph_plane": graph_decision.plane.value,
            "graph_stage": graph_decision.stage.value,
            "graph_node_id": graph_decision.node_id,
            "graph_stage_kind_id": graph_decision.stage_kind_id,
            "graph_entry_key": graph_decision.entry_key,
        },
    )


def maybe_report_completion_activation_mismatch(
    engine: RuntimeEngine,
    *,
    graph_decision: GraphActivationDecision,
) -> None:
    if not shadow_validation_enabled():
        return
    assert engine.compiled_plan is not None

    completion_behavior = engine.compiled_plan.completion_behavior
    if completion_behavior is None:
        return

    legacy_stage = completion_behavior.stage
    legacy_plane = _plane_for_stage(legacy_stage)
    if legacy_plane is graph_decision.plane and legacy_stage == graph_decision.stage:
        return

    write_runtime_event(
        engine.paths,
        event_type="compiled_graph_completion_activation_mismatch",
        data={
            "legacy_plane": legacy_plane.value,
            "legacy_stage": legacy_stage.value,
            "graph_plane": graph_decision.plane.value,
            "graph_stage": graph_decision.stage.value,
            "graph_node_id": graph_decision.node_id,
            "graph_stage_kind_id": graph_decision.stage_kind_id,
            "graph_entry_key": graph_decision.entry_key,
        },
    )


def maybe_report_routing_mismatch(
    engine: RuntimeEngine,
    *,
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    counters: RecoveryCounters,
    graph_decision: RouterDecision,
) -> None:
    if not shadow_validation_enabled():
        return
    assert engine.config is not None

    if stage_result.plane is Plane.EXECUTION:
        legacy_decision = next_execution_step(
            snapshot,
            stage_result,
            counters,
            max_fix_cycles=engine.config.recovery.max_fix_cycles,
            max_troubleshoot_attempts_before_consult=(
                engine.config.recovery.max_troubleshoot_attempts_before_consult
            ),
        )
    else:
        legacy_decision = next_planning_step(
            snapshot,
            stage_result,
            counters,
            max_mechanic_attempts=engine.config.recovery.max_mechanic_attempts,
        )

    if _decision_payload(legacy_decision) == _decision_payload(graph_decision):
        return

    write_runtime_event(
        engine.paths,
        event_type="compiled_graph_routing_mismatch",
        data={
            "plane": stage_result.plane.value,
            "stage": stage_result.stage.value,
            "terminal_result": stage_result.terminal_result.value,
            "request_kind": (
                stage_result.metadata.get("request_kind")
                if isinstance(stage_result.metadata.get("request_kind"), str)
                else None
            ),
            "legacy_decision": _decision_payload(legacy_decision),
            "graph_decision": _decision_payload(graph_decision),
        },
    )


def _decision_payload(decision: RouterDecision) -> dict[str, JsonValue]:
    return {
        "action": decision.action.value,
        "next_plane": decision.next_plane.value if decision.next_plane is not None else None,
        "next_stage": decision.next_stage.value if decision.next_stage is not None else None,
        "reason": decision.reason,
        "failure_class": decision.failure_class,
        "counter_key": decision.counter_key,
        "create_incident": decision.create_incident,
    }


def _plane_for_stage(stage: ExecutionStageName | PlanningStageName) -> Plane:
    return Plane.EXECUTION if isinstance(stage, ExecutionStageName) else Plane.PLANNING


def _legacy_entry_stage_for_kind(work_item_kind: WorkItemKind) -> ExecutionStageName | PlanningStageName:
    if work_item_kind is WorkItemKind.TASK:
        return ExecutionStageName.BUILDER
    if work_item_kind is WorkItemKind.SPEC:
        return PlanningStageName.PLANNER
    return PlanningStageName.AUDITOR


__all__ = [
    "maybe_report_completion_activation_mismatch",
    "maybe_report_routing_mismatch",
    "maybe_report_work_item_activation_mismatch",
    "shadow_validation_enabled",
]
