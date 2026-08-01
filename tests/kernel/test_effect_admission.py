"""Neutral selected-effect admission invariants."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import pytest

from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.ids import ActionId, EffectDeclarationId
from millrace.contracts.transition import AdmitPlan
from millrace.kernel import decide, empty_runtime_state
from support import generic_effect


def test_admission_refuses_effect_declaration_drift() -> None:
    plan, _fingerprint = generic_effect.compile_effect_plan()
    drifted_effects = tuple(
        replace(
            effect,
            terminal_action_id=ActionId("kernel_ping.close_worker_success"),
        )
        if str(effect.effect_declaration_id) == generic_effect.EFFECT_DECLARATION_ID
        else effect
        for effect in plan.effect_declarations
    )
    drifted_plan = replace(plan, effect_declarations=drifted_effects)
    drifted_fingerprint = authority_fingerprint(drifted_plan)

    decision = decide(
        empty_runtime_state(),
        AdmitPlan(
            "admit-drifted-learning-effect-plan",
            selected_plan=drifted_plan,
            authority_fingerprint=drifted_fingerprint,
        ),
        generic_effect.context("admit-drifted-effect-plan"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
    assert decision.refusal.detail == (
        f"effect_declaration_terminal_action:{generic_effect.EFFECT_DECLARATION_ID}"
    )


def test_admission_refuses_duplicate_effect_terminal_action() -> None:
    plan, _fingerprint = generic_effect.compile_effect_plan()
    duplicate = replace(
        plan.effect_declarations[0],
        effect_declaration_id=EffectDeclarationId("kernel_ping.effect.duplicate"),
    )
    drifted_plan = replace(
        plan,
        effect_declarations=(*plan.effect_declarations, duplicate),
    )
    drifted_fingerprint = authority_fingerprint(drifted_plan)

    decision = decide(
        empty_runtime_state(),
        AdmitPlan(
            "admit-duplicate-learning-effect-plan",
            selected_plan=drifted_plan,
            authority_fingerprint=drifted_fingerprint,
        ),
        generic_effect.context("admit-duplicate-effect-plan"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
    assert decision.refusal.detail == (
        "effect_declaration_terminal_action_duplicate:"
        f"{generic_effect.EFFECT_ACTION_ID}"
    )


def test_admission_refuses_blank_effect_target_refs() -> None:
    plan, _fingerprint = generic_effect.compile_effect_plan()
    drifted_effects = tuple(
        replace(effect, target_ref_schema="")
        if str(effect.effect_declaration_id) == generic_effect.EFFECT_DECLARATION_ID
        else effect
        for effect in plan.effect_declarations
    )
    drifted_plan = replace(plan, effect_declarations=drifted_effects)
    drifted_fingerprint = authority_fingerprint(drifted_plan)

    decision = decide(
        empty_runtime_state(),
        AdmitPlan(
            "admit-blank-learning-effect-target-plan",
            selected_plan=drifted_plan,
            authority_fingerprint=drifted_fingerprint,
        ),
        generic_effect.context("admit-blank-effect-target-plan"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
    assert decision.refusal.detail == (
        f"effect_declaration_target_ref_schema:{generic_effect.EFFECT_DECLARATION_ID}"
    )


@pytest.mark.parametrize(
    ("field_updates", "detail_prefix"),
    (
        (
            {"provider_ref": "provider.real.network"},
            "effect_declaration_provider",
        ),
        (
            {
                "allowed_reconciliation_statuses": (
                    "applied",
                    "refused",
                )
            },
            "effect_declaration_reconciliation_statuses",
        ),
        (
            {"real_side_effects_allowed": True},
            "effect_declaration_real_side_effects",
        ),
    ),
)
def test_admission_refuses_effect_policy_drift(
    field_updates: Mapping[str, object],
    detail_prefix: str,
) -> None:
    plan, _fingerprint = generic_effect.compile_effect_plan()
    drifted_effects = tuple(
        replace(effect, **field_updates)
        if str(effect.effect_declaration_id) == generic_effect.EFFECT_DECLARATION_ID
        else effect
        for effect in plan.effect_declarations
    )
    drifted_plan = replace(plan, effect_declarations=drifted_effects)
    drifted_fingerprint = authority_fingerprint(drifted_plan)

    decision = decide(
        empty_runtime_state(),
        AdmitPlan(
            "admit-learning-effect-policy-drift-plan",
            selected_plan=drifted_plan,
            authority_fingerprint=drifted_fingerprint,
        ),
        generic_effect.context("admit-effect-policy-drift-plan"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
    assert decision.refusal.detail == (
        f"{detail_prefix}:{generic_effect.EFFECT_DECLARATION_ID}"
    )
