"""Finite durable operation lookup and atomic seal; never admits run controls."""

from __future__ import annotations

from millrace.adapters.cli.context import CliCommandError, open_runtime_context
from millrace.adapters.cli.output import CliSuccess, ExitCode, success_result
from millrace.contracts.controls import ControlRequest, InvalidControlRequest
from millrace.substrate.errors import ControlOperationError


def handle_operations_command(namespace: object) -> CliSuccess:
    command = str(getattr(namespace, "command"))
    try:
        request = ControlRequest.parse(str(getattr(namespace, "request_json")))
    except InvalidControlRequest as exc:
        raise CliCommandError(
            command=command,
            code="invalid_control_request",
            message="Control request is invalid or exceeds its bound.",
            exit_code=ExitCode.CLI_USAGE,
            details={"receipt_persisted": False},
        ) from exc
    try:
        context = open_runtime_context(namespace, command=command)
        try:
            if command == "operations.resolve":
                result = context.store.resolve_operation(request)
            else:
                result = context.store.show_operation(request)
        finally:
            context.close()
    except ControlOperationError as exc:
        raise CliCommandError(
            command=command,
            code=str(exc),
            message="Operation could not be resolved; retain the same operation key.",
            exit_code=ExitCode.DOMAIN_REFUSAL,
            details={
                "status": "unknown",
                "request_digest": request.digest,
                "key": list(request.key),
                "target": request.payload["target"],
            },
        ) from exc
    return success_result(
        command=command,
        code="operation_observed",
        message="Durable operation observation.",
        data=result,
    )
