"""Compiled-plan workflow-authority compatibility helpers."""

from __future__ import annotations

from millrace_ai.architecture import CompiledRunPlan

_REQUIRED_WORKFLOW_AUTHORITY_ASSET_FAMILIES = frozenset(
    {
        "artifact_contract",
        "runtime_effect_operation",
        "runtime_effect_primitive",
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
        or not plan.runtime_effect_primitives_by_id
        or not plan.effect_stores_by_id
        or not plan.effect_validators_by_id
        or not plan.request_context_profiles_by_id
        or not plan.request_context_providers_by_id
        or not plan.request_context_render_plans_by_id
    ):
        return False

    if plan.scheduler_policy is None:
        return False

    asset_families = {ref.asset_family for ref in plan.resolved_assets}
    if not _REQUIRED_WORKFLOW_AUTHORITY_ASSET_FAMILIES <= asset_families:
        return False

    asset_logical_ids = {ref.logical_id for ref in plan.resolved_assets}

    if plan.scheduler_policy_authority_kind is None:
        return False
    if plan.scheduler_policy_authority_kind == "registry":
        if not plan.selected_scheduler_policy_asset_id:
            return False
        if f"scheduler_policy:{plan.selected_scheduler_policy_asset_id}" not in asset_logical_ids:
            return False
    elif plan.selected_scheduler_policy_asset_id is not None:
        return False

    selected_recovery_policy_ids = plan.selected_workflow_recovery_policy_ids
    if selected_recovery_policy_ids is None:
        return False

    selected_recovery_policy_id_set = set(selected_recovery_policy_ids)
    if len(selected_recovery_policy_id_set) != len(selected_recovery_policy_ids):
        return False

    recovery_policy_id_set = set(plan.workflow_recovery_policies_by_id)
    if recovery_policy_id_set != selected_recovery_policy_id_set:
        return False

    for policy_id in selected_recovery_policy_ids:
        definition = plan.workflow_recovery_policies_by_id.get(policy_id)
        if definition is None or definition.policy_id != policy_id:
            return False
        if f"workflow_recovery_policy:{policy_id}" not in asset_logical_ids:
            return False

    return True


__all__ = ["has_required_workflow_authority"]
