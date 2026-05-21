from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import millrace_ai.runtime.completion_behavior as completion_behavior
import millrace_ai.runtime.supervisor as supervisor_module
from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.contracts import (
    ClosureTargetState,
    ExecutionStageName,
    LearningRequestDocument,
    LearningStageName,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
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
from millrace_ai.runtime.error_recovery import schedule_pre_dispatch_exception_recovery
from millrace_ai.runtime.supervisor import RuntimeDaemonSupervisor
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


def _learning_request_doc(learning_request_id: str = "learn-001") -> LearningRequestDocument:
    return LearningRequestDocument(
        learning_request_id=learning_request_id,
        title="Improve checker skill",
        requested_action="improve",
        target_skill_id="checker-core",
        created_at=NOW,
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
    run_dir = Path(request.run_dir)
    stdout_path = run_dir / "runner_stdout.txt"
    stdout_path.write_text(f"### {terminal}\n", encoding="utf-8")
    _write_default_planner_disposition(request, terminal=terminal, run_dir=run_dir)
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


def _write_default_planner_disposition(
    request: StageRunRequest,
    *,
    terminal: str,
    run_dir: Path,
) -> None:
    if request.stage is not PlanningStageName.PLANNER:
        return
    if terminal not in {
        PlanningTerminalResult.PLANNER_COMPLETE.value,
        PlanningTerminalResult.BLOCKED.value,
    }:
        return
    if (run_dir / "planner_disposition.json").exists():
        return
    source_family_id = request.active_work_item_family_id
    if source_family_id is None and request.active_work_item_kind is not None:
        source_family_id = request.active_work_item_kind.value
    if source_family_id is None or request.active_work_item_id is None:
        return
    disposition = (
        "blocked"
        if terminal == PlanningTerminalResult.BLOCKED.value
        else "active_source_ready_for_manager"
    )
    (run_dir / "planner_disposition.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "planner_disposition",
                "source_work_item_family_id": source_family_id,
                "source_work_item_id": request.active_work_item_id,
                "disposition": disposition,
                "emitted_spec_ids": [],
                "refined_active_source": False,
                "recommended_next_action": disposition,
                "created_at": NOW.isoformat().replace("+00:00", "Z"),
                "created_by": "planner",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
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


def test_activate_claim_uses_durable_idea_source_when_inbox_source_is_missing(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    root_id = "idea-runtime-persistence-and-reconciliation-foundation"
    idea_markdown = "# Runtime persistence\n\nDurable copy remains available.\n"
    durable_source = paths.runtime_root / "intake" / "ideas" / f"{root_id}.md"
    durable_source.parent.mkdir(parents=True, exist_ok=True)
    durable_source.write_text(idea_markdown, encoding="utf-8")

    queue = QueueStore(paths)
    queue.enqueue_spec(
        _root_spec_doc(
            root_id,
            root_idea_id=root_id,
            created_at=NOW,
            idea_reference="ideas/inbox/runtime-persistence-and-reconciliation-foundation.md",
        )
    )

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    claim = queue.claim_next_planning_item()

    assert claim is not None

    engine._activate_claim(claim)

    target = load_closure_target_state(paths, root_spec_id=root_id)
    assert (paths.root / target.root_idea_path).read_text(encoding="utf-8") == idea_markdown


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


def test_blocked_closure_target_allows_unrelated_root_spec_to_start(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(
        _task_doc(
            "task-blocked-001",
            root_spec_id="spec-root-001",
            root_idea_id="idea-001",
            created_at=NOW,
        )
    )
    blocked_claim = queue.claim_next_execution_task()

    assert blocked_claim is not None

    queue.mark_task_blocked("task-blocked-001")
    save_closure_target_state(
        paths,
        _target_state().model_copy(
            update={
                "closure_blocked_by_lineage_work": True,
                "blocking_work_ids": ("task-blocked-001",),
            }
        ),
    )
    _write_idea(paths, "idea-002")
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
        return _runner_result(request, terminal="PLANNER_COMPLETE")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)

    engine.tick()

    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert request.stage is PlanningStageName.PLANNER
    assert request.active_work_item_kind is WorkItemKind.SPEC
    assert request.active_work_item_id == "spec-root-002"
    assert (paths.specs_active_dir / "spec-root-002.md").is_file()
    assert load_closure_target_state(paths, root_spec_id="spec-root-001").closure_open is True
    assert load_closure_target_state(paths, root_spec_id="spec-root-002").closure_open is True


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


def test_runtime_tick_reports_closure_readiness_exception_without_raising(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(paths, _target_state())

    def fail_readiness(engine: RuntimeEngine, target: ClosureTargetState) -> ClosureTargetState:
        raise RuntimeError(f"readiness exploded for {target.root_spec_id}")

    monkeypatch.setattr(
        completion_behavior,
        "refresh_closure_target_readiness",
        fail_readiness,
    )

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    outcome = engine.tick()
    snapshot = load_snapshot(paths)
    context = json.loads(paths.runtime_error_context_file.read_text(encoding="utf-8"))
    events = read_runtime_events(paths)

    assert outcome.router_decision.reason == "runtime_exception:planning_pre_dispatch_failed"
    assert snapshot.active_stage is PlanningStageName.MECHANIC
    assert snapshot.planning_status_marker == "### BLOCKED"
    assert snapshot.current_failure_class == "planning_pre_dispatch_failed"
    assert context["error_code"] == "planning_pre_dispatch_failed"
    assert context["work_item_family_id"] == "spec"
    assert context["work_item_id"] == "spec-root-001"
    assert "readiness exploded for spec-root-001" in context["exception_message"]
    assert any(
        event.event_type == "runtime_pre_dispatch_recovery_scheduled"
        and event.data.get("error_code") == "planning_pre_dispatch_failed"
        for event in events
    )


def test_daemon_supervisor_reports_closure_request_exception_without_crashing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(paths, _target_state())

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
        engine.startup()

        def fail_request(stage_plan, target_state):
            raise RuntimeError(f"closure request exploded for {target_state.root_spec_id}")

        monkeypatch.setattr(engine, "_build_closure_target_stage_run_request", fail_request)
        supervisor = RuntimeDaemonSupervisor(engine)

        dispatched = await supervisor.dispatch_ready_work()
        snapshot = load_snapshot(paths)
        context = json.loads(paths.runtime_error_context_file.read_text(encoding="utf-8"))
        events = read_runtime_events(paths)

        assert dispatched == 0
        assert supervisor.active_worker_lanes == frozenset()
        assert snapshot.active_stage is PlanningStageName.MECHANIC
        assert snapshot.planning_status_marker == "### BLOCKED"
        assert snapshot.current_failure_class == "planning_pre_dispatch_failed"
        assert context["error_code"] == "planning_pre_dispatch_failed"
        assert context["work_item_id"] == "spec-root-001"
        assert "closure request exploded for spec-root-001" in context["exception_message"]
        assert any(
            event.event_type == "runtime_pre_dispatch_recovery_scheduled"
            and event.data.get("error_code") == "planning_pre_dispatch_failed"
            for event in events
        )
        engine.close()

    asyncio.run(scenario())


def test_daemon_supervisor_reports_claim_selection_exception_without_crashing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(paths, _target_state())

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
        engine.startup()

        def fail_active_closure_target(engine: RuntimeEngine):
            raise RuntimeError("closure target lookup exploded during claim selection")

        monkeypatch.setattr(
            completion_behavior,
            "active_closure_target",
            fail_active_closure_target,
        )
        supervisor = RuntimeDaemonSupervisor(engine)

        dispatched = await supervisor.dispatch_ready_work()
        snapshot = load_snapshot(paths)
        context = json.loads(paths.runtime_error_context_file.read_text(encoding="utf-8"))
        events = read_runtime_events(paths)

        assert dispatched == 0
        assert supervisor.active_worker_lanes == frozenset()
        assert snapshot.active_stage is PlanningStageName.MECHANIC
        assert snapshot.planning_status_marker == "### BLOCKED"
        assert snapshot.current_failure_class == "planning_pre_dispatch_failed"
        assert context["error_code"] == "planning_pre_dispatch_failed"
        assert context["work_item_id"] == "runtime-pre-dispatch"
        assert "closure target lookup exploded during claim selection" in context[
            "exception_message"
        ]
        assert any(
            event.event_type == "runtime_pre_dispatch_recovery_scheduled"
            and event.data.get("error_code") == "planning_pre_dispatch_failed"
            for event in events
        )
        engine.close()

    asyncio.run(scenario())


def test_daemon_supervisor_reports_execution_claim_selection_with_task_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _workspace(tmp_path)

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
        engine.startup()

        def fail_execution_claim(engine: RuntimeEngine, plane: Plane):
            if plane is Plane.EXECUTION:
                raise RuntimeError("execution claim selection exploded")
            return None

        monkeypatch.setattr(
            supervisor_module,
            "claim_next_work_item_for_plane",
            fail_execution_claim,
        )
        supervisor = RuntimeDaemonSupervisor(engine)

        dispatched = await supervisor.dispatch_ready_work()
        snapshot = load_snapshot(paths)
        context = json.loads(paths.runtime_error_context_file.read_text(encoding="utf-8"))

        assert dispatched == 0
        assert snapshot.active_stage is ExecutionStageName.TROUBLESHOOTER
        assert snapshot.execution_status_marker == "### BLOCKED"
        assert snapshot.current_failure_class == "execution_pre_dispatch_failed"
        assert snapshot.active_work_item_family_id == "task"
        assert snapshot.active_work_item_id == "runtime-pre-dispatch"
        assert context["error_code"] == "execution_pre_dispatch_failed"
        assert context["work_item_family_id"] == "task"
        assert context["work_item_id"] == "runtime-pre-dispatch"
        engine.close()

    asyncio.run(scenario())


def test_daemon_supervisor_reports_learning_request_build_exception_without_crashing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _workspace(tmp_path)
    QueueStore(paths).enqueue_learning_request(_learning_request_doc())

    async def scenario() -> None:
        engine = RuntimeEngine(
            paths,
            stage_runner=_unused_stage_runner,
            mode_id="learning_codex",
        )
        engine.startup()

        def fail_learning_request(stage_plan):
            raise RuntimeError("learning request build exploded")

        monkeypatch.setattr(engine, "_build_stage_run_request", fail_learning_request)
        supervisor = RuntimeDaemonSupervisor(engine)

        dispatched = await supervisor.dispatch_ready_work()
        snapshot = load_snapshot(paths)
        context = json.loads(paths.runtime_error_context_file.read_text(encoding="utf-8"))

        assert dispatched == 0
        assert snapshot.active_stage is LearningStageName.LIBRARIAN
        assert snapshot.learning_status_marker == "### BLOCKED"
        assert snapshot.current_failure_class == "learning_pre_dispatch_failed"
        assert snapshot.active_work_item_family_id == "learning_request"
        assert snapshot.active_work_item_id == "learn-001"
        assert context["error_code"] == "learning_pre_dispatch_failed"
        assert context["work_item_family_id"] == "learning_request"
        assert context["work_item_id"] == "learn-001"
        engine.close()

    asyncio.run(scenario())


def test_runtime_tick_reports_learning_request_build_exception_without_misrouting(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _workspace(tmp_path)
    QueueStore(paths).enqueue_learning_request(_learning_request_doc())
    engine = RuntimeEngine(
        paths,
        stage_runner=_unused_stage_runner,
        mode_id="learning_codex",
    )
    engine.startup()

    def fail_learning_request(stage_plan):
        raise RuntimeError("serial learning request build exploded")

    monkeypatch.setattr(engine, "_build_stage_run_request", fail_learning_request)

    outcome = engine.tick()
    snapshot = load_snapshot(paths)
    context = json.loads(paths.runtime_error_context_file.read_text(encoding="utf-8"))

    assert outcome.router_decision.reason == "runtime_exception:learning_pre_dispatch_failed"
    assert snapshot.active_stage is LearningStageName.LIBRARIAN
    assert snapshot.learning_status_marker == "### BLOCKED"
    assert snapshot.current_failure_class == "learning_pre_dispatch_failed"
    assert snapshot.active_work_item_family_id == "learning_request"
    assert snapshot.active_work_item_id == "learn-001"
    assert context["error_code"] == "learning_pre_dispatch_failed"
    assert context["work_item_family_id"] == "learning_request"
    assert context["work_item_id"] == "learn-001"


def test_learning_recovery_request_keeps_runtime_error_context_with_foreground_active_run(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(
        _task_doc(
            "task-001",
            root_spec_id="spec-root-001",
            root_idea_id="idea-001",
            created_at=NOW,
        )
    )
    queue.enqueue_learning_request(_learning_request_doc())
    engine = RuntimeEngine(
        paths,
        stage_runner=_unused_stage_runner,
        mode_id="learning_codex",
    )
    engine.startup()
    execution_claim = queue.claim_next_execution_task()
    learning_claim = queue.claim_next_learning_request()
    assert execution_claim is not None
    assert learning_claim is not None
    engine._activate_claim(execution_claim)

    schedule_pre_dispatch_exception_recovery(
        engine,
        error=RuntimeError("learning recovery exploded beside foreground work"),
        plane=Plane.LEARNING,
        failed_stage=LearningStageName.ANALYST,
        work_item_family_id=learning_claim.family_id,
        work_item_kind=learning_claim.work_item_kind,
        work_item_id=learning_claim.work_item_id,
    )
    assert engine.snapshot is not None
    assert Plane.EXECUTION in engine.snapshot.active_runs_by_plane
    learning_active_run = engine.snapshot.active_runs_by_plane[Plane.LEARNING]

    request = RuntimeDaemonSupervisor(engine)._request_for_active_run(learning_active_run)

    assert request.stage is LearningStageName.LIBRARIAN
    assert request.active_work_item_id == "learn-001"
    assert request.runtime_error_code == "learning_pre_dispatch_failed"
    assert request.runtime_error_report_path is not None
    engine.close()


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


def test_maybe_activate_completion_stage_blocks_when_root_idea_source_is_missing(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
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

    queue.mark_spec_done("spec-root-001")

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    activated = maybe_activate_completion_stage(engine)
    snapshot = load_snapshot(paths)
    events = read_runtime_events(paths)

    assert activated is None
    assert load_planning_status(paths) == "### BLOCKED"
    assert snapshot.planning_status_marker == "### BLOCKED"
    assert snapshot.current_failure_class == "missing_root_idea_source"
    assert any(
        event.event_type == "completion_behavior_blocked"
        and event.data.get("reason") == "missing_root_idea_source"
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
