"""View builders shared across compounding-facing control helpers."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from .control_models import (
    CompoundingContextFactView,
    CompoundingHarnessBenchmarkView,
    CompoundingHarnessCandidateView,
    CompoundingHarnessRecommendationView,
    CompoundingLifecycleRecordView,
    CompoundingOrientationEntryView,
    CompoundingProcedureView,
    CompoundingRelationshipClusterView,
)
from .control_reports import read_run_provenance


def compounding_lifecycle_record_view(
    path: Path,
    *,
    record: object,
) -> CompoundingLifecycleRecordView:
    return CompoundingLifecycleRecordView(
        record_id=getattr(record, "record_id"),
        procedure_id=getattr(record, "procedure_id"),
        state=getattr(record, "state"),
        scope=getattr(record, "scope"),
        changed_at=getattr(record, "changed_at"),
        changed_by=getattr(record, "changed_by"),
        reason=getattr(record, "reason"),
        replacement_procedure_id=getattr(record, "replacement_procedure_id"),
        record_path=path,
    )


def compounding_procedure_view(procedure: object) -> CompoundingProcedureView:
    latest_record = getattr(procedure, "latest_record")
    latest_view = (
        compounding_lifecycle_record_view(latest_record.path, record=latest_record.record)
        if latest_record is not None
        else None
    )
    artifact = getattr(procedure, "artifact")
    return CompoundingProcedureView(
        procedure_id=artifact.procedure_id,
        scope=artifact.scope,
        source_run_id=artifact.source_run_id,
        source_stage=artifact.source_stage.value,
        title=artifact.title,
        summary=artifact.summary,
        artifact_path=getattr(procedure, "artifact_path"),
        retrieval_status=getattr(procedure, "retrieval_status"),
        eligible_for_retrieval=getattr(procedure, "eligible_for_retrieval"),
        evidence_refs=artifact.evidence_refs,
        latest_lifecycle_record=latest_view,
        lifecycle_record_count=len(getattr(procedure, "lifecycle_records")),
    )


def compounding_harness_candidate_view(candidate: object) -> CompoundingHarnessCandidateView:
    artifact = getattr(candidate, "candidate")
    return CompoundingHarnessCandidateView(
        candidate_id=artifact.candidate_id,
        name=artifact.name,
        baseline_ref=artifact.baseline_ref,
        benchmark_suite_ref=artifact.benchmark_suite_ref,
        state=artifact.state,
        changed_surfaces=artifact.changed_surfaces,
        has_compounding_policy_override=artifact.compounding_policy_override is not None,
        reviewer_note=artifact.reviewer_note,
        created_at=artifact.created_at,
        created_by=artifact.created_by,
        artifact_path=getattr(candidate, "path"),
    )


def compounding_harness_benchmark_view(result: object) -> CompoundingHarnessBenchmarkView:
    benchmark = getattr(result, "result")
    return CompoundingHarnessBenchmarkView(
        result_id=benchmark.result_id,
        candidate_id=benchmark.candidate_id,
        benchmark_suite_ref=benchmark.benchmark_suite_ref,
        status=benchmark.status,
        outcome=benchmark.outcome,
        completed_at=benchmark.completed_at,
        result_path=getattr(result, "path"),
        outcome_summary=benchmark.outcome_summary,
        cost_summary=benchmark.cost_summary,
        artifact_refs=benchmark.artifact_refs,
    )


def compounding_harness_recommendation_view(result: object) -> CompoundingHarnessRecommendationView:
    recommendation = getattr(result, "recommendation")
    return CompoundingHarnessRecommendationView(
        recommendation_id=recommendation.recommendation_id,
        search_id=recommendation.search_id,
        disposition=recommendation.disposition,
        recommended_candidate_id=recommendation.recommended_candidate_id,
        recommended_result_id=recommendation.recommended_result_id,
        candidate_ids=recommendation.candidate_ids,
        benchmark_result_ids=recommendation.benchmark_result_ids,
        summary=recommendation.summary,
        created_at=recommendation.created_at,
        created_by=recommendation.created_by,
        artifact_path=getattr(result, "path"),
    )


def compounding_context_fact_view(fact: object) -> CompoundingContextFactView:
    artifact = getattr(fact, "artifact")
    return CompoundingContextFactView(
        fact_id=artifact.fact_id,
        scope=artifact.scope,
        lifecycle_state=artifact.lifecycle_state,
        source_run_id=artifact.source_run_id,
        source_stage=artifact.source_stage.value,
        title=artifact.title,
        statement=artifact.statement,
        summary=artifact.summary,
        retrieval_status=getattr(fact, "retrieval_status"),
        eligible_for_retrieval=getattr(fact, "eligible_for_retrieval"),
        tags=artifact.tags,
        evidence_refs=artifact.evidence_refs,
        observed_at=artifact.observed_at,
        stale_reason=artifact.stale_reason,
        supersedes_fact_id=artifact.supersedes_fact_id,
        artifact_path=getattr(fact, "path"),
    )


def orientation_artifact_path(root: Path, artifact_ref: str) -> Path:
    path = Path(artifact_ref)
    return path if path.is_absolute() else (root / path).resolve()


def compounding_orientation_entry_view(root: Path, entry: object) -> CompoundingOrientationEntryView:
    return CompoundingOrientationEntryView(
        entry_id=getattr(entry, "entry_id"),
        family=getattr(entry, "family"),
        status=getattr(entry, "status"),
        label=getattr(entry, "label"),
        summary=getattr(entry, "summary"),
        artifact_path=orientation_artifact_path(root, getattr(entry, "artifact_ref")),
        source_run_id=getattr(entry, "source_run_id"),
        source_stage=(
            getattr(getattr(entry, "source_stage"), "value")
            if getattr(entry, "source_stage") is not None
            else None
        ),
        tags=getattr(entry, "tags"),
        evidence_refs=getattr(entry, "evidence_refs"),
        related_ids=getattr(entry, "related_ids"),
    )


def compounding_relationship_cluster_view(cluster: object) -> CompoundingRelationshipClusterView:
    return CompoundingRelationshipClusterView(
        cluster_id=getattr(cluster, "cluster_id"),
        kind=getattr(cluster, "kind"),
        label=getattr(cluster, "label"),
        summary=getattr(cluster, "summary"),
        member_ids=getattr(cluster, "member_ids"),
        shared_terms=getattr(cluster, "shared_terms"),
    )


def latest_compounding_usage_summary(runs_dir: Path) -> tuple[str | None, int, int]:
    if not runs_dir.exists():
        return (None, 0, 0)
    run_dirs = [path for path in runs_dir.iterdir() if path.is_dir() and not path.name.startswith(".")]
    for run_dir in sorted(run_dirs, key=lambda item: item.name, reverse=True):
        try:
            report = read_run_provenance(run_dir)
        except (ValidationError, ValueError, OSError):
            continue
        compounding = report.compounding if report is not None else None
        if compounding is None:
            continue
        if compounding.injected_procedure_count <= 0 and compounding.injected_fact_count <= 0:
            continue
        return (
            report.run_id,
            compounding.injected_procedure_count,
            compounding.injected_fact_count,
        )
    return (None, 0, 0)
