"""Recovery counter helpers for compiled graph authority."""

from __future__ import annotations

import re

from millrace_ai.architecture.loop_graphs import GraphLoopCounterName
from millrace_ai.contracts import Plane, RecoveryCounterEntry, RecoveryCounters, RuntimeSnapshot, StageResultEnvelope
from millrace_ai.router import counter_key_for_failure_class


def counter_attempts(
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    failure_class: str,
    *,
    plane: Plane,
) -> int:
    """Read a counter value from the generic counter store.

    The active runtime path should prefer *counter_attempts_for_counter_id*
    with a policy-supplied counter_id. This function is retained as a
    compatibility projection for code that still dispatches by plane.
    """
    entry = matching_counter_entry(snapshot, counters, failure_class)
    if entry is None:
        return 0
    if plane is Plane.EXECUTION:
        return entry.troubleshoot_attempt_count
    return entry.mechanic_attempt_count


def counter_attempts_for_name(
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    failure_class: str,
    *,
    counter_name: GraphLoopCounterName,
) -> int:
    """Read a counter by GraphLoopCounterName from the generic counter store.

    ``GraphLoopCounterName`` is used only as a compatibility/compiler migration
    input; the active runtime authority is the generic ``counter_id`` string.
    """
    return counter_attempts_for_counter_id(
        snapshot, counters, failure_class, counter_id=counter_name.value
    )


def counter_attempts_for_counter_id(
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    failure_class: str,
    *,
    counter_id: str,
) -> int:
    """Read a counter value from the generic counter store by *counter_id*.

    This is the canonical active runtime path for reading recovery counter
    values.  It does not dispatch on plane, family, stage, or enum.

    When the generic counter store has no value for *counter_id*, falls back
    to the snapshot's legacy compatibility field to avoid silently altering
    recovery routing during migration from legacy counter state.
    """
    entry = matching_counter_entry(snapshot, counters, failure_class)
    if entry is not None and counter_id in entry.counters:
        # Treat any explicit value (including zero) as authoritative.
        return entry.counters[counter_id]
    # Fall back to snapshot legacy field only when the generic store has no
    # matching entry or the entry lacks the requested counter ID.
    return int(getattr(snapshot, counter_id, 0))


def matching_counter_entry(
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    failure_class: str,
) -> RecoveryCounterEntry | None:
    if snapshot.active_work_item_family_id is None or snapshot.active_work_item_id is None:
        return None

    normalized_failure_class = normalize_failure_class(failure_class)
    for entry in counters.entries:
        if entry.work_item_family_id != snapshot.active_work_item_family_id:
            continue
        if entry.work_item_id != snapshot.active_work_item_id:
            continue
        if normalize_failure_class(entry.failure_class) != normalized_failure_class:
            continue
        return entry
    return None


def counter_key_from_snapshot(snapshot: RuntimeSnapshot, failure_class: str) -> str | None:
    if snapshot.active_work_item_family_id is None or snapshot.active_work_item_id is None:
        return None
    return counter_key_for_failure_class(
        work_item_family_id=snapshot.active_work_item_family_id,
        work_item_kind=snapshot.active_work_item_kind,
        work_item_id=snapshot.active_work_item_id,
        failure_class=failure_class,
    )


def resolve_failure_class(
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    *,
    default: str,
) -> str:
    metadata_failure_class = stage_result.metadata.get("failure_class")
    if isinstance(metadata_failure_class, str) and metadata_failure_class.strip():
        return normalize_failure_class(metadata_failure_class)
    if snapshot.current_failure_class is not None and snapshot.current_failure_class.strip():
        return normalize_failure_class(snapshot.current_failure_class)
    return normalize_failure_class(default)


def normalize_failure_class(failure_class: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", failure_class.strip().lower()).strip("_")
    if not normalized:
        raise ValueError("failure_class cannot be empty")
    return normalized


__all__ = [
    "counter_attempts",
    "counter_attempts_for_counter_id",
    "counter_attempts_for_name",
    "counter_key_from_snapshot",
    "matching_counter_entry",
    "normalize_failure_class",
    "resolve_failure_class",
]
