from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.ids import (
    ActionId,
    QueueFamilyId,
    RecoveryPolicyId,
    RunnerBindingId,
    StageKindId,
)
from millrace.contracts.runner import (
    RunnerSessionCompletionDiagnostic,
    runner_session_completion_diagnostic_bytes,
)
from millrace.contracts.runtime_failures import (
    runtime_failure_applied,
    runtime_failure_eligible,
)
from millrace.contracts.transition import (
    ClaimWork,
    RunnerResultObserved,
    TimerDue,
    input_payload_digest,
)
from millrace.kernel import apply, decide
from millrace.operator.dispatch import list_ready_dispatch_candidates
from millrace.substrate._sqlite_relations import validate_loaded_runtime_state
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import StorageIntegrityError
from millrace.substrate.sqlite import SQLiteRuntimeStore
from millrace.testing import (
    deterministic_context,
    materialize_fake_runner_session_cas,
)
from substrate._runtime_store_support import load_runtime_state, runtime_store_paths
from support import generic_admission
from tests.compiler.test_runtime_failure_mappings import _compile
from tests.kernel.test_runtime_failure_adversarial_review import (
    _apply_failure as _apply_failure_unmodified,
)
from tests.kernel.test_runtime_failure_adversarial_review import (
    _claim,
    _timeout_for_run,
)
from tests.kernel.test_runtime_failure_recovery import failed_state
from tests.kernel.test_runtime_failure_recurrence_review import (
    _recurrence_source,
)
from tests.substrate.test_persistence_integrity_refusals import (
    _apply_generic_observation,
    _generic_parent_claimed_state,
)

_LATER_RECOVERY_NODE_ID = "admission.recovery.later"
_RECOVERY_COMPLETE_OUTCOME_ID = "admission.recovery.complete"
_RECOVERY_COMPLETE_ACTION_ID = "admission.recovery.complete_work"


def _timeout_diagnostic_bytes(state, completion) -> bytes:
    run = state.runs[completion.run_id]
    activation = state.activations[run.activation_id]
    session = state.runner_sessions[completion.session_id]
    diagnostic = RunnerSessionCompletionDiagnostic(
        run_id=run.run_ref.run_id,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
        claim_id=run.run_ref.claim_id,
        generation=run.run_ref.generation,
        fencing_token=run.run_ref.fencing_token,
        stage_kind_id=str(run.stage_kind_id),
        graph_node_id=activation.graph_node_id,
        runner_binding_id=str(run.runner_binding_id),
        diagnostic={
            "diagnostics": "execution deadline exceeded",
            "error_kind": "timeout",
        },
    )
    return runner_session_completion_diagnostic_bytes(diagnostic)


def _apply_failure(state, completion, *, suffix="first"):
    """Apply a timeout whose canonical diagnostic digest is already durable."""
    completion = replace(
        completion,
        diagnostic_digest="sha256:"
        + sha256(_timeout_diagnostic_bytes(state, completion)).hexdigest(),
    )
    state = replace(
        state,
        runner_session_completions={
            **state.runner_session_completions,
            completion.session_id: completion,
        },
    )
    return _apply_failure_unmodified(state, completion, suffix=suffix)


def _recorded_source_return_source(
    *, recurrence_targets_later_node: bool = True
) -> dict[str, object]:
    """A selected generic workflow with inherited recovery input and later node."""
    source = _recurrence_source()
    graph = cast(list[dict[str, object]], source["graphs"])[0]
    graph["node_ids"] = (*graph["node_ids"], _LATER_RECOVERY_NODE_ID)

    recovery_stage = next(
        stage
        for stage in cast(list[dict[str, object]], source["stage_kinds"])
        if stage["id"] == generic_admission.RECOVERY_STAGE_ID
    )
    recovery_stage["declared_outcome_ids"] = (
        *recovery_stage["declared_outcome_ids"],
        _RECOVERY_COMPLETE_OUTCOME_ID,
    )
    cast(list[dict[str, object]], source["terminal_outcomes"]).append(
        {
            "id": _RECOVERY_COMPLETE_OUTCOME_ID,
            "stage_kind_id": generic_admission.RECOVERY_STAGE_ID,
            "marker": "RECOVERY_COMPLETE",
        }
    )

    actions = cast(list[dict[str, object]], source["terminal_actions"])
    recurrence_action = next(
        action for action in actions if action["id"] == _RECURRENCE_ACTION_ID
    )
    if recurrence_targets_later_node:
        recurrence_action["target_graph_node_id"] = _LATER_RECOVERY_NODE_ID
    completion = deepcopy(
        next(action for action in actions if action["id"] == "admission.complete")
    )
    completion.update(
        {
            "id": _RECOVERY_COMPLETE_ACTION_ID,
            "stage_kind_id": generic_admission.RECOVERY_STAGE_ID,
            "outcome_id": _RECOVERY_COMPLETE_OUTCOME_ID,
        }
    )
    actions.append(completion)
    policy = cast(list[dict[str, object]], source["recovery_policies"])[0]
    policy["reset_trigger_action_ids"] = (
        *policy["reset_trigger_action_ids"],
        _RECOVERY_COMPLETE_ACTION_ID,
    )
    return source


def _shared_return_policy_source() -> dict[str, object]:
    source = _recorded_source_return_source()
    policies = cast(list[dict[str, object]], source["recovery_policies"])
    primary = policies[0]
    second_policy_id = "admission.alternate_recovery_policy"
    second_wait_id = "admission.alternate_recovery_wait"
    second = deepcopy(primary)
    second.update(
        {
            "id": second_policy_id,
            "source_recovery_action_ids": (
                generic_admission.ALTERNATE_RECOVERY_SOURCE_ACTION_ID,
            ),
            "cooldown_wait_state_id": second_wait_id,
        }
    )
    policies.append(second)
    waits = cast(list[dict[str, object]], source["wait_states"])
    second_wait = deepcopy(waits[0])
    second_wait.update({"id": second_wait_id, "policy_id": second_policy_id})
    waits.append(second_wait)
    bindings = cast(list[dict[str, object]], source["runner_bindings"])
    mappings = cast(list[dict[str, object]], bindings[0]["runtime_failure_mappings"])
    parent_mapping = next(
        mapping
        for mapping in mappings
        if mapping["stage_kind_id"] == generic_admission.PARENT_STAGE_ID
    )
    parent_mapping["action_id"] = generic_admission.ALTERNATE_RECOVERY_SOURCE_ACTION_ID
    return source


_RECURRENCE_ACTION_ID = "admission.recovery_timeout"


def _claimed_nested_recovery_after_cooldown(
    monkeypatch: pytest.MonkeyPatch,
    *,
    recurrence_targets_later_node: bool = True,
    accepted_runtime_failure_outcome: bool = False,
):
    compiled = _compile(
        _recorded_source_return_source(
            recurrence_targets_later_node=recurrence_targets_later_node
        )
    )
    assert compiled.plan is not None, compiled.diagnostics
    plan = compiled.plan
    fingerprint = authority_fingerprint(plan)
    monkeypatch.setattr(
        generic_admission,
        "compile_plan",
        lambda: (plan, fingerprint),
    )
    state, _, _ = _generic_parent_claimed_state()
    state, first = _timeout_for_run(
        state,
        "run-generic-parent",
        observed_at=2000,
    )
    state, first_command, first_context = _apply_failure(
        state,
        first,
        suffix="c17-origin",
    )
    first_recovery_activation_id = next(
        route.target_activation_id
        for route in state.activation_routes
        if route.created_by_input_id == first_command.input_id
    )
    state = _claim(
        state,
        first_recovery_activation_id,
        "review-inherited-recovery",
    )
    if accepted_runtime_failure_outcome:
        source_run_id = "review-inherited-recovery"
        source_context = deterministic_context(
            transition_id="c17-recovery-runtime-failure-transition",
            activation_id="unused-c17-after-runtime-failure",
        )
        state = _apply_generic_observation(
            state,
            plan,
            fingerprint,
            run_id=source_run_id,
            action_id=_RECURRENCE_ACTION_ID,
            input_id="c17-recovery-runtime-failure",
            context=source_context,
            observed_at=3000,
        )
        source_observation = next(
            observation
            for observation in state.runner_observations.values()
            if observation.run_id == source_run_id
        )
        second = None
        second_command = RunnerResultObserved(
            source_observation.created_by_input_id,
            run_id=source_observation.run_id,
            payload=source_observation.payload,
            observed_at=source_observation.observed_at,
        )
        second_context = source_context
    else:
        state, second = _timeout_for_run(
            state,
            "review-inherited-recovery",
            observed_at=3000,
        )
        state, second_command, second_context = _apply_failure(
            state,
            second,
            suffix="c17-nested",
        )
    wait = next(iter(state.cooldown_waits.values()))
    timer = TimerDue(
        "c17-recorded-source-due",
        wait_id=wait.wait_id,
        observed_at=wait.due_at,
    )
    timer_context = deterministic_context(
        transition_id="c17-recorded-source-due-transition",
        activation_id="c17-recovery-after-cooldown",
    )
    timer_decision = decide(state, timer, timer_context)
    assert timer_decision.accepted, timer_decision.refusal
    state = apply(state, timer_decision)
    state = _claim(
        state,
        timer_context.activation_id,
        "c17-recovery-after-cooldown-run",
    )
    attempt = next(iter(state.recovery_attempts.values()))
    assert attempt.attempt_count == 2
    counter_values = [
        counter.value
        for counter in state.counters.values()
        if str(counter.counter_id) == "admission.recovery.domain_counter"
    ]
    assert counter_values == ([1] if accepted_runtime_failure_outcome else [])
    return (
        state,
        first,
        first_command,
        first_context,
        second,
        second_command,
        second_context,
    )


def _return_to_recorded_source(state, *, run_id: str, activation_id: str, suffix: str):
    plan_ref = state.runs[run_id].run_ref.plan_ref
    plan = state.admitted_plans[plan_ref.authority_fingerprint].selected_plan
    return _apply_generic_observation(
        state,
        plan,
        plan_ref.authority_fingerprint,
        run_id=run_id,
        action_id=generic_admission.RECOVERY_RETURN_ACTION_ID,
        input_id=f"c17-return-{suffix}",
        context=deterministic_context(
            transition_id=f"c17-return-transition-{suffix}",
            activation_id=activation_id,
        ),
    )


def _state_with_returned_recovery(monkeypatch: pytest.MonkeyPatch):
    state, *_ = _claimed_nested_recovery_after_cooldown(monkeypatch)
    returned_id = "c17-returned-authority-target"
    state = _return_to_recorded_source(
        state,
        run_id="c17-recovery-after-cooldown-run",
        activation_id=returned_id,
        suffix="authority-target",
    )
    return state, returned_id


def _ready_activation_ids(state) -> list[str]:
    return [
        candidate.activation_id
        for candidate in list_ready_dispatch_candidates(state).candidates
    ]


def _return_input_id(state, activation_id: str) -> str:
    return state.activations[activation_id].created_by_input_id


def _persist_and_load_under(tmp_path: Path, name: str, state):
    store_root = tmp_path / name
    store_root.mkdir()
    db_path, cas_root = runtime_store_paths(store_root)
    cas_store = ContentAddressedByteStore(cas_root)
    original_completions = dict(state.runner_session_completions)
    materialized = materialize_fake_runner_session_cas(
        state=state,
        cas_store=cas_store,
    )
    completions = dict(materialized.runner_session_completions)
    for session_id, original in original_completions.items():
        if not runtime_failure_eligible(state, original):
            continue
        actual_digest = cas_store.put_bytes(_timeout_diagnostic_bytes(state, original))
        assert actual_digest == original.diagnostic_digest
        # Keep the digest bound into the accepted timeout input. The fake
        # materializer's placeholder must never replace accepted command data.
        completions[session_id] = replace(
            completions[session_id],
            diagnostic_digest=original.diagnostic_digest,
        )
    state = replace(materialized, runner_session_completions=completions)
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()
    return load_runtime_state(db_path, cas_root)


def _resign_return_observation(state, observation, *, run_id=None, payload=None):
    input_id = observation.created_by_input_id
    updated = replace(
        observation,
        run_id=observation.run_id if run_id is None else run_id,
        payload=observation.payload if payload is None else payload,
    )
    command = RunnerResultObserved(
        input_id,
        run_id=updated.run_id,
        payload=updated.payload,
        observed_at=updated.observed_at,
    )
    receipt = state.receipts[input_id]
    return replace(
        state,
        receipts={
            **state.receipts,
            input_id: replace(
                receipt,
                receipt_ref=replace(
                    receipt.receipt_ref,
                    input_payload_digest=input_payload_digest(command),
                ),
            ),
        },
        runner_observations={
            **state.runner_observations,
            updated.observation_id: updated,
        },
    )


@pytest.mark.parametrize(
    "recurrence_targets_later_node",
    [False, True],
    ids=["same-node", "divergent-recurrence-node"],
)
@pytest.mark.parametrize(
    "accepted_runtime_failure_outcome",
    [False, True],
    ids=["clean-timeout", "accepted-runtime-failure-outcome"],
)
def test_nested_recovery_return_reactivates_recorded_source_through_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recurrence_targets_later_node: bool,
    accepted_runtime_failure_outcome: bool,
) -> None:
    (
        state,
        first,
        first_command,
        first_context,
        second,
        second_command,
        second_context,
    ) = _claimed_nested_recovery_after_cooldown(
        monkeypatch,
        recurrence_targets_later_node=recurrence_targets_later_node,
        accepted_runtime_failure_outcome=accepted_runtime_failure_outcome,
    )
    attempt = next(iter(state.recovery_attempts.values()))
    recorded_source = state.activations[attempt.source_activation_id]
    assert str(recorded_source.stage_kind_id) == generic_admission.RECOVERY_STAGE_ID
    assert recorded_source.queue_family_id == QueueFamilyId("parent")
    assert recorded_source.graph_node_id == generic_admission.RECOVERY_NODE_ID
    recovery_stage = next(
        stage
        for stage in state.admitted_plans[
            attempt.plan_ref.authority_fingerprint
        ].selected_plan.stage_kinds
        if str(stage.id) == generic_admission.RECOVERY_STAGE_ID
    )
    assert QueueFamilyId("child") in recovery_stage.input_queue_family_ids
    assert state.activations["c17-recovery-after-cooldown"].queue_family_id == (
        QueueFamilyId("parent")
    )
    assert (
        state.activations["c17-recovery-after-cooldown"].graph_node_id
        != recorded_source.graph_node_id
    ) is recurrence_targets_later_node

    first_returned_activation_id = "c17-returned-recorded-source-one"
    state = _return_to_recorded_source(
        state,
        run_id="c17-recovery-after-cooldown-run",
        activation_id=first_returned_activation_id,
        suffix="one",
    )
    ready = list_ready_dispatch_candidates(state)
    assert [candidate.activation_id for candidate in ready.candidates] == [
        first_returned_activation_id
    ], tuple(
        (item.activation_id, item.reason_code, item.message, item.detail)
        for item in ready.diagnostics
    )

    # The return target has inherited queue authority and the recorded source's
    # graph node, even when the latest recurrence route targets a different node.
    first_returned = state.activations[first_returned_activation_id]
    assert first_returned.queue_family_id == recorded_source.queue_family_id
    assert first_returned.stage_kind_id == recorded_source.stage_kind_id
    assert first_returned.graph_node_id == recorded_source.graph_node_id
    assert first_returned.runner_binding_id == recorded_source.runner_binding_id

    state = _persist_and_load_under(tmp_path, "after-first-return", state)
    validate_loaded_runtime_state(state)
    assert _ready_activation_ids(state) == [first_returned_activation_id]
    state = _claim(
        state,
        first_returned_activation_id,
        "c17-returned-recorded-source-one-run",
    )

    # A later valid return must advance from the claimed returned activation.
    second_returned_activation_id = "c17-returned-recorded-source-two"
    state = _return_to_recorded_source(
        state,
        run_id="c17-returned-recorded-source-one-run",
        activation_id=second_returned_activation_id,
        suffix="two",
    )
    assert _ready_activation_ids(state) == [second_returned_activation_id]
    state = _persist_and_load_under(tmp_path, "after-second-return", state)
    validate_loaded_runtime_state(state)
    state = _claim(
        state,
        second_returned_activation_id,
        "c17-returned-recorded-source-two-run",
    )

    plan_ref = state.runs["c17-returned-recorded-source-two-run"].run_ref.plan_ref
    plan = state.admitted_plans[plan_ref.authority_fingerprint].selected_plan
    state = _apply_generic_observation(
        state,
        plan,
        plan_ref.authority_fingerprint,
        run_id="c17-returned-recorded-source-two-run",
        action_id=_RECOVERY_COMPLETE_ACTION_ID,
        input_id="c17-complete-returned-recovery",
        context=deterministic_context(
            transition_id="c17-complete-returned-recovery-transition",
            activation_id="unused-after-c17-close",
        ),
    )
    assert "work-generic-parent" in state.closed_work_items
    assert state.recovery_attempts[attempt.record_id].phase == "resolved"
    state = _persist_and_load_under(tmp_path, "after-close", state)
    validate_loaded_runtime_state(state)
    assert "work-generic-parent" in state.closed_work_items
    assert state.recovery_attempts[attempt.record_id].phase == "resolved"

    # Historical timeout applications remain replayable after return metadata
    # has advanced and the selected completion has closed the lineage.
    assert runtime_failure_applied(state, first.run_id)
    first_replay = decide(state, first_command, first_context)
    assert first_replay.accepted and first_replay.disposition == "replayed"
    assert apply(state, first_replay) == state
    if second is None:
        replay = decide(state, second_command, second_context)
        assert replay.accepted and replay.disposition == "replayed"
        assert apply(state, replay) == state
    else:
        assert runtime_failure_applied(state, second.run_id)
        replay = decide(state, second_command, second_context)
        assert replay.accepted and replay.disposition == "replayed"
        assert apply(state, replay) == state


def test_ordinary_builder_return_keeps_recovery_activation_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, first = failed_state(monkeypatch, mapped=True)
    state, command, context = _apply_failure(
        state,
        first,
        suffix="ordinary-builder",
    )
    recovery_activation_id = next(
        route.target_activation_id
        for route in state.activation_routes
        if route.created_by_input_id == command.input_id
    )
    state = _claim(state, recovery_activation_id, "c17-ordinary-builder-run")
    attempt = next(iter(state.recovery_attempts.values()))
    latest_activation_id = attempt.latest_recovery_activation_id
    latest_run_id = attempt.latest_recovery_run_id
    state = _return_to_recorded_source(
        state,
        run_id=latest_run_id,
        activation_id="c17-ordinary-builder-return",
        suffix="ordinary-builder",
    )
    updated = state.recovery_attempts[attempt.record_id]
    assert updated.latest_recovery_activation_id == latest_activation_id
    assert updated.latest_recovery_run_id == latest_run_id
    assert _ready_activation_ids(state) == ["c17-ordinary-builder-return"]
    state = _claim(
        state,
        "c17-ordinary-builder-return",
        "c17-ordinary-builder-return-run",
    )
    run = state.runs["c17-ordinary-builder-return-run"]
    plan = state.admitted_plans[run.run_ref.plan_ref.authority_fingerprint]
    state = _apply_generic_observation(
        state,
        plan.selected_plan,
        run.run_ref.plan_ref.authority_fingerprint,
        run_id=run.run_ref.run_id,
        action_id="admission.complete",
        input_id="c17-ordinary-builder-complete",
        context=deterministic_context(
            transition_id="c17-ordinary-builder-complete-transition",
            activation_id="unused-after-c17-builder-close",
        ),
    )
    assert "work-generic-parent" in state.closed_work_items
    assert state.recovery_attempts[attempt.record_id].phase == "resolved"
    validate_loaded_runtime_state(state)
    assert runtime_failure_applied(state, first.run_id)
    replay = decide(state, command, context)
    assert replay.accepted and replay.disposition == "replayed"
    assert apply(state, replay) == state


def test_shared_return_action_keeps_policy_specific_recorded_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiled = _compile(_shared_return_policy_source())
    assert compiled.plan is not None, compiled.diagnostics
    plan = compiled.plan
    fingerprint = authority_fingerprint(plan)
    monkeypatch.setattr(
        generic_admission,
        "compile_plan",
        lambda: (plan, fingerprint),
    )
    state, _, _ = _generic_parent_claimed_state()
    state, completion = _timeout_for_run(state, "run-generic-parent", observed_at=2000)
    state, command, context = _apply_failure(
        state,
        completion,
        suffix="c17-shared-return-policy",
    )
    attempt = next(iter(state.recovery_attempts.values()))
    assert attempt.policy_id == RecoveryPolicyId(
        "admission.alternate_recovery_policy"
    )
    recovery_activation_id = next(
        route.target_activation_id
        for route in state.activation_routes
        if route.created_by_input_id == command.input_id
    )
    state = _claim(
        state,
        recovery_activation_id,
        "c17-shared-return-policy-recovery-run",
    )
    claimed_attempt = state.recovery_attempts[attempt.record_id]
    state = _return_to_recorded_source(
        state,
        run_id="c17-shared-return-policy-recovery-run",
        activation_id="c17-shared-return-policy-builder-return",
        suffix="shared-policy",
    )
    returned_attempt = state.recovery_attempts[attempt.record_id]
    assert (
        returned_attempt.latest_recovery_activation_id,
        returned_attempt.latest_recovery_run_id,
    ) == (
        claimed_attempt.latest_recovery_activation_id,
        claimed_attempt.latest_recovery_run_id,
    )
    assert _ready_activation_ids(state) == [
        "c17-shared-return-policy-builder-return"
    ]
    state = _persist_and_load_under(tmp_path, "after-shared-policy-return", state)
    validate_loaded_runtime_state(state)
    state = _claim(
        state,
        "c17-shared-return-policy-builder-return",
        "c17-shared-return-policy-builder-run",
    )
    run = state.runs["c17-shared-return-policy-builder-run"]
    selected = state.admitted_plans[run.run_ref.plan_ref.authority_fingerprint]
    state = _apply_generic_observation(
        state,
        selected.selected_plan,
        run.run_ref.plan_ref.authority_fingerprint,
        run_id=run.run_ref.run_id,
        action_id="admission.complete",
        input_id="c17-shared-return-policy-complete",
        context=deterministic_context(
            transition_id="c17-shared-return-policy-complete-transition",
            activation_id="unused-after-shared-policy-close",
        ),
    )
    assert "work-generic-parent" in state.closed_work_items
    assert state.recovery_attempts[attempt.record_id].phase == "resolved"
    state = _persist_and_load_under(tmp_path, "after-shared-policy-close", state)
    validate_loaded_runtime_state(state)
    assert runtime_failure_applied(state, completion.run_id)
    replay = decide(state, command, context)
    assert replay.accepted and replay.disposition == "replayed"
    assert apply(state, replay) == state


def test_recovered_returned_activation_refuses_selected_authority_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        state,
        _first,
        _first_command,
        _first_context,
        _second,
        _second_command,
        _second_context,
    ) = _claimed_nested_recovery_after_cooldown(monkeypatch)
    returned_id = "c17-returned-authority-target"
    state = _return_to_recorded_source(
        state,
        run_id="c17-recovery-after-cooldown-run",
        activation_id=returned_id,
        suffix="authority-target",
    )

    activation = state.activations[returned_id]
    wrong_plan_ref = replace(
        activation.plan_ref,
        authority_fingerprint=f"sha256:{'f' * 64}",
    )
    corruptions = (
        replace(activation, plan_ref=wrong_plan_ref),
        replace(activation, queue_family_id=QueueFamilyId("child")),
        replace(
            activation,
            stage_kind_id=StageKindId(generic_admission.PARENT_STAGE_ID),
        ),
        replace(activation, graph_node_id=_LATER_RECOVERY_NODE_ID),
        replace(activation, graph_node_id="unselected.graph.node"),
        replace(
            activation,
            runner_binding_id=RunnerBindingId("unselected.runner"),
        ),
        replace(activation, lineage_id="foreign-lineage"),
        replace(activation, generation=activation.generation + 1),
    )
    for corrupted_activation in corruptions:
        corrupted = replace(
            state,
            activations={
                **state.activations,
                returned_id: corrupted_activation,
            },
        )
        decision = decide(
            corrupted,
            ClaimWork("c17-corrupt-returned-claim", activation_id=returned_id),
            deterministic_context(
                transition_id="c17-corrupt-returned-claim-transition",
                activation_id=returned_id,
                run_id="c17-corrupt-returned-run",
                claim_id="c17-corrupt-returned-claim",
                fencing_token="c17-corrupt-returned-fence",
            ),
        )
        assert not decision.accepted, corrupted_activation


@pytest.mark.parametrize(
    "field",
    [
        "latest_recovery_activation_id",
        "latest_recovery_run_id",
        "latest_return_action_id",
        "policy_id",
        "attempt_count",
        "phase",
        "updated_by_input_id",
        "source_run_id",
        "source_work_item_id",
        "source_activation_id",
        "source_queue_family_id",
        "source_stage_kind_id",
        "source_graph_node_id",
        "source_runner_binding_id",
        "plan_ref",
        "lineage_id",
        "recovery_action_id",
    ],
)
def test_loaded_return_refuses_corrupt_recovery_attempt_authority(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    state, _returned_id = _state_with_returned_recovery(monkeypatch)
    attempt = next(iter(state.recovery_attempts.values()))
    wrong_plan_ref = replace(
        attempt.plan_ref,
        authority_fingerprint=f"sha256:{'e' * 64}",
    )
    updates: dict[str, object] = {
        "latest_recovery_activation_id": "forged-latest-activation",
        "latest_recovery_run_id": "forged-latest-run",
        "latest_return_action_id": ActionId("admission.complete"),
        "policy_id": RecoveryPolicyId("foreign-recovery-policy"),
        "attempt_count": attempt.attempt_count + 1,
        "phase": "resolved",
        "updated_by_input_id": "foreign-return-input",
        "source_run_id": "run-generic-parent",
        "source_work_item_id": "foreign-work-item",
        "source_activation_id": "activation-generic-parent",
        "source_queue_family_id": QueueFamilyId("child"),
        "source_stage_kind_id": StageKindId(generic_admission.CHILD_STAGE_ID),
        "source_graph_node_id": _LATER_RECOVERY_NODE_ID,
        "source_runner_binding_id": RunnerBindingId("foreign-runner"),
        "plan_ref": wrong_plan_ref,
        "lineage_id": "foreign-lineage",
        "recovery_action_id": ActionId(generic_admission.RECOVERY_SOURCE_ACTION_ID),
    }
    corrupted_attempt = replace(attempt, **{field: updates[field]})
    corrupted = replace(
        state,
        recovery_attempts={
            **state.recovery_attempts,
            attempt.record_id: corrupted_attempt,
        },
    )
    with pytest.raises(StorageIntegrityError):
        validate_loaded_runtime_state(corrupted)


def test_loaded_return_refuses_coherent_wrong_recorded_source_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _returned_id = _state_with_returned_recovery(monkeypatch)
    attempt = next(iter(state.recovery_attempts.values()))
    builder_activation = state.activations["activation-generic-parent"]
    builder_run = state.runs[builder_activation.claimed_by_run_id or ""]
    corrupted_attempt = replace(
        attempt,
        source_run_id=builder_run.run_ref.run_id,
        source_work_item_id=builder_activation.work_item_id,
        source_activation_id=builder_activation.activation_id,
        source_graph_node_id=builder_activation.graph_node_id,
        source_stage_kind_id=builder_activation.stage_kind_id,
        source_runner_binding_id=builder_activation.runner_binding_id,
        source_queue_family_id=builder_activation.queue_family_id,
    )
    corrupted = replace(
        state,
        recovery_attempts={
            **state.recovery_attempts,
            attempt.record_id: corrupted_attempt,
        },
    )
    with pytest.raises(StorageIntegrityError):
        validate_loaded_runtime_state(corrupted)


def test_loaded_return_refuses_pointer_to_older_recorded_recovery_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _returned_id = _state_with_returned_recovery(monkeypatch)
    attempt = next(iter(state.recovery_attempts.values()))
    assert attempt.latest_recovery_activation_id is not None
    older_activation_id = next(
        route.target_activation_id
        for route in state.activation_routes
        if route.created_by_input_id == attempt.created_by_input_id
    )
    assert older_activation_id in state.activations
    assert older_activation_id != attempt.latest_recovery_activation_id
    corrupted_attempt = replace(
        attempt,
        latest_recovery_activation_id=older_activation_id,
    )
    corrupted = replace(
        state,
        recovery_attempts={
            **state.recovery_attempts,
            attempt.record_id: corrupted_attempt,
        },
    )
    with pytest.raises(StorageIntegrityError):
        validate_loaded_runtime_state(corrupted)


@pytest.mark.parametrize(
    "field",
    [
        "record_id",
        "action_id",
        "source_run_id",
        "source_work_item_id",
        "target_work_item_id",
        "target_activation_id",
        "created_by_input_id",
    ],
)
def test_loaded_return_refuses_forged_route_provenance(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    state, returned_id = _state_with_returned_recovery(monkeypatch)
    route = next(
        route
        for route in state.activation_routes
        if route.target_activation_id == returned_id
    )
    updates: dict[str, object] = {
        "record_id": "c17-forged-return-route",
        "action_id": ActionId("admission.complete"),
        "source_run_id": "run-generic-parent",
        "source_work_item_id": "foreign-work-item",
        "target_work_item_id": "foreign-work-item",
        "target_activation_id": "review-recovery-origin",
        "created_by_input_id": "generic-claim",
    }
    corrupted_route = replace(route, **{field: updates[field]})
    corrupted = replace(
        state,
        activation_routes=tuple(
            corrupted_route if item.record_id == route.record_id else item
            for item in state.activation_routes
        ),
    )
    with pytest.raises(StorageIntegrityError):
        validate_loaded_runtime_state(corrupted)


def test_loaded_return_refuses_duplicate_route_and_stale_return_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, returned_id = _state_with_returned_recovery(monkeypatch)
    route = next(
        route
        for route in state.activation_routes
        if route.target_activation_id == returned_id
    )
    duplicated = replace(state, activation_routes=(*state.activation_routes, route))
    with pytest.raises(StorageIntegrityError):
        validate_loaded_runtime_state(duplicated)

    missing_route = replace(
        state,
        activation_routes=tuple(
            item
            for item in state.activation_routes
            if item.record_id != route.record_id
        ),
    )
    with pytest.raises(StorageIntegrityError):
        validate_loaded_runtime_state(missing_route)

    missing_activation = replace(
        state,
        activations={
            key: value
            for key, value in state.activations.items()
            if key != returned_id
        },
    )
    with pytest.raises(StorageIntegrityError):
        validate_loaded_runtime_state(missing_activation)

    duplicate_activation_id = "c17-returned-duplicate-target"
    duplicate_activation = replace(
        state.activations[returned_id],
        activation_id=duplicate_activation_id,
    )
    duplicate_route = replace(
        route,
        record_id=f"{route.record_id}:duplicate",
        target_activation_id=duplicate_activation_id,
    )
    duplicate_target = replace(
        state,
        activations={
            **state.activations,
            duplicate_activation_id: duplicate_activation,
        },
        activation_routes=(*state.activation_routes, duplicate_route),
    )
    with pytest.raises(StorageIntegrityError):
        validate_loaded_runtime_state(duplicate_target)

    activation = state.activations[returned_id]
    stale = replace(
        state,
        activations={
            **state.activations,
            returned_id: replace(activation, generation=activation.generation + 1),
        },
    )
    with pytest.raises(StorageIntegrityError):
        validate_loaded_runtime_state(stale)


@pytest.mark.parametrize(
    "part",
    ["receipt_digest", "transition", "governance_event", "trace"],
)
def test_loaded_return_requires_authenticated_input_proof(
    monkeypatch: pytest.MonkeyPatch,
    part: str,
) -> None:
    state, returned_id = _state_with_returned_recovery(monkeypatch)
    input_id = _return_input_id(state, returned_id)
    if part == "receipt_digest":
        receipt = state.receipts[input_id]
        corrupted = replace(
            state,
            receipts={
                **state.receipts,
                input_id: replace(
                    receipt,
                    receipt_ref=replace(
                        receipt.receipt_ref,
                        input_payload_digest=f"sha256:{'f' * 64}",
                    ),
                ),
            },
        )
    elif part == "transition":
        corrupted = replace(
            state,
            transitions=tuple(
                item for item in state.transitions if item.input_id != input_id
            ),
        )
    else:
        field = "governance_events" if part == "governance_event" else "traces"
        corrupted = replace(
            state,
            **{
                field: tuple(
                    replace(item, action_id=ActionId("admission.complete"))
                    if item.input_id == input_id
                    else item
                    for item in getattr(state, field)
                )
            },
        )
    with pytest.raises(StorageIntegrityError):
        validate_loaded_runtime_state(corrupted)


def test_loaded_return_rejects_resigned_observation_for_foreign_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, returned_id = _state_with_returned_recovery(monkeypatch)
    input_id = _return_input_id(state, returned_id)
    observation = next(
        item
        for item in state.runner_observations.values()
        if item.created_by_input_id == input_id
    )
    # Re-signing the receipt must not make an observation for another run the
    # authority for this recorded-source return.
    corrupted = _resign_return_observation(
        state,
        observation,
        run_id="run-generic-parent",
    )
    with pytest.raises(StorageIntegrityError):
        validate_loaded_runtime_state(corrupted)


@pytest.mark.parametrize(
    "field",
    ["session_id", "dispatch_generation", "session_fencing_token"],
)
def test_loaded_return_rejects_resigned_foreign_runner_session_evidence(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    state, returned_id = _state_with_returned_recovery(monkeypatch)
    input_id = _return_input_id(state, returned_id)
    observation = next(
        item
        for item in state.runner_observations.values()
        if item.created_by_input_id == input_id
    )
    payload = dict(observation.payload)
    if field == "dispatch_generation":
        payload[field] = cast(int, payload[field]) + 1
    else:
        payload[field] = f"foreign-{field}"
    corrupted = _resign_return_observation(
        state,
        observation,
        payload=payload,
    )
    with pytest.raises(StorageIntegrityError):
        validate_loaded_runtime_state(corrupted)


@pytest.mark.parametrize(
    "accepted_runtime_failure_outcome",
    [False, True],
    ids=["clean-timeout", "accepted-runtime-failure-outcome"],
)
def test_recovery_stage_completion_is_bounded_after_returned_source_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    accepted_runtime_failure_outcome: bool,
) -> None:
    (
        state,
        _first,
        _first_command,
        _first_context,
        _second,
        _second_command,
        _second_context,
    ) = _claimed_nested_recovery_after_cooldown(
        monkeypatch,
        accepted_runtime_failure_outcome=accepted_runtime_failure_outcome,
    )
    returned_id = "c17-returned-timeout-source"
    state = _return_to_recorded_source(
        state,
        run_id="c17-recovery-after-cooldown-run",
        activation_id=returned_id,
        suffix="timeout-source",
    )
    state = _claim(state, returned_id, "c17-returned-timeout-source-run")
    state, timeout = _timeout_for_run(
        state,
        "c17-returned-timeout-source-run",
        observed_at=9000,
    )
    state, command, context = _apply_failure(
        state,
        timeout,
        suffix="returned-source-timeout",
    )
    attempt = next(iter(state.recovery_attempts.values()))
    assert attempt.attempt_count == 3
    assert attempt.phase == "quarantine_eligible"
    assert len(state.lineage_quarantines) == 1
    assert not any(
        route.created_by_input_id == command.input_id
        for route in state.activation_routes
    )
    assert runtime_failure_applied(state, timeout.run_id)
    assert _ready_activation_ids(state) == []
    before_reload_replay = decide(state, command, context)
    assert before_reload_replay.accepted
    assert before_reload_replay.disposition == "replayed"
    assert apply(state, before_reload_replay) == state

    state = _persist_and_load_under(tmp_path, "after-returned-source-quarantine", state)
    validate_loaded_runtime_state(state)
    reloaded_attempt = state.recovery_attempts[attempt.record_id]
    assert reloaded_attempt.attempt_count == 3
    assert reloaded_attempt.phase == "quarantine_eligible"
    assert len(state.lineage_quarantines) == 1
    assert _ready_activation_ids(state) == []
    assert runtime_failure_applied(state, timeout.run_id)
    after_reload_replay = decide(state, command, context)
    assert after_reload_replay == before_reload_replay
    assert after_reload_replay.accepted
    assert after_reload_replay.disposition == "replayed"
    assert apply(state, after_reload_replay) == state
