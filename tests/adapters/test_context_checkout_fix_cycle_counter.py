from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from millrace.adapters.cli.context import CliWorkspacePaths
from millrace.adapters.cli.context_checkout import prepare_context_checkout
from millrace.contracts import CounterId, QueueFamilyId
from millrace.contracts.state import CounterRecord, RunnerObservationRecord
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    CreateRunnerSession,
    EnqueueWork,
    InitializeWorkspace,
    RunnerResultObserved,
    SelectDefaultPlan,
)
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.testing import (
    deterministic_context,
    fake_completed_runner_observation_state,
    fake_runner_observation_payload,
)
from support import generic_admission, generic_fanout


def _counter_route_source(
    *,
    target_stage_id: str,
    target_graph_node_id: str,
    emitted_queue_family_id: str,
    threshold_count: int,
) -> dict[str, object]:
    source = deepcopy(generic_admission.source())
    actions = cast(list[dict[str, object]], source["terminal_actions"])
    for action_id in (
        generic_admission.COUNTER_INCREMENT_ACTION_ID,
        generic_admission.COUNTER_THRESHOLD_ACTION_ID,
    ):
        action = next(item for item in actions if item["id"] == action_id)
        action.update(
            {
                "kind": "route",
                "target_stage_kind_id": target_stage_id,
                "target_graph_node_id": target_graph_node_id,
                "emitted_queue_family_id": emitted_queue_family_id,
                "artifact_schema_id": generic_fanout.PACKET_SCHEMA_ID,
                "runner_binding_id": generic_admission.RUNNER_ID,
                "payload_projection": {
                    "kind": "source",
                    "path": ("artifact_payload",),
                },
            }
        )
    counters = cast(list[dict[str, object]], source["counters"])
    increment_counter = next(
        counter
        for counter in counters
        if counter["id"] == generic_admission.COUNTER_ID
    )
    increment_counter["threshold_count"] = threshold_count
    runners = cast(list[dict[str, object]], source["runner_bindings"])
    runners[0]["adapter_kind"] = "codex"
    assets = cast(list[dict[str, object]], source["assets"])
    assets.append(
        {
            "id": "admission.context_router",
            "kind": "template",
            "body": "Route selected context.",
        }
    )
    source["context_bindings"] = [
        {
            "id": "admission.target_context",
            "stage_kind_id": target_stage_id,
            "router_asset_id": "admission.context_router",
            "checkout_root": "checkout",
            "required_sources": [
                {
                    "source_kind": "accepted_lineage_artifacts",
                    "source_ref": "current_lineage",
                    "max_files": 8,
                    "max_bytes": 4096,
                }
            ],
            "discoverable_sources": [],
        }
    ]
    return source


def _accept(
    state,
    transition_input,
    context,
    *,
    runner_observation: bool = False,
):
    if runner_observation:
        state, transition_input = fake_completed_runner_observation_state(
            state=state,
            observation=transition_input,
        )
    decision = decide(state, transition_input, context)
    assert decision.accepted, decision
    return apply(state, decision)


def _counter_artifact_state(
    *,
    target_stage_id: str,
    target_graph_node_id: str,
    emitted_queue_family_id: str,
    threshold_count: int,
    markers: tuple[str, ...],
):
    plan, fingerprint = generic_admission.compile_plan(
        _counter_route_source(
            target_stage_id=target_stage_id,
            target_graph_node_id=target_graph_node_id,
            emitted_queue_family_id=emitted_queue_family_id,
            threshold_count=threshold_count,
        )
    )
    state = empty_runtime_state()
    for transition_input, context in (
        (
            InitializeWorkspace("initialize"),
            deterministic_context(transition_id="initialize"),
        ),
        (
            AdmitPlan("admit", selected_plan=plan, authority_fingerprint=fingerprint),
            deterministic_context(transition_id="admit"),
        ),
        (
            SelectDefaultPlan("select", authority_fingerprint=fingerprint),
            deterministic_context(transition_id="select"),
        ),
        (
            EnqueueWork(
                "enqueue",
                queue_family_id=QueueFamilyId("parent"),
                payload={
                    "artifact_kind": generic_fanout.PACKET_SCHEMA_ID,
                    "items": ({"item_id": "input", "body": "Parent input"},),
                },
            ),
            deterministic_context(
                transition_id="enqueue",
                work_item_id="parent-work",
                activation_id="parent-activation",
            ),
        ),
        (
            ClaimWork("claim-parent", activation_id="parent-activation"),
            deterministic_context(
                transition_id="claim-parent",
                work_item_id="parent-work",
                activation_id="parent-activation",
                run_id="parent-run",
                claim_id="parent-claim",
                fencing_token="parent-fence",
            ),
        ),
    ):
        state = _accept(state, transition_input, context)

    current_work_item_id = "parent-work"
    current_activation_id = "parent-activation"
    current_run_id = "parent-run"
    current_claim_id = "parent-claim"
    current_fencing_token = "parent-fence"
    for index, marker in enumerate(markers):
        run = state.runs[current_run_id]
        activation = state.activations[current_activation_id]
        target_work_item_id = f"target-work-{index}"
        target_activation_id = f"target-activation-{index}"
        input_id = f"observe-{index}"
        state = _accept(
            state,
            RunnerResultObserved(
                input_id,
                run_id=current_run_id,
                payload=fake_runner_observation_payload(
                    run=run,
                    activation=activation,
                    plan_fingerprint=fingerprint,
                    marker=marker,
                    artifact_payload={
                        "artifact_kind": generic_fanout.PACKET_SCHEMA_ID,
                        "items": ({"item_id": f"item-{index}", "body": "Body"},),
                    },
                ),
                observed_at=None,
            ),
            deterministic_context(
                transition_id=input_id,
                work_item_id=target_work_item_id,
                activation_id=target_activation_id,
                run_id=current_run_id,
                claim_id=current_claim_id,
                fencing_token=current_fencing_token,
            ),
            runner_observation=True,
        )
        current_work_item_id = target_work_item_id
        current_activation_id = target_activation_id
        if index + 1 < len(markers):
            current_run_id = f"run-{index + 1}"
            current_claim_id = f"claim-{index + 1}"
            current_fencing_token = f"fence-{index + 1}"
            state = _accept(
                state,
                ClaimWork(
                    current_claim_id,
                    activation_id=current_activation_id,
                ),
                deterministic_context(
                    transition_id=current_claim_id,
                    work_item_id=current_work_item_id,
                    activation_id=current_activation_id,
                    run_id=current_run_id,
                    claim_id=current_claim_id,
                    fencing_token=current_fencing_token,
                ),
            )

    final_run_id = f"run-{len(markers)}"
    final_claim_id = f"claim-{len(markers)}"
    final_fencing_token = f"fence-{len(markers)}"
    state = _accept(
        state,
        ClaimWork(final_claim_id, activation_id=current_activation_id),
        deterministic_context(
            transition_id=final_claim_id,
            work_item_id=current_work_item_id,
            activation_id=current_activation_id,
            run_id=final_run_id,
            claim_id=final_claim_id,
            fencing_token=final_fencing_token,
        ),
    )
    final_run = state.runs[final_run_id]
    state = _accept(
        state,
        CreateRunnerSession(
            "create-target-session",
            run_ref=final_run.run_ref,
            session_id="target-session",
            session_fencing_token="target-session-fence",
            created_at=1,
            explicit_retry_intent=False,
        ),
        deterministic_context(
            transition_id="create-target-session",
            work_item_id=final_run.work_item_id,
            activation_id=final_run.activation_id,
            run_id=final_run_id,
            claim_id=final_run.run_ref.claim_id,
            fencing_token=final_run.run_ref.fencing_token,
        ),
    )
    assert len(state.artifacts) == len(markers)
    counter = next(iter(state.counters.values()))
    assert counter.value == len(markers)
    assert not state.recovery_attempts
    return plan, fingerprint, state


def _prepare_checkout(tmp_path: Path, plan, fingerprint, state):
    workspace = tmp_path / "workspace"
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir(parents=True)
    db_path.touch()
    cas_path.mkdir()
    return prepare_context_checkout(
        paths=CliWorkspacePaths(
            workspace.resolve(),
            db_path.resolve(),
            cas_path.resolve(),
        ),
        session=state.runner_sessions["target-session"],
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=ContentAddressedByteStore(cas_path.resolve()),
        reuse_existing=False,
    )


def test_context_checkout_authenticates_first_counter_increment(
    tmp_path: Path,
) -> None:
    plan, fingerprint, state = _counter_artifact_state(
        target_stage_id=generic_admission.CHILD_STAGE_ID,
        target_graph_node_id=generic_admission.CHILD_NODE_ID,
        emitted_queue_family_id="child",
        threshold_count=2,
        markers=("ADMISSION_RETRY_READY",),
    )

    prepared = _prepare_checkout(tmp_path, plan, fingerprint, state)

    assert sum(
        item.source_kind == "accepted_lineage_artifacts"
        for item in prepared.manifest.files
    ) == 1


def test_context_checkout_replays_later_counter_increment_pre_value(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.adapters.cli import context_checkout as checkout_module

    plan, fingerprint, state = _counter_artifact_state(
        target_stage_id=generic_admission.PARENT_STAGE_ID,
        target_graph_node_id=generic_admission.PARENT_NODE_ID,
        emitted_queue_family_id="parent",
        threshold_count=3,
        markers=("ADMISSION_RETRY_READY", "ADMISSION_RETRY_READY"),
    )
    first_artifact = min(
        state.artifacts.values(), key=lambda artifact: artifact.artifact_id
    )
    latest = max(state.artifacts.values(), key=lambda artifact: artifact.artifact_id)
    state = replace(state, artifacts={latest.artifact_id: latest})
    replayed_counters: list[tuple[tuple[str, int, str], ...]] = []
    original_decide = checkout_module.decide

    def capture_replay(current_state, transition_input, context):
        if context.transition_id.startswith("context-checkout:"):
            replayed_counters.append(
                tuple(
                    (record_id, record.value, record.updated_by_input_id)
                    for record_id, record in current_state.counters.items()
                )
            )
        return original_decide(current_state, transition_input, context)

    monkeypatch.setattr(checkout_module, "decide", capture_replay)
    _prepare_checkout(tmp_path, plan, fingerprint, state)

    counter_id = next(iter(state.counters))
    assert replayed_counters == [
        ((counter_id, 1, first_artifact.created_by_input_id),),
        ((counter_id, 1, first_artifact.created_by_input_id),),
    ]


def test_context_checkout_authenticates_untrimmed_counter_artifacts(
    tmp_path: Path,
) -> None:
    plan, fingerprint, state = _counter_artifact_state(
        target_stage_id=generic_admission.PARENT_STAGE_ID,
        target_graph_node_id=generic_admission.PARENT_NODE_ID,
        emitted_queue_family_id="parent",
        threshold_count=3,
        markers=("ADMISSION_RETRY_READY", "ADMISSION_RETRY_READY"),
    )

    prepared = _prepare_checkout(tmp_path, plan, fingerprint, state)

    assert sum(
        item.source_kind == "accepted_lineage_artifacts"
        for item in prepared.manifest.files
    ) == 2


def test_context_checkout_indexes_counter_history_once_per_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import context_checkout as checkout_module

    plan, fingerprint, state = _counter_artifact_state(
        target_stage_id=generic_admission.PARENT_STAGE_ID,
        target_graph_node_id=generic_admission.PARENT_NODE_ID,
        emitted_queue_family_id="parent",
        threshold_count=3,
        markers=("ADMISSION_RETRY_READY", "ADMISSION_RETRY_READY"),
    )
    calls = 0
    original_index = checkout_module._index_counter_replay_history

    def count_indexes(**kwargs):
        nonlocal calls
        calls += 1
        return original_index(**kwargs)

    monkeypatch.setattr(
        checkout_module,
        "_index_counter_replay_history",
        count_indexes,
    )

    _prepare_checkout(tmp_path, plan, fingerprint, state)

    assert calls == 2


def test_context_checkout_replays_threshold_action_from_pre_value(
    tmp_path: Path,
) -> None:
    plan, fingerprint, state = _counter_artifact_state(
        target_stage_id=generic_admission.PARENT_STAGE_ID,
        target_graph_node_id=generic_admission.PARENT_NODE_ID,
        emitted_queue_family_id="parent",
        threshold_count=2,
        markers=("ADMISSION_RETRY_READY", "ADMISSION_RETRY_EXHAUSTED"),
    )
    latest = max(state.artifacts.values(), key=lambda artifact: artifact.artifact_id)
    state = replace(state, artifacts={latest.artifact_id: latest})

    prepared = _prepare_checkout(tmp_path, plan, fingerprint, state)

    assert sum(
        item.source_kind == "accepted_lineage_artifacts"
        for item in prepared.manifest.files
    ) == 1


@pytest.mark.parametrize(
    ("foreign_plan", "foreign_lineage"),
    ((True, False), (False, True)),
    ids=("wrong-plan", "wrong-lineage"),
)
def test_context_checkout_ignores_foreign_observation_history(
    tmp_path: Path,
    foreign_plan: bool,
    foreign_lineage: bool,
) -> None:
    plan, fingerprint, state = _counter_artifact_state(
        target_stage_id=generic_admission.CHILD_STAGE_ID,
        target_graph_node_id=generic_admission.CHILD_NODE_ID,
        emitted_queue_family_id="child",
        threshold_count=2,
        markers=("ADMISSION_RETRY_READY",),
    )
    target_session = state.runner_sessions["target-session"]
    target_run = state.runs[target_session.run_id]
    target_work = state.work_items[target_run.work_item_id]
    foreign_work_item_id = "foreign-work"
    foreign_plan_ref = (
        replace(
            target_run.run_ref.plan_ref,
            authority_fingerprint="sha256:" + "f" * 64,
        )
        if foreign_plan
        else target_run.run_ref.plan_ref
    )
    foreign_run = replace(
        target_run,
        run_ref=replace(
            target_run.run_ref,
            run_id="foreign-run",
            work_item_id=foreign_work_item_id,
            plan_ref=foreign_plan_ref,
        ),
        work_item_id=foreign_work_item_id,
    )
    foreign_work = replace(
        target_work,
        ref=replace(
            target_work.ref,
            work_item_id=foreign_work_item_id,
            plan_ref=foreign_plan_ref,
        ),
        lineage_id=("foreign-lineage" if foreign_lineage else target_work.lineage_id),
    )
    foreign_observation = RunnerObservationRecord(
        observation_id="foreign-observation",
        run_id="foreign-run",
        payload={"malformed": True},
        created_by_input_id="foreign-input",
        observed_at=None,
    )
    foreign_transition = replace(
        state.transitions[-1],
        record_id="foreign-transition",
        input_id="foreign-input",
        input_kind=RunnerResultObserved.input_kind,
        input_family="workflow_observation",
        accepted=True,
    )
    state = replace(
        state,
        work_items={**state.work_items, foreign_work_item_id: foreign_work},
        runs={**state.runs, "foreign-run": foreign_run},
        runner_observations={
            **state.runner_observations,
            foreign_observation.observation_id: foreign_observation,
        },
        transitions=(*state.transitions, foreign_transition),
    )

    prepared = _prepare_checkout(tmp_path, plan, fingerprint, state)

    assert sum(
        item.source_kind == "accepted_lineage_artifacts"
        for item in prepared.manifest.files
    ) == 1


def test_context_checkout_ignores_malformed_unrelated_counter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import context_checkout as checkout_module

    plan, fingerprint, state = _counter_artifact_state(
        target_stage_id=generic_admission.PARENT_STAGE_ID,
        target_graph_node_id=generic_admission.PARENT_NODE_ID,
        emitted_queue_family_id="parent",
        threshold_count=3,
        markers=("ADMISSION_RETRY_READY", "ADMISSION_RETRY_READY"),
    )
    latest = max(state.artifacts.values(), key=lambda artifact: artifact.artifact_id)
    target_counter = next(iter(state.counters.values()))
    unrelated_counter = CounterRecord(
        record_id="unrelated-counter",
        counter_id=CounterId(generic_admission.RECOVERY_COUNTER_ID),
        selected_plan_ref=target_counter.selected_plan_ref,
        lineage_id=target_counter.lineage_id,
        value=0,
        updated_by_input_id="malformed-input",
    )
    state = replace(
        state,
        artifacts={latest.artifact_id: latest},
        counters={
            **state.counters,
            unrelated_counter.record_id: unrelated_counter,
        },
    )
    replayed_unrelated: list[CounterRecord] = []
    original_decide = checkout_module.decide

    def capture_replay(current_state, transition_input, context):
        if context.transition_id.startswith("context-checkout:"):
            replayed_unrelated.append(current_state.counters["unrelated-counter"])
        return original_decide(current_state, transition_input, context)

    monkeypatch.setattr(checkout_module, "decide", capture_replay)

    prepared = _prepare_checkout(tmp_path, plan, fingerprint, state)

    assert sum(
        item.source_kind == "accepted_lineage_artifacts"
        for item in prepared.manifest.files
    ) == 1
    assert replayed_unrelated == [unrelated_counter, unrelated_counter]


@pytest.mark.parametrize(
    "corruption",
    ("duplicate", "value", "updated_by_input_id"),
)
def test_context_checkout_refuses_corrupt_target_counter(
    tmp_path: Path,
    corruption: str,
) -> None:
    plan, fingerprint, state = _counter_artifact_state(
        target_stage_id=generic_admission.CHILD_STAGE_ID,
        target_graph_node_id=generic_admission.CHILD_NODE_ID,
        emitted_queue_family_id="child",
        threshold_count=2,
        markers=("ADMISSION_RETRY_READY",),
    )
    record_id, counter = next(iter(state.counters.items()))
    if corruption == "duplicate":
        counters = {
            **state.counters,
            "duplicate-target-counter": replace(
                counter,
                record_id="duplicate-target-counter",
            ),
        }
    elif corruption == "value":
        counters = {record_id: replace(counter, value=counter.value + 1)}
    else:
        counters = {
            record_id: replace(counter, updated_by_input_id="stale-input")
        }
    state = replace(state, counters=counters)

    with pytest.raises(ValueError, match="provenance could not be authenticated"):
        _prepare_checkout(tmp_path, plan, fingerprint, state)


def test_context_checkout_refuses_corrupt_later_same_lineage_audit(
    tmp_path: Path,
) -> None:
    plan, fingerprint, state = _counter_artifact_state(
        target_stage_id=generic_admission.PARENT_STAGE_ID,
        target_graph_node_id=generic_admission.PARENT_NODE_ID,
        emitted_queue_family_id="parent",
        threshold_count=3,
        markers=("ADMISSION_RETRY_READY", "ADMISSION_RETRY_READY"),
    )
    first_artifact = min(
        state.artifacts.values(), key=lambda artifact: artifact.artifact_id
    )
    latest = max(state.artifacts.values(), key=lambda artifact: artifact.artifact_id)
    state = replace(
        state,
        artifacts={latest.artifact_id: latest},
        governance_events=tuple(
            replace(event, authority_source="corrupt")
            if event.input_id == first_artifact.created_by_input_id
            else event
            for event in state.governance_events
        ),
    )

    with pytest.raises(ValueError, match="provenance could not be authenticated"):
        _prepare_checkout(tmp_path, plan, fingerprint, state)


def test_context_checkout_refuses_missing_target_counter(tmp_path: Path) -> None:
    plan, fingerprint, state = _counter_artifact_state(
        target_stage_id=generic_admission.CHILD_STAGE_ID,
        target_graph_node_id=generic_admission.CHILD_NODE_ID,
        emitted_queue_family_id="child",
        threshold_count=2,
        markers=("ADMISSION_RETRY_READY",),
    )
    state = replace(state, counters={})

    with pytest.raises(ValueError, match="provenance could not be authenticated"):
        _prepare_checkout(tmp_path, plan, fingerprint, state)
