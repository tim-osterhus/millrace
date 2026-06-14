from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import millrace_ai.runtime.supervisor as supervisor_module
from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    LearningRequestDocument,
    LearningTerminalResult,
    Plane,
    PlanningTerminalResult,
    SpecDocument,
    TaskDocument,
)
from millrace_ai.events import read_runtime_events
from millrace_ai.mailbox import write_mailbox_command
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.supervisor import RuntimeDaemonSupervisor, StageWorkerOutcome
from millrace_ai.state_store import load_recovery_counters, load_snapshot

NOW = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _task_doc(task_id: str) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="runtime supervisor test",
        target_paths=["src/millrace_ai/runtime/"],
        acceptance=["plane-concurrent supervisor"],
        required_checks=["pytest tests/runtime/test_supervisor.py -q"],
        references=["lab/specs/pending/2026-04-28-millrace-generic-plane-concurrent-runtime-scheduler.md"],
        risk=["scheduler drift"],
        created_at=NOW,
        created_by="tests",
    )


def _task_doc_with_dependency(task_id: str, *, depends_on: tuple[str, ...]) -> TaskDocument:
    return _task_doc(task_id).model_copy(update={"depends_on": depends_on})


def _write_blocked_metadata(
    paths,
    task_id: str,
    *,
    auto_requeue_candidate: bool,
    failure_class: str,
    blocked_at: datetime | None = None,
) -> Path:
    metadata_dir = paths.runtime_root / "diagnostics" / "blocked"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = metadata_dir / f"task-{task_id}.json"
    metadata_path.write_text(
        json.dumps(
            {
                "work_item_kind": "task",
                "work_item_id": task_id,
                "root_spec_id": "spec-root-001",
                "root_idea_id": "idea-001",
                "blocked_at": (blocked_at or (NOW - timedelta(hours=1))).isoformat(),
                "blocked_origin": "runner_failure",
                "failure_class": failure_class,
                "failure_scope": "environment" if auto_requeue_candidate else "semantic",
                "auto_requeue_candidate": auto_requeue_candidate,
                "source_run_id": "run-001",
                "source_plane": "execution",
                "source_stage": "builder",
                "terminal_result": "BLOCKED",
            }
        ),
        encoding="utf-8",
    )
    return metadata_path


def _spec_doc(spec_id: str) -> SpecDocument:
    return SpecDocument(
        spec_id=spec_id,
        title=f"Spec {spec_id}",
        summary="runtime supervisor planning input",
        source_type="manual",
        goals=["prove foreground scheduler priority"],
        constraints=["plane-concurrent runtime"],
        acceptance=["planning and execution do not overlap"],
        references=["lab/specs/pending/2026-04-28-millrace-generic-plane-concurrent-runtime-scheduler.md"],
        created_at=NOW,
        created_by="tests",
    )


def _learning_request_doc(learning_request_id: str) -> LearningRequestDocument:
    return LearningRequestDocument(
        learning_request_id=learning_request_id,
        title=f"Learning {learning_request_id}",
        requested_action="improve",
        target_skill_id="checker-core",
        target_stage="curator",
        created_at=NOW,
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
    terminal: str,
) -> RunnerRawResult:
    run_dir = Path(request.run_dir)
    stdout_path = run_dir / f"{request.request_id}.stdout.txt"
    stdout_path.write_text(f"### {terminal}\n", encoding="utf-8")
    return RunnerRawResult(
        request_id=request.request_id,
        run_id=request.run_id,
        stage=request.stage,
        runner_name=request.runner_name or "test",
        model_name=request.model_name,
        model_reasoning_effort=request.model_reasoning_effort,
        exit_kind="completed",
        exit_code=0,
        stdout_path=str(stdout_path),
        stderr_path=None,
        terminal_result_path=None,
        started_at=NOW,
        ended_at=NOW,
    )


def test_supervisor_dispatches_learning_and_execution_before_either_completes(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001"))
    queue.enqueue_learning_request(_learning_request_doc("learn-001"))
    release_workers = Event()
    both_started = Event()
    started_planes: list[Plane] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        started_planes.append(request.plane)
        if len(started_planes) == 2:
            both_started.set()
        assert release_workers.wait(timeout=5)
        terminal = (
            LearningTerminalResult.CURATOR_COMPLETE.value
            if request.plane is Plane.LEARNING
            else ExecutionTerminalResult.BUILDER_COMPLETE.value
        )
        return _runner_result(request, terminal=terminal)

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="learning_codex")
        engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)

        dispatched = await supervisor.dispatch_ready_work()
        await asyncio.wait_for(asyncio.to_thread(both_started.wait), timeout=5)

        assert dispatched == 2
        assert supervisor.active_worker_lanes == frozenset({"execution.main", "learning.main"})
        assert set(started_planes) == {Plane.EXECUTION, Plane.LEARNING}
        assert set(engine.snapshot.active_runs_by_plane) == {Plane.EXECUTION, Plane.LEARNING}
        stage_started = [
            event
            for event in read_runtime_events(paths)
            if event.event_type == "stage_started"
        ]
        assert {event.data.get("lane_id") for event in stage_started} >= {
            "execution.main",
            "learning.main",
        }

        release_workers.set()
        await supervisor.drain_completed(wait=True)
        engine.close()

    asyncio.run(scenario())


def test_supervisor_auto_requeues_transient_blocked_dependency_on_idle_cycle(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-06"))
    assert queue.claim_next_execution_task() is not None
    queue.mark_task_blocked("task-06")
    _write_blocked_metadata(
        paths,
        "task-06",
        auto_requeue_candidate=True,
        failure_class="network_unavailable",
    )
    queue.enqueue_task(_task_doc_with_dependency("task-07", depends_on=("task-06",)))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError(f"no stage should run before requeue: {request}")

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="default_codex")
        engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)

        completions = await supervisor.run_cycle()

        assert completions == ()
        assert (paths.tasks_queue_dir / "task-06.md").is_file()
        assert not (paths.tasks_blocked_dir / "task-06.md").exists()
        assert (paths.tasks_queue_dir / "task-06.requeue.jsonl").is_file()
        recovery_reports = tuple((paths.runtime_root / "diagnostics" / "auto-recovery").glob("*task-06.json"))
        assert len(recovery_reports) == 1
        engine.close()

    asyncio.run(scenario())


def test_supervisor_does_not_auto_requeue_non_retryable_blocked_dependency(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-06"))
    assert queue.claim_next_execution_task() is not None
    queue.mark_task_blocked("task-06")
    _write_blocked_metadata(
        paths,
        "task-06",
        auto_requeue_candidate=False,
        failure_class="stage_declared_blocked",
    )
    queue.enqueue_task(_task_doc_with_dependency("task-07", depends_on=("task-06",)))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError(f"no stage should run for non-retryable blocked task: {request}")

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="default_codex")
        engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)

        completions = await supervisor.run_cycle()

        assert completions == ()
        assert (paths.tasks_blocked_dir / "task-06.md").is_file()
        assert not (paths.tasks_queue_dir / "task-06.md").exists()
        engine.close()

    asyncio.run(scenario())


def test_supervisor_idle_cycles_share_runtime_idle_suppression_policy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nidle_event_heartbeat_seconds = 3600.0\n", encoding="utf-8")
    clock = [NOW]
    monkeypatch.setattr("millrace_ai.runtime.stage_requests.now", lambda: clock[0])

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage runner should not be called")

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
        engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)

        for _ in range(10):
            completions = await supervisor.run_cycle()
            assert completions == ()
            clock[0] += timedelta(seconds=1)

        idle_events = [
            event for event in read_runtime_events(paths) if event.event_type == "runtime_tick_idle"
        ]
        assert len(idle_events) == 1
        assert idle_events[0].data["reason"] == "no_work"
        assert idle_events[0].data["idle_tick_count"] == 1
        engine.close()

    asyncio.run(scenario())


def test_supervisor_does_not_dispatch_execution_with_planning(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec_doc("spec-001"))
    queue.enqueue_task(_task_doc("task-001"))
    queue.enqueue_learning_request(_learning_request_doc("learn-001"))
    release_workers = Event()
    both_started = Event()
    started_planes: list[Plane] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        started_planes.append(request.plane)
        if len(started_planes) == 2:
            both_started.set()
        assert release_workers.wait(timeout=5)
        terminal = (
            LearningTerminalResult.CURATOR_COMPLETE.value
            if request.plane is Plane.LEARNING
            else PlanningTerminalResult.PLANNER_COMPLETE.value
        )
        return _runner_result(request, terminal=terminal)

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="learning_codex")
        engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)

        dispatched = await supervisor.dispatch_ready_work()
        await asyncio.wait_for(asyncio.to_thread(both_started.wait), timeout=5)

        assert dispatched == 2
        assert started_planes == [Plane.PLANNING, Plane.LEARNING]
        assert set(engine.snapshot.active_runs_by_plane) == {Plane.PLANNING, Plane.LEARNING}

        release_workers.set()
        await supervisor.drain_completed(wait=True)
        engine.close()

    asyncio.run(scenario())


def test_supervisor_workers_return_typed_outcomes(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_learning_request(_learning_request_doc("learn-001"))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(request, terminal=LearningTerminalResult.CURATOR_COMPLETE.value)

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="learning_codex")
        engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)

        dispatched = await supervisor.dispatch_ready_work()
        assert dispatched == 1
        task = next(iter(supervisor._tasks.values()))
        outcome = await task

        assert isinstance(outcome, StageWorkerOutcome)
        assert outcome.plane is Plane.LEARNING
        assert outcome.lane_id == "learning.main"
        assert outcome.raw_result is not None

        await supervisor.drain_completed(wait=False)
        engine.close()

    asyncio.run(scenario())


def test_supervisor_defers_reconciliation_for_lane_with_live_worker(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    QueueStore(paths).enqueue_task(_task_doc("task-001"))
    started = Event()
    release_worker = Event()

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        started.set()
        assert release_worker.wait(timeout=5)
        return _runner_result(request, terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value)

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="default_codex")
        engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)

        try:
            dispatched = await supervisor.dispatch_ready_work()
            assert dispatched == 1
            assert await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=5)

            before_snapshot = load_snapshot(paths)
            before_counters = load_recovery_counters(paths)
            before_active_run = before_snapshot.active_runs_by_plane[Plane.EXECUTION]
            paths.execution_status_file.write_text("### UNKNOWN_TERMINAL\n", encoding="utf-8")

            supervisor._prepare_cycle()

            after_snapshot = load_snapshot(paths)
            after_counters = load_recovery_counters(paths)
            assert supervisor.active_worker_lanes == frozenset({"execution.main"})
            assert after_snapshot.active_runs_by_plane[Plane.EXECUTION] == before_active_run
            assert after_snapshot.active_stage is ExecutionStageName.BUILDER
            assert after_snapshot.active_run_id == before_active_run.run_id
            assert after_counters == before_counters
            assert not any(
                event.event_type == "runtime_reconciled"
                for event in read_runtime_events(paths)
            )
            deferred_events = [
                event
                for event in read_runtime_events(paths)
                if event.event_type == "runtime_reconciliation_deferred"
            ]
            assert len(deferred_events) == 1
            deferred = deferred_events[0].data
            recovery_stage = engine._stage_plan_for(Plane.EXECUTION, ExecutionStageName.TROUBLESHOOTER)
            assert deferred == {
                "signal": "impossible_execution_status_marker",
                "failure_class": "impossible_status_marker",
                "plane": "execution",
                "lane": "execution.main",
                "lane_id": "execution.main",
                "snapshot_active_run_stage": ExecutionStageName.BUILDER.value,
                "snapshot_active_run_node_id": before_active_run.node_id,
                "snapshot_active_run_stage_kind_id": before_active_run.stage_kind_id,
                "snapshot_active_run_id": before_active_run.run_id,
                "active_worker_present": True,
                "active_worker_stage": ExecutionStageName.BUILDER.value,
                "active_worker_node_id": before_active_run.node_id,
                "active_worker_stage_kind_id": before_active_run.stage_kind_id,
                "active_worker_run_id": before_active_run.run_id,
                "action": "deferred_active_worker",
                "recovery_stage": ExecutionStageName.TROUBLESHOOTER.value,
                "recommended_recovery_stage": ExecutionStageName.TROUBLESHOOTER.value,
                "recommended_recovery_node_id": recovery_stage.node_id,
                "recommended_recovery_stage_kind_id": recovery_stage.stage_kind_id,
                "counter_incremented": False,
                "signal_count": 1,
            }
        finally:
            release_worker.set()
            await supervisor.drain_completed(wait=True)
            engine.close()

    asyncio.run(scenario())


def test_supervisor_defers_reconciliation_for_lane_with_pending_completion(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    QueueStore(paths).enqueue_task(_task_doc("task-001"))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(request, terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value)

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="default_codex")
        engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)

        dispatched = await supervisor.dispatch_ready_work()
        assert dispatched == 1
        worker_task = supervisor._tasks["execution.main"]
        await asyncio.wait_for(worker_task, timeout=5)
        assert worker_task.done()
        assert supervisor.active_worker_lanes == frozenset({"execution.main"})

        before_snapshot = load_snapshot(paths)
        before_counters = load_recovery_counters(paths)
        before_active_run = before_snapshot.active_runs_by_plane[Plane.EXECUTION]
        paths.execution_status_file.write_text("### UNKNOWN_TERMINAL\n", encoding="utf-8")

        supervisor._prepare_cycle()

        after_snapshot = load_snapshot(paths)
        after_counters = load_recovery_counters(paths)
        assert supervisor.active_worker_lanes == frozenset({"execution.main"})
        assert after_snapshot.active_runs_by_plane[Plane.EXECUTION] == before_active_run
        assert after_snapshot.active_stage is ExecutionStageName.BUILDER
        assert after_snapshot.active_run_id == before_active_run.run_id
        assert after_counters == before_counters
        assert not any(
            event.event_type == "runtime_reconciled"
            for event in read_runtime_events(paths)
        )
        deferred_events = [
            event
            for event in read_runtime_events(paths)
            if event.event_type == "runtime_reconciliation_deferred"
        ]
        assert len(deferred_events) == 1
        deferred = deferred_events[0].data
        recovery_stage = engine._stage_plan_for(Plane.EXECUTION, ExecutionStageName.TROUBLESHOOTER)
        assert deferred == {
            "signal": "impossible_execution_status_marker",
            "failure_class": "impossible_status_marker",
            "plane": "execution",
            "lane": "execution.main",
            "lane_id": "execution.main",
            "snapshot_active_run_stage": ExecutionStageName.BUILDER.value,
            "snapshot_active_run_node_id": before_active_run.node_id,
            "snapshot_active_run_stage_kind_id": before_active_run.stage_kind_id,
            "snapshot_active_run_id": before_active_run.run_id,
            "active_worker_present": True,
            "active_worker_stage": ExecutionStageName.BUILDER.value,
            "active_worker_node_id": before_active_run.node_id,
            "active_worker_stage_kind_id": before_active_run.stage_kind_id,
            "active_worker_run_id": before_active_run.run_id,
            "action": "deferred_active_worker",
            "recovery_stage": ExecutionStageName.TROUBLESHOOTER.value,
            "recommended_recovery_stage": ExecutionStageName.TROUBLESHOOTER.value,
            "recommended_recovery_node_id": recovery_stage.node_id,
            "recommended_recovery_stage_kind_id": recovery_stage.stage_kind_id,
            "counter_incremented": False,
            "signal_count": 1,
        }

        completions = await supervisor.drain_completed(wait=True)
        failure_classes = {
            event.data.get("failure_class")
            for event in read_runtime_events(paths)
            if isinstance(event.data, dict)
        }
        assert len(completions) == 1
        assert supervisor.active_worker_lanes == frozenset()
        assert "execution_post_stage_apply_failed" not in failure_classes
        engine.close()

    asyncio.run(scenario())


def test_supervisor_updates_plane_indexed_queue_depths_after_completion(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_learning_request(_learning_request_doc("learn-001"))
    queue.enqueue_learning_request(_learning_request_doc("learn-002"))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(request, terminal=LearningTerminalResult.CURATOR_COMPLETE.value)

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="learning_codex")
        engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)

        dispatched = await supervisor.dispatch_ready_work()
        assert dispatched == 1
        await supervisor.drain_completed(wait=True)

        assert engine.snapshot.queue_depth_learning == 1
        assert engine.snapshot.queue_depths_by_plane[Plane.LEARNING] == 1
        engine.close()

    asyncio.run(scenario())


def test_supervisor_applies_completed_workers_before_new_claims(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_learning_request(_learning_request_doc("learn-001"))
    queue.enqueue_learning_request(_learning_request_doc("learn-002"))
    started_ids: list[str | None] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        started_ids.append(request.active_work_item_id)
        return _runner_result(request, terminal=LearningTerminalResult.CURATOR_COMPLETE.value)

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="learning_codex")
        engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)

        first = await supervisor.dispatch_ready_work()
        await asyncio.wait_for(next(iter(supervisor._tasks.values())), timeout=5)
        second = await supervisor.dispatch_ready_work()
        await asyncio.wait_for(next(iter(supervisor._tasks.values())), timeout=5)

        assert first == 1
        assert second == 1
        assert started_ids == ["learn-001", "learn-002"]
        assert set(engine.snapshot.active_runs_by_plane) == {Plane.LEARNING}
        assert engine.snapshot.active_runs_by_plane[Plane.LEARNING].work_item_id == "learn-002"

        await supervisor.drain_completed(wait=True)
        engine.close()

    asyncio.run(scenario())


def test_supervisor_canceled_worker_releases_active_run_state(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    QueueStore(paths).enqueue_task(_task_doc("task-001"))
    started = Event()
    release_worker = Event()

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        started.set()
        release_worker.wait(timeout=0.25)
        return _runner_result(request, terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value)

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner)
        engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)

        dispatched = await supervisor.dispatch_ready_work()
        assert dispatched == 1
        await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=5)

        task = next(iter(supervisor._tasks.values()))
        task.cancel()
        completions = await supervisor.drain_completed(wait=True)

        snapshot = load_snapshot(paths)
        assert completions == ()
        assert supervisor.active_worker_lanes == frozenset()
        assert snapshot.active_runs_by_plane == {}
        assert snapshot.active_stage is None
        assert snapshot.active_run_id is None

        release_worker.set()
        engine.close()

    asyncio.run(scenario())


def test_supervisor_canceled_worker_cleans_active_run_metadata_and_preserves_active_item(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    QueueStore(paths).enqueue_task(_task_doc("task-001"))
    started = Event()
    release_worker = Event()

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        started.set()
        release_worker.wait(timeout=0.25)
        return _runner_result(request, terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value)

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner)
        engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)

        dispatched = await supervisor.dispatch_ready_work()
        assert dispatched == 1
        await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=5)

        task = next(iter(supervisor._tasks.values()))
        task.cancel()
        await supervisor.drain_completed(wait=True)

        events = read_runtime_events(paths)
        assert (paths.tasks_active_dir / "task-001.md").is_file()
        assert not (paths.tasks_done_dir / "task-001.md").exists()
        assert not (paths.tasks_blocked_dir / "task-001.md").exists()
        assert not any(event.event_type == "stage_completed" for event in events)

        release_worker.set()
        engine.close()

    asyncio.run(scenario())


def test_supervisor_canceled_execution_worker_preserves_learning_failure_class(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001"))
    queue.enqueue_learning_request(_learning_request_doc("learn-001"))
    execution_started = Event()
    release_execution = Event()

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        if request.plane is Plane.LEARNING:
            raise OSError("network unavailable")
        execution_started.set()
        assert release_execution.wait(timeout=5)
        return _runner_result(request, terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value)

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="learning_codex")
        engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)

        dispatched = await supervisor.dispatch_ready_work()
        assert dispatched == 2
        assert await asyncio.wait_for(asyncio.to_thread(execution_started.wait), timeout=5)

        learning_task = supervisor._tasks["learning.main"]
        await asyncio.wait_for(learning_task, timeout=5)
        completions = await supervisor.drain_completed(wait=False)

        assert len(completions) == 1
        assert completions[0].stage_result.plane is Plane.LEARNING
        assert completions[0].stage_result.metadata["failure_class"] == "network_unavailable"
        assert completions[0].router_decision.failure_class == "network_unavailable"
        snapshot = load_snapshot(paths)
        assert Plane.EXECUTION in snapshot.active_runs_by_plane
        assert snapshot.current_failure_class == "network_unavailable"

        execution_task = supervisor._tasks["execution.main"]
        execution_task.cancel()
        cancelled = await supervisor.drain_completed(wait=True)

        snapshot_after_cancel = load_snapshot(paths)
        assert cancelled == ()
        assert Plane.EXECUTION not in snapshot_after_cancel.active_runs_by_plane
        assert snapshot_after_cancel.current_failure_class == "network_unavailable"

        release_execution.set()
        engine.close()

    asyncio.run(scenario())


def test_supervisor_runner_raised_cancelled_error_is_treated_as_worker_failure(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    QueueStore(paths).enqueue_task(_task_doc("task-001"))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise asyncio.CancelledError("runner cancelled")

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner)
        engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)

        dispatched = await supervisor.dispatch_ready_work()
        assert dispatched == 1
        completions = await supervisor.drain_completed(wait=True)
        events = read_runtime_events(paths)

        assert len(completions) == 1
        assert completions[0].stage_result.metadata["failure_class"] == "runtime_primitive_exception"
        assert any(
            event.event_type == "runtime_worker_exception"
            and event.data.get("exception_type") == "CancelledError"
            for event in events
        )
        assert not any(event.event_type == "runtime_worker_cancelled" for event in events)
        engine.close()

    asyncio.run(scenario())


def test_supervisor_reload_promotes_compiled_plan_after_completion_boundary(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\ndefault_mode = 'learning_codex'\n", encoding="utf-8")
    QueueStore(paths).enqueue_learning_request(_learning_request_doc("learn-001"))
    started = Event()
    release_worker = Event()

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        started.set()
        assert release_worker.wait(timeout=5)
        return _runner_result(request, terminal=LearningTerminalResult.CURATOR_COMPLETE.value)

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
        startup_snapshot = engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)

        dispatched = await supervisor.dispatch_ready_work()
        assert dispatched == 1
        await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=5)

        config_path.write_text("[runtime]\ndefault_mode = 'default_codex'\n", encoding="utf-8")
        write_mailbox_command(paths, _mailbox_command("cmd-reload", "reload_config"))

        worker_task = next(iter(supervisor._tasks.values()))
        release_worker.set()
        await asyncio.wait_for(worker_task, timeout=5)

        completions = await supervisor.run_cycle()
        snapshot = load_snapshot(paths)
        events = read_runtime_events(paths)

        assert len(completions) == 1
        assert snapshot.compiled_plan_id != startup_snapshot.compiled_plan_id
        assert snapshot.pending_compiled_plan_id is None
        assert snapshot.learning_loop_id != startup_snapshot.learning_loop_id
        stage_completed_index = next(
            index for index, event in enumerate(events) if event.event_type == "stage_completed"
        )
        reload_index = next(
            index for index, event in enumerate(events) if event.event_type == "runtime_config_reloaded"
        )
        assert stage_completed_index < reload_index
        engine.close()

    asyncio.run(scenario())


def test_supervisor_applies_completion_against_launch_compiled_plan_after_reload(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    QueueStore(paths).enqueue_task(_task_doc("task-001"))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        assert request.stage is ExecutionStageName.BUILDER
        return _runner_result(request, terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value)

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner)
        engine.startup()
        assert engine.compiled_plan is not None
        supervisor = RuntimeDaemonSupervisor(engine)

        dispatched = await supervisor.dispatch_ready_work()
        assert dispatched == 1
        active_run = next(iter(supervisor._task_active_runs.values()))

        builder_complete = next(
            transition
            for transition in engine.compiled_plan.execution_graph.compiled_transitions
            if transition.source_node_id == "builder"
            and transition.outcome == ExecutionTerminalResult.BUILDER_COMPLETE.value
        )
        engine.compiled_plan = engine.compiled_plan.model_copy(
            update={
                "compiled_plan_id": f"{engine.compiled_plan.compiled_plan_id}-reloaded",
                "execution_graph": engine.compiled_plan.execution_graph.model_copy(
                    update={
                        "compiled_transitions": tuple(
                            transition.model_copy(update={"target_node_id": "updater"})
                            if transition == builder_complete
                            else transition
                            for transition in engine.compiled_plan.execution_graph.compiled_transitions
                        )
                    }
                ),
            }
        )

        completions = await supervisor.drain_completed(wait=True)

        assert len(completions) == 1
        assert completions[0].router_decision.next_stage is ExecutionStageName.CHECKER
        assert active_run.compiled_plan_id != engine.compiled_plan.compiled_plan_id
        engine.close()

    asyncio.run(scenario())


def test_supervisor_completion_runtime_effects_drain_once_across_reload_and_stop_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\ndefault_mode = 'learning_codex'\n", encoding="utf-8")
    QueueStore(paths).enqueue_learning_request(_learning_request_doc("learn-001"))
    calls = {"count": 0}
    original_apply = supervisor_module.apply_runtime_effect_for_stage_result

    def counted_apply(*args, **kwargs):
        calls["count"] += 1
        return original_apply(*args, **kwargs)

    monkeypatch.setattr(supervisor_module, "apply_runtime_effect_for_stage_result", counted_apply)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(request, terminal=LearningTerminalResult.CURATOR_COMPLETE.value)

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
        engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)

        assert await supervisor.dispatch_ready_work() == 1
        worker_task = next(iter(supervisor._tasks.values()))
        await asyncio.wait_for(worker_task, timeout=5)

        config_path.write_text("[runtime]\ndefault_mode = 'default_codex'\n", encoding="utf-8")
        write_mailbox_command(paths, _mailbox_command("cmd-reload", "reload_config"))
        write_mailbox_command(paths, _mailbox_command("cmd-stop", "stop"))

        completions = await supervisor.run_cycle()
        second_pass = await supervisor.drain_completed(wait=False)
        events = read_runtime_events(paths)

        assert len(completions) == 1
        assert second_pass == ()
        assert calls["count"] == 1
        assert sum(1 for event in events if event.event_type == "stage_completed") == 1
        assert load_snapshot(paths).process_running is False
        engine.close()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Config-driven behavior tests: Learning request creation via supervisor
# ---------------------------------------------------------------------------


def test_supervisor_learning_disabled_mode_creates_no_learning_request(
    tmp_path: Path,
) -> None:
    """With a learning-disabled mode config, the supervisor does not create
    Learning requests when execution stages complete.

    Config asset: assets/modes/default_codex.json
    """
    paths = _workspace(tmp_path)
    QueueStore(paths).enqueue_task(_task_doc("task-001"))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(request, terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value)

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="default_codex")
        engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)

        dispatched = await supervisor.dispatch_ready_work()
        assert dispatched == 1

        completions = await supervisor.drain_completed(wait=True)
        events = read_runtime_events(paths)

        assert len(completions) == 1
        # No learning_request_enqueued events
        assert not any(
            event.event_type == "learning_request_enqueued" for event in events
        )
        engine.close()

    asyncio.run(scenario())


def test_supervisor_learning_enabled_mode_creates_learning_request(
    tmp_path: Path,
) -> None:
    """With a learning-enabled mode config, the supervisor processes
    completions through the correct config path.  When a stage result
    matches learning trigger rules, Learning requests are created.

    Config asset: assets/modes/learning_codex.json
    """
    paths = _workspace(tmp_path)
    QueueStore(paths).enqueue_task(_task_doc("task-001"))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(request, terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value)

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="learning_codex")
        engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)

        dispatched = await supervisor.dispatch_ready_work()
        assert dispatched == 1

        completions = await supervisor.drain_completed(wait=True)

        assert len(completions) == 1
        # The completion was processed through the learning-enabled config path.
        # BUILDER_COMPLETE is not a learning trigger in learning_codex, so
        # no learning request is expected.  But the supervisor runs with the
        # correct mode config.
        assert completions[0].stage_result.plane is Plane.EXECUTION
        engine.close()

    asyncio.run(scenario())
