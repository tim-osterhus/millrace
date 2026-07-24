"""CLI adapter for runtime workspace initialization and checks."""

from __future__ import annotations

from millrace.adapters.cli.context import (
    CliCommandError,
    contextual_input_id,
    initialize_runtime_context,
    input_id,
    open_runtime_context,
    refusal_is_pre_persist,
    store_not_initialized,
    transition_context,
    transition_refusal_error,
    workspace_paths,
)
from millrace.adapters.cli.output import CliSuccess, ExitCode, success_result


def handle_workspace_command(namespace: object) -> CliSuccess:
    command = str(getattr(namespace, "command", "workspace"))
    if command == "workspace.init":
        return _workspace_init(namespace)
    if command == "workspace.check":
        return _workspace_check(namespace)
    raise CliCommandError(
        command=command,
        code="command_not_implemented",
        message="Command is not implemented.",
        exit_code=ExitCode.DOMAIN_REFUSAL,
        details={},
    )


def _workspace_init(namespace: object) -> CliSuccess:
    from millrace.contracts.transition import InitializeWorkspace
    from millrace.kernel import apply, decide, empty_runtime_state

    command = "workspace.init"
    input_id_value = input_id(namespace, command=command)
    context = initialize_runtime_context(namespace)
    try:
        state = context.store.load_runtime_state(context.cas_store)
        was_empty = state == empty_runtime_state()
        transition_input = InitializeWorkspace(input_id=input_id_value)
        decision = decide(
            state,
            transition_input,
            transition_context(
                command=command,
                input_id_value=contextual_input_id(transition_input),
            ),
        )
        if not decision.accepted and refusal_is_pre_persist(decision):
            raise transition_refusal_error(command=command, decision=decision)
        next_state = apply(state, decision)
        context.store.persist_runtime_state(next_state, context.cas_store)
        if not decision.accepted:
            raise transition_refusal_error(command=command, decision=decision)
        metadata = context.store.schema_metadata()
    finally:
        context.close()

    return success_result(
        command=command,
        code="workspace_initialized",
        message="Workspace initialized.",
        data={
            "workspace_path": str(context.paths.workspace_path),
            "db_path": str(context.paths.db_path),
            "cas_path": str(context.paths.cas_path),
            "schema_version": metadata["store_schema_version"],
            "initialized": was_empty,
            "transition_disposition": decision.disposition,
            "input_id": input_id_value,
        },
    )


def _workspace_check(namespace: object) -> CliSuccess:
    command = "workspace.check"
    paths = workspace_paths(namespace)
    if not paths.db_path.exists():
        raise store_not_initialized(command, paths)
    context = open_runtime_context(namespace, command=command)
    try:
        metadata = context.store.schema_metadata()
        state = context.store.load_runtime_state(context.cas_store)
    finally:
        context.close()
    default_fingerprint = (
        None
        if state.default_plan_ref is None
        else state.default_plan_ref.authority_fingerprint
    )
    return success_result(
        command=command,
        code="workspace_ok",
        message="Workspace store is initialized.",
        data={
            "workspace_path": str(paths.workspace_path),
            "db_path": str(paths.db_path),
            "cas_path": str(paths.cas_path),
            "schema_version": metadata["store_schema_version"],
            "initialized": True,
            "admitted_plan_count": len(state.admitted_plans),
            "default_plan_fingerprint": default_fingerprint,
            "receipt_count": len(state.receipts),
        },
    )
