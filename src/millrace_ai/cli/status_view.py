"""Status view loading and line rendering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import typer

from millrace_ai.compilation.persistence import load_existing_plan
from millrace_ai.compiler import CompiledPlanCurrentness, inspect_workspace_plan_currentness
from millrace_ai.config import load_runtime_config
from millrace_ai.contracts import ClosureTargetState, StageResultEnvelope
from millrace_ai.contracts.blueprint import (
    BlueprintCritiqueDocument,
    BlueprintDraftDocument,
    BlueprintEvaluationDocument,
    BlueprintPacketDocument,
    BlueprintPromotionRecord,
)
from millrace_ai.events import RuntimeEventRecord, read_runtime_events
from millrace_ai.paths import WorkspacePaths
from millrace_ai.runtime.blueprint_recovery_diagnostics import (
    latest_runtime_effect_status_metadata,
)
from millrace_ai.runtime.error_recovery import load_runtime_error_context
from millrace_ai.runtime.pause_state import pause_sources_label
from millrace_ai.runtime.usage_governance import load_usage_governance_state
from millrace_ai.runtime_lock import inspect_runtime_ownership_lock
from millrace_ai.state_store import load_snapshot
from millrace_ai.workspace.arbiter_state import list_open_closure_target_states
from millrace_ai.workspace.baseline import BaselineManifest, load_baseline_manifest
from millrace_ai.workspace.queue_selection import list_deferred_root_spec_ids
from millrace_ai.workspace.work_inventory import family_counts, queue_depths_by_plane

_BUILTIN_STATUS_FAMILIES = {
    "task",
    "probe",
    "spec",
    "incident",
    "learning_request",
    "blueprint_draft",
}
_LATEST_RUNTIME_EFFECT_STATUS_KEYS = (
    "latest_runtime_effect_handler_id",
    "latest_runtime_effect_decision",
    "latest_runtime_effect_failure_class",
    "latest_runtime_effect_failure_message",
    "latest_runtime_effect_mutation_phase",
    "latest_runtime_effect_failure_policy_id",
    "latest_runtime_effect_recovery_action",
    "latest_blueprint_repair_context",
    "latest_blueprint_repair_contract",
    "latest_blueprint_replay_conflict_classes",
    "latest_blueprint_inert_artifact_guard",
    "latest_blueprint_runtime_ownership_boundary",
)


def _render_status_lines(paths: WorkspacePaths) -> tuple[str, ...]:
    snapshot = load_snapshot(paths)
    baseline_manifest = _load_baseline_manifest_safe(paths)
    currentness, currentness_error = _load_compile_currentness(paths)
    lock_status = inspect_runtime_ownership_lock(paths)
    process_running = snapshot.process_running and lock_status.state == "active"

    queue_depths = _queue_depths(paths)
    closure_status = _closure_target_status(paths)
    blueprint_status = _blueprint_status(paths)
    latest_runtime_error_context = _latest_runtime_error_context(paths)
    latest_runtime_error_report_path = (
        latest_runtime_error_context.report_path
        if latest_runtime_error_context is not None
        else None
    )
    latest_runtime_failure_origin = _failure_origin_value(latest_runtime_error_context)
    latest_operator_intervention = _latest_operator_intervention(paths)
    latest_runtime_effect = _latest_runtime_effect_metadata(
        paths,
        snapshot.last_stage_result_path,
    )
    blocked_idle = _blocked_idle(
        process_running=process_running,
        active_run_count=len(snapshot.active_runs_by_plane),
        execution_queue_depth=queue_depths["execution"],
        planning_queue_depth=queue_depths["planning"],
        learning_queue_depth=queue_depths["learning"],
        closure_target_open=closure_status["closure_target_open"],
        closure_target_blocked_by_lineage_work=closure_status[
            "closure_target_blocked_by_lineage_work"
        ],
        planning_status_marker=snapshot.planning_status_marker,
        current_failure_class=snapshot.current_failure_class,
    )

    lines = [
        f"workspace: {paths.root}",
        f"runtime_mode: {snapshot.runtime_mode.value}",
        f"process_running: {'true' if process_running else 'false'}",
        f"runtime_ownership_lock: {lock_status.state}",
        f"paused: {'true' if snapshot.paused else 'false'}",
        f"pause_sources: {pause_sources_label(snapshot)}",
        f"stop_requested: {'true' if snapshot.stop_requested else 'false'}",
        f"active_mode_id: {snapshot.active_mode_id}",
        f"compiled_plan_id: {snapshot.compiled_plan_id}",
        f"compiled_plan_fingerprint: {snapshot.compiled_plan_fingerprint}",
        f"pending_compiled_plan_id: {_status_value(snapshot.pending_compiled_plan_id)}",
        (
            "pending_compiled_plan_fingerprint: "
            f"{_status_value(snapshot.pending_compiled_plan_fingerprint)}"
        ),
        f"pending_compiled_plan_path: {_status_value(snapshot.pending_compiled_plan_path)}",
        f"compiled_plan_currentness: {_compiled_plan_currentness_value(currentness, currentness_error)}",
        f"active_plane: {_status_value(snapshot.active_plane)}",
        f"active_stage: {_status_value(snapshot.active_stage)}",
        f"active_node_id: {_status_value(snapshot.active_node_id)}",
        f"active_stage_kind_id: {_status_value(snapshot.active_stage_kind_id)}",
        f"active_work_item_family_id: {_status_value(snapshot.active_work_item_family_id)}",
        f"active_work_item_kind: {_status_value(snapshot.active_work_item_kind)}",
        f"active_work_item_id: {_status_value(snapshot.active_work_item_id)}",
        f"active_run_count: {len(snapshot.active_runs_by_plane)}",
        f"execution_queue_depth: {queue_depths['execution']}",
        f"planning_queue_depth: {queue_depths['planning']}",
        f"learning_queue_depth: {queue_depths['learning']}",
        f"execution_status_marker: {snapshot.execution_status_marker}",
        f"planning_status_marker: {snapshot.planning_status_marker}",
        f"learning_status_marker: {snapshot.learning_status_marker}",
        f"blocked_idle: {'true' if blocked_idle else 'false'}",
        f"latest_runtime_error_report_path: {_status_value(latest_runtime_error_report_path)}",
        f"latest_runtime_failure_origin: {_status_value(latest_runtime_failure_origin)}",
        "latest_operator_intervention: "
        f"{_operator_intervention_status_value(latest_operator_intervention)}",
    ]
    lines.extend(_render_lane_lines(snapshot.lanes_by_id))
    lines.extend(_render_active_run_lines(snapshot.active_runs_by_plane))
    lines.extend(_render_baseline_manifest_lines(baseline_manifest))
    lines.extend(_render_compile_currentness_lines(currentness, currentness_error))
    lines.extend(_render_latest_runtime_effect_lines(latest_runtime_effect))
    lines.extend(_render_usage_governance_status_lines(paths))
    lines.extend(_render_closure_target_status_lines_from_status(closure_status))
    lines.extend(_render_work_item_family_status_lines(paths))
    lines.extend(_render_blueprint_status_lines(blueprint_status))
    if snapshot.current_failure_class:
        lines.append(f"current_failure_class: {snapshot.current_failure_class}")
        for label, count in (
            ("troubleshoot_attempt_count", snapshot.troubleshoot_attempt_count),
            ("mechanic_attempt_count", snapshot.mechanic_attempt_count),
            ("fix_cycle_count", snapshot.fix_cycle_count),
            ("consultant_invocations", snapshot.consultant_invocations),
        ):
            if count > 0:
                lines.append(f"{label}: {count}")
    return tuple(lines)


def _status_payload(paths: WorkspacePaths) -> dict[str, Any]:
    snapshot = load_snapshot(paths)
    currentness, currentness_error = _load_compile_currentness(paths)
    lock_status = inspect_runtime_ownership_lock(paths)
    process_running = snapshot.process_running and lock_status.state == "active"
    queue_depths = _queue_depths(paths)
    closure_status = _closure_target_status(paths)
    blueprint_status = _blueprint_status(paths)
    latest_runtime_error_context = _latest_runtime_error_context(paths)
    latest_runtime_error_report_path = (
        latest_runtime_error_context.report_path
        if latest_runtime_error_context is not None
        else None
    )
    latest_operator_intervention = _latest_operator_intervention(paths)
    latest_runtime_effect = _latest_runtime_effect_metadata(
        paths,
        snapshot.last_stage_result_path,
    )
    active_run_count = len(snapshot.active_runs_by_plane)
    blocked_idle = _blocked_idle(
        process_running=process_running,
        active_run_count=active_run_count,
        execution_queue_depth=queue_depths["execution"],
        planning_queue_depth=queue_depths["planning"],
        learning_queue_depth=queue_depths["learning"],
        closure_target_open=closure_status["closure_target_open"],
        closure_target_blocked_by_lineage_work=closure_status[
            "closure_target_blocked_by_lineage_work"
        ],
        planning_status_marker=snapshot.planning_status_marker,
        current_failure_class=snapshot.current_failure_class,
    )
    return {
        "workspace": str(paths.root),
        "runtime_mode": snapshot.runtime_mode.value,
        "process_running": process_running,
        "runtime_ownership_lock": lock_status.state,
        "paused": snapshot.paused,
        "pause_sources": pause_sources_label(snapshot),
        "stop_requested": snapshot.stop_requested,
        "active_mode_id": snapshot.active_mode_id,
        "compiled_plan_id": snapshot.compiled_plan_id,
        "compiled_plan_fingerprint": snapshot.compiled_plan_fingerprint,
        "pending_compiled_plan_id": snapshot.pending_compiled_plan_id,
        "pending_compiled_plan_fingerprint": snapshot.pending_compiled_plan_fingerprint,
        "pending_compiled_plan_path": snapshot.pending_compiled_plan_path,
        "compiled_plan_currentness": _compiled_plan_currentness_value(
            currentness,
            currentness_error,
        ),
        "active_plane": _status_payload_value(snapshot.active_plane),
        "active_stage": _status_payload_value(snapshot.active_stage),
        "active_node_id": snapshot.active_node_id,
        "active_stage_kind_id": snapshot.active_stage_kind_id,
        "active_work_item_family_id": snapshot.active_work_item_family_id,
        "active_work_item_kind": _status_payload_value(snapshot.active_work_item_kind),
        "active_work_item_id": snapshot.active_work_item_id,
        "active_run_count": active_run_count,
        "execution_queue_depth": queue_depths["execution"],
        "planning_queue_depth": queue_depths["planning"],
        "learning_queue_depth": queue_depths["learning"],
        "execution_status_marker": snapshot.execution_status_marker,
        "planning_status_marker": snapshot.planning_status_marker,
        "learning_status_marker": snapshot.learning_status_marker,
        "blocked_idle": blocked_idle,
        "current_failure_class": snapshot.current_failure_class,
        "latest_runtime_error_report_path": latest_runtime_error_report_path,
        "latest_runtime_failure_origin": _failure_origin_value(latest_runtime_error_context),
        "lanes_by_id": _lanes_payload(snapshot.lanes_by_id),
        "active_runs_by_plane": _active_runs_payload(snapshot.active_runs_by_plane),
        "latest_operator_intervention": _operator_intervention_payload(latest_operator_intervention),
        "latest_runtime_effect": latest_runtime_effect or None,
        "work_item_families": _work_item_family_status_payload(paths),
        "blueprints": blueprint_status,
        **closure_status,
    }


def _render_active_run_lines(active_runs_by_plane: object) -> tuple[str, ...]:
    if not isinstance(active_runs_by_plane, dict) or not active_runs_by_plane:
        return ()
    lines: list[str] = []
    for plane in ("planning", "execution", "learning"):
        active_run = active_runs_by_plane.get(_plane_key(plane))
        if active_run is None:
            active_run = active_runs_by_plane.get(plane)
        if active_run is None:
            continue
        lines.append(
            "active_run: "
            f"plane={_status_value(getattr(active_run, 'plane', plane))} "
            f"stage={_status_value(getattr(active_run, 'stage', None))} "
            f"node={_status_value(getattr(active_run, 'node_id', None))} "
            f"stage_kind={_status_value(getattr(active_run, 'stage_kind_id', None))} "
            f"lane={_status_value(getattr(active_run, 'lane_id', None))} "
            f"launch_plan={_status_value(getattr(active_run, 'compiled_plan_id', None))} "
            "launch_fingerprint="
            f"{_status_value(getattr(active_run, 'compiled_plan_fingerprint', None))} "
            f"request_kind={_status_value(getattr(active_run, 'request_kind', None))} "
            f"work_item_kind={_status_value(getattr(active_run, 'work_item_kind', None))} "
            f"work_item_id={_status_value(getattr(active_run, 'work_item_id', None))} "
            f"run={_short_run_handle(getattr(active_run, 'run_id', None))} "
            f"work_item_family={_status_value(getattr(active_run, 'work_item_family_id', None))}"
        )
    return tuple(lines)


def _render_lane_lines(lanes_by_id: object) -> tuple[str, ...]:
    if not isinstance(lanes_by_id, dict) or not lanes_by_id:
        return ()
    lines: list[str] = []
    for lane_key, lane in sorted(lanes_by_id.items(), key=lambda item: str(item[0])):
        lane_id = str(lane_key)
        if lane is None:
            continue
        lines.append(
            "lane: "
            f"id={_status_value(getattr(lane, 'lane_id', lane_id))} "
            f"plane={_status_value(getattr(lane, 'plane', None))} "
            f"status={_status_value(getattr(lane, 'status', None))} "
            f"plan={_status_value(getattr(lane, 'compiled_plan_id', None))} "
            f"fingerprint={_status_value(getattr(lane, 'compiled_plan_fingerprint', None))} "
            f"active_runs={_joined_status_values(getattr(lane, 'active_run_ids', ()))} "
            f"active_work={_joined_status_values(getattr(lane, 'active_work_refs', ()))} "
            f"last_terminal={_status_value(getattr(lane, 'last_terminal_outcome', None))} "
            f"pause_requested={_status_value(getattr(lane, 'pause_requested', None))} "
            f"drain_requested={_status_value(getattr(lane, 'drain_requested', None))} "
            f"stop_requested={_status_value(getattr(lane, 'stop_requested', None))}"
        )
    return tuple(lines)


_OPERATOR_INTERVENTION_EVENT_TYPES = {
    "work_item_cancelled",
    "blocked_task_archived",
    "task_superseded",
    "task_dependency_retargeted",
    "incident_resolved_by_operator",
    "incident_cancelled",
    "invalid_incident_artifact_archived",
}


def _latest_operator_intervention(paths: WorkspacePaths) -> RuntimeEventRecord | None:
    events = read_runtime_events(paths)
    for event in reversed(events):
        if event.event_type in _OPERATOR_INTERVENTION_EVENT_TYPES:
            return event
    return None


def _operator_intervention_status_value(event: RuntimeEventRecord | None) -> str:
    if event is None:
        return "none"
    work_item_id = event.data.get("work_item_id")
    destination_path = event.data.get("destination_path")
    parts = [
        f"event={event.event_type}",
        f"occurred_at={event.occurred_at.isoformat()}",
    ]
    if isinstance(work_item_id, str) and work_item_id:
        parts.append(f"work_item_id={work_item_id}")
    if isinstance(destination_path, str) and destination_path:
        parts.append(f"destination_path={destination_path}")
    return " ".join(parts)


def _operator_intervention_payload(event: RuntimeEventRecord | None) -> dict[str, object] | None:
    if event is None:
        return None
    return {
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        "work_item_kind": event.data.get("work_item_kind"),
        "work_item_id": event.data.get("work_item_id"),
        "destination_path": event.data.get("destination_path"),
    }


def _latest_runtime_effect_metadata(
    paths: WorkspacePaths,
    stage_result_path: str | None,
) -> dict[str, str]:
    return latest_runtime_effect_status_metadata(paths, stage_result_path)


def _load_stage_result(path: Path) -> StageResultEnvelope | None:
    try:
        return StageResultEnvelope.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _latest_sibling_runtime_effect_metadata(
    path: Path,
    stage_result: StageResultEnvelope,
) -> dict[str, str]:
    if not path.parent.is_dir():
        return {}
    current_sort_key = _stage_result_sort_key(stage_result, path)
    candidates: list[tuple[tuple[str, str, str], dict[str, str]]] = []
    for sibling in path.parent.iterdir():
        if not sibling.is_file() or sibling.suffix != ".json":
            continue
        sibling_stage_result = _load_stage_result(sibling)
        if sibling_stage_result is None:
            continue
        sort_key = _stage_result_sort_key(sibling_stage_result, sibling)
        if sort_key > current_sort_key:
            continue
        metadata = _runtime_effect_metadata_from_stage_result(sibling_stage_result)
        if metadata:
            candidates.append((sort_key, metadata))
    if not candidates:
        return {}
    return sorted(candidates, key=lambda item: item[0])[-1][1]


def _stage_result_sort_key(
    stage_result: StageResultEnvelope,
    path: Path,
) -> tuple[str, str, str]:
    return (
        stage_result.completed_at.isoformat(),
        stage_result.started_at.isoformat(),
        path.name,
    )


def _runtime_effect_metadata_from_stage_result(
    stage_result: StageResultEnvelope,
) -> dict[str, str]:
    metadata = stage_result.metadata
    values = {
        "latest_runtime_effect_handler_id": metadata.get("runtime_effect_handler_id"),
        "latest_runtime_effect_decision": metadata.get("runtime_effect_decision"),
        "latest_runtime_effect_failure_class": metadata.get("runtime_effect_failure_class"),
        "latest_runtime_effect_failure_message": metadata.get("runtime_effect_failure_message"),
        "latest_runtime_effect_mutation_phase": metadata.get("runtime_effect_mutation_phase"),
        "latest_runtime_effect_failure_policy_id": metadata.get(
            "runtime_effect_failure_policy_id"
        ),
        "latest_runtime_effect_recovery_action": metadata.get("runtime_effect_recovery_action"),
    }
    return {
        key: value
        for key, value in values.items()
        if isinstance(value, str) and value
    }


def _render_latest_runtime_effect_lines(payload: dict[str, str]) -> tuple[str, ...]:
    if not payload:
        return ()
    return tuple(
        f"{key}: {payload[key]}"
        for key in _LATEST_RUNTIME_EFFECT_STATUS_KEYS
        if key in payload
    )


def _print_status(paths: WorkspacePaths) -> None:
    for line in _render_status_lines(paths):
        typer.echo(line)


def _print_status_json(paths: WorkspacePaths) -> None:
    typer.echo(json.dumps(_status_payload(paths), indent=2, sort_keys=True))


def _print_statuses(paths_list: Sequence[WorkspacePaths]) -> None:
    for index, paths in enumerate(paths_list):
        if index > 0:
            typer.echo("")
        _print_status(paths)


def _render_closure_target_status_lines_from_status(
    status: dict[str, object],
) -> tuple[str, ...]:
    return (
        f"closure_target_root_spec_id: {_status_value(status['closure_target_root_spec_id'])}",
        (
            "closure_target_root_source: "
            f"{_status_value(status['closure_target_root_source_kind'])}/"
            f"{_status_value(status['closure_target_root_source_id'])}"
        ),
        (
            "closure_target_root_source_path: "
            f"{_status_value(status['closure_target_root_source_path'])}"
        ),
        f"closure_target_open: {_status_value(status['closure_target_open'])}",
        (
            "closure_target_blocked_by_lineage_work: "
            f"{_status_value(status['closure_target_blocked_by_lineage_work'])}"
        ),
        (
            "planning_root_specs_deferred_by_closure_target: "
            f"{_status_value(status['planning_root_specs_deferred_by_closure_target'])}"
        ),
        f"closure_target_latest_verdict_path: {_status_value(status['closure_target_latest_verdict_path'])}",
        f"closure_target_latest_report_path: {_status_value(status['closure_target_latest_report_path'])}",
    )


def _closure_target_status(paths: WorkspacePaths) -> dict[str, object]:
    open_targets = list_open_closure_target_states(paths)
    actionable_targets = _actionable_open_closure_targets(open_targets)
    if len(actionable_targets) > 1:
        return {
            "closure_target_root_spec_id": "invalid_multiple_actionable_open_targets",
            "closure_target_root_source_kind": "invalid",
            "closure_target_root_source_id": "invalid",
            "closure_target_root_source_path": None,
            "closure_target_open": "invalid",
            "closure_target_blocked_by_lineage_work": "invalid",
            "planning_root_specs_deferred_by_closure_target": "invalid",
            "closure_target_latest_verdict_path": None,
            "closure_target_latest_report_path": None,
        }
    if not open_targets:
        return {
            "closure_target_root_spec_id": None,
            "closure_target_root_source_kind": None,
            "closure_target_root_source_id": None,
            "closure_target_root_source_path": None,
            "closure_target_open": None,
            "closure_target_blocked_by_lineage_work": None,
            "planning_root_specs_deferred_by_closure_target": 0,
            "closure_target_latest_verdict_path": None,
            "closure_target_latest_report_path": None,
        }

    target = actionable_targets[0] if actionable_targets else open_targets[0]
    deferred_root_spec_ids = list_deferred_root_spec_ids(
        paths,
        open_root_spec_id=target.root_spec_id,
    )
    return {
        "closure_target_root_spec_id": target.root_spec_id,
        "closure_target_root_source_kind": target.root_source.kind,
        "closure_target_root_source_id": target.root_source.id,
        "closure_target_root_source_path": target.root_source.path,
        "closure_target_open": target.closure_open,
        "closure_target_blocked_by_lineage_work": target.closure_blocked_by_lineage_work,
        "planning_root_specs_deferred_by_closure_target": len(deferred_root_spec_ids),
        "closure_target_latest_verdict_path": target.latest_verdict_path,
        "closure_target_latest_report_path": target.latest_report_path,
    }


def _actionable_open_closure_targets(
    open_targets: tuple[ClosureTargetState, ...],
) -> tuple[ClosureTargetState, ...]:
    return tuple(
        target
        for target in open_targets
        if not target.closure_blocked_by_lineage_work
    )


def _render_usage_governance_status_lines(paths: WorkspacePaths) -> tuple[str, ...]:
    state = load_usage_governance_state(paths)
    try:
        config_enabled = load_runtime_config(paths.runtime_root / "millrace.toml").usage_governance.enabled
    except Exception:
        config_enabled = state.enabled

    lines = [
        f"usage_governance_enabled: {'true' if config_enabled else 'false'}",
        f"usage_governance_paused: {'true' if state.paused_by_governance else 'false'}",
        f"usage_governance_blocker_count: {len(state.active_blockers)}",
        (
            "usage_governance_auto_resume_possible: "
            f"{'true' if state.auto_resume_possible else 'false'}"
        ),
        f"usage_governance_next_auto_resume_at: {_status_value(state.next_auto_resume_at)}",
        f"usage_governance_subscription_status: {state.subscription_quota_status.state}",
    ]
    if state.subscription_quota_status.detail:
        lines.append(f"usage_governance_subscription_detail: {state.subscription_quota_status.detail}")
    for blocker in state.active_blockers:
        lines.append(
            "usage_governance_blocker: "
            f"source={blocker.source} "
            f"rule={blocker.rule_id} "
            f"window={blocker.window} "
            f"observed={blocker.observed:g} "
            f"threshold={blocker.threshold:g}"
        )
    return tuple(lines)


def _queue_depths(paths: WorkspacePaths) -> dict[str, int]:
    depths = queue_depths_by_plane(paths, compiled_plan=_load_compiled_plan_safe(paths))
    return {
        "execution": depths.get(_plane_key("execution"), 0),
        "planning": depths.get(_plane_key("planning"), 0),
        "learning": depths.get(_plane_key("learning"), 0),
    }


def _load_compiled_plan_safe(paths: WorkspacePaths):
    return load_existing_plan(paths.state_dir / "compiled_plan.json")


def _work_item_family_status_payload(paths: WorkspacePaths) -> list[dict[str, object]]:
    compiled_plan = _load_compiled_plan_safe(paths)
    if compiled_plan is None:
        return []
    counts = family_counts(paths, compiled_plan=compiled_plan)
    payload: list[dict[str, object]] = []
    for family_id, family in sorted(compiled_plan.work_item_families_by_id.items()):
        if family_id in _BUILTIN_STATUS_FAMILIES:
            continue
        family_state_counts = counts.get(family_id, {})
        if not any(family_state_counts.values()):
            continue
        payload.append(
            {
                "family_id": family_id,
                "plane": family.plane.value,
                "queue": family_state_counts.get("queue", 0),
                "active": family_state_counts.get("active", 0),
                "blocked": family_state_counts.get("blocked", 0),
                "done": family_state_counts.get("done", 0),
                "canceled": family_state_counts.get("canceled", 0),
            }
        )
    return payload


def _render_work_item_family_status_lines(paths: WorkspacePaths) -> tuple[str, ...]:
    lines: list[str] = []
    for item in _work_item_family_status_payload(paths):
        lines.append(
            "work_item_family: "
            f"family={_status_value(item.get('family_id'))} "
            f"plane={_status_value(item.get('plane'))} "
            f"queue={_status_value(item.get('queue'))} "
            f"active={_status_value(item.get('active'))} "
            f"blocked={_status_value(item.get('blocked'))} "
            f"done={_status_value(item.get('done'))} "
            f"canceled={_status_value(item.get('canceled'))}"
        )
    return tuple(lines)


def _blueprint_status(paths: WorkspacePaths) -> dict[str, object]:
    root = paths.runtime_root / "blueprints"
    draft_counts: dict[str, int] = {}
    drafts: list[dict[str, object]] = []
    for state in ("queue", "active", "blocked", "approved", "canceled", "superseded"):
        directory = root / "drafts" / state
        draft_counts[state] = 0
        for path in _json_files(directory):
            draft_counts[state] += 1
            draft = _read_json_model(path, BlueprintDraftDocument)
            if draft is None:
                continue
            drafts.append(
                {
                    "state": state,
                    "draft_id": draft.draft_id,
                    "root_spec_id": draft.root_spec_id,
                    "draft_index": draft.draft_index,
                    "current_revision": draft.current_revision,
                    "latest_blueprint_id": draft.latest_blueprint_id,
                    "latest_critique_id": draft.latest_critique_id,
                    "path": _workspace_relative(paths, path),
                }
            )

    packets: list[dict[str, object]] = []
    packet_counts: dict[str, int] = {}
    for state in ("candidates", "approved", "rejected", "superseded"):
        directory = root / "packets" / state
        packet_counts[state] = 0
        for path in _json_files(directory):
            packet_counts[state] += 1
            packet = _read_json_model(path, BlueprintPacketDocument)
            if packet is None:
                continue
            packets.append(
                {
                    "state": state,
                    "blueprint_id": packet.blueprint_id,
                    "draft_id": packet.draft_id,
                    "root_spec_id": packet.root_spec_id,
                    "revision": packet.revision,
                    "path": _workspace_relative(paths, path),
                }
            )

    critiques: list[dict[str, object]] = []
    critique_counts: dict[str, int] = {}
    for state in ("open", "resolved"):
        directory = root / "critiques" / state
        critique_counts[state] = 0
        for path in _json_files(directory):
            critique_counts[state] += 1
            critique = _read_json_model(path, BlueprintCritiqueDocument)
            if critique is None:
                continue
            critiques.append(
                {
                    "state": state,
                    "critique_id": critique.critique_id,
                    "blueprint_id": critique.blueprint_id,
                    "draft_id": critique.draft_id,
                    "root_spec_id": critique.root_spec_id,
                    "path": _workspace_relative(paths, path),
                }
            )

    evaluations: list[dict[str, object]] = []
    for path in _json_files(root / "evaluations"):
        evaluation = _read_json_model(path, BlueprintEvaluationDocument)
        if evaluation is None:
            continue
        evaluations.append(
            {
                "evaluation_id": evaluation.evaluation_id,
                "decision": evaluation.decision,
                "blueprint_id": evaluation.blueprint_id,
                "draft_id": evaluation.draft_id,
                "root_spec_id": evaluation.root_spec_id,
                "critique_id": evaluation.critique_id,
                "path": _workspace_relative(paths, path),
            }
        )

    promotions: list[dict[str, object]] = []
    for path in _json_files(root / "promotions"):
        promotion = _read_json_model(path, BlueprintPromotionRecord)
        if promotion is None:
            continue
        promotions.append(
            {
                "promotion_id": promotion.promotion_id,
                "blueprint_id": promotion.blueprint_id,
                "evaluation_id": promotion.evaluation_id,
                "draft_id": promotion.draft_id,
                "root_spec_id": promotion.root_spec_id,
                "generated_task_id": promotion.generated_task_id,
                "generated_task_path": promotion.generated_task_path,
                "path": _workspace_relative(paths, path),
            }
        )

    return {
        "draft_counts": draft_counts,
        "packet_counts": packet_counts,
        "critique_counts": critique_counts,
        "evaluation_count": len(evaluations),
        "promotion_count": len(promotions),
        "drafts": sorted(drafts, key=lambda item: (str(item["state"]), str(item["draft_id"]))),
        "packets": sorted(packets, key=lambda item: (str(item["state"]), str(item["blueprint_id"]))),
        "critiques": sorted(critiques, key=lambda item: (str(item["state"]), str(item["critique_id"]))),
        "evaluations": sorted(evaluations, key=lambda item: str(item["evaluation_id"])),
        "promotions": sorted(promotions, key=lambda item: str(item["promotion_id"])),
    }


def _render_blueprint_status_lines(status: dict[str, object]) -> tuple[str, ...]:
    draft_counts = _dict_value(status, "draft_counts")
    packet_counts = _dict_value(status, "packet_counts")
    critique_counts = _dict_value(status, "critique_counts")
    lines = [
        f"blueprint_draft_queue_depth: {_count_value(draft_counts, 'queue')}",
        f"blueprint_draft_active_count: {_count_value(draft_counts, 'active')}",
        f"blueprint_draft_blocked_count: {_count_value(draft_counts, 'blocked')}",
        f"blueprint_draft_approved_count: {_count_value(draft_counts, 'approved')}",
        f"blueprint_packet_candidate_count: {_count_value(packet_counts, 'candidates')}",
        f"blueprint_packet_approved_count: {_count_value(packet_counts, 'approved')}",
        f"blueprint_packet_rejected_count: {_count_value(packet_counts, 'rejected')}",
        f"blueprint_critique_open_count: {_count_value(critique_counts, 'open')}",
        f"blueprint_evaluation_count: {_status_value(status.get('evaluation_count'))}",
        f"blueprint_promotion_count: {_status_value(status.get('promotion_count'))}",
    ]
    for draft in _dict_items(status, "drafts"):
        lines.append(
            "blueprint_draft: "
            f"state={_status_value(draft.get('state'))} "
            f"draft={_status_value(draft.get('draft_id'))} "
            f"root_spec={_status_value(draft.get('root_spec_id'))} "
            f"revision={_status_value(draft.get('current_revision'))} "
            f"latest_blueprint={_status_value(draft.get('latest_blueprint_id'))} "
            f"latest_critique={_status_value(draft.get('latest_critique_id'))} "
            f"path={_status_value(draft.get('path'))}"
        )
    for packet in _dict_items(status, "packets"):
        lines.append(
            "blueprint_packet: "
            f"state={_status_value(packet.get('state'))} "
            f"blueprint={_status_value(packet.get('blueprint_id'))} "
            f"draft={_status_value(packet.get('draft_id'))} "
            f"root_spec={_status_value(packet.get('root_spec_id'))} "
            f"revision={_status_value(packet.get('revision'))} "
            f"path={_status_value(packet.get('path'))}"
        )
    for critique in _dict_items(status, "critiques"):
        lines.append(
            "blueprint_critique: "
            f"state={_status_value(critique.get('state'))} "
            f"critique={_status_value(critique.get('critique_id'))} "
            f"blueprint={_status_value(critique.get('blueprint_id'))} "
            f"draft={_status_value(critique.get('draft_id'))} "
            f"path={_status_value(critique.get('path'))}"
        )
    for evaluation in _dict_items(status, "evaluations"):
        lines.append(
            "blueprint_evaluation: "
            f"evaluation={_status_value(evaluation.get('evaluation_id'))} "
            f"decision={_status_value(evaluation.get('decision'))} "
            f"blueprint={_status_value(evaluation.get('blueprint_id'))} "
            f"draft={_status_value(evaluation.get('draft_id'))} "
            f"critique={_status_value(evaluation.get('critique_id'))} "
            f"path={_status_value(evaluation.get('path'))}"
        )
    for promotion in _dict_items(status, "promotions"):
        lines.append(
            "blueprint_promotion: "
            f"promotion={_status_value(promotion.get('promotion_id'))} "
            f"blueprint={_status_value(promotion.get('blueprint_id'))} "
            f"evaluation={_status_value(promotion.get('evaluation_id'))} "
            f"generated_task={_status_value(promotion.get('generated_task_id'))} "
            f"generated_task_path={_status_value(promotion.get('generated_task_path'))} "
            f"path={_status_value(promotion.get('path'))}"
        )
    return tuple(lines)


def _latest_runtime_error_context(paths: WorkspacePaths) -> object | None:
    return load_runtime_error_context(paths)


def _latest_runtime_error_report_path(paths: WorkspacePaths) -> str | None:
    context = _latest_runtime_error_context(paths)
    return context.report_path if context is not None else None


def _failure_origin_value(context: object | None) -> str | None:
    if context is None:
        return None
    failure_origin = getattr(context, "failure_origin", None)
    enum_value = getattr(failure_origin, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return failure_origin if isinstance(failure_origin, str) else None


def _joined_status_values(values: object) -> str:
    if not isinstance(values, (list, tuple)) or not values:
        return "none"
    return ",".join(_status_value(value) for value in values)


def _lanes_payload(lanes_by_id: object) -> dict[str, object]:
    if not isinstance(lanes_by_id, dict):
        return {}
    payload: dict[str, object] = {}
    for lane_key, lane in sorted(lanes_by_id.items(), key=lambda item: str(item[0])):
        lane_id = str(lane_key)
        if lane is None:
            continue
        payload[lane_id] = {
            "lane_id": _status_payload_value(getattr(lane, "lane_id", lane_id)),
            "plane": _status_payload_value(getattr(lane, "plane", None)),
            "status": getattr(lane, "status", None),
            "compiled_plan_id": getattr(lane, "compiled_plan_id", None),
            "compiled_plan_fingerprint": getattr(lane, "compiled_plan_fingerprint", None),
            "active_run_ids": list(getattr(lane, "active_run_ids", ())),
            "active_work_refs": list(getattr(lane, "active_work_refs", ())),
            "pause_requested": getattr(lane, "pause_requested", None),
            "stop_requested": getattr(lane, "stop_requested", None),
            "drain_requested": getattr(lane, "drain_requested", None),
            "mutation_lock_refs": list(getattr(lane, "mutation_lock_refs", ())),
            "completion_target_refs": list(getattr(lane, "completion_target_refs", ())),
            "failure_counter_refs": list(getattr(lane, "failure_counter_refs", ())),
            "last_claim_attempt_at": _status_payload_value(
                getattr(lane, "last_claim_attempt_at", None),
            ),
            "last_terminal_outcome": getattr(lane, "last_terminal_outcome", None),
        }
    return payload


def _active_runs_payload(active_runs_by_plane: object) -> dict[str, object]:
    if not isinstance(active_runs_by_plane, dict):
        return {}
    payload: dict[str, object] = {}
    for plane_key, active_run in sorted(
        active_runs_by_plane.items(),
        key=lambda item: _status_value(item[0]),
    ):
        plane = _status_value(getattr(active_run, "plane", plane_key))
        payload[plane] = {
            "plane": plane,
            "lane_id": getattr(active_run, "lane_id", None),
            "stage": _status_payload_value(getattr(active_run, "stage", None)),
            "node_id": getattr(active_run, "node_id", None),
            "stage_kind_id": getattr(active_run, "stage_kind_id", None),
            "run_id": getattr(active_run, "run_id", None),
            "compiled_plan_id": getattr(active_run, "compiled_plan_id", None),
            "compiled_plan_fingerprint": getattr(
                active_run,
                "compiled_plan_fingerprint",
                None,
            ),
            "request_kind": getattr(active_run, "request_kind", None),
            "work_item_family_id": getattr(active_run, "work_item_family_id", None),
            "work_item_kind": _status_payload_value(
                getattr(active_run, "work_item_kind", None),
            ),
            "work_item_id": getattr(active_run, "work_item_id", None),
            "closure_target_root_spec_id": getattr(
                active_run,
                "closure_target_root_spec_id",
                None,
            ),
            "closure_target_root_idea_id": getattr(
                active_run,
                "closure_target_root_idea_id",
                None,
            ),
            "active_since": _status_payload_value(getattr(active_run, "active_since", None)),
            "running_status_marker": getattr(active_run, "running_status_marker", None),
        }
    return payload


def _blocked_idle(
    *,
    process_running: bool,
    active_run_count: int,
    execution_queue_depth: int,
    planning_queue_depth: int,
    learning_queue_depth: int,
    closure_target_open: object,
    closure_target_blocked_by_lineage_work: object,
    planning_status_marker: str,
    current_failure_class: str | None,
) -> bool:
    return (
        process_running
        and active_run_count == 0
        and execution_queue_depth == 0
        and planning_queue_depth == 0
        and learning_queue_depth == 0
        and closure_target_open is True
        and closure_target_blocked_by_lineage_work is True
        and planning_status_marker == "### BLOCKED"
        and current_failure_class is not None
    )


def _render_baseline_manifest_lines(manifest: BaselineManifest | None) -> tuple[str, ...]:
    if manifest is None:
        return (
            "baseline_manifest_id: none",
            "baseline_seed_package_version: none",
        )
    return (
        f"baseline_manifest_id: {manifest.manifest_id}",
        f"baseline_seed_package_version: {manifest.seed_package_version}",
    )


def _render_compile_currentness_lines(
    currentness: CompiledPlanCurrentness | None,
    error: str | None,
) -> tuple[str, ...]:
    if currentness is None:
        return (
            "compile_input.mode_id: none",
            "compile_input.config_fingerprint: none",
            "compile_input.assets_fingerprint: none",
            f"compile_plan_currentness_error: {error or 'none'}",
        )
    lines = (
        f"compile_input.mode_id: {currentness.expected_fingerprint.mode_id}",
        (
            "compile_input.config_fingerprint: "
            f"{currentness.expected_fingerprint.config_fingerprint}"
        ),
        (
            "compile_input.assets_fingerprint: "
            f"{currentness.expected_fingerprint.assets_fingerprint}"
        ),
    )
    if currentness.persisted_fingerprint is None:
        persisted = (
            "persisted_compile_input.mode_id: none",
            "persisted_compile_input.config_fingerprint: none",
            "persisted_compile_input.assets_fingerprint: none",
        )
    else:
        persisted = (
            f"persisted_compile_input.mode_id: {currentness.persisted_fingerprint.mode_id}",
            (
                "persisted_compile_input.config_fingerprint: "
                f"{currentness.persisted_fingerprint.config_fingerprint}"
            ),
            (
                "persisted_compile_input.assets_fingerprint: "
                f"{currentness.persisted_fingerprint.assets_fingerprint}"
            ),
        )
    return lines + persisted


def _compiled_plan_currentness_value(
    currentness: CompiledPlanCurrentness | None,
    error: str | None,
) -> str:
    if currentness is not None:
        return currentness.state
    if error is not None:
        return "unknown"
    return "missing"


def _load_baseline_manifest_safe(paths: WorkspacePaths) -> BaselineManifest | None:
    try:
        return load_baseline_manifest(paths)
    except Exception:
        return None


def _load_compile_currentness(
    paths: WorkspacePaths,
) -> tuple[CompiledPlanCurrentness | None, str | None]:
    try:
        config = load_runtime_config(paths.runtime_root / "millrace.toml")
        return (
            inspect_workspace_plan_currentness(
                paths,
                config=config,
                assets_root=paths.runtime_root,
            ),
            None,
        )
    except Exception as exc:
        return None, str(exc)


def _json_files(directory: object) -> tuple[Path, ...]:
    path = Path(directory)
    if not path.exists():
        return ()
    return tuple(sorted(candidate for candidate in path.glob("*.json") if candidate.is_file()))


def _read_json_model(path: Path, model: type[Any]) -> Any | None:
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _workspace_relative(paths: WorkspacePaths, path: Path) -> str:
    try:
        return path.relative_to(paths.root).as_posix()
    except ValueError:
        return path.as_posix()


def _dict_value(value: dict[str, object], key: str) -> dict[str, object]:
    item = value.get(key)
    return item if isinstance(item, dict) else {}


def _count_value(counts: dict[str, object], key: str) -> int:
    value = counts.get(key)
    return value if isinstance(value, int) else 0


def _dict_items(value: dict[str, object], key: str) -> tuple[dict[str, object], ...]:
    items = value.get(key)
    if not isinstance(items, list):
        return ()
    return tuple(item for item in items if isinstance(item, dict))


def _status_value(value: object) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def _status_payload_value(value: object) -> object:
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return value


def _plane_key(value: str) -> object:
    from millrace_ai.contracts import Plane

    return Plane(value)


def _short_run_handle(run_id: object) -> str:
    value = _status_value(run_id)
    if not value.startswith("run-"):
        return value
    suffix = value.removeprefix("run-")
    if len(suffix) >= 12 and all(char in "0123456789abcdefABCDEF" for char in suffix):
        return suffix[:12]
    return value


__all__ = [
    "_print_status",
    "_print_status_json",
    "_print_statuses",
    "_render_status_lines",
    "_status_payload",
]
