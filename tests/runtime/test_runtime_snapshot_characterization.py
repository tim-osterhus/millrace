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
from millrace_ai.runtime.snapshot_state import IDLE_STATUS_MARKER
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


# -- status projection characterization ------------------------------------


def test_status_by_scope_derived_from_per_plane_scalars(tmp_path) -> None:
    """status_by_scope plane-scope entries are derived from per-plane scalar fields."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    payload = load_snapshot(paths).model_dump(mode="json")
    # Purge stale surface maps so only per-plane scalars drive the result
    payload.pop("status_markers_by_plane", None)
    payload.pop("status_by_scope", None)
    payload["execution_status_marker"] = "### BUILDER_RUNNING"
    payload["planning_status_marker"] = "### PLANNER_RUNNING"
    payload["learning_status_marker"] = "### ANALYST_RUNNING"

    snapshot = RuntimeSnapshot.model_validate(payload)

    assert snapshot.status_by_scope["execution"] == "### BUILDER_RUNNING"
    assert snapshot.status_by_scope["planning"] == "### PLANNER_RUNNING"
    assert snapshot.status_by_scope["learning"] == "### ANALYST_RUNNING"
    assert snapshot.status_markers_by_plane[Plane.EXECUTION] == "### BUILDER_RUNNING"
    assert snapshot.status_markers_by_plane[Plane.PLANNING] == "### PLANNER_RUNNING"
    assert snapshot.status_markers_by_plane[Plane.LEARNING] == "### ANALYST_RUNNING"


def test_status_markers_by_plane_synced_from_per_plane_scalar_change(tmp_path) -> None:
    """status_markers_by_plane and status_by_scope reflect per-plane scalar
    changes after a model_validate round-trip (which is when validators run)."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    payload = load_snapshot(paths).model_dump(mode="json")
    payload["execution_status_marker"] = "### CHECKER_RUNNING"

    snapshot = RuntimeSnapshot.model_validate(payload)

    assert snapshot.execution_status_marker == "### CHECKER_RUNNING"
    assert snapshot.status_markers_by_plane[Plane.EXECUTION] == "### CHECKER_RUNNING"
    # status_by_scope plane-scope entry is overwritten from the per-plane scalar
    assert snapshot.status_by_scope["execution"] == "### CHECKER_RUNNING"


def test_status_by_scope_preserves_extra_scope_entries(tmp_path) -> None:
    """Non-plane scope keys in status_by_scope survive validation unchanged."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    payload = load_snapshot(paths).model_dump(mode="json")
    # Set both the per-plane scalar and status_by_scope consistently
    payload["execution_status_marker"] = "### BUILDER_RUNNING"
    payload["planning_status_marker"] = "### IDLE"
    payload["learning_status_marker"] = "### IDLE"
    payload["status_by_scope"] = {
        "execution": "### BUILDER_RUNNING",
        "planning": "### IDLE",
        "learning": "### IDLE",
        "execution.main": "### BUILDER_RUNNING",
    }

    snapshot = RuntimeSnapshot.model_validate(payload)

    # Non-plane scope entry is preserved
    assert snapshot.status_by_scope["execution.main"] == "### BUILDER_RUNNING"
    # Plane-scope entry is derived from per-plane scalar
    assert snapshot.status_by_scope["execution"] == "### BUILDER_RUNNING"
    assert snapshot.status_markers_by_plane[Plane.EXECUTION] == "### BUILDER_RUNNING"


def test_status_projection_roundtrip_preserves_both_surfaces(tmp_path) -> None:
    """Both status_by_scope and status_markers_by_plane survive dump-validate cycles."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    payload = load_snapshot(paths).model_dump(mode="json")
    payload["execution_status_marker"] = "### BUILDER_RUNNING"
    payload["status_by_scope"] = {
        "execution": "### BUILDER_RUNNING",
        "planning": "### IDLE",
        "learning": "### IDLE",
    }

    first = RuntimeSnapshot.model_validate(payload)
    second = RuntimeSnapshot.model_validate(first.model_dump(mode="json"))

    assert second.status_by_scope == first.status_by_scope
    assert second.status_markers_by_plane == first.status_markers_by_plane
    assert second.execution_status_marker == first.execution_status_marker
    assert second.planning_status_marker == first.planning_status_marker
    assert second.learning_status_marker == first.learning_status_marker


def test_legacy_plane_marker_fields_preserved_through_validate_cycle(
    tmp_path,
) -> None:
    """Legacy per-plane scalar fields stay consistent through validate cycles.

    Simulates the engine's two-step update pattern: first set the per-plane
    scalar, then update both surface maps.  After a model_validate round-trip
    all three surfaces agree.
    """
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    payload = load_snapshot(paths).model_dump(mode="json")

    # Simulate engine step 1 + 2 in a single payload, then validate
    payload["execution_status_marker"] = "### BUILDER_RUNNING"
    payload["status_markers_by_plane"] = {
        "execution": "### BUILDER_RUNNING",
        "planning": IDLE_STATUS_MARKER,
        "learning": IDLE_STATUS_MARKER,
    }
    payload["status_by_scope"] = {
        "execution": "### BUILDER_RUNNING",
        "planning": IDLE_STATUS_MARKER,
        "learning": IDLE_STATUS_MARKER,
    }

    snapshot = RuntimeSnapshot.model_validate(payload)

    assert snapshot.execution_status_marker == "### BUILDER_RUNNING"
    assert snapshot.status_markers_by_plane[Plane.EXECUTION] == "### BUILDER_RUNNING"
    assert snapshot.status_by_scope["execution"] == "### BUILDER_RUNNING"
    assert snapshot.planning_status_marker == IDLE_STATUS_MARKER
    assert snapshot.learning_status_marker == IDLE_STATUS_MARKER


def test_status_by_scope_canonical_fills_status_markers_by_plane_gaps(
    tmp_path,
) -> None:
    """Per-plane scalars always win over status_by_scope for plane-scope entries.

    Canonical status_by_scope can carry extra scope entries (e.g. lane-level)
    that survive validation.  Plane-scope entries are overwritten from per-plane
    scalars so the engine's two-step model_copy flow stays correct.
    """
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    payload = load_snapshot(paths).model_dump(mode="json")
    # Populate only status_by_scope; leave per-plane scalars at defaults.
    payload["status_by_scope"] = {
        "execution": "### BUILDER_RUNNING",
        "planning": "### PLANNER_RUNNING",
        "learning": "### IDLE",
    }
    # Clear plane-keyed map so it must be rebuilt.
    payload["status_markers_by_plane"] = {}

    snapshot = RuntimeSnapshot.model_validate(payload)

    # Per-plane scalars are the authoritative legacy signal; they were
    # IDLE_STATUS_MARKER (default).  Plane-scope status_by_scope entries
    # are overwritten from per-plane scalars.
    assert snapshot.execution_status_marker == IDLE_STATUS_MARKER
    assert snapshot.status_markers_by_plane[Plane.EXECUTION] == IDLE_STATUS_MARKER
    assert snapshot.status_by_scope["execution"] == IDLE_STATUS_MARKER
    assert snapshot.status_by_scope["planning"] == IDLE_STATUS_MARKER
    assert snapshot.status_by_scope["learning"] == IDLE_STATUS_MARKER
