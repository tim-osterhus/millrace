"""Stage-kind and node lookup helpers for compiled graph authority."""

from __future__ import annotations

from millrace_ai.architecture import FrozenGraphPlanePlan, MaterializedGraphNodePlan
from millrace_ai.contracts import StageName, WorkItemKind
from millrace_ai.contracts.work_refs import normalize_work_item_family_id


def entry_key_for_work_item_kind(work_item_kind: WorkItemKind) -> str:
    if work_item_kind is WorkItemKind.TASK:
        return "task"
    if work_item_kind is WorkItemKind.PROBE:
        return "probe"
    if work_item_kind is WorkItemKind.SPEC:
        return "spec"
    if work_item_kind is WorkItemKind.INCIDENT:
        return "incident"
    if work_item_kind is WorkItemKind.LEARNING_REQUEST:
        return "learning_request"
    if work_item_kind is WorkItemKind.BLUEPRINT_DRAFT:
        return "blueprint_draft"
    raise ValueError(f"unsupported work_item_kind: {work_item_kind}")


def entry_key_for_family_id(family_id: str) -> str:
    return normalize_work_item_family_id(family_id, field_name="family_id")


def stage_for_node(graph: FrozenGraphPlanePlan, node_id: str) -> StageName:
    node = node_plan_by_id(graph, node_id)
    runtime_stage = node.runtime_stage
    if runtime_stage is None:
        raise ValueError(f"compiled graph node `{node_id}` is missing runtime_stage")
    return runtime_stage


def node_plan_by_id(graph: FrozenGraphPlanePlan, node_id: str) -> MaterializedGraphNodePlan:
    for node in graph.nodes:
        if node.node_id == node_id:
            return node
    raise ValueError(f"compiled graph is missing node `{node_id}`")


__all__ = [
    "entry_key_for_family_id",
    "entry_key_for_work_item_kind",
    "node_plan_by_id",
    "stage_for_node",
]
