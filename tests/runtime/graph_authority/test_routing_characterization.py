from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    Plane,
    RecoveryCounters,
    ResultClass,
    RuntimeSnapshot,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runtime.graph_authority import route_stage_result_from_graph

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_default_execution_graph_routes_builder_complete_to_checker(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.active_plan is not None
    snapshot = RuntimeSnapshot(
        runtime_mode="daemon",
        process_running=True,
        paused=False,
        active_mode_id="default_codex",
        execution_loop_id="execution.standard",
        planning_loop_id="planning.standard",
        compiled_plan_id=outcome.active_plan.compiled_plan_id,
        compiled_plan_fingerprint="fingerprint-001",
        compiled_plan_path="state/compiled_plan.json",
        active_plane=Plane.EXECUTION,
        active_stage=ExecutionStageName.BUILDER,
        active_node_id="builder",
        active_stage_kind_id="builder",
        active_run_id="run-001",
        active_work_item_kind=WorkItemKind.TASK,
        active_work_item_id="task-001",
        execution_status_marker="### BUILDER_RUNNING",
        planning_status_marker="### IDLE",
        config_version="cfg-001",
        watcher_mode="off",
        updated_at=NOW,
    )
    stage_result = StageResultEnvelope(
        run_id="run-001",
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.BUILDER,
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )

    decision = route_stage_result_from_graph(
        outcome.active_plan,
        snapshot,
        stage_result,
        RecoveryCounters(),
    )

    assert decision.action.value == "run_stage"
    assert decision.next_plane is Plane.EXECUTION
    assert decision.next_stage is ExecutionStageName.CHECKER
    assert decision.next_node_id == "checker"
    assert decision.next_stage_kind_id == "checker"
    assert decision.reason == "builder:BUILDER_COMPLETE"
