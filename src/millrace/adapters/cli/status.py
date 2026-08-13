"""CLI adapter for read-only runtime status projection."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from typing import cast

from millrace.adapters.cli.context import (
    CliCommandError,
    OpenRuntimeContext,
    open_runtime_context,
    require_nonblank,
)
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
from millrace.contracts.runner import (
    RunnerResultEvidence,
    RunnerSessionCompletionDiagnostic,
    runner_result_evidence_bytes,
    runner_result_evidence_digest,
    runner_result_evidence_from_payload,
    runner_session_completion_diagnostic_bytes,
    runner_session_completion_diagnostic_from_payload,
)
from millrace.contracts.state import RuntimeState
from millrace.operator.dispatch import (
    list_ready_dispatch_candidates,
    run_may_start_while_dispatch_suspended,
)
from millrace.operator.intake import OperatorInputError
from millrace.operator.status import daemon_budget_projection, operator_status
from millrace.substrate._sqlite_relations import (
    completion_diagnostic_matches_current_authority,
    runner_result_refusal_chain,
    validate_completed_runner_evidence,
)
from millrace.substrate.errors import (
    CasDigestMismatch,
    CasObjectNotFound,
    SubstrateError,
)

_DAEMON_BUDGET_SESSION_MAX_ITEMS = 100
_REJECTED_EVIDENCE_MAX_BYTES = 64 * 1024
_REJECTED_DIAGNOSTIC_MAX_BYTES = 16 * 1024


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
    if command == "runs.follow":
        return _runs_follow(namespace)
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
            budgets = _daemon_budget_projections(runtime, state)
            ready_dispatch = list_ready_dispatch_candidates(state)
        except OperatorInputError as exc:
            raise _operator_error(command, exc) from exc
    finally:
        runtime.close()
    return success_result(
        command=command,
        code="status_projected",
        message="Status projected.",
        data=_status_projection(
            status,
            state=state,
            budgets=budgets,
            ready_dispatch=ready_dispatch,
        ),
    )


def _runs_list(namespace: object) -> CliSuccess:
    command = "runs.list"
    runtime = open_runtime_context(namespace, command=command)
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
        status = operator_status(state)
        budget_by_session = _budget_projection_by_session(runtime, state)
    finally:
        runtime.close()
    runs: list[dict[str, object]] = []
    for run in status.active_runs:
        projected = cast(dict[str, object], json_ready(run))
        run_id = str(projected["run_id"])
        projected["runner_session"] = runner_session_projection(
            state,
            run_id,
        )
        projected["may_start_while_dispatch_suspended"] = (
            run_may_start_while_dispatch_suspended(state, state.runs[run_id])
        )
        session = projected["runner_session"]
        if isinstance(session, dict):
            budget = budget_by_session.get(str(session["session_id"]))
            if budget is not None:
                session["budget"] = budget
        runs.append(projected)
    return success_result(
        command=command,
        code="runs_listed",
        message="Runs listed.",
        data={"runs": runs},
    )


def _runs_show(namespace: object) -> CliSuccess:
    command = "runs.show"
    run_id = str(getattr(namespace, "run_id"))
    runtime = open_runtime_context(namespace, command=command)
    try:
        state = runtime.store.load_runtime_state_for_rejected_result_inspection(
            runtime.cas_store,
            run_id,
        )
        budget_by_session = _budget_projection_by_session(runtime, state)
        run = state.runs.get(run_id)
        if run is None:
            raise CliCommandError(
                command=command,
                code="run_not_found",
                message="Run was not found.",
                exit_code=ExitCode.DOMAIN_REFUSAL,
                details={"run_id": run_id},
            )
        rejected_result = rejected_result_projection(
            runtime,
            state,
            run_id,
            include_rejected_evidence=bool(
                getattr(namespace, "include_rejected_evidence", False)
            ),
        )
    finally:
        runtime.close()
    activation = state.activations.get(run.activation_id)
    work_item = state.work_items.get(run.work_item_id)
    observed = any(
        observation.run_id == run_id
        for observation in state.runner_observations.values()
    )
    run_projection: dict[str, object] = {
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
        "runner_session": _runner_session_with_budget(
            state,
            run_id,
            budget_by_session,
        ),
    }
    if rejected_result is not None:
        run_projection["rejected_result"] = rejected_result
    return success_result(
        command=command,
        code="run_shown",
        message="Run shown.",
        data={
            "run": run_projection,
        },
    )


def _runs_cancel(namespace: object) -> CliSuccess:
    command = "runs.cancel"
    run_id = require_nonblank(
        str(getattr(namespace, "run_id")),
        option="RUN_ID",
        command=command,
    )
    request_id = require_nonblank(
        str(getattr(namespace, "input_id")),
        option="--input-id",
        command=command,
    )
    actor_id = require_nonblank(
        str(getattr(namespace, "actor_id", "local_operator")),
        option="--actor-id",
        command=command,
    )
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


def _runs_follow(namespace: object) -> CliSuccess:
    from millrace.substrate.runner_session_events import (
        RUNNER_SESSION_EVENT_READ_MAX_RECORDS,
        RunnerSessionEventStore,
        runner_session_event_store_path,
    )

    command = "runs.follow"
    run_id = require_nonblank(
        str(getattr(namespace, "run_id")),
        option="RUN_ID",
        command=command,
    )
    after_sequence = getattr(namespace, "after_sequence", 0)
    if type(after_sequence) is not int or after_sequence < 0:
        raise CliCommandError(
            command=command,
            code="invalid_after_sequence",
            message="--after-sequence must be a nonnegative integer.",
            exit_code=ExitCode.CLI_USAGE,
            details={"after_sequence": after_sequence},
        )
    max_events = min(
        _max_events(namespace, command=command),
        RUNNER_SESSION_EVENT_READ_MAX_RECORDS,
    )
    runtime = open_runtime_context(namespace, command=command)
    event_store = None
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
        budget_by_session = _budget_projection_by_session(runtime, state)
        run = state.runs.get(run_id)
        if run is None:
            raise CliCommandError(
                command=command,
                code="run_not_found",
                message="Run was not found.",
                exit_code=ExitCode.DOMAIN_REFUSAL,
                details={"run_id": run_id},
            )
        session = (
            None
            if run.current_session_id is None
            else state.runner_sessions.get(run.current_session_id)
        )
        event_path = runner_session_event_store_path(runtime.paths.db_path)
        events: list[dict[str, object]]
        gap: object | None
        if event_path.is_file() and max_events > 0 and session is not None:
            try:
                event_store = RunnerSessionEventStore.open(event_path)
                page = event_store.read(
                    run_id,
                    after_sequence=after_sequence,
                    limit=max_events,
                    session_id=session.session_id,
                )
            except (OSError, sqlite3.Error, TypeError, ValueError):
                events = []
                gap = {
                    "after_sequence": after_sequence,
                    "resumes_at_sequence": None,
                    "reason": "history_unavailable",
                }
                last_sequence = after_sequence
            else:
                if not page.stream_found:
                    events = []
                    gap = {
                        "after_sequence": after_sequence,
                        "resumes_at_sequence": None,
                        "reason": "history_unavailable",
                    }
                    last_sequence = after_sequence
                else:
                    events = [
                        cast(dict[str, object], json_ready(event.payload()))
                        for event in page.events
                    ]
                    gap = None if page.gap is None else json_ready(page.gap)
                    last_sequence = page.last_sequence
        else:
            events = []
            gap = None
            last_sequence = after_sequence
        completion = (
            None
            if session is None
            else state.runner_session_completions.get(session.session_id)
        )
        durable_final = {
            "session_id": None if session is None else session.session_id,
            "dispatch_generation": (
                None if session is None else session.dispatch_generation
            ),
            "session_state": None if session is None else session.state,
            "completion_persisted": completion is not None,
            "terminal_state": (
                None if completion is None else completion.terminal_state
            ),
            "application_persisted": (
                completion is not None
                and completion.application_input_id in state.receipts
            ),
        }
        next_after_sequence = (
            cast(int, events[-1]["sequence"]) if events else after_sequence
        )
    finally:
        if event_store is not None:
            event_store.close()
        runtime.close()
    return success_result(
        command=command,
        code="runner_session_events_followed",
        message="Runner session events projected.",
        data={
            "run_id": run_id,
            "after_sequence": after_sequence,
            "events": events,
            "gap": gap,
            "last_sequence": last_sequence,
            "next_after_sequence": next_after_sequence,
            "durable_final": durable_final,
            "runner_session": _runner_session_with_budget(
                state,
                run_id,
                budget_by_session,
            ),
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
            budget_by_session = _budget_projection_by_session(runtime, state)
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
        data={
            "run_id": run_id,
            "events": events,
            "dispatch_suspension": json_ready(status.dispatch_suspension),
            "queue_closures": json_ready(status.queue_closures),
            "runner_session": (
                None
                if run_id is None
                else _runner_session_with_budget(
                    state,
                    str(run_id),
                    budget_by_session,
                )
            ),
        },
    )


def _max_events(namespace: object, *, command: str) -> int:
    max_events = int(getattr(namespace, "max_events", 20))
    if max_events < 0:
        raise _operator_error(command, OperatorInputError("invalid_max_events"))
    return max_events


def _status_projection(
    status: object,
    *,
    state: RuntimeState | None = None,
    budgets: list[dict[str, object]] | None = None,
    ready_dispatch: object | None = None,
) -> dict[str, object]:
    runner_sessions = (
        []
        if state is None
        else [
            projection
            for run_id in sorted(state.runs)
            if (
                projection := runner_session_projection(state, run_id)
            )
            is not None
        ]
    )
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
        "dispatch_suspension": json_ready(
            getattr(status, "dispatch_suspension")
        ),
        "queue_closures": json_ready(getattr(status, "queue_closures")),
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
        "runner_sessions": runner_sessions,
        "daemon_budgets": [] if budgets is None else budgets,
        "ready_dispatch": _ready_dispatch_projection(ready_dispatch),
    }


def _ready_dispatch_projection(ready_dispatch: object | None) -> dict[str, object]:
    if ready_dispatch is None:
        return {"candidates": [], "diagnostics": []}
    return {
        "candidates": [
            json_ready(item) for item in getattr(ready_dispatch, "candidates", ())
        ],
        "diagnostics": [
            json_ready(item) for item in getattr(ready_dispatch, "diagnostics", ())
        ],
    }


def _budget_projection_by_session(
    runtime: OpenRuntimeContext,
    state: RuntimeState,
) -> dict[str, dict[str, object]]:
    projections: dict[str, dict[str, object]] = {}
    for session_id in state.runner_sessions:
        budget_id = runtime.store.daemon_budget_id_for_session(session_id)
        if budget_id is None:
            continue
        epoch = runtime.store.load_daemon_budget_epoch(budget_id)
        if epoch is not None:
            projection = daemon_budget_projection(epoch)
            projection["usage_evidence"] = _usage_evidence_projection(
                runtime,
                session_id,
            )
            projections[session_id] = projection
    return projections


def _daemon_budget_projections(
    runtime: OpenRuntimeContext,
    state: RuntimeState,
) -> list[dict[str, object]]:
    projections: list[dict[str, object]] = []
    for epoch in runtime.store.list_daemon_budget_epochs():
        projection = daemon_budget_projection(epoch)
        count, session_ids = runtime.store.daemon_budget_session_ids(
            epoch.budget_id,
            limit=_DAEMON_BUDGET_SESSION_MAX_ITEMS,
        )
        sessions: list[dict[str, object]] = []
        for session_id in session_ids:
            session = state.runner_sessions.get(session_id)
            if session is None:
                sessions.append(
                    {
                        "session_id": session_id,
                        "usage_evidence": {
                            "status": "contradictory",
                            "reason": "runner_usage_evidence_refused",
                        },
                    }
                )
                continue
            usage_evidence: dict[str, object]
            try:
                bound_budget_id = runtime.store.daemon_budget_id_for_session(
                    session_id
                )
            except ValueError:
                usage_evidence = {
                    "status": "contradictory",
                    "reason": "runner_usage_evidence_refused",
                }
            else:
                usage_evidence = (
                    _usage_evidence_projection(runtime, session_id)
                    if bound_budget_id == epoch.budget_id
                    else {
                        "status": "contradictory",
                        "reason": "runner_usage_evidence_refused",
                    }
                )
            sessions.append(
                {
                    "session_id": session.session_id,
                    "run_id": session.run_id,
                    "dispatch_generation": session.dispatch_generation,
                    "session_fencing_token": session.session_fencing_token,
                    "usage_evidence": usage_evidence,
                }
            )
        projection["runner_session_count"] = count
        projection["runner_sessions"] = sessions
        projection["omitted_runner_session_count"] = count - len(sessions)
        projections.append(projection)
    return projections


def _usage_evidence_projection(
    runtime: OpenRuntimeContext,
    session_id: str,
) -> dict[str, object]:
    try:
        usage = runtime.store.load_runner_session_usage(session_id)
    except ValueError:
        return {
            "status": "contradictory",
            "reason": "runner_usage_evidence_refused",
        }
    if usage is None:
        return {"status": "missing"}
    return {
        "status": "available",
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "observed_at": usage.observed_at,
        "final": usage.final,
    }


def rejected_result_projection(
    runtime: OpenRuntimeContext,
    state: RuntimeState,
    run_id: str,
    *,
    include_rejected_evidence: bool = False,
) -> dict[str, object] | None:
    run = state.runs.get(run_id)
    if run is None or run.current_session_id is None:
        return None
    session = state.runner_sessions.get(run.current_session_id)
    if session is None:
        return None
    completion = state.runner_session_completions.get(session.session_id)
    if completion is None:
        return None

    receipt = state.receipts.get(completion.application_input_id)
    evidence_digest = completion.runner_result_evidence_digest
    if evidence_digest is not None:
        if (
            completion.terminal_state != "completed"
            or completion.primary_cancellation_request_id is not None
            or receipt is None
            or receipt.accepted
        ):
            return None
        rejection_kind = "observation_refusal"
        application_status = "refused"
        application_input_id: str | None = completion.application_input_id
        adapter_error_kind = None
        kernel_refusal_reason = None
    elif (
        completion.terminal_state == "failed"
        and completion.adapter_error_kind is not None
        and completion.primary_cancellation_request_id is None
    ):
        rejection_kind = "adapter_error"
        application_status = "not_applicable"
        application_input_id = None
        adapter_error_kind = completion.adapter_error_kind
        kernel_refusal_reason = None
    else:
        return None

    evidence, evidence_status = _load_rejected_evidence(
        runtime,
        state,
        session.session_id,
        evidence_digest,
    )
    if evidence_digest is not None and evidence is not None:
        chain_valid, chain_reason = runner_result_refusal_chain(
            state,
            run_id=run_id,
            session_id=session.session_id,
            application_input_id=completion.application_input_id,
            evidence=evidence,
        )
        if not chain_valid:
            evidence = None
            evidence_status = "corrupt"
        else:
            kernel_refusal_reason = chain_reason
    diagnostic, diagnostic_status = _load_completion_diagnostic(
        runtime,
        state,
        session.session_id,
        completion.diagnostic_digest,
    )
    projection: dict[str, object] = {
        "rejection_kind": rejection_kind,
        "application_status": application_status,
        "session_id": session.session_id,
        "dispatch_generation": session.dispatch_generation,
        "application_input_id": application_input_id,
        "adapter_error_kind": adapter_error_kind,
        "kernel_refusal_reason": kernel_refusal_reason,
        "runner_result_evidence_digest": evidence_digest,
        "completion_diagnostic_digest": completion.diagnostic_digest,
        "evidence_status": evidence_status,
        "diagnostic_status": diagnostic_status,
        "marker": None,
        "artifact_candidate_present": None,
        "observation_candidate_present": None,
    }
    if evidence is not None:
        projection["marker"] = evidence.marker
        projection["artifact_candidate_present"] = evidence.artifact_payload is not None
        projection["observation_candidate_present"] = (
            evidence.observation_payload is not None
        )
    if include_rejected_evidence:
        if evidence is not None:
            projection["evidence"] = json_ready(evidence.payload())
        if diagnostic is not None:
            projection["diagnostic"] = json_ready(diagnostic)
    return projection


def _load_rejected_evidence(
    runtime: OpenRuntimeContext,
    state: RuntimeState,
    session_id: str,
    digest: str | None,
) -> tuple[RunnerResultEvidence | None, str]:
    if digest is None:
        return None, "not_present"
    payload, status = _load_bounded_cas_bytes(
        runtime,
        digest,
        max_bytes=_REJECTED_EVIDENCE_MAX_BYTES,
    )
    if payload is None:
        return None, status
    try:
        parsed = json.loads(payload.decode("utf-8"))
        evidence = runner_result_evidence_from_payload(parsed)
        if runner_result_evidence_bytes(evidence) != payload:
            return None, "corrupt"
        if runner_result_evidence_digest(evidence) != digest:
            return None, "digest_mismatch"
        validate_completed_runner_evidence(
            state,
            session_id=session_id,
            payload=payload,
        )
    except CasObjectNotFound:
        return None, "missing"
    except CasDigestMismatch:
        return None, "digest_mismatch"
    except (
        OSError,
        SubstrateError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
        RecursionError,
    ):
        return None, "corrupt"
    return evidence, "available"


def _load_completion_diagnostic(
    runtime: OpenRuntimeContext,
    state: RuntimeState,
    session_id: str,
    digest: str,
) -> tuple[Mapping[str, object] | None, str]:
    payload, status = _load_bounded_cas_bytes(
        runtime,
        digest,
        max_bytes=_REJECTED_DIAGNOSTIC_MAX_BYTES,
    )
    if payload is None:
        return None, status
    try:
        decoded = json.loads(payload.decode("utf-8"))
        diagnostic: RunnerSessionCompletionDiagnostic = (
            runner_session_completion_diagnostic_from_payload(decoded)
        )
        canonical = runner_session_completion_diagnostic_bytes(diagnostic)
        if canonical != payload or not completion_diagnostic_matches_current_authority(
            state,
            session_id=session_id,
            diagnostic=diagnostic,
        ):
            return None, "corrupt"
    except (
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
        RecursionError,
    ):
        return None, "corrupt"
    return diagnostic.diagnostic, "available"


def _load_bounded_cas_bytes(
    runtime: OpenRuntimeContext,
    digest: str,
    *,
    max_bytes: int,
) -> tuple[bytes | None, str]:
    if (
        not isinstance(digest, str)
        or len(digest) != 71
        or not digest.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in digest[7:])
    ):
        return None, "corrupt"
    object_path = runtime.paths.cas_path / "sha256" / digest[7:]
    try:
        if object_path.stat().st_size > max_bytes:
            return None, "corrupt"
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "corrupt"
    try:
        payload = runtime.cas_store.get_bytes(digest)
    except CasObjectNotFound:
        return None, "missing"
    except CasDigestMismatch:
        return None, "digest_mismatch"
    except (OSError, SubstrateError, TypeError, ValueError):
        return None, "corrupt"
    if len(payload) > max_bytes:
        return None, "corrupt"
    return payload, "available"


def _runner_session_with_budget(
    state: RuntimeState,
    run_id: str,
    budget_by_session: dict[str, dict[str, object]],
) -> dict[str, object] | None:
    projection = runner_session_projection(state, run_id)
    if projection is None:
        return None
    budget = budget_by_session.get(str(projection["session_id"]))
    if budget is not None:
        projection["budget"] = budget
    return projection


def runner_session_projection(
    state: RuntimeState,
    run_id: str,
) -> dict[str, object] | None:
    run = state.runs.get(run_id)
    if run is None or run.current_session_id is None:
        return None
    session = state.runner_sessions.get(run.current_session_id)
    if session is None:
        return None
    completion = state.runner_session_completions.get(session.session_id)
    cancellation = next(
        (
            item
            for item in state.runner_session_cancellation_requests.values()
            if item.session_id == session.session_id and item.primary
        ),
        None,
    )
    attempts = sorted(
        (
            item
            for item in state.runner_session_cancellation_attempts.values()
            if item.session_id == session.session_id
        ),
        key=lambda item: item.sequence,
    )
    last_attempt = attempts[-1] if attempts else None
    application_status = "not_completed"
    if completion is not None:
        if completion.runner_result_evidence_digest is None:
            application_status = "not_applicable"
        else:
            receipt = state.receipts.get(completion.application_input_id)
            if receipt is None:
                application_status = "pending"
            elif receipt.accepted:
                application_status = "applied"
            else:
                application_status = "refused"
    return {
        "session_id": session.session_id,
        "run_id": session.run_id,
        "dispatch_generation": session.dispatch_generation,
        "state": session.state,
        "adapter_kind": _selected_adapter_kind(state, run_id),
        "primary_cancellation_reason": (
            None if cancellation is None else cancellation.reason
        ),
        "cancellation_phase": (
            None
            if cancellation is None
            else (last_attempt.operation if last_attempt is not None else session.state)
        ),
        "cancellation_last_operation": (
            None if last_attempt is None else last_attempt.operation
        ),
        "cancellation_last_result": (
            None if last_attempt is None else last_attempt.result
        ),
        "cleanup_disposition": session.cleanup_disposition,
        "orphan_risk": (
            session.state == "lost"
            or session.cleanup_disposition == "orphan_risk"
        ),
        "completion_persisted": completion is not None,
        "completion_terminal_state": (
            None if completion is None else completion.terminal_state
        ),
        "completion_exit_kind": (
            None if completion is None else completion.exit_kind
        ),
        "application_persisted": (
            completion is not None
            and completion.application_input_id in state.receipts
        ),
        "application_status": application_status,
        "cooperative_cancel_grace_seconds": cooperative_cancel_grace_seconds,
        "terminate_grace_seconds": terminate_grace_seconds,
    }


def _selected_adapter_kind(state: RuntimeState, run_id: str) -> str | None:
    run = state.runs.get(run_id)
    if run is None:
        return None
    admitted = state.admitted_plans.get(
        run.run_ref.plan_ref.authority_fingerprint
    )
    if admitted is None:
        return None
    matches = tuple(
        binding.adapter_kind
        for binding in admitted.selected_plan.runner_bindings
        if binding.id == run.runner_binding_id
    )
    return matches[0] if len(matches) == 1 else None


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
