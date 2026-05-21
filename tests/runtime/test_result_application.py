from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.architecture import (
    ArtifactContractDefinition,
    ArtifactFilenameAdapterDefinition,
    ArtifactFormat,
)
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
    RecoveryCounters,
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
from millrace_ai.run_inspection import inspect_run_trace
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.compiled_plans import archive_compiled_plan
from millrace_ai.runtime.lanes import compiled_plan_fingerprint_for_runtime
from millrace_ai.runtime.result_application import apply_router_decision, route_stage_result
from millrace_ai.runtime.run_traces import record_router_decision_trace
from millrace_ai.runtime.supervisor import StageWorkerOutcome, apply_stage_completion
from millrace_ai.runtime.work_item_transitions import apply_idle_router_decision
from millrace_ai.state_store import load_recovery_counters, load_snapshot, save_recovery_counters, save_snapshot
from millrace_ai.workspace.work_documents import render_work_document

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


def _engine_with_active_recon_probe(paths, *, run_id: str = "run-recon") -> RuntimeEngine:
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
                        lane_id="planning.main",
                        stage=PlanningStageName.RECON,
                        node_id="recon",
                        stage_kind_id="recon",
                        run_id=run_id,
                        compiled_plan_id=engine.snapshot.compiled_plan_id,
                        compiled_plan_fingerprint=engine.snapshot.compiled_plan_fingerprint,
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
            "active_run_id": run_id,
            "active_work_item_kind": WorkItemKind.PROBE,
            "active_work_item_id": "probe-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, engine.snapshot)
    return engine


def _recon_stage_result(
    terminal_result: PlanningTerminalResult,
    *,
    artifact_paths: tuple[str, ...],
    run_id: str = "run-recon",
) -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id=run_id,
        plane="planning",
        stage="recon",
        node_id="recon",
        stage_kind_id="recon",
        work_item_kind="probe",
        work_item_id="probe-001",
        terminal_result=terminal_result,
        result_class=ResultClass.SUCCESS,
        summary_status_marker=f"### {terminal_result.value}",
        success=True,
        artifact_paths=artifact_paths,
        started_at=NOW,
        completed_at=NOW,
    )


def _idle_recon_decision() -> RouterDecision:
    return RouterDecision(action=RouterAction.IDLE, next_plane=None, next_stage=None, reason="recon")


def _prefer_recon_packet_json(engine: RuntimeEngine) -> None:
    assert engine.compiled_plan is not None
    current = engine.compiled_plan.artifact_contracts_by_id
    contract = ArtifactContractDefinition(
        artifact_id="recon_packet",
        canonical_filename="recon_packet.json",
        accepted_filenames=("recon_packet.md",),
        preferred_format=ArtifactFormat.JSON,
        schema_id="recon_packet_document_v1",
        filename_adapters=(
            ArtifactFilenameAdapterDefinition(
                filename="recon_packet.json",
                format=ArtifactFormat.JSON,
                parser_id="builtin.json",
                renderer_id="builtin.json",
            ),
            ArtifactFilenameAdapterDefinition(
                filename="recon_packet.md",
                format=ArtifactFormat.MARKDOWN,
                parser_id="builtin.markdown",
                renderer_id="builtin.markdown",
            ),
        ),
    )
    contracts_by_id = {**current, "recon_packet": contract}
    contracts = tuple(
        contracts_by_id[existing.artifact_id]
        for existing in engine.compiled_plan.artifact_contracts
    )
    engine.compiled_plan = engine.compiled_plan.model_copy(
        update={
            "artifact_contracts_by_id": contracts_by_id,
            "artifact_contracts": contracts,
        }
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
                        lane_id="execution.main",
                        stage=ExecutionStageName.BUILDER,
                        node_id="builder",
                        stage_kind_id="builder",
                        run_id="run-exec",
                        compiled_plan_id=engine.snapshot.compiled_plan_id,
                        compiled_plan_fingerprint=engine.snapshot.compiled_plan_fingerprint,
                        request_kind="active_work_item",
                        work_item_kind=WorkItemKind.TASK,
                        work_item_id="task-001",
                    active_since=NOW,
                ),
                    Plane.LEARNING: ActiveRunState(
                        plane=Plane.LEARNING,
                        lane_id="learning.main",
                        stage=LearningStageName.CURATOR,
                        node_id="curator",
                        stage_kind_id="curator",
                        run_id="run-learn",
                        compiled_plan_id=engine.snapshot.compiled_plan_id,
                        compiled_plan_fingerprint=engine.snapshot.compiled_plan_fingerprint,
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


def test_run_stage_recovery_counter_uses_family_id_without_legacy_kind(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="default_codex")
    engine.startup()
    assert engine.snapshot is not None
    assert engine.compiled_plan is not None
    active_run = ActiveRunState(
        plane=Plane.PLANNING,
        stage=PlanningStageName.PLANNER,
        node_id="planner",
        stage_kind_id="planner",
        run_id="run-custom",
        compiled_plan_id=engine.compiled_plan.compiled_plan_id,
        compiled_plan_fingerprint=compiled_plan_fingerprint_for_runtime(engine.compiled_plan),
        request_kind="active_work_item",
        work_item_family_id="custom_review",
        work_item_id="custom-001",
        active_since=NOW,
    )
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "active_runs_by_plane": {Plane.PLANNING: active_run},
            "updated_at": NOW,
        }
    )
    engine.counters = RecoveryCounters()
    save_snapshot(paths, engine.snapshot)
    save_recovery_counters(paths, engine.counters)
    stage_result = StageResultEnvelope(
        run_id="run-custom",
        plane=Plane.PLANNING,
        stage=PlanningStageName.PLANNER,
        node_id="planner",
        stage_kind_id="planner",
        work_item_family_id="custom_review",
        work_item_id="custom-001",
        terminal_result=PlanningTerminalResult.BLOCKED,
        result_class=ResultClass.BLOCKED,
        summary_status_marker="### BLOCKED",
        success=False,
        started_at=NOW,
        completed_at=NOW,
    )
    decision = RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=Plane.PLANNING,
        next_stage=PlanningStageName.MECHANIC,
        next_node_id="mechanic",
        next_stage_kind_id="mechanic",
        reason="custom_family_recovery",
        failure_class="custom_failure",
    )

    apply_router_decision(engine, decision, stage_result)

    counters = load_recovery_counters(paths)
    assert len(counters.entries) == 1
    entry = counters.entries[0]
    assert entry.work_item_family_id == "custom_review"
    assert entry.work_item_kind is None
    assert entry.work_item_id == "custom-001"
    assert entry.failure_class == "custom_failure"
    assert entry.mechanic_attempt_count == 1


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
                        lane_id="execution.main",
                        stage=ExecutionStageName.BUILDER,
                        node_id="builder",
                        stage_kind_id="builder",
                        run_id="run-exec",
                        compiled_plan_id=engine.snapshot.compiled_plan_id,
                        compiled_plan_fingerprint=engine.snapshot.compiled_plan_fingerprint,
                        request_kind="active_work_item",
                        work_item_kind=WorkItemKind.TASK,
                        work_item_id="task-001",
                    active_since=NOW,
                ),
                    Plane.LEARNING: ActiveRunState(
                        plane=Plane.LEARNING,
                        lane_id="learning.main",
                        stage=LearningStageName.CURATOR,
                        node_id="curator",
                        stage_kind_id="curator",
                        run_id="run-learn",
                        compiled_plan_id=engine.snapshot.compiled_plan_id,
                        compiled_plan_fingerprint=engine.snapshot.compiled_plan_fingerprint,
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
                        lane_id="planning.main",
                        stage=PlanningStageName.RECON,
                        node_id="recon",
                        stage_kind_id="recon",
                        run_id="run-recon",
                        compiled_plan_id=engine.snapshot.compiled_plan_id,
                        compiled_plan_fingerprint=engine.snapshot.compiled_plan_fingerprint,
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
        render_work_document(_generated_probe_task()),
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


def test_recon_to_execution_uses_declared_packet_and_generated_task_contracts(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    engine = _engine_with_active_recon_probe(paths)
    _prefer_recon_packet_json(engine)
    run_dir = paths.runs_dir / "run-recon"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "recon_packet.json").write_text(
        _recon_packet("probe-001", decision=ReconDecision.TO_EXECUTION).model_dump_json(indent=2),
        encoding="utf-8",
    )
    (run_dir / "recon_packet.md").write_text(
        "# Narrative\n\nThis is not a recon packet.\n",
        encoding="utf-8",
    )
    (run_dir / "generated_task.json").write_text(
        _generated_probe_task().model_dump_json(indent=2),
        encoding="utf-8",
    )
    (run_dir / "generated_task.md").write_text(
        "# Narrative\n\nThis is not a generated task.\n",
        encoding="utf-8",
    )
    stage_result = _recon_stage_result(
        PlanningTerminalResult.RECON_TO_EXECUTION,
        artifact_paths=("recon_packet.json", "generated_task.json"),
    )

    spawned = apply_router_decision(engine, _idle_recon_decision(), stage_result)

    assert spawned == (paths.tasks_queue_dir / "task-from-probe.md",)
    assert (paths.probes_done_dir / "probe-001.md").is_file()
    task = (paths.tasks_queue_dir / "task-from-probe.md").read_text(encoding="utf-8")
    assert "millrace-agents/recon/packets/recon-probe-001.md" in task


def test_recon_to_execution_uses_launch_plan_artifact_contracts(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    engine = _engine_with_active_recon_probe(paths)
    assert engine.compiled_plan is not None
    current_plan = engine.compiled_plan
    _prefer_recon_packet_json(engine)
    launch_plan = engine.compiled_plan.model_copy(update={"compiled_plan_id": "launch-recon-json"})
    archive_compiled_plan(paths, launch_plan)
    active_run = engine.snapshot.active_runs_by_plane[Plane.PLANNING]
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "active_runs_by_plane": {
                Plane.PLANNING: active_run.model_copy(
                    update={
                        "compiled_plan_id": launch_plan.compiled_plan_id,
                        "compiled_plan_fingerprint": compiled_plan_fingerprint_for_runtime(
                            launch_plan
                        ),
                    }
                )
            }
        }
    )
    engine.compiled_plan = current_plan
    save_snapshot(paths, engine.snapshot)
    run_dir = paths.runs_dir / "run-recon"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "recon_packet.json").write_text(
        _recon_packet("probe-001", decision=ReconDecision.TO_EXECUTION).model_dump_json(indent=2),
        encoding="utf-8",
    )
    (run_dir / "recon_packet.md").write_text(
        "# Narrative\n\nThis is not a recon packet.\n",
        encoding="utf-8",
    )
    (run_dir / "generated_task.json").write_text(
        _generated_probe_task().model_dump_json(indent=2),
        encoding="utf-8",
    )
    stage_result = _recon_stage_result(
        PlanningTerminalResult.RECON_TO_EXECUTION,
        artifact_paths=("recon_packet.json", "generated_task.json"),
    )

    spawned = apply_router_decision(engine, _idle_recon_decision(), stage_result)

    assert spawned == (paths.tasks_queue_dir / "task-from-probe.md",)
    assert (paths.probes_done_dir / "probe-001.md").is_file()


def test_supervisor_recon_completion_uses_worker_launch_plan_when_snapshot_drifts(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    engine = _engine_with_active_recon_probe(paths)
    assert engine.compiled_plan is not None
    current_plan = engine.compiled_plan
    _prefer_recon_packet_json(engine)
    launch_plan = engine.compiled_plan.model_copy(update={"compiled_plan_id": "launch-recon-json"})
    archive_compiled_plan(paths, launch_plan)
    active_run = engine.snapshot.active_runs_by_plane[Plane.PLANNING].model_copy(
        update={
            "compiled_plan_id": launch_plan.compiled_plan_id,
            "compiled_plan_fingerprint": compiled_plan_fingerprint_for_runtime(launch_plan),
        }
    )
    engine.snapshot = engine.snapshot.model_copy(
        update={"active_runs_by_plane": {Plane.PLANNING: active_run}}
    )
    engine.compiled_plan = launch_plan
    save_snapshot(paths, engine.snapshot)
    request = engine._build_stage_run_request(
        engine._stage_plan_for(Plane.PLANNING, PlanningStageName.RECON, node_id="recon")
    )
    run_dir = Path(request.run_dir)
    (run_dir / "recon_packet.json").write_text(
        _recon_packet("probe-001", decision=ReconDecision.TO_EXECUTION).model_dump_json(indent=2),
        encoding="utf-8",
    )
    (run_dir / "recon_packet.md").write_text(
        "# Narrative\n\nThis is not a recon packet.\n",
        encoding="utf-8",
    )
    (run_dir / "generated_task.json").write_text(
        _generated_probe_task().model_dump_json(indent=2),
        encoding="utf-8",
    )
    stdout_path = run_dir / "runner_stdout.txt"
    stdout_path.write_text("### RECON_TO_EXECUTION\n", encoding="utf-8")
    raw_result = RunnerRawResult(
        request_id=request.request_id,
        run_id=request.run_id,
        stage=request.stage,
        runner_name="test-runner",
        exit_kind="completed",
        exit_code=0,
        stdout_path=str(stdout_path),
        stderr_path=None,
        terminal_result_path=None,
        started_at=NOW,
        ended_at=NOW,
    )
    drifted_active_run = active_run.model_copy(
        update={
            "compiled_plan_id": current_plan.compiled_plan_id,
            "compiled_plan_fingerprint": compiled_plan_fingerprint_for_runtime(current_plan),
        }
    )
    engine.compiled_plan = current_plan
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "active_runs_by_plane": {Plane.PLANNING: drifted_active_run},
            "compiled_plan_id": current_plan.compiled_plan_id,
            "compiled_plan_fingerprint": compiled_plan_fingerprint_for_runtime(current_plan),
        }
    )
    save_snapshot(paths, engine.snapshot)
    outcome = StageWorkerOutcome(
        plane=Plane.PLANNING,
        lane_id=active_run.lane_id,
        run_id=request.run_id,
        active_run=active_run,
        request=request,
        started_at=NOW,
        completed_at=NOW,
        raw_result=raw_result,
    )

    completion = apply_stage_completion(engine, outcome=outcome)

    assert completion.router_decision.action is RouterAction.IDLE
    assert (paths.tasks_queue_dir / "task-from-probe.md").is_file()
    assert (paths.probes_done_dir / "probe-001.md").is_file()
    trace = inspect_run_trace(run_dir)
    assert trace.edges
    assert trace.edges[-1].spawned_work[0].family_id == "task"
    assert trace.edges[-1].spawned_work[0].path.endswith("task-from-probe.md")


def test_recon_to_execution_fails_malformed_canonical_generated_task_before_fallback(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    engine = _engine_with_active_recon_probe(paths)
    run_dir = paths.runs_dir / "run-recon"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "recon_packet.md").write_text(
        render_recon_packet(_recon_packet("probe-001", decision=ReconDecision.TO_EXECUTION)),
        encoding="utf-8",
    )
    (run_dir / "generated_task.json").write_text(
        '{"task_id": "task-from-probe"}',
        encoding="utf-8",
    )
    (run_dir / "generated_task.md").write_text(
        render_work_document(_generated_probe_task()),
        encoding="utf-8",
    )
    stage_result = _recon_stage_result(
        PlanningTerminalResult.RECON_TO_EXECUTION,
        artifact_paths=("recon_packet.md", "generated_task.json", "generated_task.md"),
    )

    spawned = apply_router_decision(engine, _idle_recon_decision(), stage_result)

    assert spawned == ()
    assert (paths.probes_blocked_dir / "probe-001.md").is_file()
    assert not (paths.tasks_queue_dir / "task-from-probe.md").exists()


def test_run_trace_marks_runtime_effect_recovery_edge(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    run_dir = paths.runs_dir / "run-effect-recovery"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)
    stage_result = StageResultEnvelope(
        run_id="run-effect-recovery",
        plane=Plane.PLANNING,
        stage=PlanningStageName.MANAGER,
        node_id="evaluator_blueprint",
        stage_kind_id="evaluator_blueprint",
        work_item_kind=WorkItemKind.BLUEPRINT_DRAFT,
        work_item_id="draft-blueprint-001",
        terminal_result=PlanningTerminalResult.BLUEPRINT_APPROVED,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BLUEPRINT_APPROVED",
        success=True,
        metadata={
            "request_id": "request-001",
            "runtime_effect_handler_id": "evaluator_blueprint_approved_to_task",
            "runtime_effect_decision": "request_block_source",
            "runtime_effect_failure_class": "generated_task_missing",
            "runtime_effect_failure_message": "generated_task.json is missing",
            "runtime_effect_mutation_phase": "pre_mutation",
        },
        started_at=NOW,
        completed_at=NOW,
    )
    (stage_results_dir / "request-001.json").write_text(
        stage_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    decision = RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=Plane.PLANNING,
        next_stage=PlanningStageName.MECHANIC,
        next_node_id="mechanic_blueprint",
        next_stage_kind_id="mechanic_blueprint",
        reason="runtime_effect_failure:evaluator_blueprint_approved_to_task:generated_task_missing",
        failure_class="generated_task_missing",
    )

    record_router_decision_trace(
        paths,
        run_dir=run_dir,
        stage_result=stage_result,
        decision=decision,
    )

    trace = inspect_run_trace(run_dir)
    assert trace.edges[-1].edge_kind == "runtime_effect_recovery"
    assert trace.edges[-1].target_node_id == "mechanic_blueprint"


def test_mechanic_blueprint_completion_resume_stage_metadata_routes_to_manager_blueprint(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_codex")
    engine.startup()
    assert engine.snapshot is not None
    assert engine.compiled_plan is not None
    active_run = ActiveRunState(
        plane=Plane.PLANNING,
        lane_id="planning.main",
        stage=PlanningStageName.MECHANIC,
        node_id="mechanic_blueprint",
        stage_kind_id="mechanic_blueprint",
        run_id="run-mechanic",
        compiled_plan_id=engine.compiled_plan.compiled_plan_id,
        compiled_plan_fingerprint=compiled_plan_fingerprint_for_runtime(engine.compiled_plan),
        request_kind="active_work_item",
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-001",
        active_since=NOW,
    )
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "active_runs_by_plane": {Plane.PLANNING: active_run},
            "active_plane": Plane.PLANNING,
            "active_stage": PlanningStageName.MECHANIC,
            "active_node_id": "mechanic_blueprint",
            "active_stage_kind_id": "mechanic_blueprint",
            "active_run_id": "run-mechanic",
            "active_work_item_kind": WorkItemKind.SPEC,
            "active_work_item_id": "spec-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, engine.snapshot)
    stage_result = StageResultEnvelope(
        run_id="run-mechanic",
        plane=Plane.PLANNING,
        stage=PlanningStageName.MECHANIC,
        node_id="mechanic_blueprint",
        stage_kind_id="mechanic_blueprint",
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-001",
        terminal_result=PlanningTerminalResult.MECHANIC_BLUEPRINT_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### MECHANIC_BLUEPRINT_COMPLETE",
        success=True,
        metadata={"resume_stage": "manager_blueprint"},
        started_at=NOW,
        completed_at=NOW,
    )

    decision = route_stage_result(engine, stage_result)
    apply_router_decision(engine, decision, stage_result)

    assert decision.action is RouterAction.RUN_STAGE
    assert decision.next_stage is PlanningStageName.MANAGER
    assert decision.next_node_id == "manager_blueprint"
    assert decision.next_stage_kind_id == "manager_blueprint"
    assert engine.snapshot.active_runs_by_plane[Plane.PLANNING].stage is PlanningStageName.MANAGER
    assert engine.snapshot.active_runs_by_plane[Plane.PLANNING].node_id == "manager_blueprint"


def test_recon_to_execution_fails_malformed_canonical_recon_packet_before_fallback(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    engine = _engine_with_active_recon_probe(paths)
    _prefer_recon_packet_json(engine)
    run_dir = paths.runs_dir / "run-recon"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "recon_packet.json").write_text(
        '{"recon_packet_id": "recon-probe-001"}',
        encoding="utf-8",
    )
    (run_dir / "recon_packet.md").write_text(
        render_recon_packet(_recon_packet("probe-001", decision=ReconDecision.TO_EXECUTION)),
        encoding="utf-8",
    )
    (run_dir / "generated_task.json").write_text(
        _generated_probe_task().model_dump_json(indent=2),
        encoding="utf-8",
    )
    stage_result = _recon_stage_result(
        PlanningTerminalResult.RECON_TO_EXECUTION,
        artifact_paths=("recon_packet.json", "recon_packet.md", "generated_task.json"),
    )

    spawned = apply_router_decision(engine, _idle_recon_decision(), stage_result)

    assert spawned == ()
    assert (paths.probes_blocked_dir / "probe-001.md").is_file()
    assert not (paths.tasks_queue_dir / "task-from-probe.md").exists()


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
                        lane_id="planning.main",
                        stage=PlanningStageName.RECON,
                        node_id="recon",
                        stage_kind_id="recon",
                        run_id="run-recon",
                        compiled_plan_id=engine.snapshot.compiled_plan_id,
                        compiled_plan_fingerprint=engine.snapshot.compiled_plan_fingerprint,
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
        render_work_document(_generated_probe_spec()),
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


def test_recon_to_planning_fails_malformed_canonical_generated_spec_before_fallback(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    engine = _engine_with_active_recon_probe(paths)
    run_dir = paths.runs_dir / "run-recon"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "recon_packet.md").write_text(
        render_recon_packet(_recon_packet("probe-001", decision=ReconDecision.TO_PLANNING)),
        encoding="utf-8",
    )
    (run_dir / "generated_spec.json").write_text(
        '{"spec_id": "spec-from-probe", "title": 12}',
        encoding="utf-8",
    )
    (run_dir / "generated_spec.md").write_text(
        render_work_document(_generated_probe_spec()),
        encoding="utf-8",
    )
    stage_result = _recon_stage_result(
        PlanningTerminalResult.RECON_TO_PLANNING,
        artifact_paths=("recon_packet.md", "generated_spec.json", "generated_spec.md"),
    )

    spawned = apply_router_decision(engine, _idle_recon_decision(), stage_result)

    assert spawned == ()
    assert (paths.probes_blocked_dir / "probe-001.md").is_file()
    assert not (paths.specs_queue_dir / "spec-from-probe.md").exists()
