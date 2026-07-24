from __future__ import annotations

from dataclasses import replace

import pytest

from kernel.kernel_ping_scenarios import bootstrap_to_taskmaster_claim
from millrace.contracts import ActionId
from millrace.contracts.transition import (
    EmitGovernanceEvent,
    EmitTrace,
    RunnerResultObserved,
)
from millrace.kernel import UnsupportedMutationError, apply, decide
from millrace.testing import deterministic_context
from support.kernel_ping import (
    compile_kernel_ping,
    kernel_ping_context,
    mutation_kinds,
    runner_observation,
    task_artifact_payload,
)


def test_accepted_transitions_emit_governance_events_and_trace_mutations() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(
        plan,
        fingerprint,
        body="Build event proof",
    )

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id="kernel_ping.route_taskmaster_success",
            input_id="observe-taskmaster",
            artifact_payload=task_artifact_payload(
                objective="Prove event and trace behavior",
            ),
        ),
        kernel_ping_context("observe-taskmaster"),
    )

    assert decision.accepted is True
    assert "mutation.emit_governance_event" in mutation_kinds(decision)
    assert "mutation.emit_trace" in mutation_kinds(decision)
    assert decision.governance_events
    assert decision.trace_records
    event = decision.governance_events[0]
    trace = decision.trace_records[0]
    assert event.input_id == "observe-taskmaster"
    assert event.input_kind == RunnerResultObserved.input_kind
    assert event.disposition == "accepted"
    assert event.plan_fingerprint == fingerprint
    assert event.work_item_id == "work-prompt"
    assert event.run_id == "run-taskmaster"
    assert event.action_id == ActionId("kernel_ping.route_taskmaster_success")
    assert event.authority_source == "terminal_action"
    assert trace.input_id == event.input_id
    assert trace.disposition == event.disposition
    assert trace.action_id == event.action_id

    after = apply(state, decision)
    assert after.governance_events[-1:] == decision.governance_events
    assert after.traces[-1:] == decision.trace_records


def test_refused_transitions_emit_refusal_events_and_trace_mutations() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(
        plan,
        fingerprint,
        body="Build event proof",
    )

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id="kernel_ping.route_taskmaster_success",
            input_id="observe-refused",
            artifact_payload=task_artifact_payload(
                objective="Prove event and trace behavior",
            ),
            marker="UNDECLARED",
        ),
        deterministic_context(transition_id="transition-observe-refused"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert "mutation.record_refusal" in mutation_kinds(decision)
    assert "mutation.emit_governance_event" in mutation_kinds(decision)
    assert "mutation.emit_trace" in mutation_kinds(decision)
    event = decision.governance_events[0]
    trace = decision.trace_records[0]
    assert event.input_id == "observe-refused"
    assert event.disposition == "refused"
    assert event.plan_fingerprint == fingerprint
    assert event.work_item_id == "work-prompt"
    assert event.run_id == "run-taskmaster"
    assert event.action_id is None
    assert event.authority_source is None
    assert event.refusal_reason == "undeclared_terminal_outcome"
    assert trace.input_id == event.input_id
    assert trace.disposition == event.disposition
    assert trace.plan_fingerprint == event.plan_fingerprint
    assert trace.work_item_id == event.work_item_id
    assert trace.run_id == event.run_id
    assert trace.refusal_reason == event.refusal_reason

    after = apply(state, decision)
    assert after.refusals[-1].reason == "undeclared_terminal_outcome"
    assert after.governance_events[-1] == event
    assert after.traces[-1] == trace


def test_apply_refuses_event_trace_disagreement_before_state_change() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(
        plan,
        fingerprint,
        body="Build event proof",
    )

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id="kernel_ping.route_taskmaster_success",
            input_id="observe-disagreement",
            artifact_payload=task_artifact_payload(
                objective="Prove event and trace disagreement fails",
            ),
        ),
        kernel_ping_context("observe-disagreement"),
    )
    assert decision.accepted is True
    original_event = decision.governance_events[0]
    mismatched_event = replace(original_event, run_id="different-run")
    tampered_decision = replace(
        decision,
        governance_events=(mismatched_event,),
    )

    with pytest.raises(UnsupportedMutationError, match="governance event"):
        apply(state, tampered_decision)

    assert state.receipts.get("observe-disagreement") is None
    assert state.governance_events == bootstrap_to_taskmaster_claim(
        plan,
        fingerprint,
        body="Build event proof",
    ).governance_events

    mismatched_mutation_decision = replace(
        decision,
        mutations=tuple(
            replace(mutation, event=mismatched_event)
            if isinstance(mutation, EmitGovernanceEvent)
            else mutation
            for mutation in decision.mutations
        ),
    )

    with pytest.raises(UnsupportedMutationError, match="governance event"):
        apply(state, mismatched_mutation_decision)

    assert state.receipts.get("observe-disagreement") is None


def test_apply_refuses_trace_disagreement_before_state_change() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(
        plan,
        fingerprint,
        body="Build trace proof",
    )

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id="kernel_ping.route_taskmaster_success",
            input_id="observe-trace-disagreement",
            artifact_payload=task_artifact_payload(
                objective="Prove trace disagreement fails",
            ),
        ),
        kernel_ping_context("observe-trace-disagreement"),
    )
    assert decision.accepted is True
    original_trace = decision.trace_records[0]
    mismatched_trace = replace(original_trace, run_id="different-run")
    tampered_decision = replace(
        decision,
        trace_records=(mismatched_trace,),
    )

    with pytest.raises(UnsupportedMutationError, match="trace record"):
        apply(state, tampered_decision)

    assert state.receipts.get("observe-trace-disagreement") is None
    assert state.traces == bootstrap_to_taskmaster_claim(
        plan,
        fingerprint,
        body="Build trace proof",
    ).traces

    mismatched_mutation_decision = replace(
        decision,
        mutations=tuple(
            replace(mutation, trace=mismatched_trace)
            if isinstance(mutation, EmitTrace)
            else mutation
            for mutation in decision.mutations
        ),
    )

    with pytest.raises(UnsupportedMutationError, match="trace record"):
        apply(state, mismatched_mutation_decision)

    assert state.receipts.get("observe-trace-disagreement") is None
