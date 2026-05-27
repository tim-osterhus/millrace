"""Artifact and document-adapter validation helpers."""

from __future__ import annotations

from pathlib import PurePosixPath

from millrace_ai.architecture import (
    ArtifactContractDefinition,
    ArtifactFilenameAdapterDefinition,
    ArtifactFormat,
    RegisteredStageKindDefinition,
    RuntimeEffectHandlerDefinition,
    RuntimeEffectOperationDefinition,
    RuntimeEffectOperationRunnerDefinition,
    WorkItemDocumentAdapterDefinition,
    WorkItemFamilyDefinition,
)

from ..outcomes import CompilerValidationError

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


def validate_document_adapters(
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


def validate_artifact_contracts(
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
        if (
            contract.destination_family_id is not None
            and contract.destination_family_id not in families_by_id
        ):
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
            if (
                contract.consumer_operation_ids
                and operation_id not in contract.consumer_operation_ids
            ):
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


__all__ = [
    "validate_artifact_contracts",
    "validate_document_adapters",
]
