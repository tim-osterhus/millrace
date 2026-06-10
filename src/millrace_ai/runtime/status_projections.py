"""Shared status/depth projection helpers consumed by every runtime path.

All runtime paths that refresh queue depths or status markers use these
helpers.  Canonical surfaces are family-keyed depths, scope-keyed statuses,
and lane-keyed active runs.  Legacy plane-keyed fields are derived as
compatibility projections.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from millrace_ai.contracts import ActiveRunState, Plane

if TYPE_CHECKING:
    from millrace_ai.workspace.queue_family_interpreter import QueueFamilyInterpreter


# -- canonical surface dataclasses ------------------------------------------


@dataclass(frozen=True, slots=True)
class QueueProjections:
    """Canonical queue-depth surfaces and plane-keyed compatibility projections."""

    queue_depths_by_family: dict[str, int]
    queue_depths_by_plane: dict[Plane, int]


@dataclass(frozen=True, slots=True)
class StatusProjections:
    """Canonical status-by-scope and plane-keyed compatibility projections."""

    status_by_scope: dict[str, str]
    status_markers_by_plane: dict[Plane, str]


@dataclass(frozen=True, slots=True)
class ActiveRunProjections:
    """Canonical lane-keyed active runs and plane-keyed compatibility projections."""

    active_runs_by_lane: dict[str, ActiveRunState]
    active_runs_by_plane: dict[Plane, ActiveRunState]


# -- shared projection builders ---------------------------------------------


def build_queue_projections(
    *,
    family_interpreter: QueueFamilyInterpreter | None = None,
    queue_depths_by_family: dict[str, int] | None = None,
    families_by_plane: dict[str, Plane] | None = None,
) -> QueueProjections:
    """Build canonical family-keyed queue depths and derived plane-keyed compat.

    When a *family_interpreter* is supplied, depths are read directly from the
    filesystem.  Callers that already hold a computed depths map may pass
    *queue_depths_by_family* directly instead.

    *families_by_plane* maps family_id -> Plane for deriving plane totals.
    """
    if queue_depths_by_family is None:
        if family_interpreter is None:
            queue_depths_by_family = {}
        else:
            queue_depths_by_family = family_interpreter.queue_depths_by_family()

    plane_depths: dict[Plane, int] = {Plane.EXECUTION: 0, Plane.PLANNING: 0, Plane.LEARNING: 0}
    if families_by_plane is not None:
        for family_id, depth in queue_depths_by_family.items():
            plane = families_by_plane.get(family_id)
            if plane is not None:
                plane_depths[plane] = plane_depths.get(plane, 0) + depth

    return QueueProjections(
        queue_depths_by_family=dict(queue_depths_by_family),
        queue_depths_by_plane=dict(plane_depths),
    )


def build_status_projections(
    *,
    status_by_scope: dict[str, str] | None = None,
    status_markers_by_plane: dict[Plane, str] | None = None,
) -> StatusProjections:
    """Build canonical scope-keyed statuses and derived plane-keyed compat.

    When only *status_by_scope* is given, plane compat is derived by mapping
    scope keys to Plane enums when they match known plane values.  When only
    *status_markers_by_plane* is given, scope entries are derived from plane
    keys.

    When both are given, *status_by_scope* is canonical and
    *status_markers_by_plane* is derived from it.
    """
    if status_by_scope is not None:
        resolved_status_by_scope = dict(status_by_scope)
        resolved_by_plane = _status_by_plane_from_scope(resolved_status_by_scope)
    elif status_markers_by_plane is not None:
        resolved_by_plane = dict(status_markers_by_plane)
        resolved_status_by_scope = _status_by_scope_from_plane(resolved_by_plane)
    else:
        resolved_by_plane = {}
        resolved_status_by_scope = {}

    return StatusProjections(
        status_by_scope=resolved_status_by_scope,
        status_markers_by_plane=resolved_by_plane,
    )


def build_active_run_projections(
    *,
    active_runs_by_lane: dict[str, ActiveRunState] | None = None,
    active_runs_by_plane: dict[Plane, ActiveRunState] | None = None,
) -> ActiveRunProjections:
    """Build canonical lane-keyed active runs and derived plane-keyed compat.

    When *active_runs_by_lane* is given, plane compat is derived by picking
    the foreground lane for each plane (execution > planning > learning
    priority).  When only *active_runs_by_plane* is given, lane entries are
    derived from the active run's lane_id.
    """
    if active_runs_by_lane is not None:
        resolved_by_lane = dict(active_runs_by_lane)
        resolved_by_plane = _active_runs_by_plane_from_lane(resolved_by_lane)
    elif active_runs_by_plane is not None:
        resolved_by_plane = dict(active_runs_by_plane)
        resolved_by_lane = _active_runs_by_lane_from_plane(resolved_by_plane)
    else:
        resolved_by_lane = {}
        resolved_by_plane = {}

    return ActiveRunProjections(
        active_runs_by_lane=resolved_by_lane,
        active_runs_by_plane=resolved_by_plane,
    )


def derive_plane_queue_depths(
    *,
    queue_depths_by_family: dict[str, int],
    families_by_plane: dict[str, Plane],
) -> dict[Plane, int]:
    """Derive plane-keyed queue depths from family-keyed depths.

    Every family_id found in queue_depths_by_family contributes to its
    assigned plane total.  Missing planes default to 0.
    """
    result: dict[Plane, int] = {Plane.EXECUTION: 0, Plane.PLANNING: 0, Plane.LEARNING: 0}
    for family_id, depth in queue_depths_by_family.items():
        plane = families_by_plane.get(family_id)
        if plane is not None:
            result[plane] = result.get(plane, 0) + depth
    return result


def derive_plane_status_markers(
    *,
    status_by_scope: dict[str, str],
) -> dict[Plane, str]:
    """Derive plane-keyed status markers from scope-keyed statuses."""
    return _status_by_plane_from_scope(status_by_scope)


def derive_active_runs_by_plane(
    *,
    active_runs_by_lane: dict[str, ActiveRunState],
) -> dict[Plane, ActiveRunState]:
    """Derive plane-keyed active runs from lane-keyed active runs.

    Picks the foreground lane for each plane (execution > planning > learning
    priority when multiple lanes exist for the same plane).
    """
    return _active_runs_by_plane_from_lane(active_runs_by_lane)


# -- internal helpers -------------------------------------------------------


def _status_by_plane_from_scope(status_by_scope: dict[str, str]) -> dict[Plane, str]:
    result: dict[Plane, str] = {}
    for scope_key, marker in status_by_scope.items():
        try:
            plane = Plane(scope_key)
        except ValueError:
            continue
        result[plane] = marker
    return result


def _status_by_scope_from_plane(status_markers_by_plane: dict[Plane, str]) -> dict[str, str]:
    return {plane.value: marker for plane, marker in status_markers_by_plane.items()}


def _active_runs_by_plane_from_lane(
    active_runs_by_lane: dict[str, ActiveRunState],
) -> dict[Plane, ActiveRunState]:
    """Pick one foreground active run per plane from lane-keyed runs."""
    by_plane: dict[Plane, list[ActiveRunState]] = {}
    for active_run in active_runs_by_lane.values():
        by_plane.setdefault(active_run.plane, []).append(active_run)

    result: dict[Plane, ActiveRunState] = {}
    for plane in (Plane.PLANNING, Plane.EXECUTION, Plane.LEARNING):
        runs = by_plane.get(plane)
        if runs:
            result[plane] = runs[0]
    return result


def _active_runs_by_lane_from_plane(
    active_runs_by_plane: dict[Plane, ActiveRunState],
) -> dict[str, ActiveRunState]:
    result: dict[str, ActiveRunState] = {}
    for plane, active_run in active_runs_by_plane.items():
        lane_id = active_run.lane_id or f"{plane.value}.main"
        result[lane_id] = active_run
    return result


def families_by_plane_from_interpreter(
    family_interpreter: QueueFamilyInterpreter,
) -> dict[str, Plane]:
    """Build the family_id -> Plane mapping from the interpreter's known families."""
    return {f.family_id: f.plane for f in family_interpreter.families}


__all__ = [
    "ActiveRunProjections",
    "QueueProjections",
    "StatusProjections",
    "build_active_run_projections",
    "build_queue_projections",
    "build_status_projections",
    "derive_active_runs_by_plane",
    "derive_plane_queue_depths",
    "derive_plane_status_markers",
    "families_by_plane_from_interpreter",
]
