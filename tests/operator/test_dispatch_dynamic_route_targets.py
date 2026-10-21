"""Dispatch projection must accept dynamic route activations the kernel accepted.

A terminal route action can select its target through
``dynamic_target_selector["targets"]``. The kernel accepts that route and creates
an activation on the selected node. Dispatch projection must then project the
claimed run for that activation. Every case here drives real kernel transitions
over a variant of the generic admission fixture in which the dynamic target is
reachable only through the selector.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from typing import cast

import pytest

from millrace.contracts import SelectedCompiledPlan
from millrace.contracts.ids import QueueFamilyId, RunnerBindingId
from millrace.contracts.runner import RunnerDispatchEnvelope
from millrace.contracts.runtime_failure_history import selected_activation_targets
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    InitializeWorkspace,
    RunnerResultObserved,
    SelectDefaultPlan,
    TransitionContext,
    TransitionInput,
)
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.operator import dispatch
from millrace.operator.dispatch import (
    DispatchProjectionError,
    build_dispatch_envelope_for_run,
)
from millrace.testing import (
    deterministic_context,
    fake_completed_runner_observation_state,
    fake_runner_observation_payload,
    fake_runner_session_state,
)
from support import generic_admission, generic_fanout

DYNAMIC_QUEUE_ID = "dynamic"
DYNAMIC_NODE_ID = "admission.dynamic.start"
DYNAMIC_TARGET = "dynamic"
OTHER_RUNNER_ID = "admission.other_runner"
ABSENT_NODE_ID = "admission.absent.start"

PARENT_WORK_ID = "work-parent"
PARENT_ACTIVATION_ID = "activation-parent"
PARENT_RUN_ID = "run-parent"
DYNAMIC_WORK_ID = "work-dynamic"
DYNAMIC_ACTIVATION_ID = "activation-dynamic"
DYNAMIC_RUN_ID = "run-dynamic"

# The route target the kernel accepts through the dynamic selector, as plain values.
DYNAMIC_TARGET_TUPLE = (
    DYNAMIC_QUEUE_ID,
    generic_admission.CHILD_STAGE_ID,
    DYNAMIC_NODE_ID,
    generic_admission.RUNNER_ID,
)


def _dynamic_variant_source() -> dict[str, object]:
    """Generic admission fixture where the dynamic child target is selector-only."""
    source = deepcopy(generic_admission.source())
    graph = cast(list[dict[str, object]], source["graphs"])[0]
    graph["node_ids"] = (
        generic_admission.PARENT_NODE_ID,
        generic_admission.CHILD_NODE_ID,
        generic_admission.RECOVERY_NODE_ID,
        DYNAMIC_NODE_ID,
    )
    source["queue_families"] = [
        *cast(list[dict[str, object]], source["queue_families"]),
        {
            "id": DYNAMIC_QUEUE_ID,
            "external_enqueue": False,
            "presentation": {"display_name": "Dynamic"},
        },
    ]
    stages = cast(list[dict[str, object]], source["stage_kinds"])
    parent, child = stages[0], stages[1]
    parent["output_queue_family_ids"] = ("parent", "child", DYNAMIC_QUEUE_ID)
    child["input_queue_family_ids"] = ("child", DYNAMIC_QUEUE_ID)
    actions = cast(list[dict[str, object]], source["terminal_actions"])
    action = next(
        item
        for item in actions
        if item["id"] == generic_admission.DYNAMIC_ROUTE_ACTION_ID
    )
    selector = cast(dict[str, object], action["dynamic_target_selector"])
    selector["targets"] = {
        DYNAMIC_TARGET: {
            "target_stage_kind_id": generic_admission.CHILD_STAGE_ID,
            "target_graph_node_id": DYNAMIC_NODE_ID,
            "emitted_queue_family_id": DYNAMIC_QUEUE_ID,
            "runner_binding_id": generic_admission.RUNNER_ID,
        }
    }
    return source


def _variant() -> tuple[SelectedCompiledPlan, str]:
    return generic_admission.compile_plan(_dynamic_variant_source())


def _context(
    name: str,
    *,
    work_item_id: str = "work-unused",
    activation_id: str = "activation-unused",
    run_id: str = "run-unused",
) -> TransitionContext:
    return deterministic_context(
        transition_id=f"transition-{name}",
        work_item_id=work_item_id,
        activation_id=activation_id,
        run_id=run_id,
        claim_id=f"claim-{name}",
        fencing_token=f"fence-{name}",
    )


def _accept(
    state: RuntimeState,
    transition_input: TransitionInput,
    transition_context: TransitionContext,
) -> RuntimeState:
    """Apply one accepted kernel transition, completing runner observations first."""
    if isinstance(transition_input, RunnerResultObserved):
        state, transition_input = fake_completed_runner_observation_state(
            state=state,
            observation=transition_input,
        )
    decision = decide(state, transition_input, transition_context)
    assert decision.accepted is True, decision.refusal
    return apply(state, decision)


def _parent_claimed_state() -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    plan, fingerprint = _variant()
    state = empty_runtime_state()
    state = _accept(
        state, InitializeWorkspace("init-dynamic"), _context("init-dynamic")
    )
    state = _accept(
        state,
        AdmitPlan(
            "admit-dynamic",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        _context("admit-dynamic"),
    )
    state = _accept(
        state,
        SelectDefaultPlan("select-dynamic", authority_fingerprint=fingerprint),
        _context("select-dynamic"),
    )
    state = _accept(
        state,
        EnqueueWork(
            "enqueue-parent",
            queue_family_id=QueueFamilyId("parent"),
            payload=generic_fanout.packet_payload(),
        ),
        _context(
            "enqueue-parent",
            work_item_id=PARENT_WORK_ID,
            activation_id=PARENT_ACTIVATION_ID,
        ),
    )
    state = _accept(
        state,
        ClaimWork("claim-parent", activation_id=PARENT_ACTIVATION_ID),
        _context(
            "claim-parent",
            activation_id=PARENT_ACTIVATION_ID,
            run_id=PARENT_RUN_ID,
        ),
    )
    return state, plan, fingerprint


def _dynamic_state() -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    """Observe the parent through the dynamic selector, then claim the child."""
    state, plan, fingerprint = _parent_claimed_state()
    action = next(
        item
        for item in plan.terminal_actions
        if str(item.id) == generic_admission.DYNAMIC_ROUTE_ACTION_ID
    )
    marker = next(
        outcome.marker
        for outcome in plan.terminal_outcomes
        if outcome.id == action.outcome_id
    )
    parent_run = state.runs[PARENT_RUN_ID]
    parent_activation = state.activations[parent_run.activation_id]
    observation = RunnerResultObserved(
        "observe-parent",
        run_id=PARENT_RUN_ID,
        payload=fake_runner_observation_payload(
            run=parent_run,
            activation=parent_activation,
            plan_fingerprint=fingerprint,
            marker=marker,
            artifact_payload=generic_fanout.packet_payload(),
            observation_payload_overrides={"target": DYNAMIC_TARGET},
        ),
        observed_at=None,
    )
    state = _accept(
        state,
        observation,
        _context(
            "observe-parent",
            work_item_id=DYNAMIC_WORK_ID,
            activation_id=DYNAMIC_ACTIVATION_ID,
        ),
    )
    state = _accept(
        state,
        ClaimWork("claim-dynamic", activation_id=DYNAMIC_ACTIVATION_ID),
        _context(
            "claim-dynamic",
            activation_id=DYNAMIC_ACTIVATION_ID,
            run_id=DYNAMIC_RUN_ID,
        ),
    )
    return state, plan, fingerprint


def _project(state: RuntimeState, run_id: str) -> RunnerDispatchEnvelope:
    return build_dispatch_envelope_for_run(
        state=fake_runner_session_state(state=state, run_id=run_id),
        run_id=run_id,
    )


def _plain(target: tuple[object, ...]) -> tuple[object, ...]:
    return tuple(getattr(part, "value", part) for part in target)


def test_selector_target_is_visible_to_kernel_and_dispatch() -> None:
    """The selector target is visible to both kernel authority and dispatch.

    Recorded RED before the phase-8 repair (dispatch omitted selector targets);
    both inventories now share the typed contracts normalization.
    """
    plan, _fingerprint = _variant()
    kernel_targets = {_plain(target) for target in selected_activation_targets(plan)}
    dispatch_targets = {
        _plain(target) for target in dispatch._selected_route_targets(plan)
    }
    assert DYNAMIC_TARGET_TUPLE in kernel_targets
    assert DYNAMIC_TARGET_TUPLE in dispatch_targets


def test_parent_run_projects_before_the_dynamic_route() -> None:
    state, _plan, _fingerprint = _parent_claimed_state()
    envelope = _project(state, PARENT_RUN_ID)
    assert envelope.run_id == PARENT_RUN_ID
    assert envelope.activation_id == PARENT_ACTIVATION_ID


def test_dispatch_projects_run_for_kernel_accepted_dynamic_route() -> None:
    state, _plan, _fingerprint = _dynamic_state()
    try:
        envelope = _project(state, DYNAMIC_RUN_ID)
    except DispatchProjectionError as error:
        pytest.fail(
            f"dispatch refused the kernel-accepted dynamic activation: {error.reason}"
        )
    assert envelope.run_id == DYNAMIC_RUN_ID
    assert envelope.activation_id == DYNAMIC_ACTIVATION_ID
    assert envelope.work_item_id == DYNAMIC_WORK_ID


def _drifted_dynamic_state(
    *,
    activation_changes: Mapping[str, object],
    run_changes: Mapping[str, object],
) -> RuntimeState:
    state, _plan, _fingerprint = _dynamic_state()
    run = state.runs[DYNAMIC_RUN_ID]
    activation = state.activations[run.activation_id]
    return replace(
        state,
        runs={
            **state.runs,
            DYNAMIC_RUN_ID: replace(run, **run_changes),
        },
        activations={
            **state.activations,
            run.activation_id: replace(activation, **activation_changes),
        },
    )


@pytest.mark.parametrize(
    ("activation_changes", "run_changes", "expected_reason"),
    [
        pytest.param(
            {"runner_binding_id": RunnerBindingId(OTHER_RUNNER_ID)},
            {},
            "run_activation_drift",
            id="activation-runner-binding",
        ),
        pytest.param(
            {"queue_family_id": QueueFamilyId("child")},
            {},
            "run_activation_drift",
            id="activation-queue-family",
        ),
        pytest.param(
            {"graph_node_id": ABSENT_NODE_ID},
            {},
            "graph_node_missing",
            id="activation-graph-node",
        ),
        pytest.param(
            {"runner_binding_id": RunnerBindingId(OTHER_RUNNER_ID)},
            {"runner_binding_id": RunnerBindingId(OTHER_RUNNER_ID)},
            "graph_stage_runner_drift",
            id="run-runner-binding",
        ),
    ],
)
def test_dispatch_refuses_drifted_dynamic_run_with_precise_reason(
    activation_changes: Mapping[str, object],
    run_changes: Mapping[str, object],
    expected_reason: str,
) -> None:
    state = _drifted_dynamic_state(
        activation_changes=activation_changes,
        run_changes=run_changes,
    )
    with pytest.raises(DispatchProjectionError) as info:
        _project(state, DYNAMIC_RUN_ID)
    assert info.value.reason == expected_reason
