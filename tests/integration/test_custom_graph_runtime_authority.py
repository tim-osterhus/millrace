from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from millrace_ai.contracts import (
    ActiveRunState,
    ExecutionStageName,
    Plane,
    ResultClass,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runner import RunnerRawResult, StageRunRequest, normalize_stage_result
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.lanes import compiled_plan_fingerprint_for_runtime
from millrace_ai.state_store import load_snapshot, save_snapshot

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_synthetic_custom_graph_normalizes_routes_and_applies_terminal_action(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    _write_synthetic_stage_kind_asset(assets_root)
    _write_synthetic_graph_loop_asset(assets_root)
    _write_synthetic_mode_asset(assets_root)
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"), assets_root=assets_root)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001"))
    assert queue.claim_next_execution_task() is not None

    engine = RuntimeEngine(
        paths,
        stage_runner=_unused_stage_runner,
        mode_id="synthetic_codex",
        assets_root=assets_root,
    )
    engine.startup()
    assert engine.snapshot is not None
    assert engine.compiled_plan is not None
    _activate_synthetic_run(engine, work_item_id="task-001")

    request = _synthetic_request(engine, tmp_path / "run-synthetic")
    stdout_path = Path(request.run_dir) / "stdout.txt"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text("synthetic worker output\n### SYNTHETIC_COMPLETE\n", encoding="utf-8")
    stage_result = normalize_stage_result(request, _raw_result(request, stdout_path))

    decision = engine._route_stage_result(stage_result)
    engine._apply_router_decision(decision, stage_result)

    snapshot = load_snapshot(paths)
    assert stage_result.terminal_result.value == "SYNTHETIC_COMPLETE"
    assert stage_result.result_class is ResultClass.SUCCESS
    assert decision.action.value == "idle"
    assert decision.terminal_state_id == "synthetic_complete"
    assert decision.terminal_action_id == "complete_work_item"
    assert decision.lifecycle_mutation_plan_id == "complete_work_item"
    assert decision.lifecycle_action_id == "complete"
    assert decision.terminal_writes_status == "SYNTHETIC_COMPLETE"
    assert (paths.tasks_done_dir / "task-001.md").is_file()
    assert snapshot.active_stage is None
    assert snapshot.execution_status_marker == "### SYNTHETIC_COMPLETE"


def _copy_builtin_assets(tmp_path: Path) -> Path:
    assets_root = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    copied_root = tmp_path / "assets"
    shutil.copytree(assets_root, copied_root)
    return copied_root


def _write_synthetic_stage_kind_asset(assets_root: Path) -> None:
    stage_kind_path = assets_root / "registry" / "stage_kinds" / "execution" / "synthetic_worker.json"
    payload = {
        "schema_version": "1.0",
        "kind": "registered_stage_kind",
        "stage_kind_id": "synthetic_worker",
        "plane": "execution",
        "runtime_stage": "builder",
        "display_name": "Synthetic Worker",
        "default_entrypoint_path": "entrypoints/execution/lad_builder.md",
        "required_skill_paths": ["skills/stage/execution/builder-core/SKILL.md"],
        "suggested_skill_paths": [],
        "running_status_marker": "SYNTHETIC_RUNNING",
        "legal_outcomes": ["SYNTHETIC_COMPLETE", "BLOCKED"],
        "success_outcomes": ["SYNTHETIC_COMPLETE"],
        "failure_outcomes": ["BLOCKED"],
        "allowed_result_classes_by_outcome": {
            "SYNTHETIC_COMPLETE": ["success"],
            "BLOCKED": ["blocked", "recoverable_failure"],
        },
        "allowed_input_artifacts": [],
        "declared_output_artifacts": ["stage_result", "report"],
        "allowed_work_item_families": ["task"],
        "idempotence_policy": "retry_safe_with_key",
        "allowed_overrides": [
            "entrypoint_path",
            "runner_name",
            "model_name",
            "timeout_seconds",
            "attached_skill_additions",
        ],
        "can_start_tasks": True,
        "can_start_specs": False,
        "can_start_incidents": False,
        "recovery_role": None,
        "closure_role": False,
    }
    stage_kind_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    profiles_path = assets_root / "registry" / "request_context_profiles" / "default_request_context_profiles.json"
    profiles_payload = json.loads(profiles_path.read_text(encoding="utf-8"))
    profiles_payload["definitions"].append(
        {
            "schema_version": "1.0",
            "kind": "request_context_profile",
            "profile_id": "synthetic_worker.default",
            "request_kind": "active_work_item",
            "provider_id": "generic.active_work_item",
            "primary_render_plan_id": "stage_request.default.v1",
            "required_providers": ["active_work_item"],
            "output_path_preferences": {"builder_summary": "builder_summary.md"},
            "visibility_policy": "active_item_only",
        }
    )
    profiles_path.write_text(json.dumps(profiles_payload, indent=2) + "\n", encoding="utf-8")


def _write_synthetic_graph_loop_asset(assets_root: Path) -> None:
    graph_path = assets_root / "graphs" / "execution" / "synthetic.json"
    payload = {
        "schema_version": "1.0",
        "kind": "graph_loop",
        "loop_id": "execution.synthetic",
        "plane": "execution",
        "nodes": [
            {
                "node_id": "synthetic_worker",
                "stage_kind_id": "synthetic_worker",
                "request_context_profile_id": "synthetic_worker.default",
                "context_render_plan_id": "stage_request.default.v1",
            }
        ],
        "entry_nodes": [{"entry_key": "task", "node_id": "synthetic_worker"}],
        "edges": [
            {
                "edge_id": "synthetic-complete-to-terminal",
                "from_node_id": "synthetic_worker",
                "terminal_state_id": "synthetic_complete",
                "on_outcomes": ["SYNTHETIC_COMPLETE"],
                "kind": "terminal",
            },
            {
                "edge_id": "synthetic-blocked-to-terminal",
                "from_node_id": "synthetic_worker",
                "terminal_state_id": "blocked",
                "on_outcomes": ["BLOCKED"],
                "kind": "terminal",
            },
        ],
        "terminal_states": [
            {
                "terminal_state_id": "synthetic_complete",
                "terminal_class": "success",
                "terminal_action_id": "complete_work_item",
                "writes_status": "SYNTHETIC_COMPLETE",
                "emits_artifacts": ["stage_result", "report"],
            },
            {
                "terminal_state_id": "blocked",
                "terminal_class": "blocked",
                "terminal_action_id": "block_work_item",
                "writes_status": "BLOCKED",
                "emits_artifacts": ["stage_result", "report"],
            },
        ],
    }
    graph_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_synthetic_mode_asset(assets_root: Path) -> None:
    mode_path = assets_root / "modes" / "synthetic_codex.json"
    payload = {
        "schema_version": "1.0",
        "kind": "mode",
        "mode_id": "synthetic_codex",
        "loop_ids_by_plane": {
            "execution": "execution.synthetic",
            "planning": "planning.standard",
        },
        "stage_entrypoint_overrides": {},
        "stage_skill_additions": {},
        "stage_model_bindings": {},
        "stage_thinking_bindings": {},
        "stage_runner_bindings": {"synthetic_worker": "codex_cli"},
        "required_extensions": [
            {"extension_package_id": "millrace.generic"},
            {"extension_package_id": "millrace.recon"},
            {"extension_package_id": "millrace.closure"},
        ],
    }
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _task_doc(task_id: str) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title="Synthetic runtime authority",
        summary="custom graph runtime authority integration",
        target_paths=("src/millrace_ai/runtime/result_application.py",),
        acceptance=("SYNTHETIC_COMPLETE completes the task.",),
        required_checks=("pytest tests/integration/test_custom_graph_runtime_authority.py -q",),
        references=("tests/integration/test_custom_graph_runtime_authority.py",),
        risk=("custom terminal outcomes must stay graph-driven",),
        created_at=NOW,
        created_by="tests",
    )


def _activate_synthetic_run(engine: RuntimeEngine, *, work_item_id: str) -> None:
    assert engine.snapshot is not None
    assert engine.compiled_plan is not None
    active_run = ActiveRunState(
        plane=Plane.EXECUTION,
        lane_id="execution.main",
        stage=ExecutionStageName.BUILDER,
        node_id="synthetic_worker",
        stage_kind_id="synthetic_worker",
        run_id="run-synthetic",
        compiled_plan_id=engine.compiled_plan.compiled_plan_id,
        compiled_plan_fingerprint=compiled_plan_fingerprint_for_runtime(engine.compiled_plan),
        request_kind="active_work_item",
        work_item_kind=WorkItemKind.TASK,
        work_item_id=work_item_id,
        active_since=NOW,
    )
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "active_runs_by_plane": {Plane.EXECUTION: active_run},
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "active_node_id": "synthetic_worker",
            "active_stage_kind_id": "synthetic_worker",
            "active_run_id": "run-synthetic",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": work_item_id,
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(engine.paths, engine.snapshot)


def _synthetic_request(engine: RuntimeEngine, run_dir: Path) -> StageRunRequest:
    assert engine.compiled_plan is not None
    return StageRunRequest(
        request_id="request-synthetic",
        run_id="run-synthetic",
        plane="execution",
        stage="builder",
        mode_id="synthetic_codex",
        compiled_plan_id=engine.compiled_plan.compiled_plan_id,
        node_id="synthetic_worker",
        stage_kind_id="synthetic_worker",
        legal_terminal_markers=("### SYNTHETIC_COMPLETE", "### BLOCKED"),
        allowed_result_classes_by_outcome={
            "SYNTHETIC_COMPLETE": (ResultClass.SUCCESS,),
            "BLOCKED": (ResultClass.BLOCKED, ResultClass.RECOVERABLE_FAILURE),
        },
        entrypoint_path="millrace-agents/entrypoints/execution/builder.md",
        active_work_item_kind="task",
        active_work_item_id="task-001",
        active_work_item_path="millrace-agents/tasks/active/task-001.md",
        run_dir=str(run_dir),
        summary_status_path=str(engine.paths.execution_status_file),
        runtime_snapshot_path=str(engine.paths.runtime_snapshot_file),
        recovery_counters_path=str(engine.paths.recovery_counters_file),
        runner_name="unit-runner",
    )


def _raw_result(request: StageRunRequest, stdout_path: Path) -> RunnerRawResult:
    return RunnerRawResult(
        request_id=request.request_id,
        run_id=request.run_id,
        stage=request.stage,
        runner_name="unit-runner",
        exit_kind="completed",
        exit_code=0,
        stdout_path=str(stdout_path),
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=1),
    )


def _unused_stage_runner(_request: StageRunRequest) -> RunnerRawResult:
    raise AssertionError("stage runner should not be called")
