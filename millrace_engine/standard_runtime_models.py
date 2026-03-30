"""Operator-facing view models for the standard runtime selection surface."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from .config import ComplexityRouteSelection
from .contracts import (
    ContractModel,
    ModePolicyToggles,
    OutlinePolicy,
    ReasoningEffort,
    RegistryObjectRef,
    RegistrySourceKind,
    RunnerKind,
    StageType,
)


class RegistryObjectSelectionView(ContractModel):
    """Operator-visible summary for one resolved registry object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: RegistryObjectRef
    title: str
    aliases: tuple[str, ...] = ()
    registry_layer: Literal["packaged", "workspace"] | None = None
    source_kind: RegistrySourceKind | None = None
    source_ref: str | None = None

    @field_validator("title", "source_ref")
    @classmethod
    def validate_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("aliases", mode="before")
    @classmethod
    def normalize_aliases(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            alias = str(item).strip()
            if not alias or alias in seen:
                continue
            seen.add(alias)
            normalized.append(alias)
        return tuple(normalized)


class StageExecutionBindingView(ContractModel):
    """Actual execution parameters and resolved references for one stage node."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    stage: StageType | None = None
    kind_id: str
    stage_kind: RegistryObjectSelectionView
    model_profile: RegistryObjectSelectionView | None = None
    runner: RunnerKind | None = None
    model: str | None = None
    effort: ReasoningEffort | None = None
    allow_search: bool | None = None
    timeout_seconds: int | None = Field(default=None, ge=1)
    prompt_asset_ref: str | None = None
    prompt_resolved_ref: str | None = None
    prompt_source_kind: Literal["workspace", "package"] | None = None

    @field_validator("node_id", "kind_id", "model", "prompt_asset_ref", "prompt_resolved_ref")
    @classmethod
    def validate_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} may not be empty")
        return normalized


class RuntimeSelectionView(ContractModel):
    """Coherent operator-facing view of the standard execution selection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["preview", "frozen_run"]
    selection: RegistryObjectSelectionView
    mode: RegistryObjectSelectionView | None = None
    execution_loop: RegistryObjectSelectionView | None = None
    task_authoring_profile: RegistryObjectSelectionView | None = None
    model_profile: RegistryObjectSelectionView | None = None
    frozen_plan_id: str
    frozen_plan_hash: str
    run_id: str | None = None
    research_participation: str
    outline_policy: OutlinePolicy | None = None
    policy_toggles: ModePolicyToggles | None = None
    complexity: ComplexityRouteSelection | None = None
    stage_bindings: tuple[StageExecutionBindingView, ...] = ()

    @field_validator("frozen_plan_id", "frozen_plan_hash", "run_id", "research_participation")
    @classmethod
    def validate_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_alignment(self) -> "RuntimeSelectionView":
        if self.scope == "frozen_run" and self.run_id is None:
            raise ValueError("frozen_run selection views require run_id")
        if self.scope == "preview" and self.run_id is not None:
            raise ValueError("preview selection views may not carry run_id")
        if self.mode is not None and self.selection.ref != self.mode.ref:
            raise ValueError("selection.ref must match mode.ref when a mode is present")
        if self.mode is None and self.execution_loop is not None and self.selection.ref != self.execution_loop.ref:
            raise ValueError("selection.ref must match execution_loop.ref for direct loop selections")
        return self


__all__ = [
    "RegistryObjectSelectionView",
    "RuntimeSelectionView",
    "StageExecutionBindingView",
]
