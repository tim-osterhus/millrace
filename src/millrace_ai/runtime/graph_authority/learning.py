"""Learning-plane compiled graph routing.

Compatibility surface — the learning plane's graph routing is now unified
through ``route_generic_stage_result_from_graph`` just like execution and
planning. This module is a thin wrapper preserved for import compatibility.

All public exports are preserved for import compatibility.
"""

from __future__ import annotations

from millrace_ai.architecture import CompiledRunPlan, FrozenGraphPlanePlan
from millrace_ai.contracts import (
    LearningStageName,
    RecoveryCounters,
    RuntimeSnapshot,
    StageResultEnvelope,
)
from millrace_ai.contracts.router import RouterDecision

from .generic_router import (
    _threshold_failure_class_default,
    _threshold_reason,
    learning_terminal_failure_class,
    learning_terminal_reason,
    route_generic_stage_result_from_graph,
)


def route_learning_stage_result_from_graph(
    graph_plan: CompiledRunPlan,
    graph: FrozenGraphPlanePlan,
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    counters: RecoveryCounters | None = None,
    **kwargs: object,
) -> RouterDecision:
    """
    Route a learning-plane stage result through the compiled graph.

    Compatibility wrapper — delegates to the generic compiled-graph router.
    """
    return route_generic_stage_result_from_graph(
        graph_plan,
        graph,
        snapshot,
        stage_result,
        counters or RecoveryCounters(),
        source_stage=LearningStageName(stage_result.stage),
        terminal_failure_class_fn=learning_terminal_failure_class,
        terminal_reason_fn=learning_terminal_reason,
        default_threshold_failure_class_fn=_threshold_failure_class_default,
        threshold_reason_fn=_threshold_reason,
        **kwargs,
    )


__all__ = ["route_learning_stage_result_from_graph"]
