"""Compatibility facade for Blueprint runtime-effect operation runners."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.contracts import StageResultEnvelope
from millrace_ai.workspace.paths import WorkspacePaths

from .models import RuntimeEffectResult
from .operation_runners import blueprint_contractor, blueprint_evaluator, blueprint_manager, blueprint_mechanic

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan

MANAGER_BLUEPRINT_OPERATION_ID = blueprint_manager.MANAGER_BLUEPRINT_OPERATION_ID
CONTRACTOR_BLUEPRINT_OPERATION_ID = blueprint_contractor.CONTRACTOR_BLUEPRINT_OPERATION_ID
EVALUATOR_BLUEPRINT_APPROVAL_OPERATION_ID = blueprint_evaluator.EVALUATOR_BLUEPRINT_APPROVAL_OPERATION_ID
EVALUATOR_BLUEPRINT_REJECTION_OPERATION_ID = blueprint_evaluator.EVALUATOR_BLUEPRINT_REJECTION_OPERATION_ID
MECHANIC_BLUEPRINT_REPAIR_OPERATION_ID = blueprint_mechanic.MECHANIC_BLUEPRINT_REPAIR_OPERATION_ID

# Deprecated import aliases retained for older tests and downstream diagnostics.
# New code should patch the focused runner modules for implementation behavior.
enqueue_blueprint_draft = blueprint_manager.enqueue_blueprint_draft
enqueue_task = blueprint_evaluator.enqueue_task
persist_blueprint_critique = blueprint_evaluator.persist_blueprint_critique
_normalized_markdown_sha256 = blueprint_evaluator._normalized_markdown_sha256


def manager_blueprint_manifest_to_blueprint_drafts(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Compatibility facade for the Manager Blueprint runtime operation."""

    return blueprint_manager.manager_blueprint_manifest_to_blueprint_drafts(paths, stage_result, run_dir, compiled_plan)


def contractor_blueprint_candidate_persist(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Compatibility facade for the Contractor Blueprint runtime operation."""

    return blueprint_contractor.contractor_blueprint_candidate_persist(paths, stage_result, run_dir, compiled_plan)


def evaluator_blueprint_approved_to_task(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Compatibility facade for the Evaluator Blueprint approval operation."""

    return blueprint_evaluator.evaluator_blueprint_approved_to_task(paths, stage_result, run_dir, compiled_plan)


def evaluator_blueprint_rejected_to_draft_revision(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Compatibility facade for the Evaluator Blueprint rejection operation."""

    return blueprint_evaluator.evaluator_blueprint_rejected_to_draft_revision(paths, stage_result, run_dir, compiled_plan)


def mechanic_blueprint_repair_apply(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Compatibility facade for the Mechanic Blueprint repair operation."""

    return blueprint_mechanic.mechanic_blueprint_repair_apply(paths, stage_result, run_dir, compiled_plan)


__all__ = [
    "CONTRACTOR_BLUEPRINT_OPERATION_ID",
    "EVALUATOR_BLUEPRINT_APPROVAL_OPERATION_ID",
    "EVALUATOR_BLUEPRINT_REJECTION_OPERATION_ID",
    "MANAGER_BLUEPRINT_OPERATION_ID",
    "MECHANIC_BLUEPRINT_REPAIR_OPERATION_ID",
    "contractor_blueprint_candidate_persist",
    "evaluator_blueprint_approved_to_task",
    "evaluator_blueprint_rejected_to_draft_revision",
    "manager_blueprint_manifest_to_blueprint_drafts",
    "mechanic_blueprint_repair_apply",
]
