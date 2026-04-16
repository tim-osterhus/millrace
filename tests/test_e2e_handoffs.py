from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    IncidentDecision,
    IncidentDocument,
    IncidentSeverity,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.state_store import load_recovery_counters, load_snapshot

NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _task_doc(task_id: str, *, created_at: datetime, incident_id: str | None = None) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="e2e handoff proof task",
        incident_id=incident_id,
        target_paths=["millrace/runtime.py"],
        acceptance=["runtime transition proof passes"],
        required_checks=["uv run pytest tests/test_e2e_handoffs.py -q"],
        references=["lab/specs/drafts/millrace-mvp-implementation-slice.md"],
        risk=["transition drift"],
        created_at=created_at,
        created_by="tests",
    )


def _incident_doc(incident_id: str, *, opened_at: datetime, source_task_id: str) -> IncidentDocument:
    return IncidentDocument(
        incident_id=incident_id,
        title=f"Incident {incident_id}",
        summary="consultant escalated for planning remediation",
        source_task_id=source_task_id,
        source_stage=ExecutionStageName.CONSULTANT,
        source_plane=Plane.EXECUTION,
        failure_class="needs_planning",
        severity=IncidentSeverity.HIGH,
        needs_planning=True,
        trigger_reason="CONSULTANT emitted NEEDS_PLANNING",
        observed_symptoms=("qa blocker persisted",),
        failed_attempts=("troubleshooter x2",),
        consultant_decision=IncidentDecision.NEEDS_PLANNING,
        references=("lab/specs/drafts/millrace-agent-topology-and-transition-table.md",),
        opened_at=opened_at,
        opened_by="tests",
    )


def _runner_result(
    request: StageRunRequest,
    *,
    terminal: str | None,
    now: datetime,
    exit_kind: str = "completed",
) -> RunnerRawResult:
    run_dir = Path(request.run_dir)
    stdout_path = run_dir / "runner_stdout.txt"
    stdout_payload = "no terminal token\n" if terminal is None else f"### {terminal}\n"
    stdout_path.write_text(stdout_payload, encoding="utf-8")

    return RunnerRawResult(
        request_id=request.request_id,
        run_id=request.run_id,
        stage=request.stage,
        runner_name=request.runner_name or "test-runner",
        model_name=request.model_name,
        exit_kind=exit_kind,
        exit_code=0,
        stdout_path=str(stdout_path),
        stderr_path=None,
        terminal_result_path=None,
        started_at=now,
        ended_at=now + timedelta(seconds=1),
    )


def test_e2e_direct_task_handoff_happy_path(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-direct-001", created_at=NOW))

    stage_order: list[str] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        stage_order.append(request.stage.value)
        terminal_by_stage = {
            ExecutionStageName.BUILDER.value: ExecutionTerminalResult.BUILDER_COMPLETE.value,
            ExecutionStageName.CHECKER.value: ExecutionTerminalResult.CHECKER_PASS.value,
            ExecutionStageName.UPDATER.value: ExecutionTerminalResult.UPDATE_COMPLETE.value,
        }
        return _runner_result(request, terminal=terminal_by_stage[request.stage.value], now=NOW)

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    first = engine.tick()
    second = engine.tick()
    third = engine.tick()

    assert [first.stage, second.stage, third.stage] == [
        ExecutionStageName.BUILDER,
        ExecutionStageName.CHECKER,
        ExecutionStageName.UPDATER,
    ]
    assert stage_order == ["builder", "checker", "updater"]
    assert (paths.tasks_done_dir / "task-direct-001.md").is_file()

    snapshot = load_snapshot(paths)
    assert snapshot.active_stage is None
    assert snapshot.active_work_item_id is None


def test_e2e_repair_loop_fix_needed_cycle(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-fix-001", created_at=NOW))

    stage_order: list[str] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        stage_order.append(request.stage.value)
        terminal_by_stage = {
            ExecutionStageName.BUILDER.value: ExecutionTerminalResult.BUILDER_COMPLETE.value,
            ExecutionStageName.CHECKER.value: ExecutionTerminalResult.FIX_NEEDED.value,
            ExecutionStageName.FIXER.value: ExecutionTerminalResult.FIXER_COMPLETE.value,
            ExecutionStageName.DOUBLECHECKER.value: ExecutionTerminalResult.DOUBLECHECK_PASS.value,
            ExecutionStageName.UPDATER.value: ExecutionTerminalResult.UPDATE_COMPLETE.value,
        }
        return _runner_result(request, terminal=terminal_by_stage[request.stage.value], now=NOW)

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    outcomes = [engine.tick() for _ in range(5)]

    assert [outcome.stage for outcome in outcomes] == [
        ExecutionStageName.BUILDER,
        ExecutionStageName.CHECKER,
        ExecutionStageName.FIXER,
        ExecutionStageName.DOUBLECHECKER,
        ExecutionStageName.UPDATER,
    ]
    assert stage_order == ["builder", "checker", "fixer", "doublechecker", "updater"]
    assert (paths.tasks_done_dir / "task-fix-001.md").is_file()


def test_e2e_recovery_malformed_result_routes_to_consultant(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-recover-001", created_at=NOW))

    calls = {"troubleshooter": 0}

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        if request.stage is ExecutionStageName.BUILDER:
            # Missing terminal token forces deterministic malformed-result recovery.
            return _runner_result(request, terminal=None, now=NOW)
        if request.stage is ExecutionStageName.TROUBLESHOOTER:
            calls["troubleshooter"] += 1
            return _runner_result(request, terminal=ExecutionTerminalResult.BLOCKED.value, now=NOW)
        return _runner_result(request, terminal=ExecutionTerminalResult.CONSULT_COMPLETE.value, now=NOW)

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    first = engine.tick()
    second = engine.tick()
    third = engine.tick()

    assert first.stage is ExecutionStageName.BUILDER
    assert second.stage is ExecutionStageName.TROUBLESHOOTER
    assert third.stage is ExecutionStageName.TROUBLESHOOTER
    assert calls["troubleshooter"] == 2
    assert third.router_decision.next_stage is ExecutionStageName.CONSULTANT

    snapshot = load_snapshot(paths)
    assert snapshot.active_stage is ExecutionStageName.CONSULTANT
    assert snapshot.current_failure_class == "missing_terminal_result"

    counters = load_recovery_counters(paths)
    entry = counters.entries[0]
    assert entry.failure_class == "missing_terminal_result"
    assert entry.work_item_kind is WorkItemKind.TASK
    assert entry.work_item_id == "task-recover-001"
    assert entry.troubleshoot_attempt_count == 2
    assert entry.consultant_invocations == 1


def test_e2e_needs_planning_incident_intake_reenters_execution(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_incident(
        _incident_doc(
            "incident-001",
            opened_at=NOW,
            source_task_id="task-recover-001",
        )
    )

    stage_order: list[str] = []
    task_created = {"done": False}

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        stage_order.append(request.stage.value)
        if request.stage is PlanningStageName.AUDITOR:
            return _runner_result(request, terminal=PlanningTerminalResult.AUDITOR_COMPLETE.value, now=NOW)
        if request.stage is PlanningStageName.PLANNER:
            return _runner_result(request, terminal=PlanningTerminalResult.PLANNER_COMPLETE.value, now=NOW)
        if request.stage is PlanningStageName.MANAGER:
            if not task_created["done"]:
                queue.enqueue_task(
                    _task_doc(
                        "task-remediate-001",
                        created_at=NOW + timedelta(minutes=1),
                        incident_id="incident-001",
                    )
                )
                task_created["done"] = True
            return _runner_result(request, terminal=PlanningTerminalResult.MANAGER_COMPLETE.value, now=NOW)
        if request.stage is ExecutionStageName.BUILDER:
            return _runner_result(request, terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value, now=NOW)
        if request.stage is ExecutionStageName.CHECKER:
            return _runner_result(request, terminal=ExecutionTerminalResult.CHECKER_PASS.value, now=NOW)
        return _runner_result(request, terminal=ExecutionTerminalResult.UPDATE_COMPLETE.value, now=NOW)

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    outcomes = [engine.tick() for _ in range(6)]

    assert [outcome.stage for outcome in outcomes] == [
        PlanningStageName.AUDITOR,
        PlanningStageName.PLANNER,
        PlanningStageName.MANAGER,
        ExecutionStageName.BUILDER,
        ExecutionStageName.CHECKER,
        ExecutionStageName.UPDATER,
    ]
    assert stage_order == [
        "auditor",
        "planner",
        "manager",
        "builder",
        "checker",
        "updater",
    ]
    assert (paths.incidents_resolved_dir / "incident-001.md").is_file()
    assert (paths.tasks_done_dir / "task-remediate-001.md").is_file()
