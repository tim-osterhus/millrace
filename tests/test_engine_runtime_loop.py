from __future__ import annotations

import asyncio
from pathlib import Path

from millrace_engine.contracts import ExecutionStatus
from millrace_engine.engine import MillraceEngine
from millrace_engine.events import EventType
from millrace_engine.markdown import parse_task_cards
from millrace_engine.planes.execution import ExecutionCycleResult

from .support import load_workspace_fixture


class _WatcherStub:
    def __init__(self, *, mode: str = "watch") -> None:
        self.mode = mode
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1


def test_engine_runtime_loop_restart_replaces_existing_watcher(tmp_path: Path, monkeypatch) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "config_hotswap")
    engine = MillraceEngine(config_path)
    old_watcher = _WatcherStub(mode="watch")
    new_watcher = _WatcherStub(mode="watch")

    engine.runtime_loop.input_queue = asyncio.Queue()
    engine.runtime_loop.file_watcher = old_watcher  # type: ignore[assignment]
    monkeypatch.setattr(engine.runtime_loop, "build_file_watcher", lambda: new_watcher)

    asyncio.run(engine.runtime_loop.restart_file_watcher())

    assert old_watcher.stopped == 1
    assert new_watcher.started == 1
    assert engine.runtime_loop.file_watcher is new_watcher


def test_engine_runtime_loop_reload_restarts_watcher_when_requested(tmp_path: Path, monkeypatch) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "config_hotswap")
    engine = MillraceEngine(config_path)
    trigger_path = Path("/tmp/runtime-loop-config.toml")
    seen_trigger_paths: list[Path | None] = []
    restart_calls: list[str] = []

    async def _reload_config_from_disk(*, trigger_path: Path | None = None) -> tuple[bool, bool]:
        seen_trigger_paths.append(trigger_path)
        return True, True

    async def _restart_file_watcher() -> None:
        restart_calls.append("restart")

    monkeypatch.setattr(engine.config_coordinator, "reload_config_from_disk", _reload_config_from_disk)
    monkeypatch.setattr(engine.runtime_loop, "restart_file_watcher", _restart_file_watcher)

    applied = asyncio.run(engine.runtime_loop.reload_config_from_disk(trigger_path=trigger_path))

    assert applied is True
    assert seen_trigger_paths == [trigger_path]
    assert restart_calls == ["restart"]


def test_engine_runtime_loop_once_skips_execution_cycle_after_startup_research_sync(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    engine = MillraceEngine(config_path)
    run_cycle_calls: list[str] = []

    monkeypatch.setattr(engine, "_consume_research_recovery_latch", lambda **_kwargs: 0)
    monkeypatch.setattr(engine, "_sync_ready_research_dispatch", lambda **_kwargs: object())
    monkeypatch.setattr(engine.research_plane, "shutdown", lambda: None)

    async def _run_cycle():
        run_cycle_calls.append("run_cycle")
        return None

    monkeypatch.setattr(engine.runtime_loop, "run_cycle", _run_cycle)

    result = asyncio.run(engine.runtime_loop.run(mode="once"))

    assert run_cycle_calls == []
    assert result.process_running is False
    assert result.mode == "once"


def test_engine_runtime_loop_emits_backlog_empty_audit_after_archival_progress(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "backlog_empty")
    engine = MillraceEngine(config_path)
    captured: list[object] = []

    class _Capture:
        def handle(self, event) -> None:
            captured.append(event)

    engine.event_bus.subscribe(_Capture())
    archived_task = parse_task_cards(
        "\n".join(
            [
                "# Task Backlog",
                "",
                "## 2026-03-19 - Ship the happy path",
                "",
                "- **Goal:** Execute one bounded runtime task.",
                "- **Spec-ID:** SPEC-HAPPY-PATH",
                "",
            ]
        )
    )[0]

    engine.runtime_loop.emit_cycle_events(
        ExecutionCycleResult(
            run_id="run-backlog-empty-after-progress",
            final_status=ExecutionStatus.IDLE,
            archived_task=archived_task,
            backlog_empty_after_progress=True,
        )
    )

    event_types = [event.type for event in captured]
    assert event_types[0] is EventType.TASK_ARCHIVED
    assert EventType.BACKLOG_EMPTY_AUDIT in event_types
    backlog_empty_event = next(event for event in captured if event.type is EventType.BACKLOG_EMPTY_AUDIT)
    assert backlog_empty_event.payload["after_progress"] is True
    assert backlog_empty_event.payload["task_id"] == archived_task.task_id
    assert backlog_empty_event.payload["title"] == archived_task.title
