from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.graph_authority import (
    completion_activation_for_graph,
    route_stage_result_from_graph,
    work_item_activation_for_graph,
)

NOW = datetime(2026, 4, 23, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _unused_stage_runner(request: StageRunRequest) -> RunnerRawResult:
    raise AssertionError(f"stage runner should not be called in graph-authority tests: {request.stage.value}")


def _snapshot(
    *,
    plane: Plane,
    stage: StageName,
    work_item_kind: WorkItemKind | None = WorkItemKind.TASK,
    work_item_id: str | None = "task-001",
    fix_cycle_count: int = 0,
    current_failure_class: str | None = None,
) -> RuntimeSnapshot:
    return RuntimeSnapshot(
        runtime_mode="daemon",
        process_running=True,
        paused=False,
        active_mode_id="default_codex",
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
    if terminal_result is ExecutionTerminalResult.FIX_NEEDED:
        return ResultClass.FOLLOWUP_NEEDED
    if terminal_result is ExecutionTerminalResult.NEEDS_PLANNING:
        return ResultClass.ESCALATE_PLANNING
    if terminal_result is PlanningTerminalResult.REMEDIATION_NEEDED:
        return ResultClass.FOLLOWUP_NEEDED
    if terminal_result in {ExecutionTerminalResult.BLOCKED, PlanningTerminalResult.BLOCKED}:
        return ResultClass.BLOCKED
    return ResultClass.SUCCESS


def _stage_result(
    *,
    stage: StageName,
    terminal_result: TerminalResult,
    work_item_kind: WorkItemKind = WorkItemKind.TASK,
    work_item_id: str = "task-001",
    metadata: dict[str, object] | None = None,
    closure_target: bool = False,
) -> StageResultEnvelope:
    plane = Plane.EXECUTION if isinstance(stage, ExecutionStageName) else Plane.PLANNING
    payload = dict(metadata or {})
    if closure_target:
        payload.setdefault("request_kind", "closure_target")
        payload.setdefault("closure_target_root_spec_id", work_item_id)
        payload.setdefault("closure_target_root_idea_id", "idea-001")
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
        metadata=payload,
        started_at=NOW,
        completed_at=NOW,
    )


def test_runtime_startup_loads_compiled_plan(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)

    engine.startup()

    assert engine.compiled_plan is not None
    assert engine.compiled_plan.execution_graph.loop_id == "execution.standard"
    assert engine.compiled_plan.planning_graph.loop_id == "planning.standard"


def test_work_item_activation_resolves_from_compiled_plan_entries(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None

    task = work_item_activation_for_graph(engine.compiled_plan, WorkItemKind.TASK)
    spec = work_item_activation_for_graph(engine.compiled_plan, WorkItemKind.SPEC)
    incident = work_item_activation_for_graph(engine.compiled_plan, WorkItemKind.INCIDENT)
    completion = completion_activation_for_graph(engine.compiled_plan)

    assert task.plane is Plane.EXECUTION
    assert task.stage is ExecutionStageName.BUILDER
    assert spec.plane is Plane.PLANNING
    assert spec.stage is PlanningStageName.PLANNER
    assert incident.plane is Plane.PLANNING
    assert incident.stage is PlanningStageName.AUDITOR
    assert completion.plane is Plane.PLANNING
    assert completion.stage is PlanningStageName.ARBITER


def test_work_item_activation_fails_when_required_entry_is_missing(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None
    broken_graph_plan = engine.compiled_plan.model_copy(
        update={
            "execution_graph": engine.compiled_plan.execution_graph.model_copy(
                update={"compiled_entries": ()}
            )
        }
    )

    with pytest.raises(ValueError, match="task"):
        work_item_activation_for_graph(broken_graph_plan, WorkItemKind.TASK)


def test_completion_activation_fails_when_completion_entry_is_missing(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None
    broken_graph_plan = engine.compiled_plan.model_copy(
        update={
            "planning_graph": engine.compiled_plan.planning_graph.model_copy(
                update={"compiled_completion_entry": None}
            )
        }
    )

    with pytest.raises(ValueError, match="closure_target"):
        completion_activation_for_graph(broken_graph_plan)


@pytest.mark.parametrize(
    ("snapshot", "stage_result", "counters", "expected_action", "expected_stage"),
    (
        (
            _snapshot(plane=Plane.EXECUTION, stage=ExecutionStageName.BUILDER),
            _stage_result(
                stage=ExecutionStageName.BUILDER,
                terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
            ),
            RecoveryCounters(),
            "run_stage",
            ExecutionStageName.CHECKER,
        ),
        (
            _snapshot(
                plane=Plane.EXECUTION,
                stage=ExecutionStageName.DOUBLECHECKER,
                fix_cycle_count=2,
            ),
            _stage_result(
                stage=ExecutionStageName.DOUBLECHECKER,
                terminal_result=ExecutionTerminalResult.FIX_NEEDED,
            ),
            RecoveryCounters(),
            "run_stage",
            ExecutionStageName.TROUBLESHOOTER,
        ),
        (
            _snapshot(
                plane=Plane.EXECUTION,
                stage=ExecutionStageName.UPDATER,
                current_failure_class="updater_blocked",
            ),
            _stage_result(
                stage=ExecutionStageName.UPDATER,
                terminal_result=ExecutionTerminalResult.BLOCKED,
                metadata={"failure_class": "updater_blocked"},
            ),
            RecoveryCounters(
                entries=(
                    {
                        "failure_class": "updater_blocked",
                        "work_item_kind": WorkItemKind.TASK,
                        "work_item_id": "task-001",
                        "troubleshoot_attempt_count": 2,
                        "last_updated_at": NOW,
                    },
                )
            ),
            "run_stage",
            ExecutionStageName.CONSULTANT,
        ),
        (
            _snapshot(
                plane=Plane.EXECUTION,
                stage=ExecutionStageName.TROUBLESHOOTER,
            ),
            _stage_result(
                stage=ExecutionStageName.TROUBLESHOOTER,
                terminal_result=ExecutionTerminalResult.TROUBLESHOOT_COMPLETE,
                metadata={"resume_stage": "checker"},
            ),
            RecoveryCounters(),
            "run_stage",
            ExecutionStageName.CHECKER,
        ),
        (
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
            RecoveryCounters(
                entries=(
                    {
                        "failure_class": "planning_artifact_mismatch",
                        "work_item_kind": WorkItemKind.SPEC,
                        "work_item_id": "spec-001",
                        "mechanic_attempt_count": 2,
                        "last_updated_at": NOW,
                    },
                )
            ),
            "blocked",
            None,
        ),
        (
            _snapshot(
                plane=Plane.PLANNING,
                stage=PlanningStageName.ARBITER,
                work_item_kind=None,
                work_item_id=None,
            ),
            _stage_result(
                stage=PlanningStageName.ARBITER,
                terminal_result=PlanningTerminalResult.REMEDIATION_NEEDED,
                work_item_kind=WorkItemKind.SPEC,
                work_item_id="spec-root-001",
                closure_target=True,
            ),
            RecoveryCounters(),
            "handoff",
            PlanningStageName.AUDITOR,
        ),
    ),
)
def test_route_stage_result_from_graph_matches_shipped_default_semantics(
    tmp_path: Path,
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    counters: RecoveryCounters,
    expected_action: str,
    expected_stage: StageName | None,
) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None

    decision = route_stage_result_from_graph(
        engine.compiled_plan,
        snapshot,
        stage_result,
        counters,
        max_fix_cycles=2,
        max_troubleshoot_attempts_before_consult=2,
        max_mechanic_attempts=2,
    )

    assert decision.action.value == expected_action
    assert decision.next_stage == expected_stage


def test_route_stage_result_from_graph_rejects_invalid_closure_target_identity(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None
    snapshot = _snapshot(
        plane=Plane.PLANNING,
        stage=PlanningStageName.ARBITER,
        work_item_kind=None,
        work_item_id=None,
    )
    stage_result = _stage_result(
        stage=PlanningStageName.ARBITER,
        terminal_result=PlanningTerminalResult.ARBITER_COMPLETE,
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-root-001",
        metadata={"request_kind": "closure_target"},
        closure_target=False,
    )

    with pytest.raises(ValueError, match="closure_target_root_spec_id"):
        route_stage_result_from_graph(
            engine.compiled_plan,
            snapshot,
            stage_result,
            RecoveryCounters(),
        )
