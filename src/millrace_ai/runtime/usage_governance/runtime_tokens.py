"""Runtime-token usage-governance rule evaluation."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from millrace_ai.config import (
    RuntimeConfig,
    UsageGovernanceRuntimeTokenMetric,
    UsageGovernanceRuntimeTokenWindow,
)

from .models import UsageGovernanceBlocker, UsageGovernanceLedgerEntry
from .time_windows import ROLLING_5H, calendar_week_start, ensure_utc


def evaluate_runtime_token_rules(
    entries: tuple[UsageGovernanceLedgerEntry, ...],
    *,
    config: RuntimeConfig,
    now: datetime,
    daemon_session_id: str | None,
    timezone_info: ZoneInfo,
) -> tuple[UsageGovernanceBlocker, ...]:
    blockers: list[UsageGovernanceBlocker] = []
    for rule in config.usage_governance.runtime_token_rules.rules:
        window_entries = entries_for_runtime_window(
            entries,
            window=rule.window,
            now=now,
            daemon_session_id=daemon_session_id,
            timezone_info=timezone_info,
        )
        observed = observed_metric(window_entries, rule.metric)
        if observed < rule.threshold:
            continue
        next_resume = runtime_rule_next_resume(
            window_entries,
            window=rule.window,
            metric=rule.metric,
            threshold=rule.threshold,
            now=now,
            timezone_info=timezone_info,
        )
        blockers.append(
            UsageGovernanceBlocker(
                source="runtime_token",
                rule_id=rule.rule_id,
                window=rule.window.value,
                metric=rule.metric.value,
                observed=observed,
                threshold=rule.threshold,
                auto_resume_possible=rule.window
                in {
                    UsageGovernanceRuntimeTokenWindow.ROLLING_5H,
                    UsageGovernanceRuntimeTokenWindow.CALENDAR_WEEK,
                },
                next_auto_resume_at=next_resume,
            )
        )
    return tuple(blockers)


def entries_for_runtime_window(
    entries: tuple[UsageGovernanceLedgerEntry, ...],
    *,
    window: UsageGovernanceRuntimeTokenWindow,
    now: datetime,
    daemon_session_id: str | None,
    timezone_info: ZoneInfo,
) -> tuple[UsageGovernanceLedgerEntry, ...]:
    if window is UsageGovernanceRuntimeTokenWindow.ROLLING_5H:
        cutoff = now - ROLLING_5H
        return tuple(entry for entry in entries if ensure_utc(entry.stage_completed_at) >= cutoff)
    if window is UsageGovernanceRuntimeTokenWindow.CALENDAR_WEEK:
        start = calendar_week_start(now, timezone_info=timezone_info)
        return tuple(entry for entry in entries if ensure_utc(entry.stage_completed_at) >= start)
    if window is UsageGovernanceRuntimeTokenWindow.DAEMON_SESSION:
        return tuple(entry for entry in entries if entry.daemon_session_id == daemon_session_id)
    return entries


def runtime_rule_next_resume(
    entries: tuple[UsageGovernanceLedgerEntry, ...],
    *,
    window: UsageGovernanceRuntimeTokenWindow,
    metric: UsageGovernanceRuntimeTokenMetric,
    threshold: int,
    now: datetime,
    timezone_info: ZoneInfo,
) -> datetime | None:
    if window is UsageGovernanceRuntimeTokenWindow.ROLLING_5H:
        remaining = observed_metric(entries, metric)
        for entry in sorted(entries, key=lambda item: ensure_utc(item.stage_completed_at)):
            remaining -= metric_value(entry, metric)
            candidate = ensure_utc(entry.stage_completed_at) + ROLLING_5H
            if remaining < threshold:
                return candidate
        return None
    if window is UsageGovernanceRuntimeTokenWindow.CALENDAR_WEEK:
        return calendar_week_start(now, timezone_info=timezone_info) + timedelta(days=7)
    return None


def observed_metric(
    entries: tuple[UsageGovernanceLedgerEntry, ...],
    metric: UsageGovernanceRuntimeTokenMetric,
) -> int:
    return sum(metric_value(entry, metric) for entry in entries)


def metric_value(
    entry: UsageGovernanceLedgerEntry,
    metric: UsageGovernanceRuntimeTokenMetric,
) -> int:
    if metric is UsageGovernanceRuntimeTokenMetric.TOTAL_TOKENS:
        return entry.token_usage.total_tokens
    raise ValueError(f"unsupported runtime token metric: {metric.value}")


__all__ = [
    "entries_for_runtime_window",
    "evaluate_runtime_token_rules",
    "metric_value",
    "observed_metric",
    "runtime_rule_next_resume",
]
