"""Reusable kernel_ping state scenarios for kernel tests."""

from __future__ import annotations

from collections.abc import Mapping

from millrace.contracts import QueueFamilyId, SelectedCompiledPlan
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    InitializeWorkspace,
    SelectDefaultPlan,
)
from millrace.kernel import empty_runtime_state
from support.kernel_ping import (
    apply_accepted_input,
    kernel_ping_context,
    runner_observation,
    task_artifact_payload,
)


def bootstrap_to_taskmaster_claim(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    prompt_id: str = "prompt-1",
    body: str = "Build the kernel_ping proof",
) -> RuntimeState:
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("init"),
        AdmitPlan("admit", selected_plan=plan, authority_fingerprint=fingerprint),
        SelectDefaultPlan("select", authority_fingerprint=fingerprint),
        EnqueueWork(
            "enqueue",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"prompt_id": prompt_id, "body": body},
        ),
        ClaimWork("claim-taskmaster", activation_id="activation-taskmaster"),
    ):
        state = apply_accepted_input(
            state,
            transition_input,
            kernel_ping_context(transition_input.input_id),
        )
    return state


def bootstrap_to_worker_claim(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    task_artifact: Mapping[str, AuthorityValue] | None = None,
) -> RuntimeState:
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id="kernel_ping.route_taskmaster_success",
            input_id="observe-taskmaster",
            artifact_payload=task_artifact
            or task_artifact_payload(
                objective="Prove the worker route",
            ),
        ),
        kernel_ping_context("observe-taskmaster"),
    )
    return apply_accepted_input(
        state,
        ClaimWork("claim-worker", activation_id="activation-worker"),
        kernel_ping_context("claim-worker"),
    )


def admit_select_enqueue_two_and_claim_first(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("init"),
        AdmitPlan("admit", selected_plan=plan, authority_fingerprint=fingerprint),
        SelectDefaultPlan("select", authority_fingerprint=fingerprint),
        EnqueueWork(
            "enqueue-a",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"prompt_id": "prompt-a", "body": "Build blocked proof A"},
        ),
        EnqueueWork(
            "enqueue-b",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"prompt_id": "prompt-b", "body": "Build blocked proof B"},
        ),
        ClaimWork("claim-taskmaster-a", activation_id="activation-taskmaster-a"),
    ):
        state = apply_accepted_input(
            state,
            transition_input,
            kernel_ping_context(transition_input.input_id),
        )
    return state


def bootstrap_two_prompt_state_to_worker_claim(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    state = admit_select_enqueue_two_and_claim_first(plan, fingerprint)
    state = apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster-a",
            action_id="kernel_ping.route_taskmaster_success",
            input_id="observe-taskmaster-success",
            artifact_payload=task_artifact_payload(
                source_prompt_id="prompt-a",
                objective="Prove blocked behavior",
            ),
        ),
        kernel_ping_context("observe-taskmaster-success"),
    )
    return apply_accepted_input(
        state,
        ClaimWork("claim-worker", activation_id="activation-worker"),
        kernel_ping_context("claim-worker"),
    )


__all__ = (
    "admit_select_enqueue_two_and_claim_first",
    "bootstrap_to_taskmaster_claim",
    "bootstrap_to_worker_claim",
    "bootstrap_two_prompt_state_to_worker_claim",
)
