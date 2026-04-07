"""Compounding helpers for runtime-owned governed reuse."""

from .extraction import clear_run_scoped_procedure_candidates, persist_candidate_from_transition
from .finalization import (
    CompoundingFlushCheckpoint,
    CompoundingFlushMilestone,
    flush_milestone_for_transition,
    flush_run_scoped_compounding_candidates,
)
from .integrity import build_compounding_integrity_report
from .orientation import build_compounding_orientation_snapshot
from .harness import (
    discover_harness_benchmark_results,
    discover_harness_candidates,
    discover_harness_recommendations,
    discover_harness_search_requests,
    harness_benchmark_result_for_id,
    harness_candidate_for_id,
    harness_recommendation_for_id,
    run_harness_search,
    run_harness_benchmark,
)
from .lifecycle import (
    deprecate_procedure,
    discover_governed_procedures,
    discover_lifecycle_records,
    discover_workspace_procedures,
    governed_procedure_for_id,
    lifecycle_history_for_procedure,
    load_retrievable_workspace_procedures,
    promote_procedure,
)
from .retrieval import (
    build_injected_context_fact_bundle,
    build_injected_procedure_bundle,
    context_fact_retrieval_rule_for_stage,
    procedure_retrieval_rule_for_stage,
    render_injected_context_fact_block,
    render_injected_procedure_block,
)

__all__ = [
    "CompoundingFlushCheckpoint",
    "CompoundingFlushMilestone",
    "build_injected_context_fact_bundle",
    "build_injected_procedure_bundle",
    "build_compounding_integrity_report",
    "build_compounding_orientation_snapshot",
    "clear_run_scoped_procedure_candidates",
    "context_fact_retrieval_rule_for_stage",
    "deprecate_procedure",
    "discover_governed_procedures",
    "discover_harness_benchmark_results",
    "discover_harness_candidates",
    "discover_harness_recommendations",
    "discover_harness_search_requests",
    "discover_lifecycle_records",
    "discover_workspace_procedures",
    "flush_milestone_for_transition",
    "flush_run_scoped_compounding_candidates",
    "governed_procedure_for_id",
    "harness_benchmark_result_for_id",
    "harness_candidate_for_id",
    "harness_recommendation_for_id",
    "lifecycle_history_for_procedure",
    "load_retrievable_workspace_procedures",
    "persist_candidate_from_transition",
    "promote_procedure",
    "procedure_retrieval_rule_for_stage",
    "render_injected_context_fact_block",
    "render_injected_procedure_block",
    "run_harness_benchmark",
    "run_harness_search",
]
