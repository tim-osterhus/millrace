from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from kernel.kernel_ping_scenarios import bootstrap_to_taskmaster_claim
from millrace.compiler import compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.runner import (
    RUNNER_SESSION_LOCATOR_MAX_BYTES,
    RunnerResultEvidence,
    runner_result_evidence_bytes,
    runner_result_evidence_from_payload,
    runner_session_locator_bytes,
)
from millrace.contracts.state import (
    RunnerSessionCancellationAttemptRecord,
    RunnerSessionCancellationRecord,
    RunnerSessionCompletionRecord,
    RunnerSessionRecord,
    RuntimeState,
)
from millrace.contracts.transition import (
    AdvanceRunnerSession,
    CreateRunnerSession,
    RecordRunnerSessionCompletion,
    RequestRunnerSessionCancellation,
)
from millrace.kernel import apply, decide
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import (
    StorageIntegrityError,
    StoreSchemaUpgradeRequired,
)
from millrace.substrate.sqlite import SQLiteRuntimeStore
from millrace.testing import deterministic_context
from millrace.workflows import kernel_ping


def _session_state() -> RuntimeState:
    result = compile_workflow(kernel_ping.workflow_source())
    assert result.plan is not None
    state = bootstrap_to_taskmaster_claim(
        result.plan,
        authority_fingerprint(result.plan),
    )
    run = state.runs["run-taskmaster"]
    session = RunnerSessionRecord(
        session_id="session-1",
        run_id="run-taskmaster",
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        state="interrupted",
        created_at=100,
        start_intent_at=110,
        started_at=120,
        ended_at=150,
        durable_locator_digest="sha256:" + "a" * 64,
        cleanup_disposition="complete",
    )
    request = RunnerSessionCancellationRecord(
        request_id="cancel-1",
        session_id="session-1",
        dispatch_generation=1,
        reason="operator_cancel_work",
        source_kind="operator",
        actor_id="operator-1",
        requested_at=130,
        request_order=1,
        primary=True,
    )
    attempt = RunnerSessionCancellationAttemptRecord(
        attempt_id="attempt-1",
        session_id="session-1",
        request_id="cancel-1",
        sequence=1,
        operation="cooperative_cancel",
        result="succeeded",
        started_at=135,
        completed_at=140,
        bounded_diagnostic_digest="sha256:" + "b" * 64,
    )
    completion = RunnerSessionCompletionRecord(
        completion_id="completion-1",
        session_id="session-1",
        run_id="run-taskmaster",
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        terminal_state="interrupted",
        exit_kind="cancelled",
        adapter_outcome_kind=None,
        adapter_error_kind=None,
        runner_result_evidence_digest=None,
        primary_cancellation_request_id="cancel-1",
        cleanup_disposition="complete",
        started_at=120,
        cancel_requested_at=130,
        completed_at=150,
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id="redaction.default",
        diagnostic_digest="sha256:" + "c" * 64,
        application_input_id="cli:run.session-completion:completion-1",
    )
    return replace(
        state,
        runs={
            **state.runs,
            run.run_ref.run_id: replace(
                run,
                current_session_id=session.session_id,
                last_dispatch_generation=1,
            ),
        },
        runner_sessions={session.session_id: session},
        runner_session_cancellation_requests={request.request_id: request},
        runner_session_cancellation_attempts={attempt.attempt_id: attempt},
        runner_session_completions={session.session_id: completion},
    )


def _cas_backed_session_state(
    state: RuntimeState,
    cas_store: ContentAddressedByteStore,
    *,
    completed: bool = False,
) -> tuple[RuntimeState, dict[str, str]]:
    completed_evidence = _completed_evidence_bytes(state, "session-1")
    digests = {
        "locator": cas_store.put_bytes(
            runner_session_locator_bytes(
                {"provider_request_id": "runner locator"}
            )
        ),
        "attempt_diagnostic": cas_store.put_bytes(b"attempt diagnostic"),
        "completion_diagnostic": cas_store.put_bytes(b"completion diagnostic"),
        "completed_evidence": cas_store.put_bytes(completed_evidence),
    }
    session = replace(
        state.runner_sessions["session-1"],
        durable_locator_digest=digests["locator"],
    )
    completion = replace(
        state.runner_session_completions["session-1"],
        diagnostic_digest=digests["completion_diagnostic"],
    )
    attempts = state.runner_session_cancellation_attempts
    requests = state.runner_session_cancellation_requests
    if completed:
        session = replace(session, state="completed")
        completion = replace(
            completion,
            terminal_state="completed",
            exit_kind="success",
            adapter_outcome_kind="success",
            runner_result_evidence_digest=digests["completed_evidence"],
            primary_cancellation_request_id=None,
            cancel_requested_at=None,
        )
        attempts = {}
        requests = {}
    else:
        attempt = replace(
            attempts["attempt-1"],
            bounded_diagnostic_digest=digests["attempt_diagnostic"],
        )
        attempts = {attempt.attempt_id: attempt}
    return (
        replace(
            state,
            runner_sessions={session.session_id: session},
            runner_session_cancellation_requests=requests,
            runner_session_cancellation_attempts=attempts,
            runner_session_completions={session.session_id: completion},
        ),
        digests,
    )


def _completed_evidence_bytes(state: RuntimeState, session_id: str) -> bytes:
    run = state.runs["run-taskmaster"]
    activation = state.activations[run.activation_id]
    session = state.runner_sessions[session_id]
    completed_evidence = RunnerResultEvidence(
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
        marker="TASK_COMPLETE",
        adapter_provenance=None,
        observation_payload={},
        artifact_payload={},
    )
    return runner_result_evidence_bytes(completed_evidence)


def test_candidate_write_refuses_foreign_session_completed_evidence(tmp_path) -> None:
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, _digests = _cas_backed_session_state(
        _session_state(),
        cas_store,
        completed=True,
    )
    completion = state.runner_session_completions["session-1"]
    evidence = replace(
        runner_result_evidence_from_payload(
            json.loads(
                cas_store.get_bytes(completion.runner_result_evidence_digest)
            )
        ),
        session_id="session-2",
    )
    foreign_digest = cas_store.put_bytes(runner_result_evidence_bytes(evidence))
    state = replace(
        state,
        runner_session_completions={
            "session-1": replace(
                completion,
                runner_result_evidence_digest=foreign_digest,
            )
        },
    )
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    try:
        with pytest.raises(StorageIntegrityError, match="completed evidence"):
            store.persist_runtime_state(state, cas_store)
    finally:
        store.close()


def test_load_refuses_raw_malformed_completed_evidence_cas(tmp_path) -> None:
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, _digests = _cas_backed_session_state(
        _session_state(),
        cas_store,
        completed=True,
    )
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    malformed_digest = cas_store.put_bytes(b'{"foreign":"payload"}')
    try:
        store.persist_runtime_state(state, cas_store)
        connection = sqlite3.connect(tmp_path / "runtime.sqlite3")
        try:
            connection.execute(
                """
                UPDATE runner_session_completions
                SET runner_result_evidence_digest = ?
                WHERE session_id = 'session-1'
                """,
                (malformed_digest,),
            )
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(StorageIntegrityError, match="completed evidence"):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


def _replace_session_digest(
    state: RuntimeState,
    digest_kind: str,
    digest: str,
) -> RuntimeState:
    session = state.runner_sessions["session-1"]
    completion = state.runner_session_completions["session-1"]
    attempts = state.runner_session_cancellation_attempts
    if digest_kind == "locator":
        session = replace(session, durable_locator_digest=digest)
    elif digest_kind == "attempt_diagnostic":
        attempt = replace(
            attempts["attempt-1"],
            bounded_diagnostic_digest=digest,
        )
        attempts = {attempt.attempt_id: attempt}
    elif digest_kind == "completion_diagnostic":
        completion = replace(completion, diagnostic_digest=digest)
    else:
        completion = replace(completion, runner_result_evidence_digest=digest)
    return replace(
        state,
        runner_sessions={session.session_id: session},
        runner_session_cancellation_attempts=attempts,
        runner_session_completions={session.session_id: completion},
    )


def test_store_schema_seven_owns_runner_session_tables(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()

    with sqlite3.connect(db_path) as connection:
        version = connection.execute(
            "SELECT store_schema_version FROM store_metadata WHERE id = 1"
        ).fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        run_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(runs)")
        }

    assert version == (7,)
    assert {
        "runner_sessions",
        "runner_session_cancellation_requests",
        "runner_session_cancellation_attempts",
        "runner_session_completions",
    }.issubset(tables)
    assert {"current_session_id", "last_dispatch_generation"}.issubset(run_columns)


def test_runner_session_records_round_trip(tmp_path) -> None:
    state = _session_state()
    db_path = tmp_path / "runtime.sqlite3"
    cas_root = tmp_path / "cas"
    store = SQLiteRuntimeStore.initialize(db_path)
    cas_store = ContentAddressedByteStore(cas_root)
    state, _digests = _cas_backed_session_state(state, cas_store)
    try:
        store.persist_runtime_state(state, cas_store)
        loaded = store.load_runtime_state(cas_store)
    finally:
        store.close()

    assert loaded == state


def test_cancellation_requested_aftermath_round_trips_from_decide_apply(
    tmp_path,
) -> None:
    result = compile_workflow(kernel_ping.workflow_source())
    assert result.plan is not None
    state = bootstrap_to_taskmaster_claim(
        result.plan,
        authority_fingerprint(result.plan),
    )
    run_ref = state.runs["run-taskmaster"].run_ref
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    locator_digest = cas_store.put_bytes(
        runner_session_locator_bytes(
            {"provider_request_id": "live runner locator"}
        )
    )
    inputs = (
        CreateRunnerSession(
            "create-session",
            run_ref=run_ref,
            session_id="session-1",
            session_fencing_token="session-fence-1",
            created_at=100,
            explicit_retry_intent=False,
        ),
        AdvanceRunnerSession(
            "start-session",
            run_ref=run_ref,
            session_id="session-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            expected_state="created",
            next_state="starting",
            occurred_at=110,
        ),
        AdvanceRunnerSession(
            "run-session",
            run_ref=run_ref,
            session_id="session-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            expected_state="starting",
            next_state="running",
            occurred_at=120,
            durable_locator_digest=locator_digest,
        ),
        RequestRunnerSessionCancellation(
            "cancel-session",
            run_ref=run_ref,
            session_id="session-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            expected_state="running",
            request_id="cancel-1",
            reason="operator_cancel_work",
            source_kind="operator",
            actor_id="operator-1",
            requested_at=130,
            request_order=1,
            primary=True,
        ),
    )
    for transition_input in inputs:
        decision = decide(
            state,
            transition_input,
            deterministic_context(
                transition_id=f"transition-{transition_input.input_id}"
            ),
        )
        assert decision.accepted
        state = apply(state, decision)

    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    try:
        store.persist_runtime_state(state, cas_store)
        loaded = store.load_runtime_state(cas_store)
    finally:
        store.close()

    assert loaded == state
    assert loaded.runner_sessions["session-1"].state == "cancellation_requested"


def test_starting_completion_decides_applies_and_round_trips(tmp_path) -> None:
    result = compile_workflow(kernel_ping.workflow_source())
    assert result.plan is not None
    state = bootstrap_to_taskmaster_claim(
        result.plan,
        authority_fingerprint(result.plan),
    )
    run_ref = state.runs["run-taskmaster"].run_ref
    for transition_input in (
        CreateRunnerSession(
            "create-session",
            run_ref=run_ref,
            session_id="session-1",
            session_fencing_token="session-fence-1",
            created_at=100,
            explicit_retry_intent=False,
        ),
        AdvanceRunnerSession(
            "start-session",
            run_ref=run_ref,
            session_id="session-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            expected_state="created",
            next_state="starting",
            occurred_at=110,
        ),
    ):
        decision = decide(
            state,
            transition_input,
            deterministic_context(
                transition_id=f"transition-{transition_input.input_id}"
            ),
        )
        assert decision.accepted
        state = apply(state, decision)
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    completion = RunnerSessionCompletionRecord(
        completion_id="completion-1",
        session_id="session-1",
        run_id=run_ref.run_id,
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        terminal_state="completed",
        exit_kind="success",
        adapter_outcome_kind="success",
        adapter_error_kind=None,
        runner_result_evidence_digest=cas_store.put_bytes(
            _completed_evidence_bytes(state, "session-1")
        ),
        primary_cancellation_request_id=None,
        cleanup_disposition="complete",
        started_at=120,
        cancel_requested_at=None,
        completed_at=130,
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id="redaction.default",
        diagnostic_digest=cas_store.put_bytes(b"completion diagnostic"),
        application_input_id="cli:run.session-completion:completion-1",
    )
    decision = decide(
        state,
        RecordRunnerSessionCompletion(
            "complete-session",
            run_ref=run_ref,
            expected_state="starting",
            completion=completion,
        ),
        deterministic_context(transition_id="transition-complete-session"),
    )

    assert decision.accepted
    state = apply(state, decision)
    assert state.runner_sessions["session-1"].started_at == 120
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    try:
        store.persist_runtime_state(state, cas_store)
        loaded = store.load_runtime_state(cas_store)
    finally:
        store.close()

    assert loaded == state


@pytest.mark.parametrize("prior", ("cancellation_requested", "terminating"))
def test_composed_start_completion_decides_applies_and_round_trips(
    tmp_path,
    prior: str,
) -> None:
    result = compile_workflow(kernel_ping.workflow_source())
    assert result.plan is not None
    state = bootstrap_to_taskmaster_claim(
        result.plan,
        authority_fingerprint(result.plan),
    )
    run_ref = state.runs["run-taskmaster"].run_ref
    inputs = [
        CreateRunnerSession(
            "create-session",
            run_ref=run_ref,
            session_id="session-1",
            session_fencing_token="session-fence-1",
            created_at=100,
            explicit_retry_intent=False,
        ),
        AdvanceRunnerSession(
            "start-session",
            run_ref=run_ref,
            session_id="session-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            expected_state="created",
            next_state="starting",
            occurred_at=110,
        ),
        RequestRunnerSessionCancellation(
            "cancel-session",
            run_ref=run_ref,
            session_id="session-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            expected_state="starting",
            request_id="cancel-1",
            reason="operator_cancel_work",
            source_kind="operator",
            actor_id="operator-1",
            requested_at=115,
            request_order=1,
            primary=True,
        ),
    ]
    if prior == "terminating":
        inputs.append(
            AdvanceRunnerSession(
                "terminate-session",
                run_ref=run_ref,
                session_id="session-1",
                dispatch_generation=1,
                session_fencing_token="session-fence-1",
                expected_state="cancellation_requested",
                next_state="terminating",
                occurred_at=116,
            )
        )
    for transition_input in inputs:
        decision = decide(
            state,
            transition_input,
            deterministic_context(
                transition_id=f"transition-{transition_input.input_id}"
            ),
        )
        assert decision.accepted
        state = apply(state, decision)
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    completion = RunnerSessionCompletionRecord(
        completion_id="completion-1",
        session_id="session-1",
        run_id=run_ref.run_id,
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        terminal_state="completed",
        exit_kind="success",
        adapter_outcome_kind="success",
        adapter_error_kind=None,
        runner_result_evidence_digest=cas_store.put_bytes(
            _completed_evidence_bytes(state, "session-1")
        ),
        primary_cancellation_request_id="cancel-1",
        cleanup_disposition="complete",
        started_at=112,
        cancel_requested_at=115,
        completed_at=130,
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id="redaction.default",
        diagnostic_digest=cas_store.put_bytes(b"completion diagnostic"),
        application_input_id="cli:run.session-completion:completion-1",
    )
    decision = decide(
        state,
        RecordRunnerSessionCompletion(
            "complete-session",
            run_ref=run_ref,
            expected_state=prior,
            completion=completion,
        ),
        deterministic_context(transition_id="transition-complete-session"),
    )

    assert decision.accepted
    state = apply(state, decision)
    assert state.runner_sessions["session-1"].started_at == 112
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    try:
        store.persist_runtime_state(state, cas_store)
        loaded = store.load_runtime_state(cas_store)
    finally:
        store.close()

    assert loaded == state


@pytest.mark.parametrize(
    "digest_kind",
    (
        "locator",
        "attempt_diagnostic",
        "completion_diagnostic",
        "completed_evidence",
    ),
)
def test_runner_session_missing_cas_reference_is_refused_on_persist(
    tmp_path,
    digest_kind: str,
) -> None:
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, _digests = _cas_backed_session_state(
        _session_state(),
        cas_store,
        completed=digest_kind == "completed_evidence",
    )
    state = _replace_session_digest(
        state,
        digest_kind,
        "sha256:" + "f" * 64,
    )
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    try:
        with pytest.raises(StorageIntegrityError, match=digest_kind):
            store.persist_runtime_state(state, cas_store)
    finally:
        store.close()


@pytest.mark.parametrize(
    "digest_kind",
    (
        "locator",
        "attempt_diagnostic",
        "completion_diagnostic",
        "completed_evidence",
    ),
)
@pytest.mark.parametrize("damage", ("missing", "tampered"))
def test_runner_session_invalid_cas_reference_is_refused_on_reload(
    tmp_path,
    digest_kind: str,
    damage: str,
) -> None:
    cas_root = tmp_path / "cas"
    cas_store = ContentAddressedByteStore(cas_root)
    state, digests = _cas_backed_session_state(
        _session_state(),
        cas_store,
        completed=digest_kind == "completed_evidence",
    )
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()
    digest = digests[digest_kind]
    object_path = cas_root / "sha256" / digest.removeprefix("sha256:")
    if damage == "missing":
        object_path.unlink()
    else:
        object_path.write_bytes(b"tampered")

    store = SQLiteRuntimeStore.open(tmp_path / "runtime.sqlite3")
    try:
        with pytest.raises(StorageIntegrityError, match=digest_kind):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


@pytest.mark.parametrize("operation", ("persist", "load"))
@pytest.mark.parametrize(
    "payload",
    (
        b"x" * (RUNNER_SESSION_LOCATOR_MAX_BYTES + 1),
        b"{",
        b"[]",
        b'{"value": 1}',
        b'{"value":1.5}',
        b'{"claim_id":"hostile-claim"}',
    ),
)
def test_runner_session_locator_codec_is_enforced_by_sqlite_boundaries(
    tmp_path,
    operation: str,
    payload: bytes,
) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, _digests = _cas_backed_session_state(_session_state(), cas_store)
    invalid_digest = cas_store.put_bytes(payload)
    invalid_state = _replace_session_digest(state, "locator", invalid_digest)
    store = SQLiteRuntimeStore.initialize(database_path)
    if operation == "persist":
        try:
            with pytest.raises(StorageIntegrityError, match="locator"):
                store.persist_runtime_state(invalid_state, cas_store)
        finally:
            store.close()
        return

    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            UPDATE runner_sessions
            SET durable_locator_digest = ?
            WHERE session_id = 'session-1'
            """,
            (invalid_digest,),
        )
    store = SQLiteRuntimeStore.open(database_path)
    try:
        with pytest.raises(StorageIntegrityError, match="locator"):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


def test_runner_session_generation_gap_is_refused(tmp_path) -> None:
    state = _session_state()
    session = RunnerSessionRecord(
        session_id="session-2",
        run_id="run-taskmaster",
        dispatch_generation=2,
        session_fencing_token="session-fence-2",
        state="created",
        created_at=200,
        start_intent_at=None,
        started_at=None,
        ended_at=None,
        durable_locator_digest=None,
        cleanup_disposition="pending",
    )
    run = replace(
        state.runs["run-taskmaster"],
        current_session_id=session.session_id,
        last_dispatch_generation=2,
    )
    corrupt = replace(
        state,
        runs={**state.runs, run.run_ref.run_id: run},
        runner_sessions={session.session_id: session},
        runner_session_cancellation_requests={},
        runner_session_cancellation_attempts={},
        runner_session_completions={},
    )
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    try:
        with pytest.raises(StorageIntegrityError, match="generation"):
            store.persist_runtime_state(
                corrupt,
                ContentAddressedByteStore(tmp_path / "cas"),
            )
    finally:
        store.close()


def test_runner_session_same_run_fence_reuse_raw_corruption_is_refused(
    tmp_path,
) -> None:
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, digests = _cas_backed_session_state(_session_state(), cas_store)
    prior = state.runner_sessions["session-1"]
    session = RunnerSessionRecord(
        session_id="session-2",
        run_id=prior.run_id,
        dispatch_generation=2,
        session_fencing_token="session-fence-2",
        state="failed",
        created_at=200,
        start_intent_at=None,
        started_at=None,
        ended_at=250,
        durable_locator_digest=None,
        cleanup_disposition="not_required",
    )
    completion = RunnerSessionCompletionRecord(
        completion_id="completion-2",
        session_id=session.session_id,
        run_id=session.run_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        terminal_state="failed",
        exit_kind="error",
        adapter_outcome_kind=None,
        adapter_error_kind="invocation_failed",
        runner_result_evidence_digest=None,
        primary_cancellation_request_id=None,
        cleanup_disposition="not_required",
        started_at=None,
        cancel_requested_at=None,
        completed_at=250,
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id="redaction.default",
        diagnostic_digest=digests["completion_diagnostic"],
        application_input_id="cli:run.session-completion:completion-2",
    )
    run = state.runs[session.run_id]
    state = replace(
        state,
        runs={
            **state.runs,
            session.run_id: replace(
                run,
                current_session_id=session.session_id,
                last_dispatch_generation=2,
            ),
        },
        runner_sessions={
            **state.runner_sessions,
            session.session_id: session,
        },
        runner_session_completions={
            **state.runner_session_completions,
            session.session_id: completion,
        },
    )
    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE runner_sessions
            SET session_fencing_token = ?
            WHERE session_id = ?
            """,
            (prior.session_fencing_token, session.session_id),
        )
        connection.execute(
            """
            UPDATE runner_session_completions
            SET session_fencing_token = ?
            WHERE session_id = ?
            """,
            (prior.session_fencing_token, session.session_id),
        )

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match="fencing token"):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


@pytest.mark.parametrize(
    ("corruption", "message"),
    (
        ("missing_pointer", "current_session_id"),
        ("wrong_run", "reference runs"),
        ("multiple_nonterminal", "at most one nonterminal"),
    ),
)
def test_runner_session_run_link_corruption_is_refused(
    tmp_path,
    corruption: str,
    message: str,
) -> None:
    state = _session_state()
    run = state.runs["run-taskmaster"]
    session_1 = RunnerSessionRecord(
        session_id="session-1",
        run_id=run.run_ref.run_id,
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        state="created",
        created_at=100,
        start_intent_at=None,
        started_at=None,
        ended_at=None,
        durable_locator_digest=None,
        cleanup_disposition="pending",
    )
    sessions = {session_1.session_id: session_1}
    current_session_id = session_1.session_id
    last_dispatch_generation = 1
    if corruption == "missing_pointer":
        current_session_id = "missing-session"
    elif corruption == "wrong_run":
        session_1 = replace(session_1, run_id="missing-run")
        sessions = {session_1.session_id: session_1}
    else:
        session_2 = RunnerSessionRecord(
            session_id="session-2",
            run_id=run.run_ref.run_id,
            dispatch_generation=2,
            session_fencing_token="session-fence-2",
            state="starting",
            created_at=200,
            start_intent_at=210,
            started_at=None,
            ended_at=None,
            durable_locator_digest=None,
            cleanup_disposition="pending",
        )
        sessions[session_2.session_id] = session_2
        current_session_id = session_2.session_id
        last_dispatch_generation = 2
    corrupt = replace(
        state,
        runs={
            **state.runs,
            run.run_ref.run_id: replace(
                run,
                current_session_id=current_session_id,
                last_dispatch_generation=last_dispatch_generation,
            ),
        },
        runner_sessions=sessions,
        runner_session_cancellation_requests={},
        runner_session_cancellation_attempts={},
        runner_session_completions={},
    )
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    try:
        with pytest.raises(StorageIntegrityError, match=message):
            store.persist_runtime_state(
                corrupt,
                ContentAddressedByteStore(tmp_path / "cas"),
            )
    finally:
        store.close()


@pytest.mark.parametrize(
    ("sql", "message"),
    (
        (
            """
            UPDATE runs
            SET current_session_id = 'missing-session'
            WHERE run_id = 'run-taskmaster'
            """,
            "current_session_id",
        ),
        (
            """
            UPDATE runner_sessions
            SET run_id = 'missing-run'
            WHERE session_id = 'session-1'
            """,
            "reference runs",
        ),
    ),
)
def test_runner_session_run_link_raw_corruption_is_read_only(
    tmp_path,
    sql: str,
    message: str,
) -> None:
    cas_root = tmp_path / "cas"
    cas_store = ContentAddressedByteStore(cas_root)
    state, _digests = _cas_backed_session_state(_session_state(), cas_store)
    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(sql)
    database_before = db_path.read_bytes()
    cas_before = {
        path.relative_to(cas_root): path.read_bytes()
        for path in cas_root.rglob("*")
        if path.is_file()
    }

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match=message):
            store.load_runtime_state(cas_store)
    finally:
        store.close()

    assert db_path.read_bytes() == database_before
    assert {
        path.relative_to(cas_root): path.read_bytes()
        for path in cas_root.rglob("*")
        if path.is_file()
    } == cas_before


def test_runner_session_corrupt_cancellation_attempt_link_is_refused(
    tmp_path,
) -> None:
    state = _session_state()
    db_path = tmp_path / "runtime.sqlite3"
    cas_root = tmp_path / "cas"
    cas_store = ContentAddressedByteStore(cas_root)
    state, _digests = _cas_backed_session_state(state, cas_store)
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE runner_session_cancellation_attempts
            SET request_id = 'missing-request'
            WHERE attempt_id = 'attempt-1'
            """
        )

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(
            StorageIntegrityError,
            match="cancellation attempt.*request",
        ):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


@pytest.mark.parametrize(
    ("sql", "message"),
    (
        (
            """
            UPDATE runner_session_cancellation_requests
            SET request_order = 2
            WHERE request_id = 'cancel-1'
            """,
            "order",
        ),
        (
            """
            UPDATE runner_session_cancellation_requests
            SET primary_request = 0
            WHERE request_id = 'cancel-1'
            """,
            "primary",
        ),
        (
            """
            UPDATE runner_session_cancellation_attempts
            SET sequence = 2
            WHERE attempt_id = 'attempt-1'
            """,
            "order",
        ),
        (
            """
            UPDATE runner_session_cancellation_attempts
            SET session_id = 'missing-session'
            WHERE attempt_id = 'attempt-1'
            """,
            "session",
        ),
    ),
)
def test_runner_session_cancellation_link_raw_corruption_is_refused(
    tmp_path,
    sql: str,
    message: str,
) -> None:
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, _digests = _cas_backed_session_state(_session_state(), cas_store)
    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(sql)
    database_before = db_path.read_bytes()
    cas_before = {
        path.relative_to(tmp_path / "cas"): path.read_bytes()
        for path in (tmp_path / "cas").rglob("*")
        if path.is_file()
    }

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match=message):
            store.load_runtime_state(cas_store)
    finally:
        store.close()
    assert db_path.read_bytes() == database_before
    assert {
        path.relative_to(tmp_path / "cas"): path.read_bytes()
        for path in (tmp_path / "cas").rglob("*")
        if path.is_file()
    } == cas_before


@pytest.mark.parametrize(
    ("column", "value", "message"),
    (
        ("run_id", "missing-run", "contradict session"),
        ("dispatch_generation", 2, "contradict session"),
        ("session_fencing_token", "changed-fence", "contradict session"),
        ("terminal_state", "failed", "contradict session"),
        ("cleanup_disposition", "not_required", "contradict session"),
        ("application_input_id", "wrong-input-id", "application_input_id"),
    ),
)
def test_runner_session_completion_link_raw_corruption_is_refused(
    tmp_path,
    column: str,
    value: str | int,
    message: str,
) -> None:
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, _digests = _cas_backed_session_state(_session_state(), cas_store)
    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            f"""
            UPDATE runner_session_completions
            SET {column} = ?
            WHERE session_id = 'session-1'
            """,
            (value,),
        )
    database_before = db_path.read_bytes()
    cas_before = {
        path.relative_to(tmp_path / "cas"): path.read_bytes()
        for path in (tmp_path / "cas").rglob("*")
        if path.is_file()
    }

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match=message):
            store.load_runtime_state(cas_store)
    finally:
        store.close()
    assert db_path.read_bytes() == database_before
    assert {
        path.relative_to(tmp_path / "cas"): path.read_bytes()
        for path in (tmp_path / "cas").rglob("*")
        if path.is_file()
    } == cas_before


def test_runner_session_attempt_linked_to_secondary_request_is_refused(
    tmp_path,
) -> None:
    state = _session_state()
    db_path = tmp_path / "runtime.sqlite3"
    cas_root = tmp_path / "cas"
    cas_store = ContentAddressedByteStore(cas_root)
    state, _digests = _cas_backed_session_state(state, cas_store)
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO runner_session_cancellation_requests (
                schema_version,
                request_id,
                session_id,
                dispatch_generation,
                reason,
                source_kind,
                actor_id,
                requested_at,
                request_order,
                primary_request
            ) VALUES (1, 'cancel-2', 'session-1', 1, 'daemon_shutdown',
                      'daemon', 'daemon-1', 131, 2, 0)
            """
        )
        connection.execute(
            """
            UPDATE runner_session_cancellation_attempts
            SET request_id = 'cancel-2'
            WHERE attempt_id = 'attempt-1'
            """
        )

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match="primary"):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


def test_runner_session_history_on_running_raw_corruption_is_refused(
    tmp_path,
) -> None:
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, _digests = _cas_backed_session_state(_session_state(), cas_store)
    session = replace(
        state.runner_sessions["session-1"],
        state="cancellation_requested",
        ended_at=None,
        cleanup_disposition="pending",
    )
    state = replace(
        state,
        runner_sessions={session.session_id: session},
        runner_session_completions={},
    )
    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE runner_sessions
            SET state = 'running'
            WHERE session_id = 'session-1'
            """
        )

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match="cancellation history"):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


@pytest.mark.parametrize(
    "active_state",
    ("cancellation_requested", "terminating"),
)
def test_active_cancellation_state_missing_primary_raw_corruption_is_refused(
    tmp_path,
    active_state: str,
) -> None:
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, _digests = _cas_backed_session_state(_session_state(), cas_store)
    session = replace(
        state.runner_sessions["session-1"],
        state=active_state,
        ended_at=None,
        cleanup_disposition="pending",
    )
    state = replace(
        state,
        runner_sessions={session.session_id: session},
        runner_session_completions={},
    )
    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()

    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM runner_session_cancellation_attempts")
        connection.execute("DELETE FROM runner_session_cancellation_requests")

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match="requires primary"):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


def test_interrupted_completion_missing_primary_link_raw_corruption_is_refused(
    tmp_path,
) -> None:
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, _digests = _cas_backed_session_state(_session_state(), cas_store)
    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE runner_session_completions
            SET primary_cancellation_request_id = NULL,
                cancel_requested_at = NULL
            WHERE session_id = 'session-1'
            """
        )

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match="interrupted"):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


@pytest.mark.parametrize("terminal_state", ("completed", "failed", "lost"))
def test_terminal_completion_race_may_retain_unlinked_cancellation_history(
    tmp_path,
    terminal_state: str,
) -> None:
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, digests = _cas_backed_session_state(_session_state(), cas_store)
    cleanup = "orphan_risk" if terminal_state == "lost" else "complete"
    session = replace(
        state.runner_sessions["session-1"],
        state=terminal_state,
        cleanup_disposition=cleanup,
    )
    completion = replace(
        state.runner_session_completions["session-1"],
        completion_id=f"completion-{terminal_state}",
        terminal_state=terminal_state,
        exit_kind="success" if terminal_state == "completed" else "error",
        adapter_outcome_kind=(
            "success" if terminal_state == "completed" else None
        ),
        adapter_error_kind=(
            None if terminal_state == "completed" else "invocation_failed"
        ),
        runner_result_evidence_digest=(
            digests["completed_evidence"]
            if terminal_state == "completed"
            else None
        ),
        primary_cancellation_request_id=None,
        cleanup_disposition=cleanup,
        cancel_requested_at=None,
        application_input_id=(
            f"cli:run.session-completion:completion-{terminal_state}"
        ),
    )
    state = replace(
        state,
        runner_sessions={session.session_id: session},
        runner_session_completions={session.session_id: completion},
    )
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    try:
        store.persist_runtime_state(state, cas_store)
        loaded = store.load_runtime_state(cas_store)
    finally:
        store.close()

    assert loaded.runner_sessions["session-1"].state == terminal_state
    assert "cancel-1" in loaded.runner_session_cancellation_requests


def test_runner_session_reason_source_mismatch_raw_row_is_refused(
    tmp_path,
) -> None:
    state = _session_state()
    db_path = tmp_path / "runtime.sqlite3"
    cas_root = tmp_path / "cas"
    cas_store = ContentAddressedByteStore(cas_root)
    state, _digests = _cas_backed_session_state(state, cas_store)
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE runner_session_cancellation_requests
            SET source_kind = 'daemon'
            WHERE request_id = 'cancel-1'
            """
        )

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match="reason and source"):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


@pytest.mark.parametrize(
    ("sql", "message"),
    (
        (
            """
            UPDATE runner_session_completions
            SET started_at = 121
            WHERE session_id = 'session-1'
            """,
            "started",
        ),
        (
            """
            UPDATE runner_session_completions
            SET primary_cancellation_request_id = NULL
            WHERE session_id = 'session-1'
            """,
            "cancellation",
        ),
    ),
)
def test_runner_session_completion_phase_raw_row_is_refused(
    tmp_path,
    sql: str,
    message: str,
) -> None:
    state = _session_state()
    db_path = tmp_path / "runtime.sqlite3"
    cas_root = tmp_path / "cas"
    cas_store = ContentAddressedByteStore(cas_root)
    state, _digests = _cas_backed_session_state(state, cas_store)
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(sql)

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match=message):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


@pytest.mark.parametrize("started_at", (None, 105))
def test_starting_completed_start_evidence_raw_row_is_refused(
    tmp_path,
    started_at: int | None,
) -> None:
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, _digests = _cas_backed_session_state(
        _session_state(),
        cas_store,
        completed=True,
    )
    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE runner_sessions
            SET started_at = ?
            WHERE session_id = 'session-1'
            """,
            (started_at,),
        )
        connection.execute(
            """
            UPDATE runner_session_completions
            SET started_at = ?
            WHERE session_id = 'session-1'
            """,
            (started_at,),
        )

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match="start|timestamp"):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


def test_cancellation_request_before_session_phase_raw_row_is_refused(
    tmp_path,
) -> None:
    state = _session_state()
    session = replace(
        state.runner_sessions["session-1"],
        state="cancellation_requested",
        ended_at=None,
        durable_locator_digest=None,
        cleanup_disposition="pending",
    )
    state = replace(
        state,
        runner_sessions={session.session_id: session},
        runner_session_cancellation_attempts={},
        runner_session_completions={},
    )
    db_path = tmp_path / "runtime.sqlite3"
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE runner_session_cancellation_requests
            SET requested_at = 119
            WHERE request_id = 'cancel-1'
            """
        )

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match="predates session phase"):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


@pytest.mark.parametrize("record_kind", ("request", "attempt"))
def test_post_terminal_cancellation_history_raw_row_is_refused(
    tmp_path,
    record_kind: str,
) -> None:
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, _digests = _cas_backed_session_state(_session_state(), cas_store)
    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()
    with sqlite3.connect(db_path) as connection:
        if record_kind == "request":
            connection.execute(
                "DELETE FROM runner_session_cancellation_attempts"
            )
            connection.execute(
                """
                UPDATE runner_session_completions
                SET primary_cancellation_request_id = NULL,
                    cancel_requested_at = NULL
                WHERE session_id = 'session-1'
                """
            )
            connection.execute(
                """
                UPDATE runner_session_cancellation_requests
                SET requested_at = 151
                WHERE request_id = 'cancel-1'
                """
            )
        else:
            connection.execute(
                """
                UPDATE runner_session_cancellation_attempts
                SET completed_at = 151
                WHERE attempt_id = 'attempt-1'
                """
            )

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match="ended_at"):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


def test_terminal_cancellation_history_allows_ended_at_equality(tmp_path) -> None:
    state = _session_state()
    request = replace(
        state.runner_session_cancellation_requests["cancel-1"],
        requested_at=150,
    )
    attempt = replace(
        state.runner_session_cancellation_attempts["attempt-1"],
        started_at=150,
        completed_at=150,
    )
    completion = replace(
        state.runner_session_completions["session-1"],
        cancel_requested_at=150,
    )
    state = replace(
        state,
        runner_session_cancellation_requests={request.request_id: request},
        runner_session_cancellation_attempts={attempt.attempt_id: attempt},
        runner_session_completions={completion.session_id: completion},
    )
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, _digests = _cas_backed_session_state(state, cas_store)
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    try:
        store.persist_runtime_state(state, cas_store)
        loaded = store.load_runtime_state(cas_store)
    finally:
        store.close()

    assert loaded == state


def test_runner_session_schema_rejects_oversized_text(tmp_path) -> None:
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, _digests = _cas_backed_session_state(_session_state(), cas_store)
    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE runner_session_completions
                SET bounds_summary = ?
                WHERE session_id = 'session-1'
                """,
                ("x" * 4097,),
            )


@pytest.mark.parametrize(
    ("sql", "message"),
    (
        (
            """
            UPDATE runner_session_completions
            SET truncation_metadata = ?
            WHERE session_id = 'session-1'
            """,
            "4096",
        ),
        (
            """
            UPDATE runs
            SET current_session_id = ?
            WHERE run_id = 'run-taskmaster'
            """,
            "4096",
        ),
    ),
)
def test_runner_session_oversized_raw_text_is_refused_on_reload(
    tmp_path,
    sql: str,
    message: str,
) -> None:
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, _digests = _cas_backed_session_state(_session_state(), cas_store)
    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(sql, ("x" * 4097,))

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match=message):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


def test_v6_store_is_refused_unchanged_by_session_runtime(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    cas_root = tmp_path / "cas"
    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()
    cas_root.mkdir()
    marker = cas_root / "marker"
    marker.write_bytes(b"unchanged-cas")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE store_metadata SET store_schema_version = 6 WHERE id = 1"
        )
    before_db = db_path.read_bytes()
    before_cas = marker.read_bytes()

    with pytest.raises(StoreSchemaUpgradeRequired):
        SQLiteRuntimeStore.open(db_path)
    with pytest.raises(StoreSchemaUpgradeRequired):
        SQLiteRuntimeStore.initialize(db_path)

    assert db_path.read_bytes() == before_db
    assert marker.read_bytes() == before_cas
