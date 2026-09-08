from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import QueueFamilyId
from millrace.contracts.ids import ActionId
from millrace.contracts.state import CounterRecord
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    InitializeWorkspace,
    RunnerResultObserved,
    SelectDefaultPlan,
)
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.kernel.observation_policy import (
    AuthenticatedRunnerObservation,
    authenticate_runner_observation,
)
from millrace.kernel.terminal_actions import _counter_record_id
from millrace.operator import operator_status
from millrace.operator.dispatch import build_dispatch_envelope_for_run
from millrace.testing import (
    fake_completed_runner_observation_state,
    fake_runner_observation_payload,
    fake_runner_session_state,
)
from substrate._runtime_store_support import persist_and_load_runtime_state
from support import generic_admission, generic_fanout


def _runtime_owned_threshold_plan():
    source = generic_admission.source()
    runner = cast(list[dict[str, object]], source["runner_bindings"])[0]
    runner.update(
        {
            "adapter_kind": "codex",
            "component_pin": {
                "component_kind": "runner",
                "component_id": "bounded-runner",
                "component_version": "1",
                "provider_distribution": "bounded-provider",
                "provider_version": "1",
                "descriptor_media_type": "application/json",
                "descriptor_sha256": "a" * 64,
                "required_capability_ids": (),
                "legal_terminal_result_ids": ("RUNTIME_FAILURE",),
            },
            "terminal_result_mappings": (
                {
                    "stage_kind_id": generic_admission.PARENT_STAGE_ID,
                    "runner_result_id": "RUNTIME_FAILURE",
                    "outcome_id": "admission.retry_ready",
                },
            ),
        }
    )
    retry_action = next(
        action
        for action in cast(list[dict[str, object]], source["terminal_actions"])
        if action["id"] == "admission.retry"
    )
    retry_action["artifact_field_conditions"] = {
        "artifact_kind": "fanout.packet",
    }
    return generic_admission.compile_plan(source)


def _claimed_parent_state():
    plan, fingerprint = _runtime_owned_threshold_plan()
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("init-parent"),
        AdmitPlan(
            "admit-parent",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        SelectDefaultPlan("select-parent", authority_fingerprint=fingerprint),
        EnqueueWork(
            "enqueue-parent",
            queue_family_id=QueueFamilyId("parent"),
            payload=generic_fanout.packet_payload(item_ids=("threshold",)),
        ),
        ClaimWork("claim-parent", activation_id="activation-enqueue-parent"),
    ):
        state = generic_fanout.apply_accepted_input(
            state,
            transition_input,
            generic_fanout.context(transition_input.input_id),
        )
    return state, plan, fingerprint


def test_dispatch_omits_component_outcomes_without_runner_mapping() -> None:
    state, _plan, _fingerprint = _claimed_parent_state()
    state = fake_runner_session_state(state=state, run_id="run-parent")

    envelope = build_dispatch_envelope_for_run(state=state, run_id="run-parent")

    assert tuple(option["outcome_id"] for option in envelope.terminal_options) == (
        "admission.retry_ready",
    )
    assert tuple(option["marker"] for option in envelope.terminal_options) == (
        "ADMISSION_RETRY_READY",
    )
    assert envelope.terminal_options[0]["artifact_field_conditions"] == {
        "artifact_kind": "fanout.packet",
    }


def _runtime_owned_threshold_state():
    state, plan, fingerprint = _claimed_parent_state()
    run = state.runs["run-parent"]
    work_item = state.work_items[run.work_item_id]
    counter = next(
        item
        for item in plan.counters
        if str(item.id) == generic_admission.COUNTER_ID
    )
    counter_record = CounterRecord(
        record_id=_counter_record_id(
            plan_ref=run.run_ref.plan_ref,
            counter_id=str(counter.id),
            lineage_id=cast(str, work_item.lineage_id),
        ),
        counter_id=counter.id,
        selected_plan_ref=run.run_ref.plan_ref,
        lineage_id=cast(str, work_item.lineage_id),
        value=1,
        updated_by_input_id="prior-runtime-failure",
    )
    state = replace(
        state,
        counters={counter_record.record_id: counter_record},
    )
    activation = state.activations[run.activation_id]
    observation = RunnerResultObserved(
        "observe-runtime-failure",
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=activation,
            plan_fingerprint=fingerprint,
            marker="ADMISSION_RETRY_READY",
            artifact_payload=generic_fanout.packet_payload(item_ids=("failure",)),
        ),
        observed_at=None,
    )
    seeded, authenticated = fake_completed_runner_observation_state(
        state=state,
        observation=observation,
    )

    decision = decide(
        seeded,
        authenticated,
        generic_fanout.context("observe-runtime-failure"),
    )

    assert decision.accepted is True
    assert str(decision.governance_events[0].action_id) == (
        generic_admission.COUNTER_THRESHOLD_ACTION_ID
    )
    after = apply(seeded, decision)
    return after, plan, fingerprint


def test_runtime_promotes_unmapped_increment_to_threshold_action(
    tmp_path: Path,
) -> None:
    after, plan, fingerprint = _runtime_owned_threshold_state()
    run = after.runs["run-parent"]
    work_item = after.work_items[run.work_item_id]

    assert next(iter(after.counters.values())).value == 2
    assert work_item.ref.work_item_id in after.closed_work_items
    authenticated_observation = authenticate_runner_observation(
        after,
        next(iter(after.runner_observations.values())),
    )
    assert isinstance(authenticated_observation, AuthenticatedRunnerObservation)
    assert str(authenticated_observation.action.id) == (
        generic_admission.COUNTER_THRESHOLD_ACTION_ID
    )

    status = operator_status(after)
    assert len(status.artifacts) == 1
    projected = status.artifacts[0]
    artifact = after.artifacts[projected.artifact_id]
    assert (
        projected.artifact_id,
        projected.workflow_id,
        projected.selected_plan_id,
        projected.selected_plan_fingerprint,
        projected.work_item_id,
        projected.queue_family_id,
        projected.lineage_id,
        projected.schema_id,
        projected.payload,
        projected.payload_digest,
        projected.source_run_id,
        projected.source_activation_id,
        projected.source_action_id,
        projected.terminal_action_id,
        projected.source_input_id,
        projected.source_stage_kind_id,
        projected.source_graph_node_id,
        projected.source_runner_binding_id,
        projected.latest_marker,
        projected.transition_id,
    ) == (
        artifact.artifact_id,
        str(plan.workflow.workflow_id),
        run.run_ref.plan_ref.plan_id,
        fingerprint,
        artifact.work_item_id,
        str(work_item.queue_family_id),
        work_item.lineage_id,
        str(artifact.schema_id),
        artifact.payload,
        artifact.payload_digest,
        artifact.source_run_id,
        run.activation_id,
        generic_admission.COUNTER_THRESHOLD_ACTION_ID,
        generic_admission.COUNTER_THRESHOLD_ACTION_ID,
        artifact.created_by_input_id,
        str(artifact.source_stage_kind_id),
        artifact.source_graph_node_id,
        str(run.runner_binding_id),
        "ADMISSION_RETRY_READY",
        artifact.transition_id,
    )

    reloaded = persist_and_load_runtime_state(tmp_path, after)
    reloaded_status = operator_status(reloaded)
    assert reloaded_status.artifacts == status.artifacts


@pytest.mark.parametrize(
    "corruption",
    ("absent_counter", "corrupt_counter", "corrupt_governance"),
)
def test_runtime_owned_threshold_public_projection_rejects_invalid_provenance(
    corruption: str,
) -> None:
    after, _plan, _fingerprint = _runtime_owned_threshold_state()
    if corruption == "absent_counter":
        after = replace(after, counters={})
    elif corruption == "corrupt_counter":
        counter = next(iter(after.counters.values()))
        after = replace(
            after,
            counters={counter.record_id: replace(counter, value=1)},
        )
    else:
        observation_input_id = next(
            iter(after.runner_observations.values())
        ).created_by_input_id
        after = replace(
            after,
            governance_events=tuple(
                replace(
                    event,
                    action_id=ActionId(generic_admission.COUNTER_INCREMENT_ACTION_ID),
                )
                if event.input_id == observation_input_id
                else event
                for event in after.governance_events
            ),
            traces=tuple(
                replace(
                    trace,
                    action_id=ActionId(generic_admission.COUNTER_INCREMENT_ACTION_ID),
                )
                if trace.input_id == observation_input_id
                else trace
                for trace in after.traces
            ),
        )

    assert operator_status(after).artifacts == ()


def test_runtime_owned_threshold_requires_matching_artifact_contract() -> None:
    plan, _fingerprint = _runtime_owned_threshold_plan()
    tampered = replace(
        plan,
        terminal_actions=tuple(
            replace(action, artifact_schema_id=None)
            if str(action.id) == generic_admission.COUNTER_THRESHOLD_ACTION_ID
            else action
            for action in plan.terminal_actions
        ),
    )
    decision = decide(
        empty_runtime_state(),
        AdmitPlan(
            "admit-mismatched-runtime-threshold",
            selected_plan=tampered,
            authority_fingerprint=authority_fingerprint(tampered),
        ),
        generic_fanout.context("admit-mismatched-runtime-threshold"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
    assert decision.refusal.detail == (
        "counter_runtime_threshold_artifact_schema:admission.counter"
    )

def test_runtime_owned_threshold_requires_compatible_artifact_conditions() -> None:
    plan, _fingerprint = _runtime_owned_threshold_plan()
    tampered = replace(
        plan,
        terminal_actions=tuple(
            replace(
                action,
                artifact_field_conditions={"artifact_kind": "not-a-packet"},
            )
            if str(action.id) == generic_admission.COUNTER_THRESHOLD_ACTION_ID
            else action
            for action in plan.terminal_actions
        ),
    )
    decision = decide(
        empty_runtime_state(),
        AdmitPlan(
            "admit-mismatched-runtime-threshold-conditions",
            selected_plan=tampered,
            authority_fingerprint=authority_fingerprint(tampered),
        ),
        generic_fanout.context("admit-mismatched-runtime-threshold-conditions"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
    assert decision.refusal.detail == (
        "counter_runtime_threshold_artifact_conditions:admission.counter"
    )
