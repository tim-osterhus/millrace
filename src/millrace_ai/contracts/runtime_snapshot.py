"""Runtime snapshot state contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_serializer, field_validator, model_validator

from .base import ContractModel
from .enums import (
    Plane,
    ReloadOutcome,
    RuntimeMode,
    StageName,
    WatcherMode,
    WorkItemKind,
)
from .stage_metadata import stage_plane
from .terminal_outcomes import TerminalOutcome, terminal_outcome_value
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
    active_runs_by_lane: dict[str, ActiveRunState] = Field(default_factory=dict)
    lanes_by_id: dict[str, LaneRuntimeState] = Field(default_factory=dict)

    execution_status_marker: str
    planning_status_marker: str
    learning_status_marker: str = "### IDLE"
    status_markers_by_plane: dict[Plane, str] = Field(default_factory=dict)
    status_by_scope: dict[str, str] = Field(default_factory=dict)

    queue_depth_execution: int = 0
    queue_depth_planning: int = 0
    queue_depth_learning: int = 0
    queue_depths_by_plane: dict[Plane, int] = Field(default_factory=dict)
    queue_depths_by_family: dict[str, int] = Field(default_factory=dict)

    last_terminal_result: TerminalOutcome | None = None
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
            _set_status_for_plane(status_markers, Plane.EXECUTION, payload["execution_status_marker"])
        if "planning_status_marker" in payload:
            _set_status_for_plane(status_markers, Plane.PLANNING, payload["planning_status_marker"])
        if "learning_status_marker" in payload:
            _set_status_for_plane(status_markers, Plane.LEARNING, payload["learning_status_marker"])
        if status_markers:
            payload["status_markers_by_plane"] = status_markers

        queue_depths = dict(payload.get("queue_depths_by_plane") or {})
        if "queue_depth_execution" in payload:
            _set_depth_for_plane(queue_depths, Plane.EXECUTION, payload["queue_depth_execution"])
        if "queue_depth_planning" in payload:
            _set_depth_for_plane(queue_depths, Plane.PLANNING, payload["queue_depth_planning"])
        if "queue_depth_learning" in payload:
            _set_depth_for_plane(queue_depths, Plane.LEARNING, payload["queue_depth_learning"])
        if queue_depths:
            payload["queue_depths_by_plane"] = queue_depths

        snapshot_plan_id = payload.get("compiled_plan_id") or "legacy-unknown"
        snapshot_plan_fingerprint = (
            payload.get("compiled_plan_fingerprint") or snapshot_plan_id or "legacy-unknown"
        )
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
                    "compiled_plan_id": snapshot_plan_id,
                    "compiled_plan_fingerprint": snapshot_plan_fingerprint,
                    "request_kind": request_kind,
                    "work_item_family_id": family_id,
                    "work_item_kind": legacy_kind,
                    "work_item_id": active_work_item_id,
                    "active_since": active_since,
                }
        if active_runs:
            active_runs = {
                plane: _backfill_active_run_plan_identity(
                    active_run,
                    compiled_plan_id=snapshot_plan_id,
                    compiled_plan_fingerprint=snapshot_plan_fingerprint,
                )
                for plane, active_run in active_runs.items()
            }
            payload["active_runs_by_plane"] = active_runs

        if not payload.get("compiled_plan_fingerprint"):
            payload["compiled_plan_fingerprint"] = snapshot_plan_fingerprint

        # ---- canonical surfaces: derive from legacy compat after backfill ----
        status_by_scope = dict(payload.get("status_by_scope") or {})
        if not status_by_scope:
            for plane_key, marker in dict(payload.get("status_markers_by_plane") or {}).items():
                scope_key = plane_key.value if isinstance(plane_key, Plane) else str(plane_key)
                status_by_scope[scope_key] = marker
        if status_by_scope:
            payload["status_by_scope"] = status_by_scope

        active_runs_by_lane = dict(payload.get("active_runs_by_lane") or {})
        if not active_runs_by_lane:
            for plane_key, active_run in dict(active_runs or {}).items():
                if isinstance(active_run, dict):
                    lane_id = active_run.get("lane_id")
                else:
                    lane_id = getattr(active_run, "lane_id", None)
                if not lane_id:
                    plane_str = plane_key.value if isinstance(plane_key, Plane) else str(plane_key)
                    lane_id = f"{plane_str}.main"
                active_runs_by_lane[lane_id] = active_run
        if active_runs_by_lane:
            payload["active_runs_by_lane"] = active_runs_by_lane

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

        # Derive legacy plane-keyed compat from canonical surfaces when
        # the canonical surface is populated.  This keeps older consumers
        # (monitor, web dashboard, CLI) correct while the runtime writes
        # only canonical surfaces going forward.
        self._project_canonical_into_legacy_compat()

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

    @field_serializer("last_terminal_result")
    def serialize_last_terminal_result(self, value: TerminalOutcome | None) -> str | None:
        return terminal_outcome_value(value) if value is not None else None

    def _project_canonical_into_legacy_compat(self) -> None:
        """Derive legacy plane-keyed compat from canonical family/scope/lane surfaces."""
        # Status: per-plane scalars are the authoritative legacy signal and
        # always win (the engine sets them first via model_copy).  Build the
        # plane-keyed map from them, then fold in canonical status_by_scope
        # entries for planes that weren't set explicitly.  Preserve non-plane
        # scope entries (e.g. lane-level keys) so status_by_scope stays a true
        # scope-keyed surface rather than degrading to a plane-only mirror.
        sm = dict(self.status_markers_by_plane)
        sm[Plane.EXECUTION] = self.execution_status_marker
        sm[Plane.PLANNING] = self.planning_status_marker
        sm[Plane.LEARNING] = self.learning_status_marker

        # Fold canonical status_by_scope into plane-keyed map (gap-fill only)
        if self.status_by_scope:
            for scope_key, marker in self.status_by_scope.items():
                try:
                    plane = Plane(scope_key)
                except ValueError:
                    continue
                sm.setdefault(plane, marker)
        self.status_markers_by_plane = sm

        # status_by_scope: per-plane scalars are authoritative for plane-scope
        # entries.  Overwrite those while preserving non-plane scope keys
        # (e.g. lane-level entries) that have no corresponding per-plane scalar.
        scope = dict(self.status_by_scope)
        for plane, marker in sm.items():
            scope[plane.value] = marker
        self.status_by_scope = scope

        # Active runs: derive plane-keyed from lane-keyed only when
        # active_runs_by_plane is empty.  Do NOT reconstruct plane-keyed
        # from lane-keyed when the caller explicitly set active_runs_by_plane
        # (e.g. snapshot_without_active_plane clears it to {}).  This
        # preserves validation (e.g. plane-key mismatch) and correct
        # clearing behavior.
        if self.active_runs_by_lane and not self.active_runs_by_plane:
            derived = _derive_active_runs_by_plane_from_lane(
                self.active_runs_by_lane
            )
            if derived:
                self.active_runs_by_plane = derived

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


def _derive_active_runs_by_plane_from_lane(
    active_runs_by_lane: dict[str, ActiveRunState],
) -> dict[Plane, ActiveRunState]:
    """Pick one foreground active run per plane from lane-keyed runs.

    Self-contained in the contracts layer to avoid importing runtime modules.
    """
    by_plane: dict[Plane, list[ActiveRunState]] = {}
    for active_run in active_runs_by_lane.values():
        by_plane.setdefault(active_run.plane, []).append(active_run)

    result: dict[Plane, ActiveRunState] = {}
    for plane in (Plane.PLANNING, Plane.EXECUTION, Plane.LEARNING):
        runs = by_plane.get(plane)
        if runs:
            result[plane] = runs[0]
    return result


def _foreground_active_run(active_runs_by_plane: dict[Plane, ActiveRunState]) -> ActiveRunState:
    for plane in (Plane.PLANNING, Plane.EXECUTION, Plane.LEARNING):
        active_run = active_runs_by_plane.get(plane)
        if active_run is not None:
            return active_run
    raise ValueError("active_runs_by_plane cannot be empty")


def _set_depth_for_plane(
    queue_depths: dict[object, int],
    plane: Plane,
    depth: int,
) -> None:
    """Set a queue depth for a plane, handling both enum and string keys."""
    if plane in queue_depths:
        queue_depths[plane] = depth
    elif plane.value in queue_depths:
        queue_depths[plane.value] = depth
    else:
        queue_depths[plane.value] = depth


def _set_status_for_plane(
    status_markers: dict[object, str],
    plane: Plane,
    marker: str,
) -> None:
    """Set a status marker for a plane, handling both enum and string keys."""
    # Try enum key first, then string key
    if plane in status_markers:
        status_markers[plane] = marker
    elif plane.value in status_markers:
        status_markers[plane.value] = marker
    else:
        status_markers[plane.value] = marker


def _backfill_active_run_plan_identity(
    active_run: object,
    *,
    compiled_plan_id: str,
    compiled_plan_fingerprint: str,
) -> object:
    if not isinstance(active_run, dict):
        return active_run
    normalized = dict(active_run)
    if not str(normalized.get("compiled_plan_id") or "").strip():
        normalized["compiled_plan_id"] = compiled_plan_id
    if not str(normalized.get("compiled_plan_fingerprint") or "").strip():
        normalized["compiled_plan_fingerprint"] = compiled_plan_fingerprint
    return normalized


__all__ = [
    "ActiveRunRequestKind",
    "ActiveRunState",
    "LaneRuntimeState",
    "LaneRuntimeStatus",
    "RuntimeSnapshot",
]
