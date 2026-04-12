from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from millrace_engine.events import EventRecord, EventSource, EventType, HistorySubscriber, JsonlEventSubscriber
from millrace_engine.research.governance_models import ProgressWatchdogReport, ProgressWatchdogState
from millrace_engine.sentinel_evidence import assess_meaningful_progress, collect_sentinel_evidence

from .support import runtime_paths, runtime_workspace


def _emit_event(
    paths,
    *,
    timestamp: datetime,
    event_type: EventType,
    source: EventSource,
    payload: dict[str, object],
) -> None:
    event = EventRecord.model_validate(
        {
            "type": event_type,
            "timestamp": timestamp,
            "source": source,
            "payload": payload,
        }
    )
    JsonlEventSubscriber(paths).handle(event)
    HistorySubscriber(paths).handle(event)


def _write_progress_watchdog(
    paths,
    *,
    updated_at: datetime,
    status: str,
    reason: str,
    visible_recovery_task_count: int,
) -> None:
    report = ProgressWatchdogReport(
        updated_at=updated_at,
        report_path="agents/.tmp/progress_watchdog_report.json",
        state_path="agents/.research_runtime/progress_watchdog_state.json",
        latch_path="agents/.research_runtime/recovery_latch.json",
        status=status,
        reason=reason,
        batch_id="batch-32",
        remediation_spec_id="spec-sentinel",
        visible_recovery_task_count=visible_recovery_task_count,
        escalation_action="monitor",
    )
    state = ProgressWatchdogState(
        updated_at=updated_at,
        batch_id="batch-32",
        status=status,
        reason=reason,
        remediation_spec_id="spec-sentinel",
        visible_recovery_task_count=visible_recovery_task_count,
        escalation_action="monitor",
    )
    paths.progress_watchdog_report_file.parent.mkdir(parents=True, exist_ok=True)
    paths.progress_watchdog_state_file.parent.mkdir(parents=True, exist_ok=True)
    paths.progress_watchdog_report_file.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    paths.progress_watchdog_state_file.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")


def test_collect_sentinel_evidence_detects_meaningful_progress_sources(tmp_path: Path) -> None:
    _, config_path = runtime_workspace(tmp_path)
    paths = runtime_paths(config_path)
    t0 = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)

    before = collect_sentinel_evidence(paths=paths, now=t0)

    paths.status_file.write_text("### BUILDER_RUNNING\n", encoding="utf-8")
    _emit_event(
        paths,
        timestamp=t0 + timedelta(minutes=1),
        event_type=EventType.STAGE_STARTED,
        source=EventSource.EXECUTION,
        payload={"task_id": "task-001", "stage": "builder", "run_id": "run-001"},
    )
    incident_path = paths.ideas_dir / "incidents" / "incoming" / "incident-001.md"
    incident_path.parent.mkdir(parents=True, exist_ok=True)
    incident_path.write_text("# Incident 001\n", encoding="utf-8")
    run_dir = paths.runs_dir / "run-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text('{"status":"running"}\n', encoding="utf-8")
    _write_progress_watchdog(
        paths,
        updated_at=t0 + timedelta(minutes=2),
        status="ready",
        reason="visible-recovery-work-present",
        visible_recovery_task_count=1,
    )

    after = collect_sentinel_evidence(paths=paths, now=t0 + timedelta(minutes=3))
    assessment = assess_meaningful_progress(after, previous=before, now=t0 + timedelta(minutes=3))

    assert assessment.state == "progressing"
    assert {"status_markers", "recent_events", "incident_queues", "progress_watchdog", "runs"} <= set(
        assessment.changed_sources
    )
    assert after.execution_status.marker == "BUILDER_RUNNING"
    assert after.recent_events[-1].event_type == EventType.STAGE_STARTED.value
    assert after.recent_history[-1].detail_exists is True
    assert after.progress_watchdog is not None
    assert after.progress_watchdog.status == "ready"


def test_collect_sentinel_evidence_ignores_runtime_noise_and_watchdog_heartbeat_rewrites(
    tmp_path: Path,
) -> None:
    _, config_path = runtime_workspace(tmp_path)
    paths = runtime_paths(config_path)
    t0 = datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc)

    paths.status_file.write_text("### BUILDER_RUNNING\n", encoding="utf-8")
    _emit_event(
        paths,
        timestamp=t0,
        event_type=EventType.STAGE_STARTED,
        source=EventSource.EXECUTION,
        payload={"task_id": "task-001", "stage": "builder", "run_id": "run-001"},
    )
    _write_progress_watchdog(
        paths,
        updated_at=t0 + timedelta(minutes=1),
        status="ready",
        reason="visible-recovery-work-present",
        visible_recovery_task_count=1,
    )
    before = collect_sentinel_evidence(paths=paths, now=t0 + timedelta(minutes=2))

    paths.status_file.write_text("### BUILDER_RUNNING\n", encoding="utf-8")
    _emit_event(
        paths,
        timestamp=t0 + timedelta(minutes=3),
        event_type=EventType.STATUS_CHANGED,
        source=EventSource.EXECUTION,
        payload={"previous": "BUILDER_RUNNING", "current": "BUILDER_RUNNING", "heartbeat_at": "2026-04-11T12:03:00Z"},
    )
    paths.sentinel_notification_attempts_dir.mkdir(parents=True, exist_ok=True)
    (paths.sentinel_notification_attempts_dir / "attempt-001.json").write_text(
        '{"notification":"retry"}\n',
        encoding="utf-8",
    )
    _write_progress_watchdog(
        paths,
        updated_at=t0 + timedelta(minutes=4),
        status="ready",
        reason="visible-recovery-work-present",
        visible_recovery_task_count=1,
    )

    after = collect_sentinel_evidence(paths=paths, now=t0 + timedelta(minutes=5))
    assessment = assess_meaningful_progress(after, previous=before, now=t0 + timedelta(minutes=5))

    assert after.progress_signature == before.progress_signature
    assert assessment.state == "stale"
    assert assessment.changed_sources == ()
    assert after.progress_watchdog is not None
    assert before.progress_watchdog is not None
    assert after.progress_watchdog.updated_at > before.progress_watchdog.updated_at
    assert after.progress_watchdog.signature == before.progress_watchdog.signature
