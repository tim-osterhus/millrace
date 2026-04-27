from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from millrace_ai.contracts import ExecutionStageName, LearningRequestDocument, TaskDocument
from millrace_ai.mailbox import write_mailbox_command
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
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


def _builder_complete_runner(request: StageRunRequest) -> RunnerRawResult:
    stdout_path = Path(request.run_dir) / "runner_stdout.txt"
    stdout_path.write_text("### BUILDER_COMPLETE\n", encoding="utf-8")
    return RunnerRawResult(
        request_id=request.request_id,
        run_id=request.run_id,
        stage=request.stage,
        runner_name="test-runner",
        model_name=request.model_name,
        exit_kind="completed",
        exit_code=0,
        stdout_path=str(stdout_path),
        stderr_path=None,
        terminal_result_path=None,
        observed_exit_kind=None,
        observed_exit_code=None,
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=1),
    )


def _analyst_complete_runner(request: StageRunRequest) -> RunnerRawResult:
    stdout_path = Path(request.run_dir) / "runner_stdout.txt"
    stdout_path.write_text("### ANALYST_COMPLETE\n", encoding="utf-8")
    return RunnerRawResult(
        request_id=request.request_id,
        run_id=request.run_id,
        stage=request.stage,
        runner_name="test-runner",
        model_name=request.model_name,
        exit_kind="completed",
        exit_code=0,
        stdout_path=str(stdout_path),
        stderr_path=None,
        terminal_result_path=None,
        observed_exit_kind=None,
        observed_exit_code=None,
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=1),
    )


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
    assert started.payload["baseline_seed_package_version"] == "0.15.6"
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


def test_runtime_tick_emits_stage_router_and_status_events(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    monitor = CaptureMonitor()
    engine = RuntimeEngine(paths, stage_runner=_builder_complete_runner, monitor=monitor)
    engine.startup()

    outcome = engine.tick()
    event_types = [event.event_type for event in monitor.events]

    assert outcome.stage is ExecutionStageName.BUILDER
    assert "status_marker_changed" in event_types
    assert "stage_started" in event_types
    assert "stage_completed" in event_types
    assert "router_decision" in event_types

    completed = next(event for event in monitor.events if event.event_type == "stage_completed")
    assert completed.payload["plane"] == "execution"
    assert completed.payload["stage"] == "builder"
    assert completed.payload["run_id"]
    assert completed.payload["duration_seconds"] == 1.0
    assert completed.payload["summary_status_marker"] == "### BUILDER_COMPLETE"


def test_runtime_learning_stage_monitor_event_includes_learning_identity(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_learning_request(
        LearningRequestDocument(
            learning_request_id="learn-001",
            title="Improve checker skill",
            requested_action="improve",
            target_skill_id="checker-core",
            created_at=NOW,
            created_by="tests",
        )
    )
    monitor = CaptureMonitor()
    engine = RuntimeEngine(
        paths,
        stage_runner=_analyst_complete_runner,
        monitor=monitor,
        mode_id="learning_codex",
    )
    engine.startup()
    engine.tick()

    started = next(event for event in monitor.events if event.event_type == "runtime_started")
    completed = next(event for event in monitor.events if event.event_type == "stage_completed")
    assert started.payload["concurrency_policy"]["may_run_concurrently"]
    assert completed.payload["plane"] == "learning"
    assert completed.payload["work_item_kind"] == "learning_request"
