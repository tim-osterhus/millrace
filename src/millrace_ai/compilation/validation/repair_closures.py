"""Generic runtime-failure repair-closure validation helpers."""

from __future__ import annotations

from dataclasses import dataclass

from millrace_ai.architecture import (
    ArtifactContractDefinition,
    FrozenGraphPlanePlan,
    RecoveryRole,
    RegisteredStageKindDefinition,
    RuntimeEffectOperationDefinition,
    RuntimeEffectOperationRunnerDefinition,
    RuntimeEffectRepairClosureContractDefinition,
    RuntimeEffectRuleDefinition,
    RuntimeFailurePolicyDefinition,
    RuntimeFailurePolicyRepairClosureMappingDefinition,
    WorkItemFamilyDefinition,
)
from millrace_ai.contracts import Plane
from millrace_ai.workspace.family_adapters import (
    queue_adapter_for_id,
    resolve_queue_lifecycle_adapter_id,
)

from ..outcomes import CompilerValidationError
from .operation_runners import operation_ids_for_legacy_handler


@dataclass(frozen=True, slots=True)
class ResolvedRepairClosureBinding:
    source_operation_id: str
    failure_class: str
    repair_operation_id: str
    target_node_id: str
    target_terminal_outcome: str
    required_repair_evidence_artifact_ids: tuple[str, ...]
    affected_source_family_id: str
    source_lifecycle_behavior_on_repair_success: str
    source_lifecycle_behavior_on_repair_failure: str
    supports_partial_mutation: bool
    requires_resume_guard: bool


def validate_runtime_effect_repair_route_closure(
    policy: RuntimeFailurePolicyDefinition,
    *,
    active_planes: tuple[Plane, ...],
    artifact_contracts_by_id: dict[str, ArtifactContractDefinition],
    families_by_id: dict[str, WorkItemFamilyDefinition],
    runtime_effect_operations_by_id: dict[str, RuntimeEffectOperationDefinition],
    runtime_effect_runners_by_id: dict[str, RuntimeEffectOperationRunnerDefinition],
    runtime_effect_rules_by_id: dict[str, RuntimeEffectRuleDefinition],
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    graph_node_ids_by_plane: dict[Plane, set[str]],
    stage_kinds_by_node_id: dict[str, RegisteredStageKindDefinition],
) -> None:
    if not active_planes:
        return
    target_node_id = policy.target_node_id
    if target_node_id is None:
        return
    target_stage_kind = stage_kinds_by_node_id.get(target_node_id)
    if target_stage_kind is None:
        return
    if target_stage_kind.recovery_role is not RecoveryRole.LOCAL_REPAIR:
        raise CompilerValidationError(
            f"runtime failure policy {policy.policy_id} target node {target_node_id} "
            "must declare recovery_role=local_repair"
        )

    bindings = resolve_route_to_node_repair_closures(
        policy,
        runtime_effect_operations_by_id=runtime_effect_operations_by_id,
        runtime_effect_runners_by_id=runtime_effect_runners_by_id,
    )
    _validate_repair_route_family_scope(policy, bindings=bindings)
    for binding in bindings:
        _validate_route_target_can_claim_family(
            policy,
            binding=binding,
            target_stage_kind=target_stage_kind,
            families_by_id=families_by_id,
        )
        if binding.repair_operation_id not in runtime_effect_operations_by_id:
            raise CompilerValidationError(
                f"runtime failure policy {policy.policy_id} repair closure "
                f"{binding.source_operation_id}/{binding.failure_class} references unknown "
                f"repair operation {binding.repair_operation_id}"
            )
        repair_rule = next(
            (
                rule
                for rule in runtime_effect_rules_by_id.values()
                if rule.source_node_id == binding.target_node_id
                and binding.target_terminal_outcome in rule.on_outcomes
                and rule.effect_operation_id == binding.repair_operation_id
            ),
            None,
        )
        if repair_rule is None:
            raise CompilerValidationError(
                f"runtime failure policy {policy.policy_id} target node "
                f"{binding.target_node_id} outcome {binding.target_terminal_outcome} "
                f"does not invoke repair operation {binding.repair_operation_id}"
            )
        target_declared_outputs = set(target_stage_kind.declared_output_artifacts)
        required_rule_artifacts = set(repair_rule.required_run_artifacts)
        for artifact_id in binding.required_repair_evidence_artifact_ids:
            if artifact_id not in artifact_contracts_by_id:
                raise CompilerValidationError(
                    f"runtime failure policy {policy.policy_id} repair closure "
                    f"{binding.source_operation_id}/{binding.failure_class} requires unknown "
                    f"evidence artifact {artifact_id}"
                )
            if artifact_id not in target_declared_outputs:
                raise CompilerValidationError(
                    f"runtime failure policy {policy.policy_id} repair closure "
                    f"{binding.source_operation_id}/{binding.failure_class} requires evidence "
                    f"artifact {artifact_id} not emitted by target node {binding.target_node_id}"
                )
            if artifact_id not in required_rule_artifacts:
                raise CompilerValidationError(
                    f"runtime failure policy {policy.policy_id} target node "
                    f"{binding.target_node_id} outcome {binding.target_terminal_outcome} "
                    f"rule {repair_rule.rule_id} is missing required repair evidence artifact "
                    f"{artifact_id}"
                )
        if _policy_can_match_partial_mutation(policy) and not binding.supports_partial_mutation:
            raise CompilerValidationError(
                f"runtime failure policy {policy.policy_id} applies to partial mutation but "
                f"repair closure {binding.source_operation_id}/{binding.failure_class} does "
                "not support partial mutation"
            )
        if binding.requires_resume_guard and not _repair_resume_guard_present(
            target_node_id=binding.target_node_id,
            target_terminal_outcome=binding.target_terminal_outcome,
            active_planes=active_planes,
            graphs_by_plane=graphs_by_plane,
            graph_node_ids_by_plane=graph_node_ids_by_plane,
        ):
            raise CompilerValidationError(
                f"runtime failure policy {policy.policy_id} target node "
                f"{binding.target_node_id} lacks resume guard for "
                f"{binding.target_terminal_outcome}"
            )


def resolve_route_to_node_repair_closures(
    policy: RuntimeFailurePolicyDefinition,
    *,
    runtime_effect_operations_by_id: dict[str, RuntimeEffectOperationDefinition],
    runtime_effect_runners_by_id: dict[str, RuntimeEffectOperationRunnerDefinition],
) -> tuple[ResolvedRepairClosureBinding, ...]:
    mappings = tuple(policy.repair_closure_mappings)
    operation_ids = _route_policy_operation_ids(
        policy,
        runtime_effect_operations_by_id=runtime_effect_operations_by_id,
        runtime_effect_runners_by_id=runtime_effect_runners_by_id,
    )
    if not operation_ids:
        raise CompilerValidationError(
            f"runtime failure policy {policy.policy_id} cannot resolve repair closure "
            "without applies_to_operation_ids, applies_to_handler_ids, or repair_closure_mappings"
        )
    failure_classes = _route_policy_failure_classes(policy)
    if not failure_classes:
        raise CompilerValidationError(
            f"runtime failure policy {policy.policy_id} cannot resolve repair closure "
            "without applies_to_failure_classes or repair_closure_mappings"
        )

    operation_set = tuple(sorted(set(operation_ids)))
    failure_set = tuple(sorted(set(failure_classes)))
    mapping_by_pair = {
        (mapping.source_operation_id, mapping.failure_class): mapping
        for mapping in mappings
    }
    if (len(operation_set) > 1 or len(failure_set) > 1) and not mapping_by_pair:
        raise CompilerValidationError(
            f"runtime failure policy {policy.policy_id} must declare repair_closure_mappings "
            "for multi-operation or multi-failure-class route_to_node scope"
        )
    expected_pairs = {
        (operation_id, failure_class)
        for operation_id in operation_set
        for failure_class in failure_set
    }
    if mapping_by_pair:
        missing_pairs = sorted(expected_pairs - set(mapping_by_pair))
        if missing_pairs:
            missing_operation_id, missing_failure_class = missing_pairs[0]
            raise CompilerValidationError(
                f"runtime failure policy {policy.policy_id} missing repair_closure_mapping "
                f"for operation {missing_operation_id} failure class {missing_failure_class}"
            )
        extra_pairs = sorted(set(mapping_by_pair) - expected_pairs)
        if extra_pairs:
            extra_operation_id, extra_failure_class = extra_pairs[0]
            raise CompilerValidationError(
                f"runtime failure policy {policy.policy_id} repair_closure_mapping "
                f"{extra_operation_id}/{extra_failure_class} is outside policy operation/failure scope"
            )

    resolved: list[ResolvedRepairClosureBinding] = []
    for operation_id, failure_class in sorted(expected_pairs):
        source_operation = runtime_effect_operations_by_id.get(operation_id)
        if source_operation is None:
            raise CompilerValidationError(
                f"runtime failure policy {policy.policy_id} references unknown source "
                f"operation {operation_id} for repair closure resolution"
            )
        contract = _repair_closure_contract_for_failure(
            source_operation,
            failure_class=failure_class,
        )
        if contract is None:
            raise CompilerValidationError(
                f"runtime failure policy {policy.policy_id} source operation "
                f"{operation_id} has no repair closure contract for failure class "
                f"{failure_class}"
            )
        contract_binding = _resolved_binding_from_contract(
            source_operation_id=operation_id,
            contract=contract,
        )
        mapping = mapping_by_pair.get((operation_id, failure_class))
        if mapping is None:
            binding = contract_binding
        else:
            binding = _resolved_binding_from_mapping(mapping)
            _validate_explicit_repair_mapping_matches_contract(
                policy_id=policy.policy_id,
                mapping=binding,
                contract=contract_binding,
            )
        if binding.target_node_id != policy.target_node_id:
            raise CompilerValidationError(
                f"runtime failure policy {policy.policy_id} target node "
                f"{policy.target_node_id} does not match repair closure target "
                f"{binding.target_node_id} for {operation_id}/{failure_class}"
            )
        resolved.append(binding)
    return tuple(resolved)


def _validate_repair_route_family_scope(
    policy: RuntimeFailurePolicyDefinition,
    *,
    bindings: tuple[ResolvedRepairClosureBinding, ...],
) -> None:
    declared_families = set(policy.applies_to_families)
    repair_families = {binding.affected_source_family_id for binding in bindings}
    if not declared_families:
        expected_text = ", ".join(sorted(repair_families))
        raise CompilerValidationError(
            f"runtime failure policy {policy.policy_id} route_to_node repair closure "
            f"must declare applies_to_families matching repair closure families: "
            f"{expected_text}"
        )
    if declared_families != repair_families:
        declared_text = ", ".join(sorted(declared_families))
        expected_text = ", ".join(sorted(repair_families))
        raise CompilerValidationError(
            f"runtime failure policy {policy.policy_id} applies_to_families "
            f"{declared_text} must exactly match repair closure affected source "
            f"families: {expected_text}"
        )


def _route_policy_operation_ids(
    policy: RuntimeFailurePolicyDefinition,
    *,
    runtime_effect_operations_by_id: dict[str, RuntimeEffectOperationDefinition],
    runtime_effect_runners_by_id: dict[str, RuntimeEffectOperationRunnerDefinition],
) -> tuple[str, ...]:
    declared_operation_ids = tuple(policy.applies_to_operation_ids)
    if declared_operation_ids:
        return declared_operation_ids
    inferred_operation_ids = {
        operation_id
        for handler_id in policy.applies_to_handler_ids
        for operation_id in operation_ids_for_legacy_handler(
            handler_id,
            runtime_effect_operations_by_id=runtime_effect_operations_by_id,
            runtime_effect_runners_by_id=runtime_effect_runners_by_id,
        )
    }
    if inferred_operation_ids:
        return tuple(sorted(inferred_operation_ids))
    mapped_operation_ids = {
        mapping.source_operation_id
        for mapping in policy.repair_closure_mappings
    }
    return tuple(sorted(mapped_operation_ids))


def _route_policy_failure_classes(
    policy: RuntimeFailurePolicyDefinition,
) -> tuple[str, ...]:
    declared_failure_classes = tuple(policy.applies_to_failure_classes)
    if declared_failure_classes:
        return declared_failure_classes
    mapped_failure_classes = {
        mapping.failure_class
        for mapping in policy.repair_closure_mappings
    }
    return tuple(sorted(mapped_failure_classes))


def _resolved_binding_from_contract(
    *,
    source_operation_id: str,
    contract: RuntimeEffectRepairClosureContractDefinition,
) -> ResolvedRepairClosureBinding:
    return ResolvedRepairClosureBinding(
        source_operation_id=source_operation_id,
        failure_class=contract.failure_class,
        repair_operation_id=contract.repair_operation_id,
        target_node_id=contract.target_node_id,
        target_terminal_outcome=contract.target_terminal_outcome,
        required_repair_evidence_artifact_ids=contract.required_repair_evidence_artifact_ids,
        affected_source_family_id=contract.affected_source_family_id,
        source_lifecycle_behavior_on_repair_success=contract.source_lifecycle_behavior_on_repair_success,
        source_lifecycle_behavior_on_repair_failure=contract.source_lifecycle_behavior_on_repair_failure,
        supports_partial_mutation=contract.supports_partial_mutation,
        requires_resume_guard=contract.requires_resume_guard,
    )


def _resolved_binding_from_mapping(
    mapping: RuntimeFailurePolicyRepairClosureMappingDefinition,
) -> ResolvedRepairClosureBinding:
    return ResolvedRepairClosureBinding(
        source_operation_id=mapping.source_operation_id,
        failure_class=mapping.failure_class,
        repair_operation_id=mapping.repair_operation_id,
        target_node_id=mapping.target_node_id,
        target_terminal_outcome=mapping.target_terminal_outcome,
        required_repair_evidence_artifact_ids=mapping.required_repair_evidence_artifact_ids,
        affected_source_family_id=mapping.affected_source_family_id,
        source_lifecycle_behavior_on_repair_success=mapping.source_lifecycle_behavior_on_repair_success,
        source_lifecycle_behavior_on_repair_failure=mapping.source_lifecycle_behavior_on_repair_failure,
        supports_partial_mutation=mapping.supports_partial_mutation,
        requires_resume_guard=mapping.requires_resume_guard,
    )


def _repair_closure_contract_for_failure(
    operation: RuntimeEffectOperationDefinition,
    *,
    failure_class: str,
) -> RuntimeEffectRepairClosureContractDefinition | None:
    for contract in operation.repair_closure_contracts:
        if contract.failure_class == failure_class:
            return contract
    return None


def _validate_explicit_repair_mapping_matches_contract(
    *,
    policy_id: str,
    mapping: ResolvedRepairClosureBinding,
    contract: ResolvedRepairClosureBinding,
) -> None:
    for field_name in (
        "repair_operation_id",
        "target_node_id",
        "target_terminal_outcome",
        "required_repair_evidence_artifact_ids",
        "affected_source_family_id",
        "source_lifecycle_behavior_on_repair_success",
        "source_lifecycle_behavior_on_repair_failure",
        "supports_partial_mutation",
        "requires_resume_guard",
    ):
        if getattr(mapping, field_name) == getattr(contract, field_name):
            continue
        raise CompilerValidationError(
            f"runtime failure policy {policy_id} repair closure mapping "
            f"{mapping.source_operation_id}/{mapping.failure_class} field {field_name} "
            "does not match source operation repair closure contract"
        )


def _validate_route_target_can_claim_family(
    policy: RuntimeFailurePolicyDefinition,
    *,
    binding: ResolvedRepairClosureBinding,
    target_stage_kind: RegisteredStageKindDefinition,
    families_by_id: dict[str, WorkItemFamilyDefinition],
) -> None:
    family = families_by_id.get(binding.affected_source_family_id)
    if family is None:
        raise CompilerValidationError(
            f"runtime failure policy {policy.policy_id} repair closure "
            f"{binding.source_operation_id}/{binding.failure_class} references unknown "
            f"affected source family {binding.affected_source_family_id}"
        )
    adapter_id = resolve_queue_lifecycle_adapter_id(family)
    if adapter_id is None:
        raise CompilerValidationError(
            f"runtime failure policy {policy.policy_id} repair closure "
            f"{binding.source_operation_id}/{binding.failure_class} affected source "
            f"family {family.family_id} is missing queue lifecycle adapter id"
        )
    adapter = queue_adapter_for_id(adapter_id)
    if adapter is None:
        raise CompilerValidationError(
            f"runtime failure policy {policy.policy_id} repair closure "
            f"{binding.source_operation_id}/{binding.failure_class} affected source "
            f"family {family.family_id} references unknown queue lifecycle adapter "
            f"{adapter_id}"
        )
    if adapter.family_id != family.family_id:
        raise CompilerValidationError(
            f"runtime failure policy {policy.policy_id} repair closure "
            f"{binding.source_operation_id}/{binding.failure_class} affected source "
            f"family {family.family_id} references queue lifecycle adapter {adapter_id} "
            f"bound to family {adapter.family_id}"
        )
    if family.family_id not in target_stage_kind.allowed_work_item_families:
        raise CompilerValidationError(
            f"runtime failure policy {policy.policy_id} target node "
            f"{binding.target_node_id} cannot claim source family {family.family_id}"
        )


def _policy_can_match_partial_mutation(policy: RuntimeFailurePolicyDefinition) -> bool:
    mutation_phases = tuple(policy.applies_to_mutation_phases)
    return not mutation_phases or "partial_mutation" in mutation_phases


def _repair_resume_guard_present(
    *,
    target_node_id: str,
    target_terminal_outcome: str,
    active_planes: tuple[Plane, ...],
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    graph_node_ids_by_plane: dict[Plane, set[str]],
) -> bool:
    for plane in active_planes:
        if target_node_id not in graph_node_ids_by_plane.get(plane, set()):
            continue
        graph = graphs_by_plane[plane]
        if any(
            resume.source_node_id == target_node_id
            and resume.on_outcome == target_terminal_outcome
            and "resume_stage" in resume.metadata_stage_keys
            and target_node_id in resume.disallowed_target_node_ids
            for resume in graph.compiled_resume_policies
        ):
            return True
    return False


__all__ = [
    "ResolvedRepairClosureBinding",
    "resolve_route_to_node_repair_closures",
    "validate_runtime_effect_repair_route_closure",
]
