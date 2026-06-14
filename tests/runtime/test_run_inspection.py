from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from millrace_ai.contracts import RunTraceGraph, RunTraceNode, StageResultEnvelope, TokenUsage
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.run_inspection import inspect_run, inspect_run_id, list_runs

NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)


def test_runtime_package_exposes_inspection_module() -> None:
    inspection_module = importlib.import_module("millrace_ai.runtime.inspection")

    assert inspection_module.inspect_run is inspect_run
    assert inspection_module.inspect_run_id is inspect_run_id


def test_inspect_run_surfaces_stage_result_and_primary_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-001"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = run_dir / "runner_stdout.txt"
    stdout_path.write_text("### CHECKER_PASS\n", encoding="utf-8")
    report_path = run_dir / "troubleshoot_report.md"
    report_path.write_text("# Troubleshoot\n", encoding="utf-8")

    stage_result = StageResultEnvelope(
        run_id="run-001",
        plane="execution",
        stage="checker",
        node_id="execution.checker.primary",
        stage_kind_id="checker",
        work_item_kind="task",
        work_item_id="task-001",
        terminal_result="CHECKER_PASS",
        result_class="success",
        summary_status_marker="### CHECKER_PASS",
        success=True,
        artifact_paths=(str(report_path),),
        stdout_path=str(stdout_path),
        report_artifact=str(report_path),
        runner_name="codex_cli",
        model_name="gpt-5.4",
        thinking_level="high",
        model_reasoning_effort="high",
        model_assignment_alias_id="fast",
        model_assignment_source="stage:checker",
        metadata={
            "failure_class": None,
            "request_id": "request-001",
            "execution_capability_grants": [
                {
                    "grant_id": "grant-checker-runner",
                    "capability_id": "runner.invoke",
                    "decision_state": "granted",
                    "enforcement_mode": "runtime_enforced",
                    "evidence_status": "pending",
                }
            ],
            "capability_support_decisions": [
                {
                    "grant_id": "grant-checker-runner",
                    "runner_id": "codex_cli",
                    "support_state": "supported",
                    "enforcement_mode": "runtime_enforced",
                    "evidence_available": True,
                }
            ],
        },
        started_at=NOW,
        completed_at=NOW,
    )
    (stage_results_dir / "request-001.json").write_text(
        stage_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    summary = inspect_run(run_dir)

    assert summary.run_id == "run-001"
    assert summary.status == "valid"
    assert summary.stage_results[0].request_id == "request-001"
    assert summary.stage_results[0].node_id == "execution.checker.primary"
    assert summary.stage_results[0].stage_kind_id == "checker"
    assert summary.stage_results[0].terminal_result == "CHECKER_PASS"
    assert summary.stage_results[0].thinking_level == "high"
    assert summary.stage_results[0].model_reasoning_effort == "high"
    assert summary.stage_results[0].model_assignment_alias_id == "fast"
    assert summary.stage_results[0].model_assignment_source == "stage:checker"
    assert summary.stage_results[0].capability_grant_summaries == (
        (
            "grant_id=grant-checker-runner capability=runner.invoke decision=granted "
            "enforcement=runtime_enforced evidence=pending"
        ),
    )
    assert summary.stage_results[0].capability_support_summaries == (
        (
            "grant_id=grant-checker-runner runner=codex_cli support=supported "
            "enforcement=runtime_enforced evidence_available=true"
        ),
    )
    assert summary.primary_stdout_path == "runner_stdout.txt"
    assert summary.troubleshoot_report_path == "troubleshoot_report.md"


def test_inspect_run_aggregates_duration_and_token_usage(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-usage"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)

    stage_one = StageResultEnvelope(
        run_id="run-usage",
        plane="execution",
        stage="builder",
        work_item_kind="task",
        work_item_id="task-001",
        terminal_result="BUILDER_COMPLETE",
        result_class="success",
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        duration_seconds=3.0,
        token_usage=TokenUsage(
            input_tokens=100,
            cached_input_tokens=20,
            output_tokens=10,
            thinking_tokens=4,
            total_tokens=110,
        ),
        started_at=NOW,
        completed_at=datetime(2026, 4, 15, 12, 0, 3, tzinfo=timezone.utc),
    )
    stage_two = StageResultEnvelope(
        run_id="run-usage",
        plane="execution",
        stage="checker",
        work_item_kind="task",
        work_item_id="task-001",
        terminal_result="CHECKER_PASS",
        result_class="success",
        summary_status_marker="### CHECKER_PASS",
        success=True,
        duration_seconds=5.0,
        token_usage=TokenUsage(
            input_tokens=40,
            cached_input_tokens=10,
            output_tokens=6,
            thinking_tokens=2,
            total_tokens=46,
        ),
        started_at=datetime(2026, 4, 15, 12, 0, 3, tzinfo=timezone.utc),
        completed_at=datetime(2026, 4, 15, 12, 0, 8, tzinfo=timezone.utc),
    )
    (stage_results_dir / "request-001.json").write_text(
        stage_one.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (stage_results_dir / "request-002.json").write_text(
        stage_two.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    summary = inspect_run(run_dir)

    assert summary.started_at == NOW.isoformat()
    assert summary.completed_at == datetime(2026, 4, 15, 12, 0, 8, tzinfo=timezone.utc).isoformat()
    assert summary.duration_seconds == 8.0
    assert summary.token_usage == TokenUsage(
        input_tokens=140,
        cached_input_tokens=30,
        output_tokens=16,
        thinking_tokens=6,
        total_tokens=156,
    )
    assert summary.stage_results[0].duration_seconds == 3.0
    assert summary.stage_results[1].token_usage == TokenUsage(
        input_tokens=40,
        cached_input_tokens=10,
        output_tokens=6,
        thinking_tokens=2,
        total_tokens=46,
    )


def test_inspect_run_marks_incomplete_when_stage_results_are_missing(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-002"
    run_dir.mkdir(parents=True, exist_ok=True)

    summary = inspect_run(run_dir)

    assert summary.status == "incomplete"
    assert "no stage result artifacts" in summary.notes[0]


def test_inspect_run_marks_malformed_stage_result_json(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-003"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)
    (stage_results_dir / "request-001.json").write_text("{not-json\n", encoding="utf-8")

    summary = inspect_run(run_dir)

    assert summary.status == "malformed"
    assert "invalid JSON" in summary.notes[0]


def test_list_runs_keeps_incomplete_and_malformed_runs_visible(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    valid_run_dir = paths.runs_dir / "run-b"
    malformed_run_dir = paths.runs_dir / "run-a"
    valid_stage_results_dir = valid_run_dir / "stage_results"
    valid_stage_results_dir.mkdir(parents=True, exist_ok=True)
    malformed_stage_results_dir = malformed_run_dir / "stage_results"
    malformed_stage_results_dir.mkdir(parents=True, exist_ok=True)
    (malformed_stage_results_dir / "request-001.json").write_text("{bad\n", encoding="utf-8")

    payload = {
        "schema_version": "1.0",
        "kind": "stage_result",
        "run_id": "run-b",
        "plane": "execution",
        "stage": "builder",
        "work_item_kind": "task",
        "work_item_id": "task-123",
        "terminal_result": "BUILDER_COMPLETE",
        "result_class": "success",
        "summary_status_marker": "### BUILDER_COMPLETE",
        "success": True,
        "started_at": NOW.isoformat(),
        "completed_at": NOW.isoformat(),
    }
    (valid_stage_results_dir / "request-001.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )

    summaries = list_runs(paths)

    assert [summary.run_id for summary in summaries] == ["run-a", "run-b"]
    assert [summary.status for summary in summaries] == ["malformed", "valid"]


def test_inspect_run_surfaces_closure_target_request_metadata(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-arbiter"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)

    stage_result = StageResultEnvelope(
        run_id="run-arbiter",
        plane="planning",
        stage="arbiter",
        work_item_kind="spec",
        work_item_id="spec-root-001",
        terminal_result="ARBITER_COMPLETE",
        result_class="success",
        summary_status_marker="### ARBITER_COMPLETE",
        success=True,
        metadata={
            "failure_class": None,
            "request_kind": "closure_target",
            "closure_target_root_spec_id": "spec-root-001",
            "closure_target_root_source_kind": "probe",
            "closure_target_root_source_id": "probe-root-001",
            "closure_target_root_source_path": (
                "millrace-agents/arbiter/contracts/root-sources/probe/probe-root-001.md"
            ),
        },
        started_at=NOW,
        completed_at=NOW,
    )
    (stage_results_dir / "request-001.json").write_text(
        stage_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    summary = inspect_run(run_dir)

    assert summary.request_kind == "closure_target"
    assert summary.closure_target_root_spec_id == "spec-root-001"
    assert summary.closure_target_root_source_kind == "probe"
    assert summary.closure_target_root_source_id == "probe-root-001"
    assert summary.stage_results[0].request_kind == "closure_target"
    assert summary.stage_results[0].closure_target_root_spec_id == "spec-root-001"
    assert summary.stage_results[0].closure_target_root_source_kind == "probe"
    assert summary.stage_results[0].closure_target_root_source_id == "probe-root-001"


def test_inspect_run_surfaces_context_and_failure_origin_metadata(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-context"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)

    stage_result = StageResultEnvelope(
        run_id="run-context",
        plane="execution",
        stage="builder",
        work_item_kind="task",
        work_item_id="task-001",
        terminal_result="BLOCKED",
        result_class="recoverable_failure",
        summary_status_marker="### BLOCKED",
        success=False,
        metadata={
            "failure_class": "network_unavailable",
            "failure_origin": "network_unavailable",
            "request_context_profile_id": "builder.default",
            "context_bundle_path": str(run_dir / "context" / "context.json"),
            "context_render_plan_id": "stage_request.default.v1",
            "rendered_prompt_context_path": str(run_dir / "context" / "prompt_context.md"),
            "context_artifact_refs": ["task:task-001", "draft:blueprint-001"],
        },
        started_at=NOW,
        completed_at=NOW,
    )
    (stage_results_dir / "request-001.json").write_text(
        stage_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    summary = inspect_run(run_dir)

    assert summary.failure_class == "network_unavailable"
    assert summary.failure_origin == "network_unavailable"
    assert summary.stage_results[0].failure_origin == "network_unavailable"
    assert summary.stage_results[0].request_context_profile_id == "builder.default"
    assert summary.stage_results[0].context_bundle_path == "context/context.json"
    assert summary.stage_results[0].context_render_plan_id == "stage_request.default.v1"
    assert summary.stage_results[0].rendered_prompt_context_path == "context/prompt_context.md"
    assert summary.stage_results[0].context_artifact_refs == (
        "task:task-001",
        "draft:blueprint-001",
    )


def test_inspect_run_surfaces_runtime_effect_metadata(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-blueprint"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)

    stage_result = StageResultEnvelope(
        run_id="run-blueprint",
        plane="planning",
        stage="manager",
        node_id="evaluator_blueprint",
        stage_kind_id="evaluator_blueprint",
        work_item_family_id="blueprint_draft",
        work_item_id="draft-blueprint-001",
        terminal_result="BLUEPRINT_APPROVED",
        result_class="success",
        summary_status_marker="### BLUEPRINT_APPROVED",
        success=True,
        artifact_paths=(
            "millrace-agents/blueprints/evaluations/evaluation-blueprint-001.json",
            "millrace-agents/tasks/queue/task-blueprint-001.md",
        ),
        metadata={
            "runtime_effect_handler_id": "evaluator_blueprint_approved_to_task",
            "runtime_effect_operation_id": "evaluator_blueprint_approved_to_task",
            "runtime_effect_runner_id": "legacy_python_handler",
            "runtime_effect_legacy_handler_id": "evaluator_blueprint_approved_to_task",
            "runtime_effect_decision": "request_complete_source",
            "runtime_effect_failure_class": "generated_task_missing",
            "runtime_effect_failure_message": "generated_task.json is missing",
            "runtime_effect_mutation_phase": "pre_mutation",
            "runtime_effect_created_paths": [
                "millrace-agents/blueprints/evaluations/evaluation-blueprint-001.json",
                "millrace-agents/tasks/queue/task-blueprint-001.md",
            ],
            "runtime_effect_source_lifecycle_plan_id": "approve_blueprint_draft_after_effect",
            "runtime_effect_source_lifecycle_action": "complete",
        },
        started_at=NOW,
        completed_at=NOW,
    )
    (stage_results_dir / "request-001.json").write_text(
        stage_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    summary = inspect_run(run_dir)
    inspected = summary.stage_results[0]

    assert summary.failure_class == "generated_task_missing"
    assert inspected.runtime_effect_handler_id == "evaluator_blueprint_approved_to_task"
    assert inspected.runtime_effect_operation_id == "evaluator_blueprint_approved_to_task"
    assert inspected.runtime_effect_runner_id == "legacy_python_handler"
    assert inspected.runtime_effect_legacy_handler_id == "evaluator_blueprint_approved_to_task"
    assert summary.runtime_effect_operation_id == "evaluator_blueprint_approved_to_task"
    assert inspected.runtime_effect_decision == "request_complete_source"
    assert inspected.runtime_effect_failure_class == "generated_task_missing"
    assert inspected.runtime_effect_failure_message == "generated_task.json is missing"
    assert inspected.runtime_effect_mutation_phase == "pre_mutation"
    assert inspected.runtime_effect_created_paths == (
        "millrace-agents/blueprints/evaluations/evaluation-blueprint-001.json",
        "millrace-agents/tasks/queue/task-blueprint-001.md",
    )
    assert inspected.runtime_effect_source_lifecycle_plan_id == (
        "approve_blueprint_draft_after_effect"
    )
    assert inspected.runtime_effect_source_lifecycle_action == "complete"


def test_inspect_run_surfaces_operation_only_runtime_effect_metadata(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-operation-only-effect"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)

    stage_result = StageResultEnvelope(
        run_id="run-operation-only-effect",
        plane="execution",
        stage="builder",
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind="task",
        work_item_id="task-001",
        terminal_result="BUILDER_COMPLETE",
        result_class="success",
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        metadata={
            "runtime_effect_operation_id": "operation_only_effect",
            "runtime_effect_runner_id": "operation_only_runner",
            "runtime_effect_decision": "continue_route",
        },
        started_at=NOW,
        completed_at=NOW,
    )
    (stage_results_dir / "request-001.json").write_text(
        stage_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    summary = inspect_run(run_dir)
    inspected = summary.stage_results[0]

    assert inspected.runtime_effect_handler_id is None
    assert inspected.runtime_effect_operation_id == "operation_only_effect"
    assert inspected.runtime_effect_runner_id == "operation_only_runner"
    assert inspected.runtime_effect_legacy_handler_id is None
    assert summary.runtime_effect_operation_id == "operation_only_effect"
    assert summary.runtime_effect_runner_id == "operation_only_runner"
    assert summary.runtime_effect_handler_id is None


def test_inspect_run_uses_latest_operation_only_runtime_effect(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run-operation-only-repair"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)

    repairable_failure = StageResultEnvelope(
        run_id="run-operation-only-repair",
        plane="planning",
        stage="manager",
        node_id="evaluator_blueprint",
        stage_kind_id="evaluator_blueprint",
        work_item_family_id="blueprint_draft",
        work_item_id="draft-blueprint-001",
        terminal_result="BLUEPRINT_APPROVED",
        result_class="recoverable_failure",
        summary_status_marker="### BLUEPRINT_APPROVED",
        success=False,
        metadata={
            "runtime_effect_operation_id": "evaluator_blueprint_approved_to_task",
            "runtime_effect_runner_id": "legacy_python_handler",
            "runtime_effect_decision": "request_block_source",
            "runtime_effect_failure_class": "generated_task_invalid",
            "runtime_effect_failure_message": "generated_task.md failed schema validation",
            "runtime_effect_mutation_phase": "pre_mutation",
            "runtime_effect_failure_policy_id": (
                "blueprint_approval_pre_mutation_effect_validation"
            ),
            "runtime_effect_recovery_action": "route_to_node",
        },
        started_at=NOW,
        completed_at=NOW,
    )
    mechanic_apply = StageResultEnvelope(
        run_id="run-operation-only-repair",
        plane="planning",
        stage="mechanic",
        node_id="mechanic_blueprint",
        stage_kind_id="mechanic_blueprint",
        work_item_family_id="blueprint_draft",
        work_item_id="draft-blueprint-001",
        terminal_result="MECHANIC_COMPLETE",
        result_class="success",
        summary_status_marker="### MECHANIC_COMPLETE",
        success=True,
        metadata={
            "runtime_effect_operation_id": "mechanic_blueprint_repair_apply",
            "runtime_effect_runner_id": "legacy_python_handler",
            "runtime_effect_decision": "request_complete_source",
            "runtime_effect_failure_message": "promoted repaired task",
            "runtime_effect_mutation_phase": "unknown",
        },
        started_at=NOW + timedelta(seconds=1),
        completed_at=NOW + timedelta(seconds=1),
    )
    (stage_results_dir / "request-001.json").write_text(
        repairable_failure.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (stage_results_dir / "request-002.json").write_text(
        mechanic_apply.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    summary = inspect_run(run_dir)

    assert summary.runtime_effect_operation_id == "mechanic_blueprint_repair_apply"
    assert summary.runtime_effect_runner_id == "legacy_python_handler"
    assert summary.runtime_effect_handler_id is None
    assert summary.runtime_effect_decision == "request_complete_source"
    assert summary.runtime_effect_failure_class is None
    assert summary.runtime_effect_recovery_action is None


def test_inspect_run_ignores_stale_legacy_blueprint_repair_identity(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run-stale-legacy-repair-identity"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)

    repairable_failure = StageResultEnvelope(
        run_id="run-stale-legacy-repair-identity",
        plane="planning",
        stage="manager",
        node_id="evaluator_blueprint",
        stage_kind_id="evaluator_blueprint",
        work_item_family_id="blueprint_draft",
        work_item_id="draft-blueprint-001",
        terminal_result="BLUEPRINT_APPROVED",
        result_class="recoverable_failure",
        summary_status_marker="### BLUEPRINT_APPROVED",
        success=False,
        metadata={
            "runtime_effect_operation_id": "evaluator_blueprint_approved_to_task",
            "runtime_effect_runner_id": "legacy_python_handler",
            "runtime_effect_decision": "request_block_source",
            "runtime_effect_failure_class": "generated_task_invalid",
            "runtime_effect_failure_message": "generated_task.md failed schema validation",
            "runtime_effect_mutation_phase": "pre_mutation",
            "runtime_effect_failure_policy_id": (
                "blueprint_approval_pre_mutation_effect_validation"
            ),
            "runtime_effect_recovery_action": "route_to_node",
        },
        started_at=NOW,
        completed_at=NOW,
    )
    stale_legacy_apply = StageResultEnvelope(
        run_id="run-stale-legacy-repair-identity",
        plane="planning",
        stage="mechanic",
        node_id="mechanic_blueprint",
        stage_kind_id="mechanic_blueprint",
        work_item_family_id="blueprint_draft",
        work_item_id="draft-blueprint-001",
        terminal_result="MECHANIC_COMPLETE",
        result_class="success",
        summary_status_marker="### MECHANIC_COMPLETE",
        success=True,
        metadata={
            "runtime_effect_operation_id": "unrelated_runtime_effect",
            "runtime_effect_runner_id": "legacy_python_handler",
            "runtime_effect_legacy_handler_id": "mechanic_blueprint_repair_apply",
            "runtime_effect_decision": "request_complete_source",
            "runtime_effect_failure_message": "unrelated effect completed",
            "runtime_effect_mutation_phase": "unknown",
        },
        started_at=NOW + timedelta(seconds=1),
        completed_at=NOW + timedelta(seconds=1),
    )
    (stage_results_dir / "request-001.json").write_text(
        repairable_failure.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (stage_results_dir / "request-002.json").write_text(
        stale_legacy_apply.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    summary = inspect_run(run_dir)

    assert summary.runtime_effect_operation_id == "unrelated_runtime_effect"
    assert summary.runtime_effect_legacy_handler_id == "mechanic_blueprint_repair_apply"
    assert summary.runtime_effect_decision == "request_complete_source"
    assert summary.runtime_effect_failure_class is None
    assert summary.runtime_effect_recovery_action is None


def test_inspect_run_uses_blocked_run_trace_outcome_when_stage_result_is_schema_valid(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run-blueprint-blocked"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)

    stage_result = StageResultEnvelope(
        run_id="run-blueprint-blocked",
        plane="planning",
        stage="manager",
        node_id="evaluator_blueprint",
        stage_kind_id="evaluator_blueprint",
        work_item_family_id="blueprint_draft",
        work_item_id="draft-blueprint-001",
        terminal_result="BLUEPRINT_APPROVED",
        result_class="success",
        summary_status_marker="### BLUEPRINT_APPROVED",
        success=True,
        metadata={
            "compiled_plan_id": "plan-blueprint",
            "mode_id": "blueprint_" "codex",
            "runtime_effect_handler_id": "evaluator_blueprint_approved_to_task",
            "runtime_effect_decision": "request_block_source",
            "runtime_effect_failure_class": "generated_task_missing",
            "runtime_effect_failure_message": "generated_task.json is missing",
        },
        started_at=NOW,
        completed_at=NOW,
    )
    (stage_results_dir / "request-001.json").write_text(
        stage_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    trace = RunTraceGraph(
        run_id="run-blueprint-blocked",
        run_dir=str(run_dir),
        compiled_plan_id="plan-blueprint",
        mode_id="blueprint_" "codex",
        work_item_family_id="blueprint_draft",
        work_item_kind="blueprint_draft",
        work_item_id="draft-blueprint-001",
        status="blocked",
        started_at=NOW,
        completed_at=NOW,
        duration_seconds=0.0,
        nodes=(
            RunTraceNode(
                trace_node_id="request-001",
                run_id="run-blueprint-blocked",
                request_id="request-001",
                plane="planning",
                stage="manager",
                node_id="evaluator_blueprint",
                stage_kind_id="evaluator_blueprint",
                compiled_plan_id="plan-blueprint",
                mode_id="blueprint_" "codex",
                work_item_family_id="blueprint_draft",
                work_item_kind="blueprint_draft",
                work_item_id="draft-blueprint-001",
                terminal_result="BLUEPRINT_APPROVED",
                result_class="success",
                started_at=NOW,
                completed_at=NOW,
                duration_seconds=0.0,
            ),
        ),
        notes=("runtime effect blocked source work item",),
        generated_at=NOW,
    )
    (run_dir / "run_trace.json").write_text(trace.model_dump_json(indent=2) + "\n", encoding="utf-8")

    summary = inspect_run(run_dir)

    assert summary.artifact_status == "valid"
    assert summary.runtime_outcome == "blocked"
    assert summary.runtime_effect_decision == "request_block_source"
    assert summary.runtime_effect_failure_class == "generated_task_missing"
    assert summary.failure_class == "generated_task_missing"


def test_inspect_run_degrades_runtime_outcome_when_legacy_run_has_no_trace(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run-legacy-no-trace"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)
    stage_result = StageResultEnvelope(
        run_id="run-legacy-no-trace",
        plane="planning",
        stage="manager",
        node_id="evaluator_blueprint",
        stage_kind_id="evaluator_blueprint",
        work_item_family_id="blueprint_draft",
        work_item_id="draft-blueprint-001",
        terminal_result="BLUEPRINT_APPROVED",
        result_class="success",
        summary_status_marker="### BLUEPRINT_APPROVED",
        success=True,
        metadata={"request_id": "request-001"},
        started_at=NOW,
        completed_at=NOW,
    )
    (stage_results_dir / "request-001.json").write_text(
        stage_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    summary = inspect_run(run_dir)

    assert summary.artifact_status == "valid"
    assert summary.runtime_outcome == "incomplete"


def test_inspect_run_uses_blocked_trace_even_without_runtime_effect_metadata(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run-legacy-blocked-trace"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)
    stage_result = StageResultEnvelope(
        run_id="run-legacy-blocked-trace",
        plane="planning",
        stage="manager",
        node_id="evaluator_blueprint",
        stage_kind_id="evaluator_blueprint",
        work_item_family_id="blueprint_draft",
        work_item_id="draft-blueprint-001",
        terminal_result="BLUEPRINT_APPROVED",
        result_class="success",
        summary_status_marker="### BLUEPRINT_APPROVED",
        success=True,
        metadata={"request_id": "request-001"},
        started_at=NOW,
        completed_at=NOW,
    )
    (stage_results_dir / "request-001.json").write_text(
        stage_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    trace = RunTraceGraph(
        run_id="run-legacy-blocked-trace",
        run_dir=str(run_dir),
        work_item_family_id="blueprint_draft",
        work_item_kind="blueprint_draft",
        work_item_id="draft-blueprint-001",
        status="blocked",
        started_at=NOW,
        completed_at=NOW,
        duration_seconds=0.0,
        nodes=(
            RunTraceNode(
                trace_node_id="request-001",
                run_id="run-legacy-blocked-trace",
                request_id="request-001",
                plane="planning",
                stage="manager",
                node_id="evaluator_blueprint",
                stage_kind_id="evaluator_blueprint",
                work_item_family_id="blueprint_draft",
                work_item_kind="blueprint_draft",
                work_item_id="draft-blueprint-001",
                terminal_result="BLUEPRINT_APPROVED",
                result_class="success",
                started_at=NOW,
                completed_at=NOW,
                duration_seconds=0.0,
            ),
        ),
        notes=("legacy trace blocked after runtime effect failure",),
        generated_at=NOW,
    )
    (run_dir / "run_trace.json").write_text(trace.model_dump_json(indent=2) + "\n", encoding="utf-8")

    summary = inspect_run(run_dir)

    assert summary.artifact_status == "valid"
    assert summary.runtime_outcome == "blocked"
    assert summary.runtime_effect_failure_class is None
