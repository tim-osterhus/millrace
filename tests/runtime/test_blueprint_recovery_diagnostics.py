from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from millrace_ai.contracts import Plane, ResultClass, StageResultEnvelope, WorkItemKind
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runtime.blueprint_recovery_diagnostics import (
    latest_runtime_effect_status_metadata,
    runtime_effect_status_metadata_from_stage_result,
)

NOW = datetime(2026, 5, 26, tzinfo=UTC)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _approval_failure_stage_result(
    *,
    completed_at: datetime = NOW,
    **metadata_updates: object,
) -> StageResultEnvelope:
    metadata = {
        "runtime_effect_operation_id": "evaluator_blueprint_approved_to_task",
        "runtime_effect_legacy_handler_id": "evaluator_blueprint_approved_to_task",
        "runtime_effect_decision": "request_block_source",
        "runtime_effect_failure_class": "generated_task_invalid",
        "runtime_effect_failure_message": "generated task target_paths must stay within Blueprint scope",
        "runtime_effect_mutation_phase": "pre_mutation",
        "runtime_effect_failure_policy_id": "blueprint_approval_pre_mutation_effect_validation",
        "runtime_effect_recovery_action": "route_to_node",
    }
    metadata.update(metadata_updates)
    return StageResultEnvelope(
        run_id="run-evaluator-001",
        plane=Plane.PLANNING,
        stage="manager",
        node_id="evaluator_blueprint",
        stage_kind_id="evaluator_blueprint",
        work_item_kind=WorkItemKind.BLUEPRINT_DRAFT,
        work_item_id="draft-001",
        terminal_result="BLUEPRINT_APPROVED",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BLUEPRINT_APPROVED",
        success=True,
        started_at=completed_at,
        completed_at=completed_at,
        metadata=metadata,
    )


def _mechanic_apply_stage_result(*, completed_at: datetime = NOW) -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id="run-mechanic-001",
        plane=Plane.PLANNING,
        stage="mechanic",
        node_id="mechanic_blueprint",
        stage_kind_id="mechanic_blueprint",
        work_item_kind=WorkItemKind.BLUEPRINT_DRAFT,
        work_item_id="draft-001",
        terminal_result="MECHANIC_BLUEPRINT_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### MECHANIC_BLUEPRINT_COMPLETE",
        success=True,
        started_at=completed_at,
        completed_at=completed_at,
        metadata={
            "runtime_effect_operation_id": "mechanic_blueprint_repair_apply",
            "runtime_effect_legacy_handler_id": "mechanic_blueprint_repair_apply",
            "runtime_effect_decision": "request_complete_source",
            "runtime_effect_mutation_phase": "unknown",
        },
    )


def test_blueprint_repair_diagnostics_accept_operation_identity() -> None:
    metadata = runtime_effect_status_metadata_from_stage_result(
        _approval_failure_stage_result(),
    )

    assert metadata["latest_runtime_effect_operation_id"] == (
        "evaluator_blueprint_approved_to_task"
    )
    assert metadata["latest_blueprint_repair_context"] == (
        "failed_handler=evaluator_blueprint_approved_to_task "
        "failure_class=generated_task_invalid "
        "mutation_phase=pre_mutation "
        "policy=blueprint_approval_pre_mutation_effect_validation "
        "recovery_action=route_to_node"
    )


def test_blueprint_repair_diagnostics_preserve_failed_context_after_repair_apply(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    stage_results_dir = paths.runs_dir / "run-repair" / "stage_results"
    stage_results_dir.mkdir(parents=True)
    failed_path = stage_results_dir / "request-evaluator.json"
    repair_path = stage_results_dir / "request-mechanic.json"
    failed_path.write_text(
        _approval_failure_stage_result(completed_at=NOW).model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    repair_path.write_text(
        _mechanic_apply_stage_result(
            completed_at=NOW + timedelta(seconds=1),
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )

    metadata = latest_runtime_effect_status_metadata(paths, str(repair_path))

    assert metadata["latest_runtime_effect_operation_id"] == (
        "evaluator_blueprint_approved_to_task"
    )
    assert metadata["latest_blueprint_repair_contract"].startswith(
        "action=apply_repaired_generated_task",
    )
