"""Deprecated compatibility facade for Blueprint workspace state.

Blueprint state helpers are owned by
``millrace_ai.extensions.builtin.blueprint.state``. This module remains as a
lazy public import shim for existing callers and must not be used by generic
startup paths.
"""

from __future__ import annotations

from typing import Any

_EXPORTED_NAMES = {
    "BlueprintManifestDiagnostic",
    "PacketState",
    "CritiqueState",
    "approve_active_blueprint_draft",
    "block_active_blueprint_draft",
    "blueprint_artifact_ref",
    "blueprint_manifest_path",
    "blueprint_packet_path",
    "cancel_blueprint_draft",
    "claim_next_blueprint_draft",
    "collect_blueprint_manifest_diagnostics",
    "enqueue_blueprint_draft",
    "list_blueprint_manifests",
    "list_blueprint_manifests_for_root",
    "list_open_blueprint_lineage_work_ids",
    "list_open_blueprint_lineage_work_refs",
    "move_candidate_blueprint_packet",
    "persist_blueprint_critique",
    "persist_blueprint_evaluation",
    "persist_blueprint_packet",
    "persist_blueprint_promotion",
    "read_active_blueprint_draft",
    "read_blueprint_draft",
    "read_blueprint_manifest",
    "read_blueprint_packet",
    "requeue_active_blueprint_draft",
    "resolve_blueprint_manifest_path",
    "update_active_blueprint_draft",
    "write_blueprint_manifest",
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTED_NAMES:
        raise AttributeError(name)
    from millrace_ai.extensions.builtin.blueprint import state as impl

    value = getattr(impl, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *_EXPORTED_NAMES))


__all__ = sorted(_EXPORTED_NAMES)
