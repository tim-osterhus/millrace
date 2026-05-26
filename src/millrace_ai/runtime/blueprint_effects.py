"""Compatibility facades for Blueprint runtime effect operations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.contracts import StageResultEnvelope
from millrace_ai.runtime.effects import RuntimeEffectResult
from millrace_ai.workspace.paths import WorkspacePaths

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan

MANAGER_BLUEPRINT_HANDLER_ID = "manager_blueprint_manifest_to_blueprint_drafts"
CONTRACTOR_BLUEPRINT_HANDLER_ID = "contractor_blueprint_candidate_persist"
EVALUATOR_BLUEPRINT_APPROVAL_HANDLER_ID = "evaluator_blueprint_approved_to_task"
EVALUATOR_BLUEPRINT_REJECTION_HANDLER_ID = "evaluator_blueprint_rejected_to_draft_revision"
MECHANIC_BLUEPRINT_REPAIR_HANDLER_ID = "mechanic_blueprint_repair_apply"


def manager_blueprint_manifest_to_blueprint_drafts(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Compatibility facade for the Manager Blueprint runtime operation."""

    from .effects import operations

    return operations.manager_blueprint_manifest_to_blueprint_drafts(
        paths,
        stage_result,
        run_dir,
        compiled_plan,
    )


def contractor_blueprint_candidate_persist(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Compatibility facade for the Contractor Blueprint runtime operation."""

    from .effects import operations

    return operations.contractor_blueprint_candidate_persist(
        paths,
        stage_result,
        run_dir,
        compiled_plan,
    )


def evaluator_blueprint_approved_to_task(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Compatibility facade for the Evaluator Blueprint approval operation."""

    from .effects import operations

    return operations.evaluator_blueprint_approved_to_task(
        paths,
        stage_result,
        run_dir,
        compiled_plan,
    )


def evaluator_blueprint_rejected_to_draft_revision(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Compatibility facade for the Evaluator Blueprint rejection operation."""

    from .effects import operations

    return operations.evaluator_blueprint_rejected_to_draft_revision(
        paths,
        stage_result,
        run_dir,
        compiled_plan,
    )


def mechanic_blueprint_repair_apply(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Compatibility facade for the Mechanic Blueprint repair operation."""

    from .effects import operations

    return operations.mechanic_blueprint_repair_apply(
        paths,
        stage_result,
        run_dir,
        compiled_plan,
    )


__all__ = [
    "CONTRACTOR_BLUEPRINT_HANDLER_ID",
    "EVALUATOR_BLUEPRINT_APPROVAL_HANDLER_ID",
    "EVALUATOR_BLUEPRINT_REJECTION_HANDLER_ID",
    "MANAGER_BLUEPRINT_HANDLER_ID",
    "MECHANIC_BLUEPRINT_REPAIR_HANDLER_ID",
    "contractor_blueprint_candidate_persist",
    "evaluator_blueprint_approved_to_task",
    "evaluator_blueprint_rejected_to_draft_revision",
    "manager_blueprint_manifest_to_blueprint_drafts",
    "mechanic_blueprint_repair_apply",
]
