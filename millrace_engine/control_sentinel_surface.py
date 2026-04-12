"""Control-plane Sentinel check and status surfaces."""

from __future__ import annotations

from pathlib import Path

from .control_models import SentinelCheckSurface, SentinelStatusSurface
from .control_runtime_surface import supervisor_report as supervisor_report_surface
from .sentinel_models import SentinelCheckRecord, SentinelReport, SentinelState
from .sentinel_runtime import run_sentinel_diagnostic


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


def sentinel_check(control, *, trigger: str = "manual") -> SentinelCheckSurface:
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


__all__ = ["sentinel_check", "sentinel_status"]
