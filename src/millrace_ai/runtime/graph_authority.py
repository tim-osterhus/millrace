"""Runtime-owned compiled-graph activation and routing helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from millrace_ai.architecture import (
    CompiledGraphCompletionEntryPlan,
    CompiledGraphResumePolicyPlan,
    CompiledGraphThresholdPolicyPlan,
    CompiledGraphTransitionPlan,
    CompiledRunPlan,
    FrozenGraphPlanePlan,
    GraphLoopTerminalStateDefinition,
)
from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    RecoveryCounterEntry,
    RecoveryCounters,
    RuntimeSnapshot,
    StageName,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.router import RouterAction, RouterDecision, counter_key_for_failure_class


@dataclass(frozen=True, slots=True)
class GraphActivationDecision:
    plane: Plane
    stage: StageName
    node_id: str
    stage_kind_id: str
    entry_key: str


def work_item_activation_for_graph(
    graph_plan: CompiledRunPlan,
    work_item_kind: WorkItemKind,
) -> GraphActivationDecision:
    entry_key = _entry_key_for_work_item_kind(work_item_kind)
    graph = graph_plan.execution_graph if work_item_kind is WorkItemKind.TASK else graph_plan.planning_graph
    return _activation_from_entry(graph, entry_key)


def completion_activation_for_graph(graph_plan: CompiledRunPlan) -> GraphActivationDecision:
    completion_entry = graph_plan.planning_graph.compiled_completion_entry
    if completion_entry is None:
        raise ValueError("compiled graph is missing closure_target completion entry")
    return _activation_from_completion_entry(graph_plan.planning_graph, completion_entry)


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
        return _route_execution_stage_result_from_graph(
            graph_plan.execution_graph,
            snapshot,
            stage_result,
            counters,
            max_fix_cycles=max_fix_cycles,
            max_troubleshoot_attempts_before_consult=max_troubleshoot_attempts_before_consult,
        )
    if max_mechanic_attempts < 1:
        raise ValueError("max_mechanic_attempts must be >= 1")
    return _route_planning_stage_result_from_graph(
        graph_plan.planning_graph,
        snapshot,
        stage_result,
        counters,
        max_mechanic_attempts=max_mechanic_attempts,
    )


def _activation_from_entry(
    graph: FrozenGraphPlanePlan,
    entry_key: str,
) -> GraphActivationDecision:
    for entry in graph.compiled_entries:
        if entry.entry_key.value == entry_key:
            return GraphActivationDecision(
                plane=graph.plane,
                stage=_stage_for_node(graph.plane, entry.node_id),
                node_id=entry.node_id,
                stage_kind_id=entry.stage_kind_id,
                entry_key=entry.entry_key.value,
            )
    raise ValueError(f"compiled graph is missing `{entry_key}` activation entry")


def _activation_from_completion_entry(
    graph: FrozenGraphPlanePlan,
    completion_entry: CompiledGraphCompletionEntryPlan,
) -> GraphActivationDecision:
    return GraphActivationDecision(
        plane=graph.plane,
        stage=_stage_for_node(graph.plane, completion_entry.node_id),
        node_id=completion_entry.node_id,
        stage_kind_id=completion_entry.stage_kind_id,
        entry_key=completion_entry.entry_key.value,
    )


def _entry_key_for_work_item_kind(work_item_kind: WorkItemKind) -> str:
    if work_item_kind is WorkItemKind.TASK:
        return "task"
    if work_item_kind is WorkItemKind.SPEC:
        return "spec"
    if work_item_kind is WorkItemKind.INCIDENT:
        return "incident"
    raise ValueError(f"unsupported work_item_kind: {work_item_kind}")


def _route_execution_stage_result_from_graph(
    graph: FrozenGraphPlanePlan,
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    counters: RecoveryCounters,
    *,
    max_fix_cycles: int,
    max_troubleshoot_attempts_before_consult: int,
) -> RouterDecision:
    _validate_stage_result_matches_snapshot(snapshot, stage_result, expected_plane=Plane.EXECUTION)
    source_stage = ExecutionStageName(stage_result.stage)
    outcome = ExecutionTerminalResult(stage_result.terminal_result)
    source_node_id = source_stage.value

    if outcome is ExecutionTerminalResult.FIX_NEEDED and snapshot.fix_cycle_count >= max_fix_cycles:
        policy = _threshold_policy_for_source(
            graph,
            source_node_id=source_node_id,
            outcome=outcome.value,
            counter_name="fix_cycle_count",
            threshold=max_fix_cycles,
        )
        failure_class = _resolve_failure_class(snapshot, stage_result, default="fix_cycle_exhausted")
        return _decision_from_threshold_resolution(
            graph,
            snapshot,
            source_stage=source_stage,
            stage_result=stage_result,
            policy=policy,
            failure_class=failure_class,
            reason="fix_cycle_exhausted",
        )

    if outcome is ExecutionTerminalResult.BLOCKED and source_stage is not ExecutionStageName.CONSULTANT:
        failure_class = _resolve_failure_class(
            snapshot,
            stage_result,
            default=f"{source_stage.value}_blocked",
        )
        attempts = _counter_attempts(snapshot, counters, failure_class, plane=Plane.EXECUTION)
        if attempts >= max_troubleshoot_attempts_before_consult:
            policy = _threshold_policy_for_source(
                graph,
                source_node_id=source_node_id,
                outcome=outcome.value,
                counter_name="troubleshoot_attempt_count",
                threshold=max_troubleshoot_attempts_before_consult,
            )
            return _decision_from_threshold_resolution(
                graph,
                snapshot,
                source_stage=source_stage,
                stage_result=stage_result,
                policy=policy,
                failure_class=failure_class,
                reason=f"{source_stage.value}_blocked",
            )

    resume_policy = _resume_policy_for_source(
        graph,
        source_node_id=source_node_id,
        outcome=outcome.value,
    )
    if resume_policy is not None:
        return _decision_from_resume_policy(
            graph,
            source_stage=source_stage,
            stage_result=stage_result,
            policy=resume_policy,
        )

    transition = _transition_for_source(graph, source_node_id=source_node_id, outcome=outcome.value)
    return _decision_from_execution_transition(
        graph,
        snapshot,
        source_stage=source_stage,
        stage_result=stage_result,
        transition=transition,
        counters=counters,
    )


def _route_planning_stage_result_from_graph(
    graph: FrozenGraphPlanePlan,
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    counters: RecoveryCounters,
    *,
    max_mechanic_attempts: int,
) -> RouterDecision:
    _validate_stage_result_matches_snapshot(snapshot, stage_result, expected_plane=Plane.PLANNING)
    source_stage = PlanningStageName(stage_result.stage)
    outcome = PlanningTerminalResult(stage_result.terminal_result)
    source_node_id = source_stage.value

    if outcome is PlanningTerminalResult.BLOCKED:
        failure_class = _resolve_failure_class(
            snapshot,
            stage_result,
            default=f"{source_stage.value}_blocked",
        )
        attempts = _counter_attempts(snapshot, counters, failure_class, plane=Plane.PLANNING)
        if attempts >= max_mechanic_attempts:
            policy = _threshold_policy_for_source(
                graph,
                source_node_id=source_node_id,
                outcome=outcome.value,
                counter_name="mechanic_attempt_count",
                threshold=max_mechanic_attempts,
            )
            return _decision_from_threshold_resolution(
                graph,
                snapshot,
                source_stage=source_stage,
                stage_result=stage_result,
                policy=policy,
                failure_class=failure_class,
                reason=f"{source_stage.value}_blocked",
            )

    resume_policy = _resume_policy_for_source(
        graph,
        source_node_id=source_node_id,
        outcome=outcome.value,
    )
    if resume_policy is not None:
        return _decision_from_resume_policy(
            graph,
            source_stage=source_stage,
            stage_result=stage_result,
            policy=resume_policy,
        )

    transition = _transition_for_source(graph, source_node_id=source_node_id, outcome=outcome.value)
    return _decision_from_planning_transition(
        graph,
        snapshot,
        source_stage=source_stage,
        stage_result=stage_result,
        transition=transition,
        counters=counters,
    )


def _decision_from_resume_policy(
    graph: FrozenGraphPlanePlan,
    *,
    source_stage: ExecutionStageName | PlanningStageName,
    stage_result: StageResultEnvelope,
    policy: CompiledGraphResumePolicyPlan,
) -> RouterDecision:
    target_node_id = policy.default_target_node_id
    valid_node_ids = {node.node_id for node in graph.nodes}
    for metadata_key in policy.metadata_stage_keys:
        candidate = stage_result.metadata.get(metadata_key)
        if not isinstance(candidate, str):
            continue
        normalized = candidate.strip().lower()
        if not normalized or normalized in policy.disallowed_target_node_ids:
            continue
        if normalized not in valid_node_ids:
            continue
        target_node_id = normalized
        break

    if source_stage is ExecutionStageName.TROUBLESHOOTER:
        return RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=graph.plane,
            next_stage=_stage_for_node(graph.plane, target_node_id),
            reason="troubleshoot_complete",
        )
    if source_stage is ExecutionStageName.CONSULTANT:
        return RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=graph.plane,
            next_stage=_stage_for_node(graph.plane, target_node_id),
            reason="consultant_local_recovery",
        )
    if source_stage is PlanningStageName.MECHANIC:
        return RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=graph.plane,
            next_stage=_stage_for_node(graph.plane, target_node_id),
            reason="mechanic_complete",
        )
    raise ValueError(f"unsupported resume-policy source stage: {source_stage.value}")


def _decision_from_execution_transition(
    graph: FrozenGraphPlanePlan,
    snapshot: RuntimeSnapshot,
    *,
    source_stage: ExecutionStageName,
    stage_result: StageResultEnvelope,
    transition: CompiledGraphTransitionPlan,
    counters: RecoveryCounters,
) -> RouterDecision:
    terminal_result = ExecutionTerminalResult(stage_result.terminal_result)

    if transition.target_node_id is not None:
        if terminal_result is ExecutionTerminalResult.FIX_NEEDED:
            return RouterDecision(
                action=RouterAction.RUN_STAGE,
                next_plane=graph.plane,
                next_stage=_stage_for_node(graph.plane, transition.target_node_id),
                reason="fix_needed",
            )
        if terminal_result is ExecutionTerminalResult.BLOCKED:
            failure_class = _resolve_failure_class(
                snapshot,
                stage_result,
                default=f"{source_stage.value}_blocked",
            )
            return RouterDecision(
                action=RouterAction.RUN_STAGE,
                next_plane=graph.plane,
                next_stage=_stage_for_node(graph.plane, transition.target_node_id),
                reason=f"{source_stage.value}_blocked",
                failure_class=failure_class,
                counter_key=_counter_key_from_snapshot(snapshot, failure_class),
            )
        return RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=graph.plane,
            next_stage=_stage_for_node(graph.plane, transition.target_node_id),
            reason=f"{source_stage.value}:{terminal_result.value}",
        )

    terminal_state_id = transition.terminal_state_id
    assert terminal_state_id is not None
    terminal_state = _terminal_state_by_id(graph, terminal_state_id)

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


def _decision_from_planning_transition(
    graph: FrozenGraphPlanePlan,
    snapshot: RuntimeSnapshot,
    *,
    source_stage: PlanningStageName,
    stage_result: StageResultEnvelope,
    transition: CompiledGraphTransitionPlan,
    counters: RecoveryCounters,
) -> RouterDecision:
    terminal_result = PlanningTerminalResult(stage_result.terminal_result)

    if transition.target_node_id is not None:
        if terminal_result is PlanningTerminalResult.BLOCKED:
            failure_class = _resolve_failure_class(
                snapshot,
                stage_result,
                default=f"{source_stage.value}_blocked",
            )
            return RouterDecision(
                action=RouterAction.RUN_STAGE,
                next_plane=graph.plane,
                next_stage=_stage_for_node(graph.plane, transition.target_node_id),
                reason=f"{source_stage.value}_blocked",
                failure_class=failure_class,
                counter_key=_counter_key_from_snapshot(snapshot, failure_class),
            )
        return RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=graph.plane,
            next_stage=_stage_for_node(graph.plane, transition.target_node_id),
            reason=f"{source_stage.value}:{terminal_result.value}",
        )

    terminal_state_id = transition.terminal_state_id
    assert terminal_state_id is not None
    terminal_state = _terminal_state_by_id(graph, terminal_state_id)

    if source_stage is PlanningStageName.MANAGER and terminal_result is PlanningTerminalResult.MANAGER_COMPLETE:
        return RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="manager_complete",
        )
    if source_stage is PlanningStageName.ARBITER and terminal_result is PlanningTerminalResult.ARBITER_COMPLETE:
        return RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="arbiter_complete",
        )
    if source_stage is PlanningStageName.ARBITER and terminal_result is PlanningTerminalResult.REMEDIATION_NEEDED:
        return RouterDecision(
            action=RouterAction.HANDOFF,
            next_plane=Plane.PLANNING,
            next_stage=PlanningStageName.AUDITOR,
            reason="arbiter_remediation_needed",
            failure_class="arbiter_parity_gap",
            create_incident=True,
        )
    if source_stage is PlanningStageName.ARBITER and terminal_result is PlanningTerminalResult.BLOCKED:
        failure_class = _resolve_failure_class(
            snapshot,
            stage_result,
            default="arbiter_blocked",
        )
        return RouterDecision(
            action=RouterAction.BLOCKED,
            next_plane=None,
            next_stage=None,
            reason="arbiter_blocked",
            failure_class=failure_class,
        )
    raise ValueError(
        f"unsupported planning terminal transition for {source_stage.value}:{terminal_state.terminal_state_id}"
    )


def _decision_from_threshold_resolution(
    graph: FrozenGraphPlanePlan,
    snapshot: RuntimeSnapshot,
    *,
    source_stage: ExecutionStageName | PlanningStageName,
    stage_result: StageResultEnvelope,
    policy: CompiledGraphThresholdPolicyPlan,
    failure_class: str,
    reason: str,
) -> RouterDecision:
    counter_key = _counter_key_from_snapshot(snapshot, failure_class)
    if policy.exhausted_target_node_id is not None:
        return RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=graph.plane,
            next_stage=_stage_for_node(graph.plane, policy.exhausted_target_node_id),
            reason=reason,
            failure_class=failure_class,
            counter_key=counter_key,
        )

    assert policy.exhausted_terminal_state_id is not None
    terminal_state = _terminal_state_by_id(graph, policy.exhausted_terminal_state_id)
    if terminal_state.terminal_class.value != "blocked":
        raise ValueError(
            f"unsupported threshold terminal class for {source_stage.value}:{terminal_state.terminal_class.value}"
        )
    return RouterDecision(
        action=RouterAction.BLOCKED,
        next_plane=None,
        next_stage=None,
        reason=f"{reason}:mechanic_attempts_exhausted",
        failure_class=failure_class,
        counter_key=counter_key,
    )


def _transition_for_source(
    graph: FrozenGraphPlanePlan,
    *,
    source_node_id: str,
    outcome: str,
) -> CompiledGraphTransitionPlan:
    for transition in graph.compiled_transitions:
        if transition.source_node_id == source_node_id and transition.outcome == outcome:
            return transition
    raise ValueError(f"compiled graph is missing transition for {source_node_id}:{outcome}")


def _resume_policy_for_source(
    graph: FrozenGraphPlanePlan,
    *,
    source_node_id: str,
    outcome: str,
) -> CompiledGraphResumePolicyPlan | None:
    for policy in graph.compiled_resume_policies:
        if policy.source_node_id == source_node_id and policy.on_outcome == outcome:
            return policy
    return None


def _threshold_policy_for_source(
    graph: FrozenGraphPlanePlan,
    *,
    source_node_id: str,
    outcome: str,
    counter_name: str,
    threshold: int,
) -> CompiledGraphThresholdPolicyPlan:
    for policy in graph.compiled_threshold_policies:
        if (
            source_node_id in policy.source_node_ids
            and policy.on_outcome == outcome
            and policy.counter_name.value == counter_name
            and policy.threshold == threshold
        ):
            return policy
    raise ValueError(
        "compiled graph is missing threshold policy for "
        f"{source_node_id}:{outcome}:{counter_name}:{threshold}"
    )


def _terminal_state_by_id(
    graph: FrozenGraphPlanePlan,
    terminal_state_id: str,
) -> GraphLoopTerminalStateDefinition:
    for terminal_state in graph.terminal_states:
        if terminal_state.terminal_state_id == terminal_state_id:
            return terminal_state
    raise ValueError(f"compiled graph is missing terminal state `{terminal_state_id}`")


def _stage_for_node(plane: Plane, node_id: str) -> StageName:
    if plane is Plane.EXECUTION:
        return ExecutionStageName(node_id)
    return PlanningStageName(node_id)


def _validate_stage_result_matches_snapshot(
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    *,
    expected_plane: Plane,
) -> None:
    if snapshot.active_plane is not expected_plane:
        raise ValueError("runtime snapshot active_plane does not match router plane")
    if snapshot.active_stage is None or snapshot.active_stage != stage_result.stage:
        raise ValueError("stage_result stage does not match runtime snapshot active_stage")
    if snapshot.active_run_id is None or snapshot.active_run_id != stage_result.run_id:
        raise ValueError("stage_result run_id does not match runtime snapshot active_run_id")
    if stage_result.metadata.get("request_kind") == "closure_target":
        if snapshot.active_work_item_kind is not None or snapshot.active_work_item_id is not None:
            raise ValueError("closure_target stage_result cannot use active work item snapshot identity")
        if stage_result.work_item_kind is not WorkItemKind.SPEC:
            raise ValueError("closure_target stage_result must normalize onto a spec identity")
        closure_target_root_spec_id = stage_result.metadata.get("closure_target_root_spec_id")
        if not isinstance(closure_target_root_spec_id, str) or not closure_target_root_spec_id:
            raise ValueError("closure_target stage_result requires closure_target_root_spec_id metadata")
        if closure_target_root_spec_id != stage_result.work_item_id:
            raise ValueError("closure_target_root_spec_id must match stage_result work_item_id")
        return
    if snapshot.active_work_item_kind != stage_result.work_item_kind:
        raise ValueError("stage_result work_item_kind does not match runtime snapshot active item")
    if snapshot.active_work_item_id != stage_result.work_item_id:
        raise ValueError("stage_result work_item_id does not match runtime snapshot active item")


def _counter_attempts(
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    failure_class: str,
    *,
    plane: Plane,
) -> int:
    entry = _matching_counter_entry(snapshot, counters, failure_class)
    if entry is None:
        return 0
    if plane is Plane.EXECUTION:
        return entry.troubleshoot_attempt_count
    return entry.mechanic_attempt_count


def _matching_counter_entry(
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    failure_class: str,
) -> RecoveryCounterEntry | None:
    if snapshot.active_work_item_kind is None or snapshot.active_work_item_id is None:
        return None

    normalized_failure_class = _normalize_failure_class(failure_class)
    for entry in counters.entries:
        if entry.work_item_kind is not snapshot.active_work_item_kind:
            continue
        if entry.work_item_id != snapshot.active_work_item_id:
            continue
        if _normalize_failure_class(entry.failure_class) != normalized_failure_class:
            continue
        return entry
    return None


def _counter_key_from_snapshot(snapshot: RuntimeSnapshot, failure_class: str) -> str | None:
    if snapshot.active_work_item_kind is None or snapshot.active_work_item_id is None:
        return None
    return counter_key_for_failure_class(
        work_item_kind=snapshot.active_work_item_kind,
        work_item_id=snapshot.active_work_item_id,
        failure_class=failure_class,
    )


def _resolve_failure_class(
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    *,
    default: str,
) -> str:
    metadata_failure_class = stage_result.metadata.get("failure_class")
    if isinstance(metadata_failure_class, str) and metadata_failure_class.strip():
        return _normalize_failure_class(metadata_failure_class)
    if snapshot.current_failure_class is not None and snapshot.current_failure_class.strip():
        return _normalize_failure_class(snapshot.current_failure_class)
    return _normalize_failure_class(default)


def _normalize_failure_class(failure_class: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", failure_class.strip().lower()).strip("_")
    if not normalized:
        raise ValueError("failure_class cannot be empty")
    return normalized


__all__ = [
    "GraphActivationDecision",
    "completion_activation_for_graph",
    "route_stage_result_from_graph",
    "work_item_activation_for_graph",
]
