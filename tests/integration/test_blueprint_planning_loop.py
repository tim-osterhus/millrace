from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel

from millrace_ai.contracts import (
    BlueprintCritiqueDocument,
    BlueprintDraftDocument,
    BlueprintEvaluationDocument,
    BlueprintManifestDocument,
    BlueprintPacketDocument,
    ExecutionStageName,
    ExecutionTerminalResult,
    IncidentDocument,
    PlanningStageName,
    PlanningTerminalResult,
    SpecDocument,
    TaskDocument,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.router import RouterAction
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.completion_behavior import maybe_activate_completion_stage
from millrace_ai.workspace.arbiter_state import load_closure_target_state
from millrace_ai.workspace.blueprint_state import (
    list_blueprint_manifests_for_root,
    read_blueprint_draft,
    read_blueprint_manifest,
)
from millrace_ai.workspace.work_documents import render_work_document

NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _write_idea_doc(paths, idea_id: str) -> None:
    idea_path = paths.root / "ideas" / "inbox" / f"{idea_id}.md"
    idea_path.parent.mkdir(parents=True, exist_ok=True)
    idea_path.write_text(f"# {idea_id}\n\nBlueprint E2E seed.\n", encoding="utf-8")


def _root_spec_doc(spec_id: str = "spec-blueprint-001") -> SpecDocument:
    return SpecDocument(
        spec_id=spec_id,
        title="Blueprint Planning E2E",
        summary="Exercise Blueprint Manager, Contractor, Evaluator, generated task, and Arbiter.",
        source_type="idea",
        source_id="idea-blueprint-001",
        root_idea_id="idea-blueprint-001",
        root_spec_id=spec_id,
        goals=("Blueprint drafts become execution tasks only after evaluation.",),
        constraints=("Runtime owns all queue mutations.",),
        target_paths=("src/millrace_ai/runtime/blueprint_effects.py",),
        acceptance=("Generated tasks complete before Arbiter closes the root.",),
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
        created_by="tests",
    )


def _arbiter_incident_doc() -> IncidentDocument:
    return IncidentDocument(
        incident_id="incident-blueprint-gap-001",
        title="Blueprint Arbiter Gap",
        summary="Arbiter found a remediation gap under an existing closure root.",
        root_idea_id="idea-blueprint-001",
        root_spec_id="spec-blueprint-001",
        source_spec_id="spec-blueprint-001",
        source_stage="arbiter",
        source_plane="planning",
        failure_class="arbiter_parity_gap",
        trigger_reason="arbiter_remediation_needed",
        consultant_decision="needs_planning",
        opened_at=NOW,
        opened_by="arbiter",
    )


def _child_spec_from_incident() -> SpecDocument:
    return SpecDocument(
        spec_id="spec-blueprint-gap-child-001",
        title="Blueprint Gap Child Spec",
        summary="Child remediation spec emitted by Planner from the Arbiter incident.",
        source_type="incident",
        source_id="incident-blueprint-gap-001",
        parent_spec_id="spec-blueprint-001",
        root_idea_id="idea-blueprint-001",
        root_spec_id="spec-blueprint-001",
        goals=("Represent the Arbiter gap as its own spec.",),
        constraints=("The source incident must not also go to Manager Blueprint.",),
        acceptance=("Planner resolves the incident after emitting this child spec.",),
        references=("millrace-agents/incidents/active/incident-blueprint-gap-001.md",),
        created_at=NOW + timedelta(minutes=4),
        created_by="planner",
    )


def _manifest(
    manifest_id: str = "manifest-blueprint-001",
    *,
    source_work_item_kind: str = "spec",
    source_work_item_id: str = "spec-blueprint-001",
    source_spec_id: str = "spec-blueprint-001",
    draft_ids: tuple[str, ...] = ("draft-blueprint-001", "draft-blueprint-002"),
    created_at: datetime = NOW,
) -> BlueprintManifestDocument:
    return BlueprintManifestDocument(
        manifest_id=manifest_id,
        root_spec_id="spec-blueprint-001",
        root_idea_id="idea-blueprint-001",
        source_work_item_kind=source_work_item_kind,
        source_work_item_id=source_work_item_id,
        source_spec_id=source_spec_id,
        draft_ids=draft_ids,
        draft_count=len(draft_ids),
        spec_summary="Build Blueprint-mode runtime execution.",
        decomposition_strategy="Two strict-sequence implementation drafts.",
        global_acceptance_intent=("Each approved blueprint promotes one generated task.",),
        integration_boundary_notes=("Draft two waits for draft one approval.",),
        risk_notes=("Closure must wait for generated execution work.",),
        references=("lab/specs/pending/blueprint.md",),
        created_at=created_at,
    )


def _draft(
    draft_id: str,
    *,
    draft_index: int,
    manifest_id: str = "manifest-blueprint-001",
    depends_on_draft_ids: tuple[str, ...] | None = None,
    created_at: datetime | None = None,
) -> BlueprintDraftDocument:
    previous = (
        depends_on_draft_ids
        if depends_on_draft_ids is not None
        else (() if draft_index == 1 else ("draft-blueprint-001",))
    )
    return BlueprintDraftDocument(
        draft_id=draft_id,
        manifest_id=manifest_id,
        root_spec_id="spec-blueprint-001",
        root_idea_id="idea-blueprint-001",
        source_spec_id="spec-blueprint-001",
        draft_index=draft_index,
        depends_on_draft_ids=previous,
        title=f"Blueprint Draft {draft_index}",
        summary=f"Implement Blueprint slice {draft_index}.",
        scope=("src/millrace_ai/runtime/blueprint_effects.py",),
        non_goals=("dashboard rendering",),
        target_paths=("src/millrace_ai/runtime/blueprint_effects.py",),
        acceptance_intent=(f"Draft {draft_index} promotes cleanly.",),
        verification_intent=("pytest tests/integration/test_blueprint_planning_loop.py -q",),
        dependency_notes=("Previous draft must be approved.",) if previous else (),
        integration_boundary_notes=("Runtime effect runs before lifecycle completion.",),
        context_excerpt=f"Blueprint draft {draft_index} E2E context.",
        current_revision=0,
        references=("lab/specs/pending/blueprint.md",),
        created_at=created_at or NOW + timedelta(minutes=draft_index),
    )


def _packet(
    draft_id: str,
    *,
    revision: int = 1,
    manifest_id: str = "manifest-blueprint-001",
    root_spec_id: str = "spec-blueprint-001",
    root_idea_id: str = "idea-blueprint-001",
    references: tuple[str, ...] = ("lab/specs/pending/blueprint.md",),
) -> BlueprintPacketDocument:
    index = draft_id.rsplit("-", 1)[-1]
    blueprint_id = f"blueprint-{draft_id}-r{revision}"
    return BlueprintPacketDocument(
        blueprint_id=blueprint_id,
        draft_id=draft_id,
        manifest_id=manifest_id,
        root_spec_id=root_spec_id,
        root_idea_id=root_idea_id,
        revision=revision,
        title=f"Implement {draft_id}",
        implementation_scope=(f"Implement draft {index} runtime behavior.",),
        intended_files=("src/millrace_ai/runtime/blueprint_effects.py",),
        design_decisions=("Use runtime-owned effects and queue lifecycle transitions.",),
        verification_plan=("pytest tests/integration/test_blueprint_planning_loop.py -q",),
        task_acceptance=(f"{draft_id} accepted by E2E.",),
        required_checks=("pytest tests/integration/test_blueprint_planning_loop.py -q",),
        risk_notes=("Queue mutation ordering must be deterministic.",),
        references=references,
        created_at=NOW + timedelta(minutes=revision),
    )


def _evaluation(
    packet: BlueprintPacketDocument,
    *,
    decision: str = "approved",
    critique_id: str | None = None,
) -> BlueprintEvaluationDocument:
    return BlueprintEvaluationDocument(
        evaluation_id=f"evaluation-{packet.blueprint_id}",
        blueprint_id=packet.blueprint_id,
        draft_id=packet.draft_id,
        manifest_id=packet.manifest_id,
        root_spec_id=packet.root_spec_id,
        root_idea_id=packet.root_idea_id,
        decision=decision,
        rubric_findings=("Blueprint is complete enough to promote.",),
        lineage_consistency_findings=("Packet lineage matches the active draft.",),
        verification_findings=("Required check is present.",),
        required_task_fields=(
            ("task_id", "target_paths", "acceptance", "required_checks")
            if decision == "approved"
            else ()
        ),
        critique_id=critique_id,
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW + timedelta(minutes=2),
    )


def _critique(packet: BlueprintPacketDocument) -> BlueprintCritiqueDocument:
    return BlueprintCritiqueDocument(
        critique_id=f"critique-{packet.blueprint_id}",
        evaluation_id=f"evaluation-{packet.blueprint_id}",
        blueprint_id=packet.blueprint_id,
        draft_id=packet.draft_id,
        manifest_id=packet.manifest_id,
        root_spec_id=packet.root_spec_id,
        root_idea_id=packet.root_idea_id,
        revision=packet.revision,
        required_changes=("Narrow the implementation plan before promotion.",),
        verification_issues=("Make the generated task check explicit.",),
        blocking_reason="Evaluator requires a revised Blueprint packet.",
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW + timedelta(minutes=2),
    )


def _task(packet: BlueprintPacketDocument) -> TaskDocument:
    return TaskDocument(
        task_id=f"task-{packet.draft_id}",
        title=f"Execute {packet.draft_id}",
        summary="Generated task from an approved Blueprint packet.",
        root_idea_id=packet.root_idea_id,
        root_spec_id=packet.root_spec_id,
        spec_id="spec-blueprint-001",
        target_paths=packet.intended_files,
        acceptance=packet.task_acceptance,
        required_checks=packet.required_checks,
        references=("lab/specs/pending/blueprint.md",),
        risk=packet.risk_notes,
        created_at=NOW + timedelta(minutes=3),
        created_by="evaluator_blueprint",
    )


def _standard_task_doc() -> TaskDocument:
    return TaskDocument(
        task_id="task-standard-001",
        title="Standard Manager Task",
        summary="Generated by the standard Manager flow.",
        root_idea_id="idea-blueprint-001",
        root_spec_id="spec-blueprint-001",
        spec_id="spec-blueprint-001",
        target_paths=("src/millrace_ai/runtime/engine.py",),
        acceptance=("Standard flow remains non-Blueprint.",),
        required_checks=("pytest tests/integration/test_e2e_handoffs.py -q",),
        references=("lab/specs/pending/blueprint.md",),
        risk=("Mode isolation could regress.",),
        created_at=NOW + timedelta(minutes=1),
        created_by="manager",
    )


def _write_json(path: Path, payload: object) -> None:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    elif isinstance(payload, list):
        payload = [
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for item in payload
        ]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_planner_disposition(
    run_dir: Path,
    *,
    source_work_item_family_id: str,
    source_work_item_id: str,
    disposition: str,
    emitted_spec_ids: tuple[str, ...] = (),
    refined_active_source: bool = False,
) -> None:
    _write_json(
        run_dir / "planner_disposition.json",
        {
            "schema_version": "1.0",
            "kind": "planner_disposition",
            "source_work_item_family_id": source_work_item_family_id,
            "source_work_item_id": source_work_item_id,
            "disposition": disposition,
            "emitted_spec_ids": list(emitted_spec_ids),
            "refined_active_source": refined_active_source,
            "recommended_next_action": disposition,
            "created_at": NOW.isoformat().replace("+00:00", "Z"),
            "created_by": "planner",
        },
    )


def _runner_result(
    request: StageRunRequest,
    *,
    terminal: str,
    now: datetime = NOW,
    write_planner_disposition: bool = True,
) -> RunnerRawResult:
    run_dir = Path(request.run_dir)
    stdout_path = run_dir / "runner_stdout.txt"
    stdout_path.write_text(f"### {terminal}\n", encoding="utf-8")
    if write_planner_disposition:
        _write_default_planner_disposition(request, terminal=terminal, run_dir=run_dir, now=now)
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
        started_at=now,
        ended_at=now + timedelta(seconds=1),
    )


def _write_default_planner_disposition(
    request: StageRunRequest,
    *,
    terminal: str,
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
    _write_json(
        run_dir / "planner_disposition.json",
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
    )


def test_blueprint_mode_approval_path_promotes_two_strict_sequence_drafts_and_closes(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    _write_idea_doc(paths, "idea-blueprint-001")
    queue.enqueue_spec(_root_spec_doc())

    stage_kind_order: list[str] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        stage_kind_order.append(request.stage_kind_id)
        run_dir = Path(request.run_dir)
        if request.stage is PlanningStageName.PLANNER:
            return _runner_result(request, terminal=PlanningTerminalResult.PLANNER_COMPLETE.value)
        if request.stage_kind_id == "manager_blueprint":
            _write_json(run_dir / "blueprint_manifest.json", _manifest())
            _write_json(
                run_dir / "blueprint_drafts.json",
                [
                    _draft("draft-blueprint-001", draft_index=1),
                    _draft("draft-blueprint-002", draft_index=2),
                ],
            )
            return _runner_result(request, terminal="MANAGER_BLUEPRINT_COMPLETE")
        if request.stage_kind_id == "contractor_blueprint":
            assert request.active_work_item_id is not None
            packet = _packet(request.active_work_item_id)
            _write_json(run_dir / "blueprint_packet.json", packet)
            (run_dir / "blueprint.md").write_text(
                f"# {packet.title}\n\nRuntime-owned Blueprint packet.\n",
                encoding="utf-8",
            )
            return _runner_result(request, terminal="BLUEPRINT_CANDIDATE_READY")
        if request.stage_kind_id == "evaluator_blueprint":
            assert request.active_work_item_id is not None
            packet = _packet(request.active_work_item_id)
            _write_json(run_dir / "blueprint_evaluation.json", _evaluation(packet))
            (run_dir / "generated_task.md").write_text(
                render_work_document(_task(packet)),
                encoding="utf-8",
            )
            return _runner_result(request, terminal="BLUEPRINT_APPROVED")
        if request.stage is ExecutionStageName.BUILDER:
            return _runner_result(request, terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value)
        if request.stage is ExecutionStageName.CHECKER:
            return _runner_result(request, terminal=ExecutionTerminalResult.CHECKER_PASS.value)
        if request.stage is ExecutionStageName.UPDATER:
            return _runner_result(request, terminal=ExecutionTerminalResult.UPDATE_COMPLETE.value)

        verdict_path = Path(request.preferred_verdict_path)
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        verdict_path.write_text('{"status":"pass"}\n', encoding="utf-8")
        Path(request.preferred_report_path).write_text("# Arbiter Report\n\nClosed.\n", encoding="utf-8")
        return _runner_result(request, terminal=PlanningTerminalResult.ARBITER_COMPLETE.value)

    engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="blueprint_codex")
    engine.startup()
    outcomes = [engine.tick() for _ in range(13)]
    target = load_closure_target_state(paths, root_spec_id="spec-blueprint-001")

    assert [outcome.stage_result.stage_kind_id for outcome in outcomes] == [
        "planner",
        "manager_blueprint",
        "contractor_blueprint",
        "evaluator_blueprint",
        "builder",
        "checker",
        "updater",
        "contractor_blueprint",
        "evaluator_blueprint",
        "builder",
        "checker",
        "updater",
        "arbiter",
    ]
    assert stage_kind_order == [outcome.stage_result.stage_kind_id for outcome in outcomes]
    assert (paths.specs_done_dir / "spec-blueprint-001.md").is_file()
    assert (paths.runtime_root / "blueprints/drafts/approved/draft-blueprint-001.json").is_file()
    assert (paths.runtime_root / "blueprints/drafts/approved/draft-blueprint-002.json").is_file()
    assert (paths.tasks_done_dir / "task-draft-blueprint-001.md").is_file()
    assert (paths.tasks_done_dir / "task-draft-blueprint-002.md").is_file()
    assert not (paths.runtime_root / "blueprints/drafts/queue/draft-blueprint-002.json").exists()
    approved_draft = read_blueprint_draft(
        paths.runtime_root / "blueprints/drafts/approved/draft-blueprint-002.json"
    )
    assert approved_draft.depends_on_draft_ids == ("draft-blueprint-001",)
    assert target.closure_open is False
    assert target.closed_at is not None


def test_blueprint_same_root_arbiter_remediation_uses_distinct_manifest_identity(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    _write_idea_doc(paths, "idea-blueprint-001")
    queue.enqueue_spec(_root_spec_doc())

    original_manifest_id = "manifest-blueprint-001"
    followup_manifest_id = "manifest-blueprint-followup-001"
    followup_draft_ids = (
        "draft-blueprint-followup-001",
        "draft-blueprint-followup-002",
    )
    canonical_original_manifest_path = (
        paths.runtime_root / f"blueprints/manifests/{original_manifest_id}.json"
    )
    legacy_original_manifest_path = (
        paths.runtime_root / "blueprints/manifests/spec-blueprint-001.json"
    )
    canonical_followup_manifest_path = (
        paths.runtime_root / f"blueprints/manifests/{followup_manifest_id}.json"
    )
    evaluator_context_checks: list[tuple[str, str]] = []
    stage_kind_order: list[str] = []
    manager_blueprint_sources: list[tuple[str | None, str | None]] = []
    arbiter_attempts = 0

    def _packet_for_active_draft(request: StageRunRequest) -> BlueprintPacketDocument:
        assert request.active_work_item_path is not None
        draft = read_blueprint_draft(Path(request.active_work_item_path))
        return _packet(
            draft.draft_id,
            revision=draft.current_revision + 1,
            manifest_id=draft.manifest_id,
            root_spec_id=draft.root_spec_id,
            root_idea_id=draft.root_idea_id,
        )

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal arbiter_attempts
        stage_kind_order.append(request.stage_kind_id)
        run_dir = Path(request.run_dir)
        if request.stage is PlanningStageName.AUDITOR:
            return _runner_result(request, terminal=PlanningTerminalResult.AUDITOR_COMPLETE.value)
        if request.stage is PlanningStageName.PLANNER:
            if request.active_work_item_family_id == "incident":
                assert request.active_work_item_id is not None
                _write_planner_disposition(
                    run_dir,
                    source_work_item_family_id="incident",
                    source_work_item_id=request.active_work_item_id,
                    disposition="active_source_ready_for_manager",
                    refined_active_source=False,
                )
            return _runner_result(request, terminal=PlanningTerminalResult.PLANNER_COMPLETE.value)
        if request.stage_kind_id == "manager_blueprint":
            manager_blueprint_sources.append(
                (request.active_work_item_family_id, request.active_work_item_id)
            )
            if request.active_work_item_family_id == "incident":
                assert request.active_work_item_id is not None
                _write_json(
                    run_dir / "blueprint_manifest.json",
                    _manifest(
                        followup_manifest_id,
                        source_work_item_kind="incident",
                        source_work_item_id=request.active_work_item_id,
                        draft_ids=followup_draft_ids,
                        created_at=NOW + timedelta(minutes=20),
                    ),
                )
                _write_json(
                    run_dir / "blueprint_drafts.json",
                    [
                        _draft(
                            followup_draft_ids[0],
                            draft_index=1,
                            manifest_id=followup_manifest_id,
                            created_at=NOW + timedelta(minutes=21),
                        ),
                        _draft(
                            followup_draft_ids[1],
                            draft_index=2,
                            manifest_id=followup_manifest_id,
                            depends_on_draft_ids=(followup_draft_ids[0],),
                            created_at=NOW + timedelta(minutes=22),
                        ),
                    ],
                )
            else:
                _write_json(run_dir / "blueprint_manifest.json", _manifest())
                _write_json(
                    run_dir / "blueprint_drafts.json",
                    [
                        _draft("draft-blueprint-001", draft_index=1),
                        _draft("draft-blueprint-002", draft_index=2),
                    ],
                )
            return _runner_result(request, terminal="MANAGER_BLUEPRINT_COMPLETE")
        if request.stage_kind_id == "contractor_blueprint":
            packet = _packet_for_active_draft(request)
            _write_json(run_dir / "blueprint_packet.json", packet)
            (run_dir / "blueprint.md").write_text(
                f"# {packet.title}\n\nRuntime-owned Blueprint packet.\n",
                encoding="utf-8",
            )
            return _runner_result(request, terminal="BLUEPRINT_CANDIDATE_READY")
        if request.stage_kind_id == "evaluator_blueprint":
            assert request.active_work_item_path is not None
            draft = read_blueprint_draft(Path(request.active_work_item_path))
            legacy_ref = legacy_original_manifest_path.relative_to(paths.root).as_posix()
            followup_ref = canonical_followup_manifest_path.relative_to(paths.root).as_posix()
            if draft.manifest_id == original_manifest_id:
                assert any(legacy_ref in ref for ref in request.context_artifact_refs)
                assert not any(
                    f"blueprints/manifests/{original_manifest_id}.json" in ref
                    for ref in request.context_artifact_refs
                )
                evaluator_context_checks.append(("original", draft.draft_id))
            elif draft.manifest_id == followup_manifest_id:
                assert any(followup_ref in ref for ref in request.context_artifact_refs)
                assert not any(legacy_ref in ref for ref in request.context_artifact_refs)
                evaluator_context_checks.append(("followup", draft.draft_id))
            packet = _packet_for_active_draft(request)
            _write_json(run_dir / "blueprint_evaluation.json", _evaluation(packet))
            (run_dir / "generated_task.md").write_text(
                render_work_document(_task(packet)),
                encoding="utf-8",
            )
            return _runner_result(request, terminal="BLUEPRINT_APPROVED")
        if request.stage is ExecutionStageName.BUILDER:
            return _runner_result(request, terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value)
        if request.stage is ExecutionStageName.CHECKER:
            return _runner_result(request, terminal=ExecutionTerminalResult.CHECKER_PASS.value)
        if request.stage is ExecutionStageName.UPDATER:
            return _runner_result(request, terminal=ExecutionTerminalResult.UPDATE_COMPLETE.value)

        arbiter_attempts += 1
        verdict_path = Path(request.preferred_verdict_path)
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        terminal = (
            PlanningTerminalResult.REMEDIATION_NEEDED.value
            if arbiter_attempts == 1
            else PlanningTerminalResult.ARBITER_COMPLETE.value
        )
        verdict_path.write_text(
            '{"status":"gap"}\n' if arbiter_attempts == 1 else '{"status":"pass"}\n',
            encoding="utf-8",
        )
        Path(request.preferred_report_path).write_text(
            "# Arbiter Report\n\n"
            + ("Parity gaps remain.\n" if arbiter_attempts == 1 else "Closed.\n"),
            encoding="utf-8",
        )
        return _runner_result(request, terminal=terminal)

    engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="blueprint_codex")
    engine.startup()
    outcomes = [engine.tick(), engine.tick()]

    assert stage_kind_order == ["planner", "manager_blueprint"]
    assert canonical_original_manifest_path.is_file()
    legacy_original_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_original_manifest_path.replace(legacy_original_manifest_path)
    assert not canonical_original_manifest_path.exists()
    assert read_blueprint_manifest(paths, original_manifest_id).manifest_id == original_manifest_id

    outcomes.extend(engine.tick() for _ in range(11))

    assert stage_kind_order[:13] == [
        "planner",
        "manager_blueprint",
        "contractor_blueprint",
        "evaluator_blueprint",
        "builder",
        "checker",
        "updater",
        "contractor_blueprint",
        "evaluator_blueprint",
        "builder",
        "checker",
        "updater",
        "arbiter",
    ]
    assert arbiter_attempts == 1
    target = load_closure_target_state(paths, root_spec_id="spec-blueprint-001")
    assert target.closure_open is True
    assert (paths.tasks_done_dir / "task-draft-blueprint-001.md").is_file()
    assert (paths.tasks_done_dir / "task-draft-blueprint-002.md").is_file()

    outcomes.extend(engine.tick() for _ in range(3))

    assert stage_kind_order[13:16] == ["auditor", "planner", "manager_blueprint"]
    assert manager_blueprint_sources[-1][0] == "incident"
    assert canonical_followup_manifest_path.is_file()
    assert (paths.runtime_root / f"blueprints/drafts/queue/{followup_draft_ids[0]}.json").is_file()
    assert (paths.runtime_root / f"blueprints/drafts/queue/{followup_draft_ids[1]}.json").is_file()
    assert {
        manifest.manifest_id
        for manifest in list_blueprint_manifests_for_root(paths, "spec-blueprint-001")
    } == {original_manifest_id, followup_manifest_id}
    assert read_blueprint_manifest(paths, original_manifest_id).manifest_id == original_manifest_id
    assert read_blueprint_manifest(paths, followup_manifest_id).manifest_id == followup_manifest_id
    assert not any(
        outcome.router_decision.failure_class
        in {"blueprint_manifest_invalid", "blueprint_manifest_duplicate"}
        for outcome in outcomes
    )

    assert maybe_activate_completion_stage(engine) is None
    blocked_target = load_closure_target_state(paths, root_spec_id="spec-blueprint-001")
    assert blocked_target.closure_open is True
    assert blocked_target.closure_blocked_by_lineage_work is True
    assert any(
        ref.work_item_id == followup_draft_ids[0]
        and ref.reason == "open_blueprint_draft"
        for ref in blocked_target.blocking_work_refs
    )

    outcomes.extend(engine.tick() for _ in range(11))

    followup_stage_order = stage_kind_order[16:]
    assert followup_stage_order[-1] == "arbiter"
    assert followup_stage_order.count("contractor_blueprint") == 2
    assert followup_stage_order.count("evaluator_blueprint") == 2
    assert followup_stage_order.count("builder") == 2
    assert followup_stage_order.count("checker") == 2
    assert followup_stage_order.count("updater") == 2
    assert (paths.tasks_done_dir / f"task-{followup_draft_ids[0]}.md").is_file()
    assert (paths.tasks_done_dir / f"task-{followup_draft_ids[1]}.md").is_file()
    assert {
        ("original", "draft-blueprint-001"),
        ("original", "draft-blueprint-002"),
        ("followup", followup_draft_ids[0]),
        ("followup", followup_draft_ids[1]),
    }.issubset(set(evaluator_context_checks))
    final_target = load_closure_target_state(paths, root_spec_id="spec-blueprint-001")
    assert final_target.closure_open is False
    assert final_target.closed_at is not None


def test_blueprint_mode_rejection_cycle_returns_to_contractor_before_approval(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    _write_idea_doc(paths, "idea-blueprint-001")
    queue.enqueue_spec(_root_spec_doc())

    stage_kind_order: list[str] = []
    evaluator_attempts_by_draft: dict[str, int] = {}

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        stage_kind_order.append(request.stage_kind_id)
        run_dir = Path(request.run_dir)
        if request.stage is PlanningStageName.PLANNER:
            return _runner_result(request, terminal=PlanningTerminalResult.PLANNER_COMPLETE.value)
        if request.stage_kind_id == "manager_blueprint":
            _write_json(run_dir / "blueprint_manifest.json", _manifest())
            _write_json(
                run_dir / "blueprint_drafts.json",
                [
                    _draft("draft-blueprint-001", draft_index=1),
                    _draft("draft-blueprint-002", draft_index=2),
                ],
            )
            return _runner_result(request, terminal="MANAGER_BLUEPRINT_COMPLETE")
        if request.stage_kind_id == "contractor_blueprint":
            assert request.active_work_item_path is not None
            draft = read_blueprint_draft(Path(request.active_work_item_path))
            references = ("lab/specs/pending/blueprint.md",)
            if draft.latest_critique_id is not None:
                references = (*references, f"millrace-agents/blueprints/critiques/open/{draft.latest_critique_id}.json")
            packet = _packet(
                draft.draft_id,
                revision=draft.current_revision + 1,
                references=references,
            )
            _write_json(run_dir / "blueprint_packet.json", packet)
            (run_dir / "blueprint.md").write_text(
                f"# {packet.title}\n\nRevision {packet.revision}.\n",
                encoding="utf-8",
            )
            return _runner_result(request, terminal="BLUEPRINT_CANDIDATE_READY")
        if request.stage_kind_id == "evaluator_blueprint":
            assert request.active_work_item_path is not None
            draft = read_blueprint_draft(Path(request.active_work_item_path))
            packet = _packet(draft.draft_id, revision=draft.current_revision + 1)
            evaluator_attempts_by_draft[draft.draft_id] = (
                evaluator_attempts_by_draft.get(draft.draft_id, 0) + 1
            )
            if draft.draft_id == "draft-blueprint-001" and evaluator_attempts_by_draft[draft.draft_id] == 1:
                critique = _critique(packet)
                _write_json(
                    run_dir / "blueprint_evaluation.json",
                    _evaluation(packet, decision="rejected", critique_id=critique.critique_id),
                )
                _write_json(run_dir / "critique_packet.json", critique)
                return _runner_result(request, terminal="BLUEPRINT_REJECTED")

            _write_json(run_dir / "blueprint_evaluation.json", _evaluation(packet))
            (run_dir / "generated_task.md").write_text(
                render_work_document(_task(packet)),
                encoding="utf-8",
            )
            return _runner_result(request, terminal="BLUEPRINT_APPROVED")
        if request.stage is ExecutionStageName.BUILDER:
            return _runner_result(request, terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value)
        if request.stage is ExecutionStageName.CHECKER:
            return _runner_result(request, terminal=ExecutionTerminalResult.CHECKER_PASS.value)
        if request.stage is ExecutionStageName.UPDATER:
            return _runner_result(request, terminal=ExecutionTerminalResult.UPDATE_COMPLETE.value)

        verdict_path = Path(request.preferred_verdict_path)
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        verdict_path.write_text('{"status":"pass"}\n', encoding="utf-8")
        Path(request.preferred_report_path).write_text("# Arbiter Report\n\nClosed.\n", encoding="utf-8")
        return _runner_result(request, terminal=PlanningTerminalResult.ARBITER_COMPLETE.value)

    engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="blueprint_codex")
    engine.startup()
    outcomes = [engine.tick() for _ in range(15)]

    assert [outcome.stage_result.stage_kind_id for outcome in outcomes[:6]] == [
        "planner",
        "manager_blueprint",
        "contractor_blueprint",
        "evaluator_blueprint",
        "contractor_blueprint",
        "evaluator_blueprint",
    ]
    assert evaluator_attempts_by_draft["draft-blueprint-001"] == 2
    assert (
        paths.runtime_root
        / "blueprints/packets/rejected/blueprint-draft-blueprint-001-r1.json"
    ).is_file()
    assert (
        paths.runtime_root
        / "blueprints/critiques/open/critique-blueprint-draft-blueprint-001-r1.json"
    ).is_file()
    assert (
        paths.runtime_root
        / "blueprints/packets/approved/blueprint-draft-blueprint-001-r2.json"
    ).is_file()
    assert (paths.tasks_done_dir / "task-draft-blueprint-001.md").is_file()
    assert outcomes[-1].stage_result.stage_kind_id == "arbiter"
    assert load_closure_target_state(paths, root_spec_id="spec-blueprint-001").closure_open is False


def test_default_mode_keeps_standard_manager_flow_non_blueprint(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    _write_idea_doc(paths, "idea-blueprint-001")
    queue.enqueue_spec(_root_spec_doc())

    stage_kind_order: list[str] = []
    task_created = False

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        nonlocal task_created
        stage_kind_order.append(request.stage_kind_id)
        if request.stage is PlanningStageName.PLANNER:
            return _runner_result(request, terminal=PlanningTerminalResult.PLANNER_COMPLETE.value)
        if request.stage is PlanningStageName.MANAGER:
            if not task_created:
                queue.enqueue_task(_standard_task_doc())
                task_created = True
            return _runner_result(request, terminal=PlanningTerminalResult.MANAGER_COMPLETE.value)
        if request.stage is ExecutionStageName.BUILDER:
            return _runner_result(request, terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value)
        if request.stage is ExecutionStageName.CHECKER:
            return _runner_result(request, terminal=ExecutionTerminalResult.CHECKER_PASS.value)
        if request.stage is ExecutionStageName.UPDATER:
            return _runner_result(request, terminal=ExecutionTerminalResult.UPDATE_COMPLETE.value)

        verdict_path = Path(request.preferred_verdict_path)
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        verdict_path.write_text('{"status":"pass"}\n', encoding="utf-8")
        Path(request.preferred_report_path).write_text("# Arbiter Report\n\nClosed.\n", encoding="utf-8")
        return _runner_result(request, terminal=PlanningTerminalResult.ARBITER_COMPLETE.value)

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    outcomes = [engine.tick() for _ in range(6)]

    assert [outcome.stage_result.stage_kind_id for outcome in outcomes] == [
        "planner",
        "manager",
        "builder",
        "checker",
        "updater",
        "arbiter",
    ]
    assert stage_kind_order == [outcome.stage_result.stage_kind_id for outcome in outcomes]
    assert all("blueprint" not in stage_kind_id for stage_kind_id in stage_kind_order)
    assert not any((paths.runtime_root / "blueprints" / "drafts" / "queue").glob("*.json"))
    assert (paths.tasks_done_dir / "task-standard-001.md").is_file()


def test_blueprint_planner_child_spec_disposition_resolves_arbiter_incident_without_manager(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_incident(_arbiter_incident_doc())

    stage_kind_order: list[str] = []
    manager_blueprint_sources: list[tuple[str | None, str | None]] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        stage_kind_order.append(request.stage_kind_id)
        run_dir = Path(request.run_dir)
        if request.stage is PlanningStageName.AUDITOR:
            return _runner_result(request, terminal=PlanningTerminalResult.AUDITOR_COMPLETE.value)
        if request.stage is PlanningStageName.PLANNER:
            if request.active_work_item_family_id == "incident":
                queue.enqueue_spec(_child_spec_from_incident())
                _write_planner_disposition(
                    run_dir,
                    source_work_item_family_id="incident",
                    source_work_item_id="incident-blueprint-gap-001",
                    disposition="emitted_child_specs",
                    emitted_spec_ids=("spec-blueprint-gap-child-001",),
                )
            else:
                _write_planner_disposition(
                    run_dir,
                    source_work_item_family_id="spec",
                    source_work_item_id="spec-blueprint-gap-child-001",
                    disposition="active_source_ready_for_manager",
                    refined_active_source=False,
                )
            return _runner_result(request, terminal=PlanningTerminalResult.PLANNER_COMPLETE.value)
        if request.stage_kind_id == "manager_blueprint":
            manager_blueprint_sources.append(
                (request.active_work_item_family_id, request.active_work_item_id)
            )
            return _runner_result(request, terminal="MANAGER_BLUEPRINT_COMPLETE")
        raise AssertionError(f"unexpected stage in regression: {request.stage_kind_id}")

    engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="blueprint_codex")
    engine.startup()
    outcomes = [engine.tick() for _ in range(3)]

    assert [outcome.stage_result.stage_kind_id for outcome in outcomes] == [
        "auditor",
        "planner",
        "planner",
    ]
    assert stage_kind_order == ["auditor", "planner", "planner"]
    assert manager_blueprint_sources == []
    assert (paths.incidents_resolved_dir / "incident-blueprint-gap-001.md").is_file()
    assert (paths.specs_active_dir / "spec-blueprint-gap-child-001.md").is_file()


def test_blueprint_planner_missing_disposition_blocks_incident_without_manager(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_incident(_arbiter_incident_doc())

    stage_kind_order: list[str] = []
    manager_blueprint_sources: list[tuple[str | None, str | None]] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        stage_kind_order.append(request.stage_kind_id)
        if request.stage is PlanningStageName.AUDITOR:
            return _runner_result(request, terminal=PlanningTerminalResult.AUDITOR_COMPLETE.value)
        if request.stage is PlanningStageName.PLANNER:
            queue.enqueue_spec(_child_spec_from_incident())
            return _runner_result(
                request,
                terminal=PlanningTerminalResult.PLANNER_COMPLETE.value,
                write_planner_disposition=False,
            )
        if request.stage_kind_id == "manager_blueprint":
            manager_blueprint_sources.append(
                (request.active_work_item_family_id, request.active_work_item_id)
            )
            return _runner_result(request, terminal="MANAGER_BLUEPRINT_COMPLETE")
        raise AssertionError(f"unexpected stage in regression: {request.stage_kind_id}")

    engine = RuntimeEngine(paths, stage_runner=stage_runner, mode_id="blueprint_codex")
    engine.startup()
    outcomes = [engine.tick() for _ in range(2)]

    assert [outcome.stage_result.stage_kind_id for outcome in outcomes] == [
        "auditor",
        "planner",
    ]
    assert outcomes[-1].router_decision.action is RouterAction.BLOCKED
    assert outcomes[-1].router_decision.failure_class == "planner_disposition_missing"
    assert stage_kind_order == ["auditor", "planner"]
    assert manager_blueprint_sources == []
    assert (paths.incidents_blocked_dir / "incident-blueprint-gap-001.md").is_file()
    assert (paths.specs_queue_dir / "spec-blueprint-gap-child-001.md").is_file()
