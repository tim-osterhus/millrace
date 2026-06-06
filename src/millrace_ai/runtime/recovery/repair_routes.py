"""Runtime repair-route resolution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from millrace_ai.compilation.validation.repair_closures import resolve_route_to_node_repair_closures
from millrace_ai.contracts import ExecutionStageName, LearningStageName, Plane, PlanningStageName, StageResultEnvelope
from millrace_ai.runtime.graph_authority.stage_mapping import node_plan_by_id, stage_for_node
from millrace_ai.runtime.scheduler_policy import recovery_fallback_selection

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan
    from millrace_ai.runtime.engine import RuntimeEngine


@dataclass(frozen=True, slots=True)
class RuntimeRepairRoute:
    node_id: str
    stage_kind_id: str
    stage: PlanningStageName | ExecutionStageName | LearningStageName
    counter_name: str | None = None
    threshold: int | None = None


def runtime_repair_route_for_plane(
    engine: RuntimeEngine,
    plane: Plane,
    *,
    compiled_plan: CompiledRunPlan | None = None,
    stage_result: StageResultEnvelope | None = None,
) -> RuntimeRepairRoute | None:
    plan = compiled_plan or engine.compiled_plan
    if plan is None:
        return None
    graph = plan.graphs_by_plane.get(plane)
    if graph is None:
        return None

    policy_route = _runtime_effect_policy_repair_route(
        plan,
        plane=plane,
        stage_result=stage_result,
    )
    if policy_route is not None:
        return policy_route

    # Consult scheduler-policy default recovery fallback before
    # falling through to graph.runtime_failure_recovery.
    scheduler_fallback_node_id = recovery_fallback_selection(
        plan.scheduler_policy
    )
    if scheduler_fallback_node_id is not None:
        try:
            repair_node = node_plan_by_id(graph, scheduler_fallback_node_id)
            repair_stage = stage_for_node(graph, repair_node.node_id)
        except ValueError:
            pass
        else:
            return RuntimeRepairRoute(
                node_id=repair_node.node_id,
                stage_kind_id=repair_node.stage_kind_id,
                stage=repair_stage,
                counter_name=(
                    graph.runtime_failure_recovery.counter_name.value
                    if graph.runtime_failure_recovery is not None
                    else None
                ),
                threshold=(
                    graph.runtime_failure_recovery.threshold
                    if graph.runtime_failure_recovery is not None
                    else None
                ),
            )

    if graph.runtime_failure_recovery is None:
        return None
    repair_node = node_plan_by_id(
        graph,
        graph.runtime_failure_recovery.default_repair_node_id,
    )
    repair_stage = stage_for_node(graph, repair_node.node_id)
    return RuntimeRepairRoute(
        node_id=repair_node.node_id,
        stage_kind_id=repair_node.stage_kind_id,
        stage=repair_stage,
        counter_name=graph.runtime_failure_recovery.counter_name.value,
        threshold=graph.runtime_failure_recovery.threshold,
    )


def runtime_repair_attempts_exhausted(
    engine: RuntimeEngine,
    repair_route: RuntimeRepairRoute,
) -> bool:
    if engine.snapshot is None or repair_route.counter_name is None or repair_route.threshold is None:
        return False
    return int(getattr(engine.snapshot, repair_route.counter_name, 0)) >= repair_route.threshold


def incremented_repair_counter(
    engine: RuntimeEngine,
    repair_route: RuntimeRepairRoute,
) -> dict[str, int]:
    if engine.snapshot is None or repair_route.counter_name is None:
        return {}
    current = int(getattr(engine.snapshot, repair_route.counter_name, 0))
    return {repair_route.counter_name: current + 1}


def _runtime_effect_policy_repair_route(
    compiled_plan: CompiledRunPlan,
    *,
    plane: Plane,
    stage_result: StageResultEnvelope | None,
) -> RuntimeRepairRoute | None:
    if stage_result is None:
        return None
    policy_id = _metadata_string(stage_result.metadata.get("runtime_effect_failure_policy_id"))
    operation_id = _metadata_string(stage_result.metadata.get("runtime_effect_operation_id"))
    failure_class = _metadata_string(stage_result.metadata.get("runtime_effect_failure_class"))
    if policy_id is None or operation_id is None or failure_class is None:
        return None

    policy = compiled_plan.runtime_failure_policies_by_id.get(policy_id)
    if policy is None or policy.action != "route_to_node":
        return None
    if policy.applies_to_planes and plane not in policy.applies_to_planes:
        return None

    try:
        bindings = resolve_route_to_node_repair_closures(
            policy,
            runtime_effect_operations_by_id=compiled_plan.runtime_effect_operations_by_id,
            runtime_effect_runners_by_id=compiled_plan.runtime_effect_runners_by_id,
        )
    except Exception:
        return None
    binding = next(
        (
            candidate
            for candidate in bindings
            if candidate.source_operation_id == operation_id and candidate.failure_class == failure_class
        ),
        None,
    )
    if binding is None:
        return None

    graph = compiled_plan.graphs_by_plane.get(plane)
    if graph is None:
        return None
    try:
        target = node_plan_by_id(graph, binding.target_node_id)
        stage = stage_for_node(graph, target.node_id)
    except ValueError:
        return None
    counter_name = None
    threshold = None
    if graph.runtime_failure_recovery is not None:
        counter_name = graph.runtime_failure_recovery.counter_name.value
        threshold = graph.runtime_failure_recovery.threshold
    return RuntimeRepairRoute(
        node_id=target.node_id,
        stage_kind_id=target.stage_kind_id,
        stage=stage,
        counter_name=counter_name,
        threshold=threshold,
    )


def _metadata_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


__all__ = [
    "RuntimeRepairRoute",
    "incremented_repair_counter",
    "runtime_repair_attempts_exhausted",
    "runtime_repair_route_for_plane",
]
