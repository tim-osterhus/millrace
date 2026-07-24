"""CLI adapter for audited local-operator intervention commands."""

from __future__ import annotations

from millrace.adapters.cli.context import (
    CliCommandError,
    actor_id,
    command_input_id,
    decide_apply_persist,
    open_runtime_context,
    parse_json_payload,
    require_nonblank,
)
from millrace.adapters.cli.output import CliSuccess, ExitCode, success_result
from millrace.contracts.state import PlanRef, RuntimeState
from millrace.contracts.transition import TransitionInput
from millrace.operator import intake
from millrace.operator.intake import OperatorInputError


def handle_intervention_command(namespace: object) -> CliSuccess:
    command = str(getattr(namespace, "command", "interventions"))
    if command in {
        "interventions.resume-lineage",
        "interventions.close-lineage",
        "interventions.revise-lineage",
        "waits.resume",
        "waits.close",
        "waits.revise",
    }:
        return _mutation(namespace, command=command)
    raise CliCommandError(
        command=command,
        code="command_not_implemented",
        message="Command is not implemented.",
        exit_code=ExitCode.DOMAIN_REFUSAL,
        details={},
    )


def _mutation(namespace: object, *, command: str) -> CliSuccess:
    payload = _payload(namespace, command=command)
    if command in {
        "interventions.resume-lineage",
        "interventions.close-lineage",
        "waits.resume",
        "waits.close",
    } and _nonempty_payload(payload):
        raise _payload_forbidden(command)
    input_id_value = command_input_id(
        namespace,
        command=command,
        payload=_command_payload(namespace, command=command, payload=payload),
    )
    runtime = open_runtime_context(namespace, command=command)
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
        try:
            transition_input = _build_transition_input(
                state,
                namespace,
                command=command,
                input_id_value=input_id_value,
                payload=payload,
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
    return success_result(
        command=command,
        code="operator_intervention_recorded",
        message="Operator intervention recorded.",
        data={
            "input_id": input_id_value,
            "accepted": decision.accepted,
            "transition_disposition": decision.disposition,
            "intervention_count": len(next_state.operator_interventions),
            "operator_wait_count": len(next_state.operator_waits),
            "lineage_quarantine_count": len(next_state.lineage_quarantines),
        },
    )


def _build_transition_input(
    state: RuntimeState,
    namespace: object,
    *,
    command: str,
    input_id_value: str,
    payload: object,
) -> TransitionInput:
    plan_ref = _selected_plan_ref(state, command=command)
    local_actor_id = actor_id(namespace, command=command)
    if command == "interventions.resume-lineage":
        return intake.build_resume_lineage(
            state,
            intake.OperatorResumeLineageInput(
                input_id=input_id_value,
                option_id=str(getattr(namespace, "option_id")),
                selected_plan_ref=plan_ref,
                quarantine_id=getattr(namespace, "quarantine_id", None),
                lineage_id=getattr(namespace, "lineage_id", None),
                actor_id=local_actor_id,
                actor_kind="local_operator",
                reason=_reason(namespace, command=command),
                payload=payload,
            ),
        )
    if command == "interventions.close-lineage":
        return intake.build_close_lineage(
            state,
            intake.OperatorCloseLineageInput(
                input_id=input_id_value,
                option_id=str(getattr(namespace, "option_id")),
                selected_plan_ref=plan_ref,
                quarantine_id=getattr(namespace, "quarantine_id", None),
                lineage_id=getattr(namespace, "lineage_id", None),
                actor_id=local_actor_id,
                actor_kind="local_operator",
                reason=_reason(namespace, command=command),
                payload=payload,
            ),
        )
    if command == "interventions.revise-lineage":
        return intake.build_revise_lineage(
            state,
            intake.OperatorReviseLineageInput(
                input_id=input_id_value,
                option_id=str(getattr(namespace, "option_id")),
                selected_plan_ref=plan_ref,
                quarantine_id=getattr(namespace, "quarantine_id", None),
                lineage_id=getattr(namespace, "lineage_id", None),
                actor_id=local_actor_id,
                actor_kind="local_operator",
                reason=_reason(namespace, command=command),
                payload=payload,
            ),
        )
    if command == "waits.resume":
        return intake.build_resume_wait(
            state,
            intake.OperatorResumeWaitInput(
                input_id=input_id_value,
                selected_plan_ref=plan_ref,
                wait_id=str(getattr(namespace, "wait_id")),
                actor_id=local_actor_id,
                actor_kind="local_operator",
                payload=payload,
            ),
        )
    if command == "waits.close":
        return intake.build_close_wait(
            state,
            intake.OperatorCloseWaitInput(
                input_id=input_id_value,
                selected_plan_ref=plan_ref,
                wait_id=str(getattr(namespace, "wait_id")),
                actor_id=local_actor_id,
                actor_kind="local_operator",
                payload=payload,
            ),
        )
    return intake.build_revise_wait(
        state,
        intake.OperatorReviseWaitInput(
            input_id=input_id_value,
            selected_plan_ref=plan_ref,
            wait_id=str(getattr(namespace, "wait_id")),
            actor_id=local_actor_id,
            actor_kind="local_operator",
            payload=payload,
        ),
    )


def _selected_plan_ref(state: RuntimeState, *, command: str) -> PlanRef:
    if state.default_plan_ref is None:
        raise CliCommandError(
            command=command,
            code="missing_default_plan",
            message="No default selected plan is available.",
            exit_code=ExitCode.DOMAIN_REFUSAL,
            details={},
        )
    return state.default_plan_ref


def _reason(namespace: object, *, command: str) -> str:
    return require_nonblank(
        str(getattr(namespace, "reason", "")),
        option="--reason",
        command=command,
    )


def _payload(namespace: object, *, command: str) -> object:
    raw = getattr(namespace, "payload_json", None)
    if raw is None:
        return None
    return parse_json_payload(str(raw), command=command)


def _nonempty_payload(payload: object) -> bool:
    return isinstance(payload, dict) and bool(payload)


def _payload_forbidden(command: str) -> CliCommandError:
    return CliCommandError(
        command=command,
        code="payload_forbidden",
        message="Payload is forbidden for this command.",
        exit_code=ExitCode.DOMAIN_REFUSAL,
        details={},
    )


def _command_payload(
    namespace: object,
    *,
    command: str,
    payload: object,
) -> dict[str, object]:
    if command.startswith("waits."):
        return {"wait_id": getattr(namespace, "wait_id", None), "payload": payload}
    return {
        "option_id": getattr(namespace, "option_id", None),
        "quarantine_id": getattr(namespace, "quarantine_id", None),
        "lineage_id": getattr(namespace, "lineage_id", None),
        "reason": getattr(namespace, "reason", None),
        "payload": payload,
    }


def _operator_error(command: str, exc: OperatorInputError) -> CliCommandError:
    return CliCommandError(
        command=command,
        code=exc.reason,
        message="Operator input was refused.",
        exit_code=ExitCode.DOMAIN_REFUSAL,
        details={"reason": exc.reason},
    )
