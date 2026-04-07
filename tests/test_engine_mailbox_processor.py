from __future__ import annotations

import asyncio
import json
from pathlib import Path

from millrace_engine.adapters.control_mailbox import ControlCommand, write_command
from millrace_engine.config import LoadedConfig, build_runtime_paths, load_engine_config
from millrace_engine.control_models import OperationResult
from millrace_engine.engine_mailbox_command_handlers import (
    EngineMailboxCommandContext,
    build_engine_mailbox_command_registry,
)
from millrace_engine.engine_mailbox_processor import EngineMailboxHooks, EngineMailboxProcessor
from millrace_engine.events import EventSource, EventType
from millrace_engine.paths import RuntimePaths

from .support import load_workspace_fixture


class _MailboxHarness:
    def __init__(self, config_path: Path) -> None:
        self.loaded: LoadedConfig = load_engine_config(config_path)
        self.paths: RuntimePaths = build_runtime_paths(self.loaded.config)
        self.events: list[tuple[EventType, EventSource, dict[str, object]]] = []
        self.stop_requested = False
        self.paused = False
        self.pause_reason: str | None = None
        self.pause_run_id: str | None = None
        self.restart_count = 0
        self.should_restart_watcher = False
        self.latch_calls: list[dict[str, object]] = []

    def hooks(self) -> EngineMailboxHooks:
        return EngineMailboxHooks(
            get_paths=lambda: self.paths,
            get_loaded=lambda: self.loaded,
            emit_event=self._emit_event,
            queue_or_apply_reloaded_config=self._queue_or_apply_reloaded_config,
            restart_file_watcher=self._restart_file_watcher,
            consume_research_recovery_latch=self._consume_research_recovery_latch,
            get_stop_requested=lambda: self.stop_requested,
            set_stop_requested=self._set_stop_requested,
            get_paused=lambda: self.paused,
            set_pause_state=self._set_pause_state,
        )

    def _emit_event(self, event_type: EventType, source: EventSource, payload: dict[str, object]) -> None:
        self.events.append((event_type, source, payload))

    def _queue_or_apply_reloaded_config(
        self,
        loaded: LoadedConfig,
        *,
        command_id: str | None,
        key: str | None = None,
    ) -> tuple[OperationResult, bool]:
        self.loaded = loaded
        payload: dict[str, object] = {"command_id": command_id}
        if key is not None:
            payload["key"] = key
        return (
            OperationResult(mode="direct", applied=True, message="config handled", payload=payload),
            self.should_restart_watcher,
        )

    async def _restart_file_watcher(self) -> None:
        self.restart_count += 1

    def _consume_research_recovery_latch(self, **kwargs: object) -> int:
        self.latch_calls.append(dict(kwargs))
        return 0

    def _set_stop_requested(self, requested: bool) -> None:
        self.stop_requested = requested

    def _set_pause_state(self, paused: bool, reason: str | None, run_id: str | None) -> None:
        self.paused = paused
        self.pause_reason = reason
        self.pause_run_id = run_id


def test_engine_mailbox_processor_only_consumes_config_commands_between_stages(tmp_path: Path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    harness = _MailboxHarness(config_path)
    processor = EngineMailboxProcessor(config_path=config_path, hooks=harness.hooks())

    reload_envelope = write_command(harness.paths, ControlCommand.RELOAD_CONFIG)
    stop_envelope = write_command(harness.paths, ControlCommand.STOP)

    processor.process_config_mailbox_between_stages()

    processed_path = harness.paths.commands_processed_dir / f"{reload_envelope.command_id}.json"
    incoming_path = harness.paths.commands_incoming_dir / f"{stop_envelope.command_id}.json"
    assert processed_path.exists()
    assert incoming_path.exists()
    assert sorted(path.name for path in harness.paths.commands_failed_dir.glob("*.json")) == []

    archived_payload = json.loads(processed_path.read_text(encoding="utf-8"))
    assert archived_payload["envelope"]["command"] == ControlCommand.RELOAD_CONFIG.value
    assert archived_payload["result"]["ok"] is True
    assert harness.stop_requested is False
    assert [event[0] for event in harness.events] == [
        EventType.CONTROL_COMMAND_RECEIVED,
        EventType.CONTROL_COMMAND_APPLIED,
    ]


def test_engine_mailbox_processor_archives_failed_command_results(tmp_path: Path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    harness = _MailboxHarness(config_path)
    processor = EngineMailboxProcessor(config_path=config_path, hooks=harness.hooks())

    envelope = write_command(harness.paths, ControlCommand.ADD_TASK, payload={})

    asyncio.run(processor.process_mailbox())

    failed_path = harness.paths.commands_failed_dir / f"{envelope.command_id}.json"
    assert failed_path.exists()
    assert sorted(path.name for path in harness.paths.commands_processed_dir.glob("*.json")) == []
    assert sorted(path.name for path in harness.paths.commands_incoming_dir.glob("*.json")) == []

    archived_payload = json.loads(failed_path.read_text(encoding="utf-8"))
    assert archived_payload["envelope"]["command"] == ControlCommand.ADD_TASK.value
    assert archived_payload["result"]["ok"] is False
    assert archived_payload["result"]["message"] == "add_task requires a title"
    assert [event[0] for event in harness.events] == [
        EventType.CONTROL_COMMAND_RECEIVED,
        EventType.CONTROL_COMMAND_APPLIED,
    ]


def test_engine_mailbox_command_registry_dispatches_lifecycle_and_task_families(tmp_path: Path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    harness = _MailboxHarness(config_path)
    registry = build_engine_mailbox_command_registry()
    context = EngineMailboxCommandContext(config_path=config_path, hooks=harness.hooks())

    pause_execution = registry.dispatch(
        context,
        write_command(harness.paths, ControlCommand.PAUSE).model_copy(update={"command_id": "pause-command"}),
    )
    assert pause_execution.operation.message == "engine paused"
    assert pause_execution.operation.applied is True
    assert harness.paused is True
    assert harness.events == [
        (
            EventType.ENGINE_PAUSED,
            EventSource.CONTROL,
            {"command_id": "pause-command"},
        )
    ]

    add_task_execution = registry.dispatch(
        context,
        write_command(
            harness.paths,
            ControlCommand.ADD_TASK,
            payload={"title": "Registry task"},
        ).model_copy(update={"command_id": "add-task-command"}),
    )
    backlog_path = harness.paths.backlog_file
    assert add_task_execution.operation.message == "task added"
    assert add_task_execution.operation.applied is True
    assert "Registry task" in backlog_path.read_text(encoding="utf-8")
    assert harness.latch_calls[-1]["trigger"] == "add_task"


def test_engine_mailbox_command_registry_marks_config_restart_requests(tmp_path: Path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "config_hotswap")
    harness = _MailboxHarness(config_path)
    harness.should_restart_watcher = True
    registry = build_engine_mailbox_command_registry()
    context = EngineMailboxCommandContext(config_path=config_path, hooks=harness.hooks())

    execution = registry.dispatch(
        context,
        write_command(
            harness.paths,
            ControlCommand.SET_CONFIG,
            payload={"key": "watchers.debounce_seconds", "value": "2"},
        ).model_copy(update={"command_id": "config-command"}),
    )

    assert execution.operation.message == "config handled"
    assert execution.operation.applied is True
    assert execution.operation.payload == {"command_id": "config-command", "key": "watchers.debounce_seconds"}
    assert execution.restart_file_watcher is True
