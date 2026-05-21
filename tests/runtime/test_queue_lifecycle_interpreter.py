from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from millrace_ai.contracts import (
    IncidentDecision,
    IncidentDocument,
    LearningRequestDocument,
    PlanningStageName,
    ProbeDocument,
    SpecDocument,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runtime.effects import SourceLifecycleAction, SourceLifecycleIntent
from millrace_ai.workspace.queue_lifecycle import QueueLifecycleInterpreter

NOW = datetime(2026, 5, 19, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _task_doc() -> TaskDocument:
    return TaskDocument(
        task_id="task-001",
        title="Task",
        target_paths=("src/millrace_ai/runtime/",),
        acceptance=("Lifecycle interpreter moves task.",),
        required_checks=("pytest tests/runtime/test_queue_lifecycle_interpreter.py -q",),
        references=("lab/tasks/queue/2026-05-19-v020-07-runtime-effects-and-lifecycle-interpreter.md",),
        risk=("Queue state drift.",),
        created_at=NOW,
        created_by="tests",
    )


def _spec_doc() -> SpecDocument:
    return SpecDocument(
        spec_id="spec-001",
        title="Spec",
        summary="Lifecycle interpreter moves spec.",
        source_type="manual",
        goals=("Test spec lifecycle.",),
        constraints=("Stay deterministic.",),
        acceptance=("Spec moved.",),
        references=("tests/runtime/test_queue_lifecycle_interpreter.py",),
        created_at=NOW,
        created_by="tests",
    )


def _probe_doc() -> ProbeDocument:
    return ProbeDocument(
        probe_id="probe-001",
        title="Probe",
        summary="Lifecycle interpreter moves probe.",
        request="Research the target surface.",
        created_at=NOW,
        created_by="tests",
    )


def _incident_doc() -> IncidentDocument:
    return IncidentDocument(
        incident_id="incident-001",
        title="Incident",
        summary="Lifecycle interpreter moves incident.",
        source_stage=PlanningStageName.AUDITOR,
        source_plane="planning",
        failure_class="test_incident",
        trigger_reason="Exercise incident lifecycle.",
        consultant_decision=IncidentDecision.NEEDS_PLANNING,
        opened_at=NOW,
        opened_by="tests",
    )


def _learning_request_doc() -> LearningRequestDocument:
    return LearningRequestDocument(
        learning_request_id="learn-001",
        title="Learning",
        requested_action="improve",
        target_skill_id="checker-core",
        target_stage="curator",
        created_at=NOW,
        created_by="tests",
    )


@pytest.mark.parametrize(
    ("kind", "work_item_id", "done_filename", "blocked_filename"),
    (
        (WorkItemKind.TASK, "task-001", "tasks/done/task-001.md", "tasks/blocked/task-001.md"),
        (WorkItemKind.SPEC, "spec-001", "specs/done/spec-001.md", "specs/blocked/spec-001.md"),
        (WorkItemKind.PROBE, "probe-001", "probes/done/probe-001.md", "probes/blocked/probe-001.md"),
        (
            WorkItemKind.INCIDENT,
            "incident-001",
            "incidents/resolved/incident-001.md",
            "incidents/blocked/incident-001.md",
        ),
        (
            WorkItemKind.LEARNING_REQUEST,
            "learn-001",
            "learning/requests/done/learn-001.md",
            "learning/requests/blocked/learn-001.md",
        ),
    ),
)
def test_queue_lifecycle_interpreter_moves_builtin_active_items(
    tmp_path: Path,
    kind: WorkItemKind,
    work_item_id: str,
    done_filename: str,
    blocked_filename: str,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    if kind is WorkItemKind.TASK:
        queue.enqueue_task(_task_doc())
        assert queue.claim_next_execution_task() is not None
    elif kind is WorkItemKind.SPEC:
        queue.enqueue_spec(_spec_doc())
        assert queue.claim_next_planning_item() is not None
    elif kind is WorkItemKind.PROBE:
        queue.enqueue_probe(_probe_doc())
        assert queue.claim_next_planning_item() is not None
    elif kind is WorkItemKind.INCIDENT:
        queue.enqueue_incident(_incident_doc())
        assert queue.claim_next_planning_item() is not None
    else:
        queue.enqueue_learning_request(_learning_request_doc())
        assert queue.claim_next_learning_request() is not None

    destination = QueueLifecycleInterpreter(paths).apply(
        SourceLifecycleIntent(
            lifecycle_plan_id="test.complete",
            action=SourceLifecycleAction.COMPLETE,
            work_item_kind=kind,
            work_item_id=work_item_id,
        )
    )

    assert destination == paths.root / "millrace-agents" / done_filename
    assert destination.is_file()

    queue = QueueStore(paths)
    if kind is WorkItemKind.TASK:
        queue.enqueue_task(_task_doc().model_copy(update={"task_id": "task-002"}))
        assert queue.claim_next_execution_task() is not None
        blocked_id = "task-002"
    elif kind is WorkItemKind.SPEC:
        queue.enqueue_spec(_spec_doc().model_copy(update={"spec_id": "spec-002"}))
        assert queue.claim_next_planning_item() is not None
        blocked_id = "spec-002"
    elif kind is WorkItemKind.PROBE:
        queue.enqueue_probe(_probe_doc().model_copy(update={"probe_id": "probe-002"}))
        assert queue.claim_next_planning_item() is not None
        blocked_id = "probe-002"
    elif kind is WorkItemKind.INCIDENT:
        queue.enqueue_incident(_incident_doc().model_copy(update={"incident_id": "incident-002"}))
        assert queue.claim_next_planning_item() is not None
        blocked_id = "incident-002"
    else:
        queue.enqueue_learning_request(
            _learning_request_doc().model_copy(update={"learning_request_id": "learn-002"})
        )
        assert queue.claim_next_learning_request() is not None
        blocked_id = "learn-002"

    blocked_destination = QueueLifecycleInterpreter(paths).apply(
        SourceLifecycleIntent(
            lifecycle_plan_id="test.block",
            action=SourceLifecycleAction.BLOCK,
            work_item_kind=kind,
            work_item_id=blocked_id,
        )
    )

    assert blocked_destination == paths.root / "millrace-agents" / blocked_filename.replace(work_item_id, blocked_id)
    assert blocked_destination.is_file()
