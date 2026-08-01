"""CLI adapter for selected queue-family enqueue and listing operations."""

from __future__ import annotations

import sys
from pathlib import Path

from millrace.adapters.cli.context import (
    CliCommandError,
    command_input_id,
    decide_apply_persist,
    open_runtime_context,
    parse_json_payload,
    require_nonblank,
)
from millrace.adapters.cli.output import (
    CliSuccess,
    ExitCode,
    json_ready,
    success_result,
)
from millrace.contracts.transition import CancelQueuedLineage, CancelQueuedWork
from millrace.operator.intake import (
    OperatorEnqueueInput,
    OperatorInputError,
    build_enqueue_work,
)
from millrace.operator.status import operator_status


def handle_queue_command(namespace: object) -> CliSuccess:
    command = str(getattr(namespace, "command", "queue"))
    if command == "queue.enqueue":
        return _enqueue(namespace)
    if command == "queue.list":
        return _list(namespace)
    if command == "queue.cancel":
        return _cancel(namespace)
    if command == "queue.cancel-lineage":
        return _cancel_lineage(namespace)
    raise CliCommandError(
        command=command,
        code="command_not_implemented",
        message="Command is not implemented.",
        exit_code=ExitCode.DOMAIN_REFUSAL,
        details={},
    )


def _enqueue(namespace: object) -> CliSuccess:
    command = "queue.enqueue"
    payload = _payload_from_sources(namespace, command=command)
    queue_family_id = str(getattr(namespace, "queue_family"))
    runtime = open_runtime_context(namespace, command=command)
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
        default_plan_fingerprint = (
            None
            if state.default_plan_ref is None
            else state.default_plan_ref.authority_fingerprint
        )
        input_id_value = command_input_id(
            namespace,
            command=command,
            payload={
                "queue_family_id": queue_family_id,
                "payload": payload,
                "plan_fingerprint": default_plan_fingerprint,
            },
        )
        try:
            transition_input = build_enqueue_work(
                state,
                OperatorEnqueueInput(
                    input_id=input_id_value,
                    queue_family_id=queue_family_id,
                    payload=payload,
                    plan_fingerprint=getattr(namespace, "plan_fingerprint", None),
                ),
            )
        except OperatorInputError as exc:
            raise _operator_error(command, exc) from exc
        decision, next_state = decide_apply_persist(
            runtime,
            state,
            transition_input,
            command=command,
        )
    finally:
        runtime.close()

    work_item_id = _created_work_item_id(next_state, input_id_value)
    activation_id = _created_activation_id(next_state, input_id_value)
    plan_fingerprint = (
        None
        if next_state.default_plan_ref is None
        else next_state.default_plan_ref.authority_fingerprint
    )
    return success_result(
        command=command,
        code="work_enqueued",
        message="Work enqueued.",
        data={
            "input_id": input_id_value,
            "accepted": decision.accepted,
            "plan_fingerprint": plan_fingerprint,
            "queue_family_id": queue_family_id,
            "work_item_id": work_item_id,
            "activation_id": activation_id,
            "transition_disposition": decision.disposition,
        },
    )


def _list(namespace: object) -> CliSuccess:
    command = "queue.list"
    runtime = open_runtime_context(namespace, command=command)
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
        status = operator_status(state)
    finally:
        runtime.close()
    return success_result(
        command=command,
        code="queue_families_listed",
        message="Queue families listed.",
        data={
            "queue_families": [
                {
                    "queue_family_id": family.queue_family_id,
                    "external_enqueue": family.external_enqueue,
                    "display_name": family.display_name,
                    "description": family.description,
                    "ready_count": family.ready_count,
                    "active_count": family.active_count,
                    "closed_count": family.closed_count,
                    "quarantined_count": family.quarantined_count,
                    "operator_wait_count": family.operator_wait_count,
                }
                for family in status.queue_families
            ]
        },
    )


def _cancel(namespace: object) -> CliSuccess:
    command = "queue.cancel"
    transition_input = CancelQueuedWork(
        require_nonblank(
            str(getattr(namespace, "input_id")),
            option="--input-id",
            command=command,
        ),
        work_item_id=require_nonblank(
            str(getattr(namespace, "work_item_id")),
            option="WORK_ITEM_ID",
            command=command,
        ),
        plan_fingerprint=require_nonblank(
            str(getattr(namespace, "plan_fingerprint")),
            option="--plan-fingerprint",
            command=command,
        ),
        actor_id=require_nonblank(
            str(getattr(namespace, "actor_id", "local_operator")),
            option="--actor-id",
            command=command,
        ),
        reason=require_nonblank(
            str(getattr(namespace, "reason")),
            option="--reason",
            command=command,
        ),
    )
    return _apply_queue_closure(
        namespace,
        transition_input,
        command=command,
        code="queued_work_cancelled",
        message="Queued work cancelled.",
    )


def _cancel_lineage(namespace: object) -> CliSuccess:
    command = "queue.cancel-lineage"
    transition_input = CancelQueuedLineage(
        require_nonblank(
            str(getattr(namespace, "input_id")),
            option="--input-id",
            command=command,
        ),
        lineage_id=require_nonblank(
            str(getattr(namespace, "lineage_id")),
            option="LINEAGE_ID",
            command=command,
        ),
        plan_fingerprint=require_nonblank(
            str(getattr(namespace, "plan_fingerprint")),
            option="--plan-fingerprint",
            command=command,
        ),
        actor_id=require_nonblank(
            str(getattr(namespace, "actor_id", "local_operator")),
            option="--actor-id",
            command=command,
        ),
        reason=require_nonblank(
            str(getattr(namespace, "reason")),
            option="--reason",
            command=command,
        ),
    )
    return _apply_queue_closure(
        namespace,
        transition_input,
        command=command,
        code="queued_lineage_cancelled",
        message="Queued lineage cancelled.",
    )


def _apply_queue_closure(
    namespace: object,
    transition_input: CancelQueuedWork | CancelQueuedLineage,
    *,
    command: str,
    code: str,
    message: str,
) -> CliSuccess:
    runtime = open_runtime_context(namespace, command=command)
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
        decision, next_state = decide_apply_persist(
            runtime,
            state,
            transition_input,
            command=command,
        )
    finally:
        runtime.close()
    record = next(
        (
            candidate
            for candidate in next_state.queue_closures.values()
            if candidate.created_by_input_id == transition_input.input_id
        ),
        None,
    )
    if record is None:
        raise CliCommandError(
            command=command,
            code="queue_closure_record_missing",
            message="Queue closure was accepted without durable audit evidence.",
            exit_code=ExitCode.INTERNAL_ERROR,
            details={"input_id": transition_input.input_id},
        )
    return success_result(
        command=command,
        code=code,
        message=message,
        data={
            "input_id": transition_input.input_id,
            "accepted": decision.accepted,
            "transition_disposition": decision.disposition,
            "queue_closure": json_ready(record),
            "closed_work_item_ids": list(record.closed_work_item_ids),
            "closed_activation_ids": list(record.closed_activation_ids),
            "closed_run_ids": list(record.closed_run_ids),
        },
    )


def _payload_from_sources(namespace: object, *, command: str) -> object:
    raw_json = getattr(namespace, "payload_json", None)
    payload_file = getattr(namespace, "payload_file", None)
    payload_stdin = bool(getattr(namespace, "payload_stdin", False))
    source_count = sum(
        (
            raw_json is not None,
            payload_file is not None,
            payload_stdin,
        )
    )
    if source_count != 1:
        raise CliCommandError(
            command=command,
            code="invalid_payload_source",
            message="Exactly one payload source is required.",
            exit_code=ExitCode.CLI_USAGE,
            details={},
        )
    if raw_json is not None:
        return parse_json_payload(str(raw_json), command=command)
    if payload_file is not None:
        path = Path(str(payload_file))
        try:
            return parse_json_payload(path.read_text(encoding="utf-8"), command=command)
        except OSError as exc:
            raise CliCommandError(
                command=command,
                code="payload_file_unreadable",
                message="Payload file could not be read.",
                exit_code=ExitCode.PERSISTENCE_FAILURE,
                details={"path": str(path), "error": str(exc)},
            ) from exc
    return parse_json_payload(sys.stdin.read(), command=command)


def _created_work_item_id(state: object, input_id_value: str) -> str | None:
    work_items = getattr(state, "work_items")
    return next(
        (
            work_item.ref.work_item_id
            for work_item in sorted(
                work_items.values(),
                key=lambda item: item.ref.work_item_id,
            )
            if work_item.created_by_input_id == input_id_value
        ),
        None,
    )


def _created_activation_id(state: object, input_id_value: str) -> str | None:
    activations = getattr(state, "activations")
    return next(
        (
            activation.activation_id
            for activation in sorted(
                activations.values(),
                key=lambda item: item.activation_id,
            )
            if activation.created_by_input_id == input_id_value
        ),
        None,
    )


def _operator_error(command: str, exc: OperatorInputError) -> CliCommandError:
    return CliCommandError(
        command=command,
        code=exc.reason,
        message="Operator input was refused.",
        exit_code=ExitCode.DOMAIN_REFUSAL,
        details={"reason": exc.reason},
    )
