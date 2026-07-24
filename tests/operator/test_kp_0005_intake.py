from __future__ import annotations

from dataclasses import replace

import pytest

from millrace.compiler import compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.ids import QueueFamilyId
from millrace.contracts.state import AdmittedPlan, RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    EnqueueWork,
    InitializeWorkspace,
    SelectDefaultPlan,
    TransitionInput,
)
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.operator import (
    OperatorEnqueueInput,
    OperatorInputError,
    build_enqueue_work,
)
from millrace.testing import deterministic_context
from millrace.workflows import kernel_ping
from support import kernel_ping as kernel_ping_support


def _compile_kernel_ping() -> tuple[SelectedCompiledPlan, str]:
    result = compile_workflow(kernel_ping.workflow_source())
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def _compile_no_pause_kernel_ping() -> tuple[SelectedCompiledPlan, str]:
    result = compile_workflow(kernel_ping_support.no_pause_workflow_source())
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def _admit_plan(
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    suffix: str,
    select_default: bool,
) -> RuntimeState:
    inputs: list[TransitionInput] = [
        AdmitPlan(
            f"admit-{suffix}",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        )
    ]
    if select_default:
        inputs.append(SelectDefaultPlan(f"select-{suffix}", fingerprint))
    for transition_input in inputs:
        state = apply(
            state,
            decide(
                state,
                transition_input,
                deterministic_context(
                    transition_id=f"transition-{transition_input.input_id}",
                ),
            ),
        )
    return state


def _default_state() -> tuple[RuntimeState, str]:
    plan, fingerprint = _compile_kernel_ping()
    state = apply(
        empty_runtime_state(),
        decide(
            empty_runtime_state(),
            InitializeWorkspace("init"),
            deterministic_context(transition_id="transition-init"),
        ),
    )
    return (
        _admit_plan(
            state,
            plan,
            fingerprint,
            suffix="kernel-ping",
            select_default=True,
        ),
        fingerprint,
    )


def _assert_operator_error(
    reason: str,
    exc_info: pytest.ExceptionInfo[BaseException],
) -> None:
    assert isinstance(exc_info.value, OperatorInputError)
    assert exc_info.value.reason == reason


@pytest.mark.parametrize(
    ("operator_input", "expected_reason"),
    (
        (
            OperatorEnqueueInput(
                input_id="",
                queue_family_id="prompt",
                payload={"body": "x"},
            ),
            "empty_input_id",
        ),
        (
            OperatorEnqueueInput(
                input_id=" \t ",
                queue_family_id="prompt",
                payload={"body": "x"},
            ),
            "empty_input_id",
        ),
        (
            OperatorEnqueueInput(
                input_id="enqueue",
                queue_family_id="",
                payload={"body": "x"},
            ),
            "empty_queue_family_id",
        ),
        (
            OperatorEnqueueInput(
                input_id="enqueue",
                queue_family_id=" \t ",
                payload={"body": "x"},
            ),
            "empty_queue_family_id",
        ),
        (
            OperatorEnqueueInput(
                input_id="enqueue",
                queue_family_id="prompt",
                payload=("not", "a mapping"),
            ),
            "invalid_payload",
        ),
    ),
)
def test_build_enqueue_work_rejects_operator_shape_errors(
    operator_input: OperatorEnqueueInput,
    expected_reason: str,
) -> None:
    state, _fingerprint = _default_state()

    with pytest.raises(OperatorInputError) as exc_info:
        build_enqueue_work(state, operator_input)

    _assert_operator_error(expected_reason, exc_info)


def test_build_enqueue_work_requires_default_plan() -> None:
    with pytest.raises(OperatorInputError) as exc_info:
        build_enqueue_work(
            empty_runtime_state(),
            OperatorEnqueueInput(
                input_id="enqueue",
                queue_family_id="prompt",
                payload={"body": "x"},
            ),
        )

    _assert_operator_error("missing_default_plan", exc_info)


def test_build_enqueue_work_uses_fingerprint_only_as_default_plan_guard() -> None:
    plan_a, fingerprint_a = _compile_kernel_ping()
    plan_b, fingerprint_b = _compile_no_pause_kernel_ping()
    state = apply(
        empty_runtime_state(),
        decide(
            empty_runtime_state(),
            InitializeWorkspace("init"),
            deterministic_context(transition_id="transition-init"),
        ),
    )
    state = _admit_plan(
        state,
        plan_a,
        fingerprint_a,
        suffix="a",
        select_default=True,
    )
    state = _admit_plan(
        state,
        plan_b,
        fingerprint_b,
        suffix="b",
        select_default=False,
    )

    with pytest.raises(OperatorInputError) as exc_info:
        build_enqueue_work(
            state,
            OperatorEnqueueInput(
                input_id="enqueue",
                queue_family_id="prompt",
                payload={"body": "x"},
                plan_fingerprint=fingerprint_b,
            ),
        )

    _assert_operator_error("plan_fingerprint_mismatch", exc_info)
    assert state.default_plan_ref is not None
    assert state.default_plan_ref.authority_fingerprint == fingerprint_a


@pytest.mark.parametrize(
    ("queue_family_id", "expected_reason"),
    (
        ("unknown", "unknown_queue_family"),
        ("task_artifact", "queue_family_not_external"),
    ),
)
def test_build_enqueue_work_rejects_unavailable_queue_families(
    queue_family_id: str,
    expected_reason: str,
) -> None:
    state, _fingerprint = _default_state()

    with pytest.raises(OperatorInputError) as exc_info:
        build_enqueue_work(
            state,
            OperatorEnqueueInput(
                input_id="enqueue",
                queue_family_id=queue_family_id,
                payload={"body": "x"},
            ),
        )

    _assert_operator_error(expected_reason, exc_info)


def test_build_enqueue_work_rejects_missing_external_enqueue_route() -> None:
    state, fingerprint = _default_state()
    admitted = state.admitted_plans[fingerprint]
    state = replace(
        state,
        admitted_plans={
            **state.admitted_plans,
            fingerprint: AdmittedPlan(
                plan_ref=admitted.plan_ref,
                selected_plan=admitted.selected_plan,
                external_enqueue_routes={},
            ),
        },
    )

    with pytest.raises(OperatorInputError) as exc_info:
        build_enqueue_work(
            state,
            OperatorEnqueueInput(
                input_id="enqueue",
                queue_family_id="prompt",
                payload={"body": "x"},
            ),
        )

    _assert_operator_error("missing_external_enqueue_route", exc_info)


def test_build_enqueue_work_returns_generic_enqueue_transition_input() -> None:
    state, fingerprint = _default_state()

    enqueue = build_enqueue_work(
        state,
        OperatorEnqueueInput(
            input_id="operator-enqueue",
            queue_family_id="prompt",
            payload={"body": "build the operator proof"},
            plan_fingerprint=fingerprint,
        ),
    )

    assert isinstance(enqueue, EnqueueWork)
    assert enqueue.input_id == "operator-enqueue"
    assert enqueue.queue_family_id == QueueFamilyId("prompt")
    assert enqueue.payload == {"body": "build the operator proof"}


def test_returned_enqueue_work_runs_through_existing_kernel_decide_apply() -> None:
    state, _fingerprint = _default_state()
    enqueue = build_enqueue_work(
        state,
        OperatorEnqueueInput(
            input_id="operator-enqueue",
            queue_family_id="prompt",
            payload={"body": "route through kernel only"},
        ),
    )

    decision = decide(
        state,
        enqueue,
        deterministic_context(
            transition_id="transition-operator-enqueue",
            work_item_id="work-operator",
            activation_id="activation-operator",
        ),
    )
    after = apply(state, decision)

    assert decision.accepted is True
    assert set(after.work_items) == {"work-operator"}
    work_item = after.work_items["work-operator"]
    assert work_item.queue_family_id == QueueFamilyId("prompt")
    assert work_item.payload == {"body": "route through kernel only"}
    assert after.activations["activation-operator"].work_item_id == "work-operator"
