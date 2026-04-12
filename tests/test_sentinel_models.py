from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

from millrace_engine.sentinel_models import (
    SentinelCadenceState,
    SentinelCapState,
    SentinelCheckRecord,
    SentinelMonitoringState,
    SentinelReport,
    SentinelState,
    SentinelSummary,
)


def test_sentinel_models_round_trip_with_deterministic_json() -> None:
    summary = SentinelSummary(
        status="monitoring",
        reason="recovery-request-pending",
        last_check_at="2026-04-11T10:00:00Z",
        next_check_at="2026-04-11T10:05:00Z",
        route_target="troubleshoot",
        monitoring_active=True,
        acknowledgment_required=True,
        current_interval_seconds=300,
        soft_cap_count=1,
        hard_cap_count=0,
        queued_recovery_request_id="sentinel-req-001",
    )
    cadence = SentinelCadenceState(
        schedule_started_at="2026-04-11T09:00:00Z",
        last_check_at="2026-04-11T10:00:00Z",
        next_check_at="2026-04-11T10:05:00Z",
        elapsed_seconds=3600,
        current_interval_seconds=300,
        current_step_index=3,
        reset_on_recovery=True,
    )
    caps = SentinelCapState(
        soft_cap_threshold=2,
        hard_cap_threshold=3,
        soft_cap_count=1,
        hard_cap_count=0,
        acknowledgment_required=True,
        halt_on_hard_cap=False,
        last_soft_cap_at="2026-04-11T10:00:00Z",
    )
    monitoring = SentinelMonitoringState(
        active=True,
        route_target="troubleshoot",
        queued_recovery_request_id="sentinel-req-001",
        incident_id="INC-001",
        incident_path=Path("agents/ideas/incidents/incoming/incident.md"),
        queued_at="2026-04-11T09:55:00Z",
        last_observed_progress_at="2026-04-11T09:50:00Z",
        last_observed_status_snapshot_hash="abc123",
        resolution="pending",
    )
    check = SentinelCheckRecord(
        check_id="check-001",
        checked_at="2026-04-11T10:00:00Z",
        trigger="watch",
        status="monitoring",
        reason="recovery-request-pending",
        route_target="troubleshoot",
        auto_queue_allowed=False,
        status_snapshot_hash="abc123",
        report_path=Path("agents/reports/sentinel/latest.json"),
        summary=summary,
    )
    state = SentinelState(
        updated_at="2026-04-11T10:00:00Z",
        enabled=True,
        lifecycle_status="monitoring",
        reason="recovery-request-pending",
        last_healthy_at="2026-04-11T09:45:00Z",
        latest_check_id=check.check_id,
        latest_report_path=Path("agents/reports/sentinel/latest.json"),
        cadence=cadence,
        caps=caps,
        monitoring=monitoring,
    )
    report = SentinelReport(
        generated_at="2026-04-11T10:00:00Z",
        status="monitoring",
        reason="recovery-request-pending",
        state_path=Path("agents/.runtime/sentinel/state.json"),
        summary_path=Path("agents/reports/sentinel/summary.json"),
        latest_check_path=Path("agents/.runtime/sentinel/checks/check-001.json"),
        summary=summary,
        cadence=cadence,
        caps=caps,
        monitoring=monitoring,
    )

    check_json = check.model_dump_json(indent=2)
    state_json = state.model_dump_json(indent=2)
    report_json = report.model_dump_json(indent=2)

    assert SentinelCheckRecord.model_validate_json(check_json) == check
    assert SentinelState.model_validate_json(state_json) == state
    assert SentinelReport.model_validate_json(report_json) == report
    assert json.loads(check_json)["report_path"] == "agents/reports/sentinel/latest.json"
    assert json.loads(report_json)["state_path"] == "agents/.runtime/sentinel/state.json"
    assert json.loads(state_json)["monitoring"]["incident_path"] == "agents/ideas/incidents/incoming/incident.md"


def test_sentinel_models_normalize_to_utc() -> None:
    state = SentinelState(
        updated_at=datetime(2026, 4, 11, 0, 0, tzinfo=timezone.utc),
        enabled=False,
        lifecycle_status="disabled",
        reason="sentinel-disabled-by-config",
        cadence=SentinelCadenceState(current_interval_seconds=1800),
        caps=SentinelCapState(),
    )

    assert state.updated_at == datetime(2026, 4, 11, 0, 0, tzinfo=timezone.utc)
    assert state.reason == "sentinel-disabled-by-config"
