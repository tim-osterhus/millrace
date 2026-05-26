"""Runtime state marker validation and stale-state reconciliation helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from millrace_ai.architecture import CompiledRunPlan, FrozenGraphPlanePlan, MaterializedGraphNodePlan
from millrace_ai.contracts import (
    BlueprintDraftDocument,
    BlueprintManifestDocument,
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

_STAGE_ALLOWED_MARKERS: dict[str, frozenset[str]] = {
    ExecutionStageName.BUILDER.value: frozenset({"### BUILDER_COMPLETE", "### BLOCKED"}),
    ExecutionStageName.CHECKER.value: frozenset({"### CHECKER_PASS", "### FIX_NEEDED", "### BLOCKED"}),
    ExecutionStageName.FIXER.value: frozenset({"### FIXER_COMPLETE", "### BLOCKED"}),
    ExecutionStageName.DOUBLECHECKER.value: frozenset(
        {"### DOUBLECHECK_PASS", "### FIX_NEEDED", "### BLOCKED"}
    ),
    ExecutionStageName.UPDATER.value: frozenset({"### UPDATE_COMPLETE", "### BLOCKED"}),
    ExecutionStageName.TROUBLESHOOTER.value: frozenset(
        {"### TROUBLESHOOT_COMPLETE", "### BLOCKED"}
    ),
    ExecutionStageName.CONSULTANT.value: frozenset(
        {"### CONSULT_COMPLETE", "### NEEDS_PLANNING", "### BLOCKED"}
    ),
    PlanningStageName.RECON.value: frozenset(
        {
            "### RECON_TO_EXECUTION",
            "### RECON_TO_PLANNING",
            "### RECON_NOOP",
            "### RECON_BLOCKED",
            "### BLOCKED",
        }
    ),
    PlanningStageName.PLANNER.value: frozenset({"### PLANNER_COMPLETE", "### BLOCKED"}),
    PlanningStageName.MANAGER.value: frozenset({"### MANAGER_COMPLETE", "### BLOCKED"}),
    PlanningStageName.MECHANIC.value: frozenset({"### MECHANIC_COMPLETE", "### BLOCKED"}),
    PlanningStageName.AUDITOR.value: frozenset({"### AUDITOR_COMPLETE", "### BLOCKED"}),
    PlanningStageName.ARBITER.value: frozenset(
        {"### ARBITER_COMPLETE", "### REMEDIATION_NEEDED", "### BLOCKED"}
    ),
    LearningStageName.ANALYST.value: frozenset(
        {"### ANALYST_COMPLETE", "### ANALYST_NOOP", "### BLOCKED"}
    ),
    LearningStageName.PROFESSOR.value: frozenset(
        {"### PROFESSOR_COMPLETE", "### PROFESSOR_NOOP", "### BLOCKED"}
    ),
    LearningStageName.CURATOR.value: frozenset(
        {"### CURATOR_COMPLETE", "### CURATOR_NOOP", "### BLOCKED"}
    ),
}

_STAGE_INBOUND_MARKERS: dict[str, frozenset[str]] = {
    ExecutionStageName.BUILDER.value: frozenset({"### TROUBLESHOOT_COMPLETE", "### CONSULT_COMPLETE"}),
    ExecutionStageName.CHECKER.value: frozenset(
        {"### BUILDER_COMPLETE", "### TROUBLESHOOT_COMPLETE", "### CONSULT_COMPLETE"}
    ),
    ExecutionStageName.FIXER.value: frozenset(
        {"### FIX_NEEDED", "### TROUBLESHOOT_COMPLETE", "### CONSULT_COMPLETE"}
    ),
    ExecutionStageName.DOUBLECHECKER.value: frozenset(
        {"### FIXER_COMPLETE", "### TROUBLESHOOT_COMPLETE", "### CONSULT_COMPLETE"}
    ),
    ExecutionStageName.UPDATER.value: frozenset(
        {
            "### CHECKER_PASS",
            "### DOUBLECHECK_PASS",
            "### TROUBLESHOOT_COMPLETE",
            "### CONSULT_COMPLETE",
        }
    ),
    ExecutionStageName.TROUBLESHOOTER.value: _EXECUTION_STATUS_MARKERS - {_IDLE_MARKER},
    ExecutionStageName.CONSULTANT.value: _EXECUTION_STATUS_MARKERS - {_IDLE_MARKER},
    PlanningStageName.RECON.value: frozenset(),
    PlanningStageName.PLANNER.value: frozenset({"### AUDITOR_COMPLETE", "### MECHANIC_COMPLETE"}),
    PlanningStageName.MANAGER.value: frozenset({"### PLANNER_COMPLETE"}),
    PlanningStageName.MECHANIC.value: _PLANNING_STATUS_MARKERS - {_IDLE_MARKER},
    PlanningStageName.AUDITOR.value: frozenset(),
    PlanningStageName.ARBITER.value: frozenset(),
    LearningStageName.ANALYST.value: frozenset(),
    LearningStageName.PROFESSOR.value: frozenset({"### ANALYST_COMPLETE"}),
    LearningStageName.CURATOR.value: frozenset({"### PROFESSOR_COMPLETE"}),
}


@dataclass(frozen=True, slots=True)
class ReconciliationSignal:
    """Signal emitted when runtime state is stale or impossible."""

    code: str
    failure_class: str
    plane: Plane | None
    recommended_stage: StageName | None
    message: str


@dataclass(frozen=True, slots=True)
class BlueprintManifestDiagnostic:
    """Read-only diagnostic for malformed Blueprint manifest/draft state."""

    code: str
    message: str
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class _ManifestEntry:
    path: Path
    manifest: BlueprintManifestDocument
    normalized_payload: str


@dataclass(frozen=True, slots=True)
class _DraftEntry:
    state: str
    path: Path
    draft: BlueprintDraftDocument


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
    """Collect read-only diagnostics for Blueprint manifest/draft consistency."""

    diagnostics: list[BlueprintManifestDiagnostic] = []
    manifest_entries = _blueprint_manifest_entries(paths, diagnostics)
    draft_entries = _blueprint_draft_entries(paths)

    entries_by_manifest_id: dict[str, list[_ManifestEntry]] = {}
    for entry in manifest_entries:
        entries_by_manifest_id.setdefault(entry.manifest.manifest_id, []).append(entry)
        if _is_legacy_root_keyed_manifest(entry):
            diagnostics.append(
                BlueprintManifestDiagnostic(
                    code="blueprint_manifest_legacy_root_keyed",
                    message=(
                        f"manifest {entry.manifest.manifest_id} is stored under root key "
                        f"{entry.manifest.root_spec_id}; canonical filename is "
                        f"{entry.manifest.manifest_id}.json"
                    ),
                    path=entry.path,
                )
            )

    _append_duplicate_manifest_diagnostics(entries_by_manifest_id, diagnostics)

    draft_refs = {(entry.draft.manifest_id, entry.draft.draft_id) for entry in draft_entries}
    for entries in entries_by_manifest_id.values():
        manifest_entry = _preferred_manifest_entry(entries)
        for draft_id in manifest_entry.manifest.draft_ids:
            if (manifest_entry.manifest.manifest_id, draft_id) in draft_refs:
                continue
            diagnostics.append(
                BlueprintManifestDiagnostic(
                    code="blueprint_manifest_draft_missing",
                    message=(
                        f"manifest {manifest_entry.manifest.manifest_id} references draft "
                        f"{draft_id}, but no Blueprint draft lifecycle artifact contains it"
                    ),
                    path=manifest_entry.path,
                )
            )

    for draft_entry in draft_entries:
        manifest_entries_for_draft = entries_by_manifest_id.get(draft_entry.draft.manifest_id)
        if not manifest_entries_for_draft:
            diagnostics.append(
                BlueprintManifestDiagnostic(
                    code="blueprint_draft_manifest_unresolved",
                    message=(
                        f"draft {draft_entry.draft.draft_id} references manifest "
                        f"{draft_entry.draft.manifest_id}, but no Blueprint manifest artifact "
                        "declares that manifest_id"
                    ),
                    path=draft_entry.path,
                )
            )
            continue
        manifest_entry = _preferred_manifest_entry(manifest_entries_for_draft)
        if _draft_lineage_matches_manifest(draft_entry.draft, manifest_entry.manifest):
            continue
        diagnostics.append(
            BlueprintManifestDiagnostic(
                code="blueprint_manifest_draft_lineage_mismatch",
                message=(
                    f"draft {draft_entry.draft.draft_id} lineage does not match manifest "
                    f"{manifest_entry.manifest.manifest_id}: "
                    f"manifest root_spec_id={manifest_entry.manifest.root_spec_id} "
                    f"root_idea_id={manifest_entry.manifest.root_idea_id}; "
                    f"draft root_spec_id={draft_entry.draft.root_spec_id} "
                    f"root_idea_id={draft_entry.draft.root_idea_id}"
                ),
                path=draft_entry.path,
            )
        )

    return tuple(
        sorted(
            diagnostics,
            key=lambda diagnostic: (
                "" if diagnostic.path is None else diagnostic.path.as_posix(),
                diagnostic.code,
                diagnostic.message,
            ),
        )
    )


def collect_reconciliation_signals(
    *,
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    execution_status_marker: str,
    planning_status_marker: str,
    compiled_plan: CompiledRunPlan | None = None,
) -> tuple[ReconciliationSignal, ...]:
    execution_marker = _normalize_marker_or_invalid(execution_status_marker, label="execution status")
    planning_marker = _normalize_marker_or_invalid(planning_status_marker, label="planning status")
    execution_allowed_markers = _allowed_markers_for_plane(Plane.EXECUTION, compiled_plan=compiled_plan)
    planning_allowed_markers = _allowed_markers_for_plane(Plane.PLANNING, compiled_plan=compiled_plan)

    signals: list[ReconciliationSignal] = []

    if snapshot.active_stage is not None and not snapshot.process_running and _active_stage_appears_running(
        snapshot,
        execution_marker=execution_marker,
        planning_marker=planning_marker,
        compiled_plan=compiled_plan,
    ):
        signals.append(
            ReconciliationSignal(
                code="stale_active_ownership",
                failure_class=_STALE_ACTIVE_FAILURE_CLASS,
                plane=snapshot.active_plane,
                recommended_stage=_stale_signal_recommended_stage(snapshot, counters),
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
                    recommended_stage=ExecutionStageName.TROUBLESHOOTER,
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
                    recommended_stage=PlanningStageName.MECHANIC,
                    message="planning status marker is impossible for current active stage",
                )
            )

    if snapshot.active_stage is None:
        orphaned = _signal_for_orphaned_counters(counters)
        if orphaned is not None:
            signals.append(orphaned)

    return tuple(signals)


def _blueprint_manifest_entries(
    paths: WorkspacePaths,
    diagnostics: list[BlueprintManifestDiagnostic],
) -> tuple[_ManifestEntry, ...]:
    entries: list[_ManifestEntry] = []
    for path in _json_files(paths.runtime_root / "blueprints" / "manifests"):
        try:
            manifest = BlueprintManifestDocument.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            diagnostics.append(
                BlueprintManifestDiagnostic(
                    code="blueprint_manifest_invalid",
                    message=f"Blueprint manifest artifact is not parseable: {exc}",
                    path=path,
                )
            )
            continue
        entries.append(
            _ManifestEntry(
                path=path,
                manifest=manifest,
                normalized_payload=_normalized_blueprint_manifest_payload(manifest),
            )
        )
    return tuple(entries)


def _blueprint_draft_entries(paths: WorkspacePaths) -> tuple[_DraftEntry, ...]:
    entries: list[_DraftEntry] = []
    for state in _blueprint_draft_lifecycle_states():
        for path in _json_files(paths.runtime_root / "blueprints" / "drafts" / state):
            try:
                draft = BlueprintDraftDocument.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            entries.append(_DraftEntry(state=state, path=path, draft=draft))
    return tuple(entries)


def _append_duplicate_manifest_diagnostics(
    entries_by_manifest_id: dict[str, list[_ManifestEntry]],
    diagnostics: list[BlueprintManifestDiagnostic],
) -> None:
    for manifest_id, entries in sorted(entries_by_manifest_id.items()):
        normalized_payloads = {entry.normalized_payload for entry in entries}
        if len(normalized_payloads) <= 1:
            continue
        for entry in entries[1:]:
            diagnostics.append(
                BlueprintManifestDiagnostic(
                    code="blueprint_manifest_duplicate",
                    message=(
                        f"manifest_id {manifest_id} has multiple non-equivalent "
                        "Blueprint manifest artifacts"
                    ),
                    path=entry.path,
                )
            )


def _preferred_manifest_entry(entries: list[_ManifestEntry]) -> _ManifestEntry:
    for entry in entries:
        if entry.path.stem == entry.manifest.manifest_id:
            return entry
    return entries[0]


def _is_legacy_root_keyed_manifest(entry: _ManifestEntry) -> bool:
    return (
        entry.path.stem == entry.manifest.root_spec_id
        and entry.path.stem != entry.manifest.manifest_id
    )


def _draft_lineage_matches_manifest(
    draft: BlueprintDraftDocument,
    manifest: BlueprintManifestDocument,
) -> bool:
    return (
        draft.root_spec_id == manifest.root_spec_id
        and draft.root_idea_id == manifest.root_idea_id
    )


def _normalized_blueprint_manifest_payload(manifest: BlueprintManifestDocument) -> str:
    return json.dumps(
        manifest.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )


def _blueprint_draft_lifecycle_states() -> tuple[str, ...]:
    return ("queue", "active", "approved", "blocked", "canceled", "superseded", "rejected")


def _json_files(directory: Path) -> tuple[Path, ...]:
    if not directory.exists():
        return ()
    return tuple(sorted(path for path in directory.iterdir() if path.is_file() and path.suffix == ".json"))


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
    stage_allowed = _STAGE_ALLOWED_MARKERS.get(snapshot.active_stage.value)
    stage_inbound = _STAGE_INBOUND_MARKERS.get(snapshot.active_stage.value)
    if stage_allowed is None or stage_inbound is None:
        return False
    if marker == _IDLE_MARKER:
        return False
    if marker == running_status_marker_for_stage(snapshot.active_stage):
        return False
    return marker not in stage_allowed and marker not in stage_inbound


def _active_stage_appears_running(
    snapshot: RuntimeSnapshot,
    *,
    execution_marker: str,
    planning_marker: str,
    compiled_plan: CompiledRunPlan | None = None,
) -> bool:
    if snapshot.active_stage is None or snapshot.active_plane is None:
        return False
    active_run = snapshot.active_runs_by_plane.get(snapshot.active_plane)
    if active_run is not None and active_run.running_status_marker:
        return True

    marker = execution_marker if snapshot.active_plane is Plane.EXECUTION else planning_marker
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
) -> StageName:
    if snapshot.active_plane == Plane.PLANNING:
        return PlanningStageName.MECHANIC

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
    return ExecutionStageName.TROUBLESHOOTER


def _signal_for_orphaned_counters(counters: RecoveryCounters) -> ReconciliationSignal | None:
    for entry in counters.entries:
        if (
            entry.troubleshoot_attempt_count > 0
            or entry.mechanic_attempt_count > 0
            or entry.fix_cycle_count > 0
            or entry.consultant_invocations > 0
        ):
            if entry.work_item_kind == WorkItemKind.TASK:
                plane = Plane.EXECUTION
                stage: StageName = ExecutionStageName.TROUBLESHOOTER
            else:
                plane = Plane.PLANNING
                stage = PlanningStageName.MECHANIC

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
    "BlueprintManifestDiagnostic",
    "ReconciliationSignal",
    "collect_blueprint_manifest_diagnostics",
    "collect_reconciliation_signals",
    "normalize_execution_status_marker",
    "normalize_learning_status_marker",
    "normalize_planning_status_marker",
    "running_status_marker_for_stage",
]
