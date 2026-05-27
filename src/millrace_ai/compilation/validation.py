"""Compiler validation helpers for mode maps and workflow primitive authority."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath

from millrace_ai.architecture import (
    ArtifactContractDefinition,
    ArtifactFilenameAdapterDefinition,
    ArtifactFormat,
    FrozenGraphPlanePlan,
    GraphLoopEntryKey,
    LifecycleMutationPlanDefinition,
    MaterializedGraphNodePlan,
    PlaneQueueClaimPolicyDefinition,
    RecoveryRole,
    RegisteredStageKindDefinition,
    RequestContextProfileDefinition,
    RequestContextProviderDefinition,
    RequestContextRenderPlan,
    RuntimeEffectHandlerDefinition,
    RuntimeEffectOperationDefinition,
    RuntimeEffectOperationRunnerDefinition,
    RuntimeEffectRuleDefinition,
    RuntimeFailurePolicyDefinition,
    TerminalActionDefinition,
    WorkflowPlaneSchedulerPolicyDefinition,
    WorkItemDocumentAdapterDefinition,
    WorkItemFamilyDefinition,
)
from millrace_ai.architecture.loop_graphs import graph_loop_entry_key_value
from millrace_ai.assets import WorkflowPrimitiveBundle
from millrace_ai.contracts import ModeDefinition, Plane, StageMapKey
from millrace_ai.contracts.stage_metadata import stage_plane
from millrace_ai.workspace.family_adapters import (
    queue_adapter_for_id,
    resolve_queue_lifecycle_adapter_id,
)

from .effect_operations import validate_runtime_effect_operations
from .outcomes import CompilerValidationError

_MECHANIC_BLUEPRINT_NODE_ID = "mechanic_blueprint"
_MECHANIC_BLUEPRINT_REPAIR_HANDLER_ID = "mechanic_blueprint_repair_apply"
_MECHANIC_BLUEPRINT_REPAIR_OUTCOME = "MECHANIC_BLUEPRINT_COMPLETE"
_MECHANIC_BLUEPRINT_REPAIR_ARTIFACTS = frozenset(
    {
        "blueprint_repair_decision",
        "mechanic_report",
        "repaired_generated_task",
    }
)
_MECHANIC_BLUEPRINT_REPAIR_CAPABILITY = "repair.apply_repaired_generated_task"
_BUILT_IN_ARTIFACT_ADAPTER_IDS = frozenset(
    {
        "builtin.json",
        "builtin.markdown",
        "builtin.text",
        "builtin.directory",
    }
)
_BUILT_IN_ARTIFACT_ADAPTER_FORMATS = {
    "builtin.json": ArtifactFormat.JSON,
    "builtin.markdown": ArtifactFormat.MARKDOWN,
    "builtin.text": ArtifactFormat.TEXT,
    "builtin.directory": ArtifactFormat.DIRECTORY,
}


def validate_mode_stage_maps(mode: ModeDefinition, selected_stages: set[StageMapKey]) -> None:
    for map_name, mapping in (
        ("stage_entrypoint_overrides", mode.stage_entrypoint_overrides),
        ("stage_skill_additions", mode.stage_skill_additions),
        ("stage_model_bindings", mode.stage_model_bindings),
        ("stage_runner_bindings", mode.stage_runner_bindings),
        ("stage_thinking_bindings", mode.stage_thinking_bindings),
    ):
        for stage in sorted(mapping, key=_stage_key_value):
            if stage not in selected_stages:
                raise CompilerValidationError(
                    "Mode map "
                    f"`{map_name}` references stage outside selected loops: {_stage_key_value(stage)}"
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
    queue_policies_by_plane = _queue_policies_by_plane(workflow_primitives)
    graph_nodes_by_id = _graph_nodes_by_id(graphs_by_plane.values())
    stage_kinds_by_node_id = {
        node.node_id: stage_kinds[node.stage_kind_id]
        for node in graph_nodes_by_id.values()
    }
    terminal_state_ids = {
        state.terminal_state_id
        for graph in graphs_by_plane.values()
        for state in graph.terminal_states
    }

    _validate_document_adapters(families_by_id, adapters_by_id)
    _validate_artifact_contracts(
        artifact_contracts_by_id=artifact_contracts_by_id,
        families_by_id=families_by_id,
        document_adapters_by_id=adapters_by_id,
        runtime_effect_handlers_by_id=runtime_effect_handlers_by_id,
        runtime_effect_operations_by_id=runtime_effect_operations_by_id,
        runtime_effect_runners_by_id=runtime_effect_runners_by_id,
        stage_kinds=stage_kinds,
    )
    _validate_stage_artifact_references(
        artifact_contracts_by_id=artifact_contracts_by_id,
        stage_kinds=stage_kinds,
    )
    _validate_graph_terminal_artifact_references(
        artifact_contracts_by_id=artifact_contracts_by_id,
        graphs_by_plane=graphs_by_plane,
    )
    _validate_request_context_profiles(
        artifact_contracts_by_id=artifact_contracts_by_id,
        graphs_by_plane=graphs_by_plane,
        request_context_profiles_by_id=request_context_profiles_by_id,
        request_context_providers_by_id=request_context_providers_by_id,
        request_context_render_plans_by_id=request_context_render_plans_by_id,
        workflow_primitives=workflow_primitives,
    )
    _validate_queue_claim_policies(
        mode=mode,
        families_by_id=families_by_id,
        queue_policies_by_plane=queue_policies_by_plane,
    )
    _validate_queue_lifecycle_adapters(
        families_by_id=families_by_id,
        queue_policies_by_plane=queue_policies_by_plane,
    )
    _validate_graph_entries_are_claimable(
        graphs_by_plane=graphs_by_plane,
        families_by_id=families_by_id,
        queue_policies_by_plane=queue_policies_by_plane,
    )
    _validate_entry_coverage(
        graphs_by_plane=graphs_by_plane,
        families_by_id=families_by_id,
        stage_kinds=stage_kinds,
    )
    _validate_terminal_actions(
        graphs_by_plane=graphs_by_plane,
        terminal_actions_by_id=terminal_actions_by_id,
        lifecycle_plans_by_id=lifecycle_plans_by_id,
        runtime_effect_rules_by_id=runtime_effect_rules_by_id,
    )
    _validate_lifecycle_plans(
        families_by_id=families_by_id,
        stage_kinds=stage_kinds,
        lifecycle_plan_ids=lifecycle_plans_by_id,
    )
    _validate_runtime_effect_handlers(
        artifact_contracts_by_id=artifact_contracts_by_id,
        families_by_id=families_by_id,
        runtime_effect_handlers_by_id=runtime_effect_handlers_by_id,
    )
    _validate_runtime_effect_rules(
        artifact_contracts_by_id=artifact_contracts_by_id,
        families_by_id=families_by_id,
        lifecycle_plans_by_id=lifecycle_plans_by_id,
        runtime_effect_handlers_by_id=runtime_effect_handlers_by_id,
        runtime_effect_operations_by_id=runtime_effect_operations_by_id,
        runtime_effect_runners_by_id=runtime_effect_runners_by_id,
        runtime_effect_rules_by_id=runtime_effect_rules_by_id,
        stage_kinds_by_node_id=stage_kinds_by_node_id,
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
    _validate_recovery_policies(
        workflow_primitives=workflow_primitives,
        stage_kinds_by_node_id=stage_kinds_by_node_id,
        stage_kinds=stage_kinds,
        terminal_state_ids=terminal_state_ids,
    )
    _validate_runtime_failure_policies(
        workflow_primitives=workflow_primitives,
        families_by_id=families_by_id,
        runtime_effect_handlers_by_id=runtime_effect_handlers_by_id,
        runtime_effect_operations_by_id=runtime_effect_operations_by_id,
        runtime_effect_runners_by_id=runtime_effect_runners_by_id,
        runtime_effect_rules_by_id=runtime_effect_rules_by_id,
        graphs_by_plane=graphs_by_plane,
        stage_kinds_by_node_id=stage_kinds_by_node_id,
        stage_kinds=stage_kinds,
        terminal_state_ids=terminal_state_ids,
    )
    _validate_runtime_failure_recovery(
        graphs_by_plane=graphs_by_plane,
        stage_kinds_by_node_id=stage_kinds_by_node_id,
    )
    _validate_structural_graph_smoke(
        graphs_by_plane=graphs_by_plane,
        stage_kinds=stage_kinds,
        terminal_actions_by_id=terminal_actions_by_id,
    )


def _validate_runtime_failure_recovery(
    *,
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    stage_kinds_by_node_id: dict[str, RegisteredStageKindDefinition],
) -> None:
    for graph in graphs_by_plane.values():
        recovery = graph.runtime_failure_recovery
        if recovery is None:
            continue
        repair_node = next(
            (node for node in graph.nodes if node.node_id == recovery.default_repair_node_id),
            None,
        )
        if repair_node is None:
            raise CompilerValidationError(
                f"graph {graph.loop_id} runtime failure recovery references unknown "
                f"default repair node {recovery.default_repair_node_id}"
            )
        stage_kind = stage_kinds_by_node_id.get(repair_node.node_id)
        if stage_kind is None:
            raise CompilerValidationError(
                f"graph {graph.loop_id} runtime failure recovery node "
                f"{repair_node.node_id} has no registered stage kind"
            )
        if stage_kind.plane is not graph.plane:
            raise CompilerValidationError(
                f"graph {graph.loop_id} runtime failure recovery node "
                f"{repair_node.node_id} belongs to plane {stage_kind.plane.value}, "
                f"not {graph.plane.value}"
            )
        if stage_kind.recovery_role is not RecoveryRole.LOCAL_REPAIR:
            raise CompilerValidationError(
                f"graph {graph.loop_id} runtime failure recovery node "
                f"{repair_node.node_id} must declare recovery_role=local_repair"
            )
        runtime_stage = stage_kind.runtime_stage
        if runtime_stage is None:
            raise CompilerValidationError(
                f"graph {graph.loop_id} runtime failure recovery node "
                f"{repair_node.node_id} uses stage kind {repair_node.stage_kind_id} "
                "without runtime_stage"
            )
        if stage_plane(runtime_stage) is not graph.plane:
            raise CompilerValidationError(
                f"graph {graph.loop_id} runtime failure recovery node "
                f"{repair_node.node_id} maps to runtime stage {runtime_stage.value} "
                f"outside plane {graph.plane.value}"
            )


def validate_lane_conflict_coverage(
    *,
    scheduler_policy: WorkflowPlaneSchedulerPolicyDefinition,
    mode: ModeDefinition,
) -> None:
    if mode.concurrency_policy is None:
        return

    lanes_by_plane: dict[Plane, tuple[str, ...]] = {}
    for lane in scheduler_policy.lanes:
        lanes_by_plane.setdefault(lane.plane, ())
        lanes_by_plane[lane.plane] = (*lanes_by_plane[lane.plane], lane.lane_id)

    covered_pairs = {
        pair
        for policy in scheduler_policy.lane_conflict_policies
        for pair in policy.lane_pairs
    }
    for plane_pair in mode.concurrency_policy.may_run_concurrently:
        if len(plane_pair) != 2:
            raise CompilerValidationError("may_run_concurrently entries must name exactly two planes")
        first_plane, second_plane = plane_pair
        for first_lane_id in lanes_by_plane.get(first_plane, ()):
            for second_lane_id in lanes_by_plane.get(second_plane, ()):
                lane_pair = tuple(sorted((first_lane_id, second_lane_id)))
                if lane_pair not in covered_pairs:
                    raise CompilerValidationError(
                        "lane conflict policy missing for concurrent lane pair "
                        f"{lane_pair[0]} + {lane_pair[1]}"
                    )


def _queue_policies_by_plane(
    workflow_primitives: WorkflowPrimitiveBundle,
) -> dict[Plane, PlaneQueueClaimPolicyDefinition]:
    policies_by_plane: dict[Plane, PlaneQueueClaimPolicyDefinition] = {}
    for policy in workflow_primitives.queue_claim_policies:
        if policy.plane in policies_by_plane:
            raise CompilerValidationError(f"Duplicate queue claim policy for plane: {policy.plane.value}")
        policies_by_plane[policy.plane] = policy
    return policies_by_plane


def _graph_nodes_by_id(
    graphs: Iterable[FrozenGraphPlanePlan],
) -> dict[str, MaterializedGraphNodePlan]:
    nodes_by_id: dict[str, MaterializedGraphNodePlan] = {}
    for graph in graphs:
        for node in graph.nodes:
            nodes_by_id.setdefault(node.node_id, node)
    return nodes_by_id


def _validate_document_adapters(
    families_by_id: dict[str, WorkItemFamilyDefinition],
    adapters_by_id: dict[str, WorkItemDocumentAdapterDefinition],
) -> None:
    for family in families_by_id.values():
        adapter = adapters_by_id.get(family.document_adapter_id)
        if adapter is None:
            raise CompilerValidationError(
                f"work item family {family.family_id} references unknown document adapter "
                f"{family.document_adapter_id}"
            )
        adapter_family_ids = getattr(adapter, "family_ids")
        if family.family_id not in adapter_family_ids:
            raise CompilerValidationError(
                f"document adapter {family.document_adapter_id} does not declare family "
                f"{family.family_id}"
            )

    for adapter in adapters_by_id.values():
        for family_id in getattr(adapter, "family_ids"):
            if family_id not in families_by_id:
                raise CompilerValidationError(
                    f"document adapter {getattr(adapter, 'adapter_id')} references unknown "
                    f"work item family {family_id}"
                )


def _validate_artifact_contracts(
    *,
    artifact_contracts_by_id: dict[str, ArtifactContractDefinition],
    families_by_id: dict[str, WorkItemFamilyDefinition],
    document_adapters_by_id: dict[str, WorkItemDocumentAdapterDefinition],
    runtime_effect_handlers_by_id: dict[str, RuntimeEffectHandlerDefinition],
    runtime_effect_operations_by_id: dict[str, RuntimeEffectOperationDefinition],
    runtime_effect_runners_by_id: dict[str, RuntimeEffectOperationRunnerDefinition],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
) -> None:
    known_adapter_ids = set(document_adapters_by_id) | set(_BUILT_IN_ARTIFACT_ADAPTER_IDS)
    for contract in artifact_contracts_by_id.values():
        if contract.destination_family_id is not None and contract.destination_family_id not in families_by_id:
            raise CompilerValidationError(
                f"artifact contract {contract.artifact_id} references unknown destination "
                f"family {contract.destination_family_id}"
            )
        for adapter in contract.filename_adapters:
            if adapter.parser_id not in known_adapter_ids:
                raise CompilerValidationError(
                    f"artifact contract {contract.artifact_id} filename {adapter.filename} "
                    f"references unknown parser {adapter.parser_id}"
                )
            _validate_artifact_adapter_semantics(
                contract=contract,
                filename_adapter=adapter,
                adapter_id=adapter.parser_id,
                adapter_role="parser",
                document_adapters_by_id=document_adapters_by_id,
            )
            if adapter.renderer_id is not None and adapter.renderer_id not in known_adapter_ids:
                raise CompilerValidationError(
                    f"artifact contract {contract.artifact_id} filename {adapter.filename} "
                    f"references unknown renderer {adapter.renderer_id}"
                )
            if adapter.renderer_id is not None:
                _validate_artifact_adapter_semantics(
                    contract=contract,
                    filename_adapter=adapter,
                    adapter_id=adapter.renderer_id,
                    adapter_role="renderer",
                    document_adapters_by_id=document_adapters_by_id,
                )
        for stage_kind_id in contract.producer_stage_kind_ids:
            stage_kind = stage_kinds.get(stage_kind_id)
            if stage_kind is None:
                raise CompilerValidationError(
                    f"artifact contract {contract.artifact_id} references unknown producer "
                    f"stage kind {stage_kind_id}"
                )
            if contract.artifact_id not in stage_kind.declared_output_artifacts:
                raise CompilerValidationError(
                    f"artifact contract {contract.artifact_id} declares producer stage kind "
                    f"{stage_kind_id}, but that stage kind does not output {contract.artifact_id}"
                )
        for handler_id in contract.consumer_handler_ids:
            handler = runtime_effect_handlers_by_id.get(handler_id)
            if handler is None:
                raise CompilerValidationError(
                    f"artifact contract {contract.artifact_id} references unknown consumer "
                    f"handler {handler_id}"
                )
            consumed = set(getattr(handler, "required_artifacts")) | set(
                getattr(handler, "optional_artifacts")
            )
            if contract.artifact_id not in consumed:
                raise CompilerValidationError(
                    f"artifact contract {contract.artifact_id} declares consumer handler "
                    f"{handler_id}, but that handler does not consume {contract.artifact_id}"
                )
            operation_ids = _operation_ids_for_legacy_handler(
                handler_id,
                runtime_effect_operations_by_id=runtime_effect_operations_by_id,
                runtime_effect_runners_by_id=runtime_effect_runners_by_id,
            )
            if len(operation_ids) != 1:
                if len(operation_ids) > 1:
                    raise CompilerValidationError(
                        f"artifact contract {contract.artifact_id} legacy consumer handler "
                        f"{handler_id} maps to multiple runtime effect operations or runners"
                    )
                raise CompilerValidationError(
                    f"artifact contract {contract.artifact_id} legacy consumer handler "
                    f"{handler_id} does not map to exactly one runtime effect operation"
                )
            operation_id = next(iter(operation_ids))
            if contract.consumer_operation_ids and operation_id not in contract.consumer_operation_ids:
                raise CompilerValidationError(
                    f"artifact contract {contract.artifact_id} handler {handler_id} maps to "
                    f"operation {operation_id}, but consumer_operation_ids does not list it"
                )
            _validate_artifact_consumer_operation(
                contract,
                operation_id,
                runtime_effect_operations_by_id=runtime_effect_operations_by_id,
            )
        for operation_id in contract.consumer_operation_ids:
            _validate_artifact_consumer_operation(
                contract,
                operation_id,
                runtime_effect_operations_by_id=runtime_effect_operations_by_id,
            )


def _validate_artifact_consumer_operation(
    contract: ArtifactContractDefinition,
    operation_id: str,
    *,
    runtime_effect_operations_by_id: dict[str, RuntimeEffectOperationDefinition],
) -> None:
    operation = runtime_effect_operations_by_id.get(operation_id)
    if operation is None:
        raise CompilerValidationError(
            f"artifact contract {contract.artifact_id} references unknown consumer "
            f"operation {operation_id}"
        )
    declared_artifacts = set(operation.required_artifacts) | set(operation.produced_artifacts)
    if contract.artifact_id not in declared_artifacts:
        raise CompilerValidationError(
            f"artifact contract {contract.artifact_id} declares consumer operation "
            f"{operation_id}, but that operation does not consume {contract.artifact_id}"
        )


def _operation_ids_for_legacy_handler(
    handler_id: str,
    *,
    runtime_effect_operations_by_id: dict[str, RuntimeEffectOperationDefinition],
    runtime_effect_runners_by_id: dict[str, RuntimeEffectOperationRunnerDefinition],
) -> set[str]:
    del runtime_effect_operations_by_id
    operation_ids: set[str] = set()
    for runner in runtime_effect_runners_by_id.values():
        runner_operation_id = runner.operation_id_for_legacy_handler(handler_id)
        if runner_operation_id is not None:
            operation_ids.add(runner_operation_id)
    return operation_ids


def _validate_artifact_adapter_semantics(
    *,
    contract: ArtifactContractDefinition,
    filename_adapter: ArtifactFilenameAdapterDefinition,
    adapter_id: str,
    adapter_role: str,
    document_adapters_by_id: dict[str, WorkItemDocumentAdapterDefinition],
) -> None:
    built_in_format = _BUILT_IN_ARTIFACT_ADAPTER_FORMATS.get(adapter_id)
    if built_in_format is not None:
        if filename_adapter.format is not built_in_format:
            raise CompilerValidationError(
                f"artifact contract {contract.artifact_id} filename "
                f"{filename_adapter.filename} declares format {filename_adapter.format.value} "
                f"but {adapter_role} {adapter_id} handles {built_in_format.value}"
            )
        return

    document_adapter = document_adapters_by_id.get(adapter_id)
    if document_adapter is None:
        return

    if adapter_role == "parser" and not getattr(document_adapter, "can_parse"):
        raise CompilerValidationError(
            f"artifact contract {contract.artifact_id} filename "
            f"{filename_adapter.filename} uses parser {adapter_id} without parse capability"
        )
    if adapter_role == "renderer" and not getattr(document_adapter, "can_render"):
        raise CompilerValidationError(
            f"artifact contract {contract.artifact_id} filename "
            f"{filename_adapter.filename} uses renderer {adapter_id} without render capability"
        )

    extension = PurePosixPath(filename_adapter.filename).suffix
    supported_extensions = getattr(document_adapter, "supported_file_extensions")
    if extension and extension not in supported_extensions:
        raise CompilerValidationError(
            f"artifact contract {contract.artifact_id} filename "
            f"{filename_adapter.filename} uses {adapter_role} {adapter_id}, "
            f"but its extension {extension} is not supported"
        )


def _validate_stage_artifact_references(
    *,
    artifact_contracts_by_id: dict[str, ArtifactContractDefinition],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
) -> None:
    for stage_kind in stage_kinds.values():
        for artifact_id in stage_kind.allowed_input_artifacts:
            if artifact_id not in artifact_contracts_by_id:
                raise CompilerValidationError(
                    f"stage kind {stage_kind.stage_kind_id} allows unknown input "
                    f"artifact {artifact_id}"
                )
        for artifact_id in stage_kind.declared_output_artifacts:
            if artifact_id not in artifact_contracts_by_id:
                raise CompilerValidationError(
                    f"stage kind {stage_kind.stage_kind_id} declares unknown output "
                    f"artifact {artifact_id}"
                )
            contract = artifact_contracts_by_id[artifact_id]
            if (
                contract.producer_stage_kind_ids
                and stage_kind.stage_kind_id not in contract.producer_stage_kind_ids
            ):
                raise CompilerValidationError(
                    f"stage kind {stage_kind.stage_kind_id} declares output artifact "
                    f"{artifact_id}, but artifact contract {artifact_id} does not list "
                    "that stage kind as a producer"
                )


def _validate_graph_terminal_artifact_references(
    *,
    artifact_contracts_by_id: dict[str, ArtifactContractDefinition],
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
) -> None:
    for graph in graphs_by_plane.values():
        for terminal_state in graph.terminal_states:
            for artifact_id in terminal_state.emits_artifacts:
                if artifact_id not in artifact_contracts_by_id:
                    raise CompilerValidationError(
                        f"graph {graph.loop_id} terminal {terminal_state.terminal_state_id} "
                        f"emits unknown artifact {artifact_id}"
                    )


def _validate_request_context_profiles(
    *,
    artifact_contracts_by_id: dict[str, ArtifactContractDefinition],
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    request_context_profiles_by_id: dict[str, RequestContextProfileDefinition],
    request_context_providers_by_id: dict[str, RequestContextProviderDefinition],
    request_context_render_plans_by_id: dict[str, RequestContextRenderPlan],
    workflow_primitives: WorkflowPrimitiveBundle,
) -> None:
    for profile in workflow_primitives.request_context_profiles:
        provider = request_context_providers_by_id.get(profile.provider_id)
        if provider is None:
            raise CompilerValidationError(
                f"request context profile {profile.profile_id} references unknown "
                f"provider {profile.provider_id}"
            )
        if profile.request_kind not in provider.supported_request_kinds:
            supported = ", ".join(provider.supported_request_kinds)
            raise CompilerValidationError(
                f"request context profile {profile.profile_id} request kind "
                f"{profile.request_kind} is not supported by provider {provider.provider_id}; "
                f"supported request kinds: {supported}"
            )
        render_plan = request_context_render_plans_by_id.get(profile.primary_render_plan_id)
        if render_plan is None:
            raise CompilerValidationError(
                f"request context profile {profile.profile_id} references unknown "
                f"render plan {profile.primary_render_plan_id}"
            )
        missing_capabilities = sorted(
            set(render_plan.required_provider_capabilities) - set(provider.capabilities)
        )
        if missing_capabilities:
            raise CompilerValidationError(
                f"request context profile {profile.profile_id} render plan "
                f"{render_plan.render_plan_id} requires provider capabilities not declared "
                f"by {provider.provider_id}: {', '.join(missing_capabilities)}"
            )
        missing_required_providers = sorted(
            set(profile.required_providers) - set(provider.capabilities)
        )
        if missing_required_providers:
            raise CompilerValidationError(
                f"request context profile {profile.profile_id} requires provider "
                f"capabilities not declared by {provider.provider_id}: "
                f"{', '.join(missing_required_providers)}"
            )
        for artifact_id, filename in profile.output_path_preferences.items():
            if artifact_id not in artifact_contracts_by_id:
                raise CompilerValidationError(
                    f"request context profile {profile.profile_id} references unknown "
                    f"output artifact {artifact_id}"
                )
            contract = artifact_contracts_by_id[artifact_id]
            if filename not in contract.all_filenames:
                allowed = ", ".join(contract.all_filenames)
                raise CompilerValidationError(
                    f"request context profile {profile.profile_id} maps artifact "
                    f"{artifact_id} to filename {filename}; artifact contract mismatch: "
                    f"artifact contract {artifact_id} allows filenames {allowed}"
                )
    for graph in graphs_by_plane.values():
        for node in graph.nodes:
            profile_id = node.request_context_profile_id
            if profile_id is None:
                raise CompilerValidationError(
                    f"graph node {node.node_id} has no request context profile"
                )
            node_profile = request_context_profiles_by_id.get(profile_id)
            if node_profile is None:
                raise CompilerValidationError(
                    f"graph node {node.node_id} references unknown request context "
                    f"profile {profile_id}"
                )
            provider = request_context_providers_by_id[node_profile.provider_id]
            if graph.plane not in provider.supported_planes:
                raise CompilerValidationError(
                    f"request context profile {node_profile.profile_id} provider "
                    f"{provider.provider_id} does not support plane {graph.plane.value}"
                )
            render_plan_id = node.context_render_plan_id
            if render_plan_id is None:
                raise CompilerValidationError(
                    f"graph node {node.node_id} has no context render plan"
                )
            if render_plan_id not in request_context_render_plans_by_id:
                raise CompilerValidationError(
                    f"graph node {node.node_id} references unknown context render "
                    f"plan {render_plan_id}"
                )
            if (
                render_plan_id != node_profile.primary_render_plan_id
                and not node_profile.allow_render_plan_override
            ):
                raise CompilerValidationError(
                    f"graph node {node.node_id} overrides request context render plan "
                    f"{node_profile.primary_render_plan_id} with {render_plan_id}, but "
                    f"profile {node_profile.profile_id} does not allow render plan overrides"
                )
            render_plan = request_context_render_plans_by_id[render_plan_id]
            missing_capabilities = sorted(
                set(render_plan.required_provider_capabilities) - set(provider.capabilities)
            )
            if missing_capabilities:
                raise CompilerValidationError(
                    f"graph node {node.node_id} context render plan {render_plan_id} "
                    f"requires provider capabilities not declared by {provider.provider_id}: "
                    f"{', '.join(missing_capabilities)}"
                )


def _validate_queue_claim_policies(
    *,
    mode: ModeDefinition,
    families_by_id: dict[str, WorkItemFamilyDefinition],
    queue_policies_by_plane: dict[Plane, PlaneQueueClaimPolicyDefinition],
) -> None:
    for plane in mode.loop_ids_by_plane:
        if plane not in queue_policies_by_plane:
            raise CompilerValidationError(f"mode {mode.mode_id} has no queue claim policy for plane {plane.value}")

    for policy in queue_policies_by_plane.values():
        for family_id in getattr(policy, "family_order"):
            family = families_by_id.get(family_id)
            if family is None:
                raise CompilerValidationError(
                    f"queue claim policy {getattr(policy, 'policy_id')} references unknown "
                    f"work item family {family_id}"
                )
            if family.plane is not getattr(policy, "plane"):
                raise CompilerValidationError(
                    f"queue claim policy {getattr(policy, 'policy_id')} includes family "
                    f"{family_id} from plane {family.plane.value}"
                )


def _validate_queue_lifecycle_adapters(
    *,
    families_by_id: dict[str, WorkItemFamilyDefinition],
    queue_policies_by_plane: dict[Plane, PlaneQueueClaimPolicyDefinition],
) -> None:
    for policy in queue_policies_by_plane.values():
        for family_id in policy.family_order:
            family = families_by_id[family_id]
            adapter_id = resolve_queue_lifecycle_adapter_id(family)
            if adapter_id is None:
                raise CompilerValidationError(
                    f"queue claim family {family.family_id} in policy {policy.policy_id} "
                    "is missing queue lifecycle adapter id"
                )
            adapter = queue_adapter_for_id(adapter_id)
            if adapter is None:
                raise CompilerValidationError(
                    f"queue claim family {family.family_id} references unknown queue "
                    f"lifecycle adapter {adapter_id}"
                )
            if adapter.family_id != family.family_id:
                raise CompilerValidationError(
                    f"queue claim family {family.family_id} references queue lifecycle "
                    f"adapter {adapter_id} bound to family {adapter.family_id}"
                )


def _validate_graph_entries_are_claimable(
    *,
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    families_by_id: dict[str, WorkItemFamilyDefinition],
    queue_policies_by_plane: dict[Plane, PlaneQueueClaimPolicyDefinition],
) -> None:
    families_by_plane_entry = {
        (family.plane, family.entry_key): family
        for family in families_by_id.values()
    }
    for graph in graphs_by_plane.values():
        policy = queue_policies_by_plane.get(graph.plane)
        if policy is None:
            continue
        claimable_families = set(getattr(policy, "family_order"))
        for entry in graph.entry_nodes:
            entry_key = graph_loop_entry_key_value(entry.entry_key)
            if entry_key == GraphLoopEntryKey.CLOSURE_TARGET.value:
                continue
            family = families_by_plane_entry.get((graph.plane, entry_key))
            if family is None:
                continue
            if family.family_id not in claimable_families:
                raise CompilerValidationError(
                    f"graph {graph.loop_id} entry {entry_key} uses family "
                    f"{family.family_id} missing from queue claim policy "
                    f"{getattr(policy, 'policy_id')}"
                )
def _validate_entry_coverage(
    *,
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    families_by_id: dict[str, WorkItemFamilyDefinition],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
) -> None:
    families_by_plane_entry = {
        (family.plane, family.entry_key): family
        for family in families_by_id.values()
    }

    for graph in graphs_by_plane.values():
        nodes_by_id = {node.node_id: node for node in graph.nodes}
        for entry in graph.entry_nodes:
            entry_key = graph_loop_entry_key_value(entry.entry_key)
            if entry_key == GraphLoopEntryKey.CLOSURE_TARGET.value:
                continue
            family = families_by_plane_entry.get((graph.plane, entry_key))
            if family is None:
                raise CompilerValidationError(
                    f"graph {graph.loop_id} entry {entry_key} has no matching "
                    f"work item family for plane {graph.plane.value}"
                )
            node = nodes_by_id[entry.node_id]
            stage_kind = stage_kinds[node.stage_kind_id]
            if not _stage_kind_can_start_family(stage_kind, family):
                raise CompilerValidationError(
                    f"entry {entry_key} routes to stage kind {stage_kind.stage_kind_id}, "
                    f"which cannot start family {family.family_id}"
                )


def _stage_kind_can_start_family(
    stage_kind: RegisteredStageKindDefinition,
    family: WorkItemFamilyDefinition,
) -> bool:
    return family.family_id in stage_kind.allowed_work_item_families


def _validate_terminal_actions(
    *,
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    terminal_actions_by_id: dict[str, TerminalActionDefinition],
    lifecycle_plans_by_id: dict[str, LifecycleMutationPlanDefinition],
    runtime_effect_rules_by_id: dict[str, RuntimeEffectRuleDefinition],
) -> None:
    terminal_classes = {
        getattr(action, "terminal_class")
        for action in terminal_actions_by_id.values()
    }
    for graph in graphs_by_plane.values():
        for state in graph.terminal_states:
            terminal_class = state.terminal_class.value
            if terminal_class not in terminal_classes:
                raise CompilerValidationError(
                    f"terminal state {state.terminal_state_id} uses terminal class "
                    f"{terminal_class} without a terminal action"
                )

    for action in terminal_actions_by_id.values():
        plan_id = getattr(action, "lifecycle_mutation_plan_id")
        if plan_id is not None and plan_id not in lifecycle_plans_by_id:
            raise CompilerValidationError(
                f"terminal action {getattr(action, 'terminal_action_id')} references unknown "
                f"lifecycle mutation plan {plan_id}"
            )
        for rule_id in getattr(action, "effect_rule_ids"):
            if rule_id not in runtime_effect_rules_by_id:
                raise CompilerValidationError(
                    f"terminal action {getattr(action, 'terminal_action_id')} references "
                    f"unknown runtime effect rule {rule_id}"
                )


def _validate_lifecycle_plans(
    *,
    families_by_id: dict[str, WorkItemFamilyDefinition],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
    lifecycle_plan_ids: dict[str, LifecycleMutationPlanDefinition],
) -> None:
    for plan in lifecycle_plan_ids.values():
        source_family_id = getattr(plan, "source_family_id")
        if source_family_id not in families_by_id:
            raise CompilerValidationError(
                f"lifecycle mutation plan {getattr(plan, 'plan_id')} references unknown "
                f"source family {source_family_id}"
            )
        source_node_id = getattr(plan, "source_node_id")
        if source_node_id != "any" and source_node_id not in stage_kinds:
            raise CompilerValidationError(
                f"lifecycle mutation plan {getattr(plan, 'plan_id')} references unknown "
                f"source node {source_node_id}"
            )


def _validate_runtime_effect_handlers(
    *,
    artifact_contracts_by_id: dict[str, ArtifactContractDefinition],
    families_by_id: dict[str, WorkItemFamilyDefinition],
    runtime_effect_handlers_by_id: dict[str, RuntimeEffectHandlerDefinition],
) -> None:
    for handler in runtime_effect_handlers_by_id.values():
        handler_id = getattr(handler, "handler_id")
        for family_id in getattr(handler, "allowed_source_families"):
            if family_id not in families_by_id:
                raise CompilerValidationError(
                    f"runtime effect handler {handler_id} references "
                    f"unknown source family {family_id}"
                )
        for destination in getattr(handler, "destination_kinds"):
            if destination != "custom" and destination not in families_by_id:
                raise CompilerValidationError(
                    f"runtime effect handler {handler_id} references "
                    f"unknown destination family {destination}"
                )
        for artifact_id in (*getattr(handler, "required_artifacts"), *getattr(handler, "optional_artifacts")):
            contract = artifact_contracts_by_id.get(artifact_id)
            if contract is None:
                raise CompilerValidationError(
                    f"runtime effect handler {handler_id} requires unknown artifact {artifact_id}"
                )
            if contract.consumer_handler_ids and handler_id not in contract.consumer_handler_ids:
                raise CompilerValidationError(
                    f"runtime effect handler {handler_id} consumes artifact {artifact_id}, "
                    f"but artifact contract {artifact_id} does not list that handler"
                )


def _validate_runtime_effect_rules(
    *,
    artifact_contracts_by_id: dict[str, ArtifactContractDefinition],
    families_by_id: dict[str, WorkItemFamilyDefinition],
    lifecycle_plans_by_id: dict[str, LifecycleMutationPlanDefinition],
    runtime_effect_handlers_by_id: dict[str, RuntimeEffectHandlerDefinition],
    runtime_effect_operations_by_id: dict[str, RuntimeEffectOperationDefinition],
    runtime_effect_runners_by_id: dict[str, RuntimeEffectOperationRunnerDefinition],
    runtime_effect_rules_by_id: dict[str, RuntimeEffectRuleDefinition],
    stage_kinds_by_node_id: dict[str, RegisteredStageKindDefinition],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
) -> None:
    seen_bindings: dict[tuple[str, str], str] = {}
    for rule in runtime_effect_rules_by_id.values():
        rule_id = getattr(rule, "rule_id")
        handler_id = getattr(rule, "handler_id")
        handler_required_artifacts: set[str] = set()
        handler_declared_artifacts: set[str] = set()
        if handler_id is not None:
            if handler_id not in runtime_effect_handlers_by_id:
                raise CompilerValidationError(
                    f"runtime effect rule {rule_id} references unknown handler "
                    f"{handler_id}"
                )
            operation_ids = _operation_ids_for_legacy_handler(
                handler_id,
                runtime_effect_operations_by_id=runtime_effect_operations_by_id,
                runtime_effect_runners_by_id=runtime_effect_runners_by_id,
            )
            if rule.effect_operation_id not in runtime_effect_operations_by_id:
                raise CompilerValidationError(
                    f"runtime effect rule {rule_id} references unknown operation "
                    f"{rule.effect_operation_id}"
                )
            if rule.effect_operation_id not in operation_ids:
                raise CompilerValidationError(
                    f"runtime effect rule {rule_id} handler {handler_id} is not a legacy alias "
                    f"for operation {rule.effect_operation_id}"
                )
            handler = runtime_effect_handlers_by_id[handler_id]
            handler_required_artifacts = set(getattr(handler, "required_artifacts"))
            handler_declared_artifacts = handler_required_artifacts | set(
                getattr(handler, "optional_artifacts")
            )
        rule_required_artifacts = set(getattr(rule, "required_run_artifacts"))
        for artifact_id in getattr(rule, "required_run_artifacts"):
            if artifact_id not in artifact_contracts_by_id:
                raise CompilerValidationError(
                    f"runtime effect rule {rule_id} requires unknown artifact {artifact_id}"
                )
            if handler_id is not None and artifact_id not in handler_declared_artifacts:
                raise CompilerValidationError(
                    f"runtime effect rule {rule_id} requires artifact {artifact_id} "
                    f"not declared by handler {handler_id}"
                )
        if handler_id is not None:
            for artifact_id in sorted(handler_required_artifacts - rule_required_artifacts):
                raise CompilerValidationError(
                    f"runtime effect handler {handler_id} requires artifact {artifact_id} "
                    f"missing from runtime effect rule {rule_id}"
                )
        destination_family_id = getattr(rule, "destination_family_id")
        if destination_family_id is not None and destination_family_id not in families_by_id:
            raise CompilerValidationError(
                f"runtime effect rule {rule_id} references unknown destination "
                f"family {destination_family_id}"
            )
        lifecycle_plan_id = getattr(rule, "lifecycle_mutation_plan_id")
        if lifecycle_plan_id is not None and lifecycle_plan_id not in lifecycle_plans_by_id:
            raise CompilerValidationError(
                f"runtime effect rule {rule_id} references unknown lifecycle "
                f"mutation plan {lifecycle_plan_id}"
            )
        source_node_id = getattr(rule, "source_node_id")
        stage_kind = _stage_kind_for_node_or_kind(
            source_node_id,
            stage_kinds_by_node_id=stage_kinds_by_node_id,
            stage_kinds=stage_kinds,
            source_label=f"runtime effect rule {rule_id}",
        )
        for outcome in getattr(rule, "on_outcomes"):
            if outcome not in stage_kind.legal_outcomes:
                raise CompilerValidationError(
                    f"runtime effect rule {rule_id} references illegal outcome "
                    f"{outcome} for source node {source_node_id}"
                )
            binding_key = (source_node_id, outcome)
            previous_rule_id = seen_bindings.get(binding_key)
            if previous_rule_id is not None:
                raise CompilerValidationError(
                    f"runtime effect rules {previous_rule_id} and {rule_id} both bind "
                    f"{source_node_id} outcome {outcome}"
                )
            seen_bindings[binding_key] = rule_id


def _validate_recovery_policies(
    *,
    workflow_primitives: WorkflowPrimitiveBundle,
    stage_kinds_by_node_id: dict[str, RegisteredStageKindDefinition],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
    terminal_state_ids: set[str],
) -> None:
    for policy in workflow_primitives.recovery_policies:
        for source_node_id in policy.source_node_ids:
            stage_kind = _stage_kind_for_node_or_kind(
                source_node_id,
                stage_kinds_by_node_id=stage_kinds_by_node_id,
                stage_kinds=stage_kinds,
                source_label=f"workflow recovery policy {policy.policy_id}",
            )
            for outcome in policy.on_outcomes:
                if outcome not in stage_kind.legal_outcomes:
                    raise CompilerValidationError(
                        f"workflow recovery policy {policy.policy_id} references illegal outcome "
                        f"{outcome} for source node {source_node_id}"
                    )
        for target_node_id in (policy.retry_target_node_id, policy.exhausted_target_node_id):
            if target_node_id is not None:
                _stage_kind_for_node_or_kind(
                    target_node_id,
                    stage_kinds_by_node_id=stage_kinds_by_node_id,
                    stage_kinds=stage_kinds,
                    source_label=f"workflow recovery policy {policy.policy_id}",
                )
        if (
            policy.exhausted_terminal_state_id is not None
            and policy.exhausted_terminal_state_id not in terminal_state_ids
        ):
            raise CompilerValidationError(
                f"workflow recovery policy {policy.policy_id} references unknown terminal "
                f"state {policy.exhausted_terminal_state_id}"
            )


def _validate_runtime_failure_policies(
    *,
    workflow_primitives: WorkflowPrimitiveBundle,
    families_by_id: dict[str, WorkItemFamilyDefinition],
    runtime_effect_handlers_by_id: dict[str, RuntimeEffectHandlerDefinition],
    runtime_effect_operations_by_id: dict[str, RuntimeEffectOperationDefinition],
    runtime_effect_runners_by_id: dict[str, RuntimeEffectOperationRunnerDefinition],
    runtime_effect_rules_by_id: dict[str, RuntimeEffectRuleDefinition],
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    stage_kinds_by_node_id: dict[str, RegisteredStageKindDefinition],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
    terminal_state_ids: set[str],
) -> None:
    graph_node_ids_by_plane = {
        plane: {node.node_id for node in graph.nodes}
        for plane, graph in graphs_by_plane.items()
    }
    graph_stage_kind_ids_by_plane = {
        plane: {node.stage_kind_id for node in graph.nodes}
        for plane, graph in graphs_by_plane.items()
    }
    terminal_state_ids_by_plane = {
        plane: {state.terminal_state_id for state in graph.terminal_states}
        for plane, graph in graphs_by_plane.items()
    }
    selected_node_ids = {
        node_id
        for node_ids in graph_node_ids_by_plane.values()
        for node_id in node_ids
    }
    runtime_effect_failure_classes = {
        failure_class
        for handler in runtime_effect_handlers_by_id.values()
        for failure_class in getattr(handler, "failure_classes")
    } | {
        mapping.failure_class
        for operation in runtime_effect_operations_by_id.values()
        for mapping in operation.failure_mappings
    }
    for policy in workflow_primitives.runtime_failure_policies:
        active_planes = _runtime_failure_policy_active_planes(
            policy,
            graph_node_ids_by_plane=graph_node_ids_by_plane,
            graph_stage_kind_ids_by_plane=graph_stage_kind_ids_by_plane,
        )
        for family_id in policy.applies_to_families:
            if family_id not in families_by_id:
                raise CompilerValidationError(
                    f"runtime failure policy {policy.policy_id} references unknown family "
                    f"{family_id}"
                )
        if "runtime_effect" in policy.applies_to_origins:
            for handler_id in policy.applies_to_handler_ids:
                if handler_id not in runtime_effect_handlers_by_id:
                    raise CompilerValidationError(
                        f"runtime failure policy {policy.policy_id} references unknown "
                        f"runtime effect handler {handler_id}"
                    )
            for operation_id in policy.applies_to_operation_ids:
                if operation_id not in runtime_effect_operations_by_id:
                    raise CompilerValidationError(
                        f"runtime failure policy {policy.policy_id} references unknown "
                        f"runtime effect operation {operation_id}"
                    )
            _validate_runtime_failure_policy_effect_identity_scope(
                policy,
                runtime_effect_operations_by_id=runtime_effect_operations_by_id,
                runtime_effect_runners_by_id=runtime_effect_runners_by_id,
            )
            if (
                policy.action == "route_to_node"
                and (
                    not policy.applies_to_mutation_phases
                    or "partial_mutation" in policy.applies_to_mutation_phases
                )
            ):
                raise CompilerValidationError(
                    f"runtime failure policy {policy.policy_id} cannot route partial mutation "
                    f"runtime effect failures to node {policy.target_node_id}"
                )
            for failure_class in policy.applies_to_failure_classes:
                if failure_class not in runtime_effect_failure_classes:
                    raise CompilerValidationError(
                        f"runtime failure policy {policy.policy_id} references undeclared "
                        f"runtime effect failure class {failure_class}"
                    )
            for source_node_id in policy.applies_to_source_node_ids:
                _validate_runtime_failure_policy_node_reference(
                    source_node_id,
                    selected_node_ids=selected_node_ids,
                    stage_kinds=stage_kinds,
                    policy_id=policy.policy_id,
                    role="source",
                    )
            _validate_policy_nodes_in_declared_planes(
                policy_id=policy.policy_id,
                node_ids=policy.applies_to_source_node_ids,
                active_planes=active_planes,
                graph_node_ids_by_plane=graph_node_ids_by_plane,
                role="source",
            )
            _validate_policy_terminal_states_in_declared_planes(
                policy_id=policy.policy_id,
                terminal_state_ids=policy.applies_to_source_terminal_state_ids,
                active_planes=active_planes,
                all_terminal_state_ids=terminal_state_ids,
                terminal_state_ids_by_plane=terminal_state_ids_by_plane,
                role="source",
            )
            _validate_blueprint_recovery_route_closure(
                policy,
                active_planes=active_planes,
                runtime_effect_handlers_by_id=runtime_effect_handlers_by_id,
                runtime_effect_rules_by_id=runtime_effect_rules_by_id,
                graphs_by_plane=graphs_by_plane,
                graph_node_ids_by_plane=graph_node_ids_by_plane,
            )
        if policy.recovery_node_id is not None:
            _validate_runtime_failure_policy_node_reference(
                policy.recovery_node_id,
                selected_node_ids=selected_node_ids,
                stage_kinds=stage_kinds,
                policy_id=policy.policy_id,
                role="recovery",
            )
            _validate_policy_nodes_in_declared_planes(
                policy_id=policy.policy_id,
                node_ids=(policy.recovery_node_id,),
                active_planes=active_planes,
                graph_node_ids_by_plane=graph_node_ids_by_plane,
                role="recovery",
            )
        if policy.target_node_id is not None:
            _validate_runtime_failure_policy_node_reference(
                policy.target_node_id,
                selected_node_ids=selected_node_ids,
                stage_kinds=stage_kinds,
                policy_id=policy.policy_id,
                role="target",
            )
            _validate_policy_nodes_in_declared_planes(
                policy_id=policy.policy_id,
                node_ids=(policy.target_node_id,),
                active_planes=active_planes,
                graph_node_ids_by_plane=graph_node_ids_by_plane,
                role="target",
            )
            for plane in active_planes:
                if policy.target_node_id not in graph_node_ids_by_plane.get(plane, set()):
                    continue
                target_stage_kind = stage_kinds_by_node_id[policy.target_node_id]
                for family_id in policy.applies_to_families:
                    if family_id not in target_stage_kind.allowed_work_item_families:
                        raise CompilerValidationError(
                            f"runtime failure policy {policy.policy_id} target node "
                            f"{policy.target_node_id} cannot start family {family_id}"
                        )
        if policy.target_terminal_state_id is not None:
            _validate_policy_terminal_states_in_declared_planes(
                policy_id=policy.policy_id,
                terminal_state_ids=(policy.target_terminal_state_id,),
                active_planes=active_planes,
                all_terminal_state_ids=terminal_state_ids,
                terminal_state_ids_by_plane=terminal_state_ids_by_plane,
                role="target",
            )


def _validate_blueprint_recovery_route_closure(
    policy: RuntimeFailurePolicyDefinition,
    *,
    active_planes: tuple[Plane, ...],
    runtime_effect_handlers_by_id: dict[str, RuntimeEffectHandlerDefinition],
    runtime_effect_rules_by_id: dict[str, RuntimeEffectRuleDefinition],
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    graph_node_ids_by_plane: dict[Plane, set[str]],
) -> None:
    if getattr(policy, "action") != "route_to_node":
        return
    if getattr(policy, "target_node_id") != _MECHANIC_BLUEPRINT_NODE_ID:
        return
    if not any(
        _MECHANIC_BLUEPRINT_NODE_ID in graph_node_ids_by_plane.get(plane, set())
        for plane in active_planes
    ):
        return

    repair_rule = _mechanic_blueprint_repair_rule(runtime_effect_rules_by_id)
    if repair_rule is None:
        raise CompilerValidationError(
            f"runtime failure policy {getattr(policy, 'policy_id')} routes to "
            f"{_MECHANIC_BLUEPRINT_NODE_ID} but recovery node lacks closed repair effect "
            f"on {_MECHANIC_BLUEPRINT_REPAIR_OUTCOME}"
        )

    _validate_mechanic_blueprint_resume_guard(
        policy,
        active_planes=active_planes,
        graphs_by_plane=graphs_by_plane,
        graph_node_ids_by_plane=graph_node_ids_by_plane,
    )
    _validate_mechanic_blueprint_repair_rule_artifacts(policy, repair_rule)
    _validate_mechanic_blueprint_repair_capabilities(
        policy,
        repair_rule=repair_rule,
        runtime_effect_handlers_by_id=runtime_effect_handlers_by_id,
    )


def _mechanic_blueprint_repair_rule(
    runtime_effect_rules_by_id: dict[str, RuntimeEffectRuleDefinition],
) -> RuntimeEffectRuleDefinition | None:
    for rule in runtime_effect_rules_by_id.values():
        if getattr(rule, "source_node_id") != _MECHANIC_BLUEPRINT_NODE_ID:
            continue
        if getattr(rule, "handler_id") != _MECHANIC_BLUEPRINT_REPAIR_HANDLER_ID:
            continue
        if _MECHANIC_BLUEPRINT_REPAIR_OUTCOME not in getattr(rule, "on_outcomes"):
            continue
        return rule
    return None


def _validate_mechanic_blueprint_resume_guard(
    policy: RuntimeFailurePolicyDefinition,
    *,
    active_planes: tuple[Plane, ...],
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    graph_node_ids_by_plane: dict[Plane, set[str]],
) -> None:
    for plane in active_planes:
        if _MECHANIC_BLUEPRINT_NODE_ID not in graph_node_ids_by_plane.get(plane, set()):
            continue
        graph = graphs_by_plane[plane]
        if any(
            resume.source_node_id == _MECHANIC_BLUEPRINT_NODE_ID
            and resume.on_outcome == _MECHANIC_BLUEPRINT_REPAIR_OUTCOME
            and "resume_stage" in resume.metadata_stage_keys
            and _MECHANIC_BLUEPRINT_NODE_ID in resume.disallowed_target_node_ids
            for resume in graph.compiled_resume_policies
        ):
            return
    raise CompilerValidationError(
        f"runtime failure policy {getattr(policy, 'policy_id')} routes to "
        f"{_MECHANIC_BLUEPRINT_NODE_ID} but recovery node lacks resume guard for "
        f"{_MECHANIC_BLUEPRINT_REPAIR_OUTCOME}"
    )


def _validate_mechanic_blueprint_repair_rule_artifacts(
    policy: RuntimeFailurePolicyDefinition,
    repair_rule: RuntimeEffectRuleDefinition,
) -> None:
    required_artifacts = set(getattr(repair_rule, "required_run_artifacts"))
    missing = sorted(_MECHANIC_BLUEPRINT_REPAIR_ARTIFACTS - required_artifacts)
    if missing:
        missing_text = ", ".join(missing)
        raise CompilerValidationError(
            f"runtime failure policy {getattr(policy, 'policy_id')} routes to "
            f"{_MECHANIC_BLUEPRINT_NODE_ID} but repair effect "
            f"{getattr(repair_rule, 'rule_id')} is missing required artifact "
            f"{missing_text}"
        )


def _validate_mechanic_blueprint_repair_capabilities(
    policy: RuntimeFailurePolicyDefinition,
    *,
    repair_rule: RuntimeEffectRuleDefinition,
    runtime_effect_handlers_by_id: dict[str, RuntimeEffectHandlerDefinition],
) -> None:
    repair_handler = runtime_effect_handlers_by_id.get(_MECHANIC_BLUEPRINT_REPAIR_HANDLER_ID)
    if repair_handler is None:
        raise CompilerValidationError(
            f"runtime failure policy {getattr(policy, 'policy_id')} routes to "
            f"{_MECHANIC_BLUEPRINT_NODE_ID} but repair handler "
            f"{_MECHANIC_BLUEPRINT_REPAIR_HANDLER_ID} is not declared"
        )
    handler_capabilities = set(getattr(repair_handler, "declared_capabilities"))
    if _MECHANIC_BLUEPRINT_REPAIR_CAPABILITY not in handler_capabilities:
        raise CompilerValidationError(
            f"runtime failure policy {getattr(policy, 'policy_id')} routes to "
            f"{_MECHANIC_BLUEPRINT_NODE_ID} but repair effect "
            f"{getattr(repair_rule, 'rule_id')} lacks capability "
            f"{_MECHANIC_BLUEPRINT_REPAIR_CAPABILITY}"
        )
    failure_classes = tuple(getattr(policy, "applies_to_failure_classes"))
    if not failure_classes:
        raise CompilerValidationError(
            f"runtime failure policy {getattr(policy, 'policy_id')} routes to "
            f"{_MECHANIC_BLUEPRINT_NODE_ID} without declared runtime effect failure classes"
        )
    for failure_class in failure_classes:
        capability = f"repair.{failure_class}"
        if capability in handler_capabilities:
            continue
        raise CompilerValidationError(
            f"runtime failure policy {getattr(policy, 'policy_id')} routes "
            f"{failure_class} to {_MECHANIC_BLUEPRINT_NODE_ID} but repair effect "
            f"{getattr(repair_rule, 'rule_id')} lacks capability {capability}"
        )


def _runtime_failure_policy_active_planes(
    policy: RuntimeFailurePolicyDefinition,
    *,
    graph_node_ids_by_plane: dict[Plane, set[str]],
    graph_stage_kind_ids_by_plane: dict[Plane, set[str]],
) -> tuple[Plane, ...]:
    return tuple(
        plane
        for plane in getattr(policy, "applies_to_planes")
        if _runtime_failure_policy_is_active_for_plane(
            policy,
            graph_node_ids_by_plane.get(plane, set()),
            graph_stage_kind_ids_by_plane.get(plane, set()),
        )
    )


def _policy_activating_node_ids(policy: RuntimeFailurePolicyDefinition) -> tuple[str, ...]:
    return tuple(
        node_id
        for node_id in (
            *getattr(policy, "applies_to_source_node_ids", ()),
            getattr(policy, "target_node_id", None),
            getattr(policy, "recovery_node_id", None),
        )
        if node_id is not None
            )


def _validate_runtime_failure_policy_effect_identity_scope(
    policy: RuntimeFailurePolicyDefinition,
    *,
    runtime_effect_operations_by_id: dict[str, RuntimeEffectOperationDefinition],
    runtime_effect_runners_by_id: dict[str, RuntimeEffectOperationRunnerDefinition],
) -> None:
    if not policy.applies_to_handler_ids or not policy.applies_to_operation_ids:
        return
    selected_handler_ids = set(policy.applies_to_handler_ids)
    legacy_handlers_by_operation_id = {
        operation_id: _legacy_handler_ids_for_operation(
            operation_id,
            runtime_effect_operations_by_id=runtime_effect_operations_by_id,
            runtime_effect_runners_by_id=runtime_effect_runners_by_id,
        )
        for operation_id in policy.applies_to_operation_ids
    }
    compatible_handler_ids = set().union(*legacy_handlers_by_operation_id.values())
    for handler_id in sorted(selected_handler_ids - compatible_handler_ids):
        raise CompilerValidationError(
            f"runtime failure policy {policy.policy_id} handler {handler_id} is not a "
            f"legacy alias for operation ids {', '.join(policy.applies_to_operation_ids)}"
        )
    for operation_id, legacy_handler_ids in sorted(legacy_handlers_by_operation_id.items()):
        if not (selected_handler_ids & legacy_handler_ids):
            raise CompilerValidationError(
                f"runtime failure policy {policy.policy_id} operation {operation_id} has no "
                "selected legacy handler alias"
            )


def _legacy_handler_ids_for_operation(
    operation_id: str,
    *,
    runtime_effect_operations_by_id: dict[str, RuntimeEffectOperationDefinition],
    runtime_effect_runners_by_id: dict[str, RuntimeEffectOperationRunnerDefinition],
) -> set[str]:
    del runtime_effect_operations_by_id
    legacy_handler_ids: set[str] = set()
    for runner in runtime_effect_runners_by_id.values():
        for handler_id in runner.legacy_handler_ids:
            if runner.operation_id_for_legacy_handler(handler_id) == operation_id:
                legacy_handler_ids.add(handler_id)
    return legacy_handler_ids


def _validate_runtime_failure_policy_node_reference(
    node_id: str,
    *,
    selected_node_ids: set[str],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
    policy_id: str,
    role: str,
) -> None:
    if node_id in selected_node_ids or node_id in stage_kinds:
        return
    role_label = f"{role} " if role == "target" else ""
    raise CompilerValidationError(
        f"runtime failure policy {policy_id} references unknown {role_label}node {node_id}"
    )


def _validate_policy_nodes_in_declared_planes(
    *,
    policy_id: str,
    node_ids: Iterable[str],
    active_planes: Iterable[Plane],
    graph_node_ids_by_plane: dict[Plane, set[str]],
    role: str,
) -> None:
    for plane in active_planes:
        plane_node_ids = graph_node_ids_by_plane.get(plane)
        if plane_node_ids is None:
            continue
        for node_id in node_ids:
            if node_id not in plane_node_ids:
                raise CompilerValidationError(
                    f"runtime failure policy {policy_id} {role} node {node_id} "
                    f"is not in plane {plane.value}"
                )


def _validate_policy_terminal_states_in_declared_planes(
    *,
    policy_id: str,
    terminal_state_ids: Iterable[str],
    active_planes: Iterable[Plane],
    all_terminal_state_ids: set[str],
    terminal_state_ids_by_plane: dict[Plane, set[str]],
    role: str,
) -> None:
    for plane in active_planes:
        plane_terminal_state_ids = terminal_state_ids_by_plane.get(plane)
        if plane_terminal_state_ids is None:
            continue
        for terminal_state_id in terminal_state_ids:
            if terminal_state_id not in all_terminal_state_ids:
                raise CompilerValidationError(
                    f"runtime failure policy {policy_id} references unknown terminal "
                    f"state {terminal_state_id}"
                )
            if terminal_state_id not in plane_terminal_state_ids:
                raise CompilerValidationError(
                    f"runtime failure policy {policy_id} {role} terminal state "
                    f"{terminal_state_id} is not in plane {plane.value}"
                )


def _runtime_failure_policy_is_active_for_plane(
    policy: RuntimeFailurePolicyDefinition,
    plane_node_ids: set[str],
    plane_stage_kind_ids: set[str],
) -> bool:
    source_node_ids = tuple(getattr(policy, "applies_to_source_node_ids", ()))
    if not source_node_ids:
        return True
    return any(
        node_id in plane_node_ids or node_id in plane_stage_kind_ids
        for node_id in _policy_activating_node_ids(policy)
    )


def _validate_structural_graph_smoke(
    *,
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
    terminal_actions_by_id: dict[str, TerminalActionDefinition],
) -> None:
    terminal_classes = {
        getattr(action, "terminal_class")
        for action in terminal_actions_by_id.values()
    }
    for graph in graphs_by_plane.values():
        nodes_by_id = {node.node_id: node for node in graph.nodes}
        routed_outcomes: dict[str, set[str]] = {node.node_id: set() for node in graph.nodes}
        for transition in graph.compiled_transitions:
            if transition.outcome in routed_outcomes[transition.source_node_id]:
                raise CompilerValidationError(
                    f"graph {graph.loop_id} node {transition.source_node_id} has multiple "
                    f"routes for outcome {transition.outcome}"
                )
            routed_outcomes[transition.source_node_id].add(transition.outcome)
            if transition.terminal_state_id is not None:
                terminal_state = next(
                    state
                    for state in graph.terminal_states
                    if state.terminal_state_id == transition.terminal_state_id
                )
                if terminal_state.terminal_class.value not in terminal_classes:
                    raise CompilerValidationError(
                        f"terminal state {terminal_state.terminal_state_id} uses terminal "
                        f"class {terminal_state.terminal_class.value} without a terminal action"
                    )

        for node in graph.nodes:
            stage_kind = stage_kinds[node.stage_kind_id]
            for outcome in stage_kind.legal_outcomes:
                if outcome not in routed_outcomes[node.node_id]:
                    raise CompilerValidationError(
                        f"graph {graph.loop_id} node {node.node_id} has no route for "
                        f"legal outcome {outcome}"
                    )

        for entry in graph.entry_nodes:
            _walk_graph_from_entry(
                graph=graph,
                entry_node_id=entry.node_id,
                nodes_by_id=nodes_by_id,
            )


def _walk_graph_from_entry(
    *,
    graph: FrozenGraphPlanePlan,
    entry_node_id: str,
    nodes_by_id: dict[str, MaterializedGraphNodePlan],
) -> None:
    stack = [entry_node_id]
    seen: set[str] = set()
    while stack:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        if node_id not in nodes_by_id:
            raise CompilerValidationError(
                f"graph {graph.loop_id} entry walk reached unknown node {node_id}"
            )
        for transition in graph.compiled_transitions:
            if transition.source_node_id != node_id or transition.target_node_id is None:
                continue
            stack.append(transition.target_node_id)


def _stage_kind_for_node_or_kind(
    node_or_kind_id: str,
    *,
    stage_kinds_by_node_id: dict[str, RegisteredStageKindDefinition],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
    source_label: str,
) -> RegisteredStageKindDefinition:
    stage_kind = stage_kinds_by_node_id.get(node_or_kind_id) or stage_kinds.get(node_or_kind_id)
    if stage_kind is None:
        raise CompilerValidationError(f"{source_label} references unknown node {node_or_kind_id}")
    return stage_kind


__all__ = [
    "validate_lane_conflict_coverage",
    "validate_mode_stage_maps",
    "validate_workflow_primitives",
]


def _stage_key_value(stage: StageMapKey) -> str:
    return stage.value if hasattr(stage, "value") else str(stage)
