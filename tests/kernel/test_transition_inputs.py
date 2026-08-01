from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
from typing import Any, cast

import pytest

import millrace.contracts.transition as transition_contracts
from millrace.compiler import compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import QueueFamilyId, SelectedCompiledPlan
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.state import RunnerObservationRecord, RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    CloseWorkItem,
    ControlInput,
    EnqueueWork,
    InitializeWorkspace,
    InputReceiptRef,
    KernelCommand,
    Observation,
    OperatorCommand,
    RunnerResultObserved,
    SelectDefaultPlan,
    TransitionDecision,
    TransitionRecord,
    WorkflowInput,
    input_payload_digest,
)
from millrace.kernel import (
    StateConcurrencyError,
    UnsupportedMutationError,
    apply,
    decide,
    empty_runtime_state,
)
from millrace.testing import (
    deterministic_context,
    fake_runner_dispatch_envelope_for_run,
)
from millrace.workflows import kernel_ping


def _compiled_plan() -> tuple[SelectedCompiledPlan, str]:
    result = compile_workflow(kernel_ping.workflow_source())
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def _state_with_enqueued_activation() -> tuple[RuntimeState, str, str, str]:
    plan, fingerprint = _compiled_plan()
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("init"),
        AdmitPlan("admit", selected_plan=plan, authority_fingerprint=fingerprint),
        SelectDefaultPlan("select", authority_fingerprint=fingerprint),
    ):
        state = apply(
            state,
            decide(
                state,
                transition_input,
                deterministic_context(
                    transition_id=f"transition-{transition_input.input_id}",
                ),
            ),
        )

    enqueue = EnqueueWork(
        "enqueue",
        queue_family_id=QueueFamilyId("prompt"),
        payload={"body": "write the smallest useful proof"},
    )
    state = apply(
        state,
        decide(
            state,
            enqueue,
            deterministic_context(
                work_item_id="work-a",
                activation_id="activation-a",
            ),
        ),
    )
    return state, fingerprint, "work-a", "activation-a"


def _state_with_claimed_activation() -> tuple[RuntimeState, str]:
    state, fingerprint, work_item_id, activation_id = _state_with_enqueued_activation()
    state = apply(
        state,
        decide(
            state,
            ClaimWork(
                "claim-a",
                activation_id=activation_id,
            ),
            deterministic_context(
                transition_id="transition-claim-a",
                run_id="run-a",
                claim_id="claim-a",
                fencing_token="fence-a",
            ),
        ),
    )
    assert state.runs["run-a"].work_item_id == work_item_id
    assert state.runs["run-a"].activation_id == activation_id
    return state, fingerprint


def test_transition_input_families_are_distinct() -> None:
    plan, fingerprint = _compiled_plan()

    control_inputs = (
        InitializeWorkspace("init"),
        AdmitPlan("admit", selected_plan=plan, authority_fingerprint=fingerprint),
        SelectDefaultPlan("select", authority_fingerprint=fingerprint),
    )
    for transition_input in control_inputs:
        assert isinstance(transition_input, ControlInput)
        assert not isinstance(transition_input, WorkflowInput)

    operator_command = EnqueueWork(
        "enqueue",
        queue_family_id=QueueFamilyId("prompt"),
        payload={"body": "make a tiny proof"},
    )
    kernel_command = ClaimWork("claim", activation_id="activation-a")
    observation = RunnerResultObserved(
        "observe",
        run_id="run-a",
        payload={"status": "done"},
        observed_at=None,
    )

    assert isinstance(operator_command, WorkflowInput)
    assert isinstance(operator_command, OperatorCommand)
    assert not isinstance(operator_command, KernelCommand)
    assert not isinstance(operator_command, Observation)

    assert isinstance(kernel_command, WorkflowInput)
    assert isinstance(kernel_command, KernelCommand)
    assert not isinstance(kernel_command, OperatorCommand)

    assert isinstance(observation, WorkflowInput)
    assert isinstance(observation, Observation)
    assert not isinstance(observation, OperatorCommand)

    decision = decide(
        empty_runtime_state(),
        observation,
        deterministic_context(transition_id="transition-observe"),
    )
    assert decision.input_family == "workflow_observation"
    assert decision.input_kind == RunnerResultObserved.input_kind
    assert decision.accepted is False


def test_timer_due_is_kernel_command_with_stable_kind() -> None:
    timer_type = getattr(transition_contracts, "TimerDue")
    timer = timer_type("timer-due", wait_id="wait-a", observed_at=1900)

    assert isinstance(timer, WorkflowInput)
    assert isinstance(timer, KernelCommand)
    assert not isinstance(timer, OperatorCommand)
    assert timer.input_kind == "workflow.timer_due"


def test_unknown_run_observation_is_rejected_with_exact_payload_rejection_order() -> (
    None
):
    state, fingerprint, work_item_id, activation_id = _state_with_enqueued_activation()
    del activation_id
    del work_item_id
    decision = decide(
        state,
        RunnerResultObserved(
            "observation",
            run_id="run-a",
            payload={
                "record_kind": "runner_result_evidence",
                "schema_version": 2,
                "run_id": "run-a",
                "plan_fingerprint": fingerprint,
                "claim_id": "claim-a",
                "generation": 0,
                "fencing_token": "fence-a",
                "stage_kind_id": "kernel_ping.stage_prompt_tasking",
                "graph_node_id": "kernel_ping.prompting_node",
                "runner_binding_id": "kernel_ping.taskmaster",
                "marker": "TASKMASTER_READY",
                "observation_payload": {},
                "artifact_payload": {},
                "adapter_provenance": None,
            },
            observed_at=None,
        ),
        deterministic_context(transition_id="transition-observation"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_runner_evidence"


def test_fake_runner_dispatch_envelope_derives_claimed_run_without_state_change() -> (
    None
):
    state, fingerprint = _state_with_claimed_activation()
    prior = state
    envelope = fake_runner_dispatch_envelope_for_run(
        state=state,
        run_id="run-a",
    )

    assert state == prior
    assert envelope.plan_fingerprint == fingerprint
    assert envelope.run_id == "run-a"
    assert envelope.claim_id == "claim-a"
    assert envelope.generation == 0
    assert envelope.fencing_token == "fence-a"
    assert envelope.stage_kind_id == "kernel_ping.taskmaster"
    assert envelope.graph_node_id == "kernel_ping.taskmaster.start"
    assert envelope.runner_binding_id == "kernel_ping.taskmaster_runner"
    assert envelope.work_item_id == "work-a"
    assert envelope.activation_id == "activation-a"
    assert envelope.work_item_payload == {
        "body": "write the smallest useful proof",
    }
    assert envelope.governance_context["runner_binding_id"] == (
        "kernel_ping.taskmaster_runner"
    )
    assert {
        capability["id"] for capability in envelope.governance_context["capabilities"]
    } == {
        "capability.runner.invoke",
        "terminal.intent",
        "unrestricted.filesystem.read",
        "unrestricted.filesystem.write",
        "unrestricted.process.execute",
    }
    assert {
        option["marker"]: {
            "outcome_id": option["outcome_id"],
            "action_id": option["action_id"],
            "action_kind": option["action_kind"],
            "artifact_schema_id": option["artifact_schema_id"],
        }
        for option in envelope.terminal_options
    } == {
        "TASK_COMPLETE": {
            "outcome_id": "kernel_ping.taskmaster.task_complete",
            "action_id": "kernel_ping.route_taskmaster_success",
            "action_kind": "route",
            "artifact_schema_id": "kernel_ping.task_artifact",
        },
        "BLOCKED": {
            "outcome_id": "kernel_ping.taskmaster.blocked",
            "action_id": "kernel_ping.pause_taskmaster_blocked",
            "action_kind": "pause_quarantine",
            "artifact_schema_id": None,
        },
    }


def test_fake_runner_dispatch_envelope_signature_exposes_no_payload_overrides() -> None:
    parameters = tuple(
        inspect.signature(fake_runner_dispatch_envelope_for_run).parameters
    )
    assert parameters == ("state", "run_id")


def test_fake_runner_dispatch_envelope_rejects_forged_payload_overrides() -> None:
    state, _fingerprint = _state_with_claimed_activation()
    dispatch_helper = cast(Any, fake_runner_dispatch_envelope_for_run)

    with pytest.raises(TypeError):
        dispatch_helper(
            state=state,
            run_id="run-a",
            plan_fingerprint="sha256:forged",
        )
    with pytest.raises(TypeError):
        dispatch_helper(
            state=state,
            run_id="run-a",
            work_item_payload={"body": "forged"},
        )
    with pytest.raises(TypeError):
        dispatch_helper(
            state=state,
            run_id="run-a",
            governance_context={"counters": {}},
        )


def test_fake_runner_dispatch_envelope_rejects_unknown_run() -> None:
    state, _fingerprint = _state_with_claimed_activation()
    with pytest.raises(KeyError):
        fake_runner_dispatch_envelope_for_run(
            state=state,
            run_id="run-nope",
        )


def test_input_payload_digest_is_canonical_and_not_identity_or_repr_based() -> None:
    first_payload = cast(
        Mapping[str, AuthorityValue],
        {
            "body": "same semantic payload",
            "metadata": {"tags": ("alpha", "beta"), "priority": "normal"},
        },
    )
    second_payload = cast(
        Mapping[str, AuthorityValue],
        {
            "metadata": {"priority": "normal", "tags": ("alpha", "beta")},
            "body": "same semantic payload",
        },
    )
    changed_payload = cast(
        Mapping[str, AuthorityValue],
        {
            "body": "changed",
            "metadata": {"tags": ("alpha", "beta"), "priority": "normal"},
        },
    )
    assert id(first_payload) != id(second_payload)

    first = EnqueueWork(
        "input-a",
        queue_family_id=QueueFamilyId("prompt"),
        payload=first_payload,
    )
    second = EnqueueWork(
        "input-b",
        queue_family_id=QueueFamilyId("prompt"),
        payload=second_payload,
    )
    changed = EnqueueWork(
        "input-c",
        queue_family_id=QueueFamilyId("prompt"),
        payload=changed_payload,
    )

    assert input_payload_digest(first) == input_payload_digest(second)
    assert input_payload_digest(first) != input_payload_digest(changed)


def test_runner_observed_at_and_timer_due_fields_are_in_payload_digest() -> None:
    observed_1000 = RunnerResultObserved(
        "observe-a",
        run_id="run-a",
        payload={"status": "blocked"},
        observed_at=1000,
    )
    observed_1001 = RunnerResultObserved(
        "observe-b",
        run_id="run-a",
        payload={"status": "blocked"},
        observed_at=1001,
    )
    timer_type = getattr(transition_contracts, "TimerDue")
    timer_1900 = timer_type("timer-a", wait_id="wait-a", observed_at=1900)
    timer_1901 = timer_type("timer-b", wait_id="wait-a", observed_at=1901)
    timer_other_wait = timer_type("timer-c", wait_id="wait-b", observed_at=1900)

    assert input_payload_digest(observed_1000) != input_payload_digest(observed_1001)
    assert input_payload_digest(timer_1900) != input_payload_digest(timer_1901)
    assert input_payload_digest(timer_1900) != input_payload_digest(timer_other_wait)


def test_runner_result_observed_requires_explicit_observed_at() -> None:
    parameter = inspect.signature(RunnerResultObserved).parameters["observed_at"]
    assert parameter.default is inspect.Parameter.empty

    with pytest.raises(TypeError):
        RunnerResultObserved(
            "observe-omitted",
            run_id="run-a",
            payload={"status": "complete"},
        )

    assert (
        RunnerResultObserved(
            "observe-none",
            run_id="run-a",
            payload={"status": "complete"},
            observed_at=None,
        ).observed_at
        is None
    )
    with pytest.raises(ValueError, match="observed_at must be an integer"):
        RunnerResultObserved(
            "observe-bool",
            run_id="run-a",
            payload={"status": "complete"},
            observed_at=True,
        )


def test_runner_observation_record_requires_explicit_bounded_observed_at() -> None:
    parameter = inspect.signature(RunnerObservationRecord).parameters["observed_at"]
    assert parameter.default is inspect.Parameter.empty
    base = {
        "observation_id": "transition-observe:observation",
        "run_id": "run-a",
        "payload": {"status": "complete"},
        "created_by_input_id": "observe-a",
    }

    with pytest.raises(TypeError):
        RunnerObservationRecord(**base)
    assert RunnerObservationRecord(**base, observed_at=None).observed_at is None
    assert RunnerObservationRecord(**base, observed_at=0).observed_at == 0
    assert (
        RunnerObservationRecord(
            **base,
            observed_at=transition_contracts.DURABLE_INT64_MAX,
        ).observed_at
        == transition_contracts.DURABLE_INT64_MAX
    )
    for invalid in (True, -1, transition_contracts.DURABLE_INT64_MAX + 1):
        with pytest.raises(ValueError):
            RunnerObservationRecord(**base, observed_at=invalid)


def test_runner_observed_at_and_timer_due_reject_negative_time() -> None:
    with pytest.raises(ValueError, match="observed_at must be non-negative"):
        RunnerResultObserved(
            "observe-negative",
            run_id="run-a",
            payload={"status": "blocked"},
            observed_at=-1,
        )

    timer_type = getattr(transition_contracts, "TimerDue")
    with pytest.raises(ValueError, match="observed_at must be non-negative"):
        timer_type("timer-negative", wait_id="wait-a", observed_at=-1)


def test_runner_observed_at_and_timer_due_reject_values_outside_durable_range() -> None:
    too_large = transition_contracts.DURABLE_INT64_MAX + 1
    with pytest.raises(ValueError, match="observed_at exceeds durable integer range"):
        RunnerResultObserved(
            "observe-too-large",
            run_id="run-a",
            payload={"status": "blocked"},
            observed_at=too_large,
        )

    timer_type = getattr(transition_contracts, "TimerDue")
    with pytest.raises(ValueError, match="observed_at exceeds durable integer range"):
        timer_type("timer-too-large", wait_id="wait-a", observed_at=too_large)


def test_decide_is_immutable_and_does_not_mutate_state() -> None:
    state = empty_runtime_state()
    decision = decide(
        state,
        InitializeWorkspace("init"),
        deterministic_context(transition_id="transition-init"),
    )

    assert state == empty_runtime_state()
    assert decision.accepted is True
    assert decision.input_family == "control"
    assert decision.input_kind == InitializeWorkspace.input_kind
    with pytest.raises(FrozenInstanceError):
        setattr(decision, "mutations", ())


def test_apply_rechecks_expected_facts_and_prevents_double_claim() -> None:
    state, fingerprint, work_item_id, activation_id = _state_with_enqueued_activation()

    first_decision = decide(
        state,
        ClaimWork("claim-a", activation_id=activation_id),
        deterministic_context(
            transition_id="transition-claim-a",
            run_id="run-a",
            claim_id="claim-a",
            fencing_token="fence-a",
        ),
    )
    second_decision = decide(
        state,
        ClaimWork("claim-b", activation_id=activation_id),
        deterministic_context(
            transition_id="transition-claim-b",
            run_id="run-b",
            claim_id="claim-b",
            fencing_token="fence-b",
        ),
    )

    assert first_decision.expected_plan_fingerprint == fingerprint
    assert first_decision.expected_work_item_generations == {work_item_id: 0}
    assert first_decision.expected_activation_generations == {activation_id: 0}
    assert first_decision.expected_activation_unclaimed == (activation_id,)

    after_first = apply(state, first_decision)
    assert len(after_first.runs) == 1
    with pytest.raises(StateConcurrencyError):
        apply(after_first, second_decision)
    assert len(after_first.runs) == 1

    stale_plan_decision = replace(
        decide(
            after_first,
            InitializeWorkspace("fresh-control"),
            deterministic_context(transition_id="transition-fresh-control"),
        ),
        expected_plan_fingerprint="sha256:stale",
    )
    with pytest.raises(StateConcurrencyError):
        apply(after_first, stale_plan_decision)

    run_id = next(iter(after_first.runs))
    stale_run_decision = replace(
        decide(
            after_first,
            InitializeWorkspace("fresh-control-2"),
            deterministic_context(transition_id="transition-fresh-control-2"),
        ),
        expected_run_generations={run_id: 0},
        expected_run_fencing_tokens={run_id: "wrong-fence"},
    )
    before_receipts = after_first.receipts
    with pytest.raises(StateConcurrencyError):
        apply(after_first, stale_run_decision)
    assert after_first.receipts == before_receipts


def test_claim_apply_rechecks_touched_activation_and_work_item_plan_refs() -> None:
    state, _fingerprint, work_item_id, activation_id = _state_with_enqueued_activation()
    decision = decide(
        state,
        ClaimWork("claim-plan-facts", activation_id=activation_id),
        deterministic_context(
            run_id="run-plan-facts",
            claim_id="claim-plan-facts",
            fencing_token="fence-plan-facts",
        ),
    )
    assert decision.accepted is True

    work_item = state.work_items[work_item_id]
    activation = state.activations[activation_id]
    changed_plan_ref = replace(
        work_item.ref.plan_ref,
        authority_fingerprint="sha256:changed-plan",
    )
    tampered_work_item = replace(
        work_item,
        ref=replace(work_item.ref, plan_ref=changed_plan_ref),
    )
    tampered_activation = replace(
        activation,
        plan_ref=changed_plan_ref,
    )
    tampered_state = replace(
        state,
        work_items={**state.work_items, work_item_id: tampered_work_item},
        activations={**state.activations, activation_id: tampered_activation},
    )

    with pytest.raises(StateConcurrencyError):
        apply(tampered_state, decision)
    assert tampered_state.runs == {}


def test_observations_require_known_run_and_deferred_mutations_remain_inert() -> None:
    state = empty_runtime_state()
    observation = RunnerResultObserved(
        "observation",
        run_id="run-a",
        payload={"status": "done"},
        observed_at=None,
    )
    decision = decide(
        state,
        observation,
        deterministic_context(transition_id="transition-observation"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_runner_evidence"
    after_observation = apply(state, decision)
    assert after_observation.work_items == {}
    assert after_observation.activations == {}
    assert after_observation.runs == {}

    unsupported_decision = TransitionDecision(
        input_id="manual",
        input_kind="ManualUnsupported",
        input_family="workflow_kernel_command",
        input_payload_digest="sha256:manual",
        accepted=True,
        receipt_ref=InputReceiptRef("manual", "sha256:manual"),
        refusal=None,
        expected_plan_fingerprint=None,
        expected_work_item_generations={},
        expected_activation_generations={},
        expected_activation_unclaimed=(),
        expected_run_generations={},
        expected_run_fencing_tokens={},
        expected_run_unobserved=(),
        expected_pause_absent=False,
        expected_dispatch_suspension_absent=False,
        expected_dispatch_suspension_generation=None,
        expected_lineage_quarantine_absent=(),
        mutations=(CloseWorkItem(record_id="close-work"),),
        governance_events=(),
        trace_records=(),
    )

    with pytest.raises(UnsupportedMutationError):
        apply(state, unsupported_decision)
    assert state == empty_runtime_state()

    assert (
        TransitionRecord(
            record_id="transition-a",
            input_id="input-a",
            input_kind=InitializeWorkspace.input_kind,
            input_family="control",
            accepted=True,
        ).accepted
        is True
    )
