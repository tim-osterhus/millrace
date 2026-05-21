from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from millrace_ai.architecture import WorkItemFamilyDefinition
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    ExecutionStageName,
    IncidentDecision,
    IncidentDocument,
    Plane,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.errors import QueueStateError
from millrace_ai.events import read_runtime_events
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.work_documents import read_work_document_as
from millrace_ai.workspace.operator_interventions import (
    archive_invalid_incident_artifact,
    cancel_incident,
    cancel_work_item,
    retarget_queued_task_dependency,
    supersede_task,
)

NOW = datetime(2026, 5, 12, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _task_doc(task_id: str, *, depends_on: tuple[str, ...] = ()) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="operator intervention test task",
        root_spec_id="spec-root-001",
        target_paths=("src/example.py",),
        acceptance=("operator intervention is audited",),
        required_checks=("uv run --extra dev python -m pytest tests/workspace/test_operator_interventions.py -q",),
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
        source_task_id="task-old",
        source_stage=ExecutionStageName.CONSULTANT,
        source_plane=Plane.EXECUTION,
        failure_class="bad_intake",
        trigger_reason="known bad intake",
        consultant_decision=IncidentDecision.NEEDS_PLANNING,
        opened_at=NOW,
        opened_by="tests",
    )


def _json_lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _custom_planning_family() -> WorkItemFamilyDefinition:
    return WorkItemFamilyDefinition(
        family_id="custom_review",
        plane=Plane.PLANNING,
        entry_key="custom_review",
        display_name="Custom Review",
        document_kind="custom_review",
        runtime_relative_dir="custom/reviews",
        file_extension=".json",
        schema_id="custom_review_document_v1",
        document_adapter_id="custom_review_json_v1",
        queue_dirs={
            "queue": "custom/reviews/queue",
            "active": "custom/reviews/active",
            "done": "custom/reviews/done",
            "blocked": "custom/reviews/blocked",
            "canceled": "custom/reviews/canceled",
        },
        lifecycle_states=("queue", "active", "done", "blocked", "canceled"),
        claimable_state="queue",
        active_state="active",
        done_state="done",
        blocked_state="blocked",
        canceled_state="canceled",
        closure_blocking_states=("queue", "active", "blocked"),
        default_entry_key="custom_review",
        id_field="custom_id",
        created_at_field="created_at",
        lineage_fields=("root_spec_id",),
        operator_capabilities=("cancel", "inspect"),
    )


def _persist_custom_family(paths, family: WorkItemFamilyDefinition) -> None:
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.active_plan is not None
    updated = outcome.active_plan.model_copy(
        update={
            "work_item_families_by_id": {
                **outcome.active_plan.work_item_families_by_id,
                family.family_id: family,
            }
        }
    )
    (paths.state_dir / "compiled_plan.json").write_text(
        updated.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def test_cancel_queued_task_archives_document_and_writes_audit_event(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    QueueStore(paths).enqueue_task(_task_doc("task-cancel"))

    result = cancel_work_item(
        paths,
        work_item_id="task-cancel",
        work_item_kind=WorkItemKind.TASK,
        reason="task had the wrong implementation scope",
        actor="operator",
        now=NOW,
    )

    assert result.action == "cancel"
    assert result.work_item_kind is WorkItemKind.TASK
    assert result.work_item_id == "task-cancel"
    assert result.source_state == "queue"
    assert result.destination_state == "cancelled"
    assert result.source_path == paths.tasks_queue_dir / "task-cancel.md"
    assert result.destination_path.parent == paths.tasks_queue_dir / "cancelled"
    assert result.destination_path.is_file()
    assert not result.source_path.exists()

    audit = _json_lines(paths.tasks_queue_dir / "cancelled" / "interventions.jsonl")
    assert audit[0]["action"] == "cancel"
    assert audit[0]["work_item_id"] == "task-cancel"
    assert audit[0]["reason"] == "task had the wrong implementation scope"
    events = read_runtime_events(paths)
    assert events[-1].event_type == "work_item_cancelled"
    assert events[-1].data["work_item_id"] == "task-cancel"


def test_cancel_custom_family_uses_persisted_family_contract(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    family = _custom_planning_family()
    _persist_custom_family(paths, family)
    queue_dir = paths.runtime_root / family.queue_dirs.queue
    queue_dir.mkdir(parents=True, exist_ok=True)
    source = queue_dir / "custom-001.json"
    source.write_text('{"custom_id":"custom-001"}\n', encoding="utf-8")

    result = cancel_work_item(
        paths,
        work_item_id="custom-001",
        work_item_family_id="custom_review",
        reason="custom graph work was superseded",
        actor="operator",
        now=NOW,
    )

    assert result.action == "cancel"
    assert result.work_item_family_id == "custom_review"
    assert result.work_item_kind is None
    assert result.source_state == "queue"
    assert result.destination_state == "canceled"
    assert result.destination_path.parent == paths.runtime_root / family.queue_dirs.canceled
    assert result.destination_path.suffix == ".json"
    assert result.destination_path.is_file()
    assert not source.exists()
    audit = _json_lines(result.destination_path.parent / "interventions.jsonl")
    assert audit[0]["work_item_family_id"] == "custom_review"
    assert audit[0]["work_item_kind"] is None


def test_supersede_blocked_task_can_retarget_queued_dependents(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-old"))
    assert queue.claim_next_execution_task() is not None
    queue.mark_task_blocked("task-old")
    queue.enqueue_task(_task_doc("task-new"))
    queue.enqueue_task(_task_doc("task-dependent", depends_on=("task-old",)))

    result = supersede_task(
        paths,
        old_task_id="task-old",
        replacement_task_id="task-new",
        reason="new task has corrected scope",
        actor="operator",
        cascade="retarget",
        now=NOW,
    )

    assert result.action == "supersede"
    assert result.source_state == "blocked"
    assert result.destination_state == "superseded"
    assert result.replacement_work_item_id == "task-new"
    assert result.affected_dependents == ("task-dependent",)
    assert result.destination_path.parent == paths.tasks_blocked_dir / "superseded"
    assert not (paths.tasks_blocked_dir / "task-old.md").exists()

    dependent = read_work_document_as(paths.tasks_queue_dir / "task-dependent.md", model=TaskDocument)
    assert dependent.depends_on == ("task-new",)
    event_types = [event.event_type for event in read_runtime_events(paths)]
    assert "task_superseded" in event_types
    assert "task_dependency_retargeted" in event_types


def test_retarget_dependency_requires_queued_task_and_existing_replacement(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-old"))
    queue.enqueue_task(_task_doc("task-new"))
    queue.enqueue_task(_task_doc("task-dependent", depends_on=("task-old",)))

    result = retarget_queued_task_dependency(
        paths,
        task_id="task-dependent",
        old_dependency_id="task-old",
        new_dependency_id="task-new",
        reason="replace superseded dependency",
        actor="operator",
        now=NOW,
    )

    assert result.action == "retarget_dependency"
    assert result.affected_dependents == ("task-dependent",)
    dependent = read_work_document_as(paths.tasks_queue_dir / "task-dependent.md", model=TaskDocument)
    assert dependent.depends_on == ("task-new",)

    with pytest.raises(QueueStateError, match="does not depend on"):
        retarget_queued_task_dependency(
            paths,
            task_id="task-dependent",
            old_dependency_id="missing-old",
            new_dependency_id="task-new",
            reason="bad retarget",
            actor="operator",
            now=NOW,
        )


def test_cancel_active_incident_archives_it_without_marking_resolved(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_incident(_incident_doc("incident-bad-intake"))
    assert queue.claim_next_planning_item() is not None

    result = cancel_incident(
        paths,
        incident_id="incident-bad-intake",
        reason="incident was caused by bad intake",
        actor="operator",
        now=NOW,
    )

    assert result.action == "cancel_incident"
    assert result.source_state == "active"
    assert result.destination_state == "cancelled"
    assert result.destination_path.parent == paths.incidents_active_dir / "cancelled"
    assert result.destination_path.is_file()
    assert not (paths.incidents_active_dir / "incident-bad-intake.md").exists()
    events = read_runtime_events(paths)
    assert events[-1].event_type == "incident_cancelled"


def test_archive_invalid_incident_artifact_rejects_path_traversal(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    invalid = paths.incidents_incoming_dir / "INC-bad.md.invalid"
    invalid.write_text("not a valid incident", encoding="utf-8")

    result = archive_invalid_incident_artifact(
        paths,
        filename="INC-bad.md.invalid",
        reason="bad generated incident artifact",
        actor="operator",
        now=NOW,
    )

    assert result.action == "archive_invalid_incident"
    assert result.destination_path.parent == paths.incidents_incoming_dir / "invalid-archived"
    assert result.destination_path.is_file()
    assert not invalid.exists()

    with pytest.raises(ValueError, match="single relative filename"):
        archive_invalid_incident_artifact(
            paths,
            filename="../escape.invalid",
            reason="bad generated incident artifact",
            actor="operator",
            now=NOW,
        )
