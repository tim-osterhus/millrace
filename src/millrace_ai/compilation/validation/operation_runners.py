"""Runtime effect runner registry validation and legacy alias resolution."""

from __future__ import annotations

from millrace_ai.architecture import (
    RuntimeEffectOperationDefinition,
    RuntimeEffectOperationRunnerDefinition,
)

from ..outcomes import CompilerValidationError


def validate_runtime_effect_runner_registry(
    *,
    runtime_effect_operations_by_id: dict[str, RuntimeEffectOperationDefinition],
    runtime_effect_runners_by_id: dict[str, RuntimeEffectOperationRunnerDefinition],
) -> dict[str, RuntimeEffectOperationRunnerDefinition]:
    runners_by_operation_id: dict[str, RuntimeEffectOperationRunnerDefinition] = {}
    aliases_by_handler_id: dict[str, tuple[str, str]] = {}

    for runner in runtime_effect_runners_by_id.values():
        for operation_id in runner.operation_ids:
            if operation_id not in runtime_effect_operations_by_id:
                raise CompilerValidationError(
                    f"runtime effect runner {runner.runner_id} references unknown operation "
                    f"{operation_id}"
                )
            previous_runner = runners_by_operation_id.get(operation_id)
            if previous_runner is not None:
                raise CompilerValidationError(
                    f"runtime effect operation {operation_id} is owned by multiple runners: "
                    f"{previous_runner.runner_id}, {runner.runner_id}"
                )
            runners_by_operation_id[operation_id] = runner

        for handler_id in runner.legacy_handler_ids:
            alias_operation_id = runner.operation_id_for_legacy_handler(handler_id)
            if alias_operation_id is None:
                raise CompilerValidationError(
                    f"runtime effect runner {runner.runner_id} maps legacy handler "
                    f"{handler_id} ambiguously"
                )
            previous = aliases_by_handler_id.get(handler_id)
            current = (alias_operation_id, runner.runner_id)
            if previous is not None and previous != current:
                previous_operation_id, previous_runner_id = previous
                raise CompilerValidationError(
                    f"legacy runtime effect handler {handler_id} is mapped to multiple "
                    f"operations or runners: {previous_operation_id}/{previous_runner_id}, "
                    f"{alias_operation_id}/{runner.runner_id}"
                )
            aliases_by_handler_id[handler_id] = current

    for operation in runtime_effect_operations_by_id.values():
        operation_runner = runners_by_operation_id.get(operation.operation_id)
        if operation_runner is None:
            continue
        for handler_id in operation.legacy_handler_ids:
            if operation_runner.operation_id_for_legacy_handler(handler_id) != operation.operation_id:
                raise CompilerValidationError(
                    f"runtime effect operation {operation.operation_id} legacy handler "
                    f"{handler_id} is not mapped by runner {operation_runner.runner_id}"
                )

    return runners_by_operation_id


def operation_ids_for_legacy_handler(
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


def legacy_handler_ids_for_operation(
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


__all__ = [
    "legacy_handler_ids_for_operation",
    "operation_ids_for_legacy_handler",
    "validate_runtime_effect_runner_registry",
]
