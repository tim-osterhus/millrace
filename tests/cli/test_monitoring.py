from __future__ import annotations

from datetime import datetime, timezone
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
                "baseline_seed_package_version": "0.15.2",
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
    assert "baseline manifest=baseline-abc seed_package=0.15.2" in output
    assert "concurrency" in output
    assert "snapshot status execution=IDLE planning=IDLE learning=IDLE" in output
