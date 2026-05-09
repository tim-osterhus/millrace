from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.contracts import (
    ActiveRunState,
    ExecutionStageName,
    LearningRequestDocument,
    LearningStageName,
    LearningTerminalResult,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    ProbeDocument,
    ReconConfidence,
    ReconDecision,
    ReconPacketDocument,
    ReconPathFinding,
    ReconRiskLevel,
    ReconVerificationPlan,
    ResultClass,
    SpecDocument,
    StageResultEnvelope,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.recon_packets import render_recon_packet
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.result_application import apply_router_decision
from millrace_ai.runtime.work_item_transitions import apply_idle_router_decision
from millrace_ai.state_store import load_snapshot, save_snapshot

NOW = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _unused_stage_runner(request: StageRunRequest) -> RunnerRawResult:
    raise AssertionError("stage runner should not be called")


def _task_doc(task_id: str) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="runtime result application test",
        target_paths=["src/millrace_ai/runtime/"],
        acceptance=["lane-aware result application"],
        required_checks=["pytest tests/runtime/test_result_application.py -q"],
        references=["lab/specs/pending/2026-04-28-millrace-generic-plane-concurrent-runtime-scheduler.md"],
        risk=["active state drift"],
        created_at=NOW,
        created_by="tests",
    )


def _learning_request_doc(learning_request_id: str) -> LearningRequestDocument:
    return LearningRequestDocument(
        learning_request_id=learning_request_id,
        title=f"Learning {learning_request_id}",
        requested_action="improve",
        target_skill_id="checker-core",
        target_stage="curator",
        created_at=NOW,
        created_by="tests",
    )


def _probe_doc(probe_id: str) -> ProbeDocument:
    return ProbeDocument(
        probe_id=probe_id,
        title=f"Probe {probe_id}",
        summary="research before routing",
        request="Research the repo surface and route this work safely.",
        target_paths=("src/example.py",),
        acceptance=("Recon packet is produced.",),
        created_at=NOW,
        created_by="tests",
    )


def _recon_packet(probe_id: str, *, decision: ReconDecision) -> ReconPacketDocument:
    return ReconPacketDocument(
        recon_packet_id=f"recon-{probe_id}",
        probe_id=probe_id,
        decision=decision,
        confidence=ReconConfidence.HIGH,
        risk_level=ReconRiskLevel.MEDIUM,
        request_summary="Research before routing.",
        interpreted_goal="Route this work through the smallest safe lane.",
        relevant_paths=(
            ReconPathFinding(path="src/example.py", reason="Likely behavior owner."),
        ),
        semantic_invariants=("Preserve adjacent behavior.",),
        verification_plan=ReconVerificationPlan(
            required_commands=("uv run --extra dev python -m pytest -q",),
            focused_checks=("Run adjacent tests.",),
        ),
        handoff_target="execution" if decision is ReconDecision.TO_EXECUTION else "planning",
        emitted_task_id="task-from-probe" if decision is ReconDecision.TO_EXECUTION else None,
        emitted_spec_id="spec-from-probe" if decision is ReconDecision.TO_PLANNING else None,
        created_at=NOW,
    )


def _generated_probe_task() -> TaskDocument:
    return TaskDocument(
        task_id="task-from-probe",
        title="Task from probe",
        summary="direct execution route",
        root_intake_kind="probe",
        root_intake_id="probe-001",
        target_paths=("src/example.py",),
        acceptance=("Apply the recon packet.",),
        required_checks=("uv run --extra dev python -m pytest -q",),
        references=("millrace-agents/probes/active/probe-001.md",),
        risk=("Regression risk from ambiguous request.",),
        created_at=NOW,
        created_by="recon",
    )


def _generated_probe_spec() -> SpecDocument:
    return SpecDocument(
        spec_id="spec-from-probe",
        title="Spec from probe",
        summary="planning route",
        source_type="probe",
        source_id="probe-001",
        root_intake_kind="probe",
        root_intake_id="probe-001",
        root_spec_id="spec-from-probe",
        goals=("Plan the probe-derived change.",),
        constraints=("Use the recon packet as required context.",),
        acceptance=("Manager can decompose the spec.",),
        references=("millrace-agents/probes/active/probe-001.md",),
        created_at=NOW,
        created_by="recon",
    )


def test_learning_idle_result_does_not_clear_active_execution_lane(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001"))
    queue.enqueue_learning_request(_learning_request_doc("learn-001"))
    task_claim = queue.claim_next_execution_task()
    learning_claim = queue.claim_next_learning_request()
    assert task_claim is not None
    assert learning_claim is not None

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="learning_codex")
    engine.startup()
    assert engine.snapshot is not None
    snapshot = engine.snapshot.model_copy(
        update={
            "active_runs_by_plane": {
                Plane.EXECUTION: ActiveRunState(
                    plane=Plane.EXECUTION,
                    stage=ExecutionStageName.BUILDER,
                    node_id="builder",
                    stage_kind_id="builder",
                    run_id="run-exec",
                    request_kind="active_work_item",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    active_since=NOW,
                ),
                Plane.LEARNING: ActiveRunState(
                    plane=Plane.LEARNING,
                    stage=LearningStageName.CURATOR,
                    node_id="curator",
                    stage_kind_id="curator",
                    run_id="run-learn",
                    request_kind="learning_request",
                    work_item_kind=WorkItemKind.LEARNING_REQUEST,
                    work_item_id="learn-001",
                    active_since=NOW,
                ),
            },
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "active_node_id": "builder",
            "active_stage_kind_id": "builder",
            "active_run_id": "run-exec",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    engine.snapshot = snapshot
    save_snapshot(paths, snapshot)
    stage_result = StageResultEnvelope(
        run_id="run-learn",
        plane="learning",
        stage="curator",
        node_id="curator",
        stage_kind_id="curator",
        work_item_kind="learning_request",
        work_item_id="learn-001",
        terminal_result=LearningTerminalResult.CURATOR_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### CURATOR_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )

    apply_idle_router_decision(engine, stage_result)

    updated = load_snapshot(paths)
    assert set(updated.active_runs_by_plane) == {Plane.EXECUTION}
    assert updated.active_runs_by_plane[Plane.EXECUTION].work_item_id == "task-001"
    assert updated.active_plane is Plane.EXECUTION
    assert updated.active_stage is ExecutionStageName.BUILDER
    assert (paths.learning_requests_done_dir / "learn-001.md").is_file()
    assert (paths.tasks_active_dir / "task-001.md").is_file()


def test_execution_run_stage_result_does_not_clear_active_learning_lane(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001"))
    queue.enqueue_learning_request(_learning_request_doc("learn-001"))
    assert queue.claim_next_execution_task() is not None
    assert queue.claim_next_learning_request() is not None

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="learning_codex")
    engine.startup()
    assert engine.snapshot is not None
    snapshot = engine.snapshot.model_copy(
        update={
            "active_runs_by_plane": {
                Plane.EXECUTION: ActiveRunState(
                    plane=Plane.EXECUTION,
                    stage=ExecutionStageName.BUILDER,
                    node_id="builder",
                    stage_kind_id="builder",
                    run_id="run-exec",
                    request_kind="active_work_item",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    active_since=NOW,
                ),
                Plane.LEARNING: ActiveRunState(
                    plane=Plane.LEARNING,
                    stage=LearningStageName.CURATOR,
                    node_id="curator",
                    stage_kind_id="curator",
                    run_id="run-learn",
                    request_kind="learning_request",
                    work_item_kind=WorkItemKind.LEARNING_REQUEST,
                    work_item_id="learn-001",
                    active_since=NOW,
                ),
            },
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "active_node_id": "builder",
            "active_stage_kind_id": "builder",
            "active_run_id": "run-exec",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    engine.snapshot = snapshot
    save_snapshot(paths, snapshot)
    stage_result = StageResultEnvelope(
        run_id="run-exec",
        plane="execution",
        stage="builder",
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind="task",
        work_item_id="task-001",
        terminal_result="BUILDER_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )
    decision = RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=Plane.EXECUTION,
        next_stage=ExecutionStageName.CHECKER,
        next_node_id="checker",
        next_stage_kind_id="checker",
        reason="builder:BUILDER_COMPLETE",
    )

    apply_router_decision(engine, decision, stage_result)

    assert engine.snapshot.active_runs_by_plane[Plane.EXECUTION].stage is ExecutionStageName.CHECKER
    assert engine.snapshot.active_runs_by_plane[Plane.LEARNING].stage is LearningStageName.CURATOR
    assert engine.snapshot.active_runs_by_plane[Plane.LEARNING].work_item_id == "learn-001"


def test_recon_to_execution_persists_packet_marks_probe_done_and_enqueues_task(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_probe(_probe_doc("probe-001"))
    probe_claim = queue.claim_next_planning_item()
    assert probe_claim is not None

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    assert engine.snapshot is not None
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "active_runs_by_plane": {
                Plane.PLANNING: ActiveRunState(
                    plane=Plane.PLANNING,
                    stage=PlanningStageName.RECON,
                    node_id="recon",
                    stage_kind_id="recon",
                    run_id="run-recon",
                    request_kind="active_work_item",
                    work_item_kind=WorkItemKind.PROBE,
                    work_item_id="probe-001",
                    active_since=NOW,
                )
            },
            "active_plane": Plane.PLANNING,
            "active_stage": PlanningStageName.RECON,
            "active_node_id": "recon",
            "active_stage_kind_id": "recon",
            "active_run_id": "run-recon",
            "active_work_item_kind": WorkItemKind.PROBE,
            "active_work_item_id": "probe-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, engine.snapshot)
    run_dir = paths.runs_dir / "run-recon"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "recon_packet.md").write_text(
        render_recon_packet(_recon_packet("probe-001", decision=ReconDecision.TO_EXECUTION)),
        encoding="utf-8",
    )
    (run_dir / "generated_task.md").write_text(
        _generated_probe_task().model_dump_json(indent=2),
        encoding="utf-8",
    )
    stage_result = StageResultEnvelope(
        run_id="run-recon",
        plane="planning",
        stage="recon",
        node_id="recon",
        stage_kind_id="recon",
        work_item_kind="probe",
        work_item_id="probe-001",
        terminal_result=PlanningTerminalResult.RECON_TO_EXECUTION,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### RECON_TO_EXECUTION",
        success=True,
        artifact_paths=("recon_packet.md", "generated_task.md"),
        started_at=NOW,
        completed_at=NOW,
    )
    decision = RouterDecision(action=RouterAction.IDLE, next_plane=None, next_stage=None, reason="recon_to_execution")

    spawned = apply_router_decision(engine, decision, stage_result)

    assert spawned == (paths.tasks_queue_dir / "task-from-probe.md",)
    assert (paths.probes_done_dir / "probe-001.md").is_file()
    assert (paths.recon_packets_dir / "recon-probe-001.md").is_file()
    task = (paths.tasks_queue_dir / "task-from-probe.md").read_text(encoding="utf-8")
    assert "Root-Intake-Kind: probe" in task
    assert "Root-Intake-ID: probe-001" in task
    assert "millrace-agents/recon/packets/recon-probe-001.md" in task


def test_recon_to_planning_persists_packet_marks_probe_done_and_enqueues_spec(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_probe(_probe_doc("probe-001"))
    assert queue.claim_next_planning_item() is not None

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    assert engine.snapshot is not None
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "active_runs_by_plane": {
                Plane.PLANNING: ActiveRunState(
                    plane=Plane.PLANNING,
                    stage=PlanningStageName.RECON,
                    node_id="recon",
                    stage_kind_id="recon",
                    run_id="run-recon",
                    request_kind="active_work_item",
                    work_item_kind=WorkItemKind.PROBE,
                    work_item_id="probe-001",
                    active_since=NOW,
                )
            },
            "active_plane": Plane.PLANNING,
            "active_stage": PlanningStageName.RECON,
            "active_node_id": "recon",
            "active_stage_kind_id": "recon",
            "active_run_id": "run-recon",
            "active_work_item_kind": WorkItemKind.PROBE,
            "active_work_item_id": "probe-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, engine.snapshot)
    run_dir = paths.runs_dir / "run-recon"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "recon_packet.md").write_text(
        render_recon_packet(_recon_packet("probe-001", decision=ReconDecision.TO_PLANNING)),
        encoding="utf-8",
    )
    (run_dir / "generated_spec.md").write_text(
        _generated_probe_spec().model_dump_json(indent=2),
        encoding="utf-8",
    )
    stage_result = StageResultEnvelope(
        run_id="run-recon",
        plane="planning",
        stage="recon",
        node_id="recon",
        stage_kind_id="recon",
        work_item_kind="probe",
        work_item_id="probe-001",
        terminal_result=PlanningTerminalResult.RECON_TO_PLANNING,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### RECON_TO_PLANNING",
        success=True,
        artifact_paths=("recon_packet.md", "generated_spec.md"),
        started_at=NOW,
        completed_at=NOW,
    )
    decision = RouterDecision(action=RouterAction.IDLE, next_plane=None, next_stage=None, reason="recon_to_planning")

    spawned = apply_router_decision(engine, decision, stage_result)

    assert spawned == (paths.specs_queue_dir / "spec-from-probe.md",)
    assert (paths.probes_done_dir / "probe-001.md").is_file()
    assert (paths.recon_packets_dir / "recon-probe-001.md").is_file()
    spec = (paths.specs_queue_dir / "spec-from-probe.md").read_text(encoding="utf-8")
    assert "Source-Type: probe" in spec
    assert "Source-ID: probe-001" in spec
    assert "Root-Intake-Kind: probe" in spec
    assert "millrace-agents/recon/packets/recon-probe-001.md" in spec
