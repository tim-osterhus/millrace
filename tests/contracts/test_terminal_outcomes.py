from __future__ import annotations

import json
from datetime import datetime, timezone

from millrace_ai.contracts import (
    ExecutionTerminalResult,
    Plane,
    ResultClass,
    RuntimeErrorCode,
    RuntimeErrorContext,
    RuntimeFailureOrigin,
    RuntimeMode,
    RuntimeSnapshot,
    StageResultEnvelope,
    TerminalOutcome,
    WatcherMode,
)

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_terminal_outcome_exposes_value_and_dumps_as_string() -> None:
    outcome = TerminalOutcome(ExecutionTerminalResult.BUILDER_COMPLETE)

    assert outcome == "BUILDER_COMPLETE"
    assert outcome.value == "BUILDER_COMPLETE"


def test_stage_result_accepts_builtin_enum_and_custom_terminal_outcome_string() -> None:
    builtin = _stage_result(terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE)
    custom = _stage_result(
        node_id="synthetic_2",
        stage_kind_id="synthetic_builder",
        terminal_result="SYNTHETIC_2_COMPLETE",
        summary_status_marker="### SYNTHETIC_2_COMPLETE",
    )

    assert builtin.terminal_result.value == "BUILDER_COMPLETE"
    assert custom.terminal_result.value == "SYNTHETIC_2_COMPLETE"
    assert json.loads(custom.model_dump_json())["terminal_result"] == "SYNTHETIC_2_COMPLETE"


def test_runtime_snapshot_and_error_context_accept_terminal_outcome_strings() -> None:
    snapshot = RuntimeSnapshot(
        runtime_mode=RuntimeMode.DAEMON,
        process_running=True,
        paused=False,
        active_mode_id="learning_codex",
        execution_loop_id="execution-loop",
        planning_loop_id="planning-loop",
        compiled_plan_id="plan-001",
        compiled_plan_path="millrace-agents/compiled/plan-001.json",
        execution_status_marker="### IDLE",
        planning_status_marker="### IDLE",
        last_terminal_result="SYNTHETIC_2_COMPLETE",
        config_version="1",
        watcher_mode=WatcherMode.OFF,
        updated_at=NOW,
    )
    error_context = RuntimeErrorContext(
        error_code=RuntimeErrorCode.EXECUTION_POST_STAGE_APPLY_FAILED,
        plane=Plane.EXECUTION,
        failed_stage="builder",
        repair_stage="troubleshooter",
        work_item_kind="task",
        work_item_id="task-001",
        run_id="run-001",
        terminal_result=ExecutionTerminalResult.BLOCKED,
        report_path="millrace-agents/runs/run-001/report.md",
        exception_type="RuntimeError",
        exception_message="boom",
        failure_origin=RuntimeFailureOrigin.RUNTIME_PRIMITIVE_EXCEPTION,
        captured_at=NOW,
    )

    assert snapshot.last_terminal_result.value == "SYNTHETIC_2_COMPLETE"
    assert error_context.terminal_result is not None
    assert error_context.terminal_result.value == "BLOCKED"
    assert json.loads(snapshot.model_dump_json())["last_terminal_result"] == "SYNTHETIC_2_COMPLETE"
    assert json.loads(error_context.model_dump_json())["terminal_result"] == "BLOCKED"


def test_terminal_outcome_json_schema_generation_succeeds() -> None:
    for model in (StageResultEnvelope, RuntimeSnapshot, RuntimeErrorContext):
        schema = model.model_json_schema()
        assert isinstance(schema, dict)


def _stage_result(
    *,
    terminal_result: object,
    summary_status_marker: str = "### BUILDER_COMPLETE",
    node_id: str = "builder",
    stage_kind_id: str = "builder",
) -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id="run-001",
        plane="execution",
        stage="builder",
        node_id=node_id,
        stage_kind_id=stage_kind_id,
        work_item_kind="task",
        work_item_id="task-001",
        terminal_result=terminal_result,
        result_class=ResultClass.SUCCESS,
        summary_status_marker=summary_status_marker,
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )
