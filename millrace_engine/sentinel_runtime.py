"""Bounded Sentinel diagnostic persistence helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from .config import EngineConfig
from .control_models import RecoveryRequestRecord, SupervisorReport
from .markdown import write_text_atomic
from .paths import RuntimePaths
from .sentinel_evidence import assess_meaningful_progress, collect_sentinel_evidence
from .sentinel_models import (
    SentinelAcknowledgmentState,
    SentinelCapState,
    SentinelCadenceState,
    SentinelCheckRecord,
    SentinelMonitoringState,
    SentinelReport,
    SentinelState,
    SentinelSummary,
)


def _normalize_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    moment = value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _relative_path(path: Path, *, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _check_id_for(moment: datetime) -> str:
    return f"sentinel-{moment.strftime('%Y%m%dT%H%M%SZ')}"


def persist_sentinel_artifacts(
    paths: RuntimePaths,
    *,
    state: SentinelState,
    summary: SentinelSummary,
    report: SentinelReport,
    check: SentinelCheckRecord,
) -> None:
    check_path = paths.sentinel_check_records_dir / f"{check.check_id}.json"
    paths.sentinel_runtime_dir.mkdir(parents=True, exist_ok=True)
    paths.sentinel_check_records_dir.mkdir(parents=True, exist_ok=True)
    paths.sentinel_reports_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(paths.sentinel_state_file, state.model_dump_json(indent=2) + "\n")
    write_text_atomic(paths.sentinel_summary_file, summary.model_dump_json(indent=2) + "\n")
    write_text_atomic(paths.sentinel_latest_report_file, report.model_dump_json(indent=2) + "\n")
    write_text_atomic(check_path, check.model_dump_json(indent=2) + "\n")


def _load_sentinel_state(paths: RuntimePaths) -> SentinelState | None:
    if not paths.sentinel_state_file.exists():
        return None
    return SentinelState.model_validate_json(paths.sentinel_state_file.read_text(encoding="utf-8"))


def _load_sentinel_report(paths: RuntimePaths) -> SentinelReport | None:
    if not paths.sentinel_latest_report_file.exists():
        return None
    return SentinelReport.model_validate_json(paths.sentinel_latest_report_file.read_text(encoding="utf-8"))


def _load_recovery_request(paths: RuntimePaths, *, request_id: str) -> RecoveryRequestRecord | None:
    normalized = request_id.strip()
    if not normalized:
        return None
    artifact_path = paths.recovery_requests_dir / f"{normalized}.json"
    if not artifact_path.exists():
        return None
    return RecoveryRequestRecord.model_validate_json(artifact_path.read_text(encoding="utf-8"))


def _cadence_state(
    config: EngineConfig,
    *,
    checked_at: datetime,
    previous_state: SentinelState | None,
    autonomous_state_applied: bool,
) -> SentinelCadenceState:
    if not autonomous_state_applied:
        return SentinelCadenceState(reset_on_recovery=config.sentinel.reset_cadence_on_recovery)
    previous_cadence = None if previous_state is None else previous_state.cadence
    schedule_started_at = (
        None if previous_cadence is None else previous_cadence.schedule_started_at
    ) or checked_at
    elapsed_seconds = max(0, int((checked_at - schedule_started_at).total_seconds()))
    cadence_steps = config.sentinel.cadence
    current_step_index = 0
    for index, step in enumerate(cadence_steps):
        if elapsed_seconds >= step.activate_after_seconds:
            current_step_index = index
    current_interval_seconds = cadence_steps[current_step_index].interval_seconds
    return SentinelCadenceState(
        schedule_started_at=schedule_started_at,
        last_check_at=checked_at,
        next_check_at=checked_at + timedelta(seconds=current_interval_seconds),
        elapsed_seconds=elapsed_seconds,
        current_interval_seconds=current_interval_seconds,
        current_step_index=current_step_index,
        reset_on_recovery=config.sentinel.reset_cadence_on_recovery,
    )


def _cap_state(
    config: EngineConfig,
    *,
    checked_at: datetime,
    previous_state: SentinelState | None,
    monitoring: SentinelMonitoringState | None,
    progress_state: str,
    changed_sources: tuple[str, ...],
    autonomous_state_applied: bool,
) -> SentinelCapState:
    previous_caps = None if previous_state is None else previous_state.caps
    threshold_fields = {
        "soft_cap_threshold": config.sentinel.caps.soft_cap_threshold,
        "hard_cap_threshold": config.sentinel.caps.hard_cap_threshold,
        "halt_on_hard_cap": config.sentinel.caps.halt_on_hard_cap,
    }
    if not autonomous_state_applied:
        return SentinelCapState(**threshold_fields)
    effective_progress_state = progress_state
    if progress_state == "progressing" and set(changed_sources).issubset({"incident_queues", "recent_events"}):
        effective_progress_state = "stale"
    if effective_progress_state == "progressing":
        if previous_caps is not None:
            return previous_caps.model_copy(
                update={
                    **threshold_fields,
                    "recovery_cycles_queued": 0,
                    "soft_cap_active": False,
                    "hard_cap_triggered": False,
                    "acknowledgment_required": False,
                    "last_counted_recovery_request_id": "",
                }
            )
        return SentinelCapState(**threshold_fields)
    if previous_state is not None:
        caps = previous_caps.model_copy(update=threshold_fields)
    else:
        caps = SentinelCapState(**threshold_fields)
    if monitoring is None or not monitoring.active:
        return caps
    request_id = monitoring.queued_recovery_request_id
    if request_id and request_id != caps.last_counted_recovery_request_id:
        caps = caps.model_copy(
            update={
                "recovery_cycles_queued": caps.recovery_cycles_queued + 1,
                "last_counted_recovery_request_id": request_id,
            }
        )
    soft_cap_active = caps.soft_cap_active
    hard_cap_triggered = caps.hard_cap_triggered
    soft_cap_count = caps.soft_cap_count
    hard_cap_count = caps.hard_cap_count
    last_soft_cap_at = caps.last_soft_cap_at
    last_hard_cap_at = caps.last_hard_cap_at
    last_notification_attempt_at = caps.last_notification_attempt_at
    last_notification_status = caps.last_notification_status
    if caps.recovery_cycles_queued >= caps.soft_cap_threshold and not soft_cap_active:
        soft_cap_active = True
        soft_cap_count += 1
        last_soft_cap_at = checked_at
    if caps.recovery_cycles_queued >= caps.hard_cap_threshold and not hard_cap_triggered:
        hard_cap_triggered = True
        hard_cap_count += 1
        last_hard_cap_at = checked_at
        last_notification_attempt_at = checked_at
        last_notification_status = "local-record-only-notification-attempt-recorded"
    return caps.model_copy(
        update={
            "soft_cap_count": soft_cap_count,
            "hard_cap_count": hard_cap_count,
            "soft_cap_active": soft_cap_active,
            "hard_cap_triggered": hard_cap_triggered,
            "acknowledgment_required": soft_cap_active or hard_cap_triggered,
            "last_soft_cap_at": last_soft_cap_at,
            "last_hard_cap_at": last_hard_cap_at,
            "last_notification_attempt_at": last_notification_attempt_at,
            "last_notification_status": last_notification_status,
        }
    )


def _acknowledgment_state(previous_state: SentinelState | None) -> SentinelAcknowledgmentState:
    if previous_state is None:
        return SentinelAcknowledgmentState()
    return previous_state.acknowledgment


def _monitoring_state(
    *,
    config: EngineConfig,
    paths: RuntimePaths,
    checked_at: datetime,
    previous_state: SentinelState | None,
    progress_state: str,
    changed_sources: tuple[str, ...],
    progress_signature: str,
    latest_progress_at: datetime | None,
    supervisor_error: str | None,
) -> SentinelMonitoringState | None:
    previous_monitoring = None if previous_state is None else previous_state.monitoring
    linked_request_id = "" if previous_state is None else previous_state.last_recovery_request_id
    monitored_request_id = (
        ""
        if previous_monitoring is None
        else previous_monitoring.queued_recovery_request_id
    )
    request_id = monitored_request_id
    if linked_request_id and linked_request_id != monitored_request_id:
        request_id = linked_request_id
    elif linked_request_id:
        request_id = linked_request_id
    recovery_request = _load_recovery_request(paths, request_id=request_id)
    if recovery_request is None:
        return None

    route_target = recovery_request.target.value
    incident_id = "" if previous_state is None else previous_state.last_incident_id
    incident_path = "" if previous_state is None else previous_state.last_incident_path
    if (
        previous_monitoring is not None
        and not previous_monitoring.active
        and previous_monitoring.queued_recovery_request_id == recovery_request.request_id
        and previous_monitoring.resolution in {"resolved", "escalated"}
        and progress_state != "progressing"
        and supervisor_error is None
        ):
        return previous_monitoring.model_copy(
            update={
                "last_observed_progress_at": latest_progress_at,
                "last_observed_status_snapshot_hash": progress_signature,
            }
        )
    effective_progress_state = progress_state
    if progress_state == "progressing" and set(changed_sources).issubset({"incident_queues", "recent_events"}):
        effective_progress_state = "stale"
    if supervisor_error is not None:
        return SentinelMonitoringState(
            active=False,
            route_target=route_target,
            queued_recovery_request_id=recovery_request.request_id,
            incident_id=incident_id,
            incident_path=incident_path,
            queued_at=recovery_request.requested_at,
            last_observed_progress_at=latest_progress_at,
            last_observed_status_snapshot_hash=progress_signature,
            resolution="escalated",
            suppression_active=False,
            suppression_reason="",
            resolution_changed_at=checked_at,
        )
    if effective_progress_state == "progressing":
        return SentinelMonitoringState(
            active=False,
            route_target=route_target,
            queued_recovery_request_id=recovery_request.request_id,
            incident_id=incident_id,
            incident_path=incident_path,
            queued_at=recovery_request.requested_at,
            last_observed_progress_at=latest_progress_at,
            last_observed_status_snapshot_hash=progress_signature,
            resolution="resolved",
            suppression_active=False,
            suppression_reason="",
            resolution_changed_at=checked_at,
        )
    pending_deadline = recovery_request.requested_at + timedelta(
        seconds=config.sentinel.progress_thresholds.no_progress_seconds
    )
    resolution: Literal["pending", "stalled"] = "pending"
    if checked_at >= pending_deadline:
        resolution = "stalled"
    return SentinelMonitoringState(
        active=True,
        route_target=route_target,
        queued_recovery_request_id=recovery_request.request_id,
        incident_id=incident_id,
        incident_path=incident_path,
        queued_at=recovery_request.requested_at,
        last_observed_progress_at=latest_progress_at,
        last_observed_status_snapshot_hash=progress_signature,
        resolution=resolution,
        suppression_active=True,
        suppression_reason="repeat-route-suppressed-for-unresolved-monitoring-cycle",
        resolution_changed_at=checked_at,
    )


def _healthy_idle_reason(report: SupervisorReport | None) -> str | None:
    if report is None:
        return None
    if report.execution_status.value == "IDLE":
        return "execution-idle-is-neutral-when-no-stall-is-observed"
    return None


def _classify_status(
    config: EngineConfig,
    *,
    checked_at: datetime,
    supervisor_report: SupervisorReport | None,
    supervisor_error: str | None,
    progress_state: str,
    latest_progress_at: datetime | None,
    autonomous_state_applied: bool,
    monitoring: SentinelMonitoringState | None,
) -> tuple[str, str]:
    if not config.sentinel.enabled and autonomous_state_applied:
        return "disabled", "sentinel-disabled-by-config"
    if supervisor_error is not None:
        return "degraded", f"supervisor-observation-unavailable: {supervisor_error}"
    if not config.sentinel.enabled:
        return "disabled", "manual-diagnostic-only-while-sentinel-disabled"
    if monitoring is not None and monitoring.active and monitoring.acknowledgment_required:
        if monitoring.hard_cap_triggered:
            if config.sentinel.caps.halt_on_hard_cap:
                return "escalated", "hard-cap-triggered-halt-request-required-with-notification-evidence"
            return "escalated", "hard-cap-triggered-notification-evidence-recorded"
        return "suppressed", "soft-cap-reached-acknowledgment-required-before-rearming-auto-queue"
    if monitoring is not None:
        if monitoring.active:
            if monitoring.resolution == "stalled":
                return "monitoring", "recovery-cycle-stalled-while-repeat-route-remains-suppressed"
            return "monitoring", "recovery-cycle-pending-while-repeat-route-remains-suppressed"
        if monitoring.resolution == "resolved" and progress_state == "progressing":
            return "healthy", "recovery-cycle-resolved-after-meaningful-progress"
        if monitoring.resolution == "escalated" and supervisor_error is not None:
            return "degraded", "monitoring-cycle-materially-changed-and-suppression-cleared"
    if progress_state == "stale" and latest_progress_at is not None:
        stale_seconds = max(0, int((checked_at - latest_progress_at).total_seconds()))
        if stale_seconds >= config.sentinel.progress_thresholds.no_progress_seconds:
            return "degraded", f"no-meaningful-progress-for-{stale_seconds}-seconds"
        return "degraded", f"unchanged-progress-signature-for-{stale_seconds}-seconds"
    healthy_idle_reason = _healthy_idle_reason(supervisor_report)
    if healthy_idle_reason is not None:
        return "healthy", healthy_idle_reason
    if progress_state == "unknown":
        return "healthy", "baseline-evidence-captured"
    if progress_state == "progressing":
        return "healthy", "meaningful-progress-observed"
    return "healthy", "bounded-diagnostic-pass-complete"


def run_sentinel_diagnostic(
    *,
    config: EngineConfig,
    paths: RuntimePaths,
    supervisor_report: SupervisorReport | None = None,
    supervisor_error: str | None = None,
    trigger: str = "manual",
    autonomous_state_applied: bool | None = None,
    now: datetime | None = None,
) -> tuple[SentinelState, SentinelReport, SentinelCheckRecord]:
    """Run one bounded Sentinel diagnostic pass and persist the resulting artifacts."""

    checked_at = _normalize_datetime(now)
    applied_autonomy = config.sentinel.enabled if autonomous_state_applied is None else autonomous_state_applied
    previous_state = _load_sentinel_state(paths)
    previous_report = _load_sentinel_report(paths)
    previous_evidence = None if previous_report is None else previous_report.evidence
    evidence = collect_sentinel_evidence(
        paths=paths,
        supervisor_report=supervisor_report,
        now=checked_at,
    )
    progress = assess_meaningful_progress(
        evidence,
        previous=previous_evidence,
        now=checked_at,
    )
    acknowledgment = _acknowledgment_state(previous_state)
    monitoring = _monitoring_state(
        config=config,
        paths=paths,
        checked_at=checked_at,
        previous_state=previous_state,
        progress_state=progress.state,
        changed_sources=progress.changed_sources,
        progress_signature=evidence.progress_signature,
        latest_progress_at=progress.latest_progress_at,
        supervisor_error=supervisor_error,
    )
    caps = _cap_state(
        config,
        checked_at=checked_at,
        previous_state=previous_state,
        monitoring=monitoring,
        progress_state=progress.state,
        changed_sources=progress.changed_sources,
        autonomous_state_applied=applied_autonomy,
    )
    if caps.acknowledgment_required and not acknowledgment.required:
        acknowledgment = acknowledgment.model_copy(update={"required": True})
    cadence = _cadence_state(
        config,
        checked_at=checked_at,
        previous_state=previous_state,
        autonomous_state_applied=applied_autonomy,
    )
    status, reason = _classify_status(
        config,
        checked_at=checked_at,
        supervisor_report=supervisor_report,
        supervisor_error=supervisor_error,
        progress_state=progress.state,
        latest_progress_at=progress.latest_progress_at,
        autonomous_state_applied=applied_autonomy,
        monitoring=monitoring.model_copy(
            update={
                "acknowledgment_required": acknowledgment.required,
                "hard_cap_triggered": caps.hard_cap_triggered,
            }
        )
        if monitoring is not None
        else None,
    )
    route_target = "none" if monitoring is None else monitoring.route_target
    queued_recovery_request_id = "" if monitoring is None else monitoring.queued_recovery_request_id
    last_incident_id = "" if previous_state is None else previous_state.last_incident_id
    last_incident_path = "" if previous_state is None else previous_state.last_incident_path
    last_recovery_request_id = queued_recovery_request_id or ("" if previous_state is None else previous_state.last_recovery_request_id)
    checks_performed = 1 if previous_state is None else previous_state.checks_performed + 1
    check_id = _check_id_for(checked_at)
    check_path = paths.sentinel_check_records_dir / f"{check_id}.json"
    summary = SentinelSummary(
        status=status,
        reason=reason,
        last_check_at=checked_at,
        next_check_at=cadence.next_check_at,
        checks_performed=checks_performed,
        route_target=route_target,
        monitoring_active=monitoring is not None and monitoring.active,
        acknowledgment_required=acknowledgment.required,
        current_interval_seconds=cadence.current_interval_seconds,
        recovery_cycles_queued=caps.recovery_cycles_queued,
        soft_cap_active=caps.soft_cap_active,
        hard_cap_triggered=caps.hard_cap_triggered,
        soft_cap_count=caps.soft_cap_count,
        hard_cap_count=caps.hard_cap_count,
        last_notification_status=caps.last_notification_status,
        queued_recovery_request_id=queued_recovery_request_id,
        last_incident_id=last_incident_id,
        last_incident_path=last_incident_path,
    )
    state = SentinelState(
        updated_at=checked_at,
        enabled=config.sentinel.enabled,
        lifecycle_status=(
            "disabled"
            if status == "disabled"
            else (
                "escalated"
                if caps.hard_cap_triggered
                else (
                    "suppressed"
                    if caps.soft_cap_active
                    else ("monitoring" if monitoring is not None and monitoring.active else "idle")
                )
            )
        ),
        reason=reason,
        last_healthy_at=(
            checked_at
            if status == "healthy"
            else (None if previous_state is None else previous_state.last_healthy_at)
        ),
        checks_performed=checks_performed,
        latest_check_id=check_id,
        latest_report_path=_relative_path(paths.sentinel_latest_report_file, root=paths.root),
        last_incident_id=last_incident_id,
        last_incident_path=last_incident_path,
        last_recovery_request_id=last_recovery_request_id,
        cadence=cadence,
        caps=caps,
        monitoring=monitoring,
        acknowledgment=acknowledgment,
    )
    report = SentinelReport(
        generated_at=checked_at,
        status=status,
        reason=reason,
        state_path=_relative_path(paths.sentinel_state_file, root=paths.root),
        summary_path=_relative_path(paths.sentinel_summary_file, root=paths.root),
        latest_check_path=_relative_path(check_path, root=paths.root),
        summary=summary,
        cadence=cadence,
        caps=caps,
        monitoring=monitoring,
        evidence=evidence,
        progress=progress,
    )
    check = SentinelCheckRecord(
        check_id=check_id,
        checked_at=checked_at,
        trigger=trigger,
        status=status,
        reason=reason,
        route_target=route_target,
        auto_queue_allowed=not (
            (monitoring is not None and monitoring.suppression_active)
            or acknowledgment.required
            or caps.soft_cap_active
            or caps.hard_cap_triggered
        ),
        status_snapshot_hash=evidence.progress_signature,
        report_path=_relative_path(paths.sentinel_latest_report_file, root=paths.root),
        summary=summary,
    )
    persist_sentinel_artifacts(
        paths,
        state=state,
        summary=summary,
        report=report,
        check=check,
    )
    return state, report, check


__all__ = ["persist_sentinel_artifacts", "run_sentinel_diagnostic"]
