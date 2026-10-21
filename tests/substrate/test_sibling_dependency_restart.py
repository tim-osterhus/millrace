from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from millrace.contracts.transition import ClaimWork, FanoutFromArtifact
from millrace.kernel import apply, decide
from millrace.substrate.errors import StorageIntegrityError
from substrate._runtime_store_support import (
    load_runtime_state,
    persist_runtime_state,
    runtime_store_paths,
)
from support import generic_fanout, sibling_dependencies


def _claim(state):
    record = next(r for r in state.fanout_records.values() if r.item_key == "later")
    command = ClaimWork("claim-later", activation_id=record.target_activation_id)
    return command, decide(state, command, generic_fanout.context(command.input_id))


def test_waiting_routed_success_claim_reload_and_exact_replay(tmp_path):
    state = sibling_dependencies.fanned_state({"later": ("first",), "first": ()})
    db, cas = runtime_store_paths(tmp_path)
    persist_runtime_state(db, cas, state)
    loaded = load_runtime_state(db, cas)
    assert not _claim(loaded)[1].accepted
    command = FanoutFromArtifact(
        "fanout-dag",
        fanout_id="fanout.packet.children",
        source_artifact_id="transition-observe-parent-done:artifact",
    )
    replayed = apply(
        loaded, decide(loaded, command, generic_fanout.context(command.input_id))
    )
    assert replayed.fanout_records == loaded.fanout_records
    assert replayed.work_dependencies == loaded.work_dependencies
    state = sibling_dependencies.finish_card(loaded, "first")
    persist_runtime_state(db, cas, state)
    loaded = load_runtime_state(db, cas)
    claim, decision = _claim(loaded)
    assert decision.accepted
    state = apply(loaded, decision)
    persist_runtime_state(db, cas, state)
    loaded = load_runtime_state(db, cas)
    replayed = apply(
        loaded, decide(loaded, claim, generic_fanout.context(claim.input_id))
    )
    assert replayed.work_dependencies == state.work_dependencies
    assert replayed.runs == state.runs


def test_unclaimed_recovery_creation_return_and_final_success_survive_reload(tmp_path):
    state = sibling_dependencies.fanned_state(
        {"later": ("first",), "first": ()}, sibling_dependencies.source_with_recovery()
    )
    first = next(r for r in state.fanout_records.values() if r.item_key == "first")
    state = sibling_dependencies.complete_activation(
        state, first.target_activation_id, "first-recover", "RECOVER"
    )
    db, cas = runtime_store_paths(tmp_path)
    persist_runtime_state(db, cas, state)
    state = load_runtime_state(db, cas)
    recovery = next(
        r.target_activation_id
        for r in state.activation_routes
        if r.record_id == "transition-observe-first-recover:route"
    )
    assert state.activations[recovery].claimed_by_run_id is None
    state = sibling_dependencies.complete_activation(
        state, recovery, "first-return", "RETURN"
    )
    persist_runtime_state(db, cas, state)
    state = load_runtime_state(db, cas)
    returned = next(
        r.target_activation_id
        for r in state.activation_routes
        if r.record_id == "transition-observe-first-return:route"
    )
    state = sibling_dependencies.complete_activation(
        state, returned, "returned-next", "NEXT"
    )
    final = next(
        r.target_activation_id
        for r in state.activation_routes
        if r.record_id == "transition-observe-returned-next:route"
    )
    state = sibling_dependencies.complete_activation(
        state, final, "returned-final", "DONE"
    )
    assert _claim(state)[1].accepted
    persist_runtime_state(db, cas, state)
    state = load_runtime_state(db, cas)
    assert _claim(state)[1].accepted


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_edge",
        "duplicate_edge",
        "wrong_prerequisite",
        "wrong_source_record",
        "wrong_creator",
        "wrong_lineage",
        "claim_before_success",
        "wrong_close_action",
        "wrong_final_route",
    ),
)
def test_durable_corruption_refuses_graph_and_original_success_provenance(
    tmp_path, mutation
):
    state = sibling_dependencies.fanned_state(
        {"later": ("first",), "other": (), "first": ()}
    )
    state = sibling_dependencies.finish_card(state, "first")
    claim, decision = _claim(state)
    assert decision.accepted
    state = apply(state, decision)
    db, cas = runtime_store_paths(tmp_path)
    persist_runtime_state(db, cas, state)
    edge = next(
        d
        for d in state.work_dependencies.values()
        if d.dependency_id.startswith("dependency-sibling:")
    )
    other = next(r for r in state.fanout_records.values() if r.item_key == "other")
    with sqlite3.connect(db) as connection:
        if mutation == "missing_edge":
            connection.execute(
                "DELETE FROM work_dependencies WHERE dependency_id = ?",
                (edge.dependency_id,),
            )
        elif mutation == "duplicate_edge":
            columns = [
                r[1] for r in connection.execute("PRAGMA table_info(work_dependencies)")
            ]
            values = list(
                connection.execute(
                    "SELECT * FROM work_dependencies WHERE dependency_id=?",
                    (edge.dependency_id,),
                ).fetchone()
            )
            values[columns.index("dependency_id")] = "forged-extra-edge"
            connection.execute(
                "INSERT INTO work_dependencies VALUES ("
                + ",".join("?" for _ in columns)
                + ")",
                values,
            )
        elif mutation == "wrong_prerequisite":
            connection.execute(
                "UPDATE work_dependencies SET dependency_work_item_id=? "
                "WHERE dependency_id=?",
                (other.target_work_item_id, edge.dependency_id),
            )
        elif mutation == "wrong_source_record":
            connection.execute(
                "UPDATE work_dependencies SET fanout_record_id=? WHERE dependency_id=?",
                (other.record_id, edge.dependency_id),
            )
        elif mutation == "wrong_creator":
            connection.execute(
                "UPDATE work_dependencies SET created_by_input_id=? "
                "WHERE dependency_id=?",
                ("enqueue-parent", edge.dependency_id),
            )
        elif mutation == "wrong_lineage":
            connection.execute(
                "UPDATE work_dependencies SET lineage_id=? WHERE dependency_id=?",
                ("foreign-lineage", edge.dependency_id),
            )
        elif mutation == "claim_before_success":
            close_input = state.artifacts[
                "transition-observe-first-final:artifact"
            ].created_by_input_id
            claim_input = claim.input_id
            close_order = connection.execute(
                "SELECT transition_order FROM transitions WHERE input_id=?",
                (close_input,),
            ).fetchone()[0]
            claim_order = connection.execute(
                "SELECT transition_order FROM transitions WHERE input_id=?",
                (claim_input,),
            ).fetchone()[0]
            for table in ("transitions", "governance_events", "traces"):
                connection.execute(
                    f"UPDATE {table} SET transition_order=? WHERE input_id=?",
                    (claim_order + 100, close_input),
                )
                connection.execute(
                    f"UPDATE {table} SET transition_order=? WHERE input_id=?",
                    (close_order, claim_input),
                )
                connection.execute(
                    f"UPDATE {table} SET transition_order=? WHERE input_id=?",
                    (claim_order, close_input),
                )
        elif mutation == "wrong_close_action":
            connection.execute(
                "UPDATE closed_work_items SET action_id=? WHERE work_item_id=?",
                (
                    "final.gap",
                    state.artifacts[
                        "transition-observe-first-final:artifact"
                    ].work_item_id,
                ),
            )
        else:
            connection.execute(
                "UPDATE activation_routes SET source_work_item_id=? WHERE record_id=?",
                (other.target_work_item_id, "transition-observe-first-next:route"),
            )
    with pytest.raises(StorageIntegrityError):
        load_runtime_state(db, cas)


def test_malformed_manager_result_recovers_returns_and_accepts_valid_graph(tmp_path):
    from millrace.contracts.runner import (
        RunnerSessionCompletionDiagnostic,
        runner_result_evidence_bytes,
        runner_result_evidence_from_payload,
        runner_session_completion_diagnostic_bytes,
    )
    from millrace.contracts.runtime_failures import (
        runtime_failure_input,
        runtime_failure_kind,
    )
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore
    from millrace.testing import materialize_fake_runner_session_cas

    source = sibling_dependencies.source_with_recovery()
    source["stage_kinds"][0]["declared_outcome_ids"] += ("parent.recover",)
    source["terminal_outcomes"].append(
        {
            "id": "parent.recover",
            "stage_kind_id": "parent_stage",
            "marker": "PARENT_RECOVER",
        }
    )
    source["terminal_actions"].append(
        {
            "id": "parent.recover",
            "stage_kind_id": "parent_stage",
            "outcome_id": "parent.recover",
            "kind": "recovery_route",
            "target_stage_kind_id": "recovery_stage",
            "target_graph_node_id": "fanout.recovery.start",
            "runner_binding_id": "fanout.runner",
            "asset_ids": ("fanout.child.prompt",),
        }
    )
    source["recovery_policies"][0]["source_recovery_action_ids"] += ("parent.recover",)
    source["runner_bindings"][0]["runtime_failure_mappings"] = (
        {
            "stage_kind_id": "parent_stage",
            "failure_kind": "result_rejected",
            "action_id": "parent.recover",
        },
    )
    plan, fingerprint = generic_fanout.compile_fanout(source)
    state = sibling_dependencies.parent_ready_state(plan, fingerprint)
    state, rejected_input, rejected = sibling_dependencies.observed_decision(
        state,
        "run-parent",
        "observe-malformed-manager",
        "FANOUT_READY",
        sibling_dependencies.packet({"one": ("two",), "two": ("one",)}),
        observed_at=0,
    )
    assert not rejected.accepted and rejected.refusal.reason == "invalid_fanout_payload"
    state = apply(state, rejected)
    assert not state.closed_work_items and not state.fanout_records
    completion = state.runner_session_completions[
        state.runs["run-parent"].current_session_id
    ]
    db, cas = runtime_store_paths(tmp_path)
    cas_store = ContentAddressedByteStore(cas)
    cas_store.put_bytes(
        runner_result_evidence_bytes(
            runner_result_evidence_from_payload(rejected_input.payload)
        )
    )
    run = state.runs[completion.run_id]
    diagnostic = RunnerSessionCompletionDiagnostic(
        run_id=run.run_ref.run_id,
        session_id=completion.session_id,
        dispatch_generation=completion.dispatch_generation,
        session_fencing_token=completion.session_fencing_token,
        plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
        claim_id=run.run_ref.claim_id,
        generation=run.run_ref.generation,
        fencing_token=run.run_ref.fencing_token,
        stage_kind_id=str(run.stage_kind_id),
        graph_node_id=state.activations[run.activation_id].graph_node_id,
        runner_binding_id=str(run.runner_binding_id),
        diagnostic={"outcome": "successful_adapter_result"},
    )
    completion = replace(
        completion,
        diagnostic_digest=cas_store.put_bytes(
            runner_session_completion_diagnostic_bytes(diagnostic)
        ),
    )
    state = replace(
        state,
        runner_session_completions={
            **state.runner_session_completions,
            completion.session_id: completion,
        },
    )
    assert runtime_failure_kind(state, completion) == "result_rejected"
    command = runtime_failure_input(state, completion)
    decision = decide(state, command, generic_fanout.context("manager-recovery"))
    assert decision.accepted, decision.refusal
    state = apply(state, decision)
    state = sibling_dependencies.complete_activation(
        state, "activation-manager-recovery", "manager-return", "RETURN"
    )
    resumed = next(
        r.target_activation_id
        for r in state.activation_routes
        if r.record_id == "transition-observe-manager-return:route"
    )
    claim = ClaimWork("claim-manager-valid", activation_id=resumed)
    state = generic_fanout.apply_accepted_input(
        state, claim, generic_fanout.context(claim.input_id)
    )
    state, _, observed = sibling_dependencies.observed_decision(
        state,
        "run-manager-valid",
        "observe-manager-valid",
        "FANOUT_READY",
        sibling_dependencies.packet({"later": ("first",), "first": ()}),
    )
    assert observed.accepted, observed.refusal
    state = apply(state, observed)
    fanout = FanoutFromArtifact(
        "fanout-valid-manager",
        fanout_id="fanout.packet.children",
        source_artifact_id="transition-observe-manager-valid:artifact",
    )
    state = generic_fanout.apply_accepted_input(
        state, fanout, generic_fanout.context(fanout.input_id)
    )
    assert len(state.fanout_records) == 2 and len(state.work_dependencies) == 3
    state = materialize_fake_runner_session_cas(state=state, cas_store=cas_store)
    state = replace(
        state,
        runner_session_completions={
            **state.runner_session_completions,
            completion.session_id: completion,
        },
    )
    store = SQLiteRuntimeStore.initialize(db)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()
    loaded = load_runtime_state(db, cas)
    assert not _claim(loaded)[1].accepted


def test_unclaimed_recovery_generation_drift_refuses_restart(tmp_path):
    state = sibling_dependencies.fanned_state(
        {"later": ("first",), "first": ()}, sibling_dependencies.source_with_recovery()
    )
    first = next(r for r in state.fanout_records.values() if r.item_key == "first")
    state = sibling_dependencies.complete_activation(
        state, first.target_activation_id, "first-recover", "RECOVER"
    )
    recovery = next(
        r.target_activation_id
        for r in state.activation_routes
        if r.record_id == "transition-observe-first-recover:route"
    )
    assert state.activations[recovery].claimed_by_run_id is None
    db, cas = runtime_store_paths(tmp_path)
    persist_runtime_state(db, cas, state)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE activations SET generation=999 WHERE activation_id=?", (recovery,)
        )
    with pytest.raises(StorageIntegrityError):
        load_runtime_state(db, cas)


@pytest.mark.parametrize(
    "workflow_source",
    (
        sibling_dependencies.source_with_recovery,
        sibling_dependencies.source_with_same_stage_recovery,
    ),
)
def test_deleting_formal_attempt_row_refuses_unclaimed_recovery_reload(
    tmp_path, workflow_source
):
    state = sibling_dependencies.fanned_state(
        {"later": ("first",), "first": ()}, workflow_source()
    )
    first = next(r for r in state.fanout_records.values() if r.item_key == "first")
    state = sibling_dependencies.complete_activation(
        state, first.target_activation_id, "first-recover", "RECOVER"
    )
    db, cas = runtime_store_paths(tmp_path)
    persist_runtime_state(db, cas, state)
    with sqlite3.connect(db) as connection:
        connection.execute("DELETE FROM recovery_attempts")
    with pytest.raises(StorageIntegrityError):
        load_runtime_state(db, cas)
