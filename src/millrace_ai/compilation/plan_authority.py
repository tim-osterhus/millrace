"""Compiled-plan workflow-authority compatibility helpers."""

from __future__ import annotations

from millrace_ai.architecture import CompiledRunPlan

_REQUIRED_WORKFLOW_AUTHORITY_ASSET_FAMILIES = frozenset(
    {
        "artifact_contract",
        "runtime_effect_operation",
        "runtime_effect_runner",
        "runtime_effect_store",
        "runtime_effect_validator",
        "request_context_profile",
        "request_context_provider",
        "request_context_render_plan",
    }
)


def has_required_workflow_authority(plan: CompiledRunPlan) -> bool:
    """Return whether a persisted plan carries v0.20 workflow authority data."""

    if (
        not plan.artifact_contracts_by_id
        or not plan.artifact_contracts
        or not plan.runtime_effect_runners_by_id
        or not plan.runtime_effect_operations_by_id
        or not plan.effect_stores_by_id
        or not plan.effect_validators_by_id
        or not plan.request_context_profiles_by_id
        or not plan.request_context_providers_by_id
        or not plan.request_context_render_plans_by_id
    ):
        return False

    asset_families = {ref.asset_family for ref in plan.resolved_assets}
    return _REQUIRED_WORKFLOW_AUTHORITY_ASSET_FAMILIES <= asset_families


__all__ = ["has_required_workflow_authority"]
