"""Compiled graph routing dispatcher and public entrypoint."""

from __future__ import annotations

from collections.abc import Callable

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.contracts import (
    Plane,
    RecoveryCounters,
    RuntimeSnapshot,
    StageResultEnvelope,
)
from millrace_ai.router import RouterDecision

from .execution import route_execution_stage_result_from_graph
from .generic_router import route_generic_stage_result_from_graph  # noqa: F401 — re-export
from .learning import route_learning_stage_result_from_graph
from .planning import route_planning_stage_result_from_graph
from .validation import validate_stage_result_matches_snapshot

# Re-export the generic router for callers that import from ``routing``.
# The active dispatch authority lives in ``generic_router.py``.


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
    """
    Resolve a stage result through the compiled graph for the result's plane.

    This is the public dispatch entrypoint. It validates identity, resolves
    the per-plane graph, and delegates to the per-plane router wrapper.
    The active dispatch authority lives in ``generic_router.py``.
    """
    # Validate stage-result identity against active run before any mutable
    # routing consequence — no durable state mutation without identity check.
    validate_stage_result_matches_snapshot(
        snapshot, stage_result, expected_plane=stage_result.plane
    )

    if max_fix_cycles < 1:
        raise ValueError("max_fix_cycles must be >= 1")
    if max_troubleshoot_attempts_before_consult < 1:
        raise ValueError("max_troubleshoot_attempts_before_consult must be >= 1")
    if max_mechanic_attempts < 1:
        raise ValueError("max_mechanic_attempts must be >= 1")

    # Resolve the compiled graph plane plan from compiled-plan metadata using
    # stage-result identity — routing decisions use compiled graph metadata
    # plus stage-result identity, not plane-enum branching in the active path.
    graph = graph_plan.graphs_by_plane.get(stage_result.plane)
    if graph is None:
        raise ValueError(f"compiled plan has no graph for plane {stage_result.plane.value}")

    # Per-plane internal helpers remain, but the active dispatch authority is
    # the compiled-plan-resolved graph, not a plane-enum branch.
    router = _PLANE_ROUTERS.get(stage_result.plane)
    if router is None:
        raise ValueError(f"no router registered for plane {stage_result.plane.value}")
    return router(
        graph_plan,
        graph,
        snapshot,
        stage_result,
        counters,
        max_fix_cycles=max_fix_cycles,
        max_troubleshoot_attempts_before_consult=max_troubleshoot_attempts_before_consult,
        max_mechanic_attempts=max_mechanic_attempts,
    )


_PLANE_ROUTERS: dict[Plane, Callable[..., RouterDecision]] = {
    Plane.EXECUTION: route_execution_stage_result_from_graph,
    Plane.LEARNING: route_learning_stage_result_from_graph,
    Plane.PLANNING: route_planning_stage_result_from_graph,
}


__all__ = [
    "route_generic_stage_result_from_graph",
    "route_stage_result_from_graph",
]
