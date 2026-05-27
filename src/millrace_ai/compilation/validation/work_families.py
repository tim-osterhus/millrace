"""Work-family and queue-claim validation helpers."""

from __future__ import annotations

from millrace_ai.architecture import (
    FrozenGraphPlanePlan,
    GraphLoopEntryKey,
    PlaneQueueClaimPolicyDefinition,
    WorkItemFamilyDefinition,
)
from millrace_ai.architecture.loop_graphs import graph_loop_entry_key_value
from millrace_ai.assets import WorkflowPrimitiveBundle
from millrace_ai.contracts import ModeDefinition, Plane
from millrace_ai.workspace.family_adapters import (
    queue_adapter_for_id,
    resolve_queue_lifecycle_adapter_id,
)

from ..outcomes import CompilerValidationError


def queue_policies_by_plane(
    workflow_primitives: WorkflowPrimitiveBundle,
) -> dict[Plane, PlaneQueueClaimPolicyDefinition]:
    policies_by_plane: dict[Plane, PlaneQueueClaimPolicyDefinition] = {}
    for policy in workflow_primitives.queue_claim_policies:
        if policy.plane in policies_by_plane:
            raise CompilerValidationError(
                f"Duplicate queue claim policy for plane: {policy.plane.value}"
            )
        policies_by_plane[policy.plane] = policy
    return policies_by_plane


def validate_queue_claim_policies(
    *,
    mode: ModeDefinition,
    families_by_id: dict[str, WorkItemFamilyDefinition],
    queue_policies_by_plane: dict[Plane, PlaneQueueClaimPolicyDefinition],
) -> None:
    for plane in mode.loop_ids_by_plane:
        if plane not in queue_policies_by_plane:
            raise CompilerValidationError(
                f"mode {mode.mode_id} has no queue claim policy for plane {plane.value}"
            )

    for policy in queue_policies_by_plane.values():
        for family_id in getattr(policy, "family_order"):
            family = families_by_id.get(family_id)
            if family is None:
                raise CompilerValidationError(
                    f"queue claim policy {getattr(policy, 'policy_id')} references unknown "
                    f"work item family {family_id}"
                )
            if family.plane is not getattr(policy, "plane"):
                raise CompilerValidationError(
                    f"queue claim policy {getattr(policy, 'policy_id')} includes family "
                    f"{family_id} from plane {family.plane.value}"
                )


def validate_queue_lifecycle_adapters(
    *,
    families_by_id: dict[str, WorkItemFamilyDefinition],
    queue_policies_by_plane: dict[Plane, PlaneQueueClaimPolicyDefinition],
) -> None:
    for policy in queue_policies_by_plane.values():
        for family_id in policy.family_order:
            family = families_by_id[family_id]
            adapter_id = resolve_queue_lifecycle_adapter_id(family)
            if adapter_id is None:
                raise CompilerValidationError(
                    f"queue claim family {family.family_id} in policy {policy.policy_id} "
                    "is missing queue lifecycle adapter id"
                )
            adapter = queue_adapter_for_id(adapter_id)
            if adapter is None:
                raise CompilerValidationError(
                    f"queue claim family {family.family_id} references unknown queue "
                    f"lifecycle adapter {adapter_id}"
                )
            if adapter.family_id != family.family_id:
                raise CompilerValidationError(
                    f"queue claim family {family.family_id} references queue lifecycle "
                    f"adapter {adapter_id} bound to family {adapter.family_id}"
                )


def validate_graph_entries_are_claimable(
    *,
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    families_by_id: dict[str, WorkItemFamilyDefinition],
    queue_policies_by_plane: dict[Plane, PlaneQueueClaimPolicyDefinition],
) -> None:
    families_by_plane_entry = {
        (family.plane, family.entry_key): family
        for family in families_by_id.values()
    }
    for graph in graphs_by_plane.values():
        policy = queue_policies_by_plane.get(graph.plane)
        if policy is None:
            continue
        claimable_families = set(getattr(policy, "family_order"))
        for entry in graph.entry_nodes:
            entry_key = graph_loop_entry_key_value(entry.entry_key)
            if entry_key == GraphLoopEntryKey.CLOSURE_TARGET.value:
                continue
            family = families_by_plane_entry.get((graph.plane, entry_key))
            if family is None:
                continue
            if family.family_id not in claimable_families:
                raise CompilerValidationError(
                    f"graph {graph.loop_id} entry {entry_key} uses family "
                    f"{family.family_id} missing from queue claim policy "
                    f"{getattr(policy, 'policy_id')}"
                )


__all__ = [
    "queue_policies_by_plane",
    "validate_graph_entries_are_claimable",
    "validate_queue_claim_policies",
    "validate_queue_lifecycle_adapters",
]
