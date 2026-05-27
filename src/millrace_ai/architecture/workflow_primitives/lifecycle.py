"""Lifecycle mutation contracts for workflow primitives."""

from __future__ import annotations

from typing import Literal

from pydantic import ValidationInfo, field_validator, model_validator

from ..common import normalize_nonempty_text, normalize_status
from ..stage_kinds import ArchitectureContractModel
from ._validation import _canonical, _normalize_unique_id_tuple
from .identifiers import (
    LifecycleMutationPlanId,
    RuntimeEffectRuleId,
    TerminalActionId,
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
    non_mutating: bool = False

    @field_validator("terminal_action_id", "lifecycle_mutation_plan_id")
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

    @model_validator(mode="after")
    def validate_lifecycle_plan(self) -> "TerminalActionDefinition":
        if not self.non_mutating and self.lifecycle_mutation_plan_id is None:
            raise ValueError("lifecycle_mutation_plan_id is required for mutating terminal actions")
        return self


class LifecycleMutationPlanDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["lifecycle_mutation_plan"] = "lifecycle_mutation_plan"
    plan_id: LifecycleMutationPlanId
    source_node_id: str
    outcome_id: str
    source_family_id: WorkItemFamilyId
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
        "source_node_id",
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
    def validate_outcome_id(cls, value: str) -> str:
        return normalize_status(value, field_label="outcome_id")

    @model_validator(mode="after")
    def validate_mutation(self) -> "LifecycleMutationPlanDefinition":
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
