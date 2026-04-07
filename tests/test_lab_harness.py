from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.contracts import (
    HarnessBenchmarkOutcome,
    HarnessBenchmarkResult,
    HarnessBenchmarkStatus,
    HarnessCandidateArtifact,
    HarnessCandidatePromptAssetOverride,
    HarnessCandidateState,
    HarnessChangedSurfaceKind,
    HarnessRecommendationArtifact,
    HarnessRecommendationDisposition,
)
from millrace_engine.lab import (
    discover_lab_harness_comparisons,
    discover_lab_harness_proposals,
    discover_lab_harness_requests,
    lab_harness_comparison_for_id,
    lab_harness_proposal_for_id,
    lab_harness_request_for_id,
    run_meta_harness_candidate_pipeline,
)
from tests.support import runtime_workspace


def _write_artifact(path: Path, artifact: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")


def test_run_meta_harness_candidate_pipeline_materializes_off_path_lab_artifacts(tmp_path: Path) -> None:
    workspace_root, config_path = runtime_workspace(tmp_path)
    paths = build_runtime_paths(load_engine_config(config_path).config)

    source_prompt_path = workspace_root / "agents/compounding/harness_candidate_assets/search-001/builder.md"
    source_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    source_prompt_path.write_text("# Builder override\n\nRuntime bounded candidate prompt.\n", encoding="utf-8")

    candidate = HarnessCandidateArtifact(
        candidate_id="harness.search.search.20260407T211000Z.asset.builder",
        name="Bounded Builder Prompt Trial",
        baseline_ref="workspace.live",
        benchmark_suite_ref="preview.standard.v1",
        state=HarnessCandidateState.CANDIDATE,
        changed_surfaces=(
            {
                "kind": HarnessChangedSurfaceKind.PROMPT_ASSET.value,
                "target": "builder:package:agents/_start.md",
                "summary": "Swap the builder prompt to the candidate-owned override.",
            },
        ),
        prompt_asset_overrides=(
            HarnessCandidatePromptAssetOverride(
                stage="builder",
                source_ref="package:agents/_start.md",
                candidate_prompt_file=Path("agents/compounding/harness_candidate_assets/search-001/builder.md"),
            ),
        ),
        reviewer_note="runtime-owned bounded candidate",
        created_at="2026-04-07T21:10:00Z",
        created_by="runtime.search",
    )
    benchmark = HarnessBenchmarkResult(
        result_id="bench.20260407T211500Z.harness-search-builder",
        candidate_id=candidate.candidate_id,
        baseline_ref=candidate.baseline_ref,
        benchmark_suite_ref=candidate.benchmark_suite_ref,
        status=HarnessBenchmarkStatus.COMPLETE,
        outcome=HarnessBenchmarkOutcome.CHANGED,
        started_at="2026-04-07T21:15:00Z",
        completed_at="2026-04-07T21:15:05Z",
        outcome_summary={
            "selection_changed": False,
            "changed_config_fields": [],
            "changed_stage_bindings": ["builder"],
            "baseline_mode_ref": "mode.standard@1.0.0",
            "candidate_mode_ref": "mode.standard@1.0.0",
            "message": "Candidate changes the builder prompt binding only.",
        },
        cost_summary={
            "baseline_governed_plus_budget_characters": 3200,
            "candidate_governed_plus_budget_characters": 3200,
            "budget_delta_characters": 0,
        },
        artifact_refs=("agents/compounding/benchmark_results/example-baseline.json",),
    )
    recommendation = HarnessRecommendationArtifact(
        recommendation_id="recommend.search.20260407T211000Z",
        search_id="search.20260407T211000Z",
        disposition=HarnessRecommendationDisposition.RECOMMEND,
        recommended_candidate_id=candidate.candidate_id,
        recommended_result_id=benchmark.result_id,
        candidate_ids=(candidate.candidate_id,),
        benchmark_result_ids=(benchmark.result_id,),
        summary="Recommend the bounded builder prompt candidate for off-path lab follow-up.",
        created_at="2026-04-07T21:16:00Z",
        created_by="runtime.search",
    )

    _write_artifact(paths.compounding_harness_candidates_dir / f"{candidate.candidate_id}.json", candidate)
    _write_artifact(paths.compounding_benchmark_results_dir / f"{benchmark.result_id}.json", benchmark)
    _write_artifact(
        paths.compounding_harness_recommendations_dir / f"{recommendation.recommendation_id}.json",
        recommendation,
    )

    runtime_files_before = {
        path.relative_to(workspace_root).as_posix()
        for path in paths.compounding_dir.rglob("*")
        if path.is_file()
    }

    created_at = datetime(2026, 4, 7, 22, 0, 0, tzinfo=timezone.utc)
    outcome = run_meta_harness_candidate_pipeline(
        paths,
        recommendation_id=recommendation.recommendation_id,
        created_by="lab.fixture",
        created_at=created_at,
    )

    assert outcome.request_path == (
        paths.lab_harness_requests_dir / "lab.request.search.20260407T211000Z.20260407T220000Z.json"
    )
    assert outcome.comparison_path == (
        paths.lab_harness_comparisons_dir
        / "lab.compare.lab-request-search-20260407T211000Z-20260407T220000Z.json"
    )
    assert len(outcome.proposals) == 1
    proposal = outcome.proposals[0].proposal
    assert outcome.proposals[0].path == (
        paths.lab_harness_proposals_dir
        / "lab.harness.proposal.lab-request-search-20260407T211000Z-20260407T220000Z.harness-search-search-20260407T211000Z-asset-builder.json"
    )
    assert proposal.source_candidate_id == candidate.candidate_id
    assert proposal.source_benchmark_result_id == benchmark.result_id
    assert proposal.prompt_asset_overrides[0].candidate_prompt_file == Path(
        "agents/lab/harness_candidate_assets/lab.harness.proposal.lab-request-search-20260407T211000Z-20260407T220000Z.harness-search-search-20260407T211000Z-asset-builder/builder.md"
    )
    copied_prompt_path = workspace_root / proposal.prompt_asset_overrides[0].candidate_prompt_file
    assert copied_prompt_path.exists()
    assert copied_prompt_path.read_text(encoding="utf-8") == source_prompt_path.read_text(encoding="utf-8")

    comparison = outcome.comparison
    assert comparison.source_recommendation_id == recommendation.recommendation_id
    assert comparison.proposal_ids == (proposal.proposal_id,)
    assert comparison.rows[0].source_candidate_id == candidate.candidate_id
    assert comparison.rows[0].source_benchmark_result_id == benchmark.result_id
    assert comparison.rows[0].changed_stage_bindings == ("builder",)

    runtime_files_after = {
        path.relative_to(workspace_root).as_posix()
        for path in paths.compounding_dir.rglob("*")
        if path.is_file()
    }
    assert runtime_files_after == runtime_files_before

    assert discover_lab_harness_requests(paths)[0].request.request_id == outcome.request.request_id
    assert discover_lab_harness_proposals(paths)[0].proposal.proposal_id == proposal.proposal_id
    assert discover_lab_harness_comparisons(paths)[0].comparison.comparison_id == comparison.comparison_id
    assert lab_harness_request_for_id(paths, outcome.request.request_id).path == outcome.request_path
    assert lab_harness_proposal_for_id(paths, proposal.proposal_id).path == outcome.proposals[0].path
    assert lab_harness_comparison_for_id(paths, comparison.comparison_id).path == outcome.comparison_path
