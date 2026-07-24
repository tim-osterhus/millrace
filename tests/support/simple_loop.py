"""Shared simple_loop setup primitives for runtime proof tests."""

from __future__ import annotations

from collections.abc import Mapping

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.compiled_plan import (
    AuthorityValue,
    SelectedCompiledPlan,
    StageKindDeclaration,
    TerminalActionDeclaration,
)
from millrace.contracts.state import Activation, RunRecord, RuntimeState
from millrace.contracts.transition import (
    RunnerResultObserved,
    TransitionContext,
    TransitionDecision,
    TransitionInput,
)
from millrace.kernel import apply, decide
from millrace.testing import deterministic_context, fake_runner_observation_payload
from millrace.workflows import simple_loop

_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def compile_simple_loop() -> tuple[SelectedCompiledPlan, str]:
    result = compile_workflow(
        simple_loop.workflow_source(), selected_runner_policy=_CODEX_POLICY
    )
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def simple_loop_context(input_id: str) -> TransitionContext:
    contexts = {
        "enqueue": deterministic_context(
            transition_id="transition-enqueue",
            work_item_id="work-prompt",
            activation_id="activation-manager",
        ),
        "claim-manager": deterministic_context(
            transition_id="transition-claim-manager",
            run_id="run-manager",
            claim_id="claim-manager",
            fencing_token="fence-manager",
        ),
        "observe-manager-detail": deterministic_context(
            transition_id="transition-observe-manager-detail",
        ),
        "observe-manager-packet-ready": deterministic_context(
            transition_id="transition-observe-manager-packet-ready",
            work_item_id="work-worker",
            activation_id="activation-worker",
        ),
        "observe-manager-blocked": deterministic_context(
            transition_id="transition-observe-manager-blocked",
            activation_id="activation-troubleshooter-manager",
        ),
        "claim-worker": deterministic_context(
            transition_id="transition-claim-worker",
            run_id="run-worker",
            claim_id="claim-worker",
            fencing_token="fence-worker",
        ),
        "observe-worker-done": deterministic_context(
            transition_id="transition-observe-worker-done",
            work_item_id="work-reviewer",
            activation_id="activation-reviewer",
        ),
        "observe-worker-insufficient-spec": deterministic_context(
            transition_id="transition-observe-worker-insufficient-spec",
            work_item_id="work-manager-detail",
            activation_id="activation-manager-detail",
        ),
        "observe-worker-blocked": deterministic_context(
            transition_id="transition-observe-worker-blocked",
            activation_id="activation-troubleshooter-worker-blocked",
        ),
        "observe-worker-failed": deterministic_context(
            transition_id="transition-observe-worker-failed",
            activation_id="activation-troubleshooter-worker-failed",
        ),
        "claim-reviewer": deterministic_context(
            transition_id="transition-claim-reviewer",
            run_id="run-reviewer",
            claim_id="claim-reviewer",
            fencing_token="fence-reviewer",
        ),
        "observe-reviewer-accepted": deterministic_context(
            transition_id="transition-observe-reviewer-accepted",
        ),
        "observe-reviewer-gaps-found": deterministic_context(
            transition_id="transition-observe-reviewer-gaps-found",
            work_item_id="work-worker-gap",
            activation_id="activation-worker-gap",
        ),
        "claim-worker-gap": deterministic_context(
            transition_id="transition-claim-worker-gap",
            run_id="run-worker-gap",
            claim_id="claim-worker-gap",
            fencing_token="fence-worker-gap",
        ),
        "observe-gap-worker-done": deterministic_context(
            transition_id="transition-observe-gap-worker-done",
            work_item_id="work-reviewer-after-gap",
            activation_id="activation-reviewer-after-gap",
        ),
        "claim-manager-detail": deterministic_context(
            transition_id="transition-claim-manager-detail",
            run_id="run-manager-detail",
            claim_id="claim-manager-detail",
            fencing_token="fence-manager-detail",
        ),
        "observe-manager-detail-packet-ready": deterministic_context(
            transition_id="transition-observe-manager-detail-packet-ready",
            work_item_id="work-worker-revised",
            activation_id="activation-worker-revised",
        ),
        "observe-reviewer-incident-required": deterministic_context(
            transition_id="transition-observe-reviewer-incident-required",
            work_item_id="work-manager-incident",
            activation_id="activation-manager-incident",
        ),
        "observe-reviewer-blocked": deterministic_context(
            transition_id="transition-observe-reviewer-blocked",
            activation_id="activation-troubleshooter-reviewer",
        ),
        "claim-manager-incident": deterministic_context(
            transition_id="transition-claim-manager-incident",
            run_id="run-manager-incident",
            claim_id="claim-manager-incident",
            fencing_token="fence-manager-incident",
        ),
        "observe-manager-incident-triaged": deterministic_context(
            transition_id="transition-observe-manager-incident-triaged",
        ),
    }
    return contexts.get(
        input_id,
        deterministic_context(transition_id=f"transition-{input_id}"),
    )


def stage_kind_by_id(
    plan: SelectedCompiledPlan,
    stage_kind_id: str,
) -> StageKindDeclaration:
    return next(stage for stage in plan.stage_kinds if str(stage.id) == stage_kind_id)


def action_by_id(
    plan: SelectedCompiledPlan,
    action_id: str,
) -> TerminalActionDeclaration:
    return next(
        action for action in plan.terminal_actions if str(action.id) == action_id
    )


def marker_for_action(
    plan: SelectedCompiledPlan,
    action: TerminalActionDeclaration,
) -> str:
    return next(
        outcome.marker
        for outcome in plan.terminal_outcomes
        if outcome.id == action.outcome_id
    )


def work_prompt_payload() -> Mapping[str, AuthorityValue]:
    return {
        "prompt_id": "prompt-1",
        "body": "Build the simple loop proof, including this punctuation.",
    }


def work_packet_payload() -> Mapping[str, AuthorityValue]:
    return {
        "artifact_kind": "simple_loop.work_packet",
        "source_prompt_id": "prompt-1",
        "title": "Simple loop route proof",
        "objective": "Prove Manager routes executable work to Worker.",
        "completion_definition": "Worker receives the Manager-authored packet.",
    }


def work_result_payload() -> Mapping[str, AuthorityValue]:
    return {
        "artifact_kind": "simple_loop.work_result",
        "summary": "Worker completed the Manager-authored packet.",
    }


def detail_request_payload() -> Mapping[str, AuthorityValue]:
    return {
        "artifact_kind": "simple_loop.detail_request",
        "missing_details": ("Clarify the acceptance evidence.",),
    }


def gap_packet_payload() -> Mapping[str, AuthorityValue]:
    return {
        "artifact_kind": "simple_loop.gap_packet",
        "gaps": ("Add evidence that the completion definition is satisfied.",),
    }


def incident_report_payload() -> Mapping[str, AuthorityValue]:
    return {
        "artifact_kind": "simple_loop.incident_report",
        "reason": "Repeated review gaps require Manager triage.",
    }


def troubleshooting_report_payload(
    *,
    result: str = "ready to retry recorded source",
    next_route: str = "retry_recorded_source",
) -> Mapping[str, AuthorityValue]:
    return {
        "artifact_kind": "simple_loop.troubleshooting_report",
        "result": result,
        "blocker_cause": "The recorded source stage returned BLOCKED.",
        "attempted_repair": "Prepared the recorded source for retry.",
        "next_route": next_route,
    }


def apply_accepted_input(
    state: RuntimeState,
    transition_input: TransitionInput,
    context: TransitionContext,
) -> RuntimeState:
    decision = decide(state, transition_input, context)
    assert decision.accepted is True
    return apply(state, decision)


def run_activation(state: RuntimeState, run_id: str) -> tuple[RunRecord, Activation]:
    run = state.runs[run_id]
    return run, state.activations[run.activation_id]


def runner_observation(
    *,
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    run_id: str,
    action_id: str,
    input_id: str,
    artifact_payload: Mapping[str, AuthorityValue],
    marker: str | None = None,
    observed_at: int | None = None,
    observation_payload_overrides: Mapping[str, AuthorityValue] | None = None,
    overrides: Mapping[str, AuthorityValue] | None = None,
) -> RunnerResultObserved:
    run, activation = run_activation(state, run_id)
    action = action_by_id(plan, action_id)
    payload = fake_runner_observation_payload(
        run=run,
        activation=activation,
        plan_fingerprint=fingerprint,
        marker=marker or marker_for_action(plan, action),
        artifact_payload=artifact_payload,
        observation_payload_overrides=observation_payload_overrides,
        overrides=overrides,
    )
    return RunnerResultObserved(
        input_id,
        run_id=run.run_ref.run_id,
        payload=payload,
        observed_at=observed_at,
    )


def mutation_kinds(decision: TransitionDecision) -> tuple[str, ...]:
    return tuple(mutation.mutation_kind for mutation in decision.mutations)


__all__ = (
    "action_by_id",
    "apply_accepted_input",
    "compile_simple_loop",
    "detail_request_payload",
    "gap_packet_payload",
    "incident_report_payload",
    "marker_for_action",
    "mutation_kinds",
    "run_activation",
    "runner_observation",
    "simple_loop_context",
    "stage_kind_by_id",
    "troubleshooting_report_payload",
    "work_packet_payload",
    "work_prompt_payload",
    "work_result_payload",
)
