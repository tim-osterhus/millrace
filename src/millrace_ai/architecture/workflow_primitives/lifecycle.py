"""Lifecycle mutation contracts for workflow primitives."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

from millrace_ai.contracts import Plane

from ..common import normalize_canonical_id, normalize_nonempty_text, normalize_status
from ..loop_graphs import GraphLoopEntryKeyValue, normalize_graph_loop_entry_key
from ..stage_kinds import ArchitectureContractModel
from ._validation import _canonical, _normalize_unique_id_tuple
from .identifiers import (
    LifecycleMutationPlanId,
    RuntimeEffectRuleId,
    TerminalActionId,
    TerminalActionRuntimeOperationId,
    WorkItemFamilyId,
)


class TerminalActionDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["terminal_action"] = "terminal_action"
    terminal_action_id: TerminalActionId
    terminal_class: Literal["success", "no_op", "followup_needed", "blocked", "escalate_planning"]
    lifecycle_mutation_plan_id: LifecycleMutationPlanId | None = None
    effect_rule_ids: tuple[RuntimeEffectRuleId, ...] = ()
    status_marker: str | None = None
    operator_summary_template: str | None = None
    runtime_operation_id: TerminalActionRuntimeOperationId | None = None
    non_mutating: bool = False
    router_consequence: Literal["idle", "blocked", "handoff"]
    handoff_plane: Plane | None = None
    handoff_entry_key: GraphLoopEntryKeyValue | None = None
    create_incident: bool = False
    failure_class: str | None = None

    @field_validator("terminal_action_id", "lifecycle_mutation_plan_id", "runtime_operation_id")
    @classmethod
    def validate_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _canonical(value, info)

    @field_validator("effect_rule_ids", mode="before")
    @classmethod
    def normalize_effect_rule_ids(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(value, field_label="effect_rule_ids", allow_empty=True)

    @field_validator("status_marker")
    @classmethod
    def validate_status_marker(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_status(value, field_label="status_marker")

    @field_validator("operator_summary_template")
    @classmethod
    def validate_summary_template(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_nonempty_text(value, field_label="operator_summary_template")

    @field_validator("handoff_entry_key", mode="before")
    @classmethod
    def validate_handoff_entry_key(cls, value: object) -> GraphLoopEntryKeyValue | None:
        if value is None:
            return None
        return normalize_graph_loop_entry_key(value)

    @field_validator("handoff_entry_key", mode="after")
    @classmethod
    def coerce_known_handoff_entry_key(
        cls,
        value: GraphLoopEntryKeyValue | None,
    ) -> GraphLoopEntryKeyValue | None:
        if value is None:
            return None
        return normalize_graph_loop_entry_key(value)

    @field_validator("failure_class")
    @classmethod
    def validate_failure_class(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_canonical_id(value, field_label="failure_class")

    @model_validator(mode="after")
    def validate_terminal_action_shape(self) -> "TerminalActionDefinition":
        if not self.non_mutating and self.lifecycle_mutation_plan_id is None:
            raise ValueError("lifecycle_mutation_plan_id is required for mutating terminal actions")
        if self.router_consequence == "handoff":
            missing = [
                field_name
                for field_name in (
                    "handoff_plane",
                    "handoff_entry_key",
                    "failure_class",
                )
                if getattr(self, field_name) is None
            ]
            if missing:
                raise ValueError(
                    "handoff terminal actions must declare "
                    + ", ".join(missing)
                )
        else:
            if self.handoff_plane is not None or self.handoff_entry_key is not None:
                raise ValueError("non-handoff terminal actions may not declare handoff targets")
            if self.create_incident:
                raise ValueError("create_incident is only valid for handoff terminal actions")
            if self.failure_class is not None:
                raise ValueError("failure_class is only valid for handoff terminal actions")
        return self


class LifecycleMutationPlanDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["lifecycle_mutation_plan"] = "lifecycle_mutation_plan"
    plan_id: LifecycleMutationPlanId
    source_scope: Literal["any", "graph_node", "stage_kind"]
    source_graph_node_id: str | None = None
    source_stage_kind_id: str | None = None
    outcome_scope: Literal["any", "outcome"]
    outcome_id: str | None = None
    source_family_scope: Literal["any", "family"]
    source_family_id: WorkItemFamilyId | None = None
    applicability_contexts: tuple[
        Literal[
            "graph_transition",
            "threshold_exhaustion",
            "runtime_failure_exhaustion",
            "completion_behavior",
        ],
        ...,
    ] = Field(min_length=1)
    owner: Literal["terminal_action", "effect_rule", "recovery_policy", "runtime_failure_policy", "none"]
    source_from_state: str
    source_to_state: str | None = None
    ordering: Literal[
        "after_effect_success",
        "before_route",
        "after_route",
        "on_recovery_exhaustion",
        "none",
    ]
    lifecycle_action_id: str | None = None

    @field_validator(
        "plan_id",
        "source_graph_node_id",
        "source_stage_kind_id",
        "source_family_id",
        "source_from_state",
        "source_to_state",
        "lifecycle_action_id",
    )
    @classmethod
    def validate_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _canonical(value, info)

    @field_validator("outcome_id")
    @classmethod
    def validate_outcome_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_status(value, field_label="outcome_id")

    @model_validator(mode="after")
    def validate_mutation(self) -> "LifecycleMutationPlanDefinition":
        if self.source_scope == "any":
            if self.source_graph_node_id is not None or self.source_stage_kind_id is not None:
                raise ValueError("source_scope=any may not declare source ids")
        elif self.source_scope == "graph_node":
            if self.source_graph_node_id is None:
                raise ValueError("source_graph_node_id is required when source_scope=graph_node")
            if self.source_stage_kind_id is not None:
                raise ValueError("source_scope=graph_node may not declare source_stage_kind_id")
        else:
            if self.source_stage_kind_id is None:
                raise ValueError("source_stage_kind_id is required when source_scope=stage_kind")
            if self.source_graph_node_id is not None:
                raise ValueError("source_scope=stage_kind may not declare source_graph_node_id")

        if self.outcome_scope == "any":
            if self.outcome_id is not None:
                raise ValueError("outcome_scope=any may not declare outcome_id")
        elif self.outcome_id is None:
            raise ValueError("outcome_id is required when outcome_scope=outcome")

        if self.source_family_scope == "any":
            if self.source_family_id is not None:
                raise ValueError("source_family_scope=any may not declare source_family_id")
        elif self.source_family_id is None:
            raise ValueError("source_family_id is required when source_family_scope=family")

        if self.owner == "none":
            if (
                self.source_to_state is not None
                or self.lifecycle_action_id is not None
                or self.ordering != "none"
            ):
                raise ValueError("owner=none may not declare lifecycle mutation fields")
            return self
        if self.ordering == "none":
            raise ValueError("ordering=none is only valid when owner=none")
        if self.source_to_state is not None and self.lifecycle_action_id is None:
            raise ValueError("lifecycle_action_id is required when source_to_state is declared")
        if self.source_to_state is None and self.lifecycle_action_id is not None:
            raise ValueError("source_to_state is required when lifecycle_action_id is declared")
        return self
