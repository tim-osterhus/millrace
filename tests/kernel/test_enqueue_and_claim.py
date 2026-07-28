from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import QueueFamilyId, RunnerBindingId, SelectedCompiledPlan
from millrace.contracts.ids import StageKindId
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    InitializeWorkspace,
    RunnerResultObserved,
    SelectDefaultPlan,
)
from millrace.kernel import StateConcurrencyError, apply, empty_runtime_state
from millrace.operator import operator_status
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.sqlite import SQLiteRuntimeStore
from millrace.testing import decide_with_fake_runner_completion as decide
from millrace.testing import (
    deterministic_context,
    fake_runner_observation_payload,
    materialize_fake_runner_session_cas,
)
from millrace.workflows import kernel_ping
from support import kernel_ping as kernel_ping_support

_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def _compile_source(
    source: Mapping[str, object],
    *,
    selected_runner_policy: SelectedRunnerAdapterPolicy | None = None,
) -> tuple[SelectedCompiledPlan, str]:
    result = (
        compile_workflow(source)
        if selected_runner_policy is None
        else compile_workflow(source, selected_runner_policy=selected_runner_policy)
    )
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def _admitted_default_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    suffix: str,
) -> RuntimeState:
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace(f"init-{suffix}"),
        AdmitPlan(
            f"admit-{suffix}",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        SelectDefaultPlan(f"select-{suffix}", authority_fingerprint=fingerprint),
    ):
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


def _with_admitted_default(
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    suffix: str,
) -> RuntimeState:
    for transition_input in (
        AdmitPlan(
            f"admit-{suffix}",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        SelectDefaultPlan(f"select-{suffix}", authority_fingerprint=fingerprint),
    ):
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


def _no_lineage_source() -> dict[str, object]:
    return {
        "lineage_policy": "none",
        "workflow": {
            "id": "no_lineage_ping",
            "version": "0.1",
            "name": "No Lineage Ping",
            "compatibility_profile": None,
            "required_extensions": (),
        },
        "graphs": [
            {
                "id": "no_lineage_ping.graph",
                "node_ids": ("no_lineage_ping.worker.start",),
                "presentation": {},
            }
        ],
        "partitions": [
            {
                "id": "single",
                "kind": "plane",
                "presentation": {},
            }
        ],
        "queue_families": [
            {
                "id": "prompt",
                "external_enqueue": True,
                "presentation": {},
            }
        ],
        "external_enqueue_routes": [
            {
                "id": "no_lineage_ping.external_prompt",
                "queue_family_id": "prompt",
                "graph_node_id": "no_lineage_ping.worker.start",
                "stage_kind_id": "no_lineage_ping.worker",
                "runner_binding_id": "no_lineage_ping.fake_runner",
            }
        ],
        "artifact_schemas": [],
        "assets": [],
        "terminal_outcomes": [
            {
                "id": "no_lineage_ping.worker.done",
                "stage_kind_id": "no_lineage_ping.worker",
                "marker": "DONE",
                "presentation": {},
            }
        ],
        "stage_kinds": [
            {
                "id": "no_lineage_ping.worker",
                "partition_id": "single",
                "runner_binding_id": "no_lineage_ping.fake_runner",
                "input_queue_family_ids": ("prompt",),
                "output_queue_family_ids": (),
                "artifact_schema_ids": (),
                "asset_ids": (),
                "declared_outcome_ids": ("no_lineage_ping.worker.done",),
                "presentation": {},
            }
        ],
        "terminal_actions": [
            {
                "id": "no_lineage_ping.close",
                "stage_kind_id": "no_lineage_ping.worker",
                "outcome_id": "no_lineage_ping.worker.done",
                "kind": "close",
                "target_stage_kind_id": None,
                "target_graph_node_id": None,
                "emitted_queue_family_id": None,
                "artifact_schema_id": None,
                "runner_binding_id": None,
                "asset_ids": (),
                "payload_projection": None,
                "presentation": {},
            }
        ],
        "runner_bindings": [
            {
                "id": "no_lineage_ping.fake_runner",
                "adapter_kind": "fake_local",
                "stage_kind_ids": ("no_lineage_ping.worker",),
                "presentation": {},
            }
        ],
    }


def _persist_and_load_state(tmp_path: Path, state: RuntimeState) -> RuntimeState:
    db_path = tmp_path / "runtime.sqlite3"
    cas_root = tmp_path / "cas"
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        cas_store = ContentAddressedByteStore(cas_root)
        state = materialize_fake_runner_session_cas(
            state=state,
            cas_store=cas_store,
        )
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()
    store = SQLiteRuntimeStore.open(db_path)
    try:
        return store.load_runtime_state(ContentAddressedByteStore(cas_root))
    finally:
        store.close()


def test_enqueue_requires_admitted_default_plan_and_external_family() -> None:
    enqueue = EnqueueWork(
        "enqueue-without-default",
        queue_family_id=QueueFamilyId("prompt"),
        payload={"body": "make a proof"},
    )
    empty_state = empty_runtime_state()
    no_default_decision = decide(
        empty_state,
        enqueue,
        deterministic_context(transition_id="transition-no-default"),
    )
    assert no_default_decision.accepted is False
    after_no_default = apply(empty_state, no_default_decision)
    assert after_no_default.work_items == {}
    assert after_no_default.activations == {}

    plan, fingerprint = _compile_source(kernel_ping.workflow_source())
    state = _admitted_default_state(plan, fingerprint, suffix="a")
    internal_enqueue = EnqueueWork(
        "enqueue-internal",
        queue_family_id=QueueFamilyId("task_artifact"),
        payload={"body": "not an external item"},
    )
    internal_decision = decide(
        state,
        internal_enqueue,
        deterministic_context(transition_id="transition-internal"),
    )
    assert internal_decision.accepted is False
    after_internal = apply(state, internal_decision)
    assert after_internal.work_items == {}
    assert after_internal.activations == {}


def test_enqueue_records_receipt_and_creates_one_work_item_and_activation() -> None:
    plan, fingerprint = _compile_source(kernel_ping.workflow_source())
    selected_route = plan.external_enqueue_routes[0]
    state = _admitted_default_state(plan, fingerprint, suffix="a")
    selected_plan_ref = state.default_plan_ref
    assert selected_plan_ref is not None

    enqueue = EnqueueWork(
        "enqueue",
        queue_family_id=QueueFamilyId("prompt"),
        payload={"body": "build the narrow proof"},
    )
    decision = decide(
        state,
        enqueue,
        deterministic_context(
            transition_id="transition-enqueue",
            work_item_id="work-a",
            activation_id="activation-a",
        ),
    )
    assert decision.accepted is True
    after = apply(state, decision)

    assert "enqueue" in after.receipts
    assert set(after.work_items) == {"work-a"}
    assert set(after.activations) == {"activation-a"}

    work_item = after.work_items["work-a"]
    activation = after.activations["activation-a"]
    assert work_item.ref.plan_ref == selected_plan_ref
    assert work_item.queue_family_id == QueueFamilyId("prompt")
    assert activation.plan_ref == selected_plan_ref
    assert activation.work_item_id == work_item.ref.work_item_id
    assert activation.graph_node_id == selected_route.graph_node_id
    assert activation.stage_kind_id == selected_route.stage_kind_id
    assert activation.runner_binding_id == selected_route.runner_binding_id

    replay_decision = decide(
        after,
        enqueue,
        deterministic_context(
            transition_id="transition-replay",
            work_item_id="work-b",
            activation_id="activation-b",
        ),
    )
    assert replay_decision.disposition == "replayed"
    assert replay_decision.mutations == ()
    assert apply(after, replay_decision) == after

    conflict = EnqueueWork(
        "enqueue",
        queue_family_id=QueueFamilyId("prompt"),
        payload={"body": "changed"},
    )
    conflict_decision = decide(
        after,
        conflict,
        deterministic_context(transition_id="transition-conflict"),
    )
    after_conflict = apply(after, conflict_decision)
    assert conflict_decision.accepted is False
    assert after_conflict.work_items == after.work_items
    assert after_conflict.activations == after.activations
    assert after_conflict.runs == after.runs


def test_enqueue_replay_refuses_different_effective_plan_without_poisoning_original() -> None:  # noqa: E501
    plan_a, fingerprint_a = _compile_source(kernel_ping.workflow_source())
    plan_b, fingerprint_b = _compile_source(
        kernel_ping_support.no_pause_workflow_source()
    )
    payload = {"body": "same input cannot cross default plan authority"}
    state = _admitted_default_state(plan_a, fingerprint_a, suffix="a")

    enqueue_a = EnqueueWork(
        "same-input",
        queue_family_id=QueueFamilyId("prompt"),
        payload=payload,
    )
    accepted = decide(
        state,
        enqueue_a,
        deterministic_context(
            transition_id="transition-enqueue-a",
            work_item_id="work-a",
            activation_id="activation-a",
        ),
    )
    assert accepted.accepted is True
    after_a = apply(state, accepted)

    default_b = _with_admitted_default(
        after_a,
        plan_b,
        fingerprint_b,
        suffix="b",
    )
    enqueue_b = EnqueueWork(
        "same-input",
        queue_family_id=QueueFamilyId("prompt"),
        payload=payload,
    )
    refused_b = decide(
        default_b,
        enqueue_b,
        deterministic_context(
            transition_id="transition-enqueue-b",
            work_item_id="work-b",
            activation_id="activation-b",
        ),
    )
    assert refused_b.accepted is False
    assert refused_b.refusal is not None
    assert refused_b.refusal.reason == "idempotency_conflict"
    after_refused_b = apply(default_b, refused_b)
    assert after_refused_b.receipts == default_b.receipts
    assert after_refused_b.work_items == default_b.work_items
    assert after_refused_b.activations == default_b.activations

    default_a_again = _with_admitted_default(
        after_refused_b,
        plan_a,
        fingerprint_a,
        suffix="a-again",
    )
    replay_a = decide(
        default_a_again,
        enqueue_a,
        deterministic_context(
            transition_id="transition-replay-a",
            work_item_id="work-a-new",
            activation_id="activation-a-new",
        ),
    )
    assert replay_a.disposition == "replayed"
    assert replay_a.mutations == ()
    assert apply(default_a_again, replay_a) == default_a_again


def test_enqueue_replay_survives_claim_and_restart_without_ready_target(
    tmp_path: Path,
) -> None:
    plan, fingerprint = _compile_source(kernel_ping.workflow_source())
    state = _admitted_default_state(plan, fingerprint, suffix="a")
    enqueue = EnqueueWork(
        "enqueue-claim-replay",
        queue_family_id=QueueFamilyId("prompt"),
        payload={"body": "replay after claim and reload"},
    )
    state = apply(
        state,
        decide(
            state,
            enqueue,
            deterministic_context(
                transition_id="transition-enqueue-claim-replay",
                work_item_id="work-claim-replay",
                activation_id="activation-claim-replay",
            ),
        ),
    )
    state = apply(
        state,
        decide(
            state,
            ClaimWork("claim-before-replay", activation_id="activation-claim-replay"),
            deterministic_context(
                transition_id="transition-claim-before-replay",
                run_id="run-claim-replay",
                claim_id="claim-claim-replay",
                fencing_token="fence-claim-replay",
            ),
        ),
    )
    loaded = _persist_and_load_state(tmp_path, state)

    replay = decide(
        loaded,
        enqueue,
        deterministic_context(
            transition_id="transition-replay-after-reload",
            work_item_id="work-after-reload",
            activation_id="activation-after-reload",
        ),
    )

    assert replay.disposition == "replayed"
    assert replay.mutations == ()
    assert apply(loaded, replay) == loaded


@pytest.mark.parametrize(
    "corruption",
    (
        "missing_work",
        "missing_activation",
        "activation_to_work_mismatch",
        "work_created_by_input_mismatch",
        "activation_created_by_input_mismatch",
        "work_queue_family_mismatch",
        "activation_queue_family_mismatch",
        "activation_graph_node_mismatch",
        "activation_stage_kind_mismatch",
        "activation_runner_binding_mismatch",
        "work_plan_ref_mismatch",
        "activation_plan_ref_mismatch",
    ),
)
def test_enqueue_replay_refuses_corrupt_selected_route_links(
    corruption: str,
) -> None:
    plan_a, fingerprint_a = _compile_source(kernel_ping.workflow_source())
    plan_b, fingerprint_b = _compile_source(
        kernel_ping_support.no_pause_workflow_source()
    )
    state = _admitted_default_state(plan_a, fingerprint_a, suffix="a")
    enqueue = EnqueueWork(
        "enqueue-corrupt-target",
        queue_family_id=QueueFamilyId("prompt"),
        payload={"body": "detect corrupt target"},
    )
    state = apply(
        state,
        decide(
            state,
            enqueue,
            deterministic_context(
                transition_id="transition-enqueue-corrupt-target",
                work_item_id="work-corrupt-target",
                activation_id="activation-corrupt-target",
            ),
        ),
    )
    state = apply(
        state,
        decide(
            state,
            AdmitPlan(
                "admit-b-for-corruption",
                selected_plan=plan_b,
                authority_fingerprint=fingerprint_b,
            ),
            deterministic_context(transition_id="transition-admit-b-for-corruption"),
        ),
    )
    work_item = state.work_items["work-corrupt-target"]
    activation = state.activations["activation-corrupt-target"]
    plan_b_ref = state.admitted_plans[fingerprint_b].plan_ref

    if corruption == "missing_work":
        corrupted = replace(state, work_items={})
    elif corruption == "missing_activation":
        corrupted = replace(state, activations={})
    elif corruption == "activation_to_work_mismatch":
        corrupted = replace(
            state,
            activations={
                **state.activations,
                activation.activation_id: replace(
                    activation,
                    work_item_id="other-work",
                ),
            },
        )
    elif corruption == "work_created_by_input_mismatch":
        corrupted = replace(
            state,
            work_items={
                **state.work_items,
                work_item.ref.work_item_id: replace(
                    work_item,
                    created_by_input_id="other-input",
                ),
            },
        )
    elif corruption == "activation_created_by_input_mismatch":
        corrupted = replace(
            state,
            activations={
                **state.activations,
                activation.activation_id: replace(
                    activation,
                    created_by_input_id="other-input",
                ),
            },
        )
    elif corruption == "work_queue_family_mismatch":
        corrupted = replace(
            state,
            work_items={
                **state.work_items,
                work_item.ref.work_item_id: replace(
                    work_item,
                    queue_family_id=QueueFamilyId("task_artifact"),
                ),
            },
        )
    elif corruption == "activation_queue_family_mismatch":
        corrupted = replace(
            state,
            activations={
                **state.activations,
                activation.activation_id: replace(
                    activation,
                    queue_family_id=QueueFamilyId("task_artifact"),
                ),
            },
        )
    elif corruption == "activation_graph_node_mismatch":
        corrupted = replace(
            state,
            activations={
                **state.activations,
                activation.activation_id: replace(
                    activation,
                    graph_node_id="wrong.graph.node",
                ),
            },
        )
    elif corruption == "activation_stage_kind_mismatch":
        corrupted = replace(
            state,
            activations={
                **state.activations,
                activation.activation_id: replace(
                    activation,
                    stage_kind_id=StageKindId("wrong.stage"),
                ),
            },
        )
    elif corruption == "activation_runner_binding_mismatch":
        corrupted = replace(
            state,
            activations={
                **state.activations,
                activation.activation_id: replace(
                    activation,
                    runner_binding_id=RunnerBindingId("wrong.runner"),
                ),
            },
        )
    elif corruption == "work_plan_ref_mismatch":
        corrupted = replace(
            state,
            work_items={
                **state.work_items,
                work_item.ref.work_item_id: replace(
                    work_item,
                    ref=replace(work_item.ref, plan_ref=plan_b_ref),
                ),
            },
        )
    elif corruption == "activation_plan_ref_mismatch":
        corrupted = replace(
            state,
            activations={
                **state.activations,
                activation.activation_id: replace(
                    activation,
                    plan_ref=plan_b_ref,
                ),
            },
        )
    else:  # pragma: no cover - parametrization guard
        raise AssertionError(f"unhandled corruption case: {corruption}")

    replay = decide(
        corrupted,
        enqueue,
        deterministic_context(transition_id=f"transition-replay-{corruption}"),
    )

    assert replay.accepted is False
    assert replay.refusal is not None
    assert replay.refusal.reason == "enqueue_replay_target_invalid"


@pytest.mark.parametrize(
    ("route_patch", "expected_detail"),
    (
        (
            {"queue_family_id": QueueFamilyId("task_artifact")},
            "selected_route_authority_mismatch",
        ),
        (
            {"graph_node_id": "wrong.graph.node"},
            "selected_route_authority_mismatch",
        ),
        (
            {"stage_kind_id": StageKindId("wrong.stage")},
            "selected_route_authority_mismatch",
        ),
        (
            {"runner_binding_id": RunnerBindingId("wrong.runner")},
            "selected_route_authority_mismatch",
        ),
    ),
)
def test_enqueue_replay_refuses_same_plan_ref_corrupt_selected_route_target(
    route_patch: Mapping[str, object],
    expected_detail: str,
) -> None:
    plan, fingerprint = _compile_source(kernel_ping.workflow_source())
    state = _admitted_default_state(plan, fingerprint, suffix="route-corrupt")
    enqueue = EnqueueWork(
        "enqueue-corrupt-selected-route",
        queue_family_id=QueueFamilyId("prompt"),
        payload={"body": "detect selected route corruption"},
    )
    state = apply(
        state,
        decide(
            state,
            enqueue,
            deterministic_context(
                transition_id="transition-enqueue-corrupt-selected-route",
                work_item_id="work-corrupt-selected-route",
                activation_id="activation-corrupt-selected-route",
            ),
        ),
    )
    admitted = state.admitted_plans[fingerprint]
    route = admitted.external_enqueue_routes[QueueFamilyId("prompt")]
    corrupted_admitted = replace(
        admitted,
        external_enqueue_routes={
            **admitted.external_enqueue_routes,
            QueueFamilyId("prompt"): replace(route, **dict(route_patch)),
        },
    )
    corrupted = replace(
        state,
        admitted_plans={**state.admitted_plans, fingerprint: corrupted_admitted},
    )

    replay = decide(
        corrupted,
        enqueue,
        deterministic_context(transition_id="transition-replay-corrupt-selected-route"),
    )

    assert replay.accepted is False
    assert replay.refusal is not None
    assert replay.refusal.reason == "enqueue_replay_target_invalid"
    assert replay.refusal.detail == expected_detail


def test_enqueue_replay_refuses_coherent_route_under_wrong_selected_plan() -> None:
    plan, fingerprint = _compile_source(kernel_ping.workflow_source())
    alternate_source = kernel_ping.workflow_source()
    graph_records = cast(list[dict[str, object]], alternate_source["graphs"])
    graph_records[0]["node_ids"] = (
        *cast(tuple[str, ...], graph_records[0]["node_ids"]),
        "kernel_ping.taskmaster.alternate_start",
    )
    route_records = cast(
        list[dict[str, object]],
        alternate_source["external_enqueue_routes"],
    )
    route_records[0]["graph_node_id"] = "kernel_ping.taskmaster.alternate_start"
    alternate_plan, _alternate_fingerprint = _compile_source(alternate_source)
    alternate_route = alternate_plan.external_enqueue_routes[0]

    state = _admitted_default_state(plan, fingerprint, suffix="route-plan-corrupt")
    enqueue = EnqueueWork(
        "enqueue-coherent-wrong-plan-route",
        queue_family_id=QueueFamilyId("prompt"),
        payload={"body": "detect coherent selected plan drift"},
    )
    state = apply(
        state,
        decide(
            state,
            enqueue,
            deterministic_context(
                transition_id="transition-enqueue-coherent-wrong-plan-route",
                work_item_id="work-coherent-wrong-plan-route",
                activation_id="activation-coherent-wrong-plan-route",
            ),
        ),
    )
    admitted = state.admitted_plans[fingerprint]
    route = admitted.external_enqueue_routes[QueueFamilyId("prompt")]
    activation = state.activations["activation-coherent-wrong-plan-route"]
    corrupted_route = replace(
        route,
        graph_node_id=alternate_route.graph_node_id,
        stage_kind_id=alternate_route.stage_kind_id,
        runner_binding_id=alternate_route.runner_binding_id,
        payload_schema_id=alternate_route.payload_schema_id,
    )
    corrupted = replace(
        state,
        admitted_plans={
            **state.admitted_plans,
            fingerprint: replace(
                admitted,
                selected_plan=alternate_plan,
                external_enqueue_routes={
                    **admitted.external_enqueue_routes,
                    QueueFamilyId("prompt"): corrupted_route,
                },
            ),
        },
        activations={
            **state.activations,
            activation.activation_id: replace(
                activation,
                graph_node_id=alternate_route.graph_node_id,
                stage_kind_id=alternate_route.stage_kind_id,
                runner_binding_id=alternate_route.runner_binding_id,
            ),
        },
    )

    replay = decide(
        corrupted,
        enqueue,
        deterministic_context(
            transition_id="transition-replay-coherent-wrong-plan-route",
        ),
    )

    assert replay.accepted is False
    assert replay.refusal is not None
    assert replay.refusal.reason == "enqueue_replay_target_invalid"
    assert replay.refusal.detail == "selected_plan_authority_mismatch"


def test_refused_enqueue_input_id_is_idempotent_after_state_changes() -> None:
    plan, fingerprint = _compile_source(kernel_ping.workflow_source())
    enqueue = EnqueueWork(
        "refused-enqueue",
        queue_family_id=QueueFamilyId("prompt"),
        payload={"body": "wait for a default plan"},
    )

    state = empty_runtime_state()
    refused_decision = decide(
        state,
        enqueue,
        deterministic_context(transition_id="transition-refused"),
    )
    assert refused_decision.accepted is False
    assert refused_decision.refusal is not None
    assert refused_decision.refusal.reason == "missing_default_plan"

    refused_state = apply(state, refused_decision)
    assert "refused-enqueue" in refused_state.receipts
    original_receipt = refused_state.receipts["refused-enqueue"].receipt_ref

    later_state = _with_admitted_default(
        refused_state,
        plan,
        fingerprint,
        suffix="later",
    )
    replay_decision = decide(
        later_state,
        enqueue,
        deterministic_context(
            transition_id="transition-replay-refused",
            work_item_id="work-replay",
            activation_id="activation-replay",
        ),
    )
    assert replay_decision.accepted is False
    assert replay_decision.receipt_ref == original_receipt
    assert replay_decision.mutations == ()
    assert apply(later_state, replay_decision) == later_state

    conflict_decision = decide(
        later_state,
        EnqueueWork(
            "refused-enqueue",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"body": "changed payload"},
        ),
        deterministic_context(transition_id="transition-refused-conflict"),
    )
    assert conflict_decision.accepted is False
    assert conflict_decision.refusal is not None
    assert conflict_decision.refusal.reason == "idempotency_conflict"
    after_conflict = apply(later_state, conflict_decision)
    assert after_conflict.work_items == later_state.work_items
    assert after_conflict.activations == later_state.activations
    assert after_conflict.runs == later_state.runs


def test_no_lineage_workflow_runs_closes_persists_and_projects_status(
    tmp_path: Path,
) -> None:
    plan, fingerprint = _compile_source(
        _no_lineage_source(), selected_runner_policy=_CODEX_POLICY
    )
    assert plan.lineage_policy == "none"
    state = _admitted_default_state(plan, fingerprint, suffix="no-lineage")

    enqueue_decision = decide(
        state,
        EnqueueWork(
            "enqueue-no-lineage",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"body": "close without lineage"},
        ),
        deterministic_context(
            transition_id="transition-enqueue-no-lineage",
            work_item_id="work-no-lineage",
            activation_id="activation-no-lineage",
        ),
    )
    assert enqueue_decision.accepted is True
    state = apply(state, enqueue_decision)
    assert state.work_items["work-no-lineage"].lineage_id is None
    assert state.activations["activation-no-lineage"].lineage_id is None

    claim_decision = decide(
        state,
        ClaimWork("claim-no-lineage", activation_id="activation-no-lineage"),
        deterministic_context(
            transition_id="transition-claim-no-lineage",
            run_id="run-no-lineage",
            claim_id="claim-no-lineage",
            fencing_token="fence-no-lineage",
        ),
    )
    assert claim_decision.accepted is True
    state = apply(state, claim_decision)

    active_status = operator_status(state)
    assert len(active_status.active_runs) == 1
    assert active_status.active_runs[0].lineage_id is None

    run = state.runs["run-no-lineage"]
    activation = state.activations[run.activation_id]
    close_decision = decide(
        state,
        RunnerResultObserved(
            "observe-no-lineage",
            run_id="run-no-lineage",
            payload=fake_runner_observation_payload(
                run=run,
                activation=activation,
                plan_fingerprint=fingerprint,
                marker="DONE",
                artifact_payload={},
            ),
            observed_at=None,
        ),
        deterministic_context(transition_id="transition-observe-no-lineage"),
    )
    assert close_decision.accepted is True
    state = apply(state, close_decision)
    assert "work-no-lineage" in state.closed_work_items
    assert state.work_items["work-no-lineage"].lineage_id is None
    assert state.activations["activation-no-lineage"].lineage_id is None

    loaded = _persist_and_load_state(tmp_path, state)
    assert loaded == state
    loaded_status = operator_status(loaded)
    assert loaded_status.active_runs == ()
    assert loaded_status.queue_families[0].closed_count == 1
    assert loaded_status.queue_families[0].ready_count == 0
    assert loaded_status.queue_families[0].active_count == 0


def test_enqueue_apply_rechecks_default_plan_before_creating_new_work() -> None:
    plan_a, fingerprint_a = _compile_source(kernel_ping.workflow_source())
    plan_b, fingerprint_b = _compile_source(
        kernel_ping_support.no_pause_workflow_source()
    )
    state = _admitted_default_state(plan_a, fingerprint_a, suffix="a")

    stale_decision = decide(
        state,
        EnqueueWork(
            "enqueue",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"body": "new work under current default"},
        ),
        deterministic_context(
            transition_id="transition-enqueue",
            work_item_id="work-a",
            activation_id="activation-a",
        ),
    )
    assert stale_decision.accepted is True

    changed_default = _with_admitted_default(
        state,
        plan_b,
        fingerprint_b,
        suffix="b",
    )
    with pytest.raises(StateConcurrencyError):
        apply(changed_default, stale_decision)
    assert changed_default.work_items == {}
    assert changed_default.activations == {}


def test_plan_admission_indexes_declared_external_enqueue_route_not_stage_scan() -> (
    None
):
    source = kernel_ping.workflow_source()
    graph_records = cast(list[dict[str, object]], source["graphs"])
    graph_records[0]["node_ids"] = (
        *cast(tuple[str, ...], graph_records[0]["node_ids"]),
        "kernel_ping.taskmaster.alternate_start",
    )
    route_records = cast(list[dict[str, object]], source["external_enqueue_routes"])
    route_records[0]["id"] = "kernel_ping.external_prompt_alternate"
    route_records[0]["graph_node_id"] = "kernel_ping.taskmaster.alternate_start"
    route_plan, route_fingerprint = _compile_source(source)
    declared_route = route_plan.external_enqueue_routes[0]

    state = _admitted_default_state(route_plan, route_fingerprint, suffix="route")
    route = state.admitted_plans[route_fingerprint].external_enqueue_routes[
        QueueFamilyId("prompt")
    ]
    assert route.graph_node_id == declared_route.graph_node_id
    assert route.stage_kind_id == declared_route.stage_kind_id

    decision = decide(
        state,
        EnqueueWork(
            "enqueue-declared-route",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"body": "must follow declared route"},
        ),
        deterministic_context(
            transition_id="transition-declared-route",
            work_item_id="work-declared-route",
            activation_id="activation-declared-route",
        ),
    )
    assert decision.accepted is True
    after = apply(state, decision)
    assert (
        after.activations["activation-declared-route"].graph_node_id
        == declared_route.graph_node_id
    )
    assert (
        after.activations["activation-declared-route"].stage_kind_id
        == declared_route.stage_kind_id
    )


def test_claim_creates_run_with_activation_plan_pin_and_fencing_token() -> None:
    plan_a, fingerprint_a = _compile_source(kernel_ping.workflow_source())
    plan_b, fingerprint_b = _compile_source(
        kernel_ping_support.no_pause_workflow_source()
    )
    state = _admitted_default_state(plan_a, fingerprint_a, suffix="a")

    enqueue = EnqueueWork(
        "enqueue",
        queue_family_id=QueueFamilyId("prompt"),
        payload={"body": "prove launch-plan pinning"},
    )
    state = apply(
        state,
        decide(
            state,
            enqueue,
            deterministic_context(
                transition_id="transition-enqueue",
                work_item_id="work-a",
                activation_id="activation-a",
            ),
        ),
    )
    plan_a_ref = state.work_items["work-a"].ref.plan_ref

    state = _with_admitted_default(
        state,
        plan_b,
        fingerprint_b,
        suffix="b",
    )
    assert state.default_plan_ref is not None
    assert state.default_plan_ref.authority_fingerprint == fingerprint_b

    claim = ClaimWork("claim", activation_id="activation-a")
    claim_decision = decide(
        state,
        claim,
        deterministic_context(
            transition_id="transition-claim",
            run_id="run-a",
            claim_id="claim-a",
            fencing_token="fence-a",
        ),
    )
    assert claim_decision.accepted is True
    after_claim = apply(state, claim_decision)

    work_item = after_claim.work_items["work-a"]
    activation = after_claim.activations["activation-a"]
    run = after_claim.runs["run-a"]
    assert work_item.ref.plan_ref == plan_a_ref
    assert activation.plan_ref == plan_a_ref
    assert run.run_ref.plan_ref == plan_a_ref
    assert run.run_ref.plan_ref.authority_fingerprint == fingerprint_a
    assert run.run_ref.claim_id == "claim-a"
    assert run.run_ref.fencing_token == "fence-a"
    assert run.work_item_id == "work-a"
    assert run.activation_id == "activation-a"
    assert run.stage_kind_id == activation.stage_kind_id
    assert run.runner_binding_id == activation.runner_binding_id
    assert run.run_ref.generation == 0

    replay_decision = decide(
        after_claim,
        claim,
        deterministic_context(
            transition_id="transition-claim-replay",
            run_id="run-b",
            claim_id="claim-b",
            fencing_token="fence-b",
        ),
    )
    assert replay_decision.disposition == "replayed"
    assert replay_decision.mutations == ()
    assert apply(after_claim, replay_decision) == after_claim

    conflict = ClaimWork("claim", activation_id="missing-activation")
    conflict_decision = decide(
        after_claim,
        conflict,
        deterministic_context(transition_id="transition-claim-conflict"),
    )
    after_conflict = apply(after_claim, conflict_decision)
    assert conflict_decision.accepted is False
    assert after_conflict.runs == after_claim.runs


def test_claim_refuses_missing_or_stale_activation_without_workflow_mutation() -> None:
    plan, fingerprint = _compile_source(kernel_ping.workflow_source())
    state = _admitted_default_state(plan, fingerprint, suffix="a")
    missing_decision = decide(
        state,
        ClaimWork("missing-claim", activation_id="missing"),
        deterministic_context(transition_id="transition-missing"),
    )
    after_missing = apply(state, missing_decision)
    assert missing_decision.accepted is False
    assert after_missing.work_items == state.work_items
    assert after_missing.activations == state.activations
    assert after_missing.runs == state.runs

    state = apply(
        state,
        decide(
            state,
            EnqueueWork(
                "enqueue",
                queue_family_id=QueueFamilyId("prompt"),
                payload={"body": "claim once"},
            ),
            deterministic_context(
                transition_id="transition-enqueue",
                work_item_id="work-a",
                activation_id="activation-a",
            ),
        ),
    )
    claimed = apply(
        state,
        decide(
            state,
            ClaimWork("claim", activation_id="activation-a"),
            deterministic_context(
                transition_id="transition-claim",
                run_id="run-a",
                claim_id="claim-a",
                fencing_token="fence-a",
            ),
        ),
    )

    stale_decision = decide(
        claimed,
        ClaimWork("stale-claim", activation_id="activation-a"),
        deterministic_context(
            transition_id="transition-stale",
            run_id="run-b",
            claim_id="claim-b",
            fencing_token="fence-b",
        ),
    )
    after_stale = apply(claimed, stale_decision)
    assert stale_decision.accepted is False
    assert after_stale.work_items == claimed.work_items
    assert after_stale.activations == claimed.activations
    assert after_stale.runs == claimed.runs
