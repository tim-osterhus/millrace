from __future__ import annotations

import importlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from millrace_ai.architecture import WorkItemFamilyDefinition
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    ActiveRunState,
    ClosureTargetState,
    ExecutionStageName,
    ExecutionTerminalResult,
    IncidentDocument,
    LearningRequestDocument,
    LearningStageName,
    LearningTerminalResult,
    MailboxCommandEnvelope,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    ProbeDocument,
    ReconConfidence,
    ReconDecision,
    ReconPacketDocument,
    ReconPathFinding,
    ReconRiskLevel,
    RecoveryCounterEntry,
    RecoveryCounters,
    ResultClass,
    RuntimeMode,
    RuntimeSnapshot,
    SpecDocument,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.control import RuntimeControl
from millrace_ai.errors import ControlRoutingError, RuntimeLifecycleError
from millrace_ai.events import read_runtime_events
from millrace_ai.mailbox import read_pending_mailbox_commands, write_mailbox_command
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.recon_packets import render_recon_packet
from millrace_ai.router import RouterAction
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.watcher_intake import safe_spec_id_from_idea_path
from millrace_ai.runtime_lock import (
    RuntimeOwnershipLockError,
    acquire_runtime_ownership_lock,
    inspect_runtime_ownership_lock,
    release_runtime_ownership_lock,
)
from millrace_ai.state_store import (
    load_execution_status,
    load_planning_status,
    load_recovery_counters,
    load_snapshot,
    save_recovery_counters,
    save_snapshot,
    set_execution_status,
    set_planning_status,
)
from millrace_ai.watchers import WatcherMode, WatchEvent
from millrace_ai.work_documents import read_work_document_as
from millrace_ai.workspace.arbiter_state import load_closure_target_state, save_closure_target_state

NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _write_runtime_error_catalog(root: Path) -> Path:
    catalog_path = root / "docs" / "runtime" / "millrace-runtime-error-codes.md"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text("# Runtime Error Codes\n", encoding="utf-8")
    return catalog_path


def test_runtime_import_surface_moves_to_package_directory() -> None:
    runtime_module = importlib.import_module("millrace_ai.runtime")

    assert Path(runtime_module.__file__).as_posix().endswith("/runtime/__init__.py")


def _task_doc(task_id: str, *, created_at: datetime) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="runtime test task",
        target_paths=["millrace/runtime.py"],
        acceptance=["runtime stage sequence is deterministic"],
        required_checks=["uv run pytest tests/runtime/test_runtime.py -q"],
        references=["lab/specs/drafts/millrace-runtime-module-and-cli-plan.md"],
        risk=["runtime drift"],
        created_at=created_at,
        created_by="tests",
    )


def _spec_doc(spec_id: str, *, created_at: datetime) -> SpecDocument:
    return SpecDocument(
        spec_id=spec_id,
        title=f"Spec {spec_id}",
        summary="runtime planning input",
        source_type="manual",
        goals=["prove planning runs before execution"],
        constraints=["deterministic selection"],
        acceptance=["planning stage runs first"],
        references=["lab/specs/drafts/millrace-agent-topology-and-transition-table.md"],
        created_at=created_at,
        created_by="tests",
    )


def _custom_planning_family() -> WorkItemFamilyDefinition:
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
        operator_capabilities=("cancel", "inspect"),
    )


def _persist_custom_family(paths, family: WorkItemFamilyDefinition) -> None:
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.active_plan is not None
    updated = outcome.active_plan.model_copy(
        update={
            "work_item_families_by_id": {
                **outcome.active_plan.work_item_families_by_id,
                family.family_id: family,
            }
        }
    )
    (paths.state_dir / "compiled_plan.json").write_text(
        updated.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def _probe_doc(probe_id: str, *, created_at: datetime) -> ProbeDocument:
    return ProbeDocument(
        probe_id=probe_id,
        title=f"Probe {probe_id}",
        summary="runtime probe input",
        request="Research the codebase before routing this work.",
        target_paths=("src/example.py",),
        acceptance=("Recon routes the probe.",),
        created_at=created_at,
        created_by="tests",
    )


def _recon_packet(
    probe_id: str,
    *,
    decision: ReconDecision,
    emitted_task_id: str | None = None,
    emitted_spec_id: str | None = None,
) -> ReconPacketDocument:
    return ReconPacketDocument(
        recon_packet_id=f"recon-{probe_id}",
        probe_id=probe_id,
        decision=decision,
        confidence=ReconConfidence.HIGH,
        risk_level=ReconRiskLevel.MEDIUM,
        request_summary="Research before routing.",
        interpreted_goal="Route this work through the smallest safe lane.",
        relevant_paths=(ReconPathFinding(path="src/example.py", reason="Likely behavior owner."),),
        semantic_invariants=("Preserve adjacent behavior.",),
        handoff_target="execution" if decision is ReconDecision.TO_EXECUTION else "planning",
        emitted_task_id=emitted_task_id,
        emitted_spec_id=emitted_spec_id,
        created_at=NOW,
    )


def _generated_probe_spec(spec_id: str = "spec-from-probe") -> SpecDocument:
    return SpecDocument(
        spec_id=spec_id,
        title="Spec from probe",
        summary="planning route",
        source_type="probe",
        source_id="probe-001",
        root_intake_kind="probe",
        root_intake_id="probe-001",
        root_spec_id=spec_id,
        goals=("Plan the probe-derived change.",),
        constraints=("Use the recon packet as required context.",),
        acceptance=("Manager can decompose the spec.",),
        references=("millrace-agents/probes/active/probe-001.md",),
        created_at=NOW,
        created_by="recon",
    )


def _closure_target_state(
    *,
    root_spec_id: str = "spec-root-001",
    root_idea_id: str = "idea-001",
) -> ClosureTargetState:
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


def _mailbox_command(
    command_id: str,
    command: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "command_id": command_id,
        "command": command,
        "issued_at": NOW,
        "issuer": "tests",
        "payload": payload or {},
    }


def _runner_result(
    request: StageRunRequest,
    *,
    terminal: str | None,
    now: datetime,
    exit_kind: str = "completed",
    exit_code: int = 0,
    observed_exit_kind: str | None = None,
    observed_exit_code: int | None = None,
) -> RunnerRawResult:
    run_dir = Path(request.run_dir)
    stdout_path = run_dir / "runner_stdout.txt"
    stdout_payload = "no terminal token\n" if terminal is None else f"### {terminal}\n"
    stdout_path.write_text(stdout_payload, encoding="utf-8")
    _write_default_planner_disposition(request, terminal=terminal, run_dir=run_dir, now=now)

    return RunnerRawResult(
        request_id=request.request_id,
        run_id=request.run_id,
        stage=request.stage,
        runner_name=request.runner_name or "test-runner",
        model_name=request.model_name,
        exit_kind=exit_kind,
        exit_code=exit_code,
        stdout_path=str(stdout_path),
        stderr_path=None,
        terminal_result_path=None,
        observed_exit_kind=observed_exit_kind,
        observed_exit_code=observed_exit_code,
        started_at=now,
        ended_at=now + timedelta(seconds=1),
    )


def _write_default_planner_disposition(
    request: StageRunRequest,
    *,
    terminal: str | None,
    run_dir: Path,
    now: datetime,
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
                "created_at": now.isoformat().replace("+00:00", "Z"),
                "created_by": "planner",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _renamed_compiled_node(
    compiled_plan,
    *,
    plane: Plane,
    old_node_id: str,
    new_node_id: str,
):
    graph_attr = (
        "execution_graph"
        if plane is Plane.EXECUTION
        else "learning_graph"
        if plane is Plane.LEARNING
        else "planning_graph"
    )
    graph = getattr(compiled_plan, graph_attr)
    assert graph is not None

    def rename(node_id: str | None) -> str | None:
        if node_id == old_node_id:
            return new_node_id
        return node_id

    updated_graph = graph.model_copy(
        update={
            "nodes": tuple(
                node.model_copy(update={"node_id": new_node_id})
                if node.node_id == old_node_id
                else node
                for node in graph.nodes
            ),
            "compiled_entries": tuple(
                entry.model_copy(update={"node_id": new_node_id})
                if entry.node_id == old_node_id
                else entry
                for entry in graph.compiled_entries
            ),
            "compiled_completion_entry": (
                graph.compiled_completion_entry.model_copy(update={"node_id": new_node_id})
                if graph.compiled_completion_entry is not None
                and graph.compiled_completion_entry.node_id == old_node_id
                else graph.compiled_completion_entry
            ),
            "compiled_transitions": tuple(
                transition.model_copy(
                    update={
                        "source_node_id": rename(transition.source_node_id),
                        "target_node_id": rename(transition.target_node_id),
                    }
                )
                if transition.source_node_id == old_node_id or transition.target_node_id == old_node_id
                else transition
                for transition in graph.compiled_transitions
            ),
            "compiled_resume_policies": tuple(
                policy.model_copy(
                    update={
                        "source_node_id": rename(policy.source_node_id),
                        "default_target_node_id": rename(policy.default_target_node_id),
                        "disallowed_target_node_ids": tuple(
                            rename(node_id) or node_id for node_id in policy.disallowed_target_node_ids
                        ),
                    }
                )
                if (
                    policy.source_node_id == old_node_id
                    or policy.default_target_node_id == old_node_id
                    or old_node_id in policy.disallowed_target_node_ids
                )
                else policy
                for policy in graph.compiled_resume_policies
            ),
            "compiled_threshold_policies": tuple(
                policy.model_copy(
                    update={
                        "source_node_ids": tuple(rename(node_id) or node_id for node_id in policy.source_node_ids),
                        "exhausted_target_node_id": rename(policy.exhausted_target_node_id),
                    }
                )
                if old_node_id in policy.source_node_ids or policy.exhausted_target_node_id == old_node_id
                else policy
                for policy in graph.compiled_threshold_policies
            ),
        }
    )
    graphs_by_plane = dict(compiled_plan.graphs_by_plane)
    graphs_by_plane[plane] = updated_graph
    return compiled_plan.model_copy(update={graph_attr: updated_graph, "graphs_by_plane": graphs_by_plane})


def test_runtime_tick_prioritizes_planning_before_execution(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW + timedelta(minutes=2)))
    queue.enqueue_spec(_spec_doc("spec-001", created_at=NOW))

    seen_stages: list[str] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        seen_stages.append(request.stage.value)
        terminal_by_stage = {
            "planner": PlanningTerminalResult.PLANNER_COMPLETE.value,
            "manager": PlanningTerminalResult.MANAGER_COMPLETE.value,
            "builder": ExecutionTerminalResult.BUILDER_COMPLETE.value,
        }
        return _runner_result(
            request,
            terminal=terminal_by_stage.get(request.stage.value),
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    first = engine.tick()
    second = engine.tick()
    third = engine.tick()

    assert first.stage == PlanningStageName.PLANNER
    assert second.stage == PlanningStageName.MANAGER
    assert third.stage == ExecutionStageName.BUILDER
    assert seen_stages[:3] == ["planner", "manager", "builder"]

    assert (paths.specs_done_dir / "spec-001.md").is_file()
    assert (paths.tasks_active_dir / "task-001.md").is_file()


def test_runtime_tick_claim_activation_uses_compiled_plan_authority(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    captured_request: StageRunRequest | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal captured_request
        captured_request = request
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.CHECKER_PASS.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None
    task_entry = next(
        entry
        for entry in engine.compiled_plan.execution_graph.compiled_entries
        if entry.entry_key.value == "task"
    )
    engine.compiled_plan = engine.compiled_plan.model_copy(
        update={
            "execution_graph": engine.compiled_plan.execution_graph.model_copy(
                update={
                    "compiled_entries": tuple(
                        entry.model_copy(
                            update={
                                "node_id": "checker",
                                "stage_kind_id": "checker",
                            }
                        )
                        if entry == task_entry
                        else entry
                        for entry in engine.compiled_plan.execution_graph.compiled_entries
                    )
                }
            )
        }
    )

    outcome = engine.tick()
    snapshot = load_snapshot(paths)

    assert captured_request is not None
    assert captured_request.stage is ExecutionStageName.CHECKER
    assert captured_request.node_id == "checker"
    assert captured_request.stage_kind_id == "checker"
    assert outcome.stage is ExecutionStageName.CHECKER
    assert outcome.router_decision.next_stage is ExecutionStageName.UPDATER
    assert outcome.router_decision.next_node_id == "updater"
    assert outcome.router_decision.next_stage_kind_id == "updater"
    assert snapshot.active_stage is ExecutionStageName.UPDATER
    assert snapshot.active_node_id == "updater"
    assert snapshot.active_stage_kind_id == "updater"


def test_runtime_tick_routes_missing_compiled_planning_queue_claim_policy_to_recovery(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    QueueStore(paths).enqueue_spec(_spec_doc("spec-001", created_at=NOW))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError(f"stage runner should not be called: {request}")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    assert engine.compiled_plan is not None
    engine.compiled_plan = engine.compiled_plan.model_copy(
        update={
            "queue_claim_policies_by_plane": {
                plane: policy
                for plane, policy in engine.compiled_plan.queue_claim_policies_by_plane.items()
                if plane is not Plane.PLANNING
            }
        }
    )

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
    assert context["work_item_id"] == "runtime-pre-dispatch"
    assert "compiled plan missing planning queue claim policy" in context["exception_message"]
    assert any(
        event.event_type == "runtime_pre_dispatch_recovery_scheduled"
        and event.data.get("error_code") == "planning_pre_dispatch_failed"
        for event in events
    )


def test_learning_mode_stage_requests_persist_skill_revision_evidence(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    captured_request: StageRunRequest | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal captured_request
        captured_request = request
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="learning_codex")
    engine.startup()
    engine.tick()

    assert captured_request is not None
    assert captured_request.skill_revision_evidence_path is not None
    evidence_path = Path(captured_request.skill_revision_evidence_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    assert evidence["kind"] == "skill_revision_evidence"
    assert evidence["mode_id"] == "learning_codex"
    assert evidence["request_id"] == captured_request.request_id
    assert evidence["skills"]
    assert {skill["path"] for skill in evidence["skills"]} >= set(
        captured_request.required_skill_paths
    )
    assert all(skill["sha256"] for skill in evidence["skills"])


def test_learning_stage_request_uses_learning_request_kind_and_per_request_evidence(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_learning_request(
        LearningRequestDocument(
            learning_request_id="learn-001",
            title="Improve checker skill",
            requested_action="improve",
            target_skill_id="checker-core",
            created_at=NOW,
            created_by="tests",
        )
    )
    captured_requests: list[StageRunRequest] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        captured_requests.append(request)
        terminal_by_stage = {
            LearningStageName.ANALYST: LearningTerminalResult.ANALYST_COMPLETE.value,
            LearningStageName.PROFESSOR: LearningTerminalResult.PROFESSOR_COMPLETE.value,
        }
        return _runner_result(
            request,
            terminal=terminal_by_stage[request.stage],
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="learning_codex")
    engine.startup()
    first = engine.tick()
    second = engine.tick()

    assert first.stage is LearningStageName.ANALYST
    assert second.stage is LearningStageName.PROFESSOR
    assert [request.request_kind for request in captured_requests] == [
        "learning_request",
        "learning_request",
    ]
    evidence_paths = [Path(request.skill_revision_evidence_path) for request in captured_requests]
    assert len(set(evidence_paths)) == 2
    assert all(path.is_file() for path in evidence_paths)
    for request, evidence_path in zip(captured_requests, evidence_paths, strict=True):
        assert request.request_id in evidence_path.name
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        assert evidence["request_id"] == request.request_id


def test_learning_mode_execution_trigger_enqueues_analyst_first_learning_request(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    seen_stages: list[str] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        seen_stages.append(request.stage.value)
        terminal_by_stage = {
            ExecutionStageName.BUILDER: ExecutionTerminalResult.BUILDER_COMPLETE.value,
            ExecutionStageName.CHECKER: ExecutionTerminalResult.FIX_NEEDED.value,
            ExecutionStageName.FIXER: ExecutionTerminalResult.FIXER_COMPLETE.value,
            ExecutionStageName.DOUBLECHECKER: ExecutionTerminalResult.DOUBLECHECK_PASS.value,
        }
        return _runner_result(
            request,
            terminal=terminal_by_stage[request.stage],
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="learning_codex")
    engine.startup()

    for _ in range(4):
        engine.tick()

    queued_requests = tuple(paths.learning_requests_queue_dir.glob("*.md"))
    assert seen_stages == ["builder", "checker", "fixer", "doublechecker"]
    assert len(queued_requests) == 1
    doc = read_work_document_as(queued_requests[0], model=LearningRequestDocument)
    assert doc.requested_action == "improve"
    assert doc.target_stage is LearningStageName.ANALYST
    assert doc.target_skill_id is None
    assert doc.preferred_output_paths == ()
    assert doc.originating_run_ids == (engine.snapshot.active_run_id,)
    assert doc.trigger_metadata["rule_id"] == "execution.doublechecker.success-to-analyst"
    assert doc.trigger_metadata["source_stage"] == "doublechecker"
    assert doc.trigger_metadata["terminal_result"] == "DOUBLECHECK_PASS"


def test_learning_mode_planner_complete_triggers_librarian_request_with_planner_artifacts(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec_doc("spec-001", created_at=NOW))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        assert request.stage is PlanningStageName.PLANNER
        run_dir = Path(request.run_dir)
        planner_summary = run_dir / "planner_summary.md"
        planner_summary.write_text(
            "# Planner Summary\n\nGenerated or refined spec paths:\n- millrace-agents/specs/active/spec-001.md\n",
            encoding="utf-8",
        )
        terminal_path = run_dir / "stage_terminal_result.json"
        terminal_path.write_text(
            json.dumps(
                {
                    "stage": "planner",
                    "terminal_result": "PLANNER_COMPLETE",
                    "result_class": "success",
                    "summary_artifact_paths": ["planner_summary.md"],
                }
            ),
            encoding="utf-8",
        )
        stdout_path = run_dir / "runner_stdout.txt"
        stdout_path.write_text("### PLANNER_COMPLETE\n", encoding="utf-8")
        _write_default_planner_disposition(
            request,
            terminal=PlanningTerminalResult.PLANNER_COMPLETE.value,
            run_dir=run_dir,
            now=NOW,
        )
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
            terminal_result_path=str(terminal_path),
            started_at=NOW,
            ended_at=NOW + timedelta(seconds=1),
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="learning_codex")
    engine.startup()
    outcome = engine.tick()

    queued_requests = tuple(paths.learning_requests_queue_dir.glob("*.md"))
    assert outcome.stage is PlanningStageName.PLANNER
    assert len(queued_requests) == 1
    doc = read_work_document_as(queued_requests[0], model=LearningRequestDocument)
    assert doc.requested_action == "install"
    assert doc.target_stage is LearningStageName.LIBRARIAN
    assert doc.trigger_metadata["rule_id"] == "planning.planner.complete-to-librarian"
    assert doc.trigger_metadata["source_stage"] == "planner"
    assert doc.trigger_metadata["source_work_item_kind"] == "spec"
    assert doc.trigger_metadata["source_work_item_id"] == "spec-001"
    assert doc.trigger_metadata["source_active_work_item_path"].endswith(
        "millrace-agents/specs/active/spec-001.md"
    )
    assert any("/stage_results/" in path and path.endswith(".json") for path in doc.artifact_paths)
    assert any(path.endswith("planner_summary.md") for path in doc.artifact_paths)


def test_runtime_generated_learning_request_copies_trigger_destination_metadata(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    mode_path = paths.runtime_root / "modes" / "learning_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["learning_trigger_rules"] = [
        {
            "rule_id": "execution.doublechecker.precise-to-curator",
            "source_plane": "execution",
            "source_stage": "doublechecker",
            "on_terminal_results": ["DOUBLECHECK_PASS"],
            "target_stage": "curator",
            "requested_action": "improve",
            "target_skill_id": "doublechecker-core",
            "preferred_output_paths": [
                "millrace-agents/skills/stage/execution/doublechecker-core/SKILL.md",
            ],
        }
    ]
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        terminal_by_stage = {
            ExecutionStageName.BUILDER: ExecutionTerminalResult.BUILDER_COMPLETE.value,
            ExecutionStageName.CHECKER: ExecutionTerminalResult.FIX_NEEDED.value,
            ExecutionStageName.FIXER: ExecutionTerminalResult.FIXER_COMPLETE.value,
            ExecutionStageName.DOUBLECHECKER: ExecutionTerminalResult.DOUBLECHECK_PASS.value,
        }
        return _runner_result(
            request,
            terminal=terminal_by_stage[request.stage],
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="learning_codex")
    engine.startup()

    for _ in range(4):
        engine.tick()

    queued_requests = tuple(paths.learning_requests_queue_dir.glob("*.md"))
    assert len(queued_requests) == 1
    doc = read_work_document_as(queued_requests[0], model=LearningRequestDocument)
    assert doc.target_stage is LearningStageName.CURATOR
    assert doc.target_skill_id == "doublechecker-core"
    assert doc.preferred_output_paths == (
        "millrace-agents/skills/stage/execution/doublechecker-core/SKILL.md",
    )


def test_targeted_learning_request_activates_requested_stage(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_learning_request(
        LearningRequestDocument(
            learning_request_id="learn-001",
            title="Curate checker skill",
            requested_action="improve",
            target_skill_id="checker-core",
            target_stage="curator",
            created_at=NOW,
            created_by="tests",
        )
    )
    captured_request: StageRunRequest | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal captured_request
        captured_request = request
        return _runner_result(
            request,
            terminal=LearningTerminalResult.CURATOR_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="learning_codex")
    engine.startup()
    outcome = engine.tick()

    assert captured_request is not None
    assert captured_request.stage is LearningStageName.CURATOR
    assert captured_request.request_kind == "learning_request"
    assert outcome.router_decision.reason == "curator:CURATOR_COMPLETE"
    assert (paths.learning_requests_done_dir / "learn-001.md").is_file()


def test_targeted_librarian_request_activates_librarian_stage(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_learning_request(
        LearningRequestDocument(
            learning_request_id="learn-librarian-001",
            title="Install relevant remote skills",
            requested_action="install",
            target_stage="librarian",
            artifact_paths=("millrace-agents/runs/run-planner/stage_results/planner.json",),
            created_at=NOW,
            created_by="tests",
        )
    )
    captured_request: StageRunRequest | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal captured_request
        captured_request = request
        return _runner_result(
            request,
            terminal=LearningTerminalResult.LIBRARIAN_NOOP.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="learning_codex")
    engine.startup()
    outcome = engine.tick()

    assert captured_request is not None
    assert captured_request.stage is LearningStageName.LIBRARIAN
    assert captured_request.request_kind == "learning_request"
    assert outcome.router_decision.reason == "librarian:LIBRARIAN_NOOP"
    assert (paths.learning_requests_done_dir / "learn-librarian-001.md").is_file()


def test_learning_noop_terminal_marks_request_done(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_learning_request(
        LearningRequestDocument(
            learning_request_id="learn-001",
            title="Review recovered incident",
            requested_action="improve",
            target_stage="analyst",
            created_at=NOW,
            created_by="tests",
        )
    )

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=LearningTerminalResult.ANALYST_NOOP.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="learning_codex")
    engine.startup()
    outcome = engine.tick()

    assert outcome.stage is LearningStageName.ANALYST
    assert outcome.stage_result is not None
    assert outcome.stage_result.terminal_result is LearningTerminalResult.ANALYST_NOOP
    assert outcome.stage_result.result_class is ResultClass.NO_OP
    assert outcome.stage_result.success is False
    assert outcome.router_decision.action is RouterAction.IDLE
    assert outcome.router_decision.reason == "analyst:ANALYST_NOOP"
    assert (paths.learning_requests_done_dir / "learn-001.md").is_file()
    assert not (paths.learning_requests_blocked_dir / "learn-001.md").exists()


def test_runtime_tick_completion_activation_uses_compiled_plan_authority(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id="spec-root-001",
            root_idea_id="idea-001",
            root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
            root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
            rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
            latest_verdict_path=None,
            latest_report_path=None,
            closure_open=True,
            closure_blocked_by_lineage_work=False,
            blocking_work_ids=(),
            opened_at=NOW,
        ),
    )
    captured_request: StageRunRequest | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal captured_request
        captured_request = request
        verdict_path = Path(request.preferred_verdict_path)
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        verdict_path.write_text('{"status":"pass"}\n', encoding="utf-8")
        report_path = Path(request.preferred_report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("# Arbiter Report\n\nParity holds.\n", encoding="utf-8")
        return _runner_result(
            request,
            terminal=(
                PlanningTerminalResult.PLANNER_COMPLETE.value
                if request.stage is PlanningStageName.PLANNER
                else PlanningTerminalResult.ARBITER_COMPLETE.value
            ),
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None
    engine.compiled_plan = engine.compiled_plan.model_copy(
        update={
            "planning_graph": engine.compiled_plan.planning_graph.model_copy(
                update={
                    "nodes": tuple(
                        node.model_copy(
                            update={
                                "entrypoint_path": "entrypoints/planning/mechanic.md",
                                "entrypoint_contract_id": "arbiter.contract.v999",
                                "required_skill_paths": ("skills/custom-arbiter.md",),
                                "attached_skill_additions": ("skills/custom-audit-pass.md",),
                                "runner_name": "custom_runner",
                                "model_name": "custom-model",
                                "timeout_seconds": 47,
                            }
                        )
                        if node.node_id == "arbiter"
                        else node
                        for node in engine.compiled_plan.planning_graph.nodes
                    )
                }
            )
        }
    )

    outcome = engine.tick()

    assert captured_request is not None
    assert captured_request.stage is PlanningStageName.ARBITER
    assert captured_request.entrypoint_path.endswith("entrypoints/planning/mechanic.md")
    assert captured_request.entrypoint_contract_id == "arbiter.contract.v999"
    assert captured_request.required_skill_paths == (
        str(paths.runtime_root / "skills/custom-arbiter.md"),
    )
    assert captured_request.attached_skill_paths == (
        str(paths.runtime_root / "skills/custom-audit-pass.md"),
    )
    assert captured_request.runner_name == "custom_runner"
    assert captured_request.model_name == "custom-model"
    assert captured_request.timeout_seconds == 47
    assert outcome.stage is PlanningStageName.ARBITER
    assert outcome.router_decision.reason == "arbiter_complete"

def test_runtime_tick_routes_stage_results_from_compiled_plan_authority(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        assert request.stage is ExecutionStageName.BUILDER
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None
    builder_complete = next(
        transition
        for transition in engine.compiled_plan.execution_graph.compiled_transitions
        if transition.source_node_id == "builder"
        and transition.outcome == ExecutionTerminalResult.BUILDER_COMPLETE.value
    )
    engine.compiled_plan = engine.compiled_plan.model_copy(
        update={
            "execution_graph": engine.compiled_plan.execution_graph.model_copy(
                update={
                    "compiled_transitions": tuple(
                        transition.model_copy(update={"target_node_id": "updater"})
                        if transition == builder_complete
                        else transition
                        for transition in engine.compiled_plan.execution_graph.compiled_transitions
                    )
                }
            )
        }
    )

    outcome = engine.tick()
    snapshot = load_snapshot(paths)

    assert outcome.stage is ExecutionStageName.BUILDER
    assert outcome.router_decision.next_stage is ExecutionStageName.UPDATER
    assert snapshot.active_stage is ExecutionStageName.UPDATER


def test_runtime_snapshot_queue_depths_match_filesystem_after_tick(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    engine.tick()

    snapshot = load_snapshot(paths)
    execution_queue_depth = len(tuple(paths.tasks_queue_dir.glob("*.md")))
    planning_queue_depth = len(tuple(paths.specs_queue_dir.glob("*.md"))) + len(
        tuple(paths.incidents_incoming_dir.glob("*.md"))
    )
    assert snapshot.queue_depth_execution == execution_queue_depth
    assert snapshot.queue_depth_planning == planning_queue_depth


def test_runtime_advances_after_reconciled_builder_timeout(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    seen_stages: list[str] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        seen_stages.append(request.stage.value)
        if request.stage is ExecutionStageName.BUILDER:
            return _runner_result(
                request,
                terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
                now=NOW,
                exit_kind="completed",
                exit_code=0,
                observed_exit_kind="timeout",
                observed_exit_code=124,
            )
        if request.stage is ExecutionStageName.CHECKER:
            return _runner_result(
                request,
                terminal=ExecutionTerminalResult.CHECKER_PASS.value,
                now=NOW,
            )
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.UPDATE_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    first = engine.tick()
    snapshot = load_snapshot(paths)

    assert first.stage == ExecutionStageName.BUILDER
    assert first.stage_result.terminal_result == ExecutionTerminalResult.BUILDER_COMPLETE
    assert first.router_decision.action is RouterAction.RUN_STAGE
    assert first.router_decision.next_stage == ExecutionStageName.CHECKER
    assert snapshot.active_stage == ExecutionStageName.CHECKER
    assert snapshot.current_failure_class is None
    assert seen_stages == ["builder"]


def test_runtime_writes_snapshot_status_events_and_stage_result_artifacts(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    stage_results = {
        "builder": ExecutionTerminalResult.BUILDER_COMPLETE.value,
        "checker": ExecutionTerminalResult.CHECKER_PASS.value,
        "updater": ExecutionTerminalResult.UPDATE_COMPLETE.value,
    }

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(request, terminal=stage_results.get(request.stage.value), now=NOW)

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    first = engine.tick()
    second = engine.tick()
    third = engine.tick()

    for outcome in (first, second, third):
        assert outcome.stage_result_path is not None
        assert outcome.stage_result_path.is_file()
        payload = json.loads(outcome.stage_result_path.read_text(encoding="utf-8"))
        assert payload["kind"] == "stage_result"

    snapshot = load_snapshot(paths)
    assert snapshot.active_stage is None
    assert snapshot.active_plane is None
    assert snapshot.last_terminal_result == ExecutionTerminalResult.UPDATE_COMPLETE
    assert snapshot.last_stage_result_path == str(third.stage_result_path.relative_to(paths.root))

    assert load_execution_status(paths) == "### IDLE"
    assert (paths.tasks_done_dir / "task-001.md").is_file()

    events = read_runtime_events(paths)
    event_types = [event.event_type for event in events]
    assert "runtime_started" in event_types
    assert "stage_started" in event_types
    assert "stage_completed" in event_types
    assert "router_decision" in event_types


def test_runtime_stage_events_surface_failure_class_and_troubleshoot_report_path(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    captured_request: StageRunRequest | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal captured_request
        captured_request = request
        report_path = Path(request.preferred_troubleshoot_report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("# Troubleshoot\n", encoding="utf-8")
        return _runner_result(
            request,
            terminal=None,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    outcome = engine.tick()
    events = read_runtime_events(paths)

    stage_started = next(event for event in events if event.event_type == "stage_started")
    stage_completed = next(event for event in events if event.event_type == "stage_completed")
    router_decision = next(event for event in events if event.event_type == "router_decision")

    assert captured_request is not None
    assert stage_started.data["run_id"] == captured_request.run_id
    assert stage_started.data["work_item_id"] == "task-001"
    assert stage_started.data["troubleshoot_report_path"] == captured_request.preferred_troubleshoot_report_path
    assert stage_completed.data["failure_class"] == "missing_terminal_result"
    assert stage_completed.data["troubleshoot_report_path"] == captured_request.preferred_troubleshoot_report_path
    assert router_decision.data["failure_class"] == "missing_terminal_result"
    assert outcome.stage_result.report_artifact == captured_request.preferred_troubleshoot_report_path


def test_runtime_single_tick_emits_stage_events_in_order(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    captured_request: StageRunRequest | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal captured_request
        captured_request = request
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    outcome = engine.tick()
    events = read_runtime_events(paths)
    event_types = [event.event_type for event in events]

    assert captured_request is not None
    assert outcome.stage is ExecutionStageName.BUILDER
    runtime_started_index = event_types.index("runtime_started")
    stage_started_index = event_types.index("stage_started")
    stage_completed_index = event_types.index("stage_completed")
    router_decision_index = event_types.index("router_decision")

    assert runtime_started_index < stage_started_index < stage_completed_index < router_decision_index
    assert events[stage_started_index].data["run_id"] == captured_request.run_id
    assert events[stage_started_index].data["stage"] == "builder"
    assert (
        events[stage_completed_index].data["terminal_result"]
        == ExecutionTerminalResult.BUILDER_COMPLETE.value
    )
    assert events[router_decision_index].data["action"] == "run_stage"
    assert events[router_decision_index].data["next_stage"] == "checker"


def test_runtime_stage_request_entrypoint_path_exists_after_startup(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    captured_request: StageRunRequest | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal captured_request
        captured_request = request
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    engine.tick()

    assert captured_request is not None
    assert Path(captured_request.entrypoint_path).is_file()
    assert captured_request.active_work_item_path is not None
    assert captured_request.active_work_item_path.endswith(".md")


def test_runtime_stage_request_uses_compiled_node_binding(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    captured_request: StageRunRequest | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal captured_request
        captured_request = request
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    assert engine.compiled_plan is not None

    engine.compiled_plan = engine.compiled_plan.model_copy(
        update={
            "execution_graph": engine.compiled_plan.execution_graph.model_copy(
                update={
                    "nodes": tuple(
                        stage_plan.model_copy(
                            update={
                                "entrypoint_path": "entrypoints/execution/consultant.md",
                                "entrypoint_contract_id": "compat-builder.contract.v999",
                                "required_skill_paths": ("skills/compat-only.md",),
                                "attached_skill_additions": ("skills/compat-extra.md",),
                                "runner_name": "compat_runner",
                                "model_name": "compat-model",
                                "timeout_seconds": 17,
                            }
                        )
                        if stage_plan.plane is Plane.EXECUTION and stage_plan.node_id == "builder"
                        else stage_plan
                        for stage_plan in engine.compiled_plan.execution_graph.nodes
                    )
                }
            )
        }
    )

    engine.tick()

    assert captured_request is not None
    assert captured_request.entrypoint_path.endswith("entrypoints/execution/consultant.md")
    assert captured_request.entrypoint_contract_id == "compat-builder.contract.v999"
    assert captured_request.required_skill_paths == (
        str(paths.runtime_root / "skills/compat-only.md"),
    )
    assert captured_request.attached_skill_paths == (
        str(paths.runtime_root / "skills/compat-extra.md"),
    )
    assert captured_request.runner_name == "compat_runner"
    assert captured_request.model_name == "compat-model"
    assert captured_request.timeout_seconds == 17


def test_runtime_writes_running_status_marker_while_stage_runner_is_active(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    observed_execution_status: str | None = None
    observed_snapshot_marker: str | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal observed_execution_status, observed_snapshot_marker
        observed_execution_status = load_execution_status(paths)
        observed_snapshot_marker = load_snapshot(paths).execution_status_marker
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    engine.tick()

    assert observed_execution_status == "### BUILDER_RUNNING"
    assert observed_snapshot_marker == "### BUILDER_RUNNING"
    assert load_execution_status(paths) == "### CHECKER_RUNNING"


def test_runtime_can_build_closure_target_request_without_active_work_item(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.ARBITER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    assert engine.snapshot is not None

    target = ClosureTargetState(
        root_spec_id="spec-root-001",
        root_idea_id="idea-001",
        root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
        root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
        rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
        latest_verdict_path="millrace-agents/arbiter/verdicts/spec-root-001.json",
        latest_report_path="millrace-agents/arbiter/reports/run-001.md",
        closure_open=True,
        closure_blocked_by_lineage_work=False,
        blocking_work_ids=(),
        opened_at=NOW,
    )
    save_closure_target_state(paths, target)

    arbiter_plan = engine._stage_plan_for(Plane.PLANNING, PlanningStageName.ARBITER)
    request = engine._build_closure_target_stage_run_request(arbiter_plan, target)

    assert request.request_kind == "closure_target"
    assert request.active_work_item_kind is None
    assert request.active_work_item_id is None
    assert request.active_work_item_path is None
    assert request.closure_target_root_spec_id == "spec-root-001"
    assert request.canonical_root_spec_path.endswith("spec-root-001.md")


def test_runtime_closure_target_request_uses_compiled_node_binding(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.ARBITER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    assert engine.snapshot is not None
    assert engine.compiled_plan is not None

    target = ClosureTargetState(
        root_spec_id="spec-root-001",
        root_idea_id="idea-001",
        root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
        root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
        rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
        latest_verdict_path="millrace-agents/arbiter/verdicts/spec-root-001.json",
        latest_report_path="millrace-agents/arbiter/reports/run-001.md",
        closure_open=True,
        closure_blocked_by_lineage_work=False,
        blocking_work_ids=(),
        opened_at=NOW,
    )
    save_closure_target_state(paths, target)

    engine.compiled_plan = engine.compiled_plan.model_copy(
        update={
            "planning_graph": engine.compiled_plan.planning_graph.model_copy(
                update={
                    "nodes": tuple(
                        stage_plan.model_copy(
                            update={
                                "entrypoint_path": "entrypoints/planning/mechanic.md",
                                "entrypoint_contract_id": "compat-arbiter.contract.v999",
                                "required_skill_paths": ("skills/compat-arbiter.md",),
                                "attached_skill_additions": ("skills/compat-arbiter-extra.md",),
                                "runner_name": "compat_planning_runner",
                                "model_name": "compat-planning-model",
                                "timeout_seconds": 19,
                            }
                        )
                        if stage_plan.plane is Plane.PLANNING and stage_plan.node_id == "arbiter"
                        else stage_plan
                        for stage_plan in engine.compiled_plan.planning_graph.nodes
                    )
                }
            )
        }
    )

    arbiter_plan = engine._stage_plan_for(Plane.PLANNING, PlanningStageName.ARBITER)
    request = engine._build_closure_target_stage_run_request(arbiter_plan, target)

    assert request.entrypoint_path.endswith("entrypoints/planning/mechanic.md")
    assert request.entrypoint_contract_id == "compat-arbiter.contract.v999"
    assert request.required_skill_paths == (
        str(paths.runtime_root / "skills/compat-arbiter.md"),
    )
    assert request.attached_skill_paths == (
        str(paths.runtime_root / "skills/compat-arbiter-extra.md"),
    )
    assert request.runner_name == "compat_planning_runner"
    assert request.model_name == "compat-planning-model"
    assert request.timeout_seconds == 19


def test_runtime_planning_retry_scope_skips_execution_active_work(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    claim = queue.claim_next_execution_task()
    assert claim is not None

    engine = RuntimeEngine(paths, stage_runner=lambda request: _runner_result(request, terminal=None, now=NOW))
    engine.startup()
    engine.snapshot = load_snapshot(paths).model_copy(
        update={
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "active_run_id": "run-active",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, engine.snapshot)

    engine._handle_mailbox_command(
        MailboxCommandEnvelope(
            command_id="cmd-001",
            command="retry_active",
            issued_at=NOW,
            issuer="operator",
            payload={"reason": "planning-only retry", "scope": "planning"},
        )
    )

    snapshot = load_snapshot(paths)
    assert snapshot.active_work_item_id == "task-001"
    assert (paths.tasks_active_dir / "task-001.md").is_file()


def test_runtime_mailbox_retry_scope_rejects_invalid_scope_payloads() -> None:
    with pytest.raises(ControlRoutingError, match="retry_active scope must be a string"):
        RuntimeEngine._mailbox_retry_scope(
            MailboxCommandEnvelope.model_validate(
                _mailbox_command("cmd-invalid-scope-type", "retry_active", payload={"scope": 123})
            )
        )

    with pytest.raises(ControlRoutingError, match="Unsupported retry_active scope: unsupported"):
        RuntimeEngine._mailbox_retry_scope(
            MailboxCommandEnvelope.model_validate(
                _mailbox_command(
                    "cmd-invalid-scope-value",
                    "retry_active",
                    payload={"scope": "unsupported"},
                )
            )
        )


def test_runtime_routes_malformed_stage_exit_into_recovery(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    call_index = {"count": 0}

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        call_index["count"] += 1
        if call_index["count"] == 1:
            return _runner_result(request, terminal=None, now=NOW)
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    first = engine.tick()
    assert first.stage == ExecutionStageName.BUILDER

    snapshot = load_snapshot(paths)
    assert snapshot.active_stage == ExecutionStageName.TROUBLESHOOTER
    assert snapshot.current_failure_class == "missing_terminal_result"
    assert load_execution_status(paths) == "### TROUBLESHOOTER_RUNNING"


def test_runtime_routes_post_stage_planning_completion_conflict_into_mechanic(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec_doc("spec-001", created_at=NOW))
    catalog_path = _write_runtime_error_catalog(paths.root)

    captured_mechanic_request: StageRunRequest | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal captured_mechanic_request
        if request.stage is PlanningStageName.PLANNER:
            return _runner_result(
                request,
                terminal=PlanningTerminalResult.PLANNER_COMPLETE.value,
                now=NOW,
            )
        if request.stage is PlanningStageName.MANAGER:
            queue.mark_spec_done("spec-001")
            return _runner_result(
                request,
                terminal=PlanningTerminalResult.MANAGER_COMPLETE.value,
                now=NOW,
            )
        captured_mechanic_request = request
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.MECHANIC_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    first = engine.tick()
    second = engine.tick()

    assert first.stage is PlanningStageName.PLANNER
    assert second.stage is PlanningStageName.MANAGER
    assert second.router_decision.next_stage is PlanningStageName.MECHANIC

    snapshot = load_snapshot(paths)
    assert snapshot.active_stage is PlanningStageName.MECHANIC
    assert snapshot.current_failure_class == "planning_work_item_completion_conflict"
    assert load_planning_status(paths) == "### BLOCKED"
    assert (paths.specs_done_dir / "spec-001.md").is_file()
    assert not (paths.specs_active_dir / "spec-001.md").exists()

    third = engine.tick()

    assert third.stage is PlanningStageName.MECHANIC
    assert captured_mechanic_request is not None
    assert captured_mechanic_request.runtime_error_code == "planning_work_item_completion_conflict"
    assert captured_mechanic_request.runtime_error_catalog_path == str(catalog_path)
    assert captured_mechanic_request.runtime_error_report_path is not None

    report_path = Path(captured_mechanic_request.runtime_error_report_path)
    assert report_path.is_file()
    report_text = report_path.read_text(encoding="utf-8")
    assert "planning_work_item_completion_conflict" in report_text
    assert "QueueStateError" in report_text
    assert "spec spec-001 is not active" in report_text


def test_runtime_blocks_malformed_recon_handoff_without_running_mechanic_or_manager(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_probe(_probe_doc("probe-001", created_at=NOW))

    seen_stages: list[PlanningStageName] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        seen_stages.append(PlanningStageName(request.stage))
        run_dir = Path(request.run_dir)
        if request.stage is PlanningStageName.RECON:
            (run_dir / "recon_packet.md").write_text(
                """# Bad Recon Packet

Recon-Packet-ID: recon-probe-001
Probe-ID: probe-001
Decision: to_planning
Confidence: high
Risk-Level: medium
Request-Summary: Research before routing.
Interpreted-Goal: Route this work safely.
Handoff-Target: planning
Emitted-Task-ID: task-from-probe
Created-At: 2026-04-28T12:00:00Z
Created-By: recon

Relevant-Paths:
- src/example.py | likely behavior owner

Semantic-Invariants:
- Preserve adjacent behavior.
""",
                encoding="utf-8",
            )
            return _runner_result(
                request,
                terminal=PlanningTerminalResult.RECON_TO_PLANNING.value,
                now=NOW,
            )
        raise AssertionError(f"unexpected stage after malformed recon handoff: {request.stage}")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    outcome = engine.tick()

    assert outcome.stage is PlanningStageName.RECON
    assert seen_stages == [PlanningStageName.RECON]
    snapshot = load_snapshot(paths)
    assert snapshot.active_stage is None
    assert snapshot.active_work_item_kind is None
    assert snapshot.current_failure_class == "recon_handoff_invalid"
    assert load_planning_status(paths) == "### BLOCKED"
    assert (paths.probes_blocked_dir / "probe-001.md").is_file()
    assert not (paths.probes_active_dir / "probe-001.md").exists()
    assert not any(paths.specs_queue_dir.glob("*.md"))

    second = engine.tick()
    assert second.router_decision.reason == "no_work"


def test_runtime_claims_generated_spec_after_recon_to_planning_handoff(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_probe(_probe_doc("probe-001", created_at=NOW))

    captured_requests: list[StageRunRequest] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        captured_requests.append(request)
        run_dir = Path(request.run_dir)
        if request.stage is PlanningStageName.RECON:
            packet = _recon_packet(
                "probe-001",
                decision=ReconDecision.TO_PLANNING,
                emitted_spec_id="spec-from-probe",
            )
            (run_dir / "recon_packet.md").write_text(render_recon_packet(packet), encoding="utf-8")
            (run_dir / "generated_spec.json").write_text(
                _generated_probe_spec().model_dump_json(indent=2),
                encoding="utf-8",
            )
            return _runner_result(
                request,
                terminal=PlanningTerminalResult.RECON_TO_PLANNING.value,
                now=NOW,
            )
        if request.stage is PlanningStageName.PLANNER:
            return _runner_result(
                request,
                terminal=PlanningTerminalResult.PLANNER_COMPLETE.value,
                now=NOW,
            )
        if request.stage is PlanningStageName.MANAGER:
            return _runner_result(
                request,
                terminal=PlanningTerminalResult.MANAGER_COMPLETE.value,
                now=NOW,
            )
        raise AssertionError(f"unexpected planning stage: {request.stage}")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    recon = engine.tick()
    planner = engine.tick()
    manager = engine.tick()

    assert recon.stage is PlanningStageName.RECON
    assert planner.stage is PlanningStageName.PLANNER
    assert manager.stage is PlanningStageName.MANAGER
    assert captured_requests[0].active_work_item_kind is WorkItemKind.PROBE
    assert captured_requests[1].active_work_item_kind is WorkItemKind.SPEC
    assert captured_requests[1].active_work_item_id == "spec-from-probe"
    assert captured_requests[1].active_work_item_path is not None
    assert "/specs/active/spec-from-probe.md" in captured_requests[1].active_work_item_path
    assert captured_requests[2].active_work_item_kind is WorkItemKind.SPEC
    assert captured_requests[2].active_work_item_id == "spec-from-probe"
    assert not (paths.probes_active_dir / "probe-001.md").exists()


def test_runtime_refuses_manager_request_for_stale_active_probe(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_probe(_probe_doc("probe-001", created_at=NOW))
    queue.claim_next_planning_item()

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError(f"runner should not be invoked for {request.stage}")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    engine.snapshot = load_snapshot(paths).model_copy(
        update={
            "process_running": True,
            "active_plane": Plane.PLANNING,
            "active_stage": PlanningStageName.MANAGER,
            "active_node_id": "manager",
            "active_stage_kind_id": "manager",
            "active_run_id": "run-stale-manager-probe",
            "active_work_item_kind": WorkItemKind.PROBE,
            "active_work_item_id": "probe-001",
            "planning_status_marker": "### MANAGER_RUNNING",
        }
    )
    save_snapshot(paths, engine.snapshot)
    set_planning_status(paths, "### MANAGER_RUNNING")

    outcome = engine.tick()

    assert outcome.router_decision.reason == "stage_work_item_ownership_invalid"
    snapshot = load_snapshot(paths)
    assert snapshot.active_stage is None
    assert snapshot.active_work_item_kind is None
    assert snapshot.current_failure_class == "stage_work_item_ownership_invalid"
    assert (paths.probes_queue_dir / "probe-001.md").is_file()
    assert not (paths.probes_active_dir / "probe-001.md").exists()
    events = read_runtime_events(paths)
    assert any(
        event.event_type == "runtime_stage_work_item_ownership_invalid"
        for event in events
    )


def test_runtime_routes_post_stage_execution_completion_conflict_into_troubleshooter(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    catalog_path = _write_runtime_error_catalog(paths.root)

    captured_troubleshooter_request: StageRunRequest | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal captured_troubleshooter_request
        if request.stage is ExecutionStageName.BUILDER:
            return _runner_result(
                request,
                terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
                now=NOW,
            )
        if request.stage is ExecutionStageName.CHECKER:
            return _runner_result(
                request,
                terminal=ExecutionTerminalResult.CHECKER_PASS.value,
                now=NOW,
            )
        if request.stage is ExecutionStageName.UPDATER:
            queue.mark_task_done("task-001")
            return _runner_result(
                request,
                terminal=ExecutionTerminalResult.UPDATE_COMPLETE.value,
                now=NOW,
            )
        captured_troubleshooter_request = request
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    assert engine.compiled_plan is not None
    custom_troubleshooter_node_id = "recovery.execution.troubleshooter"
    engine.compiled_plan = _renamed_compiled_node(
        engine.compiled_plan,
        plane=Plane.EXECUTION,
        old_node_id="troubleshooter",
        new_node_id=custom_troubleshooter_node_id,
    )

    first = engine.tick()
    second = engine.tick()
    third = engine.tick()

    assert first.stage is ExecutionStageName.BUILDER
    assert second.stage is ExecutionStageName.CHECKER
    assert third.stage is ExecutionStageName.UPDATER
    assert third.router_decision.next_stage is ExecutionStageName.TROUBLESHOOTER

    snapshot = load_snapshot(paths)
    assert snapshot.active_stage is ExecutionStageName.TROUBLESHOOTER
    assert snapshot.active_node_id == custom_troubleshooter_node_id
    assert snapshot.active_stage_kind_id == ExecutionStageName.TROUBLESHOOTER.value
    assert snapshot.current_failure_class == "execution_work_item_completion_conflict"
    assert load_execution_status(paths) == "### BLOCKED"
    assert (paths.tasks_done_dir / "task-001.md").is_file()
    assert not (paths.tasks_active_dir / "task-001.md").exists()

    fourth = engine.tick()

    assert fourth.stage is ExecutionStageName.TROUBLESHOOTER
    assert captured_troubleshooter_request is not None
    assert captured_troubleshooter_request.node_id == custom_troubleshooter_node_id
    assert captured_troubleshooter_request.stage_kind_id == ExecutionStageName.TROUBLESHOOTER.value
    assert captured_troubleshooter_request.runtime_error_code == "execution_work_item_completion_conflict"
    assert captured_troubleshooter_request.runtime_error_catalog_path == str(catalog_path)
    assert captured_troubleshooter_request.runtime_error_report_path is not None

    report_path = Path(captured_troubleshooter_request.runtime_error_report_path)
    assert report_path.is_file()
    report_text = report_path.read_text(encoding="utf-8")
    assert "execution_work_item_completion_conflict" in report_text
    assert "QueueStateError" in report_text
    assert "task task-001 is not active" in report_text


def test_runtime_blocked_planning_item_is_moved_to_blocked_without_snapshot_crash(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec_doc("spec-001", created_at=NOW))

    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[recovery]\nmax_mechanic_attempts = 1\n", encoding="utf-8")

    seen_stages: list[str] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        seen_stages.append(request.stage.value)
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.BLOCKED.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()

    first = engine.tick()
    assert first.stage == PlanningStageName.PLANNER

    second = engine.tick()
    assert second.stage == PlanningStageName.MECHANIC

    snapshot = load_snapshot(paths)
    assert snapshot.active_stage is None
    assert snapshot.active_plane is None
    assert snapshot.active_work_item_kind is None
    assert snapshot.active_work_item_id is None
    assert snapshot.current_failure_class == "planner_blocked"
    assert load_planning_status(paths) == "### BLOCKED"
    assert (paths.specs_blocked_dir / "spec-001.md").is_file()
    assert not (paths.specs_active_dir / "spec-001.md").exists()
    assert load_recovery_counters(paths).entries == ()
    assert seen_stages == ["planner", "mechanic"]
    assert second.router_decision.action is RouterAction.BLOCKED
    assert second.router_decision.reason == "mechanic_blocked:mechanic_attempts_exhausted"


def test_runtime_handoff_creates_incident_and_transitions_to_planning(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text(
        "[recovery]\nmax_troubleshoot_attempts_before_consult = 1\n",
        encoding="utf-8",
    )

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        if request.stage is ExecutionStageName.BUILDER:
            return _runner_result(
                request,
                terminal=ExecutionTerminalResult.BLOCKED.value,
                now=NOW,
            )
        if request.stage is ExecutionStageName.TROUBLESHOOTER:
            return _runner_result(
                request,
                terminal=ExecutionTerminalResult.BLOCKED.value,
                now=NOW,
            )
        if request.stage is ExecutionStageName.CONSULTANT:
            return _runner_result(
                request,
                terminal=ExecutionTerminalResult.NEEDS_PLANNING.value,
                now=NOW,
            )
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.AUDITOR_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()

    first = engine.tick()
    second = engine.tick()
    third = engine.tick()

    assert first.stage is ExecutionStageName.BUILDER
    assert second.stage is ExecutionStageName.TROUBLESHOOTER
    assert third.stage is ExecutionStageName.CONSULTANT
    assert third.router_decision.action is RouterAction.HANDOFF

    snapshot_after_handoff = load_snapshot(paths)
    assert snapshot_after_handoff.active_stage is None
    assert snapshot_after_handoff.active_plane is None
    assert snapshot_after_handoff.active_work_item_id is None
    assert snapshot_after_handoff.current_failure_class is None
    assert (paths.tasks_blocked_dir / "task-001.md").is_file()
    assert len(tuple(paths.incidents_incoming_dir.glob("*.md"))) == 1
    incident_path = next(paths.incidents_incoming_dir.glob("*.md"))
    incident = read_work_document_as(incident_path, model=IncidentDocument)
    assert incident.needs_planning is True
    assert incident.trigger_reason == "consultant_needs_planning"
    assert incident.source_stage is ExecutionStageName.CONSULTANT

    event_types = [event.event_type for event in read_runtime_events(paths)]
    assert "runtime_handoff_incident_enqueued" in event_types

    fourth = engine.tick()
    assert fourth.stage is PlanningStageName.AUDITOR


def test_runtime_handoff_incident_inherits_task_lineage_under_open_closure_target(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    root_spec_id = "spec-root-001"
    root_idea_id = "idea-001"
    queue = QueueStore(paths)
    queue.enqueue_task(
        _task_doc("task-001", created_at=NOW).model_copy(
            update={
                "root_idea_id": root_idea_id,
                "root_spec_id": root_spec_id,
                "spec_id": root_spec_id,
            }
        )
    )
    queue.enqueue_spec(
        _spec_doc("spec-unrelated", created_at=NOW + timedelta(seconds=1)).model_copy(
            update={
                "root_idea_id": "idea-002",
                "root_spec_id": "spec-unrelated",
            }
        )
    )
    save_closure_target_state(
        paths,
        _closure_target_state(root_spec_id=root_spec_id, root_idea_id=root_idea_id),
    )

    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text(
        "[recovery]\nmax_troubleshoot_attempts_before_consult = 1\n",
        encoding="utf-8",
    )
    captured_requests: list[StageRunRequest] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        captured_requests.append(request)
        if request.stage is ExecutionStageName.BUILDER:
            return _runner_result(
                request,
                terminal=ExecutionTerminalResult.BLOCKED.value,
                now=NOW,
            )
        if request.stage is ExecutionStageName.TROUBLESHOOTER:
            return _runner_result(
                request,
                terminal=ExecutionTerminalResult.BLOCKED.value,
                now=NOW,
            )
        if request.stage is ExecutionStageName.CONSULTANT:
            return _runner_result(
                request,
                terminal=ExecutionTerminalResult.NEEDS_PLANNING.value,
                now=NOW,
            )
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.AUDITOR_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()

    first = engine.tick()
    second = engine.tick()
    third = engine.tick()

    assert first.stage is ExecutionStageName.BUILDER
    assert second.stage is ExecutionStageName.TROUBLESHOOTER
    assert third.stage is ExecutionStageName.CONSULTANT
    assert third.router_decision.action is RouterAction.HANDOFF
    assert (paths.tasks_blocked_dir / "task-001.md").is_file()
    assert (paths.specs_queue_dir / "spec-unrelated.md").is_file()

    incident_path = next(paths.incidents_incoming_dir.glob("*.md"))
    incident = read_work_document_as(incident_path, model=IncidentDocument)
    assert incident.root_idea_id == root_idea_id
    assert incident.root_spec_id == root_spec_id
    assert incident.source_spec_id == root_spec_id
    assert incident.source_task_id == "task-001"

    fourth = engine.tick()

    assert fourth.stage is PlanningStageName.AUDITOR
    assert captured_requests[-1].active_work_item_kind is WorkItemKind.INCIDENT
    assert captured_requests[-1].active_work_item_id == incident.incident_id
    assert (paths.specs_queue_dir / "spec-unrelated.md").is_file()


def test_runtime_blocked_transition_recovers_when_active_artifact_missing(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec_doc("spec-001", created_at=NOW))

    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[recovery]\nmax_mechanic_attempts = 1\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.BLOCKED.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()

    first = engine.tick()
    assert first.stage is PlanningStageName.PLANNER

    active_spec_path = paths.specs_active_dir / "spec-001.md"
    assert active_spec_path.is_file()
    active_spec_path.unlink()

    second = engine.tick()
    assert second.stage is PlanningStageName.MECHANIC
    snapshot = load_snapshot(paths)
    assert snapshot.active_stage is None
    assert snapshot.active_plane is None


def test_runtime_startup_reconciles_stale_state_to_recovery_stage(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    claimed = queue.claim_next_execution_task()
    assert claimed is not None

    stale_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": False,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.CHECKER,
            "active_run_id": "run-stale",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, stale_snapshot)
    set_execution_status(paths, "### CHECKER_RUNNING")

    seen_stages: list[str] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        seen_stages.append(request.stage.value)
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    reconciled = engine.startup()

    assert reconciled.active_stage == ExecutionStageName.TROUBLESHOOTER
    assert reconciled.current_failure_class == "stale_active_ownership"
    persisted_counters = load_recovery_counters(paths)
    assert persisted_counters.entries
    assert persisted_counters.entries[0].troubleshoot_attempt_count == 1
    assert engine.counters is not None
    assert engine.counters.entries[0].troubleshoot_attempt_count == 1

    engine.tick()
    assert seen_stages[0] == "troubleshooter"


def test_runtime_tick_reconciles_execution_anomaly_before_stage_execution(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    claimed = queue.claim_next_execution_task()
    assert claimed is not None

    seen_stages: list[ExecutionStageName] = []
    captured_request: StageRunRequest | None = None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal captured_request
        assert isinstance(request.stage, ExecutionStageName)
        captured_request = request
        seen_stages.append(request.stage)
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    assert engine.compiled_plan is not None
    custom_troubleshooter_node_id = "recovery.execution.troubleshooter"
    engine.compiled_plan = _renamed_compiled_node(
        engine.compiled_plan,
        plane=Plane.EXECUTION,
        old_node_id="troubleshooter",
        new_node_id=custom_troubleshooter_node_id,
    )

    stale_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": False,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.CHECKER,
            "active_run_id": "run-stale-tick",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, stale_snapshot)
    engine.snapshot = stale_snapshot
    set_execution_status(paths, "### CHECKER_RUNNING")

    outcome = engine.tick()

    assert outcome.stage is ExecutionStageName.TROUBLESHOOTER
    assert seen_stages == [ExecutionStageName.TROUBLESHOOTER]
    assert captured_request is not None
    assert captured_request.node_id == custom_troubleshooter_node_id
    assert captured_request.stage_kind_id == ExecutionStageName.TROUBLESHOOTER.value
    counters = load_recovery_counters(paths)
    assert len(counters.entries) == 1
    assert counters.entries[0].failure_class == "stale_active_ownership"
    assert counters.entries[0].troubleshoot_attempt_count == 1

    event_types = [event.event_type for event in read_runtime_events(paths)]
    assert "runtime_reconciled" in event_types
    assert event_types.index("runtime_reconciled") < event_types.index("stage_started")


def test_runtime_tick_routes_planning_anomaly_into_mechanic(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec_doc("spec-001", created_at=NOW))
    claimed = queue.claim_next_planning_item()
    assert claimed is not None

    seen_stages: list[PlanningStageName] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        assert isinstance(request.stage, PlanningStageName)
        seen_stages.append(request.stage)
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.MECHANIC_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    anomalous_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "active_plane": Plane.PLANNING,
            "active_stage": PlanningStageName.PLANNER,
            "active_run_id": "run-planning-anomaly",
            "active_work_item_kind": WorkItemKind.SPEC,
            "active_work_item_id": "spec-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, anomalous_snapshot)
    engine.snapshot = anomalous_snapshot
    set_planning_status(paths, "### MANAGER_COMPLETE")

    outcome = engine.tick()

    assert outcome.stage is PlanningStageName.MECHANIC
    assert seen_stages == [PlanningStageName.MECHANIC]
    counters = load_recovery_counters(paths)
    assert len(counters.entries) == 1
    assert counters.entries[0].failure_class == "impossible_status_marker"
    assert counters.entries[0].mechanic_attempt_count == 1


def test_runtime_tick_routes_unknown_execution_marker_into_troubleshooter(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    claimed = queue.claim_next_execution_task()
    assert claimed is not None

    seen_stages: list[ExecutionStageName] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        assert isinstance(request.stage, ExecutionStageName)
        seen_stages.append(request.stage)
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    active_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.CHECKER,
            "active_run_id": "run-invalid-marker",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, active_snapshot)
    engine.snapshot = active_snapshot
    paths.execution_status_file.write_text("### NOT_A_REAL_MARKER\n", encoding="utf-8")

    outcome = engine.tick()

    assert outcome.stage is ExecutionStageName.TROUBLESHOOTER
    assert seen_stages == [ExecutionStageName.TROUBLESHOOTER]


def test_runtime_tick_routes_malformed_execution_marker_into_troubleshooter(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    claimed = queue.claim_next_execution_task()
    assert claimed is not None

    seen_stages: list[ExecutionStageName] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        assert isinstance(request.stage, ExecutionStageName)
        seen_stages.append(request.stage)
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    active_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.CHECKER,
            "active_run_id": "run-invalid-marker",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, active_snapshot)
    engine.snapshot = active_snapshot
    paths.execution_status_file.write_text("### CHECKER_PASS\n### EXTRA\n", encoding="utf-8")

    outcome = engine.tick()

    assert outcome.stage is ExecutionStageName.TROUBLESHOOTER
    assert seen_stages == [ExecutionStageName.TROUBLESHOOTER]


def test_runtime_tick_stale_execution_anomaly_escalates_to_consultant(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    claimed = queue.claim_next_execution_task()
    assert claimed is not None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BLOCKED.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    save_recovery_counters(
        paths,
        RecoveryCounters(
            entries=(
                RecoveryCounterEntry(
                    failure_class="stale_active_ownership",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    troubleshoot_attempt_count=2,
                    last_updated_at=NOW,
                ),
            )
        ),
    )
    stale_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": False,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.CHECKER,
            "active_run_id": "run-stale-consult",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, stale_snapshot)
    engine.snapshot = stale_snapshot
    engine.counters = load_recovery_counters(paths)
    set_execution_status(paths, "### CHECKER_RUNNING")

    outcome = engine.tick()

    assert outcome.stage is ExecutionStageName.CONSULTANT
    assert outcome.router_decision.action is RouterAction.BLOCKED


def test_runtime_tick_enforces_pause_and_stop_commands(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    calls = {"count": 0}

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        calls["count"] += 1
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    write_mailbox_command(paths, _mailbox_command("cmd-001", "pause"))
    paused = engine.tick()
    assert paused.router_decision.reason == "paused"
    assert paused.stage_result.result_class is ResultClass.SUCCESS
    assert paused.stage_result.terminal_result is ExecutionTerminalResult.UPDATE_COMPLETE
    assert calls["count"] == 0

    write_mailbox_command(paths, _mailbox_command("cmd-002", "stop"))
    stopped = engine.tick()
    assert stopped.router_decision.reason == "stop_requested"
    assert stopped.stage_result.result_class is ResultClass.SUCCESS
    assert stopped.stage_result.terminal_result is ExecutionTerminalResult.UPDATE_COMPLETE
    assert calls["count"] == 0
    snapshot = load_snapshot(paths)
    assert snapshot.process_running is False
    assert snapshot.stop_requested is False
    assert snapshot.paused is False


def test_runtime_tick_stop_normalizes_active_snapshot_to_stopped_idle_invariant(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    claimed = queue.claim_next_execution_task()
    assert claimed is not None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    active_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "paused": True,
            "stop_requested": False,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "active_run_id": "run-stop-active",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "current_failure_class": "runner_transport_failure",
            "troubleshoot_attempt_count": 2,
            "mechanic_attempt_count": 1,
            "fix_cycle_count": 1,
            "consultant_invocations": 1,
            "execution_status_marker": "### BLOCKED",
            "planning_status_marker": "### MANAGER_COMPLETE",
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, active_snapshot)
    engine.snapshot = active_snapshot
    set_execution_status(paths, "### BLOCKED")
    set_planning_status(paths, "### MANAGER_COMPLETE")

    write_mailbox_command(paths, _mailbox_command("cmd-stop-active", "stop"))
    outcome = engine.tick()

    assert outcome.router_decision.reason == "stop_requested"
    snapshot = load_snapshot(paths)
    assert snapshot.process_running is False
    assert snapshot.paused is False
    assert snapshot.stop_requested is False
    assert snapshot.active_plane is None
    assert snapshot.active_stage is None
    assert snapshot.active_run_id is None
    assert snapshot.active_work_item_kind is None
    assert snapshot.active_work_item_id is None
    assert snapshot.active_since is None
    assert snapshot.current_failure_class is None
    assert snapshot.troubleshoot_attempt_count == 0
    assert snapshot.mechanic_attempt_count == 0
    assert snapshot.fix_cycle_count == 0
    assert snapshot.consultant_invocations == 0
    assert snapshot.execution_status_marker == "### IDLE"
    assert snapshot.planning_status_marker == "### IDLE"
    assert load_execution_status(paths) == "### IDLE"
    assert load_planning_status(paths) == "### IDLE"


def test_runtime_tick_applies_mailbox_pause_before_reconciliation(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    claimed = queue.claim_next_execution_task()
    assert claimed is not None

    calls = {"count": 0}

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        calls["count"] += 1
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    stale_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": False,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.CHECKER,
            "active_run_id": "run-ordering",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, stale_snapshot)
    engine.snapshot = stale_snapshot
    engine.counters = load_recovery_counters(paths)
    set_execution_status(paths, "### CHECKER_PASS")
    write_mailbox_command(paths, _mailbox_command("cmd-pause-ordering", "pause"))

    outcome = engine.tick()

    assert outcome.router_decision.reason == "paused"
    assert calls["count"] == 0
    event_types = [event.event_type for event in read_runtime_events(paths)]
    assert "runtime_tick_paused" in event_types
    assert "runtime_reconciled" not in event_types


def test_runtime_tick_normalizes_idea_watch_event_before_execution(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[watchers]",
                "enabled = true",
                "debounce_ms = 100",
                "watch_ideas_inbox = true",
            ]
        ),
        encoding="utf-8",
    )
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))

    seen_stages: list[PlanningStageName | ExecutionStageName] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        seen_stages.append(request.stage)
        terminal = (
            PlanningTerminalResult.PLANNER_COMPLETE.value
            if request.stage is PlanningStageName.PLANNER
            else ExecutionTerminalResult.BUILDER_COMPLETE.value
        )
        return _runner_result(
            request,
            terminal=terminal,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()

    ideas_inbox = paths.root / "ideas" / "inbox"
    ideas_inbox.mkdir(parents=True, exist_ok=True)
    (ideas_inbox / "idea-001.md").write_text("# Idea 001\n\nPrioritize planning from watcher input.\n", encoding="utf-8")

    outcome = engine.tick()

    assert outcome.stage is PlanningStageName.PLANNER
    assert seen_stages == [PlanningStageName.PLANNER]
    assert any(paths.specs_active_dir.glob("idea-*.md"))


def test_runtime_tick_normalizes_preexisting_idea_inbox_file_on_startup(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[watchers]",
                "enabled = true",
                "debounce_ms = 100",
                "watch_ideas_inbox = true",
            ]
        ),
        encoding="utf-8",
    )
    ideas_inbox = paths.root / "ideas" / "inbox"
    ideas_inbox.mkdir(parents=True, exist_ok=True)
    (ideas_inbox / "startup-idea.md").write_text(
        "# Startup Idea\n\nNormalize this idea on the first daemon tick.\n",
        encoding="utf-8",
    )

    seen_stages: list[PlanningStageName | ExecutionStageName] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        seen_stages.append(request.stage)
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.PLANNER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()

    outcome = engine.tick()

    assert outcome.stage is PlanningStageName.PLANNER
    assert seen_stages == [PlanningStageName.PLANNER]
    assert (paths.specs_active_dir / "idea-startup-idea.md").is_file()


def test_runtime_tick_applies_mailbox_then_watcher_before_no_work_idle(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'daemon'\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()

    config_path.write_text(
        "[runtime]\nrun_style = 'daemon'\nidle_sleep_seconds = 2.0\n",
        encoding="utf-8",
    )
    write_mailbox_command(paths, _mailbox_command("cmd-reload-idle-order", "reload_config"))

    ignored_path = paths.root / "ignored.target"
    ignored_path.write_text("ignore watcher event\n", encoding="utf-8")

    class _FakeWatcherSession:
        mode = WatcherMode.POLL

        def poll_once(self, *, now: datetime | None = None) -> tuple[WatchEvent, ...]:
            del now
            return (
                WatchEvent(
                    target="unknown_target",
                    path=ignored_path,
                    event_kind="changed",
                    observed_at=NOW,
                ),
            )

        def close(self) -> None:
            return

    fake_watcher = _FakeWatcherSession()

    def rebuild_watcher_session() -> None:
        engine._watcher_session = fake_watcher

    engine._watcher_session = fake_watcher
    engine._rebuild_watcher_session = rebuild_watcher_session  # type: ignore[method-assign]

    outcome = engine.tick()
    snapshot = load_snapshot(paths)
    event_types = [event.event_type for event in read_runtime_events(paths)]

    assert outcome.router_decision.reason == "no_work"
    assert snapshot.runtime_mode is RuntimeMode.DAEMON
    assert snapshot.last_reload_outcome == "applied"
    assert event_types.index("runtime_config_reloaded") < event_types.index("watcher_event_ignored")
    assert event_types.index("watcher_event_ignored") < event_types.index("watcher_events_consumed")
    assert event_types.index("watcher_events_consumed") < event_types.index("runtime_tick_idle")


def test_runtime_normalize_idea_watch_event_ignores_read_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    idea_path = paths.root / "ideas" / "inbox" / "idea-error.md"
    idea_path.parent.mkdir(parents=True, exist_ok=True)
    idea_path.write_text("# Idea\n", encoding="utf-8")

    original_read_text = Path.read_text

    def flaky_read_text(self: Path, *args, **kwargs):
        if self == idea_path:
            raise OSError("simulated transient read failure")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)
    engine._normalize_idea_watch_event(idea_path)

    assert not any(paths.specs_queue_dir.glob("idea-*.md"))


def test_runtime_normalize_idea_watch_event_writes_root_lineage_fields(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    idea_path = paths.root / "ideas" / "inbox" / "seed-idea.md"
    idea_path.parent.mkdir(parents=True, exist_ok=True)
    idea_path.write_text("# Seed Idea\n\nPreserve root lineage from watcher input.\n", encoding="utf-8")

    engine._normalize_idea_watch_event(idea_path)

    queued_specs = sorted(paths.specs_queue_dir.glob("idea-*.md"))
    assert len(queued_specs) == 1

    spec_path = queued_specs[0]
    spec_text = spec_path.read_text(encoding="utf-8")

    assert f"Root-Idea-ID: {spec_path.stem}" in spec_text
    assert f"Root-Spec-ID: {spec_path.stem}" in spec_text


def test_runtime_normalize_idea_watch_event_writes_durable_source_reference(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    idea_markdown = "# Seed Idea\n\nPreserve this exact markdown.\n"
    idea_path = paths.root / "ideas" / "inbox" / "seed-idea.md"
    idea_path.parent.mkdir(parents=True, exist_ok=True)
    idea_path.write_text(idea_markdown, encoding="utf-8")

    engine._normalize_idea_watch_event(idea_path)

    durable_source = paths.runtime_root / "intake" / "ideas" / "idea-seed-idea.md"
    assert durable_source.read_text(encoding="utf-8") == idea_markdown

    queued_specs = sorted(paths.specs_queue_dir.glob("idea-*.md"))
    assert len(queued_specs) == 1
    spec = read_work_document_as(queued_specs[0], model=SpecDocument)
    assert spec.references == (
        "millrace-agents/intake/ideas/idea-seed-idea.md",
        "ideas/inbox/seed-idea.md",
    )


def test_watcher_idea_spec_ids_are_idempotent_for_prefixed_filenames() -> None:
    assert safe_spec_id_from_idea_path(Path("seed-idea.md")) == "idea-seed-idea"
    assert (
        safe_spec_id_from_idea_path(Path("idea-2026-04-27-browser-local-qa.md"))
        == "idea-2026-04-27-browser-local-qa"
    )
    assert safe_spec_id_from_idea_path(Path("Feature idea!.md")) == "idea-Feature-idea"


def test_runtime_tick_handles_active_stage_without_work_item_identity(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    broken_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "active_run_id": "run-broken",
            "active_work_item_kind": None,
            "active_work_item_id": None,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, broken_snapshot)

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    outcome = engine.tick()

    assert outcome.router_decision.reason == "missing_active_work_item_identity"
    snapshot = load_snapshot(paths)
    assert snapshot.active_stage is None
    assert snapshot.active_plane is None


def test_runtime_startup_projects_config_runtime_mode(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'daemon'\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    snapshot = engine.startup()

    assert snapshot.runtime_mode.value == "daemon"


def test_runtime_startup_preserves_pause_flag_across_restart(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    snapshot = load_snapshot(paths).model_copy(update={"paused": True, "updated_at": NOW})
    save_snapshot(paths, snapshot)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    started = engine.startup()

    assert started.paused is True


def test_runtime_tick_with_no_work_reports_non_blocked_idle_result(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    outcome = engine.tick()

    assert outcome.router_decision.reason == "no_work"
    assert outcome.stage_result.result_class is ResultClass.SUCCESS
    assert outcome.stage_result.terminal_result is ExecutionTerminalResult.UPDATE_COMPLETE


def test_runtime_tick_with_no_work_suppresses_completion_when_lineage_work_remains(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(
        _task_doc("task-001", created_at=NOW).model_copy(
            update={"root_idea_id": "idea-001", "root_spec_id": "spec-root-001"}
        )
    )
    claimed = queue.claim_next_execution_task()
    assert claimed is not None
    queue.mark_task_blocked("task-001")

    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id="spec-root-001",
            root_idea_id="idea-001",
            root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
            root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
            rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
            latest_verdict_path=None,
            latest_report_path=None,
            closure_open=True,
            closure_blocked_by_lineage_work=False,
            blocking_work_ids=(),
            opened_at=NOW,
        ),
    )

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called while completion is suppressed")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    outcome = engine.tick()
    target = load_closure_target_state(paths, root_spec_id="spec-root-001")

    assert outcome.router_decision.reason == "no_work"
    assert target.closure_blocked_by_lineage_work is True
    assert target.blocking_work_ids == ("task-001",)


def test_runtime_tick_closes_closure_target_on_arbiter_complete(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id="spec-root-001",
            root_idea_id="idea-001",
            root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
            root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
            rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
            latest_verdict_path=None,
            latest_report_path=None,
            closure_open=True,
            closure_blocked_by_lineage_work=False,
            blocking_work_ids=(),
            opened_at=NOW,
        ),
    )

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        verdict_path = Path(request.preferred_verdict_path)
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        verdict_path.write_text('{"status":"pass"}\n', encoding="utf-8")
        report_path = Path(request.preferred_report_path)
        report_path.write_text("# Arbiter Report\n\nParity holds.\n", encoding="utf-8")
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.ARBITER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    outcome = engine.tick()
    target = load_closure_target_state(paths, root_spec_id="spec-root-001")
    report_copy = paths.arbiter_reports_dir / f"{outcome.stage_result.run_id}.md"

    assert outcome.stage is PlanningStageName.ARBITER
    assert outcome.router_decision.reason == "arbiter_complete"
    assert target.closure_open is False
    assert target.closed_at is not None
    assert target.last_arbiter_run_id == outcome.stage_result.run_id
    assert target.latest_verdict_path == "millrace-agents/arbiter/verdicts/spec-root-001.json"
    assert target.latest_report_path == f"millrace-agents/arbiter/reports/{outcome.stage_result.run_id}.md"
    assert report_copy.is_file()


def test_runtime_tick_enqueues_remediation_incident_for_arbiter_gap(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id="spec-root-001",
            root_idea_id="idea-001",
            root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
            root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
            rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
            latest_verdict_path=None,
            latest_report_path=None,
            closure_open=True,
            closure_blocked_by_lineage_work=False,
            blocking_work_ids=(),
            opened_at=NOW,
        ),
    )

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        verdict_path = Path(request.preferred_verdict_path)
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        verdict_path.write_text('{"status":"gap"}\n', encoding="utf-8")
        report_path = Path(request.preferred_report_path)
        report_path.write_text("# Arbiter Report\n\nParity gaps remain.\n", encoding="utf-8")
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.REMEDIATION_NEEDED.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    outcome = engine.tick()
    target = load_closure_target_state(paths, root_spec_id="spec-root-001")
    incident_paths = tuple(paths.incidents_incoming_dir.glob("*.md"))

    assert outcome.stage is PlanningStageName.ARBITER
    assert outcome.router_decision.reason == "arbiter_remediation_needed"
    assert target.closure_open is True
    assert target.closed_at is None
    assert target.last_arbiter_run_id == outcome.stage_result.run_id
    assert target.latest_verdict_path == "millrace-agents/arbiter/verdicts/spec-root-001.json"
    assert target.latest_report_path == f"millrace-agents/arbiter/reports/{outcome.stage_result.run_id}.md"
    assert len(incident_paths) == 1
    incident_text = incident_paths[0].read_text(encoding="utf-8")
    assert "Failure-Class: arbiter_parity_gap" in incident_text
    assert "Root-Spec-ID: spec-root-001" in incident_text
    assert "Root-Idea-ID: idea-001" in incident_text
    assert "Source-Stage: arbiter" in incident_text
    assert "Trigger-Reason: arbiter_remediation_needed" in incident_text


def test_runtime_tick_blocks_repeated_arbiter_remediation_without_execution(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id="spec-root-001",
            root_idea_id="idea-001",
            root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
            root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
            rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
            latest_verdict_path=None,
            latest_report_path=None,
            closure_open=True,
            closure_blocked_by_lineage_work=False,
            blocking_work_ids=(),
            opened_at=NOW,
            last_arbiter_run_id="run-previous-arbiter",
        ),
    )

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        verdict_path = Path(request.preferred_verdict_path)
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        verdict_path.write_text('{"status":"gap"}\n', encoding="utf-8")
        report_path = Path(request.preferred_report_path)
        report_path.write_text("# Arbiter Report\n\nParity gaps still remain.\n", encoding="utf-8")
        return _runner_result(
            request,
            terminal=PlanningTerminalResult.REMEDIATION_NEEDED.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    outcome = engine.tick()
    snapshot = load_snapshot(paths)
    incident_paths = tuple(paths.incidents_incoming_dir.glob("*.md"))

    assert outcome.stage is PlanningStageName.ARBITER
    assert incident_paths == ()
    assert snapshot.active_stage is None
    assert snapshot.planning_status_marker == "### BLOCKED"
    assert snapshot.current_failure_class == "closure_repeated_remediation_without_execution"
    assert any(
        event.event_type == "closure_repeated_remediation_blocked"
        and event.data.get("root_spec_id") == "spec-root-001"
        for event in read_runtime_events(paths)
    )


def test_runtime_mailbox_retry_active_requeues_active_item_and_resets_counters(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    claimed = queue.claim_next_execution_task()
    assert claimed is not None

    stale_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "active_run_id": "run-active",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "current_failure_class": "missing_terminal_result",
            "troubleshoot_attempt_count": 2,
            "fix_cycle_count": 1,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, stale_snapshot)
    save_recovery_counters(
        paths,
        RecoveryCounters(
            entries=(
                RecoveryCounterEntry(
                    failure_class="missing_terminal_result",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    troubleshoot_attempt_count=2,
                    fix_cycle_count=1,
                    last_updated_at=NOW,
                ),
                RecoveryCounterEntry(
                    failure_class="other_item",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-keep",
                    troubleshoot_attempt_count=1,
                    last_updated_at=NOW,
                ),
            )
        ),
    )

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    write_mailbox_command(paths, _mailbox_command("cmd-retry-active", "retry_active"))
    engine._drain_mailbox()

    assert (paths.tasks_active_dir / "task-001.md").exists() is False
    assert (paths.tasks_queue_dir / "task-001.md").is_file()
    assert (paths.tasks_queue_dir / "task-001.requeue.jsonl").is_file()

    snapshot = load_snapshot(paths)
    assert snapshot.active_plane is None
    assert snapshot.active_stage is None
    assert snapshot.active_run_id is None
    assert snapshot.active_work_item_kind is None
    assert snapshot.active_work_item_id is None
    assert snapshot.active_since is None
    assert snapshot.current_failure_class is None
    assert snapshot.troubleshoot_attempt_count == 0
    assert snapshot.fix_cycle_count == 0

    persisted_counters = load_recovery_counters(paths)
    assert len(persisted_counters.entries) == 1
    assert persisted_counters.entries[0].work_item_id == "task-keep"


def test_runtime_mailbox_clear_stale_state_requeues_multiple_active_artifacts(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001", created_at=NOW))
    queue.enqueue_spec(_spec_doc("spec-001", created_at=NOW))
    task_claim = queue.claim_next_execution_task()
    spec_claim = queue.claim_next_planning_item()
    assert task_claim is not None
    assert spec_claim is not None

    stale_snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "active_run_id": "run-stale",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "current_failure_class": "stale_active_ownership",
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, stale_snapshot)
    save_recovery_counters(
        paths,
        RecoveryCounters(
            entries=(
                RecoveryCounterEntry(
                    failure_class="stale_active_ownership",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    troubleshoot_attempt_count=1,
                    last_updated_at=NOW,
                ),
            )
        ),
    )

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    write_mailbox_command(paths, _mailbox_command("cmd-clear-stale", "clear_stale_state"))
    engine._drain_mailbox()

    assert (paths.tasks_active_dir / "task-001.md").exists() is False
    assert (paths.specs_active_dir / "spec-001.md").exists() is False
    assert (paths.tasks_queue_dir / "task-001.md").is_file()
    assert (paths.specs_queue_dir / "spec-001.md").is_file()
    assert (paths.tasks_queue_dir / "task-001.requeue.jsonl").is_file()
    assert (paths.specs_queue_dir / "spec-001.requeue.jsonl").is_file()

    snapshot = load_snapshot(paths)
    assert snapshot.active_plane is None
    assert snapshot.active_stage is None
    assert snapshot.active_run_id is None
    assert snapshot.active_work_item_kind is None
    assert snapshot.active_work_item_id is None
    assert snapshot.active_since is None
    assert snapshot.current_failure_class is None

    assert load_recovery_counters(paths).entries == ()


def test_runtime_mailbox_add_task_spec_and_idea_apply_payloads(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    task_doc = _task_doc("task-mailbox-001", created_at=NOW)
    spec_doc = _spec_doc("spec-mailbox-001", created_at=NOW)
    write_mailbox_command(
        paths,
        _mailbox_command(
            "cmd-add-task",
            "add_task",
            payload={"document": task_doc.model_dump(mode="json")},
        ),
    )
    write_mailbox_command(
        paths,
        _mailbox_command(
            "cmd-add-spec",
            "add_spec",
            payload={"document": spec_doc.model_dump(mode="json")},
        ),
    )
    write_mailbox_command(
        paths,
        _mailbox_command(
            "cmd-add-idea",
            "add_idea",
            payload={"source_name": "idea-mailbox-001.md", "markdown": "# Idea Mailbox 001\n"},
        ),
    )

    engine._drain_mailbox()

    assert (paths.tasks_queue_dir / "task-mailbox-001.md").is_file()
    assert (paths.specs_queue_dir / "spec-mailbox-001.md").is_file()
    assert (paths.root / "ideas" / "inbox" / "idea-mailbox-001.md").is_file()

    snapshot = load_snapshot(paths)
    assert snapshot.queue_depth_execution == 1
    assert snapshot.queue_depth_planning == 1


def test_runtime_mailbox_applies_operator_intervention_before_new_work_claim(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-old", created_at=NOW))
    claim = queue.claim_next_execution_task()
    assert claim is not None
    queue.mark_task_blocked("task-old")
    queue.enqueue_task(_task_doc("task-new", created_at=NOW))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    write_mailbox_command(
        paths,
        _mailbox_command(
            "cmd-supersede-task",
            "supersede_task",
            payload={
                "old_task_id": "task-old",
                "replacement_task_id": "task-new",
                "reason": "operator corrected bad intake",
            },
        ),
    )

    engine._drain_mailbox()

    assert not (paths.tasks_blocked_dir / "task-old.md").exists()
    assert tuple((paths.tasks_blocked_dir / "superseded").glob("task-old.*.md"))
    snapshot = load_snapshot(paths)
    assert snapshot.queue_depth_execution == 1
    event_types = [event.event_type for event in read_runtime_events(paths)]
    assert "task_superseded" in event_types


def test_runtime_mailbox_cancel_supports_custom_family(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    family = _custom_planning_family()
    _persist_custom_family(paths, family)
    queue_dir = paths.runtime_root / family.queue_dirs.queue
    queue_dir.mkdir(parents=True, exist_ok=True)
    source = queue_dir / "custom-001.json"
    source.write_text('{"custom_id":"custom-001"}\n', encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    write_mailbox_command(
        paths,
        _mailbox_command(
            "cmd-cancel-custom",
            "cancel_work_item",
            payload={
                "work_item_id": "custom-001",
                "work_item_family_id": "custom_review",
                "reason": "operator cancelled custom item",
            },
        ),
    )

    engine._drain_mailbox()

    assert not source.exists()
    assert tuple((paths.runtime_root / family.queue_dirs.canceled).glob("custom-001.*.json"))
    events = read_runtime_events(paths)
    applied = [event for event in events if event.event_type == "mailbox_operator_intervention_applied"]
    assert applied[-1].data["work_item_family_id"] == "custom_review"
    assert applied[-1].data["work_item_kind"] is None


def test_retry_active_planning_supports_custom_family_without_kind(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    family = _custom_planning_family()
    _persist_custom_family(paths, family)
    active_dir = paths.runtime_root / family.queue_dirs.active
    active_dir.mkdir(parents=True, exist_ok=True)
    active = active_dir / "custom-active.json"
    active.write_text('{"custom_id":"custom-active"}\n', encoding="utf-8")
    snapshot = load_snapshot(paths)
    save_snapshot(
        paths,
        snapshot.model_copy(
            update={
                "runtime_mode": RuntimeMode.DAEMON,
                "process_running": False,
                "active_runs_by_plane": {
                    Plane.PLANNING: ActiveRunState(
                        plane=Plane.PLANNING,
                        lane_id="planning.main",
                        stage=PlanningStageName.PLANNER,
                        node_id="planner",
                        stage_kind_id="planner",
                        run_id="run-custom-active",
                        compiled_plan_id="bootstrap",
                        compiled_plan_fingerprint="bootstrap",
                        request_kind="active_work_item",
                        work_item_family_id="custom_review",
                        work_item_id="custom-active",
                        active_since=NOW,
                    )
                },
                "active_plane": Plane.PLANNING,
                "active_stage": PlanningStageName.PLANNER,
                "active_run_id": "run-custom-active",
                "active_work_item_family_id": "custom_review",
                "active_work_item_id": "custom-active",
                "updated_at": NOW,
            }
        ),
    )

    result = RuntimeControl(paths).retry_active_planning(reason="operator requested retry")

    assert result.applied is True
    assert "custom_review custom-active" in result.detail
    assert not active.exists()
    assert (paths.runtime_root / family.queue_dirs.queue / "custom-active.json").is_file()


def test_runtime_mailbox_defers_operator_intervention_until_active_run_drains(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-old", created_at=NOW))
    claim = queue.claim_next_execution_task()
    assert claim is not None
    queue.mark_task_blocked("task-old")
    queue.enqueue_task(_task_doc("task-new", created_at=NOW))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    assert engine.snapshot is not None
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "active_runs_by_plane": {
                Plane.EXECUTION: ActiveRunState(
                    plane=Plane.EXECUTION,
                    lane_id="execution.main",
                    stage=ExecutionStageName.BUILDER,
                    node_id="builder",
                    stage_kind_id="builder",
                    run_id="run-active",
                    compiled_plan_id=engine.snapshot.compiled_plan_id,
                    compiled_plan_fingerprint=engine.snapshot.compiled_plan_fingerprint,
                    request_kind="active_work_item",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-in-flight",
                    active_since=NOW,
                )
            },
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, engine.snapshot)

    write_mailbox_command(
        paths,
        _mailbox_command(
            "cmd-supersede-task",
            "supersede_task",
            payload={
                "old_task_id": "task-old",
                "replacement_task_id": "task-new",
                "reason": "operator corrected bad intake",
            },
        ),
    )

    engine._drain_mailbox()

    assert (paths.tasks_blocked_dir / "task-old.md").is_file()
    pending = read_pending_mailbox_commands(paths)
    assert len(pending) == 1
    assert pending[0].command.value == "supersede_task"
    assert pending[0].issued_at == NOW
    event_types = [event.event_type for event in read_runtime_events(paths)]
    assert "operator_intervention_deferred" in event_types

    engine.snapshot = load_snapshot(paths).model_copy(
        update={
            "active_runs_by_plane": {},
            "active_plane": None,
            "active_stage": None,
            "active_node_id": None,
            "active_stage_kind_id": None,
            "active_run_id": None,
            "active_work_item_kind": None,
            "active_work_item_id": None,
            "active_since": None,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, engine.snapshot)

    engine._drain_mailbox()

    assert not (paths.tasks_blocked_dir / "task-old.md").exists()
    assert tuple((paths.tasks_blocked_dir / "superseded").glob("task-old.*.md"))
    assert not read_pending_mailbox_commands(paths)


def test_runtime_mailbox_reload_config_rejects_legacy_run_once_style(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'daemon'\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()

    config_path.write_text("[runtime]\nrun_style = 'once'\n", encoding="utf-8")
    write_mailbox_command(paths, _mailbox_command("cmd-reload-config", "reload_config"))
    engine._drain_mailbox()

    assert engine.config is not None
    assert engine.config.runtime.run_style is RuntimeMode.DAEMON
    snapshot = load_snapshot(paths)
    assert snapshot.runtime_mode is RuntimeMode.DAEMON
    assert snapshot.last_reload_outcome == "failed_retained_previous_plan"
    assert snapshot.last_reload_error is not None
    assert "run_style" in snapshot.last_reload_error
    assert "once" in snapshot.last_reload_error
    event_types = [event.event_type for event in read_runtime_events(paths)]
    assert "runtime_config_reload_failed" in event_types


def test_runtime_mailbox_reload_config_rejects_stale_previous_plan_on_compile_failure(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\ndefault_mode = 'standard_plain'\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    snapshot = engine.startup()
    original_compiled_plan_id = snapshot.compiled_plan_id

    config_path.write_text("[runtime]\ndefault_mode = 'missing_mode'\n", encoding="utf-8")
    write_mailbox_command(paths, _mailbox_command("cmd-reload-failed", "reload_config"))
    engine._drain_mailbox()

    reloaded_snapshot = load_snapshot(paths)
    assert reloaded_snapshot.compiled_plan_id == original_compiled_plan_id
    assert reloaded_snapshot.active_mode_id == "default_codex"
    assert reloaded_snapshot.process_running is True
    assert reloaded_snapshot.stop_requested is False
    assert reloaded_snapshot.last_reload_outcome == "failed_retained_previous_plan"
    assert reloaded_snapshot.last_reload_error is not None
    assert "missing_mode" in reloaded_snapshot.last_reload_error
    event_types = [event.event_type for event in read_runtime_events(paths)]
    assert "runtime_config_reload_failed" in event_types


def test_runtime_mailbox_rejects_unsafe_add_payloads(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()

    unsafe_task = _task_doc("task-safe", created_at=NOW).model_dump(mode="json")
    unsafe_task["task_id"] = "../escape"
    write_mailbox_command(
        paths,
        _mailbox_command(
            "cmd-add-task-unsafe",
            "add_task",
            payload={"document": unsafe_task},
        ),
    )
    write_mailbox_command(
        paths,
        _mailbox_command(
            "cmd-add-idea-unsafe",
            "add_idea",
            payload={"source_name": "../escape.md", "markdown": "# Escape\n"},
        ),
    )

    engine._drain_mailbox()

    assert not (paths.root / "escape.md").exists()
    assert not (paths.root / "ideas" / "escape.md").exists()
    failed_archives = sorted(paths.mailbox_failed_dir.glob("*.json"))
    assert len(failed_archives) >= 2


def test_runtime_startup_compile_failure_raises_typed_runtime_error(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="missing_mode")

    with pytest.raises(RuntimeLifecycleError, match="missing_mode"):
        engine.startup()


def test_runtime_startup_rejects_stale_existing_plan_after_input_change(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    initial_engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    initial_engine.startup()
    initial_engine.close()

    compiled_plan_path = paths.state_dir / "compiled_plan.json"
    compiled_before = compiled_plan_path.read_bytes()

    config_path.write_text("[runtime]\ndefault_mode = 'missing_mode'\n", encoding="utf-8")
    stale_engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)

    with pytest.raises(RuntimeLifecycleError, match="missing_mode"):
        stale_engine.startup()

    assert compiled_plan_path.read_bytes() == compiled_before


def test_runtime_startup_rejects_second_daemon_for_same_workspace(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    first = RuntimeEngine(paths, stage_runner=stage_runner)
    second = RuntimeEngine(paths, stage_runner=stage_runner)

    first.startup()

    with pytest.raises(RuntimeLifecycleError, match="workspace runtime ownership lock") as excinfo:
        second.startup()

    assert isinstance(excinfo.value.__cause__, RuntimeOwnershipLockError)


def test_runtime_startup_lock_contention_does_not_rewrite_compile_artifacts(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    owner = RuntimeEngine(paths, stage_runner=stage_runner)
    contender = RuntimeEngine(paths, stage_runner=stage_runner)
    owner.startup()

    compiled_plan_path = paths.state_dir / "compiled_plan.json"
    diagnostics_path = paths.state_dir / "compile_diagnostics.json"
    compiled_before = compiled_plan_path.read_bytes()
    diagnostics_before = diagnostics_path.read_bytes()

    with pytest.raises(RuntimeLifecycleError, match="workspace runtime ownership lock") as excinfo:
        contender.startup()

    assert isinstance(excinfo.value.__cause__, RuntimeOwnershipLockError)

    assert compiled_plan_path.read_bytes() == compiled_before
    assert diagnostics_path.read_bytes() == diagnostics_before


def test_runtime_startup_allows_independent_daemon_ownership_per_workspace(tmp_path: Path) -> None:
    workspace_a = bootstrap_workspace(workspace_paths(tmp_path / "workspace-a"))
    workspace_b = bootstrap_workspace(workspace_paths(tmp_path / "workspace-b"))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine_a = RuntimeEngine(workspace_a, stage_runner=stage_runner)
    engine_b = RuntimeEngine(workspace_b, stage_runner=stage_runner)
    engine_a.startup()
    engine_b.startup()

    assert workspace_a.runtime_lock_file.is_file()
    assert workspace_b.runtime_lock_file.is_file()


def test_runtime_daemon_startup_acquires_lock_and_close_releases_it(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'daemon'\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()
    assert paths.runtime_lock_file.is_file()

    engine.close()
    assert paths.runtime_lock_file.exists() is False


def test_runtime_startup_rejects_daemon_when_workspace_lock_is_already_held(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'daemon'\n", encoding="utf-8")
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="external-owner",
    )

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)

    with pytest.raises(RuntimeLifecycleError, match="workspace runtime ownership lock") as excinfo:
        engine.startup()

    assert isinstance(excinfo.value.__cause__, RuntimeOwnershipLockError)


def test_runtime_daemon_lock_contention_does_not_rewrite_compile_artifacts(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'daemon'\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            now=NOW,
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()
    engine.close()

    compiled_plan_path = paths.state_dir / "compiled_plan.json"
    diagnostics_path = paths.state_dir / "compile_diagnostics.json"
    snapshot_path = paths.state_dir / "runtime_snapshot.json"
    compiled_before = compiled_plan_path.read_bytes()
    diagnostics_before = diagnostics_path.read_bytes()
    snapshot_before = snapshot_path.read_bytes()

    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="external-owner",
    )

    contender = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    with pytest.raises(RuntimeLifecycleError, match="workspace runtime ownership lock") as excinfo:
        contender.startup()

    assert isinstance(excinfo.value.__cause__, RuntimeOwnershipLockError)
    assert compiled_plan_path.read_bytes() == compiled_before
    assert diagnostics_path.read_bytes() == diagnostics_before
    assert snapshot_path.read_bytes() == snapshot_before


def test_runtime_tick_stop_releases_daemon_ownership_lock(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    assert paths.runtime_lock_file.is_file()

    write_mailbox_command(paths, _mailbox_command("cmd-stop", "stop"))
    outcome = engine.tick()

    assert outcome.router_decision.reason == "stop_requested"
    assert paths.runtime_lock_file.exists() is False


def test_runtime_tick_stop_without_owned_lock_does_not_release_external_lock(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'daemon'\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()
    status = inspect_runtime_ownership_lock(paths)
    assert status.record is not None
    assert release_runtime_ownership_lock(
        paths,
        owner_session_id=status.record.owner_session_id,
    )
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="external-owner-replaced",
    )

    write_mailbox_command(paths, _mailbox_command("cmd-stop", "stop"))
    outcome = engine.tick()

    assert outcome.router_decision.reason == "stop_requested"
    assert paths.runtime_lock_file.is_file()


def test_runtime_mailbox_reload_config_retains_lock_when_rejecting_legacy_run_once(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'daemon'\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()
    assert paths.runtime_lock_file.is_file()

    config_path.write_text("[runtime]\nrun_style = 'once'\n", encoding="utf-8")
    write_mailbox_command(paths, _mailbox_command("cmd-reload-once", "reload_config"))
    engine._drain_mailbox()

    assert paths.runtime_lock_file.is_file()
    snapshot = load_snapshot(paths)
    assert snapshot.runtime_mode is RuntimeMode.DAEMON
    assert snapshot.last_reload_outcome == "failed_retained_previous_plan"
    assert snapshot.last_reload_error is not None
    assert "run_style" in snapshot.last_reload_error


def test_runtime_mailbox_reload_config_retains_lock_when_reloading_daemon_config(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'daemon'\n", encoding="utf-8")

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage_runner should not be called")

    engine = RuntimeEngine(paths, stage_runner=stage_runner, config_path=config_path)
    engine.startup()
    assert paths.runtime_lock_file.is_file()

    config_path.write_text(
        "[runtime]\nrun_style = 'daemon'\nidle_sleep_seconds = 2.0\n",
        encoding="utf-8",
    )
    write_mailbox_command(paths, _mailbox_command("cmd-reload-daemon", "reload_config"))
    engine._drain_mailbox()

    assert paths.runtime_lock_file.is_file()
    assert engine.config is not None
    assert engine.config.runtime.idle_sleep_seconds == 2.0
    snapshot = load_snapshot(paths)
    assert snapshot.runtime_mode is RuntimeMode.DAEMON


def test_clear_stale_state_direct_clears_stale_runtime_ownership_lock(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=999_999_999,
        owner_session_id="stale-owner",
    )
    assert paths.runtime_lock_file.is_file()

    control = RuntimeControl(paths)
    result = control.clear_stale_state(reason="operator stale ownership recovery")

    assert result.applied is True
    assert "runtime_ownership_lock=cleared_stale" in result.detail
    assert paths.runtime_lock_file.exists() is False


def test_clear_stale_state_prefers_direct_path_for_stale_lock_even_if_snapshot_claims_running(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=999_999_999,
        owner_session_id="stale-owner",
    )
    snapshot = load_snapshot(paths)
    save_snapshot(
        paths,
        snapshot.model_copy(
            update={
                "runtime_mode": RuntimeMode.DAEMON,
                "process_running": True,
                "updated_at": NOW,
            }
        ),
    )

    control = RuntimeControl(paths)
    result = control.clear_stale_state(reason="operator stale ownership recovery")

    assert result.mode == "direct"
    assert result.applied is True
    assert paths.runtime_lock_file.exists() is False
