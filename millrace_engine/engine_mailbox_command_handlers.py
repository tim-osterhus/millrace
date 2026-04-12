"""Registry-backed mailbox command handlers for the runtime engine."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .adapters.control_mailbox import ControlCommand, ControlCommandEnvelope
from .config import LoadedConfig, load_engine_config
from .control_actions import (
    compounding_deprecate as compounding_deprecate_operation,
)
from .control_actions import (
    compounding_promote as compounding_promote_operation,
)
from .control_actions import (
    active_task_remediate as active_task_remediate_operation,
    clear_pending_active_task_clear,
    write_recovery_request_record,
    read_pending_active_task_clear,
)
from .control_common import ControlError
from .control_models import OperationResult
from .control_mutations import (
    _assert_reload_safe,
    append_task_to_backlog,
    apply_native_config_value,
    copy_idea_into_raw_queue,
)
from .events import EventSource, EventType
from .paths import RuntimePaths
from .queue import TaskQueue
from .control_models import RecoveryRequestRecord, RecoveryRequestResult, RecoveryRequestTarget


class EngineMailboxHookSet(Protocol):
    """Minimal hook surface required by mailbox command handlers."""

    def get_paths(self) -> RuntimePaths: ...

    def get_loaded(self) -> LoadedConfig: ...

    def emit_event(self, event_type: EventType, source: EventSource, payload: dict[str, object]) -> None: ...

    def queue_or_apply_reloaded_config(
        self,
        loaded: LoadedConfig,
        command_id: str | None,
        key: str | None,
    ) -> tuple[OperationResult, bool]: ...

    def consume_research_recovery_latch(self, **kwargs: object) -> int: ...

    def get_stop_requested(self) -> bool: ...

    def set_stop_requested(self, requested: bool) -> None: ...

    def get_paused(self) -> bool: ...

    def set_pause_state(self, paused: bool, reason: str | None, run_id: str | None) -> None: ...


@dataclass(frozen=True)
class EngineMailboxCommandContext:
    """Immutable runtime context shared across mailbox handlers."""

    config_path: Path
    hooks: EngineMailboxHookSet


@dataclass(frozen=True)
class EngineMailboxCommandExecution:
    """Result of handling one mailbox command."""

    operation: OperationResult
    restart_file_watcher: bool = False


EngineMailboxCommandHandler = Callable[[EngineMailboxCommandContext, ControlCommandEnvelope], EngineMailboxCommandExecution]


class EngineMailboxCommandRegistry:
    """Bounded registry that owns mailbox command-family dispatch."""

    def __init__(self, handlers: Mapping[ControlCommand, EngineMailboxCommandHandler]) -> None:
        self._handlers = dict(handlers)

    def dispatch(
        self,
        context: EngineMailboxCommandContext,
        envelope: ControlCommandEnvelope,
    ) -> EngineMailboxCommandExecution:
        try:
            handler = self._handlers[envelope.command]
        except KeyError as exc:
            raise ControlError(f"unsupported control command: {envelope.command.value}") from exc
        return handler(context, envelope)


def build_engine_mailbox_command_registry() -> EngineMailboxCommandRegistry:
    """Create the default mailbox command registry."""

    return EngineMailboxCommandRegistry(
        {
            ControlCommand.STOP: _handle_stop,
            ControlCommand.PAUSE: _handle_pause,
            ControlCommand.RESUME: _handle_resume,
            ControlCommand.ACTIVE_TASK_CLEAR: _handle_active_task_clear,
            ControlCommand.RELOAD_CONFIG: _handle_reload_config,
            ControlCommand.SET_CONFIG: _handle_set_config,
            ControlCommand.ADD_TASK: _handle_add_task,
            ControlCommand.ADD_IDEA: _handle_add_idea,
            ControlCommand.QUEUE_REORDER: _handle_queue_reorder,
            ControlCommand.QUEUE_CLEANUP_REMOVE: _handle_queue_cleanup_remove,
            ControlCommand.QUEUE_CLEANUP_QUARANTINE: _handle_queue_cleanup_quarantine,
            ControlCommand.RECOVERY_REQUEST: _handle_recovery_request,
            ControlCommand.COMPOUNDING_PROMOTE: _handle_compounding_promote,
            ControlCommand.COMPOUNDING_DEPRECATE: _handle_compounding_deprecate,
        }
    )


def _handle_stop(
    context: EngineMailboxCommandContext,
    envelope: ControlCommandEnvelope,
) -> EngineMailboxCommandExecution:
    already_requested = context.hooks.get_stop_requested()
    context.hooks.set_stop_requested(True)
    return EngineMailboxCommandExecution(
        operation=OperationResult(
            mode="direct",
            applied=not already_requested,
            message="stop requested" if not already_requested else "stop already requested",
        )
    )


def _handle_pause(
    context: EngineMailboxCommandContext,
    envelope: ControlCommandEnvelope,
) -> EngineMailboxCommandExecution:
    if context.hooks.get_paused():
        return EngineMailboxCommandExecution(
            operation=OperationResult(mode="direct", applied=False, message="engine already paused")
        )
    context.hooks.set_pause_state(True, "manual", None)
    context.hooks.emit_event(
        EventType.ENGINE_PAUSED,
        EventSource.CONTROL,
        {"command_id": envelope.command_id},
    )
    return EngineMailboxCommandExecution(
        operation=OperationResult(mode="direct", applied=True, message="engine paused")
    )


def _handle_resume(
    context: EngineMailboxCommandContext,
    envelope: ControlCommandEnvelope,
) -> EngineMailboxCommandExecution:
    if not context.hooks.get_paused():
        return EngineMailboxCommandExecution(
            operation=OperationResult(mode="direct", applied=False, message="engine already running")
        )
    context.hooks.set_pause_state(False, None, None)
    context.hooks.emit_event(
        EventType.ENGINE_RESUMED,
        EventSource.CONTROL,
        {"command_id": envelope.command_id},
    )
    return EngineMailboxCommandExecution(
        operation=OperationResult(mode="direct", applied=True, message="engine resumed")
    )


def _handle_reload_config(
    context: EngineMailboxCommandContext,
    envelope: ControlCommandEnvelope,
) -> EngineMailboxCommandExecution:
    reloaded = load_engine_config(context.config_path)
    _assert_reload_safe(context.hooks.get_loaded(), reloaded)
    operation, restart_watcher = context.hooks.queue_or_apply_reloaded_config(
        reloaded,
        command_id=envelope.command_id,
        key=None,
    )
    return EngineMailboxCommandExecution(operation=operation, restart_file_watcher=restart_watcher)


def _handle_active_task_clear(
    context: EngineMailboxCommandContext,
    envelope: ControlCommandEnvelope,
) -> EngineMailboxCommandExecution:
    pending = read_pending_active_task_clear(context.hooks.get_paths())
    reason = (
        pending.request.reason
        if pending is not None and pending.command_id == envelope.command_id
        else str(envelope.payload.get("reason", "")).strip()
    )
    requested_at = (
        pending.request.requested_at
        if pending is not None and pending.command_id == envelope.command_id
        else envelope.issued_at
    )
    issuer = (
        pending.request.issuer
        if pending is not None and pending.command_id == envelope.command_id
        else envelope.issuer
    )
    try:
        operation = active_task_remediate_operation(
            context.hooks.get_paths(),
            intent="clear",
            reason=reason,
            daemon_running=False,
            issuer=issuer,
            requested_at=requested_at,
            command_id=envelope.command_id,
            response_mode="mailbox",
        )
    finally:
        clear_pending_active_task_clear(context.hooks.get_paths(), command_id=envelope.command_id)
    return EngineMailboxCommandExecution(operation=operation)


def _handle_set_config(
    context: EngineMailboxCommandContext,
    envelope: ControlCommandEnvelope,
) -> EngineMailboxCommandExecution:
    key = str(envelope.payload.get("key", "")).strip()
    value = str(envelope.payload.get("value", ""))
    reloaded = apply_native_config_value(
        context.config_path,
        context.hooks.get_loaded(),
        key,
        value,
        reject_startup_only=True,
    )
    operation, restart_watcher = context.hooks.queue_or_apply_reloaded_config(
        reloaded,
        command_id=envelope.command_id,
        key=key,
    )
    return EngineMailboxCommandExecution(operation=operation, restart_file_watcher=restart_watcher)


def _handle_add_task(
    context: EngineMailboxCommandContext,
    envelope: ControlCommandEnvelope,
) -> EngineMailboxCommandExecution:
    title = str(envelope.payload.get("title", "")).strip()
    if not title:
        raise ControlError("add_task requires a title")
    card = append_task_to_backlog(
        context.hooks.get_paths(),
        title=title,
        body=envelope.payload.get("body"),
        spec_id=envelope.payload.get("spec_id"),
    )
    thawed = context.hooks.consume_research_recovery_latch(
        trigger="add_task",
        command_id=envelope.command_id,
    )
    payload: dict[str, object] = {"task_id": card.task_id}
    if thawed > 0:
        payload["thawed_cards"] = thawed
    return EngineMailboxCommandExecution(
        operation=OperationResult(mode="direct", applied=True, message="task added", payload=payload)
    )


def _handle_add_idea(
    context: EngineMailboxCommandContext,
    envelope: ControlCommandEnvelope,
) -> EngineMailboxCommandExecution:
    file_value = envelope.payload.get("file")
    if not isinstance(file_value, str) or not file_value.strip():
        raise ControlError("add_idea requires a file path")
    copied = copy_idea_into_raw_queue(context.hooks.get_paths(), Path(file_value).expanduser().resolve())
    return EngineMailboxCommandExecution(
        operation=OperationResult(mode="direct", applied=True, message="idea queued", payload={"path": copied})
    )


def _handle_recovery_request(
    context: EngineMailboxCommandContext,
    envelope: ControlCommandEnvelope,
) -> EngineMailboxCommandExecution:
    target = str(envelope.payload.get("target", "")).strip().lower()
    reason = str(envelope.payload.get("reason", "")).strip()
    request_id = str(envelope.payload.get("request_id", "")).strip()
    force_queue = bool(envelope.payload.get("force_queue", False))
    if not target:
        raise ControlError("recovery_request requires a target")
    if not reason:
        raise ControlError("recovery_request requires a reason")
    if not request_id:
        raise ControlError("recovery_request requires a request id")
    if not force_queue:
        raise ControlError("recovery_request requires force_queue=true")
    request = RecoveryRequestRecord(
        request_id=request_id,
        requested_at=envelope.issued_at,
        target=RecoveryRequestTarget(target),
        issuer=envelope.issuer,
        reason=reason,
        force_queue=True,
        source="manual",
        mode="mailbox",
        command_id=envelope.command_id,
        active_task_id=(
            None
            if envelope.payload.get("active_task_id") in (None, "")
            else str(envelope.payload.get("active_task_id"))
        ),
        execution_status=(
            None
            if envelope.payload.get("execution_status") in (None, "")
            else str(envelope.payload.get("execution_status"))
        ),
        research_status=(
            None
            if envelope.payload.get("research_status") in (None, "")
            else str(envelope.payload.get("research_status"))
        ),
    )
    artifact_path = write_recovery_request_record(context.hooks.get_paths(), request)
    context.hooks.emit_event(
        EventType.RECOVERY_REQUEST_QUEUED,
        EventSource.CONTROL,
        {
            "request_id": request.request_id,
            "target": request.target.value,
            "issuer": request.issuer,
            "reason": request.reason,
            "force_queue": request.force_queue,
            "command_id": envelope.command_id,
            "artifact_path": artifact_path.as_posix(),
            "active_task_id": request.active_task_id,
            "execution_status": request.execution_status,
            "research_status": request.research_status,
        },
    )
    return EngineMailboxCommandExecution(
        operation=RecoveryRequestResult(
            command_id=envelope.command_id,
            mode="mailbox",
            applied=True,
            message="recovery request queued",
            payload={
                "request_id": request.request_id,
                "target": request.target.value,
                "issuer": request.issuer,
                "reason": request.reason,
                "force_queue": request.force_queue,
                "artifact_path": artifact_path.as_posix(),
                "active_task_id": request.active_task_id,
                "execution_status": request.execution_status,
                "research_status": request.research_status,
            },
            request=request,
            artifact_path=artifact_path,
        )
    )


def _handle_queue_reorder(
    context: EngineMailboxCommandContext,
    envelope: ControlCommandEnvelope,
) -> EngineMailboxCommandExecution:
    task_ids = envelope.payload.get("task_ids")
    if not isinstance(task_ids, list) or not all(isinstance(item, str) for item in task_ids):
        raise ControlError("queue_reorder requires a task_ids string list")
    reordered = TaskQueue(context.hooks.get_paths()).reorder(task_ids)
    return EngineMailboxCommandExecution(
        operation=OperationResult(
            mode="direct",
            applied=True,
            message="queue reordered",
            payload={"task_ids": [card.task_id for card in reordered]},
        )
    )


def _read_queue_cleanup_payload(
    envelope: ControlCommandEnvelope,
    *,
    action_label: str,
) -> tuple[str, str]:
    task_id = envelope.payload.get("task_id")
    reason = envelope.payload.get("reason")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ControlError(f"{action_label} requires a task id")
    if not isinstance(reason, str) or not reason.strip():
        raise ControlError("queue cleanup requires a reason")
    return task_id.strip(), reason


def _queue_cleanup_execution(
    *,
    envelope: ControlCommandEnvelope,
    record,
    message: str,
) -> EngineMailboxCommandExecution:
    return EngineMailboxCommandExecution(
        operation=OperationResult(
            mode="direct",
            applied=True,
            message=message,
            payload={
                "task_id": record.task.task_id,
                "title": record.task.title,
                "source_store": record.source_store,
                "destination_store": record.destination_store,
                "reason": record.reason,
                "cleanup_action": record.action,
                "issuer": envelope.issuer,
            },
        )
    )


def _handle_queue_cleanup_remove(
    context: EngineMailboxCommandContext,
    envelope: ControlCommandEnvelope,
) -> EngineMailboxCommandExecution:
    task_id, reason = _read_queue_cleanup_payload(envelope, action_label="queue cleanup remove")
    record = TaskQueue(context.hooks.get_paths()).remove_task(task_id, reason=reason)
    return _queue_cleanup_execution(
        envelope=envelope,
        record=record,
        message="queue cleanup removed task",
    )


def _handle_queue_cleanup_quarantine(
    context: EngineMailboxCommandContext,
    envelope: ControlCommandEnvelope,
) -> EngineMailboxCommandExecution:
    task_id, reason = _read_queue_cleanup_payload(envelope, action_label="queue cleanup quarantine")
    record = TaskQueue(context.hooks.get_paths()).quarantine_task(task_id, reason=reason)
    return _queue_cleanup_execution(
        envelope=envelope,
        record=record,
        message="queue cleanup quarantined task",
    )


def _handle_compounding_promote(
    context: EngineMailboxCommandContext,
    envelope: ControlCommandEnvelope,
) -> EngineMailboxCommandExecution:
    procedure_id = envelope.payload.get("procedure_id")
    changed_by = envelope.payload.get("changed_by", envelope.issuer)
    reason = envelope.payload.get("reason", "")
    if not isinstance(procedure_id, str) or not procedure_id.strip():
        raise ControlError("compounding_promote requires a procedure_id")
    if not isinstance(changed_by, str):
        raise ControlError("compounding_promote requires a changed_by string")
    if not isinstance(reason, str):
        raise ControlError("compounding_promote requires a reason string")
    return EngineMailboxCommandExecution(
        operation=compounding_promote_operation(
            context.hooks.get_paths(),
            procedure_id=procedure_id,
            changed_by=changed_by,
            reason=reason,
            daemon_running=False,
        )
    )


def _handle_compounding_deprecate(
    context: EngineMailboxCommandContext,
    envelope: ControlCommandEnvelope,
) -> EngineMailboxCommandExecution:
    procedure_id = envelope.payload.get("procedure_id")
    changed_by = envelope.payload.get("changed_by", envelope.issuer)
    reason = envelope.payload.get("reason", "")
    replacement_procedure_id = envelope.payload.get("replacement_procedure_id")
    if not isinstance(procedure_id, str) or not procedure_id.strip():
        raise ControlError("compounding_deprecate requires a procedure_id")
    if not isinstance(changed_by, str):
        raise ControlError("compounding_deprecate requires a changed_by string")
    if not isinstance(reason, str):
        raise ControlError("compounding_deprecate requires a reason string")
    if replacement_procedure_id is not None and not isinstance(replacement_procedure_id, str):
        raise ControlError("compounding_deprecate replacement_procedure_id must be a string")
    return EngineMailboxCommandExecution(
        operation=compounding_deprecate_operation(
            context.hooks.get_paths(),
            procedure_id=procedure_id,
            changed_by=changed_by,
            reason=reason,
            replacement_procedure_id=replacement_procedure_id,
            daemon_running=False,
        )
    )
