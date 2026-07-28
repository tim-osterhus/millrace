"""Shared LAD Planning setup primitives for hosted workflow tests."""

from __future__ import annotations

from collections.abc import Mapping

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import QueueFamilyId, SelectedCompiledPlan
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.state import RuntimeState
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

STAGE_RESULT_SCHEMA_ID = "planning.artifacts.stage_result"
REPORT_SCHEMA_ID = "planning.artifacts.report"
TASK_CARDS_SCHEMA_ID = "planning.artifacts.task_cards"
_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def compile_lad_planning(
    source: Mapping[str, object] | None = None,
) -> tuple[SelectedCompiledPlan, str]:
    from millrace.workflows import lad_planning

    result = compile_workflow(
        source or lad_planning.workflow_source(),
        selected_runner_policy=_CODEX_POLICY,
    )
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def planning_context(
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


def artifact_payload(
    artifact_kind: str,
    *,
    summary: str | None = None,
) -> Mapping[str, AuthorityValue]:
    return {
        "artifact_kind": artifact_kind,
        "summary": summary or f"{artifact_kind} payload",
    }


def task_cards_payload() -> Mapping[str, AuthorityValue]:
    return {
        "artifact_kind": "task_cards",
        "cards": (
            {
                "task_card_id": "task-card-1",
                "title": "Implement selected execution work",
                "body": "Use LAD-B-0003 to fan this out later.",
            },
        ),
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


def bootstrap_route_ready(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    queue_family_id: str,
    payload: Mapping[str, AuthorityValue] | None = None,
    work_item_id: str,
    activation_id: str,
) -> RuntimeState:
    state = empty_runtime_state()
    for transition_input, context in (
        (InitializeWorkspace("init-planning"), planning_context("init-planning")),
        (
            AdmitPlan(
                "admit-planning",
                selected_plan=plan,
                authority_fingerprint=fingerprint,
            ),
            planning_context("admit-planning"),
        ),
        (
            SelectDefaultPlan("select-planning", authority_fingerprint=fingerprint),
            planning_context("select-planning"),
        ),
        (
            EnqueueWork(
                f"enqueue-{queue_family_id}",
                queue_family_id=QueueFamilyId(queue_family_id),
                payload=payload
                or {
                    "title": f"{queue_family_id} intake",
                    "body": f"Shape this {queue_family_id} through Planning.",
                    "root_source": {
                        "kind": queue_family_id,
                        "source_id": f"{queue_family_id}-source-1",
                    },
                },
            ),
            planning_context(
                f"enqueue-{queue_family_id}",
                work_item_id=work_item_id,
                activation_id=activation_id,
            ),
        ),
    ):
        state = apply_accepted_input(state, transition_input, context)
    return state


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
        planning_context(
            input_id,
            run_id=run_id,
            claim_id=input_id,
            fencing_token=f"fence-{run_id.removeprefix('run-')}",
        ),
    )


def bootstrap_route_claim(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    queue_family_id: str,
    activation_id: str,
    run_id: str,
    work_item_id: str,
) -> RuntimeState:
    state = bootstrap_route_ready(
        plan,
        fingerprint,
        queue_family_id=queue_family_id,
        work_item_id=work_item_id,
        activation_id=activation_id,
    )
    return claim_activation(
        state,
        activation_id=activation_id,
        run_id=run_id,
        input_id=f"claim-{queue_family_id}",
    )


def runner_observation(
    *,
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    run_id: str,
    action_id: str,
    input_id: str,
    artifact: Mapping[str, AuthorityValue],
    marker: str | None = None,
    overrides: Mapping[str, AuthorityValue] | None = None,
    observation_payload_overrides: Mapping[str, AuthorityValue] | None = None,
) -> RunnerResultObserved:
    run = state.runs[run_id]
    activation = state.activations[run.activation_id]
    action = next(item for item in plan.terminal_actions if str(item.id) == action_id)
    selected_marker = marker or next(
        outcome.marker
        for outcome in plan.terminal_outcomes
        if outcome.id == action.outcome_id
    )
    payload = fake_runner_observation_payload(
        run=run,
        activation=activation,
        plan_fingerprint=fingerprint,
        marker=selected_marker,
        artifact_payload=artifact,
        overrides=overrides,
        observation_payload_overrides=observation_payload_overrides,
    )
    return RunnerResultObserved(
        input_id,
        run_id=run.run_ref.run_id,
        payload=payload,
        observed_at=None,
    )


def apply_runner_observation(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    run_id: str,
    action_id: str,
    input_id: str,
    artifact: Mapping[str, AuthorityValue] | None = None,
    target_work_item_id: str | None = None,
    target_activation_id: str | None = None,
    observation_payload_overrides: Mapping[str, AuthorityValue] | None = None,
) -> tuple[RuntimeState, TransitionDecision]:
    observation = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=run_id,
        action_id=action_id,
        input_id=input_id,
        artifact=artifact or artifact_payload(STAGE_RESULT_SCHEMA_ID),
        observation_payload_overrides=observation_payload_overrides,
    )
    state, observation = fake_completed_runner_observation_state(
        state=state,
        observation=observation,
    )
    decision = decide(
        state,
        observation,
        planning_context(
            input_id,
            work_item_id=target_work_item_id,
            activation_id=target_activation_id,
        ),
    )
    assert decision.accepted is True
    return apply(state, decision), decision
