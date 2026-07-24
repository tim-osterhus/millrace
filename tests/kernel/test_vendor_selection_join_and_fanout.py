from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from millrace.contracts import QueueFamilyId
from millrace.contracts.state import ActivationRouteRecord, PlanRef, RuntimeState
from millrace.contracts.transition import (
    FanoutFromArtifact,
    JoinFromArtifact,
    TransitionContext,
)
from millrace.kernel import apply, decide
from millrace.kernel.lifecycle import project_next_lifecycle_transition
from support import vendor_selection


def _assert_no_join_progress(before: RuntimeState, after: RuntimeState) -> None:
    assert after.work_items == before.work_items
    assert after.activations == before.activations
    assert after.activation_routes == before.activation_routes
    assert after.closed_work_items == before.closed_work_items


def _apply_selected_packager_fanouts(state: RuntimeState) -> RuntimeState:
    for fanout_id, suffix in (
        ("vendor_selection.candidate_packager.rubric_fanout", "rubric"),
        ("vendor_selection.candidate_packager.conflict_fanout", "conflict"),
    ):
        state = vendor_selection.apply_accepted_input(
            state,
            FanoutFromArtifact(
                f"fanout-{suffix}-a",
                fanout_id=fanout_id,
                source_artifact_id=vendor_selection.artifact_id_for(
                    "observe-packager-a"
                ),
            ),
            vendor_selection.context(f"fanout-{suffix}-a"),
        )
    return state


def _award_join_input(input_id: str) -> JoinFromArtifact:
    return JoinFromArtifact(
        input_id,
        join_id=vendor_selection.JOIN_ID,
        source_artifact_id=vendor_selection.artifact_id_for("observe-conflict-a"),
    )


def _award_join_context(
    input_id: str,
    *,
    work_item_id: str = "work-award-a",
    activation_id: str = "activation-award-a",
) -> TransitionContext:
    return vendor_selection.context(
        input_id,
        work_item_id=work_item_id,
        activation_id=activation_id,
    )


def _apply_award_join(state: RuntimeState) -> RuntimeState:
    decision = decide(
        state,
        _award_join_input("join-award-a"),
        _award_join_context("join-award-a"),
    )
    assert decision.accepted is True
    return apply(state, decision)


def _join_route(state: RuntimeState) -> ActivationRouteRecord:
    return next(
        route
        for route in state.activation_routes
        if route.created_by_input_id == "join-award-a"
    )


def _with_join_route(state: RuntimeState, **changes: Any) -> RuntimeState:
    route = _join_route(state)
    return replace(
        state,
        activation_routes=tuple(
            replace(candidate, **changes)
            if candidate.record_id == route.record_id
            else candidate
            for candidate in state.activation_routes
        ),
    )


def _duplicate_join_route(state: RuntimeState) -> RuntimeState:
    route = _join_route(state)
    duplicate = replace(route, record_id=f"{route.record_id}:duplicate")
    return replace(state, activation_routes=(*state.activation_routes, duplicate))


def _with_award_work(state: RuntimeState, **changes: Any) -> RuntimeState:
    work = state.work_items["work-award-a"]
    return replace(
        state,
        work_items={
            **state.work_items,
            work.ref.work_item_id: replace(work, **changes),
        },
    )


def _with_award_activation(state: RuntimeState, **changes: Any) -> RuntimeState:
    activation = state.activations["activation-award-a"]
    return replace(
        state,
        activations={
            **state.activations,
            activation.activation_id: replace(activation, **changes),
        },
    )


def _other_plan_ref(state: RuntimeState) -> PlanRef:
    plan_ref = state.work_items["work-award-a"].ref.plan_ref
    return replace(
        plan_ref,
        authority_fingerprint=f"{plan_ref.authority_fingerprint}:other",
    )


def test_candidate_packager_creates_two_selected_evaluator_branches() -> None:
    state, _plan, fingerprint = vendor_selection.packager_closed_state("a")

    after = _apply_selected_packager_fanouts(state)

    generated = [
        item
        for item in after.work_items.values()
        if item.created_by_input_id in {"fanout-rubric-a", "fanout-conflict-a"}
    ]
    assert len(generated) == 2
    assert {item.lineage_id for item in generated} == {"work-request-a"}
    assert {item.queue_family_id for item in generated} == {
        QueueFamilyId("candidate_bundle")
    }
    activations = {
        after.activations[
            next(
                record.target_activation_id
                for record in after.fanout_records.values()
                if record.target_work_item_id == item.ref.work_item_id
            )
        ]
        for item in generated
    }
    assert {str(activation.stage_kind_id) for activation in activations} == {
        "rubric_evaluator",
        "conflict_checker",
    }
    assert {
        record.selected_plan_ref.authority_fingerprint
        for record in after.fanout_records.values()
    } == {fingerprint}
    assert {
        dependency.dependency_work_item_id
        for dependency in after.work_dependencies.values()
    } == {"work-packager-a"}


def test_vendor_selection_records_ready_evaluator_state_with_deferred_claiming() -> (
    None
):
    state, plan, _fingerprint = vendor_selection.packager_closed_state("a")

    after = _apply_selected_packager_fanouts(state)

    evaluation_policy = next(
        policy
        for policy in plan.concurrency_policies
        if str(policy.partition_id) == "evaluation"
    )
    ready_evaluator_activations = [
        activation
        for activation in after.activations.values()
        if str(activation.stage_kind_id) in {"rubric_evaluator", "conflict_checker"}
        and activation.claimed_by_run_id is None
    ]
    assert evaluation_policy.max_active_runs == 2
    assert len(ready_evaluator_activations) == 2


def test_vendor_selection_award_decider_waits_with_one_report() -> None:
    from millrace.contracts.transition import JoinFromArtifact

    state, _plan, _fingerprint = vendor_selection.one_report_state()

    decision = decide(
        state,
        JoinFromArtifact(
            "join-one-report-a",
            join_id=vendor_selection.JOIN_ID,
            source_artifact_id=vendor_selection.artifact_id_for("observe-rubric-a"),
        ),
        vendor_selection.context(
            "join-one-report-a",
            work_item_id="work-award-a",
            activation_id="activation-award-a",
        ),
    )
    after = apply(state, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "join_evidence_missing"
    _assert_no_join_progress(state, after)


def test_award_decider_refuses_mismatch_or_corrupt_duplicate() -> None:
    from millrace.contracts.transition import JoinFromArtifact

    state, plan, fingerprint = vendor_selection.packager_closed_state("a")
    state = _apply_selected_packager_fanouts(state)
    rubric_activation = vendor_selection.report_branch_activation_id(
        state,
        "rubric_evaluator",
    )
    state = vendor_selection.claim_activation(
        state,
        activation_id=rubric_activation,
        suffix="rubric-a",
    )
    state = vendor_selection.apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-rubric-a",
        action_id="vendor_selection.rubric_evaluator.rubric_complete",
        input_id="observe-rubric-a",
        artifact_payload=vendor_selection.rubric_report_payload("bundle-a"),
    )
    conflict_activation = vendor_selection.report_branch_activation_id(
        state,
        "conflict_checker",
    )
    state = vendor_selection.claim_activation(
        state,
        activation_id=conflict_activation,
        suffix="conflict-a",
    )
    mismatched = vendor_selection.apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-conflict-a",
        action_id="vendor_selection.conflict_checker.conflict_complete",
        input_id="observe-conflict-a",
        artifact_payload=vendor_selection.conflict_report_payload("bundle-other"),
    )

    mismatch_decision = decide(
        mismatched,
        JoinFromArtifact(
            "join-mismatched-a",
            join_id=vendor_selection.JOIN_ID,
            source_artifact_id=vendor_selection.artifact_id_for("observe-rubric-a"),
        ),
        vendor_selection.context(
            "join-mismatched-a",
            work_item_id="work-award-mismatch-a",
            activation_id="activation-award-mismatch-a",
        ),
    )
    after_mismatch = apply(mismatched, mismatch_decision)

    assert mismatch_decision.accepted is False
    assert mismatch_decision.refusal is not None
    assert mismatch_decision.refusal.reason == "join_evidence_mismatch"
    _assert_no_join_progress(mismatched, after_mismatch)

    duplicate = vendor_selection.with_duplicate_rubric_artifact(
        vendor_selection.two_report_state()[0]
    )
    duplicate_decision = decide(
        duplicate,
        JoinFromArtifact(
            "join-duplicate-a",
            join_id=vendor_selection.JOIN_ID,
            source_artifact_id=vendor_selection.artifact_id_for("observe-conflict-a"),
        ),
        vendor_selection.context(
            "join-duplicate-a",
            work_item_id="work-award-duplicate-a",
            activation_id="activation-award-duplicate-a",
        ),
    )
    after_duplicate = apply(duplicate, duplicate_decision)

    assert duplicate_decision.accepted is False
    assert duplicate_decision.refusal is not None
    assert duplicate_decision.refusal.reason == "wrong_source_artifact"
    _assert_no_join_progress(duplicate, after_duplicate)


def test_vendor_selection_award_decider_proceeds_after_both_reports() -> None:
    state, _plan, fingerprint = vendor_selection.two_report_state()

    decision = decide(
        state,
        JoinFromArtifact(
            "join-award-a",
            join_id=vendor_selection.JOIN_ID,
            source_artifact_id=vendor_selection.artifact_id_for("observe-conflict-a"),
        ),
        vendor_selection.context(
            "join-award-a",
            work_item_id="work-award-a",
            activation_id="activation-award-a",
        ),
    )
    after = apply(state, decision)

    assert decision.accepted is True
    assert after.work_items["work-award-a"].queue_family_id == QueueFamilyId(
        "candidate_bundle"
    )
    assert after.work_items["work-award-a"].payload["bundle_id"] == "bundle-a"
    assert after.work_items["work-award-a"].ref.plan_ref.authority_fingerprint == (
        fingerprint
    )
    assert str(after.activations["activation-award-a"].stage_kind_id) == (
        "award_decider"
    )
    assert after.activations["activation-award-a"].graph_node_id == (
        "vendor_selection.award_decider.start"
    )


def test_vendor_multi_candidate_join_waits_for_every_selected_target() -> None:
    from millrace.contracts.transition import JoinFromArtifact

    state, _plan, _fingerprint = (
        vendor_selection.multi_candidate_schema_covered_state()
    )
    transition_input = JoinFromArtifact(
        "join-vendor-multi-early",
        join_id=vendor_selection.JOIN_ID,
        source_artifact_id=vendor_selection.artifact_id_for(
            "observe-conflict-multi-1"
        ),
    )

    projection = project_next_lifecycle_transition(state)
    decision = decide(
        state,
        transition_input,
        vendor_selection.context(transition_input.input_id),
    )

    assert projection.candidate is None
    assert projection.diagnostics == ()
    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "join_evidence_missing"


def test_vendor_multi_candidate_join_accepts_one_report_per_target() -> None:
    from millrace.contracts.transition import JoinFromArtifact

    state, _plan, _fingerprint = (
        vendor_selection.multi_candidate_complete_report_state()
    )
    transition_input = JoinFromArtifact(
        "join-vendor-multi-complete",
        join_id=vendor_selection.JOIN_ID,
        source_artifact_id=vendor_selection.artifact_id_for(
            "observe-conflict-multi-1"
        ),
    )

    projection = project_next_lifecycle_transition(state)
    decision = decide(
        state,
        transition_input,
        vendor_selection.context(
            transition_input.input_id,
            work_item_id="work-award-multi",
            activation_id="activation-award-multi",
        ),
    )

    assert projection.diagnostics == ()
    assert projection.candidate is not None
    assert projection.candidate.kind == "join"
    assert decision.accepted is True
    assert sum(
        str(artifact.schema_id) == "RubricReport"
        for artifact in state.artifacts.values()
    ) == 2
    assert sum(
        str(artifact.schema_id) == "ConflictReport"
        for artifact in state.artifacts.values()
    ) == 2


def test_duplicate_selected_join_with_fresh_ids_is_refused_by_admission() -> None:
    state, _plan, _fingerprint = vendor_selection.two_report_state()
    joined = _apply_award_join(state)

    duplicate_decision = decide(
        joined,
        _award_join_input("join-award-a-fresh"),
        _award_join_context(
            "join-award-a-fresh",
            work_item_id="work-award-fresh-a",
            activation_id="activation-award-fresh-a",
        ),
    )
    after_duplicate = apply(joined, duplicate_decision)

    assert duplicate_decision.accepted is False
    assert duplicate_decision.refusal is not None
    assert duplicate_decision.refusal.reason == "join_already_applied"
    _assert_no_join_progress(joined, after_duplicate)


def test_exact_selected_join_input_replay_preserves_replay_behavior() -> None:
    state, _plan, _fingerprint = vendor_selection.two_report_state()
    joined = _apply_award_join(state)

    replay_decision = decide(
        joined,
        _award_join_input("join-award-a"),
        _award_join_context("join-award-a"),
    )
    after_replay = apply(joined, replay_decision)

    assert replay_decision.accepted is True
    assert replay_decision.disposition == "replayed"
    assert replay_decision.mutations == ()
    assert after_replay == joined


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("multiple_route", _duplicate_join_route),
        (
            "wrong_route_action",
            lambda state: _with_join_route(state, action_id="wrong"),
        ),
        (
            "wrong_target_work",
            lambda state: _with_award_work(
                state,
                queue_family_id=QueueFamilyId("decision_pack"),
            ),
        ),
        (
            "wrong_plan",
            lambda state: _with_award_work(
                state,
                ref=replace(
                    state.work_items["work-award-a"].ref,
                    plan_ref=_other_plan_ref(state),
                ),
            ),
        ),
        (
            "wrong_lineage",
            lambda state: _with_award_activation(state, lineage_id="wrong-lineage"),
        ),
    ],
)
def test_partial_selected_join_aftermath_refuses_fresh_duplicate(
    label: str,
    mutate: Callable[[RuntimeState], RuntimeState],
) -> None:
    state, _plan, _fingerprint = vendor_selection.two_report_state()
    corrupt = mutate(_apply_award_join(state))

    duplicate_decision = decide(
        corrupt,
        _award_join_input(f"join-award-{label}-fresh"),
        _award_join_context(
            f"join-award-{label}-fresh",
            work_item_id=f"work-award-{label}-fresh",
            activation_id=f"activation-award-{label}-fresh",
        ),
    )
    after_duplicate = apply(corrupt, duplicate_decision)

    assert duplicate_decision.accepted is False
    assert duplicate_decision.refusal is not None
    assert duplicate_decision.refusal.reason == "join_partial_state"
    _assert_no_join_progress(corrupt, after_duplicate)


def test_vendor_lifecycle_projection_ignores_unrelated_same_schema_artifact() -> None:
    state, _plan, _fingerprint = vendor_selection.packager_closed_state("a")

    first = project_next_lifecycle_transition(state)

    assert first.diagnostics == ()
    assert first.candidate is not None
    assert first.candidate.kind == "fanout"
    assert first.candidate.declaration_id == (
        "vendor_selection.candidate_packager.conflict_fanout"
    )


def test_vendor_lifecycle_projection_applies_both_selected_fanouts_in_order() -> None:
    state, _plan, _fingerprint = vendor_selection.packager_closed_state("a")
    first = project_next_lifecycle_transition(state).candidate
    assert first is not None
    after_first = apply(
        state,
        decide(state, first.transition_input, first.transition_context),
    )
    second = project_next_lifecycle_transition(after_first).candidate
    assert second is not None
    after_second = apply(
        after_first,
        decide(after_first, second.transition_input, second.transition_context),
    )

    assert first.declaration_id == "vendor_selection.candidate_packager.conflict_fanout"
    assert second.declaration_id == "vendor_selection.candidate_packager.rubric_fanout"
    assert {
        str(record.fanout_id) for record in after_second.fanout_records.values()
    } == {
        "vendor_selection.candidate_packager.rubric_fanout",
        "vendor_selection.candidate_packager.conflict_fanout",
    }


def test_vendor_selection_refuses_evaluator_close_or_gate_observation() -> None:
    state, plan, fingerprint = vendor_selection.packager_closed_state("a")
    state = _apply_selected_packager_fanouts(state)
    rubric_activation = vendor_selection.report_branch_activation_id(
        state,
        "rubric_evaluator",
    )
    state = vendor_selection.claim_activation(
        state,
        activation_id=rubric_activation,
        suffix="rubric-a",
    )
    from millrace.contracts.transition import RunnerResultObserved

    decision = decide(
        state,
        RunnerResultObserved(
            "observe-rubric-close-attempt",
            run_id="run-rubric-a",
            payload=vendor_selection.runner_observation(
                state=state,
                plan=plan,
                fingerprint=fingerprint,
                run_id="run-rubric-a",
                action_id="vendor_selection.rubric_evaluator.rubric_complete",
                input_id="observe-rubric-close-attempt",
                artifact_payload=vendor_selection.rubric_report_payload("bundle-a"),
                marker="DECISION_PACK_READY",
            ).payload,
            observed_at=None,
        ),
        vendor_selection.context("observe-rubric-close-attempt"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "undeclared_terminal_outcome"
    assert plan.effect_declarations == ()


@pytest.mark.parametrize(
    ("missing_field", "reason"),
    (
        ("fanout_records", "fanout_partial_state"),
        ("work_dependencies", "fanout_partial_state"),
    ),
)
def test_vendor_selection_award_decider_refuses_corrupt_evidence_refs(
    missing_field: str,
    reason: str,
) -> None:
    from millrace.contracts.transition import JoinFromArtifact

    state, _plan, _fingerprint = vendor_selection.two_report_state()
    corrupt = (
        replace(state, fanout_records={})
        if missing_field == "fanout_records"
        else replace(state, work_dependencies={})
    )

    decision = decide(
        corrupt,
        JoinFromArtifact(
            f"join-corrupt-{missing_field}",
            join_id=vendor_selection.JOIN_ID,
            source_artifact_id=vendor_selection.artifact_id_for("observe-conflict-a"),
        ),
        vendor_selection.context(
            f"join-corrupt-{missing_field}",
            work_item_id=f"work-award-corrupt-{missing_field}",
            activation_id=f"activation-award-corrupt-{missing_field}",
        ),
    )
    after = apply(corrupt, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == reason
    _assert_no_join_progress(corrupt, after)
