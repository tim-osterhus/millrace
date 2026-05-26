"""Legacy Python runtime effect handler registrations."""

from __future__ import annotations

from .. import blueprint_effects, planner_effects
from . import operations
from .registry import RuntimeEffectHandlerRegistration, RuntimeEffectHandlerRegistry

LEGACY_PYTHON_EFFECT_RUNNER_ID = "legacy_python_handler"


def legacy_runtime_effect_handler_registrations() -> tuple[RuntimeEffectHandlerRegistration, ...]:
    return (
        RuntimeEffectHandlerRegistration(
            handler_id=planner_effects.PLANNER_DISPOSITION_HANDLER_ID,
            runner_id=LEGACY_PYTHON_EFFECT_RUNNER_ID,
            handler=planner_effects.planner_disposition,
        ),
        RuntimeEffectHandlerRegistration(
            handler_id=blueprint_effects.MANAGER_BLUEPRINT_HANDLER_ID,
            runner_id=LEGACY_PYTHON_EFFECT_RUNNER_ID,
            handler=operations.manager_blueprint_manifest_to_blueprint_drafts,
        ),
        RuntimeEffectHandlerRegistration(
            handler_id=blueprint_effects.CONTRACTOR_BLUEPRINT_HANDLER_ID,
            runner_id=LEGACY_PYTHON_EFFECT_RUNNER_ID,
            handler=operations.contractor_blueprint_candidate_persist,
        ),
        RuntimeEffectHandlerRegistration(
            handler_id=blueprint_effects.EVALUATOR_BLUEPRINT_APPROVAL_HANDLER_ID,
            runner_id=LEGACY_PYTHON_EFFECT_RUNNER_ID,
            handler=blueprint_effects.evaluator_blueprint_approved_to_task,
        ),
        RuntimeEffectHandlerRegistration(
            handler_id=blueprint_effects.EVALUATOR_BLUEPRINT_REJECTION_HANDLER_ID,
            runner_id=LEGACY_PYTHON_EFFECT_RUNNER_ID,
            handler=blueprint_effects.evaluator_blueprint_rejected_to_draft_revision,
        ),
        RuntimeEffectHandlerRegistration(
            handler_id=blueprint_effects.MECHANIC_BLUEPRINT_REPAIR_HANDLER_ID,
            runner_id=LEGACY_PYTHON_EFFECT_RUNNER_ID,
            handler=blueprint_effects.mechanic_blueprint_repair_apply,
        ),
    )


def default_legacy_runtime_effect_handler_registry() -> RuntimeEffectHandlerRegistry:
    return RuntimeEffectHandlerRegistry.from_registrations(
        legacy_runtime_effect_handler_registrations()
    )


__all__ = [
    "LEGACY_PYTHON_EFFECT_RUNNER_ID",
    "default_legacy_runtime_effect_handler_registry",
    "legacy_runtime_effect_handler_registrations",
]
