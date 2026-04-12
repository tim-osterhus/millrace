"""Bounded Sentinel diagnostic persistence helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import EngineConfig
from .control_models import SupervisorReport
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


def _load_sentinel_state(paths: RuntimePaths) -> SentinelState | None:
    if not paths.sentinel_state_file.exists():
        return None
    return SentinelState.model_validate_json(paths.sentinel_state_file.read_text(encoding="utf-8"))


def _load_sentinel_report(paths: RuntimePaths) -> SentinelReport | None:
    if not paths.sentinel_latest_report_file.exists():
        return None
    return SentinelReport.model_validate_json(paths.sentinel_latest_report_file.read_text(encoding="utf-8"))


def _cadence_state(
    config: EngineConfig,
    *,
    checked_at: datetime,
    previous_state: SentinelState | None,
) -> SentinelCadenceState:
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


def _cap_state(config: EngineConfig, previous_state: SentinelState | None) -> SentinelCapState:
    if previous_state is not None:
        previous_caps = previous_state.caps
        return previous_caps.model_copy(
            update={
                "soft_cap_threshold": config.sentinel.caps.soft_cap_threshold,
                "hard_cap_threshold": config.sentinel.caps.hard_cap_threshold,
                "halt_on_hard_cap": config.sentinel.caps.halt_on_hard_cap,
            }
        )
    return SentinelCapState(
        soft_cap_threshold=config.sentinel.caps.soft_cap_threshold,
        hard_cap_threshold=config.sentinel.caps.hard_cap_threshold,
        halt_on_hard_cap=config.sentinel.caps.halt_on_hard_cap,
    )


def _acknowledgment_state(previous_state: SentinelState | None) -> SentinelAcknowledgmentState:
    if previous_state is None:
        return SentinelAcknowledgmentState()
    return previous_state.acknowledgment


def _monitoring_state(
    *,
    stale: bool,
    progress_signature: str,
    latest_progress_at: datetime | None,
) -> SentinelMonitoringState | None:
    if not stale:
        return None
    return SentinelMonitoringState(
        active=True,
        route_target="none",
        last_observed_progress_at=latest_progress_at,
        last_observed_status_snapshot_hash=progress_signature,
        resolution="pending",
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
    progress_state: str,
    latest_progress_at: datetime | None,
) -> tuple[str, str]:
    if not config.sentinel.enabled:
        return "disabled", "sentinel-disabled-by-config"
    if progress_state == "stale" and latest_progress_at is not None:
        stale_seconds = max(0, int((checked_at - latest_progress_at).total_seconds()))
        if stale_seconds >= config.sentinel.progress_thresholds.no_progress_seconds:
            return "degraded", f"no-meaningful-progress-for-{stale_seconds}-seconds"
        return "monitoring", f"unchanged-progress-signature-for-{stale_seconds}-seconds"
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
    trigger: str = "manual",
    now: datetime | None = None,
) -> tuple[SentinelState, SentinelReport, SentinelCheckRecord]:
    """Run one bounded Sentinel diagnostic pass and persist the resulting artifacts."""

    checked_at = _normalize_datetime(now)
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
    cadence = _cadence_state(config, checked_at=checked_at, previous_state=previous_state)
    caps = _cap_state(config, previous_state)
    acknowledgment = _acknowledgment_state(previous_state)
    status, reason = _classify_status(
        config,
        checked_at=checked_at,
        supervisor_report=supervisor_report,
        progress_state=progress.state,
        latest_progress_at=progress.latest_progress_at,
    )
    monitoring = _monitoring_state(
        stale=status in {"monitoring", "degraded"},
        progress_signature=evidence.progress_signature,
        latest_progress_at=progress.latest_progress_at,
    )
    check_id = _check_id_for(checked_at)
    check_path = paths.sentinel_check_records_dir / f"{check_id}.json"
    summary = SentinelSummary(
        status=status,
        reason=reason,
        last_check_at=checked_at,
        next_check_at=cadence.next_check_at,
        route_target="none",
        monitoring_active=monitoring is not None and monitoring.active,
        acknowledgment_required=acknowledgment.required,
        current_interval_seconds=cadence.current_interval_seconds,
        soft_cap_count=caps.soft_cap_count,
        hard_cap_count=caps.hard_cap_count,
    )
    state = SentinelState(
        updated_at=checked_at,
        enabled=config.sentinel.enabled,
        lifecycle_status=(
            "disabled"
            if status == "disabled"
            else ("monitoring" if monitoring is not None and monitoring.active else "idle")
        ),
        reason=reason,
        last_healthy_at=(
            checked_at
            if status == "healthy"
            else (None if previous_state is None else previous_state.last_healthy_at)
        ),
        latest_check_id=check_id,
        latest_report_path=_relative_path(paths.sentinel_latest_report_file, root=paths.root),
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
        route_target="none",
        auto_queue_allowed=False,
        status_snapshot_hash=evidence.progress_signature,
        report_path=_relative_path(paths.sentinel_latest_report_file, root=paths.root),
        summary=summary,
    )
    paths.sentinel_runtime_dir.mkdir(parents=True, exist_ok=True)
    paths.sentinel_check_records_dir.mkdir(parents=True, exist_ok=True)
    paths.sentinel_reports_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(paths.sentinel_state_file, state.model_dump_json(indent=2) + "\n")
    write_text_atomic(paths.sentinel_summary_file, summary.model_dump_json(indent=2) + "\n")
    write_text_atomic(paths.sentinel_latest_report_file, report.model_dump_json(indent=2) + "\n")
    write_text_atomic(check_path, check.model_dump_json(indent=2) + "\n")
    return state, report, check


__all__ = ["run_sentinel_diagnostic"]
