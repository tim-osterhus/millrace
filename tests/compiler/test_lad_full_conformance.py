from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import cast

import pytest

from millrace.compiler import (
    CompiledPlanExportError,
    SelectedRunnerAdapterPolicy,
    authority_fingerprint,
    canonical_authority_bytes,
    compiled_plan_export_bytes,
    compiled_plan_export_record,
    verify_compiled_plan_export_bytes,
    verify_compiled_plan_export_record,
)
from millrace.compiler import (
    compile_workflow as _raw_compile_workflow,
)
from millrace.workflows import lad_learning
from support import lad_execution

Source = dict[str, object]
Record = dict[str, object]
_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def _compile_codex(source: Source):
    return _raw_compile_workflow(source, selected_runner_policy=_CODEX_POLICY)


def _compile_full(source: Source | None = None):
    result = _compile_codex(source or lad_learning.workflow_source())
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def _compile_errors(source: Source):
    return [
        diagnostic
        for diagnostic in _compile_codex(source).diagnostics
        if diagnostic.severity == "error"
    ]


def _records(source: Source, key: str) -> Iterable[Record]:
    return cast(Iterable[Record], source[key])


def _record(source: Source, key: str, record_id: str) -> Record:
    return next(record for record in _records(source, key) if record["id"] == record_id)


def test_full_lad_selected_export_is_deterministic() -> None:
    source = lad_learning.workflow_source()

    plan_a, fingerprint_a = _compile_full(deepcopy(source))
    plan_b, fingerprint_b = _compile_full(deepcopy(source))
    export_a = compiled_plan_export_bytes(plan_a)
    export_b = compiled_plan_export_bytes(plan_b)
    verified = verify_compiled_plan_export_bytes(export_a)

    assert plan_a == plan_b
    assert fingerprint_a == fingerprint_b
    assert canonical_authority_bytes(plan_a) == canonical_authority_bytes(plan_b)
    assert export_a == export_b
    assert verified.authority_fingerprint == fingerprint_a
    assert verified.workflow_id == "lad.full"
    assert {str(graph.id) for graph in plan_a.graphs} == {
        "execution.lad.graph",
        "learning.standard.graph",
        "planning.lad.graph",
    }
    assert {str(partition.id) for partition in plan_a.partitions} == {
        "execution",
        "learning",
        "planning",
    }
    assert {
        "task",
        "spec",
        "probe",
        "incident",
        "learning_request",
    }.issubset({str(queue.id) for queue in plan_a.queue_families})


def test_unselected_legacy_catalog_does_not_affect_full_lad_fingerprint() -> None:
    base_plan, base_fingerprint = _compile_full()
    source = lad_learning.workflow_source()
    source["unselected_catalog"] = (
        {
            "id": "legacy-blueprint-learning-evidence",
            "catalog_payload": {
                "mode_id": "blueprint_learning_lad_codex",
                "legacy_alias": "learning_lad_codex",
                "old_workspace_path": "millrace-agents/learning/requests",
            },
        },
    )

    changed_plan, changed_fingerprint = _compile_full(source)

    assert canonical_authority_bytes(changed_plan) == canonical_authority_bytes(
        base_plan
    )
    assert changed_fingerprint == base_fingerprint
    assert b"blueprint_learning_lad_codex" not in canonical_authority_bytes(
        changed_plan
    )


@pytest.mark.parametrize(
    "compatibility_profile",
    (
        "lad_codex",
        "learning_lad_codex",
        "blueprint_lad_codex",
        "blueprint_learning_lad_codex",
    ),
)
def test_full_lad_diagnostics_reject_legacy_alias_authority(
    compatibility_profile: str,
) -> None:
    source = lad_learning.workflow_source()
    workflow = cast(Record, source["workflow"])
    workflow["compatibility_profile"] = compatibility_profile

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.code == "unsupported_compatibility_profile"
    )
    assert error.declaration_path == "workflow.compatibility_profile"
    assert error.context["compatibility_profile"] == compatibility_profile


def test_full_lad_diagnostics_reject_old_or_route_bearing_needs_planning_action() -> (
    None
):
    old_kind_source = lad_learning.workflow_source()
    old_kind_action = _record(
        old_kind_source,
        "terminal_actions",
        "execution.close_consultant_needs_plan",
    )
    old_kind_action["kind"] = "escalate_to_planning"

    route_source = lad_learning.workflow_source()
    route_action = _record(
        route_source,
        "terminal_actions",
        "execution.close_consultant_needs_plan",
    )
    route_action["target_graph_node_id"] = "planning.lad.planner.start"

    assert any(
        diagnostic.code == "unsupported_terminal_action_kind"
        for diagnostic in _compile_errors(old_kind_source)
    )
    assert any(
        diagnostic.code == "terminal_close_with_escalation_route_authority"
        for diagnostic in _compile_errors(route_source)
    )


def test_full_lad_diagnostics_reject_invalid_learning_trigger_concurrency_effects() -> (
    None
):
    trigger_source = lad_learning.workflow_source()
    _record(
        trigger_source,
        "generated_work_routes",
        "learning.trigger.librarian",
    )["payload_schema_id"] = "learning.artifacts.stage_result"

    concurrency_source = lad_learning.workflow_source()
    _record(
        concurrency_source,
        "concurrency_policies",
        "learning.standard",
    )["coexist_partition_ids"] = ("learning",)

    effect_source = lad_learning.workflow_source()
    _record(
        effect_source,
        "effect_declarations",
        "learning.effect.curator.workspace_skill_update",
    )["real_side_effects_allowed"] = True

    assert any(
        diagnostic.code == "invalid_fanout_declaration"
        and diagnostic.context.get("reason") == "target_route_contract_mismatch"
        for diagnostic in _compile_errors(trigger_source)
    )
    assert any(
        diagnostic.code == "invalid_concurrency_policy"
        and diagnostic.context.get("reason") == "self_coexist"
        for diagnostic in _compile_errors(concurrency_source)
    )
    assert any(
        diagnostic.code == "invalid_effect_declaration"
        and diagnostic.context.get("field_name") == "real_side_effects_allowed"
        for diagnostic in _compile_errors(effect_source)
    )


def test_full_lad_export_verification_refuses_selected_authority_drift() -> None:
    plan, _fingerprint = _compile_full()
    record = dict(compiled_plan_export_record(plan))
    selected = dict(cast(dict[str, object], record["selected_authority"]))
    workflow = dict(cast(dict[str, object], selected["workflow"]))
    workflow["workflow_name"] = "Full LAD drifted"
    selected["workflow"] = workflow
    record["selected_authority"] = selected

    with pytest.raises(CompiledPlanExportError, match="authority fingerprint mismatch"):
        verify_compiled_plan_export_record(record)


def test_execution_base_and_integrator_variants_remain_distinct_authority() -> None:
    base_plan, base_fingerprint = lad_execution.compile_lad(integrator=False)
    integrator_plan, integrator_fingerprint = lad_execution.compile_lad(
        integrator=True,
    )

    assert base_fingerprint != integrator_fingerprint
    assert canonical_authority_bytes(base_plan) != canonical_authority_bytes(
        integrator_plan
    )
    assert "lad_integrator" not in {str(stage.id) for stage in base_plan.stage_kinds}
    assert "lad_integrator" in {str(stage.id) for stage in integrator_plan.stage_kinds}
