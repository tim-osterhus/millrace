"""Governed harness candidate discovery and bounded benchmark execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import re

from ..config import CompoundingProfile, LoadedConfig, diff_config_fields
from ..contract_harness import (
    HarnessBenchmarkCostSummary,
    HarnessBenchmarkOutcome,
    HarnessBenchmarkOutcomeSummary,
    HarnessBenchmarkResult,
    HarnessBenchmarkStatus,
    HarnessCandidateArtifact,
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
class HarnessBenchmarkRunOutcome:
    """Outcome of one bounded benchmark execution."""

    candidate: StoredHarnessCandidate
    result_path: Path
    result: HarnessBenchmarkResult
    baseline_artifact_path: Path
    candidate_artifact_path: Path


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
    elif candidate.compounding_policy_override is None:
        unsupported_reason = "candidate does not include a compounding policy override"

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
        candidate_config.policies.compounding.profile = CompoundingProfile(candidate.compounding_policy_override.profile)
        candidate_config.policies.compounding.governed_plus_budget_characters = (
            candidate.compounding_policy_override.governed_plus_budget_characters
        )
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
                    else "candidate changes compounding policy without altering preview selection"
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


def _benchmark_result_id(candidate_id: str, timestamp: datetime) -> str:
    token = _FILENAME_TOKEN_RE.sub("-", candidate_id).strip("-") or "candidate"
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
