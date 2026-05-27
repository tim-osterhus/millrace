"""Runtime effect handler, rule, and operation catalog validation."""

from __future__ import annotations

from millrace_ai.architecture import (
    ArtifactContractDefinition,
    LifecycleMutationPlanDefinition,
    RegisteredStageKindDefinition,
    RuntimeEffectHandlerDefinition,
    RuntimeEffectOperationDefinition,
    RuntimeEffectOperationRunnerDefinition,
    RuntimeEffectRuleDefinition,
    RuntimeEffectStoreDefinition,
    RuntimeEffectValidatorDefinition,
    WorkItemFamilyDefinition,
)

from ..outcomes import CompilerValidationError
from .operation_runners import (
    operation_ids_for_legacy_handler,
    validate_runtime_effect_runner_registry,
)

_SUPPORTED_EFFECT_PRIMITIVE_IDS = frozenset(
    {
        "artifact_presence",
        "artifact_model_parse",
        "active_work_item_lookup",
        "blueprint_critique_packet_validation",
        "blueprint_evaluation_packet_validation",
        "blueprint_generated_task_validation",
        "blueprint_manifest_sequence_validation",
        "blueprint_packet_draft_validation",
        "copy_artifact",
        "enqueue_work_items",
        "legacy_python_handler",
        "move_record",
        "mutation_journal_append",
        "persist_record",
        "source_lifecycle",
        "store_equivalence_check",
        "work_item_patch",
    }
)


def validate_runtime_effect_handlers(
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


def validate_runtime_effect_rules(
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
            operation_ids = operation_ids_for_legacy_handler(
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


def validate_runtime_effect_operations(
    *,
    artifact_contracts_by_id: dict[str, ArtifactContractDefinition],
    runtime_effect_rules_by_id: dict[str, RuntimeEffectRuleDefinition],
    effect_stores_by_id: dict[str, RuntimeEffectStoreDefinition],
    effect_validators_by_id: dict[str, RuntimeEffectValidatorDefinition],
    runtime_effect_operations_by_id: dict[str, RuntimeEffectOperationDefinition],
    runtime_effect_runners_by_id: dict[str, RuntimeEffectOperationRunnerDefinition],
) -> None:
    runners_by_operation_id = validate_runtime_effect_runner_registry(
        runtime_effect_operations_by_id=runtime_effect_operations_by_id,
        runtime_effect_runners_by_id=runtime_effect_runners_by_id,
    )

    for rule in runtime_effect_rules_by_id.values():
        operation_id = rule.effect_operation_id
        operation = runtime_effect_operations_by_id.get(operation_id)
        if operation is None:
            raise CompilerValidationError(
                f"runtime effect rule {rule.rule_id} references unknown operation {operation_id}"
            )
        runner = runners_by_operation_id.get(operation_id)
        if runner is None:
            raise CompilerValidationError(
                f"runtime effect rule {rule.rule_id} references operation {operation_id} "
                "without a runtime effect runner"
            )
        _validate_rule_operation_compatibility(rule, operation, runner)

    for validator in effect_validators_by_id.values():
        _validate_primitive_id(
            validator.primitive_id,
            source_label=f"runtime effect validator {validator.validator_id}",
        )
        for artifact_id in validator.input_artifact_ids:
            if artifact_id not in artifact_contracts_by_id:
                raise CompilerValidationError(
                    f"runtime effect validator {validator.validator_id} references unknown artifact "
                    f"{artifact_id}"
                )
        for store_id in validator.store_ids:
            if store_id not in effect_stores_by_id:
                raise CompilerValidationError(
                    f"runtime effect validator {validator.validator_id} references unknown store "
                    f"{store_id}"
                )

    for operation in runtime_effect_operations_by_id.values():
        _validate_runtime_effect_operation(
            operation,
            artifact_contracts_by_id=artifact_contracts_by_id,
            effect_stores_by_id=effect_stores_by_id,
            effect_validators_by_id=effect_validators_by_id,
        )


def _validate_runtime_effect_operation(
    operation: RuntimeEffectOperationDefinition,
    *,
    artifact_contracts_by_id: dict[str, ArtifactContractDefinition],
    effect_stores_by_id: dict[str, RuntimeEffectStoreDefinition],
    effect_validators_by_id: dict[str, RuntimeEffectValidatorDefinition],
) -> None:
    known_operation_artifacts = set(operation.required_artifacts) | set(operation.produced_artifacts)
    for artifact_id in known_operation_artifacts:
        if artifact_id not in artifact_contracts_by_id:
            raise CompilerValidationError(
                f"runtime effect operation {operation.operation_id} references unknown artifact "
                f"{artifact_id}"
            )

    write_step_count = 0
    for step in operation.steps:
        _validate_primitive_id(
            step.primitive_id,
            source_label=f"runtime effect operation {operation.operation_id} step {step.step_id}",
        )
        for artifact_id in step.reads_artifact_ids:
            if artifact_id not in known_operation_artifacts:
                raise CompilerValidationError(
                    f"runtime effect operation {operation.operation_id} step {step.step_id} "
                    f"references artifact {artifact_id} not declared by the operation"
                )
        if step.store_id is not None and step.store_id not in effect_stores_by_id:
            raise CompilerValidationError(
                f"runtime effect operation {operation.operation_id} step {step.step_id} "
                f"references unknown store {step.store_id}"
            )
        for validator_id in step.validator_ids:
            validator = effect_validators_by_id.get(validator_id)
            if validator is None:
                raise CompilerValidationError(
                    f"runtime effect operation {operation.operation_id} step {step.step_id} "
                    f"references unknown validator {validator_id}"
                )
            _validate_operation_validator_binding(operation, validator)
        if step.writes_store:
            write_step_count += 1

    for mapping in operation.failure_mappings:
        if mapping.validator_id is not None and mapping.validator_id not in effect_validators_by_id:
            raise CompilerValidationError(
                f"runtime effect operation {operation.operation_id} failure mapping "
                f"{mapping.failure_class} references unknown validator {mapping.validator_id}"
            )
    for validator_id in operation.idempotency.equivalence_validator_ids:
        validator = effect_validators_by_id.get(validator_id)
        if validator is None:
            raise CompilerValidationError(
                f"runtime effect operation {operation.operation_id} idempotency references unknown "
                f"validator {validator_id}"
            )
        _validate_operation_validator_binding(operation, validator)

    if write_step_count > 1 and operation.partial_commit_policy is None:
        raise CompilerValidationError(
            f"runtime effect operation {operation.operation_id} has multiple write steps "
            "without partial_commit_policy"
        )


def _validate_rule_operation_compatibility(
    rule: RuntimeEffectRuleDefinition,
    operation: RuntimeEffectOperationDefinition,
    runner: RuntimeEffectOperationRunnerDefinition,
) -> None:
    if (
        rule.handler_id is not None
        and runner.operation_id_for_legacy_handler(rule.handler_id) != operation.operation_id
    ):
        raise CompilerValidationError(
            f"runtime effect rule {rule.rule_id} handler {rule.handler_id} is not a legacy alias "
            f"for operation {operation.operation_id}"
        )

    missing_artifacts = set(rule.required_run_artifacts) - set(operation.required_artifacts)
    if missing_artifacts:
        artifacts = ", ".join(sorted(missing_artifacts))
        raise CompilerValidationError(
            f"runtime effect rule {rule.rule_id} requires artifacts not declared by operation "
            f"{operation.operation_id}: {artifacts}"
        )

    if rule.duplicate_policy != operation.idempotency.duplicate_policy:
        raise CompilerValidationError(
            f"runtime effect rule {rule.rule_id} duplicate_policy does not match operation "
            f"{operation.operation_id}"
        )
    if rule.replay_policy != operation.idempotency.replay_policy:
        raise CompilerValidationError(
            f"runtime effect rule {rule.rule_id} replay_policy does not match operation "
            f"{operation.operation_id}"
        )
    missing_capabilities = set(rule.required_handler_capabilities) - set(
        runner.runtime_capabilities_for_operation(operation.operation_id)
    )
    if missing_capabilities:
        capability = sorted(missing_capabilities)[0]
        raise CompilerValidationError(
            f"runtime effect rule {rule.rule_id} requires runner capability "
            f"{capability} not declared by runner {runner.runner_id}"
        )


def _validate_operation_validator_binding(
    operation: RuntimeEffectOperationDefinition,
    validator: RuntimeEffectValidatorDefinition,
) -> None:
    known_operation_artifacts = set(operation.required_artifacts) | set(operation.produced_artifacts)
    for artifact_id in validator.input_artifact_ids:
        if artifact_id not in known_operation_artifacts:
            raise CompilerValidationError(
                f"runtime effect operation {operation.operation_id} binds validator "
                f"{validator.validator_id} to artifact {artifact_id} not declared by the operation"
            )
    mapped_failure_classes = {
        mapping.failure_class
        for mapping in operation.failure_mappings
    }
    if validator.failure_class not in mapped_failure_classes:
        raise CompilerValidationError(
            f"runtime effect operation {operation.operation_id} binds validator "
            f"{validator.validator_id} with unmapped failure class {validator.failure_class}"
        )


def _validate_primitive_id(primitive_id: str, *, source_label: str) -> None:
    if primitive_id in _SUPPORTED_EFFECT_PRIMITIVE_IDS:
        return
    raise CompilerValidationError(
        f"{source_label} references unknown primitive {primitive_id}"
    )


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
    "validate_runtime_effect_handlers",
    "validate_runtime_effect_operations",
    "validate_runtime_effect_rules",
]
