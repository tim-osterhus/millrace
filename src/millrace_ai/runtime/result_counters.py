"""Recovery-counter mutation for routed stage results."""

from __future__ import annotations

from typing import TYPE_CHECKING

from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    PlanningStageName,
    RecoveryCounterEntry,
    RecoveryCounters,
    RuntimeSnapshot,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.router import RouterDecision
from millrace_ai.state_store import save_recovery_counters

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


def increment_route_counters(
    engine: RuntimeEngine,
    snapshot: RuntimeSnapshot,
    decision: RouterDecision,
    stage_result: StageResultEnvelope,
) -> RuntimeSnapshot:
    assert engine.counters is not None
    work_item_kind = snapshot.active_work_item_kind
    work_item_id = snapshot.active_work_item_id
    if work_item_kind is None or work_item_id is None:
        return snapshot
    if decision.next_stage is ExecutionStageName.TROUBLESHOOTER:
        snapshot = increment_counter_field(
            engine,
            snapshot,
            engine.counters,
            failure_class=decision.failure_class or "recoverable_failure",
            work_item_kind=work_item_kind,
            work_item_id=work_item_id,
            field="troubleshoot_attempt_count",
        )
    elif decision.next_stage is PlanningStageName.MECHANIC:
        snapshot = increment_counter_field(
            engine,
            snapshot,
            engine.counters,
            failure_class=decision.failure_class or "recoverable_failure",
            work_item_kind=work_item_kind,
            work_item_id=work_item_id,
            field="mechanic_attempt_count",
        )
    elif decision.next_stage is ExecutionStageName.CONSULTANT:
        snapshot = increment_counter_field(
            engine,
            snapshot,
            engine.counters,
            failure_class=decision.failure_class or "recoverable_failure",
            work_item_kind=work_item_kind,
            work_item_id=work_item_id,
            field="consultant_invocations",
        )
    elif stage_result.terminal_result is ExecutionTerminalResult.FIX_NEEDED:
        snapshot = increment_counter_field(
            engine,
            snapshot,
            engine.counters,
            failure_class=decision.failure_class or "fix_cycle",
            work_item_kind=work_item_kind,
            work_item_id=work_item_id,
            field="fix_cycle_count",
        )
    return snapshot


def increment_counter_field(
    engine: RuntimeEngine,
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    *,
    failure_class: str,
    work_item_kind: WorkItemKind,
    work_item_id: str,
    field: str,
) -> RuntimeSnapshot:
    timestamp = engine._now()
    mutable_entries = list(counters.entries)
    for index, entry in enumerate(mutable_entries):
        if (
            entry.failure_class == failure_class
            and entry.work_item_kind is work_item_kind
            and entry.work_item_id == work_item_id
        ):
            mutable_entries[index] = entry.model_copy(
                update={field: getattr(entry, field) + 1, "last_updated_at": timestamp}
            )
            break
    else:
        mutable_entries.append(
            RecoveryCounterEntry(
                failure_class=failure_class,
                work_item_kind=work_item_kind,
                work_item_id=work_item_id,
                troubleshoot_attempt_count=1 if field == "troubleshoot_attempt_count" else 0,
                mechanic_attempt_count=1 if field == "mechanic_attempt_count" else 0,
                fix_cycle_count=1 if field == "fix_cycle_count" else 0,
                consultant_invocations=1 if field == "consultant_invocations" else 0,
                last_updated_at=timestamp,
            )
        )
    updated_counters = RecoveryCounters(entries=tuple(mutable_entries))
    engine.counters = updated_counters
    save_recovery_counters(engine.paths, updated_counters)
    updated_snapshot = snapshot.model_copy(
        update={field: getattr(snapshot, field) + 1, "updated_at": engine._now()}
    )
    engine.snapshot = updated_snapshot
    return updated_snapshot


__all__ = ["increment_counter_field", "increment_route_counters"]
