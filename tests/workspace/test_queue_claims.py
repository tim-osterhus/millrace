from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.contracts import (
    ActiveRunState,
    MailboxCancelWorkItemPayload,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    RecoveryCounterEntry,
    ResultClass,
    RuntimeErrorCode,
    RuntimeErrorContext,
    RuntimeFailureOrigin,
    RuntimeMode,
    RuntimeSnapshot,
    StageResultEnvelope,
    WatcherMode,
    WorkItemKind,
)
from millrace_ai.contracts.run_trace import RunTraceGraph, RunTraceNode, RunTraceSpawnedWorkRef
from millrace_ai.queue_store import QueueClaim
from millrace_ai.runners import StageRunRequest

NOW = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)


def test_queue_claim_carries_family_id_without_legacy_kind(tmp_path: Path) -> None:
    claim = QueueClaim(
        family_id="custom_family",
        plane=Plane.PLANNING,
        work_item_id="custom-001",
        path=tmp_path / "custom-001.json",
    )

    assert claim.family_id == "custom_family"
    assert claim.work_item_kind is None
    assert claim.plane is Plane.PLANNING


def test_family_id_backfills_legacy_queue_claim_kind(tmp_path: Path) -> None:
    claim = QueueClaim(
        family_id="task",
        work_item_id="task-001",
        path=tmp_path / "task-001.md",
    )

    assert claim.work_item_kind is WorkItemKind.TASK
    assert claim.plane is Plane.EXECUTION


def test_runtime_contracts_can_persist_family_id_without_enum_extension(tmp_path: Path) -> None:
    active_run = ActiveRunState(
        plane=Plane.PLANNING,
        stage=PlanningStageName.PLANNER,
        node_id="custom_node",
        stage_kind_id="custom_stage",
        run_id="run-custom",
        compiled_plan_id="plan-001",
        compiled_plan_fingerprint="fingerprint-001",
        request_kind="active_work_item",
        work_item_family_id="custom_family",
        work_item_id="custom-001",
        active_since=NOW,
    )
    assert active_run.work_item_kind is None
    assert active_run.work_item_family_id == "custom_family"

    snapshot = RuntimeSnapshot(
        runtime_mode=RuntimeMode.DAEMON,
        process_running=True,
        paused=False,
        active_mode_id="custom_mode",
        execution_loop_id="execution.standard",
        planning_loop_id="planning.custom",
        compiled_plan_id="plan-001",
        compiled_plan_fingerprint="fingerprint-001",
        compiled_plan_path="millrace-agents/state/compiled_plan.json",
        active_runs_by_plane={Plane.PLANNING: active_run},
        execution_status_marker="### IDLE",
        planning_status_marker="### MANAGER_RUNNING",
        config_version="1",
        watcher_mode=WatcherMode.OFF,
        updated_at=NOW,
    )
    assert snapshot.active_work_item_family_id == "custom_family"
    assert snapshot.active_work_item_kind is None

    request = StageRunRequest(
        request_id="request-001",
        run_id="run-custom",
        plane=Plane.PLANNING,
        stage=PlanningStageName.PLANNER,
        mode_id="custom_mode",
        compiled_plan_id="plan-001",
        node_id="custom_node",
        stage_kind_id="custom_stage",
        entrypoint_path="entrypoints/custom.md",
        active_work_item_family_id="custom_family",
        active_work_item_id="custom-001",
        active_work_item_path=str(tmp_path / "custom-001.json"),
        run_dir=str(tmp_path / "run-custom"),
        summary_status_path=str(tmp_path / "summary.md"),
        runtime_snapshot_path=str(tmp_path / "runtime_snapshot.json"),
        recovery_counters_path=str(tmp_path / "recovery_counters.json"),
    )
    assert request.active_work_item_family_id == "custom_family"
    assert request.active_work_item_kind is None

    stage_result = StageResultEnvelope(
        run_id="run-custom",
        plane=Plane.PLANNING,
        stage=PlanningStageName.PLANNER,
        node_id="custom_node",
        stage_kind_id="custom_stage",
        work_item_family_id="custom_family",
        work_item_id="custom-001",
        terminal_result=PlanningTerminalResult.PLANNER_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### PLANNER_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )
    assert stage_result.work_item_kind is None
    assert stage_result.work_item_family_id == "custom_family"

    error_context = RuntimeErrorContext(
        error_code=RuntimeErrorCode.PLANNING_POST_STAGE_APPLY_FAILED,
        plane=Plane.PLANNING,
        failed_stage=PlanningStageName.PLANNER,
        repair_stage=PlanningStageName.MECHANIC,
        work_item_family_id="custom_family",
        work_item_id="custom-001",
        run_id="run-custom",
        report_path=str(tmp_path / "runtime_error.json"),
        exception_type="RuntimeError",
        exception_message="boom",
        failure_origin=RuntimeFailureOrigin.RUNTIME_PRIMITIVE_EXCEPTION,
        captured_at=NOW,
    )
    assert error_context.work_item_kind is None
    assert error_context.work_item_family_id == "custom_family"

    recovery_entry = RecoveryCounterEntry(
        failure_class="custom_failure",
        work_item_family_id="custom_family",
        work_item_id="custom-001",
        last_updated_at=NOW,
    )
    assert recovery_entry.work_item_kind is None
    assert recovery_entry.work_item_family_id == "custom_family"

    mailbox_payload = MailboxCancelWorkItemPayload(
        work_item_family_id="custom_family",
        work_item_id="custom-001",
        reason="operator requested cancellation",
    )
    assert mailbox_payload.work_item_kind is None
    assert mailbox_payload.work_item_family_id == "custom_family"

    spawned = RunTraceSpawnedWorkRef(
        family_id="custom_family",
        item_id="custom-002",
        path="millrace-agents/custom/queue/custom-002.json",
    )
    assert spawned.kind is None
    assert spawned.family_id == "custom_family"

    node = RunTraceNode(
        trace_node_id="node-001",
        run_id="run-custom",
        request_id="request-001",
        plane=Plane.PLANNING,
        stage="custom",
        node_id="custom_node",
        stage_kind_id="custom_stage",
        work_item_family_id="custom_family",
        work_item_id="custom-001",
        terminal_result="UPDATE_COMPLETE",
        result_class=ResultClass.SUCCESS,
        started_at=NOW,
        completed_at=NOW,
        duration_seconds=0,
    )
    trace = RunTraceGraph(
        run_id="run-custom",
        run_dir=str(tmp_path / "run-custom"),
        work_item_family_id="custom_family",
        work_item_id="custom-001",
        status="complete",
        nodes=(node,),
        generated_at=NOW,
    )
    assert trace.work_item_kind is None
    assert trace.work_item_family_id == "custom_family"
