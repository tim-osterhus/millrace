from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from millrace_ai.contracts import (
    IncidentDecision,
    IncidentDocument,
    Plane,
    ProbeDocument,
    SpecDocument,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.errors import QueueStateError
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.work_documents import render_work_document
from millrace_ai.workspace.work_item_adapters import (
    adapter_for_family_id,
    adapter_for_kind,
    builtin_work_item_adapters,
    enqueue_with_adapter,
    move_active_with_adapter,
    parse_with_adapter,
)

NOW = datetime(2026, 5, 19, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _task_doc(task_id: str, *, created_at: datetime = NOW) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        target_paths=("src/millrace_ai/workspace/",),
        acceptance=("Adapter parity holds.",),
        required_checks=("pytest tests/workspace/test_work_item_adapters.py -q",),
        references=("lab/tasks/queue/2026-05-19-v020-08-generic-queue-adapters.md",),
        risk=("Queue behavior drift.",),
        created_at=created_at,
        created_by="tests",
    )


def _spec_doc(spec_id: str, *, created_at: datetime = NOW) -> SpecDocument:
    return SpecDocument(
        spec_id=spec_id,
        title=f"Spec {spec_id}",
        summary="Adapter parity spec.",
        source_type="manual",
        goals=("Test adapter registry.",),
        constraints=("Stay deterministic.",),
        acceptance=("Adapter parses spec.",),
        references=("tests/workspace/test_work_item_adapters.py",),
        created_at=created_at,
        created_by="tests",
    )


def _probe_doc(probe_id: str, *, created_at: datetime = NOW) -> ProbeDocument:
    return ProbeDocument(
        probe_id=probe_id,
        title=f"Probe {probe_id}",
        summary="Adapter parity probe.",
        request="Research the current code.",
        created_at=created_at,
        created_by="tests",
    )


def _incident_doc(incident_id: str, *, opened_at: datetime = NOW) -> IncidentDocument:
    return IncidentDocument(
        incident_id=incident_id,
        title=f"Incident {incident_id}",
        summary="Adapter parity incident.",
        source_stage="consultant",
        source_plane=Plane.EXECUTION,
        failure_class="test_failure",
        trigger_reason="adapter parity",
        consultant_decision=IncidentDecision.NEEDS_PLANNING,
        opened_at=opened_at,
        opened_by="tests",
    )


def test_builtin_adapter_registry_covers_current_work_item_families() -> None:
    adapters = builtin_work_item_adapters()

    assert [adapter.family_id for adapter in adapters] == [
        "task",
        "probe",
        "spec",
        "incident",
        "learning_request",
    ]
    assert adapter_for_kind(WorkItemKind.TASK).id_attr == "task_id"
    assert adapter_for_family_id("incident").timestamp_attr == "opened_at"


@pytest.mark.parametrize(
    ("kind", "document", "expected_id"),
    (
        (WorkItemKind.TASK, _task_doc("task-001"), "task-001"),
        (WorkItemKind.SPEC, _spec_doc("spec-001"), "spec-001"),
        (WorkItemKind.PROBE, _probe_doc("probe-001"), "probe-001"),
        (WorkItemKind.INCIDENT, _incident_doc("incident-001"), "incident-001"),
    ),
)
def test_adapter_parse_matches_typed_work_document_parsing(
    kind: WorkItemKind,
    document: TaskDocument | SpecDocument | ProbeDocument | IncidentDocument,
    expected_id: str,
) -> None:
    adapter = adapter_for_kind(kind)
    parsed = parse_with_adapter(adapter, render_work_document(document), path=Path(f"{expected_id}.md"))

    assert adapter.item_id(parsed) == expected_id
    assert adapter.timestamp(parsed) == NOW


def test_generic_enqueue_and_lifecycle_match_public_queue_wrappers(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    adapter = adapter_for_kind(WorkItemKind.TASK)

    generic_path = enqueue_with_adapter(paths, adapter, _task_doc("task-generic"))
    wrapper_path = QueueStore(paths).enqueue_task(_task_doc("task-wrapper"))

    assert generic_path == paths.tasks_queue_dir / "task-generic.md"
    assert wrapper_path == paths.tasks_queue_dir / "task-wrapper.md"

    assert QueueStore(paths).claim_next_execution_task() is not None
    done_path = move_active_with_adapter(paths, adapter, "task-generic", target_state="done")
    assert done_path == paths.tasks_done_dir / "task-generic.md"


def test_adapter_rejects_filename_id_mismatch_before_enqueue(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    adapter = adapter_for_kind(WorkItemKind.TASK)
    existing = paths.tasks_queue_dir / "task-other.md"
    existing.write_text(render_work_document(_task_doc("task-001")), encoding="utf-8")

    with pytest.raises(QueueStateError, match="filename stem does not match task_id"):
        adapter.validate_filename(existing)
