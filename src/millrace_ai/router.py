"""Deterministic stage routing for execution and planning planes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

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

DEFAULT_MAX_FIX_CYCLES = 2
DEFAULT_MAX_TROUBLESHOOT_ATTEMPTS_BEFORE_CONSULT = 2
DEFAULT_MAX_MECHANIC_ATTEMPTS = 2


class RouterAction(str, Enum):
    RUN_STAGE = "run_stage"
    HANDOFF = "handoff"
    IDLE = "idle"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class RouterDecision:
    action: RouterAction
    next_plane: Plane | None
    next_stage: StageName | None
    reason: str
    failure_class: str | None = None
    counter_key: str | None = None
    create_incident: bool = False


_EXECUTION_SUCCESS_TRANSITIONS: dict[ExecutionTerminalResult, ExecutionStageName] = {
    ExecutionTerminalResult.BUILDER_COMPLETE: ExecutionStageName.CHECKER,
    ExecutionTerminalResult.CHECKER_PASS: ExecutionStageName.UPDATER,
    ExecutionTerminalResult.FIXER_COMPLETE: ExecutionStageName.DOUBLECHECKER,
    ExecutionTerminalResult.DOUBLECHECK_PASS: ExecutionStageName.UPDATER,
}


_PLANNING_SUCCESS_TRANSITIONS: dict[PlanningTerminalResult, PlanningStageName] = {
    PlanningTerminalResult.PLANNER_COMPLETE: PlanningStageName.MANAGER,
    PlanningTerminalResult.AUDITOR_COMPLETE: PlanningStageName.PLANNER,
}


def counter_key_for_failure_class(
    *,
    work_item_kind: WorkItemKind | str,
    work_item_id: str,
    failure_class: str,
) -> str:
    kind = WorkItemKind(work_item_kind)
    normalized_id = work_item_id.strip()
    if not normalized_id:
        raise ValueError("work_item_id cannot be empty")

    normalized_failure_class = _normalize_failure_class(failure_class)
    return f"{kind.value}:{normalized_id}:{normalized_failure_class}"


def next_execution_step(
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    counters: RecoveryCounters,
    *,
    max_fix_cycles: int = DEFAULT_MAX_FIX_CYCLES,
    max_troubleshoot_attempts_before_consult: int = (
        DEFAULT_MAX_TROUBLESHOOT_ATTEMPTS_BEFORE_CONSULT
    ),
) -> RouterDecision:
    if stage_result.plane is not Plane.EXECUTION:
        raise ValueError("next_execution_step requires execution stage_result")
    _validate_stage_result_matches_snapshot(snapshot, stage_result, expected_plane=Plane.EXECUTION)
    if max_fix_cycles < 1:
        raise ValueError("max_fix_cycles must be >= 1")
    if max_troubleshoot_attempts_before_consult < 1:
        raise ValueError("max_troubleshoot_attempts_before_consult must be >= 1")

    terminal_result = ExecutionTerminalResult(stage_result.terminal_result)
    source_stage = ExecutionStageName(stage_result.stage)

    if terminal_result in _EXECUTION_SUCCESS_TRANSITIONS:
        return _run_stage(
            _EXECUTION_SUCCESS_TRANSITIONS[terminal_result],
            reason=f"{source_stage.value}:{terminal_result.value}",
        )

    if terminal_result is ExecutionTerminalResult.UPDATE_COMPLETE:
        return _idle(reason="updater_complete")

    if terminal_result is ExecutionTerminalResult.FIX_NEEDED:
        if snapshot.fix_cycle_count < max_fix_cycles:
            return _run_stage(
                ExecutionStageName.FIXER,
                reason="fix_needed",
            )
        return route_execution_recovery(
            snapshot,
            counters,
            failure_class=_resolve_failure_class(
                snapshot,
                stage_result,
                default="fix_cycle_exhausted",
            ),
            max_troubleshoot_attempts_before_consult=max_troubleshoot_attempts_before_consult,
            reason="fix_cycle_exhausted",
        )

    if source_stage is ExecutionStageName.CONSULTANT:
        return build_consultant_escalation(stage_result)

    if terminal_result is ExecutionTerminalResult.TROUBLESHOOT_COMPLETE:
        resume_stage = _resolve_execution_stage_from_metadata(
            stage_result.metadata.get("resume_stage"),
            default=ExecutionStageName.BUILDER,
            disallowed=frozenset({ExecutionStageName.CONSULTANT}),
        )
        return _run_stage(resume_stage, reason="troubleshoot_complete")

    if terminal_result is ExecutionTerminalResult.BLOCKED:
        return route_execution_recovery(
            snapshot,
            counters,
            failure_class=_resolve_failure_class(
                snapshot,
                stage_result,
                default=f"{source_stage.value}_blocked",
            ),
            max_troubleshoot_attempts_before_consult=max_troubleshoot_attempts_before_consult,
            reason=f"{source_stage.value}_blocked",
        )

    raise ValueError(f"Unsupported execution terminal_result: {terminal_result.value}")


def next_planning_step(
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    counters: RecoveryCounters,
    *,
    max_mechanic_attempts: int = DEFAULT_MAX_MECHANIC_ATTEMPTS,
) -> RouterDecision:
    if stage_result.plane is not Plane.PLANNING:
        raise ValueError("next_planning_step requires planning stage_result")
    _validate_stage_result_matches_snapshot(snapshot, stage_result, expected_plane=Plane.PLANNING)
    if max_mechanic_attempts < 1:
        raise ValueError("max_mechanic_attempts must be >= 1")

    terminal_result = PlanningTerminalResult(stage_result.terminal_result)
    source_stage = PlanningStageName(stage_result.stage)

    if terminal_result in _PLANNING_SUCCESS_TRANSITIONS:
        return _run_stage(
            _PLANNING_SUCCESS_TRANSITIONS[terminal_result],
            reason=f"{source_stage.value}:{terminal_result.value}",
        )

    if source_stage is PlanningStageName.ARBITER:
        if terminal_result is PlanningTerminalResult.ARBITER_COMPLETE:
            return RouterDecision(
                action=RouterAction.IDLE,
                next_plane=None,
                next_stage=None,
                reason="arbiter_complete",
            )
        if terminal_result is PlanningTerminalResult.REMEDIATION_NEEDED:
            return RouterDecision(
                action=RouterAction.HANDOFF,
                next_plane=Plane.PLANNING,
                next_stage=PlanningStageName.AUDITOR,
                reason="arbiter_remediation_needed",
                failure_class="arbiter_parity_gap",
                create_incident=True,
            )
        if terminal_result is PlanningTerminalResult.BLOCKED:
            return _blocked(
                reason="arbiter_blocked",
                failure_class=_resolve_failure_class(
                    snapshot,
                    stage_result,
                    default="arbiter_blocked",
                ),
            )

    if terminal_result is PlanningTerminalResult.MANAGER_COMPLETE:
        return _idle(reason="manager_complete")

    if terminal_result is PlanningTerminalResult.MECHANIC_COMPLETE:
        resume_stage = _resolve_planning_stage_from_metadata(
            stage_result.metadata.get("resume_stage"),
            default=PlanningStageName.PLANNER,
            disallowed=frozenset({PlanningStageName.MECHANIC}),
        )
        return _run_stage(resume_stage, reason="mechanic_complete")

    if terminal_result is PlanningTerminalResult.BLOCKED:
        return route_planning_recovery(
            snapshot,
            counters,
            failure_class=_resolve_failure_class(
                snapshot,
                stage_result,
                default=f"{source_stage.value}_blocked",
            ),
            max_mechanic_attempts=max_mechanic_attempts,
            reason=f"{source_stage.value}_blocked",
        )

    raise ValueError(f"Unsupported planning terminal_result: {terminal_result.value}")


def route_execution_recovery(
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    *,
    failure_class: str,
    max_troubleshoot_attempts_before_consult: int = (
        DEFAULT_MAX_TROUBLESHOOT_ATTEMPTS_BEFORE_CONSULT
    ),
    reason: str = "execution_recovery",
) -> RouterDecision:
    if max_troubleshoot_attempts_before_consult < 1:
        raise ValueError("max_troubleshoot_attempts_before_consult must be >= 1")

    counter_entry = _matching_counter_entry(snapshot, counters, failure_class)
    counter_key = _counter_key_from_snapshot(snapshot, failure_class)
    attempts = 0 if counter_entry is None else counter_entry.troubleshoot_attempt_count
    normalized_failure_class = _normalize_failure_class(failure_class)

    if attempts >= max_troubleshoot_attempts_before_consult:
        return _run_stage(
            ExecutionStageName.CONSULTANT,
            reason=reason,
            failure_class=normalized_failure_class,
            counter_key=counter_key,
        )
    return _run_stage(
        ExecutionStageName.TROUBLESHOOTER,
        reason=reason,
        failure_class=normalized_failure_class,
        counter_key=counter_key,
    )


def route_planning_recovery(
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    *,
    failure_class: str,
    max_mechanic_attempts: int = DEFAULT_MAX_MECHANIC_ATTEMPTS,
    reason: str = "planning_recovery",
) -> RouterDecision:
    if max_mechanic_attempts < 1:
        raise ValueError("max_mechanic_attempts must be >= 1")

    counter_entry = _matching_counter_entry(snapshot, counters, failure_class)
    counter_key = _counter_key_from_snapshot(snapshot, failure_class)
    attempts = 0 if counter_entry is None else counter_entry.mechanic_attempt_count
    normalized_failure_class = _normalize_failure_class(failure_class)

    if attempts >= max_mechanic_attempts:
        return _blocked(
            reason=f"{reason}:mechanic_attempts_exhausted",
            failure_class=normalized_failure_class,
            counter_key=counter_key,
        )
    return _run_stage(
        PlanningStageName.MECHANIC,
        reason=reason,
        failure_class=normalized_failure_class,
        counter_key=counter_key,
    )


def build_consultant_escalation(stage_result: StageResultEnvelope) -> RouterDecision:
    if stage_result.stage is not ExecutionStageName.CONSULTANT:
        raise ValueError("build_consultant_escalation requires consultant stage_result")

    terminal_result = ExecutionTerminalResult(stage_result.terminal_result)

    if terminal_result is ExecutionTerminalResult.NEEDS_PLANNING:
        return RouterDecision(
            action=RouterAction.HANDOFF,
            next_plane=Plane.PLANNING,
            next_stage=PlanningStageName.AUDITOR,
            reason="consultant_needs_planning",
            create_incident=True,
        )

    if terminal_result is ExecutionTerminalResult.CONSULT_COMPLETE:
        target_stage = _resolve_execution_stage_from_metadata(
            stage_result.metadata.get("target_stage")
            or stage_result.metadata.get("resume_stage"),
            default=ExecutionStageName.TROUBLESHOOTER,
            disallowed=frozenset({ExecutionStageName.CONSULTANT}),
        )
        return _run_stage(target_stage, reason="consultant_local_recovery")

    if terminal_result is ExecutionTerminalResult.BLOCKED:
        return _blocked(reason="consultant_blocked")

    raise ValueError(f"Unsupported consultant terminal_result: {terminal_result.value}")


def _run_stage(
    stage: StageName,
    *,
    reason: str,
    failure_class: str | None = None,
    counter_key: str | None = None,
) -> RouterDecision:
    plane = _plane_for_stage(stage)
    return RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=plane,
        next_stage=stage,
        reason=reason,
        failure_class=failure_class,
        counter_key=counter_key,
    )


def _idle(*, reason: str) -> RouterDecision:
    return RouterDecision(
        action=RouterAction.IDLE,
        next_plane=None,
        next_stage=None,
        reason=reason,
    )


def _blocked(
    *,
    reason: str,
    failure_class: str | None = None,
    counter_key: str | None = None,
) -> RouterDecision:
    return RouterDecision(
        action=RouterAction.BLOCKED,
        next_plane=None,
        next_stage=None,
        reason=reason,
        failure_class=failure_class,
        counter_key=counter_key,
    )


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


def _resolve_execution_stage_from_metadata(
    raw_stage: object,
    *,
    default: ExecutionStageName,
    disallowed: frozenset[ExecutionStageName] = frozenset(),
) -> ExecutionStageName:
    stage = _parse_execution_stage(raw_stage)
    if stage is None or stage in disallowed:
        return default
    return stage


def _resolve_planning_stage_from_metadata(
    raw_stage: object,
    *,
    default: PlanningStageName,
    disallowed: frozenset[PlanningStageName] = frozenset(),
) -> PlanningStageName:
    stage = _parse_planning_stage(raw_stage)
    if stage is None or stage in disallowed:
        return default
    return stage


def _parse_execution_stage(raw_stage: object) -> ExecutionStageName | None:
    if isinstance(raw_stage, ExecutionStageName):
        return raw_stage
    if isinstance(raw_stage, str):
        candidate = raw_stage.strip().lower()
        if not candidate:
            return None
        try:
            return ExecutionStageName(candidate)
        except ValueError:
            return None
    return None


def _parse_planning_stage(raw_stage: object) -> PlanningStageName | None:
    if isinstance(raw_stage, PlanningStageName):
        return raw_stage
    if isinstance(raw_stage, str):
        candidate = raw_stage.strip().lower()
        if not candidate:
            return None
        try:
            return PlanningStageName(candidate)
        except ValueError:
            return None
    return None


def _normalize_failure_class(failure_class: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", failure_class.strip().lower()).strip("_")
    if not normalized:
        raise ValueError("failure_class cannot be empty")
    return normalized


def _plane_for_stage(stage: StageName) -> Plane:
    if isinstance(stage, ExecutionStageName):
        return Plane.EXECUTION
    return Plane.PLANNING


__all__ = [
    "DEFAULT_MAX_FIX_CYCLES",
    "DEFAULT_MAX_MECHANIC_ATTEMPTS",
    "DEFAULT_MAX_TROUBLESHOOT_ATTEMPTS_BEFORE_CONSULT",
    "RouterAction",
    "RouterDecision",
    "build_consultant_escalation",
    "counter_key_for_failure_class",
    "next_execution_step",
    "next_planning_step",
    "route_execution_recovery",
    "route_planning_recovery",
]
