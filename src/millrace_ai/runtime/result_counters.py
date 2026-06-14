"""Recovery-counter mutation for routed stage results."""

from __future__ import annotations

from typing import TYPE_CHECKING

from millrace_ai.contracts import (
    RecoveryCounterEntry,
    RecoveryCounters,
    RuntimeSnapshot,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.contracts.router import RouterDecision
from millrace_ai.contracts.work_refs import coerce_family_and_kind
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
    work_item_family_id = snapshot.active_work_item_family_id
    work_item_kind = snapshot.active_work_item_kind
    work_item_id = snapshot.active_work_item_id
    if (work_item_family_id is None and work_item_kind is None) or work_item_id is None:
        return snapshot
    counter_mutation_name = decision.counter_mutation_name or decision.recovery_counter_name
    if counter_mutation_name is not None:
        snapshot = increment_counter_field(
            engine,
            snapshot,
            engine.counters,
            failure_class=decision.failure_class or "recoverable_failure",
            work_item_family_id=work_item_family_id,
            work_item_kind=work_item_kind,
            work_item_id=work_item_id,
            counter_id=counter_mutation_name,
        )
    return snapshot


def increment_counter_field(
    engine: RuntimeEngine,
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    *,
    failure_class: str,
    work_item_family_id: str | None = None,
    work_item_kind: WorkItemKind | None = None,
    work_item_id: str,
    counter_id: str,
) -> RuntimeSnapshot:
    """Increment a generic counter identified by *counter_id*.

    The counter value is stored in the generic ``counters`` dict of the
    matching :class:`RecoveryCounterEntry`.
    """
    family_id, work_item_kind = coerce_family_and_kind(
        family_id=work_item_family_id,
        work_item_kind=work_item_kind,
    )
    if family_id is None:
        raise ValueError("work_item_family_id or work_item_kind is required")
    timestamp = engine._now()
    mutable_entries = list(counters.entries)
    for index, entry in enumerate(mutable_entries):
        if (
            entry.failure_class == failure_class
            and entry.work_item_family_id == family_id
            and entry.work_item_id == work_item_id
        ):
            updated_counters = dict(entry.counters)
            updated_counters[counter_id] = updated_counters.get(counter_id, 0) + 1
            mutable_entries[index] = entry.model_copy(
                update={"counters": updated_counters, "last_updated_at": timestamp}
            )
            break
    else:
        mutable_entries.append(
            RecoveryCounterEntry(
                failure_class=failure_class,
                work_item_family_id=family_id,
                work_item_kind=work_item_kind,
                work_item_id=work_item_id,
                counters={counter_id: 1},
                last_updated_at=timestamp,
            )
        )
    updated_counters = RecoveryCounters(entries=tuple(mutable_entries))
    engine.counters = updated_counters
    save_recovery_counters(engine.paths, updated_counters)

    snapshot_update: dict[str, object] = {"updated_at": engine._now()}
    updated_snapshot = snapshot.model_copy(update=snapshot_update)
    engine.snapshot = updated_snapshot
    return updated_snapshot


__all__ = ["increment_counter_field", "increment_route_counters"]
