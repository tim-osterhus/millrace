"""Closure-target mutation paths for arbiter-driven completion results."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.contracts import ClosureTargetState, Plane, StageResultEnvelope
from millrace_ai.errors import QueueStateError
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.state_store import (
    load_recovery_counters,
    save_snapshot,
)
from millrace_ai.workspace.arbiter_state import load_closure_target_state, save_closure_target_state

from .handoff_incidents import enqueue_handoff_incident

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


def apply_closure_target_router_decision(
    engine: RuntimeEngine,
    decision: RouterDecision,
    stage_result: StageResultEnvelope,
) -> None:
    assert engine.snapshot is not None
    target = _load_target_for_closure_result(engine, stage_result)
    target_update = {
        "latest_verdict_path": _existing_workspace_artifact(
            engine,
            _metadata_string(stage_result, "preferred_verdict_path"),
        ),
        "latest_report_path": _canonicalize_arbiter_report(engine, stage_result),
        "last_arbiter_run_id": stage_result.run_id,
        "closure_blocked_by_lineage_work": False,
        "blocking_work_ids": (),
    }

    if decision.action is RouterAction.IDLE:
        updated_target = target.model_copy(
            update={
                **target_update,
                "closure_open": False,
                "closed_at": engine._now(),
            }
        )
        save_closure_target_state(engine.paths, updated_target)
        engine.snapshot = engine.snapshot.model_copy(
            update={
                "active_plane": None,
                "active_stage": None,
                "active_node_id": None,
                "active_stage_kind_id": None,
                "active_run_id": None,
                "active_work_item_kind": None,
                "active_work_item_id": None,
                "active_since": None,
                "current_failure_class": None,
                "troubleshoot_attempt_count": 0,
                "mechanic_attempt_count": 0,
                "fix_cycle_count": 0,
                "consultant_invocations": 0,
                "execution_status_marker": "### IDLE",
                "planning_status_marker": "### IDLE",
                "updated_at": engine._now(),
            }
        )
        save_snapshot(engine.paths, engine.snapshot)
        engine._set_plane_status_marker(
            plane=Plane.EXECUTION,
            marker="### IDLE",
            run_id=None,
            source="closure_idle",
        )
        engine._set_plane_status_marker(
            plane=Plane.PLANNING,
            marker="### IDLE",
            run_id=stage_result.run_id,
            source="closure_idle",
        )
        engine.counters = load_recovery_counters(engine.paths)
        return

    if decision.action is RouterAction.HANDOFF:
        updated_target = target.model_copy(
            update={
                **target_update,
                "closure_open": True,
                "closed_at": None,
            }
        )
        save_closure_target_state(engine.paths, updated_target)
        if decision.create_incident:
            enqueue_handoff_incident(engine, decision=decision, stage_result=stage_result)
        engine.snapshot = engine.snapshot.model_copy(
            update={
                "active_plane": None,
                "active_stage": None,
                "active_node_id": None,
                "active_stage_kind_id": None,
                "active_run_id": None,
                "active_work_item_kind": None,
                "active_work_item_id": None,
                "active_since": None,
                "current_failure_class": decision.failure_class,
                "troubleshoot_attempt_count": 0,
                "mechanic_attempt_count": 0,
                "fix_cycle_count": 0,
                "consultant_invocations": 0,
                "updated_at": engine._now(),
            }
        )
        save_snapshot(engine.paths, engine.snapshot)
        engine.counters = load_recovery_counters(engine.paths)
        return

    if decision.action is RouterAction.BLOCKED:
        updated_target = target.model_copy(
            update={
                **target_update,
                "closure_open": True,
                "closed_at": None,
            }
        )
        save_closure_target_state(engine.paths, updated_target)
        engine.snapshot = engine.snapshot.model_copy(
            update={
                "active_plane": None,
                "active_stage": None,
                "active_node_id": None,
                "active_stage_kind_id": None,
                "active_run_id": None,
                "active_work_item_kind": None,
                "active_work_item_id": None,
                "active_since": None,
                "current_failure_class": decision.failure_class,
                "troubleshoot_attempt_count": 0,
                "mechanic_attempt_count": 0,
                "fix_cycle_count": 0,
                "consultant_invocations": 0,
                "updated_at": engine._now(),
            }
        )
        save_snapshot(engine.paths, engine.snapshot)
        engine.counters = load_recovery_counters(engine.paths)
        return

    raise ValueError(f"Unsupported closure-target router action: {decision.action.value}")


def _metadata_string(stage_result: StageResultEnvelope, key: str) -> str | None:
    value = stage_result.metadata.get(key)
    return value if isinstance(value, str) and value else None


def _load_target_for_closure_result(
    engine: RuntimeEngine,
    stage_result: StageResultEnvelope,
) -> ClosureTargetState:
    root_spec_id = _metadata_string(stage_result, "closure_target_root_spec_id")
    if root_spec_id is None:
        raise QueueStateError("closure_target_root_spec_id is required for closure-target results")
    return load_closure_target_state(engine.paths, root_spec_id=root_spec_id)


def _existing_workspace_artifact(engine: RuntimeEngine, candidate: str | None) -> str | None:
    if not candidate:
        return None
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = engine.paths.root / path
    if not path.exists():
        return None
    try:
        return str(path.relative_to(engine.paths.root))
    except ValueError:
        return str(path)


def _canonicalize_arbiter_report(engine: RuntimeEngine, stage_result: StageResultEnvelope) -> str | None:
    report_artifact = stage_result.report_artifact
    if report_artifact is None:
        return None
    source_path = Path(report_artifact).expanduser()
    if not source_path.is_absolute():
        source_path = engine.paths.root / source_path
    if not source_path.exists():
        return None
    destination = engine.paths.arbiter_reports_dir / f"{stage_result.run_id}.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, destination)
    return str(destination.relative_to(engine.paths.root))


__all__ = ["apply_closure_target_router_decision"]
