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
from .config import LoadedConfig
from .control_models import OperationResult
from .engine_mailbox_command_handlers import (
    EngineMailboxCommandContext,
    EngineMailboxCommandRegistry,
    build_engine_mailbox_command_registry,
)
from .events import EventSource, EventType
from .paths import RuntimePaths


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
        self._command_context = EngineMailboxCommandContext(config_path=config_path, hooks=hooks)
        self._command_registry: EngineMailboxCommandRegistry = build_engine_mailbox_command_registry()

    async def apply_command(self, envelope: ControlCommandEnvelope) -> OperationResult:
        execution = self._command_registry.dispatch(self._command_context, envelope)
        if execution.restart_file_watcher:
            await self._hooks.restart_file_watcher()
        return execution.operation

    def process_config_mailbox_between_stages(self) -> None:
        for command_path in list_incoming_command_paths(self._hooks.get_paths()):
            envelope: ControlCommandEnvelope | None = None
            try:
                envelope = read_command(command_path)
                if envelope.command not in {ControlCommand.RELOAD_CONFIG, ControlCommand.SET_CONFIG}:
                    break
                self._emit_command_received(envelope)
                execution = self._command_registry.dispatch(self._command_context, envelope)
                self._archive_mailbox_result(command_path, envelope=envelope, operation=execution.operation)
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
