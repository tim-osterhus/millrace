from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.contracts import (
    ExecutionStageName,
    Plane,
    PlanningStageName,
    RecoveryCounters,
    ResultClass,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.result_counters import increment_route_counters
from millrace_ai.state_store import load_recovery_counters, save_recovery_counters, save_snapshot

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _unused_stage_runner(request: StageRunRequest) -> RunnerRawResult:
    raise AssertionError("stage runner should not be called")


def test_route_counter_mutation_uses_declared_counter_intent(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    assert engine.snapshot is not None

    snapshot = engine.snapshot.model_copy(
        update={
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "active_node_id": "builder",
            "active_stage_kind_id": "builder",
            "active_run_id": "run-001",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "updated_at": NOW,
        }
    )
    engine.snapshot = snapshot
    engine.counters = RecoveryCounters()
    save_snapshot(paths, snapshot)
    save_recovery_counters(paths, engine.counters)

    decision = RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=Plane.EXECUTION,
        next_stage=ExecutionStageName.TROUBLESHOOTER,
        next_node_id="custom_recovery_node",
        next_stage_kind_id="troubleshooter",
        reason="custom_recovery",
        failure_class="custom_failure",
        counter_mutation_name="troubleshoot_attempt_count",
    )

    updated = increment_route_counters(engine, snapshot, decision, _stage_result())

    assert updated.troubleshoot_attempt_count == 1
    counters = load_recovery_counters(paths)
    assert len(counters.entries) == 1
    assert counters.entries[0].failure_class == "custom_failure"
    assert counters.entries[0].troubleshoot_attempt_count == 1


def test_route_counter_mutation_does_not_infer_from_next_stage_name(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    assert engine.snapshot is not None

    snapshot = engine.snapshot.model_copy(
        update={
            "active_plane": Plane.PLANNING,
            "active_stage": PlanningStageName.PLANNER,
            "active_node_id": "planner",
            "active_stage_kind_id": "planner",
            "active_run_id": "run-001",
            "active_work_item_kind": WorkItemKind.SPEC,
            "active_work_item_id": "spec-001",
            "updated_at": NOW,
        }
    )
    engine.snapshot = snapshot
    engine.counters = RecoveryCounters()
    save_snapshot(paths, snapshot)
    save_recovery_counters(paths, engine.counters)

    decision = RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=Plane.PLANNING,
        next_stage=PlanningStageName.MECHANIC,
        next_node_id="mechanic",
        next_stage_kind_id="mechanic",
        reason="legacy_stage_name_only",
        failure_class="custom_failure",
    )

    updated = increment_route_counters(engine, snapshot, decision, _stage_result())

    assert updated.mechanic_attempt_count == 0
    assert load_recovery_counters(paths).entries == ()


def _stage_result() -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id="run-001",
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.BUILDER,
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        terminal_result="BLOCKED",
        result_class=ResultClass.BLOCKED,
        summary_status_marker="### BLOCKED",
        success=False,
        started_at=NOW,
        completed_at=NOW,
    )
