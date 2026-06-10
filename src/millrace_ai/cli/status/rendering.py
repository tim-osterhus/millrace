"""Rendering for CLI status view models."""

from __future__ import annotations

from typing import Any

from millrace_ai.compiler import CompiledPlanCurrentness
from millrace_ai.contracts import Plane
from millrace_ai.events import RuntimeEventRecord
from millrace_ai.runtime.pause_state import pause_sources_label
from millrace_ai.workspace.baseline import BaselineManifest

from .models import StatusViewModel

_LATEST_RUNTIME_EFFECT_STATUS_KEYS = (
    "latest_runtime_effect_handler_id",
    "latest_runtime_effect_decision",
    "latest_runtime_effect_failure_class",
    "latest_runtime_effect_failure_message",
    "latest_runtime_effect_mutation_phase",
    "latest_runtime_effect_failure_policy_id",
    "latest_runtime_effect_recovery_action",
)


def render_status_lines(view_model: StatusViewModel) -> tuple[str, ...]:
    snapshot = view_model.snapshot
    queue_depths = view_model.queue_depths

    lines = [
        f"workspace: {view_model.paths.root}",
        f"runtime_mode: {snapshot.runtime_mode.value}",
        f"process_running: {'true' if view_model.process_running else 'false'}",
        f"runtime_ownership_lock: {view_model.runtime_ownership_lock}",
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
        "compiled_plan_currentness: "
        f"{_compiled_plan_currentness_value(view_model)}",
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
        f"blocked_idle: {'true' if view_model.blocked_idle else 'false'}",
        "latest_runtime_error_report_path: "
        f"{_status_value(view_model.latest_runtime_error_report_path)}",
        "latest_runtime_failure_origin: "
        f"{_status_value(view_model.latest_runtime_failure_origin)}",
        "latest_operator_intervention: "
        f"{_operator_intervention_status_value(view_model.latest_operator_intervention)}",
    ]
    lines.extend(_render_lane_lines(snapshot.lanes_by_id))
    lines.extend(_render_active_run_lines(snapshot.active_runs_by_plane))
    lines.extend(_render_queue_depths_by_family_lines(view_model.queue_depths_by_family))
    lines.extend(_render_baseline_manifest_lines(view_model.baseline_manifest))
    lines.extend(_render_compile_currentness_lines(view_model))
    lines.extend(_render_latest_runtime_effect_lines(view_model.latest_runtime_effect))
    lines.extend(_render_usage_governance_status_lines(view_model))
    lines.extend(_render_closure_target_status_lines(view_model.closure_status))
    lines.extend(_render_work_item_family_status_lines(view_model.work_item_families))
    lines.extend(_render_blueprint_status_lines(view_model.blueprint_status))
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


def status_payload(view_model: StatusViewModel) -> dict[str, Any]:
    snapshot = view_model.snapshot
    queue_depths = view_model.queue_depths
    active_run_count = len(snapshot.active_runs_by_plane)
    return {
        "workspace": str(view_model.paths.root),
        "runtime_mode": snapshot.runtime_mode.value,
        "process_running": view_model.process_running,
        "queue_depths_by_family": view_model.queue_depths_by_family,
        "runtime_ownership_lock": view_model.runtime_ownership_lock,
        "paused": snapshot.paused,
        "pause_sources": pause_sources_label(snapshot),
        "stop_requested": snapshot.stop_requested,
        "active_mode_id": snapshot.active_mode_id,
        "compiled_plan_id": snapshot.compiled_plan_id,
        "compiled_plan_fingerprint": snapshot.compiled_plan_fingerprint,
        "pending_compiled_plan_id": snapshot.pending_compiled_plan_id,
        "pending_compiled_plan_fingerprint": snapshot.pending_compiled_plan_fingerprint,
        "pending_compiled_plan_path": snapshot.pending_compiled_plan_path,
        "compiled_plan_currentness": _compiled_plan_currentness_value(view_model),
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
        "blocked_idle": view_model.blocked_idle,
        "current_failure_class": snapshot.current_failure_class,
        "latest_runtime_error_report_path": view_model.latest_runtime_error_report_path,
        "latest_runtime_failure_origin": view_model.latest_runtime_failure_origin,
        "lanes_by_id": _lanes_payload(snapshot.lanes_by_id),
        "active_runs_by_plane": _active_runs_payload(snapshot.active_runs_by_plane),
        "latest_operator_intervention": _operator_intervention_payload(
            view_model.latest_operator_intervention,
        ),
        "latest_runtime_effect": view_model.latest_runtime_effect or None,
        "work_item_families": view_model.work_item_families,
        "blueprints": view_model.blueprint_status,
        **view_model.closure_status,
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


def _operator_intervention_payload(
    event: RuntimeEventRecord | None,
) -> dict[str, object] | None:
    if event is None:
        return None
    return {
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        "work_item_kind": event.data.get("work_item_kind"),
        "work_item_id": event.data.get("work_item_id"),
        "destination_path": event.data.get("destination_path"),
    }


def _render_latest_runtime_effect_lines(payload: dict[str, str]) -> tuple[str, ...]:
    if not payload:
        return ()
    return tuple(
        f"{key}: {payload[key]}"
        for key in _LATEST_RUNTIME_EFFECT_STATUS_KEYS
        if key in payload
    )


def _render_closure_target_status_lines(
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
        "closure_target_latest_verdict_path: "
        f"{_status_value(status['closure_target_latest_verdict_path'])}",
        "closure_target_latest_report_path: "
        f"{_status_value(status['closure_target_latest_report_path'])}",
    )


def _render_usage_governance_status_lines(
    view_model: StatusViewModel,
) -> tuple[str, ...]:
    state = view_model.usage_governance_state
    lines = [
        (
            "usage_governance_enabled: "
            f"{'true' if view_model.usage_governance_config_enabled else 'false'}"
        ),
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
        lines.append(
            "usage_governance_subscription_detail: "
            f"{state.subscription_quota_status.detail}"
        )
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


def _render_queue_depths_by_family_lines(
    depths: dict[str, int],
) -> tuple[str, ...]:
    if not depths:
        return ()
    return tuple(
        f"queue_depth_{family_id}: {depth}"
        for family_id, depth in sorted(depths.items())
    )


def _render_work_item_family_status_lines(
    items: list[dict[str, object]],
) -> tuple[str, ...]:
    lines: list[str] = []
    for item in items:
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


def _render_baseline_manifest_lines(
    manifest: BaselineManifest | None,
) -> tuple[str, ...]:
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
    view_model: StatusViewModel,
) -> tuple[str, ...]:
    currentness = view_model.compile_currentness
    error = view_model.compile_currentness_error
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


def _compiled_plan_currentness_value(view_model: StatusViewModel) -> str:
    currentness: CompiledPlanCurrentness | None = view_model.compile_currentness
    if currentness is not None:
        return currentness.state
    if view_model.compile_currentness_error is not None:
        return "unknown"
    return "missing"


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


def _plane_key(value: str) -> Plane:
    return Plane(value)


def _short_run_handle(run_id: object) -> str:
    value = _status_value(run_id)
    if not value.startswith("run-"):
        return value
    suffix = value.removeprefix("run-")
    if len(suffix) >= 12 and all(char in "0123456789abcdefABCDEF" for char in suffix):
        return suffix[:12]
    return value


__all__ = ["render_status_lines", "status_payload"]
