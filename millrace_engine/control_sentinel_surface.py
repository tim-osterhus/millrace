"""Control-plane Sentinel check and status surfaces."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .control_actions import sentinel_incident as sentinel_incident_operation
from .control_models import SentinelCheckSurface, SentinelIncidentSurface, SentinelStatusSurface, SentinelWatchSurface
from .control_runtime_surface import supervisor_report as supervisor_report_surface
from .sentinel_models import SentinelCheckRecord, SentinelIncidentBundle, SentinelReport, SentinelState
from .sentinel_runtime import run_sentinel_diagnostic
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


__all__ = ["sentinel_check", "sentinel_incident", "sentinel_status", "sentinel_watch"]
