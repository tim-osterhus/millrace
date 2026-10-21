from __future__ import annotations

from dataclasses import replace

import pytest
from tests.compiler.test_terminal_ingress import _ordinary_ingress_source

from millrace.compiler import compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.state import AdmittedPlan, PlanRef
from millrace.kernel import empty_runtime_state
from millrace.substrate._sqlite_relations import _validate_selected_lifecycle_aftermath
from millrace.substrate.errors import StorageIntegrityError
from support import generic_admission


def test_loaded_lifecycle_validation_refuses_coherently_rehashed_ingress_contract():
    result = compile_workflow(
        _ordinary_ingress_source(ordinary_exit=True),
        selected_runner_policy=generic_admission._CODEX_POLICY,
    )
    assert result.plan is not None
    forged = replace(
        result.plan,
        terminal_actions=tuple(
            action
            for action in result.plan.terminal_actions
            if str(action.id) != "admission.recovery.close"
        ),
    )
    fingerprint = authority_fingerprint(forged)
    plan_ref = PlanRef(
        plan_id=f"{forged.workflow.workflow_id}:{forged.workflow.workflow_version}",
        authority_fingerprint=fingerprint,
        plan_format_version=forged.schema_version,
    )
    state = replace(
        empty_runtime_state(),
        admitted_plans={
            fingerprint: AdmittedPlan(
                plan_ref=plan_ref,
                selected_plan=forged,
            )
        },
    )
    with pytest.raises(
        StorageIntegrityError, match="ordinary_ingress_requires_terminal_option"
    ):
        _validate_selected_lifecycle_aftermath(state)


@pytest.mark.parametrize(
    "corruption", ("missing_attempt", "wrong_generation", "missing_route")
)
def test_nonfanout_formal_ingress_overlap_requires_complete_creator_on_claim_and_reload(
    tmp_path, monkeypatch, corruption
):
    import sqlite3

    from tests.substrate.test_persistence_integrity_refusals import (
        _apply_generic_observation,
        _generic_parent_claimed_state,
    )

    from millrace.contracts.runtime_failures import (
        formal_recovery_activation_authority_refusal,
    )
    from millrace.contracts.transition import ClaimWork
    from millrace.kernel import decide
    from millrace.testing import deterministic_context
    from substrate._runtime_store_support import (
        load_runtime_state,
        persist_runtime_state,
        runtime_store_paths,
    )

    source = _ordinary_ingress_source(ordinary_exit=True)
    recovery_stage = next(
        stage
        for stage in source["stage_kinds"]
        if stage["id"] == generic_admission.RECOVERY_STAGE_ID
    )
    recovery_stage["input_queue_family_ids"] = ("parent", "child")
    ordinary = next(
        action
        for action in source["terminal_actions"]
        if action["id"] == generic_admission.DYNAMIC_ROUTE_ACTION_ID
    )
    ordinary["emitted_queue_family_id"] = "parent"
    result = compile_workflow(
        source, selected_runner_policy=generic_admission._CODEX_POLICY
    )
    assert result.plan is not None, result.diagnostics
    plan = result.plan
    fingerprint = authority_fingerprint(plan)
    monkeypatch.setattr(generic_admission, "compile_plan", lambda: (plan, fingerprint))
    state, _, _ = _generic_parent_claimed_state()
    state = _apply_generic_observation(
        state,
        plan,
        fingerprint,
        run_id="run-generic-parent",
        action_id=generic_admission.RECOVERY_SOURCE_ACTION_ID,
        input_id="observe-generic-recovery",
        context=deterministic_context(
            transition_id="transition-observe-generic-recovery",
            activation_id="activation-generic-recovery",
        ),
    )
    target = "activation-generic-recovery"
    assert not state.fanout_records and not state.work_dependencies
    assert formal_recovery_activation_authority_refusal(state, plan, target) is None
    claim = ClaimWork("claim-nonfanout-overlap", activation_id=target)
    assert decide(state, claim, deterministic_context()).accepted
    if corruption == "missing_attempt":
        corrupt = replace(state, recovery_attempts={})
    elif corruption == "wrong_generation":
        corrupt = replace(
            state,
            activations={
                **state.activations,
                target: replace(state.activations[target], generation=999),
            },
        )
    else:
        corrupt = replace(
            state,
            activation_routes=tuple(
                route
                for route in state.activation_routes
                if route.target_activation_id != target
            ),
        )
    assert (
        formal_recovery_activation_authority_refusal(corrupt, plan, target) is not None
    )
    assert not decide(corrupt, claim, deterministic_context()).accepted
    db, cas = runtime_store_paths(tmp_path)
    persist_runtime_state(db, cas, state)
    with sqlite3.connect(db) as connection:
        if corruption == "missing_attempt":
            connection.execute("DELETE FROM recovery_attempts")
        elif corruption == "wrong_generation":
            connection.execute(
                "UPDATE activations SET generation=999 WHERE activation_id=?", (target,)
            )
        else:
            connection.execute(
                "DELETE FROM activation_routes WHERE target_activation_id=?", (target,)
            )
    with pytest.raises(StorageIntegrityError):
        load_runtime_state(db, cas)
