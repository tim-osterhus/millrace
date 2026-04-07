from __future__ import annotations

import json
from pathlib import Path

from millrace_engine.compounding.integrity import CompoundingIntegrityStatus, build_compounding_integrity_report
from millrace_engine.compounding.orientation import build_compounding_orientation_snapshot
from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.context_facts import persist_context_fact
from millrace_engine.contracts import (
    ContextFactArtifact,
    ContextFactLifecycleState,
    ContextFactScope,
    HarnessBenchmarkCostSummary,
    HarnessBenchmarkOutcome,
    HarnessBenchmarkOutcomeSummary,
    HarnessBenchmarkResult,
    HarnessBenchmarkStatus,
    HarnessCandidateArtifact,
    HarnessCandidateState,
    HarnessChangedSurfaceKind,
    HarnessRecommendationArtifact,
    HarnessRecommendationDisposition,
    ProcedureLifecycleRecord,
    ProcedureLifecycleState,
    ProcedureScope,
    ReusableProcedureArtifact,
    StageType,
)
from tests.support import runtime_workspace


def _write_model(path: Path, model: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(getattr(model, "model_dump_json")(indent=2) + "\n", encoding="utf-8")
    return path


def _write_procedure(paths: object, *, procedure_id: str, scope: ProcedureScope, source_run_id: str) -> Path:
    artifact = ReusableProcedureArtifact(
        procedure_id=procedure_id,
        scope=scope,
        source_run_id=source_run_id,
        source_stage=StageType.BUILDER,
        title=f"Title for {procedure_id}",
        summary=f"Summary for {procedure_id}",
        procedure_markdown="Preserve the builder audit trail.",
        tags=("audit", "builder"),
        evidence_refs=(f"agents/runs/{source_run_id}/transition_history.jsonl",),
        created_at="2026-04-07T18:00:00Z",
    )
    target = paths.compounding_procedures_dir / f"{procedure_id}.json"
    if scope is ProcedureScope.RUN:
        target = paths.compounding_procedures_dir / source_run_id / f"{procedure_id}.json"
    return _write_model(target, artifact)


def _write_lifecycle(paths: object, *, procedure_id: str) -> Path:
    record = ProcedureLifecycleRecord(
        record_id=f"record.promoted.{procedure_id}",
        procedure_id=procedure_id,
        state=ProcedureLifecycleState.PROMOTED,
        scope=ProcedureScope.WORKSPACE,
        changed_at="2026-04-07T18:30:00Z",
        changed_by="test.fixture",
        reason="Approved for broader reuse.",
    )
    return _write_model(paths.compounding_lifecycle_records_dir / f"{record.record_id}.json", record)


def _write_candidate(paths: object, *, candidate_id: str) -> Path:
    artifact = HarnessCandidateArtifact(
        candidate_id=candidate_id,
        name="Governed Plus Preview Trial",
        baseline_ref="workspace.live",
        benchmark_suite_ref="preview.standard.v1",
        state=HarnessCandidateState.CANDIDATE,
        changed_surfaces=(
            {
                "kind": HarnessChangedSurfaceKind.CONFIG.value,
                "target": "policies.compounding.profile",
                "summary": "Switch to governed_plus for bounded comparison.",
            },
        ),
        created_at="2026-04-07T19:00:00Z",
        created_by="test.fixture",
    )
    return _write_model(paths.compounding_harness_candidates_dir / f"{candidate_id}.json", artifact)


def _write_benchmark(paths: object, *, result_id: str, candidate_id: str) -> Path:
    result = HarnessBenchmarkResult(
        result_id=result_id,
        candidate_id=candidate_id,
        baseline_ref="workspace.live",
        benchmark_suite_ref="preview.standard.v1",
        status=HarnessBenchmarkStatus.COMPLETE,
        outcome=HarnessBenchmarkOutcome.CHANGED,
        started_at="2026-04-07T19:10:00Z",
        completed_at="2026-04-07T19:12:00Z",
        outcome_summary=HarnessBenchmarkOutcomeSummary(
            selection_changed=True,
            changed_config_fields=("policies.compounding.profile",),
            changed_stage_bindings=(),
            baseline_mode_ref="baseline",
            candidate_mode_ref="governed_plus",
            message="Selection changed under governed_plus preview.",
        ),
        cost_summary=HarnessBenchmarkCostSummary(
            baseline_governed_plus_budget_characters=3200,
            candidate_governed_plus_budget_characters=4800,
            budget_delta_characters=1600,
        ),
        artifact_refs=(),
    )
    return _write_model(paths.compounding_benchmark_results_dir / f"{result_id}.json", result)


def _write_recommendation(paths: object, *, recommendation_id: str, candidate_id: str, result_id: str) -> Path:
    artifact = HarnessRecommendationArtifact(
        recommendation_id=recommendation_id,
        search_id="search.missing",
        disposition=HarnessRecommendationDisposition.RECOMMEND,
        recommended_candidate_id=candidate_id,
        recommended_result_id=result_id,
        candidate_ids=(candidate_id,),
        benchmark_result_ids=(result_id,),
        summary="Recommend the preview candidate after bounded benchmark review.",
        created_at="2026-04-07T19:15:00Z",
        created_by="test.fixture",
    )
    return _write_model(paths.compounding_harness_recommendations_dir / f"{recommendation_id}.json", artifact)


def test_compounding_integrity_report_warns_for_stale_workspace_artifacts(tmp_path: Path) -> None:
    _, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)

    _write_procedure(
        paths,
        procedure_id="proc.workspace.builder.stale",
        scope=ProcedureScope.WORKSPACE,
        source_run_id="run-stale",
    )

    report = build_compounding_integrity_report(paths)

    assert report.status is CompoundingIntegrityStatus.WARN
    assert report.warning_count == 1
    assert report.failure_count == 0
    assert report.issues[0].issue_id == "procedure.stale.proc.workspace.builder.stale"


def test_compounding_integrity_report_fails_for_broken_primary_and_orientation_links(tmp_path: Path) -> None:
    workspace_root, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)

    _write_procedure(
        paths,
        procedure_id="proc.workspace.builder.audit",
        scope=ProcedureScope.WORKSPACE,
        source_run_id="run-audit",
    )
    _write_lifecycle(paths, procedure_id="proc.workspace.builder.audit")
    persist_context_fact(
        paths,
        ContextFactArtifact(
            fact_id="fact.workspace.builder.audit",
            scope=ContextFactScope.WORKSPACE,
            lifecycle_state=ContextFactLifecycleState.PROMOTED,
            source_run_id="run-audit",
            source_stage=StageType.BUILDER,
            title="Audit trail fact",
            statement="Keep the audit trail coherent across retries.",
            summary="Approved governed audit fact.",
            tags=("audit", "builder"),
            evidence_refs=("agents/runs/run-audit/transition_history.jsonl",),
            created_at="2026-04-07T18:10:00Z",
        ),
    )
    _write_candidate(paths, candidate_id="candidate.fixture")
    _write_benchmark(paths, result_id="benchmark.fixture", candidate_id="candidate.fixture")
    _write_recommendation(
        paths,
        recommendation_id="recommendation.fixture",
        candidate_id="candidate.fixture",
        result_id="benchmark.fixture",
    )
    build_compounding_orientation_snapshot(paths)

    relationship_path = workspace_root / "agents" / "compounding" / "indexes" / "relationship_summary.json"
    payload = json.loads(relationship_path.read_text(encoding="utf-8"))
    payload["clusters"][0]["member_ids"] = ["missing.entry"]
    relationship_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    report = build_compounding_integrity_report(paths)

    assert report.status is CompoundingIntegrityStatus.FAIL
    issue_ids = {issue.issue_id for issue in report.issues}
    assert "harness_recommendation.missing_search.recommendation.fixture" in issue_ids
    assert any(issue_id.startswith("orientation_relationship.missing_members.") for issue_id in issue_ids)
    assert "orientation_relationship.out_of_sync" in issue_ids
    assert report.orientation_index_present is True
    assert report.relationship_summary_present is True
    assert report.checked_counts["orientation_cluster"] > 0
