from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

from millrace_ai.contracts import (
    ExecutionTerminalResult,
    LearningRequestDocument,
    LearningTerminalResult,
    Plane,
    PlanningTerminalResult,
    SpecDocument,
    TaskDocument,
)
from millrace_ai.events import read_runtime_events
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.supervisor import RuntimeDaemonSupervisor, StageWorkerOutcome

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
