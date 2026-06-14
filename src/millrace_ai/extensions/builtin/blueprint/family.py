"""Blueprint draft family adapter."""

from __future__ import annotations

from millrace_ai.architecture.workflow_primitives import (
    builtin_queue_lifecycle_adapter_id_for_family,
)
from millrace_ai.contracts import Plane
from millrace_ai.workspace.families.builtin import BuiltinWorkFamilyQueueAdapter


def blueprint_draft_queue_family_adapter() -> BuiltinWorkFamilyQueueAdapter:
    adapter_id = builtin_queue_lifecycle_adapter_id_for_family(
        "blueprint_draft"
    )
    if adapter_id is None:
        raise RuntimeError("missing built-in queue lifecycle adapter id for blueprint_draft")
    return BuiltinWorkFamilyQueueAdapter(
        adapter_id=adapter_id,
        family_id="blueprint_draft",
        plane=Plane.PLANNING,
        active_relative_dir="blueprints/drafts/active",
        file_extension=".json",
        planning_claim_policy_id="planning.blueprint_draft.adapter",
    )


__all__ = ["blueprint_draft_queue_family_adapter"]
