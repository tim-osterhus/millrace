"""Status view loading and line rendering."""

from __future__ import annotations

from typing import Sequence

import typer

from millrace_ai.compiler import CompiledPlanCurrentness, inspect_workspace_plan_currentness
from millrace_ai.config import load_runtime_config
from millrace_ai.paths import WorkspacePaths
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

    execution_queue_depth = len(tuple(paths.tasks_queue_dir.glob("*.md")))
    planning_queue_depth = len(tuple(paths.specs_queue_dir.glob("*.md"))) + len(
        tuple(paths.incidents_incoming_dir.glob("*.md"))
    )
    learning_queue_depth = len(tuple(paths.learning_requests_queue_dir.glob("*.md")))

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
        f"execution_queue_depth: {execution_queue_depth}",
        f"planning_queue_depth: {planning_queue_depth}",
        f"learning_queue_depth: {learning_queue_depth}",
        f"execution_status_marker: {snapshot.execution_status_marker}",
        f"planning_status_marker: {snapshot.planning_status_marker}",
        f"learning_status_marker: {snapshot.learning_status_marker}",
    ]
    lines.extend(_render_baseline_manifest_lines(baseline_manifest))
    lines.extend(_render_compile_currentness_lines(currentness, currentness_error))
    lines.extend(_render_usage_governance_status_lines(paths))
    lines.extend(_render_closure_target_status_lines(paths))
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


def _print_status(paths: WorkspacePaths) -> None:
    for line in _render_status_lines(paths):
        typer.echo(line)


def _print_statuses(paths_list: Sequence[WorkspacePaths]) -> None:
    for index, paths in enumerate(paths_list):
        if index > 0:
            typer.echo("")
        _print_status(paths)


def _render_closure_target_status_lines(paths: WorkspacePaths) -> tuple[str, ...]:
    open_targets = list_open_closure_target_states(paths)
    if len(open_targets) > 1:
        return (
            "closure_target_root_spec_id: invalid_multiple_open_targets",
            "closure_target_open: invalid",
            "closure_target_blocked_by_lineage_work: invalid",
            "planning_root_specs_deferred_by_closure_target: invalid",
            "closure_target_latest_verdict_path: none",
            "closure_target_latest_report_path: none",
        )
    if not open_targets:
        return (
            "closure_target_root_spec_id: none",
            "closure_target_open: none",
            "closure_target_blocked_by_lineage_work: none",
            "planning_root_specs_deferred_by_closure_target: 0",
            "closure_target_latest_verdict_path: none",
            "closure_target_latest_report_path: none",
        )

    target = open_targets[0]
    deferred_root_spec_ids = list_deferred_root_spec_ids(
        paths,
        open_root_spec_id=target.root_spec_id,
    )
    return (
        f"closure_target_root_spec_id: {target.root_spec_id}",
        f"closure_target_open: {'true' if target.closure_open else 'false'}",
        (
            "closure_target_blocked_by_lineage_work: "
            f"{'true' if target.closure_blocked_by_lineage_work else 'false'}"
        ),
        f"planning_root_specs_deferred_by_closure_target: {len(deferred_root_spec_ids)}",
        f"closure_target_latest_verdict_path: {_status_value(target.latest_verdict_path)}",
        f"closure_target_latest_report_path: {_status_value(target.latest_report_path)}",
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
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


__all__ = ["_print_status", "_print_statuses", "_render_status_lines"]
