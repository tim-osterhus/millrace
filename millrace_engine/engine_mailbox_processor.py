"""Mailbox command ownership for the runtime engine."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .adapters.control_mailbox import (
    ControlCommand,
    ControlCommandEnvelope,
    MailboxCommandResult,
    archive_command,
    list_incoming_command_paths,
    read_command,
)
from .config import LoadedConfig, load_engine_config
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


@dataclass(frozen=True)
class EngineMailboxHooks:
    """Explicit engine-owned hooks used by mailbox command processing."""

    get_paths: Callable[[], RuntimePaths]
    get_loaded: Callable[[], LoadedConfig]
    emit_event: Callable[[EventType, EventSource, dict[str, object]], None]
    queue_or_apply_reloaded_config: Callable[[LoadedConfig, str | None, str | None], tuple[OperationResult, bool]]
    restart_file_watcher: Callable[[], Awaitable[None]]
    consume_research_recovery_latch: Callable[..., int]
    get_stop_requested: Callable[[], bool]
    set_stop_requested: Callable[[bool], None]
    get_paused: Callable[[], bool]
    set_pause_state: Callable[[bool, str | None, str | None], None]


class EngineMailboxProcessor:
    """Own mailbox command loading, dispatch, archiving, and result production."""

    def __init__(self, *, config_path: Path, hooks: EngineMailboxHooks) -> None:
        self._config_path = config_path
        self._hooks = hooks

    def apply_config_command_sync(self, envelope: ControlCommandEnvelope) -> tuple[OperationResult, bool]:
        if envelope.command is ControlCommand.RELOAD_CONFIG:
            reloaded = load_engine_config(self._config_path)
            _assert_reload_safe(self._hooks.get_loaded(), reloaded)
            return self._hooks.queue_or_apply_reloaded_config(
                reloaded,
                command_id=envelope.command_id,
                key=None,
            )

        if envelope.command is ControlCommand.SET_CONFIG:
            key = str(envelope.payload.get("key", "")).strip()
            value = str(envelope.payload.get("value", ""))
            reloaded = apply_native_config_value(
                self._config_path,
                self._hooks.get_loaded(),
                key,
                value,
                reject_startup_only=True,
            )
            return self._hooks.queue_or_apply_reloaded_config(
                reloaded,
                command_id=envelope.command_id,
                key=key,
            )

        raise ControlError(f"unsupported config command: {envelope.command.value}")

    async def apply_command(self, envelope: ControlCommandEnvelope) -> OperationResult:
        if envelope.command is ControlCommand.STOP:
            already_requested = self._hooks.get_stop_requested()
            self._hooks.set_stop_requested(True)
            return OperationResult(
                mode="direct",
                applied=not already_requested,
                message="stop requested" if not already_requested else "stop already requested",
            )

        if envelope.command is ControlCommand.PAUSE:
            if self._hooks.get_paused():
                return OperationResult(mode="direct", applied=False, message="engine already paused")
            self._hooks.set_pause_state(True, "manual", None)
            self._hooks.emit_event(
                EventType.ENGINE_PAUSED,
                EventSource.CONTROL,
                {"command_id": envelope.command_id},
            )
            return OperationResult(mode="direct", applied=True, message="engine paused")

        if envelope.command is ControlCommand.RESUME:
            if not self._hooks.get_paused():
                return OperationResult(mode="direct", applied=False, message="engine already running")
            self._hooks.set_pause_state(False, None, None)
            self._hooks.emit_event(
                EventType.ENGINE_RESUMED,
                EventSource.CONTROL,
                {"command_id": envelope.command_id},
            )
            return OperationResult(mode="direct", applied=True, message="engine resumed")

        if envelope.command in {ControlCommand.RELOAD_CONFIG, ControlCommand.SET_CONFIG}:
            operation, restart_watcher = self.apply_config_command_sync(envelope)
            if restart_watcher:
                await self._hooks.restart_file_watcher()
            return operation

        paths = self._hooks.get_paths()

        if envelope.command is ControlCommand.ADD_TASK:
            title = str(envelope.payload.get("title", "")).strip()
            if not title:
                raise ControlError("add_task requires a title")
            card = append_task_to_backlog(
                paths,
                title=title,
                body=envelope.payload.get("body"),
                spec_id=envelope.payload.get("spec_id"),
            )
            thawed = self._hooks.consume_research_recovery_latch(
                trigger="add_task",
                command_id=envelope.command_id,
            )
            payload: dict[str, object] = {"task_id": card.task_id}
            if thawed > 0:
                payload["thawed_cards"] = thawed
            return OperationResult(mode="direct", applied=True, message="task added", payload=payload)

        if envelope.command is ControlCommand.ADD_IDEA:
            file_value = envelope.payload.get("file")
            if not isinstance(file_value, str) or not file_value.strip():
                raise ControlError("add_idea requires a file path")
            copied = copy_idea_into_raw_queue(paths, Path(file_value).expanduser().resolve())
            return OperationResult(mode="direct", applied=True, message="idea queued", payload={"path": copied})

        if envelope.command is ControlCommand.QUEUE_REORDER:
            task_ids = envelope.payload.get("task_ids")
            if not isinstance(task_ids, list) or not all(isinstance(item, str) for item in task_ids):
                raise ControlError("queue_reorder requires a task_ids string list")
            reordered = TaskQueue(paths).reorder(task_ids)
            return OperationResult(
                mode="direct",
                applied=True,
                message="queue reordered",
                payload={"task_ids": [card.task_id for card in reordered]},
            )

        raise ControlError(f"unsupported control command: {envelope.command.value}")

    def process_config_mailbox_between_stages(self) -> None:
        for command_path in list_incoming_command_paths(self._hooks.get_paths()):
            envelope: ControlCommandEnvelope | None = None
            try:
                envelope = read_command(command_path)
                if envelope.command not in {ControlCommand.RELOAD_CONFIG, ControlCommand.SET_CONFIG}:
                    break
                self._emit_command_received(envelope)
                operation, _ = self.apply_config_command_sync(envelope)
                self._archive_mailbox_result(command_path, envelope=envelope, operation=operation)
            except Exception as exc:  # noqa: BLE001 - mailbox failures must be archived
                self._archive_mailbox_result(command_path, envelope=envelope, error=exc)

    async def process_mailbox(self) -> None:
        for command_path in list_incoming_command_paths(self._hooks.get_paths()):
            envelope: ControlCommandEnvelope | None = None
            try:
                envelope = read_command(command_path)
                self._emit_command_received(envelope)
                operation = await self.apply_command(envelope)
                self._archive_mailbox_result(command_path, envelope=envelope, operation=operation)
            except Exception as exc:  # noqa: BLE001 - mailbox failures must be archived
                self._archive_mailbox_result(command_path, envelope=envelope, error=exc)

    def _emit_command_received(self, envelope: ControlCommandEnvelope) -> None:
        self._hooks.emit_event(
            EventType.CONTROL_COMMAND_RECEIVED,
            EventSource.ADAPTER,
            {"command_id": envelope.command_id, "command": envelope.command.value},
        )

    def _archive_mailbox_result(
        self,
        command_path: Path,
        *,
        envelope: ControlCommandEnvelope | None,
        operation: OperationResult | None = None,
        error: Exception | None = None,
    ) -> None:
        command_id = envelope.command_id if envelope is not None else command_path.stem
        if error is None and operation is not None:
            result = MailboxCommandResult.model_validate(
                {
                    "command_id": command_id,
                    "processed_at": datetime.now(timezone.utc),
                    "ok": True,
                    "applied": operation.applied,
                    "message": operation.message,
                    "payload": operation.payload,
                }
            )
            archive_command(
                self._hooks.get_paths(),
                command_path,
                envelope=envelope,
                result=result,
                failed=False,
            )
            self._hooks.emit_event(
                EventType.CONTROL_COMMAND_APPLIED,
                EventSource.CONTROL,
                {
                    "command_id": command_id,
                    "command": envelope.command.value if envelope is not None else None,
                    "applied": operation.applied,
                },
            )
            return

        message = str(error) if error is not None else "unknown mailbox error"
        result = MailboxCommandResult.model_validate(
            {
                "command_id": command_id,
                "processed_at": datetime.now(timezone.utc),
                "ok": False,
                "applied": False,
                "message": message,
            }
        )
        archive_command(
            self._hooks.get_paths(),
            command_path,
            envelope=envelope,
            result=result,
            failed=True,
        )
        self._hooks.emit_event(
            EventType.CONTROL_COMMAND_APPLIED,
            EventSource.CONTROL,
            {"command_id": command_id, "ok": False, "message": message},
        )
