"""Runtime-effect rule and handler contracts for workflow primitives."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

from millrace_ai.contracts import Plane

from ..common import normalize_canonical_id, normalize_status
from ..stage_kinds import ArchitectureContractModel
from ._validation import (
    _canonical,
    _normalize_unique_id_tuple,
    _normalize_unique_status_tuple,
)
from .identifiers import (
    LifecycleMutationPlanId,
    RuntimeEffectHandlerId,
    RuntimeEffectOperationRunnerId,
    RuntimeEffectRuleId,
    WorkItemFamilyId,
)


class RuntimeEffectHandlerDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["runtime_effect_handler"] = "runtime_effect_handler"
    handler_id: RuntimeEffectHandlerId
    source_planes: tuple[Plane, ...] = Field(min_length=1)
    allowed_source_families: tuple[WorkItemFamilyId, ...] = ()
    destination_kinds: tuple[str, ...] = ()
    required_artifacts: tuple[str, ...] = ()
    optional_artifacts: tuple[str, ...] = ()
    returns_source_lifecycle_intent: bool
    requires_lifecycle_mutation_plan: bool
    creates_work_items: bool
    creates_incidents: bool = False
    creates_closure_targets: bool = False
    declared_capabilities: tuple[str, ...] = ()
    failure_classes: tuple[str, ...] = Field(min_length=1)

    @field_validator("handler_id")
    @classmethod
    def validate_handler_id(cls, value: str) -> str:
        return normalize_canonical_id(value, field_label="handler_id")

    @field_validator(
        "allowed_source_families",
        "destination_kinds",
        "required_artifacts",
        "optional_artifacts",
        "declared_capabilities",
        "failure_classes",
        mode="before",
    )
    @classmethod
    def normalize_id_tuples(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        field_name = info.field_name or "id tuple"
        return _normalize_unique_id_tuple(
            value,
            field_label=field_name,
            allow_empty=field_name != "failure_classes",
        )

    @model_validator(mode="after")
    def validate_handler_metadata(self) -> "RuntimeEffectHandlerDefinition":
        if self.returns_source_lifecycle_intent and not self.requires_lifecycle_mutation_plan:
            raise ValueError(
                "requires_lifecycle_mutation_plan must be true when returns_source_lifecycle_intent is true"
            )
        if self.creates_work_items and not self.destination_kinds:
            raise ValueError("destination_kinds is required when creates_work_items is true")
        return self


class RuntimeEffectOperationRunnerDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["runtime_effect_runner"] = "runtime_effect_runner"
    runner_id: RuntimeEffectOperationRunnerId
    operation_ids: tuple[str, ...] = Field(min_length=1)
    required_runtime_capabilities: tuple[str, ...] = ()
    required_runtime_capabilities_by_operation_id: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    legacy_handler_ids: tuple[RuntimeEffectHandlerId, ...] = ()
    legacy_handler_operation_ids: dict[RuntimeEffectHandlerId, str] = Field(default_factory=dict)
    result_display_aliases: dict[str, RuntimeEffectHandlerId] = Field(default_factory=dict)

    @field_validator("runner_id")
    @classmethod
    def validate_runner_id(cls, value: str) -> str:
        return normalize_canonical_id(value, field_label="runner_id")

    @field_validator(
        "operation_ids",
        "required_runtime_capabilities",
        "legacy_handler_ids",
        mode="before",
    )
    @classmethod
    def normalize_id_tuples(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label=info.field_name or "runtime effect runner references",
            allow_empty=info.field_name != "operation_ids",
        )

    @field_validator("legacy_handler_operation_ids", "result_display_aliases", mode="before")
    @classmethod
    def normalize_alias_maps(cls, value: object, info: ValidationInfo) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError(f"{info.field_name or 'alias map'} must be a mapping")
        normalized: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            key = normalize_canonical_id(
                str(raw_key),
                field_label=f"{info.field_name or 'alias map'} key",
            )
            if key in normalized:
                raise ValueError(f"duplicate {info.field_name or 'alias map'} key: {key}")
            normalized[key] = normalize_canonical_id(
                str(raw_value),
                field_label=f"{info.field_name or 'alias map'} value",
            )
        return normalized

    @field_validator("required_runtime_capabilities_by_operation_id", mode="before")
    @classmethod
    def normalize_operation_capability_map(cls, value: object) -> dict[str, tuple[str, ...]]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("required_runtime_capabilities_by_operation_id must be a mapping")
        normalized: dict[str, tuple[str, ...]] = {}
        for raw_operation_id, raw_capabilities in value.items():
            operation_id = normalize_canonical_id(
                str(raw_operation_id),
                field_label="required_runtime_capabilities_by_operation_id key",
            )
            if operation_id in normalized:
                raise ValueError(
                    "duplicate required_runtime_capabilities_by_operation_id key: "
                    f"{operation_id}"
                )
            normalized[operation_id] = _normalize_unique_id_tuple(
                raw_capabilities,
                field_label="required_runtime_capabilities_by_operation_id value",
                allow_empty=True,
            )
        return normalized

    @model_validator(mode="after")
    def validate_alias_mapping(self) -> "RuntimeEffectOperationRunnerDefinition":
        operation_ids = set(self.operation_ids)
        legacy_handler_ids = set(self.legacy_handler_ids)
        mapped_handlers = set(self.legacy_handler_operation_ids)
        unknown_handlers = mapped_handlers - legacy_handler_ids
        if unknown_handlers:
            raise ValueError("legacy_handler_operation_ids keys must be declared in legacy_handler_ids")
        unknown_capability_operations = (
            set(self.required_runtime_capabilities_by_operation_id) - operation_ids
        )
        if unknown_capability_operations:
            raise ValueError(
                "required_runtime_capabilities_by_operation_id keys must be declared in operation_ids"
            )
        unknown_operations = set(self.legacy_handler_operation_ids.values()) - operation_ids
        if unknown_operations:
            raise ValueError("legacy_handler_operation_ids values must be declared in operation_ids")
        if len(operation_ids) > 1 and legacy_handler_ids != mapped_handlers:
            raise ValueError(
                "legacy_handler_operation_ids must map every legacy_handler_id "
                "when a runner supports multiple operations"
            )
        unknown_display_operations = set(self.result_display_aliases) - operation_ids
        if unknown_display_operations:
            raise ValueError("result_display_aliases keys must be declared in operation_ids")
        unknown_display_aliases = set(self.result_display_aliases.values()) - legacy_handler_ids
        if unknown_display_aliases:
            raise ValueError("result_display_aliases values must be declared in legacy_handler_ids")
        return self

    def operation_id_for_legacy_handler(self, handler_id: RuntimeEffectHandlerId) -> str | None:
        if handler_id not in self.legacy_handler_ids:
            return None
        explicit_operation_id = self.legacy_handler_operation_ids.get(handler_id)
        if explicit_operation_id is not None:
            return explicit_operation_id
        if len(self.operation_ids) == 1:
            return self.operation_ids[0]
        return None

    def runtime_capabilities_for_operation(self, operation_id: str) -> tuple[str, ...]:
        operation_capabilities = self.required_runtime_capabilities_by_operation_id.get(operation_id)
        if operation_capabilities is not None:
            return operation_capabilities
        return self.required_runtime_capabilities


class RuntimeEffectRuleDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["runtime_effect_rule"] = "runtime_effect_rule"
    rule_id: RuntimeEffectRuleId
    effect_operation_id: str
    source_node_id: str
    on_outcomes: tuple[str, ...] = Field(min_length=1)
    handler_id: RuntimeEffectHandlerId | None = None
    required_run_artifacts: tuple[str, ...] = ()
    destination_family_id: WorkItemFamilyId | None = None
    creates_work_items: bool = False
    required_handler_capabilities: tuple[str, ...] = ()
    duplicate_policy: Literal["fail", "supersede", "idempotent"]
    partial_commit_policy: Literal["block_source", "pause_lane", "stop_daemon", "require_operator"]
    replay_policy: Literal["resume_idempotently", "fail_if_seen", "require_operator"]
    lineage_policy: Literal["preserve_root", "require_root", "derive_from_source"]
    applies_before_route: bool
    lifecycle_mutation_plan_id: LifecycleMutationPlanId | None = None
    source_completion_lifecycle_mutation_plan_id: LifecycleMutationPlanId | None = None
    source_blocking_lifecycle_mutation_plan_id: LifecycleMutationPlanId | None = None
    source_completion_lifecycle_mutation_plan_ids_by_family: dict[
        WorkItemFamilyId,
        LifecycleMutationPlanId,
    ] = Field(default_factory=dict)
    source_blocking_lifecycle_mutation_plan_ids_by_family: dict[
        WorkItemFamilyId,
        LifecycleMutationPlanId,
    ] = Field(default_factory=dict)

    @field_validator(
        "rule_id",
        "effect_operation_id",
        "source_node_id",
        "handler_id",
        "destination_family_id",
        "lifecycle_mutation_plan_id",
        "source_completion_lifecycle_mutation_plan_id",
        "source_blocking_lifecycle_mutation_plan_id",
    )
    @classmethod
    def validate_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _canonical(value, info)

    @field_validator("on_outcomes", mode="before")
    @classmethod
    def normalize_outcomes(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_status_tuple(value, field_label="on_outcomes", allow_empty=False)

    @field_validator("required_run_artifacts", mode="before")
    @classmethod
    def normalize_required_run_artifacts(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(value, field_label="required_run_artifacts", allow_empty=True)

    @field_validator("required_handler_capabilities", mode="before")
    @classmethod
    def normalize_required_handler_capabilities(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label="required_handler_capabilities",
            allow_empty=True,
        )

    @field_validator(
        "source_completion_lifecycle_mutation_plan_ids_by_family",
        "source_blocking_lifecycle_mutation_plan_ids_by_family",
        mode="before",
    )
    @classmethod
    def normalize_lifecycle_plan_maps(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError(f"{info.field_name or 'lifecycle plan map'} must be a mapping")
        normalized: dict[str, str] = {}
        for raw_family_id, raw_plan_id in value.items():
            family_id = normalize_canonical_id(
                str(raw_family_id),
                field_label=f"{info.field_name or 'lifecycle plan map'} key",
            )
            if family_id in normalized:
                raise ValueError(f"duplicate {info.field_name or 'lifecycle plan map'} key: {family_id}")
            normalized[family_id] = normalize_canonical_id(
                str(raw_plan_id),
                field_label=f"{info.field_name or 'lifecycle plan map'} value",
            )
        return normalized

    @model_validator(mode="after")
    def validate_destination(self) -> "RuntimeEffectRuleDefinition":
        if self.creates_work_items and self.destination_family_id is None:
            raise ValueError("destination_family_id is required when creates_work_items is true")
        return self


class OutcomeArtifactDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["outcome_artifact"] = "outcome_artifact"
    outcome_id: str
    required_artifacts: tuple[str, ...] = ()
    optional_artifacts: tuple[str, ...] = ()

    @field_validator("outcome_id")
    @classmethod
    def validate_outcome_id(cls, value: str) -> str:
        return normalize_status(value, field_label="outcome_id")

    @field_validator("required_artifacts", "optional_artifacts", mode="before")
    @classmethod
    def normalize_artifacts(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label=info.field_name or "artifact ids",
            allow_empty=True,
        )
