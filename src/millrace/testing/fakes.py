"""Deterministic fakes for runtime tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from hashlib import sha256

from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.runner import (
    RunnerDispatchEnvelope,
    RunnerResultEvidence,
    runner_result_evidence_bytes,
    runner_result_evidence_digest,
    runner_result_evidence_from_payload,
    runner_result_payload,
)
from millrace.contracts.state import (
    Activation,
    RunnerSessionCompletionRecord,
    RunnerSessionRecord,
    RunRecord,
    RuntimeState,
)
from millrace.contracts.transition import (
    RunnerResultObserved,
    TransitionContext,
    TransitionDecision,
    TransitionInput,
)
from millrace.substrate.cas import ContentAddressedByteStore


def deterministic_context(
    *,
    transition_id: str = "transition-1",
    work_item_id: str = "work-1",
    activation_id: str = "activation-1",
    run_id: str = "run-1",
    claim_id: str = "claim-1",
    fencing_token: str = "fence-1",
) -> TransitionContext:
    return TransitionContext(
        transition_id=transition_id,
        work_item_id=work_item_id,
        activation_id=activation_id,
        run_id=run_id,
        claim_id=claim_id,
        fencing_token=fencing_token,
    )


def fake_runner_observation_payload(
    *,
    run: RunRecord,
    activation: Activation,
    plan_fingerprint: str,
    marker: str,
    artifact_payload: Mapping[str, AuthorityValue],
    overrides: Mapping[str, AuthorityValue] | None = None,
    observation_payload_overrides: Mapping[str, AuthorityValue] | None = None,
) -> Mapping[str, AuthorityValue]:
    session = (
        None
        if run.current_session_id is None
        else run.current_session_id
    )
    evidence = RunnerResultEvidence(
        run_id=run.run_ref.run_id,
        session_id=session or f"test-session:{run.run_ref.run_id}",
        dispatch_generation=max(1, run.last_dispatch_generation),
        session_fencing_token=(
            f"test-session-fence:{run.run_ref.run_id}"
            if session is None
            else f"test-session-fence:{session}"
        ),
        plan_fingerprint=plan_fingerprint,
        claim_id=run.run_ref.claim_id,
        generation=run.run_ref.generation,
        fencing_token=run.run_ref.fencing_token,
        stage_kind_id=str(run.stage_kind_id),
        graph_node_id=activation.graph_node_id,
        runner_binding_id=str(activation.runner_binding_id),
        marker=marker,
        adapter_provenance=None,
        observation_payload=observation_payload_overrides or {},
        artifact_payload=artifact_payload,
    )
    payload = dict(runner_result_payload(evidence))
    if overrides:
        payload.update(overrides)
    return payload


def fake_runner_dispatch_envelope_for_run(
    *,
    state: RuntimeState,
    run_id: str,
) -> RunnerDispatchEnvelope:
    if run_id not in state.runs:
        raise KeyError(run_id)
    from millrace.operator.dispatch import build_dispatch_envelope_for_run

    state = fake_runner_session_state(state=state, run_id=run_id)
    return build_dispatch_envelope_for_run(state=state, run_id=run_id)


def fake_runner_session_state(
    *,
    state: RuntimeState,
    run_id: str,
) -> RuntimeState:
    if run_id not in state.runs:
        return state
    run = state.runs[run_id]
    if run.current_session_id is not None:
        return state
    session = RunnerSessionRecord(
        session_id=f"test-session:{run_id}",
        run_id=run_id,
        dispatch_generation=1,
        session_fencing_token=f"test-session-fence:{run_id}",
        state="created",
        created_at=0,
        start_intent_at=None,
        started_at=None,
        ended_at=None,
        durable_locator_digest=None,
        cleanup_disposition="pending",
    )
    return replace(
        state,
        runs={
            **state.runs,
            run_id: replace(
                run,
                current_session_id=session.session_id,
                last_dispatch_generation=1,
            ),
        },
        runner_sessions={
            **state.runner_sessions,
            session.session_id: session,
        },
    )


def fake_completed_runner_observation_state(
    *,
    state: RuntimeState,
    observation: RunnerResultObserved,
) -> tuple[RuntimeState, RunnerResultObserved]:
    try:
        evidence = runner_result_evidence_from_payload(observation.payload)
    except (TypeError, ValueError):
        return state, observation
    application_input_id = fake_runner_completion_input_id(observation.input_id)
    completion_id = application_input_id.removeprefix(
        "cli:run.session-completion:"
    )
    diagnostic_bytes = (
        f"fake completion diagnostic:{completion_id}".encode()
    )
    authorized_observation = replace(
        observation,
        input_id=application_input_id,
    )
    run = state.runs.get(observation.run_id)
    if run is None or run.current_session_id is not None:
        return state, authorized_observation
    session = RunnerSessionRecord(
        session_id=evidence.session_id,
        run_id=run.run_ref.run_id,
        dispatch_generation=evidence.dispatch_generation,
        session_fencing_token=evidence.session_fencing_token,
        state="completed",
        created_at=0,
        start_intent_at=1,
        started_at=2,
        ended_at=3,
        durable_locator_digest=None,
        cleanup_disposition="not_required",
    )
    completion = RunnerSessionCompletionRecord(
        completion_id=completion_id,
        session_id=session.session_id,
        run_id=run.run_ref.run_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        terminal_state="completed",
        exit_kind="success",
        adapter_outcome_kind="success",
        adapter_error_kind=None,
        runner_result_evidence_digest=runner_result_evidence_digest(evidence),
        primary_cancellation_request_id=None,
        cleanup_disposition="not_required",
        started_at=2,
        cancel_requested_at=None,
        completed_at=3,
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id="test",
        diagnostic_digest="sha256:" + sha256(diagnostic_bytes).hexdigest(),
        application_input_id=application_input_id,
    )
    return (
        replace(
            state,
            runs={
                **state.runs,
                run.run_ref.run_id: replace(
                    run,
                    current_session_id=session.session_id,
                    last_dispatch_generation=session.dispatch_generation,
                ),
            },
            runner_sessions={
                **state.runner_sessions,
                session.session_id: session,
            },
            runner_session_completions={
                **state.runner_session_completions,
                session.session_id: completion,
            },
        ),
        authorized_observation,
    )


def materialize_fake_runner_session_cas(
    *,
    state: RuntimeState,
    cas_store: ContentAddressedByteStore,
) -> RuntimeState:
    completions = dict(state.runner_session_completions)
    for session_id, completion in completions.items():
        evidence_digest = completion.runner_result_evidence_digest
        if evidence_digest is not None:
            observation = next(
                (
                    candidate
                    for candidate in state.runner_observations.values()
                    if candidate.created_by_input_id
                    == completion.application_input_id
                ),
                None,
            )
            if observation is not None:
                evidence = runner_result_evidence_from_payload(observation.payload)
                actual = cas_store.put_bytes(
                    runner_result_evidence_bytes(evidence)
                )
                if actual != evidence_digest:
                    raise ValueError("fake runner evidence digest mismatch")
        diagnostic_digest = cas_store.put_bytes(
            f"fake completion diagnostic:{completion.completion_id}".encode()
        )
        completions[session_id] = replace(
            completion,
            diagnostic_digest=diagnostic_digest,
        )
    return replace(state, runner_session_completions=completions)


def decide_with_fake_runner_completion(
    state: RuntimeState,
    transition_input: TransitionInput,
    context: TransitionContext,
) -> TransitionDecision:
    from millrace.kernel import decide

    if isinstance(transition_input, RunnerResultObserved):
        seeded, transition_input = fake_completed_runner_observation_state(
            state=state,
            observation=transition_input,
        )
        decision = decide(seeded, transition_input, context)
        if decision.accepted:
            for field_name in (
                "runs",
                "runner_sessions",
                "runner_session_completions",
            ):
                object.__setattr__(state, field_name, getattr(seeded, field_name))
        return decision
    return decide(state, transition_input, context)


def fake_runner_completion_input_id(input_id: str) -> str:
    prefix = "cli:run.session-completion:"
    if input_id.startswith(prefix):
        return input_id
    completion_id = "test-" + sha256(input_id.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}{completion_id}"


__all__ = (
    "deterministic_context",
    "fake_runner_dispatch_envelope_for_run",
    "fake_runner_session_state",
    "fake_runner_observation_payload",
)
