from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from tests.kernel.test_runtime_failure_recovery import failed_state

from millrace.contracts.runner import (
    RunnerSessionCompletionDiagnostic,
    runner_session_completion_diagnostic_bytes,
)
from millrace.contracts.runtime_failures import (
    runtime_failure_applied,
    runtime_failure_input,
)
from millrace.kernel import apply, decide
from millrace.kernel.lookups import run_is_settled
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import StorageIntegrityError
from millrace.substrate.sqlite import SQLiteRuntimeStore
from millrace.testing import deterministic_context, materialize_fake_runner_session_cas
from substrate._runtime_store_support import load_runtime_state, runtime_store_paths


def _accepted_failure_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, str, str, str, str, str, str]:
    state, completion = failed_state(monkeypatch, mapped=True)
    db_path, cas_root = runtime_store_paths(tmp_path)
    cas_store = ContentAddressedByteStore(cas_root)

    # Materialize the fixture's other runner-session CAS references, then
    # replace the failed completion's placeholder with an honest diagnostic.
    state = materialize_fake_runner_session_cas(state=state, cas_store=cas_store)
    run = state.runs[completion.run_id]
    activation = state.activations[run.activation_id]
    session = state.runner_sessions[completion.session_id]
    diagnostic = RunnerSessionCompletionDiagnostic(
        run_id=run.run_ref.run_id,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
        claim_id=run.run_ref.claim_id,
        generation=run.run_ref.generation,
        fencing_token=run.run_ref.fencing_token,
        stage_kind_id=str(run.stage_kind_id),
        graph_node_id=activation.graph_node_id,
        runner_binding_id=str(run.runner_binding_id),
        diagnostic={
            "diagnostics": "execution deadline exceeded",
            "error_kind": "timeout",
        },
    )
    diagnostic_digest = cas_store.put_bytes(
        runner_session_completion_diagnostic_bytes(diagnostic)
    )
    completion = replace(completion, diagnostic_digest=diagnostic_digest)
    state = replace(
        state,
        runner_session_completions={
            **state.runner_session_completions,
            completion.session_id: completion,
        },
    )

    command = runtime_failure_input(state, completion)
    decision = decide(
        state,
        command,
        deterministic_context(
            transition_id="transition-runtime-failure-integrity",
            work_item_id=run.work_item_id,
            activation_id="recovery-activation-integrity",
            run_id=run.run_ref.run_id,
            claim_id=run.run_ref.claim_id,
            fencing_token=run.run_ref.fencing_token,
        ),
    )
    assert decision.accepted, decision.refusal
    state = apply(state, decision)
    assert runtime_failure_applied(state, run.run_ref.run_id)

    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()

    route = next(
        route
        for route in state.activation_routes
        if route.created_by_input_id == command.input_id
    )
    attempt = next(
        attempt
        for attempt in state.recovery_attempts.values()
        if attempt.created_by_input_id == command.input_id
    )
    return (
        db_path,
        cas_root,
        command.input_id,
        route.record_id,
        attempt.record_id,
        route.target_activation_id,
        completion.completion_id,
        completion.session_id,
    )


def _corrupt_one(
    db_path: Path,
    statement: str,
    parameters: tuple[object, ...],
) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA ignore_check_constraints = ON")
        cursor = connection.execute(statement, parameters)
        assert cursor.rowcount == 1


def _delete_one(
    db_path: Path,
    statement: str,
    parameters: tuple[object, ...],
) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        cursor = connection.execute(statement, parameters)
        assert cursor.rowcount == 1


def test_runtime_failure_roundtrips_as_settled_and_applied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        db_path,
        cas_root,
        input_id,
        _route_id,
        _attempt_id,
        _target_id,
        _completion_id,
        _session_id,
    ) = _accepted_failure_store(tmp_path, monkeypatch)

    loaded = load_runtime_state(db_path, cas_root)
    run = loaded.runs["run-generic-parent"]
    assert runtime_failure_applied(loaded, run.run_ref.run_id)
    assert run_is_settled(loaded, run.run_ref.run_id)
    assert loaded.receipts[input_id].accepted
    assert not any(
        observation.run_id == run.run_ref.run_id
        for observation in loaded.runner_observations.values()
    )
    assert not any(
        artifact.source_run_id == run.run_ref.run_id
        for artifact in loaded.artifacts.values()
    )


@pytest.mark.parametrize(
    ("case", "statement", "parameters"),
    (
        (
            "receipt-input-digest",
            "UPDATE input_receipts SET input_payload_digest = ? WHERE input_id = ?",
            (f"sha256:{'f' * 64}",),
        ),
        (
            "receipt-rejected",
            "UPDATE input_receipts SET accepted = 0 WHERE input_id = ?",
            (),
        ),
        (
            "input-event-kind",
            "UPDATE transitions SET input_kind = 'ClaimWork' WHERE input_id = ?",
            (),
        ),
        (
            "governance-action",
            "UPDATE governance_events SET action_id = 'unselected.action' "
            "WHERE input_id = ?",
            (),
        ),
        (
            "trace-disposition",
            "UPDATE traces SET disposition = 'refused' WHERE input_id = ?",
            (),
        ),
        (
            "route-action",
            "UPDATE activation_routes SET action_id = 'unselected.action' "
            "WHERE record_id = ?",
            (),
        ),
        (
            "route-source-run",
            "UPDATE activation_routes SET source_run_id = 'other.run' "
            "WHERE record_id = ?",
            (),
        ),
        (
            "route-work-item",
            "UPDATE activation_routes SET target_work_item_id = 'other.work' "
            "WHERE record_id = ?",
            (),
        ),
        (
            "target-lineage",
            "UPDATE activations SET lineage_id = 'other.lineage' "
            "WHERE activation_id = ?",
            (),
        ),
        (
            "target-queue",
            "UPDATE activations SET queue_family_id = 'other.queue' "
            "WHERE activation_id = ?",
            (),
        ),
        (
            "target-node",
            "UPDATE activations SET graph_node_id = 'other.node' "
            "WHERE activation_id = ?",
            (),
        ),
        (
            "target-stage",
            "UPDATE activations SET stage_kind_id = 'other.stage' "
            "WHERE activation_id = ?",
            (),
        ),
        (
            "target-binding",
            "UPDATE activations SET runner_binding_id = 'other.binding' "
            "WHERE activation_id = ?",
            (),
        ),
        (
            "attempt-policy",
            "UPDATE recovery_attempts SET policy_id = 'other.policy' "
            "WHERE record_id = ?",
            (),
        ),
        (
            "attempt-plan",
            "UPDATE recovery_attempts SET plan_authority_fingerprint = ? "
            "WHERE record_id = ?",
            (f"sha256:{'e' * 64}",),
        ),
        (
            "attempt-source-work",
            "UPDATE recovery_attempts SET source_work_item_id = 'other.work' "
            "WHERE record_id = ?",
            (),
        ),
        (
            "attempt-source-activation",
            "UPDATE recovery_attempts SET source_activation_id = 'other.activation' "
            "WHERE record_id = ?",
            (),
        ),
        (
            "attempt-source-node",
            "UPDATE recovery_attempts SET source_graph_node_id = 'other.node' "
            "WHERE record_id = ?",
            (),
        ),
        (
            "attempt-source-stage",
            "UPDATE recovery_attempts SET source_stage_kind_id = 'other.stage' "
            "WHERE record_id = ?",
            (),
        ),
        (
            "attempt-source-binding",
            "UPDATE recovery_attempts SET source_runner_binding_id = 'other.binding' "
            "WHERE record_id = ?",
            (),
        ),
        (
            "attempt-source-queue",
            "UPDATE recovery_attempts SET source_queue_family_id = 'other.queue' "
            "WHERE record_id = ?",
            (),
        ),
        (
            "completion-timeout-kind",
            "UPDATE runner_session_completions SET adapter_error_kind = 'segfault' "
            "WHERE completion_id = ?",
            (),
        ),
        (
            "completion-current-fence",
            "UPDATE runner_session_completions "
            "SET session_fencing_token = 'stale.fence' "
            "WHERE completion_id = ?",
            (),
        ),
        (
            "completion-diagnostic-cas-hash",
            "UPDATE runner_session_completions SET diagnostic_digest = ? "
            "WHERE completion_id = ?",
            (f"sha256:{'0' * 64}",),
        ),
        (
            "session-current-fence",
            "UPDATE runner_sessions SET session_fencing_token = 'stale.fence' "
            "WHERE session_id = ?",
            (),
        ),
    ),
)
def test_restart_refuses_runtime_failure_authority_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    statement: str,
    parameters: tuple[object, ...],
) -> None:
    (
        db_path,
        cas_root,
        input_id,
        route_id,
        attempt_id,
        target_id,
        completion_id,
        session_id,
    ) = _accepted_failure_store(tmp_path, monkeypatch)
    if case.startswith("route-"):
        record_id = route_id
    elif case.startswith("attempt-"):
        record_id = attempt_id
    elif case.startswith("target-"):
        record_id = target_id
    elif case.startswith("completion-"):
        record_id = completion_id
    elif case == "session-current-fence":
        record_id = session_id
    else:
        record_id = input_id
    _corrupt_one(db_path, statement, (*parameters, record_id))

    with pytest.raises(StorageIntegrityError):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("case", "statement", "record_kind"),
    (
        ("missing-route", "DELETE FROM activation_routes WHERE record_id = ?", "route"),
        (
            "missing-attempt",
            "DELETE FROM recovery_attempts WHERE record_id = ?",
            "attempt",
        ),
        (
            "missing-governance-event",
            "DELETE FROM governance_events WHERE input_id = ?",
            "input",
        ),
        ("missing-trace", "DELETE FROM traces WHERE input_id = ?", "input"),
        (
            "missing-failure-receipt",
            "DELETE FROM input_receipts WHERE input_id = ?",
            "input",
        ),
    ),
)
def test_restart_refuses_missing_runtime_failure_effect_or_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    statement: str,
    record_kind: str,
) -> None:
    (
        db_path,
        cas_root,
        input_id,
        route_id,
        attempt_id,
        _target_id,
        _completion_id,
        _session_id,
    ) = _accepted_failure_store(tmp_path, monkeypatch)
    record_id = (
        route_id
        if record_kind == "route"
        else attempt_id
        if record_kind == "attempt"
        else input_id
    )
    _delete_one(db_path, statement, (record_id,))

    with pytest.raises(StorageIntegrityError):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_missing_source_claim_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        db_path,
        cas_root,
        _input_id,
        _route_id,
        _attempt_id,
        _target_id,
        _completion_id,
        _session_id,
    ) = _accepted_failure_store(tmp_path, monkeypatch)
    _delete_one(
        db_path,
        "DELETE FROM input_receipts WHERE input_id = ?",
        ("generic-claim",),
    )

    with pytest.raises(StorageIntegrityError):
        load_runtime_state(db_path, cas_root)
