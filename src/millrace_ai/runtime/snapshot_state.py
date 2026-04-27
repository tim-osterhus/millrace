"""Shared snapshot normalization helpers for runtime idle and stopped states."""

from __future__ import annotations

from datetime import datetime

from millrace_ai.contracts import Plane

IDLE_STATUS_MARKER = "### IDLE"


def idle_snapshot_update(
    *,
    now: datetime,
    process_running: bool,
    queue_depth_execution: int,
    queue_depth_planning: int,
    queue_depth_learning: int,
    clear_stop_requested: bool,
    clear_paused: bool,
) -> dict[str, object]:
    """Build the normalized snapshot update for idle or fully stopped runtime state."""

    update: dict[str, object] = {
        "process_running": process_running,
        "active_plane": None,
        "active_stage": None,
        "active_node_id": None,
        "active_stage_kind_id": None,
        "active_run_id": None,
        "active_work_item_kind": None,
        "active_work_item_id": None,
        "active_since": None,
        "current_failure_class": None,
        "troubleshoot_attempt_count": 0,
        "mechanic_attempt_count": 0,
        "fix_cycle_count": 0,
        "consultant_invocations": 0,
        "execution_status_marker": IDLE_STATUS_MARKER,
        "planning_status_marker": IDLE_STATUS_MARKER,
        "learning_status_marker": IDLE_STATUS_MARKER,
        "queue_depth_execution": queue_depth_execution,
        "queue_depth_planning": queue_depth_planning,
        "queue_depth_learning": queue_depth_learning,
        "queue_depths_by_plane": {
            Plane.EXECUTION: queue_depth_execution,
            Plane.PLANNING: queue_depth_planning,
            Plane.LEARNING: queue_depth_learning,
        },
        "updated_at": now,
    }
    if clear_paused:
        update["paused"] = False
        update["pause_sources"] = ()
    if clear_stop_requested:
        update["stop_requested"] = False
    return update


__all__ = ["IDLE_STATUS_MARKER", "idle_snapshot_update"]
