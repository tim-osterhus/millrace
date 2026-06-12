"""Mechanic Blueprint runtime-effect operation runner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import JsonValue, ValidationError

from millrace_ai.contracts import StageResultEnvelope
from millrace_ai.contracts.work_documents import TaskDocument
from millrace_ai.errors import QueueStateError
from millrace_ai.extensions.builtin.blueprint.contracts import (
    BlueprintDraftDocument,
    BlueprintEvaluationDocument,
    BlueprintPacketDocument,
    BlueprintRepairDecisionDocument,
)
from millrace_ai.runtime.artifact_contracts import RuntimeArtifactError
from millrace_ai.runtime.effects.models import RuntimeEffectResult
from millrace_ai.runtime.effects.operation_runners.artifacts import (
    parse_required_run_artifact_as,
    read_required_run_artifact_text,
)
from millrace_ai.workspace.paths import WorkspacePaths

from .artifact_workflow_common import _read_json_model
from .candidate_evaluation import (
    _approval_draft_for_stage_result,
    _approval_packet_for_draft,
    _ApprovalBlueprintEffectError,
    _evaluator_failure_result,
    _promote_approved_blueprint_task,
    _validate_generated_task,
)

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan

MECHANIC_BLUEPRINT_REPAIR_OPERATION_ID = "mechanic_blueprint_repair_apply"

@dataclass(frozen=True, slots=True)
class _RepairBlueprintEffectError(Exception):
    failure_class: str
    message: str

    def __str__(self) -> str:
        return self.message

def mechanic_blueprint_repair_apply(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Run the compiled Mechanic Blueprint repair operation."""

    created_paths: list[str] = []
    mutation_journal: list[dict[str, JsonValue]] = []
    try:
        decision = _read_blueprint_repair_decision(compiled_plan, run_dir)
        _ensure_mechanic_report_present(compiled_plan, run_dir)
        _validate_mechanic_repair_stage_result(decision, stage_result)
        if decision.repair_action == "block_for_operator":
            raise _RepairBlueprintEffectError(
                "blueprint_repair_requested_operator",
                decision.operator_reason or "Mechanic Blueprint requested operator review",
            )
        if decision.repair_action != "apply_repaired_generated_task":
            raise _RepairBlueprintEffectError(
                "blueprint_repair_context_mismatch",
                f"repair action {decision.repair_action} is not runtime-applied by this operation",
            )

        _failed_runtime_effect_context_for_decision(run_dir, decision)
        draft, source_state = _approval_draft_for_stage_result(paths, stage_result)
        _validate_repair_decision_matches_draft(decision, draft)
        packet = _approval_packet_for_draft(paths, draft)
        evaluation = _read_repair_evaluation(run_dir)
        _validate_repair_decision_matches_approval_context(
            decision,
            packet=packet,
            evaluation=evaluation,
        )
        task = _read_repaired_generated_task(compiled_plan, run_dir)
        _validate_repaired_generated_task(
            task,
            decision=decision,
            draft=draft,
            packet=packet,
        )

        return _promote_approved_blueprint_task(
            paths,
            operation_id=MECHANIC_BLUEPRINT_REPAIR_OPERATION_ID,
            stage_result=stage_result,
            draft=draft,
            source_state=source_state,
            packet=packet,
            evaluation=evaluation,
            task=task,
            run_dir=run_dir,
            created_paths=created_paths,
            mutation_journal=mutation_journal,
        )
    except _RepairBlueprintEffectError as exc:
        return _evaluator_failure_result(
            MECHANIC_BLUEPRINT_REPAIR_OPERATION_ID,
            stage_result,
            failure_class=exc.failure_class,
            message=str(exc),
            created_paths=created_paths,
            mutation_journal=mutation_journal,
        )
    except _ApprovalBlueprintEffectError as exc:
        return _evaluator_failure_result(
            MECHANIC_BLUEPRINT_REPAIR_OPERATION_ID,
            stage_result,
            failure_class=(
                "blueprint_partial_mutation"
                if created_paths
                else "blueprint_repair_context_mismatch"
            ),
            message=str(exc),
            created_paths=created_paths,
            mutation_journal=mutation_journal,
        )
    except Exception as exc:
        return _evaluator_failure_result(
            MECHANIC_BLUEPRINT_REPAIR_OPERATION_ID,
            stage_result,
            failure_class=(
                "blueprint_partial_mutation"
                if created_paths
                else "blueprint_repair_context_mismatch"
            ),
            message=str(exc),
            created_paths=created_paths,
            mutation_journal=mutation_journal,
        )


def _read_blueprint_repair_decision(
    compiled_plan: CompiledRunPlan | None,
    run_dir: Path,
) -> BlueprintRepairDecisionDocument:
    try:
        return parse_required_run_artifact_as(
            compiled_plan,
            "blueprint_repair_decision",
            run_dir,
            BlueprintRepairDecisionDocument,
        )
    except RuntimeArtifactError as exc:
        failure_class = (
            "blueprint_repair_decision_missing"
            if exc.failure_class == "artifact_missing"
            else "blueprint_repair_decision_invalid"
        )
        raise _RepairBlueprintEffectError(failure_class, str(exc)) from exc
    except (OSError, ValueError, ValidationError) as exc:
        raise _RepairBlueprintEffectError(
            "blueprint_repair_decision_invalid",
            str(exc),
        ) from exc


def _read_repaired_generated_task(
    compiled_plan: CompiledRunPlan | None,
    run_dir: Path,
) -> TaskDocument:
    try:
        return parse_required_run_artifact_as(
            compiled_plan,
            "repaired_generated_task",
            run_dir,
            TaskDocument,
        )
    except RuntimeArtifactError as exc:
        failure_class = (
            "blueprint_repaired_generated_task_missing"
            if exc.failure_class == "artifact_missing"
            else "blueprint_repaired_generated_task_invalid"
        )
        raise _RepairBlueprintEffectError(failure_class, str(exc)) from exc
    except (OSError, ValueError, ValidationError) as exc:
        raise _RepairBlueprintEffectError(
            "blueprint_repaired_generated_task_invalid",
            str(exc),
        ) from exc


def _ensure_mechanic_report_present(
    compiled_plan: CompiledRunPlan | None,
    run_dir: Path,
) -> None:
    try:
        read_required_run_artifact_text(compiled_plan, "mechanic_report", run_dir)
    except RuntimeArtifactError as exc:
        failure_class = (
            "blueprint_repaired_generated_task_missing"
            if exc.failure_class == "artifact_missing"
            else "blueprint_repair_context_mismatch"
        )
        raise _RepairBlueprintEffectError(failure_class, str(exc)) from exc
    except OSError as exc:
        raise _RepairBlueprintEffectError(
            "blueprint_repair_context_mismatch",
            f"mechanic_report cannot be read: {exc}",
        ) from exc


def _read_repair_evaluation(run_dir: Path) -> BlueprintEvaluationDocument:
    try:
        return _read_json_model(run_dir / "blueprint_evaluation.json", BlueprintEvaluationDocument)
    except (OSError, ValueError, ValidationError, QueueStateError) as exc:
        raise _RepairBlueprintEffectError(
            "blueprint_repair_context_mismatch",
            f"failed approval evaluation cannot be verified: {exc}",
        ) from exc


def _validate_mechanic_repair_stage_result(
    decision: BlueprintRepairDecisionDocument,
    stage_result: StageResultEnvelope,
) -> None:
    _ensure_repair_context_equal(
        "stage_kind_id",
        stage_result.stage_kind_id,
        "mechanic_blueprint",
    )
    _ensure_repair_context_equal(
        "terminal_result",
        stage_result.terminal_result.value,
        "MECHANIC_BLUEPRINT_COMPLETE",
    )
    _ensure_repair_context_equal(
        "work_item_family_id",
        stage_result.work_item_family_id,
        decision.work_item_family_id,
    )
    _ensure_repair_context_equal("work_item_id", stage_result.work_item_id, decision.work_item_id)


def _failed_runtime_effect_context_for_decision(
    run_dir: Path,
    decision: BlueprintRepairDecisionDocument,
) -> StageResultEnvelope:
    stage_results_dir = run_dir / "stage_results"
    for path in sorted(stage_results_dir.glob("*.json")):
        try:
            stage_result = StageResultEnvelope.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            continue
        if _stage_result_matches_repair_decision(stage_result, decision):
            return stage_result
    raise _RepairBlueprintEffectError(
        "blueprint_repair_context_mismatch",
        "no failed runtime-effect stage result matches blueprint_repair_decision.json",
    )


def _stage_result_matches_repair_decision(
    stage_result: StageResultEnvelope,
    decision: BlueprintRepairDecisionDocument,
) -> bool:
    metadata = stage_result.metadata
    return (
        stage_result.run_id == decision.failed_run_id
        and stage_result.stage_kind_id == decision.failed_stage_kind_id
        and stage_result.node_id == decision.failed_node_id
        and stage_result.terminal_result.value == decision.failed_terminal_result
        and stage_result.work_item_family_id == decision.work_item_family_id
        and stage_result.work_item_id == decision.work_item_id
        and _stage_result_matches_failed_effect_identity(metadata, decision)
        and metadata.get("runtime_effect_decision") == "request_block_source"
        and metadata.get("runtime_effect_failure_class") == decision.failure_class
        and metadata.get("runtime_effect_mutation_phase") == decision.mutation_phase
    )


def _stage_result_matches_failed_effect_identity(
    metadata: Mapping[str, object],
    decision: BlueprintRepairDecisionDocument,
) -> bool:
    if (
        decision.failed_runner_id is not None
        and metadata.get("runtime_effect_runner_id") != decision.failed_runner_id
    ):
        return False
    if decision.failed_operation_id is not None:
        if metadata.get("runtime_effect_operation_id") != decision.failed_operation_id:
            return False
        if (
            decision.failed_handler_id is not None
            and decision.failed_handler_id != decision.failed_operation_id
            and not _stage_result_matches_legacy_failed_handler(metadata, decision)
        ):
            return False
        return True
    return _stage_result_matches_legacy_failed_handler(metadata, decision)


def _stage_result_matches_legacy_failed_handler(
    metadata: Mapping[str, object],
    decision: BlueprintRepairDecisionDocument,
) -> bool:
    handler_values = {
        metadata.get("runtime_effect_handler_id"),
        metadata.get("runtime_effect_legacy_handler_id"),
    }
    expected_values = {
        decision.failed_handler_id,
        decision.legacy_failed_handler_id,
    }
    return any(expected is not None and expected in handler_values for expected in expected_values)


def _validate_repair_decision_matches_draft(
    decision: BlueprintRepairDecisionDocument,
    draft: BlueprintDraftDocument,
) -> None:
    _ensure_repair_context_equal("work_item_id", decision.work_item_id, draft.draft_id)
    _ensure_repair_context_equal("draft_id", decision.draft_id, draft.draft_id)
    _ensure_repair_context_equal("manifest_id", decision.manifest_id, draft.manifest_id)
    _ensure_repair_context_equal("root_spec_id", decision.root_spec_id, draft.root_spec_id)
    _ensure_repair_context_equal("root_idea_id", decision.root_idea_id, draft.root_idea_id)


def _validate_repair_decision_matches_approval_context(
    decision: BlueprintRepairDecisionDocument,
    *,
    packet: BlueprintPacketDocument,
    evaluation: BlueprintEvaluationDocument,
) -> None:
    try:
        decision.ensure_matches_packet(packet)
        decision.ensure_matches_evaluation(evaluation)
    except ValueError as exc:
        raise _RepairBlueprintEffectError(
            "blueprint_repair_context_mismatch",
            str(exc),
        ) from exc


def _validate_repaired_generated_task(
    task: TaskDocument,
    *,
    decision: BlueprintRepairDecisionDocument,
    draft: BlueprintDraftDocument,
    packet: BlueprintPacketDocument,
) -> None:
    try:
        decision.ensure_matches_repaired_generated_task(task)
        _validate_generated_task(task, draft, packet)
    except ValueError as exc:
        raise _RepairBlueprintEffectError(
            "blueprint_repaired_generated_task_invalid",
            str(exc),
        ) from exc


def _ensure_repair_context_equal(field_name: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise _RepairBlueprintEffectError(
            "blueprint_repair_context_mismatch",
            f"{field_name} mismatch",
        )

__all__ = [
    "MECHANIC_BLUEPRINT_REPAIR_OPERATION_ID",
    "mechanic_blueprint_repair_apply",
]
