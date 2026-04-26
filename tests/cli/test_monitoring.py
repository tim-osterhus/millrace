from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import StringIO

from millrace_ai.cli.monitoring import BasicTerminalMonitor
from millrace_ai.runtime.monitoring import RuntimeMonitorEvent

NOW = datetime(2026, 4, 25, 12, 14, 3, tzinfo=timezone.utc)


def test_basic_terminal_monitor_renders_startup_context_lines() -> None:
    stream = StringIO()
    monitor = BasicTerminalMonitor(stream=stream)
    monitor.emit(
        RuntimeMonitorEvent(
            event_type="runtime_started",
            occurred_at=NOW,
            payload={
                "mode_id": "learning_codex",
                "compiled_plan_id": "plan-123",
                "compiled_plan_currentness": "current",
                "baseline_manifest_id": "baseline-abc",
                "baseline_seed_package_version": "0.15.3",
                "loop_ids_by_plane": {
                    "execution": "execution.standard",
                    "planning": "planning.standard",
                    "learning": "learning.standard",
                },
                "concurrency_policy": {
                    "mutually_exclusive_planes": [["execution", "planning"]],
                    "may_run_concurrently": [["learning", "execution"]],
                },
                "status_markers_by_plane": {
                    "execution": "### IDLE",
                    "planning": "### IDLE",
                    "learning": "### IDLE",
                },
                "queue_depths_by_plane": {
                    "execution": 2,
                    "planning": 0,
                    "learning": 1,
                },
            },
        )
    )

    output = stream.getvalue()
    assert "runtime started mode=learning_codex plan=plan-123 currentness=current" in output
    assert "baseline manifest=baseline-abc seed_package=0.15.3" in output
    assert "concurrency" in output
    assert "snapshot status execution=IDLE planning=IDLE learning=IDLE" in output


def test_basic_terminal_monitor_renders_stage_done_and_run_update_lines() -> None:
    stream = StringIO()
    monitor = BasicTerminalMonitor(stream=stream)
    event = RuntimeMonitorEvent(
        event_type="stage_completed",
        occurred_at=NOW,
        payload={
            "plane": "execution",
            "stage": "builder",
            "node_id": "builder",
            "stage_kind_id": "builder",
            "run_id": "run-123",
            "terminal_result": "BUILDER_COMPLETE",
            "summary_status_marker": "### BUILDER_COMPLETE",
            "duration_seconds": 39.2,
            "started_at": NOW.isoformat(),
            "completed_at": (NOW + timedelta(seconds=39, milliseconds=200)).isoformat(),
            "token_usage": {
                "input_tokens": 1200,
                "cached_input_tokens": 300,
                "output_tokens": 410,
                "thinking_tokens": 900,
                "total_tokens": 2810,
            },
        },
    )

    monitor.emit(event)

    output = stream.getvalue()
    assert "stage done plane=execution stage=builder" in output
    assert "tokens=in=1200 cached=300 out=410 think=900 total=2810" in output
    assert "run update plane=execution run=run-123 elapsed=39.2s" in output


def test_basic_terminal_monitor_suppresses_redundant_stage_status_lines() -> None:
    stream = StringIO()
    monitor = BasicTerminalMonitor(stream=stream)
    monitor.emit(
        RuntimeMonitorEvent(
            event_type="status_marker_changed",
            occurred_at=NOW,
            payload={
                "plane": "execution",
                "run_id": "run-123",
                "previous_marker": "### IDLE",
                "current_marker": "### BUILDER_RUNNING",
                "source": "stage_started",
            },
        )
    )
    assert stream.getvalue() == ""


def test_basic_terminal_monitor_renders_independent_status_lines() -> None:
    stream = StringIO()
    monitor = BasicTerminalMonitor(stream=stream)
    monitor.emit(
        RuntimeMonitorEvent(
            event_type="status_marker_changed",
            occurred_at=NOW,
            payload={
                "plane": "planning",
                "run_id": "run-456",
                "previous_marker": "### MANAGER_RUNNING",
                "current_marker": "### NEEDS_EXECUTION",
                "source": "result_application",
            },
        )
    )
    assert "status plane=planning run=run-456 from=MANAGER_RUNNING to=NEEDS_EXECUTION" in stream.getvalue()


def test_basic_terminal_monitor_renders_unknown_tokens_when_usage_missing() -> None:
    stream = StringIO()
    monitor = BasicTerminalMonitor(stream=stream)
    monitor.emit(
        RuntimeMonitorEvent(
            event_type="stage_completed",
            occurred_at=NOW,
            payload={
                "plane": "learning",
                "stage": "analyst",
                "node_id": "analyst",
                "stage_kind_id": "analyst",
                "run_id": "run-learning-1",
                "terminal_result": "ANALYST_COMPLETE",
                "summary_status_marker": "### ANALYST_COMPLETE",
                "started_at": NOW.isoformat(),
                "completed_at": (NOW + timedelta(seconds=5)).isoformat(),
                "duration_seconds": 5.0,
                "token_usage": None,
            },
        )
    )
    assert "tokens=unknown" in stream.getvalue()


def test_basic_terminal_monitor_keys_aggregates_by_plane_and_run() -> None:
    stream = StringIO()
    monitor = BasicTerminalMonitor(stream=stream)
    monitor.emit(
        RuntimeMonitorEvent(
            event_type="stage_completed",
            occurred_at=NOW,
            payload={
                "plane": "execution",
                "stage": "builder",
                "node_id": "builder",
                "stage_kind_id": "builder",
                "run_id": "run-shared",
                "terminal_result": "BUILDER_COMPLETE",
                "summary_status_marker": "### BUILDER_COMPLETE",
                "started_at": NOW.isoformat(),
                "completed_at": (NOW + timedelta(seconds=10)).isoformat(),
                "duration_seconds": 10.0,
                "token_usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 0,
                    "output_tokens": 10,
                    "thinking_tokens": 5,
                    "total_tokens": 115,
                },
            },
        )
    )
    monitor.emit(
        RuntimeMonitorEvent(
            event_type="stage_completed",
            occurred_at=NOW,
            payload={
                "plane": "learning",
                "stage": "analyst",
                "node_id": "analyst",
                "stage_kind_id": "analyst",
                "run_id": "run-shared",
                "terminal_result": "ANALYST_COMPLETE",
                "summary_status_marker": "### ANALYST_COMPLETE",
                "started_at": NOW.isoformat(),
                "completed_at": (NOW + timedelta(seconds=5)).isoformat(),
                "duration_seconds": 5.0,
                "token_usage": {
                    "input_tokens": 7,
                    "cached_input_tokens": 0,
                    "output_tokens": 3,
                    "thinking_tokens": 2,
                    "total_tokens": 12,
                },
            },
        )
    )
    output = stream.getvalue()
    assert (
        "run update plane=execution run=run-shared elapsed=10.0s "
        "tokens=in=100 cached=0 out=10 think=5 total=115"
    ) in output
    assert (
        "run update plane=learning run=run-shared elapsed=5.0s "
        "tokens=in=7 cached=0 out=3 think=2 total=12"
    ) in output
