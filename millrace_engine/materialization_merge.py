"""Pure merge and lookup helpers for materialization orchestration."""

from __future__ import annotations

from typing import Any, TypeVar

from .contracts import (
    ContractModel,
    ControlPlane,
    LoopConfigPayload,
    ModelBinding,
    ModelProfileDefinition,
    PersistedObjectKind,
    RegistryObjectRef,
)
from .materialization_models import (
    ProvenanceEntry,
    ProvenanceLane,
    ProvenanceSource,
    ResolvedRegistryBinding,
    StageInvocationOverride,
)
from .registry import RegistryDocument

ContractModelT = TypeVar("ContractModelT", bound=ContractModel)


def merge_loop_payload(parent: LoopConfigPayload, child: LoopConfigPayload) -> LoopConfigPayload:
    child_updates = child.model_dump(mode="python", exclude_unset=True, exclude_none=True)
    payload = {
        "plane": child.plane,
        "nodes": child.nodes,
        "edges": child.edges,
        "entry_node_id": child.entry_node_id,
        "terminal_states": child.terminal_states,
        "task_authoring_profile_ref": (
            child.task_authoring_profile_ref
            if "task_authoring_profile_ref" in child_updates
            else parent.task_authoring_profile_ref
        ),
        "task_authoring_required": (
            child.task_authoring_required
            if "task_authoring_required" in child_updates
            else parent.task_authoring_required
        ),
        "model_profile_ref": (
            child.model_profile_ref if "model_profile_ref" in child_updates else parent.model_profile_ref
        ),
        "outline_policy": overlay_model(parent.outline_policy, child.outline_policy),
    }
    return LoopConfigPayload.model_validate(payload)


def overlay_model(base: ContractModelT | None, override: ContractModelT | None) -> ContractModelT | None:
    if override is None:
        return base
    if base is None:
        return override
    payload = base.model_dump(mode="python")
    payload.update(override.model_dump(mode="python", exclude_unset=True, exclude_none=True))
    return type(base).model_validate(payload)


def merge_optional_model(
    base: ContractModelT | None,
    override: ContractModelT | None,
    *,
    lane: ProvenanceLane,
    source: ProvenanceSource,
    detail: str,
    object_ref: str | None,
    path: str,
) -> tuple[ContractModelT | None, ProvenanceEntry | None]:
    merged = overlay_model(base, override)
    if merged is None:
        return None, None
    return (
        merged,
        ProvenanceEntry(
            path=path,
            lane=lane,
            source=source,
            detail=detail,
            object_ref=object_ref,
            value=merged.model_dump(mode="json"),
        ),
    )


def stage_override_map(
    overrides: tuple[StageInvocationOverride, ...],
) -> dict[tuple[ControlPlane, str], StageInvocationOverride]:
    return {(override.plane, override.node_id): override for override in overrides}


def binding_scope_for(
    profile: ModelProfileDefinition,
    target_ref: RegistryObjectRef,
) -> ModelBinding | None:
    for scoped_default in profile.payload.scoped_defaults:
        if scoped_default.target_ref == target_ref:
            return scoped_default.binding
    return None


def profile_stage_override_for(profile: ModelProfileDefinition, kind_id: str) -> ModelBinding | None:
    for stage_override in profile.payload.stage_overrides:
        if stage_override.kind_id == kind_id:
            return stage_override.binding
    return None


def registry_binding(ref: RegistryObjectRef, document: RegistryDocument) -> ResolvedRegistryBinding:
    return ResolvedRegistryBinding(
        requested_ref=ref,
        resolved_ref=object_ref(document.definition),
        registry_layer=document.layer,
        source_ref=document.definition.source.ref,
        title=document.definition.title,
    )


def ref_key(ref: RegistryObjectRef) -> tuple[str, str, str]:
    return (ref.kind.value, ref.id, ref.version)


def object_ref(obj: Any) -> RegistryObjectRef:
    return RegistryObjectRef(kind=PersistedObjectKind(obj.kind), id=obj.id, version=obj.version)


def ref_string(ref: RegistryObjectRef | None) -> str | None:
    if ref is None:
        return None
    return f"{ref.kind.value}:{ref.id}@{ref.version}"


__all__ = [
    "binding_scope_for",
    "merge_loop_payload",
    "merge_optional_model",
    "object_ref",
    "overlay_model",
    "profile_stage_override_for",
    "ref_key",
    "ref_string",
    "registry_binding",
    "stage_override_map",
]
