from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.contracts import (
    ExecutionStageName,
    IncidentDecision,
    IncidentDocument,
    Plane,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.control import RuntimeControl
from millrace_ai.events import read_runtime_events
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.state_store import load_snapshot

NOW = datetime(2026, 5, 12, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _task_doc(task_id: str, *, depends_on: tuple[str, ...] = ()) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="operator intervention integration test task",
        root_spec_id="spec-root-001",
        target_paths=("src/example.py",),
        acceptance=("bad intake is removed without file deletion",),
        required_checks=("uv run --extra dev python -m pytest tests/integration/test_operator_intervention_cleanup.py -q",),
        references=("lab/specs/pending/2026-05-12-millrace-operator-queue-and-incident-clearance-controls.md",),
        risk=("queue state drift",),
        depends_on=depends_on,
        created_at=NOW,
        created_by="tests",
    )


def _incident_doc(incident_id: str) -> IncidentDocument:
    return IncidentDocument(
        incident_id=incident_id,
        title=f"Incident {incident_id}",
        summary="bad intake incident",
        root_spec_id="spec-root-001",
        source_task_id="WP-000",
        source_stage=ExecutionStageName.CONSULTANT,
        source_plane=Plane.EXECUTION,
        failure_class="bad_intake",
        trigger_reason="known bad intake",
        consultant_decision=IncidentDecision.NEEDS_PLANNING,
        opened_at=NOW,
        opened_by="tests",
    )


def test_operator_intervention_cleanup_leaves_only_corrected_tasks_claimable(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("WP-000"))
    assert queue.claim_next_execution_task() is not None
    queue.mark_task_blocked("WP-000")
    queue.enqueue_task(_task_doc("WP-001A", depends_on=("WP-000",)))
    queue.enqueue_task(_task_doc("WP-000R"))
    queue.enqueue_task(_task_doc("WP-001A-after-WP-000R", depends_on=("WP-000R",)))
    queue.enqueue_incident(_incident_doc("incident-WP-000"))
    assert queue.claim_next_planning_item() is not None

    control = RuntimeControl(paths)
    supersede = control.supersede_task(
        old_task_id="WP-000",
        replacement_task_id="WP-000R",
        reason="replacement task has corrected scope",
    )
    cancel_dependent = control.cancel_work_item(
        work_item_id="WP-001A",
        work_item_kind=WorkItemKind.TASK,
        reason="dependent task was generated from superseded predecessor",
    )
    cancel_incident = control.cancel_incident(
        incident_id="incident-WP-000",
        reason="incident came from superseded bad intake",
    )

    assert supersede.applied is True
    assert cancel_dependent.applied is True
    assert cancel_incident.applied is True
    assert not (paths.tasks_blocked_dir / "WP-000.md").exists()
    assert not (paths.tasks_queue_dir / "WP-001A.md").exists()
    assert not (paths.incidents_active_dir / "incident-WP-000.md").exists()
    assert sorted(path.stem for path in paths.tasks_queue_dir.glob("*.md")) == [
        "WP-000R",
        "WP-001A-after-WP-000R",
    ]

    snapshot = load_snapshot(paths)
    assert snapshot.queue_depth_execution == 2
    assert snapshot.queue_depth_planning == 0
    event_types = [event.event_type for event in read_runtime_events(paths)]
    assert "task_superseded" in event_types
    assert "work_item_cancelled" in event_types
    assert "incident_cancelled" in event_types
