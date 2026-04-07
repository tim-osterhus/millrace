"""Off-path meta-harness lab pipeline over persisted runtime harness outputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..compounding.harness import (
    StoredHarnessBenchmarkResult,
    StoredHarnessCandidate,
    StoredHarnessRecommendation,
    harness_benchmark_result_for_id,
    harness_candidate_for_id,
    harness_recommendation_for_id,
)
from ..contract_harness import HarnessBenchmarkResult, HarnessCandidatePromptAssetOverride
from ..contract_lab import (
    LabHarnessComparisonArtifact,
    LabHarnessComparisonRow,
    LabHarnessProposalArtifact,
    LabHarnessRequestArtifact,
)
from ..markdown import write_text_atomic
from ..paths import RuntimePaths


@dataclass(frozen=True, slots=True)
class StoredLabHarnessRequest:
    """One discovered lab pipeline request artifact plus its path."""

    path: Path
    request: LabHarnessRequestArtifact


@dataclass(frozen=True, slots=True)
class StoredLabHarnessProposal:
    """One discovered lab proposal artifact plus its path."""

    path: Path
    proposal: LabHarnessProposalArtifact


@dataclass(frozen=True, slots=True)
class StoredLabHarnessComparison:
    """One discovered lab comparison artifact plus its path."""

    path: Path
    comparison: LabHarnessComparisonArtifact


@dataclass(frozen=True, slots=True)
class MetaHarnessLabRunOutcome:
    """Outcome of one off-path lab candidate-generation pipeline run."""

    request_path: Path
    request: LabHarnessRequestArtifact
    proposals: tuple[StoredLabHarnessProposal, ...]
    comparison_path: Path
    comparison: LabHarnessComparisonArtifact


def discover_lab_harness_requests(paths: RuntimePaths) -> tuple[StoredLabHarnessRequest, ...]:
    """Return persisted lab pipeline requests, newest-first."""

    if not paths.lab_harness_requests_dir.exists():
        return ()
    requests = [
        StoredLabHarnessRequest(
            path=path,
            request=LabHarnessRequestArtifact.model_validate_json(path.read_text(encoding="utf-8")),
        )
        for path in sorted(paths.lab_harness_requests_dir.glob("*.json"))
    ]
    requests.sort(key=lambda item: (item.request.created_at, item.request.request_id), reverse=True)
    return tuple(requests)


def lab_harness_request_for_id(paths: RuntimePaths, request_id: str) -> StoredLabHarnessRequest:
    """Resolve one persisted lab request artifact by id."""

    normalized_request_id = request_id.strip()
    if not normalized_request_id:
        raise ValueError("request_id may not be empty")
    for stored in discover_lab_harness_requests(paths):
        if stored.request.request_id == normalized_request_id:
            return stored
    raise ValueError(f"lab harness request not found: {normalized_request_id}")


def discover_lab_harness_proposals(paths: RuntimePaths) -> tuple[StoredLabHarnessProposal, ...]:
    """Return persisted lab proposals, newest-first."""

    if not paths.lab_harness_proposals_dir.exists():
        return ()
    proposals = [
        StoredLabHarnessProposal(
            path=path,
            proposal=LabHarnessProposalArtifact.model_validate_json(path.read_text(encoding="utf-8")),
        )
        for path in sorted(paths.lab_harness_proposals_dir.glob("*.json"))
    ]
    proposals.sort(key=lambda item: (item.proposal.created_at, item.proposal.proposal_id), reverse=True)
    return tuple(proposals)


def lab_harness_proposal_for_id(paths: RuntimePaths, proposal_id: str) -> StoredLabHarnessProposal:
    """Resolve one lab proposal artifact by id."""

    normalized_proposal_id = proposal_id.strip()
    if not normalized_proposal_id:
        raise ValueError("proposal_id may not be empty")
    for stored in discover_lab_harness_proposals(paths):
        if stored.proposal.proposal_id == normalized_proposal_id:
            return stored
    raise ValueError(f"lab harness proposal not found: {normalized_proposal_id}")


def discover_lab_harness_comparisons(paths: RuntimePaths) -> tuple[StoredLabHarnessComparison, ...]:
    """Return persisted lab comparison reports, newest-first."""

    if not paths.lab_harness_comparisons_dir.exists():
        return ()
    comparisons = [
        StoredLabHarnessComparison(
            path=path,
            comparison=LabHarnessComparisonArtifact.model_validate_json(path.read_text(encoding="utf-8")),
        )
        for path in sorted(paths.lab_harness_comparisons_dir.glob("*.json"))
    ]
    comparisons.sort(key=lambda item: (item.comparison.created_at, item.comparison.comparison_id), reverse=True)
    return tuple(comparisons)


def lab_harness_comparison_for_id(paths: RuntimePaths, comparison_id: str) -> StoredLabHarnessComparison:
    """Resolve one persisted lab comparison report by id."""

    normalized_comparison_id = comparison_id.strip()
    if not normalized_comparison_id:
        raise ValueError("comparison_id may not be empty")
    for stored in discover_lab_harness_comparisons(paths):
        if stored.comparison.comparison_id == normalized_comparison_id:
            return stored
    raise ValueError(f"lab harness comparison not found: {normalized_comparison_id}")


def run_meta_harness_candidate_pipeline(
    paths: RuntimePaths,
    *,
    recommendation_id: str,
    created_by: str = "lab.manual",
    created_at: datetime | None = None,
) -> MetaHarnessLabRunOutcome:
    """Consume persisted runtime harness outputs and emit off-path lab proposals/comparison reports."""

    recommendation = harness_recommendation_for_id(paths, recommendation_id)
    created_at = created_at or datetime.now(timezone.utc)
    request = _build_request(recommendation, created_at=created_at, created_by=created_by)
    source_candidates = tuple(
        harness_candidate_for_id(paths, candidate_id)
        for candidate_id in request.source_candidate_ids
    )
    source_results = tuple(
        harness_benchmark_result_for_id(paths, result_id)
        for result_id in request.source_benchmark_result_ids
    )
    result_by_candidate = {
        stored.result.candidate_id: stored
        for stored in source_results
    }
    request_path = _write_request(paths, request)
    proposals = tuple(
        _write_proposal(
            paths,
            _build_proposal(
                paths,
                request,
                stored_candidate,
                result_by_candidate.get(stored_candidate.candidate.candidate_id),
                created_at=created_at,
                created_by=created_by,
            ),
        )
        for stored_candidate in source_candidates
    )
    comparison = _build_comparison(
        request,
        recommendation,
        proposals,
        result_by_candidate=result_by_candidate,
        created_at=created_at,
        created_by=created_by,
    )
    comparison_path = _write_comparison(paths, comparison)
    return MetaHarnessLabRunOutcome(
        request_path=request_path,
        request=request,
        proposals=proposals,
        comparison_path=comparison_path,
        comparison=comparison,
    )


def _build_request(
    recommendation: StoredHarnessRecommendation,
    *,
    created_at: datetime,
    created_by: str,
) -> LabHarnessRequestArtifact:
    artifact = recommendation.recommendation
    timestamp_token = created_at.strftime("%Y%m%dT%H%M%SZ")
    return LabHarnessRequestArtifact(
        request_id=f"lab.request.{artifact.search_id}.{timestamp_token}",
        source_recommendation_id=artifact.recommendation_id,
        source_search_id=artifact.search_id,
        source_candidate_ids=artifact.candidate_ids,
        source_benchmark_result_ids=artifact.benchmark_result_ids,
        created_at=created_at,
        created_by=created_by,
    )


def _write_request(paths: RuntimePaths, artifact: LabHarnessRequestArtifact) -> Path:
    request_dir = paths.lab_harness_requests_dir
    request_dir.mkdir(parents=True, exist_ok=True)
    path = request_dir / f"{artifact.request_id}.json"
    write_text_atomic(path, artifact.model_dump_json(indent=2) + "\n")
    return path


def _build_proposal(
    paths: RuntimePaths,
    request: LabHarnessRequestArtifact,
    stored_candidate: StoredHarnessCandidate,
    stored_result: StoredHarnessBenchmarkResult | None,
    *,
    created_at: datetime,
    created_by: str,
) -> LabHarnessProposalArtifact:
    candidate = stored_candidate.candidate
    proposal_id = (
        f"lab.harness.proposal.{_slugify(request.request_id)}.{_slugify(candidate.candidate_id)}"
    )
    prompt_asset_overrides = _copy_prompt_asset_overrides(paths, proposal_id, candidate.prompt_asset_overrides)
    benchmark_summary = (
        stored_result.result.outcome_summary.message
        if stored_result is not None
        else "No benchmark result was associated with this source candidate."
    )
    return LabHarnessProposalArtifact(
        proposal_id=proposal_id,
        request_id=request.request_id,
        source_candidate_id=candidate.candidate_id,
        source_benchmark_result_id=(stored_result.result.result_id if stored_result is not None else None),
        name=f"Lab proposal derived from {candidate.name}",
        summary=(
            f"Off-path lab proposal copied from runtime candidate {candidate.candidate_id}. "
            f"Benchmark summary: {benchmark_summary}"
        ),
        changed_surfaces=candidate.changed_surfaces,
        compounding_policy_override=candidate.compounding_policy_override,
        prompt_asset_overrides=prompt_asset_overrides,
        created_at=created_at,
        created_by=created_by,
    )


def _write_proposal(paths: RuntimePaths, artifact: LabHarnessProposalArtifact) -> StoredLabHarnessProposal:
    proposal_dir = paths.lab_harness_proposals_dir
    proposal_dir.mkdir(parents=True, exist_ok=True)
    path = proposal_dir / f"{artifact.proposal_id}.json"
    write_text_atomic(path, artifact.model_dump_json(indent=2) + "\n")
    return StoredLabHarnessProposal(path=path, proposal=artifact)


def _copy_prompt_asset_overrides(
    paths: RuntimePaths,
    proposal_id: str,
    overrides: tuple[HarnessCandidatePromptAssetOverride, ...],
) -> tuple[HarnessCandidatePromptAssetOverride, ...]:
    if not overrides:
        return ()
    asset_dir = paths.lab_harness_candidate_assets_dir / proposal_id
    asset_dir.mkdir(parents=True, exist_ok=True)
    copied: list[HarnessCandidatePromptAssetOverride] = []
    for override in overrides:
        source_path = override.candidate_prompt_file
        if not source_path.is_absolute():
            source_path = paths.root / source_path
        target_path = asset_dir / source_path.name
        write_text_atomic(target_path, source_path.read_text(encoding="utf-8").rstrip("\n") + "\n")
        copied.append(
            HarnessCandidatePromptAssetOverride(
                stage=override.stage,
                source_ref=override.source_ref,
                candidate_prompt_file=target_path.relative_to(paths.root),
            )
        )
    return tuple(copied)


def _build_comparison(
    request: LabHarnessRequestArtifact,
    recommendation: StoredHarnessRecommendation,
    proposals: tuple[StoredLabHarnessProposal, ...],
    *,
    result_by_candidate: dict[str, StoredHarnessBenchmarkResult],
    created_at: datetime,
    created_by: str,
) -> LabHarnessComparisonArtifact:
    rows: list[LabHarnessComparisonRow] = []
    for proposal in proposals:
        stored_result = result_by_candidate.get(proposal.proposal.source_candidate_id)
        result: HarnessBenchmarkResult | None = stored_result.result if stored_result is not None else None
        rows.append(
            LabHarnessComparisonRow(
                source_candidate_id=proposal.proposal.source_candidate_id,
                source_benchmark_result_id=(result.result_id if result is not None else None),
                proposal_id=proposal.proposal.proposal_id,
                benchmark_status=(result.status.value if result is not None else "missing"),
                benchmark_outcome=(result.outcome.value if result is not None else "missing"),
                selection_changed=(result.outcome_summary.selection_changed if result is not None else False),
                changed_config_fields=(result.outcome_summary.changed_config_fields if result is not None else ()),
                changed_stage_bindings=(result.outcome_summary.changed_stage_bindings if result is not None else ()),
                budget_delta_characters=(result.cost_summary.budget_delta_characters if result is not None else 0),
                summary=proposal.proposal.summary,
            )
        )
    summary = (
        f"Generated {len(proposals)} off-path lab proposals from runtime recommendation "
        f"{recommendation.recommendation.recommendation_id}."
    )
    return LabHarnessComparisonArtifact(
        comparison_id=f"lab.compare.{_slugify(request.request_id)}",
        request_id=request.request_id,
        source_recommendation_id=request.source_recommendation_id,
        proposal_ids=tuple(item.proposal.proposal_id for item in proposals),
        rows=tuple(rows),
        summary=summary,
        created_at=created_at,
        created_by=created_by,
    )


def _write_comparison(paths: RuntimePaths, artifact: LabHarnessComparisonArtifact) -> Path:
    comparison_dir = paths.lab_harness_comparisons_dir
    comparison_dir.mkdir(parents=True, exist_ok=True)
    path = comparison_dir / f"{artifact.comparison_id}.json"
    write_text_atomic(path, artifact.model_dump_json(indent=2) + "\n")
    return path


def _slugify(value: str) -> str:
    return value.replace(":", "-").replace(".", "-").strip("-")
