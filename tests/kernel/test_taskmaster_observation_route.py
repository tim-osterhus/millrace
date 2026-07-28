from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from millrace.contracts import (
    QueueFamilyId,
    SelectedCompiledPlan,
)
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
)
from millrace.kernel import apply, empty_runtime_state
from millrace.testing import decide_with_fake_runner_completion as decide
from millrace.testing import deterministic_context, fake_runner_observation_payload
from support.kernel_ping import (
    action_by_id,
    apply_accepted_input,
    compile_kernel_ping,
    marker_for_action,
    mutation_kinds,
    run_activation,
)

FORBIDDEN_PROGRESS_MUTATIONS = {
    "mutation.record_runner_observation",
    "mutation.record_artifact",
    "mutation.route_activation",
    "mutation.create_work_item",
    "mutation.create_activation",
}

SUCCESS_ACTION_ID = "kernel_ping.route_taskmaster_success"


def _admit_select_enqueue_claim(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("init"),
        AdmitPlan("admit", selected_plan=plan, authority_fingerprint=fingerprint),
        SelectDefaultPlan("select", authority_fingerprint=fingerprint),
        EnqueueWork(
            "enqueue",
            queue_family_id=QueueFamilyId("prompt"),
            payload={
                "prompt_id": "p-1",
                "body": "Build the route proof",
            },
        ),
        ClaimWork("claim", activation_id="activation-a"),
    ):
        state = apply_accepted_input(
            state,
            transition_input,
            _context_for(transition_input.input_id),
        )
    return state


def _context_for(input_id: str) -> TransitionContext:
    if input_id == "enqueue":
        return deterministic_context(
            transition_id="transition-enqueue",
            work_item_id="work-a",
            activation_id="activation-a",
        )
    if input_id == "claim":
        return deterministic_context(
            transition_id="transition-claim",
            run_id="run-a",
            claim_id="claim-a",
            fencing_token="fence-a",
        )
    return deterministic_context(transition_id=f"transition-{input_id}")


def _valid_artifact_payload() -> Mapping[str, AuthorityValue]:
    return {
        "artifact_kind": "kernel_ping.task_artifact",
        "artifact_version": 1,
        "source_prompt_id": "p-1",
        "title": "Executable task",
        "objective": "Prove the runner route",
        "requirements": ({"id": "r1", "description": "Do the thing"},),
        "completion_tests": (
            {
                "id": "t1",
                "description": "Run the focused tests",
                "expected_result": "pass",
            },
        ),
    }


def _valid_observation(
    *,
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    input_id: str = "observe-success",
    marker: str | None = None,
    artifact_payload: Mapping[str, AuthorityValue] | None = None,
    run_id: str = "run-a",
    overrides: Mapping[str, AuthorityValue] | None = None,
) -> RunnerResultObserved:
    payload_run_id = run_id if run_id in state.runs else "run-a"
    run, activation = run_activation(state, payload_run_id)
    action = action_by_id(plan, SUCCESS_ACTION_ID)
    return RunnerResultObserved(
        input_id,
        run_id=run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=activation,
            plan_fingerprint=fingerprint,
            marker=marker or marker_for_action(plan, action),
            artifact_payload=artifact_payload or _valid_artifact_payload(),
            overrides=overrides or {},
        ),
        observed_at=None,
    )


def _observe_context(input_id: str = "observe-success") -> TransitionContext:
    return deterministic_context(
        transition_id=f"transition-{input_id}",
        work_item_id=f"work-{input_id}",
        activation_id=f"activation-{input_id}",
    )


def _assert_no_progress_mutations(decision: TransitionDecision) -> None:
    assert FORBIDDEN_PROGRESS_MUTATIONS.isdisjoint(mutation_kinds(decision))


def test_valid_taskmaster_observation_records_evidence_artifact_and_route() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = _admit_select_enqueue_claim(plan, fingerprint)
    run, activation = run_activation(state, "run-a")
    action = action_by_id(plan, SUCCESS_ACTION_ID)
    observation = _valid_observation(state=state, plan=plan, fingerprint=fingerprint)

    decision = decide(state, observation, _observe_context())

    assert decision.accepted is True
    assert decision.input_family == "workflow_observation"
    assert decision.expected_plan_fingerprint == fingerprint
    assert decision.expected_run_generations == {run.run_ref.run_id: 0}
    assert decision.expected_run_fencing_tokens == {run.run_ref.run_id: "fence-a"}
    assert decision.expected_activation_generations == {activation.activation_id: 1}
    assert {
        "mutation.record_runner_observation",
        "mutation.record_artifact",
        "mutation.route_activation",
        "mutation.create_work_item",
        "mutation.create_activation",
    } <= set(mutation_kinds(decision))

    after = apply(state, decision)

    routed_work_id = "work-observe-success"
    routed_activation_id = "activation-observe-success"
    assert set(after.runner_observations) == {"transition-observe-success:observation"}
    assert set(after.artifacts) == {"transition-observe-success:artifact"}
    assert routed_work_id in after.work_items
    assert routed_activation_id in after.activations
    routed_work = after.work_items[routed_work_id]
    routed_activation = after.activations[routed_activation_id]
    assert routed_work.queue_family_id == action.emitted_queue_family_id
    assert routed_work.payload == _valid_artifact_payload()
    assert routed_activation.work_item_id == routed_work_id
    assert routed_activation.graph_node_id == action.target_graph_node_id
    assert routed_activation.stage_kind_id == action.target_stage_kind_id
    assert routed_activation.runner_binding_id == action.runner_binding_id


@pytest.mark.parametrize(
    ("field_name", "expected_reason"),
    (
        ("title", "invalid_artifact_payload"),
        ("objective", "invalid_artifact_payload"),
        ("requirements", "invalid_artifact_payload"),
        ("completion_tests", "invalid_artifact_payload"),
    ),
)
def test_missing_required_artifact_fields_are_refused(
    field_name: str,
    expected_reason: str,
) -> None:
    plan, fingerprint = compile_kernel_ping()
    state = _admit_select_enqueue_claim(plan, fingerprint)
    payload = dict(_valid_artifact_payload())
    del payload[field_name]
    observation = _valid_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        input_id=f"observe-missing-{field_name}",
        artifact_payload=cast(Mapping[str, AuthorityValue], payload),
    )

    decision = decide(state, observation, _observe_context(observation.input_id))

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == expected_reason
    _assert_no_progress_mutations(decision)
    assert apply(state, decision).work_items == state.work_items
    assert apply(state, decision).activations == state.activations


@pytest.mark.parametrize(
    ("payload_change", "input_id"),
    (
        ({"completion_tests": ()}, "observe-empty-tests"),
        ({"artifact_kind": "wrong.kind"}, "observe-wrong-kind"),
        ({"extra": "not declared"}, "observe-extra-property"),
    ),
)
def test_invalid_artifact_payloads_are_refused_without_route(
    payload_change: Mapping[str, AuthorityValue],
    input_id: str,
) -> None:
    plan, fingerprint = compile_kernel_ping()
    state = _admit_select_enqueue_claim(plan, fingerprint)
    payload = {**_valid_artifact_payload(), **payload_change}
    observation = _valid_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        input_id=input_id,
        artifact_payload=payload,
    )

    decision = decide(state, observation, _observe_context(input_id))
    after = apply(state, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_artifact_payload"
    _assert_no_progress_mutations(decision)
    assert after.work_items == state.work_items
    assert after.activations == state.activations
    assert after.artifacts == state.artifacts


@pytest.mark.parametrize(
    ("input_id", "run_id", "overrides"),
    (
        ("observe-wrong-plan", "run-a", {"plan_fingerprint": "sha256:wrong"}),
        ("observe-wrong-run", "missing-run", {}),
        ("observe-wrong-claim", "run-a", {"claim_id": "wrong-claim"}),
        ("observe-stale-generation", "run-a", {"generation": 1}),
        ("observe-wrong-fence", "run-a", {"fencing_token": "wrong-fence"}),
        ("observe-wrong-stage", "run-a", {"stage_kind_id": "wrong-stage"}),
        ("observe-wrong-node", "run-a", {"graph_node_id": "wrong-node"}),
        ("observe-wrong-marker", "run-a", {"marker": "UNDECLARED"}),
    ),
)
def test_invalid_observation_authority_is_refused_without_progress(
    input_id: str,
    run_id: str,
    overrides: Mapping[str, AuthorityValue],
) -> None:
    plan, fingerprint = compile_kernel_ping()
    state = _admit_select_enqueue_claim(plan, fingerprint)
    observation = _valid_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        input_id=input_id,
        run_id=run_id,
        overrides=overrides,
    )

    decision = decide(state, observation, _observe_context(input_id))
    after = apply(state, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    _assert_no_progress_mutations(decision)
    assert after.work_items == state.work_items
    assert after.activations == state.activations
    assert after.artifacts == state.artifacts
    assert after.runner_observations == state.runner_observations


def test_kernel_source_omits_hosted_workflow_literals() -> None:
    kernel_root = Path(__file__).resolve().parents[2] / "src" / "millrace" / "kernel"
    forbidden = (
        "kernel_ping",
        "Taskmaster",
        "Worker",
        "TASK_COMPLETE",
        "craft",
        "prompt",
        "task_artifact",
    )
    matches: list[tuple[Path, str]] = []
    for path in sorted(kernel_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for literal in forbidden:
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_]){re.escape(literal)}(?![A-Za-z0-9_])"
            )
            if pattern.search(text):
                matches.append((path.relative_to(kernel_root), literal))

    assert matches == []
