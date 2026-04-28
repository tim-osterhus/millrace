"""Public workspace queue facade over selection, transitions, and reconciliation."""

from __future__ import annotations

from pathlib import Path

from millrace_ai.contracts import IncidentDocument, LearningRequestDocument, SpecDocument, TaskDocument, WorkItemKind

from .initialization import bootstrap_workspace
from .paths import WorkspacePaths, workspace_paths
from .queue_reconciliation import (
    StaleActiveState,
    detect_execution_stale_state,
    detect_planning_stale_state,
)
from .queue_selection import (
    QueueClaim,
    claim_next_execution_task,
    claim_next_learning_request,
    claim_next_planning_item,
)
from .queue_transitions import (
    enqueue_incident,
    enqueue_learning_request,
    enqueue_spec,
    enqueue_task,
    mark_incident_blocked,
    mark_incident_resolved,
    mark_learning_request_blocked,
    mark_learning_request_done,
    mark_spec_blocked,
    mark_spec_done,
    mark_task_blocked,
    mark_task_done,
    requeue_incident,
    requeue_learning_request,
    requeue_spec,
    requeue_task,
)


class QueueStore:
    """Queue operations for tasks, specs, and incidents."""

    def __init__(self, target: WorkspacePaths | Path | str) -> None:
        paths = target if isinstance(target, WorkspacePaths) else workspace_paths(target)
        self.paths = bootstrap_workspace(paths)

    def enqueue_task(self, doc: TaskDocument) -> Path:
        return enqueue_task(self.paths, doc)

    def enqueue_spec(self, doc: SpecDocument) -> Path:
        return enqueue_spec(self.paths, doc)

    def enqueue_incident(self, doc: IncidentDocument) -> Path:
        return enqueue_incident(self.paths, doc)

    def enqueue_learning_request(self, doc: LearningRequestDocument) -> Path:
        return enqueue_learning_request(self.paths, doc)

    def claim_next_execution_task(self, *, root_spec_id: str | None = None) -> QueueClaim | None:
        return claim_next_execution_task(self.paths, root_spec_id=root_spec_id)

    def claim_next_planning_item(self, *, root_spec_id: str | None = None) -> QueueClaim | None:
        return claim_next_planning_item(self.paths, root_spec_id=root_spec_id)

    def claim_next_learning_request(self) -> QueueClaim | None:
        return claim_next_learning_request(self.paths)

    def mark_task_done(self, task_id: str) -> Path:
        return mark_task_done(self.paths, task_id)

    def mark_task_blocked(self, task_id: str) -> Path:
        return mark_task_blocked(self.paths, task_id)

    def mark_spec_done(self, spec_id: str) -> Path:
        return mark_spec_done(self.paths, spec_id)

    def mark_spec_blocked(self, spec_id: str) -> Path:
        return mark_spec_blocked(self.paths, spec_id)

    def mark_incident_resolved(self, incident_id: str) -> Path:
        return mark_incident_resolved(self.paths, incident_id)

    def mark_incident_blocked(self, incident_id: str) -> Path:
        return mark_incident_blocked(self.paths, incident_id)

    def mark_learning_request_done(self, learning_request_id: str) -> Path:
        return mark_learning_request_done(self.paths, learning_request_id)

    def mark_learning_request_blocked(self, learning_request_id: str) -> Path:
        return mark_learning_request_blocked(self.paths, learning_request_id)

    def requeue_task(self, task_id: str, *, reason: str) -> Path:
        return requeue_task(self.paths, task_id, reason=reason)

    def requeue_spec(self, spec_id: str, *, reason: str) -> Path:
        return requeue_spec(self.paths, spec_id, reason=reason)

    def requeue_incident(self, incident_id: str, *, reason: str) -> Path:
        return requeue_incident(self.paths, incident_id, reason=reason)

    def requeue_learning_request(self, learning_request_id: str, *, reason: str) -> Path:
        return requeue_learning_request(self.paths, learning_request_id, reason=reason)

    def detect_execution_stale_state(self, *, snapshot_active_task_id: str | None) -> StaleActiveState:
        return detect_execution_stale_state(self.paths, snapshot_active_task_id=snapshot_active_task_id)

    def detect_planning_stale_state(
        self,
        *,
        snapshot_active_kind: WorkItemKind | None,
        snapshot_active_item_id: str | None,
    ) -> StaleActiveState:
        return detect_planning_stale_state(
            self.paths,
            snapshot_active_kind=snapshot_active_kind,
            snapshot_active_item_id=snapshot_active_item_id,
        )


__all__ = ["QueueClaim", "QueueStore", "StaleActiveState"]
