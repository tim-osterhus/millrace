from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from millrace_ai.contracts import (
    ArbiterVerdict,
    ClosureEvidenceWindow,
    ClosureTargetState,
    ExecutionStageName,
    IncidentDocument,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    ResultClass,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.contracts.router import RouterAction, RouterDecision
from millrace_ai.events import read_runtime_events
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.handoff_incidents import enqueue_handoff_incident
from millrace_ai.runtime.result_application import apply_router_decision
from millrace_ai.state_store import load_snapshot
from millrace_ai.work_documents import read_work_document_as
from millrace_ai.workspace.arbiter_state import load_closure_target_state, save_closure_target_state

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _unused_stage_runner(request: StageRunRequest) -> RunnerRawResult:
    raise AssertionError(f"stage runner should not be called: {request.stage.value}")


def _target_state() -> ClosureTargetState:
    return ClosureTargetState(
        root_spec_id="spec-root-001",
        root_idea_id="idea-001",
        root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
        root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
        rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
        latest_verdict_path="millrace-agents/arbiter/verdicts/spec-root-001.json",
        latest_report_path="millrace-agents/arbiter/reports/run-prev.md",
        closure_open=True,
        closure_blocked_by_lineage_work=False,
        blocking_work_ids=(),
        opened_at=NOW,
        last_arbiter_run_id="run-prev",
    )


def _write_verdict(paths, *, status: str, provenance: tuple[str, ...] = ()) -> Path:
    verdict_path = paths.arbiter_verdicts_dir / "spec-root-001.json"
    verdict_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"status": status}
    if provenance:
        payload["criteria"] = [
            {
                "criterion_id": f"criterion-{index}",
                "status": status,
                "provenance": item,
            }
            for index, item in enumerate(provenance, start=1)
        ]
    verdict_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return verdict_path


def _write_current_style_verdict(
    paths,
    *,
    provenance: tuple[str, ...],
    run_id: str = "run-current",
    criterion_roles: tuple[str | None, ...] | None = None,
) -> Path:
    verdict_path = paths.arbiter_verdicts_dir / "spec-root-001.json"
    verdict_path.parent.mkdir(parents=True, exist_ok=True)
    report_path = _write_report(paths, run_id=run_id)
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "kind": "arbiter_verdict",
        "root_spec_id": "spec-root-001",
        "root_idea_id": "idea-001",
        "request_id": "request-current",
        "run_id": run_id,
        "decided_at": NOW.isoformat().replace("+00:00", "Z"),
        "status": "complete",
        "terminal_result": "ARBITER_COMPLETE",
        "result_class": "success",
        "rubric_path": "millrace-agents/arbiter/rubrics/spec-root-001.md",
        "report_path": str(report_path.relative_to(paths.root)),
        "remediation_incident_path": None,
        "summary": "Current-style durable verdict with machine-checkable provenance.",
        "checks": [
            {
                "command": "uv run --extra dev pytest -q tests/runtime/test_closure_transitions.py",
                "result": "pass",
                "summary": "focused runtime coverage passed",
            }
        ],
        "parity_gaps": [],
        "remediation_guidance": [],
        "residual_uncertainty": [
            "Current-style durable verdict parsing should retain typed provenance."
        ],
        "criteria": [],
    }
    criteria: list[dict[str, object]] = []
    for index, item in enumerate(provenance, start=1):
        criterion: dict[str, object] = {
            "id": f"criterion-{index}",
            "title": f"Criterion {index}",
            "status": "pass",
            "evidence_depth": "current durable artifact",
            "evidence": [f"evidence-{index}"],
            "provenance": item,
        }
        if criterion_roles is not None:
            role = criterion_roles[index - 1]
            if role is not None:
                criterion["criterion_role"] = role
        criteria.append(criterion)
    payload["criteria"] = criteria
    verdict_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return verdict_path


def _write_report(paths, *, run_id: str = "run-current") -> Path:
    report_path = paths.runs_dir / run_id / "arbiter_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# Arbiter Report\n\nResult.\n", encoding="utf-8")
    return report_path


def _write_window(paths, *, with_remediation: bool) -> Path:
    run_dir = paths.runs_dir / "run-current"
    run_dir.mkdir(parents=True, exist_ok=True)
    completed_lineage_evidence = ()
    if with_remediation:
        completed_lineage_evidence = (
            {
                "run_id": "run-remediate",
                "request_id": "request-remediate",
                "plane": Plane.EXECUTION,
                "stage": ExecutionStageName.BUILDER,
                "work_item_family_id": "task",
                "work_item_id": "task-remediate-001",
                "terminal_result": "BUILDER_COMPLETE",
                "completed_at": NOW + timedelta(minutes=5),
                "stage_result_path": (
                    "millrace-agents/runs/run-remediate/stage_results/request-remediate.json"
                ),
            },
        )
    window = ClosureEvidenceWindow(
        root_spec_id="spec-root-001",
        current_arbiter_run_id="run-current",
        current_arbiter_request_id="request-current",
        previous_arbiter={
            "run_id": "run-prev" if with_remediation else None,
            "request_id": "request-prev" if with_remediation else None,
            "verdict_path": "millrace-agents/arbiter/verdicts/spec-root-001.json"
            if with_remediation
            else None,
            "report_path": "millrace-agents/arbiter/reports/run-prev.md"
            if with_remediation
            else None,
            "completed_at": NOW if with_remediation else None,
        },
        freshness_watermark_at=NOW if with_remediation else None,
        completed_lineage_evidence=completed_lineage_evidence,
    )
    path = run_dir / "closure_evidence_window.json"
    path.write_text(window.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _stage_result(
    paths,
    *,
    terminal_result: PlanningTerminalResult,
    result_class: ResultClass,
    success: bool,
    report_path: Path,
    verdict_path: Path,
    window_path: Path,
) -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id="run-current",
        plane=Plane.PLANNING,
        stage=PlanningStageName.ARBITER,
        node_id="arbiter",
        stage_kind_id="arbiter",
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-root-001",
        terminal_result=terminal_result,
        result_class=result_class,
        summary_status_marker=f"### {terminal_result.value}",
        success=success,
        report_artifact=str(report_path),
        metadata={
            "request_kind": "closure_target",
            "closure_target_root_spec_id": "spec-root-001",
            "closure_evidence_window_path": str(window_path),
            "preferred_verdict_path": str(verdict_path),
        },
        started_at=NOW,
        completed_at=NOW,
    )


def _start_engine(paths) -> RuntimeEngine:
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    return engine


def test_arbiter_verdict_parses_machine_checkable_criterion_provenance() -> None:
    verdict = ArbiterVerdict.model_validate(
        {
            "status": "pass",
            "criteria_evidence": [
                {"criterion_id": "fresh", "provenance": "fresh"},
                {"criterion_id": "revalidated", "provenance": "revalidated"},
                {"criterion_id": "historical", "provenance": "historical_only"},
                {"criterion_id": "missing", "provenance": "missing"},
            ],
        }
    )

    assert tuple(item.value for item in verdict.decision_provenance) == (
        "fresh",
        "revalidated",
        "historical_only",
        "missing",
    )


def test_arbiter_verdict_parses_current_style_durable_payload_with_provenance(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    verdict_path = _write_current_style_verdict(paths, provenance=("fresh",))

    verdict = ArbiterVerdict.model_validate_json(verdict_path.read_text(encoding="utf-8"))

    assert verdict.root_spec_id == "spec-root-001"
    assert verdict.root_idea_id == "idea-001"
    assert verdict.request_id == "request-current"
    assert verdict.run_id == "run-current"
    assert verdict.report_path == "millrace-agents/runs/run-current/arbiter_report.md"
    assert verdict.criteria[0].criterion_id == "criterion-1"
    assert verdict.criteria[0].title == "Criterion 1"
    assert verdict.criteria[0].evidence_depth == "current durable artifact"
    assert verdict.criteria[0].evidence == ("evidence-1",)
    assert tuple(item.value for item in verdict.decision_provenance) == ("fresh",)


def test_arbiter_verdict_excludes_explicit_context_criteria_from_decision_provenance() -> None:
    verdict = ArbiterVerdict.model_validate(
        {
            "status": "pass",
            "criteria": [
                {
                    "criterion_id": "deciding",
                    "provenance": "fresh",
                    "status": "pass",
                },
                {
                    "criterion_id": "context",
                    "criterion_role": "context",
                    "provenance": "historical_only",
                    "status": "pass",
                },
            ],
        }
    )

    assert tuple(item.value for item in verdict.decision_provenance) == ("fresh",)


def test_stale_only_success_after_newer_remediation_is_blocked(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(paths, _target_state())
    verdict_path = _write_verdict(paths, status="pass", provenance=("historical_only",))
    report_path = _write_report(paths)
    window_path = _write_window(paths, with_remediation=True)
    engine = _start_engine(paths)

    apply_router_decision(
        engine,
        RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="arbiter_complete",
        ),
        _stage_result(
            paths,
            terminal_result=PlanningTerminalResult.ARBITER_COMPLETE,
            result_class=ResultClass.SUCCESS,
            success=True,
            report_path=report_path,
            verdict_path=verdict_path,
            window_path=window_path,
        ),
    )

    target = load_closure_target_state(paths, root_spec_id="spec-root-001")
    snapshot = load_snapshot(paths)
    assert target.closure_open is True
    assert target.closed_at is None
    assert snapshot.planning_status_marker == "### BLOCKED"
    assert snapshot.current_failure_class == "closure_insufficient_fresh_arbiter_evidence"


def test_mixed_fresh_and_historical_success_after_newer_remediation_is_blocked(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(paths, _target_state())
    verdict_path = _write_verdict(paths, status="pass", provenance=("fresh", "historical_only"))
    report_path = _write_report(paths)
    window_path = _write_window(paths, with_remediation=True)
    engine = _start_engine(paths)

    apply_router_decision(
        engine,
        RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="arbiter_complete",
        ),
        _stage_result(
            paths,
            terminal_result=PlanningTerminalResult.ARBITER_COMPLETE,
            result_class=ResultClass.SUCCESS,
            success=True,
            report_path=report_path,
            verdict_path=verdict_path,
            window_path=window_path,
        ),
    )

    target = load_closure_target_state(paths, root_spec_id="spec-root-001")
    snapshot = load_snapshot(paths)
    assert target.closure_open is True
    assert target.closed_at is None
    assert snapshot.planning_status_marker == "### BLOCKED"
    assert snapshot.current_failure_class == "closure_insufficient_fresh_arbiter_evidence"


def test_stale_only_failure_after_newer_remediation_is_blocked_without_handoff(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(paths, _target_state())
    verdict_path = _write_verdict(paths, status="fail", provenance=("missing",))
    report_path = _write_report(paths)
    window_path = _write_window(paths, with_remediation=True)
    engine = _start_engine(paths)

    apply_router_decision(
        engine,
        RouterDecision(
            action=RouterAction.HANDOFF,
            next_plane=None,
            next_stage=None,
            reason="arbiter_remediation_needed",
            failure_class="arbiter_parity_gap",
            create_incident=True,
        ),
        _stage_result(
            paths,
            terminal_result=PlanningTerminalResult.REMEDIATION_NEEDED,
            result_class=ResultClass.FOLLOWUP_NEEDED,
            success=False,
            report_path=report_path,
            verdict_path=verdict_path,
            window_path=window_path,
        ),
    )

    assert tuple(paths.incidents_incoming_dir.glob("*.md")) == ()
    assert any(
        event.event_type == "closure_fresh_evidence_blocked"
        and event.data.get("root_spec_id") == "spec-root-001"
        for event in read_runtime_events(paths)
    )


def test_mixed_fresh_and_missing_failure_after_newer_remediation_is_blocked_without_handoff(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(paths, _target_state())
    verdict_path = _write_verdict(paths, status="fail", provenance=("fresh", "missing"))
    report_path = _write_report(paths)
    window_path = _write_window(paths, with_remediation=True)
    engine = _start_engine(paths)

    apply_router_decision(
        engine,
        RouterDecision(
            action=RouterAction.HANDOFF,
            next_plane=None,
            next_stage=None,
            reason="arbiter_remediation_needed",
            failure_class="arbiter_parity_gap",
            create_incident=True,
        ),
        _stage_result(
            paths,
            terminal_result=PlanningTerminalResult.REMEDIATION_NEEDED,
            result_class=ResultClass.FOLLOWUP_NEEDED,
            success=False,
            report_path=report_path,
            verdict_path=verdict_path,
            window_path=window_path,
        ),
    )

    target = load_closure_target_state(paths, root_spec_id="spec-root-001")
    snapshot = load_snapshot(paths)
    assert target.closure_open is True
    assert target.closed_at is None
    assert snapshot.planning_status_marker == "### BLOCKED"
    assert snapshot.current_failure_class == "closure_insufficient_fresh_arbiter_evidence"
    assert tuple(paths.incidents_incoming_dir.glob("*.md")) == ()


def test_runtime_deduplicates_closure_remediation_incident_by_previous_arbiter(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    verdict_path = _write_current_style_verdict(paths, provenance=("fresh",))
    report_path = _write_report(paths)
    window_path = _write_window(paths, with_remediation=True)
    engine = _start_engine(paths)
    stage_result = _stage_result(
        paths,
        terminal_result=PlanningTerminalResult.REMEDIATION_NEEDED,
        result_class=ResultClass.FOLLOWUP_NEEDED,
        success=False,
        report_path=report_path,
        verdict_path=verdict_path,
        window_path=window_path,
    )
    decision = RouterDecision(
        action=RouterAction.HANDOFF,
        next_plane=None,
        next_stage=None,
        reason="arbiter_remediation_needed",
        failure_class="arbiter_parity_gap",
        create_incident=True,
    )

    first = enqueue_handoff_incident(engine, decision=decision, stage_result=stage_result)
    second = enqueue_handoff_incident(engine, decision=decision, stage_result=stage_result)

    incident_paths = tuple(paths.incidents_incoming_dir.glob("*.md"))
    incident = read_work_document_as(first, model=IncidentDocument)
    assert first == second
    assert len(incident_paths) == 1
    assert incident.created_by == "millrace-runtime"
    assert incident.trigger_metadata["runtime_created"] is True
    assert incident.trigger_metadata["previous_arbiter_run_id"] == "run-prev"
    assert incident.trigger_metadata["previous_arbiter_request_id"] == "request-prev"
    assert any(
        event.event_type == "runtime_handoff_incident_deduped"
        and event.data.get("root_spec_id") == "spec-root-001"
        for event in read_runtime_events(paths)
    )


def test_mixed_fresh_revalidated_verdict_after_remediation_can_close(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(paths, _target_state())
    verdict_path = _write_current_style_verdict(paths, provenance=("fresh", "revalidated"))
    report_path = _write_report(paths)
    window_path = _write_window(paths, with_remediation=True)
    engine = _start_engine(paths)

    apply_router_decision(
        engine,
        RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="arbiter_complete",
        ),
        _stage_result(
            paths,
            terminal_result=PlanningTerminalResult.ARBITER_COMPLETE,
            result_class=ResultClass.SUCCESS,
            success=True,
            report_path=report_path,
            verdict_path=verdict_path,
            window_path=window_path,
        ),
    )

    target = load_closure_target_state(paths, root_spec_id="spec-root-001")
    assert target.closure_open is False
    assert target.closed_at is not None


def test_context_only_historical_criterion_after_remediation_can_close(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(paths, _target_state())
    verdict_path = _write_current_style_verdict(
        paths,
        provenance=("fresh", "historical_only"),
        criterion_roles=(None, "context"),
    )
    report_path = _write_report(paths)
    window_path = _write_window(paths, with_remediation=True)
    engine = _start_engine(paths)

    apply_router_decision(
        engine,
        RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="arbiter_complete",
        ),
        _stage_result(
            paths,
            terminal_result=PlanningTerminalResult.ARBITER_COMPLETE,
            result_class=ResultClass.SUCCESS,
            success=True,
            report_path=report_path,
            verdict_path=verdict_path,
            window_path=window_path,
        ),
    )

    target = load_closure_target_state(paths, root_spec_id="spec-root-001")
    assert target.closure_open is False
    assert target.closed_at is not None


def test_first_pass_minimal_verdict_without_prior_arbiter_evidence_can_close(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(paths, _target_state().model_copy(update={"last_arbiter_run_id": None}))
    verdict_path = _write_verdict(paths, status="pass")
    report_path = _write_report(paths)
    window_path = _write_window(paths, with_remediation=False)
    engine = _start_engine(paths)

    apply_router_decision(
        engine,
        RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="arbiter_complete",
        ),
        _stage_result(
            paths,
            terminal_result=PlanningTerminalResult.ARBITER_COMPLETE,
            result_class=ResultClass.SUCCESS,
            success=True,
            report_path=report_path,
            verdict_path=verdict_path,
            window_path=window_path,
        ),
    )

    target = load_closure_target_state(paths, root_spec_id="spec-root-001")
    assert target.closure_open is False
