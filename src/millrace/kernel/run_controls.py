"""Pure eligibility for the exact, unstarted run boundary."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from millrace.contracts.state import RunRecord, RuntimeState


def run_hold_refusal(state: RuntimeState, run_id: str) -> str | None:
    control = state.run_execution_controls.get(run_id)
    if control is not None:
        if control.state == "unknown":
            return "run_aftermath_unknown"
        if control.state in {
            "paused",
            "pause_pending",
            "resume_pending",
            "recover_pending",
            "retired",
        }:
            return "run_execution_held"
    return None


def run_eligibility_refusal(state: RuntimeState, run: RunRecord) -> str | None:
    run_id = run.run_ref.run_id
    control = state.run_execution_controls.get(run_id)
    if control is not None and control.state == "unknown":
        return "run_aftermath_unknown"
    session = state.runner_sessions.get(run.current_session_id or "")
    if session is not None and session.state == "lost":
        return "run_aftermath_unknown"
    if any(item.run_id == run_id for item in state.runner_observations.values()):
        return "run_observed"
    if run.work_item_id in state.closed_work_items:
        return "run_work_closed"
    work = state.work_items.get(run.work_item_id)
    activation = state.activations.get(run.activation_id)
    if (
        work is None
        or activation is None
        or (
            work.ref.plan_ref != run.run_ref.plan_ref
            or activation.claimed_by_run_id != run_id
            or activation.plan_ref != run.run_ref.plan_ref
        )
    ):
        return "run_authority_superseded"
    if any(
        item.work_item_id == run.work_item_id for item in state.quarantines.values()
    ) or any(
        item.status == "active"
        and item.lineage_id == work.lineage_id
        and item.selected_plan_ref == run.run_ref.plan_ref
        for item in state.lineage_quarantines.values()
    ):
        return "run_quarantined"
    if any(
        item.source_run_id == run_id for item in state.operator_waits.values()
    ) or any(item.source_run_id == run_id for item in state.cooldown_waits.values()):
        return "run_wait_source"
    if any(
        item.session_id == run.current_session_id
        for item in state.runner_session_cancellation_requests.values()
    ):
        return "run_cancellation_in_progress"
    if run.current_session_id is None:
        if run.last_dispatch_generation != 0 or any(
            item.run_id == run_id for item in state.runner_sessions.values()
        ):
            return "run_session_contradiction"
    elif session is None or (
        session.run_id != run_id
        or session.dispatch_generation != run.last_dispatch_generation
    ):
        return "run_session_contradiction"
    else:
        if session.session_id in state.runner_session_completions:
            return "run_attempt_terminal"
        refusal = {
            "starting": "run_pause_unsupported_state",
            "running": "runner_pause_unsupported",
            "cancellation_requested": "run_cancellation_in_progress",
            "terminating": "run_cancellation_in_progress",
            "completed": "run_attempt_terminal",
            "interrupted": "run_attempt_terminal",
            "failed": "run_attempt_terminal",
            "lost": "run_aftermath_unknown",
        }.get(session.state)
        if refusal is not None:
            return refusal
        if (
            session.state != "created"
            or any(
                value is not None
                for value in (
                    session.start_intent_at,
                    session.started_at,
                    session.ended_at,
                    session.durable_locator_digest,
                )
            )
            or session.cleanup_disposition != "pending"
        ):
            return "run_pause_unsupported_state"
    admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
    if admitted is None or admitted.plan_ref != run.run_ref.plan_ref:
        return "run_selected_authority_missing"
    bindings = [
        binding
        for binding in admitted.selected_plan.runner_bindings
        if binding.id == run.runner_binding_id
    ]
    if len(bindings) != 1:
        return "runner_pause_unsupported"
    return None


def exact_run_target_refusal(state: RuntimeState, target: dict[str, Any]) -> str | None:
    run = state.runs.get(target["run_id"])
    if run is None:
        return "run_not_found"
    if (
        target["plan_fingerprint"] != str(run.run_ref.plan_ref.authority_fingerprint)
        or target["run_generation"] != run.run_ref.generation
        or target["run_fencing_token"] != run.run_ref.fencing_token
    ):
        return "run_target_mismatch"
    session = state.runner_sessions.get(run.current_session_id or "")
    actual = (
        None
        if session is None
        else {
            "session_id": session.session_id,
            "dispatch_generation": session.dispatch_generation,
            "session_fencing_token": session.session_fencing_token,
            "state": session.state,
        }
    )
    if (
        target["expected_session"] != actual
        or target["last_dispatch_generation"] != run.last_dispatch_generation
    ):
        return "run_session_target_mismatch"
    control = state.run_execution_controls.get(run.run_ref.run_id)
    revision = 0 if control is None else control.control_revision
    if target["expected_control_revision"] != revision:
        return "stale_control_revision"
    return None


def run_control_projection(state: RuntimeState, run_id: str) -> dict[str, Any]:
    control = state.run_execution_controls.get(run_id)
    run = state.runs[run_id]
    refusal = run_eligibility_refusal(state, run)
    session = state.runner_sessions.get(run.current_session_id or "")
    if control is not None and control.native is not None:
        execution_state = control.state
    elif refusal is None:
        execution_state = (
            "paused"
            if control is not None and control.state == "paused"
            else "runnable_unstarted"
        )
    elif refusal in {"run_aftermath_unknown", "run_session_contradiction"}:
        execution_state = "unknown"
    elif refusal == "run_attempt_terminal" and session is not None:
        execution_state = session.state
    elif session is not None and session.state in {
        "starting",
        "running",
        "cancellation_requested",
        "terminating",
    }:
        execution_state = session.state
    else:
        execution_state = refusal
    return {
        "control_revision": 0 if control is None else control.control_revision,
        "execution_hold": None if control is None else asdict(control),
        "execution_state": execution_state,
        "runner_activity": "not_started"
        if (control is None or control.native is None)
        and execution_state in {"paused", "runnable_unstarted"}
        else "unknown",
    }
