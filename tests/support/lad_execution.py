"""Shared LAD execution setup primitives for LAD-A tests."""

from __future__ import annotations

from collections.abc import Mapping

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import QueueFamilyId, SelectedCompiledPlan
from millrace.contracts.compiled_plan import (
    AuthorityValue,
    TerminalActionDeclaration,
)
from millrace.contracts.state import Activation, RunRecord, RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    InitializeWorkspace,
    RunnerResultObserved,
    SelectDefaultPlan,
    TransitionContext,
    TransitionDecision,
    TransitionInput,
)
from millrace.kernel import apply, empty_runtime_state
from millrace.testing import (
    decide_with_fake_runner_completion as decide,
)
from millrace.testing import (
    deterministic_context,
    fake_completed_runner_observation_state,
    fake_runner_observation_payload,
)
from millrace.workflows import lad_execution

STAGE_RESULT_SCHEMA_ID = "execution.artifacts.stage_result"
REPORT_SCHEMA_ID = "execution.artifacts.report"
INTEGRATION_REPORT_SCHEMA_ID = "execution.artifacts.integration_report"
INCIDENT_REPORT_SCHEMA_ID = "execution.artifacts.incident_report"
BUILDER_SUMMARY_SCHEMA_ID = "execution.artifacts.builder_summary"
_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def compile_lad(
    source: Mapping[str, object] | None = None,
    *,
    integrator: bool = False,
) -> tuple[SelectedCompiledPlan, str]:
    result = compile_workflow(
        source
        or (
            lad_execution.integrator_workflow_source()
            if integrator
            else lad_execution.workflow_source()
        ),
        selected_runner_policy=_CODEX_POLICY,
    )
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def lad_context(
    input_id: str,
    *,
    work_item_id: str | None = None,
    activation_id: str | None = None,
    run_id: str | None = None,
    claim_id: str | None = None,
    fencing_token: str | None = None,
) -> TransitionContext:
    suffix = input_id.removeprefix("observe-").removeprefix("claim-")
    return deterministic_context(
        transition_id=f"transition-{input_id}",
        work_item_id=work_item_id or f"work-{suffix}",
        activation_id=activation_id or f"activation-{suffix}",
        run_id=run_id or f"run-{suffix}",
        claim_id=claim_id or f"claim-{suffix}",
        fencing_token=fencing_token or f"fence-{suffix}",
    )


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


def artifact_payload(
    artifact_kind: str,
    *,
    summary: str | None = None,
) -> Mapping[str, AuthorityValue]:
    return {
        "artifact_kind": artifact_kind,
        "summary": summary or f"{artifact_kind} payload",
    }


def task_payload() -> Mapping[str, AuthorityValue]:
    return {
        "task_id": "task-1",
        "body": "Implement the selected LAD execution work.",
    }


def apply_accepted_input(
    state: RuntimeState,
    transition_input: TransitionInput,
    context: TransitionContext,
) -> RuntimeState:
    if isinstance(transition_input, RunnerResultObserved):
        state, transition_input = fake_completed_runner_observation_state(
            state=state,
            observation=transition_input,
        )
    decision = decide(state, transition_input, context)
    assert decision.accepted is True
    return apply(state, decision)


def bootstrap_builder_ready(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    state = empty_runtime_state()
    for transition_input, context in (
        (
            InitializeWorkspace("init-lad"),
            lad_context("init-lad"),
        ),
        (
            AdmitPlan(
                "admit-lad",
                selected_plan=plan,
                authority_fingerprint=fingerprint,
            ),
            lad_context("admit-lad"),
        ),
        (
            SelectDefaultPlan("select-lad", authority_fingerprint=fingerprint),
            lad_context("select-lad"),
        ),
        (
            EnqueueWork(
                "enqueue-task",
                queue_family_id=QueueFamilyId("task"),
                payload=task_payload(),
            ),
            lad_context(
                "enqueue-task",
                work_item_id="work-task",
                activation_id="activation-builder",
            ),
        ),
    ):
        state = apply_accepted_input(state, transition_input, context)
    return state


def bootstrap_builder_claim(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    state = bootstrap_builder_ready(plan, fingerprint)
    return apply_accepted_input(
        state,
        ClaimWork("claim-builder", activation_id="activation-builder"),
        lad_context(
            "claim-builder",
            run_id="run-builder",
            claim_id="claim-builder",
            fencing_token="fence-builder",
        ),
    )


def claim_activation(
    state: RuntimeState,
    *,
    activation_id: str,
    run_id: str,
    input_id: str,
) -> RuntimeState:
    return apply_accepted_input(
        state,
        ClaimWork(input_id, activation_id=activation_id),
        lad_context(
            input_id,
            run_id=run_id,
            claim_id=input_id,
            fencing_token=f"fence-{run_id.removeprefix('run-')}",
        ),
    )


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
    overrides: Mapping[str, AuthorityValue] | None = None,
    observation_payload_overrides: Mapping[str, AuthorityValue] | None = None,
) -> RunnerResultObserved:
    run, activation = run_activation(state, run_id)
    action = action_by_id(plan, action_id)
    payload = fake_runner_observation_payload(
        run=run,
        activation=activation,
        plan_fingerprint=fingerprint,
        marker=marker or marker_for_action(plan, action),
        artifact_payload=artifact_payload,
        overrides=overrides,
        observation_payload_overrides=observation_payload_overrides,
    )
    return RunnerResultObserved(
        input_id,
        run_id=run.run_ref.run_id,
        payload=payload,
        observed_at=observed_at,
    )


def apply_runner_observation(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    run_id: str,
    action_id: str,
    input_id: str,
    schema_id: str = STAGE_RESULT_SCHEMA_ID,
    target_work_item_id: str | None = None,
    target_activation_id: str | None = None,
    marker: str | None = None,
    observed_at: int | None = None,
    observation_payload_overrides: Mapping[str, AuthorityValue] | None = None,
) -> tuple[RuntimeState, TransitionDecision]:
    observation = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=run_id,
        action_id=action_id,
        input_id=input_id,
        marker=marker,
        observed_at=observed_at,
        observation_payload_overrides=observation_payload_overrides,
        artifact_payload=artifact_payload(schema_id),
    )
    state, observation = fake_completed_runner_observation_state(
        state=state,
        observation=observation,
    )
    decision = decide(
        state,
        observation,
        lad_context(
            input_id,
            work_item_id=target_work_item_id,
            activation_id=target_activation_id,
        ),
    )
    assert decision.accepted is True
    return apply(state, decision), decision


def runtime_failure_exhausted_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    state = bootstrap_builder_claim(plan, fingerprint)
    state, _first_failure = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.recover_builder_runtime_failure",
        input_id="observe-builder-runtime-failure",
        marker="RUNTIME_FAILURE",
        target_activation_id="activation-runtime-troubleshooter",
    )
    state = claim_activation(
        state,
        activation_id="activation-runtime-troubleshooter",
        run_id="run-runtime-troubleshooter",
        input_id="claim-runtime-troubleshooter",
    )
    state, _recovered = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-runtime-troubleshooter",
        action_id="execution.return_troubleshooter_recovered",
        input_id="observe-runtime-troubleshooter-recovered",
        schema_id=REPORT_SCHEMA_ID,
        target_activation_id="activation-builder-after-runtime-recovery",
        marker="TROUBLESHOOT_RECOVERED",
    )
    state = claim_activation(
        state,
        activation_id="activation-builder-after-runtime-recovery",
        run_id="run-builder-after-runtime-recovery",
        input_id="claim-builder-after-runtime-recovery",
    )
    state, _exhausted = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder-after-runtime-recovery",
        action_id="execution.close_builder_runtime_failure_exhausted",
        input_id="observe-builder-runtime-failure-exhausted",
        marker="RUNTIME_FAILURE_ESCALATE",
    )
    return state


def mutation_kinds(decision: TransitionDecision) -> tuple[str, ...]:
    return tuple(mutation.mutation_kind for mutation in decision.mutations)


__all__ = (
    "BUILDER_SUMMARY_SCHEMA_ID",
    "INCIDENT_REPORT_SCHEMA_ID",
    "INTEGRATION_REPORT_SCHEMA_ID",
    "REPORT_SCHEMA_ID",
    "STAGE_RESULT_SCHEMA_ID",
    "action_by_id",
    "apply_accepted_input",
    "apply_runner_observation",
    "artifact_payload",
    "bootstrap_builder_claim",
    "bootstrap_builder_ready",
    "claim_activation",
    "compile_lad",
    "lad_context",
    "marker_for_action",
    "mutation_kinds",
    "run_activation",
    "runner_observation",
    "runtime_failure_exhausted_state",
    "task_payload",
)
