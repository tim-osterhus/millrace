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
    SpecDocument,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.state_store import load_recovery_counters, load_snapshot
from millrace_ai.workspace.arbiter_state import load_closure_target_state

NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _task_doc(
    task_id: str,
    *,
    created_at: datetime,
    incident_id: str | None = None,
    root_spec_id: str | None = None,
    root_idea_id: str | None = None,
) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="e2e handoff proof task",
        incident_id=incident_id,
        root_spec_id=root_spec_id,
        root_idea_id=root_idea_id,
        target_paths=["millrace/runtime.py"],
        acceptance=["runtime transition proof passes"],
        required_checks=["uv run pytest tests/integration/test_e2e_handoffs.py -q"],
        references=["lab/specs/drafts/millrace-mvp-implementation-slice.md"],
        risk=["transition drift"],
        created_at=created_at,
        created_by="tests",
    )


def _root_spec_doc(spec_id: str, *, root_idea_id: str, created_at: datetime) -> SpecDocument:
    return SpecDocument(
        spec_id=spec_id,
        title=f"Root Spec {spec_id}",
        summary="root lineage for completion-behavior integration coverage",
        source_type="idea",
        source_id=root_idea_id,
        root_spec_id=spec_id,
        root_idea_id=root_idea_id,
        goals=("prove arbiter triggers after backlog drain",),
        constraints=("keep the test deterministic",),
        acceptance=("runtime reaches arbiter after the lineage drains",),
        references=(f"ideas/inbox/{root_idea_id}.md",),
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


def _write_idea_doc(paths, idea_id: str) -> None:
    idea_path = paths.root / "ideas" / "inbox" / f"{idea_id}.md"
    idea_path.parent.mkdir(parents=True, exist_ok=True)
    idea_path.write_text(f"# {idea_id}\n\nSeed contract for {idea_id}.\n", encoding="utf-8")


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


def test_e2e_lineage_drain_triggers_arbiter_complete(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    _write_idea_doc(paths, "idea-root-001")
    queue.enqueue_spec(_root_spec_doc("spec-root-001", root_idea_id="idea-root-001", created_at=NOW))

    stage_order: list[str] = []
    task_created = {"done": False}
    captured_arbiter_request: StageRunRequest | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal captured_arbiter_request
        stage_order.append(request.stage.value)
        if request.stage is PlanningStageName.PLANNER:
            return _runner_result(request, terminal=PlanningTerminalResult.PLANNER_COMPLETE.value, now=NOW)
        if request.stage is PlanningStageName.MANAGER:
            if not task_created["done"]:
                queue.enqueue_task(
                    _task_doc(
                        "task-root-001",
                        created_at=NOW + timedelta(minutes=1),
                        root_spec_id="spec-root-001",
                        root_idea_id="idea-root-001",
                    )
                )
                task_created["done"] = True
            return _runner_result(request, terminal=PlanningTerminalResult.MANAGER_COMPLETE.value, now=NOW)
        if request.stage is ExecutionStageName.BUILDER:
            return _runner_result(request, terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value, now=NOW)
        if request.stage is ExecutionStageName.CHECKER:
            return _runner_result(request, terminal=ExecutionTerminalResult.CHECKER_PASS.value, now=NOW)
        if request.stage is ExecutionStageName.UPDATER:
            return _runner_result(request, terminal=ExecutionTerminalResult.UPDATE_COMPLETE.value, now=NOW)

        captured_arbiter_request = request
        verdict_path = Path(request.preferred_verdict_path)
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        verdict_path.write_text('{"status":"pass"}\n', encoding="utf-8")
        report_path = Path(request.preferred_report_path)
        report_path.write_text("# Arbiter Report\n\nParity holds.\n", encoding="utf-8")
        return _runner_result(request, terminal=PlanningTerminalResult.ARBITER_COMPLETE.value, now=NOW)

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    outcomes = [engine.tick() for _ in range(6)]
    target = load_closure_target_state(paths, root_spec_id="spec-root-001")

    assert [outcome.stage for outcome in outcomes] == [
        PlanningStageName.PLANNER,
        PlanningStageName.MANAGER,
        ExecutionStageName.BUILDER,
        ExecutionStageName.CHECKER,
        ExecutionStageName.UPDATER,
        PlanningStageName.ARBITER,
    ]
    assert stage_order == ["planner", "manager", "builder", "checker", "updater", "arbiter"]
    assert captured_arbiter_request is not None
    assert captured_arbiter_request.request_kind == "closure_target"
    assert target.closure_open is False
    assert target.closed_at is not None
    assert (paths.specs_done_dir / "spec-root-001.md").is_file()
    assert (paths.tasks_done_dir / "task-root-001.md").is_file()


def test_e2e_lineage_drain_triggers_arbiter_remediation_gap(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    _write_idea_doc(paths, "idea-root-gap")
    queue.enqueue_spec(_root_spec_doc("spec-root-gap", root_idea_id="idea-root-gap", created_at=NOW))

    stage_order: list[str] = []
    task_created = {"done": False}

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        stage_order.append(request.stage.value)
        if request.stage is PlanningStageName.PLANNER:
            return _runner_result(request, terminal=PlanningTerminalResult.PLANNER_COMPLETE.value, now=NOW)
        if request.stage is PlanningStageName.MANAGER:
            if not task_created["done"]:
                queue.enqueue_task(
                    _task_doc(
                        "task-root-gap",
                        created_at=NOW + timedelta(minutes=1),
                        root_spec_id="spec-root-gap",
                        root_idea_id="idea-root-gap",
                    )
                )
                task_created["done"] = True
            return _runner_result(request, terminal=PlanningTerminalResult.MANAGER_COMPLETE.value, now=NOW)
        if request.stage is ExecutionStageName.BUILDER:
            return _runner_result(request, terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value, now=NOW)
        if request.stage is ExecutionStageName.CHECKER:
            return _runner_result(request, terminal=ExecutionTerminalResult.CHECKER_PASS.value, now=NOW)
        if request.stage is ExecutionStageName.UPDATER:
            return _runner_result(request, terminal=ExecutionTerminalResult.UPDATE_COMPLETE.value, now=NOW)

        verdict_path = Path(request.preferred_verdict_path)
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        verdict_path.write_text('{"status":"gap"}\n', encoding="utf-8")
        report_path = Path(request.preferred_report_path)
        report_path.write_text("# Arbiter Report\n\nParity gaps remain.\n", encoding="utf-8")
        return _runner_result(request, terminal=PlanningTerminalResult.REMEDIATION_NEEDED.value, now=NOW)

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    outcomes = [engine.tick() for _ in range(6)]
    target = load_closure_target_state(paths, root_spec_id="spec-root-gap")
    incident_paths = tuple(paths.incidents_incoming_dir.glob("*.md"))

    assert outcomes[-1].stage is PlanningStageName.ARBITER
    assert stage_order == ["planner", "manager", "builder", "checker", "updater", "arbiter"]
    assert target.closure_open is True
    assert target.closed_at is None
    assert len(incident_paths) == 1
    assert "Source-Stage: arbiter" in incident_paths[0].read_text(encoding="utf-8")
