"""Recovery event emission helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import JsonValue

from millrace_ai.events import write_runtime_event
from millrace_ai.workspace.paths import WorkspacePaths

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine

    from .queue_mutation import BlockedWorkItemRetryResult, StrandedBlockedDependency


def emit_blocked_retry_events(paths: WorkspacePaths, result: "BlockedWorkItemRetryResult") -> None:
    payload: dict[str, JsonValue] = {
        "work_item_family_id": result.work_item_family_id,
        "work_item_kind": result.work_item_kind.value if result.work_item_kind is not None else None,
        "work_item_id": result.work_item_id,
        "actor": result.actor,
        "auto": result.auto,
        "reason": result.reason,
        "failure_class": result.failure_class,
        "attempt_number": result.attempt_number,
        "source_state": result.source_state,
        "destination_state": result.destination_state,
    }
    write_runtime_event(paths, event_type="blocked_work_item_requeued", data=payload)
    if result.work_item_family_id == "task":
        write_runtime_event(
            paths,
            event_type="blocked_task_requeued",
            data={
                "task_id": result.work_item_id,
                "actor": result.actor,
                "auto": result.auto,
                "reason": result.reason,
                "failure_class": result.failure_class,
                "attempt_number": result.attempt_number,
                "source_state": result.source_state,
                "destination_state": result.destination_state,
            },
        )


def emit_auto_recovery_skipped(
    engine: "RuntimeEngine",
    candidate: "StrandedBlockedDependency",
    *,
    reason: str,
) -> None:
    write_runtime_event(
        engine.paths,
        event_type="blocked_dependency_auto_requeue_skipped",
        data={
            "task_id": candidate.blocked_task_id,
            "queued_dependents": list(candidate.queued_dependent_ids),
            "reason": reason,
            "failure_class": (candidate.metadata.failure_class if candidate.metadata is not None else None),
        },
    )
    engine._emit_monitor_event(
        "blocked_lineage_requires_operator_review",
        task_id=candidate.blocked_task_id,
        queued_dependents=list(candidate.queued_dependent_ids),
        reason=reason,
    )


__all__ = [
    "emit_auto_recovery_skipped",
    "emit_blocked_retry_events",
]
