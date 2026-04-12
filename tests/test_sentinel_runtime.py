from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.control import EngineControl
from millrace_engine.control_actions import write_recovery_request_record
from millrace_engine.control_models import RecoveryRequestRecord, RecoveryRequestTarget
from millrace_engine.control_runtime_surface import supervisor_report
from millrace_engine.sentinel_models import SentinelCheckRecord, SentinelReport, SentinelState
from millrace_engine.sentinel_runtime import run_sentinel_diagnostic
import millrace_engine.sentinel_watch as sentinel_watch_module

from .support import runtime_workspace


def test_workspace_init_ships_first_class_sentinel_doc(tmp_path: Path) -> None:
    workspace, _ = runtime_workspace(tmp_path)
    sentinel_path = workspace / "SENTINEL.md"
    public_path = Path(__file__).resolve().parents[1] / "SENTINEL.md"

    assert sentinel_path.exists()
    assert sentinel_path.read_text(encoding="utf-8") == public_path.read_text(encoding="utf-8")


def test_run_sentinel_diagnostic_persists_bounded_artifacts_and_treats_idle_as_healthy(
    tmp_path: Path,
) -> None:
    _, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)
    control = EngineControl(config_path)
    report = supervisor_report(control)
    checked_at = datetime(2026, 4, 11, 20, 0, tzinfo=timezone.utc)

    state, latest_report, check = run_sentinel_diagnostic(
        config=loaded.config,
        paths=paths,
        supervisor_report=report,
        now=checked_at,
    )

    assert latest_report.status == "healthy"
    assert latest_report.reason == "execution-idle-is-neutral-when-no-stall-is-observed"
    assert latest_report.progress is not None
    assert latest_report.progress.state == "unknown"
    assert latest_report.evidence is not None
    assert latest_report.evidence.supervisor is not None
    assert latest_report.evidence.supervisor.execution_status == "IDLE"
    assert state.lifecycle_status == "idle"
    assert state.latest_check_id == check.check_id
    assert state.cadence.current_interval_seconds == 300
    assert check.status_snapshot_hash == latest_report.evidence.progress_signature

    persisted_state = SentinelState.model_validate_json(paths.sentinel_state_file.read_text(encoding="utf-8"))
    persisted_report = SentinelReport.model_validate_json(
        paths.sentinel_latest_report_file.read_text(encoding="utf-8")
    )
    persisted_check = SentinelCheckRecord.model_validate_json(
        (paths.sentinel_check_records_dir / f"{check.check_id}.json").read_text(encoding="utf-8")
    )

    assert persisted_state == state
    assert persisted_report == latest_report
    assert persisted_check == check
    assert paths.sentinel_summary_file.exists()


def test_run_sentinel_diagnostic_keeps_unchanged_idle_workspace_degraded_until_recovery_cycle_is_linked(
    tmp_path: Path,
) -> None:
    _, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)
    control = EngineControl(config_path)
    report = supervisor_report(control)
    t0 = datetime.now(timezone.utc)

    run_sentinel_diagnostic(
        config=loaded.config,
        paths=paths,
        supervisor_report=report,
        now=t0,
    )
    state, latest_report, check = run_sentinel_diagnostic(
        config=loaded.config,
        paths=paths,
        supervisor_report=report,
        now=t0 + timedelta(seconds=loaded.config.sentinel.progress_thresholds.no_progress_seconds + 1),
    )

    assert latest_report.status == "degraded"
    assert latest_report.progress is not None
    assert latest_report.progress.state == "stale"
    assert latest_report.monitoring is None
    assert check.auto_queue_allowed is True
    assert state.lifecycle_status == "idle"


def test_run_sentinel_diagnostic_allows_manual_diagnostic_when_sentinel_is_disabled(
    tmp_path: Path,
) -> None:
    _, config_path = runtime_workspace(tmp_path)
    control = EngineControl(config_path)
    control.config_set("sentinel.enabled", "false")

    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)
    report = supervisor_report(control)
    checked_at = datetime(2026, 4, 11, 21, 0, tzinfo=timezone.utc)

    state, latest_report, check = run_sentinel_diagnostic(
        config=loaded.config,
        paths=paths,
        supervisor_report=report,
        autonomous_state_applied=False,
        now=checked_at,
    )

    assert loaded.config.sentinel.enabled is False
    assert latest_report.status == "disabled"
    assert latest_report.reason == "manual-diagnostic-only-while-sentinel-disabled"
    assert latest_report.evidence is not None
    assert state.enabled is False
    assert state.lifecycle_status == "disabled"
    assert state.latest_check_id == check.check_id
    assert state.cadence.current_interval_seconds == 0
    assert state.cadence.last_check_at is None
    assert state.cadence.next_check_at is None
    assert state.caps.soft_cap_count == 0
    assert state.caps.hard_cap_count == 0

    persisted_state = SentinelState.model_validate_json(paths.sentinel_state_file.read_text(encoding="utf-8"))
    persisted_report = SentinelReport.model_validate_json(
        paths.sentinel_latest_report_file.read_text(encoding="utf-8")
    )

    assert persisted_state == state
    assert persisted_report == latest_report


def test_run_sentinel_diagnostic_enters_monitoring_mode_and_suppresses_repeat_route_for_linked_recovery_cycle(
    tmp_path: Path,
) -> None:
    _, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)
    control = EngineControl(config_path)
    report = supervisor_report(control)
    t0 = datetime(2026, 4, 11, 20, 0, tzinfo=timezone.utc)

    run_sentinel_diagnostic(
        config=loaded.config,
        paths=paths,
        supervisor_report=report,
        now=t0,
    )
    request = RecoveryRequestRecord(
        request_id="recovery-20260411T200010000000Z-troubleshoot",
        requested_at=t0 + timedelta(seconds=10),
        target=RecoveryRequestTarget.TROUBLESHOOT,
        issuer="sentinel.test",
        reason="execution stalled",
        force_queue=True,
        source="manual",
        mode="direct",
    )
    write_recovery_request_record(paths, request)
    control.sentinel_incident(
        failure_signature="sentinel:no-progress",
        summary="Sentinel detected no meaningful progress.",
        severity="S2",
        routing_target="troubleshoot",
        recovery_request_id=request.request_id,
        issuer="sentinel.test",
    )

    state, latest_report, check = run_sentinel_diagnostic(
        config=loaded.config,
        paths=paths,
        supervisor_report=report,
        now=t0 + timedelta(seconds=20),
    )

    assert latest_report.status == "monitoring"
    assert latest_report.reason == "recovery-cycle-pending-while-repeat-route-remains-suppressed"
    assert latest_report.monitoring is not None
    assert latest_report.monitoring.active is True
    assert latest_report.monitoring.route_target == "troubleshoot"
    assert latest_report.monitoring.queued_recovery_request_id == request.request_id
    assert latest_report.monitoring.resolution == "pending"
    assert latest_report.monitoring.suppression_active is True
    assert latest_report.monitoring.suppression_reason == "repeat-route-suppressed-for-unresolved-monitoring-cycle"
    assert latest_report.monitoring.incident_id == state.last_incident_id
    assert latest_report.monitoring.incident_path == state.last_incident_path
    assert check.auto_queue_allowed is False
    assert check.route_target == "troubleshoot"
    assert state.lifecycle_status == "monitoring"
    assert state.last_recovery_request_id == request.request_id


def test_run_sentinel_diagnostic_resolves_and_escalates_monitoring_cycles_intentionally(
    tmp_path: Path,
) -> None:
    _, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)
    control = EngineControl(config_path)
    report = supervisor_report(control)
    t0 = datetime(2026, 4, 11, 20, 0, tzinfo=timezone.utc)

    run_sentinel_diagnostic(
        config=loaded.config,
        paths=paths,
        supervisor_report=report,
        now=t0,
    )
    request = RecoveryRequestRecord(
        request_id="recovery-20260411T200010000000Z-mechanic",
        requested_at=t0 + timedelta(seconds=10),
        target=RecoveryRequestTarget.MECHANIC,
        issuer="sentinel.test",
        reason="research stalled",
        force_queue=True,
        source="manual",
        mode="direct",
    )
    write_recovery_request_record(paths, request)
    control.sentinel_incident(
        failure_signature="sentinel:research-stalled",
        summary="Sentinel detected a stalled research plane.",
        severity="S3",
        routing_target="mechanic",
        recovery_request_id=request.request_id,
        issuer="sentinel.test",
    )

    stalled_state, stalled_report, stalled_check = run_sentinel_diagnostic(
        config=loaded.config,
        paths=paths,
        supervisor_report=report,
        now=t0 + timedelta(seconds=loaded.config.sentinel.progress_thresholds.no_progress_seconds + 11),
    )

    assert stalled_report.monitoring is not None
    assert stalled_report.monitoring.active is True
    assert stalled_report.monitoring.resolution == "stalled"
    assert stalled_check.auto_queue_allowed is False

    run_dir = paths.runs_dir / "run-progress-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "stdout.log").write_text("progress\n", encoding="utf-8")

    resolved_state, resolved_report, resolved_check = run_sentinel_diagnostic(
        config=loaded.config,
        paths=paths,
        supervisor_report=report,
        now=t0 + timedelta(seconds=loaded.config.sentinel.progress_thresholds.no_progress_seconds + 40),
    )

    assert resolved_report.status == "healthy"
    assert resolved_report.reason == "recovery-cycle-resolved-after-meaningful-progress"
    assert resolved_report.monitoring is not None
    assert resolved_report.monitoring.active is False
    assert resolved_report.monitoring.resolution == "resolved"
    assert resolved_report.monitoring.suppression_active is False
    assert resolved_check.auto_queue_allowed is True
    assert resolved_state.lifecycle_status == "idle"

    later_state, later_report, later_check = run_sentinel_diagnostic(
        config=loaded.config,
        paths=paths,
        supervisor_report=report,
        now=t0 + timedelta(seconds=loaded.config.sentinel.progress_thresholds.no_progress_seconds + 120),
    )

    assert later_report.status == "degraded"
    assert later_report.monitoring is not None
    assert later_report.monitoring.active is False
    assert later_report.monitoring.resolution == "resolved"
    assert later_check.auto_queue_allowed is True
    assert later_state.lifecycle_status == "idle"

    escalated_state, escalated_report, escalated_check = run_sentinel_diagnostic(
        config=loaded.config,
        paths=paths,
        supervisor_report=None,
        supervisor_error="mailbox unavailable",
        now=t0 + timedelta(seconds=loaded.config.sentinel.progress_thresholds.no_progress_seconds + 80),
    )

    assert escalated_report.status == "degraded"
    assert escalated_report.reason == "supervisor-observation-unavailable: mailbox unavailable"
    assert escalated_report.monitoring is not None
    assert escalated_report.monitoring.active is False
    assert escalated_report.monitoring.resolution == "escalated"
    assert escalated_report.monitoring.suppression_active is False
    assert escalated_check.auto_queue_allowed is True
    assert escalated_state.lifecycle_status == "idle"


def test_run_sentinel_diagnostic_rebases_monitoring_to_newer_linked_recovery_cycle(
    tmp_path: Path,
) -> None:
    _, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)
    control = EngineControl(config_path)
    report = supervisor_report(control)
    t0 = datetime(2026, 4, 11, 20, 0, tzinfo=timezone.utc)

    run_sentinel_diagnostic(
        config=loaded.config,
        paths=paths,
        supervisor_report=report,
        now=t0,
    )
    request_a = RecoveryRequestRecord(
        request_id="recovery-20260411T200010000000Z-troubleshoot",
        requested_at=t0 + timedelta(seconds=10),
        target=RecoveryRequestTarget.TROUBLESHOOT,
        issuer="sentinel.test",
        reason="execution stalled",
        force_queue=True,
        source="manual",
        mode="direct",
    )
    write_recovery_request_record(paths, request_a)
    control.sentinel_incident(
        failure_signature="sentinel:no-progress-a",
        summary="Sentinel detected the first stalled condition.",
        severity="S2",
        routing_target="troubleshoot",
        recovery_request_id=request_a.request_id,
        issuer="sentinel.test",
    )
    state_a, report_a, check_a = run_sentinel_diagnostic(
        config=loaded.config,
        paths=paths,
        supervisor_report=report,
        now=t0 + timedelta(seconds=20),
    )

    assert state_a.monitoring is not None
    assert state_a.monitoring.queued_recovery_request_id == request_a.request_id
    assert state_a.last_recovery_request_id == request_a.request_id
    assert check_a.auto_queue_allowed is False

    request_b = RecoveryRequestRecord(
        request_id="recovery-20260411T200040000000Z-mechanic",
        requested_at=t0 + timedelta(seconds=40),
        target=RecoveryRequestTarget.MECHANIC,
        issuer="sentinel.test",
        reason="research also stalled",
        force_queue=True,
        source="manual",
        mode="direct",
    )
    write_recovery_request_record(paths, request_b)
    control.sentinel_incident(
        failure_signature="sentinel:no-progress-b",
        summary="Sentinel detected a newer stalled condition.",
        severity="S2",
        routing_target="mechanic",
        recovery_request_id=request_b.request_id,
        issuer="sentinel.test",
    )

    rebased_state, rebased_report, rebased_check = run_sentinel_diagnostic(
        config=loaded.config,
        paths=paths,
        supervisor_report=report,
        now=t0 + timedelta(seconds=50),
    )

    assert rebased_state.last_recovery_request_id == request_b.request_id
    assert rebased_state.monitoring is not None
    assert rebased_state.monitoring.queued_recovery_request_id == request_b.request_id
    assert rebased_state.monitoring.route_target == "mechanic"
    assert rebased_report.summary.queued_recovery_request_id == request_b.request_id
    assert rebased_report.monitoring is not None
    assert rebased_report.monitoring.queued_recovery_request_id == request_b.request_id
    assert rebased_report.monitoring.route_target == "mechanic"
    assert rebased_check.route_target == "mechanic"
    assert rebased_check.auto_queue_allowed is False


def test_sentinel_watch_runs_repeated_checks_and_persists_check_count(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, config_path = runtime_workspace(tmp_path)
    control = EngineControl(config_path)
    t0 = datetime(2026, 4, 11, 20, 0, tzinfo=timezone.utc)
    observed_sleeps: list[float] = []
    moments = iter((t0, t0, t0 + timedelta(minutes=5)))

    monkeypatch.setattr(sentinel_watch_module, "_utc_now", lambda: next(moments))
    monkeypatch.setattr(sentinel_watch_module.time, "sleep", lambda seconds: observed_sleeps.append(seconds))

    watch = control.sentinel_watch(max_checks=2)

    assert watch.config_enabled is True
    assert watch.autonomous_state_applied is True
    assert watch.iterations_completed == 2
    assert watch.stop_reason == "max_checks_reached"
    assert watch.check.trigger == "watch"
    assert watch.state.checks_performed == 2
    assert watch.report.summary.checks_performed == 2
    assert watch.state.cadence.last_check_at == t0 + timedelta(minutes=5)
    assert watch.state.cadence.next_check_at == t0 + timedelta(minutes=10)
    assert observed_sleeps == [300.0]


def test_sentinel_watch_stops_after_one_disabled_diagnostic_pass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, config_path = runtime_workspace(tmp_path)
    control = EngineControl(config_path)
    control.config_set("sentinel.enabled", "false")
    t0 = datetime(2026, 4, 11, 21, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(sentinel_watch_module, "_utc_now", lambda: t0)
    monkeypatch.setattr(sentinel_watch_module.time, "sleep", lambda seconds: None)

    watch = control.sentinel_watch(max_checks=2)

    assert watch.config_enabled is False
    assert watch.autonomous_state_applied is False
    assert watch.iterations_completed == 1
    assert watch.stop_reason == "no_next_check_scheduled"
    assert watch.report.status == "disabled"
    assert watch.state.enabled is False
    assert watch.state.checks_performed == 1
    assert watch.state.cadence.last_check_at is None
    assert watch.state.cadence.next_check_at is None


def test_sentinel_watch_stops_after_one_disabled_pass_even_with_prior_enabled_cadence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, config_path = runtime_workspace(tmp_path)
    control = EngineControl(config_path)
    initial_checked_at = datetime(2026, 4, 11, 19, 0, tzinfo=timezone.utc)
    control.sentinel_check(now=initial_checked_at)
    control.config_set("sentinel.enabled", "false")
    disabled_checked_at = datetime(2026, 4, 11, 20, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(sentinel_watch_module, "_utc_now", lambda: disabled_checked_at)
    monkeypatch.setattr(sentinel_watch_module.time, "sleep", lambda seconds: None)

    watch = control.sentinel_watch(max_checks=3)

    assert watch.config_enabled is False
    assert watch.autonomous_state_applied is False
    assert watch.iterations_completed == 1
    assert watch.stop_reason == "no_next_check_scheduled"
    assert watch.report.status == "disabled"
    assert watch.state.enabled is False
    assert watch.state.checks_performed == 2
    assert watch.state.cadence.current_interval_seconds == 0
    assert watch.state.cadence.last_check_at is None
    assert watch.state.cadence.next_check_at is None
