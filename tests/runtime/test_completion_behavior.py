from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from millrace_ai.contracts import (
    ClosureTargetState,
    Plane,
    PlanningStageName,
    SpecDocument,
    TaskDocument,
)
from millrace_ai.errors import WorkspaceStateError
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.completion_behavior import maybe_activate_completion_stage
from millrace_ai.workspace.arbiter_state import (
    load_closure_target_state,
    save_closure_target_state,
)

NOW = datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _root_spec_doc(
    spec_id: str,
    *,
    root_idea_id: str,
    created_at: datetime,
    idea_reference: str,
) -> SpecDocument:
    return SpecDocument(
        spec_id=spec_id,
        title=f"Root Spec {spec_id}",
        summary="root closure target",
        source_type="idea",
        source_id=root_idea_id,
        root_idea_id=root_idea_id,
        root_spec_id=spec_id,
        goals=("ship the requested product",),
        constraints=("keep the implementation deterministic",),
        acceptance=("runtime can carry the lineage to closure",),
        references=(idea_reference,),
        created_at=created_at,
        created_by="tests",
    )


def _task_doc(
    task_id: str,
    *,
    root_spec_id: str,
    root_idea_id: str,
    created_at: datetime,
) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="remaining lineage work",
        spec_id=root_spec_id,
        root_spec_id=root_spec_id,
        root_idea_id=root_idea_id,
        target_paths=("src/millrace_ai/runtime/engine.py",),
        acceptance=("arbiter should stay suppressed while lineage work remains",),
        required_checks=("uv run --extra dev python -m pytest tests/runtime/test_completion_behavior.py -q",),
        references=("lab/ideas/later/2026-04-18-millrace-arbiter-compiler-driven-completion-behavior.md",),
        risk=("false completion audit",),
        created_at=created_at,
        created_by="tests",
    )


def _target_state(*, root_spec_id: str = "spec-root-001", root_idea_id: str = "idea-001") -> ClosureTargetState:
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


def _unused_stage_runner(request: StageRunRequest) -> RunnerRawResult:
    raise AssertionError(f"stage_runner should not be called during setup: {request.stage.value}")


def test_activate_claim_opens_closure_target_and_snapshots_canonical_contracts(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    idea_path = paths.root / "ideas" / "inbox" / "idea-001.md"
    idea_path.parent.mkdir(parents=True, exist_ok=True)
    idea_markdown = "# Idea 001\n\nShip the root lineage cleanly.\n"
    idea_path.write_text(idea_markdown, encoding="utf-8")

    queue = QueueStore(paths)
    queue.enqueue_spec(
        _root_spec_doc(
            "spec-root-001",
            root_idea_id="idea-001",
            created_at=NOW,
            idea_reference="ideas/inbox/idea-001.md",
        )
    )

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    claim = queue.claim_next_planning_item()

    assert claim is not None

    engine._activate_claim(claim)

    target = load_closure_target_state(paths, root_spec_id="spec-root-001")

    assert target.root_idea_path == "millrace-agents/arbiter/contracts/ideas/idea-001.md"
    assert target.root_spec_path == "millrace-agents/arbiter/contracts/root-specs/spec-root-001.md"
    assert (paths.root / target.root_idea_path).read_text(encoding="utf-8") == idea_markdown
    assert "Root-Spec-ID: spec-root-001" in (paths.root / target.root_spec_path).read_text(
        encoding="utf-8"
    )


def test_activate_claim_rejects_second_open_closure_target(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(paths, _target_state())

    idea_path = paths.root / "ideas" / "inbox" / "idea-002.md"
    idea_path.parent.mkdir(parents=True, exist_ok=True)
    idea_path.write_text("# Idea 002\n\nAnother root lineage.\n", encoding="utf-8")

    queue = QueueStore(paths)
    queue.enqueue_spec(
        _root_spec_doc(
            "spec-root-002",
            root_idea_id="idea-002",
            created_at=NOW,
            idea_reference="ideas/inbox/idea-002.md",
        )
    )

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    claim = queue.claim_next_planning_item()

    assert claim is not None

    with pytest.raises(WorkspaceStateError, match="open closure target"):
        engine._activate_claim(claim)


def test_maybe_activate_completion_stage_marks_target_blocked_when_lineage_work_remains(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(paths, _target_state())

    queue = QueueStore(paths)
    queue.enqueue_task(
        _task_doc(
            "task-001",
            root_spec_id="spec-root-001",
            root_idea_id="idea-001",
            created_at=NOW,
        )
    )

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    activated = maybe_activate_completion_stage(engine)
    target = load_closure_target_state(paths, root_spec_id="spec-root-001")

    assert activated is None
    assert target.closure_blocked_by_lineage_work is True
    assert target.blocking_work_ids == ("task-001",)
    assert engine.snapshot is not None
    assert engine.snapshot.active_stage is None


def test_maybe_activate_completion_stage_sets_snapshot_to_arbiter_when_target_is_eligible(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(paths, _target_state())

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    activated = maybe_activate_completion_stage(engine)
    target = load_closure_target_state(paths, root_spec_id="spec-root-001")

    assert activated is not None
    assert activated.root_spec_id == "spec-root-001"
    assert target.closure_blocked_by_lineage_work is False
    assert target.blocking_work_ids == ()
    assert engine.snapshot is not None
    assert engine.snapshot.active_plane is Plane.PLANNING
    assert engine.snapshot.active_stage is PlanningStageName.ARBITER
    assert engine.snapshot.active_run_id is not None
    assert engine.snapshot.active_work_item_kind is None
    assert engine.snapshot.active_work_item_id is None
