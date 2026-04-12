from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.control import EngineControl
from millrace_engine.control_runtime_surface import supervisor_report
from millrace_engine.sentinel_models import SentinelCheckRecord, SentinelReport, SentinelState
from millrace_engine.sentinel_runtime import run_sentinel_diagnostic

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


def test_run_sentinel_diagnostic_transitions_unchanged_idle_workspace_into_monitoring_state(
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
    assert latest_report.monitoring is not None
    assert latest_report.monitoring.active is True
    assert latest_report.monitoring.last_observed_status_snapshot_hash == check.status_snapshot_hash
    assert state.lifecycle_status == "monitoring"


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
