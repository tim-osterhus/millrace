from __future__ import annotations

from datetime import datetime, timezone

from millrace_ai.contracts import (
    ActiveRunState,
    ExecutionStageName,
    LearningStageName,
    LearningTerminalResult,
    Plane,
    ResultClass,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.events import read_runtime_events
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.active_runs import snapshot_without_active_plane
from millrace_ai.runtime.learning_promotions import (
    apply_deferred_learning_promotions_if_safe,
    handle_learning_curator_promotion_boundary,
)

NOW = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _unused_runner(request):  # pragma: no cover - should not execute
    raise AssertionError(f"unexpected runner call: {request}")


def _curator_result() -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id="run-learning",
        plane=Plane.LEARNING,
        stage=LearningStageName.CURATOR,
        node_id="curator",
        stage_kind_id="curator",
        work_item_kind=WorkItemKind.LEARNING_REQUEST,
        work_item_id="learn-001",
        terminal_result=LearningTerminalResult.CURATOR_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### CURATOR_COMPLETE",
        success=True,
        retryable=False,
        exit_code=0,
        duration_seconds=1,
        artifact_paths=("skill_update.checker-core.json",),
        started_at=NOW,
        completed_at=NOW,
    )


def test_curator_promotion_defers_until_foreground_planes_drain(tmp_path) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_runner, mode_id="learning_codex")
    engine.startup()
    assert engine.snapshot is not None
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "active_runs_by_plane": {
                Plane.EXECUTION: ActiveRunState(
                    plane=Plane.EXECUTION,
                    stage=ExecutionStageName.BUILDER,
                    node_id="builder",
                    stage_kind_id="builder",
                    run_id="run-execution",
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
                    run_id="run-learning",
                    request_kind="learning_request",
                    work_item_kind=WorkItemKind.LEARNING_REQUEST,
                    work_item_id="learn-001",
                    active_since=NOW,
                ),
            }
        }
    )

    handle_learning_curator_promotion_boundary(engine, stage_result=_curator_result())

    deferred = tuple((paths.learning_update_candidates_dir / "deferred").glob("*.json"))
    assert len(deferred) == 1
    assert not tuple((paths.learning_update_candidates_dir / "applied").glob("*.json"))
    assert read_runtime_events(paths)[-1].event_type == "learning_curator_promotion_deferred"

    engine.snapshot = snapshot_without_active_plane(
        engine.snapshot,
        plane=Plane.EXECUTION,
        now=NOW,
        current_failure_class=None,
    )
    applied_count = apply_deferred_learning_promotions_if_safe(engine)

    assert applied_count == 1
    assert not tuple((paths.learning_update_candidates_dir / "deferred").glob("*.json"))
    assert len(tuple((paths.learning_update_candidates_dir / "applied").glob("*.json"))) == 1
    assert read_runtime_events(paths)[-1].event_type == "learning_curator_promotion_applied"
