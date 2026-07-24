"""Shared kernel_ping setup primitives for KP-0001 tests."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import cast

from millrace.compiler import compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.compiled_plan import (
    AuthorityValue,
    SelectedCompiledPlan,
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
from millrace.workflows import kernel_ping

UNSELECTED_CATALOG_ID = "kernel_ping.unselected.catalog_entry"


def workflow_source_with_unselected_catalog() -> dict[str, object]:
    source = kernel_ping.workflow_source()
    source["unselected_catalog"] = (
        {
            "id": UNSELECTED_CATALOG_ID,
            "kind": "runner_binding_catalog_entry",
            "adapter_kind": "not_selected",
            "catalog_payload": {
                "contract_version": 1,
                "stage_kind_ids": ("kernel_ping.unused_stage",),
            },
        },
    )
    return source


def no_pause_workflow_source() -> dict[str, object]:
    source = deepcopy(kernel_ping.WORKFLOW_SOURCE)
    workflow = cast(dict[str, object], source["workflow"])
    workflow["version"] = "0.1-no-pause"

    stage_kinds = cast(list[dict[str, object]], source["stage_kinds"])
    taskmaster_stage = next(
        stage for stage in stage_kinds if stage["id"] == "kernel_ping.taskmaster"
    )
    taskmaster_stage["artifact_schema_ids"] = (
        "kernel_ping.task_artifact",
        "kernel_ping.task_incident",
    )

    terminal_actions = cast(list[dict[str, object]], source["terminal_actions"])
    taskmaster_assets = (
        "kernel_ping.taskmaster_prompt",
        "kernel_ping.tdd_core",
        "kernel_ping.task_artifact_authoring",
    )
    worker_assets = ("kernel_ping.worker_prompt", "kernel_ping.tdd_core")
    for action in terminal_actions:
        if action["id"] == "kernel_ping.pause_taskmaster_blocked":
            action["kind"] = "route"
            action["target_stage_kind_id"] = "kernel_ping.worker"
            action["target_graph_node_id"] = "kernel_ping.worker.retry_from_blocked"
            action["emitted_queue_family_id"] = "task_artifact"
            action["artifact_schema_id"] = "kernel_ping.task_artifact"
            action["runner_binding_id"] = "kernel_ping.worker_runner"
            action["asset_ids"] = worker_assets
            action["payload_projection"] = {
                "kind": "source",
                "path": ("artifact_payload",),
            }
            action["presentation"] = {"display_name": "Retry Taskmaster block"}
        elif action["id"] == "kernel_ping.pause_worker_blocked":
            action["kind"] = "route"
            action["target_stage_kind_id"] = "kernel_ping.taskmaster"
            action["target_graph_node_id"] = "kernel_ping.taskmaster.retry_from_blocked"
            action["emitted_queue_family_id"] = "task_incident"
            action["artifact_schema_id"] = "kernel_ping.task_incident"
            action["runner_binding_id"] = "kernel_ping.taskmaster_runner"
            action["asset_ids"] = taskmaster_assets
            action["payload_projection"] = {
                "kind": "source",
                "path": ("artifact_payload",),
            }
            action["presentation"] = {"display_name": "Retry Worker block"}

    graphs = cast(list[dict[str, object]], source["graphs"])
    node_ids = list(cast(tuple[str, ...], graphs[0]["node_ids"]))
    node_ids.extend(
        (
            "kernel_ping.worker.retry_from_blocked",
            "kernel_ping.taskmaster.retry_from_blocked",
        )
    )
    graphs[0]["node_ids"] = tuple(node_ids)
    return source


def compile_kernel_ping(
    source: Mapping[str, object] | None = None,
) -> tuple[SelectedCompiledPlan, str]:
    result = compile_workflow(source or kernel_ping.workflow_source())
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def kernel_ping_context(input_id: str) -> TransitionContext:
    contexts = {
        "enqueue": deterministic_context(
            transition_id="transition-enqueue",
            work_item_id="work-prompt",
            activation_id="activation-taskmaster",
        ),
        "enqueue-a": deterministic_context(
            transition_id="transition-enqueue-a",
            work_item_id="work-prompt-a",
            activation_id="activation-taskmaster-a",
        ),
        "enqueue-b": deterministic_context(
            transition_id="transition-enqueue-b",
            work_item_id="work-prompt-b",
            activation_id="activation-taskmaster-b",
        ),
        "claim-taskmaster": deterministic_context(
            transition_id="transition-claim-taskmaster",
            run_id="run-taskmaster",
            claim_id="claim-taskmaster",
            fencing_token="fence-taskmaster",
        ),
        "claim-taskmaster-a": deterministic_context(
            transition_id="transition-claim-taskmaster-a",
            run_id="run-taskmaster-a",
            claim_id="claim-taskmaster-a",
            fencing_token="fence-taskmaster-a",
        ),
        "observe-taskmaster": deterministic_context(
            transition_id="transition-observe-taskmaster",
            work_item_id="work-task-artifact",
            activation_id="activation-worker",
        ),
        "observe-taskmaster-success": deterministic_context(
            transition_id="transition-observe-taskmaster-success",
            work_item_id="work-task-artifact",
            activation_id="activation-worker",
        ),
        "observe-taskmaster-blocked": deterministic_context(
            transition_id="transition-observe-taskmaster-blocked",
        ),
        "observe-taskmaster-no-pause": deterministic_context(
            transition_id="transition-observe-taskmaster-no-pause",
            work_item_id="work-no-pause-route",
            activation_id="activation-no-pause-route",
        ),
        "claim-worker": deterministic_context(
            transition_id="transition-claim-worker",
            run_id="run-worker",
            claim_id="claim-worker",
            fencing_token="fence-worker",
        ),
        "observe-worker": deterministic_context(
            transition_id="transition-observe-worker",
        ),
        "observe-worker-success": deterministic_context(
            transition_id="transition-observe-worker-success",
        ),
        "observe-worker-blocked": deterministic_context(
            transition_id="transition-observe-worker-blocked",
        ),
        "observe-needs-review": deterministic_context(
            transition_id="transition-observe-needs-review",
            work_item_id="work-review-incident",
            activation_id="activation-review-taskmaster",
        ),
    }
    return contexts.get(
        input_id,
        deterministic_context(transition_id=f"transition-{input_id}"),
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


def task_artifact_payload(
    *,
    source_prompt_id: str = "prompt-1",
    objective: str = "Prove the in-memory transition path",
) -> Mapping[str, AuthorityValue]:
    return cast(
        Mapping[str, AuthorityValue],
        {
            "artifact_kind": "kernel_ping.task_artifact",
            "artifact_version": 1,
            "source_prompt_id": source_prompt_id,
            "title": "Executable task",
            "objective": objective,
            "requirements": (
                {"id": "r1", "description": "Complete the proof"},
            ),
            "completion_tests": (
                {
                    "id": "t1",
                    "description": "Run the focused tests",
                    "expected_result": "pass",
                },
            ),
        },
    )


def task_incident_payload() -> Mapping[str, AuthorityValue]:
    return cast(
        Mapping[str, AuthorityValue],
        {
            "incident_kind": "kernel_ping.task_incident",
            "incident_version": 1,
            "source_prompt_id": "prompt-a",
            "source_task_artifact_id": "work-task-artifact",
            "worker_run_id": "run-worker",
            "reason": "insufficient_task_detail",
            "worker_summary": "The task needs a clearer command.",
            "missing_details": ("command",),
            "requested_taskmaster_action": "revise_task_artifact",
        },
    )


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
    observation_payload_overrides: Mapping[str, AuthorityValue] | None = None,
    overrides: Mapping[str, AuthorityValue] | None = None,
) -> RunnerResultObserved:
    run, activation = run_activation(state, run_id)
    action = action_by_id(plan, action_id)
    return RunnerResultObserved(
        input_id,
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=activation,
            plan_fingerprint=fingerprint,
            marker=marker or marker_for_action(plan, action),
            artifact_payload=artifact_payload,
            observation_payload_overrides=observation_payload_overrides,
            overrides=overrides,
        ),
        observed_at=None,
    )


def mutation_kinds(decision: TransitionDecision) -> tuple[str, ...]:
    return tuple(mutation.mutation_kind for mutation in decision.mutations)


__all__ = (
    "action_by_id",
    "apply_accepted_input",
    "compile_kernel_ping",
    "kernel_ping_context",
    "marker_for_action",
    "mutation_kinds",
    "run_activation",
    "runner_observation",
    "task_artifact_payload",
    "task_incident_payload",
)
