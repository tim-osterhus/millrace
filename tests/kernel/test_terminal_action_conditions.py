from __future__ import annotations

from copy import deepcopy
from typing import cast

from kernel.kernel_ping_scenarios import admit_select_enqueue_two_and_claim_first
from millrace.contracts import SelectedCompiledPlan
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.kernel import apply
from millrace.testing import decide_with_fake_runner_completion as decide
from millrace.workflows import kernel_ping
from support.kernel_ping import (
    compile_kernel_ping,
    kernel_ping_context,
    mutation_kinds,
    runner_observation,
    task_artifact_payload,
)


def _route_action_source(*, conditions: object) -> dict[str, object]:
    source = deepcopy(kernel_ping.WORKFLOW_SOURCE)
    actions = cast(list[dict[str, object]], source["terminal_actions"])
    action = next(
        item
        for item in actions
        if item["id"] == "kernel_ping.route_taskmaster_success"
    )
    action["artifact_field_conditions"] = conditions
    return source


def _block_action_source(*, conditions: object) -> dict[str, object]:
    source = deepcopy(kernel_ping.WORKFLOW_SOURCE)
    actions = cast(list[dict[str, object]], source["terminal_actions"])
    action = next(
        item
        for item in actions
        if item["id"] == "kernel_ping.pause_taskmaster_blocked"
    )
    action.update(
        {
            "kind": "block_work_item",
            "artifact_schema_id": "kernel_ping.task_artifact",
            "artifact_field_conditions": conditions,
        }
    )
    return source


def _condition_refusal_decision(
    *,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    state,
    action_id: str,
    input_id: str,
    artifact_payload: dict[str, AuthorityValue],
):
    return decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster-a",
            action_id=action_id,
            input_id=input_id,
            artifact_payload=artifact_payload,
        ),
        kernel_ping_context(input_id),
    )


def test_condition_mismatch_refuses_route_without_progress_mutations() -> None:
    plan, fingerprint = compile_kernel_ping(
        _route_action_source(conditions={"title": "expected title"})
    )
    state = admit_select_enqueue_two_and_claim_first(plan, fingerprint)
    decision = _condition_refusal_decision(
        plan=plan,
        fingerprint=fingerprint,
        state=state,
        action_id="kernel_ping.route_taskmaster_success",
        input_id="observe-condition-route-mismatch",
        artifact_payload=cast(
            dict[str, AuthorityValue],
            {**task_artifact_payload(), "title": "actual title"},
        ),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_artifact_payload"
    assert not {
        "mutation.record_artifact",
        "mutation.route_activation",
        "mutation.create_work_item",
        "mutation.create_activation",
    }.intersection(mutation_kinds(decision))
    after = apply(state, decision)
    assert after.work_items == state.work_items
    assert after.activations == state.activations
    assert after.artifacts == state.artifacts


def test_condition_mismatch_refuses_static_block_without_close() -> None:
    plan, fingerprint = compile_kernel_ping(
        _block_action_source(conditions={"title": "expected title"})
    )
    state = admit_select_enqueue_two_and_claim_first(plan, fingerprint)
    decision = _condition_refusal_decision(
        plan=plan,
        fingerprint=fingerprint,
        state=state,
        action_id="kernel_ping.pause_taskmaster_blocked",
        input_id="observe-condition-block-mismatch",
        artifact_payload=cast(
            dict[str, AuthorityValue],
            {**task_artifact_payload(), "title": "actual title"},
        ),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_artifact_payload"
    assert "mutation.close_work_item" not in mutation_kinds(decision)
    after = apply(state, decision)
    assert after.work_items == state.work_items
    assert after.activations == state.activations
    assert after.artifacts == state.artifacts


def test_condition_comparison_is_type_strict() -> None:
    source = _route_action_source(conditions={"artifact_version": True})
    schemas = cast(list[dict[str, object]], source["artifact_schemas"])
    schema_record = next(
        item
        for item in schemas
        if item["id"] == "kernel_ping.task_artifact"
    )
    schema = cast(dict[str, object], schema_record["schema"])
    properties = cast(dict[str, object], schema["properties"])
    properties["artifact_version"] = {"enum": (1, True)}
    schema["required"] = (
        *cast(tuple[str, ...], schema["required"]),
        "artifact_version",
    )

    plan, fingerprint = compile_kernel_ping(source)
    state = admit_select_enqueue_two_and_claim_first(plan, fingerprint)
    decision = _condition_refusal_decision(
        plan=plan,
        fingerprint=fingerprint,
        state=state,
        action_id="kernel_ping.route_taskmaster_success",
        input_id="observe-condition-type-mismatch",
        artifact_payload=cast(dict[str, AuthorityValue], task_artifact_payload()),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_artifact_payload"
