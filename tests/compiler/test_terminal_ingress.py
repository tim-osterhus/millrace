from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from millrace.compiler import compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.compiler.export import (
    CompiledPlanExportError,
    compiled_plan_export_bytes,
    verify_compiled_plan_export_bytes,
)
from millrace.contracts.terminal_ingress import terminal_ingress_authority_refusal
from millrace.contracts.transition import AdmitPlan, InitializeWorkspace
from millrace.kernel import apply, decide, empty_runtime_state
from support import generic_admission, generic_fanout


def _ordinary_ingress_source(*, ordinary_exit: bool = False) -> dict[str, object]:
    source = generic_admission.source()
    source["intervention_options"] = ()
    stages = cast(list[dict[str, object]], source["stage_kinds"])
    recovery = next(
        stage for stage in stages if stage["id"] == generic_admission.RECOVERY_STAGE_ID
    )
    recovery["input_queue_family_ids"] = ("child",)
    actions = cast(list[dict[str, object]], source["terminal_actions"])
    route = next(
        action
        for action in actions
        if action["id"] == generic_admission.DYNAMIC_ROUTE_ACTION_ID
    )
    route.pop("dynamic_target_selector")
    route["target_stage_kind_id"] = generic_admission.RECOVERY_STAGE_ID
    route["target_graph_node_id"] = generic_admission.RECOVERY_NODE_ID
    if ordinary_exit:
        outcome_id = "admission.recovery.closed"
        recovery["declared_outcome_ids"] = (
            *cast(tuple[str, ...], recovery["declared_outcome_ids"]),
            outcome_id,
        )
        cast(list[dict[str, object]], source["terminal_outcomes"]).append(
            {
                "id": outcome_id,
                "stage_kind_id": generic_admission.RECOVERY_STAGE_ID,
                "marker": "CLOSED",
            }
        )
        actions.append(
            {
                "id": "admission.recovery.close",
                "stage_kind_id": generic_admission.RECOVERY_STAGE_ID,
                "outcome_id": outcome_id,
                "kind": "close",
            }
        )
    return source


def test_compiler_refuses_ordinary_ingress_with_only_attempt_owned_exits() -> None:
    result = compile_workflow(
        _ordinary_ingress_source(),
        selected_runner_policy=generic_admission._CODEX_POLICY,
    )
    assert result.plan is None
    assert "ordinary_ingress_requires_terminal_option" in {
        diagnostic.code for diagnostic in result.diagnostics
    }


def test_ordinary_and_formal_ingress_can_share_a_stage_with_ordinary_exit() -> None:
    result = compile_workflow(
        _ordinary_ingress_source(ordinary_exit=True),
        selected_runner_policy=generic_admission._CODEX_POLICY,
    )
    assert result.plan is not None, result.diagnostics


def test_formal_only_recovery_stage_remains_supported() -> None:
    result = compile_workflow(
        generic_admission.source(),
        selected_runner_policy=generic_admission._CODEX_POLICY,
    )
    assert result.plan is not None, result.diagnostics


def test_admission_refuses_forged_ordinary_ingress_like_compiler() -> None:
    result = compile_workflow(
        _ordinary_ingress_source(ordinary_exit=True),
        selected_runner_policy=generic_admission._CODEX_POLICY,
    )
    assert result.plan is not None
    plan = result.plan
    forged = replace(
        plan,
        terminal_actions=tuple(
            action
            for action in plan.terminal_actions
            if str(action.id) != "admission.recovery.close"
        ),
    )
    state = empty_runtime_state()
    initialization = decide(
        state,
        InitializeWorkspace("init-ingress"),
        generic_fanout.context("init-ingress"),
    )
    state = apply(state, initialization)
    transition = decide(
        state,
        AdmitPlan(
            input_id="ordinary-ingress-forgery",
            selected_plan=forged,
            authority_fingerprint=authority_fingerprint(forged),
        ),
        generic_fanout.context("ordinary-ingress-forgery"),
    )
    assert transition.accepted is False
    assert transition.refusal is not None
    assert transition.refusal.detail == terminal_ingress_authority_refusal(forged)
    with pytest.raises(
        CompiledPlanExportError, match="ordinary_ingress_requires_terminal_option"
    ):
        verify_compiled_plan_export_bytes(compiled_plan_export_bytes(forged))


@pytest.mark.parametrize(
    "section,target_field",
    [
        ("external_enqueue_routes", "stage_kind_id"),
        ("generated_work_routes", "stage_kind_id"),
        ("fanout_declarations", "target_stage_kind_id"),
        ("operator_waits", "target_stage_kind_id"),
        ("completion_behaviors", "target_stage_kind_id"),
        ("remediation_policies", "target_stage_kind_id"),
    ],
)
def test_declared_ordinary_target_sections_share_the_ingress_rule(
    section, target_field
):
    source = generic_admission.source()
    source[section] = [
        {"id": "ordinary.target", target_field: generic_admission.RECOVERY_STAGE_ID}
    ]
    assert terminal_ingress_authority_refusal(source) == (
        "ordinary_ingress_requires_terminal_option:ordinary.target:"
        + generic_admission.RECOVERY_STAGE_ID
    )


def test_unselected_ordinary_outcome_does_not_satisfy_bound_runner_exit():
    source = _ordinary_ingress_source(ordinary_exit=True)
    binding = cast(list[dict[str, object]], source["runner_bindings"])[0]
    binding["component_pin"] = {"id": "selected.runner"}
    binding["terminal_result_mappings"] = [
        {
            "stage_kind_id": generic_admission.RECOVERY_STAGE_ID,
            "outcome_id": "admission.recovered",
        }
    ]
    assert terminal_ingress_authority_refusal(source) is not None
    cast(list[dict[str, object]], binding["terminal_result_mappings"]).append(
        {
            "stage_kind_id": generic_admission.RECOVERY_STAGE_ID,
            "outcome_id": "admission.recovery.closed",
        }
    )
    assert terminal_ingress_authority_refusal(source) is None


@pytest.mark.parametrize("marker", ["", "   "])
def test_blank_ordinary_marker_does_not_satisfy_ingress(marker):
    source = _ordinary_ingress_source(ordinary_exit=True)
    outcome = next(
        outcome
        for outcome in source["terminal_outcomes"]
        if outcome["id"] == "admission.recovery.closed"
    )
    outcome["marker"] = marker
    result = compile_workflow(
        source, selected_runner_policy=generic_admission._CODEX_POLICY
    )
    assert result.plan is None
    assert any(
        d.code == "ordinary_ingress_requires_terminal_option"
        for d in result.diagnostics
    )
