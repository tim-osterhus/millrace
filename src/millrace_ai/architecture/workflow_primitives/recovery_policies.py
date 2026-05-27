"""Recovery and runtime-failure policy contracts for workflow primitives."""

from __future__ import annotations

from typing import Literal, cast

from pydantic import Field, ValidationInfo, field_validator, model_validator

from millrace_ai.contracts import Plane

from ..common import normalize_canonical_id
from ..effect_operations import RuntimeEffectRepairClosureContractDefinition
from ..stage_kinds import ArchitectureContractModel
from ._validation import _canonical, _normalize_unique_id_tuple, _normalize_unique_status_tuple
from .identifiers import (
    RuntimeEffectHandlerId,
    RuntimeEffectMutationPhaseValue,
    WorkItemFamilyId,
)


class WorkflowRecoveryPolicyDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["workflow_recovery_policy"] = "workflow_recovery_policy"
    policy_id: str
    source_node_ids: tuple[str, ...] = Field(min_length=1)
    on_outcomes: tuple[str, ...] = Field(min_length=1)
    counter_name: str
    threshold: int = Field(ge=1)
    retry_target_node_id: str | None = None
    exhausted_target_node_id: str | None = None
    exhausted_terminal_state_id: str | None = None
    failure_class_template: str

    @field_validator(
        "policy_id",
        "counter_name",
        "retry_target_node_id",
        "exhausted_target_node_id",
        "exhausted_terminal_state_id",
        "failure_class_template",
    )
    @classmethod
    def validate_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _canonical(value, info)

    @field_validator("source_node_ids", mode="before")
    @classmethod
    def normalize_source_nodes(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(value, field_label="source_node_ids", allow_empty=False)

    @field_validator("on_outcomes", mode="before")
    @classmethod
    def normalize_on_outcomes(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_status_tuple(value, field_label="on_outcomes", allow_empty=False)

    @model_validator(mode="after")
    def validate_exhausted_target(self) -> "WorkflowRecoveryPolicyDefinition":
        target_count = int(self.exhausted_target_node_id is not None) + int(
            self.exhausted_terminal_state_id is not None
        )
        if target_count != 1:
            raise ValueError("recovery policy must declare exactly one exhausted target")
        return self


class RuntimeFailurePolicyRepairClosureMappingDefinition(
    RuntimeEffectRepairClosureContractDefinition
):
    source_operation_id: str

    @field_validator("source_operation_id")
    @classmethod
    def validate_source_operation_id(cls, value: str) -> str:
        return normalize_canonical_id(value, field_label="source_operation_id")


class RuntimeFailurePolicyDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["runtime_failure_policy"] = "runtime_failure_policy"
    policy_id: str
    applies_to_origins: tuple[str, ...] = Field(min_length=1)
    applies_to_planes: tuple[Plane, ...] = Field(min_length=1)
    applies_to_families: tuple[WorkItemFamilyId, ...] = ()
    applies_to_failure_classes: tuple[str, ...] = ()
    applies_to_mutation_phases: tuple[RuntimeEffectMutationPhaseValue, ...] = ()
    applies_to_operation_ids: tuple[str, ...] = ()
    applies_to_handler_ids: tuple[RuntimeEffectHandlerId, ...] = ()
    applies_to_source_node_ids: tuple[str, ...] = ()
    applies_to_source_terminal_state_ids: tuple[str, ...] = ()
    action: Literal[
        "retry_same_node",
        "route_to_recovery_node",
        "block_source_work_item",
        "block_source",
        "route_to_node",
        "retry_source_stage",
        "pause_lane",
        "pause_plane",
        "stop_daemon",
        "create_incident",
        "require_operator",
        "refuse_startup",
    ]
    threshold: int | None = Field(default=None, ge=1)
    counter_name: str | None = None
    failure_class_template: str
    repair_closure_mappings: tuple[RuntimeFailurePolicyRepairClosureMappingDefinition, ...] = ()
    recovery_node_id: str | None = None
    target_node_id: str | None = None
    target_terminal_state_id: str | None = None
    max_attempts: int | None = Field(default=None, ge=1)
    incident_severity: Literal["low", "medium", "high", "critical"] | None = None

    @field_validator(
        "policy_id",
        "counter_name",
        "failure_class_template",
        "recovery_node_id",
        "target_node_id",
        "target_terminal_state_id",
    )
    @classmethod
    def validate_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _canonical(value, info)

    @field_validator(
        "applies_to_origins",
        "applies_to_families",
        "applies_to_failure_classes",
        "applies_to_operation_ids",
        "applies_to_handler_ids",
        "applies_to_source_node_ids",
        "applies_to_source_terminal_state_ids",
        mode="before",
    )
    @classmethod
    def normalize_id_tuples(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label=info.field_name or "id tuple",
            allow_empty=info.field_name != "applies_to_origins",
        )

    @field_validator("applies_to_mutation_phases", mode="before")
    @classmethod
    def normalize_mutation_phases(
        cls,
        value: object,
    ) -> tuple[RuntimeEffectMutationPhaseValue, ...]:
        normalized = _normalize_unique_id_tuple(
            value,
            field_label="applies_to_mutation_phases",
            allow_empty=True,
        )
        allowed = {"pre_mutation", "partial_mutation", "unknown"}
        unknown = sorted(set(normalized) - allowed)
        if unknown:
            raise ValueError(
                "applies_to_mutation_phases contains unknown value: "
                + ", ".join(unknown)
            )
        return cast(tuple[RuntimeEffectMutationPhaseValue, ...], normalized)

    @model_validator(mode="after")
    def validate_action_requirements(self) -> "RuntimeFailurePolicyDefinition":
        if self.action == "route_to_recovery_node" and self.recovery_node_id is None:
            raise ValueError("recovery_node_id is required for action=route_to_recovery_node")
        if self.action == "route_to_node" and self.target_node_id is None:
            raise ValueError("target_node_id is required for action=route_to_node")
        if (self.threshold is None) != (self.counter_name is None):
            raise ValueError("threshold and counter_name must be declared together")
        if self.repair_closure_mappings:
            if self.action != "route_to_node":
                raise ValueError("repair_closure_mappings are only valid for action=route_to_node")
            seen_pairs: set[tuple[str, str]] = set()
            for mapping in self.repair_closure_mappings:
                pair = (mapping.source_operation_id, mapping.failure_class)
                if pair in seen_pairs:
                    raise ValueError(
                        "repair_closure_mappings may not contain duplicate "
                        "source_operation_id + failure_class pairs"
                    )
                seen_pairs.add(pair)
                if self.target_node_id is not None and mapping.target_node_id != self.target_node_id:
                    raise ValueError(
                        "repair_closure_mappings target_node_id must match policy target_node_id"
                    )
            if self.applies_to_failure_classes:
                unknown_failure_classes = {
                    mapping.failure_class
                    for mapping in self.repair_closure_mappings
                } - set(self.applies_to_failure_classes)
                if unknown_failure_classes:
                    raise ValueError(
                        "repair_closure_mappings failure_class values must be included in "
                        "applies_to_failure_classes"
                    )
            if self.applies_to_operation_ids:
                unknown_operation_ids = {
                    mapping.source_operation_id
                    for mapping in self.repair_closure_mappings
                } - set(self.applies_to_operation_ids)
                if unknown_operation_ids:
                    raise ValueError(
                        "repair_closure_mappings source_operation_id values must be included in "
                        "applies_to_operation_ids"
                    )
            if self.applies_to_families:
                unknown_family_ids = {
                    mapping.affected_source_family_id
                    for mapping in self.repair_closure_mappings
                } - set(self.applies_to_families)
                if unknown_family_ids:
                    raise ValueError(
                        "repair_closure_mappings affected_source_family_id values must be included in "
                        "applies_to_families"
                    )
        return self
