"""Typed contracts for declarative runtime effect operation assets."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .common import normalize_canonical_id, normalize_nonempty_text
from .stage_kinds import ArchitectureContractModel

RuntimeEffectOperationId = str
RuntimeEffectPrimitiveId = str
RuntimeEffectStoreId = str
RuntimeEffectValidatorId = str
RuntimeEffectStepId = str
RuntimeEffectMutationPhaseValue = Literal["pre_mutation", "partial_mutation", "unknown"]


class RuntimeEffectStoreDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["runtime_effect_store"] = "runtime_effect_store"
    store_id: RuntimeEffectStoreId
    store_type: Literal[
        "run_artifacts",
        "workspace_state",
        "queue_family",
        "blueprint_state",
        "runtime_state",
        "mutation_journal",
    ]
    runtime_relative_root: str
    path_template: str | None = None
    owner: Literal["runtime", "operator", "stage"] = "runtime"
    write_policy: Literal["read_only", "single_write", "idempotent_write", "append_only", "move"] = "single_write"

    @field_validator("store_id")
    @classmethod
    def validate_store_id(cls, value: str) -> str:
        return normalize_canonical_id(value, field_label="store_id")

    @field_validator("runtime_relative_root", "path_template")
    @classmethod
    def validate_runtime_path_template(
        cls,
        value: str | None,
        info: ValidationInfo,
    ) -> str | None:
        if value is None:
            return None
        return _normalize_runtime_path_template(
            value,
            field_label=info.field_name or "runtime path template",
        )


class RuntimeEffectValidatorDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["runtime_effect_validator"] = "runtime_effect_validator"
    validator_id: RuntimeEffectValidatorId
    primitive_id: RuntimeEffectPrimitiveId
    input_artifact_ids: tuple[str, ...] = ()
    store_ids: tuple[RuntimeEffectStoreId, ...] = ()
    failure_class: str
    mutation_phase: RuntimeEffectMutationPhaseValue = "pre_mutation"

    @field_validator("validator_id", "primitive_id", "failure_class")
    @classmethod
    def validate_ids(cls, value: str, info: ValidationInfo) -> str:
        return normalize_canonical_id(value, field_label=info.field_name or "validator id")

    @field_validator("input_artifact_ids", "store_ids", mode="before")
    @classmethod
    def normalize_id_tuples(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label=info.field_name or "validator references",
            allow_empty=True,
        )


class RuntimeEffectOperationStepDefinition(ArchitectureContractModel):
    step_id: RuntimeEffectStepId
    primitive_id: RuntimeEffectPrimitiveId
    mutation_phase: RuntimeEffectMutationPhaseValue = "pre_mutation"
    reads_artifact_ids: tuple[str, ...] = ()
    store_id: RuntimeEffectStoreId | None = None
    validator_ids: tuple[RuntimeEffectValidatorId, ...] = ()
    writes_store: bool = False
    journal_event_type: str | None = None

    @field_validator("step_id", "primitive_id", "store_id", "journal_event_type")
    @classmethod
    def validate_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return normalize_canonical_id(value, field_label=info.field_name or "operation step id")

    @field_validator("reads_artifact_ids", "validator_ids", mode="before")
    @classmethod
    def normalize_id_tuples(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label=info.field_name or "operation step references",
            allow_empty=True,
        )

    @model_validator(mode="after")
    def validate_write_store(self) -> "RuntimeEffectOperationStepDefinition":
        if self.writes_store and self.store_id is None:
            raise ValueError("store_id is required when writes_store is true")
        return self


class RuntimeEffectFailureMappingDefinition(ArchitectureContractModel):
    failure_class: str
    mutation_phase: RuntimeEffectMutationPhaseValue
    validator_id: RuntimeEffectValidatorId | None = None
    retryable: bool = False

    @field_validator("failure_class", "validator_id")
    @classmethod
    def validate_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return normalize_canonical_id(value, field_label=info.field_name or "failure mapping id")


class RuntimeEffectIdempotencyDefinition(ArchitectureContractModel):
    duplicate_policy: Literal["fail", "supersede", "idempotent"]
    replay_policy: Literal["resume_idempotently", "fail_if_seen", "require_operator"]
    equivalence_validator_ids: tuple[RuntimeEffectValidatorId, ...] = ()

    @field_validator("equivalence_validator_ids", mode="before")
    @classmethod
    def normalize_equivalence_validators(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label="equivalence_validator_ids",
            allow_empty=True,
        )


class RuntimeEffectMutationJournalDefinition(ArchitectureContractModel):
    schema_id: Literal["runtime_effect_mutation_journal_v1"] = "runtime_effect_mutation_journal_v1"
    entry_id_template: str
    required_fields: tuple[str, ...] = Field(min_length=1)
    record_step_ids: tuple[RuntimeEffectStepId, ...] = ()

    @field_validator("entry_id_template")
    @classmethod
    def validate_entry_id_template(cls, value: str) -> str:
        return normalize_nonempty_text(value, field_label="entry_id_template")

    @field_validator("required_fields", "record_step_ids", mode="before")
    @classmethod
    def normalize_id_tuples(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label=info.field_name or "journal references",
            allow_empty=info.field_name != "required_fields",
        )


class RuntimeEffectOperationDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["runtime_effect_operation"] = "runtime_effect_operation"
    operation_id: RuntimeEffectOperationId
    display_name: str
    legacy_handler_ids: tuple[str, ...] = ()
    required_artifacts: tuple[str, ...] = ()
    produced_artifacts: tuple[str, ...] = ()
    steps: tuple[RuntimeEffectOperationStepDefinition, ...] = Field(min_length=1)
    idempotency: RuntimeEffectIdempotencyDefinition
    failure_mappings: tuple[RuntimeEffectFailureMappingDefinition, ...] = Field(min_length=1)
    mutation_journal: RuntimeEffectMutationJournalDefinition
    partial_commit_policy: Literal["block_source", "pause_lane", "stop_daemon", "require_operator"] | None = None

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        return normalize_canonical_id(value, field_label="operation_id")

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        return normalize_nonempty_text(value, field_label="display_name")

    @field_validator("legacy_handler_ids", "required_artifacts", "produced_artifacts", mode="before")
    @classmethod
    def normalize_id_tuples(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label=info.field_name or "operation references",
            allow_empty=True,
        )

    @model_validator(mode="after")
    def validate_step_closure(self) -> "RuntimeEffectOperationDefinition":
        step_ids = [step.step_id for step in self.steps]
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("operation steps may not contain duplicate step_id values")
        unknown_journal_steps = set(self.mutation_journal.record_step_ids) - set(step_ids)
        if unknown_journal_steps:
            raise ValueError("mutation_journal record_step_ids must reference operation steps")
        return self


def _ensure_sequence(
    value: object,
    *,
    field_label: str,
    allow_empty: bool,
) -> tuple[object, ...]:
    if value is None:
        values: tuple[object, ...] = ()
    elif isinstance(value, str):
        values = (value,)
    else:
        try:
            values = tuple(value)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ValueError(f"{field_label} must be a sequence") from exc
    if not values and not allow_empty:
        raise ValueError(f"{field_label} must not be empty")
    return values


def _normalize_unique_id_tuple(
    value: object,
    *,
    field_label: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    raw = _ensure_sequence(value, field_label=field_label, allow_empty=allow_empty)
    normalized = [
        normalize_canonical_id(str(item), field_label=field_label)
        for item in raw
    ]
    seen: set[str] = set()
    deduped: list[str] = []
    for item in normalized:
        if item in seen:
            raise ValueError(f"duplicate {field_label} value: {item}")
        seen.add(item)
        deduped.append(item)
    return tuple(deduped)


def _normalize_runtime_path_template(value: str, *, field_label: str) -> str:
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        raise ValueError(f"{field_label} may not be empty")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or path.as_posix() == ".":
        raise ValueError(f"{field_label} must be a safe runtime-relative path template")
    return path.as_posix()


__all__ = [
    "RuntimeEffectFailureMappingDefinition",
    "RuntimeEffectIdempotencyDefinition",
    "RuntimeEffectMutationJournalDefinition",
    "RuntimeEffectMutationPhaseValue",
    "RuntimeEffectOperationDefinition",
    "RuntimeEffectOperationId",
    "RuntimeEffectOperationStepDefinition",
    "RuntimeEffectPrimitiveId",
    "RuntimeEffectStepId",
    "RuntimeEffectStoreDefinition",
    "RuntimeEffectStoreId",
    "RuntimeEffectValidatorDefinition",
    "RuntimeEffectValidatorId",
]
