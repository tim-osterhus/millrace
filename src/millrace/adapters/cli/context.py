"""Shared CLI context helpers for runtime store access and transition calls."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from millrace.adapters.cli.output import CliError, ExitCode, error_result

if TYPE_CHECKING:
    from millrace.contracts.state import RuntimeState
    from millrace.contracts.transition import (
        TransitionContext,
        TransitionDecision,
        TransitionInput,
    )
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore


@dataclass(frozen=True, slots=True)
class CliCommandError(ValueError):
    command: str
    code: str
    message: str
    exit_code: ExitCode
    details: dict[str, object]

    def to_cli_error(self) -> CliError:
        return error_result(
            command=self.command,
            code=self.code,
            message=self.message,
            exit_code=self.exit_code,
            details=self.details,
        )


@dataclass(frozen=True, slots=True)
class CliWorkspacePaths:
    workspace_path: Path
    db_path: Path
    cas_path: Path


@dataclass(frozen=True, slots=True)
class OpenRuntimeContext:
    paths: CliWorkspacePaths
    store: SQLiteRuntimeStore
    cas_store: ContentAddressedByteStore

    def close(self) -> None:
        close = getattr(self.store, "close", None)
        if close is not None:
            close()


def workspace_paths(namespace: object) -> CliWorkspacePaths:
    workspace_value = getattr(namespace, "workspace", None)
    db_value = getattr(namespace, "db", None)
    cas_value = getattr(namespace, "cas", None)

    workspace_path = _absolute_path(
        Path.cwd() if workspace_value is None else Path(str(workspace_value))
    )
    default_root = workspace_path / ".millrace"
    db_path = _absolute_path(
        default_root / "runtime.sqlite3" if db_value is None else Path(str(db_value))
    )
    cas_path = _absolute_path(
        default_root / "cas" if cas_value is None else Path(str(cas_value))
    )
    return CliWorkspacePaths(
        workspace_path=workspace_path,
        db_path=db_path,
        cas_path=cas_path,
    )


def open_runtime_context(namespace: object, *, command: str) -> OpenRuntimeContext:
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.errors import (
        StoreNotInitialized,
        StoreSchemaUpgradeRequired,
    )
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    paths = workspace_paths(namespace)
    if not paths.cas_path.is_dir():
        raise CliCommandError(
            command=command,
            code="cas_root_not_initialized",
            message="CAS root is not initialized.",
            exit_code=ExitCode.PERSISTENCE_FAILURE,
            details=_path_details(paths),
        )
    try:
        store = SQLiteRuntimeStore.open(paths.db_path)
    except StoreSchemaUpgradeRequired as exc:
        raise workspace_upgrade_required(command) from exc
    except StoreNotInitialized as exc:
        raise store_not_initialized(command, paths) from exc
    return OpenRuntimeContext(
        paths=paths,
        store=store,
        cas_store=ContentAddressedByteStore(paths.cas_path),
    )


def initialize_runtime_context(namespace: object) -> OpenRuntimeContext:
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    paths = workspace_paths(namespace)
    paths.db_path.parent.mkdir(parents=True, exist_ok=True)
    paths.cas_path.mkdir(parents=True, exist_ok=True)
    store = SQLiteRuntimeStore.initialize(paths.db_path)
    return OpenRuntimeContext(
        paths=paths,
        store=store,
        cas_store=ContentAddressedByteStore(paths.cas_path),
    )


def require_nonblank(value: str, *, option: str, command: str) -> str:
    if not value.strip():
        raise CliCommandError(
            command=command,
            code=f"invalid_{option.removeprefix('--').replace('-', '_')}",
            message=f"{option} must be nonblank.",
            exit_code=ExitCode.CLI_USAGE,
            details={option.removeprefix("--").replace("-", "_"): value},
        )
    return value


def command_id(namespace: object, *, command: str) -> str:
    return require_nonblank(
        str(getattr(namespace, "command_id", "")),
        option="--command-id",
        command=command,
    )


def input_id(namespace: object, *, command: str) -> str:
    return require_nonblank(
        str(getattr(namespace, "input_id", "")),
        option="--input-id",
        command=command,
    )


def command_input_id(
    namespace: object,
    *,
    command: str,
    payload: object,
) -> str:
    explicit = getattr(namespace, "input_id", None)
    if explicit is not None:
        return require_nonblank(str(explicit), option="--input-id", command=command)
    serialized = json.dumps(
        _canonical_cli_value({"command": command, "payload": payload}),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"cli:{command}:{sha256(serialized).hexdigest()[:32]}"


def optional_claim_id(namespace: object, *, command: str) -> str | None:
    value = getattr(namespace, "claim_id", None)
    if value is None:
        return None
    return require_nonblank(str(value), option="--claim-id", command=command)


def actor_id(namespace: object, *, command: str) -> str:
    return require_nonblank(
        str(getattr(namespace, "actor_id", "")),
        option="--actor-id",
        command=command,
    )


def transition_context(
    *,
    command: str,
    input_id_value: str,
    claim_id_value: str | None = None,
) -> TransitionContext:
    from millrace.contracts.transition import TransitionContext

    prefix = f"cli:{command}:{input_id_value}"
    return TransitionContext(
        transition_id=f"{prefix}:transition",
        work_item_id=f"{prefix}:work-item",
        activation_id=f"{prefix}:activation",
        run_id=f"{prefix}:run",
        claim_id=f"{prefix}:claim" if claim_id_value is None else claim_id_value,
        fencing_token=f"{prefix}:fence",
    )


def apply_control_transition(
    namespace: object,
    transition_input: TransitionInput,
    *,
    command: str,
) -> tuple[TransitionDecision, RuntimeState]:
    from millrace.kernel import apply, decide

    context = open_runtime_context(namespace, command=command)
    try:
        state = context.store.load_runtime_state(context.cas_store)
        decision = decide(
            state,
            transition_input,
            transition_context(
                command=command,
                input_id_value=_contextual_input_id(transition_input),
            ),
        )
        if not decision.accepted and _refusal_is_pre_persist(decision):
            raise transition_refusal_error(command=command, decision=decision)
        next_state = apply(state, decision)
        context.store.persist_runtime_state(next_state, context.cas_store)
        if not decision.accepted:
            raise transition_refusal_error(command=command, decision=decision)
        return decision, next_state
    finally:
        context.close()


def decide_apply_persist(
    runtime: OpenRuntimeContext,
    state: RuntimeState,
    transition_input: TransitionInput,
    *,
    command: str,
    claim_id_value: str | None = None,
) -> tuple[TransitionDecision, RuntimeState]:
    from millrace.kernel import apply, decide

    decision = decide(
        state,
        transition_input,
        transition_context(
            command=command,
            input_id_value=_contextual_input_id(transition_input),
            claim_id_value=claim_id_value,
        ),
    )
    if not decision.accepted and _refusal_is_pre_persist(decision):
        raise transition_refusal_error(command=command, decision=decision)
    next_state = apply(state, decision)
    runtime.store.persist_runtime_state(next_state, runtime.cas_store)
    if not decision.accepted:
        raise transition_refusal_error(command=command, decision=decision)
    return decision, next_state


def contextual_input_id(transition_input: TransitionInput) -> str:
    return _contextual_input_id(transition_input)


def transition_refusal_error(
    *,
    command: str,
    decision: TransitionDecision,
) -> CliCommandError:
    refusal = decision.refusal
    reason = "transition_refused" if refusal is None else refusal.reason
    detail = None if refusal is None else refusal.detail
    return CliCommandError(
        command=command,
        code=reason,
        message="Transition was refused.",
        exit_code=ExitCode.DOMAIN_REFUSAL,
        details={
            "input_id": decision.input_id,
            "input_kind": decision.input_kind,
            "transition_disposition": decision.disposition,
            "refusal_detail": detail,
        },
    )


def refusal_is_pre_persist(decision: TransitionDecision) -> bool:
    return _refusal_is_pre_persist(decision)


def store_not_initialized(command: str, paths: CliWorkspacePaths) -> CliCommandError:
    return CliCommandError(
        command=command,
        code="store_not_initialized",
        message="SQLite runtime store is not initialized.",
        exit_code=ExitCode.PERSISTENCE_FAILURE,
        details=_path_details(paths),
    )


def workspace_upgrade_required(command: str) -> CliCommandError:
    return CliCommandError(
        command=command,
        code="workspace_upgrade_required",
        message="Workspace schema upgrade is required.",
        exit_code=ExitCode.PERSISTENCE_FAILURE,
        details={
            "current_schema_version": 6,
            "required_schema_version": 7,
        },
    )


def package_command_failed(
    *,
    command: str,
    code: str,
    details: dict[str, object] | None = None,
) -> CliCommandError:
    return CliCommandError(
        command=command,
        code=code,
        message="Package command failed.",
        exit_code=ExitCode.DOMAIN_REFUSAL,
        details=details or {},
    )


def parse_json_payload(raw: str, *, command: str) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliCommandError(
            command=command,
            code="invalid_payload_json",
            message="Payload JSON is invalid.",
            exit_code=ExitCode.CLI_USAGE,
            details={"error": str(exc)},
        ) from exc


def _absolute_path(path: Path) -> Path:
    return path.expanduser().resolve()


def _contextual_input_id(transition_input: TransitionInput) -> str:
    from millrace.contracts.transition import input_payload_digest

    return f"{transition_input.input_id}:{input_payload_digest(transition_input)}"


def _refusal_is_pre_persist(decision: TransitionDecision) -> bool:
    refusal = decision.refusal
    return refusal is not None and refusal.reason in {
        "enqueue_replay_target_invalid",
        "idempotency_conflict",
    }


def _path_details(paths: CliWorkspacePaths) -> dict[str, object]:
    return {
        "db_path": str(paths.db_path),
        "cas_path": str(paths.cas_path),
    }


def _canonical_cli_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, (list, tuple)):
        return [_canonical_cli_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _canonical_cli_value(nested) for key, nested in value.items()}
    return str(value)
