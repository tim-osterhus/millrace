from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import millrace_ai.runtime.blocked_recovery as blocked_recovery_module
from millrace_ai.architecture import WorkItemDocumentAdapterDefinition, WorkItemFamilyDefinition
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    ExecutionStageName,
    IncidentDecision,
    IncidentDocument,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    ResultClass,
    SpecDocument,
    StageResultEnvelope,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.contracts.router import RouterAction, RouterDecision
from millrace_ai.errors import QueueStateError
from millrace_ai.events import read_runtime_events
from millrace_ai.extensions.builtin.blueprint.contracts import BlueprintDraftDocument
from millrace_ai.extensions.builtin.blueprint.state import enqueue_blueprint_draft
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runtime.blocked_recovery import (
    retry_blocked_task,
    retry_blocked_work_item,
    write_blocked_item_metadata,
)
from millrace_ai.runtime.recovery.environmental import (
    auto_retryable_scope,
    blocked_origin_from_metadata,
    failure_scope_from_metadata,
)
from millrace_ai.state_store import load_snapshot

NOW = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("metadata", "expected_origin", "expected_scope"),
    (
        ({}, "stage_terminal", "semantic"),
        ({"normalization_source": "failure"}, "runner_failure", "unknown"),
        ({"blocked_origin": "runtime_exception"}, "runtime_exception", "unknown"),
        (
            {"blocked_origin": "runner_failure", "failure_scope": "provider"},
            "runner_failure",
            "provider",
        ),
        (
            {"blocked_origin": "stage_terminal", "failure_scope": "contract"},
            "stage_terminal",
            "contract",
        ),
    ),
)
def test_environmental_classification_is_table_driven(
    metadata: dict[str, object],
    expected_origin: str,
    expected_scope: str,
) -> None:
    origin = blocked_origin_from_metadata(metadata)
    scope = failure_scope_from_metadata(metadata, blocked_origin=origin)
    assert origin == expected_origin
    assert scope == expected_scope


@pytest.mark.parametrize(
    ("scope", "expected"),
    (
        ("environment", True),
        ("provider", True),
        ("local_configuration", True),
        ("unknown", True),
        ("contract", False),
        ("semantic", False),
    ),
)
def test_environmental_retry_scope_matrix(scope: str, expected: bool) -> None:
    assert auto_retryable_scope(scope) is expected  # type: ignore[arg-type]


def test_blocked_metadata_accepts_custom_family_without_work_item_kind(tmp_path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    stage_result = StageResultEnvelope(
        run_id="run-custom-review",
        plane=Plane.PLANNING,
        stage=PlanningStageName.PLANNER,
        work_item_family_id="custom_review",
        work_item_id="custom-001",
        terminal_result=PlanningTerminalResult.BLOCKED,
        result_class=ResultClass.BLOCKED,
        summary_status_marker="### BLOCKED",
        success=False,
        started_at=NOW,
        completed_at=NOW,
        metadata={"failure_class": "custom_blocked"},
    )
    decision = RouterDecision(
        action=RouterAction.BLOCKED,
        next_plane=None,
        next_stage=None,
        reason="custom blocked",
        failure_class="custom_blocked",
    )

    metadata_path = write_blocked_item_metadata(
        paths,
        stage_result=stage_result,
        decision=decision,
        now=NOW,
    )

    assert metadata_path == paths.runtime_root / "diagnostics/blocked/custom_review-custom-001.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert payload["work_item_family_id"] == "custom_review"
    assert payload["work_item_kind"] is None
    assert payload["work_item_id"] == "custom-001"


def test_blocked_metadata_blueprint_draft_includes_root_lineage(tmp_path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    draft = _blueprint_draft("draft-001").model_copy(update={"status": "blocked"})
    draft_path = paths.runtime_root / "blueprints" / "drafts" / "blocked" / "draft-001.json"
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(draft.model_dump_json(indent=2) + "\n", encoding="utf-8")
    stage_result = StageResultEnvelope(
        run_id="run-blueprint-draft",
        plane=Plane.PLANNING,
        stage=PlanningStageName.MANAGER,
        work_item_family_id="blueprint_draft",
        work_item_id="draft-001",
        terminal_result=PlanningTerminalResult.BLOCKED,
        result_class=ResultClass.BLOCKED,
        summary_status_marker="### BLOCKED",
        success=False,
        started_at=NOW,
        completed_at=NOW,
        metadata={"failure_class": "blueprint_blocked"},
    )
    decision = RouterDecision(
        action=RouterAction.BLOCKED,
        next_plane=None,
        next_stage=None,
        reason="blueprint blocked",
        failure_class="blueprint_blocked",
    )

    metadata_path = write_blocked_item_metadata(
        paths,
        stage_result=stage_result,
        decision=decision,
        now=NOW,
    )

    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert payload["work_item_family_id"] == "blueprint_draft"
    assert payload["work_item_kind"] is None
    assert payload["work_item_id"] == "draft-001"
    assert payload["root_spec_id"] == "spec-001"
    assert payload["root_idea_id"] == "idea-001"


def test_retry_blocked_task_refreshes_inventory_with_compiled_plan(tmp_path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    _stage_retry_fixture(paths)
    enqueue_blueprint_draft(paths, _blueprint_draft("draft-001"))

    retry_blocked_task(
        paths,
        task_id="task-001",
        reason="operator retry",
        actor="tests",
        auto=False,
        force=True,
    )

    snapshot = load_snapshot(paths)
    assert snapshot.queue_depth_execution == 1
    assert snapshot.queue_depth_planning == 1
    assert snapshot.queue_depths_by_plane[Plane.EXECUTION] == 1
    assert snapshot.queue_depths_by_plane[Plane.PLANNING] == 1


def test_retry_blocked_task_refreshes_inventory_without_compiled_plan(tmp_path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    compiled_plan_path = paths.state_dir / "compiled_plan.json"
    if compiled_plan_path.exists():
        compiled_plan_path.unlink()
    _stage_retry_fixture(paths)
    enqueue_blueprint_draft(paths, _blueprint_draft("draft-001"))

    retry_blocked_task(
        paths,
        task_id="task-001",
        reason="operator retry",
        actor="tests",
        auto=False,
        force=True,
    )

    snapshot = load_snapshot(paths)
    assert snapshot.queue_depth_execution == 1
    assert snapshot.queue_depth_planning == 1
    assert snapshot.queue_depths_by_plane[Plane.EXECUTION] == 1
    assert snapshot.queue_depths_by_plane[Plane.PLANNING] == 1


def test_retry_blocked_work_item_requeues_blocked_spec_and_writes_family_audit(tmp_path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec("spec-retry"))
    assert queue.claim_next_planning_item() is not None
    queue.mark_spec_blocked("spec-retry")
    _write_blocked_metadata(
        paths,
        family_id="spec",
        work_item_id="spec-retry",
        failure_class="provider_unavailable",
        auto_requeue_candidate=True,
    )

    result = retry_blocked_work_item(
        paths,
        work_item_family_id="spec",
        work_item_id="spec-retry",
        reason="operator retry after provider recovery",
        actor="tests",
        auto=False,
        root_spec_id="spec-root-001",
    )

    assert result.work_item_family_id == "spec"
    assert result.work_item_kind is WorkItemKind.SPEC
    assert result.work_item_id == "spec-retry"
    assert result.source_state == "blocked"
    assert result.destination_state == "queue"
    assert result.failure_class == "provider_unavailable"
    assert result.attempt_number == 1
    assert (paths.specs_queue_dir / "spec-retry.md").is_file()
    assert not (paths.specs_blocked_dir / "spec-retry.md").exists()
    audit_lines = [
        json.loads(line)
        for line in (paths.specs_queue_dir / "spec-retry.requeue.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert audit_lines[0]["work_item_family_id"] == "spec"
    assert audit_lines[0]["work_item_id"] == "spec-retry"
    assert audit_lines[0]["attempt_number"] == 1
    assert read_runtime_events(paths)[-1].event_type == "blocked_work_item_requeued"
    assert read_runtime_events(paths)[-1].data["work_item_family_id"] == "spec"


def test_retry_blocked_work_item_refuses_malformed_blocked_document(tmp_path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    malformed_path = paths.specs_blocked_dir / "spec-malformed.md"
    malformed_path.write_text("# Broken spec\nSpec-ID: spec-malformed\n", encoding="utf-8")

    with pytest.raises(QueueStateError, match="blocked spec spec-malformed is invalid"):
        retry_blocked_work_item(
            paths,
            work_item_family_id="spec",
            work_item_id="spec-malformed",
            reason="operator retry",
            actor="tests",
            auto=False,
            force=True,
        )

    assert malformed_path.is_file()
    assert not (paths.specs_queue_dir / "spec-malformed.md").exists()


def test_retry_blocked_work_item_refuses_ambiguous_family_without_selector(tmp_path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    queue = QueueStore(paths)
    queue.enqueue_task(_task("work-retry"))
    assert queue.claim_next_execution_task() is not None
    queue.mark_task_blocked("work-retry")
    queue.enqueue_spec(_spec("work-retry"))
    assert queue.claim_next_planning_item() is not None
    queue.mark_spec_blocked("work-retry")

    with pytest.raises(QueueStateError, match="blocked work item id is ambiguous"):
        retry_blocked_work_item(
            paths,
            work_item_id="work-retry",
            reason="operator retry",
            actor="tests",
            auto=False,
            force=True,
        )

    assert (paths.tasks_blocked_dir / "work-retry.md").is_file()
    assert (paths.specs_blocked_dir / "work-retry.md").is_file()


def test_retry_blocked_work_item_enforces_root_spec_guard(tmp_path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    queue = QueueStore(paths)
    queue.enqueue_incident(_incident("incident-retry"))
    assert queue.claim_next_planning_item() is not None
    queue.mark_incident_blocked("incident-retry")

    with pytest.raises(QueueStateError, match="does not belong to root spec spec-other"):
        retry_blocked_work_item(
            paths,
            work_item_family_id="incident",
            work_item_id="incident-retry",
            reason="operator retry",
            actor="tests",
            auto=False,
            force=True,
            root_spec_id="spec-other",
        )

    assert (paths.incidents_blocked_dir / "incident-retry.md").is_file()
    assert not (paths.incidents_incoming_dir / "incident-retry.md").exists()


def test_retry_blocked_work_item_requeues_blueprint_draft_when_family_parser_validates(tmp_path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    draft = _blueprint_draft("draft-retry").model_copy(update={"status": "blocked"})
    blocked_path = paths.runtime_root / "blueprints" / "drafts" / "blocked" / "draft-retry.json"
    blocked_path.parent.mkdir(parents=True, exist_ok=True)
    blocked_path.write_text(draft.model_dump_json(indent=2) + "\n", encoding="utf-8")

    result = retry_blocked_work_item(
        paths,
        work_item_family_id="blueprint_draft",
        work_item_id="draft-retry",
        reason="operator retry after blueprint repair",
        actor="tests",
        auto=False,
        force=True,
        root_spec_id="spec-001",
    )

    queued_path = paths.runtime_root / "blueprints" / "drafts" / "queue" / "draft-retry.json"
    assert result.work_item_family_id == "blueprint_draft"
    assert result.work_item_kind is None
    assert queued_path.is_file()
    assert not blocked_path.exists()
    audit_lines = [
        json.loads(line)
        for line in (queued_path.parent / "draft-retry.requeue.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert audit_lines[0]["work_item_family_id"] == "blueprint_draft"
    assert audit_lines[0]["work_item_id"] == "draft-retry"


def test_retry_blocked_work_item_refuses_unsupported_family(tmp_path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))

    with pytest.raises(QueueStateError, match="blocked retry for family custom_review is not supported"):
        retry_blocked_work_item(
            paths,
            work_item_family_id="custom_review",
            work_item_id="custom-001",
            reason="operator retry",
            actor="tests",
            auto=False,
            force=True,
        )


def test_blocked_metadata_custom_family_reads_lineage_from_adapter_active_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    family = _custom_retry_family()
    _persist_custom_retry_family(paths, family)

    active_path = paths.runtime_root / "custom" / "reviews" / "adapter-active" / "custom-001.json"
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text(
        json.dumps(
            {
                "custom_id": "custom-001",
                "root_spec_id": "spec-001",
                "root_idea_id": "idea-001",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    class _Adapter:
        def active_path(self, paths, *, work_item_id: str):
            assert work_item_id == "custom-001"
            return active_path

    monkeypatch.setattr(
        blocked_recovery_module,
        "queue_adapter_for_id",
        lambda adapter_id: _Adapter() if adapter_id == family.queue_lifecycle_adapter_id else None,
    )

    stage_result = StageResultEnvelope(
        run_id="run-custom-lineage",
        plane=Plane.PLANNING,
        stage=PlanningStageName.PLANNER,
        work_item_family_id="custom_review",
        work_item_id="custom-001",
        terminal_result=PlanningTerminalResult.BLOCKED,
        result_class=ResultClass.BLOCKED,
        summary_status_marker="### BLOCKED",
        success=False,
        started_at=NOW,
        completed_at=NOW,
        metadata={"failure_class": "custom_blocked"},
    )
    decision = RouterDecision(
        action=RouterAction.BLOCKED,
        next_plane=None,
        next_stage=None,
        reason="custom blocked",
        failure_class="custom_blocked",
    )

    metadata_path = write_blocked_item_metadata(
        paths,
        stage_result=stage_result,
        decision=decision,
        now=NOW,
    )
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert payload["work_item_family_id"] == "custom_review"
    assert payload["root_spec_id"] == "spec-001"
    assert payload["root_idea_id"] == "idea-001"


def test_retry_blocked_work_item_requeues_custom_family_via_adapter_family_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    family = _custom_retry_family()
    _persist_custom_retry_family(paths, family)
    blocked_path = paths.runtime_root / family.queue_dirs.blocked / "custom-001.json"
    blocked_path.parent.mkdir(parents=True, exist_ok=True)
    blocked_path.write_text(
        json.dumps(
            {
                "custom_id": "custom-001",
                "root_spec_id": "spec-001",
                "root_idea_id": "idea-001",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    adapter_ids: list[str] = []

    class _Adapter:
        def active_path(self, paths, *, work_item_id: str):
            return paths.runtime_root / "custom" / "reviews" / "adapter-active" / f"{work_item_id}.json"

    def fake_queue_adapter_for_id(adapter_id: str):
        adapter_ids.append(adapter_id)
        if adapter_id == family.queue_lifecycle_adapter_id:
            return _Adapter()
        return None

    monkeypatch.setattr(blocked_recovery_module, "queue_adapter_for_id", fake_queue_adapter_for_id)

    result = retry_blocked_work_item(
        paths,
        work_item_family_id="custom_review",
        work_item_id="custom-001",
        reason="operator retry custom review",
        actor="tests",
        auto=False,
        force=True,
        root_spec_id="spec-001",
    )

    queued_path = paths.runtime_root / family.queue_dirs.queue / "custom-001.json"
    assert result.work_item_family_id == "custom_review"
    assert result.work_item_kind is None
    assert queued_path.is_file()
    assert not blocked_path.exists()
    assert "tests.custom.review.adapter" in adapter_ids
    audit_lines = [
        json.loads(line)
        for line in (queued_path.parent / "custom-001.requeue.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert audit_lines[0]["work_item_family_id"] == "custom_review"
    assert audit_lines[0]["work_item_id"] == "custom-001"


def _stage_retry_fixture(paths) -> None:
    queue = QueueStore(paths)
    queue.enqueue_task(_task("task-001"))
    assert queue.claim_next_execution_task() is not None
    queue.mark_task_blocked("task-001")


def _task(task_id: str) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="blocked recovery fixture",
        root_spec_id="spec-001",
        root_idea_id="idea-001",
        spec_id="spec-001",
        target_paths=("src/millrace_ai/runtime/blocked_recovery.py",),
        acceptance=("blocked task can be retried",),
        required_checks=("pytest tests/runtime/test_blocked_recovery.py -q",),
        references=("lab/tasks/queue/remediation-05.md",),
        risk=("stale queue depths",),
        created_at=NOW,
        created_by="tests",
    )


def _spec(spec_id: str) -> SpecDocument:
    return SpecDocument(
        spec_id=spec_id,
        title=f"Spec {spec_id}",
        summary="blocked recovery spec fixture",
        source_type="manual",
        root_idea_id="idea-001",
        root_spec_id="spec-root-001",
        goals=("Retry blocked spec.",),
        constraints=("Preserve queue validation.",),
        acceptance=("blocked spec can be retried",),
        references=("lab/specs/blocked-recovery.md",),
        created_at=NOW,
        created_by="tests",
    )


def _incident(incident_id: str) -> IncidentDocument:
    return IncidentDocument(
        incident_id=incident_id,
        title=f"Incident {incident_id}",
        summary="blocked recovery incident fixture",
        root_idea_id="idea-001",
        root_spec_id="spec-root-001",
        source_stage=ExecutionStageName.CONSULTANT,
        source_plane=Plane.EXECUTION,
        failure_class="provider_unavailable",
        trigger_reason="exercise retry root guard",
        consultant_decision=IncidentDecision.NEEDS_PLANNING,
        opened_at=NOW,
        opened_by="tests",
    )


def _write_blocked_metadata(
    paths,
    *,
    family_id: str,
    work_item_id: str,
    failure_class: str,
    auto_requeue_candidate: bool,
) -> None:
    metadata_dir = paths.runtime_root / "diagnostics" / "blocked"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (metadata_dir / f"{family_id}-{work_item_id}.json").write_text(
        json.dumps(
            {
                "work_item_family_id": family_id,
                "work_item_id": work_item_id,
                "blocked_at": NOW.isoformat(),
                "blocked_origin": "runner_failure",
                "failure_class": failure_class,
                "failure_scope": "provider",
                "auto_requeue_candidate": auto_requeue_candidate,
                "source_run_id": "run-001",
                "source_plane": "planning",
                "source_stage": "planner",
                "terminal_result": "BLOCKED",
            }
        ),
        encoding="utf-8",
    )


def _blueprint_draft(draft_id: str) -> BlueprintDraftDocument:
    return BlueprintDraftDocument(
        draft_id=draft_id,
        manifest_id="manifest-001",
        root_spec_id="spec-001",
        root_idea_id="idea-001",
        source_spec_id="spec-001",
        draft_index=1,
        title=f"Draft {draft_id}",
        summary="Blueprint draft lineage fixture.",
        scope=("src/millrace_ai/runtime/blocked_recovery.py",),
        target_paths=("src/millrace_ai/runtime/blocked_recovery.py",),
        acceptance_intent=("Root lineage is visible in blocked diagnostics.",),
        verification_intent=("pytest tests/runtime/test_blocked_recovery.py -q",),
        context_excerpt="Blocked diagnostics need root lineage.",
        current_revision=0,
        status="queued",
        references=("lab/tasks/queue/remediation-05.md",),
        created_at=NOW,
    )


def _custom_retry_family() -> WorkItemFamilyDefinition:
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
        queue_lifecycle_adapter_id="tests.custom.review.adapter",
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
        operator_capabilities=("retry", "cancel", "inspect"),
    )


def _custom_retry_document_adapter() -> WorkItemDocumentAdapterDefinition:
    return WorkItemDocumentAdapterDefinition(
        adapter_id="custom_review_json_v1",
        schema_id="custom_review_document_v1",
        supported_file_extensions=(".json",),
        family_ids=("custom_review",),
        can_parse=True,
        can_render=True,
        can_summarize=True,
        supports_dependencies=False,
        supports_lineage=True,
    )


def _persist_custom_retry_family(paths, family: WorkItemFamilyDefinition) -> None:
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.active_plan is not None
    adapter = _custom_retry_document_adapter()
    updated = outcome.active_plan.model_copy(
        update={
            "work_item_families_by_id": {
                **outcome.active_plan.work_item_families_by_id,
                family.family_id: family,
            },
            "document_adapters_by_id": {
                **outcome.active_plan.document_adapters_by_id,
                adapter.adapter_id: adapter,
            },
        }
    )
    (paths.state_dir / "compiled_plan.json").write_text(
        updated.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
