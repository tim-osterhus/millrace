from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.contracts import TaskDocument
from millrace_ai.mailbox import write_mailbox_command
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.monitoring import RuntimeMonitorEvent, RuntimeMonitorSink

NOW = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _task_doc(task_id: str, *, created_at: datetime) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="monitor test task",
        target_paths=["src/millrace_ai/runtime/monitoring.py"],
        acceptance=["monitor event stream is emitted"],
        required_checks=["uv run --extra dev python -m pytest tests/runtime/test_monitoring.py -q"],
        references=["lab/specs/review/2026-04-21-millrace-daemon-basic-terminal-monitoring-spec.md"],
        risk=["monitor drift"],
        created_at=created_at,
        created_by="tests",
    )


def _mailbox_command(
    command_id: str,
    command: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "command_id": command_id,
        "command": command,
        "issued_at": NOW,
        "issuer": "tests",
        "payload": payload or {},
    }


def _no_op_stage_runner(request: StageRunRequest) -> RunnerRawResult:
    raise AssertionError("stage_runner should not be called")


class CaptureMonitor(RuntimeMonitorSink):
    def __init__(self) -> None:
        self.events: list[RuntimeMonitorEvent] = []

    def emit(self, event: RuntimeMonitorEvent) -> None:
        self.events.append(event)


def test_runtime_startup_emits_lifecycle_context(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    monitor = CaptureMonitor()
    engine = RuntimeEngine(paths, stage_runner=_no_op_stage_runner, monitor=monitor)
    engine.startup()

    started = next(event for event in monitor.events if event.event_type == "runtime_started")
    assert started.payload["compiled_plan_currentness"] == "current"
    assert started.payload["baseline_manifest_id"]
    assert started.payload["baseline_seed_package_version"] == "0.15.2"
    assert "execution" in started.payload["loop_ids_by_plane"]
    assert "status_markers_by_plane" in started.payload
    assert "queue_depths_by_plane" in started.payload


def test_runtime_tick_emits_idle_paused_and_stopped_monitor_events(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    monitor = CaptureMonitor()
    engine = RuntimeEngine(paths, stage_runner=_no_op_stage_runner, monitor=monitor)
    engine.startup()

    idle = engine.tick()
    assert idle.router_decision.reason == "no_work"
    assert any(event.event_type == "runtime_idle" for event in monitor.events)

    write_mailbox_command(paths, _mailbox_command("cmd-pause", "pause"))
    paused = engine.tick()
    assert paused.router_decision.reason == "paused"
    assert any(event.event_type == "runtime_paused" for event in monitor.events)

    write_mailbox_command(paths, _mailbox_command("cmd-stop", "stop"))
    stopped = engine.tick()
    assert stopped.router_decision.reason == "stop_requested"
    assert any(event.event_type == "runtime_stopped" for event in monitor.events)
