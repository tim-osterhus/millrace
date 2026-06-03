"""Status data collection for the CLI status view."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.compilation.persistence import load_existing_plan
from millrace_ai.compiler import CompiledPlanCurrentness, inspect_workspace_plan_currentness
from millrace_ai.config import load_runtime_config
from millrace_ai.contracts import ClosureTargetState, Plane, RuntimeErrorContext
from millrace_ai.contracts.blueprint import (
    BlueprintCritiqueDocument,
    BlueprintDraftDocument,
    BlueprintEvaluationDocument,
    BlueprintPacketDocument,
    BlueprintPromotionRecord,
)
from millrace_ai.events import RuntimeEventRecord, read_runtime_events
from millrace_ai.paths import WorkspacePaths
from millrace_ai.runtime.error_recovery import load_runtime_error_context
from millrace_ai.runtime.runtime_effect_status import (
    latest_runtime_effect_status_metadata,
)
from millrace_ai.runtime.usage_governance import load_usage_governance_state
from millrace_ai.runtime_lock import inspect_runtime_ownership_lock
from millrace_ai.state_store import load_snapshot
from millrace_ai.workspace.arbiter_state import list_open_closure_target_states
from millrace_ai.workspace.baseline import BaselineManifest, load_baseline_manifest
from millrace_ai.workspace.queue_selection import list_deferred_root_spec_ids
from millrace_ai.workspace.work_inventory import family_counts, queue_depths_by_plane

from .models import StatusViewModel

_BUILTIN_STATUS_FAMILIES = {
    "task",
    "probe",
    "spec",
    "incident",
    "learning_request",
    "blueprint_draft",
}

_OPERATOR_INTERVENTION_EVENT_TYPES = {
    "work_item_cancelled",
    "blocked_task_archived",
    "task_superseded",
    "task_dependency_retargeted",
    "incident_resolved_by_operator",
    "incident_cancelled",
    "invalid_incident_artifact_archived",
}


def collect_status_view_model(paths: WorkspacePaths) -> StatusViewModel:
    """Collect read-only workspace status data for later rendering."""

    snapshot = load_snapshot(paths)
    baseline_manifest = _load_baseline_manifest_safe(paths)
    currentness, currentness_error = _load_compile_currentness(paths)
    lock_status = inspect_runtime_ownership_lock(paths)
    process_running = snapshot.process_running and lock_status.state == "active"
    queue_depths = _queue_depths(paths)
    closure_status = _closure_target_status(paths)
    latest_runtime_error_context = _latest_runtime_error_context(paths)
    latest_operator_intervention = _latest_operator_intervention(paths)
    usage_governance_state = load_usage_governance_state(paths)

    try:
        config_enabled = load_runtime_config(
            paths.runtime_root / "millrace.toml",
        ).usage_governance.enabled
    except Exception:
        config_enabled = usage_governance_state.enabled

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

    return StatusViewModel(
        paths=paths,
        snapshot=snapshot,
        baseline_manifest=baseline_manifest,
        compile_currentness=currentness,
        compile_currentness_error=currentness_error,
        runtime_ownership_lock=lock_status.state,
        process_running=process_running,
        queue_depths=queue_depths,
        closure_status=closure_status,
        blueprint_status=_blueprint_status(paths),
        latest_runtime_error_report_path=(
            latest_runtime_error_context.report_path
            if latest_runtime_error_context is not None
            else None
        ),
        latest_runtime_failure_origin=_failure_origin_value(latest_runtime_error_context),
        latest_operator_intervention=latest_operator_intervention,
        latest_runtime_effect=_latest_runtime_effect_metadata(
            paths,
            snapshot.last_stage_result_path,
        ),
        work_item_families=_work_item_family_status_payload(paths),
        usage_governance_config_enabled=config_enabled,
        usage_governance_state=usage_governance_state,
        blocked_idle=blocked_idle,
    )


def _latest_operator_intervention(paths: WorkspacePaths) -> RuntimeEventRecord | None:
    events = read_runtime_events(paths)
    for event in reversed(events):
        if event.event_type in _OPERATOR_INTERVENTION_EVENT_TYPES:
            return event
    return None


def _latest_runtime_effect_metadata(
    paths: WorkspacePaths,
    stage_result_path: str | None,
) -> dict[str, str]:
    return latest_runtime_effect_status_metadata(paths, stage_result_path)


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


def _queue_depths(paths: WorkspacePaths) -> dict[str, int]:
    depths = queue_depths_by_plane(paths, compiled_plan=_load_compiled_plan_safe(paths))
    return {
        "execution": depths.get(_plane_key("execution"), 0),
        "planning": depths.get(_plane_key("planning"), 0),
        "learning": depths.get(_plane_key("learning"), 0),
    }


def _load_compiled_plan_safe(paths: WorkspacePaths) -> CompiledRunPlan | None:
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
        "drafts": sorted(
            drafts,
            key=lambda item: (str(item["state"]), str(item["draft_id"])),
        ),
        "packets": sorted(
            packets,
            key=lambda item: (str(item["state"]), str(item["blueprint_id"])),
        ),
        "critiques": sorted(
            critiques,
            key=lambda item: (str(item["state"]), str(item["critique_id"])),
        ),
        "evaluations": sorted(evaluations, key=lambda item: str(item["evaluation_id"])),
        "promotions": sorted(promotions, key=lambda item: str(item["promotion_id"])),
    }


def _latest_runtime_error_context(paths: WorkspacePaths) -> RuntimeErrorContext | None:
    return load_runtime_error_context(paths)


def _failure_origin_value(context: object | None) -> str | None:
    if context is None:
        return None
    failure_origin = getattr(context, "failure_origin", None)
    enum_value = getattr(failure_origin, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return failure_origin if isinstance(failure_origin, str) else None


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


def _json_files(directory: str | Path) -> tuple[Path, ...]:
    path = Path(directory)
    if not path.exists():
        return ()
    return tuple(
        sorted(candidate for candidate in path.glob("*.json") if candidate.is_file())
    )


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


def _plane_key(value: str) -> Plane:
    return Plane(value)


__all__ = ["collect_status_view_model"]
