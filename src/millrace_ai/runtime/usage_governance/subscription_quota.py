"""Subscription-quota usage-governance evaluation."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from millrace_ai.config import (
    RuntimeConfig,
    UsageGovernanceDegradedPolicy,
    UsageGovernanceSubscriptionWindow,
)
from millrace_ai.paths import WorkspacePaths

from .models import (
    SubscriptionQuotaProvider,
    SubscriptionQuotaStatus,
    SubscriptionQuotaWindowReading,
    UsageGovernanceBlocker,
)
from .state import load_usage_governance_state
from .time_windows import datetime_from_unix_seconds, ensure_utc


class CodexChatGPTOAuthQuotaAdapter:
    """Best-effort reader for Codex-local token_count rate-limit telemetry."""

    def __init__(self, *, codex_home: Path | None = None) -> None:
        self.codex_home = codex_home or Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()

    def read(self, *, now: datetime) -> SubscriptionQuotaStatus:
        session_root = self.codex_home / "sessions"
        if not session_root.is_dir():
            return degraded_subscription_status(
                now=now,
                detail="quota_telemetry_unavailable",
            )

        for session_file in recent_jsonl_files(session_root, limit=20):
            payload = latest_token_count_payload(session_file)
            if payload is None:
                continue
            status = subscription_status_from_token_count_payload(payload, now=now)
            if status is not None:
                return status

        return degraded_subscription_status(
            now=now,
            detail="quota_telemetry_unavailable",
        )


def subscription_quota_status(
    paths: WorkspacePaths,
    *,
    config: RuntimeConfig,
    now: datetime,
    provider: SubscriptionQuotaProvider | None,
) -> SubscriptionQuotaStatus:
    quota = config.usage_governance.subscription_quota_rules
    if not quota.enabled:
        return SubscriptionQuotaStatus(
            enabled=False,
            provider=quota.provider.value,
            state="disabled",
            degraded_policy=quota.degraded_policy.value,
        )

    previous = load_usage_governance_state(paths)
    previous_status = previous.subscription_quota_status
    if previous_status.last_refreshed_at is not None:
        age = now - ensure_utc(previous_status.last_refreshed_at)
        if age < timedelta(seconds=quota.refresh_interval_seconds):
            return previous_status

    adapter = provider or CodexChatGPTOAuthQuotaAdapter()
    status = adapter.read(now=now)
    return status.model_copy(
        update={
            "enabled": True,
            "provider": quota.provider.value,
            "degraded_policy": quota.degraded_policy.value,
            "last_refreshed_at": status.last_refreshed_at or now,
        }
    )


def evaluate_subscription_quota_rules(
    status: SubscriptionQuotaStatus,
    *,
    config: RuntimeConfig,
) -> tuple[UsageGovernanceBlocker, ...]:
    quota = config.usage_governance.subscription_quota_rules
    if not quota.enabled:
        return ()
    if status.state == "degraded":
        if quota.degraded_policy is not UsageGovernanceDegradedPolicy.FAIL_CLOSED:
            return ()
        return (
            UsageGovernanceBlocker(
                source="subscription_quota",
                rule_id="subscription-quota-degraded-fail-closed",
                window="degraded",
                observed=100,
                threshold=100,
                auto_resume_possible=False,
                detail=status.detail or "quota_telemetry_unavailable",
            ),
        )

    blockers: list[UsageGovernanceBlocker] = []
    for rule in quota.rules:
        reading = status.windows.get(rule.window.value)
        if reading is None:
            continue
        if reading.percent_used < rule.pause_at_percent_used:
            continue
        blockers.append(
            UsageGovernanceBlocker(
                source="subscription_quota",
                rule_id=rule.rule_id,
                window=rule.window.value,
                observed=reading.percent_used,
                threshold=rule.pause_at_percent_used,
                auto_resume_possible=reading.resets_at is not None,
                next_auto_resume_at=reading.resets_at,
            )
        )
    return tuple(blockers)


def degraded_subscription_status(*, now: datetime, detail: str) -> SubscriptionQuotaStatus:
    return SubscriptionQuotaStatus(
        enabled=True,
        provider="codex_chatgpt_oauth",
        state="degraded",
        detail=detail,
        last_refreshed_at=now,
    )


def subscription_status_from_token_count_payload(
    payload: dict[str, object],
    *,
    now: datetime,
) -> SubscriptionQuotaStatus | None:
    rate_limits = payload.get("rate_limits")
    if not isinstance(rate_limits, dict):
        return None

    windows: dict[str, SubscriptionQuotaWindowReading] = {}
    for key, window in (
        ("primary", UsageGovernanceSubscriptionWindow.FIVE_HOUR),
        ("secondary", UsageGovernanceSubscriptionWindow.WEEKLY),
    ):
        raw = rate_limits.get(key)
        if not isinstance(raw, dict):
            continue
        percent_used = raw.get("used_percent")
        if not isinstance(percent_used, int | float):
            continue
        resets_at = datetime_from_unix_seconds(raw.get("resets_at"))
        windows[window.value] = SubscriptionQuotaWindowReading(
            window=window.value,
            percent_used=float(percent_used),
            resets_at=resets_at,
            read_at=now,
        )

    if not windows:
        return None
    return SubscriptionQuotaStatus(
        enabled=True,
        provider="codex_chatgpt_oauth",
        state="healthy",
        last_refreshed_at=now,
        windows=windows,
    )


def recent_jsonl_files(root: Path, *, limit: int) -> tuple[Path, ...]:
    candidates: list[tuple[float, Path]] = []
    for path in root.rglob("*.jsonl"):
        try:
            candidates.append((path.stat().st_mtime, path))
        except OSError:
            continue
    return tuple(
        path
        for _, path in sorted(
            candidates,
            key=lambda item: item[0],
            reverse=True,
        )[:limit]
    )


def latest_token_count_payload(path: Path) -> dict[str, object] | None:
    for line in tail_lines(path, max_bytes=512_000):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("type") != "event_msg":
            continue
        nested = payload.get("payload")
        if not isinstance(nested, dict) or nested.get("type") != "token_count":
            continue
        return nested
    return None


def tail_lines(path: Path, *, max_bytes: int) -> tuple[str, ...]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            payload = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ()
    return tuple(reversed(payload.splitlines()))


__all__ = [
    "CodexChatGPTOAuthQuotaAdapter",
    "degraded_subscription_status",
    "evaluate_subscription_quota_rules",
    "latest_token_count_payload",
    "recent_jsonl_files",
    "subscription_quota_status",
    "subscription_status_from_token_count_payload",
    "tail_lines",
]
