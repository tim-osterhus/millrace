"""Compounding helpers for runtime-owned governed reuse."""

from .extraction import clear_run_scoped_procedure_candidates, persist_candidate_from_transition
from .lifecycle import (
    deprecate_procedure,
    discover_governed_procedures,
    discover_workspace_procedures,
    governed_procedure_for_id,
    lifecycle_history_for_procedure,
    load_retrievable_workspace_procedures,
    promote_procedure,
)
from .retrieval import (
    build_injected_procedure_bundle,
    procedure_retrieval_rule_for_stage,
    render_injected_procedure_block,
)

__all__ = [
    "build_injected_procedure_bundle",
    "clear_run_scoped_procedure_candidates",
    "deprecate_procedure",
    "discover_governed_procedures",
    "discover_workspace_procedures",
    "governed_procedure_for_id",
    "lifecycle_history_for_procedure",
    "load_retrievable_workspace_procedures",
    "persist_candidate_from_transition",
    "promote_procedure",
    "procedure_retrieval_rule_for_stage",
    "render_injected_procedure_block",
]
