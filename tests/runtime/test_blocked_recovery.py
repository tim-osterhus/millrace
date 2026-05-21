from __future__ import annotations

import json
from datetime import datetime, timezone

from millrace_ai.contracts import (
    BlueprintDraftDocument,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    ResultClass,
    StageResultEnvelope,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.runtime.blocked_recovery import retry_blocked_task, write_blocked_item_metadata
from millrace_ai.state_store import load_snapshot
from millrace_ai.workspace.blueprint_state import enqueue_blueprint_draft

NOW = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)


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
        work_item_kind=WorkItemKind.BLUEPRINT_DRAFT,
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
    assert payload["work_item_kind"] == "blueprint_draft"
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
