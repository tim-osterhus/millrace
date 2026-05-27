"""Stage and entry-coverage validation helpers."""

from __future__ import annotations

from collections.abc import Mapping

from millrace_ai.architecture import (
    ArtifactContractDefinition,
    FrozenGraphPlanePlan,
    GraphLoopEntryKey,
    MaterializedGraphNodePlan,
    RecoveryRole,
    RegisteredStageKindDefinition,
    WorkItemFamilyDefinition,
)
from millrace_ai.architecture.loop_graphs import graph_loop_entry_key_value
from millrace_ai.contracts import Plane
from millrace_ai.contracts.stage_metadata import stage_plane

from ..outcomes import CompilerValidationError


def stage_kinds_by_node_id(
    *,
    graph_nodes_by_id: Mapping[str, MaterializedGraphNodePlan],
    stage_kinds: Mapping[str, RegisteredStageKindDefinition],
) -> dict[str, RegisteredStageKindDefinition]:
    return {
        node.node_id: stage_kinds[node.stage_kind_id]
        for node in graph_nodes_by_id.values()
    }


def validate_stage_artifact_references(
    *,
    artifact_contracts_by_id: dict[str, ArtifactContractDefinition],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
) -> None:
    for stage_kind in stage_kinds.values():
        for artifact_id in stage_kind.allowed_input_artifacts:
            if artifact_id not in artifact_contracts_by_id:
                raise CompilerValidationError(
                    f"stage kind {stage_kind.stage_kind_id} allows unknown input "
                    f"artifact {artifact_id}"
                )
        for artifact_id in stage_kind.declared_output_artifacts:
            if artifact_id not in artifact_contracts_by_id:
                raise CompilerValidationError(
                    f"stage kind {stage_kind.stage_kind_id} declares unknown output "
                    f"artifact {artifact_id}"
                )
            contract = artifact_contracts_by_id[artifact_id]
            if (
                contract.producer_stage_kind_ids
                and stage_kind.stage_kind_id not in contract.producer_stage_kind_ids
            ):
                raise CompilerValidationError(
                    f"stage kind {stage_kind.stage_kind_id} declares output artifact "
                    f"{artifact_id}, but artifact contract {artifact_id} does not list "
                    "that stage kind as a producer"
                )


def validate_entry_coverage(
    *,
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    families_by_id: dict[str, WorkItemFamilyDefinition],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
) -> None:
    families_by_plane_entry = {
        (family.plane, family.entry_key): family
        for family in families_by_id.values()
    }

    for graph in graphs_by_plane.values():
        nodes_by_id = {node.node_id: node for node in graph.nodes}
        for entry in graph.entry_nodes:
            entry_key = graph_loop_entry_key_value(entry.entry_key)
            if entry_key == GraphLoopEntryKey.CLOSURE_TARGET.value:
                continue
            family = families_by_plane_entry.get((graph.plane, entry_key))
            if family is None:
                raise CompilerValidationError(
                    f"graph {graph.loop_id} entry {entry_key} has no matching "
                    f"work item family for plane {graph.plane.value}"
                )
            node = nodes_by_id[entry.node_id]
            stage_kind = stage_kinds[node.stage_kind_id]
            if not _stage_kind_can_start_family(stage_kind, family):
                raise CompilerValidationError(
                    f"entry {entry_key} routes to stage kind {stage_kind.stage_kind_id}, "
                    f"which cannot start family {family.family_id}"
                )


def validate_runtime_failure_recovery(
    *,
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    stage_kinds_by_node_id: dict[str, RegisteredStageKindDefinition],
) -> None:
    for graph in graphs_by_plane.values():
        recovery = graph.runtime_failure_recovery
        if recovery is None:
            continue
        repair_node = next(
            (node for node in graph.nodes if node.node_id == recovery.default_repair_node_id),
            None,
        )
        if repair_node is None:
            raise CompilerValidationError(
                f"graph {graph.loop_id} runtime failure recovery references unknown "
                f"default repair node {recovery.default_repair_node_id}"
            )
        stage_kind = stage_kinds_by_node_id.get(repair_node.node_id)
        if stage_kind is None:
            raise CompilerValidationError(
                f"graph {graph.loop_id} runtime failure recovery node "
                f"{repair_node.node_id} has no registered stage kind"
            )
        if stage_kind.plane is not graph.plane:
            raise CompilerValidationError(
                f"graph {graph.loop_id} runtime failure recovery node "
                f"{repair_node.node_id} belongs to plane {stage_kind.plane.value}, "
                f"not {graph.plane.value}"
            )
        if stage_kind.recovery_role is not RecoveryRole.LOCAL_REPAIR:
            raise CompilerValidationError(
                f"graph {graph.loop_id} runtime failure recovery node "
                f"{repair_node.node_id} must declare recovery_role=local_repair"
            )
        runtime_stage = stage_kind.runtime_stage
        if runtime_stage is None:
            raise CompilerValidationError(
                f"graph {graph.loop_id} runtime failure recovery node "
                f"{repair_node.node_id} uses stage kind {repair_node.stage_kind_id} "
                "without runtime_stage"
            )
        if stage_plane(runtime_stage) is not graph.plane:
            raise CompilerValidationError(
                f"graph {graph.loop_id} runtime failure recovery node "
                f"{repair_node.node_id} maps to runtime stage {runtime_stage.value} "
                f"outside plane {graph.plane.value}"
            )


def _stage_kind_can_start_family(
    stage_kind: RegisteredStageKindDefinition,
    family: WorkItemFamilyDefinition,
) -> bool:
    return family.family_id in stage_kind.allowed_work_item_families


__all__ = [
    "stage_kinds_by_node_id",
    "validate_entry_coverage",
    "validate_runtime_failure_recovery",
    "validate_stage_artifact_references",
]
