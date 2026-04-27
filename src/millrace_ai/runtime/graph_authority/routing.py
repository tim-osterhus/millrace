"""Compiled graph routing dispatcher."""

from __future__ import annotations

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.contracts import Plane, RecoveryCounters, RuntimeSnapshot, StageResultEnvelope
from millrace_ai.router import RouterDecision

from .execution import route_execution_stage_result_from_graph
from .learning import route_learning_stage_result_from_graph
from .planning import route_planning_stage_result_from_graph


def route_stage_result_from_graph(
    graph_plan: CompiledRunPlan,
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    counters: RecoveryCounters,
    *,
    max_fix_cycles: int = 2,
    max_troubleshoot_attempts_before_consult: int = 2,
    max_mechanic_attempts: int = 2,
) -> RouterDecision:
    if stage_result.plane is Plane.EXECUTION:
        if max_fix_cycles < 1:
            raise ValueError("max_fix_cycles must be >= 1")
        if max_troubleshoot_attempts_before_consult < 1:
            raise ValueError("max_troubleshoot_attempts_before_consult must be >= 1")
        return route_execution_stage_result_from_graph(
            graph_plan.execution_graph,
            snapshot,
            stage_result,
            counters,
            max_fix_cycles=max_fix_cycles,
            max_troubleshoot_attempts_before_consult=max_troubleshoot_attempts_before_consult,
        )
    if stage_result.plane is Plane.LEARNING:
        if graph_plan.learning_graph is None:
            raise ValueError("compiled graph is missing learning plane")
        return route_learning_stage_result_from_graph(
            graph_plan.learning_graph,
            snapshot,
            stage_result,
        )
    if max_mechanic_attempts < 1:
        raise ValueError("max_mechanic_attempts must be >= 1")
    return route_planning_stage_result_from_graph(
        graph_plan.planning_graph,
        snapshot,
        stage_result,
        counters,
        max_mechanic_attempts=max_mechanic_attempts,
    )


__all__ = ["route_stage_result_from_graph"]
