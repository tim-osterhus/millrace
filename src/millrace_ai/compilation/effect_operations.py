"""Compiler validation for declarative runtime effect operation assets."""

from __future__ import annotations

from millrace_ai.architecture import (
    ArtifactContractDefinition,
    RuntimeEffectOperationDefinition,
    RuntimeEffectRuleDefinition,
    RuntimeEffectStoreDefinition,
    RuntimeEffectValidatorDefinition,
)

from .outcomes import CompilerValidationError

_SUPPORTED_EFFECT_PRIMITIVE_IDS = frozenset(
    {
        "artifact_presence",
        "legacy_python_handler",
        "mutation_journal_append",
    }
)


def validate_runtime_effect_operations(
    *,
    artifact_contracts_by_id: dict[str, ArtifactContractDefinition],
    runtime_effect_rules_by_id: dict[str, RuntimeEffectRuleDefinition],
    effect_stores_by_id: dict[str, RuntimeEffectStoreDefinition],
    effect_validators_by_id: dict[str, RuntimeEffectValidatorDefinition],
    runtime_effect_operations_by_id: dict[str, RuntimeEffectOperationDefinition],
) -> None:
    """Validate inert declarative operation catalogs against selected effect rules."""

    for rule in runtime_effect_rules_by_id.values():
        operation_id = rule.effect_operation_id
        operation = runtime_effect_operations_by_id.get(operation_id)
        if operation is None:
            raise CompilerValidationError(
                f"runtime effect rule {rule.rule_id} references unknown operation {operation_id}"
            )
        _validate_rule_operation_compatibility(rule, operation)

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
) -> None:
    if rule.handler_id not in operation.legacy_handler_ids:
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


__all__ = ["validate_runtime_effect_operations"]
