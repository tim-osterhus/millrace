from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from millrace.contracts import ClosedWorkItemRecord


@pytest.mark.parametrize(
    "closure_case",
    ("valid", "missing", "wrong_source", "wrong_action", "wrong_input"),
)
def test_native_close_artifact_replay_uses_existing_kernel_ping_builders(
    closure_case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kernel.kernel_ping_scenarios import bootstrap_to_worker_claim
    from millrace.adapters.cli import context_checkout as checkout_module
    from millrace.kernel import apply, decide
    from millrace.testing import fake_completed_runner_observation_state
    from millrace.workflows import kernel_ping
    from support.kernel_ping import (
        compile_kernel_ping,
        kernel_ping_context,
        runner_observation,
        task_artifact_payload,
    )

    source = deepcopy(kernel_ping.WORKFLOW_SOURCE)
    close_action = next(
        action
        for action in source["terminal_actions"]
        if action["id"] == "kernel_ping.close_worker_success"
    )
    close_action["artifact_schema_id"] = "kernel_ping.task_artifact"
    plan, fingerprint = compile_kernel_ping(source)
    state = bootstrap_to_worker_claim(plan, fingerprint)
    observation = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-worker",
        action_id="kernel_ping.close_worker_success",
        input_id="observe-worker-success",
        artifact_payload=task_artifact_payload(
            objective="Close with authenticated evidence"
        ),
    )
    seeded_state, authorized_observation = fake_completed_runner_observation_state(
        state=state,
        observation=observation,
    )
    decision = decide(
        seeded_state,
        authorized_observation,
        kernel_ping_context("observe-worker-success"),
    )
    assert decision.accepted is True
    closed_state = apply(seeded_state, decision)
    artifact = next(
        artifact
        for artifact in closed_state.artifacts.values()
        if str(artifact.source_action_id) == "kernel_ping.close_worker_success"
    )
    unrelated = ClosedWorkItemRecord(
        record_id="unrelated-closed-work-item",
        work_item_id="unrelated-work-item",
        source_run_id=None,
        action_id=None,
        created_by_input_id="unrelated-input",
        close_kind="queue_cancellation",
    )
    closed_state = replace(
        closed_state,
        closed_work_items={
            **closed_state.closed_work_items,
            unrelated.work_item_id: unrelated,
        },
    )
    original_closed_work_items = dict(closed_state.closed_work_items)
    original_artifacts = dict(closed_state.artifacts)
    if closure_case != "valid":
        closed = closed_state.closed_work_items[artifact.work_item_id]
        if closure_case == "missing":
            closed_state = replace(
                closed_state,
                closed_work_items={
                    key: value
                    for key, value in closed_state.closed_work_items.items()
                    if key != artifact.work_item_id
                },
            )
        else:
            if closure_case == "wrong_source":
                closed = replace(closed, source_run_id="forged-source-run")
            elif closure_case == "wrong_action":
                closed = replace(closed, action_id="forged-close-action")
            else:
                closed = replace(closed, created_by_input_id="wrong-input")
            closed_state = replace(
                closed_state,
                closed_work_items={
                    **closed_state.closed_work_items,
                    artifact.work_item_id: closed,
                },
            )
        invalid_state_snapshot = dict(closed_state.closed_work_items)
        with pytest.raises(
            checkout_module.ContextCheckoutPreparationError,
            match="closure",
        ):
            checkout_module._authenticate_artifact_source(
                closed_state,
                artifact,
                counter_replay_history=None,
            )
        assert dict(closed_state.closed_work_items) == invalid_state_snapshot
        return
    replay_states = []
    original_decide = checkout_module.decide

    def capture_replay(replay_state, observed_input, context):
        replay_states.append(replay_state)
        return original_decide(replay_state, observed_input, context)

    monkeypatch.setattr(checkout_module, "decide", capture_replay)
    checkout_module._authenticate_artifact_source(
        closed_state,
        artifact,
        counter_replay_history=None,
    )

    assert artifact.work_item_id not in replay_states[0].closed_work_items
    assert replay_states[0].closed_work_items[unrelated.work_item_id] == unrelated
    assert dict(closed_state.closed_work_items) == original_closed_work_items
    assert dict(closed_state.artifacts) == original_artifacts


def _native_fanout_plan_source() -> dict[str, object]:
    from support import generic_fanout

    source = generic_fanout.source()
    source["assets"].append(
        {
            "id": "fanout.child.router",
            "kind": "template",
            "body": "Prepare the generated child context.",
            "presentation": {},
        }
    )
    source["context_bindings"] = [
        {
            "id": "fanout.child.context",
            "stage_kind_id": "child_stage",
            "router_asset_id": "fanout.child.router",
            "checkout_root": "checkout",
            "max_hydrated_files": 16,
            "max_hydrated_bytes": 16_384,
            "mutation_policy": "forbid_selected_roots",
            "materialization_retention": "until_session_durable_terminal",
            "required_sources": [
                {
                    "source_kind": "dispatch_material",
                    "source_ref": "current",
                    "max_files": 4,
                    "max_bytes": 100_000,
                },
                {
                    "source_kind": "selected_artifacts",
                    "source_ref": "direct_predecessors",
                    "empty_policy": "omit_if_absent",
                    "max_files": 4,
                    "max_bytes": 100_000,
                },
            ],
            "discoverable_sources": [],
        }
    ]
    return source


def _native_fanout_checkout_fixture(tmp_path: Path):
    from millrace.adapters.cli.context import CliWorkspacePaths
    from millrace.contracts import ClaimWork, FanoutFromArtifact
    from millrace.kernel import apply, decide
    from millrace.testing import fake_runner_session_state
    from support import generic_fanout

    plan, fingerprint = generic_fanout.compile_fanout(_native_fanout_plan_source())
    state = generic_fanout.parent_closed_state(plan, fingerprint)
    state = apply(
        state,
        decide(
            state,
            FanoutFromArtifact(
                "fanout-parent-packet",
                fanout_id="fanout.packet.children",
                source_artifact_id="transition-observe-parent-done:artifact",
            ),
            generic_fanout.context("fanout-parent-packet"),
        ),
    )
    child_activation = next(
        activation
        for activation in state.activations.values()
        if str(activation.stage_kind_id) == "child_stage"
    )
    state = apply(
        state,
        decide(
            state,
            ClaimWork("claim-child", activation_id=child_activation.activation_id),
            generic_fanout.context("claim-child"),
        ),
    )
    state = fake_runner_session_state(state=state, run_id="run-child")
    session = state.runner_sessions["test-session:run-child"]
    workspace = tmp_path / "fanout"
    (workspace / ".millrace/cas").mkdir(parents=True)
    database = workspace / ".millrace/runtime.sqlite3"
    database.touch()
    paths = CliWorkspacePaths(workspace, database, workspace / ".millrace/cas")
    target_record = next(
        record
        for record in state.fanout_records.values()
        if record.target_activation_id == child_activation.activation_id
    )
    return state, plan, fingerprint, session, paths, target_record


@pytest.mark.parametrize("fanout_case", ("valid", "missing", "foreign", "ambiguous"))
def test_prepare_context_checkout_authenticates_generated_predecessor_records(
    tmp_path: Path,
    fanout_case: str,
) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout
    from millrace.substrate.cas import ContentAddressedByteStore

    state, plan, fingerprint, session, paths, target_record = (
        _native_fanout_checkout_fixture(tmp_path)
    )
    if fanout_case == "missing":
        state = replace(
            state,
            fanout_records={
                key: value
                for key, value in state.fanout_records.items()
                if value.record_id != target_record.record_id
            },
        )
    elif fanout_case == "foreign":
        state = replace(
            state,
            fanout_records={
                **state.fanout_records,
                target_record.record_id: replace(
                    target_record,
                    source_artifact_id="foreign-artifact",
                ),
            },
        )
    elif fanout_case == "ambiguous":
        duplicate = replace(target_record, record_id="ambiguous-fanout-record")
        state = replace(
            state,
            fanout_records={
                **state.fanout_records,
                duplicate.record_id: duplicate,
            },
        )

    if fanout_case != "valid":
        with pytest.raises(ValueError, match="dispatch relation authority"):
            prepare_context_checkout(
                paths=paths,
                session=session,
                plan_fingerprint=fingerprint,
                binding=plan.context_bindings[0],
                state=state,
                cas_store=ContentAddressedByteStore(paths.cas_path),
            )
        return

    prepared = prepare_context_checkout(
        paths=paths,
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=ContentAddressedByteStore(paths.cas_path),
    )
    predecessor_files = tuple(
        item
        for item in prepared.manifest.files
        if item.source_kind == "selected_artifacts"
    )
    assert len(predecessor_files) == 1
    predecessor = json.loads(
        (
            prepared.materialized_checkout_root / predecessor_files[0].checkout_path
        ).read_text(encoding="utf-8")
    )
    assert predecessor["artifact_id"] == "transition-observe-parent-done:artifact"
