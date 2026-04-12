"""Control-plane Sentinel check and status surfaces."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .control_actions import sentinel_incident as sentinel_incident_operation
from .control_common import ControlError
from .control_models import OperationResult, SentinelCheckSurface, SentinelIncidentSurface, SentinelStatusSurface, SentinelWatchSurface
from .control_runtime_surface import supervisor_report as supervisor_report_surface
from .events import EventBus, EventSource, EventType, HistorySubscriber, JsonlEventSubscriber
from .sentinel_models import SentinelCheckRecord, SentinelIncidentBundle, SentinelReport, SentinelState
from .sentinel_runtime import persist_sentinel_artifacts, run_sentinel_diagnostic
from .sentinel_watch import run_sentinel_watch


def _resolve_relative_path(path_token: str, *, root: Path) -> Path | None:
    normalized = path_token.strip()
    if not normalized:
        return None
    candidate = Path(normalized)
    if candidate.is_absolute():
        return candidate
    return root / candidate


def _read_check_record(paths, report: SentinelReport | None) -> tuple[SentinelCheckRecord | None, Path | None]:
    check_path = None if report is None else _resolve_relative_path(report.latest_check_path, root=paths.root)
    if check_path is None or not check_path.exists():
        return None, check_path
    return SentinelCheckRecord.model_validate_json(check_path.read_text(encoding="utf-8")), check_path


def _event_bus(paths) -> EventBus:
    return EventBus([JsonlEventSubscriber(paths), HistorySubscriber(paths)])


def _summary_for(state: SentinelState, report: SentinelReport) -> object:
    return report.summary.model_copy(
        update={
            "status": report.status,
            "reason": report.reason,
            "last_check_at": state.cadence.last_check_at,
            "next_check_at": state.cadence.next_check_at,
            "checks_performed": state.checks_performed,
            "monitoring_active": state.monitoring is not None and state.monitoring.active,
            "acknowledgment_required": state.acknowledgment.required,
            "current_interval_seconds": state.cadence.current_interval_seconds,
            "recovery_cycles_queued": state.caps.recovery_cycles_queued,
            "soft_cap_active": state.caps.soft_cap_active,
            "hard_cap_triggered": state.caps.hard_cap_triggered,
            "soft_cap_count": state.caps.soft_cap_count,
            "hard_cap_count": state.caps.hard_cap_count,
            "last_notification_status": state.caps.last_notification_status,
            "queued_recovery_request_id": state.last_recovery_request_id,
            "last_incident_id": state.last_incident_id,
            "last_incident_path": state.last_incident_path,
        }
    )


def _persist_sentinel_update(
    control,
    *,
    state: SentinelState,
    report: SentinelReport,
    check: SentinelCheckRecord,
) -> tuple[SentinelState, SentinelReport, SentinelCheckRecord]:
    summary = _summary_for(state, report)
    updated_report = report.model_copy(update={"summary": summary, "caps": state.caps, "monitoring": state.monitoring})
    updated_check = check.model_copy(update={"summary": summary})
    persist_sentinel_artifacts(
        control.paths,
        state=state,
        summary=summary,
        report=updated_report,
        check=updated_check,
    )
    return state, updated_report, updated_check


def sentinel_check(control, *, trigger: str = "manual", now: datetime | None = None) -> SentinelCheckSurface:
    control.reload_local_config()
    supervisor = None
    supervisor_error = None
    try:
        supervisor = supervisor_report_surface(control)
    except Exception as exc:  # noqa: BLE001 - bounded diagnostic should still persist without supervisor report
        supervisor_error = str(exc).strip() or exc.__class__.__name__
    state, report, check = run_sentinel_diagnostic(
        config=control.loaded.config,
        paths=control.paths,
        supervisor_report=supervisor,
        supervisor_error=supervisor_error,
        trigger=trigger,
        autonomous_state_applied=control.loaded.config.sentinel.enabled,
        now=now,
    )
    emitted_at = check.checked_at
    bus = _event_bus(control.paths)
    if state.caps.last_soft_cap_at == emitted_at:
        bus.emit(
            EventType.SENTINEL_SOFT_CAP_TRIGGERED,
            source=EventSource.CONTROL,
            payload={
                "check_id": check.check_id,
                "soft_cap_count": state.caps.soft_cap_count,
                "recovery_cycles_queued": state.caps.recovery_cycles_queued,
                "queued_recovery_request_id": state.last_recovery_request_id,
            },
        )
    if state.caps.last_hard_cap_at == emitted_at:
        bus.emit(
            EventType.SENTINEL_HARD_CAP_TRIGGERED,
            source=EventSource.CONTROL,
            payload={
                "check_id": check.check_id,
                "hard_cap_count": state.caps.hard_cap_count,
                "recovery_cycles_queued": state.caps.recovery_cycles_queued,
                "queued_recovery_request_id": state.last_recovery_request_id,
                "halt_on_hard_cap": state.caps.halt_on_hard_cap,
            },
        )
        bus.emit(
            EventType.SENTINEL_NOTIFICATION_ATTEMPT_RECORDED,
            source=EventSource.CONTROL,
            payload={
                "check_id": check.check_id,
                "status": state.caps.last_notification_status,
                "attempted_at": state.caps.last_notification_attempt_at,
            },
        )
    if (
        state.caps.hard_cap_triggered
        and state.caps.halt_on_hard_cap
        and not state.caps.last_halt_action_status
    ):
        halt_result = control.supervisor_pause(issuer="sentinel")
        updated_caps = state.caps.model_copy(
            update={
                "last_halt_action_at": emitted_at,
                "last_halt_action_status": halt_result.message,
            }
        )
        updated_state = state.model_copy(update={"caps": updated_caps})
        state, report, check = _persist_sentinel_update(
            control,
            state=updated_state,
            report=report,
            check=check,
        )
    return SentinelCheckSurface(
        config_enabled=control.loaded.config.sentinel.enabled,
        autonomous_state_applied=control.loaded.config.sentinel.enabled,
        supervisor_observation_error=supervisor_error,
        state_path=control.paths.sentinel_state_file,
        summary_path=control.paths.sentinel_summary_file,
        latest_report_path=control.paths.sentinel_latest_report_file,
        latest_check_path=control.paths.sentinel_check_records_dir / f"{check.check_id}.json",
        state=state,
        report=report,
        check=check,
    )


def sentinel_acknowledge(control, *, issuer: str, reason: str) -> OperationResult:
    control.reload_local_config()
    normalized_issuer = control._normalize_supervisor_issuer(issuer)
    normalized_reason = " ".join(reason.strip().split())
    if not normalized_reason:
        raise ControlError("reason may not be empty")
    status = sentinel_status(control)
    if status.state is None or status.report is None or status.check is None:
        raise ControlError("sentinel acknowledgment requires a persisted sentinel result")
    if not (
        status.state.acknowledgment.required
        or status.state.caps.soft_cap_active
        or status.state.caps.hard_cap_triggered
    ):
        raise ControlError("sentinel acknowledgment requires a pending cap or escalation acknowledgment state")
    acknowledged_at = datetime.now(timezone.utc)
    updated_caps = status.state.caps.model_copy(
        update={
            "recovery_cycles_queued": 0,
            "soft_cap_active": False,
            "hard_cap_triggered": False,
            "acknowledgment_required": False,
            "last_counted_recovery_request_id": "",
        }
    )
    updated_monitoring = status.state.monitoring
    if updated_monitoring is not None:
        updated_monitoring = updated_monitoring.model_copy(
            update={"acknowledgment_required": False, "hard_cap_triggered": False}
        )
    updated_state = status.state.model_copy(
        update={
            "updated_at": acknowledged_at,
            "reason": "sentinel-acknowledged-by-operator",
            "lifecycle_status": (
                "monitoring"
                if updated_monitoring is not None and updated_monitoring.active
                else "idle"
            ),
            "caps": updated_caps,
            "monitoring": updated_monitoring,
            "acknowledgment": status.state.acknowledgment.model_copy(
                update={
                    "required": False,
                    "last_acknowledged_at": acknowledged_at,
                    "last_acknowledged_by": normalized_issuer,
                    "last_acknowledgment_reason": normalized_reason,
                }
            ),
        }
    )
    updated_state, updated_report, _updated_check = _persist_sentinel_update(
        control,
        state=updated_state,
        report=status.report.model_copy(
            update={
                "generated_at": acknowledged_at,
                "reason": "sentinel-acknowledged-by-operator",
                "status": (
                    "monitoring"
                    if updated_monitoring is not None and updated_monitoring.active
                    else "healthy"
                ),
                "monitoring": updated_monitoring,
            }
        ),
        check=status.check,
    )
    _event_bus(control.paths).emit(
        EventType.SENTINEL_ACKNOWLEDGED,
        source=EventSource.CONTROL,
        payload={
            "issuer": normalized_issuer,
            "reason": normalized_reason,
            "acknowledged_at": acknowledged_at,
            "latest_check_id": updated_state.latest_check_id,
        },
    )
    return OperationResult(
        mode="direct",
        applied=True,
        message="sentinel acknowledged",
        payload={
            "issuer": normalized_issuer,
            "reason": normalized_reason,
            "acknowledged_at": acknowledged_at.isoformat().replace("+00:00", "Z"),
            "state_path": control.paths.sentinel_state_file.as_posix(),
            "summary_path": control.paths.sentinel_summary_file.as_posix(),
            "latest_report_path": control.paths.sentinel_latest_report_file.as_posix(),
        },
    )


def sentinel_watch(control, *, max_checks: int | None = None) -> SentinelWatchSurface:
    return run_sentinel_watch(
        run_check=lambda checked_at: sentinel_check(control, trigger="watch", now=checked_at),
        max_checks=max_checks,
    )


def sentinel_status(control) -> SentinelStatusSurface:
    control.reload_local_config()
    state = None
    report = None
    check = None
    if control.paths.sentinel_state_file.exists():
        state = SentinelState.model_validate_json(control.paths.sentinel_state_file.read_text(encoding="utf-8"))
    if control.paths.sentinel_latest_report_file.exists():
        report = SentinelReport.model_validate_json(
            control.paths.sentinel_latest_report_file.read_text(encoding="utf-8")
        )
    check, check_path = _read_check_record(control.paths, report)
    available = state is not None or report is not None
    return SentinelStatusSurface(
        config_enabled=control.loaded.config.sentinel.enabled,
        available=available,
        reason=("latest-sentinel-result-available" if available else "no-persisted-sentinel-result"),
        state_path=control.paths.sentinel_state_file,
        summary_path=control.paths.sentinel_summary_file,
        latest_report_path=control.paths.sentinel_latest_report_file,
        latest_check_path=check_path,
        state=state,
        report=report,
        check=check,
    )


def sentinel_incident(
    control,
    *,
    failure_signature: str,
    summary: str,
    severity: str,
    routing_target: str,
    evidence_pointers: tuple[str, ...] | list[str] | None = None,
    recovery_request_id: str = "",
    suggested_recovery: str = "",
    issuer: str = "sentinel",
) -> SentinelIncidentSurface:
    control.reload_local_config()
    normalized_issuer = control._normalize_supervisor_issuer(issuer)
    status_markers = []
    if control.paths.status_file.exists():
        status_markers.append(
            {
                "plane": "execution",
                "marker": control.paths.status_file.read_text(encoding="utf-8").strip(),
                "source_path": control.paths.status_file.relative_to(control.paths.root).as_posix(),
            }
        )
    if control.paths.research_status_file.exists():
        status_markers.append(
            {
                "plane": "research",
                "marker": control.paths.research_status_file.read_text(encoding="utf-8").strip(),
                "source_path": control.paths.research_status_file.relative_to(control.paths.root).as_posix(),
            }
        )
    report = None
    if control.paths.sentinel_latest_report_file.exists():
        report = SentinelReport.model_validate_json(control.paths.sentinel_latest_report_file.read_text(encoding="utf-8"))
    payload = {
        "failure_signature": failure_signature,
        "summary": summary,
        "severity": severity,
        "routing_target": routing_target,
        "evidence_pointers": tuple(evidence_pointers or ()),
        "observed_status_markers": tuple(status_markers),
        "elapsed_since_last_progress_seconds": (
            0
            if report is None or report.evidence is None or report.evidence.latest_progress_at is None
            else max(
                0,
                int(
                    (
                        datetime.now(timezone.utc) - report.evidence.latest_progress_at
                    ).total_seconds()
                ),
            )
        ),
        "source": "sentinel",
        "suggested_recovery": suggested_recovery,
        "recovery_request_id": recovery_request_id,
        "sentinel_check_id": "" if report is None else Path(report.latest_check_path).stem,
        "sentinel_report_path": control.paths.sentinel_latest_report_file.relative_to(control.paths.root).as_posix(),
        "sentinel_state_path": control.paths.sentinel_state_file.relative_to(control.paths.root).as_posix(),
        "report_status": "healthy" if report is None else report.status,
        "report_reason": "" if report is None else report.reason,
    }
    command_id, mode, emitted_at, incident_payload, incident_path, bundle_path = sentinel_incident_operation(
        control.paths,
        payload=payload,
        issuer=normalized_issuer,
        daemon_running=control.is_daemon_running(),
    )
    if mode == "direct":
        bundle = SentinelIncidentBundle.model_validate_json(bundle_path.read_text(encoding="utf-8"))
    else:
        bundle = SentinelIncidentBundle(
            emitted_at=emitted_at,
            incident_id=incident_path.stem,
            incident_path=incident_path.as_posix(),
            bundle_path=bundle_path.as_posix(),
            issuer=normalized_issuer,
            command_id=command_id or "",
            payload=incident_payload,
            linked_to_persisted_sentinel_state=control.paths.sentinel_state_file.exists(),
        )
    return SentinelIncidentSurface(
        command_id=command_id,
        mode=mode,
        applied=True,
        message="sentinel incident generated" if mode == "direct" else "sentinel incident queued",
        incident_path=incident_path,
        bundle_path=bundle_path,
        bundle=bundle,
    )


__all__ = ["sentinel_acknowledge", "sentinel_check", "sentinel_incident", "sentinel_status", "sentinel_watch"]
