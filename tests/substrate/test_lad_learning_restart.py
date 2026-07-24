from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import (
    ArtifactSchemaId,
    ClaimWork,
    EnqueueWork,
    OperatorCloseWait,
    OperatorResumeWait,
    OperatorReviseWait,
    PartitionId,
    QueueFamilyId,
    RunnerBindingId,
    StageKindId,
)
from millrace.contracts.operator_waits import _operator_wait_record_id
from millrace.contracts.state import Activation, WorkItem, WorkItemRef
from millrace.kernel import apply, decide
from millrace.operator import operator_status
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.codecs import dumps_cas_object, encode_selected_compiled_plan
from millrace.substrate.errors import StorageIntegrityError
from substrate._runtime_store_support import (
    load_runtime_state,
    persist_and_load_runtime_state,
    persist_runtime_state,
    runtime_store_paths,
)
from support import lad_learning

_OBSERVATION_EVIDENCE_AUTHORITY_ERROR = (
    "runner_observations accepted-input authority invalid: evidence_authority"
)
_ARTIFACT_SOURCE_AUTHORITY_ERROR = (
    "artifacts runner-observation provenance invalid: artifact_source_authority"
)
_ARTIFACT_PAYLOAD_AUTHORITY_ERROR = (
    "artifacts runner-observation provenance invalid: artifact_payload_authority"
)


def test_trigger_generated_learning_work_and_active_learning_survive_restart(
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.ready_learning_state(plan, fingerprint)
    state = lad_learning.claim(
        state,
        activation_id="activation-learning-request",
        run_id="run-active-learning",
        input_id="claim-active-learning",
    )
    state = _doublechecker_pass_with_learning_request(state, plan, fingerprint)

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded == state
    fanout = next(
        record
        for record in loaded.fanout_records.values()
        if str(record.fanout_id)
        == "learning.trigger.execution.doublechecker_pass"
    )
    assert fanout.target_activation_id in loaded.activations
    assert fanout.target_work_item_id in loaded.work_items
    assert not loaded.work_dependencies

    decision = decide(
        loaded,
        ClaimWork("claim-reloaded-learning", activation_id=fanout.target_activation_id),
        lad_learning.context("claim-reloaded-learning", run_id="run-reloaded-learning"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "concurrency_policy_blocked"


def test_restart_refuses_two_active_learning_runs(tmp_path: Path) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.ready_learning_state(plan, fingerprint)
    state = lad_learning.claim(
        state,
        activation_id="activation-learning-request",
        run_id="run-active-learning",
        input_id="claim-active-learning",
    )
    state = _doublechecker_pass_with_learning_request(state, plan, fingerprint)
    fanout = next(
        record
        for record in state.fanout_records.values()
        if str(record.fanout_id)
        == "learning.trigger.execution.doublechecker_pass"
    )
    target_activation = state.activations[fanout.target_activation_id]
    plan_ref = target_activation.plan_ref
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE activations SET claimed_by_run_id = ? WHERE activation_id = ?",
            ("run-second-learning", fanout.target_activation_id),
        )
        connection.execute(
            """
            INSERT INTO runs (
                run_id,
                activation_id,
                work_item_id,
                claim_id,
                plan_id,
                plan_authority_fingerprint,
                plan_format_version,
                generation,
                fencing_token,
                stage_kind_id,
                runner_binding_id,
                created_by_input_id,
                started_at_order
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-second-learning",
                fanout.target_activation_id,
                fanout.target_work_item_id,
                "claim-second-learning",
                plan_ref.plan_id,
                plan_ref.authority_fingerprint,
                plan_ref.plan_format_version,
                0,
                "fence-second-learning",
                str(target_activation.stage_kind_id),
                str(target_activation.runner_binding_id),
                "claim-second-learning",
                999,
            ),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="concurrency_policy max_active_runs",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_selected_generated_route_missing_queue_family(
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.admitted_state(plan, fingerprint)
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    generated_routes = list(plan.generated_work_routes)
    generated_routes[0] = replace(
        generated_routes[0],
        queue_family_id=QueueFamilyId("missing-learning-queue"),
    )
    corrupt_plan = replace(plan, generated_work_routes=tuple(generated_routes))
    corrupt_fingerprint = authority_fingerprint(corrupt_plan)
    corrupt_digest = ContentAddressedByteStore(cas_root).put_bytes(
        dumps_cas_object(encode_selected_compiled_plan(corrupt_plan))
    )
    with sqlite3.connect(db_path) as connection:
        _replace_plan_authority_fingerprint(
            connection,
            old_fingerprint=fingerprint,
            new_fingerprint=corrupt_fingerprint,
        )
        connection.execute(
            "UPDATE admitted_plan_pins SET selected_plan_digest = ?",
            (corrupt_digest,),
        )
        connection.execute(
            "UPDATE default_plan SET selected_plan_digest = ?",
            (corrupt_digest,),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="selected enqueue route queue_family_id",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("mutate", "match"),
    (
        (
            lambda plan, route: replace(
                route,
                graph_node_id="missing.learning.node",
            ),
            "selected enqueue route graph_node_id",
        ),
        (
            lambda plan, route: replace(
                route,
                stage_kind_id=StageKindId("missing_learning_stage"),
            ),
            "selected enqueue route stage_kind_id",
        ),
        (
            lambda plan, route: replace(
                route,
                payload_schema_id=ArtifactSchemaId("missing.learning.payload"),
            ),
            "selected enqueue route payload_schema_id",
        ),
        (
            lambda plan, route: replace(
                route,
                stage_kind_id=StageKindId("professor"),
                graph_node_id="learning.standard.professor",
            ),
            "target stage input queue",
        ),
        (
            lambda plan, route: replace(
                route,
                runner_binding_id=RunnerBindingId("planning.lad.local_runner"),
            ),
            "runner_binding_id must match target stage",
        ),
        (
            lambda plan, route: route,
            "runner_binding_id must list target stage_kind_id",
        ),
    ),
)
def test_restart_refuses_selected_generated_route_structural_corruption(
    tmp_path: Path,
    mutate,
    match: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.admitted_state(plan, fingerprint)
    generated_routes = list(plan.generated_work_routes)
    route_index = (
        next(
            index
            for index, route in enumerate(generated_routes)
            if route.id == "learning.trigger.librarian"
        )
        if match == "runner_binding_id must list target stage_kind_id"
        else 0
    )
    generated_routes[route_index] = mutate(plan, generated_routes[route_index])
    runner_bindings = (
        tuple(
            replace(
                runner,
                stage_kind_ids=tuple(
                    stage_id
                    for stage_id in runner.stage_kind_ids
                    if stage_id != StageKindId("librarian")
                ),
            )
            if runner.id == RunnerBindingId("learning.standard.local_runner")
            else runner
            for runner in plan.runner_bindings
        )
        if match == "runner_binding_id must list target stage_kind_id"
        else plan.runner_bindings
    )
    corrupt_plan = replace(
        plan,
        generated_work_routes=tuple(generated_routes),
        runner_bindings=runner_bindings,
    )
    db_path, cas_root = _persist_with_replaced_selected_plan(
        tmp_path,
        state=state,
        old_fingerprint=fingerprint,
        corrupt_plan=corrupt_plan,
    )

    with pytest.raises(StorageIntegrityError, match=match):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("mutate", "match"),
    (
        (
            lambda policies: [
                replace(policy, coexist_partition_ids=(PartitionId("learning"),))
                if policy.partition_id == PartitionId("learning")
                else policy
                for policy in policies
            ],
            "selected concurrency policy self coexist",
        ),
        (
            lambda policies: [
                replace(
                    policy,
                    coexist_partition_ids=(
                        PartitionId("planning"),
                        PartitionId("planning"),
                    ),
                )
                if policy.partition_id == PartitionId("learning")
                else policy
                for policy in policies
            ],
            "selected concurrency policy coexist_partition_ids must be unique",
        ),
        (
            lambda policies: [
                replace(policy, coexist_partition_ids=())
                if policy.partition_id == PartitionId("planning")
                else policy
                for policy in policies
            ],
            "selected concurrency policy coexist_partition_ids must be symmetric",
        ),
    ),
)
def test_restart_refuses_selected_concurrency_policy_shape_corruption(
    tmp_path: Path,
    mutate,
    match: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.admitted_state(plan, fingerprint)
    corrupt_plan = replace(
        plan,
        concurrency_policies=tuple(mutate(list(plan.concurrency_policies))),
    )
    db_path, cas_root = _persist_with_replaced_selected_plan(
        tmp_path,
        state=state,
        old_fingerprint=fingerprint,
        corrupt_plan=corrupt_plan,
    )

    with pytest.raises(StorageIntegrityError, match=match):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize("resolution_kind", ("active", "resume", "close", "revise"))
def test_restart_preserves_learning_recovery_intervention_records(
    tmp_path: Path,
    resolution_kind: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state, wait = lad_learning.learning_blocked_wait_state(
        plan,
        fingerprint,
        input_id=f"observe-analyst-blocked-restart-{resolution_kind}",
    )

    if resolution_kind == "resume":
        state = lad_learning.apply_accepted_input(
            state,
            OperatorResumeWait(
                "operator-resume-learning-restart",
                selected_plan_ref=wait.selected_plan_ref,
                wait_id=wait.wait_id,
                lineage_id=wait.lineage_id,
                actor_id="local_operator",
                actor_kind="local_operator",
                payload={},
            ),
            lad_learning.context(
                "operator-resume-learning-restart",
                activation_id="activation-learning-restart-resumed",
            ),
        )
    elif resolution_kind == "close":
        state = lad_learning.apply_accepted_input(
            state,
            OperatorCloseWait(
                "operator-close-learning-restart",
                selected_plan_ref=wait.selected_plan_ref,
                wait_id=wait.wait_id,
                lineage_id=wait.lineage_id,
                actor_id="local_operator",
                actor_kind="local_operator",
                payload={},
            ),
            lad_learning.context("operator-close-learning-restart"),
        )
    elif resolution_kind == "revise":
        state = lad_learning.apply_accepted_input(
            state,
            OperatorReviseWait(
                "operator-revise-learning-restart",
                selected_plan_ref=wait.selected_plan_ref,
                wait_id=wait.wait_id,
                lineage_id=wait.lineage_id,
                actor_id="local_operator",
                actor_kind="local_operator",
                payload=lad_learning.learning_payload(
                    request_id="operator-revised-restart",
                    body="Operator revised the Learning request before restart.",
                ),
            ),
            lad_learning.context(
                "operator-revise-learning-restart",
                work_item_id="work-operator-revised-restart",
                activation_id="activation-operator-revised-restart",
            ),
        )

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded == state
    loaded_wait = loaded.operator_waits[wait.wait_id]
    assert loaded_wait.operator_wait_id == wait.operator_wait_id
    assert loaded_wait.source_action_id == wait.source_action_id
    assert loaded_wait.source_work_item_id == wait.source_work_item_id
    assert loaded_wait.source_activation_id == wait.source_activation_id
    assert loaded_wait.source_run_id == wait.source_run_id
    assert loaded_wait.source_graph_node_id == wait.source_graph_node_id
    assert loaded_wait.source_stage_kind_id == wait.source_stage_kind_id
    assert loaded_wait.source_runner_binding_id == wait.source_runner_binding_id
    assert loaded_wait.source_queue_family_id == wait.source_queue_family_id
    assert loaded_wait.source_artifact_id == wait.source_artifact_id
    assert loaded_wait.selected_plan_fingerprint == fingerprint


def test_restart_preserves_refused_learning_operator_wait_decision(
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state, wait = lad_learning.learning_blocked_wait_state(plan, fingerprint)

    decision = decide(
        state,
        OperatorReviseWait(
            "operator-revise-learning-refused-restart",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=wait.lineage_id,
            actor_id="local_operator",
            actor_kind="local_operator",
            payload={"request_id": "missing-body"},
        ),
        lad_learning.context(
            "operator-revise-learning-refused-restart",
            work_item_id="work-refused-learning-revision",
            activation_id="activation-refused-learning-revision",
        ),
    )
    refused = apply(state, decision)

    loaded = persist_and_load_runtime_state(tmp_path, refused)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_operator_wait_payload_schema"
    assert loaded == refused
    assert (
        loaded.receipts["operator-revise-learning-refused-restart"].accepted
        is False
    )
    assert loaded.refusals[-1].input_id == "operator-revise-learning-refused-restart"
    assert loaded.refusals[-1].reason == "invalid_operator_wait_payload_schema"
    assert loaded.operator_waits[wait.wait_id].status == "active"
    assert "work-refused-learning-revision" not in loaded.work_items
    assert "activation-refused-learning-revision" not in loaded.activations


def test_restart_refuses_learning_recovery_authority_drift(
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state, _wait = lad_learning.learning_blocked_wait_state(plan, fingerprint)
    corrupt_actions = tuple(
        replace(action, action_kind="block_work_item")
        if str(action.id) == "learning.close_analyst_blocked"
        else action
        for action in plan.terminal_actions
    )
    corrupt_plan = replace(plan, terminal_actions=corrupt_actions)
    db_path, cas_root = _persist_with_replaced_selected_plan(
        tmp_path,
        state=state,
        old_fingerprint=fingerprint,
        corrupt_plan=corrupt_plan,
    )

    with pytest.raises(
        StorageIntegrityError,
        match=_OBSERVATION_EVIDENCE_AUTHORITY_ERROR,
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_learning_operator_wait_revise_schema_authority_drift(
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state, _wait = lad_learning.learning_blocked_wait_state(plan, fingerprint)
    corrupt_waits = tuple(
        replace(
            wait,
            payload_schema_id=ArtifactSchemaId(
                lad_learning.LEARNING_REPORT_SCHEMA_ID
            ),
        )
        if str(wait.id) == "learning.analyst_blocked_wait"
        else wait
        for wait in plan.operator_waits
    )
    corrupt_plan = replace(plan, operator_waits=corrupt_waits)
    db_path, cas_root = _persist_with_replaced_selected_plan(
        tmp_path,
        state=state,
        old_fingerprint=fingerprint,
        corrupt_plan=corrupt_plan,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="operator_waits selected revise payload schema must match target route",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("field_name", "value", "match"),
    (
        (
            "target_queue_family_id",
            QueueFamilyId("task"),
            "operator_waits selected revise target queue family must be target "
            "stage input",
        ),
        (
            "target_queue_family_id",
            QueueFamilyId("missing_queue"),
            "operator_waits selected revise target queue family must reference "
            "queue_families",
        ),
        (
            "target_stage_kind_id",
            StageKindId("missing_stage"),
            "operator_waits selected revise target stage kind must reference "
            "stage_kinds",
        ),
        (
            "target_graph_node_id",
            "learning.standard.professor",
            "operator_waits selected revise target must match selected route",
        ),
        (
            "target_graph_node_id",
            "missing.learning.node",
            "operator_waits selected revise target graph node must reference graphs",
        ),
        (
            "target_runner_binding_id",
            RunnerBindingId("planning.lad.local_runner"),
            "operator_waits selected revise target runner binding must match "
            "target stage",
        ),
        (
            "target_runner_binding_id",
            RunnerBindingId("missing.runner"),
            "operator_waits selected revise target runner binding must reference "
            "runner_bindings",
        ),
    ),
)
def test_restart_refuses_learning_operator_wait_revise_target_authority_drift_with_coherent_wait_id(  # noqa: E501
    tmp_path: Path,
    field_name: str,
    value: object,
    match: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state, wait = lad_learning.learning_blocked_wait_state(plan, fingerprint)
    corrupt_waits = tuple(
        replace(wait_declaration, **{field_name: value})
        if str(wait_declaration.id) == "learning.analyst_blocked_wait"
        else wait_declaration
        for wait_declaration in plan.operator_waits
    )
    corrupt_plan = replace(plan, operator_waits=corrupt_waits)
    corrupt_fingerprint = authority_fingerprint(corrupt_plan)
    db_path, cas_root = _persist_with_replaced_selected_plan(
        tmp_path,
        state=state,
        old_fingerprint=fingerprint,
        corrupt_plan=corrupt_plan,
    )
    corrupt_wait_id = _operator_wait_record_id(
        authority_fingerprint=corrupt_fingerprint,
        operator_wait_id=str(wait.operator_wait_id),
        lineage_id=wait.lineage_id,
        created_by_input_id=wait.created_input_id,
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE operator_waits SET wait_id = ? WHERE wait_id = ?",
            (corrupt_wait_id, wait.wait_id),
        )

    with pytest.raises(StorageIntegrityError, match=match):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column", "value", "match"),
    (
        ("status", "stale", "operator_waits.status is unsupported"),
        ("actor_kind", "runtime", "operator_waits.actor_kind must match selected"),
        (
            "resolution_kind",
            "delegate_to_learning",
            "operator_waits.resolution_kind must be selected",
        ),
    ),
)
def test_restart_refuses_learning_intervention_status_drift(
    tmp_path: Path,
    column: str,
    value: str,
    match: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state, wait = lad_learning.learning_blocked_wait_state(plan, fingerprint)
    state = lad_learning.apply_accepted_input(
        state,
        OperatorResumeWait(
            "operator-resume-learning-status-drift",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=wait.lineage_id,
            actor_id="local_operator",
            actor_kind="local_operator",
            payload={},
        ),
        lad_learning.context(
            "operator-resume-learning-status-drift",
            activation_id="activation-learning-status-drift-resumed",
        ),
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        if column == "status":
            connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            f"UPDATE operator_waits SET {column} = ? WHERE wait_id = ?",
            (value, wait.wait_id),
        )

    with pytest.raises(StorageIntegrityError, match=match):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column", "value", "match"),
    (
        (
            "source_work_item_id",
            "missing-learning-work",
            "operator_waits.source_work_item_id must reference work_items",
        ),
        (
            "source_activation_id",
            "missing-learning-activation",
            "operator_waits.source_activation_id must reference activations",
        ),
        (
            "source_run_id",
            "missing-learning-run",
            "operator_waits.source_run_id must reference runs",
        ),
        (
            "source_graph_node_id",
            "learning.standard.professor",
            "operator_waits source context fields must match recorded source",
        ),
        (
            "source_stage_kind_id",
            "professor",
            "operator_waits source context fields must match recorded source",
        ),
        (
            "source_queue_family_id",
            "stage_result",
            "operator_waits source context fields must match recorded source",
        ),
        (
            "source_runner_binding_id",
            "planning.lad.local_runner",
            "operator_waits source context fields must match recorded source",
        ),
        (
            "source_action_id",
            "learning.close_professor_blocked",
            "operator_waits.source_action_id must reference wait source actions",
        ),
    ),
)
def test_restart_refuses_learning_operator_wait_source_context_drift(
    tmp_path: Path,
    column: str,
    value: str,
    match: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state, wait = lad_learning.learning_blocked_wait_state(plan, fingerprint)
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE operator_waits SET {column} = ? WHERE wait_id = ?",
            (value, wait.wait_id),
        )

    with pytest.raises(StorageIntegrityError, match=match):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_closure_root_drift_with_learning_active(
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.planning_closure_with_generated_learning_state(
        plan,
        fingerprint,
        active_learning=True,
    )
    assert persist_and_load_runtime_state(tmp_path, state) == state
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE closure_targets
            SET closure_root_work_item_id = ?
            WHERE closure_target_id = ?
            """,
            ("wrong-root-work-item", "closure-target-learning"),
        )

    with pytest.raises(StorageIntegrityError, match="closure_root_work_item_id"):
        load_runtime_state(db_path, cas_root)


def test_restart_preserves_learning_status_source_rows(tmp_path: Path) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _learning_effect_reconciled_state(plan, fingerprint)

    loaded = persist_and_load_runtime_state(tmp_path, state)
    status = operator_status(loaded)

    effect = next(iter(status.effects))
    assert effect.selected_plan_fingerprint == fingerprint
    assert effect.status == "applied"
    assert effect.source_input_id == "observe-curator-complete"
    assert effect.source_action_id == "learning.close_curator_complete"
    assert effect.terminal_action_id == "learning.close_curator_complete"
    assert effect.source_work_item_id == "work-curator"
    assert effect.source_activation_id == "activation-curator"
    assert effect.source_stage_kind_id == "curator"
    assert effect.source_queue_family_id == "stage_result"
    assert effect.reconciliation_id == (
        "transition-reconcile-learning-effect:reconciliation"
    )
    assert any(
        artifact.artifact_id == effect.artifact_id
        and artifact.source_input_id == effect.source_input_id
        and artifact.terminal_action_id == effect.terminal_action_id
        for artifact in status.artifacts
    )


def test_restart_refuses_closure_root_drift_with_learning_effects(
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.planning_closure_with_generated_learning_state(
        plan,
        fingerprint,
        active_learning=True,
    )
    state = lad_learning.observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-closure-librarian",
        marker="LIBRARIAN_COMPLETE",
        artifact=lad_learning.artifact_payload(
            lad_learning.LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID
        ),
        input_id="observe-librarian-complete-restart-effect",
    )
    assert state.effect_proposals
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE closure_targets
            SET closure_root_work_item_id = ?
            WHERE closure_target_id = ?
            """,
            ("wrong-root-work-item", "closure-target-learning"),
        )

    with pytest.raises(StorageIntegrityError, match="closure_root_work_item_id"):
        load_runtime_state(db_path, cas_root)


def test_restart_preserves_learning_after_closed_source(tmp_path: Path) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.closed_source_learning_effect_state(
        plan,
        fingerprint,
        reconciliation_status="applied",
    )

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded == state
    source_close = loaded.closed_work_items["work-consultant-closed-source"]
    fanout = lad_learning.closed_source_learning_fanout(loaded)
    effect = next(iter(loaded.effect_proposals.values()))
    reconciliation = next(iter(loaded.effect_reconciliations.values()))
    assert str(source_close.action_id) == "execution.close_consultant_needs_plan"
    assert fanout.source_work_item_id == source_close.work_item_id
    assert fanout.source_run_id == source_close.source_run_id
    assert fanout.source_action_id == source_close.action_id
    assert effect.lineage_id == fanout.lineage_id
    assert reconciliation.effect_id == effect.effect_id


@pytest.mark.parametrize(
    ("drift", "match"),
    (
        (
            "resolved_wait_missing_closed_source",
            "operator_waits.closed_work_item_ids",
        ),
        (
            "artifact_source_action",
            _ARTIFACT_PAYLOAD_AUTHORITY_ERROR,
        ),
        ("fanout_target_lineage", "fanout target lineage must match fanout record"),
        (
            "fanout_target_queue",
            "fanout target queue family must match fanout record",
        ),
        (
            "artifact_schema",
            _ARTIFACT_PAYLOAD_AUTHORITY_ERROR,
        ),
        (
            "artifact_source_input",
            _ARTIFACT_SOURCE_AUTHORITY_ERROR,
        ),
        (
            "artifact_payload_digest",
            _ARTIFACT_PAYLOAD_AUTHORITY_ERROR,
        ),
        (
            "artifact_source_run",
            _ARTIFACT_SOURCE_AUTHORITY_ERROR,
        ),
        (
            "effect_plan_id",
            "effect_proposals PlanRef must match admitted plan pin",
        ),
        (
            "effect_plan_fingerprint",
            "effect_proposals.selected_plan_fingerprint must match PlanRef",
        ),
        (
            "effect_artifact_payload_digest",
            "effect_proposals.artifact_payload_digest must match artifact",
        ),
        (
            "effect_source_run",
            "effect_proposals.source_run_id",
        ),
        (
            "effect_source_work_item",
            "effect_proposals.source_work_item_id",
        ),
        (
            "effect_source_activation",
            "effect_proposals.source_activation_id",
        ),
        (
            "effect_graph_node",
            "effect_proposals.source_graph_node_id must match artifact",
        ),
        (
            "effect_stage_kind",
            "effect_proposals.source_stage_kind_id must match artifact",
        ),
        (
            "effect_runner_binding",
            "effect_proposals.source_runner_binding_id must match source run",
        ),
        (
            "effect_queue_family",
            "effect_proposals.source_queue_family_id must match source activation",
        ),
        (
            "effect_provider",
            "effect_proposals.effect_declaration_id must reference selected effect",
        ),
        (
            "effect_capability_policy",
            "effect_proposals.effect_declaration_id must reference selected effect",
        ),
        (
            "effect_target_skill",
            "effect_proposals.target_skill_id must match artifact payload",
        ),
        ("effect_status", "effect_proposals.status must be pending"),
        (
            "reconciliation_status",
            "effect_reconciliations.status",
        ),
        (
            "reconciliation_effect_id",
            "effect_reconciliations.effect_id",
        ),
        (
            "reconciliation_provider",
            "effect_reconciliations.provider_ref must match proposal",
        ),
        (
            "wait_operator_id",
            "operator_waits.source_action_id must reference wait source actions",
        ),
        (
            "wait_source_action",
            "operator_waits.source_action_id must reference wait source actions",
        ),
        (
            "wait_source_graph_node",
            "operator_waits source context fields must match recorded source",
        ),
        (
            "wait_actor_on_active",
            "operator_waits active wait must not carry resolution fields",
        ),
        ("wait_status", "operator_waits.status is unsupported"),
        (
            "resolved_wait_actor_kind",
            "operator_waits.actor_kind must match selected operator_wait",
        ),
        (
            "resolved_wait_resolution",
            "operator_waits.resolution_kind must be selected",
        ),
        (
            "closed_source_created_input",
            "closed_work_items.created_by_input_id must match source runner "
            "observation",
        ),
    ),
)
def test_restart_refuses_c3_family_cross_record_drift(
    tmp_path: Path,
    drift: str,
    match: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    if drift.startswith("wait_"):
        state, wait = lad_learning.closed_source_learning_blocked_wait_state(
            plan,
            fingerprint,
        )
    elif drift.startswith("resolved_wait_"):
        state, wait = lad_learning.closed_source_learning_blocked_wait_state(
            plan,
            fingerprint,
        )
        state = lad_learning.apply_accepted_input(
            state,
            OperatorCloseWait(
                "operator-close-c3-family-cross-record",
                selected_plan_ref=wait.selected_plan_ref,
                wait_id=wait.wait_id,
                lineage_id=wait.lineage_id,
                actor_id="local_operator",
                actor_kind="local_operator",
                payload={},
            ),
            lad_learning.context("operator-close-c3-family-cross-record"),
        )
    else:
        state = lad_learning.closed_source_learning_effect_state(
            plan,
            fingerprint,
            reconciliation_status="applied",
        )
        wait = None

    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        if drift == "resolved_wait_missing_closed_source":
            assert wait is not None
            connection.execute(
                "DELETE FROM closed_work_items WHERE work_item_id = ?",
                (wait.source_work_item_id,),
            )
        elif drift == "artifact_source_action":
            connection.execute(
                """
                UPDATE artifacts
                SET source_action_id = ?
                WHERE created_by_input_id = ?
                """,
                (
                    "execution.route_doublechecker_pass",
                    "observe-consultant-closed-source",
                ),
            )
        elif drift == "fanout_target_lineage":
            connection.execute(
                """
                UPDATE work_items
                SET lineage_id = ?
                WHERE work_item_id = (
                    SELECT target_work_item_id
                    FROM fanout_records
                    WHERE item_key = ?
                )
                """,
                ("wrong-lineage", "closed-source-learning"),
            )
            connection.execute(
                """
                UPDATE activations
                SET lineage_id = ?
                WHERE activation_id = (
                    SELECT target_activation_id
                    FROM fanout_records
                    WHERE item_key = ?
                )
                """,
                ("wrong-lineage", "closed-source-learning"),
            )
        elif drift == "fanout_target_queue":
            connection.execute(
                """
                UPDATE fanout_records
                SET target_queue_family_id = ?
                WHERE item_key = ?
                """,
                ("stage_result", "closed-source-learning"),
            )
        elif drift == "artifact_schema":
            connection.execute(
                """
                UPDATE artifacts
                SET artifact_schema_id = ?
                WHERE created_by_input_id = ?
                """,
                (
                    lad_learning.LEARNING_STAGE_RESULT_SCHEMA_ID,
                    "observe-closed-source-curator-complete",
                ),
            )
        elif drift == "artifact_source_input":
            connection.execute(
                """
                UPDATE artifacts
                SET created_by_input_id = ?
                WHERE created_by_input_id = ?
                """,
                (
                    "claim-closed-source-curator",
                    "observe-closed-source-curator-complete",
                ),
            )
        elif drift == "artifact_payload_digest":
            connection.execute(
                """
                UPDATE artifacts
                SET artifact_payload_digest = ?
                WHERE created_by_input_id = ?
                """,
                (
                    f"sha256:{'1' * 64}",
                    "observe-closed-source-curator-complete",
                ),
            )
        elif drift == "artifact_source_run":
            connection.execute(
                """
                UPDATE artifacts
                SET source_run_id = ?
                WHERE created_by_input_id = ?
                """,
                (
                    "missing-run",
                    "observe-closed-source-curator-complete",
                ),
            )
        elif drift == "effect_plan_id":
            connection.execute(
                "UPDATE effect_proposals SET plan_id = ?",
                ("wrong-plan",),
            )
        elif drift == "effect_plan_fingerprint":
            connection.execute(
                """
                UPDATE effect_proposals
                SET selected_plan_fingerprint = ?
                """,
                (f"sha256:{'0' * 64}",),
            )
        elif drift == "effect_artifact_payload_digest":
            connection.execute(
                "UPDATE effect_proposals SET artifact_payload_digest = ?",
                (f"sha256:{'2' * 64}",),
            )
        elif drift == "effect_source_run":
            connection.execute(
                "UPDATE effect_proposals SET source_run_id = ?",
                ("missing-run",),
            )
        elif drift == "effect_source_work_item":
            connection.execute(
                "UPDATE effect_proposals SET source_work_item_id = ?",
                ("missing-work-item",),
            )
        elif drift == "effect_source_activation":
            connection.execute(
                "UPDATE effect_proposals SET source_activation_id = ?",
                ("missing-activation",),
            )
        elif drift == "effect_graph_node":
            connection.execute(
                """
                UPDATE effect_proposals
                SET source_graph_node_id = ?
                """,
                ("learning.standard.professor",),
            )
        elif drift == "effect_stage_kind":
            connection.execute(
                """
                UPDATE effect_proposals
                SET source_stage_kind_id = ?
                """,
                ("professor",),
            )
        elif drift == "effect_runner_binding":
            connection.execute(
                """
                UPDATE effect_proposals
                SET source_runner_binding_id = ?
                """,
                ("planning.lad.local_runner",),
            )
        elif drift == "effect_queue_family":
            connection.execute(
                """
                UPDATE effect_proposals
                SET source_queue_family_id = ?
                """,
                ("learning_request",),
            )
        elif drift == "effect_provider":
            connection.execute(
                "UPDATE effect_proposals SET provider_ref = ?",
                ("provider.real.workspace",),
            )
        elif drift == "effect_capability_policy":
            connection.execute(
                "UPDATE effect_proposals SET capability_policy_ref = ?",
                ("policy.real.side_effects",),
            )
        elif drift == "effect_target_skill":
            connection.execute(
                "UPDATE effect_proposals SET target_skill_id = ?",
                ("wrong.skill",),
            )
        elif drift == "effect_status":
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(
                "UPDATE effect_proposals SET status = ?",
                ("applied",),
            )
        elif drift == "reconciliation_status":
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(
                "UPDATE effect_reconciliations SET status = ?",
                ("stale",),
            )
        elif drift == "reconciliation_effect_id":
            connection.execute(
                "UPDATE effect_reconciliations SET effect_id = ?",
                ("missing-effect",),
            )
        elif drift == "reconciliation_provider":
            connection.execute(
                "UPDATE effect_reconciliations SET provider_ref = ?",
                ("provider.real.workspace",),
            )
        elif drift == "wait_operator_id":
            assert wait is not None
            connection.execute(
                "UPDATE operator_waits SET operator_wait_id = ? WHERE wait_id = ?",
                ("learning.professor_blocked_wait", wait.wait_id),
            )
        elif drift == "wait_source_action":
            assert wait is not None
            connection.execute(
                "UPDATE operator_waits SET source_action_id = ? WHERE wait_id = ?",
                ("learning.close_professor_blocked", wait.wait_id),
            )
        elif drift == "wait_source_graph_node":
            assert wait is not None
            connection.execute(
                """
                UPDATE operator_waits
                SET source_graph_node_id = ?
                WHERE wait_id = ?
                """,
                ("learning.standard.professor", wait.wait_id),
            )
        elif drift == "wait_actor_on_active":
            assert wait is not None
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(
                "UPDATE operator_waits SET actor_id = ? WHERE wait_id = ?",
                ("local_operator", wait.wait_id),
            )
        elif drift == "wait_status":
            assert wait is not None
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(
                "UPDATE operator_waits SET status = ? WHERE wait_id = ?",
                ("stale", wait.wait_id),
            )
        elif drift == "resolved_wait_actor_kind":
            assert wait is not None
            connection.execute(
                "UPDATE operator_waits SET actor_kind = ? WHERE wait_id = ?",
                ("remote_operator", wait.wait_id),
            )
        elif drift == "resolved_wait_resolution":
            assert wait is not None
            connection.execute(
                "UPDATE operator_waits SET resolution_kind = ? WHERE wait_id = ?",
                ("delegate_to_learning", wait.wait_id),
            )
        elif drift == "closed_source_created_input":
            connection.execute(
                """
                UPDATE closed_work_items
                SET created_by_input_id = ?
                WHERE work_item_id = ?
                """,
                (
                    "claim-consultant-closed-source",
                    "work-consultant-closed-source",
                ),
            )
        else:  # pragma: no cover - parameter table guard
            raise AssertionError(f"unhandled drift case: {drift}")

    with pytest.raises(StorageIntegrityError, match=match):
        load_runtime_state(db_path, cas_root)


def _closure_root_c3e_restart_state(
    plan,
    fingerprint: str,
    state_kind: str,
):
    if state_kind == "active_wait":
        state, _wait = lad_learning.closure_librarian_blocked_wait_state(
            plan,
            fingerprint,
            input_id="observe-closure-librarian-blocked-active-restart-c3e",
        )
        return state
    if state_kind in {"resolved_close", "resolved_revise"}:
        state, wait = lad_learning.closure_librarian_blocked_wait_state(
            plan,
            fingerprint,
            input_id=f"observe-closure-librarian-blocked-{state_kind}",
        )
        if state_kind == "resolved_close":
            transition_input = OperatorCloseWait(
                "operator-close-closure-librarian-restart-c3e",
                selected_plan_ref=wait.selected_plan_ref,
                wait_id=wait.wait_id,
                lineage_id=wait.lineage_id,
                actor_id="local_operator",
                actor_kind="local_operator",
                payload={},
            )
            transition_context = lad_learning.context(
                "operator-close-closure-librarian-restart-c3e"
            )
        else:
            transition_input = OperatorReviseWait(
                "operator-revise-closure-librarian-restart-c3e",
                selected_plan_ref=wait.selected_plan_ref,
                wait_id=wait.wait_id,
                lineage_id=wait.lineage_id,
                actor_id="local_operator",
                actor_kind="local_operator",
                payload=lad_learning.learning_payload(
                    request_id="closure-librarian-revised-restart",
                    body="Operator revised the closure-root Learning request.",
                ),
            )
            transition_context = lad_learning.context(
                "operator-revise-closure-librarian-restart-c3e",
                work_item_id="work-closure-librarian-revised-restart",
                activation_id="activation-closure-librarian-revised-restart",
            )
        return lad_learning.apply_accepted_input(
            state,
            transition_input,
            transition_context,
        )
    if state_kind == "terminal_noop":
        return lad_learning.closure_librarian_terminal_state(
            plan,
            fingerprint,
            outcome="noop",
            input_id="observe-closure-librarian-noop-restart-c3e",
        )
    if state_kind == "effect_pending":
        return lad_learning.closure_librarian_effect_state(
            plan,
            fingerprint,
        )
    if state_kind.startswith("effect_"):
        return lad_learning.closure_librarian_effect_state(
            plan,
            fingerprint,
            reconciliation_status=state_kind.removeprefix("effect_"),
        )
    raise AssertionError(f"unhandled C3E closure-root state kind: {state_kind}")


@pytest.mark.parametrize(
    "state_kind",
    (
        "active_wait",
        "resolved_close",
        "resolved_revise",
        "terminal_noop",
        "effect_pending",
        "effect_applied",
        "effect_no_op",
        "effect_refused",
    ),
)
def test_restart_refuses_closure_root_drift_with_learning_recovery_wait_intervention_and_quarantine(  # noqa: E501
    tmp_path: Path,
    state_kind: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _closure_root_c3e_restart_state(plan, fingerprint, state_kind)
    legal_root = tmp_path / "legal"
    legal_root.mkdir()
    assert persist_and_load_runtime_state(legal_root, state) == state
    corrupt_root = tmp_path / "corrupt"
    corrupt_root.mkdir()
    db_path, cas_root = runtime_store_paths(corrupt_root)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE closure_targets
            SET closure_root_work_item_id = ?
            WHERE closure_target_id = ?
            """,
            ("wrong-root-work-item", "closure-target-learning"),
        )

    with pytest.raises(StorageIntegrityError, match="closure_root_work_item_id"):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    "aftermath",
    (
        "professor_noop",
        "professor_blocked",
        "professor_complete_curator_complete",
        "professor_complete_curator_noop",
        "professor_complete_curator_blocked",
        "librarian_complete",
        "librarian_noop",
        "librarian_blocked",
    ),
)
def test_restart_preserves_learning_stage_artifact_route_close_and_block(
    tmp_path: Path,
    aftermath: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state, expected_input_ids = _learning_aftermath_state(
        plan,
        fingerprint,
        aftermath,
    )

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded == state
    assert expected_input_ids <= {
        artifact.created_by_input_id for artifact in loaded.artifacts.values()
    }
    assert len(
        {
            artifact.created_by_input_id
            for artifact in loaded.artifacts.values()
            if artifact.created_by_input_id in expected_input_ids
        }
    ) == len(expected_input_ids)
    assert all(
        str(artifact.schema_id)
        != lad_learning.LEARNING_STAGE_RESULT_SCHEMA_ID
        for artifact in loaded.artifacts.values()
        if artifact.created_by_input_id in expected_input_ids
    )


@pytest.mark.parametrize(
    ("column", "value", "match"),
    (
        (
            "artifact_schema_id",
            lad_learning.LEARNING_STAGE_RESULT_SCHEMA_ID,
            _ARTIFACT_PAYLOAD_AUTHORITY_ERROR,
        ),
        (
            "source_action_id",
            "learning.route_professor_complete",
            _ARTIFACT_PAYLOAD_AUTHORITY_ERROR,
        ),
        (
            "source_action_id",
            "learning.close_analyst_noop",
            _ARTIFACT_PAYLOAD_AUTHORITY_ERROR,
        ),
        (
            "source_stage_kind_id",
            "professor",
            _ARTIFACT_SOURCE_AUTHORITY_ERROR,
        ),
        (
            "source_graph_node_id",
            "learning.standard.professor",
            _ARTIFACT_SOURCE_AUTHORITY_ERROR,
        ),
        (
            "artifact_payload_digest",
            f"sha256:{'0' * 64}",
            _ARTIFACT_PAYLOAD_AUTHORITY_ERROR,
        ),
        (
            "created_by_input_id",
            "claim-analyst",
            _ARTIFACT_SOURCE_AUTHORITY_ERROR,
        ),
        (
            "transition_id",
            "transition-claim-analyst",
            _ARTIFACT_SOURCE_AUTHORITY_ERROR,
        ),
        (
            "source_run_id",
            "missing-learning-run",
            _ARTIFACT_SOURCE_AUTHORITY_ERROR,
        ),
    ),
)
def test_restart_refuses_learning_artifact_authority_drift(
    tmp_path: Path,
    column: str,
    value: str,
    match: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _learning_route_artifact_state(plan, fingerprint)
    artifact_id = next(iter(state.artifacts))
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE artifacts SET {column} = ? WHERE artifact_id = ?",
            (value, artifact_id),
        )

    with pytest.raises(StorageIntegrityError, match=match):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("artifact_schema_id", "extra_source_stage_id"),
    (
        (lad_learning.LEARNING_PROFESSOR_NOTES_SCHEMA_ID, None),
        (lad_learning.LEARNING_SKILL_UPDATE_SCHEMA_ID, "analyst"),
    ),
)
def test_restart_refuses_learning_route_artifact_schema_contract_drift(
    tmp_path: Path,
    artifact_schema_id: str,
    extra_source_stage_id: str | None,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _learning_route_artifact_state(plan, fingerprint)
    stage_kinds = (
        tuple(
            replace(
                stage,
                artifact_schema_ids=(
                    *stage.artifact_schema_ids,
                    ArtifactSchemaId(artifact_schema_id),
                ),
            )
            if str(stage.id) == extra_source_stage_id
            else stage
            for stage in plan.stage_kinds
        )
        if extra_source_stage_id is not None
        else plan.stage_kinds
    )
    terminal_actions = tuple(
        replace(
            action,
            artifact_schema_id=ArtifactSchemaId(artifact_schema_id),
        )
        if str(action.id) == "learning.route_analyst_complete"
        else action
        for action in plan.terminal_actions
    )
    corrupt_plan = replace(
        plan,
        stage_kinds=stage_kinds,
        terminal_actions=terminal_actions,
    )
    db_path, cas_root = _persist_with_replaced_selected_plan(
        tmp_path,
        state=state,
        old_fingerprint=fingerprint,
        corrupt_plan=corrupt_plan,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="selected terminal route artifact_schema_id",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_preserves_learning_effect_proposal_and_reconciliation(
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _learning_effect_reconciled_state(plan, fingerprint)

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded == state
    assert len(loaded.effect_proposals) == 1
    assert len(loaded.effect_reconciliations) == 1


def test_restart_allows_learning_effect_reconciliation_after_loading_pending_proposal(
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _learning_effect_proposal_state(plan, fingerprint)
    effect_id = next(iter(state.effect_proposals))
    loaded = persist_and_load_runtime_state(tmp_path, state)

    from millrace.contracts.transition import ReconcileEffect

    decision = decide(
        loaded,
        ReconcileEffect(
            "reconcile-loaded-learning-effect",
            effect_id=effect_id,
            provider_ref=lad_learning.FAKE_LOCAL_EFFECT_PROVIDER_REF,
            status="applied",
            result={
                "provider_result_id": "fake-local-result-loaded",
                "summary": "Recorded after restart.",
            },
        ),
        lad_learning.context("reconcile-loaded-learning-effect"),
    )

    assert decision.accepted is True
    after = apply(loaded, decision)
    assert len(after.effect_reconciliations) == 1


def test_restart_allows_learning_effect_reconciliation_replay_after_load(
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _learning_effect_reconciled_state(plan, fingerprint)
    effect_id = next(iter(state.effect_proposals))
    loaded = persist_and_load_runtime_state(tmp_path, state)

    from millrace.contracts.transition import ReconcileEffect

    decision = decide(
        loaded,
        ReconcileEffect(
            "reconcile-loaded-learning-effect-replay",
            effect_id=effect_id,
            provider_ref=lad_learning.FAKE_LOCAL_EFFECT_PROVIDER_REF,
            status="applied",
            result={
                "provider_result_id": "fake-local-result-restart",
                "summary": "Recorded as fake local evidence only.",
            },
        ),
        lad_learning.context("reconcile-loaded-learning-effect-replay"),
    )

    assert decision.accepted is True
    after = apply(loaded, decision)
    assert after.effect_reconciliations == loaded.effect_reconciliations


@pytest.mark.parametrize(
    ("column", "value", "match"),
    (
        (
            "artifact_schema_id",
            lad_learning.LEARNING_REPORT_SCHEMA_ID,
            "effect_proposals.artifact_schema_id must match artifact",
        ),
        (
            "selected_plan_fingerprint",
            f"sha256:{'0' * 64}",
            "effect_proposals.selected_plan_fingerprint must match PlanRef",
        ),
        (
            "source_run_id",
            "missing-learning-run",
            "effect_proposals.source_run_id must reference runs",
        ),
        (
            "provider_ref",
            "provider.real.workspace",
            "effect_proposals.effect_declaration_id must reference selected effect",
        ),
        (
            "source_input_id",
            "claim-curator-effect",
            "effect_proposals.source_input_id must match created_input_id",
        ),
        (
            "target_skill_id",
            "wrong.skill",
            "effect_proposals.target_skill_id must match artifact payload",
        ),
    ),
)
def test_restart_refuses_learning_effect_record_drift(
    tmp_path: Path,
    column: str,
    value: str,
    match: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _learning_effect_reconciled_state(plan, fingerprint)
    effect_id = next(iter(state.effect_proposals))
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE effect_proposals SET {column} = ? WHERE effect_id = ?",
            (value, effect_id),
        )

    with pytest.raises(StorageIntegrityError, match=match):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_learning_effect_correlated_source_action_drift(
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _librarian_effect_reconciled_state(plan, fingerprint)
    effect_id = next(iter(state.effect_proposals))
    artifact_id = state.effect_proposals[effect_id].artifact_id
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE artifacts SET source_action_id = ? WHERE artifact_id = ?",
            ("learning.close_librarian_noop", artifact_id),
        )
        connection.execute(
            """
            UPDATE effect_proposals
            SET source_action_id = ?
            WHERE effect_id = ?
            """,
            ("learning.close_librarian_noop", effect_id),
        )

    with pytest.raises(
        StorageIntegrityError,
        match=_ARTIFACT_PAYLOAD_AUTHORITY_ERROR,
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_learning_effect_target_path_ref_drift(
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _librarian_effect_reconciled_state(plan, fingerprint)
    effect_id = next(iter(state.effect_proposals))
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE effect_proposals
            SET target_path_ref = ?
            WHERE effect_id = ?
            """,
            ("skills/stage/wrong/SKILL.md", effect_id),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="effect_proposals.target_path_ref must match artifact payload",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_learning_effect_reconciliation_status_drift(
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _learning_effect_reconciled_state(plan, fingerprint)
    reconciliation_id = next(iter(state.effect_reconciliations))
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE effect_reconciliations
            SET status = ?
            WHERE reconciliation_id = ?
            """,
            ("stale", reconciliation_id),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="effect_reconciliations.status",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column", "value", "match"),
    (
        (
            "created_input_id",
            "observe-curator-complete-for-effect-restart",
            "effect_reconciliations.created_input_id must match transition",
        ),
        (
            "created_transition_id",
            "transition-observe-curator-complete",
            "effect_reconciliations.created_transition_id must reference "
            "accepted effect reconciliation transition",
        ),
        (
            "fake_local_result_digest",
            "not-a-digest",
            "effect_reconciliations.fake_local_result_digest must be sha256 digest",
        ),
    ),
)
def test_restart_refuses_learning_effect_reconciliation_authority_drift(
    tmp_path: Path,
    column: str,
    value: str,
    match: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _learning_effect_reconciled_state(plan, fingerprint)
    reconciliation_id = next(iter(state.effect_reconciliations))
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            (
                f"UPDATE effect_reconciliations SET {column} = ? "
                "WHERE reconciliation_id = ?"
            ),
            (value, reconciliation_id),
        )
        if column == "created_transition_id":
            connection.execute(
                """
                UPDATE effect_reconciliations
                SET created_input_id = ?
                WHERE reconciliation_id = ?
                """,
                ("observe-curator-complete", reconciliation_id),
            )

    with pytest.raises(StorageIntegrityError, match=match):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_learning_effect_duplicate_or_conflicting_records(
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _learning_effect_reconciled_state(plan, fingerprint)
    reconciliation_id = next(iter(state.effect_reconciliations))
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE effect_reconciliations
            SET effect_id = ?
            WHERE reconciliation_id = ?
            """,
            ("missing-effect", reconciliation_id),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="effect_reconciliations.effect_id must reference effect_proposals",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("action_id", "match"),
    (
        (
            "learning.route_analyst_complete",
            "selected terminal route artifact_schema_id",
        ),
        (
            "learning.close_analyst_noop",
            "selected terminal action artifact_schema_id",
        ),
    ),
)
def test_restart_refuses_learning_stage_result_terminal_reselection(
    tmp_path: Path,
    action_id: str,
    match: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _learning_route_artifact_state(plan, fingerprint)
    terminal_actions = tuple(
        replace(
            action,
            artifact_schema_id=ArtifactSchemaId(
                lad_learning.LEARNING_STAGE_RESULT_SCHEMA_ID
            ),
        )
        if str(action.id) == action_id
        else action
        for action in plan.terminal_actions
    )
    corrupt_plan = replace(plan, terminal_actions=terminal_actions)
    db_path, cas_root = _persist_with_replaced_selected_plan(
        tmp_path,
        state=state,
        old_fingerprint=fingerprint,
        corrupt_plan=corrupt_plan,
    )

    with pytest.raises(StorageIntegrityError, match=match):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("mutate", "match"),
    (
        (
            lambda plan, actions, stages, runners: (
                tuple(
                    replace(action, target_graph_node_id=None)
                    if str(action.id) == "learning.route_analyst_complete"
                    else action
                    for action in actions
                ),
                stages,
                runners,
            ),
            "selected terminal route target_graph_node_id",
        ),
        (
            lambda plan, actions, stages, runners: (
                tuple(
                    replace(
                        action,
                        target_graph_node_id="learning.standard.analyst",
                    )
                    if str(action.id) == "learning.route_analyst_complete"
                    else action
                    for action in actions
                ),
                stages,
                runners,
            ),
            "selected terminal route target_graph_node_id must belong",
        ),
        (
            lambda plan, actions, stages, runners: (
                actions,
                tuple(
                    replace(
                        stage,
                        output_queue_family_ids=tuple(
                            queue_id
                            for queue_id in stage.output_queue_family_ids
                            if queue_id != QueueFamilyId("stage_result")
                        ),
                    )
                    if str(stage.id) == "analyst"
                    else stage
                    for stage in stages
                ),
                runners,
            ),
            "selected terminal route emitted_queue_family_id must be a source",
        ),
        (
            lambda plan, actions, stages, runners: (
                actions,
                tuple(
                    replace(
                        stage,
                        input_queue_family_ids=tuple(
                            queue_id
                            for queue_id in stage.input_queue_family_ids
                            if queue_id != QueueFamilyId("stage_result")
                        ),
                    )
                    if str(stage.id) == "professor"
                    else stage
                    for stage in stages
                ),
                runners,
            ),
            "selected terminal route emitted_queue_family_id must be a target",
        ),
        (
            lambda plan, actions, stages, runners: (
                tuple(
                    replace(
                        action,
                        runner_binding_id=RunnerBindingId("planning.lad.local_runner"),
                    )
                    if str(action.id) == "learning.route_analyst_complete"
                    else action
                    for action in actions
                ),
                stages,
                runners,
            ),
            "selected terminal route runner_binding_id must match target stage",
        ),
        (
            lambda plan, actions, stages, runners: (
                actions,
                stages,
                tuple(
                    replace(
                        runner,
                        stage_kind_ids=tuple(
                            stage_id
                            for stage_id in runner.stage_kind_ids
                            if stage_id != StageKindId("professor")
                        ),
                    )
                    if str(runner.id) == "learning.standard.local_runner"
                    else runner
                    for runner in runners
                ),
            ),
            "selected terminal route runner_binding_id must list target stage",
        ),
    ),
)
def test_restart_refuses_learning_static_route_authority_drift(
    tmp_path: Path,
    mutate,
    match: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _learning_route_artifact_state(plan, fingerprint)
    terminal_actions, stage_kinds, runner_bindings = mutate(
        plan,
        plan.terminal_actions,
        plan.stage_kinds,
        plan.runner_bindings,
    )
    corrupt_plan = replace(
        plan,
        terminal_actions=terminal_actions,
        stage_kinds=stage_kinds,
        runner_bindings=runner_bindings,
    )
    db_path, cas_root = _persist_with_replaced_selected_plan(
        tmp_path,
        state=state,
        old_fingerprint=fingerprint,
        corrupt_plan=corrupt_plan,
    )

    with pytest.raises(StorageIntegrityError, match=match):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize("queue_family_id", ("spec", "task"))
def test_restart_preserves_learning_with_active_selected_foreground(
    tmp_path: Path,
    queue_family_id: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _active_foreground_state(plan, fingerprint, queue_family_id=queue_family_id)
    state = lad_learning.apply_accepted_input(
        state,
        EnqueueWork(
            "enqueue-learning-request",
            queue_family_id=QueueFamilyId("learning_request"),
            payload=lad_learning.learning_payload(),
        ),
        lad_learning.context(
            "enqueue-learning-request",
            work_item_id="work-learning-request",
            activation_id="activation-learning-request",
        ),
    )
    state = lad_learning.claim(
        state,
        activation_id="activation-learning-request",
        run_id="run-learning",
        input_id="claim-learning",
    )

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded == state


def test_restart_refuses_corrupt_coexist_policy_drift(tmp_path: Path) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _active_foreground_state(plan, fingerprint, queue_family_id="spec")
    state = lad_learning.apply_accepted_input(
        state,
        EnqueueWork(
            "enqueue-learning-request",
            queue_family_id=QueueFamilyId("learning_request"),
            payload=lad_learning.learning_payload(),
        ),
        lad_learning.context(
            "enqueue-learning-request",
            work_item_id="work-learning-request",
            activation_id="activation-learning-request",
        ),
    )
    state = lad_learning.claim(
        state,
        activation_id="activation-learning-request",
        run_id="run-learning",
        input_id="claim-learning",
    )
    corrupt_plan = replace(
        plan,
        concurrency_policies=tuple(
            replace(policy, coexist_partition_ids=())
            for policy in plan.concurrency_policies
        ),
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    corrupt_fingerprint = authority_fingerprint(corrupt_plan)
    corrupt_digest = ContentAddressedByteStore(cas_root).put_bytes(
        dumps_cas_object(encode_selected_compiled_plan(corrupt_plan))
    )
    with sqlite3.connect(db_path) as connection:
        _replace_plan_authority_fingerprint(
            connection,
            old_fingerprint=fingerprint,
            new_fingerprint=corrupt_fingerprint,
        )
        connection.execute(
            "UPDATE admitted_plan_pins SET selected_plan_digest = ?",
            (corrupt_digest,),
        )
        connection.execute(
            "UPDATE default_plan SET selected_plan_digest = ?",
            (corrupt_digest,),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="concurrency_policy coexist_partition_ids",
    ):
        load_runtime_state(db_path, cas_root)


def _doublechecker_pass_with_learning_request(state, plan, fingerprint):
    plan_ref = state.default_plan_ref
    assert plan_ref is not None
    work_item = WorkItem(
        ref=WorkItemRef(
            work_item_id="work-doublechecker",
            plan_ref=plan_ref,
            generation=0,
        ),
        queue_family_id=QueueFamilyId("stage_result"),
        payload={
            "artifact_kind": "execution.artifacts.stage_result",
            "summary": "source",
        },
        lineage_id="work-task",
        created_by_input_id="seed-doublechecker",
    )
    activation = Activation(
        activation_id="activation-doublechecker",
        work_item_id="work-doublechecker",
        lineage_id="work-task",
        plan_ref=plan_ref,
        queue_family_id=QueueFamilyId("stage_result"),
        graph_node_id="execution.lad.doublechecker.start",
        stage_kind_id=StageKindId("lad_doublechecker"),
        runner_binding_id=RunnerBindingId("execution.lad.local_runner"),
        generation=0,
        created_by_input_id="seed-doublechecker",
    )
    state = replace(
        state,
        work_items={**state.work_items, work_item.ref.work_item_id: work_item},
        activations={**state.activations, activation.activation_id: activation},
    )
    state = lad_learning.claim(
        state,
        activation_id="activation-doublechecker",
        run_id="run-doublechecker",
        input_id="claim-doublechecker",
    )
    return lad_learning.observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-doublechecker",
        marker="DOUBLECHECK_PASS",
        artifact=lad_learning.source_artifact_with_learning_request(),
        input_id="observe-doublecheck",
        work_item_id="work-updater",
        activation_id="activation-updater",
    )


def _learning_aftermath_state(plan, fingerprint, aftermath: str):
    if aftermath.startswith("librarian_"):
        state = lad_learning.planning_closure_with_generated_learning_state(
            plan,
            fingerprint,
            active_learning=True,
        )
        outcome = aftermath.removeprefix("librarian_")
        action_id, marker, schema_id = {
            "complete": (
                "learning.close_librarian_complete",
                "LIBRARIAN_COMPLETE",
                lad_learning.LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID,
            ),
            "noop": (
                "learning.close_librarian_noop",
                "LIBRARIAN_NOOP",
                lad_learning.LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID,
            ),
            "blocked": (
                "learning.close_librarian_blocked",
                "BLOCKED",
                lad_learning.LEARNING_REPORT_SCHEMA_ID,
            ),
        }[outcome]
        input_id = f"observe-{aftermath.replace('_', '-')}"
        state = lad_learning.observe(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-closure-librarian",
            marker=marker,
            artifact=lad_learning.artifact_payload(schema_id),
            input_id=input_id,
        )
        return state, frozenset({input_id})

    state = _learning_route_artifact_state(plan, fingerprint)
    state = lad_learning.claim(
        state,
        activation_id="activation-professor",
        run_id="run-professor",
        input_id="claim-professor",
    )
    if aftermath == "professor_noop":
        state = lad_learning.observe(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-professor",
            marker="PROFESSOR_NOOP",
            artifact=lad_learning.artifact_payload(
                lad_learning.LEARNING_PROFESSOR_NOTES_SCHEMA_ID
            ),
            input_id="observe-professor-noop",
        )
        return state, frozenset(
            {"observe-analyst-complete", "observe-professor-noop"}
        )
    if aftermath == "professor_blocked":
        state = lad_learning.observe(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-professor",
            marker="BLOCKED",
            artifact=lad_learning.artifact_payload(
                lad_learning.LEARNING_REPORT_SCHEMA_ID
            ),
            input_id="observe-professor-blocked",
        )
        return state, frozenset(
            {"observe-analyst-complete", "observe-professor-blocked"}
        )

    state = lad_learning.observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-professor",
        marker="PROFESSOR_COMPLETE",
        artifact=lad_learning.artifact_payload(
            lad_learning.LEARNING_SKILL_CANDIDATE_SCHEMA_ID
        ),
        input_id="observe-professor-complete",
        work_item_id="work-curator",
        activation_id="activation-curator",
    )
    state = lad_learning.claim(
        state,
        activation_id="activation-curator",
        run_id="run-curator",
        input_id="claim-curator",
    )
    curator_outcome = aftermath.removeprefix("professor_complete_curator_")
    action_marker_schema = {
        "complete": (
            "CURATOR_COMPLETE",
            lad_learning.LEARNING_SKILL_UPDATE_SCHEMA_ID,
        ),
        "noop": (
            "CURATOR_NOOP",
            lad_learning.LEARNING_CURATOR_DECISION_SCHEMA_ID,
        ),
        "blocked": (
            "BLOCKED",
            lad_learning.LEARNING_REPORT_SCHEMA_ID,
        ),
    }
    marker, schema_id = action_marker_schema[curator_outcome]
    curator_input_id = f"observe-curator-{curator_outcome}"
    state = lad_learning.observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-curator",
        marker=marker,
        artifact=lad_learning.artifact_payload(schema_id),
        input_id=curator_input_id,
    )
    return state, frozenset(
        {
            "observe-analyst-complete",
            "observe-professor-complete",
            curator_input_id,
        }
    )


def _learning_route_artifact_state(plan, fingerprint):
    state = lad_learning.ready_learning_state(plan, fingerprint)
    state = lad_learning.claim(
        state,
        activation_id="activation-learning-request",
        run_id="run-analyst",
        input_id="claim-analyst",
    )
    return lad_learning.observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-analyst",
        marker="ANALYST_COMPLETE",
        artifact=lad_learning.artifact_payload(
            lad_learning.LEARNING_RESEARCH_PACKET_SCHEMA_ID
        ),
        input_id="observe-analyst-complete",
        work_item_id="work-professor",
        activation_id="activation-professor",
    )


def _learning_effect_proposal_state(plan, fingerprint):
    state, _input_ids = _learning_aftermath_state(
        plan,
        fingerprint,
        "professor_complete_curator_complete",
    )
    assert len(state.effect_proposals) == 1
    return state


def _learning_effect_reconciled_state(plan, fingerprint):
    return _reconcile_first_effect(
        _learning_effect_proposal_state(plan, fingerprint),
        result_id="fake-local-result-restart",
    )


def _librarian_effect_reconciled_state(plan, fingerprint):
    state, _input_ids = _learning_aftermath_state(
        plan,
        fingerprint,
        "librarian_complete",
    )
    assert len(state.effect_proposals) == 1
    return _reconcile_first_effect(
        state,
        result_id="fake-local-result-librarian-restart",
    )


def _reconcile_first_effect(state, *, result_id: str):
    effect_id = next(iter(state.effect_proposals))

    from millrace.contracts.transition import ReconcileEffect

    decision = decide(
        state,
        ReconcileEffect(
            "reconcile-learning-effect",
            effect_id=effect_id,
            provider_ref=lad_learning.FAKE_LOCAL_EFFECT_PROVIDER_REF,
            status="applied",
            result={
                "provider_result_id": result_id,
                "summary": "Recorded as fake local evidence only.",
            },
        ),
        lad_learning.context("reconcile-learning-effect"),
    )
    assert decision.accepted is True
    return apply(state, decision)


def _active_foreground_state(plan, fingerprint, *, queue_family_id: str):
    state = lad_learning.admitted_state(plan, fingerprint)
    payload = (
        {
            "title": "Spec input",
            "body": "Shape planning work.",
            "root_source": {"kind": "spec", "source_id": "spec-1"},
        }
        if queue_family_id == "spec"
        else {"task_id": "task-1", "body": "Run execution work."}
    )
    state = lad_learning.apply_accepted_input(
        state,
        EnqueueWork(
            f"enqueue-{queue_family_id}",
            queue_family_id=QueueFamilyId(queue_family_id),
            payload=payload,
        ),
        lad_learning.context(
            f"enqueue-{queue_family_id}",
            work_item_id=f"work-{queue_family_id}",
            activation_id=f"activation-{queue_family_id}",
        ),
    )
    return lad_learning.claim(
        state,
        activation_id=f"activation-{queue_family_id}",
        run_id=f"run-{queue_family_id}",
        input_id=f"claim-{queue_family_id}",
    )


def _replace_plan_authority_fingerprint(
    connection: sqlite3.Connection,
    *,
    old_fingerprint: str,
    new_fingerprint: str,
) -> None:
    tables = tuple(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        if isinstance(row[0], str)
    )
    for table_name in tables:
        columns = {
            row[1]
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
            if isinstance(row[1], str)
        }
        for column_name in (
            "authority_fingerprint",
            "plan_authority_fingerprint",
            "plan_fingerprint",
        ):
            if column_name not in columns:
                continue
            connection.execute(
                f"UPDATE {table_name} SET {column_name} = ? WHERE {column_name} = ?",
                (new_fingerprint, old_fingerprint),
            )


def _persist_with_replaced_selected_plan(
    tmp_path: Path,
    *,
    state,
    old_fingerprint: str,
    corrupt_plan,
):
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    corrupt_fingerprint = authority_fingerprint(corrupt_plan)
    corrupt_digest = ContentAddressedByteStore(cas_root).put_bytes(
        dumps_cas_object(encode_selected_compiled_plan(corrupt_plan))
    )
    with sqlite3.connect(db_path) as connection:
        _replace_plan_authority_fingerprint(
            connection,
            old_fingerprint=old_fingerprint,
            new_fingerprint=corrupt_fingerprint,
        )
        connection.execute(
            "UPDATE admitted_plan_pins SET selected_plan_digest = ?",
            (corrupt_digest,),
        )
        connection.execute(
            "UPDATE default_plan SET selected_plan_digest = ?",
            (corrupt_digest,),
        )
    return db_path, cas_root
