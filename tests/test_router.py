from __future__ import annotations

from datetime import datetime, timezone

import pytest

from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    RecoveryCounters,
    ResultClass,
    RuntimeSnapshot,
    StageName,
    StageResultEnvelope,
    TerminalResult,
    WorkItemKind,
)
from millrace_ai.router import (
    RouterAction,
    counter_key_for_failure_class,
    next_execution_step,
    next_planning_step,
)

NOW = datetime(2026, 4, 15, tzinfo=timezone.utc)


def _snapshot(
    *,
    plane: Plane,
    stage: StageName,
    work_item_kind: WorkItemKind = WorkItemKind.TASK,
    work_item_id: str = "task-001",
    fix_cycle_count: int = 0,
    current_failure_class: str | None = None,
) -> RuntimeSnapshot:
    return RuntimeSnapshot(
        runtime_mode="daemon",
        process_running=True,
        paused=False,
        active_mode_id="standard_plain",
        execution_loop_id="execution.standard",
        planning_loop_id="planning.standard",
        compiled_plan_id="plan-001",
        compiled_plan_path="state/compiled_plan.json",
        active_plane=plane,
        active_stage=stage,
        active_run_id="run-001",
        active_work_item_kind=work_item_kind,
        active_work_item_id=work_item_id,
        execution_status_marker="### IDLE",
        planning_status_marker="### IDLE",
        fix_cycle_count=fix_cycle_count,
        current_failure_class=current_failure_class,
        config_version="cfg-001",
        watcher_mode="off",
        updated_at=NOW,
    )


def _result_class_for_terminal(terminal_result: TerminalResult) -> ResultClass:
    if terminal_result in {
        ExecutionTerminalResult.FIX_NEEDED,
    }:
        return ResultClass.FOLLOWUP_NEEDED
    if terminal_result is ExecutionTerminalResult.NEEDS_PLANNING:
        return ResultClass.ESCALATE_PLANNING
    if terminal_result in {
        ExecutionTerminalResult.BLOCKED,
        PlanningTerminalResult.BLOCKED,
    }:
        return ResultClass.BLOCKED
    return ResultClass.SUCCESS


def _stage_result(
    *,
    stage: StageName,
    terminal_result: TerminalResult,
    work_item_kind: WorkItemKind = WorkItemKind.TASK,
    work_item_id: str = "task-001",
    metadata: dict[str, object] | None = None,
) -> StageResultEnvelope:
    plane = (
        Plane.EXECUTION
        if isinstance(stage, ExecutionStageName)
        else Plane.PLANNING
    )
    result_class = _result_class_for_terminal(terminal_result)
    return StageResultEnvelope(
        run_id="run-001",
        plane=plane,
        stage=stage,
        work_item_kind=work_item_kind,
        work_item_id=work_item_id,
        terminal_result=terminal_result,
        result_class=result_class,
        summary_status_marker=f"### {terminal_result.value}",
        success=result_class is ResultClass.SUCCESS,
        metadata=metadata or {},
        started_at=NOW,
        completed_at=NOW,
    )


def _counters(
    *,
    failure_class: str,
    work_item_kind: WorkItemKind,
    work_item_id: str,
    troubleshoot_attempt_count: int = 0,
    mechanic_attempt_count: int = 0,
) -> RecoveryCounters:
    return RecoveryCounters(
        entries=[
            {
                "failure_class": failure_class,
                "work_item_kind": work_item_kind,
                "work_item_id": work_item_id,
                "troubleshoot_attempt_count": troubleshoot_attempt_count,
                "mechanic_attempt_count": mechanic_attempt_count,
                "last_updated_at": NOW,
            }
        ]
    )


def test_execution_transition_table_happy_and_repair_paths() -> None:
    builder = next_execution_step(
        _snapshot(plane=Plane.EXECUTION, stage=ExecutionStageName.BUILDER),
        _stage_result(
            stage=ExecutionStageName.BUILDER,
            terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
        ),
        RecoveryCounters(),
    )
    assert builder.action is RouterAction.RUN_STAGE
    assert builder.next_stage is ExecutionStageName.CHECKER

    checker = next_execution_step(
        _snapshot(plane=Plane.EXECUTION, stage=ExecutionStageName.CHECKER),
        _stage_result(
            stage=ExecutionStageName.CHECKER,
            terminal_result=ExecutionTerminalResult.CHECKER_PASS,
        ),
        RecoveryCounters(),
    )
    assert checker.action is RouterAction.RUN_STAGE
    assert checker.next_stage is ExecutionStageName.UPDATER

    fixer = next_execution_step(
        _snapshot(plane=Plane.EXECUTION, stage=ExecutionStageName.FIXER),
        _stage_result(
            stage=ExecutionStageName.FIXER,
            terminal_result=ExecutionTerminalResult.FIXER_COMPLETE,
        ),
        RecoveryCounters(),
    )
    assert fixer.action is RouterAction.RUN_STAGE
    assert fixer.next_stage is ExecutionStageName.DOUBLECHECKER

    doublecheck = next_execution_step(
        _snapshot(plane=Plane.EXECUTION, stage=ExecutionStageName.DOUBLECHECKER),
        _stage_result(
            stage=ExecutionStageName.DOUBLECHECKER,
            terminal_result=ExecutionTerminalResult.DOUBLECHECK_PASS,
        ),
        RecoveryCounters(),
    )
    assert doublecheck.action is RouterAction.RUN_STAGE
    assert doublecheck.next_stage is ExecutionStageName.UPDATER

    updater = next_execution_step(
        _snapshot(plane=Plane.EXECUTION, stage=ExecutionStageName.UPDATER),
        _stage_result(
            stage=ExecutionStageName.UPDATER,
            terminal_result=ExecutionTerminalResult.UPDATE_COMPLETE,
        ),
        RecoveryCounters(),
    )
    assert updater.action is RouterAction.IDLE
    assert updater.next_stage is None


def test_fix_needed_obeys_max_fix_cycle_limit() -> None:
    under_limit = next_execution_step(
        _snapshot(
            plane=Plane.EXECUTION,
            stage=ExecutionStageName.CHECKER,
            fix_cycle_count=1,
        ),
        _stage_result(
            stage=ExecutionStageName.CHECKER,
            terminal_result=ExecutionTerminalResult.FIX_NEEDED,
        ),
        RecoveryCounters(),
        max_fix_cycles=2,
    )
    assert under_limit.action is RouterAction.RUN_STAGE
    assert under_limit.next_stage is ExecutionStageName.FIXER

    exhausted = next_execution_step(
        _snapshot(
            plane=Plane.EXECUTION,
            stage=ExecutionStageName.CHECKER,
            fix_cycle_count=2,
        ),
        _stage_result(
            stage=ExecutionStageName.CHECKER,
            terminal_result=ExecutionTerminalResult.FIX_NEEDED,
            metadata={"failure_class": "fix_cycle_exhausted"},
        ),
        RecoveryCounters(),
        max_fix_cycles=2,
    )
    assert exhausted.action is RouterAction.RUN_STAGE
    assert exhausted.next_stage is ExecutionStageName.TROUBLESHOOTER


def test_troubleshooter_blocked_escalates_to_consultant_for_same_failure_class() -> None:
    snapshot = _snapshot(
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.TROUBLESHOOTER,
        current_failure_class="missing_terminal_result",
    )
    result = _stage_result(
        stage=ExecutionStageName.TROUBLESHOOTER,
        terminal_result=ExecutionTerminalResult.BLOCKED,
        metadata={"failure_class": "missing_terminal_result"},
    )
    counters = _counters(
        failure_class="missing_terminal_result",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        troubleshoot_attempt_count=2,
    )

    decision = next_execution_step(
        snapshot,
        result,
        counters,
        max_troubleshoot_attempts_before_consult=2,
    )

    assert decision.action is RouterAction.RUN_STAGE
    assert decision.next_stage is ExecutionStageName.CONSULTANT


def test_troubleshooter_blocked_does_not_escalate_for_different_failure_class() -> None:
    snapshot = _snapshot(
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.TROUBLESHOOTER,
        current_failure_class="missing_terminal_result",
    )
    result = _stage_result(
        stage=ExecutionStageName.TROUBLESHOOTER,
        terminal_result=ExecutionTerminalResult.BLOCKED,
        metadata={"failure_class": "missing_terminal_result"},
    )
    counters = _counters(
        failure_class="transport_failure",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        troubleshoot_attempt_count=99,
    )

    decision = next_execution_step(
        snapshot,
        result,
        counters,
        max_troubleshoot_attempts_before_consult=2,
    )

    assert decision.action is RouterAction.RUN_STAGE
    assert decision.next_stage is ExecutionStageName.TROUBLESHOOTER


def test_consultant_transitions_cover_local_and_planning_escalation() -> None:
    planning_handoff = next_execution_step(
        _snapshot(plane=Plane.EXECUTION, stage=ExecutionStageName.CONSULTANT),
        _stage_result(
            stage=ExecutionStageName.CONSULTANT,
            terminal_result=ExecutionTerminalResult.NEEDS_PLANNING,
        ),
        RecoveryCounters(),
    )
    assert planning_handoff.action is RouterAction.HANDOFF
    assert planning_handoff.next_plane is Plane.PLANNING
    assert planning_handoff.next_stage is PlanningStageName.AUDITOR

    local_resume = next_execution_step(
        _snapshot(plane=Plane.EXECUTION, stage=ExecutionStageName.CONSULTANT),
        _stage_result(
            stage=ExecutionStageName.CONSULTANT,
            terminal_result=ExecutionTerminalResult.CONSULT_COMPLETE,
            metadata={"target_stage": "checker"},
        ),
        RecoveryCounters(),
    )
    assert local_resume.action is RouterAction.RUN_STAGE
    assert local_resume.next_stage is ExecutionStageName.CHECKER


def test_planning_transition_table_and_mechanic_recovery() -> None:
    planner = next_planning_step(
        _snapshot(
            plane=Plane.PLANNING,
            stage=PlanningStageName.PLANNER,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-001",
        ),
        _stage_result(
            stage=PlanningStageName.PLANNER,
            terminal_result=PlanningTerminalResult.PLANNER_COMPLETE,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-001",
        ),
        RecoveryCounters(),
    )
    assert planner.action is RouterAction.RUN_STAGE
    assert planner.next_stage is PlanningStageName.MANAGER

    auditor = next_planning_step(
        _snapshot(
            plane=Plane.PLANNING,
            stage=PlanningStageName.AUDITOR,
            work_item_kind=WorkItemKind.INCIDENT,
            work_item_id="inc-001",
        ),
        _stage_result(
            stage=PlanningStageName.AUDITOR,
            terminal_result=PlanningTerminalResult.AUDITOR_COMPLETE,
            work_item_kind=WorkItemKind.INCIDENT,
            work_item_id="inc-001",
        ),
        RecoveryCounters(),
    )
    assert auditor.action is RouterAction.RUN_STAGE
    assert auditor.next_stage is PlanningStageName.PLANNER

    manager = next_planning_step(
        _snapshot(
            plane=Plane.PLANNING,
            stage=PlanningStageName.MANAGER,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-001",
        ),
        _stage_result(
            stage=PlanningStageName.MANAGER,
            terminal_result=PlanningTerminalResult.MANAGER_COMPLETE,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-001",
        ),
        RecoveryCounters(),
    )
    assert manager.action is RouterAction.IDLE
    assert manager.next_stage is None

    planner_blocked = next_planning_step(
        _snapshot(
            plane=Plane.PLANNING,
            stage=PlanningStageName.PLANNER,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-001",
        ),
        _stage_result(
            stage=PlanningStageName.PLANNER,
            terminal_result=PlanningTerminalResult.BLOCKED,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-001",
            metadata={"failure_class": "planning_artifact_mismatch"},
        ),
        RecoveryCounters(),
        max_mechanic_attempts=2,
    )
    assert planner_blocked.action is RouterAction.RUN_STAGE
    assert planner_blocked.next_stage is PlanningStageName.MECHANIC

    mechanic_blocked = next_planning_step(
        _snapshot(
            plane=Plane.PLANNING,
            stage=PlanningStageName.MECHANIC,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-001",
            current_failure_class="planning_artifact_mismatch",
        ),
        _stage_result(
            stage=PlanningStageName.MECHANIC,
            terminal_result=PlanningTerminalResult.BLOCKED,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-001",
            metadata={"failure_class": "planning_artifact_mismatch"},
        ),
        _counters(
            failure_class="planning_artifact_mismatch",
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-001",
            mechanic_attempt_count=2,
        ),
        max_mechanic_attempts=2,
    )
    assert mechanic_blocked.action is RouterAction.BLOCKED
    assert mechanic_blocked.next_stage is None


def test_recovery_routing_is_deterministic_for_same_inputs() -> None:
    snapshot = _snapshot(
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.TROUBLESHOOTER,
        current_failure_class="missing_terminal_result",
    )
    result = _stage_result(
        stage=ExecutionStageName.TROUBLESHOOTER,
        terminal_result=ExecutionTerminalResult.BLOCKED,
        metadata={"failure_class": "missing_terminal_result"},
    )
    counters = _counters(
        failure_class="missing_terminal_result",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        troubleshoot_attempt_count=1,
    )

    first = next_execution_step(snapshot, result, counters)
    second = next_execution_step(snapshot, result, counters)
    assert first == second


def test_counter_keying_is_stable_and_failure_class_scoped() -> None:
    key_one = counter_key_for_failure_class(
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        failure_class=" Missing_Terminal_Result ",
    )
    key_two = counter_key_for_failure_class(
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        failure_class="missing_terminal_result",
    )
    key_three = counter_key_for_failure_class(
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        failure_class="transport_failure",
    )

    assert key_one == key_two
    assert key_three != key_two


def test_execution_router_rejects_stage_result_identity_mismatch() -> None:
    snapshot = _snapshot(plane=Plane.EXECUTION, stage=ExecutionStageName.CHECKER)
    stage_result = _stage_result(
        stage=ExecutionStageName.CHECKER,
        terminal_result=ExecutionTerminalResult.CHECKER_PASS,
    ).model_copy(update={"run_id": "run-999"})

    with pytest.raises(ValueError, match="run_id"):
        next_execution_step(snapshot, stage_result, RecoveryCounters())


def test_planning_router_rejects_stage_result_stage_mismatch() -> None:
    snapshot = _snapshot(
        plane=Plane.PLANNING,
        stage=PlanningStageName.PLANNER,
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-001",
    )
    stage_result = _stage_result(
        stage=PlanningStageName.MANAGER,
        terminal_result=PlanningTerminalResult.MANAGER_COMPLETE,
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-001",
    )

    with pytest.raises(ValueError, match="active_stage"):
        next_planning_step(snapshot, stage_result, RecoveryCounters())


def test_execution_router_rejects_stage_result_work_item_mismatch() -> None:
    snapshot = _snapshot(plane=Plane.EXECUTION, stage=ExecutionStageName.CHECKER)
    stage_result = _stage_result(
        stage=ExecutionStageName.CHECKER,
        terminal_result=ExecutionTerminalResult.CHECKER_PASS,
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-001",
    )

    with pytest.raises(ValueError, match="work_item_kind"):
        next_execution_step(snapshot, stage_result, RecoveryCounters())
