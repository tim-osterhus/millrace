from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.contracts import (
    BlueprintDraftDocument,
    BlueprintPacketDocument,
    BlueprintPromotionRecord,
    ClosureTargetState,
    TaskDocument,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.completion_behavior import maybe_activate_completion_stage
from millrace_ai.workspace.arbiter_state import load_closure_target_state, save_closure_target_state
from millrace_ai.workspace.blueprint_state import (
    approve_active_blueprint_draft,
    claim_next_blueprint_draft,
    enqueue_blueprint_draft,
    list_open_blueprint_lineage_work_ids,
    list_open_blueprint_lineage_work_refs,
    persist_blueprint_packet,
    persist_blueprint_promotion,
)

NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _unused_stage_runner(request: StageRunRequest) -> RunnerRawResult:
    raise AssertionError(f"stage runner should not be called: {request.stage.value}")


def _target_state(*, root_spec_id: str = "spec-001", root_idea_id: str = "idea-001") -> ClosureTargetState:
    return ClosureTargetState(
        root_spec_id=root_spec_id,
        root_idea_id=root_idea_id,
        root_spec_path=f"millrace-agents/arbiter/contracts/root-specs/{root_spec_id}.md",
        root_idea_path=f"millrace-agents/arbiter/contracts/ideas/{root_idea_id}.md",
        rubric_path=f"millrace-agents/arbiter/rubrics/{root_spec_id}.md",
        latest_verdict_path=None,
        latest_report_path=None,
        closure_open=True,
        closure_blocked_by_lineage_work=False,
        blocking_work_ids=(),
        opened_at=NOW,
    )


def _draft(
    draft_id: str = "draft-001",
    *,
    draft_index: int = 1,
    status: str = "queued",
) -> BlueprintDraftDocument:
    return BlueprintDraftDocument(
        draft_id=draft_id,
        manifest_id="manifest-001",
        root_spec_id="spec-001",
        root_idea_id="idea-001",
        source_spec_id="spec-001",
        draft_index=draft_index,
        title=f"Draft {draft_id}",
        summary="Closure blocker draft.",
        scope=("src/millrace_ai/runtime/completion_behavior.py",),
        target_paths=("src/millrace_ai/runtime/completion_behavior.py",),
        acceptance_intent=("Closure waits for Blueprint work.",),
        verification_intent=("pytest tests/blueprint/test_closure_readiness.py -q",),
        context_excerpt="Closure readiness is in scope.",
        current_revision=0,
        status=status,
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _packet(blueprint_id: str = "blueprint-001") -> BlueprintPacketDocument:
    return BlueprintPacketDocument(
        blueprint_id=blueprint_id,
        draft_id="draft-001",
        manifest_id="manifest-001",
        root_spec_id="spec-001",
        root_idea_id="idea-001",
        revision=1,
        title="Closure readiness packet",
        implementation_scope=("Scan Blueprint state.",),
        intended_files=("src/millrace_ai/runtime/completion_behavior.py",),
        design_decisions=("Block closure on open candidate packets.",),
        verification_plan=("pytest tests/blueprint/test_closure_readiness.py -q",),
        task_acceptance=("Closure blockers are visible.",),
        required_checks=("pytest tests/blueprint/test_closure_readiness.py -q",),
        risk_notes=("false closure",),
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _task() -> TaskDocument:
    return TaskDocument(
        task_id="task-001",
        title="Generated Blueprint task",
        summary="Generated from a promoted Blueprint.",
        root_spec_id="spec-001",
        root_idea_id="idea-001",
        spec_id="spec-001",
        target_paths=("src/millrace_ai/runtime/completion_behavior.py",),
        acceptance=("Closure waits until this task is done.",),
        required_checks=("pytest tests/blueprint/test_closure_readiness.py -q",),
        references=("millrace-agents/blueprints/packets/approved/blueprint-001.json",),
        risk=("Generated task can outlive planning.",),
        created_at=NOW,
        created_by="runtime",
    )


def _promotion(generated_task_path: str = "millrace-agents/tasks/queue/task-001.md") -> BlueprintPromotionRecord:
    return BlueprintPromotionRecord(
        promotion_id="promotion-001",
        blueprint_id="blueprint-001",
        evaluation_id="evaluation-001",
        draft_id="draft-001",
        manifest_id="manifest-001",
        root_spec_id="spec-001",
        root_idea_id="idea-001",
        generated_task_id="task-001",
        generated_task_path=generated_task_path,
        approved_blueprint_path="millrace-agents/blueprints/packets/approved/blueprint-001.json",
        evaluation_path="millrace-agents/blueprints/evaluations/evaluation-001.json",
        promoted_at=NOW,
    )


def test_closure_target_state_migrates_legacy_packed_blueprint_blockers() -> None:
    payload = _target_state().model_dump(mode="python")
    payload.update(
        {
            "closure_blocked_by_lineage_work": True,
            "blocking_work_ids": ("task-001", "blueprint_draft:draft-001"),
        }
    )

    target = ClosureTargetState.model_validate(payload)

    assert target.blocking_work_ids == ("task-001",)
    assert tuple(
        (ref.blocker_type, ref.work_item_family_id, ref.work_item_id, ref.reason)
        for ref in target.blocking_work_refs
    ) == (
        (
            "blueprint_draft",
            "blueprint_draft",
            "draft-001",
            "open_blueprint_draft",
        ),
    )


def test_blueprint_lineage_scan_blocks_on_queued_and_active_drafts(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    enqueue_blueprint_draft(paths, _draft("draft-001", draft_index=1))
    assert claim_next_blueprint_draft(paths) is not None
    enqueue_blueprint_draft(paths, _draft("draft-002", draft_index=2))

    blockers = list_open_blueprint_lineage_work_ids(paths, root_spec_id="spec-001")

    assert blockers == ("blueprint_draft:draft-001", "blueprint_draft:draft-002")


def test_blueprint_lineage_scan_blocks_on_rejected_draft_artifacts(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    rejected_path = paths.runtime_root / "blueprints" / "drafts" / "active" / "draft-001.json"
    rejected_path.parent.mkdir(parents=True, exist_ok=True)
    rejected_path.write_text(
        json.dumps(_draft(status="rejected").model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )

    blockers = list_open_blueprint_lineage_work_ids(paths, root_spec_id="spec-001")

    assert blockers == ("blueprint_draft:draft-001",)


def test_blueprint_lineage_scan_blocks_on_candidate_packets(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    persist_blueprint_packet(paths, _packet(), packet_state="candidates")

    blockers = list_open_blueprint_lineage_work_ids(paths, root_spec_id="spec-001")

    assert blockers == ("blueprint_candidate:blueprint-001",)


def test_blueprint_lineage_scan_exposes_structured_refs(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    enqueue_blueprint_draft(paths, _draft("draft-001", draft_index=1))
    persist_blueprint_packet(paths, _packet(), packet_state="candidates")

    refs = list_open_blueprint_lineage_work_refs(paths, root_spec_id="spec-001")

    assert tuple(
        (
            ref.blocker_type,
            ref.work_item_family_id,
            ref.work_item_id,
            ref.state,
            ref.reason,
        )
        for ref in refs
    ) == (
        (
            "blueprint_draft",
            "blueprint_draft",
            "draft-001",
            "queue",
            "open_blueprint_draft",
        ),
        (
            "blueprint_candidate",
            "blueprint_packet",
            "blueprint-001",
            "candidates",
            "candidate_packet",
        ),
    )


def test_closure_readiness_persists_structured_blueprint_blockers(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(paths, _target_state())
    QueueStore(paths).enqueue_task(_task())
    enqueue_blueprint_draft(paths, _draft("draft-001", draft_index=1))
    persist_blueprint_packet(paths, _packet(), packet_state="candidates")

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    activated = maybe_activate_completion_stage(engine)
    target = load_closure_target_state(paths, root_spec_id="spec-001")

    assert activated is None
    assert target.closure_blocked_by_lineage_work is True
    assert target.blocking_work_ids == ("task-001",)
    assert all(":" not in work_id and "/" not in work_id for work_id in target.blocking_work_ids)
    ref_keys = {
        (ref.blocker_type, ref.work_item_family_id, ref.work_item_id, ref.reason)
        for ref in target.blocking_work_refs
    }
    assert ("work_item", "task", "task-001", "open_lineage_work") in ref_keys
    assert (
        "blueprint_draft",
        "blueprint_draft",
        "draft-001",
        "open_blueprint_draft",
    ) in ref_keys
    assert (
        "blueprint_candidate",
        "blueprint_packet",
        "blueprint-001",
        "candidate_packet",
    ) in ref_keys


def test_closure_readiness_blocks_on_approved_blueprint_generated_task(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(paths, _target_state())
    enqueue_blueprint_draft(paths, _draft())
    assert claim_next_blueprint_draft(paths) is not None
    approve_active_blueprint_draft(paths, "draft-001")
    persist_blueprint_packet(paths, _packet(), packet_state="approved")
    persist_blueprint_promotion(paths, _promotion())
    QueueStore(paths).enqueue_task(_task())

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    activated = maybe_activate_completion_stage(engine)
    target = load_closure_target_state(paths, root_spec_id="spec-001")

    assert activated is None
    assert target.closure_blocked_by_lineage_work is True
    assert target.blocking_work_ids == ("task-001",)


def test_blueprint_lineage_scan_blocks_on_orphan_promotion_records(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    persist_blueprint_packet(paths, _packet(), packet_state="approved")
    persist_blueprint_promotion(paths, _promotion())

    blockers = list_open_blueprint_lineage_work_ids(paths, root_spec_id="spec-001")

    assert blockers == ("blueprint_promotion:promotion-001:missing_generated_task",)


def test_blueprint_lineage_scan_ignores_completed_promotions(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    persist_blueprint_packet(paths, _packet(), packet_state="approved")
    persist_blueprint_promotion(paths, _promotion())
    queue = QueueStore(paths)
    queue.enqueue_task(_task())
    assert queue.claim_next_execution_task() is not None
    queue.mark_task_done("task-001")

    blockers = list_open_blueprint_lineage_work_ids(paths, root_spec_id="spec-001")

    assert blockers == ()
