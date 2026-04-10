from __future__ import annotations

import asyncio
from pathlib import Path

from millrace_engine.contracts import ExecutionStatus, ResearchMode, ResearchStatus
from millrace_engine.control import EngineControl
from millrace_engine.engine import MillraceEngine
from millrace_engine.events import EventType
from millrace_engine.markdown import parse_task_cards
from millrace_engine.planes.execution import ExecutionCycleResult
from millrace_engine.research.state import ResearchRuntimeMode, load_research_runtime_state

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

    def poll_once(self) -> list[object]:
        return []

    def wakeup_timeout_seconds(self, timeout: float) -> float:
        return timeout


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


def test_engine_runtime_loop_daemon_advances_active_goalspec_checkpoint_on_next_tick(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    config_text = config_path.read_text(encoding="utf-8")
    config_text = config_text.replace('mode = "stub"', 'mode = "goalspec"', 1)
    config_path.write_text(config_text, encoding="utf-8")

    goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    goal_path.parent.mkdir(parents=True, exist_ok=True)
    goal_path.write_text("# queued\n", encoding="utf-8")

    engine = MillraceEngine(config_path)
    monkeypatch.setattr(engine, "_consume_research_recovery_latch", lambda **_kwargs: 0)
    monkeypatch.setattr(engine.research_plane, "shutdown", lambda: None)
    monkeypatch.setattr(engine.runtime_loop, "build_file_watcher", lambda: _WatcherStub(mode="poll"))

    async def _run_cycle() -> ExecutionCycleResult:
        engine.stop_requested = True
        return ExecutionCycleResult(run_id="daemon-goalspec-cycle", final_status=ExecutionStatus.IDLE)

    monkeypatch.setattr(engine.runtime_loop, "run_cycle", _run_cycle)

    result = asyncio.run(engine.runtime_loop.run(mode="daemon"))

    assert result.process_running is False
    state = load_research_runtime_state(workspace / "agents" / "research_state.json")
    assert state is not None
    assert state.current_mode is ResearchRuntimeMode.GOALSPEC
    assert state.checkpoint is not None
    assert state.checkpoint.node_id == "completion_manifest_draft"
    assert (workspace / "agents" / "research_status.md").read_text(encoding="utf-8") == (
        f"### {ResearchStatus.COMPLETION_MANIFEST_RUNNING.value}\n"
    )


def test_engine_runtime_loop_applies_deferred_active_clear_only_after_cycle_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    engine = MillraceEngine(config_path)
    control = EngineControl(config_path)
    control.add_task("Deferred active clear")
    engine.execution_plane.queue.promote_next()
    monkeypatch.setattr(engine, "_consume_research_recovery_latch", lambda **_kwargs: 0)
    monkeypatch.setattr(engine.research_plane, "shutdown", lambda: None)
    monkeypatch.setattr(engine.runtime_loop, "build_file_watcher", lambda: _WatcherStub(mode="poll"))

    cycle_calls = 0

    async def _run_cycle() -> ExecutionCycleResult:
        nonlocal cycle_calls
        cycle_calls += 1
        active_before = parse_task_cards((workspace / "agents" / "tasks.md").read_text(encoding="utf-8"))
        if cycle_calls == 1:
            assert len(active_before) == 1
            result = control.active_task_clear(reason="request during active stage")
            assert result.outcome_state.value == "deferred"
            assert parse_task_cards((workspace / "agents" / "tasks.md").read_text(encoding="utf-8")) == active_before
            return ExecutionCycleResult(run_id="daemon-cycle-1", final_status=ExecutionStatus.BUILDER_RUNNING)
        assert active_before == []
        engine.stop_requested = True
        return ExecutionCycleResult(run_id="daemon-cycle-2", final_status=ExecutionStatus.IDLE)

    monkeypatch.setattr(engine.runtime_loop, "run_cycle", _run_cycle)

    result = asyncio.run(engine.runtime_loop.run(mode="daemon"))

    assert cycle_calls == 2
    assert result.process_running is False
    assert parse_task_cards((workspace / "agents" / "tasks.md").read_text(encoding="utf-8")) == []
    runtime = control.status(detail=False).runtime
    assert runtime.pending_active_task_clear is None
    assert runtime.last_active_task_clear is not None
    assert runtime.last_active_task_clear.outcome_state.value == "applied"
