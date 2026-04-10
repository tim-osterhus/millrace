"""Public models and enums for Phase 01B materialization boundaries."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator

from .assets.resolver import AssetSourceKind
from .contracts import (
    ContractModel,
    ControlPlane,
    HeadlessPermissionProfile,
    LoopConfigDefinition,
    LoopStageNodeOverrides,
    ModeDefinition,
    ModelProfileDefinition,
    ModePolicyToggles,
    OptionalPermissionProfileModel,
    OutlinePolicy,
    PersistedObjectKind,
    ReasoningEffort,
    RegisteredStageKindDefinition,
    RegistryObjectRef,
    ResearchParticipationMode,
    RunnerKind,
    TaskAuthoringProfileDefinition,
)
from .registry import RegistryLayer


class MaterializationError(RuntimeError):
    """Raised when deterministic object materialization fails."""


class ProvenanceLane(str, Enum):
    LOOKUP = "lookup"
    LOOP = "loop"
    MODE = "mode"
    TASK_AUTHORING = "task_authoring"
    MODEL_PROFILE = "model_profile"
    STAGE_BINDING = "stage_binding"
    ASSET = "asset"


class ProvenanceSource(str, Enum):
    WORKSPACE_REGISTRY = "workspace_registry"
    PACKAGED_REGISTRY = "packaged_registry"
    LOOP_PARENT = "loop_parent"
    LOOP_CHILD = "loop_child"
    MODE = "mode"
    MODEL_PROFILE_DEFAULT = "model_profile_default"
    MODEL_PROFILE_LOOP_SCOPE = "model_profile_loop_scope"
    MODEL_PROFILE_MODE_SCOPE = "model_profile_mode_scope"
    MODEL_PROFILE_STAGE_OVERRIDE = "model_profile_stage_override"
    STAGE_OVERRIDE = "stage_override"
    INVOCATION = "invocation"
    ASSET_WORKSPACE = "asset_workspace"
    ASSET_PACKAGE = "asset_package"


class ResolvedRegistryBinding(ContractModel):
    requested_ref: RegistryObjectRef
    resolved_ref: RegistryObjectRef
    registry_layer: RegistryLayer
    source_ref: str | None = None
    title: str

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("registry binding title may not be empty")
        return normalized


class ProvenanceEntry(ContractModel):
    path: str
    lane: ProvenanceLane
    source: ProvenanceSource
    detail: str
    object_ref: str | None = None
    value: Any = None

    @field_validator("path", "detail")
    @classmethod
    def validate_text(cls, value: str, info: object) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} may not be empty")
        return normalized


class StageInvocationOverride(ContractModel):
    plane: ControlPlane = ControlPlane.EXECUTION
    node_id: str
    overrides: LoopStageNodeOverrides

    @field_validator("node_id")
    @classmethod
    def validate_node_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("stage override node_id may not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_override_fields(self) -> "StageInvocationOverride":
        if not self.overrides.override_fields():
            raise ValueError("stage invocation overrides must declare at least one override field")
        return self


def _validate_unique_stage_overrides(overrides: tuple[StageInvocationOverride, ...]) -> None:
    seen: set[tuple[ControlPlane, str]] = set()
    for override in overrides:
        key = (override.plane, override.node_id)
        if key in seen:
            raise ValueError(
                f"duplicate stage invocation override for {override.plane.value}:{override.node_id}"
            )
        seen.add(key)


class LoopMaterializationOverrides(ContractModel):
    task_authoring_profile_ref: RegistryObjectRef | None = None
    model_profile_ref: RegistryObjectRef | None = None
    outline_policy: OutlinePolicy | None = None
    stage_overrides: tuple[StageInvocationOverride, ...] = ()

    @model_validator(mode="after")
    def validate_refs(self) -> "LoopMaterializationOverrides":
        if (
            self.task_authoring_profile_ref is not None
            and self.task_authoring_profile_ref.kind is not PersistedObjectKind.TASK_AUTHORING_PROFILE
        ):
            raise ValueError("task_authoring_profile_ref overrides must reference task_authoring_profile objects")
        if self.model_profile_ref is not None and self.model_profile_ref.kind is not PersistedObjectKind.MODEL_PROFILE:
            raise ValueError("model_profile_ref overrides must reference model_profile objects")
        _validate_unique_stage_overrides(self.stage_overrides)
        return self


class ModeMaterializationOverrides(LoopMaterializationOverrides):
    execution_loop_ref: RegistryObjectRef | None = None
    research_loop_ref: RegistryObjectRef | None = None
    research_participation: ResearchParticipationMode | None = None
    policy_toggles: ModePolicyToggles | None = None

    @model_validator(mode="after")
    def validate_mode_refs(self) -> "ModeMaterializationOverrides":
        if self.execution_loop_ref is not None and self.execution_loop_ref.kind is not PersistedObjectKind.LOOP_CONFIG:
            raise ValueError("execution_loop_ref overrides must reference loop_config objects")
        if self.research_loop_ref is not None and self.research_loop_ref.kind is not PersistedObjectKind.LOOP_CONFIG:
            raise ValueError("research_loop_ref overrides must reference loop_config objects")
        return self


class MaterializedAssetBinding(ContractModel):
    node_id: str
    requested_ref: str
    resolved_ref: str
    source_kind: AssetSourceKind
    workspace_path: Path
    relative_path: str | None = None
    bundle_version: str | None = None

    @field_validator("node_id", "requested_ref", "resolved_ref")
    @classmethod
    def validate_non_empty_text(cls, value: str, info: object) -> str:
        normalized = value.strip()
        if not normalized:
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} may not be empty")
        return normalized


class MaterializedStageBinding(OptionalPermissionProfileModel):
    plane: ControlPlane
    node_id: str
    kind_id: str
    stage_kind_binding: ResolvedRegistryBinding
    model_profile_ref: RegistryObjectRef | None = None
    model_profile_binding: ResolvedRegistryBinding | None = None
    runner: RunnerKind | None = None
    model: str | None = None
    effort: ReasoningEffort | None = None
    permission_profile: HeadlessPermissionProfile | None = None
    allow_search: bool | None = None
    prompt_asset_ref: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1)
    prompt_asset: MaterializedAssetBinding | None = None

    @field_validator("node_id", "kind_id")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("stage binding ids may not be empty")
        return normalized


class MaterializedLoop(ContractModel):
    requested_ref: RegistryObjectRef | None = None
    loop_binding: ResolvedRegistryBinding | None = None
    parent_binding: ResolvedRegistryBinding | None = None
    materialized_definition: LoopConfigDefinition
    task_authoring_profile_ref: RegistryObjectRef | None = None
    task_authoring_profile_binding: ResolvedRegistryBinding | None = None
    task_authoring_profile: TaskAuthoringProfileDefinition | None = None
    model_profile_ref: RegistryObjectRef | None = None
    model_profile_binding: ResolvedRegistryBinding | None = None
    model_profile: ModelProfileDefinition | None = None
    stage_bindings: tuple[MaterializedStageBinding, ...] = ()
    provenance: dict[str, ProvenanceEntry] = Field(default_factory=dict)


class MaterializedMode(ContractModel):
    requested_ref: RegistryObjectRef | None = None
    mode_binding: ResolvedRegistryBinding | None = None
    mode_definition: ModeDefinition
    execution_loop: MaterializedLoop
    research_loop: MaterializedLoop | None = None
    task_authoring_profile_ref: RegistryObjectRef
    task_authoring_profile_binding: ResolvedRegistryBinding
    task_authoring_profile: TaskAuthoringProfileDefinition
    model_profile_ref: RegistryObjectRef | None = None
    model_profile_binding: ResolvedRegistryBinding | None = None
    model_profile: ModelProfileDefinition | None = None
    research_participation: ResearchParticipationMode
    outline_policy: OutlinePolicy | None = None
    policy_toggles: ModePolicyToggles | None = None
    provenance: dict[str, ProvenanceEntry] = Field(default_factory=dict)


__all__ = [
    "LoopMaterializationOverrides",
    "MaterializationError",
    "MaterializedAssetBinding",
    "MaterializedLoop",
    "MaterializedMode",
    "MaterializedStageBinding",
    "ModeMaterializationOverrides",
    "ProvenanceEntry",
    "ProvenanceLane",
    "ProvenanceSource",
    "ResolvedRegistryBinding",
    "StageInvocationOverride",
]
