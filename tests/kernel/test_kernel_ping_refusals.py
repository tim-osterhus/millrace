from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import pytest

from kernel.kernel_ping_scenarios import (
    bootstrap_to_taskmaster_claim,
    bootstrap_to_worker_claim,
)
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.ids import RunnerBindingId, StageKindId
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import (
    RunnerResultObserved,
    TransitionDecision,
)
from millrace.kernel import StateConcurrencyError, apply, decide
from millrace.testing import deterministic_context
from support.kernel_ping import (
    compile_kernel_ping,
    kernel_ping_context,
    mutation_kinds,
    runner_observation,
    task_artifact_payload,
)


def _assert_no_workflow_progress(decision: TransitionDecision) -> None:
    forbidden = {
        "mutation.record_runner_observation",
        "mutation.record_artifact",
        "mutation.route_activation",
        "mutation.create_work_item",
        "mutation.create_activation",
        "mutation.close_work_item",
        "mutation.set_pause",
        "mutation.set_quarantine",
    }
    assert forbidden.isdisjoint(mutation_kinds(decision))


def _assert_no_workflow_state_progress(
    after: RuntimeState,
    before: RuntimeState,
) -> None:
    assert after.work_items == before.work_items
    assert after.activations == before.activations
    assert after.runs == before.runs
    assert after.runner_observations == before.runner_observations
    assert after.artifacts == before.artifacts
    assert after.activation_routes == before.activation_routes
    assert after.closed_work_items == before.closed_work_items
    assert after.pause == before.pause
    assert after.quarantines == before.quarantines


@pytest.mark.parametrize(
    ("input_id", "overrides", "expected_reason"),
    (
        (
            "observe-wrong-plan",
            {"plan_fingerprint": "sha256:wrong"},
            "invalid_observation_authority",
        ),
        ("observe-wrong-claim", {"claim_id": "wrong"}, "invalid_observation_authority"),
        (
            "observe-stale-generation",
            {"generation": 1},
            "invalid_observation_authority",
        ),
        (
            "observe-wrong-fence",
            {"fencing_token": "wrong"},
            "invalid_observation_authority",
        ),
        (
            "observe-wrong-stage",
            {"stage_kind_id": "wrong-stage"},
            "invalid_observation_authority",
        ),
        (
            "observe-wrong-runner-binding",
            {"runner_binding_id": "wrong-runner"},
            "invalid_observation_authority",
        ),
        (
            "observe-undeclared-marker",
            {"marker": "UNDECLARED"},
            "undeclared_terminal_outcome",
        ),
    ),
)
def test_hostile_worker_observations_are_refused_without_quarantine(
    input_id: str,
    overrides: Mapping[str, AuthorityValue],
    expected_reason: str,
) -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_worker_claim(plan, fingerprint)
    observation = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-worker",
        action_id="kernel_ping.close_worker_success",
        input_id=input_id,
        artifact_payload={},
        overrides=overrides,
    )

    decision = decide(state, observation, kernel_ping_context(input_id))
    after = apply(state, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == expected_reason
    _assert_no_workflow_progress(decision)
    assert after.pause is None
    assert after.quarantines == {}
    assert after.closed_work_items == state.closed_work_items


@pytest.mark.parametrize(
    "case",
    (
        "missing_record_kind",
        "missing_observation_payload",
        "extra_top_level_key",
        "schema_version_bool",
        "generation_bool",
        "bad_artifact_payload",
        "bad_observation_payload",
    ),
)
def test_malformed_runner_result_payloads_are_rejected_before_progress(
    case: str,
) -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    payload = dict(
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id="kernel_ping.route_taskmaster_success",
            input_id=f"observe-{case}",
            artifact_payload=task_artifact_payload(objective="Keep valid until mutate"),
        ).payload
    )

    if case == "missing_record_kind":
        payload.pop("record_kind")
    elif case == "missing_observation_payload":
        payload.pop("observation_payload")
    elif case == "extra_top_level_key":
        payload["top_level"] = "forged"
    elif case == "schema_version_bool":
        payload["schema_version"] = True
    elif case == "generation_bool":
        payload["generation"] = True
    elif case == "bad_artifact_payload":
        payload["artifact_payload"] = ("not", "mapping")
    elif case == "bad_observation_payload":
        payload["observation_payload"] = ("not", "mapping")
    else:  # pragma: no cover - parametrization guard
        raise AssertionError(f"unknown malformed payload case: {case}")

    decision = decide(
        state,
        RunnerResultObserved(
            input_id=f"observe-malformed-{case}",
            run_id="run-taskmaster",
            payload=payload,
            observed_at=None,
        ),
        kernel_ping_context(f"observe-malformed-{case}"),
    )
    after = apply(state, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_runner_evidence"
    _assert_no_workflow_progress(decision)
    _assert_no_workflow_state_progress(after, state)


def test_unclaimed_activation_is_authority_refusal_without_progress() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_worker_claim(plan, fingerprint)
    run = state.runs["run-worker"]
    activation = state.activations[run.activation_id]
    tampered = replace(
        state,
        activations={
            **state.activations,
            activation.activation_id: replace(activation, claimed_by_run_id=None),
        },
    )

    observation = runner_observation(
        state=tampered,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-worker",
        action_id="kernel_ping.close_worker_success",
        input_id="observe-unclaimed-run",
        artifact_payload={},
    )
    decision = decide(
        tampered,
        observation,
        kernel_ping_context("observe-unclaimed-run"),
    )
    after = apply(tampered, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_observation_authority"
    _assert_no_workflow_progress(decision)
    _assert_no_workflow_state_progress(after, tampered)


@pytest.mark.parametrize(
    "case",
    (
        "activation_work_item_id",
        "activation_stage_kind_id",
        "activation_runner_binding_id",
        "activation_plan_ref",
        "activation_generation",
        "work_item_plan_ref",
        "work_item_generation",
    ),
)
def test_incoherent_run_activation_work_item_state_is_authority_refusal(
    case: str,
) -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_worker_claim(plan, fingerprint)
    run = state.runs["run-worker"]
    activation = state.activations[run.activation_id]
    work_item = state.work_items[run.work_item_id]
    tampered_activation = activation
    tampered_work_item = work_item

    if case == "activation_work_item_id":
        tampered_activation = replace(activation, work_item_id="work-prompt")
    elif case == "activation_stage_kind_id":
        tampered_activation = replace(
            activation,
            stage_kind_id=StageKindId("kernel_ping.wrong_stage"),
        )
    elif case == "activation_runner_binding_id":
        tampered_activation = replace(
            activation,
            runner_binding_id=RunnerBindingId("kernel_ping.wrong_runner"),
        )
    elif case == "activation_plan_ref":
        tampered_activation = replace(
            activation,
            plan_ref=replace(
                activation.plan_ref,
                authority_fingerprint="sha256:wrong-activation",
            ),
        )
    elif case == "activation_generation":
        tampered_activation = replace(activation, generation=activation.generation + 1)
    elif case == "work_item_plan_ref":
        tampered_work_item = replace(
            work_item,
            ref=replace(
                work_item.ref,
                plan_ref=replace(
                    work_item.ref.plan_ref,
                    authority_fingerprint="sha256:wrong-work-item",
                ),
            ),
        )
    elif case == "work_item_generation":
        tampered_work_item = replace(
            work_item,
            ref=replace(work_item.ref, generation=work_item.ref.generation + 1),
        )
    else:  # pragma: no cover - parametrization guard
        raise AssertionError(f"unknown incoherent state case: {case}")

    tampered = replace(
        state,
        activations={
            **state.activations,
            activation.activation_id: tampered_activation,
        },
        work_items={
            **state.work_items,
            work_item.ref.work_item_id: tampered_work_item,
        },
    )
    observation = runner_observation(
        state=tampered,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-worker",
        action_id="kernel_ping.close_worker_success",
        input_id=f"observe-incoherent-{case}",
        artifact_payload={},
    )
    decision = decide(
        tampered,
        observation,
        kernel_ping_context(f"observe-incoherent-{case}"),
    )
    after = apply(tampered, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_observation_authority"
    _assert_no_workflow_progress(decision)
    _assert_no_workflow_state_progress(after, tampered)


def test_invalid_taskmaster_artifact_payload_is_refused_without_quarantine() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(
        plan,
        fingerprint,
        body="Build refusal proof",
    )
    observation = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-taskmaster",
        action_id="kernel_ping.route_taskmaster_success",
        input_id="observe-invalid-artifact",
        artifact_payload={"artifact_kind": "wrong.kind"},
    )

    decision = decide(
        state,
        observation,
        kernel_ping_context("observe-invalid-artifact"),
    )
    after = apply(state, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_artifact_payload"
    _assert_no_workflow_progress(decision)
    assert after.pause is None
    assert after.quarantines == {}


def test_duplicate_input_digest_replays_and_conflicting_digest_is_refused() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_worker_claim(plan, fingerprint)
    observation = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-worker",
        action_id="kernel_ping.close_worker_success",
        input_id="observe-worker-success",
        artifact_payload={},
    )
    first_decision = decide(
        state,
        observation,
        kernel_ping_context("observe-worker-success"),
    )
    assert first_decision.accepted is True
    after_first = apply(state, first_decision)

    replay_decision = decide(
        after_first,
        observation,
        deterministic_context(transition_id="transition-replay"),
    )
    assert replay_decision.disposition == "replayed"
    assert replay_decision.mutations == ()
    assert apply(after_first, replay_decision) == after_first

    conflict = runner_observation(
        state=after_first,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-worker",
        action_id="kernel_ping.close_worker_success",
        input_id="observe-worker-success",
        artifact_payload={},
        observation_payload_overrides={
            "worker_summary": "same input id, changed payload",
        },
    )
    conflict_decision = decide(
        after_first,
        conflict,
        deterministic_context(transition_id="transition-conflict"),
    )
    after_conflict = apply(after_first, conflict_decision)
    assert conflict_decision.accepted is False
    assert conflict_decision.refusal is not None
    assert conflict_decision.refusal.reason == "idempotency_conflict"
    event = conflict_decision.governance_events[0]
    trace = conflict_decision.trace_records[0]
    assert event.plan_fingerprint == fingerprint
    assert event.work_item_id == "work-task-artifact"
    assert event.run_id == "run-worker"
    assert event.refusal_reason == "idempotency_conflict"
    assert trace.plan_fingerprint == event.plan_fingerprint
    assert trace.work_item_id == event.work_item_id
    assert trace.run_id == event.run_id
    assert trace.refusal_reason == event.refusal_reason
    _assert_no_workflow_progress(conflict_decision)
    assert after_conflict.closed_work_items == after_first.closed_work_items


def test_same_runner_result_new_input_after_terminal_state_is_refused() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_worker_claim(plan, fingerprint)
    first = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-worker",
        action_id="kernel_ping.close_worker_success",
        input_id="observe-worker-success",
        artifact_payload={},
    )
    closed = apply(
        state,
        decide(state, first, kernel_ping_context("observe-worker-success")),
    )

    duplicate = runner_observation(
        state=closed,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-worker",
        action_id="kernel_ping.close_worker_success",
        input_id="observe-worker-success-again",
        artifact_payload={},
    )
    duplicate_decision = decide(
        closed,
        duplicate,
        deterministic_context(transition_id="transition-duplicate-run-result"),
    )
    after_duplicate = apply(closed, duplicate_decision)

    assert duplicate_decision.accepted is False
    assert duplicate_decision.refusal is not None
    assert duplicate_decision.refusal.reason == "duplicate_runner_observation"
    _assert_no_workflow_progress(duplicate_decision)
    assert after_duplicate.closed_work_items == closed.closed_work_items
    assert after_duplicate.activation_routes == closed.activation_routes
    assert after_duplicate.pause == closed.pause
    assert after_duplicate.quarantines == closed.quarantines


def test_stale_duplicate_runner_observation_decision_is_rejected_at_apply() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_worker_claim(plan, fingerprint)
    first = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-worker",
        action_id="kernel_ping.close_worker_success",
        input_id="observe-worker-success",
        artifact_payload={},
    )
    second = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-worker",
        action_id="kernel_ping.close_worker_success",
        input_id="observe-worker-success-again",
        artifact_payload={},
    )

    first_decision = decide(state, first, kernel_ping_context("observe-worker-success"))
    stale_second_decision = decide(
        state,
        second,
        deterministic_context(transition_id="transition-stale-second"),
    )
    assert first_decision.accepted is True
    assert stale_second_decision.accepted is True

    after_first = apply(state, first_decision)
    with pytest.raises(StateConcurrencyError, match="run observation state changed"):
        apply(after_first, stale_second_decision)
