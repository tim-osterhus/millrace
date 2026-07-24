from __future__ import annotations

from dataclasses import replace

from millrace.contracts import QueueFamilyId
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import (
    ClaimWork,
    FanoutFromArtifact,
    TransitionDecision,
)
from millrace.kernel import apply, decide
from support import generic_fanout

_FANOUT_AFTERMATH_MUTATION_KINDS = frozenset(
    (
        "mutation.create_work_item",
        "mutation.create_activation",
        "mutation.route_activation",
        "mutation.record_fanout",
        "mutation.record_work_dependency",
    )
)


def _fanout_decision_for_payload(
    workflow_source,
    artifact_payload,
    *,
    input_id: str,
):
    plan, fingerprint = generic_fanout.compile_fanout(workflow_source)
    state = generic_fanout.parent_closed_state(
        plan,
        fingerprint,
        artifact_payload=artifact_payload,
    )
    transition_input = FanoutFromArtifact(
        input_id,
        fanout_id="fanout.packet.children",
        source_artifact_id="transition-observe-parent-done:artifact",
    )
    return state, decide(
        state,
        transition_input,
        generic_fanout.context(input_id),
    )


def _assert_no_fanout_aftermath_mutations(decision: TransitionDecision) -> None:
    assert not (
        _FANOUT_AFTERMATH_MUTATION_KINDS
        & {mutation.mutation_kind for mutation in decision.mutations}
    )


def _generated_child_payloads(state: RuntimeState) -> tuple[object, ...]:
    return tuple(
        item.payload
        for item in state.work_items.values()
        if item.queue_family_id == QueueFamilyId("child")
    )


def _assert_no_fanout_progress_after_refusal(
    before: RuntimeState,
    after: RuntimeState,
) -> None:
    assert after.work_items == before.work_items
    assert after.activations == before.activations
    assert after.activation_routes == before.activation_routes
    assert after.fanout_records == before.fanout_records
    assert after.work_dependencies == before.work_dependencies


def _with_selected_plan(state: RuntimeState, fingerprint: str, plan):
    return replace(
        state,
        admitted_plans={
            **state.admitted_plans,
            fingerprint: replace(
                state.admitted_plans[fingerprint],
                selected_plan=plan,
            ),
        },
    )


def test_fanout_from_accepted_artifact_creates_generated_work_atomically() -> None:
    plan, fingerprint = generic_fanout.compile_fanout()
    state = generic_fanout.parent_closed_state(plan, fingerprint)

    decision = decide(
        state,
        FanoutFromArtifact(
            "fanout-parent-packet",
            fanout_id="fanout.packet.children",
            source_artifact_id="transition-observe-parent-done:artifact",
        ),
        generic_fanout.context("fanout-parent-packet"),
    )

    assert decision.accepted is True
    assert [
        mutation.mutation_kind for mutation in decision.mutations
    ].count("mutation.create_work_item") == 2
    after = apply(state, decision)
    generated = [
        item
        for item in after.work_items.values()
        if item.queue_family_id == QueueFamilyId("child")
    ]
    assert len(generated) == 2
    assert {item.payload["child_id"] for item in generated} == {"one", "two"}
    assert {item.lineage_id for item in generated} == {"work-enqueue-parent"}
    assert {item.created_by_input_id for item in generated} == {
        "fanout-parent-packet"
    }
    assert len(after.fanout_records) == 2
    assert len(after.work_dependencies) == 2
    assert {
        dependency.dependency_work_item_id
        for dependency in after.work_dependencies.values()
    } == {"work-enqueue-parent"}
    assert {
        dependency.dependent_work_item_id
        for dependency in after.work_dependencies.values()
    } == {item.ref.work_item_id for item in generated}
    assert len(after.governance_events) == len(state.governance_events) + 1
    assert len(after.traces) == len(state.traces) + 1


def test_fanout_payload_uses_item_value_before_source_fallback() -> None:
    state, decision = _fanout_decision_for_payload(
        generic_fanout.source_with_optional_child_note(),
        generic_fanout.packet_payload(
            item_ids=("one",),
            source_note="source note",
            item_notes={"one": "item note"},
        ),
        input_id="fanout-item-note",
    )

    assert decision.accepted is True
    payloads = _generated_child_payloads(apply(state, decision))

    assert payloads == (
        {"child_id": "one", "body": "Body for one", "note": "item note"},
    )


def test_fanout_payload_uses_source_fallback_when_item_omits_field() -> None:
    state, decision = _fanout_decision_for_payload(
        generic_fanout.source_with_optional_child_note(),
        generic_fanout.packet_payload(
            item_ids=("one",),
            source_note="source note",
        ),
        input_id="fanout-source-note",
    )

    assert decision.accepted is True
    payloads = _generated_child_payloads(apply(state, decision))

    assert payloads == (
        {"child_id": "one", "body": "Body for one", "note": "source note"},
    )


def test_fanout_omits_one_or_multiple_absent_optional_mappings() -> None:
    state, decision = _fanout_decision_for_payload(
        generic_fanout.source_with_optional_child_note(),
        generic_fanout.packet_payload(item_ids=("one", "two")),
        input_id="fanout-optional-note-omitted",
    )

    assert decision.accepted is True
    payloads = _generated_child_payloads(apply(state, decision))

    assert payloads == (
        {"child_id": "one", "body": "Body for one"},
        {"child_id": "two", "body": "Body for two"},
    )


def test_fanout_refuses_missing_required_mapping_without_partial_mutation() -> None:
    state, decision = _fanout_decision_for_payload(
        generic_fanout.source_with_optional_child_note(note_required=True),
        generic_fanout.packet_payload(item_ids=("one",)),
        input_id="fanout-required-note-missing",
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_fanout_payload"
    after = apply(state, decision)
    _assert_no_fanout_progress_after_refusal(state, after)


def test_fanout_refuses_invalid_source_fallback_without_partial_mutation() -> None:
    state, decision = _fanout_decision_for_payload(
        generic_fanout.source_with_optional_child_note(
            source_note_type="integer",
            target_note_type="string",
        ),
        generic_fanout.packet_payload(
            item_ids=("one",),
            source_note=7,
        ),
        input_id="fanout-invalid-source-fallback",
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_fanout_payload"
    _assert_no_fanout_aftermath_mutations(decision)
    after = apply(state, decision)
    _assert_no_fanout_progress_after_refusal(state, after)


def test_fanout_refuses_mixed_items_all_or_nothing_without_partial_mutation() -> None:
    state, decision = _fanout_decision_for_payload(
        generic_fanout.source_with_optional_child_note(note_required=True),
        generic_fanout.packet_payload(
            item_ids=("one", "two"),
            item_notes={"one": "legal note"},
        ),
        input_id="fanout-mixed-item-note-missing",
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_fanout_payload"
    _assert_no_fanout_aftermath_mutations(decision)
    after = apply(state, decision)
    _assert_no_fanout_progress_after_refusal(state, after)


def test_fanout_preserves_explicit_null_and_schema_rejects_it_when_not_nullable(
) -> None:
    source_payload = generic_fanout.packet_payload(
        item_ids=("one",),
        item_notes={"one": None},
    )
    state, accepted = _fanout_decision_for_payload(
        generic_fanout.source_with_optional_child_note(
            source_note_type="null",
            target_note_type="null",
        ),
        source_payload,
        input_id="fanout-null-note",
    )

    assert accepted.accepted is True
    assert _generated_child_payloads(apply(state, accepted)) == (
        {"child_id": "one", "body": "Body for one", "note": None},
    )

    state, refused = _fanout_decision_for_payload(
        generic_fanout.source_with_optional_child_note(
            source_note_type="null",
            target_note_type="string",
        ),
        source_payload,
        input_id="fanout-null-note-not-nullable",
    )

    assert refused.accepted is False
    assert refused.refusal is not None
    assert refused.refusal.reason == "invalid_fanout_payload"
    _assert_no_fanout_progress_after_refusal(state, apply(state, refused))


def test_generated_work_is_claimable_only_after_dependency_is_ready() -> None:
    plan, fingerprint = generic_fanout.compile_fanout()
    state = generic_fanout.parent_closed_state(plan, fingerprint)
    state = apply(
        state,
        decide(
            state,
            FanoutFromArtifact(
                "fanout-parent-packet",
                fanout_id="fanout.packet.children",
                source_artifact_id="transition-observe-parent-done:artifact",
            ),
            generic_fanout.context("fanout-parent-packet"),
        ),
    )
    child_activation = next(
        activation
        for activation in state.activations.values()
        if activation.queue_family_id == QueueFamilyId("child")
    )

    blocked_state = replace(state, closed_work_items={})
    blocked_decision = decide(
        blocked_state,
        ClaimWork(
            "claim-child-before-dependency",
            activation_id=child_activation.activation_id,
        ),
        generic_fanout.context("claim-child-before-dependency"),
    )
    assert blocked_decision.accepted is False
    assert blocked_decision.refusal is not None
    assert blocked_decision.refusal.reason == "dependency_not_ready"
    after_blocked = apply(blocked_state, blocked_decision)
    assert "run-child-before-dependency" not in after_blocked.runs

    claim_decision = decide(
        state,
        ClaimWork(
            "claim-child-after-dependency",
            activation_id=child_activation.activation_id,
        ),
        generic_fanout.context("claim-child-after-dependency"),
    )
    assert claim_decision.accepted is True


def test_fanout_refuses_duplicate_input_without_partial_mutation() -> None:
    plan, fingerprint = generic_fanout.compile_fanout()
    state = generic_fanout.parent_closed_state(plan, fingerprint)
    state = apply(
        state,
        decide(
            state,
            FanoutFromArtifact(
                "fanout-parent-packet",
                fanout_id="fanout.packet.children",
                source_artifact_id="transition-observe-parent-done:artifact",
            ),
            generic_fanout.context("fanout-parent-packet"),
        ),
    )

    decision = decide(
        state,
        FanoutFromArtifact(
            "fanout-parent-packet-again",
            fanout_id="fanout.packet.children",
            source_artifact_id="transition-observe-parent-done:artifact",
        ),
        generic_fanout.context("fanout-parent-packet-again"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "fanout_already_applied"
    after = apply(state, decision)
    _assert_no_fanout_progress_after_refusal(state, after)


def test_fanout_refuses_orphaned_generated_work_without_partial_mutation() -> None:
    plan, fingerprint = generic_fanout.compile_fanout()
    state = generic_fanout.parent_closed_state(plan, fingerprint)
    fanned = apply(
        state,
        decide(
            state,
            FanoutFromArtifact(
                "fanout-parent-packet",
                fanout_id="fanout.packet.children",
                source_artifact_id="transition-observe-parent-done:artifact",
            ),
            generic_fanout.context("fanout-parent-packet"),
        ),
    )
    orphaned = replace(fanned, fanout_records={})

    decision = decide(
        orphaned,
        FanoutFromArtifact(
            "fanout-parent-packet-replay",
            fanout_id="fanout.packet.children",
            source_artifact_id="transition-observe-parent-done:artifact",
        ),
        generic_fanout.context("fanout-parent-packet-replay"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "fanout_partial_state"
    after = apply(orphaned, decision)
    _assert_no_fanout_progress_after_refusal(orphaned, after)


def test_fanout_refuses_missing_closed_source_aftermath() -> None:
    plan, fingerprint = generic_fanout.compile_fanout()
    state = generic_fanout.parent_closed_state(plan, fingerprint)
    open_source = replace(state, closed_work_items={})

    decision = decide(
        open_source,
        FanoutFromArtifact(
            "fanout-open-source",
            fanout_id="fanout.packet.children",
            source_artifact_id="transition-observe-parent-done:artifact",
        ),
        generic_fanout.context("fanout-open-source"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "source_work_item_not_closed"
    after = apply(open_source, decision)
    _assert_no_fanout_progress_after_refusal(open_source, after)


def test_fanout_refuses_tampered_selected_item_id_key() -> None:
    plan, fingerprint = generic_fanout.compile_fanout()
    state = generic_fanout.parent_closed_state(plan, fingerprint)
    tampered_fanout = replace(plan.fanout_declarations[0], item_id_key="")
    tampered_plan = replace(plan, fanout_declarations=(tampered_fanout,))
    tampered_state = _with_selected_plan(state, fingerprint, tampered_plan)

    decision = decide(
        tampered_state,
        FanoutFromArtifact(
            "fanout-tampered-item-id-key",
            fanout_id="fanout.packet.children",
            source_artifact_id="transition-observe-parent-done:artifact",
        ),
        generic_fanout.context("fanout-tampered-item-id-key"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
    assert decision.refusal.detail == "fanout_item_selector:fanout.packet.children"
    after = apply(tampered_state, decision)
    _assert_no_fanout_progress_after_refusal(tampered_state, after)


def test_fanout_refuses_tampered_empty_selected_payload_mapping() -> None:
    plan, fingerprint = generic_fanout.compile_fanout()
    state = generic_fanout.parent_closed_state(plan, fingerprint)
    tampered_fanout = replace(
        plan.fanout_declarations[0],
        target_payload_mapping={},
    )
    tampered_plan = replace(plan, fanout_declarations=(tampered_fanout,))
    tampered_state = _with_selected_plan(state, fingerprint, tampered_plan)

    decision = decide(
        tampered_state,
        FanoutFromArtifact(
            "fanout-tampered-empty-payload-mapping",
            fanout_id="fanout.packet.children",
            source_artifact_id="transition-observe-parent-done:artifact",
        ),
        generic_fanout.context("fanout-tampered-empty-payload-mapping"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
    assert decision.refusal.detail == "fanout_payload_mapping:fanout.packet.children"
    after = apply(tampered_state, decision)
    _assert_no_fanout_progress_after_refusal(tampered_state, after)


def test_fanout_refuses_tampered_non_closing_source_action() -> None:
    plan, fingerprint = generic_fanout.compile_fanout()
    state = generic_fanout.parent_closed_state(plan, fingerprint)
    tampered_plan = generic_fanout.plan_with_valid_route_source_action(plan)
    tampered_state = _with_selected_plan(state, fingerprint, tampered_plan)

    decision = decide(
        tampered_state,
        FanoutFromArtifact(
            "fanout-tampered-source-action",
            fanout_id="fanout.packet.children",
            source_artifact_id="transition-observe-parent-done:artifact",
        ),
        generic_fanout.context("fanout-tampered-source-action"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
    assert decision.refusal.detail == (
        "fanout_source_action_kind:fanout.packet.children"
    )
    after = apply(tampered_state, decision)
    _assert_no_fanout_progress_after_refusal(tampered_state, after)
