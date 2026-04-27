"""Usage-governance orchestration and pause-source application."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import RuntimeSnapshot, StageResultEnvelope
from millrace_ai.paths import WorkspacePaths
from millrace_ai.state_store import save_snapshot

from ..pause_state import (
    USAGE_GOVERNANCE_PAUSE_SOURCE,
    add_pause_source,
    has_pause_source,
    remove_pause_source,
)
from .ledger import (
    load_usage_governance_ledger,
    reconcile_usage_ledger_from_stage_results,
    record_stage_result_usage,
)
from .models import SubscriptionQuotaProvider, UsageGovernanceBlocker, UsageGovernanceState
from .monitoring import emit_governance_monitor_events
from .runtime_tokens import evaluate_runtime_token_rules
from .state import disabled_state, load_usage_governance_state, save_usage_governance_state
from .subscription_quota import evaluate_subscription_quota_rules, subscription_quota_status
from .time_windows import calendar_timezone

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine

_GOVERNANCE_LOCK = RLock()


def evaluate_and_apply_usage_governance(
    engine: RuntimeEngine,
    *,
    stage_result: StageResultEnvelope | None = None,
    stage_result_path: Path | None = None,
) -> UsageGovernanceState | None:
    assert engine.config is not None
    assert engine.snapshot is not None

    if not should_evaluate(engine.paths, engine.config, engine.snapshot):
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

        emit_governance_monitor_events(
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
        state = disabled_state(now=now).model_copy(
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
            evaluate_runtime_token_rules(
                ledger_entries,
                config=config,
                now=now,
                daemon_session_id=daemon_session_id,
                timezone_info=calendar_timezone(config.usage_governance.calendar_timezone),
            )
        )

    subscription_status = subscription_quota_status(
        paths,
        config=config,
        now=now,
        provider=subscription_provider,
    )
    blockers.extend(
        evaluate_subscription_quota_rules(
            subscription_status,
            config=config,
        )
    )

    active_blockers = tuple(blockers)
    auto_resume_possible = all(blocker.auto_resume_possible for blocker in active_blockers)
    next_resume = next_auto_resume_at(active_blockers)
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
        next_auto_resume_at=next_resume,
        subscription_quota_status=subscription_status,
    )
    save_usage_governance_state(paths, state)
    return state


def next_auto_resume_at(
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


def should_evaluate(
    paths: WorkspacePaths,
    config: RuntimeConfig,
    snapshot: RuntimeSnapshot,
) -> bool:
    return (
        config.usage_governance.enabled
        or paths.usage_governance_state_file.exists()
        or has_pause_source(snapshot, USAGE_GOVERNANCE_PAUSE_SOURCE)
    )


__all__ = [
    "evaluate_and_apply_usage_governance",
    "evaluate_usage_governance",
    "next_auto_resume_at",
    "should_evaluate",
]
