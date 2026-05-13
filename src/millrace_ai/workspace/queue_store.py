"""Public workspace queue facade over selection, transitions, and reconciliation."""

from __future__ import annotations

from pathlib import Path

from millrace_ai.contracts import (
    IncidentDocument,
    LearningRequestDocument,
    ProbeDocument,
    SpecDocument,
    TaskDocument,
    WorkItemKind,
)

from .initialization import bootstrap_workspace
from .operator_interventions import (
    OperatorInterventionResult,
    TaskSupersedeCascade,
    archive_blocked_task,
    archive_invalid_incident_artifact,
    cancel_incident,
    cancel_work_item,
    resolve_incident_by_operator,
    retarget_queued_task_dependency,
    supersede_task,
)
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
    enqueue_probe,
    enqueue_spec,
    enqueue_task,
    mark_incident_blocked,
    mark_incident_resolved,
    mark_learning_request_blocked,
    mark_learning_request_done,
    mark_probe_blocked,
    mark_probe_done,
    mark_spec_blocked,
    mark_spec_done,
    mark_task_blocked,
    mark_task_done,
    requeue_blocked_task,
    requeue_incident,
    requeue_learning_request,
    requeue_probe,
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

    def enqueue_probe(self, doc: ProbeDocument) -> Path:
        return enqueue_probe(self.paths, doc)

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

    def mark_probe_done(self, probe_id: str) -> Path:
        return mark_probe_done(self.paths, probe_id)

    def mark_probe_blocked(self, probe_id: str) -> Path:
        return mark_probe_blocked(self.paths, probe_id)

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

    def requeue_blocked_task(
        self,
        task_id: str,
        *,
        reason: str,
        actor: str,
        auto: bool,
        failure_class: str | None = None,
        attempt_number: int | None = None,
    ) -> Path:
        return requeue_blocked_task(
            self.paths,
            task_id,
            reason=reason,
            actor=actor,
            auto=auto,
            failure_class=failure_class,
            attempt_number=attempt_number,
        )

    def requeue_spec(self, spec_id: str, *, reason: str) -> Path:
        return requeue_spec(self.paths, spec_id, reason=reason)

    def requeue_probe(self, probe_id: str, *, reason: str) -> Path:
        return requeue_probe(self.paths, probe_id, reason=reason)

    def requeue_incident(self, incident_id: str, *, reason: str) -> Path:
        return requeue_incident(self.paths, incident_id, reason=reason)

    def requeue_learning_request(self, learning_request_id: str, *, reason: str) -> Path:
        return requeue_learning_request(self.paths, learning_request_id, reason=reason)

    def cancel_work_item(
        self,
        work_item_id: str,
        *,
        reason: str,
        work_item_kind: WorkItemKind | None = None,
        actor: str = "operator",
        force: bool = False,
    ) -> OperatorInterventionResult:
        return cancel_work_item(
            self.paths,
            work_item_id=work_item_id,
            work_item_kind=work_item_kind,
            reason=reason,
            actor=actor,
            force=force,
        )

    def archive_blocked_task(
        self,
        task_id: str,
        *,
        reason: str,
        actor: str = "operator",
    ) -> OperatorInterventionResult:
        return archive_blocked_task(self.paths, task_id=task_id, reason=reason, actor=actor)

    def supersede_task(
        self,
        old_task_id: str,
        *,
        replacement_task_id: str,
        reason: str,
        actor: str = "operator",
        cascade: TaskSupersedeCascade = "none",
    ) -> OperatorInterventionResult:
        return supersede_task(
            self.paths,
            old_task_id=old_task_id,
            replacement_task_id=replacement_task_id,
            reason=reason,
            actor=actor,
            cascade=cascade,
        )

    def retarget_queued_task_dependency(
        self,
        task_id: str,
        *,
        old_dependency_id: str,
        new_dependency_id: str,
        reason: str,
        actor: str = "operator",
    ) -> OperatorInterventionResult:
        return retarget_queued_task_dependency(
            self.paths,
            task_id=task_id,
            old_dependency_id=old_dependency_id,
            new_dependency_id=new_dependency_id,
            reason=reason,
            actor=actor,
        )

    def resolve_incident_by_operator(
        self,
        incident_id: str,
        *,
        reason: str,
        actor: str = "operator",
    ) -> OperatorInterventionResult:
        return resolve_incident_by_operator(self.paths, incident_id=incident_id, reason=reason, actor=actor)

    def cancel_incident(
        self,
        incident_id: str,
        *,
        reason: str,
        actor: str = "operator",
    ) -> OperatorInterventionResult:
        return cancel_incident(self.paths, incident_id=incident_id, reason=reason, actor=actor)

    def archive_invalid_incident_artifact(
        self,
        filename: str,
        *,
        reason: str,
        actor: str = "operator",
    ) -> OperatorInterventionResult:
        return archive_invalid_incident_artifact(self.paths, filename=filename, reason=reason, actor=actor)

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


__all__ = ["OperatorInterventionResult", "QueueClaim", "QueueStore", "StaleActiveState"]
