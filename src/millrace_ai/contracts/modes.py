"""Mode selection and learning-trigger contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from .base import ContractModel
from .enums import LearningRequestAction, LearningStageName, Plane, StageName
from .stage_metadata import legal_terminal_results, stage_plane, validate_safe_identifier


class LearningTriggerRuleDefinition(ContractModel):
    rule_id: str
    source_plane: Plane
    source_stage: StageName
    on_terminal_results: tuple[str, ...] = Field(min_length=1)
    target_stage: LearningStageName
    requested_action: LearningRequestAction = LearningRequestAction.IMPROVE

    @field_validator("on_terminal_results", mode="before")
    @classmethod
    def normalize_terminal_results(cls, value: tuple[str, ...] | list[str] | str) -> tuple[str, ...]:
        if isinstance(value, str):
            raw_values = [value]
        else:
            raw_values = list(value)
        normalized = tuple(str(item).strip() for item in raw_values if str(item).strip())
        if not normalized:
            raise ValueError("on_terminal_results must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_rule_shape(self) -> "LearningTriggerRuleDefinition":
        validate_safe_identifier(self.rule_id, field_name="rule_id")
        if stage_plane(self.source_stage) is not self.source_plane:
            raise ValueError("source_stage must belong to source_plane")
        if self.source_plane is Plane.LEARNING:
            raise ValueError("learning triggers must originate outside the learning plane")
        legal = legal_terminal_results(self.source_stage)
        unknown = tuple(result for result in self.on_terminal_results if result not in legal)
        if unknown:
            raise ValueError(
                "on_terminal_results contains values illegal for source_stage: "
                + ", ".join(unknown)
            )
        return self


class PlaneConcurrencyPolicyDefinition(ContractModel):
    mutually_exclusive_planes: tuple[tuple[Plane, ...], ...] = ()
    may_run_concurrently: tuple[tuple[Plane, ...], ...] = ()


class ModeDefinition(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["mode"] = "mode"

    mode_id: str
    loop_ids_by_plane: dict[Plane, str]

    stage_entrypoint_overrides: dict[StageName, str] = Field(default_factory=dict)
    stage_skill_additions: dict[StageName, tuple[str, ...]] = Field(default_factory=dict)
    stage_model_bindings: dict[StageName, str] = Field(default_factory=dict)
    stage_runner_bindings: dict[StageName, str] = Field(default_factory=dict)
    concurrency_policy: PlaneConcurrencyPolicyDefinition | None = None
    learning_trigger_rules: tuple[LearningTriggerRuleDefinition, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_loop_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value

        payload = dict(value)
        loop_ids = dict(payload.get("loop_ids_by_plane") or {})
        legacy_execution = payload.pop("execution_loop_id", None)
        legacy_planning = payload.pop("planning_loop_id", None)

        if legacy_execution is not None:
            loop_ids[Plane.EXECUTION.value] = legacy_execution
        if legacy_planning is not None:
            loop_ids[Plane.PLANNING.value] = legacy_planning
        if loop_ids:
            payload["loop_ids_by_plane"] = loop_ids
        return payload

    @model_validator(mode="after")
    def validate_loop_bindings(self) -> "ModeDefinition":
        if Plane.EXECUTION not in self.loop_ids_by_plane:
            raise ValueError("loop_ids_by_plane must include execution")
        if Plane.PLANNING not in self.loop_ids_by_plane:
            raise ValueError("loop_ids_by_plane must include planning")
        for plane, loop_id in self.loop_ids_by_plane.items():
            expected_prefix = f"{plane.value}."
            if not loop_id.startswith(expected_prefix):
                raise ValueError(
                    f"loop id for plane {plane.value} must start with {expected_prefix!r}"
                )
        if self.learning_trigger_rules and not self.learning_enabled:
            raise ValueError("learning_trigger_rules require a learning loop binding")
        return self

    @property
    def execution_loop_id(self) -> str:
        return self.loop_ids_by_plane[Plane.EXECUTION]

    @property
    def planning_loop_id(self) -> str:
        return self.loop_ids_by_plane[Plane.PLANNING]

    @property
    def learning_loop_id(self) -> str | None:
        return self.loop_ids_by_plane.get(Plane.LEARNING)

    @property
    def learning_enabled(self) -> bool:
        return Plane.LEARNING in self.loop_ids_by_plane


__all__ = [
    "LearningTriggerRuleDefinition",
    "ModeDefinition",
    "PlaneConcurrencyPolicyDefinition",
]
