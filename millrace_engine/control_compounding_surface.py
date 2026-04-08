"""Compounding-facing control surface helpers."""

from __future__ import annotations

from pydantic import ValidationError

from .compounding import (
    build_compounding_integrity_report,
    build_compounding_orientation_snapshot,
    discover_governed_procedures,
    discover_harness_benchmark_results,
    discover_harness_candidates,
    discover_harness_recommendations,
    governed_procedure_for_id,
    harness_benchmark_result_for_id,
    harness_candidate_for_id,
    harness_recommendation_for_id,
    lifecycle_history_for_procedure,
    run_harness_benchmark,
    run_harness_search,
)
from .compounding.integrity import CompoundingIntegrityReport
from .context_facts import context_fact_for_id, discover_context_facts
from .contract_compounding import ProcedureScope
from .control_actions import (
    compounding_deprecate as compounding_deprecate_operation,
)
from .control_actions import (
    compounding_promote as compounding_promote_operation,
)
from .control_common import ControlError, single_line_message, validation_error_message
from .control_compounding_views import (
    compounding_context_fact_view,
    compounding_harness_benchmark_view,
    compounding_harness_candidate_view,
    compounding_harness_recommendation_view,
    compounding_lifecycle_record_view,
    compounding_orientation_entry_view,
    compounding_procedure_view,
    compounding_relationship_cluster_view,
    latest_compounding_usage_summary,
)
from .control_models import (
    CompoundingContextFactListReport,
    CompoundingContextFactReport,
    CompoundingGovernanceSummaryView,
    CompoundingHarnessBenchmarkListReport,
    CompoundingHarnessBenchmarkReport,
    CompoundingHarnessCandidateListReport,
    CompoundingHarnessCandidateReport,
    CompoundingHarnessRecommendationListReport,
    CompoundingHarnessRecommendationReport,
    CompoundingOrientationArtifactView,
    CompoundingOrientationReport,
    CompoundingProcedureListReport,
    CompoundingProcedureReport,
    OperationResult,
)


def compounding_procedures(control) -> CompoundingProcedureListReport:
    try:
        procedures = discover_governed_procedures(control.paths)
    except ValidationError as exc:
        raise ControlError(f"compounding procedures are invalid: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"compounding procedures are invalid: {single_line_message(exc)}") from exc
    return CompoundingProcedureListReport(
        config_path=control.config_path,
        procedures=tuple(compounding_procedure_view(procedure) for procedure in procedures),
    )


def compounding_procedure(control, procedure_id: str) -> CompoundingProcedureReport:
    normalized_procedure_id = procedure_id.strip()
    if not normalized_procedure_id:
        raise ControlError("compounding_procedure requires a procedure_id")
    try:
        procedure = governed_procedure_for_id(control.paths, normalized_procedure_id, include_run_candidates=True)
        history = (
            lifecycle_history_for_procedure(control.paths, procedure.artifact.procedure_id)
            if procedure.artifact.scope is ProcedureScope.WORKSPACE
            else ()
        )
    except ValidationError as exc:
        raise ControlError(f"compounding procedure is invalid: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"compounding procedure is invalid: {single_line_message(exc)}") from exc
    return CompoundingProcedureReport(
        config_path=control.config_path,
        procedure=compounding_procedure_view(procedure),
        lifecycle_records=tuple(
            compounding_lifecycle_record_view(item.path, record=item.record) for item in history
        ),
    )


def compounding_context_facts(control) -> CompoundingContextFactListReport:
    try:
        facts = discover_context_facts(control.paths, include_run_candidates=True)
    except ValidationError as exc:
        raise ControlError(f"compounding context facts are invalid: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"compounding context facts are invalid: {single_line_message(exc)}") from exc
    return CompoundingContextFactListReport(
        config_path=control.config_path,
        facts=tuple(compounding_context_fact_view(fact) for fact in facts),
    )


def compounding_context_fact(control, fact_id: str) -> CompoundingContextFactReport:
    normalized_fact_id = fact_id.strip()
    if not normalized_fact_id:
        raise ControlError("compounding_context_fact requires a fact_id")
    try:
        fact = context_fact_for_id(control.paths, normalized_fact_id, include_run_candidates=True)
    except ValidationError as exc:
        raise ControlError(f"compounding context fact is invalid: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"compounding context fact is invalid: {single_line_message(exc)}") from exc
    return CompoundingContextFactReport(
        config_path=control.config_path,
        fact=compounding_context_fact_view(fact),
    )


def compounding_governance_summary(control) -> CompoundingGovernanceSummaryView:
    try:
        procedures = discover_governed_procedures(control.paths)
        facts = discover_context_facts(control.paths, include_run_candidates=True)
        candidates = discover_harness_candidates(control.paths)
        benchmarks = discover_harness_benchmark_results(control.paths)
        recommendations = discover_harness_recommendations(control.paths)
    except ValidationError as exc:
        raise ControlError(f"compounding governance summary is invalid: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"compounding governance summary is invalid: {single_line_message(exc)}") from exc

    procedure_eligible = sum(1 for item in procedures if item.retrieval_status == "eligible")
    procedure_pending_review = sum(1 for item in procedures if item.retrieval_status in {"stale", "run_candidate"})
    procedure_deprecated = sum(1 for item in procedures if item.retrieval_status == "deprecated")

    context_fact_eligible = sum(1 for item in facts if item.retrieval_status == "eligible")
    context_fact_pending_review = sum(1 for item in facts if item.retrieval_status in {"stale", "run_candidate"})
    context_fact_deprecated = sum(1 for item in facts if item.retrieval_status == "deprecated")

    harness_candidate_pending_review = sum(1 for item in candidates if item.candidate.state.value == "candidate")
    harness_candidate_accepted = sum(1 for item in candidates if item.candidate.state.value == "accepted")
    harness_candidate_rejected = sum(1 for item in candidates if item.candidate.state.value == "rejected")

    recommendation_pending = sum(
        1 for item in recommendations if item.recommendation.disposition.value == "recommend"
    )
    recommendation_no_change = sum(
        1 for item in recommendations if item.recommendation.disposition.value == "no_change"
    )

    latest_recommendation = recommendations[0] if recommendations else None
    recent_usage_run_id, recent_usage_procedure_count, recent_usage_context_fact_count = (
        latest_compounding_usage_summary(control.paths.runs_dir)
    )
    pending_governance_items = (
        procedure_pending_review
        + context_fact_pending_review
        + harness_candidate_pending_review
        + recommendation_pending
    )
    return CompoundingGovernanceSummaryView(
        config_path=control.config_path,
        procedure_total=len(procedures),
        procedure_eligible=procedure_eligible,
        procedure_pending_review=procedure_pending_review,
        procedure_deprecated=procedure_deprecated,
        context_fact_total=len(facts),
        context_fact_eligible=context_fact_eligible,
        context_fact_pending_review=context_fact_pending_review,
        context_fact_deprecated=context_fact_deprecated,
        harness_candidate_total=len(candidates),
        harness_candidate_pending_review=harness_candidate_pending_review,
        harness_candidate_accepted=harness_candidate_accepted,
        harness_candidate_rejected=harness_candidate_rejected,
        benchmark_total=len(benchmarks),
        recommendation_total=len(recommendations),
        recommendation_pending=recommendation_pending,
        recommendation_no_change=recommendation_no_change,
        pending_governance_items=pending_governance_items,
        latest_recommendation_id=(
            latest_recommendation.recommendation.recommendation_id if latest_recommendation is not None else None
        ),
        latest_recommendation_summary=(
            latest_recommendation.recommendation.summary if latest_recommendation is not None else None
        ),
        recent_usage_run_id=recent_usage_run_id,
        recent_usage_procedure_count=recent_usage_procedure_count,
        recent_usage_context_fact_count=recent_usage_context_fact_count,
    )


def compounding_orientation(control, *, query: str | None = None) -> CompoundingOrientationReport:
    normalized_query = " ".join(query.strip().split()) if query is not None else None
    if normalized_query == "":
        normalized_query = None
    try:
        snapshot = build_compounding_orientation_snapshot(control.paths, query=normalized_query)
    except ValidationError as exc:
        raise ControlError(f"compounding orientation is invalid: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"compounding orientation is invalid: {single_line_message(exc)}") from exc
    return CompoundingOrientationReport(
        config_path=control.config_path,
        query=normalized_query,
        secondary_surface_note=snapshot.index_artifact.secondary_surface_note,
        index_artifact=CompoundingOrientationArtifactView(
            path=snapshot.index_path,
            generated_at=snapshot.index_artifact.generated_at,
            item_count=len(snapshot.index_artifact.entries),
        ),
        relationship_artifact=CompoundingOrientationArtifactView(
            path=snapshot.relationship_summary_path,
            generated_at=snapshot.relationship_summary_artifact.generated_at,
            item_count=len(snapshot.relationship_summary_artifact.clusters),
        ),
        family_counts=snapshot.index_artifact.family_counts,
        cluster_counts=snapshot.relationship_summary_artifact.cluster_counts,
        entries=tuple(
            compounding_orientation_entry_view(control.paths.root, entry)
            for entry in snapshot.entries
        ),
        relationship_clusters=tuple(
            compounding_relationship_cluster_view(cluster)
            for cluster in snapshot.relationship_clusters
        ),
    )


def compounding_lint(control) -> CompoundingIntegrityReport:
    try:
        return build_compounding_integrity_report(control.paths)
    except ValidationError as exc:
        raise ControlError(f"compounding lint is invalid: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"compounding lint is invalid: {single_line_message(exc)}") from exc


def compounding_harness_candidates(control) -> CompoundingHarnessCandidateListReport:
    try:
        candidates = discover_harness_candidates(control.paths)
    except ValidationError as exc:
        raise ControlError(f"compounding harness candidates are invalid: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"compounding harness candidates are invalid: {single_line_message(exc)}") from exc
    return CompoundingHarnessCandidateListReport(
        config_path=control.config_path,
        candidates=tuple(compounding_harness_candidate_view(candidate) for candidate in candidates),
    )


def compounding_harness_candidate(control, candidate_id: str) -> CompoundingHarnessCandidateReport:
    normalized_candidate_id = candidate_id.strip()
    if not normalized_candidate_id:
        raise ControlError("compounding_harness_candidate requires a candidate_id")
    try:
        candidate = harness_candidate_for_id(control.paths, normalized_candidate_id)
        benchmarks = discover_harness_benchmark_results(control.paths, candidate_id=normalized_candidate_id)
    except ValidationError as exc:
        raise ControlError(f"compounding harness candidate is invalid: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"compounding harness candidate is invalid: {single_line_message(exc)}") from exc
    return CompoundingHarnessCandidateReport(
        config_path=control.config_path,
        candidate=compounding_harness_candidate_view(candidate),
        recent_benchmarks=tuple(compounding_harness_benchmark_view(item) for item in benchmarks[:5]),
    )


def compounding_harness_benchmarks(control, *, candidate_id: str | None = None) -> CompoundingHarnessBenchmarkListReport:
    try:
        benchmarks = discover_harness_benchmark_results(control.paths, candidate_id=candidate_id)
    except ValidationError as exc:
        raise ControlError(f"compounding harness benchmarks are invalid: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"compounding harness benchmarks are invalid: {single_line_message(exc)}") from exc
    return CompoundingHarnessBenchmarkListReport(
        config_path=control.config_path,
        benchmarks=tuple(compounding_harness_benchmark_view(item) for item in benchmarks),
    )


def compounding_harness_benchmark(control, result_id: str) -> CompoundingHarnessBenchmarkReport:
    normalized_result_id = result_id.strip()
    if not normalized_result_id:
        raise ControlError("compounding_harness_benchmark requires a result_id")
    try:
        benchmark = harness_benchmark_result_for_id(control.paths, normalized_result_id)
    except ValidationError as exc:
        raise ControlError(f"compounding harness benchmark is invalid: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"compounding harness benchmark is invalid: {single_line_message(exc)}") from exc
    return CompoundingHarnessBenchmarkReport(
        config_path=control.config_path,
        benchmark=compounding_harness_benchmark_view(benchmark),
    )


def compounding_harness_run_benchmark(control, candidate_id: str) -> OperationResult:
    try:
        outcome = run_harness_benchmark(control.paths, control.loaded, candidate_id=candidate_id)
    except ValidationError as exc:
        raise ControlError(f"compounding harness benchmark failed: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"compounding harness benchmark failed: {single_line_message(exc)}") from exc
    return OperationResult(
        mode="direct",
        applied=True,
        message="compounding harness benchmark recorded",
        payload={
            "candidate_id": outcome.result.candidate_id,
            "result_id": outcome.result.result_id,
            "result_path": outcome.result_path.as_posix(),
            "status": outcome.result.status.value,
            "outcome": outcome.result.outcome.value,
            "benchmark_suite_ref": outcome.result.benchmark_suite_ref,
        },
    )


def compounding_harness_run_search(control, *, created_by: str = "cli.search") -> OperationResult:
    try:
        outcome = run_harness_search(control.paths, control.loaded, created_by=created_by)
    except ValidationError as exc:
        raise ControlError(f"compounding harness search failed: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"compounding harness search failed: {single_line_message(exc)}") from exc
    return OperationResult(
        mode="direct",
        applied=True,
        message="compounding harness search recorded",
        payload={
            "search_id": outcome.request.search_id,
            "search_path": outcome.search_path.as_posix(),
            "candidate_count": len(outcome.candidates),
            "benchmark_count": len(outcome.benchmark_results),
            "recommendation_id": outcome.recommendation.recommendation_id,
            "recommendation_path": outcome.recommendation_path.as_posix(),
            "disposition": outcome.recommendation.disposition.value,
            "recommended_candidate_id": outcome.recommendation.recommended_candidate_id,
            "recommended_result_id": outcome.recommendation.recommended_result_id,
        },
    )


def compounding_harness_recommendations(control) -> CompoundingHarnessRecommendationListReport:
    try:
        recommendations = discover_harness_recommendations(control.paths)
    except ValidationError as exc:
        raise ControlError(f"compounding harness recommendations are invalid: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"compounding harness recommendations are invalid: {single_line_message(exc)}") from exc
    return CompoundingHarnessRecommendationListReport(
        config_path=control.config_path,
        recommendations=tuple(compounding_harness_recommendation_view(item) for item in recommendations),
    )


def compounding_harness_recommendation(control, recommendation_id: str) -> CompoundingHarnessRecommendationReport:
    normalized_recommendation_id = recommendation_id.strip()
    if not normalized_recommendation_id:
        raise ControlError("compounding_harness_recommendation requires a recommendation_id")
    try:
        recommendation = harness_recommendation_for_id(control.paths, normalized_recommendation_id)
    except ValidationError as exc:
        raise ControlError(f"compounding harness recommendation is invalid: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"compounding harness recommendation is invalid: {single_line_message(exc)}") from exc
    return CompoundingHarnessRecommendationReport(
        config_path=control.config_path,
        recommendation=compounding_harness_recommendation_view(recommendation),
    )


def compounding_promote(control, procedure_id: str, *, changed_by: str = "cli", reason: str) -> OperationResult:
    return compounding_promote_operation(
        control.paths,
        procedure_id=procedure_id,
        changed_by=changed_by,
        reason=reason,
        daemon_running=control.is_daemon_running(),
    )


def compounding_deprecate(
    control,
    procedure_id: str,
    *,
    changed_by: str = "cli",
    reason: str,
    replacement_procedure_id: str | None = None,
) -> OperationResult:
    return compounding_deprecate_operation(
        control.paths,
        procedure_id=procedure_id,
        changed_by=changed_by,
        reason=reason,
        replacement_procedure_id=replacement_procedure_id,
        daemon_running=control.is_daemon_running(),
    )
