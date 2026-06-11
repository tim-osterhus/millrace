"""Runtime state marker validation and stale-state reconciliation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from millrace_ai.architecture import CompiledRunPlan, FrozenGraphPlanePlan, MaterializedGraphNodePlan
from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    LearningStageName,
    LearningTerminalResult,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    RecoveryCounters,
    RuntimeSnapshot,
    StageName,
    WorkItemKind,
)
from millrace_ai.errors import WorkspaceStateError

from .paths import WorkspacePaths

if TYPE_CHECKING:
    from millrace_ai.workspace.blueprint_state import BlueprintManifestDiagnostic

_IDLE_MARKER = "### IDLE"
_INVALID_MARKER = "### INVALID_STATUS_MARKER"
_STALE_ACTIVE_FAILURE_CLASS = "stale_active_ownership"
_IMPOSSIBLE_STATUS_FAILURE_CLASS = "impossible_status_marker"
_ORPHANED_COUNTER_FAILURE_CLASS = "stale_recovery_without_active_stage"

_RUNNING_MARKER_BY_STAGE: dict[str, str] = {
    stage.value: f"### {stage.value.upper()}_RUNNING"
    for stage in (*ExecutionStageName, *PlanningStageName, *LearningStageName)
}
_EXECUTION_RUNNING_MARKERS = frozenset(
    _RUNNING_MARKER_BY_STAGE[stage.value] for stage in ExecutionStageName
)
_PLANNING_RUNNING_MARKERS = frozenset(
    _RUNNING_MARKER_BY_STAGE[stage.value] for stage in PlanningStageName
)
_LEARNING_RUNNING_MARKERS = frozenset(
    _RUNNING_MARKER_BY_STAGE[stage.value] for stage in LearningStageName
)

_EXECUTION_STATUS_MARKERS = frozenset(
    {_IDLE_MARKER, *_EXECUTION_RUNNING_MARKERS, *(f"### {value.value}" for value in ExecutionTerminalResult)}
)
_PLANNING_STATUS_MARKERS = frozenset(
    {_IDLE_MARKER, *_PLANNING_RUNNING_MARKERS, *(f"### {value.value}" for value in PlanningTerminalResult)}
)
_LEARNING_STATUS_MARKERS = frozenset(
    {_IDLE_MARKER, *_LEARNING_RUNNING_MARKERS, *(f"### {value.value}" for value in LearningTerminalResult)}
)

@dataclass(frozen=True, slots=True)
class ReconciliationSignal:
    """Signal emitted when runtime state is stale or impossible."""

    code: str
    failure_class: str
    plane: Plane | None
    recommended_stage: StageName | None
    message: str


def normalize_execution_status_marker(marker: str) -> str:
    return _validate_status_marker_shape(marker, label="execution status")


def normalize_planning_status_marker(marker: str) -> str:
    return _validate_status_marker_shape(marker, label="planning status")


def normalize_learning_status_marker(marker: str) -> str:
    return _validate_status_marker_shape(marker, label="learning status")


def running_status_marker_for_stage(stage: StageName) -> str:
    return _RUNNING_MARKER_BY_STAGE[stage.value]


def collect_blueprint_manifest_diagnostics(
    paths: WorkspacePaths,
) -> tuple[BlueprintManifestDiagnostic, ...]:
    """Collect read-only diagnostics for Blueprint manifest/draft consistency.

    Lazily imports the implementation from ``blueprint_state`` to keep
    Blueprint module loading out of generic reconciliation import paths.
    """
    from millrace_ai.workspace.blueprint_state import collect_blueprint_manifest_diagnostics as _impl

    return _impl(paths)


def collect_reconciliation_signals(
    *,
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    execution_status_marker: str,
    planning_status_marker: str,
    learning_status_marker: str = _IDLE_MARKER,
    compiled_plan: CompiledRunPlan | None = None,
) -> tuple[ReconciliationSignal, ...]:
    execution_marker = _normalize_marker_or_invalid(execution_status_marker, label="execution status")
    planning_marker = _normalize_marker_or_invalid(planning_status_marker, label="planning status")
    learning_marker = _normalize_marker_or_invalid(learning_status_marker, label="learning status")
    execution_allowed_markers = _allowed_markers_for_plane(Plane.EXECUTION, compiled_plan=compiled_plan)
    planning_allowed_markers = _allowed_markers_for_plane(Plane.PLANNING, compiled_plan=compiled_plan)
    learning_allowed_markers = _allowed_markers_for_plane(Plane.LEARNING, compiled_plan=compiled_plan)

    signals: list[ReconciliationSignal] = []

    if snapshot.active_stage is not None and not snapshot.process_running and _active_stage_appears_running(
        snapshot,
        execution_marker=execution_marker,
        planning_marker=planning_marker,
        learning_marker=learning_marker,
        compiled_plan=compiled_plan,
    ):
        signals.append(
            ReconciliationSignal(
                code="stale_active_ownership",
                failure_class=_STALE_ACTIVE_FAILURE_CLASS,
                plane=snapshot.active_plane,
                recommended_stage=_stale_signal_recommended_stage(snapshot, counters, compiled_plan=compiled_plan),
                message="runtime snapshot has active ownership while process is not running",
            )
        )

    if snapshot.active_stage is not None and snapshot.active_plane == Plane.EXECUTION:
        if execution_marker not in execution_allowed_markers or _has_impossible_marker_for_active_stage(
            snapshot,
            execution_marker,
            compiled_plan=compiled_plan,
        ):
            signals.append(
                ReconciliationSignal(
                    code="impossible_execution_status_marker",
                    failure_class=_IMPOSSIBLE_STATUS_FAILURE_CLASS,
                    plane=Plane.EXECUTION,
                    recommended_stage=_stale_signal_recommended_stage_for_plane(Plane.EXECUTION, compiled_plan=compiled_plan),
                    message="execution status marker is impossible for current active stage",
                )
            )

    if snapshot.active_stage is not None and snapshot.active_plane == Plane.PLANNING:
        if planning_marker not in planning_allowed_markers or _has_impossible_marker_for_active_stage(
            snapshot,
            planning_marker,
            compiled_plan=compiled_plan,
        ):
            signals.append(
                ReconciliationSignal(
                    code="impossible_planning_status_marker",
                    failure_class=_IMPOSSIBLE_STATUS_FAILURE_CLASS,
                    plane=Plane.PLANNING,
                    recommended_stage=_stale_signal_recommended_stage_for_plane(Plane.PLANNING, compiled_plan=compiled_plan),
                    message="planning status marker is impossible for current active stage",
                )
            )

    if snapshot.active_stage is not None and snapshot.active_plane == Plane.LEARNING:
        if learning_marker not in learning_allowed_markers or _has_impossible_marker_for_active_stage(
            snapshot,
            learning_marker,
            compiled_plan=compiled_plan,
        ):
            signals.append(
                ReconciliationSignal(
                    code="impossible_learning_status_marker",
                    failure_class=_IMPOSSIBLE_STATUS_FAILURE_CLASS,
                    plane=Plane.LEARNING,
                    recommended_stage=_stale_signal_recommended_stage_for_plane(Plane.LEARNING, compiled_plan=compiled_plan),
                    message="learning status marker is impossible for current active stage",
                )
            )

    if snapshot.active_stage is None:
        orphaned = _signal_for_orphaned_counters(counters, compiled_plan=compiled_plan)
        if orphaned is not None:
            signals.append(orphaned)

    return tuple(signals)


def _normalize_marker(marker: str, *, label: str) -> str:
    normalized = marker.strip()
    if not normalized:
        raise WorkspaceStateError(f"{label} marker cannot be empty")
    lines = normalized.splitlines()
    if len(lines) != 1:
        raise WorkspaceStateError(f"{label} marker must be a single line")
    return lines[0]


def _validate_marker(marker: str, allowed: frozenset[str], *, label: str) -> str:
    normalized = _normalize_marker(marker, label=label)
    if normalized not in allowed:
        raise WorkspaceStateError(f"Unknown {label} marker: {normalized}")
    return normalized


def _validate_status_marker_shape(marker: str, *, label: str) -> str:
    normalized = _normalize_marker(marker, label=label)
    if not normalized.startswith("### ") or not normalized[4:].strip():
        raise WorkspaceStateError(f"{label} marker must start with '### '")
    return normalized


def _has_impossible_marker_for_active_stage(
    snapshot: RuntimeSnapshot,
    marker: str,
    *,
    compiled_plan: CompiledRunPlan | None = None,
) -> bool:
    if snapshot.active_stage is None:
        return False
    if compiled_plan is not None and snapshot.active_plane is not None:
        graph = _graph_for_plane(compiled_plan, snapshot.active_plane)
        if graph is not None:
            node_id = snapshot.active_node_id or snapshot.active_stage.value
            node = _compiled_node_for_id(graph, node_id)
            if node is None:
                return True
            allowed = frozenset(
                f"### {outcome}" for outcome in node.allowed_result_classes_by_outcome
            )
            inbound = _compiled_inbound_markers(graph, node.node_id)
            running_marker = f"### {node.running_status_marker}"
            if marker == _IDLE_MARKER:
                return False
            if marker == running_marker:
                return False
            return marker not in allowed and marker not in inbound
    # No compiled plan available; return False (conservative fail-open)
    # to avoid blocking daemon operation when compiled plan data is missing.
    return False


def _active_stage_appears_running(
    snapshot: RuntimeSnapshot,
    *,
    execution_marker: str,
    planning_marker: str,
    learning_marker: str = _IDLE_MARKER,
    compiled_plan: CompiledRunPlan | None = None,
) -> bool:
    if snapshot.active_stage is None or snapshot.active_plane is None:
        return False
    active_run = snapshot.active_runs_by_plane.get(snapshot.active_plane)
    if active_run is not None and active_run.running_status_marker:
        return True

    if snapshot.active_plane is Plane.EXECUTION:
        marker = execution_marker
    elif snapshot.active_plane is Plane.PLANNING:
        marker = planning_marker
    else:
        marker = learning_marker
    return marker == _active_stage_running_marker(snapshot, compiled_plan=compiled_plan)


def _active_stage_running_marker(
    snapshot: RuntimeSnapshot,
    *,
    compiled_plan: CompiledRunPlan | None = None,
) -> str:
    if snapshot.active_stage is None:
        return ""
    if compiled_plan is not None and snapshot.active_plane is not None:
        graph = _graph_for_plane(compiled_plan, snapshot.active_plane)
        if graph is not None:
            node_id = snapshot.active_node_id or snapshot.active_stage.value
            node = _compiled_node_for_id(graph, node_id)
            if node is not None:
                return f"### {node.running_status_marker}"
    return running_status_marker_for_stage(snapshot.active_stage)


def _stale_signal_recommended_stage(
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    *,
    compiled_plan: CompiledRunPlan | None = None,
) -> StageName:
    if snapshot.active_plane == Plane.PLANNING:
        return _stale_signal_recommended_stage_for_plane(Plane.PLANNING, compiled_plan=compiled_plan)

    attempts = 0
    if snapshot.active_work_item_family_id and snapshot.active_work_item_id:
        for entry in counters.entries:
            if (
                entry.failure_class == _STALE_ACTIVE_FAILURE_CLASS
                and entry.work_item_family_id == snapshot.active_work_item_family_id
                and entry.work_item_id == snapshot.active_work_item_id
            ):
                attempts = max(attempts, entry.troubleshoot_attempt_count)

    if attempts >= 2:
        return ExecutionStageName.CONSULTANT

    if compiled_plan is not None and snapshot.active_plane is not None:
        graph = _graph_for_plane(compiled_plan, snapshot.active_plane)
        if graph is not None and graph.runtime_failure_recovery is not None:
            repair_node = _compiled_node_for_id(graph, graph.runtime_failure_recovery.default_repair_node_id)
            if repair_node is not None and repair_node.runtime_stage is not None:
                return repair_node.runtime_stage
    return _stale_signal_recommended_stage_for_plane(snapshot.active_plane, compiled_plan=compiled_plan)


def _stale_signal_recommended_stage_for_plane(
    plane: Plane | None,
    *,
    compiled_plan: CompiledRunPlan | None = None,
) -> StageName:
    if compiled_plan is not None and plane is not None:
        graph = _graph_for_plane(compiled_plan, plane)
        if graph is not None and graph.runtime_failure_recovery is not None:
            repair_node = _compiled_node_for_id(graph, graph.runtime_failure_recovery.default_repair_node_id)
            if repair_node is not None and repair_node.runtime_stage is not None:
                return repair_node.runtime_stage
    if plane == Plane.PLANNING:
        return PlanningStageName.MECHANIC
    if plane == Plane.LEARNING:
        return LearningStageName.ANALYST
    return ExecutionStageName.TROUBLESHOOTER


def _signal_for_orphaned_counters(
    counters: RecoveryCounters,
    *,
    compiled_plan: CompiledRunPlan | None = None,
) -> ReconciliationSignal | None:
    for entry in counters.entries:
        if (
            entry.troubleshoot_attempt_count > 0
            or entry.mechanic_attempt_count > 0
            or entry.fix_cycle_count > 0
            or entry.consultant_invocations > 0
        ):
            plane, stage = _orphaned_counter_plane_and_stage(
                entry.work_item_family_id,
                entry.work_item_kind,
                compiled_plan=compiled_plan,
            )

            return ReconciliationSignal(
                code="orphaned_recovery_counters",
                failure_class=_ORPHANED_COUNTER_FAILURE_CLASS,
                plane=plane,
                recommended_stage=stage,
                message=(
                    "recovery counters indicate in-flight work while runtime snapshot "
                    "has no active stage"
                ),
            )
    return None


def _orphaned_counter_plane_and_stage(
    work_item_family_id: str | None,
    work_item_kind: WorkItemKind | None,
    *,
    compiled_plan: CompiledRunPlan | None = None,
) -> tuple[Plane, StageName]:
    """Derive plane and recommended stage for orphaned recovery counters.

    Uses compiled plan work-item family metadata when available.
    Otherwise falls back to a generic kind-based mapping.
    """
    if compiled_plan is not None and work_item_family_id is not None:
        family = compiled_plan.work_item_families_by_id.get(work_item_family_id)
        if family is not None:
            if family.plane is Plane.EXECUTION:
                return Plane.EXECUTION, ExecutionStageName.TROUBLESHOOTER
            if family.plane is Plane.LEARNING:
                return Plane.LEARNING, LearningStageName.ANALYST
            return Plane.PLANNING, PlanningStageName.MECHANIC
    # Generic fallback: execution-plane families default to TROUBLESHOOTER,
    # all others to MECHANIC.
    if work_item_kind in (
        WorkItemKind.TASK,
        WorkItemKind.SPEC,
        WorkItemKind.PROBE,
        WorkItemKind.LEARNING_REQUEST,
    ):
        if work_item_kind is WorkItemKind.TASK:
            return Plane.EXECUTION, ExecutionStageName.TROUBLESHOOTER
        if work_item_kind is WorkItemKind.LEARNING_REQUEST:
            return Plane.LEARNING, LearningStageName.ANALYST
    return Plane.PLANNING, PlanningStageName.MECHANIC


def _normalize_marker_or_invalid(marker: str, *, label: str) -> str:
    try:
        return _normalize_marker(marker, label=label)
    except WorkspaceStateError:
        return _INVALID_MARKER


def _allowed_markers_for_plane(
    plane: Plane,
    *,
    compiled_plan: CompiledRunPlan | None,
) -> frozenset[str]:
    if compiled_plan is None:
        if plane is Plane.EXECUTION:
            return _EXECUTION_STATUS_MARKERS
        if plane is Plane.LEARNING:
            return _LEARNING_STATUS_MARKERS
        return _PLANNING_STATUS_MARKERS

    graph = _graph_for_plane(compiled_plan, plane)
    if graph is None:
        return frozenset({_IDLE_MARKER})
    markers = {_IDLE_MARKER}
    markers.update(f"### {node.running_status_marker}" for node in graph.nodes)
    for node in graph.nodes:
        markers.update(
            f"### {outcome}" for outcome in node.allowed_result_classes_by_outcome
        )
    markers.update(f"### {terminal_state.writes_status}" for terminal_state in graph.terminal_states)
    return frozenset(markers)


def _graph_for_plane(
    compiled_plan: CompiledRunPlan,
    plane: Plane,
) -> FrozenGraphPlanePlan | None:
    if plane is Plane.EXECUTION:
        return compiled_plan.execution_graph
    if plane is Plane.LEARNING:
        return compiled_plan.learning_graph
    return compiled_plan.planning_graph


def _compiled_node_for_id(
    graph: FrozenGraphPlanePlan,
    node_id: str,
) -> MaterializedGraphNodePlan | None:
    for node in graph.nodes:
        if node.node_id == node_id:
            return node
    return None


def _compiled_inbound_markers(
    graph: FrozenGraphPlanePlan,
    node_id: str,
) -> frozenset[str]:
    markers = {
        f"### {transition.outcome}"
        for transition in graph.compiled_transitions
        if transition.target_node_id == node_id
    }
    return frozenset(markers)


__all__ = [
    "ReconciliationSignal",
    "collect_blueprint_manifest_diagnostics",
    "collect_reconciliation_signals",
    "normalize_execution_status_marker",
    "normalize_learning_status_marker",
    "normalize_planning_status_marker",
    "running_status_marker_for_stage",
]
