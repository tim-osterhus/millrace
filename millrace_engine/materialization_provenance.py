"""Provenance bookkeeping helpers for materialization results."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from .materialization_merge import ref_string
from .materialization_models import (
    ProvenanceEntry,
    ProvenanceLane,
    ProvenanceSource,
    ResolvedRegistryBinding,
)
from .registry import RegistryLayer


def set_provenance(
    provenance: dict[str, ProvenanceEntry],
    path: str,
    lane: ProvenanceLane,
    source: ProvenanceSource,
    detail: str,
    *,
    object_ref: str | None,
    value: Any,
) -> None:
    provenance[path] = ProvenanceEntry(
        path=path,
        lane=lane,
        source=source,
        detail=detail,
        object_ref=object_ref,
        value=value,
    )


def set_registry_binding_provenance(
    provenance: dict[str, ProvenanceEntry],
    path: str,
    binding: ResolvedRegistryBinding,
) -> None:
    set_provenance(
        provenance,
        path,
        ProvenanceLane.LOOKUP,
        registry_provenance_source(binding.registry_layer),
        f"{binding.registry_layer.value} registry object {binding.title}",
        object_ref=ref_string(binding.resolved_ref),
        value=binding.model_dump(mode="json"),
    )


def registry_provenance_source(layer: RegistryLayer) -> ProvenanceSource:
    if layer is RegistryLayer.WORKSPACE:
        return ProvenanceSource.WORKSPACE_REGISTRY
    return ProvenanceSource.PACKAGED_REGISTRY


def sorted_provenance_map(entries: dict[str, ProvenanceEntry]) -> dict[str, ProvenanceEntry]:
    return OrderedDict((key, entries[key]) for key in sorted(entries))


__all__ = [
    "registry_provenance_source",
    "set_provenance",
    "set_registry_binding_provenance",
    "sorted_provenance_map",
]
