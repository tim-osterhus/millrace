"""CLI adapter for read-only dispatch projection."""

from __future__ import annotations

import time
from uuid import uuid4

from millrace.adapters.cli.context import (
    CliCommandError,
    command_input_id,
    decide_apply_persist,
    open_runtime_context,
    optional_claim_id,
)
from millrace.adapters.cli.output import (
    CliSuccess,
    ExitCode,
    json_ready,
    success_result,
)
from millrace.contracts.transition import ClaimWork, CreateRunnerSession
from millrace.operator.dispatch import (
    DispatchProjectionError,
    build_dispatch_envelope_for_run,
)


def handle_dispatch_command(namespace: object) -> CliSuccess:
    command = str(getattr(namespace, "command", "dispatch"))
    if command == "dispatch.claim":
        return _claim(namespace)
    if command == "dispatch.show":
        return _show(namespace)
    raise CliCommandError(
        command=command,
        code="command_not_implemented",
        message="Command is not implemented.",
        exit_code=ExitCode.DOMAIN_REFUSAL,
        details={},
    )


def _claim(namespace: object) -> CliSuccess:
    command = "dispatch.claim"
    activation_id = str(getattr(namespace, "activation_id"))
    input_id_value = command_input_id(
        namespace,
        command=command,
        payload={"activation_id": activation_id},
    )
    claim_id_value = optional_claim_id(namespace, command=command)
    runtime = open_runtime_context(namespace, command=command)
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
        transition_input = ClaimWork(input_id_value, activation_id=activation_id)
        decision, next_state = decide_apply_persist(
            runtime,
            state,
            transition_input,
            command=command,
            claim_id_value=claim_id_value,
        )
        run_id = _created_run_id(next_state, input_id_value)
        if run_id is None:
            raise CliCommandError(
                command=command,
                code="claimed_run_missing",
                message="Claim was accepted but no created run was persisted.",
                exit_code=ExitCode.INTERNAL_ERROR,
                details={"input_id": input_id_value},
            )
        run = next_state.runs[run_id]
        if run.current_session_id is None:
            session_id = f"session-{uuid4().hex}"
            _, next_state = decide_apply_persist(
                runtime,
                next_state,
                CreateRunnerSession(
                    f"cli:dispatch.claim:session:{run_id}",
                    run_ref=run.run_ref,
                    session_id=session_id,
                    session_fencing_token=f"session-fence-{uuid4().hex}",
                    created_at=time.time_ns(),
                    explicit_retry_intent=False,
                ),
                command=command,
                claim_id_value=run.run_ref.claim_id,
            )
        envelope = build_dispatch_envelope_for_run(state=next_state, run_id=run_id)
    finally:
        runtime.close()
    run = next_state.runs[run_id]
    work_item = next_state.work_items[run.work_item_id]
    return success_result(
        command=command,
        code="work_claimed",
        message="Work claimed.",
        data={
            "input_id": input_id_value,
            "accepted": decision.accepted,
            "plan_fingerprint": run.run_ref.plan_ref.authority_fingerprint,
            "queue_family_id": str(work_item.queue_family_id),
            "work_item_id": run.work_item_id,
            "activation_id": run.activation_id,
            "run_id": run_id,
            "claim_id": run.run_ref.claim_id,
            "fencing_token": run.run_ref.fencing_token,
            "transition_disposition": decision.disposition,
            "dispatch_envelope": json_ready(envelope.payload()),
        },
    )


def _show(namespace: object) -> CliSuccess:
    command = "dispatch.show"
    run_id = str(getattr(namespace, "run_id"))
    runtime = open_runtime_context(namespace, command=command)
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
        try:
            envelope = build_dispatch_envelope_for_run(state=state, run_id=run_id)
        except DispatchProjectionError as exc:
            raise _projection_error(command, exc) from exc
    finally:
        runtime.close()
    return success_result(
        command=command,
        code="dispatch_shown",
        message="Dispatch envelope shown.",
        data={
            "run_id": run_id,
            "dispatch_envelope": json_ready(envelope.payload()),
        },
    )


def _projection_error(command: str, exc: DispatchProjectionError) -> CliCommandError:
    return CliCommandError(
        command=command,
        code=exc.reason,
        message=exc.message,
        exit_code=ExitCode.DOMAIN_REFUSAL,
        details=dict(exc.details),
    )


def _created_run_id(state: object, input_id_value: str) -> str | None:
    runs = getattr(state, "runs")
    return next(
        (
            run.run_ref.run_id
            for run in sorted(
                runs.values(),
                key=lambda item: item.run_ref.run_id,
            )
            if run.created_by_input_id == input_id_value
        ),
        None,
    )
