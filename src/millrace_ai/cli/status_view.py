"""Status view loading and line rendering."""

from __future__ import annotations

import json
from typing import Any, Sequence

import typer

from millrace_ai.compiler import CompiledPlanCurrentness, inspect_workspace_plan_currentness
from millrace_ai.config import load_runtime_config
from millrace_ai.contracts import ClosureTargetState
from millrace_ai.events import RuntimeEventRecord, read_runtime_events
from millrace_ai.paths import WorkspacePaths
from millrace_ai.runtime.error_recovery import load_runtime_error_context
from millrace_ai.runtime.pause_state import pause_sources_label
from millrace_ai.runtime.usage_governance import load_usage_governance_state
from millrace_ai.runtime_lock import inspect_runtime_ownership_lock
from millrace_ai.state_store import load_snapshot
from millrace_ai.workspace.arbiter_state import list_open_closure_target_states
from millrace_ai.workspace.baseline import BaselineManifest, load_baseline_manifest
from millrace_ai.workspace.queue_selection import list_deferred_root_spec_ids


def _render_status_lines(paths: WorkspacePaths) -> tuple[str, ...]:
    snapshot = load_snapshot(paths)
    baseline_manifest = _load_baseline_manifest_safe(paths)
    currentness, currentness_error = _load_compile_currentness(paths)
    lock_status = inspect_runtime_ownership_lock(paths)
    process_running = snapshot.process_running and lock_status.state == "active"

    queue_depths = _queue_depths(paths)
    closure_status = _closure_target_status(paths)
    latest_runtime_error_report_path = _latest_runtime_error_report_path(paths)
    latest_operator_intervention = _latest_operator_intervention(paths)
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
        f"compiled_plan_currentness: {_compiled_plan_currentness_value(currentness, currentness_error)}",
        f"active_plane: {_status_value(snapshot.active_plane)}",
        f"active_stage: {_status_value(snapshot.active_stage)}",
        f"active_node_id: {_status_value(snapshot.active_node_id)}",
        f"active_stage_kind_id: {_status_value(snapshot.active_stage_kind_id)}",
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
        "latest_operator_intervention: "
        f"{_operator_intervention_status_value(latest_operator_intervention)}",
    ]
    lines.extend(_render_active_run_lines(snapshot.active_runs_by_plane))
    lines.extend(_render_baseline_manifest_lines(baseline_manifest))
    lines.extend(_render_compile_currentness_lines(currentness, currentness_error))
    lines.extend(_render_usage_governance_status_lines(paths))
    lines.extend(_render_closure_target_status_lines_from_status(closure_status))
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
    latest_runtime_error_report_path = _latest_runtime_error_report_path(paths)
    latest_operator_intervention = _latest_operator_intervention(paths)
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
        "compiled_plan_currentness": _compiled_plan_currentness_value(
            currentness,
            currentness_error,
        ),
        "active_plane": _status_payload_value(snapshot.active_plane),
        "active_stage": _status_payload_value(snapshot.active_stage),
        "active_node_id": snapshot.active_node_id,
        "active_stage_kind_id": snapshot.active_stage_kind_id,
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
        "latest_operator_intervention": _operator_intervention_payload(latest_operator_intervention),
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
            f"request_kind={_status_value(getattr(active_run, 'request_kind', None))} "
            f"work_item_kind={_status_value(getattr(active_run, 'work_item_kind', None))} "
            f"work_item_id={_status_value(getattr(active_run, 'work_item_id', None))} "
            f"run={_short_run_handle(getattr(active_run, 'run_id', None))}"
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
            "closure_target_open": "invalid",
            "closure_target_blocked_by_lineage_work": "invalid",
            "planning_root_specs_deferred_by_closure_target": "invalid",
            "closure_target_latest_verdict_path": None,
            "closure_target_latest_report_path": None,
        }
    if not open_targets:
        return {
            "closure_target_root_spec_id": None,
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
    return {
        "execution": len(tuple(paths.tasks_queue_dir.glob("*.md"))),
        "planning": (
            len(tuple(paths.specs_queue_dir.glob("*.md")))
            + len(tuple(paths.probes_queue_dir.glob("*.md")))
            + len(tuple(paths.incidents_incoming_dir.glob("*.md")))
        ),
        "learning": len(tuple(paths.learning_requests_queue_dir.glob("*.md"))),
    }


def _latest_runtime_error_report_path(paths: WorkspacePaths) -> str | None:
    context = load_runtime_error_context(paths)
    return context.report_path if context is not None else None


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
