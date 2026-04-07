from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_engine.compounding import (
    build_compounding_integrity_report,
    build_compounding_orientation_snapshot,
    build_injected_context_fact_bundle,
    build_injected_procedure_bundle,
    discover_harness_search_requests,
    harness_recommendation_for_id,
    promote_procedure,
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
    HarnessCandidatePromptAssetOverride,
    HarnessCandidateState,
    HarnessChangedSurfaceKind,
    HarnessRecommendationArtifact,
    HarnessRecommendationDisposition,
    HarnessSearchAssetTarget,
    HarnessSearchRequestArtifact,
    ProcedureScope,
    ReusableProcedureArtifact,
    StageType,
)
from millrace_engine.lab import run_meta_harness_candidate_pipeline
from tests.support import runtime_workspace


def _write_json(path: Path, artifact: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _write_text(path: Path, contents: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return path


def test_compounding_closeout_regression_keeps_governed_stores_separate_and_lab_off_path(
    tmp_path: Path,
) -> None:
    workspace_root, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)

    _write_text(
        workspace_root / "agents" / "runs" / "run-701" / "transition_history.jsonl",
        '{"event":"builder.complete"}\n',
    )
    run_candidate = ReusableProcedureArtifact(
        procedure_id="proc.run.builder.audit.001",
        scope=ProcedureScope.RUN,
        source_run_id="run-701",
        source_stage=StageType.BUILDER,
        title="Builder audit trail",
        summary="Preserve the builder audit trail before QA reruns.",
        procedure_markdown=(
            "# Builder audit trail\n\n"
            "Preserve the builder audit trail before QA reruns and recovery passes.\n"
        ),
        tags=("builder", "audit"),
        evidence_refs=("agents/runs/run-701/transition_history.jsonl",),
        created_at="2026-04-07T18:00:00Z",
    )
    _write_json(
        paths.compounding_procedures_dir / run_candidate.source_run_id / f"{run_candidate.procedure_id}.json",
        run_candidate,
    )

    promotion = promote_procedure(
        paths,
        procedure_id=run_candidate.procedure_id,
        changed_by="test.closeout",
        reason="Promote builder audit guidance for governed reuse.",
    )
    assert promotion.applied is True
    promoted_procedure_id = promotion.procedure.artifact.procedure_id

    _write_text(
        workspace_root / "agents" / "runs" / "run-702" / "transition_history.jsonl",
        '{"event":"builder.fact"}\n',
    )
    promoted_fact = ContextFactArtifact(
        fact_id="fact.workspace.builder.audit.001",
        scope=ContextFactScope.WORKSPACE,
        lifecycle_state=ContextFactLifecycleState.PROMOTED,
        source_run_id="run-702",
        source_stage=StageType.BUILDER,
        title="Audit trail prerequisite",
        statement="Keep the builder audit trail files available before QA reruns.",
        summary="Builder audit trail files must survive until QA reruns complete.",
        tags=("builder", "audit"),
        evidence_refs=("agents/runs/run-702/transition_history.jsonl",),
        created_at="2026-04-07T18:05:00Z",
    )
    persist_context_fact(paths, promoted_fact)

    source_prompt_relative = Path("agents/compounding/harness_candidate_assets/search.fixture.closeout/builder.md")
    source_prompt_path = workspace_root / source_prompt_relative
    _write_text(
        source_prompt_path,
        "# Builder override\n\nUse the governed builder audit wording in this candidate.\n",
    )

    search_request = HarnessSearchRequestArtifact(
        search_id="search.fixture.closeout",
        baseline_ref="workspace.live",
        benchmark_suite_ref="preview.standard.v1",
        asset_targets=(
            HarnessSearchAssetTarget(stage=StageType.BUILDER, source_ref="package:agents/_start.md"),
        ),
        created_at="2026-04-07T19:00:00Z",
        created_by="test.closeout",
    )
    _write_json(
        paths.compounding_harness_search_requests_dir / f"{search_request.search_id}.json",
        search_request,
    )

    candidate = HarnessCandidateArtifact(
        candidate_id="candidate.fixture.closeout",
        name="Builder prompt closeout candidate",
        baseline_ref="workspace.live",
        benchmark_suite_ref="preview.standard.v1",
        state=HarnessCandidateState.CANDIDATE,
        changed_surfaces=(
            {
                "kind": HarnessChangedSurfaceKind.PROMPT_ASSET.value,
                "target": "builder:package:agents/_start.md",
                "summary": "Swap the builder prompt to the closeout candidate-owned override.",
            },
        ),
        prompt_asset_overrides=(
            HarnessCandidatePromptAssetOverride(
                stage=StageType.BUILDER,
                source_ref="package:agents/_start.md",
                candidate_prompt_file=source_prompt_relative,
            ),
        ),
        reviewer_note="runtime-owned bounded candidate for closeout regression",
        created_at="2026-04-07T19:01:00Z",
        created_by="test.closeout",
    )
    _write_json(paths.compounding_harness_candidates_dir / f"{candidate.candidate_id}.json", candidate)

    baseline_ref_path = paths.compounding_benchmark_results_dir / "benchmark.fixture.closeout__baseline.json"
    candidate_ref_path = paths.compounding_benchmark_results_dir / "benchmark.fixture.closeout__candidate.json"
    _write_text(baseline_ref_path, '{\n  "selection": "baseline"\n}\n')
    _write_text(candidate_ref_path, '{\n  "selection": "candidate"\n}\n')
    benchmark = HarnessBenchmarkResult(
        result_id="benchmark.fixture.closeout",
        candidate_id=candidate.candidate_id,
        baseline_ref="workspace.live",
        benchmark_suite_ref="preview.standard.v1",
        status=HarnessBenchmarkStatus.COMPLETE,
        outcome=HarnessBenchmarkOutcome.CHANGED,
        started_at="2026-04-07T19:05:00Z",
        completed_at="2026-04-07T19:06:00Z",
        outcome_summary=HarnessBenchmarkOutcomeSummary(
            selection_changed=False,
            changed_config_fields=(),
            changed_stage_bindings=("builder",),
            baseline_mode_ref="mode.standard@1.0.0",
            candidate_mode_ref="mode.standard@1.0.0",
            message="Candidate changes the builder prompt binding only.",
        ),
        cost_summary=HarnessBenchmarkCostSummary(
            baseline_governed_plus_budget_characters=3200,
            candidate_governed_plus_budget_characters=3200,
            budget_delta_characters=0,
        ),
        artifact_refs=(
            "agents/compounding/benchmark_results/benchmark.fixture.closeout__baseline.json",
            "agents/compounding/benchmark_results/benchmark.fixture.closeout__candidate.json",
        ),
    )
    _write_json(paths.compounding_benchmark_results_dir / f"{benchmark.result_id}.json", benchmark)

    recommendation = HarnessRecommendationArtifact(
        recommendation_id="recommend.search.fixture.closeout",
        search_id=search_request.search_id,
        disposition=HarnessRecommendationDisposition.RECOMMEND,
        recommended_candidate_id=candidate.candidate_id,
        recommended_result_id=benchmark.result_id,
        candidate_ids=(candidate.candidate_id,),
        benchmark_result_ids=(benchmark.result_id,),
        summary="Recommend the builder prompt closeout candidate after bounded benchmark review.",
        created_at="2026-04-07T19:07:00Z",
        created_by="test.closeout",
    )
    _write_json(
        paths.compounding_harness_recommendations_dir / f"{recommendation.recommendation_id}.json",
        recommendation,
    )

    procedure_bundle = build_injected_procedure_bundle(paths, run_id="run-900", stage=StageType.BUILDER)
    fact_bundle = build_injected_context_fact_bundle(
        paths,
        run_id="run-900",
        stage=StageType.BUILDER,
        task_text="Preserve the builder audit trail before QA reruns.",
    )

    assert procedure_bundle is not None
    assert [item.procedure_id for item in procedure_bundle.procedures] == [promoted_procedure_id]
    assert fact_bundle is not None
    assert [item.fact_id for item in fact_bundle.facts] == [promoted_fact.fact_id]

    search_requests = discover_harness_search_requests(paths)
    assert [item.request.search_id for item in search_requests] == [search_request.search_id]
    assert harness_recommendation_for_id(paths, recommendation.recommendation_id).recommendation.search_id == (
        search_request.search_id
    )

    orientation = build_compounding_orientation_snapshot(paths)
    assert orientation.index_artifact.family_counts == {
        "context_fact": 1,
        "harness_benchmark": 1,
        "harness_candidate": 1,
        "harness_recommendation": 1,
        "procedure": 2,
    }
    assert any(cluster.kind.value == "benchmark_candidate" for cluster in orientation.relationship_clusters)
    assert any(cluster.kind.value == "recommendation_bundle" for cluster in orientation.relationship_clusters)

    integrity = build_compounding_integrity_report(paths)
    assert integrity.status.value == "pass"
    assert integrity.orientation_index_present is True
    assert integrity.relationship_summary_present is True

    compounding_files_before = {
        path.relative_to(workspace_root).as_posix()
        for path in paths.compounding_dir.rglob("*")
        if path.is_file()
    }

    lab_outcome = run_meta_harness_candidate_pipeline(
        paths,
        recommendation_id=recommendation.recommendation_id,
        created_by="lab.fixture",
        created_at=datetime(2026, 4, 7, 20, 0, 0, tzinfo=timezone.utc),
    )

    compounding_files_after = {
        path.relative_to(workspace_root).as_posix()
        for path in paths.compounding_dir.rglob("*")
        if path.is_file()
    }
    assert compounding_files_after == compounding_files_before

    proposal = lab_outcome.proposals[0].proposal
    assert proposal.source_candidate_id == candidate.candidate_id
    assert proposal.source_benchmark_result_id == benchmark.result_id
    copied_prompt_path = workspace_root / proposal.prompt_asset_overrides[0].candidate_prompt_file
    assert copied_prompt_path.exists()
    assert copied_prompt_path.read_text(encoding="utf-8") == source_prompt_path.read_text(encoding="utf-8")

    integrity_after_lab = build_compounding_integrity_report(paths)
    assert integrity_after_lab.status.value == "pass"
