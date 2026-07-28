"""CLI adapter for read-only runtime status projection."""

from __future__ import annotations

from collections.abc import Iterable

from millrace.adapters.cli.context import CliCommandError, open_runtime_context
from millrace.adapters.cli.output import (
    CliSuccess,
    ExitCode,
    json_ready,
    success_result,
)
from millrace.adapters.cli.session_coordinator import (
    cooperative_cancel_grace_seconds,
    request_operator_cancellation,
    terminate_grace_seconds,
)
from millrace.operator.intake import OperatorInputError
from millrace.operator.status import operator_status


def handle_status_command(namespace: object) -> CliSuccess:
    command = str(getattr(namespace, "command", "status"))
    if command == "status":
        return _status(namespace)
    if command == "runs.list":
        return _runs_list(namespace)
    if command == "runs.show":
        return _runs_show(namespace)
    if command == "runs.cancel":
        return _runs_cancel(namespace)
    if command == "waits.list":
        return _waits_list(namespace)
    if command == "interventions.list":
        return _interventions_list(namespace)
    if command == "trace.show":
        return _trace_show(namespace)
    raise CliCommandError(
        command=command,
        code="command_not_implemented",
        message="Command is not implemented.",
        exit_code=ExitCode.DOMAIN_REFUSAL,
        details={},
    )


def _status(namespace: object) -> CliSuccess:
    command = "status"
    max_events = _max_events(namespace, command=command)
    runtime = open_runtime_context(namespace, command=command)
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
        try:
            status = operator_status(
                state,
                plan_fingerprint=getattr(namespace, "plan_fingerprint", None),
                max_events=max_events,
            )
        except OperatorInputError as exc:
            raise _operator_error(command, exc) from exc
    finally:
        runtime.close()
    return success_result(
        command=command,
        code="status_projected",
        message="Status projected.",
        data=_status_projection(status),
    )


def _runs_list(namespace: object) -> CliSuccess:
    command = "runs.list"
    runtime = open_runtime_context(namespace, command=command)
    try:
        status = operator_status(runtime.store.load_runtime_state(runtime.cas_store))
    finally:
        runtime.close()
    return success_result(
        command=command,
        code="runs_listed",
        message="Runs listed.",
        data={"runs": [json_ready(run) for run in status.active_runs]},
    )


def _runs_show(namespace: object) -> CliSuccess:
    command = "runs.show"
    run_id = str(getattr(namespace, "run_id"))
    runtime = open_runtime_context(namespace, command=command)
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
    finally:
        runtime.close()
    run = state.runs.get(run_id)
    if run is None:
        raise CliCommandError(
            command=command,
            code="run_not_found",
            message="Run was not found.",
            exit_code=ExitCode.DOMAIN_REFUSAL,
            details={"run_id": run_id},
        )
    activation = state.activations.get(run.activation_id)
    work_item = state.work_items.get(run.work_item_id)
    observed = any(
        observation.run_id == run_id
        for observation in state.runner_observations.values()
    )
    session = (
        None
        if run.current_session_id is None
        else state.runner_sessions.get(run.current_session_id)
    )
    cancellation = None
    if session is not None:
        cancellation = next(
            (
                item
                for item in state.runner_session_cancellation_requests.values()
                if item.session_id == session.session_id and item.primary
            ),
            None,
        )
    completion = (
        None
        if session is None
        else state.runner_session_completions.get(session.session_id)
    )
    return success_result(
        command=command,
        code="run_shown",
        message="Run shown.",
        data={
            "run": {
                "run_id": run.run_ref.run_id,
                "work_item_id": run.work_item_id,
                "activation_id": run.activation_id,
                "claim_id": run.run_ref.claim_id,
                "generation": run.run_ref.generation,
                "fencing_token": run.run_ref.fencing_token,
                "plan_fingerprint": run.run_ref.plan_ref.authority_fingerprint,
                "stage_kind_id": str(run.stage_kind_id),
                "runner_binding_id": str(run.runner_binding_id),
                "queue_family_id": (
                    None if work_item is None else str(work_item.queue_family_id)
                ),
                "graph_node_id": (
                    None if activation is None else activation.graph_node_id
                ),
                "observed": observed,
                "closed": run.work_item_id in state.closed_work_items,
                "runner_session": (
                    None
                    if session is None
                    else {
                        "session_id": session.session_id,
                        "dispatch_generation": session.dispatch_generation,
                        "state": session.state,
                        "cleanup_disposition": session.cleanup_disposition,
                        "primary_cancellation_reason": (
                            None if cancellation is None else cancellation.reason
                        ),
                        "completion_persisted": completion is not None,
                        "application_persisted": (
                            completion is not None
                            and completion.application_input_id in state.receipts
                        ),
                        "cooperative_cancel_grace_seconds": (
                            cooperative_cancel_grace_seconds
                        ),
                        "terminate_grace_seconds": terminate_grace_seconds,
                    }
                ),
            }
        },
    )


def _runs_cancel(namespace: object) -> CliSuccess:
    command = "runs.cancel"
    run_id = str(getattr(namespace, "run_id"))
    request_id = str(getattr(namespace, "input_id"))
    actor_id = str(getattr(namespace, "actor_id", "local_operator"))
    runtime = open_runtime_context(namespace, command=command)
    try:
        result = request_operator_cancellation(
            runtime,
            run_id=run_id,
            request_id=request_id,
            actor_id=actor_id,
        )
    finally:
        runtime.close()
    if not result.accepted:
        raise CliCommandError(
            command=command,
            code="runner_session_cancel_refused",
            message="Runner session cancellation was refused.",
            exit_code=ExitCode.DOMAIN_REFUSAL,
            details={"run_id": run_id, "input_id": request_id},
        )
    return success_result(
        command=command,
        code="runner_session_cancel_requested",
        message="Runner session cancellation requested.",
        data={
            "run_id": run_id,
            "session_id": result.session_id,
            "input_id": request_id,
            "reason": "operator_cancel_work",
            "source_kind": "operator",
        },
    )


def _waits_list(namespace: object) -> CliSuccess:
    command = "waits.list"
    runtime = open_runtime_context(namespace, command=command)
    try:
        status = operator_status(runtime.store.load_runtime_state(runtime.cas_store))
    finally:
        runtime.close()
    return success_result(
        command=command,
        code="waits_listed",
        message="Waits listed.",
        data={
            "operator_waits": [json_ready(wait) for wait in status.operator_waits],
            "cooldown_waits": [json_ready(wait) for wait in status.cooldown_waits],
        },
    )


def _interventions_list(namespace: object) -> CliSuccess:
    command = "interventions.list"
    runtime = open_runtime_context(namespace, command=command)
    try:
        status = operator_status(runtime.store.load_runtime_state(runtime.cas_store))
    finally:
        runtime.close()
    return success_result(
        command=command,
        code="interventions_listed",
        message="Interventions listed.",
        data={
            "interventions": [
                json_ready(intervention) for intervention in status.interventions
            ]
        },
    )


def _trace_show(namespace: object) -> CliSuccess:
    command = "trace.show"
    max_events = _max_events(namespace, command=command)
    run_id = getattr(namespace, "run_id", None)
    runtime = open_runtime_context(namespace, command=command)
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
        try:
            status = operator_status(state, max_events=max_events)
        except OperatorInputError as exc:
            raise _operator_error(command, exc) from exc
    finally:
        runtime.close()

    if run_id is None:
        events = [json_ready(event) for event in status.recent_events]
    else:
        run_id_value = str(run_id)
        run = state.runs.get(run_id_value)
        if run is None:
            raise CliCommandError(
                command=command,
                code="run_not_found",
                message="Run was not found.",
                exit_code=ExitCode.DOMAIN_REFUSAL,
                details={"run_id": run_id_value},
            )
        events = [
            _event_projection(
                record,
                run_id_override=(
                    run_id_value
                    if getattr(record, "input_id") == run.created_by_input_id
                    else None
                ),
            )
            for record in _event_rows(state.governance_events, state.traces)
            if getattr(record, "run_id") == run_id_value
            or getattr(record, "input_id") == run.created_by_input_id
        ]
        events = events[-max_events:] if max_events else []
    return success_result(
        command=command,
        code="trace_projected",
        message="Trace projected.",
        data={"run_id": run_id, "events": events},
    )


def _max_events(namespace: object, *, command: str) -> int:
    max_events = int(getattr(namespace, "max_events", 20))
    if max_events < 0:
        raise _operator_error(command, OperatorInputError("invalid_max_events"))
    return max_events


def _status_projection(status: object) -> dict[str, object]:
    return {
        "selected_plan": json_ready(getattr(status, "selected_plan")),
        "known_plans": [json_ready(item) for item in getattr(status, "known_plans")],
        "active_package_pins": [
            json_ready(item) for item in getattr(status, "active_package_pins")
        ],
        "queue_families": [
            json_ready(item) for item in getattr(status, "queue_families")
        ],
        "partitions": [json_ready(item) for item in getattr(status, "partitions")],
        "stage_kinds": [json_ready(item) for item in getattr(status, "stage_kinds")],
        "active_runs": [json_ready(item) for item in getattr(status, "active_runs")],
        "artifacts": [json_ready(item) for item in getattr(status, "artifacts")],
        "effects": [json_ready(item) for item in getattr(status, "effects")],
        "generated_work": [
            json_ready(item) for item in getattr(status, "generated_work")
        ],
        "joins": [json_ready(item) for item in getattr(status, "joins")],
        "pause": json_ready(getattr(status, "pause")),
        "quarantines": [json_ready(item) for item in getattr(status, "quarantines")],
        "recovery_attempts": [
            json_ready(item) for item in getattr(status, "recovery_attempts")
        ],
        "cooldown_waits": [
            json_ready(item) for item in getattr(status, "cooldown_waits")
        ],
        "counters": [json_ready(item) for item in getattr(status, "counters")],
        "interventions": [
            json_ready(item) for item in getattr(status, "interventions")
        ],
        "operator_waits": [
            json_ready(item) for item in getattr(status, "operator_waits")
        ],
        "closure_targets": [
            json_ready(item) for item in getattr(status, "closure_targets")
        ],
        "closure_evaluations": [
            json_ready(item) for item in getattr(status, "closure_evaluations")
        ],
        "closure_remediations": [
            json_ready(item) for item in getattr(status, "closure_remediations")
        ],
        "closure_blocks": [
            json_ready(item) for item in getattr(status, "closure_blocks")
        ],
        "recent_events": [
            json_ready(item) for item in getattr(status, "recent_events")
        ],
    }


def _event_rows(
    governance_events: Iterable[object],
    traces: Iterable[object],
) -> list[object]:
    return [*governance_events, *traces]


def _event_projection(
    record: object,
    *,
    run_id_override: str | None = None,
) -> dict[str, object]:
    return {
        "record_id": getattr(record, "record_id"),
        "input_id": getattr(record, "input_id"),
        "input_kind": getattr(record, "input_kind"),
        "input_family": getattr(record, "input_family"),
        "disposition": getattr(record, "disposition"),
        "plan_fingerprint": getattr(record, "plan_fingerprint"),
        "work_item_id": getattr(record, "work_item_id"),
        "run_id": run_id_override or getattr(record, "run_id"),
        "action_id": (
            None
            if getattr(record, "action_id") is None
            else str(getattr(record, "action_id"))
        ),
        "authority_source": getattr(record, "authority_source"),
        "refusal_reason": getattr(record, "refusal_reason"),
    }


def _operator_error(command: str, exc: OperatorInputError) -> CliCommandError:
    return CliCommandError(
        command=command,
        code=exc.reason,
        message="Operator input was refused.",
        exit_code=ExitCode.DOMAIN_REFUSAL,
        details={"reason": exc.reason},
    )
