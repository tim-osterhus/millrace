"""Runtime-effect operation runner package."""

from __future__ import annotations

from ..registry import RuntimeEffectHandlerRegistration
from . import blueprint_contractor, blueprint_evaluator, blueprint_manager, blueprint_mechanic


def blueprint_runtime_effect_handler_registrations(
    runner_id: str,
) -> tuple[RuntimeEffectHandlerRegistration, ...]:
    return (
        RuntimeEffectHandlerRegistration(
            handler_id=blueprint_manager.MANAGER_BLUEPRINT_OPERATION_ID,
            runner_id=runner_id,
            handler=blueprint_manager.manager_blueprint_manifest_to_blueprint_drafts,
        ),
        RuntimeEffectHandlerRegistration(
            handler_id=blueprint_contractor.CONTRACTOR_BLUEPRINT_OPERATION_ID,
            runner_id=runner_id,
            handler=blueprint_contractor.contractor_blueprint_candidate_persist,
        ),
        RuntimeEffectHandlerRegistration(
            handler_id=blueprint_evaluator.EVALUATOR_BLUEPRINT_APPROVAL_OPERATION_ID,
            runner_id=runner_id,
            handler=blueprint_evaluator.evaluator_blueprint_approved_to_task,
        ),
        RuntimeEffectHandlerRegistration(
            handler_id=blueprint_evaluator.EVALUATOR_BLUEPRINT_REJECTION_OPERATION_ID,
            runner_id=runner_id,
            handler=blueprint_evaluator.evaluator_blueprint_rejected_to_draft_revision,
        ),
        RuntimeEffectHandlerRegistration(
            handler_id=blueprint_mechanic.MECHANIC_BLUEPRINT_REPAIR_OPERATION_ID,
            runner_id=runner_id,
            handler=blueprint_mechanic.mechanic_blueprint_repair_apply,
        ),
    )


__all__ = [
    "blueprint_contractor",
    "blueprint_evaluator",
    "blueprint_manager",
    "blueprint_mechanic",
    "blueprint_runtime_effect_handler_registrations",
]
