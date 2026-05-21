"""Runtime snapshot state contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

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
from .work_refs import coerce_family_and_kind

ActiveRunRequestKind = Literal["active_work_item", "closure_target", "learning_request"]
LaneRuntimeStatus = Literal["idle", "active", "paused", "draining", "stopped", "blocked"]


class LaneRuntimeState(ContractModel):
    """Durable runtime projection for one scheduler lane."""

    lane_id: str
    plane: Plane
    status: LaneRuntimeStatus = "idle"
    compiled_plan_id: str
    compiled_plan_fingerprint: str
    active_run_ids: tuple[str, ...] = ()
    active_work_refs: tuple[str, ...] = ()
    pause_requested: bool = False
    stop_requested: bool = False
    drain_requested: bool = False
    mutation_lock_refs: tuple[str, ...] = ()
    completion_target_refs: tuple[str, ...] = ()
    failure_counter_refs: tuple[str, ...] = ()
    last_claim_attempt_at: datetime | None = None
    last_terminal_outcome: str | None = None

    @field_validator("lane_id", "compiled_plan_id", "compiled_plan_fingerprint")
    @classmethod
    def validate_nonempty_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("lane runtime state identity fields must be non-empty")
        return normalized

    @model_validator(mode="after")
    def validate_lane_runtime_state(self) -> "LaneRuntimeState":
        if not self.lane_id.startswith(f"{self.plane.value}."):
            raise ValueError("lane_id must be namespaced by lane plane")
        if self.status == "active" and not self.active_run_ids:
            raise ValueError("active lane runtime state requires active_run_ids")
        if self.status == "idle" and self.active_run_ids:
            raise ValueError("idle lane runtime state cannot declare active_run_ids")
        return self


class ActiveRunState(ContractModel):
    plane: Plane
    lane_id: str = ""
    stage: StageName
    node_id: str
    stage_kind_id: str
    run_id: str
    compiled_plan_id: str = ""
    compiled_plan_fingerprint: str = ""
    request_kind: ActiveRunRequestKind
    work_item_family_id: str | None = None
    work_item_kind: WorkItemKind | None = None
    work_item_id: str | None = None
    closure_target_root_spec_id: str | None = None
    closure_target_root_idea_id: str | None = None
    active_since: datetime
    running_status_marker: str | None = None

    @model_validator(mode="after")
    def validate_active_run_state(self) -> "ActiveRunState":
        if not self.lane_id:
            self.lane_id = f"{self.plane.value}.main"
        if stage_plane(self.stage) != self.plane:
            raise ValueError("active run stage must belong to active run plane")
        if not self.lane_id.startswith(f"{self.plane.value}."):
            raise ValueError("active run lane_id must be namespaced by active run plane")
        if not self.node_id.strip():
            raise ValueError("active run requires node_id")
        if not self.stage_kind_id.strip():
            raise ValueError("active run requires stage_kind_id")
        if not self.run_id.strip():
            raise ValueError("active run requires run_id")
        if not self.compiled_plan_id.strip():
            raise ValueError("active run requires launch compiled_plan_id")
        if not self.compiled_plan_fingerprint.strip():
            raise ValueError("active run requires launch compiled_plan_fingerprint")

        family_id, work_item_kind = coerce_family_and_kind(
            family_id=self.work_item_family_id,
            work_item_kind=self.work_item_kind,
        )
        self.work_item_family_id = family_id
        self.work_item_kind = work_item_kind

        has_work_family = self.work_item_family_id is not None
        has_work_id = self.work_item_id is not None
        has_closure_root = self.closure_target_root_spec_id is not None
        has_closure_idea = self.closure_target_root_idea_id is not None

        if has_work_family != has_work_id:
            raise ValueError("active run work_item_family_id and work_item_id must be set together")

        if self.request_kind == "active_work_item":
            if not has_work_family or not has_work_id:
                raise ValueError("active_work_item active runs require work item identity")
            if self.work_item_family_id == WorkItemKind.LEARNING_REQUEST.value:
                raise ValueError("learning request active runs must use request_kind=learning_request")
            if has_closure_root or has_closure_idea:
                raise ValueError("active_work_item active runs cannot declare closure target fields")
            return self

        if self.request_kind == "learning_request":
            if self.plane is not Plane.LEARNING:
                raise ValueError("learning_request active runs must use plane=learning")
            if self.work_item_family_id != WorkItemKind.LEARNING_REQUEST.value or not has_work_id:
                raise ValueError("learning_request active runs require learning_request work item identity")
            if has_closure_root or has_closure_idea:
                raise ValueError("learning_request active runs cannot declare closure target fields")
            return self

        if self.plane is not Plane.PLANNING:
            raise ValueError("closure_target active runs must use plane=planning")
        if has_work_family or has_work_id:
            raise ValueError("closure_target active runs cannot declare work item identity")
        if not has_closure_root:
            raise ValueError("closure_target active runs require closure_target_root_spec_id")
        return self


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
    compiled_plan_fingerprint: str = ""
    compiled_plan_path: str
    pending_compiled_plan_id: str | None = None
    pending_compiled_plan_path: str | None = None
    pending_compiled_plan_fingerprint: str | None = None

    active_plane: Plane | None = None
    active_stage: StageName | None = None
    active_node_id: str | None = None
    active_stage_kind_id: str | None = None
    active_run_id: str | None = None
    active_work_item_family_id: str | None = None
    active_work_item_kind: WorkItemKind | None = None
    active_work_item_id: str | None = None
    active_runs_by_plane: dict[Plane, ActiveRunState] = Field(default_factory=dict)
    lanes_by_id: dict[str, LaneRuntimeState] = Field(default_factory=dict)

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

        active_runs = dict(payload.get("active_runs_by_plane") or {})
        if not active_runs and payload.get("active_stage") is not None:
            active_plane = payload.get("active_plane")
            active_stage = payload.get("active_stage")
            active_work_item_kind = payload.get("active_work_item_kind")
            active_work_item_family_id = payload.get("active_work_item_family_id")
            active_work_item_id = payload.get("active_work_item_id")
            active_run_id = payload.get("active_run_id")
            active_since = payload.get("active_since") or payload.get("updated_at")
            if (
                active_plane is not None
                and active_stage is not None
                and active_run_id is not None
                and active_since is not None
                and (active_work_item_family_id is not None or active_work_item_kind is not None)
                and active_work_item_id is not None
            ):
                family_id, legacy_kind = coerce_family_and_kind(
                    family_id=active_work_item_family_id,
                    work_item_kind=active_work_item_kind,
                )
                request_kind: ActiveRunRequestKind = (
                    "learning_request"
                    if family_id == WorkItemKind.LEARNING_REQUEST.value
                    else "active_work_item"
                )
                active_runs[active_plane] = {
                    "plane": active_plane,
                    "lane_id": payload.get("active_lane_id")
                    or f"{Plane(active_plane).value if isinstance(active_plane, str) else active_plane.value}.main",
                    "stage": active_stage,
                    "node_id": payload.get("active_node_id") or active_stage,
                    "stage_kind_id": payload.get("active_stage_kind_id") or active_stage,
                    "run_id": active_run_id,
                    # Older snapshots only had snapshot-level plan authority.
                    # This projection is intentionally confined to legacy active_* state.
                    "compiled_plan_id": payload.get("compiled_plan_id") or "legacy-unknown",
                    "compiled_plan_fingerprint": payload.get("compiled_plan_fingerprint")
                    or payload.get("compiled_plan_id")
                    or "legacy-unknown",
                    "request_kind": request_kind,
                    "work_item_family_id": family_id,
                    "work_item_kind": legacy_kind,
                    "work_item_id": active_work_item_id,
                    "active_since": active_since,
                }
        if active_runs:
            payload["active_runs_by_plane"] = active_runs

        if not payload.get("compiled_plan_fingerprint"):
            payload["compiled_plan_fingerprint"] = payload.get("compiled_plan_id") or "legacy-unknown"

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
        self._project_active_runs_into_legacy_fields()

        for plane, active_run in self.active_runs_by_plane.items():
            if plane is not active_run.plane:
                raise ValueError("active_runs_by_plane key must match active run plane")

        for lane_id, lane_state in self.lanes_by_id.items():
            if lane_id != lane_state.lane_id:
                raise ValueError("lanes_by_id key must match lane runtime state lane_id")

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
            if self.active_work_item_id is None and self.active_work_item_kind is None:
                self.active_work_item_family_id = None

        if self.active_stage is not None and not self.active_node_id:
            raise ValueError("active_stage requires active_node_id")
        if self.active_stage is not None and not self.active_stage_kind_id:
            raise ValueError("active_stage requires active_stage_kind_id")

        family_id, work_item_kind = coerce_family_and_kind(
            family_id=self.active_work_item_family_id,
            work_item_kind=self.active_work_item_kind,
        )
        self.active_work_item_family_id = family_id
        self.active_work_item_kind = work_item_kind

        has_family = self.active_work_item_family_id is not None
        has_id = self.active_work_item_id is not None
        if has_family != has_id:
            raise ValueError(
                "active_work_item_family_id and active_work_item_id must be set together"
            )
        if has_family and self.active_stage is None:
            raise ValueError("active work item requires active_stage")
        if has_family and self.active_plane is None:
            raise ValueError("active work item requires active_plane")
        if has_family and self.active_run_id is None:
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

    def _project_active_runs_into_legacy_fields(self) -> None:
        if not self.active_runs_by_plane:
            return

        active_run = _foreground_active_run(self.active_runs_by_plane)
        self.active_plane = active_run.plane
        self.active_stage = active_run.stage
        self.active_node_id = active_run.node_id
        self.active_stage_kind_id = active_run.stage_kind_id
        self.active_run_id = active_run.run_id
        self.active_work_item_family_id = active_run.work_item_family_id
        self.active_work_item_kind = active_run.work_item_kind
        self.active_work_item_id = active_run.work_item_id
        self.active_since = active_run.active_since


def _foreground_active_run(active_runs_by_plane: dict[Plane, ActiveRunState]) -> ActiveRunState:
    for plane in (Plane.PLANNING, Plane.EXECUTION, Plane.LEARNING):
        active_run = active_runs_by_plane.get(plane)
        if active_run is not None:
            return active_run
    raise ValueError("active_runs_by_plane cannot be empty")


__all__ = [
    "ActiveRunRequestKind",
    "ActiveRunState",
    "LaneRuntimeState",
    "LaneRuntimeStatus",
    "RuntimeSnapshot",
]
