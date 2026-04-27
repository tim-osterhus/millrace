"""Runtime snapshot state contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel
from .enums import (
    Plane,
    ReloadOutcome,
    RuntimeMode,
    StageName,
    TerminalResult,
    WatcherMode,
    WorkItemKind,
)
from .stage_metadata import stage_plane


class RuntimeSnapshot(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["runtime_snapshot"] = "runtime_snapshot"

    runtime_mode: RuntimeMode
    process_running: bool
    paused: bool
    pause_sources: tuple[Literal["operator", "usage_governance"], ...] = ()
    stop_requested: bool = False
    active_mode_id: str
    execution_loop_id: str
    planning_loop_id: str
    learning_loop_id: str | None = None
    loop_ids_by_plane: dict[Plane, str] = Field(default_factory=dict)
    compiled_plan_id: str
    compiled_plan_path: str

    active_plane: Plane | None = None
    active_stage: StageName | None = None
    active_node_id: str | None = None
    active_stage_kind_id: str | None = None
    active_run_id: str | None = None
    active_work_item_kind: WorkItemKind | None = None
    active_work_item_id: str | None = None

    execution_status_marker: str
    planning_status_marker: str
    learning_status_marker: str = "### IDLE"
    status_markers_by_plane: dict[Plane, str] = Field(default_factory=dict)

    queue_depth_execution: int = 0
    queue_depth_planning: int = 0
    queue_depth_learning: int = 0
    queue_depths_by_plane: dict[Plane, int] = Field(default_factory=dict)

    last_terminal_result: TerminalResult | None = None
    last_stage_result_path: str | None = None

    current_failure_class: str | None = None
    troubleshoot_attempt_count: int = 0
    mechanic_attempt_count: int = 0
    fix_cycle_count: int = 0
    consultant_invocations: int = 0

    config_version: str
    watcher_mode: WatcherMode
    last_reload_outcome: ReloadOutcome | None = None
    last_reload_error: str | None = None

    started_at: datetime | None = None
    active_since: datetime | None = None
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def normalize_plane_indexed_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value

        payload = dict(value)
        pause_sources = tuple(dict.fromkeys(payload.get("pause_sources") or ()))
        if payload.get("paused") and not pause_sources:
            pause_sources = ("operator",)
        if pause_sources:
            payload["pause_sources"] = pause_sources
            payload["paused"] = True
        else:
            payload["pause_sources"] = ()

        loop_ids = dict(payload.get("loop_ids_by_plane") or {})
        if "execution_loop_id" in payload:
            loop_ids.setdefault(Plane.EXECUTION.value, payload["execution_loop_id"])
        if "planning_loop_id" in payload:
            loop_ids.setdefault(Plane.PLANNING.value, payload["planning_loop_id"])
        if payload.get("learning_loop_id") is not None:
            loop_ids.setdefault(Plane.LEARNING.value, payload["learning_loop_id"])
        if loop_ids:
            payload["loop_ids_by_plane"] = loop_ids

        status_markers = dict(payload.get("status_markers_by_plane") or {})
        if "execution_status_marker" in payload:
            status_markers.setdefault(Plane.EXECUTION.value, payload["execution_status_marker"])
        if "planning_status_marker" in payload:
            status_markers.setdefault(Plane.PLANNING.value, payload["planning_status_marker"])
        if "learning_status_marker" in payload:
            status_markers.setdefault(Plane.LEARNING.value, payload["learning_status_marker"])
        if status_markers:
            payload["status_markers_by_plane"] = status_markers

        queue_depths = dict(payload.get("queue_depths_by_plane") or {})
        if "queue_depth_execution" in payload:
            queue_depths.setdefault(Plane.EXECUTION.value, payload["queue_depth_execution"])
        if "queue_depth_planning" in payload:
            queue_depths.setdefault(Plane.PLANNING.value, payload["queue_depth_planning"])
        if "queue_depth_learning" in payload:
            queue_depths.setdefault(Plane.LEARNING.value, payload["queue_depth_learning"])
        if queue_depths:
            payload["queue_depths_by_plane"] = queue_depths

        if payload.get("active_stage") is None:
            payload["active_node_id"] = None
            payload["active_stage_kind_id"] = None
        else:
            active_stage = payload["active_stage"]
            payload.setdefault("active_node_id", active_stage)
            payload.setdefault("active_stage_kind_id", active_stage)
        return payload

    @model_validator(mode="after")
    def validate_active_state(self) -> "RuntimeSnapshot":
        if self.active_stage is None and self.active_plane is not None:
            raise ValueError("active_plane cannot be set when active_stage is missing")

        if self.active_stage is not None:
            if self.active_plane is None:
                raise ValueError("active_plane is required when active_stage is set")
            if stage_plane(self.active_stage) != self.active_plane:
                raise ValueError("active_stage must belong to active_plane")
            if self.active_node_id is None:
                self.active_node_id = self.active_stage.value
            if self.active_stage_kind_id is None:
                self.active_stage_kind_id = self.active_stage.value
        else:
            self.active_node_id = None
            self.active_stage_kind_id = None

        if self.active_stage is not None and not self.active_node_id:
            raise ValueError("active_stage requires active_node_id")
        if self.active_stage is not None and not self.active_stage_kind_id:
            raise ValueError("active_stage requires active_stage_kind_id")

        has_kind = self.active_work_item_kind is not None
        has_id = self.active_work_item_id is not None
        if has_kind != has_id:
            raise ValueError(
                "active_work_item_kind and active_work_item_id must be set together"
            )
        if has_kind and self.active_stage is None:
            raise ValueError("active work item requires active_stage")
        if has_kind and self.active_plane is None:
            raise ValueError("active work item requires active_plane")
        if has_kind and self.active_run_id is None:
            raise ValueError("active work item requires active_run_id")

        if self.active_since is not None and self.active_stage is None:
            raise ValueError("active_since requires active_stage")

        if (
            self.queue_depth_execution < 0
            or self.queue_depth_planning < 0
            or self.queue_depth_learning < 0
        ):
            raise ValueError("queue depth values must be >= 0")
        if any(depth < 0 for depth in self.queue_depths_by_plane.values()):
            raise ValueError("plane-indexed queue depth values must be >= 0")

        if self.pause_sources:
            self.paused = True
        elif not self.paused:
            self.pause_sources = ()
        elif self.paused and not self.pause_sources:
            self.pause_sources = ("operator",)

        return self


__all__ = ["RuntimeSnapshot"]
