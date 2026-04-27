"""Compiled-graph stage-result validation."""

from __future__ import annotations

from millrace_ai.contracts import Plane, RuntimeSnapshot, StageResultEnvelope, WorkItemKind


def validate_stage_result_matches_snapshot(
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    *,
    expected_plane: Plane,
) -> None:
    if snapshot.active_plane is not expected_plane:
        raise ValueError("runtime snapshot active_plane does not match router plane")
    if snapshot.active_stage is None or snapshot.active_stage != stage_result.stage:
        raise ValueError("stage_result stage does not match runtime snapshot active_stage")
    if snapshot.active_node_id is not None and snapshot.active_node_id != stage_result.node_id:
        raise ValueError("stage_result node_id does not match runtime snapshot active_node_id")
    if (
        snapshot.active_stage_kind_id is not None
        and snapshot.active_stage_kind_id != stage_result.stage_kind_id
    ):
        raise ValueError(
            "stage_result stage_kind_id does not match runtime snapshot active_stage_kind_id"
        )
    if snapshot.active_run_id is None or snapshot.active_run_id != stage_result.run_id:
        raise ValueError("stage_result run_id does not match runtime snapshot active_run_id")
    if stage_result.metadata.get("request_kind") == "closure_target":
        if snapshot.active_work_item_kind is not None or snapshot.active_work_item_id is not None:
            raise ValueError("closure_target stage_result cannot use active work item snapshot identity")
        if stage_result.work_item_kind is not WorkItemKind.SPEC:
            raise ValueError("closure_target stage_result must normalize onto a spec identity")
        closure_target_root_spec_id = stage_result.metadata.get("closure_target_root_spec_id")
        if not isinstance(closure_target_root_spec_id, str) or not closure_target_root_spec_id:
            raise ValueError("closure_target stage_result requires closure_target_root_spec_id metadata")
        if closure_target_root_spec_id != stage_result.work_item_id:
            raise ValueError("closure_target_root_spec_id must match stage_result work_item_id")
        return
    if snapshot.active_work_item_kind != stage_result.work_item_kind:
        raise ValueError("stage_result work_item_kind does not match runtime snapshot active item")
    if snapshot.active_work_item_id != stage_result.work_item_id:
        raise ValueError("stage_result work_item_id does not match runtime snapshot active item")


__all__ = ["validate_stage_result_matches_snapshot"]
