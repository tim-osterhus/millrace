"""Governed harness candidate discovery, search, and bounded benchmarking."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..assets.resolver import AssetResolutionError, AssetResolver, AssetSourceKind
from ..config import CompoundingProfile, LoadedConfig, diff_config_fields
from ..contract_core import StageType
from ..contract_harness import (
    HarnessBenchmarkCostSummary,
    HarnessBenchmarkOutcome,
    HarnessBenchmarkOutcomeSummary,
    HarnessBenchmarkResult,
    HarnessBenchmarkStatus,
    HarnessCandidateArtifact,
    HarnessCandidateCompoundingPolicy,
    HarnessCandidatePromptAssetOverride,
    HarnessChangedSurface,
    HarnessChangedSurfaceKind,
    HarnessRecommendationArtifact,
    HarnessRecommendationDisposition,
    HarnessSearchAssetTarget,
    HarnessSearchRequestArtifact,
)
from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from ..standard_runtime import preview_standard_runtime_selection

DEFAULT_HARNESS_BASELINE_REF = "workspace.live"
DEFAULT_HARNESS_BENCHMARK_SUITE_REF = "preview.standard.v1"
_FILENAME_TOKEN_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class StoredHarnessCandidate:
    """One discovered harness candidate artifact plus its path."""

    path: Path
    candidate: HarnessCandidateArtifact


@dataclass(frozen=True, slots=True)
class StoredHarnessBenchmarkResult:
    """One discovered persisted benchmark result plus its path."""

    path: Path
    result: HarnessBenchmarkResult


@dataclass(frozen=True, slots=True)
class StoredHarnessSearchRequest:
    """One discovered bounded search request plus its path."""

    path: Path
    request: HarnessSearchRequestArtifact


@dataclass(frozen=True, slots=True)
class StoredHarnessRecommendation:
    """One discovered recommendation artifact plus its path."""

    path: Path
    recommendation: HarnessRecommendationArtifact


@dataclass(frozen=True, slots=True)
class HarnessBenchmarkRunOutcome:
    """Outcome of one bounded benchmark execution."""

    candidate: StoredHarnessCandidate
    result_path: Path
    result: HarnessBenchmarkResult
    baseline_artifact_path: Path
    candidate_artifact_path: Path


@dataclass(frozen=True, slots=True)
class HarnessSearchRunOutcome:
    """Outcome of one bounded config/assets-only search run."""

    search_path: Path
    request: HarnessSearchRequestArtifact
    recommendation_path: Path
    recommendation: HarnessRecommendationArtifact
    candidates: tuple[StoredHarnessCandidate, ...]
    benchmark_results: tuple[StoredHarnessBenchmarkResult, ...]


def discover_harness_candidates(paths: RuntimePaths) -> tuple[StoredHarnessCandidate, ...]:
    """Return every discovered governed harness candidate."""

    if not paths.compounding_harness_candidates_dir.exists():
        return ()
    candidates = [
        StoredHarnessCandidate(
            path=path,
            candidate=HarnessCandidateArtifact.model_validate_json(path.read_text(encoding="utf-8")),
        )
        for path in sorted(paths.compounding_harness_candidates_dir.glob("*.json"))
    ]
    candidates.sort(key=lambda item: item.candidate.candidate_id)
    return tuple(candidates)


def harness_candidate_for_id(paths: RuntimePaths, candidate_id: str) -> StoredHarnessCandidate:
    """Resolve one stored harness candidate by id."""

    normalized_candidate_id = candidate_id.strip()
    if not normalized_candidate_id:
        raise ValueError("candidate_id may not be empty")
    for stored in discover_harness_candidates(paths):
        if stored.candidate.candidate_id == normalized_candidate_id:
            return stored
    raise ValueError(f"harness candidate not found: {normalized_candidate_id}")


def discover_harness_benchmark_results(
    paths: RuntimePaths,
    *,
    candidate_id: str | None = None,
) -> tuple[StoredHarnessBenchmarkResult, ...]:
    """Return persisted harness benchmark results, newest-first."""

    if not paths.compounding_benchmark_results_dir.exists():
        return ()
    result_paths = [
        path
        for path in sorted(paths.compounding_benchmark_results_dir.glob("*.json"))
        if "__baseline" not in path.name and "__candidate" not in path.name
    ]
    results = [
        StoredHarnessBenchmarkResult(
            path=path,
            result=HarnessBenchmarkResult.model_validate_json(path.read_text(encoding="utf-8")),
        )
        for path in result_paths
    ]
    if candidate_id is not None:
        normalized_candidate_id = candidate_id.strip()
        results = [item for item in results if item.result.candidate_id == normalized_candidate_id]
    results.sort(key=lambda item: (item.result.completed_at, item.result.result_id), reverse=True)
    return tuple(results)


def harness_benchmark_result_for_id(paths: RuntimePaths, result_id: str) -> StoredHarnessBenchmarkResult:
    """Resolve one stored benchmark result by id."""

    normalized_result_id = result_id.strip()
    if not normalized_result_id:
        raise ValueError("result_id may not be empty")
    for stored in discover_harness_benchmark_results(paths):
        if stored.result.result_id == normalized_result_id:
            return stored
    raise ValueError(f"harness benchmark result not found: {normalized_result_id}")


def discover_harness_recommendations(paths: RuntimePaths) -> tuple[StoredHarnessRecommendation, ...]:
    """Return persisted harness recommendations, newest-first."""

    if not paths.compounding_harness_recommendations_dir.exists():
        return ()
    recommendations = [
        StoredHarnessRecommendation(
            path=path,
            recommendation=HarnessRecommendationArtifact.model_validate_json(path.read_text(encoding="utf-8")),
        )
        for path in sorted(paths.compounding_harness_recommendations_dir.glob("*.json"))
    ]
    recommendations.sort(
        key=lambda item: (item.recommendation.created_at, item.recommendation.recommendation_id),
        reverse=True,
    )
    return tuple(recommendations)


def discover_harness_search_requests(paths: RuntimePaths) -> tuple[StoredHarnessSearchRequest, ...]:
    """Return persisted bounded harness search requests, newest-first."""

    if not paths.compounding_harness_search_requests_dir.exists():
        return ()
    requests = [
        StoredHarnessSearchRequest(
            path=path,
            request=HarnessSearchRequestArtifact.model_validate_json(path.read_text(encoding="utf-8")),
        )
        for path in sorted(paths.compounding_harness_search_requests_dir.glob("*.json"))
    ]
    requests.sort(key=lambda item: (item.request.created_at, item.request.search_id), reverse=True)
    return tuple(requests)


def harness_recommendation_for_id(paths: RuntimePaths, recommendation_id: str) -> StoredHarnessRecommendation:
    """Resolve one stored recommendation artifact by id."""

    normalized_recommendation_id = recommendation_id.strip()
    if not normalized_recommendation_id:
        raise ValueError("recommendation_id may not be empty")
    for stored in discover_harness_recommendations(paths):
        if stored.recommendation.recommendation_id == normalized_recommendation_id:
            return stored
    raise ValueError(f"harness recommendation not found: {normalized_recommendation_id}")


def run_harness_benchmark(
    paths: RuntimePaths,
    loaded: LoadedConfig,
    *,
    candidate_id: str,
) -> HarnessBenchmarkRunOutcome:
    """Run one bounded preview-based benchmark against the current workspace baseline."""

    stored_candidate = harness_candidate_for_id(paths, candidate_id)
    candidate = stored_candidate.candidate
    started_at = datetime.now(timezone.utc)
    result_id = _benchmark_result_id(candidate.candidate_id, started_at)
    result_dir = paths.compounding_benchmark_results_dir
    result_dir.mkdir(parents=True, exist_ok=True)

    baseline_selection = preview_standard_runtime_selection(
        loaded.config,
        paths,
        preview_run_id=f"harness-baseline-{candidate.candidate_id}",
    )

    baseline_payload = {
        "baseline_ref": candidate.baseline_ref,
        "selection": baseline_selection.model_dump(mode="json"),
        "compounding_policy": loaded.config.policies.compounding.model_dump(mode="json"),
    }

    baseline_artifact_path = result_dir / f"{result_id}__baseline.json"
    write_text_atomic(baseline_artifact_path, json.dumps(baseline_payload, indent=2, sort_keys=True) + "\n")

    unsupported_reason: str | None = None
    if candidate.baseline_ref != DEFAULT_HARNESS_BASELINE_REF:
        unsupported_reason = (
            f"unsupported baseline_ref {candidate.baseline_ref!r}; supported baseline is {DEFAULT_HARNESS_BASELINE_REF!r}"
        )
    elif candidate.benchmark_suite_ref != DEFAULT_HARNESS_BENCHMARK_SUITE_REF:
        unsupported_reason = (
            "unsupported benchmark_suite_ref "
            f"{candidate.benchmark_suite_ref!r}; supported suite is {DEFAULT_HARNESS_BENCHMARK_SUITE_REF!r}"
        )
    elif candidate.compounding_policy_override is None and not candidate.prompt_asset_overrides:
        unsupported_reason = "candidate does not include a bounded config or prompt-asset override"

    if unsupported_reason is not None:
        completed_at = datetime.now(timezone.utc)
        candidate_artifact_path = result_dir / f"{result_id}__candidate.json"
        write_text_atomic(
            candidate_artifact_path,
            json.dumps(
                {
                    "candidate_id": candidate.candidate_id,
                    "state": candidate.state.value,
                    "supported": False,
                    "reason": unsupported_reason,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        result = HarnessBenchmarkResult(
            result_id=result_id,
            candidate_id=candidate.candidate_id,
            baseline_ref=candidate.baseline_ref,
            benchmark_suite_ref=candidate.benchmark_suite_ref,
            status=HarnessBenchmarkStatus.UNSUPPORTED,
            outcome=HarnessBenchmarkOutcome.UNSUPPORTED,
            started_at=started_at,
            completed_at=completed_at,
            outcome_summary=HarnessBenchmarkOutcomeSummary(
                selection_changed=False,
                changed_config_fields=(),
                changed_stage_bindings=(),
                baseline_mode_ref=_selection_ref_text(baseline_selection),
                candidate_mode_ref=_selection_ref_text(baseline_selection),
                message=unsupported_reason,
            ),
            cost_summary=HarnessBenchmarkCostSummary(
                baseline_governed_plus_budget_characters=loaded.config.policies.compounding.governed_plus_budget_characters,
                candidate_governed_plus_budget_characters=loaded.config.policies.compounding.governed_plus_budget_characters,
                budget_delta_characters=0,
            ),
            artifact_refs=(
                baseline_artifact_path.as_posix(),
                candidate_artifact_path.as_posix(),
            ),
        )
    else:
        candidate_config = loaded.config.model_copy(deep=True)
        if candidate.compounding_policy_override is not None:
            candidate_config.policies.compounding.profile = CompoundingProfile(candidate.compounding_policy_override.profile)
            candidate_config.policies.compounding.governed_plus_budget_characters = (
                candidate.compounding_policy_override.governed_plus_budget_characters
            )
        for prompt_override in candidate.prompt_asset_overrides:
            candidate_config.stages[prompt_override.stage].prompt_file = prompt_override.candidate_prompt_file
        changed_config_fields = diff_config_fields(loaded.config, candidate_config)
        candidate_selection = preview_standard_runtime_selection(
            candidate_config,
            paths,
            preview_run_id=f"harness-candidate-{candidate.candidate_id}",
        )
        completed_at = datetime.now(timezone.utc)
        changed_stage_bindings = _changed_stage_bindings(baseline_selection, candidate_selection)
        selection_changed = _selection_changed(baseline_selection, candidate_selection)
        candidate_artifact_path = result_dir / f"{result_id}__candidate.json"
        write_text_atomic(
            candidate_artifact_path,
            json.dumps(
                {
                    "candidate_id": candidate.candidate_id,
                    "selection": candidate_selection.model_dump(mode="json"),
                    "compounding_policy": candidate_config.policies.compounding.model_dump(mode="json"),
                    "changed_config_fields": list(changed_config_fields),
                    "prompt_asset_overrides": [
                        {
                            "stage": item.stage.value,
                            "source_ref": item.source_ref,
                            "candidate_prompt_file": item.candidate_prompt_file.as_posix(),
                        }
                        for item in candidate.prompt_asset_overrides
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        result = HarnessBenchmarkResult(
            result_id=result_id,
            candidate_id=candidate.candidate_id,
            baseline_ref=candidate.baseline_ref,
            benchmark_suite_ref=candidate.benchmark_suite_ref,
            status=HarnessBenchmarkStatus.COMPLETE,
            outcome=HarnessBenchmarkOutcome.CHANGED if selection_changed or changed_config_fields else HarnessBenchmarkOutcome.UNCHANGED,
            started_at=started_at,
            completed_at=completed_at,
            outcome_summary=HarnessBenchmarkOutcomeSummary(
                selection_changed=selection_changed,
                changed_config_fields=changed_config_fields,
                changed_stage_bindings=changed_stage_bindings,
                baseline_mode_ref=_selection_ref_text(baseline_selection),
                candidate_mode_ref=_selection_ref_text(candidate_selection),
                message=(
                    "candidate changes runtime preview selection"
                    if selection_changed
                    else "candidate changes bounded harness surfaces without altering preview selection"
                ),
            ),
            cost_summary=HarnessBenchmarkCostSummary(
                baseline_governed_plus_budget_characters=loaded.config.policies.compounding.governed_plus_budget_characters,
                candidate_governed_plus_budget_characters=candidate_config.policies.compounding.governed_plus_budget_characters,
                budget_delta_characters=(
                    candidate_config.policies.compounding.governed_plus_budget_characters
                    - loaded.config.policies.compounding.governed_plus_budget_characters
                ),
            ),
            artifact_refs=(
                baseline_artifact_path.as_posix(),
                candidate_artifact_path.as_posix(),
            ),
        )

    result_path = result_dir / f"{result.result_id}.json"
    write_text_atomic(result_path, result.model_dump_json(indent=2) + "\n")
    return HarnessBenchmarkRunOutcome(
        candidate=stored_candidate,
        result_path=result_path,
        result=result,
        baseline_artifact_path=baseline_artifact_path,
        candidate_artifact_path=candidate_artifact_path,
    )


def run_harness_search(
    paths: RuntimePaths,
    loaded: LoadedConfig,
    *,
    created_by: str = "cli.search",
) -> HarnessSearchRunOutcome:
    """Run one bounded config/assets-only search and persist recommendations."""

    request = _build_search_request(paths, loaded, created_by=created_by)
    request_path = _write_search_request(paths, request)
    generated_candidates = _materialize_search_candidates(paths, loaded, request)
    benchmark_outcomes = tuple(
        run_harness_benchmark(paths, loaded, candidate_id=item.candidate.candidate_id)
        for item in generated_candidates
    )
    stored_results = tuple(
        StoredHarnessBenchmarkResult(path=item.result_path, result=item.result)
        for item in benchmark_outcomes
    )
    recommendation = _build_recommendation(request, generated_candidates, stored_results, created_by=created_by)
    recommendation_path = _write_recommendation(paths, recommendation)
    return HarnessSearchRunOutcome(
        search_path=request_path,
        request=request,
        recommendation_path=recommendation_path,
        recommendation=recommendation,
        candidates=generated_candidates,
        benchmark_results=stored_results,
    )


def _build_search_request(
    paths: RuntimePaths,
    loaded: LoadedConfig,
    *,
    created_by: str,
) -> HarnessSearchRequestArtifact:
    created_at = datetime.now(timezone.utc)
    resolver = AssetResolver(paths.root)
    config_variants: list[HarnessCandidateCompoundingPolicy] = []
    seen_variants: set[tuple[str, int]] = set()
    for profile in (
        CompoundingProfile.BASELINE.value,
        CompoundingProfile.COMPOUNDING.value,
        CompoundingProfile.GOVERNED_PLUS.value,
    ):
        variant = HarnessCandidateCompoundingPolicy(
            profile=profile,
            governed_plus_budget_characters=loaded.config.policies.compounding.governed_plus_budget_characters,
        )
        key = (variant.profile, variant.governed_plus_budget_characters)
        if key in seen_variants:
            continue
        seen_variants.add(key)
        config_variants.append(variant)

    asset_targets: list[HarnessSearchAssetTarget] = []
    for stage, stage_config in sorted(loaded.config.stages.items(), key=lambda item: item[0].value):
        prompt_file = stage_config.prompt_file
        if prompt_file is None:
            continue
        try:
            resolved = resolver.resolve_file(prompt_file)
        except AssetResolutionError:
            continue
        if resolved.source_kind is not AssetSourceKind.WORKSPACE or resolved.relative_path is None:
            continue
        package_ref = f"package:{resolved.relative_path.as_posix()}"
        try:
            resolver.resolve_ref(package_ref)
        except AssetResolutionError:
            continue
        asset_targets.append(HarnessSearchAssetTarget(stage=stage, source_ref=package_ref))

    return HarnessSearchRequestArtifact(
        search_id=_search_id(created_at),
        baseline_ref=DEFAULT_HARNESS_BASELINE_REF,
        benchmark_suite_ref=DEFAULT_HARNESS_BENCHMARK_SUITE_REF,
        config_variants=tuple(config_variants),
        asset_targets=tuple(asset_targets),
        created_at=created_at,
        created_by=created_by,
    )


def _write_search_request(paths: RuntimePaths, request: HarnessSearchRequestArtifact) -> Path:
    request_dir = paths.compounding_harness_search_requests_dir
    request_dir.mkdir(parents=True, exist_ok=True)
    path = request_dir / f"{request.search_id}.json"
    write_text_atomic(path, request.model_dump_json(indent=2) + "\n")
    return path


def _materialize_search_candidates(
    paths: RuntimePaths,
    loaded: LoadedConfig,
    request: HarnessSearchRequestArtifact,
) -> tuple[StoredHarnessCandidate, ...]:
    candidates: list[StoredHarnessCandidate] = []
    for variant in request.config_variants:
        artifact = HarnessCandidateArtifact(
            candidate_id=_config_candidate_id(request.search_id, variant),
            name=f"Bounded config variant {variant.profile}",
            baseline_ref=request.baseline_ref,
            benchmark_suite_ref=request.benchmark_suite_ref,
            changed_surfaces=(
                HarnessChangedSurface(
                    kind=HarnessChangedSurfaceKind.CONFIG,
                    target="policies.compounding",
                    summary=f"Evaluate compounding profile {variant.profile} through bounded preview benchmarking.",
                ),
            ),
            compounding_policy_override=variant,
            reviewer_note=f"Generated from bounded harness search {request.search_id}.",
            created_at=request.created_at,
            created_by=request.created_by,
        )
        candidates.append(_write_candidate(paths, artifact))

    resolver = AssetResolver(paths.root)
    for target in request.asset_targets:
        resolved = resolver.resolve_ref(target.source_ref)
        asset_dir = paths.compounding_harness_candidate_assets_dir / request.search_id
        asset_dir.mkdir(parents=True, exist_ok=True)
        asset_name = f"{target.stage.value}--{_slugify_filename(target.source_ref)}.md"
        candidate_prompt_file = asset_dir / asset_name
        write_text_atomic(candidate_prompt_file, resolved.read_text() + "\n")
        artifact = HarnessCandidateArtifact(
            candidate_id=f"harness.search.{request.search_id}.asset.{target.stage.value}",
            name=f"Bounded asset variant {target.stage.value}",
            baseline_ref=request.baseline_ref,
            benchmark_suite_ref=request.benchmark_suite_ref,
            changed_surfaces=(
                HarnessChangedSurface(
                    kind=HarnessChangedSurfaceKind.PROMPT_ASSET,
                    target=f"stages.{target.stage.value}.prompt_file",
                    summary=f"Evaluate {target.source_ref} for the {target.stage.value} stage prompt.",
                ),
            ),
            prompt_asset_overrides=(
                HarnessCandidatePromptAssetOverride(
                    stage=target.stage,
                    source_ref=target.source_ref,
                    candidate_prompt_file=candidate_prompt_file,
                ),
            ),
            reviewer_note=f"Generated from bounded harness search {request.search_id}.",
            created_at=request.created_at,
            created_by=request.created_by,
        )
        candidates.append(_write_candidate(paths, artifact))

    return tuple(candidates)


def _write_candidate(paths: RuntimePaths, artifact: HarnessCandidateArtifact) -> StoredHarnessCandidate:
    candidate_dir = paths.compounding_harness_candidates_dir
    candidate_dir.mkdir(parents=True, exist_ok=True)
    path = candidate_dir / f"{artifact.candidate_id}.json"
    write_text_atomic(path, artifact.model_dump_json(indent=2) + "\n")
    return StoredHarnessCandidate(path=path, candidate=artifact)


def _build_recommendation(
    request: HarnessSearchRequestArtifact,
    candidates: tuple[StoredHarnessCandidate, ...],
    benchmark_results: tuple[StoredHarnessBenchmarkResult, ...],
    *,
    created_by: str,
) -> HarnessRecommendationArtifact:
    ranked = [
        item
        for item in benchmark_results
        if item.result.status is HarnessBenchmarkStatus.COMPLETE
        and item.result.outcome_summary.changed_stage_bindings
    ]
    ranked.sort(
        key=lambda item: (
            item.result.cost_summary.budget_delta_characters,
            item.result.candidate_id,
        )
    )
    recommended_candidate_id: str | None = None
    recommended_result_id: str | None = None
    disposition = HarnessRecommendationDisposition.NO_CHANGE
    if ranked:
        recommended_candidate_id = ranked[0].result.candidate_id
        recommended_result_id = ranked[0].result.result_id
        disposition = HarnessRecommendationDisposition.RECOMMEND
        summary = (
            f"Recommend {recommended_candidate_id} from bounded search {request.search_id} "
            f"based on benchmark {recommended_result_id}."
        )
    else:
        summary = f"No bounded harness change is recommended from search {request.search_id}."
    created_at = datetime.now(timezone.utc)
    return HarnessRecommendationArtifact(
        recommendation_id=f"recommend.{request.search_id}",
        search_id=request.search_id,
        disposition=disposition,
        recommended_candidate_id=recommended_candidate_id,
        recommended_result_id=recommended_result_id,
        candidate_ids=tuple(item.candidate.candidate_id for item in candidates),
        benchmark_result_ids=tuple(item.result.result_id for item in benchmark_results),
        summary=summary,
        created_at=created_at,
        created_by=created_by,
    )


def _write_recommendation(paths: RuntimePaths, artifact: HarnessRecommendationArtifact) -> Path:
    recommendation_dir = paths.compounding_harness_recommendations_dir
    recommendation_dir.mkdir(parents=True, exist_ok=True)
    path = recommendation_dir / f"{artifact.recommendation_id}.json"
    write_text_atomic(path, artifact.model_dump_json(indent=2) + "\n")
    return path


def _search_id(timestamp: datetime) -> str:
    return f"search.{timestamp.strftime('%Y%m%dT%H%M%SZ')}"


def _config_candidate_id(search_id: str, variant: HarnessCandidateCompoundingPolicy) -> str:
    if variant.profile == CompoundingProfile.GOVERNED_PLUS.value:
        return (
            f"harness.search.{search_id}.config.{variant.profile}"
            f".budget-{variant.governed_plus_budget_characters}"
        )
    return f"harness.search.{search_id}.config.{variant.profile}"


def _benchmark_result_id(candidate_id: str, timestamp: datetime) -> str:
    token = _slugify_filename(candidate_id)
    return f"bench.{timestamp.strftime('%Y%m%dT%H%M%SZ')}.{token}"


def _selection_ref_text(selection: object) -> str:
    selection_ref = getattr(getattr(selection, "selection", None), "ref", None)
    if selection_ref is None:
        return "unknown"
    return f"{selection_ref.id}@{selection_ref.version}"


def _selection_changed(baseline: object, candidate: object) -> bool:
    return baseline.model_dump(mode="json") != candidate.model_dump(mode="json")


def _changed_stage_bindings(baseline: object, candidate: object) -> tuple[str, ...]:
    baseline_bindings = {
        binding.node_id: binding.model_dump(mode="json")
        for binding in getattr(baseline, "stage_bindings", ())
    }
    candidate_bindings = {
        binding.node_id: binding.model_dump(mode="json")
        for binding in getattr(candidate, "stage_bindings", ())
    }
    changed = [
        node_id
        for node_id in sorted(set(baseline_bindings) | set(candidate_bindings))
        if baseline_bindings.get(node_id) != candidate_bindings.get(node_id)
    ]
    return tuple(changed)


def _slugify_filename(value: str) -> str:
    return _FILENAME_TOKEN_RE.sub("-", value).strip("-") or "artifact"
