from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.contracts import (
    ActiveRunState,
    ExecutionStageName,
    LearningRequestDocument,
    LearningStageName,
    LearningTerminalResult,
    Plane,
    ResultClass,
    StageResultEnvelope,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.result_application import apply_router_decision
from millrace_ai.runtime.work_item_transitions import apply_idle_router_decision
from millrace_ai.state_store import load_snapshot, save_snapshot

NOW = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _unused_stage_runner(request: StageRunRequest) -> RunnerRawResult:
    raise AssertionError("stage runner should not be called")


def _task_doc(task_id: str) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="runtime result application test",
        target_paths=["src/millrace_ai/runtime/"],
        acceptance=["lane-aware result application"],
        required_checks=["pytest tests/runtime/test_result_application.py -q"],
        references=["lab/specs/pending/2026-04-28-millrace-generic-plane-concurrent-runtime-scheduler.md"],
        risk=["active state drift"],
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


def test_learning_idle_result_does_not_clear_active_execution_lane(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001"))
    queue.enqueue_learning_request(_learning_request_doc("learn-001"))
    task_claim = queue.claim_next_execution_task()
    learning_claim = queue.claim_next_learning_request()
    assert task_claim is not None
    assert learning_claim is not None

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="learning_codex")
    engine.startup()
    assert engine.snapshot is not None
    snapshot = engine.snapshot.model_copy(
        update={
            "active_runs_by_plane": {
                Plane.EXECUTION: ActiveRunState(
                    plane=Plane.EXECUTION,
                    stage=ExecutionStageName.BUILDER,
                    node_id="builder",
                    stage_kind_id="builder",
                    run_id="run-exec",
                    request_kind="active_work_item",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    active_since=NOW,
                ),
                Plane.LEARNING: ActiveRunState(
                    plane=Plane.LEARNING,
                    stage=LearningStageName.CURATOR,
                    node_id="curator",
                    stage_kind_id="curator",
                    run_id="run-learn",
                    request_kind="learning_request",
                    work_item_kind=WorkItemKind.LEARNING_REQUEST,
                    work_item_id="learn-001",
                    active_since=NOW,
                ),
            },
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "active_node_id": "builder",
            "active_stage_kind_id": "builder",
            "active_run_id": "run-exec",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    engine.snapshot = snapshot
    save_snapshot(paths, snapshot)
    stage_result = StageResultEnvelope(
        run_id="run-learn",
        plane="learning",
        stage="curator",
        node_id="curator",
        stage_kind_id="curator",
        work_item_kind="learning_request",
        work_item_id="learn-001",
        terminal_result=LearningTerminalResult.CURATOR_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### CURATOR_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )

    apply_idle_router_decision(engine, stage_result)

    updated = load_snapshot(paths)
    assert set(updated.active_runs_by_plane) == {Plane.EXECUTION}
    assert updated.active_runs_by_plane[Plane.EXECUTION].work_item_id == "task-001"
    assert updated.active_plane is Plane.EXECUTION
    assert updated.active_stage is ExecutionStageName.BUILDER
    assert (paths.learning_requests_done_dir / "learn-001.md").is_file()
    assert (paths.tasks_active_dir / "task-001.md").is_file()


def test_execution_run_stage_result_does_not_clear_active_learning_lane(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001"))
    queue.enqueue_learning_request(_learning_request_doc("learn-001"))
    assert queue.claim_next_execution_task() is not None
    assert queue.claim_next_learning_request() is not None

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="learning_codex")
    engine.startup()
    assert engine.snapshot is not None
    snapshot = engine.snapshot.model_copy(
        update={
            "active_runs_by_plane": {
                Plane.EXECUTION: ActiveRunState(
                    plane=Plane.EXECUTION,
                    stage=ExecutionStageName.BUILDER,
                    node_id="builder",
                    stage_kind_id="builder",
                    run_id="run-exec",
                    request_kind="active_work_item",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    active_since=NOW,
                ),
                Plane.LEARNING: ActiveRunState(
                    plane=Plane.LEARNING,
                    stage=LearningStageName.CURATOR,
                    node_id="curator",
                    stage_kind_id="curator",
                    run_id="run-learn",
                    request_kind="learning_request",
                    work_item_kind=WorkItemKind.LEARNING_REQUEST,
                    work_item_id="learn-001",
                    active_since=NOW,
                ),
            },
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "active_node_id": "builder",
            "active_stage_kind_id": "builder",
            "active_run_id": "run-exec",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    engine.snapshot = snapshot
    save_snapshot(paths, snapshot)
    stage_result = StageResultEnvelope(
        run_id="run-exec",
        plane="execution",
        stage="builder",
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind="task",
        work_item_id="task-001",
        terminal_result="BUILDER_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )
    decision = RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=Plane.EXECUTION,
        next_stage=ExecutionStageName.CHECKER,
        next_node_id="checker",
        next_stage_kind_id="checker",
        reason="builder:BUILDER_COMPLETE",
    )

    apply_router_decision(engine, decision, stage_result)

    assert engine.snapshot.active_runs_by_plane[Plane.EXECUTION].stage is ExecutionStageName.CHECKER
    assert engine.snapshot.active_runs_by_plane[Plane.LEARNING].stage is LearningStageName.CURATOR
    assert engine.snapshot.active_runs_by_plane[Plane.LEARNING].work_item_id == "learn-001"
