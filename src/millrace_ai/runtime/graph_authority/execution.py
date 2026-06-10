"""Execution-plane compiled graph routing compatibility wrapper.

This module is a compatibility surface over the generic compiled-graph router
in `routing.py`. The active dispatch authority lives in
`route_generic_stage_result_from_graph`; this module forwards to it with
plane-specific terminal-reason and failure-class formatters.

All public exports are preserved for import compatibility.
"""

from __future__ import annotations

from millrace_ai.architecture import (
    CompiledRunPlan,
    FrozenGraphPlanePlan,
)
from millrace_ai.contracts import (
    ExecutionStageName,
    RecoveryCounters,
    RuntimeSnapshot,
    StageResultEnvelope,
)
from millrace_ai.router import RouterDecision

from .generic_router import (
    _threshold_failure_class_default,
    _threshold_reason,
    execution_terminal_failure_class,
    execution_terminal_reason,
    route_generic_stage_result_from_graph,
)


def route_execution_stage_result_from_graph(
    graph_plan: CompiledRunPlan,
    graph: FrozenGraphPlanePlan,
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    counters: RecoveryCounters,
    **kwargs: object,
) -> RouterDecision:
    """
    Route an execution-plane stage result through the compiled graph.

    Compatibility wrapper — delegates to the generic compiled-graph router.
    Recovery thresholds are read from compiled graph threshold policies;
    no route-time recovery knobs are accepted.
    """
    return route_generic_stage_result_from_graph(
        graph_plan,
        graph,
        snapshot,
        stage_result,
        counters,
        source_stage=ExecutionStageName(stage_result.stage),
        terminal_failure_class_fn=execution_terminal_failure_class,
        terminal_reason_fn=execution_terminal_reason,
        default_threshold_failure_class_fn=_threshold_failure_class_default,
        threshold_reason_fn=_threshold_reason,
        **kwargs,
    )





def decision_from_execution_transition(
    graph_plan: CompiledRunPlan,
    graph: FrozenGraphPlanePlan,
    snapshot: RuntimeSnapshot,
    *,
    source_stage: ExecutionStageName,
    stage_result: StageResultEnvelope,
    transition: object,
    threshold_policy: object = None,
) -> RouterDecision:
    """
    Compatibility shim — legacy export preserved for import compatibility.

    Active routing does not depend on this path. The generic router in
    routing.py owns active dispatch. This function is retained only so
    that existing imports from ``.execution import decision_from_execution_transition``
    continue to work.
    """
    raise NotImplementedError(
        "decision_from_execution_transition is a legacy compatibility export. "
        "Active routing uses route_generic_stage_result_from_graph in routing.py. "
        "If you need the logic previously provided by this function, import "
        "the generic router directly."
    )


__all__ = [
    "decision_from_execution_transition",
    "route_execution_stage_result_from_graph",
]
