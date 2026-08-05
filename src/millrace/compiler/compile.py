"""Public compiler orchestration API.

This module coordinates compiler passes and preserves the package public
`compile_workflow` and `CompileResult` surface.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from millrace.compiler.authority import (
    validate_known_source_sections,
    validate_selected_authority_values,
    validate_unselected_catalog,
)
from millrace.compiler.build import build_selected_plan
from millrace.compiler.identity import validate_workflow_identity
from millrace.compiler.operator_waits import (
    normalize_operator_waits,
    validate_operator_wait_references,
)
from millrace.compiler.references import (
    collect_id_indexes,
    validate_action_outcomes_belong_to_action_stage,
    validate_action_references,
    validate_capability_values,
    validate_completion_remediation_references,
    validate_concurrency_policy_references,
    validate_counter_references,
    validate_declared_outcomes_belong_to_stage,
    validate_declared_outcomes_have_actions,
    validate_effect_declarations,
    validate_external_enqueue_route_references,
    validate_fanout_references,
    validate_generated_work_route_references,
    validate_intervention_option_references,
    validate_join_references,
    validate_lineage_policy_references,
    validate_outcome_references,
    validate_partition_references,
    validate_recovery_policy_references,
    validate_runner_binding_references,
    validate_stage_references,
    validate_wait_state_references,
)
from millrace.compiler.runner_bindings import (
    DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY,
    SelectedRunnerAdapterPolicy,
    normalize_selected_runner_bindings,
)
from millrace.compiler.schemas import validate_artifact_schema_declarations
from millrace.compiler.source import mapping
from millrace.compiler.terminal_actions import (
    validate_route_action_contracts,
    validate_terminal_action_kinds,
    validate_terminal_actions_are_unambiguous,
    validate_terminal_outcome_markers_are_unambiguous,
)
from millrace.contracts import Diagnostic, SelectedCompiledPlan
from millrace.contracts.diagnostics import DiagnosticContextValue


@dataclass(frozen=True, slots=True)
class CompileResult:
    """Workflow compile output; plan is None when diagnostics include errors."""

    plan: SelectedCompiledPlan | None
    diagnostics: tuple[Diagnostic, ...]


def compile_workflow(
    source: Mapping[str, object],
    *,
    selected_runner_policy: SelectedRunnerAdapterPolicy = (
        DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY
    ),
    declaration_path_prefix: str = "",
    diagnostic_context: Mapping[str, DiagnosticContextValue] | None = None,
) -> CompileResult:
    """Compile authored workflow data, returning diagnostics instead of raising."""

    diagnostics: list[Diagnostic] = []
    workflow = mapping(source.get("workflow"))

    validate_known_source_sections(source, diagnostics)
    validate_workflow_identity(workflow, diagnostics)

    indexes = collect_id_indexes(source, diagnostics)

    validate_partition_references(source, indexes, diagnostics)
    validate_stage_references(source, indexes, diagnostics)
    validate_external_enqueue_route_references(source, indexes, diagnostics)
    validate_generated_work_route_references(source, indexes, diagnostics)
    validate_fanout_references(source, indexes, diagnostics)
    validate_join_references(source, indexes, diagnostics)
    validate_outcome_references(source, indexes, diagnostics)
    validate_terminal_outcome_markers_are_unambiguous(source, diagnostics)
    validate_action_references(source, indexes, diagnostics)
    validate_effect_declarations(source, indexes, diagnostics)
    validate_runner_binding_references(source, indexes, diagnostics)
    validate_capability_values(source, diagnostics)
    validate_terminal_action_kinds(source, diagnostics)
    validate_terminal_actions_are_unambiguous(source, diagnostics)
    validate_route_action_contracts(source, diagnostics)
    validate_recovery_policy_references(source, indexes, diagnostics)
    validate_completion_remediation_references(source, indexes, diagnostics)
    validate_wait_state_references(source, indexes, diagnostics)
    validate_counter_references(source, indexes, diagnostics)
    validate_concurrency_policy_references(source, indexes, diagnostics)
    validate_intervention_option_references(source, indexes, diagnostics)
    validate_operator_wait_references(source, indexes, diagnostics)
    validate_lineage_policy_references(source, diagnostics)
    validate_action_outcomes_belong_to_action_stage(source, diagnostics)
    validate_declared_outcomes_have_actions(source, diagnostics)
    validate_declared_outcomes_belong_to_stage(source, diagnostics)
    validate_artifact_schema_declarations(source, diagnostics)
    validate_selected_authority_values(
        source,
        diagnostics,
        declaration_path_prefix=declaration_path_prefix,
    )
    validate_unselected_catalog(source, diagnostics)

    if _has_errors(diagnostics):
        return CompileResult(plan=None, diagnostics=tuple(diagnostics))

    source = normalize_selected_runner_bindings(
        source,
        workflow_id=str(workflow["id"]),
        workflow_version=str(workflow["version"]),
        diagnostics=diagnostics,
        policy=selected_runner_policy,
        declaration_path_prefix=declaration_path_prefix,
        diagnostic_context=diagnostic_context,
    )
    if _has_errors(diagnostics):
        return CompileResult(plan=None, diagnostics=tuple(diagnostics))

    source = normalize_operator_waits(source)

    return CompileResult(
        plan=build_selected_plan(source, workflow),
        diagnostics=tuple(diagnostics),
    )


def _has_errors(diagnostics: tuple[Diagnostic, ...] | list[Diagnostic]) -> bool:
    return any(diagnostic.severity == "error" for diagnostic in diagnostics)


__all__ = ("CompileResult", "compile_workflow")
