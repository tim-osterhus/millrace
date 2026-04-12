"""Standalone Sentinel watch lifecycle helpers."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Literal

from .control_models import SentinelCheckSurface, SentinelWatchSurface


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_sentinel_watch(
    *,
    run_check: Callable[[datetime | None], SentinelCheckSurface],
    max_checks: int | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> SentinelWatchSurface:
    """Run the standalone Sentinel watch loop until interrupted or bounded exit."""

    sleep = time.sleep if sleep_fn is None else sleep_fn
    iterations_completed = 0
    latest: SentinelCheckSurface | None = None
    stop_reason: Literal["max_checks_reached", "no_next_check_scheduled", "interrupted"] = "interrupted"

    while True:
        checked_at = _utc_now()
        try:
            latest = run_check(checked_at)
        except KeyboardInterrupt:
            stop_reason = "interrupted"
            break
        iterations_completed += 1
        if max_checks is not None and iterations_completed >= max_checks:
            stop_reason = "max_checks_reached"
            break
        next_check_at = latest.state.cadence.next_check_at
        if next_check_at is None:
            stop_reason = "no_next_check_scheduled"
            break
        delay_seconds = max(0.0, (next_check_at - _utc_now()).total_seconds())
        try:
            sleep(delay_seconds)
        except KeyboardInterrupt:
            stop_reason = "interrupted"
            break

    if latest is None:
        raise RuntimeError("sentinel watch did not complete an initial diagnostic pass")
    return SentinelWatchSurface(
        config_enabled=latest.config_enabled,
        autonomous_state_applied=latest.autonomous_state_applied,
        iterations_completed=iterations_completed,
        stop_reason=stop_reason,
        state_path=latest.state_path,
        summary_path=latest.summary_path,
        latest_report_path=latest.latest_report_path,
        latest_check_path=latest.latest_check_path,
        state=latest.state,
        report=latest.report,
        check=latest.check,
    )


__all__ = ["run_sentinel_watch"]
