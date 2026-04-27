"""Execution-plane compiled graph routing."""

from __future__ import annotations

from millrace_ai.architecture import CompiledGraphTransitionPlan, FrozenGraphPlanePlan
from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    Plane,
    PlanningStageName,
    RecoveryCounters,
    RuntimeSnapshot,
    StageResultEnvelope,
)
from millrace_ai.router import RouterAction, RouterDecision

from .counters import counter_attempts, counter_key_from_snapshot, resolve_failure_class
from .policies import (
    decision_from_resume_policy,
    decision_from_threshold_resolution,
    resume_policy_for_source,
    terminal_state_by_id,
    threshold_policy_for_source,
    transition_for_source,
)
from .stage_mapping import node_plan_by_id, stage_for_node
from .validation import validate_stage_result_matches_snapshot


def route_execution_stage_result_from_graph(
    graph: FrozenGraphPlanePlan,
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    counters: RecoveryCounters,
    *,
    max_fix_cycles: int,
    max_troubleshoot_attempts_before_consult: int,
) -> RouterDecision:
    validate_stage_result_matches_snapshot(snapshot, stage_result, expected_plane=Plane.EXECUTION)
    source_stage = ExecutionStageName(stage_result.stage_kind_id)
    outcome = ExecutionTerminalResult(stage_result.terminal_result)
    source_node_id = stage_result.node_id

    if outcome is ExecutionTerminalResult.FIX_NEEDED and snapshot.fix_cycle_count >= max_fix_cycles:
        policy = threshold_policy_for_source(
            graph,
            source_node_id=source_node_id,
            outcome=outcome.value,
            counter_name="fix_cycle_count",
            threshold=max_fix_cycles,
        )
        failure_class = resolve_failure_class(snapshot, stage_result, default="fix_cycle_exhausted")
        return decision_from_threshold_resolution(
            graph,
            snapshot,
            source_stage=source_stage,
            policy=policy,
            failure_class=failure_class,
            reason="fix_cycle_exhausted",
        )

    if outcome is ExecutionTerminalResult.BLOCKED and source_stage is not ExecutionStageName.CONSULTANT:
        failure_class = resolve_failure_class(
            snapshot,
            stage_result,
            default=f"{source_stage.value}_blocked",
        )
        attempts = counter_attempts(snapshot, counters, failure_class, plane=Plane.EXECUTION)
        if attempts >= max_troubleshoot_attempts_before_consult:
            policy = threshold_policy_for_source(
                graph,
                source_node_id=source_node_id,
                outcome=outcome.value,
                counter_name="troubleshoot_attempt_count",
                threshold=max_troubleshoot_attempts_before_consult,
            )
            return decision_from_threshold_resolution(
                graph,
                snapshot,
                source_stage=source_stage,
                policy=policy,
                failure_class=failure_class,
                reason=f"{source_stage.value}_blocked",
            )

    resume_policy = resume_policy_for_source(
        graph,
        source_node_id=source_node_id,
        outcome=outcome.value,
    )
    if resume_policy is not None:
        return decision_from_resume_policy(
            graph,
            source_stage=source_stage,
            stage_result=stage_result,
            policy=resume_policy,
        )

    transition = transition_for_source(graph, source_node_id=source_node_id, outcome=outcome.value)
    return decision_from_execution_transition(
        graph,
        snapshot,
        source_stage=source_stage,
        stage_result=stage_result,
        transition=transition,
    )


def decision_from_execution_transition(
    graph: FrozenGraphPlanePlan,
    snapshot: RuntimeSnapshot,
    *,
    source_stage: ExecutionStageName,
    stage_result: StageResultEnvelope,
    transition: CompiledGraphTransitionPlan,
) -> RouterDecision:
    terminal_result = ExecutionTerminalResult(stage_result.terminal_result)

    if transition.target_node_id is not None:
        if terminal_result is ExecutionTerminalResult.FIX_NEEDED:
            return RouterDecision(
                action=RouterAction.RUN_STAGE,
                next_plane=graph.plane,
                next_stage=stage_for_node(graph, transition.target_node_id),
                next_node_id=transition.target_node_id,
                next_stage_kind_id=node_plan_by_id(graph, transition.target_node_id).stage_kind_id,
                reason="fix_needed",
            )
        if terminal_result is ExecutionTerminalResult.BLOCKED:
            failure_class = resolve_failure_class(
                snapshot,
                stage_result,
                default=f"{source_stage.value}_blocked",
            )
            return RouterDecision(
                action=RouterAction.RUN_STAGE,
                next_plane=graph.plane,
                next_stage=stage_for_node(graph, transition.target_node_id),
                next_node_id=transition.target_node_id,
                next_stage_kind_id=node_plan_by_id(graph, transition.target_node_id).stage_kind_id,
                reason=f"{source_stage.value}_blocked",
                failure_class=failure_class,
                counter_key=counter_key_from_snapshot(snapshot, failure_class),
            )
        return RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=graph.plane,
            next_stage=stage_for_node(graph, transition.target_node_id),
            next_node_id=transition.target_node_id,
            next_stage_kind_id=node_plan_by_id(graph, transition.target_node_id).stage_kind_id,
            reason=f"{source_stage.value}:{terminal_result.value}",
        )

    terminal_state_id = transition.terminal_state_id
    assert terminal_state_id is not None
    terminal_state = terminal_state_by_id(graph, terminal_state_id)

    if source_stage is ExecutionStageName.UPDATER and terminal_result is ExecutionTerminalResult.UPDATE_COMPLETE:
        return RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="updater_complete",
        )
    if source_stage is ExecutionStageName.CONSULTANT and terminal_result is ExecutionTerminalResult.NEEDS_PLANNING:
        return RouterDecision(
            action=RouterAction.HANDOFF,
            next_plane=Plane.PLANNING,
            next_stage=PlanningStageName.AUDITOR,
            reason="consultant_needs_planning",
            create_incident=True,
        )
    if source_stage is ExecutionStageName.CONSULTANT and terminal_result is ExecutionTerminalResult.BLOCKED:
        return RouterDecision(
            action=RouterAction.BLOCKED,
            next_plane=None,
            next_stage=None,
            reason="consultant_blocked",
        )
    raise ValueError(
        f"unsupported execution terminal transition for {source_stage.value}:{terminal_state.terminal_state_id}"
    )


__all__ = [
    "decision_from_execution_transition",
    "route_execution_stage_result_from_graph",
]
