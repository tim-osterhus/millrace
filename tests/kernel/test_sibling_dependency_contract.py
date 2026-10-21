from __future__ import annotations

from dataclasses import replace

import pytest

from millrace.contracts.transition import ClaimWork, FanoutFromArtifact
from millrace.kernel import apply, decide
from millrace.operator.dispatch import (
    build_dispatch_envelope_for_run,
    list_ready_dispatch_candidates,
)
from millrace.testing import fake_runner_session_state
from support import generic_fanout, sibling_dependencies


def _claim(state, key):
    record = next(
        record for record in state.fanout_records.values() if record.item_key == key
    )
    command = ClaimWork(f"claim-check-{key}", activation_id=record.target_activation_id)
    return decide(state, command, generic_fanout.context(command.input_id))


def test_selected_dependency_authority_is_versioned_and_materializes_exact_edges():
    plan, fingerprint = generic_fanout.compile_fanout(sibling_dependencies.source())
    assert plan.fanout_declarations[0].schema_version == 3
    state = generic_fanout.parent_closed_state(
        plan,
        fingerprint,
        artifact_payload=sibling_dependencies.packet(
            {"later": ("first",), "first": ()}
        ),
    )
    command = FanoutFromArtifact(
        "fanout-dag",
        fanout_id="fanout.packet.children",
        source_artifact_id="transition-observe-parent-done:artifact",
    )
    decision = decide(state, command, generic_fanout.context(command.input_id))
    assert decision.accepted
    state = apply(state, decision)
    records = {record.item_key: record for record in state.fanout_records.values()}
    assert len(state.work_dependencies) == 3
    for key, ready in (("later", False), ("first", True)):
        claim = ClaimWork(
            f"claim-{key}", activation_id=records[key].target_activation_id
        )
        decision = decide(state, claim, generic_fanout.context(claim.input_id))
        assert decision.accepted is ready
        if not ready:
            assert decision.refusal.reason == "dependency_not_ready"


def test_exact_original_routed_final_success_releases_and_other_sibling_does_not():
    state = sibling_dependencies.fanned_state(
        {"later": ("first",), "other": (), "first": ()}
    )
    state = sibling_dependencies.finish_card(state, "other")
    assert not _claim(state, "later").accepted
    first = next(r for r in state.fanout_records.values() if r.item_key == "first")
    state = sibling_dependencies.complete_activation(
        state, first.target_activation_id, "first-next", "NEXT"
    )
    assert not _claim(state, "later").accepted
    assert first.target_work_item_id not in state.closed_work_items
    final = next(
        r.target_activation_id
        for r in state.activation_routes
        if r.record_id == "transition-observe-first-next:route"
    )
    state = sibling_dependencies.complete_activation(
        state, final, "first-final", "DONE"
    )
    assert _claim(state, "later").accepted
    assert first.target_work_item_id not in state.closed_work_items


@pytest.mark.parametrize("closure", ("BLOCKED", "GAP"))
def test_blocked_or_gap_close_does_not_release(closure):
    state = sibling_dependencies.fanned_state({"later": ("first",), "first": ()})
    if closure == "GAP":
        state = sibling_dependencies.finish_card(state, "first", marker="GAP")
    else:
        first = next(r for r in state.fanout_records.values() if r.item_key == "first")
        state = sibling_dependencies.complete_activation(
            state, first.target_activation_id, "first-blocked", closure
        )
    decision = _claim(state, "later")
    assert not decision.accepted
    assert decision.refusal.reason == "dependency_not_ready"


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_artifact",
        "wrong_action",
        "wrong_source",
        "missing_route",
        "wrong_route",
        "missing_dependency",
        "foreign_plan",
    ),
)
def test_corrupt_evidence_or_edges_never_release(mutation):
    state = sibling_dependencies.fanned_state(
        {"later": ("first",), "other": (), "first": ()}
    )
    state = sibling_dependencies.finish_card(state, "first")
    assert _claim(state, "later").accepted
    artifact = state.artifacts["transition-observe-first-final:artifact"]
    if mutation == "missing_artifact":
        state = replace(
            state,
            artifacts={
                k: v for k, v in state.artifacts.items() if k != artifact.artifact_id
            },
        )
    elif mutation in {"wrong_action", "wrong_source"}:
        artifact = replace(
            artifact,
            **(
                {
                    "source_action_id": state.artifacts[
                        "transition-observe-parent-done:artifact"
                    ].source_action_id
                }
                if mutation == "wrong_action"
                else {"source_run_id": "run-parent"}
            ),
        )
        state = replace(
            state, artifacts={**state.artifacts, artifact.artifact_id: artifact}
        )
    elif mutation in {"missing_route", "wrong_route"}:
        routes = tuple(
            r
            for r in state.activation_routes
            if r.record_id != "transition-observe-first-next:route"
        )
        if mutation == "wrong_route":
            other = next(
                r for r in state.fanout_records.values() if r.item_key == "other"
            )
            routes += tuple(
                replace(r, source_work_item_id=other.target_work_item_id)
                for r in state.activation_routes
                if r.record_id == "transition-observe-first-next:route"
            )
        state = replace(state, activation_routes=routes)
    else:
        edge = next(
            d
            for d in state.work_dependencies.values()
            if d.dependency_id.startswith("dependency-sibling:")
        )
        edges = {
            k: v for k, v in state.work_dependencies.items() if k != edge.dependency_id
        }
        if mutation == "foreign_plan":
            edges[edge.dependency_id] = replace(
                edge,
                selected_plan_ref=replace(edge.selected_plan_ref, plan_id="foreign"),
            )
        state = replace(state, work_dependencies=edges)
    assert not _claim(state, "later").accepted


@pytest.mark.parametrize(
    "edges",
    (
        {"one": ("unknown",)},
        {"one": ("one",)},
        {"one": ("two",), "two": ("one",)},
        {"one": ("two", "two"), "two": ()},
        {"one": "two", "two": ()},
        {"one": (2,), "two": ()},
        {"one": None},
    ),
)
def test_malformed_graph_refuses_manager_close_without_partial_work(edges):
    plan, fingerprint = generic_fanout.compile_fanout(sibling_dependencies.source())
    state = sibling_dependencies.parent_ready_state(plan, fingerprint)
    before = state
    state, command, decision = sibling_dependencies.observed_decision(
        state,
        "run-parent",
        "observe-bad-parent",
        "FANOUT_READY",
        sibling_dependencies.packet(edges),
    )
    assert not decision.accepted
    assert decision.refusal.reason in {
        "invalid_fanout_payload",
        "invalid_artifact_payload",
    }
    after = apply(state, decision)
    assert not after.closed_work_items
    assert after.work_items == before.work_items
    assert after.activations == before.activations
    assert not after.fanout_records and not after.work_dependencies
    assert not after.artifacts and not after.runner_observations


@pytest.mark.parametrize(
    "order",
    (
        ("m06", "m05", "m02", "m07", "m03", "m04", "m01"),
        ("m01", "m07", "m04", "m03", "m02", "m05", "m06"),
    ),
)
def test_seven_card_dag_is_order_independent(order):
    edges = {
        "m01": (),
        "m02": ("m01",),
        "m03": ("m01",),
        "m04": ("m02",),
        "m05": ("m03",),
        "m06": ("m01", "m02", "m03", "m04", "m05"),
        "m07": (),
    }
    state = sibling_dependencies.fanned_state({key: edges[key] for key in order})
    assert _claim(state, "m01").accepted and _claim(state, "m07").accepted
    assert not _claim(state, "m06").accepted
    for key in ("m01", "m02", "m03", "m04", "m05"):
        state = sibling_dependencies.finish_card(state, key)
        if key != "m05":
            assert not _claim(state, "m06").accepted
    assert _claim(state, "m06").accepted
    assert _claim(state, "m07").accepted


def test_accepted_observation_fanout_also_materializes_sibling_edges():
    source = sibling_dependencies.source()
    source["fanout_declarations"][0]["source_state_policy"] = (
        "accepted_terminal_observation"
    )
    source["fanout_declarations"][0]["dependency_policy"] = "none"
    state = sibling_dependencies.fanned_state(
        {"later": ("first",), "first": ()}, source
    )
    assert len(state.work_dependencies) == 1
    assert _claim(state, "first").accepted
    assert not _claim(state, "later").accepted
    state = sibling_dependencies.finish_card(state, "first")
    assert _claim(state, "later").accepted


def test_dispatch_projection_and_claim_follow_same_exact_success_gate():
    state = sibling_dependencies.fanned_state({"later": ("first",), "first": ()})
    later = next(r for r in state.fanout_records.values() if r.item_key == "later")
    projection = list_ready_dispatch_candidates(state)
    assert later.target_activation_id not in {
        c.activation_id for c in projection.candidates
    }
    assert any(
        d.activation_id == later.target_activation_id
        and d.reason_code == "dependency_not_ready"
        for d in projection.diagnostics
    )
    state = sibling_dependencies.finish_card(state, "first")
    assert later.target_activation_id in {
        c.activation_id for c in list_ready_dispatch_candidates(state).candidates
    }


def test_duplicate_card_ids_refuse_manager_without_parent_close_or_partial_work():
    plan, fingerprint = generic_fanout.compile_fanout(sibling_dependencies.source())
    state = sibling_dependencies.parent_ready_state(plan, fingerprint)
    payload = sibling_dependencies.packet({"one": ()})
    payload["items"] *= 2
    state, _, decision = sibling_dependencies.observed_decision(
        state, "run-parent", "observe-duplicate-cards", "FANOUT_READY", payload
    )
    assert not decision.accepted
    assert not apply(state, decision).closed_work_items
    assert len(state.work_items) == 1
    assert not state.fanout_records and not state.work_dependencies


def test_removing_both_fanout_record_and_edges_cannot_bypass_claim_gate():
    state = sibling_dependencies.fanned_state({"later": ("first",), "first": ()})
    later = next(r for r in state.fanout_records.values() if r.item_key == "later")
    state = replace(
        state,
        fanout_records={
            key: record
            for key, record in state.fanout_records.items()
            if key != later.record_id
        },
        work_dependencies={
            key: edge
            for key, edge in state.work_dependencies.items()
            if edge.fanout_record_id != later.record_id
        },
    )
    command = ClaimWork(
        "claim-missing-provenance", activation_id=later.target_activation_id
    )
    decision = decide(state, command, generic_fanout.context(command.input_id))
    assert not decision.accepted and decision.refusal.reason == "dependency_not_ready"


@pytest.mark.parametrize(
    "workflow_source",
    (
        sibling_dependencies.source_with_recovery,
        sibling_dependencies.source_with_same_stage_recovery,
    ),
)
def test_returned_activation_preserves_exact_card_origin_and_then_releases(
    workflow_source,
):
    state = sibling_dependencies.fanned_state(
        {"later": ("first",), "other": (), "first": ()},
        workflow_source(),
    )
    first = next(r for r in state.fanout_records.values() if r.item_key == "first")
    state = sibling_dependencies.complete_activation(
        state, first.target_activation_id, "first-recover", "RECOVER"
    )
    recovery = next(
        r.target_activation_id
        for r in state.activation_routes
        if r.record_id == "transition-observe-first-recover:route"
    )
    state = sibling_dependencies.complete_activation(
        state, recovery, "first-return", "RETURN"
    )
    returned = next(
        r.target_activation_id
        for r in state.activation_routes
        if r.record_id == "transition-observe-first-return:route"
    )
    assert not _claim(state, "later").accepted
    state = sibling_dependencies.complete_activation(
        state, returned, "returned-next", "NEXT"
    )
    final = next(
        r.target_activation_id
        for r in state.activation_routes
        if r.record_id == "transition-observe-returned-next:route"
    )
    state = sibling_dependencies.complete_activation(
        state, final, "returned-final", "DONE"
    )
    assert _claim(state, "later").accepted


def test_inherited_recovery_dispatch_keeps_exact_fanout_source():
    state = sibling_dependencies.fanned_state(
        {"later": ("first",), "first": ()}, sibling_dependencies.source_with_recovery()
    )
    first = next(r for r in state.fanout_records.values() if r.item_key == "first")
    state = sibling_dependencies.complete_activation(
        state, first.target_activation_id, "first-recover", "RECOVER"
    )
    recovery = next(
        r.target_activation_id
        for r in state.activation_routes
        if r.record_id == "transition-observe-first-recover:route"
    )
    claim = ClaimWork("claim-recovery-dispatch", activation_id=recovery)
    state = generic_fanout.apply_accepted_input(
        state, claim, generic_fanout.context(claim.input_id)
    )
    envelope = build_dispatch_envelope_for_run(
        state=fake_runner_session_state(state=state, run_id="run-recovery-dispatch"),
        run_id="run-recovery-dispatch",
    )
    assert envelope.governance_context["generated_work_source"]["item_key"] == "first"
    state, _, decision = sibling_dependencies.observed_decision(
        state,
        "run-recovery-dispatch",
        "observe-recovery-return",
        "RETURN",
        state.work_items[first.target_work_item_id].payload,
    )
    assert decision.accepted
    state = apply(state, decision)
    returned = next(
        r.target_activation_id
        for r in state.activation_routes
        if r.record_id == "transition-observe-recovery-return:route"
    )
    claim = ClaimWork("claim-returned-dispatch", activation_id=returned)
    state = generic_fanout.apply_accepted_input(
        state, claim, generic_fanout.context(claim.input_id)
    )
    envelope = build_dispatch_envelope_for_run(
        state=fake_runner_session_state(state=state, run_id="run-returned-dispatch"),
        run_id="run-returned-dispatch",
    )
    assert (
        envelope.governance_context["generated_work_source"]["fanout_record_id"]
        == first.record_id
    )


@pytest.mark.parametrize(
    "corruption", ("arbitrary_creator", "ordinary_target_shortcut", "wrong_generation")
)
def test_unclaimed_recovery_route_requires_exact_creator_and_selected_target(
    corruption,
):
    state = sibling_dependencies.fanned_state(
        {"later": ("first",), "other": (), "first": ()},
        sibling_dependencies.source_with_recovery(),
    )
    first = next(r for r in state.fanout_records.values() if r.item_key == "first")
    state = sibling_dependencies.complete_activation(
        state, first.target_activation_id, "first-recover", "RECOVER"
    )
    route = next(
        r
        for r in state.activation_routes
        if r.record_id == "transition-observe-first-recover:route"
    )
    recovery = state.activations[route.target_activation_id]
    assert recovery.claimed_by_run_id is None
    if corruption == "arbitrary_creator":
        forged = replace(
            recovery,
            activation_id="forged-unclaimed",
            created_by_input_id="forged-creator",
        )
        state = replace(
            state,
            activations={**state.activations, forged.activation_id: forged},
            activation_routes=(
                *state.activation_routes,
                replace(
                    route,
                    record_id="forged-route",
                    target_activation_id=forged.activation_id,
                    created_by_input_id="forged-creator",
                ),
            ),
        )
    elif corruption == "wrong_generation":
        forged = replace(recovery, generation=999)
        state = replace(
            state, activations={**state.activations, forged.activation_id: forged}
        )
    else:
        original = state.activations[first.target_activation_id]
        forged = replace(
            recovery,
            stage_kind_id=original.stage_kind_id,
            graph_node_id=original.graph_node_id,
            runner_binding_id=original.runner_binding_id,
        )
        state = replace(
            state, activations={**state.activations, forged.activation_id: forged}
        )
    decision = _claim(state, "other")
    assert not decision.accepted and decision.refusal.reason == "dependency_not_ready"


def test_independent_siblings_claim_within_declared_concurrency():
    source = sibling_dependencies.source()
    source["concurrency_policies"] = [
        {
            "id": "cards.concurrency",
            "partition_id": "primary",
            "max_active_runs": 2,
            "coexist_partition_ids": (),
        }
    ]
    state = sibling_dependencies.fanned_state(
        {"later": ("first",), "other": (), "first": ()}, source
    )
    first = _claim(state, "first")
    assert first.accepted
    state = apply(state, first)
    assert _claim(state, "other").accepted
    assert not _claim(state, "later").accepted


@pytest.mark.parametrize(
    "workflow_source",
    (
        sibling_dependencies.source_with_recovery,
        sibling_dependencies.source_with_same_stage_recovery,
    ),
)
def test_unclaimed_formal_route_never_skips_creator_audit_on_ordinary_target(
    workflow_source,
):
    state = sibling_dependencies.fanned_state(
        {"later": ("first",), "other": (), "first": ()}, workflow_source()
    )
    first = next(r for r in state.fanout_records.values() if r.item_key == "first")
    state = sibling_dependencies.complete_activation(
        state, first.target_activation_id, "first-recover", "RECOVER"
    )
    route = next(
        r
        for r in state.activation_routes
        if r.record_id == "transition-observe-first-recover:route"
    )
    assert state.activations[route.target_activation_id].claimed_by_run_id is None
    plan = next(iter(state.admitted_plans.values())).selected_plan
    wrong_action = next(
        a.id for a in plan.terminal_actions if str(a.id) == "child.next"
    )
    state = replace(
        state,
        governance_events=tuple(
            replace(e, action_id=wrong_action)
            if e.input_id == route.created_by_input_id
            else e
            for e in state.governance_events
        ),
        traces=tuple(
            replace(t, action_id=wrong_action)
            if t.input_id == route.created_by_input_id
            else t
            for t in state.traces
        ),
    )
    assert not _claim(state, "other").accepted


@pytest.mark.parametrize(
    "workflow_source",
    (
        sibling_dependencies.source_with_recovery,
        sibling_dependencies.source_with_same_stage_recovery,
    ),
)
def test_missing_formal_attempt_cannot_make_unclaimed_recovery_ordinary(
    workflow_source,
):
    state = sibling_dependencies.fanned_state(
        {"later": ("first",), "other": (), "first": ()}, workflow_source()
    )
    first = next(r for r in state.fanout_records.values() if r.item_key == "first")
    state = sibling_dependencies.complete_activation(
        state, first.target_activation_id, "first-recover", "RECOVER"
    )
    assert state.recovery_attempts
    state = replace(state, recovery_attempts={})
    assert not _claim(state, "other").accepted


def test_historical_return_success_remains_exact_after_new_sibling_attempt():
    state = sibling_dependencies.fanned_state(
        {"later": ("first",), "other": (), "first": ()},
        sibling_dependencies.source_with_same_stage_recovery(),
    )
    first = next(r for r in state.fanout_records.values() if r.item_key == "first")
    state = sibling_dependencies.complete_activation(
        state, first.target_activation_id, "first-recover", "RECOVER"
    )
    recovery = next(
        r.target_activation_id
        for r in state.activation_routes
        if r.record_id == "transition-observe-first-recover:route"
    )
    state = sibling_dependencies.complete_activation(
        state, recovery, "first-return", "RETURN"
    )
    returned = next(
        r.target_activation_id
        for r in state.activation_routes
        if r.record_id == "transition-observe-first-return:route"
    )
    state = sibling_dependencies.complete_activation(
        state, returned, "returned-next", "NEXT"
    )
    final = next(
        r.target_activation_id
        for r in state.activation_routes
        if r.record_id == "transition-observe-returned-next:route"
    )
    state = sibling_dependencies.complete_activation(
        state, final, "returned-final", "DONE"
    )
    assert _claim(state, "later").accepted
    other = next(r for r in state.fanout_records.values() if r.item_key == "other")
    state = sibling_dependencies.complete_activation(
        state, other.target_activation_id, "other-recover", "RECOVER"
    )
    assert len(state.recovery_attempts) == 2
    assert _claim(state, "later").accepted
