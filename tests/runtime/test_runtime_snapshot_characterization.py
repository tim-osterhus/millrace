from __future__ import annotations

import json
from datetime import datetime, timezone

from millrace_ai.contracts import (
    ActiveRunState,
    ExecutionStageName,
    Plane,
    RuntimeSnapshot,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.state_store import load_snapshot

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_runtime_snapshot_loads_plane_indexed_active_run_and_projects_legacy_fields(
    tmp_path,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    active_run = ActiveRunState(
        plane="execution",
        lane_id="execution.main",
        stage="builder",
        node_id="custom-builder-node",
        stage_kind_id="builder_custom",
        run_id="run-001",
        compiled_plan_id="plan-001",
        compiled_plan_fingerprint="fingerprint-001",
        request_kind="active_work_item",
        work_item_kind="task",
        work_item_id="task-001",
        active_since=NOW,
        running_status_marker="BUILDER_CUSTOM_RUNNING",
    )
    payload = load_snapshot(paths).model_dump(mode="json")
    payload.update(
        {
            "active_runs_by_plane": {"execution": active_run.model_dump(mode="json")},
            "active_plane": None,
            "active_stage": None,
            "active_node_id": None,
            "active_stage_kind_id": None,
            "active_run_id": None,
            "active_work_item_family_id": None,
            "active_work_item_kind": None,
            "active_work_item_id": None,
            "active_since": None,
        }
    )
    paths.runtime_snapshot_file.write_text(json.dumps(payload), encoding="utf-8")

    snapshot = load_snapshot(paths)

    assert snapshot.active_runs_by_plane[Plane.EXECUTION].stage is ExecutionStageName.BUILDER
    assert snapshot.active_runs_by_plane[Plane.EXECUTION].stage_kind_id == "builder_custom"
    assert snapshot.active_runs_by_plane[Plane.EXECUTION].running_status_marker == (
        "BUILDER_CUSTOM_RUNNING"
    )
    assert snapshot.active_plane is Plane.EXECUTION
    assert snapshot.active_node_id == "custom-builder-node"
    assert snapshot.active_stage_kind_id == "builder_custom"
    assert snapshot.active_work_item_family_id == "task"
    assert snapshot.active_work_item_kind is WorkItemKind.TASK


def test_runtime_snapshot_backfills_active_run_from_legacy_active_fields(tmp_path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    payload = load_snapshot(paths).model_dump(mode="json")
    payload.update(
        {
            "compiled_plan_id": "plan-legacy",
            "compiled_plan_fingerprint": "",
            "active_plane": "execution",
            "active_stage": "builder",
            "active_node_id": "builder",
            "active_stage_kind_id": "builder",
            "active_run_id": "run-legacy",
            "active_work_item_kind": "task",
            "active_work_item_id": "task-legacy",
            "active_since": NOW.isoformat(),
            "active_runs_by_plane": {},
        }
    )
    snapshot = RuntimeSnapshot.model_validate(payload)

    active_run = snapshot.active_runs_by_plane[Plane.EXECUTION]
    assert active_run.request_kind == "active_work_item"
    assert active_run.compiled_plan_id == "plan-legacy"
    assert active_run.compiled_plan_fingerprint == "plan-legacy"
    assert active_run.work_item_family_id == "task"
    assert active_run.work_item_kind is WorkItemKind.TASK


def test_runtime_snapshot_backfills_plan_identity_for_legacy_plane_indexed_active_run(
    tmp_path,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    payload = load_snapshot(paths).model_dump(mode="json")
    payload.update(
        {
            "compiled_plan_id": "plan-legacy-active-map",
            "compiled_plan_fingerprint": "",
            "active_runs_by_plane": {
                "execution": {
                    "plane": "execution",
                    "stage": "builder",
                    "node_id": "builder",
                    "stage_kind_id": "builder",
                    "run_id": "run-legacy-active-map",
                    "request_kind": "active_work_item",
                    "work_item_kind": "task",
                    "work_item_id": "task-legacy-active-map",
                    "active_since": NOW.isoformat(),
                }
            },
        }
    )

    snapshot = RuntimeSnapshot.model_validate(payload)

    active_run = snapshot.active_runs_by_plane[Plane.EXECUTION]
    assert active_run.compiled_plan_id == "plan-legacy-active-map"
    assert active_run.compiled_plan_fingerprint == "plan-legacy-active-map"
