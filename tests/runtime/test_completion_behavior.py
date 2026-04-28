from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.contracts import (
    ClosureTargetState,
    ExecutionStageName,
    Plane,
    PlanningStageName,
    SpecDocument,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.events import read_runtime_events
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.completion_behavior import maybe_activate_completion_stage
from millrace_ai.state_store import load_planning_status, load_snapshot
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


def _runner_result(request: StageRunRequest, *, terminal: str) -> RunnerRawResult:
    stdout_path = Path(request.run_dir) / "runner_stdout.txt"
    stdout_path.write_text(f"### {terminal}\n", encoding="utf-8")
    return RunnerRawResult(
        request_id=request.request_id,
        run_id=request.run_id,
        stage=request.stage,
        runner_name=request.runner_name or "test-runner",
        model_name=request.model_name,
        exit_kind="completed",
        exit_code=0,
        stdout_path=str(stdout_path),
        stderr_path=None,
        terminal_result_path=None,
        observed_exit_kind=None,
        observed_exit_code=None,
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=1),
    )


def _write_idea(paths, idea_id: str) -> None:
    idea_path = paths.root / "ideas" / "inbox" / f"{idea_id}.md"
    idea_path.parent.mkdir(parents=True, exist_ok=True)
    idea_path.write_text(f"# {idea_id}\n\nSeed contract for {idea_id}.\n", encoding="utf-8")


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


def test_activate_claim_backpressures_second_open_closure_target_without_half_claiming(
    tmp_path: Path,
) -> None:
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

    engine._activate_claim(claim)

    snapshot = load_snapshot(paths)
    assert snapshot.active_stage is None
    assert snapshot.active_work_item_kind is None
    assert snapshot.active_work_item_id is None
    assert (paths.specs_queue_dir / "spec-root-002.md").is_file()
    assert not (paths.specs_active_dir / "spec-root-002.md").exists()
    assert load_closure_target_state(paths, root_spec_id="spec-root-001").closure_open is True


def test_open_closure_target_backpressures_unrelated_root_spec_and_runs_lineage_task(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(paths, _target_state())
    _write_idea(paths, "idea-002")

    queue = QueueStore(paths)
    queue.enqueue_task(
        _task_doc(
            "task-lineage-001",
            root_spec_id="spec-root-001",
            root_idea_id="idea-001",
            created_at=NOW,
        )
    )
    queue.enqueue_spec(
        _root_spec_doc(
            "spec-root-002",
            root_idea_id="idea-002",
            created_at=NOW,
            idea_reference="ideas/inbox/idea-002.md",
        )
    )
    captured_requests: list[StageRunRequest] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        captured_requests.append(request)
        return _runner_result(request, terminal="BUILDER_COMPLETE")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)

    engine.tick()

    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert request.stage is ExecutionStageName.BUILDER
    assert request.active_work_item_kind is WorkItemKind.TASK
    assert request.active_work_item_id == "task-lineage-001"
    assert (paths.specs_queue_dir / "spec-root-002.md").is_file()
    assert not (paths.specs_active_dir / "spec-root-002.md").exists()
    assert load_closure_target_state(paths, root_spec_id="spec-root-001").closure_open is True
    events = read_runtime_events(paths)
    assert any(
        event.event_type == "closure_target_backpressure"
        and event.data.get("open_root_spec_id") == "spec-root-001"
        and event.data.get("deferred_root_spec_ids") == ["spec-root-002"]
        for event in events
    )


def test_open_closure_target_activates_arbiter_before_unrelated_root_spec(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(paths, _target_state())
    _write_idea(paths, "idea-002")

    QueueStore(paths).enqueue_spec(
        _root_spec_doc(
            "spec-root-002",
            root_idea_id="idea-002",
            created_at=NOW,
            idea_reference="ideas/inbox/idea-002.md",
        )
    )
    captured_requests: list[StageRunRequest] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        captured_requests.append(request)
        return _runner_result(request, terminal="ARBITER_COMPLETE")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)

    engine.tick()

    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert request.stage is PlanningStageName.ARBITER
    assert request.request_kind == "closure_target"
    assert request.closure_target_root_spec_id == "spec-root-001"
    assert (paths.specs_queue_dir / "spec-root-002.md").is_file()
    assert not (paths.specs_active_dir / "spec-root-002.md").exists()


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


def test_maybe_activate_completion_stage_blocks_on_closure_lineage_drift(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    canonical_root = "idea-idea-2026-04-27-browser-local-qa"
    stale_root = "idea-2026-04-27-browser-local-qa"
    save_closure_target_state(
        paths,
        _target_state(root_spec_id=canonical_root, root_idea_id=canonical_root),
    )
    QueueStore(paths).enqueue_task(
        _task_doc(
            "task-browser-local-qa",
            root_spec_id=stale_root,
            root_idea_id=canonical_root,
            created_at=NOW,
        )
    )

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    activated = maybe_activate_completion_stage(engine)
    snapshot = load_snapshot(paths)
    target = load_closure_target_state(paths, root_spec_id=canonical_root)
    events = read_runtime_events(paths)
    diagnostic_path = (
        paths.arbiter_dir
        / "diagnostics"
        / "lineage-drift"
        / f"{canonical_root}.json"
    )

    assert activated is None
    assert snapshot.active_stage is None
    assert snapshot.planning_status_marker == "### BLOCKED"
    assert snapshot.current_failure_class == "closure_lineage_drift"
    assert target.closure_blocked_by_lineage_work is True
    assert target.blocking_work_ids == ("task-browser-local-qa",)
    assert diagnostic_path.is_file()
    assert any(
        event.event_type == "closure_lineage_drift_detected"
        and event.data.get("root_spec_id") == canonical_root
        for event in events
    )


def test_maybe_activate_completion_stage_sets_snapshot_to_arbiter_when_target_is_eligible(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(paths, _target_state())

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    graph_plan = CompiledRunPlan.model_validate_json(
        (paths.state_dir / "compiled_plan.json").read_text(encoding="utf-8")
    )

    activated = maybe_activate_completion_stage(engine)
    target = load_closure_target_state(paths, root_spec_id="spec-root-001")

    assert graph_plan.planning_graph.compiled_completion_entry is not None
    assert graph_plan.planning_graph.compiled_completion_entry.node_id == "arbiter"
    assert activated is not None
    assert activated.root_spec_id == "spec-root-001"
    assert target.closure_blocked_by_lineage_work is False
    assert target.blocking_work_ids == ()
    assert engine.snapshot is not None
    assert engine.snapshot.active_plane is Plane.PLANNING
    assert engine.snapshot.active_stage is PlanningStageName.ARBITER
    assert engine.snapshot.active_stage.value == graph_plan.planning_graph.compiled_completion_entry.node_id
    assert engine.snapshot.active_run_id is not None
    assert engine.snapshot.active_work_item_kind is None
    assert engine.snapshot.active_work_item_id is None


def test_maybe_activate_completion_stage_backfills_open_target_from_done_root_spec(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    idea_path = paths.root / "ideas" / "inbox" / "idea-001.md"
    idea_path.parent.mkdir(parents=True, exist_ok=True)
    idea_path.write_text("# Idea 001\n\nBackfill closure target from root spec.\n", encoding="utf-8")

    queue = QueueStore(paths)
    queue.enqueue_spec(
        _root_spec_doc(
            "spec-root-001",
            root_idea_id="idea-001",
            created_at=NOW,
            idea_reference="ideas/inbox/idea-001.md",
        )
    )
    claim = queue.claim_next_planning_item()

    assert claim is not None
    assert not (paths.arbiter_targets_dir / "spec-root-001.json").exists()

    queue.mark_spec_done("spec-root-001")

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    activated = maybe_activate_completion_stage(engine)
    target = load_closure_target_state(paths, root_spec_id="spec-root-001")

    assert activated is not None
    assert target.root_spec_id == "spec-root-001"
    assert target.closure_open is True
    assert engine.snapshot is not None
    assert engine.snapshot.active_stage is PlanningStageName.ARBITER


def test_maybe_activate_completion_stage_blocks_when_root_spec_missing_lineage(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    idea_path = paths.root / "ideas" / "inbox" / "idea-001.md"
    idea_path.parent.mkdir(parents=True, exist_ok=True)
    idea_path.write_text("# Idea 001\n\nMissing root lineage should block closure.\n", encoding="utf-8")

    root_spec = _root_spec_doc(
        "spec-root-001",
        root_idea_id="idea-001",
        created_at=NOW,
        idea_reference="ideas/inbox/idea-001.md",
    ).model_copy(update={"root_idea_id": None, "root_spec_id": None})

    queue = QueueStore(paths)
    queue.enqueue_spec(root_spec)
    claim = queue.claim_next_planning_item()

    assert claim is not None

    queue.mark_spec_done("spec-root-001")

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    activated = maybe_activate_completion_stage(engine)
    snapshot = load_snapshot(paths)
    events = read_runtime_events(paths)

    assert activated is None
    assert load_planning_status(paths) == "### BLOCKED"
    assert snapshot.planning_status_marker == "### BLOCKED"
    assert snapshot.current_failure_class == "missing_root_lineage"
    assert any(
        event.event_type == "completion_behavior_blocked"
        and event.data.get("reason") == "missing_root_lineage"
        and event.data.get("spec_id") == "spec-root-001"
        for event in events
    )


def test_maybe_activate_completion_stage_uses_task_spec_id_as_root_lineage_fallback(
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
        ).model_copy(
            update={
                "root_spec_id": None,
                "root_idea_id": None,
                "spec_id": "spec-root-001",
            }
        )
    )

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    activated = maybe_activate_completion_stage(engine)
    target = load_closure_target_state(paths, root_spec_id="spec-root-001")

    assert activated is None
    assert target.closure_blocked_by_lineage_work is True
    assert target.blocking_work_ids == ("task-001",)
