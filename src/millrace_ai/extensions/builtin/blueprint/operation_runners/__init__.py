"""Blueprint runtime-effect operation runners."""

from __future__ import annotations

from millrace_ai.runtime.effects.registry import RuntimeEffectHandlerRegistration

from . import candidate_evaluation, candidate_packet, decomposition_manifest, repair_application


def artifact_runtime_effect_handler_registrations(
    runner_id: str,
) -> tuple[RuntimeEffectHandlerRegistration, ...]:
    return (
        RuntimeEffectHandlerRegistration(
            handler_id=decomposition_manifest.MANAGER_BLUEPRINT_OPERATION_ID,
            runner_id=runner_id,
            handler=decomposition_manifest.manager_blueprint_manifest_to_blueprint_drafts,
        ),
        RuntimeEffectHandlerRegistration(
            handler_id=candidate_packet.CONTRACTOR_BLUEPRINT_OPERATION_ID,
            runner_id=runner_id,
            handler=candidate_packet.contractor_blueprint_candidate_persist,
        ),
        RuntimeEffectHandlerRegistration(
            handler_id=candidate_evaluation.EVALUATOR_BLUEPRINT_APPROVAL_OPERATION_ID,
            runner_id=runner_id,
            handler=candidate_evaluation.evaluator_blueprint_approved_to_task,
        ),
        RuntimeEffectHandlerRegistration(
            handler_id=candidate_evaluation.EVALUATOR_BLUEPRINT_REJECTION_OPERATION_ID,
            runner_id=runner_id,
            handler=candidate_evaluation.evaluator_blueprint_rejected_to_draft_revision,
        ),
        RuntimeEffectHandlerRegistration(
            handler_id=repair_application.MECHANIC_BLUEPRINT_REPAIR_OPERATION_ID,
            runner_id=runner_id,
            handler=repair_application.mechanic_blueprint_repair_apply,
        ),
    )


__all__ = [
    "artifact_runtime_effect_handler_registrations",
]
