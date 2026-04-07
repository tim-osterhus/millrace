from __future__ import annotations

import json
from pathlib import Path

from millrace_engine.compounding.orientation import (
    INDEX_ARTIFACT_NAME,
    RELATIONSHIP_ARTIFACT_NAME,
    build_compounding_orientation_snapshot,
)
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


def _write_workspace_procedure(paths: object, *, procedure_id: str) -> Path:
    artifact = ReusableProcedureArtifact(
        procedure_id=procedure_id,
        scope=ProcedureScope.WORKSPACE,
        source_run_id="run-audit",
        source_stage=StageType.BUILDER,
        title="Audit-ready builder procedure",
        summary="Approved governed audit procedure.",
        procedure_markdown="Preserve the audit trail through builder retries.",
        tags=("audit", "builder"),
        evidence_refs=("agents/runs/run-audit/transition_history.jsonl",),
        created_at="2026-04-07T18:00:00Z",
    )
    return _write_model(
        paths.compounding_procedures_dir / "proc.workspace.audit.json",
        artifact,
    )


def _write_lifecycle_record(paths: object, *, procedure_id: str) -> Path:
    record = ProcedureLifecycleRecord(
        record_id="record.promoted.proc.workspace.audit",
        procedure_id=procedure_id,
        state=ProcedureLifecycleState.PROMOTED,
        scope=ProcedureScope.WORKSPACE,
        changed_at="2026-04-07T18:30:00Z",
        changed_by="test.fixture",
        reason="Approved for governed reuse.",
    )
    return _write_model(
        paths.compounding_lifecycle_records_dir / "record.promoted.proc.workspace.audit.json",
        record,
    )


def _write_harness_candidate(paths: object, *, candidate_id: str) -> Path:
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
        reviewer_note="fixture candidate",
        created_at="2026-04-07T19:00:00Z",
        created_by="test.fixture",
    )
    return _write_model(paths.compounding_harness_candidates_dir / f"{candidate_id}.json", artifact)


def _write_harness_benchmark(paths: object, *, result_id: str, candidate_id: str) -> Path:
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
        artifact_refs=(
            "agents/compounding/benchmark_results/benchmark.fixture.__baseline.json",
            "agents/compounding/benchmark_results/benchmark.fixture.__candidate.json",
        ),
    )
    return _write_model(paths.compounding_benchmark_results_dir / f"{result_id}.json", result)


def _write_harness_recommendation(paths: object, *, recommendation_id: str, candidate_id: str, result_id: str) -> Path:
    artifact = HarnessRecommendationArtifact(
        recommendation_id=recommendation_id,
        search_id="search.fixture",
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


def test_compounding_orientation_generates_secondary_artifacts_and_query_matches(tmp_path: Path) -> None:
    workspace_root, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)

    _write_workspace_procedure(paths, procedure_id="proc.workspace.audit")
    _write_lifecycle_record(paths, procedure_id="proc.workspace.audit")
    persist_context_fact(
        paths,
        ContextFactArtifact(
            fact_id="fact.workspace.audit",
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
    _write_harness_candidate(paths, candidate_id="candidate.fixture")
    _write_harness_benchmark(paths, result_id="benchmark.fixture", candidate_id="candidate.fixture")
    _write_harness_recommendation(
        paths,
        recommendation_id="recommendation.fixture",
        candidate_id="candidate.fixture",
        result_id="benchmark.fixture",
    )

    full_snapshot = build_compounding_orientation_snapshot(paths)

    assert full_snapshot.index_path == workspace_root / "agents" / "compounding" / "indexes" / INDEX_ARTIFACT_NAME
    assert full_snapshot.relationship_summary_path == (
        workspace_root / "agents" / "compounding" / "indexes" / RELATIONSHIP_ARTIFACT_NAME
    )
    assert full_snapshot.index_artifact.secondary_surface_note.endswith("source of truth.")
    assert full_snapshot.index_artifact.family_counts == {
        "context_fact": 1,
        "harness_benchmark": 1,
        "harness_candidate": 1,
        "harness_recommendation": 1,
        "procedure": 1,
    }
    assert any(cluster.kind.value == "recommendation_bundle" for cluster in full_snapshot.relationship_clusters)
    assert any(cluster.kind.value == "benchmark_candidate" for cluster in full_snapshot.relationship_clusters)

    index_payload = json.loads(full_snapshot.index_path.read_text(encoding="utf-8"))
    relationship_payload = json.loads(full_snapshot.relationship_summary_path.read_text(encoding="utf-8"))
    assert index_payload["family_counts"]["procedure"] == 1
    assert relationship_payload["cluster_counts"]["source_run"] >= 1

    audit_snapshot = build_compounding_orientation_snapshot(paths, query="audit")

    assert {entry.entry_id for entry in audit_snapshot.entries} == {
        "fact.workspace.audit",
        "proc.workspace.audit",
    }
    assert {cluster.kind.value for cluster in audit_snapshot.relationship_clusters} >= {
        "source_run",
        "tag",
    }
