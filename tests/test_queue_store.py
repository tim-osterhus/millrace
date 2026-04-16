from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from millrace_ai.contracts import IncidentDocument, SpecDocument, TaskDocument, WorkItemKind
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.work_documents import parse_work_document, render_work_document

NOW = datetime(2026, 4, 15, tzinfo=timezone.utc)


def _task_doc(task_id: str, *, created_at: datetime) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="queue test",
        target_paths=["millrace/queue_store.py"],
        acceptance=["queue behavior is deterministic"],
        required_checks=["uv run pytest tests/test_queue_store.py -q"],
        references=["lab/specs/drafts/millrace-work-item-queue-and-ownership-contract.md"],
        risk=["queue drift"],
        created_at=created_at,
        created_by="tests",
    )


def _spec_doc(spec_id: str, *, created_at: datetime) -> SpecDocument:
    return SpecDocument(
        spec_id=spec_id,
        title=f"Spec {spec_id}",
        summary="planning input",
        source_type="manual",
        goals=["define implementation plan"],
        constraints=["stay deterministic"],
        acceptance=["planning queue works"],
        references=["lab/specs/drafts/millrace-work-item-queue-and-ownership-contract.md"],
        created_at=created_at,
        created_by="tests",
    )


def _incident_doc(incident_id: str, *, opened_at: datetime) -> IncidentDocument:
    return IncidentDocument(
        incident_id=incident_id,
        title=f"Incident {incident_id}",
        summary="execution recovery",
        source_stage="consultant",
        source_plane="execution",
        failure_class="malformed_output",
        trigger_reason="bad terminal marker",
        consultant_decision="needs_planning",
        opened_at=opened_at,
        opened_by="tests",
    )


def _read_json_lines(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _task_markdown(payload: dict[str, object]) -> str:
    lines = [
        "# Invalid task",
        f"Task-ID: {payload.get('task_id', 'task-invalid')}",
        f"Title: {payload.get('title', 'Invalid task')}",
    ]
    if "created_at" in payload:
        lines.append(f"Created-At: {payload['created_at']}")
    if "created_by" in payload:
        lines.append(f"Created-By: {payload['created_by']}")
    return "\n".join(lines) + "\n"


def _spec_markdown(payload: dict[str, object]) -> str:
    lines = [
        "# Invalid spec",
        f"Spec-ID: {payload.get('spec_id', 'spec-invalid')}",
        f"Title: {payload.get('title', 'Invalid spec')}",
    ]
    if "created_at" in payload:
        lines.append(f"Created-At: {payload['created_at']}")
    if "created_by" in payload:
        lines.append(f"Created-By: {payload['created_by']}")
    return "\n".join(lines) + "\n"


def test_work_documents_round_trip_for_task_spec_and_incident() -> None:
    documents: tuple[TaskDocument | SpecDocument | IncidentDocument, ...] = (
        _task_doc("task-roundtrip", created_at=NOW),
        _spec_doc("spec-roundtrip", created_at=NOW),
        _incident_doc("inc-roundtrip", opened_at=NOW),
    )

    for document in documents:
        raw = render_work_document(document)
        assert raw.startswith(f"# {document.title}\n")
        assert "---" not in raw
        parsed = parse_work_document(raw, path=Path(f"{document.kind}.md"))
        assert parsed == document


def test_task_lifecycle_claim_done_blocked_is_deterministic(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)

    store.enqueue_task(_task_doc("task-002", created_at=NOW + timedelta(seconds=30)))
    store.enqueue_task(_task_doc("task-001", created_at=NOW))

    first = store.claim_next_execution_task()
    assert first is not None
    assert first.work_item_kind is WorkItemKind.TASK
    assert first.work_item_id == "task-001"
    assert first.path == paths.tasks_active_dir / "task-001.md"

    store.mark_task_done("task-001")
    assert (paths.tasks_done_dir / "task-001.md").is_file()
    assert not (paths.tasks_active_dir / "task-001.md").exists()

    second = store.claim_next_execution_task()
    assert second is not None
    assert second.work_item_id == "task-002"

    store.mark_task_blocked("task-002")
    assert (paths.tasks_blocked_dir / "task-002.md").is_file()


def test_planning_lifecycle_incidents_then_specs_with_done_and_blocked(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)

    store.enqueue_spec(_spec_doc("spec-001", created_at=NOW + timedelta(minutes=5)))
    store.enqueue_spec(_spec_doc("spec-002", created_at=NOW + timedelta(minutes=8)))
    store.enqueue_incident(_incident_doc("inc-001", opened_at=NOW + timedelta(minutes=6)))
    store.enqueue_incident(_incident_doc("inc-002", opened_at=NOW + timedelta(minutes=7)))

    first = store.claim_next_planning_item()
    assert first is not None
    assert first.work_item_kind is WorkItemKind.INCIDENT
    assert first.work_item_id == "inc-001"
    store.mark_incident_resolved("inc-001")
    assert (paths.incidents_resolved_dir / "inc-001.md").is_file()

    second = store.claim_next_planning_item()
    assert second is not None
    assert second.work_item_kind is WorkItemKind.INCIDENT
    assert second.work_item_id == "inc-002"
    store.mark_incident_blocked("inc-002")
    assert (paths.incidents_blocked_dir / "inc-002.md").is_file()

    third = store.claim_next_planning_item()
    assert third is not None
    assert third.work_item_kind is WorkItemKind.SPEC
    assert third.work_item_id == "spec-001"
    store.mark_spec_done("spec-001")
    assert (paths.specs_done_dir / "spec-001.md").is_file()

    fourth = store.claim_next_planning_item()
    assert fourth is not None
    assert fourth.work_item_kind is WorkItemKind.SPEC
    assert fourth.work_item_id == "spec-002"
    store.mark_spec_blocked("spec-002")
    assert (paths.specs_blocked_dir / "spec-002.md").is_file()


def test_requeue_is_deterministic_and_records_reasons(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)

    store.enqueue_task(_task_doc("task-001", created_at=NOW))
    claim = store.claim_next_execution_task()
    assert claim is not None
    store.requeue_task("task-001", reason="retry after consultant guidance")

    assert (paths.tasks_queue_dir / "task-001.md").is_file()
    first_reason_log = _read_json_lines(paths.tasks_queue_dir / "task-001.requeue.jsonl")
    assert [entry["reason"] for entry in first_reason_log] == ["retry after consultant guidance"]

    claim_again = store.claim_next_execution_task()
    assert claim_again is not None
    assert claim_again.work_item_id == "task-001"
    store.requeue_task("task-001", reason="operator requested rerun")

    reason_log = _read_json_lines(paths.tasks_queue_dir / "task-001.requeue.jsonl")
    assert [entry["reason"] for entry in reason_log] == [
        "retry after consultant guidance",
        "operator requested rerun",
    ]


def test_requeue_spec_and_incident_return_to_queue_surfaces(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)

    store.enqueue_spec(_spec_doc("spec-001", created_at=NOW))
    spec_claim = store.claim_next_planning_item()
    assert spec_claim is not None
    assert spec_claim.work_item_kind is WorkItemKind.SPEC
    store.requeue_spec("spec-001", reason="manager requested updates")
    assert (paths.specs_queue_dir / "spec-001.md").is_file()

    store.enqueue_incident(_incident_doc("inc-001", opened_at=NOW + timedelta(seconds=1)))
    incident_claim = store.claim_next_planning_item()
    assert incident_claim is not None
    assert incident_claim.work_item_kind is WorkItemKind.INCIDENT
    store.requeue_incident("inc-001", reason="mechanic needs another pass")
    assert (paths.incidents_incoming_dir / "inc-001.md").is_file()


def test_duplicate_id_is_rejected_across_all_known_task_states(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)

    store.enqueue_task(_task_doc("task-001", created_at=NOW))
    with pytest.raises(ValueError, match="already exists"):
        store.enqueue_task(_task_doc("task-001", created_at=NOW + timedelta(seconds=1)))

    claim = store.claim_next_execution_task()
    assert claim is not None
    store.mark_task_done("task-001")

    with pytest.raises(ValueError, match="already exists"):
        store.enqueue_task(_task_doc("task-001", created_at=NOW + timedelta(seconds=2)))


def test_detect_execution_stale_state_conditions(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)

    store.enqueue_task(_task_doc("task-001", created_at=NOW))
    store.enqueue_task(_task_doc("task-002", created_at=NOW + timedelta(seconds=1)))

    claim = store.claim_next_execution_task()
    assert claim is not None

    stale_no_snapshot = store.detect_execution_stale_state(snapshot_active_task_id=None)
    assert stale_no_snapshot.is_stale is True
    assert "active_without_snapshot" in stale_no_snapshot.reasons

    stale_snapshot_mismatch = store.detect_execution_stale_state(snapshot_active_task_id="task-999")
    assert stale_snapshot_mismatch.is_stale is True
    assert "snapshot_active_id_mismatch" in stale_snapshot_mismatch.reasons

    stale_snapshot_queue = store.detect_execution_stale_state(snapshot_active_task_id="task-002")
    assert stale_snapshot_queue.is_stale is True
    assert "snapshot_points_to_queued_item" in stale_snapshot_queue.reasons

    extra_active = render_work_document(_task_doc("task-777", created_at=NOW + timedelta(seconds=2)))
    (paths.tasks_active_dir / "task-777.md").write_text(extra_active, encoding="utf-8")

    stale_multiple_active = store.detect_execution_stale_state(snapshot_active_task_id="task-001")
    assert stale_multiple_active.is_stale is True
    assert "multiple_active_items" in stale_multiple_active.reasons


def test_detect_planning_stale_state_conditions(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)

    store.enqueue_spec(_spec_doc("spec-001", created_at=NOW))
    store.enqueue_spec(_spec_doc("spec-002", created_at=NOW + timedelta(seconds=1)))

    first = store.claim_next_planning_item()
    assert first is not None
    assert first.work_item_kind is WorkItemKind.SPEC

    stale_no_snapshot = store.detect_planning_stale_state(
        snapshot_active_kind=None,
        snapshot_active_item_id=None,
    )
    assert stale_no_snapshot.is_stale is True
    assert "active_without_snapshot" in stale_no_snapshot.reasons

    stale_snapshot_queue = store.detect_planning_stale_state(
        snapshot_active_kind=WorkItemKind.SPEC,
        snapshot_active_item_id="spec-002",
    )
    assert stale_snapshot_queue.is_stale is True
    assert "snapshot_points_to_queued_item" in stale_snapshot_queue.reasons

    stale_snapshot_mismatch = store.detect_planning_stale_state(
        snapshot_active_kind=WorkItemKind.SPEC,
        snapshot_active_item_id="spec-999",
    )
    assert stale_snapshot_mismatch.is_stale is True
    assert "snapshot_active_id_mismatch" in stale_snapshot_mismatch.reasons

    extra_incident = render_work_document(_incident_doc("inc-001", opened_at=NOW + timedelta(seconds=2)))
    (paths.incidents_active_dir / "inc-001.md").write_text(extra_incident, encoding="utf-8")

    stale_multiple_active = store.detect_planning_stale_state(
        snapshot_active_kind=WorkItemKind.SPEC,
        snapshot_active_item_id="spec-001",
    )
    assert stale_multiple_active.is_stale is True
    assert "multiple_active_items" in stale_multiple_active.reasons


def test_claim_next_execution_task_skips_malformed_markdown_and_claims_valid_work(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)

    (paths.tasks_queue_dir / "broken.md").write_text("# Broken\nnot a valid task document\n", encoding="utf-8")
    store.enqueue_task(_task_doc("task-001", created_at=NOW))

    claim = store.claim_next_execution_task()
    assert claim is not None
    assert claim.work_item_kind is WorkItemKind.TASK
    assert claim.work_item_id == "task-001"
    assert (paths.tasks_queue_dir / "broken.md").exists() is False
    assert (paths.tasks_queue_dir / "broken.md.invalid").is_file()
    invalid_log = _read_json_lines(paths.tasks_queue_dir / "invalid-artifacts.jsonl")
    assert invalid_log
    assert invalid_log[0]["source_name"] == "broken.md"


def test_claim_next_execution_task_skips_schema_invalid_task_and_claims_valid_work(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)

    invalid_payload = {
        "task_id": "task-invalid",
        "title": "Invalid task",
        "created_at": NOW.isoformat(),
        "created_by": "tests",
    }
    (paths.tasks_queue_dir / "task-invalid.md").write_text(
        _task_markdown(invalid_payload),
        encoding="utf-8",
    )
    store.enqueue_task(_task_doc("task-001", created_at=NOW + timedelta(seconds=1)))

    claim = store.claim_next_execution_task()
    assert claim is not None
    assert claim.work_item_kind is WorkItemKind.TASK
    assert claim.work_item_id == "task-001"
    assert (paths.tasks_queue_dir / "task-invalid.md").exists() is False
    assert (paths.tasks_queue_dir / "task-invalid.md.invalid").is_file()


def test_claim_next_execution_task_quarantines_filename_and_frontmatter_id_mismatch(
    tmp_path: Path,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)

    mismatched = _task_doc("task-mismatch", created_at=NOW)
    (paths.tasks_queue_dir / "task-alias.md").write_text(
        render_work_document(mismatched),
        encoding="utf-8",
    )
    store.enqueue_task(_task_doc("task-001", created_at=NOW + timedelta(seconds=1)))

    claim = store.claim_next_execution_task()
    assert claim is not None
    assert claim.work_item_kind is WorkItemKind.TASK
    assert claim.work_item_id == "task-001"
    assert (paths.tasks_queue_dir / "task-alias.md").exists() is False
    assert (paths.tasks_queue_dir / "task-alias.md.invalid").is_file()
    invalid_log = _read_json_lines(paths.tasks_queue_dir / "invalid-artifacts.jsonl")
    assert any(entry["source_name"] == "task-alias.md" for entry in invalid_log)


def test_claim_next_planning_item_skips_malformed_incident_and_claims_valid_incident(
    tmp_path: Path,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)

    (paths.incidents_incoming_dir / "inc-bad.md").write_text(
        "# Broken incident\nnot a valid incident document\n",
        encoding="utf-8",
    )
    store.enqueue_incident(_incident_doc("inc-001", opened_at=NOW))

    claim = store.claim_next_planning_item()
    assert claim is not None
    assert claim.work_item_kind is WorkItemKind.INCIDENT
    assert claim.work_item_id == "inc-001"
    assert (paths.incidents_incoming_dir / "inc-bad.md.invalid").is_file()


def test_claim_next_planning_item_skips_schema_invalid_spec_and_claims_valid_spec(
    tmp_path: Path,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)

    invalid_spec_payload = {
        "spec_id": "spec-bad",
        "title": "Bad spec",
        "created_at": NOW.isoformat(),
        "created_by": "tests",
    }
    (paths.specs_queue_dir / "spec-bad.md").write_text(
        _spec_markdown(invalid_spec_payload),
        encoding="utf-8",
    )
    store.enqueue_spec(_spec_doc("spec-001", created_at=NOW + timedelta(seconds=1)))

    claim = store.claim_next_planning_item()
    assert claim is not None
    assert claim.work_item_kind is WorkItemKind.SPEC
    assert claim.work_item_id == "spec-001"
    assert (paths.specs_queue_dir / "spec-bad.md.invalid").is_file()


def test_claim_next_planning_item_quarantines_spec_filename_id_mismatch(
    tmp_path: Path,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)

    mismatched = _spec_doc("spec-mismatch", created_at=NOW)
    (paths.specs_queue_dir / "spec-alias.md").write_text(
        render_work_document(mismatched),
        encoding="utf-8",
    )
    store.enqueue_spec(_spec_doc("spec-001", created_at=NOW + timedelta(seconds=1)))

    claim = store.claim_next_planning_item()
    assert claim is not None
    assert claim.work_item_kind is WorkItemKind.SPEC
    assert claim.work_item_id == "spec-001"
    assert (paths.specs_queue_dir / "spec-alias.md").exists() is False
    assert (paths.specs_queue_dir / "spec-alias.md.invalid").is_file()


def test_claim_next_execution_task_retries_when_candidate_disappears(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)
    store.enqueue_task(_task_doc("task-001", created_at=NOW))

    original_replace = Path.replace
    attempts = {"count": 0}

    def flaky_replace(self: Path, target: Path):
        if self.name == "task-001.md" and attempts["count"] == 0:
            attempts["count"] += 1
            raise FileNotFoundError("simulated race")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    claim = store.claim_next_execution_task()
    assert claim is not None
    assert claim.work_item_id == "task-001"


def test_claim_next_planning_item_retries_when_candidate_disappears(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)
    store.enqueue_incident(_incident_doc("inc-001", opened_at=NOW))

    original_replace = Path.replace
    attempts = {"count": 0}

    def flaky_replace(self: Path, target: Path):
        if self.name == "inc-001.md" and attempts["count"] == 0:
            attempts["count"] += 1
            raise FileNotFoundError("simulated race")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    claim = store.claim_next_planning_item()
    assert claim is not None
    assert claim.work_item_kind is WorkItemKind.INCIDENT
    assert claim.work_item_id == "inc-001"


def test_claim_next_execution_task_handles_file_missing_during_candidate_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)
    store.enqueue_task(_task_doc("task-001", created_at=NOW))
    store.enqueue_task(_task_doc("task-002", created_at=NOW + timedelta(seconds=1)))

    original_read_text = Path.read_text
    seen = {"raised": False}

    def flaky_read_text(self: Path, *args, **kwargs):
        if self.name == "task-001.md" and not seen["raised"]:
            seen["raised"] = True
            raise FileNotFoundError("simulated race during scan")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    claim = store.claim_next_execution_task()
    assert claim is not None
    assert claim.work_item_id == "task-002"
