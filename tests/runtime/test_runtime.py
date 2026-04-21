from __future__ import annotations

import importlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from millrace_ai.contracts import (
    ClosureTargetState,
    ExecutionStageName,
    ExecutionTerminalResult,
    IncidentDocument,
    MailboxCommandEnvelope,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    RecoveryCounterEntry,
    RecoveryCounters,
    ResultClass,
    RuntimeMode,
    RuntimeSnapshot,
    SpecDocument,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.control import RuntimeControl
from millrace_ai.errors import ControlRoutingError, RuntimeLifecycleError
from millrace_ai.events import read_runtime_events
from millrace_ai.mailbox import write_mailbox_command
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.router import RouterAction
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime_lock import (
    RuntimeOwnershipLockError,
    acquire_runtime_ownership_lock,
    inspect_runtime_ownership_lock,
    release_runtime_ownership_lock,
)
from millrace_ai.state_store import (
    load_execution_status,
    load_planning_status,
    load_recovery_counters,
    load_snapshot,
    save_recovery_counters,
    save_snapshot,
    set_execution_status,
    set_planning_status,
)
from millrace_ai.watchers import WatcherMode, WatchEvent
from millrace_ai.work_documents import read_work_document_as
from millrace_ai.workspace.arbiter_state import load_closure_target_state, save_closure_target_state

NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _write_runtime_error_catalog(root: Path) -> Path:
    catalog_path = root / "docs" / "runtime" / "millrace-runtime-error-codes.md"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text("# Runtime Error Codes\n", encoding="utf-8")
    return catalog_path


def test_runtime_import_surface_moves_to_package_directory() -> None:
    runtime_module = importlib.import_module("millrace_ai.runtime")

    assert Path(runtime_module.__file__).as_posix().endswith("/runtime/__init__.py")


def _task_doc(task_id: str, *, created_at: datetime) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="runtime test task",
        target_paths=["millrace/runtime.py"],
        acceptance=["runtime stage sequence is deterministic"],
        required_checks=["uv run pytest tests/runtime/test_runtime.py -q"],
        references=["lab/specs/drafts/millrace-runtime-module-and-cli-plan.md"],
        risk=["runtime drift"],
        created_at=created_at,
        created_by="tests",
    )


def _spec_doc(spec_id: str, *, created_at: datetime) -> SpecDocument:
    return SpecDocument(
        spec_id=spec_id,
        title=f"Spec {spec_id}",
        summary="runtime planning input",
        source_type="manual",
        goals=["prove planning runs before execution"],
        constraints=["deterministic selection"],
        acceptance=["planning stage runs first"],
        references=["lab/specs/drafts/millrace-agent-topology-and-transition-table.md"],
        created_at=created_at,
        created_by="tests",
    )


def _mailbox_command(
    command_id: str,
    command: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "command_id": command_id,
        "command": command,
        "issued_at": NOW,
        "issuer": "tests",
        "payload": payload or {},
    }


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


def test_runtime_tick_prioritizes_planning_before_execution(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW + timedelta(minutes=2)))
    queue.enqueue_spec(_spec_doc("spec-001", created_at=NOW))

    seen_stages: list[str] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        seen_stages.append(request.stage.value)
        terminal_by_stage = {
            "planner": PlanningTerminalResult.PLANNER_COMPLETE.value,
            "manager": PlanningTerminalResult.MANAGER_COMPLETE.value,
            "builder": ExecutionTerminalResult.BUILDER_COMPLETE.value,
        }
        return _runner_result(
            request,
            terminal=terminal_by_stage.get(request.stage.value),
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    first = engine.tick()
    second = engine.tick()
    third = engine.tick()

    assert first.stage == PlanningStageName.PLANNER
    assert second.stage == PlanningStageName.MANAGER
    assert third.stage == ExecutionStageName.BUILDER
    assert seen_stages[:3] == ["planner", "manager", "builder"]

    assert (paths.specs_done_dir / "spec-001.md").is_file()
    assert (paths.tasks_active_dir / "task-001.md").is_file()


def test_runtime_snapshot_queue_depths_match_filesystem_after_tick(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    engine.tick()

    snapshot = load_snapshot(paths)
    execution_queue_depth = len(tuple(paths.tasks_queue_dir.glob("*.md")))
    planning_queue_depth = len(tuple(paths.specs_queue_dir.glob("*.md"))) + len(
        tuple(paths.incidents_incoming_dir.glob("*.md"))
    )
    assert snapshot.queue_depth_execution == execution_queue_depth
    assert snapshot.queue_depth_planning == planning_queue_depth


def test_runtime_writes_snapshot_status_events_and_stage_result_artifacts(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    stage_results = {
        "builder": ExecutionTerminalResult.BUILDER_COMPLETE.value,
        "checker": ExecutionTerminalResult.CHECKER_PASS.value,
        "updater": ExecutionTerminalResult.UPDATE_COMPLETE.value,
    }

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(request, terminal=stage_results.get(request.stage.value), now=NOW)

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    first = engine.tick()
    second = engine.tick()
    third = engine.tick()

    for outcome in (first, second, third):
        assert outcome.stage_result_path is not None
        assert outcome.stage_result_path.is_file()
        payload = json.loads(outcome.stage_result_path.read_text(encoding="utf-8"))
        assert payload["kind"] == "stage_result"

    snapshot = load_snapshot(paths)
    assert snapshot.active_stage is None
    assert snapshot.active_plane is None
    assert snapshot.last_terminal_result == ExecutionTerminalResult.UPDATE_COMPLETE
    assert snapshot.last_stage_result_path == str(third.stage_result_path.relative_to(paths.root))

    assert load_execution_status(paths) == "### IDLE"
    assert (paths.tasks_done_dir / "task-001.md").is_file()

    events = read_runtime_events(paths)
    event_types = [event.event_type for event in events]
    assert "runtime_started" in event_types
    assert "stage_started" in event_types
    assert "stage_completed" in event_types
    assert "router_decision" in event_types


def test_runtime_stage_events_surface_failure_class_and_troubleshoot_report_path(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    captured_request: StageRunRequest | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal captured_request
        captured_request = request
        report_path = Path(request.preferred_troubleshoot_report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("# Troubleshoot\n", encoding="utf-8")
        return _runner_result(
            request,
            terminal=None,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    outcome = engine.tick()
    events = read_runtime_events(paths)

    stage_started = next(event for event in events if event.event_type == "stage_started")
    stage_completed = next(event for event in events if event.event_type == "stage_completed")
    router_decision = next(event for event in events if event.event_type == "router_decision")

    assert captured_request is not None
    assert stage_started.data["run_id"] == captured_request.run_id
    assert stage_started.data["work_item_id"] == "task-001"
    assert stage_started.data["troubleshoot_report_path"] == captured_request.preferred_troubleshoot_report_path
    assert stage_completed.data["failure_class"] == "missing_terminal_result"
    assert stage_completed.data["troubleshoot_report_path"] == captured_request.preferred_troubleshoot_report_path
    assert router_decision.data["failure_class"] == "missing_terminal_result"
    assert outcome.stage_result.report_artifact == captured_request.preferred_troubleshoot_report_path


def test_runtime_single_tick_emits_stage_events_in_order(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    captured_request: StageRunRequest | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal captured_request
        captured_request = request
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    outcome = engine.tick()
    events = read_runtime_events(paths)
    event_types = [event.event_type for event in events]

    assert captured_request is not None
    assert outcome.stage is ExecutionStageName.BUILDER
    runtime_started_index = event_types.index("runtime_started")
    stage_started_index = event_types.index("stage_started")
    stage_completed_index = event_types.index("stage_completed")
    router_decision_index = event_types.index("router_decision")

    assert runtime_started_index < stage_started_index < stage_completed_index < router_decision_index
    assert events[stage_started_index].data["run_id"] == captured_request.run_id
    assert events[stage_started_index].data["stage"] == "builder"
    assert (
        events[stage_completed_index].data["terminal_result"]
        == ExecutionTerminalResult.BUILDER_COMPLETE.value
    )
    assert events[router_decision_index].data["action"] == "run_stage"
    assert events[router_decision_index].data["next_stage"] == "checker"


def test_runtime_stage_request_entrypoint_path_exists_after_startup(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    captured_request: StageRunRequest | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal captured_request
        captured_request = request
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    engine.tick()

    assert captured_request is not None
    assert Path(captured_request.entrypoint_path).is_file()
    assert captured_request.active_work_item_path is not None
    assert captured_request.active_work_item_path.endswith(".md")


def test_runtime_writes_running_status_marker_while_stage_runner_is_active(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    observed_execution_status: str | None = None
    observed_snapshot_marker: str | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal observed_execution_status, observed_snapshot_marker
        observed_execution_status = load_execution_status(paths)
        observed_snapshot_marker = load_snapshot(paths).execution_status_marker
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    engine.tick()

    assert observed_execution_status == "### BUILDER_RUNNING"
    assert observed_snapshot_marker == "### BUILDER_RUNNING"
    assert load_execution_status(paths) == "### BUILDER_COMPLETE"


def test_runtime_can_build_closure_target_request_without_active_work_item(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.ARBITER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    assert engine.snapshot is not None

    target = ClosureTargetState(
        root_spec_id="spec-root-001",
        root_idea_id="idea-001",
        root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
        root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
        rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
        latest_verdict_path="millrace-agents/arbiter/verdicts/spec-root-001.json",
        latest_report_path="millrace-agents/arbiter/reports/run-001.md",
        closure_open=True,
        closure_blocked_by_lineage_work=False,
        blocking_work_ids=(),
        opened_at=NOW,
    )
    save_closure_target_state(paths, target)

    arbiter_plan = engine._stage_plan_for(Plane.PLANNING, PlanningStageName.ARBITER)
    request = engine._build_closure_target_stage_run_request(arbiter_plan, target)

    assert request.request_kind == "closure_target"
    assert request.active_work_item_kind is None
    assert request.active_work_item_id is None
    assert request.active_work_item_path is None
    assert request.closure_target_root_spec_id == "spec-root-001"
    assert request.canonical_root_spec_path.endswith("spec-root-001.md")


def test_runtime_planning_retry_scope_skips_execution_active_work(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    claim = queue.claim_next_execution_task()
    assert claim is not None

    engine = RuntimeEngine(paths, stage_runner=lambda request: _runner_result(request, terminal=None, now=NOW))
    engine.startup()
    engine.snapshot = load_snapshot(paths).model_copy(
        update={
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "active_run_id": "run-active",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, engine.snapshot)

    engine._handle_mailbox_command(
        MailboxCommandEnvelope(
            command_id="cmd-001",
            command="retry_active",
            issued_at=NOW,
            issuer="operator",
            payload={"reason": "planning-only retry", "scope": "planning"},
        )
    )

    snapshot = load_snapshot(paths)
    assert snapshot.active_work_item_id == "task-001"
    assert (paths.tasks_active_dir / "task-001.md").is_file()


def test_runtime_mailbox_retry_scope_rejects_invalid_scope_payloads() -> None:
    with pytest.raises(ControlRoutingError, match="retry_active scope must be a string"):
        RuntimeEngine._mailbox_retry_scope(
            MailboxCommandEnvelope.model_validate(
                _mailbox_command("cmd-invalid-scope-type", "retry_active", payload={"scope": 123})
            )
        )

    with pytest.raises(ControlRoutingError, match="Unsupported retry_active scope: unsupported"):
        RuntimeEngine._mailbox_retry_scope(
            MailboxCommandEnvelope.model_validate(
                _mailbox_command(
                    "cmd-invalid-scope-value",
                    "retry_active",
                    payload={"scope": "unsupported"},
                )
            )
        )


def test_runtime_routes_malformed_stage_exit_into_recovery(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    call_index = {"count": 0}

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        call_index["count"] += 1
        if call_index["count"] == 1:
            return _runner_result(request, terminal=None, now=NOW)
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    first = engine.tick()
    assert first.stage == ExecutionStageName.BUILDER

    snapshot = load_snapshot(paths)
    assert snapshot.active_stage == ExecutionStageName.TROUBLESHOOTER
    assert snapshot.current_failure_class == "missing_terminal_result"
    assert load_execution_status(paths) == "### BLOCKED"


def test_runtime_routes_post_stage_planning_completion_conflict_into_mechanic(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec_doc("spec-001", created_at=NOW))
    catalog_path = _write_runtime_error_catalog(paths.root)

    captured_mechanic_request: StageRunRequest | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal captured_mechanic_request
        if request.stage is PlanningStageName.PLANNER:
            return _runner_result(
                request,
                terminal=PlanningTerminalResult.PLANNER_COMPLETE.value,
                now=NOW,
            )
        if request.stage is PlanningStageName.MANAGER:
            queue.mark_spec_done("spec-001")
            return _runner_result(
                request,
                terminal=PlanningTerminalResult.MANAGER_COMPLETE.value,
                now=NOW,
            )
        captured_mechanic_request = request
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.MECHANIC_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    first = engine.tick()
    second = engine.tick()

    assert first.stage is PlanningStageName.PLANNER
    assert second.stage is PlanningStageName.MANAGER
    assert second.router_decision.next_stage is PlanningStageName.MECHANIC

    snapshot = load_snapshot(paths)
    assert snapshot.active_stage is PlanningStageName.MECHANIC
    assert snapshot.current_failure_class == "planning_work_item_completion_conflict"
    assert load_planning_status(paths) == "### BLOCKED"
    assert (paths.specs_done_dir / "spec-001.md").is_file()
    assert not (paths.specs_active_dir / "spec-001.md").exists()

    third = engine.tick()

    assert third.stage is PlanningStageName.MECHANIC
    assert captured_mechanic_request is not None
    assert captured_mechanic_request.runtime_error_code == "planning_work_item_completion_conflict"
    assert captured_mechanic_request.runtime_error_catalog_path == str(catalog_path)
    assert captured_mechanic_request.runtime_error_report_path is not None

    report_path = Path(captured_mechanic_request.runtime_error_report_path)
    assert report_path.is_file()
    report_text = report_path.read_text(encoding="utf-8")
    assert "planning_work_item_completion_conflict" in report_text
    assert "QueueStateError" in report_text
    assert "spec spec-001 is not active" in report_text


def test_runtime_routes_post_stage_execution_completion_conflict_into_troubleshooter(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    catalog_path = _write_runtime_error_catalog(paths.root)

    captured_troubleshooter_request: StageRunRequest | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal captured_troubleshooter_request
        if request.stage is ExecutionStageName.BUILDER:
            return _runner_result(
                request,
                terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
                now=NOW,
            )
        if request.stage is ExecutionStageName.CHECKER:
            return _runner_result(
                request,
                terminal=ExecutionTerminalResult.CHECKER_PASS.value,
                now=NOW,
            )
        if request.stage is ExecutionStageName.UPDATER:
            queue.mark_task_done("task-001")
            return _runner_result(
                request,
                terminal=ExecutionTerminalResult.UPDATE_COMPLETE.value,
                now=NOW,
            )
        captured_troubleshooter_request = request
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    first = engine.tick()
    second = engine.tick()
    third = engine.tick()

    assert first.stage is ExecutionStageName.BUILDER
    assert second.stage is ExecutionStageName.CHECKER
    assert third.stage is ExecutionStageName.UPDATER
    assert third.router_decision.next_stage is ExecutionStageName.TROUBLESHOOTER

    snapshot = load_snapshot(paths)
    assert snapshot.active_stage is ExecutionStageName.TROUBLESHOOTER
    assert snapshot.current_failure_class == "execution_work_item_completion_conflict"
    assert load_execution_status(paths) == "### BLOCKED"
    assert (paths.tasks_done_dir / "task-001.md").is_file()
    assert not (paths.tasks_active_dir / "task-001.md").exists()

    fourth = engine.tick()

    assert fourth.stage is ExecutionStageName.TROUBLESHOOTER
    assert captured_troubleshooter_request is not None
    assert captured_troubleshooter_request.runtime_error_code == "execution_work_item_completion_conflict"
    assert captured_troubleshooter_request.runtime_error_catalog_path == str(catalog_path)
    assert captured_troubleshooter_request.runtime_error_report_path is not None

    report_path = Path(captured_troubleshooter_request.runtime_error_report_path)
    assert report_path.is_file()
    report_text = report_path.read_text(encoding="utf-8")
    assert "execution_work_item_completion_conflict" in report_text
    assert "QueueStateError" in report_text
    assert "task task-001 is not active" in report_text


def test_runtime_blocked_planning_item_is_moved_to_blocked_without_snapshot_crash(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec_doc("spec-001", created_at=NOW))

    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[recovery]\nmax_mechanic_attempts = 1\n", encoding="utf-8")

    seen_stages: list[str] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        seen_stages.append(request.stage.value)
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.BLOCKED.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()

    first = engine.tick()
    assert first.stage == PlanningStageName.PLANNER

    second = engine.tick()
    assert second.stage == PlanningStageName.MECHANIC

    snapshot = load_snapshot(paths)
    assert snapshot.active_stage is None
    assert snapshot.active_plane is None
    assert snapshot.active_work_item_kind is None
    assert snapshot.active_work_item_id is None
    assert snapshot.current_failure_class == "planner_blocked"
    assert load_planning_status(paths) == "### BLOCKED"
    assert (paths.specs_blocked_dir / "spec-001.md").is_file()
    assert not (paths.specs_active_dir / "spec-001.md").exists()
    assert load_recovery_counters(paths).entries == ()
    assert seen_stages == ["planner", "mechanic"]
    assert second.router_decision.action is RouterAction.BLOCKED
    assert second.router_decision.reason == "mechanic_blocked:mechanic_attempts_exhausted"


def test_runtime_handoff_creates_incident_and_transitions_to_planning(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text(
        "[recovery]\nmax_troubleshoot_attempts_before_consult = 1\n",
        encoding="utf-8",
    )

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        if request.stage is ExecutionStageName.BUILDER:
            return _runner_result(
                request,
                terminal=ExecutionTerminalResult.BLOCKED.value,
                now=NOW,
            )
        if request.stage is ExecutionStageName.TROUBLESHOOTER:
            return _runner_result(
                request,
                terminal=ExecutionTerminalResult.BLOCKED.value,
                now=NOW,
            )
        if request.stage is ExecutionStageName.CONSULTANT:
            return _runner_result(
                request,
                terminal=ExecutionTerminalResult.NEEDS_PLANNING.value,
                now=NOW,
            )
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.AUDITOR_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()

    first = engine.tick()
    second = engine.tick()
    third = engine.tick()

    assert first.stage is ExecutionStageName.BUILDER
    assert second.stage is ExecutionStageName.TROUBLESHOOTER
    assert third.stage is ExecutionStageName.CONSULTANT
    assert third.router_decision.action is RouterAction.HANDOFF

    snapshot_after_handoff = load_snapshot(paths)
    assert snapshot_after_handoff.active_stage is None
    assert snapshot_after_handoff.active_plane is None
    assert snapshot_after_handoff.active_work_item_id is None
    assert snapshot_after_handoff.current_failure_class is None
    assert (paths.tasks_blocked_dir / "task-001.md").is_file()
    assert len(tuple(paths.incidents_incoming_dir.glob("*.md"))) == 1
    incident_path = next(paths.incidents_incoming_dir.glob("*.md"))
    incident = read_work_document_as(incident_path, model=IncidentDocument)
    assert incident.needs_planning is True
    assert incident.trigger_reason == "consultant_needs_planning"
    assert incident.source_stage is ExecutionStageName.CONSULTANT

    event_types = [event.event_type for event in read_runtime_events(paths)]
    assert "runtime_handoff_incident_enqueued" in event_types

    fourth = engine.tick()
    assert fourth.stage is PlanningStageName.AUDITOR


def test_runtime_blocked_transition_recovers_when_active_artifact_missing(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec_doc("spec-001", created_at=NOW))

    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[recovery]\nmax_mechanic_attempts = 1\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.BLOCKED.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()

    first = engine.tick()
    assert first.stage is PlanningStageName.PLANNER

    active_spec_path = paths.specs_active_dir / "spec-001.md"
    assert active_spec_path.is_file()
    active_spec_path.unlink()

    second = engine.tick()
    assert second.stage is PlanningStageName.MECHANIC
    snapshot = load_snapshot(paths)
    assert snapshot.active_stage is None
    assert snapshot.active_plane is None


def test_runtime_startup_reconciles_stale_state_to_recovery_stage(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    claimed = queue.claim_next_execution_task()
    assert claimed is not None

    stale_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": False,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.CHECKER,
            "active_run_id": "run-stale",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, stale_snapshot)
    set_execution_status(paths, "### CHECKER_PASS")

    seen_stages: list[str] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        seen_stages.append(request.stage.value)
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    reconciled = engine.startup()

    assert reconciled.active_stage == ExecutionStageName.TROUBLESHOOTER
    assert reconciled.current_failure_class == "stale_active_ownership"
    persisted_counters = load_recovery_counters(paths)
    assert persisted_counters.entries
    assert persisted_counters.entries[0].troubleshoot_attempt_count == 1
    assert engine.counters is not None
    assert engine.counters.entries[0].troubleshoot_attempt_count == 1

    engine.tick()
    assert seen_stages[0] == "troubleshooter"


def test_runtime_tick_reconciles_execution_anomaly_before_stage_execution(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    claimed = queue.claim_next_execution_task()
    assert claimed is not None

    seen_stages: list[ExecutionStageName] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        assert isinstance(request.stage, ExecutionStageName)
        seen_stages.append(request.stage)
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    stale_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": False,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.CHECKER,
            "active_run_id": "run-stale-tick",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, stale_snapshot)
    engine.snapshot = stale_snapshot
    set_execution_status(paths, "### CHECKER_PASS")

    outcome = engine.tick()

    assert outcome.stage is ExecutionStageName.TROUBLESHOOTER
    assert seen_stages == [ExecutionStageName.TROUBLESHOOTER]
    counters = load_recovery_counters(paths)
    assert len(counters.entries) == 1
    assert counters.entries[0].failure_class == "stale_active_ownership"
    assert counters.entries[0].troubleshoot_attempt_count == 1

    event_types = [event.event_type for event in read_runtime_events(paths)]
    assert "runtime_reconciled" in event_types
    assert event_types.index("runtime_reconciled") < event_types.index("stage_started")


def test_runtime_tick_routes_planning_anomaly_into_mechanic(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec_doc("spec-001", created_at=NOW))
    claimed = queue.claim_next_planning_item()
    assert claimed is not None

    seen_stages: list[PlanningStageName] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        assert isinstance(request.stage, PlanningStageName)
        seen_stages.append(request.stage)
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.MECHANIC_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    anomalous_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "active_plane": Plane.PLANNING,
            "active_stage": PlanningStageName.PLANNER,
            "active_run_id": "run-planning-anomaly",
            "active_work_item_kind": WorkItemKind.SPEC,
            "active_work_item_id": "spec-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, anomalous_snapshot)
    engine.snapshot = anomalous_snapshot
    set_planning_status(paths, "### MANAGER_COMPLETE")

    outcome = engine.tick()

    assert outcome.stage is PlanningStageName.MECHANIC
    assert seen_stages == [PlanningStageName.MECHANIC]
    counters = load_recovery_counters(paths)
    assert len(counters.entries) == 1
    assert counters.entries[0].failure_class == "impossible_status_marker"
    assert counters.entries[0].mechanic_attempt_count == 1


def test_runtime_tick_routes_unknown_execution_marker_into_troubleshooter(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    claimed = queue.claim_next_execution_task()
    assert claimed is not None

    seen_stages: list[ExecutionStageName] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        assert isinstance(request.stage, ExecutionStageName)
        seen_stages.append(request.stage)
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    active_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.CHECKER,
            "active_run_id": "run-invalid-marker",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, active_snapshot)
    engine.snapshot = active_snapshot
    paths.execution_status_file.write_text("### NOT_A_REAL_MARKER\n", encoding="utf-8")

    outcome = engine.tick()

    assert outcome.stage is ExecutionStageName.TROUBLESHOOTER
    assert seen_stages == [ExecutionStageName.TROUBLESHOOTER]


def test_runtime_tick_routes_malformed_execution_marker_into_troubleshooter(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    claimed = queue.claim_next_execution_task()
    assert claimed is not None

    seen_stages: list[ExecutionStageName] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        assert isinstance(request.stage, ExecutionStageName)
        seen_stages.append(request.stage)
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    active_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.CHECKER,
            "active_run_id": "run-invalid-marker",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, active_snapshot)
    engine.snapshot = active_snapshot
    paths.execution_status_file.write_text("### CHECKER_PASS\n### EXTRA\n", encoding="utf-8")

    outcome = engine.tick()

    assert outcome.stage is ExecutionStageName.TROUBLESHOOTER
    assert seen_stages == [ExecutionStageName.TROUBLESHOOTER]


def test_runtime_tick_stale_execution_anomaly_escalates_to_consultant(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    claimed = queue.claim_next_execution_task()
    assert claimed is not None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BLOCKED.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    save_recovery_counters(
        paths,
        RecoveryCounters(
            entries=(
                RecoveryCounterEntry(
                    failure_class="stale_active_ownership",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    troubleshoot_attempt_count=2,
                    last_updated_at=NOW,
                ),
            )
        ),
    )
    stale_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": False,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.CHECKER,
            "active_run_id": "run-stale-consult",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, stale_snapshot)
    engine.snapshot = stale_snapshot
    engine.counters = load_recovery_counters(paths)
    set_execution_status(paths, "### CHECKER_PASS")

    outcome = engine.tick()

    assert outcome.stage is ExecutionStageName.CONSULTANT
    assert outcome.router_decision.action is RouterAction.BLOCKED


def test_runtime_tick_enforces_pause_and_stop_commands(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    calls = {"count": 0}

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        calls["count"] += 1
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    write_mailbox_command(paths, _mailbox_command("cmd-001", "pause"))
    paused = engine.tick()
    assert paused.router_decision.reason == "paused"
    assert paused.stage_result.result_class is ResultClass.SUCCESS
    assert paused.stage_result.terminal_result is ExecutionTerminalResult.UPDATE_COMPLETE
    assert calls["count"] == 0

    write_mailbox_command(paths, _mailbox_command("cmd-002", "stop"))
    stopped = engine.tick()
    assert stopped.router_decision.reason == "stop_requested"
    assert stopped.stage_result.result_class is ResultClass.SUCCESS
    assert stopped.stage_result.terminal_result is ExecutionTerminalResult.UPDATE_COMPLETE
    assert calls["count"] == 0
    snapshot = load_snapshot(paths)
    assert snapshot.process_running is False
    assert snapshot.stop_requested is False
    assert snapshot.paused is False


def test_runtime_tick_stop_normalizes_active_snapshot_to_stopped_idle_invariant(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    claimed = queue.claim_next_execution_task()
    assert claimed is not None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    active_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "paused": True,
            "stop_requested": False,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "active_run_id": "run-stop-active",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "current_failure_class": "runner_transport_failure",
            "troubleshoot_attempt_count": 2,
            "mechanic_attempt_count": 1,
            "fix_cycle_count": 1,
            "consultant_invocations": 1,
            "execution_status_marker": "### BLOCKED",
            "planning_status_marker": "### MANAGER_COMPLETE",
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, active_snapshot)
    engine.snapshot = active_snapshot
    set_execution_status(paths, "### BLOCKED")
    set_planning_status(paths, "### MANAGER_COMPLETE")

    write_mailbox_command(paths, _mailbox_command("cmd-stop-active", "stop"))
    outcome = engine.tick()

    assert outcome.router_decision.reason == "stop_requested"
    snapshot = load_snapshot(paths)
    assert snapshot.process_running is False
    assert snapshot.paused is False
    assert snapshot.stop_requested is False
    assert snapshot.active_plane is None
    assert snapshot.active_stage is None
    assert snapshot.active_run_id is None
    assert snapshot.active_work_item_kind is None
    assert snapshot.active_work_item_id is None
    assert snapshot.active_since is None
    assert snapshot.current_failure_class is None
    assert snapshot.troubleshoot_attempt_count == 0
    assert snapshot.mechanic_attempt_count == 0
    assert snapshot.fix_cycle_count == 0
    assert snapshot.consultant_invocations == 0
    assert snapshot.execution_status_marker == "### IDLE"
    assert snapshot.planning_status_marker == "### IDLE"
    assert load_execution_status(paths) == "### IDLE"
    assert load_planning_status(paths) == "### IDLE"


def test_runtime_tick_applies_mailbox_pause_before_reconciliation(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    claimed = queue.claim_next_execution_task()
    assert claimed is not None

    calls = {"count": 0}

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        calls["count"] += 1
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    stale_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": False,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.CHECKER,
            "active_run_id": "run-ordering",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, stale_snapshot)
    engine.snapshot = stale_snapshot
    engine.counters = load_recovery_counters(paths)
    set_execution_status(paths, "### CHECKER_PASS")
    write_mailbox_command(paths, _mailbox_command("cmd-pause-ordering", "pause"))

    outcome = engine.tick()

    assert outcome.router_decision.reason == "paused"
    assert calls["count"] == 0
    event_types = [event.event_type for event in read_runtime_events(paths)]
    assert "runtime_tick_paused" in event_types
    assert "runtime_reconciled" not in event_types


def test_runtime_tick_normalizes_idea_watch_event_before_execution(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[watchers]",
                "enabled = true",
                "debounce_ms = 100",
                "watch_ideas_inbox = true",
            ]
        ),
        encoding="utf-8",
    )
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    seen_stages: list[PlanningStageName | ExecutionStageName] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        seen_stages.append(request.stage)
        terminal = (
            PlanningTerminalResult.PLANNER_COMPLETE.value
            if request.stage is PlanningStageName.PLANNER
            else ExecutionTerminalResult.BUILDER_COMPLETE.value
        )
        return _runner_result(
            request,
            terminal=terminal,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()

    ideas_inbox = paths.root / "ideas" / "inbox"
    ideas_inbox.mkdir(parents=True, exist_ok=True)
    (ideas_inbox / "idea-001.md").write_text("# Idea 001\n\nPrioritize planning from watcher input.\n", encoding="utf-8")

    outcome = engine.tick()

    assert outcome.stage is PlanningStageName.PLANNER
    assert seen_stages == [PlanningStageName.PLANNER]
    assert any(paths.specs_active_dir.glob("idea-*.md"))


def test_runtime_tick_applies_mailbox_then_watcher_before_no_work_idle(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'daemon'\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()

    config_path.write_text("[runtime]\nrun_style = 'once'\n", encoding="utf-8")
    write_mailbox_command(paths, _mailbox_command("cmd-reload-idle-order", "reload_config"))

    ignored_path = paths.root / "ignored.target"
    ignored_path.write_text("ignore watcher event\n", encoding="utf-8")

    class _FakeWatcherSession:
        mode = WatcherMode.POLL

        def poll_once(self, *, now: datetime | None = None) -> tuple[WatchEvent, ...]:
            del now
            return (
                WatchEvent(
                    target="unknown_target",
                    path=ignored_path,
                    event_kind="changed",
                    observed_at=NOW,
                ),
            )

        def close(self) -> None:
            return

    fake_watcher = _FakeWatcherSession()

    def rebuild_watcher_session() -> None:
        engine._watcher_session = fake_watcher

    engine._watcher_session = fake_watcher
    engine._rebuild_watcher_session = rebuild_watcher_session  # type: ignore[method-assign]

    outcome = engine.tick()
    snapshot = load_snapshot(paths)
    event_types = [event.event_type for event in read_runtime_events(paths)]

    assert outcome.router_decision.reason == "no_work"
    assert snapshot.runtime_mode is RuntimeMode.ONCE
    assert snapshot.last_reload_outcome == "applied"
    assert event_types.index("runtime_config_reloaded") < event_types.index("watcher_event_ignored")
    assert event_types.index("watcher_event_ignored") < event_types.index("watcher_events_consumed")
    assert event_types.index("watcher_events_consumed") < event_types.index("runtime_tick_idle")


def test_runtime_normalize_idea_watch_event_ignores_read_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    idea_path = paths.root / "ideas" / "inbox" / "idea-error.md"
    idea_path.parent.mkdir(parents=True, exist_ok=True)
    idea_path.write_text("# Idea\n", encoding="utf-8")

    original_read_text = Path.read_text

    def flaky_read_text(self: Path, *args, **kwargs):
        if self == idea_path:
            raise OSError("simulated transient read failure")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)
    engine._normalize_idea_watch_event(idea_path)

    assert not any(paths.specs_queue_dir.glob("idea-*.md"))


def test_runtime_normalize_idea_watch_event_writes_root_lineage_fields(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    idea_path = paths.root / "ideas" / "inbox" / "seed-idea.md"
    idea_path.parent.mkdir(parents=True, exist_ok=True)
    idea_path.write_text("# Seed Idea\n\nPreserve root lineage from watcher input.\n", encoding="utf-8")

    engine._normalize_idea_watch_event(idea_path)

    queued_specs = sorted(paths.specs_queue_dir.glob("idea-*.md"))
    assert len(queued_specs) == 1

    spec_path = queued_specs[0]
    spec_text = spec_path.read_text(encoding="utf-8")

    assert f"Root-Idea-ID: {spec_path.stem}" in spec_text
    assert f"Root-Spec-ID: {spec_path.stem}" in spec_text


def test_runtime_tick_handles_active_stage_without_work_item_identity(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    broken_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "active_run_id": "run-broken",
            "active_work_item_kind": None,
            "active_work_item_id": None,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, broken_snapshot)

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    outcome = engine.tick()

    assert outcome.router_decision.reason == "missing_active_work_item_identity"
    snapshot = load_snapshot(paths)
    assert snapshot.active_stage is None
    assert snapshot.active_plane is None


def test_runtime_startup_projects_config_runtime_mode(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'once'\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    snapshot = engine.startup()

    assert snapshot.runtime_mode.value == "once"


def test_runtime_startup_preserves_pause_flag_across_restart(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    snapshot = load_snapshot(paths).model_copy(update={"paused": True, "updated_at": NOW})
    save_snapshot(paths, snapshot)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    started = engine.startup()

    assert started.paused is True


def test_runtime_tick_with_no_work_reports_non_blocked_idle_result(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    outcome = engine.tick()

    assert outcome.router_decision.reason == "no_work"
    assert outcome.stage_result.result_class is ResultClass.SUCCESS
    assert outcome.stage_result.terminal_result is ExecutionTerminalResult.UPDATE_COMPLETE


def test_runtime_tick_with_no_work_suppresses_completion_when_lineage_work_remains(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(
        _task_doc("task-001", created_at=NOW).model_copy(
            update={"root_idea_id": "idea-001", "root_spec_id": "spec-root-001"}
        )
    )
    claimed = queue.claim_next_execution_task()
    assert claimed is not None
    queue.mark_task_blocked("task-001")

    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id="spec-root-001",
            root_idea_id="idea-001",
            root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
            root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
            rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
            latest_verdict_path=None,
            latest_report_path=None,
            closure_open=True,
            closure_blocked_by_lineage_work=False,
            blocking_work_ids=(),
            opened_at=NOW,
        ),
    )

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called while completion is suppressed")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    outcome = engine.tick()
    target = load_closure_target_state(paths, root_spec_id="spec-root-001")

    assert outcome.router_decision.reason == "no_work"
    assert target.closure_blocked_by_lineage_work is True
    assert target.blocking_work_ids == ("task-001",)


def test_runtime_tick_closes_closure_target_on_arbiter_complete(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id="spec-root-001",
            root_idea_id="idea-001",
            root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
            root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
            rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
            latest_verdict_path=None,
            latest_report_path=None,
            closure_open=True,
            closure_blocked_by_lineage_work=False,
            blocking_work_ids=(),
            opened_at=NOW,
        ),
    )

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        verdict_path = Path(request.preferred_verdict_path)
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        verdict_path.write_text('{"status":"pass"}\n', encoding="utf-8")
        report_path = Path(request.preferred_report_path)
        report_path.write_text("# Arbiter Report\n\nParity holds.\n", encoding="utf-8")
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.ARBITER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    outcome = engine.tick()
    target = load_closure_target_state(paths, root_spec_id="spec-root-001")
    report_copy = paths.arbiter_reports_dir / f"{outcome.stage_result.run_id}.md"

    assert outcome.stage is PlanningStageName.ARBITER
    assert outcome.router_decision.reason == "arbiter_complete"
    assert target.closure_open is False
    assert target.closed_at is not None
    assert target.last_arbiter_run_id == outcome.stage_result.run_id
    assert target.latest_verdict_path == "millrace-agents/arbiter/verdicts/spec-root-001.json"
    assert target.latest_report_path == f"millrace-agents/arbiter/reports/{outcome.stage_result.run_id}.md"
    assert report_copy.is_file()


def test_runtime_tick_enqueues_remediation_incident_for_arbiter_gap(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id="spec-root-001",
            root_idea_id="idea-001",
            root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
            root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
            rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
            latest_verdict_path=None,
            latest_report_path=None,
            closure_open=True,
            closure_blocked_by_lineage_work=False,
            blocking_work_ids=(),
            opened_at=NOW,
        ),
    )

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        verdict_path = Path(request.preferred_verdict_path)
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        verdict_path.write_text('{"status":"gap"}\n', encoding="utf-8")
        report_path = Path(request.preferred_report_path)
        report_path.write_text("# Arbiter Report\n\nParity gaps remain.\n", encoding="utf-8")
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.REMEDIATION_NEEDED.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    outcome = engine.tick()
    target = load_closure_target_state(paths, root_spec_id="spec-root-001")
    incident_paths = tuple(paths.incidents_incoming_dir.glob("*.md"))

    assert outcome.stage is PlanningStageName.ARBITER
    assert outcome.router_decision.reason == "arbiter_remediation_needed"
    assert target.closure_open is True
    assert target.closed_at is None
    assert target.last_arbiter_run_id == outcome.stage_result.run_id
    assert target.latest_verdict_path == "millrace-agents/arbiter/verdicts/spec-root-001.json"
    assert target.latest_report_path == f"millrace-agents/arbiter/reports/{outcome.stage_result.run_id}.md"
    assert len(incident_paths) == 1
    incident_text = incident_paths[0].read_text(encoding="utf-8")
    assert "Failure-Class: arbiter_parity_gap" in incident_text
    assert "Root-Spec-ID: spec-root-001" in incident_text
    assert "Root-Idea-ID: idea-001" in incident_text
    assert "Source-Stage: arbiter" in incident_text
    assert "Trigger-Reason: arbiter_remediation_needed" in incident_text


def test_runtime_mailbox_retry_active_requeues_active_item_and_resets_counters(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    claimed = queue.claim_next_execution_task()
    assert claimed is not None

    stale_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "active_run_id": "run-active",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "current_failure_class": "missing_terminal_result",
            "troubleshoot_attempt_count": 2,
            "fix_cycle_count": 1,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, stale_snapshot)
    save_recovery_counters(
        paths,
        RecoveryCounters(
            entries=(
                RecoveryCounterEntry(
                    failure_class="missing_terminal_result",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    troubleshoot_attempt_count=2,
                    fix_cycle_count=1,
                    last_updated_at=NOW,
                ),
                RecoveryCounterEntry(
                    failure_class="other_item",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-keep",
                    troubleshoot_attempt_count=1,
                    last_updated_at=NOW,
                ),
            )
        ),
    )

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    write_mailbox_command(paths, _mailbox_command("cmd-retry-active", "retry_active"))
    engine._drain_mailbox()

    assert (paths.tasks_active_dir / "task-001.md").exists() is False
    assert (paths.tasks_queue_dir / "task-001.md").is_file()
    assert (paths.tasks_queue_dir / "task-001.requeue.jsonl").is_file()

    snapshot = load_snapshot(paths)
    assert snapshot.active_plane is None
    assert snapshot.active_stage is None
    assert snapshot.active_run_id is None
    assert snapshot.active_work_item_kind is None
    assert snapshot.active_work_item_id is None
    assert snapshot.active_since is None
    assert snapshot.current_failure_class is None
    assert snapshot.troubleshoot_attempt_count == 0
    assert snapshot.fix_cycle_count == 0

    persisted_counters = load_recovery_counters(paths)
    assert len(persisted_counters.entries) == 1
    assert persisted_counters.entries[0].work_item_id == "task-keep"


def test_runtime_mailbox_clear_stale_state_requeues_multiple_active_artifacts(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    queue.enqueue_spec(_spec_doc("spec-001", created_at=NOW))
    task_claim = queue.claim_next_execution_task()
    spec_claim = queue.claim_next_planning_item()
    assert task_claim is not None
    assert spec_claim is not None

    stale_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "active_run_id": "run-stale",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "current_failure_class": "stale_active_ownership",
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, stale_snapshot)
    save_recovery_counters(
        paths,
        RecoveryCounters(
            entries=(
                RecoveryCounterEntry(
                    failure_class="stale_active_ownership",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    troubleshoot_attempt_count=1,
                    last_updated_at=NOW,
                ),
            )
        ),
    )

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    write_mailbox_command(paths, _mailbox_command("cmd-clear-stale", "clear_stale_state"))
    engine._drain_mailbox()

    assert (paths.tasks_active_dir / "task-001.md").exists() is False
    assert (paths.specs_active_dir / "spec-001.md").exists() is False
    assert (paths.tasks_queue_dir / "task-001.md").is_file()
    assert (paths.specs_queue_dir / "spec-001.md").is_file()
    assert (paths.tasks_queue_dir / "task-001.requeue.jsonl").is_file()
    assert (paths.specs_queue_dir / "spec-001.requeue.jsonl").is_file()

    snapshot = load_snapshot(paths)
    assert snapshot.active_plane is None
    assert snapshot.active_stage is None
    assert snapshot.active_run_id is None
    assert snapshot.active_work_item_kind is None
    assert snapshot.active_work_item_id is None
    assert snapshot.active_since is None
    assert snapshot.current_failure_class is None

    assert load_recovery_counters(paths).entries == ()


def test_runtime_mailbox_add_task_spec_and_idea_apply_payloads(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    task_doc = _task_doc("task-mailbox-001", created_at=NOW)
    spec_doc = _spec_doc("spec-mailbox-001", created_at=NOW)
    write_mailbox_command(
        paths,
        _mailbox_command(
            "cmd-add-task",
            "add_task",
            payload={"document": task_doc.model_dump(mode="json")},
        ),
    )
    write_mailbox_command(
        paths,
        _mailbox_command(
            "cmd-add-spec",
            "add_spec",
            payload={"document": spec_doc.model_dump(mode="json")},
        ),
    )
    write_mailbox_command(
        paths,
        _mailbox_command(
            "cmd-add-idea",
            "add_idea",
            payload={"source_name": "idea-mailbox-001.md", "markdown": "# Idea Mailbox 001\n"},
        ),
    )

    engine._drain_mailbox()

    assert (paths.tasks_queue_dir / "task-mailbox-001.md").is_file()
    assert (paths.specs_queue_dir / "spec-mailbox-001.md").is_file()
    assert (paths.root / "ideas" / "inbox" / "idea-mailbox-001.md").is_file()

    snapshot = load_snapshot(paths)
    assert snapshot.queue_depth_execution == 1
    assert snapshot.queue_depth_planning == 1


def test_runtime_mailbox_reload_config_applies_updated_runtime_mode(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'daemon'\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()

    config_path.write_text("[runtime]\nrun_style = 'once'\n", encoding="utf-8")
    write_mailbox_command(paths, _mailbox_command("cmd-reload-config", "reload_config"))
    engine._drain_mailbox()

    assert engine.config is not None
    assert engine.config.runtime.run_style is RuntimeMode.ONCE
    snapshot = load_snapshot(paths)
    assert snapshot.runtime_mode is RuntimeMode.ONCE
    assert snapshot.last_reload_outcome == "applied"
    assert snapshot.last_reload_error is None


def test_runtime_mailbox_reload_config_retains_previous_plan_on_compile_failure(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\ndefault_mode = 'standard_plain'\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    snapshot = engine.startup()
    original_compiled_plan_id = snapshot.compiled_plan_id

    config_path.write_text("[runtime]\ndefault_mode = 'missing_mode'\n", encoding="utf-8")
    write_mailbox_command(paths, _mailbox_command("cmd-reload-failed", "reload_config"))
    engine._drain_mailbox()

    reloaded_snapshot = load_snapshot(paths)
    assert reloaded_snapshot.compiled_plan_id == original_compiled_plan_id
    assert reloaded_snapshot.active_mode_id == "standard_plain"
    assert reloaded_snapshot.last_reload_outcome == "failed_retained_previous_plan"
    assert reloaded_snapshot.last_reload_error is not None
    assert "missing_mode" in reloaded_snapshot.last_reload_error
    event_types = [event.event_type for event in read_runtime_events(paths)]
    assert "runtime_config_reload_failed" in event_types


def test_runtime_mailbox_rejects_unsafe_add_payloads(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    unsafe_task = _task_doc("task-safe", created_at=NOW).model_dump(mode="json")
    unsafe_task["task_id"] = "../escape"
    write_mailbox_command(
        paths,
        _mailbox_command(
            "cmd-add-task-unsafe",
            "add_task",
            payload={"document": unsafe_task},
        ),
    )
    write_mailbox_command(
        paths,
        _mailbox_command(
            "cmd-add-idea-unsafe",
            "add_idea",
            payload={"source_name": "../escape.md", "markdown": "# Escape\n"},
        ),
    )

    engine._drain_mailbox()

    assert not (paths.root / "escape.md").exists()
    assert not (paths.root / "ideas" / "escape.md").exists()
    failed_archives = sorted(paths.mailbox_failed_dir.glob("*.json"))
    assert len(failed_archives) >= 2


def test_runtime_startup_compile_failure_raises_typed_runtime_error(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="missing_mode")

    with pytest.raises(RuntimeLifecycleError, match="missing_mode"):
        engine.startup()


def test_runtime_startup_rejects_second_daemon_for_same_workspace(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    first = RuntimeEngine(paths, stage_runner=stage_runner)
    second = RuntimeEngine(paths, stage_runner=stage_runner)

    first.startup()

    with pytest.raises(RuntimeLifecycleError, match="workspace runtime ownership lock") as excinfo:
        second.startup()

    assert isinstance(excinfo.value.__cause__, RuntimeOwnershipLockError)


def test_runtime_startup_lock_contention_does_not_rewrite_compile_artifacts(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    owner = RuntimeEngine(paths, stage_runner=stage_runner)
    contender = RuntimeEngine(paths, stage_runner=stage_runner)
    owner.startup()

    compiled_plan_path = paths.state_dir / "compiled_plan.json"
    diagnostics_path = paths.state_dir / "compile_diagnostics.json"
    compiled_before = compiled_plan_path.read_bytes()
    diagnostics_before = diagnostics_path.read_bytes()

    with pytest.raises(RuntimeLifecycleError, match="workspace runtime ownership lock") as excinfo:
        contender.startup()

    assert isinstance(excinfo.value.__cause__, RuntimeOwnershipLockError)

    assert compiled_plan_path.read_bytes() == compiled_before
    assert diagnostics_path.read_bytes() == diagnostics_before


def test_runtime_startup_allows_independent_daemon_ownership_per_workspace(tmp_path: Path) -> None:
    workspace_a = bootstrap_workspace(workspace_paths(tmp_path / "workspace-a"))
    workspace_b = bootstrap_workspace(workspace_paths(tmp_path / "workspace-b"))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine_a = RuntimeEngine(workspace_a, stage_runner=stage_runner)
    engine_b = RuntimeEngine(workspace_b, stage_runner=stage_runner)
    engine_a.startup()
    engine_b.startup()

    assert workspace_a.runtime_lock_file.is_file()
    assert workspace_b.runtime_lock_file.is_file()


def test_runtime_once_startup_acquires_lock_and_close_releases_it(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'once'\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()
    assert paths.runtime_lock_file.is_file()

    engine.close()
    assert paths.runtime_lock_file.exists() is False


def test_runtime_startup_rejects_run_once_when_workspace_lock_is_already_held(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'once'\n", encoding="utf-8")
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="external-owner",
    )

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)

    with pytest.raises(RuntimeLifecycleError, match="workspace runtime ownership lock") as excinfo:
        engine.startup()

    assert isinstance(excinfo.value.__cause__, RuntimeOwnershipLockError)


def test_runtime_run_once_lock_contention_does_not_rewrite_compile_artifacts(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'once'\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()
    engine.close()

    compiled_plan_path = paths.state_dir / "compiled_plan.json"
    diagnostics_path = paths.state_dir / "compile_diagnostics.json"
    snapshot_path = paths.state_dir / "runtime_snapshot.json"
    compiled_before = compiled_plan_path.read_bytes()
    diagnostics_before = diagnostics_path.read_bytes()
    snapshot_before = snapshot_path.read_bytes()

    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="external-owner",
    )

    contender = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    with pytest.raises(RuntimeLifecycleError, match="workspace runtime ownership lock") as excinfo:
        contender.startup()

    assert isinstance(excinfo.value.__cause__, RuntimeOwnershipLockError)
    assert compiled_plan_path.read_bytes() == compiled_before
    assert diagnostics_path.read_bytes() == diagnostics_before
    assert snapshot_path.read_bytes() == snapshot_before


def test_runtime_tick_stop_releases_daemon_ownership_lock(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    assert paths.runtime_lock_file.is_file()

    write_mailbox_command(paths, _mailbox_command("cmd-stop", "stop"))
    outcome = engine.tick()

    assert outcome.router_decision.reason == "stop_requested"
    assert paths.runtime_lock_file.exists() is False


def test_runtime_tick_stop_without_owned_lock_does_not_release_external_lock(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'once'\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()
    status = inspect_runtime_ownership_lock(paths)
    assert status.record is not None
    assert release_runtime_ownership_lock(
        paths,
        owner_session_id=status.record.owner_session_id,
    )
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="external-owner-replaced",
    )

    write_mailbox_command(paths, _mailbox_command("cmd-stop", "stop"))
    outcome = engine.tick()

    assert outcome.router_decision.reason == "stop_requested"
    assert paths.runtime_lock_file.is_file()


def test_runtime_mailbox_reload_config_retains_lock_when_switching_to_once(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'daemon'\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()
    assert paths.runtime_lock_file.is_file()

    config_path.write_text("[runtime]\nrun_style = 'once'\n", encoding="utf-8")
    write_mailbox_command(paths, _mailbox_command("cmd-reload-once", "reload_config"))
    engine._drain_mailbox()

    assert paths.runtime_lock_file.is_file()
    snapshot = load_snapshot(paths)
    assert snapshot.runtime_mode is RuntimeMode.ONCE


def test_runtime_mailbox_reload_config_retains_lock_when_switching_to_daemon(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'once'\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()
    assert paths.runtime_lock_file.is_file()

    config_path.write_text("[runtime]\nrun_style = 'daemon'\n", encoding="utf-8")
    write_mailbox_command(paths, _mailbox_command("cmd-reload-daemon", "reload_config"))
    engine._drain_mailbox()

    assert paths.runtime_lock_file.is_file()
    snapshot = load_snapshot(paths)
    assert snapshot.runtime_mode is RuntimeMode.DAEMON


def test_clear_stale_state_direct_clears_stale_runtime_ownership_lock(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=999_999_999,
        owner_session_id="stale-owner",
    )
    assert paths.runtime_lock_file.is_file()

    control = RuntimeControl(paths)
    result = control.clear_stale_state(reason="operator stale ownership recovery")

    assert result.applied is True
    assert "runtime_ownership_lock=cleared_stale" in result.detail
    assert paths.runtime_lock_file.exists() is False


def test_clear_stale_state_prefers_direct_path_for_stale_lock_even_if_snapshot_claims_running(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=999_999_999,
        owner_session_id="stale-owner",
    )
    snapshot = load_snapshot(paths)
    save_snapshot(
        paths,
        snapshot.model_copy(
            update={
                "runtime_mode": RuntimeMode.DAEMON,
                "process_running": True,
                "updated_at": NOW,
            }
        ),
    )

    control = RuntimeControl(paths)
    result = control.clear_stale_state(reason="operator stale ownership recovery")

    assert result.mode == "direct"
    assert result.applied is True
    assert paths.runtime_lock_file.exists() is False
