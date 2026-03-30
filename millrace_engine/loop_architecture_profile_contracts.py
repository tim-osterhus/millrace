"""Mode and profile contract families for loop architecture."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from .contracts import ContractModel, ReasoningEffort, RunnerKind
from .loop_architecture_common import (
    AcceptanceProfile,
    GateStrictness,
    PersistedObjectEnvelope,
    PersistedObjectKind,
    RegistryObjectRef,
    ResearchAssumption,
    ResearchParticipationMode,
    TaskBreadth,
    TaskDecompositionStyle,
    _dedupe,
    _normalize_canonical_id,
    _normalize_text,
)
from .loop_architecture_loop_contracts import OutlinePolicy


class ModePolicyToggles(ContractModel):
    allow_execution_search: bool | None = None
    allow_research_search: bool | None = None
    run_update_on_empty: bool | None = None
    integration_mode: Literal["always", "large_only", "never"] | None = None


class ModeCompositionRules(ContractModel):
    allow_ephemeral_execution_loop_override: bool = False
    allow_ephemeral_research_loop_override: bool = False


class ModePayload(ContractModel):
    execution_loop_ref: RegistryObjectRef
    research_loop_ref: RegistryObjectRef | None = None
    task_authoring_profile_ref: RegistryObjectRef
    model_profile_ref: RegistryObjectRef | None = None
    research_participation: ResearchParticipationMode = ResearchParticipationMode.NONE
    policy_toggles: ModePolicyToggles | None = None
    outline_policy: OutlinePolicy | None = None
    composition_rules: ModeCompositionRules | None = None

    @model_validator(mode="after")
    def validate_refs(self) -> "ModePayload":
        if self.execution_loop_ref.kind is not PersistedObjectKind.LOOP_CONFIG:
            raise ValueError("execution_loop_ref must reference a loop_config object")
        if self.research_loop_ref and self.research_loop_ref.kind is not PersistedObjectKind.LOOP_CONFIG:
            raise ValueError("research_loop_ref must reference a loop_config object")
        if self.task_authoring_profile_ref.kind is not PersistedObjectKind.TASK_AUTHORING_PROFILE:
            raise ValueError("task_authoring_profile_ref must reference a task_authoring_profile object")
        if self.model_profile_ref and self.model_profile_ref.kind is not PersistedObjectKind.MODEL_PROFILE:
            raise ValueError("model_profile_ref must reference a model_profile object")
        if (
            self.research_participation in {
                ResearchParticipationMode.SELECTED_RESEARCH_STAGES,
                ResearchParticipationMode.FULL_RESEARCH_HANDOFF,
            }
            and self.research_loop_ref is None
        ):
            raise ValueError(
                "research participation modes selected_research_stages and full_research_handoff require research_loop_ref"
            )
        return self


class CardCountRange(ContractModel):
    min_cards: int = Field(ge=1)
    max_cards: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> "CardCountRange":
        if self.max_cards < self.min_cards:
            raise ValueError("max_cards may not be lower than min_cards")
        return self


class TaskAuthoringProfilePayload(ContractModel):
    decomposition_style: TaskDecompositionStyle
    expected_card_count: CardCountRange
    allowed_task_breadth: TaskBreadth
    required_metadata_fields: tuple[str, ...] = ()
    acceptance_profile: AcceptanceProfile = AcceptanceProfile.STANDARD
    gate_strictness: GateStrictness = GateStrictness.STANDARD
    single_card_synthesis_allowed: bool = False
    research_assumption: ResearchAssumption = ResearchAssumption.CONSULT_IF_AMBIGUOUS
    suitable_use_cases: tuple[str, ...]

    @field_validator("required_metadata_fields", mode="before")
    @classmethod
    def normalize_required_metadata_fields(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if not value:
            return ()
        fields = [_normalize_canonical_id(str(item), field_label="required metadata field") for item in value]
        return _dedupe(fields, field_label="")

    @field_validator("suitable_use_cases", mode="before")
    @classmethod
    def normalize_use_cases(
        cls,
        value: tuple[str, ...] | list[str],
    ) -> tuple[str, ...]:
        use_cases = [_normalize_text(str(item), field_label="task authoring use case") for item in value]
        return _dedupe(use_cases, field_label="suitable_use_cases")


class ModelBinding(ContractModel):
    runner: RunnerKind
    model: str
    effort: ReasoningEffort | None = None
    allow_search: bool = False

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        return _normalize_text(value, field_label="model binding model")


class ScopedModelBinding(ContractModel):
    target_ref: RegistryObjectRef
    binding: ModelBinding

    @model_validator(mode="after")
    def validate_target(self) -> "ScopedModelBinding":
        if self.target_ref.kind not in {PersistedObjectKind.LOOP_CONFIG, PersistedObjectKind.MODE}:
            raise ValueError("scoped model bindings may target only loop_config or mode objects")
        return self


class StageKindModelBinding(ContractModel):
    kind_id: str
    binding: ModelBinding

    @field_validator("kind_id")
    @classmethod
    def validate_kind_id(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="stage override kind id")


class ModelProfilePayload(ContractModel):
    default_binding: ModelBinding
    scoped_defaults: tuple[ScopedModelBinding, ...] = ()
    stage_overrides: tuple[StageKindModelBinding, ...] = ()

    @model_validator(mode="after")
    def validate_uniqueness(self) -> "ModelProfilePayload":
        scoped_keys = [
            (item.target_ref.kind.value, item.target_ref.id, item.target_ref.version)
            for item in self.scoped_defaults
        ]
        if len(set(scoped_keys)) != len(scoped_keys):
            raise ValueError("model profile scoped_defaults may not target the same object more than once")
        stage_keys = [item.kind_id for item in self.stage_overrides]
        if len(set(stage_keys)) != len(stage_keys):
            raise ValueError("model profile stage_overrides may not target the same stage kind more than once")
        return self


class ModeDefinition(PersistedObjectEnvelope):
    kind: Literal["mode"] = "mode"
    payload: ModePayload

    @model_validator(mode="after")
    def validate_mode_definition(self) -> "ModeDefinition":
        if self.extends is not None:
            raise ValueError("modes do not support extends")
        return self


class TaskAuthoringProfileDefinition(PersistedObjectEnvelope):
    kind: Literal["task_authoring_profile"] = "task_authoring_profile"
    payload: TaskAuthoringProfilePayload

    @model_validator(mode="after")
    def validate_task_profile_definition(self) -> "TaskAuthoringProfileDefinition":
        if self.extends is not None:
            raise ValueError("task authoring profiles do not support extends")
        return self


class ModelProfileDefinition(PersistedObjectEnvelope):
    kind: Literal["model_profile"] = "model_profile"
    payload: ModelProfilePayload

    @model_validator(mode="after")
    def validate_model_profile_definition(self) -> "ModelProfileDefinition":
        if self.extends is not None:
            raise ValueError("model profiles do not support extends")
        return self


__all__ = [
    "CardCountRange",
    "ModeCompositionRules",
    "ModeDefinition",
    "ModePayload",
    "ModePolicyToggles",
    "ModelBinding",
    "ModelProfileDefinition",
    "ModelProfilePayload",
    "ScopedModelBinding",
    "StageKindModelBinding",
    "TaskAuthoringProfileDefinition",
    "TaskAuthoringProfilePayload",
]
