"""Compiled graph routing dispatcher and public entrypoint."""

from __future__ import annotations

from millrace_ai.architecture import CompiledGraphThresholdPolicyPlan, CompiledRunPlan
from millrace_ai.architecture.loop_graphs import GraphLoopTerminalClass
from millrace_ai.contracts import (
    Plane,
    RecoveryCounters,
    RuntimeSnapshot,
    StageName,
    StageResultEnvelope,
)
from millrace_ai.contracts.router import RouterDecision

from .counters import normalize_failure_class
from .generic_router import route_generic_stage_result_from_graph  # noqa: F401 — re-export
from .validation import validate_stage_result_matches_snapshot


def route_stage_result_from_graph(
    graph_plan: CompiledRunPlan,
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    counters: RecoveryCounters,
) -> RouterDecision:
    """
    Resolve a stage result through the compiled graph for the result's plane.

    This is the single active dispatch entrypoint. All planes route through
    ``route_generic_stage_result_from_graph`` with metadata-derived formatter
    callbacks. No per-plane wrapper dispatch or max-cycle recovery knobs.

    Recovery thresholds are read from compiled graph threshold policies;
    recovery config values are materialized at compile time.
    """
    validate_stage_result_matches_snapshot(
        snapshot, stage_result, expected_plane=stage_result.plane
    )

    graph = graph_plan.graphs_by_plane.get(stage_result.plane)
    if graph is None:
        raise ValueError(f"compiled plan has no graph for plane {stage_result.plane.value}")

    source_stage = _source_stage_from_result(stage_result)
    return route_generic_stage_result_from_graph(
        graph_plan,
        graph,
        snapshot,
        stage_result,
        counters,
        source_stage=source_stage,
        terminal_failure_class_fn=_generic_terminal_failure_class,
        terminal_reason_fn=_generic_terminal_reason,
        default_threshold_failure_class_fn=_generic_threshold_failure_class_default,
        threshold_reason_fn=_generic_threshold_reason,
    )


# ---------------------------------------------------------------------------
# Generic formatter callbacks (plane-agnostic, metadata-derived)
# ---------------------------------------------------------------------------


def _source_stage_from_result(stage_result: StageResultEnvelope) -> StageName:
    """Derive the canonical stage identity from the stage result."""
    return stage_result.stage


def _generic_terminal_reason(
    stage_result: StageResultEnvelope,
    source_stage: StageName,
    terminal_result: str,
    writes_status: str,
    router_reason: str | None,
) -> str:
    """Generic terminal reason derived from compiled node identity.

    Prefers compiled ``router_reason`` when it matches the status being written;
    otherwise falls back to ``stage_result.node_id`` (compiled node identity)
    rather than ``source_stage.value`` (runtime stage-name string).

    Uses ``_blocked`` suffix only for truly blocked results (result_class BLOCKED);
    non-success results like NO_OP are formatted as ``node_id:terminal_result``.

    When ``terminal_result`` equals ``writes_status`` and the result is a
    success, the prior planning-plane convention of returning the bare
    ``node_id`` is preserved so ``arbiter`` resolves instead of
    ``arbiter:ARBITER_COMPLETE``.
    """
    from millrace_ai.contracts import ResultClass

    if router_reason is not None and terminal_result == writes_status:
        return router_reason
    if stage_result.result_class is ResultClass.BLOCKED:
        return f"{stage_result.node_id}_blocked"
    if stage_result.success and terminal_result == writes_status:
        # Preserve prior planning-plane convention (bare node_id).
        # Learning and execution planes keep the explicit terminal_result.
        if stage_result.plane is Plane.PLANNING:
            return stage_result.node_id
    return f"{stage_result.node_id}:{terminal_result}"


def _generic_terminal_failure_class(
    stage_result: StageResultEnvelope,
    source_stage: StageName,
    terminal_result: str,
    terminal_class: GraphLoopTerminalClass,
    failure_class_template: str | None,
) -> str | None:
    """Generic terminal failure class derived from compiled node identity.

    Falls back to ``failure_class_template`` from compiled terminal state,
    then to ``stage_result.node_id`` (compiled node identity) rather than
    ``source_stage.value`` (runtime stage-name string).
    """
    if terminal_class is not GraphLoopTerminalClass.BLOCKED:
        return None
    metadata_failure_class = stage_result.metadata.get("failure_class")
    if isinstance(metadata_failure_class, str) and metadata_failure_class.strip():
        return normalize_failure_class(metadata_failure_class)
    return normalize_failure_class(failure_class_template or f"{stage_result.node_id}_blocked")


def _generic_threshold_failure_class_default(
    stage_result: StageResultEnvelope,
    source_stage: StageName,
    threshold_policy: CompiledGraphThresholdPolicyPlan,
    *,
    exhausted: bool,
) -> str:
    """Generic threshold failure class default derived from compiled node identity.

    Prefers compiled policy templates; falls back to
    ``stage_result.node_id`` (compiled node identity) rather than
    ``source_stage.value`` (runtime stage-name string).
    """
    template = (
        threshold_policy.exhausted_failure_class_template
        if exhausted
        else threshold_policy.default_failure_class_template
    )
    if template is not None:
        return template
    return f"{stage_result.node_id}_{threshold_policy.on_outcome.lower()}"


def _generic_threshold_reason(
    stage_result: StageResultEnvelope,
    source_stage: StageName,
    threshold_policy: CompiledGraphThresholdPolicyPlan,
    *,
    exhausted: bool,
) -> str:
    """Generic threshold reason derived from compiled node identity.

    Prefers compiled policy route reasons; falls back to
    ``stage_result.node_id`` (compiled node identity) rather than
    ``source_stage.value`` (runtime stage-name string).
    """
    policy_reason = (
        threshold_policy.exhausted_route_reason
        if exhausted
        else threshold_policy.route_reason
    )
    if policy_reason is not None:
        return policy_reason
    return f"{stage_result.node_id}_{threshold_policy.on_outcome.lower()}"


__all__ = [
    "route_generic_stage_result_from_graph",
    "route_stage_result_from_graph",
]
