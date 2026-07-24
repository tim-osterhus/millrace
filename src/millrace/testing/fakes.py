"""Deterministic fakes for runtime tests."""

from __future__ import annotations

from collections.abc import Mapping

from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.runner import (
    RunnerDispatchEnvelope,
    RunnerResultEvidence,
    runner_result_payload,
)
from millrace.contracts.state import Activation, RunRecord, RuntimeState
from millrace.contracts.transition import TransitionContext


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
    evidence = RunnerResultEvidence(
        run_id=run.run_ref.run_id,
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

    return build_dispatch_envelope_for_run(state=state, run_id=run_id)


__all__ = (
    "deterministic_context",
    "fake_runner_dispatch_envelope_for_run",
    "fake_runner_observation_payload",
)
