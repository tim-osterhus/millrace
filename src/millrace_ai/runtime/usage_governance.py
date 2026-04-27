"""Runtime-owned usage governance evaluation and durable accounting."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field

from millrace_ai.config import (
    RuntimeConfig,
    UsageGovernanceDegradedPolicy,
    UsageGovernanceRuntimeTokenMetric,
    UsageGovernanceRuntimeTokenWindow,
    UsageGovernanceSubscriptionWindow,
)
from millrace_ai.contracts import Plane, RuntimeSnapshot, StageResultEnvelope, TokenUsage, WorkItemKind
from millrace_ai.events import write_runtime_event
from millrace_ai.paths import WorkspacePaths
from millrace_ai.state_store import save_snapshot

from .pause_state import (
    USAGE_GOVERNANCE_PAUSE_SOURCE,
    add_pause_source,
    has_pause_source,
    remove_pause_source,
)

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine

_GOVERNANCE_LOCK = RLock()
_ROLLING_5H = timedelta(hours=5)


class UsageGovernanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UsageGovernanceBlocker(UsageGovernanceModel):
    source: Literal["runtime_token", "subscription_quota"]
    rule_id: str
    window: str
    observed: float
    threshold: float
    metric: str | None = None
    auto_resume_possible: bool = True
    next_auto_resume_at: datetime | None = None
    detail: str = ""


class SubscriptionQuotaWindowReading(UsageGovernanceModel):
    window: str
    percent_used: float
    resets_at: datetime | None = None
    read_at: datetime


class SubscriptionQuotaStatus(UsageGovernanceModel):
    enabled: bool = False
    provider: str = "codex_chatgpt_oauth"
    state: Literal["disabled", "healthy", "degraded"] = "disabled"
    degraded_policy: str | None = None
    detail: str | None = None
    last_refreshed_at: datetime | None = None
    windows: dict[str, SubscriptionQuotaWindowReading] = Field(default_factory=dict)


class UsageGovernanceState(UsageGovernanceModel):
    version: Literal["1.0"] = "1.0"
    enabled: bool = False
    auto_resume: bool = True
    auto_resume_possible: bool = True
    evaluation_boundary: str = "between_stages"
    calendar_timezone: str = "UTC"
    daemon_session_id: str | None = None
    last_evaluated_at: datetime
    active_blockers: tuple[UsageGovernanceBlocker, ...] = ()
    paused_by_governance: bool = False
    next_auto_resume_at: datetime | None = None
    subscription_quota_status: SubscriptionQuotaStatus = Field(
        default_factory=SubscriptionQuotaStatus
    )


class UsageGovernanceLedgerEntry(UsageGovernanceModel):
    dedupe_key: str
    counted_at: datetime
    stage_completed_at: datetime
    plane: Plane
    run_id: str
    stage_id: str
    work_item_kind: WorkItemKind
    work_item_id: str
    token_usage: TokenUsage
    stage_result_path: str
    daemon_session_id: str | None = None


class SubscriptionQuotaProvider(Protocol):
    def read(self, *, now: datetime) -> SubscriptionQuotaStatus: ...


class CodexChatGPTOAuthQuotaAdapter:
    """Best-effort reader for Codex-local token_count rate-limit telemetry."""

    def __init__(self, *, codex_home: Path | None = None) -> None:
        self.codex_home = codex_home or Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()

    def read(self, *, now: datetime) -> SubscriptionQuotaStatus:
        session_root = self.codex_home / "sessions"
        if not session_root.is_dir():
            return _degraded_subscription_status(
                now=now,
                detail="quota_telemetry_unavailable",
            )

        for session_file in _recent_jsonl_files(session_root, limit=20):
            payload = _latest_token_count_payload(session_file)
            if payload is None:
                continue
            status = _subscription_status_from_token_count_payload(payload, now=now)
            if status is not None:
                return status

        return _degraded_subscription_status(
            now=now,
            detail="quota_telemetry_unavailable",
        )


def load_usage_governance_state(paths: WorkspacePaths) -> UsageGovernanceState:
    if not paths.usage_governance_state_file.is_file():
        return _disabled_state(now=datetime.fromtimestamp(0, timezone.utc))
    payload = json.loads(paths.usage_governance_state_file.read_text(encoding="utf-8"))
    return UsageGovernanceState.model_validate(payload)


def save_usage_governance_state(paths: WorkspacePaths, state: UsageGovernanceState) -> None:
    _atomic_write_text(paths.usage_governance_state_file, state.model_dump_json(indent=2) + "\n")


def evaluate_and_apply_usage_governance(
    engine: RuntimeEngine,
    *,
    stage_result: StageResultEnvelope | None = None,
    stage_result_path: Path | None = None,
) -> UsageGovernanceState | None:
    assert engine.config is not None
    assert engine.snapshot is not None

    if not _should_evaluate(engine.paths, engine.config, engine.snapshot):
        return None

    with _GOVERNANCE_LOCK:
        previous_state = load_usage_governance_state(engine.paths)
        had_governance_pause = has_pause_source(
            engine.snapshot,
            USAGE_GOVERNANCE_PAUSE_SOURCE,
        )
        state = evaluate_usage_governance(
            engine.paths,
            config=engine.config,
            now=engine._now(),
            daemon_session_id=engine._daemon_lock_session_id,
            paused_by_governance=had_governance_pause,
            stage_result=stage_result,
            stage_result_path=stage_result_path,
        )

        updated_snapshot = engine.snapshot
        if not engine.config.usage_governance.enabled:
            if had_governance_pause:
                updated_snapshot = remove_pause_source(
                    updated_snapshot,
                    source=USAGE_GOVERNANCE_PAUSE_SOURCE,
                    now=engine._now(),
                )
        elif state.active_blockers:
            updated_snapshot = add_pause_source(
                updated_snapshot,
                source=USAGE_GOVERNANCE_PAUSE_SOURCE,
                now=engine._now(),
            )
        elif had_governance_pause and engine.config.usage_governance.auto_resume:
            updated_snapshot = remove_pause_source(
                updated_snapshot,
                source=USAGE_GOVERNANCE_PAUSE_SOURCE,
                now=engine._now(),
            )

        paused_after = has_pause_source(updated_snapshot, USAGE_GOVERNANCE_PAUSE_SOURCE)
        if state.paused_by_governance != paused_after:
            state = state.model_copy(update={"paused_by_governance": paused_after})
            save_usage_governance_state(engine.paths, state)

        if updated_snapshot.model_dump(mode="python") != engine.snapshot.model_dump(mode="python"):
            engine.snapshot = updated_snapshot
            save_snapshot(engine.paths, engine.snapshot)

        _emit_governance_monitor_events(
            engine,
            state=state,
            previous_state=previous_state,
            had_governance_pause=had_governance_pause,
            has_governance_pause=paused_after,
        )
        return state


def evaluate_usage_governance(
    paths: WorkspacePaths,
    *,
    config: RuntimeConfig,
    now: datetime,
    daemon_session_id: str | None,
    paused_by_governance: bool,
    stage_result: StageResultEnvelope | None = None,
    stage_result_path: Path | None = None,
    subscription_provider: SubscriptionQuotaProvider | None = None,
) -> UsageGovernanceState:
    governance = config.usage_governance
    if not governance.enabled:
        state = _disabled_state(now=now).model_copy(
            update={
                "auto_resume": governance.auto_resume,
                "calendar_timezone": governance.calendar_timezone,
                "daemon_session_id": daemon_session_id,
                "paused_by_governance": False,
            }
        )
        if paths.usage_governance_state_file.exists() or paused_by_governance:
            save_usage_governance_state(paths, state)
        return state

    if stage_result is not None and stage_result_path is not None:
        record_stage_result_usage(
            paths,
            stage_result=stage_result,
            stage_result_path=stage_result_path,
            now=now,
            daemon_session_id=daemon_session_id,
            config=config,
        )

    reconcile_usage_ledger_from_stage_results(
        paths,
        now=now,
        daemon_session_id=daemon_session_id,
        config=config,
    )
    ledger_entries = load_usage_governance_ledger(paths)

    blockers: list[UsageGovernanceBlocker] = []
    if governance.runtime_token_rules.enabled:
        blockers.extend(
            _evaluate_runtime_token_rules(
                ledger_entries,
                config=config,
                now=now,
                daemon_session_id=daemon_session_id,
            )
        )

    subscription_status = _subscription_quota_status(
        paths,
        config=config,
        now=now,
        provider=subscription_provider,
    )
    blockers.extend(
        _evaluate_subscription_quota_rules(
            subscription_status,
            config=config,
        )
    )

    active_blockers = tuple(blockers)
    auto_resume_possible = all(blocker.auto_resume_possible for blocker in active_blockers)
    next_auto_resume_at = _next_auto_resume_at(active_blockers)
    state = UsageGovernanceState(
        enabled=True,
        auto_resume=governance.auto_resume,
        auto_resume_possible=auto_resume_possible,
        evaluation_boundary=governance.evaluation_boundary.value,
        calendar_timezone=governance.calendar_timezone,
        daemon_session_id=daemon_session_id,
        last_evaluated_at=now,
        active_blockers=active_blockers,
        paused_by_governance=paused_by_governance or bool(active_blockers),
        next_auto_resume_at=next_auto_resume_at,
        subscription_quota_status=subscription_status,
    )
    save_usage_governance_state(paths, state)
    return state


def record_stage_result_usage(
    paths: WorkspacePaths,
    *,
    stage_result: StageResultEnvelope,
    stage_result_path: Path,
    now: datetime,
    daemon_session_id: str | None,
    config: RuntimeConfig,
) -> bool:
    if not _should_record_runtime_tokens(config, stage_result):
        return False

    dedupe_key = _stage_result_dedupe_key(paths, stage_result_path)
    existing_keys = {entry.dedupe_key for entry in load_usage_governance_ledger(paths)}
    if dedupe_key in existing_keys:
        return False

    entry = _ledger_entry_from_stage_result(
        paths,
        stage_result=stage_result,
        stage_result_path=stage_result_path,
        counted_at=now,
        daemon_session_id=daemon_session_id,
    )
    _append_ledger_entry(paths, entry)
    return True


def reconcile_usage_ledger_from_stage_results(
    paths: WorkspacePaths,
    *,
    now: datetime,
    daemon_session_id: str | None,
    config: RuntimeConfig,
) -> int:
    if not config.usage_governance.enabled or not config.usage_governance.runtime_token_rules.enabled:
        return 0

    existing_keys = {entry.dedupe_key for entry in load_usage_governance_ledger(paths)}
    repaired = 0
    for stage_result_path in sorted(paths.runs_dir.glob("*/stage_results/*.json")):
        dedupe_key = _stage_result_dedupe_key(paths, stage_result_path)
        if dedupe_key in existing_keys:
            continue
        try:
            payload = json.loads(stage_result_path.read_text(encoding="utf-8"))
            stage_result = StageResultEnvelope.model_validate(payload)
        except Exception:
            continue
        if not _should_record_runtime_tokens(config, stage_result):
            continue
        entry = _ledger_entry_from_stage_result(
            paths,
            stage_result=stage_result,
            stage_result_path=stage_result_path,
            counted_at=now,
            daemon_session_id=daemon_session_id,
        )
        _append_ledger_entry(paths, entry)
        existing_keys.add(dedupe_key)
        repaired += 1
    return repaired


def load_usage_governance_ledger(paths: WorkspacePaths) -> tuple[UsageGovernanceLedgerEntry, ...]:
    if not paths.usage_governance_ledger_file.is_file():
        return ()
    entries: list[UsageGovernanceLedgerEntry] = []
    for line in paths.usage_governance_ledger_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entries.append(UsageGovernanceLedgerEntry.model_validate_json(line))
    return tuple(entries)


def _evaluate_runtime_token_rules(
    entries: tuple[UsageGovernanceLedgerEntry, ...],
    *,
    config: RuntimeConfig,
    now: datetime,
    daemon_session_id: str | None,
) -> tuple[UsageGovernanceBlocker, ...]:
    blockers: list[UsageGovernanceBlocker] = []
    timezone_info = _calendar_timezone(config.usage_governance.calendar_timezone)
    for rule in config.usage_governance.runtime_token_rules.rules:
        window_entries = _entries_for_runtime_window(
            entries,
            window=rule.window,
            now=now,
            daemon_session_id=daemon_session_id,
            timezone_info=timezone_info,
        )
        observed = _observed_metric(window_entries, rule.metric)
        if observed < rule.threshold:
            continue
        next_resume = _runtime_rule_next_resume(
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


def _evaluate_subscription_quota_rules(
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


def _subscription_quota_status(
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
        age = now - _ensure_utc(previous_status.last_refreshed_at)
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


def _entries_for_runtime_window(
    entries: tuple[UsageGovernanceLedgerEntry, ...],
    *,
    window: UsageGovernanceRuntimeTokenWindow,
    now: datetime,
    daemon_session_id: str | None,
    timezone_info: ZoneInfo,
) -> tuple[UsageGovernanceLedgerEntry, ...]:
    if window is UsageGovernanceRuntimeTokenWindow.ROLLING_5H:
        cutoff = now - _ROLLING_5H
        return tuple(entry for entry in entries if _ensure_utc(entry.stage_completed_at) >= cutoff)
    if window is UsageGovernanceRuntimeTokenWindow.CALENDAR_WEEK:
        start = _calendar_week_start(now, timezone_info=timezone_info)
        return tuple(entry for entry in entries if _ensure_utc(entry.stage_completed_at) >= start)
    if window is UsageGovernanceRuntimeTokenWindow.DAEMON_SESSION:
        return tuple(entry for entry in entries if entry.daemon_session_id == daemon_session_id)
    return entries


def _runtime_rule_next_resume(
    entries: tuple[UsageGovernanceLedgerEntry, ...],
    *,
    window: UsageGovernanceRuntimeTokenWindow,
    metric: UsageGovernanceRuntimeTokenMetric,
    threshold: int,
    now: datetime,
    timezone_info: ZoneInfo,
) -> datetime | None:
    if window is UsageGovernanceRuntimeTokenWindow.ROLLING_5H:
        remaining = _observed_metric(entries, metric)
        for entry in sorted(entries, key=lambda item: _ensure_utc(item.stage_completed_at)):
            remaining -= _metric_value(entry, metric)
            candidate = _ensure_utc(entry.stage_completed_at) + _ROLLING_5H
            if remaining < threshold:
                return candidate
        return None
    if window is UsageGovernanceRuntimeTokenWindow.CALENDAR_WEEK:
        return _calendar_week_start(now, timezone_info=timezone_info) + timedelta(days=7)
    return None


def _observed_metric(
    entries: tuple[UsageGovernanceLedgerEntry, ...],
    metric: UsageGovernanceRuntimeTokenMetric,
) -> int:
    return sum(_metric_value(entry, metric) for entry in entries)


def _metric_value(
    entry: UsageGovernanceLedgerEntry,
    metric: UsageGovernanceRuntimeTokenMetric,
) -> int:
    if metric is UsageGovernanceRuntimeTokenMetric.TOTAL_TOKENS:
        return entry.token_usage.total_tokens
    raise ValueError(f"unsupported runtime token metric: {metric.value}")


def _next_auto_resume_at(
    blockers: tuple[UsageGovernanceBlocker, ...],
) -> datetime | None:
    if not blockers:
        return None
    if any(not blocker.auto_resume_possible for blocker in blockers):
        return None
    candidates = tuple(
        blocker.next_auto_resume_at for blocker in blockers if blocker.next_auto_resume_at is not None
    )
    if len(candidates) != len(blockers):
        return None
    return min(candidates)


def _should_evaluate(
    paths: WorkspacePaths,
    config: RuntimeConfig,
    snapshot: RuntimeSnapshot,
) -> bool:
    return (
        config.usage_governance.enabled
        or paths.usage_governance_state_file.exists()
        or has_pause_source(snapshot, USAGE_GOVERNANCE_PAUSE_SOURCE)
    )


def _should_record_runtime_tokens(
    config: RuntimeConfig,
    stage_result: StageResultEnvelope,
) -> bool:
    return (
        config.usage_governance.enabled
        and config.usage_governance.runtime_token_rules.enabled
        and stage_result.token_usage is not None
    )


def _ledger_entry_from_stage_result(
    paths: WorkspacePaths,
    *,
    stage_result: StageResultEnvelope,
    stage_result_path: Path,
    counted_at: datetime,
    daemon_session_id: str | None,
) -> UsageGovernanceLedgerEntry:
    assert stage_result.token_usage is not None
    relative_path = _stage_result_dedupe_key(paths, stage_result_path)
    return UsageGovernanceLedgerEntry(
        dedupe_key=relative_path,
        counted_at=counted_at,
        stage_completed_at=stage_result.completed_at,
        plane=stage_result.plane,
        run_id=stage_result.run_id,
        stage_id=stage_result.stage_kind_id or stage_result.stage.value,
        work_item_kind=stage_result.work_item_kind,
        work_item_id=stage_result.work_item_id,
        token_usage=stage_result.token_usage,
        stage_result_path=relative_path,
        daemon_session_id=daemon_session_id,
    )


def _append_ledger_entry(paths: WorkspacePaths, entry: UsageGovernanceLedgerEntry) -> None:
    paths.usage_governance_ledger_file.parent.mkdir(parents=True, exist_ok=True)
    with paths.usage_governance_ledger_file.open("a", encoding="utf-8") as handle:
        handle.write(entry.model_dump_json() + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _stage_result_dedupe_key(paths: WorkspacePaths, stage_result_path: Path) -> str:
    try:
        return stage_result_path.resolve().relative_to(paths.root).as_posix()
    except ValueError:
        return stage_result_path.resolve().as_posix()


def _disabled_state(*, now: datetime) -> UsageGovernanceState:
    return UsageGovernanceState(
        enabled=False,
        auto_resume=True,
        auto_resume_possible=True,
        last_evaluated_at=now,
        subscription_quota_status=SubscriptionQuotaStatus(),
    )


def _degraded_subscription_status(*, now: datetime, detail: str) -> SubscriptionQuotaStatus:
    return SubscriptionQuotaStatus(
        enabled=True,
        provider="codex_chatgpt_oauth",
        state="degraded",
        detail=detail,
        last_refreshed_at=now,
    )


def _subscription_status_from_token_count_payload(
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
        resets_at = _datetime_from_unix_seconds(raw.get("resets_at"))
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


def _recent_jsonl_files(root: Path, *, limit: int) -> tuple[Path, ...]:
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


def _latest_token_count_payload(path: Path) -> dict[str, object] | None:
    for line in _tail_lines(path, max_bytes=512_000):
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


def _tail_lines(path: Path, *, max_bytes: int) -> tuple[str, ...]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            payload = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ()
    return tuple(reversed(payload.splitlines()))


def _calendar_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _calendar_week_start(now: datetime, *, timezone_info: ZoneInfo) -> datetime:
    local_now = _ensure_utc(now).astimezone(timezone_info)
    local_start = local_now - timedelta(
        days=local_now.weekday(),
        hours=local_now.hour,
        minutes=local_now.minute,
        seconds=local_now.second,
        microseconds=local_now.microsecond,
    )
    return local_start.astimezone(timezone.utc)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _datetime_from_unix_seconds(value: object) -> datetime | None:
    if not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(float(value), timezone.utc)


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _emit_governance_monitor_events(
    engine: RuntimeEngine,
    *,
    state: UsageGovernanceState,
    previous_state: UsageGovernanceState,
    had_governance_pause: bool,
    has_governance_pause: bool,
) -> None:
    if state.subscription_quota_status.state == "degraded":
        engine._emit_monitor_event(
            "usage_governance_degraded",
            source=state.subscription_quota_status.provider,
            policy=state.subscription_quota_status.degraded_policy,
            detail=state.subscription_quota_status.detail,
        )

    if not had_governance_pause and has_governance_pause and state.active_blockers:
        for blocker in state.active_blockers:
            write_runtime_event(
                engine.paths,
                event_type="usage_governance_blocked",
                data=blocker.model_dump(mode="json"),
            )
            engine._emit_monitor_event(
                "usage_governance_paused",
                source=blocker.source,
                rule_id=blocker.rule_id,
                window=blocker.window,
                observed=blocker.observed,
                threshold=blocker.threshold,
                next_auto_resume_at=(
                    blocker.next_auto_resume_at.isoformat()
                    if blocker.next_auto_resume_at is not None
                    else None
                ),
            )
        return

    if had_governance_pause and not has_governance_pause:
        engine._emit_monitor_event(
            "usage_governance_resumed",
            cleared_rules=",".join(blocker.rule_id for blocker in previous_state.active_blockers),
        )


__all__ = [
    "CodexChatGPTOAuthQuotaAdapter",
    "SubscriptionQuotaProvider",
    "SubscriptionQuotaStatus",
    "SubscriptionQuotaWindowReading",
    "UsageGovernanceBlocker",
    "UsageGovernanceLedgerEntry",
    "UsageGovernanceState",
    "evaluate_and_apply_usage_governance",
    "evaluate_usage_governance",
    "load_usage_governance_ledger",
    "load_usage_governance_state",
    "reconcile_usage_ledger_from_stage_results",
    "record_stage_result_usage",
    "save_usage_governance_state",
]
