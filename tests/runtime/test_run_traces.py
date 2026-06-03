from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from millrace_ai.architecture import WorkItemFamilyDefinition
from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    ResultClass,
    StageResultEnvelope,
    TaskDocument,
    TokenUsage,
    WorkItemKind,
)
from millrace_ai.contracts.run_trace import RunTraceGraph
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.run_inspection import inspect_run_trace, inspect_run_trace_id
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.run_traces import (
    record_router_decision_trace,
    spawned_work_ref_from_path,
    upsert_stage_result_trace_node,
)

NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)


def _task_doc(task_id: str) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="run trace task",
        target_paths=["src/millrace_ai/runtime/"],
        acceptance=["run traces are written"],
        required_checks=["pytest tests/runtime/test_run_traces.py -q"],
        references=["lab/specs/pending/2026-05-04-millrace-compiled-stage-graph-and-run-trace-artifacts.md"],
        risk=["trace drift"],
        created_at=NOW,
        created_by="tests",
    )


def _runner_result(
    request: StageRunRequest,
    *,
    terminal_result: str = "BUILDER_COMPLETE",
) -> RunnerRawResult:
    run_dir = Path(request.run_dir)
    stdout_path = run_dir / "runner_stdout.txt"
    stdout_path.write_text(f"### {terminal_result}\n", encoding="utf-8")
    return RunnerRawResult(
        request_id=request.request_id,
        run_id=request.run_id,
        stage=request.stage,
        runner_name=request.runner_name or "test-runner",
        model_name=request.model_name,
        exit_kind="completed",
        exit_code=0,
        stdout_path=str(stdout_path),
        stderr_path=None,
        terminal_result_path=None,
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=2),
    )


def test_inspect_run_trace_derives_fallback_from_stage_results(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-fallback"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)
    stage_result = StageResultEnvelope(
        run_id="run-fallback",
        plane="execution",
        stage=ExecutionStageName.BUILDER,
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        runner_name="codex_cli",
        model_name="gpt-5.4",
        token_usage=TokenUsage(
            input_tokens=10,
            cached_input_tokens=2,
            output_tokens=4,
            thinking_tokens=1,
            total_tokens=14,
        ),
        metadata={"request_id": "request-001", "compiled_plan_id": "plan-001"},
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=2),
        duration_seconds=2,
    )
    (stage_results_dir / "request-001.json").write_text(
        stage_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    trace = inspect_run_trace(run_dir)

    assert trace.kind == "run_trace_graph"
    assert trace.status == "incomplete"
    assert trace.run_id == "run-fallback"
    assert trace.compiled_plan_id == "plan-001"
    assert trace.nodes[0].trace_node_id == "request-001"
    assert trace.nodes[0].terminal_result == "BUILDER_COMPLETE"
    assert trace.nodes[0].token_usage is not None
    assert trace.edges == ()
    assert "derived from stage result artifacts" in trace.notes


def test_runtime_tick_writes_run_trace_node_and_router_edge(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    QueueStore(paths).enqueue_task(_task_doc("task-001"))

    engine = RuntimeEngine(
        paths,
        stage_runner=lambda request: _runner_result(request),
        mode_id="default_codex",
    )
    engine.startup()
    outcome = engine.tick()

    trace_path = Path(outcome.stage_result_path).parents[1] / "run_trace.json"
    assert trace_path.is_file()
    trace = RunTraceGraph.model_validate_json(trace_path.read_text(encoding="utf-8"))
    assert trace.run_id == outcome.stage_result.run_id
    assert trace.status == "active"
    assert trace.nodes[0].stage == "builder"
    assert trace.nodes[0].terminal_result == "BUILDER_COMPLETE"
    assert trace.nodes[0].artifacts
    assert trace.edges[0].source_trace_node_id == trace.nodes[0].trace_node_id
    assert trace.edges[0].outcome == "BUILDER_COMPLETE"
    assert trace.edges[0].target_node_id == "checker"

    inspected = inspect_run_trace_id(paths, trace.run_id)
    assert inspected == trace


def test_inspect_run_trace_falls_back_when_trace_json_is_malformed(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-malformed"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_trace.json").write_text("{bad\n", encoding="utf-8")
    stage_result = StageResultEnvelope(
        run_id="run-malformed",
        plane="execution",
        stage=ExecutionStageName.BUILDER,
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        metadata={"request_id": "request-001"},
        started_at=NOW,
        completed_at=NOW,
    )
    (stage_results_dir / "request-001.json").write_text(
        stage_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    trace = inspect_run_trace(run_dir)

    assert trace.status == "malformed"
    assert any("run_trace.json malformed" in note for note in trace.notes)
    assert trace.nodes[0].trace_node_id == "request-001"


def test_record_router_decision_trace_includes_spawned_learning_request(
    tmp_path: Path,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    run_dir = paths.runs_dir / "run-spawned"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)
    stage_result_path = stage_results_dir / "request-001.json"
    stage_result = StageResultEnvelope(
        run_id="run-spawned",
        plane="execution",
        stage=ExecutionStageName.BUILDER,
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        metadata={"request_id": "request-001"},
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=2),
        duration_seconds=2,
    )
    stage_result_path.write_text(stage_result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    upsert_stage_result_trace_node(
        paths,
        run_dir=run_dir,
        stage_result=stage_result,
        stage_result_path=stage_result_path,
    )
    learning_request_path = paths.learning_requests_queue_dir / "learn-001.md"
    learning_request_path.parent.mkdir(parents=True, exist_ok=True)
    learning_request_path.write_text("# Learn\n", encoding="utf-8")

    record_router_decision_trace(
        paths,
        run_dir=run_dir,
        stage_result=stage_result,
        decision=RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=None,
            next_stage=ExecutionStageName.CHECKER,
            next_node_id="checker",
            reason="builder_complete",
        ),
        spawned_work=(
            spawned_work_ref_from_path(
                learning_request_path,
                source_stage_result=stage_result,
                reason="learning_trigger",
            ),
        ),
    )

    trace = RunTraceGraph.model_validate_json(
        (run_dir / "run_trace.json").read_text(encoding="utf-8")
    )
    assert trace.edges[0].target_node_id == "checker"
    assert trace.edges[0].spawned_work[0].kind == "learning_request"
    assert trace.edges[0].spawned_work[0].item_id == "learn-001"
    assert trace.edges[0].spawned_work[0].reason == "learning_trigger"


def test_router_decision_trace_records_resolved_terminal_action_metadata(
    tmp_path: Path,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    run_dir = paths.runs_dir / "run-terminal-action"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)
    stage_result_path = stage_results_dir / "request-001.json"
    stage_result = StageResultEnvelope(
        run_id="run-terminal-action",
        plane="execution",
        stage=ExecutionStageName.UPDATER,
        node_id="updater",
        stage_kind_id="updater",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        terminal_result=ExecutionTerminalResult.UPDATE_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### UPDATE_COMPLETE",
        success=True,
        metadata={"request_id": "request-001"},
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=2),
        duration_seconds=2,
    )
    stage_result_path.write_text(stage_result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    upsert_stage_result_trace_node(
        paths,
        run_dir=run_dir,
        stage_result=stage_result,
        stage_result_path=stage_result_path,
    )

    record_router_decision_trace(
        paths,
        run_dir=run_dir,
        stage_result=stage_result,
        decision=RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="updater_complete",
            terminal_state_id="update_complete",
            terminal_action_id="complete_work_item",
            terminal_action_router_consequence="idle",
            runtime_operation_id="recon.enqueue_task",
            lifecycle_mutation_plan_id="complete_work_item",
            lifecycle_action_id="complete",
            terminal_writes_status="UPDATE_COMPLETE",
        ),
    )

    trace = RunTraceGraph.model_validate_json(
        (run_dir / "run_trace.json").read_text(encoding="utf-8")
    )
    edge = trace.edges[0]
    assert edge.terminal_state_id == "update_complete"
    assert edge.terminal_action_id == "complete_work_item"
    assert edge.terminal_action_router_consequence == "idle"
    assert edge.runtime_operation_id == "recon.enqueue_task"
    assert edge.lifecycle_mutation_plan_id == "complete_work_item"
    assert edge.lifecycle_action_id == "complete"
    assert edge.terminal_writes_status == "UPDATE_COMPLETE"
    assert edge.terminal_metadata_source == "graph_resolved"


def test_router_decision_trace_does_not_infer_terminal_state_from_raw_outcome(
    tmp_path: Path,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    run_dir = paths.runs_dir / "run-fallback-terminal"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)
    stage_result_path = stage_results_dir / "request-001.json"
    stage_result = StageResultEnvelope(
        run_id="run-fallback-terminal",
        plane="execution",
        stage=ExecutionStageName.BUILDER,
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        terminal_result="CUSTOM_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### CUSTOM_COMPLETE",
        success=True,
        metadata={"request_id": "request-001"},
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=2),
        duration_seconds=2,
    )
    stage_result_path.write_text(stage_result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    upsert_stage_result_trace_node(
        paths,
        run_dir=run_dir,
        stage_result=stage_result,
        stage_result_path=stage_result_path,
    )

    record_router_decision_trace(
        paths,
        run_dir=run_dir,
        stage_result=stage_result,
        decision=RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="custom_complete_legacy",
        ),
    )

    trace = RunTraceGraph.model_validate_json(
        (run_dir / "run_trace.json").read_text(encoding="utf-8")
    )
    edge = trace.edges[0]
    assert edge.outcome == "CUSTOM_COMPLETE"
    assert edge.terminal_state_id is None
    assert edge.terminal_action_id is None
    assert edge.terminal_metadata_source == "unknown"


def test_spawned_work_ref_from_path_preserves_blueprint_draft_family(tmp_path: Path) -> None:
    stage_result = StageResultEnvelope(
        run_id="run-blueprint",
        plane="execution",
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
    path = tmp_path / "millrace-agents" / "blueprints" / "drafts" / "queue" / "draft-001.json"

    ref = spawned_work_ref_from_path(
        path,
        source_stage_result=stage_result,
        reason="manager_blueprint",
    )

    assert ref.family_id == "blueprint_draft"
    assert ref.kind == "blueprint_draft"


def test_legacy_run_trace_spawned_work_kind_backfills_family_id(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-legacy-spawned-work"
    payload = {
        "schema_version": "1.0",
        "kind": "run_trace_graph",
        "run_id": "run-legacy-spawned-work",
        "run_dir": str(run_dir),
        "status": "complete",
        "nodes": [
            {
                "trace_node_id": "request-001",
                "run_id": "run-legacy-spawned-work",
                "request_id": "request-001",
                "plane": "planning",
                "stage": "manager",
                "node_id": "manager",
                "stage_kind_id": "manager",
                "terminal_result": "MANAGER_COMPLETE",
                "result_class": "success",
                "started_at": NOW.isoformat(),
                "completed_at": NOW.isoformat(),
                "duration_seconds": 0.0,
            },
        ],
        "edges": [
            {
                "trace_edge_id": "request-001--MANAGER_COMPLETE--terminal:manager_complete",
                "source_trace_node_id": "request-001",
                "outcome": "MANAGER_COMPLETE",
                "edge_kind": "complete",
                "terminal_state_id": "manager_complete",
                "spawned_work": [
                    {
                        "kind": "task",
                        "item_id": "task-001",
                        "path": "millrace-agents/tasks/queue/task-001.md",
                        "reason": "legacy_path_inferred",
                    }
                ],
                "decided_at": NOW.isoformat(),
            }
        ],
        "generated_at": NOW.isoformat(),
    }

    trace = RunTraceGraph.model_validate(payload)

    assert trace.edges[0].spawned_work[0].family_id == "task"
    assert trace.edges[0].spawned_work[0].kind == "task"


def test_spawned_work_ref_from_path_uses_compiled_custom_family_paths(tmp_path: Path) -> None:
    stage_result = StageResultEnvelope(
        run_id="run-custom",
        plane="execution",
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
    family = WorkItemFamilyDefinition(
        family_id="custom_review",
        plane="planning",
        entry_key="custom_review",
        display_name="Custom Review",
        document_kind="custom_review",
        runtime_relative_dir="custom/reviews",
        file_extension=".json",
        schema_id="custom_review_document_v1",
        document_adapter_id="custom_review_json_v1",
        queue_dirs={
            "queue": "custom/reviews/queue",
            "active": "custom/reviews/active",
            "done": "custom/reviews/done",
            "blocked": "custom/reviews/blocked",
        },
        lifecycle_states=("queue", "active", "done", "blocked"),
        claimable_state="queue",
        active_state="active",
        done_state="done",
        blocked_state="blocked",
        default_entry_key="custom_review",
    )
    compiled_plan = SimpleNamespace(work_item_families_by_id={"custom_review": family})
    path = tmp_path / "millrace-agents" / "custom" / "reviews" / "queue" / "custom-001.json"

    ref = spawned_work_ref_from_path(
        path,
        source_stage_result=stage_result,
        reason="custom_effect",
        compiled_plan=compiled_plan,
    )

    assert ref.family_id == "custom_review"
    assert ref.kind == "custom_review"


def test_spawned_work_ref_from_path_uses_runtime_effect_rule_destination_family(
    tmp_path: Path,
) -> None:
    stage_result = StageResultEnvelope(
        run_id="run-custom",
        plane="execution",
        stage=ExecutionStageName.BUILDER,
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        metadata={"runtime_effect_handler_id": "custom_review_promotion"},
        started_at=NOW,
        completed_at=NOW,
    )
    compiled_plan = SimpleNamespace(
        runtime_effect_rules=(
            SimpleNamespace(
                handler_id="custom_review_promotion",
                on_outcomes=("BUILDER_COMPLETE",),
                destination_family_id="custom_review",
            ),
        ),
        work_item_families_by_id={
            "custom_review": SimpleNamespace(
                queue_dirs=SimpleNamespace(queue="millrace-agents/reviews/queue")
            )
        },
    )
    path = tmp_path / "millrace-agents" / "reviews" / "queue" / "custom-001.json"

    ref = spawned_work_ref_from_path(
        path,
        source_stage_result=stage_result,
        reason="runtime_effect",
        compiled_plan=compiled_plan,
    )

    assert ref.family_id == "custom_review"
    assert ref.kind == "custom_review"
