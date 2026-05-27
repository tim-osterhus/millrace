"""Compiler validation facade orchestrating focused validator families."""

from __future__ import annotations

from millrace_ai.architecture import FrozenGraphPlanePlan, RegisteredStageKindDefinition
from millrace_ai.assets import WorkflowPrimitiveBundle
from millrace_ai.contracts import ModeDefinition, Plane

from .artifacts import validate_artifact_contracts, validate_document_adapters
from .failure_policies import (
    validate_recovery_policies,
    validate_runtime_failure_policies,
)
from .graphs import (
    graph_nodes_by_id,
    validate_graph_terminal_artifact_references,
    validate_structural_graph_smoke,
)
from .lane_conflicts import validate_lane_conflict_coverage
from .lifecycle import validate_lifecycle_plans, validate_terminal_actions
from .modes import validate_mode_stage_maps
from .request_context_profiles import validate_request_context_profiles
from .runtime_effects import (
    validate_runtime_effect_handlers,
    validate_runtime_effect_operations,
    validate_runtime_effect_rules,
)
from .stages import (
    stage_kinds_by_node_id,
    validate_entry_coverage,
    validate_runtime_failure_recovery,
    validate_stage_artifact_references,
)
from .work_families import (
    queue_policies_by_plane,
    validate_graph_entries_are_claimable,
    validate_queue_claim_policies,
    validate_queue_lifecycle_adapters,
)


def validate_workflow_primitives(
    *,
    mode: ModeDefinition,
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
    workflow_primitives: WorkflowPrimitiveBundle,
) -> None:
    families_by_id = {family.family_id: family for family in workflow_primitives.work_item_families}
    artifact_contracts_by_id = {
        contract.artifact_id: contract
        for contract in workflow_primitives.artifact_contracts
    }
    adapters_by_id = {
        adapter.adapter_id: adapter
        for adapter in workflow_primitives.document_adapters
    }
    lifecycle_plans_by_id = {
        plan.plan_id: plan
        for plan in workflow_primitives.lifecycle_mutation_plans
    }
    terminal_actions_by_id = {
        action.terminal_action_id: action
        for action in workflow_primitives.terminal_actions
    }
    runtime_effect_handlers_by_id = {
        handler.handler_id: handler
        for handler in workflow_primitives.runtime_effect_handlers
    }
    runtime_effect_runners_by_id = {
        runner.runner_id: runner
        for runner in workflow_primitives.runtime_effect_runners
    }
    runtime_effect_rules_by_id = {
        rule.rule_id: rule
        for rule in workflow_primitives.runtime_effect_rules
    }
    effect_stores_by_id = {
        store.store_id: store
        for store in workflow_primitives.effect_stores
    }
    effect_validators_by_id = {
        validator.validator_id: validator
        for validator in workflow_primitives.effect_validators
    }
    runtime_effect_operations_by_id = {
        operation.operation_id: operation
        for operation in workflow_primitives.runtime_effect_operations
    }
    request_context_profiles_by_id = {
        profile.profile_id: profile
        for profile in workflow_primitives.request_context_profiles
    }
    request_context_providers_by_id = {
        provider.provider_id: provider
        for provider in workflow_primitives.request_context_providers
    }
    request_context_render_plans_by_id = {
        render_plan.render_plan_id: render_plan
        for render_plan in workflow_primitives.request_context_render_plans
    }
    claim_policies_by_plane = queue_policies_by_plane(workflow_primitives)
    node_plans_by_id = graph_nodes_by_id(graphs_by_plane.values())
    stage_kinds_by_node = stage_kinds_by_node_id(
        graph_nodes_by_id=node_plans_by_id,
        stage_kinds=stage_kinds,
    )
    terminal_state_ids = {
        state.terminal_state_id
        for graph in graphs_by_plane.values()
        for state in graph.terminal_states
    }

    validate_document_adapters(families_by_id, adapters_by_id)
    validate_artifact_contracts(
        artifact_contracts_by_id=artifact_contracts_by_id,
        families_by_id=families_by_id,
        document_adapters_by_id=adapters_by_id,
        runtime_effect_handlers_by_id=runtime_effect_handlers_by_id,
        runtime_effect_operations_by_id=runtime_effect_operations_by_id,
        runtime_effect_runners_by_id=runtime_effect_runners_by_id,
        stage_kinds=stage_kinds,
    )
    validate_stage_artifact_references(
        artifact_contracts_by_id=artifact_contracts_by_id,
        stage_kinds=stage_kinds,
    )
    validate_graph_terminal_artifact_references(
        artifact_contracts_by_id=artifact_contracts_by_id,
        graphs_by_plane=graphs_by_plane,
    )
    validate_request_context_profiles(
        artifact_contracts_by_id=artifact_contracts_by_id,
        graphs_by_plane=graphs_by_plane,
        request_context_profiles_by_id=request_context_profiles_by_id,
        request_context_providers_by_id=request_context_providers_by_id,
        request_context_render_plans_by_id=request_context_render_plans_by_id,
        workflow_primitives=workflow_primitives,
    )
    validate_queue_claim_policies(
        mode=mode,
        families_by_id=families_by_id,
        queue_policies_by_plane=claim_policies_by_plane,
    )
    validate_queue_lifecycle_adapters(
        families_by_id=families_by_id,
        queue_policies_by_plane=claim_policies_by_plane,
    )
    validate_graph_entries_are_claimable(
        graphs_by_plane=graphs_by_plane,
        families_by_id=families_by_id,
        queue_policies_by_plane=claim_policies_by_plane,
    )
    validate_entry_coverage(
        graphs_by_plane=graphs_by_plane,
        families_by_id=families_by_id,
        stage_kinds=stage_kinds,
    )
    validate_terminal_actions(
        graphs_by_plane=graphs_by_plane,
        terminal_actions_by_id=terminal_actions_by_id,
        lifecycle_plans_by_id=lifecycle_plans_by_id,
        runtime_effect_rules_by_id=runtime_effect_rules_by_id,
    )
    validate_lifecycle_plans(
        families_by_id=families_by_id,
        stage_kinds=stage_kinds,
        lifecycle_plan_ids=lifecycle_plans_by_id,
    )
    validate_runtime_effect_handlers(
        artifact_contracts_by_id=artifact_contracts_by_id,
        families_by_id=families_by_id,
        runtime_effect_handlers_by_id=runtime_effect_handlers_by_id,
    )
    validate_runtime_effect_rules(
        artifact_contracts_by_id=artifact_contracts_by_id,
        families_by_id=families_by_id,
        lifecycle_plans_by_id=lifecycle_plans_by_id,
        runtime_effect_handlers_by_id=runtime_effect_handlers_by_id,
        runtime_effect_operations_by_id=runtime_effect_operations_by_id,
        runtime_effect_runners_by_id=runtime_effect_runners_by_id,
        runtime_effect_rules_by_id=runtime_effect_rules_by_id,
        stage_kinds_by_node_id=stage_kinds_by_node,
        stage_kinds=stage_kinds,
    )
    validate_runtime_effect_operations(
        artifact_contracts_by_id=artifact_contracts_by_id,
        runtime_effect_rules_by_id=runtime_effect_rules_by_id,
        effect_stores_by_id=effect_stores_by_id,
        effect_validators_by_id=effect_validators_by_id,
        runtime_effect_operations_by_id=runtime_effect_operations_by_id,
        runtime_effect_runners_by_id=runtime_effect_runners_by_id,
    )
    validate_recovery_policies(
        workflow_primitives=workflow_primitives,
        stage_kinds_by_node_id=stage_kinds_by_node,
        stage_kinds=stage_kinds,
        terminal_state_ids=terminal_state_ids,
    )
    validate_runtime_failure_policies(
        workflow_primitives=workflow_primitives,
        artifact_contracts_by_id=artifact_contracts_by_id,
        families_by_id=families_by_id,
        runtime_effect_handlers_by_id=runtime_effect_handlers_by_id,
        runtime_effect_operations_by_id=runtime_effect_operations_by_id,
        runtime_effect_runners_by_id=runtime_effect_runners_by_id,
        runtime_effect_rules_by_id=runtime_effect_rules_by_id,
        graphs_by_plane=graphs_by_plane,
        stage_kinds_by_node_id=stage_kinds_by_node,
        stage_kinds=stage_kinds,
        terminal_state_ids=terminal_state_ids,
    )
    validate_runtime_failure_recovery(
        graphs_by_plane=graphs_by_plane,
        stage_kinds_by_node_id=stage_kinds_by_node,
    )
    validate_structural_graph_smoke(
        graphs_by_plane=graphs_by_plane,
        stage_kinds=stage_kinds,
        terminal_actions_by_id=terminal_actions_by_id,
    )


__all__ = [
    "validate_lane_conflict_coverage",
    "validate_mode_stage_maps",
    "validate_workflow_primitives",
]
