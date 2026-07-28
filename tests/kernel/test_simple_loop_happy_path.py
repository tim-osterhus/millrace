from __future__ import annotations

from collections.abc import Mapping

import pytest

from kernel.simple_loop_scenarios import (
    bootstrap_to_reviewer_ready,
    bootstrap_to_worker_ready,
)
from millrace.contracts import ActionId, ArtifactSchemaId, ClaimWork, QueueFamilyId
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import TransitionDecision
from millrace.kernel import apply
from millrace.operator import OperatorStatus, operator_status
from millrace.testing import decide_with_fake_runner_completion as decide
from millrace.testing import (
    deterministic_context,
    fake_runner_dispatch_envelope_for_run,
)
from support.simple_loop import (
    apply_accepted_input,
    compile_simple_loop,
    mutation_kinds,
    runner_observation,
    simple_loop_context,
    work_prompt_payload,
    work_result_payload,
)

WORK_DONE_ACTION_ID = ActionId("simple_loop.worker.work_done")
ACCEPTED_ACTION_ID = ActionId("simple_loop.reviewer.accepted")

FORBIDDEN_PROGRESS_MUTATIONS = {
    "mutation.record_runner_observation",
    "mutation.record_artifact",
    "mutation.create_work_item",
    "mutation.create_activation",
    "mutation.route_activation",
    "mutation.close_work_item",
    "mutation.set_pause",
    "mutation.set_quarantine",
}


def _assert_no_workflow_progress(decision: TransitionDecision) -> None:
    assert FORBIDDEN_PROGRESS_MUTATIONS.isdisjoint(mutation_kinds(decision))


def _assert_no_progress_state_changes(
    after: RuntimeState,
    before: RuntimeState,
) -> None:
    assert after.runner_observations == before.runner_observations
    assert after.artifacts == before.artifacts
    assert after.work_items == before.work_items
    assert after.activations == before.activations
    assert after.activation_routes == before.activation_routes
    assert after.closed_work_items == before.closed_work_items
    assert after.pause == before.pause
    assert after.quarantines == before.quarantines


def _queue_counts(
    status: OperatorStatus,
    queue_family_id: str,
) -> tuple[int, int, int, int]:
    family = next(
        family
        for family in status.queue_families
        if family.queue_family_id == queue_family_id
    )
    return (
        family.ready_count,
        family.active_count,
        family.closed_count,
        family.quarantined_count,
    )


def _lineage_live_or_ready_work_item_ids(
    state: RuntimeState,
    lineage_id: str,
) -> set[str]:
    status = operator_status(state)
    live_or_ready = {
        active.work_item_id
        for active in status.active_runs
        if active.lineage_id == lineage_id
    }
    closed_work_item_ids = set(state.closed_work_items)
    quarantined_work_item_ids = set(state.quarantines)
    for activation in state.activations.values():
        work_item = state.work_items.get(activation.work_item_id)
        if work_item is None or work_item.lineage_id != lineage_id:
            continue
        if activation.lineage_id != lineage_id:
            continue
        if activation.claimed_by_run_id is not None:
            continue
        if work_item.ref.work_item_id in closed_work_item_ids:
            continue
        if work_item.ref.work_item_id in quarantined_work_item_ids:
            continue
        if activation.plan_ref != work_item.ref.plan_ref:
            continue
        if activation.queue_family_id != work_item.queue_family_id:
            continue
        if activation.generation != work_item.ref.generation:
            continue
        live_or_ready.add(work_item.ref.work_item_id)
    return live_or_ready


def _assert_audit_context(
    decision: TransitionDecision,
    *,
    fingerprint: str,
    work_item_id: str,
    run_id: str,
    action_id: ActionId,
    refusal_reason: str | None = None,
) -> None:
    assert len(decision.governance_events) == 1
    assert len(decision.trace_records) == 1
    for record in (*decision.governance_events, *decision.trace_records):
        assert record.plan_fingerprint == fingerprint
        assert record.work_item_id == work_item_id
        assert record.run_id == run_id
        assert record.action_id == action_id
        assert record.authority_source == "terminal_action"
        assert record.refusal_reason == refusal_reason


def test_worker_work_done_validates_work_result_and_routes_reviewer() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_worker_ready(plan, fingerprint)
    worker_claim = decide(
        state,
        ClaimWork("claim-worker", activation_id="activation-worker"),
        simple_loop_context("claim-worker"),
    )
    assert worker_claim.accepted is True
    state = apply(state, worker_claim)
    work_packet = state.work_items["work-worker"].payload["work_packet"]
    assert isinstance(work_packet, Mapping)
    work_result = work_result_payload()

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker",
            action_id=str(WORK_DONE_ACTION_ID),
            input_id="observe-worker-done",
            artifact_payload=work_result,
            marker="WORK_DONE",
        ),
        simple_loop_context("observe-worker-done"),
    )

    assert decision.accepted is True
    assert {
        "mutation.record_runner_observation",
        "mutation.record_artifact",
        "mutation.create_work_item",
        "mutation.create_activation",
        "mutation.route_activation",
    } <= set(mutation_kinds(decision))
    after = apply(state, decision)

    artifact = after.artifacts["transition-observe-worker-done:artifact"]
    assert artifact.schema_id == ArtifactSchemaId("simple_loop.work_result")
    assert artifact.work_item_id == "work-worker"
    assert artifact.payload == work_result

    reviewer_work = after.work_items["work-reviewer"]
    reviewer_activation = after.activations["activation-reviewer"]
    assert reviewer_work.queue_family_id == QueueFamilyId("work_packet")
    assert reviewer_work.lineage_id == "work-prompt"
    assert reviewer_work.payload == dict(work_prompt_payload()) | {
        "work_packet": work_packet,
        "work_result": work_result,
    }
    assert reviewer_activation.work_item_id == "work-reviewer"
    assert reviewer_activation.lineage_id == "work-prompt"
    assert str(reviewer_activation.stage_kind_id) == "simple_loop.reviewer"
    assert reviewer_activation.graph_node_id == "simple_loop.reviewer.start"
    assert str(reviewer_activation.runner_binding_id) == (
        "simple_loop.default_agent_runner"
    )

    route = after.activation_routes[-1]
    assert route.action_id == WORK_DONE_ACTION_ID
    assert route.source_run_id == "run-worker"
    assert route.source_work_item_id == "work-worker"
    assert route.target_work_item_id == "work-reviewer"
    assert route.target_activation_id == "activation-reviewer"

    reviewer_claimed = apply_accepted_input(
        after,
        ClaimWork("claim-reviewer", activation_id="activation-reviewer"),
        simple_loop_context("claim-reviewer"),
    )
    dispatch = fake_runner_dispatch_envelope_for_run(
        state=reviewer_claimed,
        run_id="run-reviewer",
    )
    assert set(dispatch.work_item_payload) == {
        "prompt_id",
        "body",
        "work_packet",
        "work_result",
    }
    assert {
        "prompt_id": dispatch.work_item_payload["prompt_id"],
        "body": dispatch.work_item_payload["body"],
    } == work_prompt_payload()
    dispatch_work_packet = dispatch.work_item_payload["work_packet"]
    assert isinstance(dispatch_work_packet, Mapping)
    assert (
        dispatch_work_packet["completion_definition"]
        == work_packet["completion_definition"]
    )

    _assert_audit_context(
        decision,
        fingerprint=fingerprint,
        work_item_id="work-worker",
        run_id="run-worker",
        action_id=WORK_DONE_ACTION_ID,
    )


@pytest.mark.parametrize(
    "artifact_payload",
    (
        {"artifact_kind": "simple_loop.work_result"},
        {
            "artifact_kind": "wrong.artifact",
            "summary": "The worker reported with the wrong schema marker.",
        },
    ),
)
def test_invalid_worker_result_refuses_without_side_effects(
    artifact_payload: Mapping[str, AuthorityValue],
) -> None:
    plan, fingerprint = compile_simple_loop()
    state = apply_accepted_input(
        bootstrap_to_worker_ready(plan, fingerprint),
        ClaimWork("claim-worker", activation_id="activation-worker"),
        simple_loop_context("claim-worker"),
    )
    observation = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-worker",
        action_id=str(WORK_DONE_ACTION_ID),
        input_id=f"observe-invalid-worker-{artifact_payload['artifact_kind']}",
        artifact_payload=artifact_payload,
        marker="WORK_DONE",
    )

    decision = decide(
        state,
        observation,
        deterministic_context(
            transition_id=f"transition-{observation.input_id}",
            work_item_id="work-reviewer-invalid",
            activation_id="activation-reviewer-invalid",
        ),
    )
    after = apply(state, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_artifact_payload"
    _assert_no_workflow_progress(decision)
    _assert_no_progress_state_changes(after, state)
    _assert_audit_context(
        decision,
        fingerprint=fingerprint,
        work_item_id="work-worker",
        run_id="run-worker",
        action_id=WORK_DONE_ACTION_ID,
        refusal_reason="invalid_artifact_payload",
    )


def test_reviewer_accepted_closes_reviewer_item_and_completes_lineage() -> None:
    plan, fingerprint = compile_simple_loop()
    state = apply_accepted_input(
        bootstrap_to_reviewer_ready(plan, fingerprint),
        ClaimWork("claim-reviewer", activation_id="activation-reviewer"),
        simple_loop_context("claim-reviewer"),
    )

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-reviewer",
            action_id=str(ACCEPTED_ACTION_ID),
            input_id="observe-reviewer-accepted",
            artifact_payload={},
            marker="ACCEPTED",
        ),
        simple_loop_context("observe-reviewer-accepted"),
    )

    assert decision.accepted is True
    assert "mutation.record_runner_observation" in mutation_kinds(decision)
    assert "mutation.close_work_item" in mutation_kinds(decision)
    after = apply(state, decision)

    close = after.closed_work_items["work-reviewer"]
    assert close.action_id == ACCEPTED_ACTION_ID
    assert close.source_run_id == "run-reviewer"
    assert close.work_item_id == "work-reviewer"
    assert any(
        observation.run_id == "run-reviewer"
        for observation in after.runner_observations.values()
    )
    assert _lineage_live_or_ready_work_item_ids(after, "work-prompt") == set()

    status = operator_status(after)
    assert _queue_counts(status, "work_prompt") == (0, 0, 0, 0)
    assert _queue_counts(status, "work_packet") == (0, 0, 1, 0)
    assert status.active_runs == ()
    _assert_audit_context(
        decision,
        fingerprint=fingerprint,
        work_item_id="work-reviewer",
        run_id="run-reviewer",
        action_id=ACCEPTED_ACTION_ID,
    )


def test_duplicate_reviewer_observations_replay_or_refuse_without_progress() -> None:
    plan, fingerprint = compile_simple_loop()
    state = apply_accepted_input(
        bootstrap_to_reviewer_ready(plan, fingerprint),
        ClaimWork("claim-reviewer", activation_id="activation-reviewer"),
        simple_loop_context("claim-reviewer"),
    )
    accepted = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-reviewer",
        action_id=str(ACCEPTED_ACTION_ID),
        input_id="observe-reviewer-accepted",
        artifact_payload={},
        marker="ACCEPTED",
    )
    closed = apply(
        state,
        decide(state, accepted, simple_loop_context("observe-reviewer-accepted")),
    )

    replay = decide(
        closed,
        accepted,
        deterministic_context(transition_id="transition-replay-reviewer-accepted"),
    )
    assert replay.disposition == "replayed"
    assert replay.mutations == ()
    assert apply(closed, replay) == closed

    duplicate = runner_observation(
        state=closed,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-reviewer",
        action_id=str(ACCEPTED_ACTION_ID),
        input_id="observe-reviewer-accepted-again",
        artifact_payload={},
        marker="ACCEPTED",
        observation_payload_overrides={"review_summary": "second observation"},
    )
    duplicate_decision = decide(
        closed,
        duplicate,
        deterministic_context(transition_id="transition-duplicate-reviewer"),
    )
    after_duplicate = apply(closed, duplicate_decision)

    assert duplicate_decision.accepted is False
    assert duplicate_decision.refusal is not None
    assert duplicate_decision.refusal.reason == "invalid_observation_authority"
    _assert_no_workflow_progress(duplicate_decision)
    assert after_duplicate.runner_observations == closed.runner_observations
    assert after_duplicate.artifacts == closed.artifacts
    assert after_duplicate.work_items == closed.work_items
    assert after_duplicate.activations == closed.activations
    assert after_duplicate.activation_routes == closed.activation_routes
    assert after_duplicate.closed_work_items == closed.closed_work_items
