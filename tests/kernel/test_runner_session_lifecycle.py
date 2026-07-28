from __future__ import annotations

from dataclasses import replace

import pytest

from kernel.kernel_ping_scenarios import bootstrap_to_taskmaster_claim
from millrace.compiler import compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.state import (
    RunnerSessionCancellationAttemptRecord,
    RunnerSessionCancellationRecord,
    RunnerSessionCompletionRecord,
    RunnerSessionRecord,
)
from millrace.contracts.transition import (
    AdvanceRunnerSession,
    CreateRunnerSession,
    RecordRunnerSessionCancellationAttempt,
    RecordRunnerSessionCompletion,
    RequestRunnerSessionCancellation,
)
from millrace.kernel import StateConcurrencyError, apply, decide
from millrace.kernel.runner_sessions import is_legal_runner_session_transition
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.sqlite import SQLiteRuntimeStore
from millrace.testing import deterministic_context
from millrace.workflows import kernel_ping


def _claimed_state():
    result = compile_workflow(kernel_ping.workflow_source())
    assert result.plan is not None
    fingerprint = authority_fingerprint(result.plan)
    return bootstrap_to_taskmaster_claim(result.plan, fingerprint)


def test_session_creation_refuses_run_authority_drift() -> None:
    state = _claimed_state()
    run = state.runs["run-taskmaster"]
    stale_ref = replace(run.run_ref, generation=run.run_ref.generation + 1)

    decision = decide(
        state,
        CreateRunnerSession(
            "create-session-drift",
            run_ref=stale_ref,
            session_id="session-1",
            session_fencing_token="session-fence-1",
            created_at=100,
            explicit_retry_intent=False,
        ),
        deterministic_context(transition_id="transition-create-session-drift"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "runner_session_authority_mismatch"


@pytest.mark.parametrize(
    "field_name",
    (
        "run_id",
        "work_item_id",
        "claim_id",
        "plan_id",
        "authority_fingerprint",
        "plan_format_version",
        "generation",
        "fencing_token",
    ),
)
def test_session_creation_refuses_every_runref_authority_drift(
    field_name: str,
) -> None:
    state = _claimed_state()
    run_ref = state.runs["run-taskmaster"].run_ref
    if field_name in {
        "plan_id",
        "authority_fingerprint",
        "plan_format_version",
    }:
        value: str | int
        if field_name == "plan_format_version":
            value = run_ref.plan_ref.plan_format_version + 1
        else:
            value = f"changed-{field_name}"
        stale_ref = replace(
            run_ref,
            plan_ref=replace(run_ref.plan_ref, **{field_name: value}),
        )
    else:
        current = getattr(run_ref, field_name)
        value = current + 1 if isinstance(current, int) else f"changed-{field_name}"
        stale_ref = replace(run_ref, **{field_name: value})

    decision = decide(
        state,
        CreateRunnerSession(
            f"create-drift-{field_name}",
            run_ref=stale_ref,
            session_id="session-1",
            session_fencing_token="session-fence-1",
            created_at=100,
            explicit_retry_intent=False,
        ),
        deterministic_context(transition_id=f"transition-drift-{field_name}"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "runner_session_authority_mismatch"


def test_session_creation_apply_rechecks_complete_run_authority() -> None:
    state = _claimed_state()
    run = state.runs["run-taskmaster"]
    transition_input = CreateRunnerSession(
        "create-session",
        run_ref=run.run_ref,
        session_id="session-1",
        session_fencing_token="session-fence-1",
        created_at=100,
        explicit_retry_intent=False,
    )
    decision = decide(
        state,
        transition_input,
        deterministic_context(transition_id="transition-create-session"),
    )
    drifted_run = replace(
        run,
        run_ref=replace(run.run_ref, claim_id="changed-claim"),
    )
    drifted_state = replace(
        state,
        runs={**state.runs, run.run_ref.run_id: drifted_run},
    )

    with pytest.raises(StateConcurrencyError, match="authority"):
        apply(drifted_state, decision)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("dispatch_generation", 2),
        ("session_fencing_token", "changed-fence"),
        ("state", "cancellation_requested"),
    ),
)
def test_session_advance_apply_rechecks_session_authority(
    field_name: str,
    value: str | int,
) -> None:
    state = _claimed_state()
    run_ref = state.runs["run-taskmaster"].run_ref
    state = apply(
        state,
        decide(
            state,
            CreateRunnerSession(
                "create-session",
                run_ref=run_ref,
                session_id="session-1",
                session_fencing_token="session-fence-1",
                created_at=100,
                explicit_retry_intent=False,
            ),
            deterministic_context(transition_id="transition-create-session"),
        ),
    )
    decision = decide(
        state,
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
        deterministic_context(transition_id="transition-start-session"),
    )
    session = replace(state.runner_sessions["session-1"], **{field_name: value})
    drifted = replace(
        state,
        runner_sessions={session.session_id: session},
    )

    with pytest.raises(StateConcurrencyError, match="session"):
        apply(drifted, decision)


def test_session_creation_does_not_persist_a_prestart_locator() -> None:
    state = _claimed_state()
    run_ref = state.runs["run-taskmaster"].run_ref
    decision = decide(
        state,
        CreateRunnerSession(
            "create-session",
            run_ref=run_ref,
            session_id="session-1",
            session_fencing_token="session-fence-1",
            created_at=100,
            explicit_retry_intent=False,
        ),
        deterministic_context(transition_id="transition-create-session"),
    )

    created = apply(state, decision).runner_sessions["session-1"]

    assert created.durable_locator_digest is None


def test_explicit_retry_keeps_runref_and_advances_session_generation(
    tmp_path,
) -> None:
    state = _claimed_state()
    original_run_ref = state.runs["run-taskmaster"].run_ref
    first = CreateRunnerSession(
        "create-session-1",
        run_ref=original_run_ref,
        session_id="session-1",
        session_fencing_token="session-fence-1",
        created_at=100,
        explicit_retry_intent=False,
    )
    state = apply(
        state,
        decide(
            state,
            first,
            deterministic_context(transition_id="transition-create-session-1"),
        ),
    )
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    completion = RunnerSessionCompletionRecord(
        completion_id="completion-1",
        session_id="session-1",
        run_id=original_run_ref.run_id,
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        terminal_state="failed",
        exit_kind="error",
        adapter_outcome_kind=None,
        adapter_error_kind="invocation_failed",
        runner_result_evidence_digest=None,
        primary_cancellation_request_id=None,
        cleanup_disposition="not_required",
        started_at=None,
        cancel_requested_at=None,
        completed_at=150,
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id="redaction.default",
        diagnostic_digest=cas_store.put_bytes(b"failure diagnostic"),
        application_input_id="cli:run.session-completion:completion-1",
    )
    completion_decision = decide(
        state,
        RecordRunnerSessionCompletion(
            "complete-session-1",
            run_ref=original_run_ref,
            expected_state="created",
            completion=completion,
        ),
        deterministic_context(transition_id="transition-complete-session-1"),
    )
    assert completion_decision.accepted
    state = apply(state, completion_decision)
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    try:
        store.persist_runtime_state(state, cas_store)
        state = store.load_runtime_state(cas_store)
    finally:
        store.close()

    retry = CreateRunnerSession(
        "create-session-2",
        run_ref=original_run_ref,
        session_id="session-2",
        session_fencing_token="session-fence-2",
        created_at=200,
        explicit_retry_intent=True,
    )
    reused_fence_decision = decide(
        state,
        replace(retry, session_fencing_token="session-fence-1"),
        deterministic_context(transition_id="transition-reused-session-fence"),
    )
    assert reused_fence_decision.accepted is False
    assert reused_fence_decision.refusal is not None
    assert (
        reused_fence_decision.refusal.reason
        == "runner_session_reconciliation_contradiction"
    )

    state = apply(
        state,
        decide(
            state,
            retry,
            deterministic_context(transition_id="transition-create-session-2"),
        ),
    )

    assert state.runs["run-taskmaster"].run_ref == original_run_ref
    assert state.runs["run-taskmaster"].current_session_id == "session-2"
    assert state.runs["run-taskmaster"].last_dispatch_generation == 2
    assert state.runner_sessions["session-2"].dispatch_generation == 2


@pytest.mark.parametrize(
    ("prior", "next_state"),
    (
        ("created", "starting"),
        ("created", "cancellation_requested"),
        ("created", "failed"),
        ("starting", "running"),
        ("starting", "completed"),
        ("starting", "failed"),
        ("starting", "cancellation_requested"),
        ("starting", "lost"),
        ("running", "completed"),
        ("running", "failed"),
        ("running", "cancellation_requested"),
        ("running", "lost"),
        ("cancellation_requested", "terminating"),
        ("cancellation_requested", "completed"),
        ("cancellation_requested", "interrupted"),
        ("cancellation_requested", "failed"),
        ("cancellation_requested", "lost"),
        ("terminating", "completed"),
        ("terminating", "interrupted"),
        ("terminating", "failed"),
        ("terminating", "lost"),
    ),
)
def test_runner_session_legal_state_transitions(
    prior: str,
    next_state: str,
) -> None:
    assert is_legal_runner_session_transition(prior, next_state)


@pytest.mark.parametrize(
    ("prior", "next_state"),
    (
        ("created", "starting"),
        ("created", "cancellation_requested"),
        ("created", "failed"),
        ("starting", "running"),
        ("starting", "completed"),
        ("starting", "failed"),
        ("starting", "cancellation_requested"),
        ("starting", "lost"),
        ("running", "completed"),
        ("running", "failed"),
        ("running", "cancellation_requested"),
        ("running", "lost"),
        ("cancellation_requested", "terminating"),
        ("cancellation_requested", "completed"),
        ("cancellation_requested", "interrupted"),
        ("cancellation_requested", "failed"),
        ("cancellation_requested", "lost"),
        ("terminating", "completed"),
        ("terminating", "interrupted"),
        ("terminating", "failed"),
        ("terminating", "lost"),
    ),
)
def test_every_legal_runner_session_edge_decides_and_applies(
    prior: str,
    next_state: str,
) -> None:
    state = _claimed_state()
    run = state.runs["run-taskmaster"]
    start_intent_at = None if prior == "created" else 110
    started_at = (
        120
        if prior in {"running", "cancellation_requested", "terminating"}
        else None
    )
    session = RunnerSessionRecord(
        session_id="session-1",
        run_id=run.run_ref.run_id,
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        state=prior,
        created_at=100,
        start_intent_at=start_intent_at,
        started_at=started_at,
        ended_at=None,
        durable_locator_digest=None,
        cleanup_disposition="pending",
    )
    requests = {}
    if prior in {"cancellation_requested", "terminating"}:
        request = RunnerSessionCancellationRecord(
            request_id="cancel-1",
            session_id=session.session_id,
            dispatch_generation=1,
            reason="operator_cancel_work",
            source_kind="operator",
            actor_id="operator-1",
            requested_at=130,
            request_order=1,
            primary=True,
        )
        requests = {request.request_id: request}
    state = replace(
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
        runner_session_cancellation_requests=requests,
    )
    terminal_states = {"completed", "interrupted", "failed", "lost"}
    if next_state in terminal_states:
        completion_started_at = started_at
        if prior == "starting" and next_state == "completed":
            completion_started_at = 120
        cleanup = "orphan_risk" if next_state == "lost" else "complete"
        completion = RunnerSessionCompletionRecord(
            completion_id="completion-1",
            session_id=session.session_id,
            run_id=run.run_ref.run_id,
            dispatch_generation=1,
            session_fencing_token=session.session_fencing_token,
            terminal_state=next_state,
            exit_kind="success" if next_state == "completed" else "error",
            adapter_outcome_kind=(
                "success" if next_state == "completed" else None
            ),
            adapter_error_kind=(
                None if next_state == "completed" else "invocation_failed"
            ),
            runner_result_evidence_digest=(
                "sha256:" + "a" * 64 if next_state == "completed" else None
            ),
            primary_cancellation_request_id=(
                "cancel-1"
                if prior in {"cancellation_requested", "terminating"}
                else None
            ),
            cleanup_disposition=cleanup,
            started_at=completion_started_at,
            cancel_requested_at=(
                130
                if prior in {"cancellation_requested", "terminating"}
                else None
            ),
            completed_at=150,
            bounds_summary="bounded",
            truncation_metadata="none",
            redaction_policy_id="redaction.default",
            diagnostic_digest="sha256:" + "b" * 64,
            application_input_id="cli:run.session-completion:completion-1",
        )
        transition_input = RecordRunnerSessionCompletion(
            "complete-session",
            run_ref=run.run_ref,
            expected_state=prior,
            completion=completion,
        )
    elif next_state == "cancellation_requested":
        transition_input = RequestRunnerSessionCancellation(
            "cancel-session",
            run_ref=run.run_ref,
            session_id=session.session_id,
            dispatch_generation=1,
            session_fencing_token=session.session_fencing_token,
            expected_state=prior,
            request_id="cancel-1",
            reason="operator_cancel_work",
            source_kind="operator",
            actor_id="operator-1",
            requested_at=130,
            request_order=1,
            primary=True,
        )
    else:
        transition_input = AdvanceRunnerSession(
            "advance-session",
            run_ref=run.run_ref,
            session_id=session.session_id,
            dispatch_generation=1,
            session_fencing_token=session.session_fencing_token,
            expected_state=prior,
            next_state=next_state,
            occurred_at=140,
        )

    decision = decide(
        state,
        transition_input,
        deterministic_context(
            transition_id=f"transition-{prior}-{next_state}"
        ),
    )

    assert decision.accepted
    next_runtime_state = apply(state, decision)
    assert next_runtime_state.runner_sessions[session.session_id].state == next_state
    assert (
        session.session_id in next_runtime_state.runner_session_completions
    ) == (next_state in terminal_states)


@pytest.mark.parametrize(
    ("prior", "next_state"),
    (
        ("created", "running"),
        ("running", "starting"),
        ("completed", "running"),
        ("interrupted", "failed"),
        ("failed", "starting"),
        ("lost", "created"),
    ),
)
def test_runner_session_illegal_state_transitions(
    prior: str,
    next_state: str,
) -> None:
    assert not is_legal_runner_session_transition(prior, next_state)


@pytest.mark.parametrize(
    ("state", "cleanup", "valid"),
    (
        ("completed", "complete", True),
        ("failed", "not_required", True),
        ("interrupted", "complete", True),
        ("lost", "orphan_risk", True),
        ("completed", "pending", False),
        ("failed", "orphan_risk", False),
        ("interrupted", "pending", False),
        ("lost", "complete", False),
    ),
)
def test_runner_session_terminal_cleanup_pairing(
    state: str,
    cleanup: str,
    valid: bool,
) -> None:
    kwargs = {
        "session_id": "session-1",
        "run_id": "run-1",
        "dispatch_generation": 1,
        "session_fencing_token": "session-fence-1",
        "state": state,
        "created_at": 100,
        "start_intent_at": 110,
        "started_at": 120,
        "ended_at": 130,
        "durable_locator_digest": "sha256:" + "a" * 64,
        "cleanup_disposition": cleanup,
    }
    if valid:
        assert RunnerSessionRecord(**kwargs).cleanup_disposition == cleanup
    else:
        with pytest.raises(ValueError):
            RunnerSessionRecord(**kwargs)


def test_completed_session_requires_result_evidence() -> None:
    with pytest.raises(ValueError, match="result evidence"):
        RunnerSessionCompletionRecord(
            completion_id="completion-1",
            session_id="session-1",
            run_id="run-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            terminal_state="completed",
            exit_kind="success",
            adapter_outcome_kind="success",
            adapter_error_kind=None,
            runner_result_evidence_digest=None,
            primary_cancellation_request_id=None,
            cleanup_disposition="complete",
            started_at=120,
            cancel_requested_at=None,
            completed_at=130,
            bounds_summary="bounded",
            truncation_metadata="none",
            redaction_policy_id="redaction.default",
            diagnostic_digest="sha256:" + "a" * 64,
            application_input_id="cli:run.session-completion:completion-1",
        )


@pytest.mark.parametrize("terminal_state", ("failed", "interrupted", "lost"))
def test_noncompleted_session_refuses_result_evidence(
    terminal_state: str,
) -> None:
    cleanup = "orphan_risk" if terminal_state == "lost" else "complete"
    with pytest.raises(ValueError, match="completed"):
        RunnerSessionCompletionRecord(
            completion_id="completion-1",
            session_id="session-1",
            run_id="run-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            terminal_state=terminal_state,
            exit_kind="error",
            adapter_outcome_kind=None,
            adapter_error_kind="invocation_failed",
            runner_result_evidence_digest="sha256:" + "a" * 64,
            primary_cancellation_request_id=None,
            cleanup_disposition=cleanup,
            started_at=120,
            cancel_requested_at=None,
            completed_at=130,
            bounds_summary="bounded",
            truncation_metadata="none",
            redaction_policy_id="redaction.default",
            diagnostic_digest="sha256:" + "b" * 64,
            application_input_id="cli:run.session-completion:completion-1",
        )


def test_completed_session_requires_started_phase() -> None:
    with pytest.raises(ValueError, match="start"):
        RunnerSessionRecord(
            session_id="session-1",
            run_id="run-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            state="completed",
            created_at=100,
            start_intent_at=None,
            started_at=None,
            ended_at=130,
            durable_locator_digest=None,
            cleanup_disposition="not_required",
        )


def test_completion_cancel_time_requires_primary_request_id() -> None:
    with pytest.raises(ValueError, match="cancellation"):
        RunnerSessionCompletionRecord(
            completion_id="completion-1",
            session_id="session-1",
            run_id="run-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            terminal_state="interrupted",
            exit_kind="cancelled",
            adapter_outcome_kind=None,
            adapter_error_kind=None,
            runner_result_evidence_digest=None,
            primary_cancellation_request_id=None,
            cleanup_disposition="complete",
            started_at=120,
            cancel_requested_at=125,
            completed_at=130,
            bounds_summary="bounded",
            truncation_metadata="none",
            redaction_policy_id="redaction.default",
            diagnostic_digest="sha256:" + "a" * 64,
            application_input_id="cli:run.session-completion:completion-1",
        )


@pytest.mark.parametrize("started_at", (None, 105))
def test_starting_completed_refuses_absent_or_backdated_start_evidence(
    started_at: int | None,
) -> None:
    state = _claimed_state()
    run = state.runs["run-taskmaster"]
    session = RunnerSessionRecord(
        session_id="session-1",
        run_id=run.run_ref.run_id,
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        state="starting",
        created_at=100,
        start_intent_at=110,
        started_at=None,
        ended_at=None,
        durable_locator_digest=None,
        cleanup_disposition="pending",
    )
    state = replace(
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
    )
    completion = RunnerSessionCompletionRecord(
        completion_id="completion-1",
        session_id=session.session_id,
        run_id=run.run_ref.run_id,
        dispatch_generation=1,
        session_fencing_token=session.session_fencing_token,
        terminal_state="completed",
        exit_kind="success",
        adapter_outcome_kind="success",
        adapter_error_kind=None,
        runner_result_evidence_digest="sha256:" + "a" * 64,
        primary_cancellation_request_id=None,
        cleanup_disposition="complete",
        started_at=started_at,
        cancel_requested_at=None,
        completed_at=130,
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id="redaction.default",
        diagnostic_digest="sha256:" + "b" * 64,
        application_input_id="cli:run.session-completion:completion-1",
    )

    decision = decide(
        state,
        RecordRunnerSessionCompletion(
            "complete-starting",
            run_ref=run.run_ref,
            expected_state="starting",
            completion=completion,
        ),
        deterministic_context(transition_id="transition-complete-starting"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "runner_session_reconciliation_contradiction"


@pytest.mark.parametrize(
    ("prior", "started_at"),
    (
        ("cancellation_requested", None),
        ("cancellation_requested", 105),
        ("terminating", None),
        ("terminating", 105),
    ),
)
def test_composed_completed_refuses_absent_or_backdated_start_evidence(
    prior: str,
    started_at: int | None,
) -> None:
    state = _claimed_state()
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
        runner_result_evidence_digest="sha256:" + "a" * 64,
        primary_cancellation_request_id="cancel-1",
        cleanup_disposition="complete",
        started_at=started_at,
        cancel_requested_at=115,
        completed_at=130,
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id="redaction.default",
        diagnostic_digest="sha256:" + "b" * 64,
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

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "runner_session_reconciliation_contradiction"


@pytest.mark.parametrize(
    ("scenario", "completed_at", "accepted"),
    (
        ("created", 99, False),
        ("starting", 109, False),
        ("secondary_request", 135, False),
        ("attempt_completion", 140, False),
        ("equality", 145, True),
    ),
)
def test_completion_temporal_admission_is_stable_before_apply(
    scenario: str,
    completed_at: int,
    accepted: bool,
) -> None:
    state = _claimed_state()
    run = state.runs["run-taskmaster"]
    prior = scenario
    if scenario in {"secondary_request", "attempt_completion", "equality"}:
        prior = "cancellation_requested"
    session = RunnerSessionRecord(
        session_id="session-1",
        run_id=run.run_ref.run_id,
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        state=prior,
        created_at=100,
        start_intent_at=None if prior == "created" else 110,
        started_at=120 if prior == "cancellation_requested" else None,
        ended_at=None,
        durable_locator_digest=None,
        cleanup_disposition="pending",
    )
    requests = {}
    attempts = {}
    if prior == "cancellation_requested":
        primary = RunnerSessionCancellationRecord(
            request_id="cancel-1",
            session_id=session.session_id,
            dispatch_generation=1,
            reason="operator_cancel_work",
            source_kind="operator",
            actor_id="operator-1",
            requested_at=130,
            request_order=1,
            primary=True,
        )
        requests[primary.request_id] = primary
        if scenario == "secondary_request":
            secondary = RunnerSessionCancellationRecord(
                request_id="cancel-2",
                session_id=session.session_id,
                dispatch_generation=1,
                reason="daemon_shutdown",
                source_kind="daemon",
                actor_id="daemon-1",
                requested_at=140,
                request_order=2,
                primary=False,
            )
            requests[secondary.request_id] = secondary
        if scenario in {"attempt_completion", "equality"}:
            attempt = RunnerSessionCancellationAttemptRecord(
                attempt_id="attempt-1",
                session_id=session.session_id,
                request_id=primary.request_id,
                sequence=1,
                operation="cooperative_cancel",
                result="succeeded",
                started_at=135,
                completed_at=145,
                bounded_diagnostic_digest="sha256:" + "a" * 64,
            )
            attempts[attempt.attempt_id] = attempt
    state = replace(
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
        runner_session_cancellation_requests=requests,
        runner_session_cancellation_attempts=attempts,
    )
    completion = RunnerSessionCompletionRecord(
        completion_id="completion-1",
        session_id=session.session_id,
        run_id=run.run_ref.run_id,
        dispatch_generation=1,
        session_fencing_token=session.session_fencing_token,
        terminal_state="failed",
        exit_kind="error",
        adapter_outcome_kind=None,
        adapter_error_kind="invocation_failed",
        runner_result_evidence_digest=None,
        primary_cancellation_request_id=(
            "cancel-1" if prior == "cancellation_requested" else None
        ),
        cleanup_disposition="complete",
        started_at=session.started_at,
        cancel_requested_at=(
            130 if prior == "cancellation_requested" else None
        ),
        completed_at=completed_at,
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id="redaction.default",
        diagnostic_digest="sha256:" + "b" * 64,
        application_input_id="cli:run.session-completion:completion-1",
    )

    decision = decide(
        state,
        RecordRunnerSessionCompletion(
            "complete-session",
            run_ref=run.run_ref,
            expected_state=prior,
            completion=completion,
        ),
        deterministic_context(transition_id="transition-complete-session"),
    )

    assert decision.accepted is accepted
    if accepted:
        assert apply(state, decision).runner_sessions["session-1"].ended_at == 145
    else:
        assert decision.refusal is not None
        assert (
            decision.refusal.reason
            == "runner_session_reconciliation_contradiction"
        )


def test_cancellation_request_refuses_time_before_latest_session_phase() -> None:
    state = _claimed_state()
    run = state.runs["run-taskmaster"]
    session = RunnerSessionRecord(
        session_id="session-1",
        run_id=run.run_ref.run_id,
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        state="running",
        created_at=100,
        start_intent_at=110,
        started_at=120,
        ended_at=None,
        durable_locator_digest=None,
        cleanup_disposition="pending",
    )
    state = replace(
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
    )

    decision = decide(
        state,
        RequestRunnerSessionCancellation(
            "cancel-before-start",
            run_ref=run.run_ref,
            session_id=session.session_id,
            dispatch_generation=1,
            session_fencing_token=session.session_fencing_token,
            expected_state="running",
            request_id="cancel-1",
            reason="operator_cancel_work",
            source_kind="operator",
            actor_id="operator-1",
            requested_at=119,
            request_order=1,
            primary=True,
        ),
        deterministic_context(transition_id="transition-cancel-before-start"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "runner_session_reconciliation_contradiction"


def test_runner_session_variable_text_is_bounded_by_utf8_bytes() -> None:
    allowed_actor = "é" * 2048
    record = RunnerSessionCancellationRecord(
        request_id="cancel-1",
        session_id="session-1",
        dispatch_generation=1,
        reason="operator_cancel_work",
        source_kind="operator",
        actor_id=allowed_actor,
        requested_at=130,
        request_order=1,
        primary=True,
    )
    assert record.actor_id == allowed_actor

    with pytest.raises(ValueError, match="4096"):
        replace(record, actor_id=allowed_actor + "a")


@pytest.mark.parametrize(
    "field_name",
    ("bounds_summary", "truncation_metadata", "redaction_policy_id"),
)
def test_runner_session_completion_metadata_is_bounded(
    field_name: str,
) -> None:
    completion = RunnerSessionCompletionRecord(
        completion_id="completion-1",
        session_id="session-1",
        run_id="run-1",
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        terminal_state="interrupted",
        exit_kind="cancelled",
        adapter_outcome_kind=None,
        adapter_error_kind=None,
        runner_result_evidence_digest=None,
        primary_cancellation_request_id=None,
        cleanup_disposition="complete",
        started_at=120,
        cancel_requested_at=None,
        completed_at=130,
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id="redaction.default",
        diagnostic_digest="sha256:" + "a" * 64,
        application_input_id="cli:run.session-completion:completion-1",
    )

    at_limit = replace(completion, **{field_name: "x" * 4096})
    assert len(getattr(at_limit, field_name)) == 4096
    with pytest.raises(ValueError, match="4096"):
        replace(completion, **{field_name: "x" * 4097})


def test_optional_runner_session_text_cannot_be_empty() -> None:
    with pytest.raises(ValueError, match="non-blank"):
        RunnerSessionCompletionRecord(
            completion_id="completion-1",
            session_id="session-1",
            run_id="run-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            terminal_state="interrupted",
            exit_kind="cancelled",
            adapter_outcome_kind="",
            adapter_error_kind=None,
            runner_result_evidence_digest=None,
            primary_cancellation_request_id=None,
            cleanup_disposition="complete",
            started_at=120,
            cancel_requested_at=None,
            completed_at=130,
            bounds_summary="bounded",
            truncation_metadata="none",
            redaction_policy_id="redaction.default",
            diagnostic_digest="sha256:" + "a" * 64,
            application_input_id="cli:run.session-completion:completion-1",
        )


def test_runner_session_transition_text_is_bounded() -> None:
    state = _claimed_state()
    run_ref = state.runs["run-taskmaster"].run_ref

    assert CreateRunnerSession(
        "create-at-limit",
        run_ref=run_ref,
        session_id="x" * 4096,
        session_fencing_token="session-fence-1",
        created_at=100,
        explicit_retry_intent=False,
    ).session_id == "x" * 4096
    with pytest.raises(ValueError, match="4096"):
        CreateRunnerSession(
            "create-over-limit",
            run_ref=run_ref,
            session_id="x" * 4097,
            session_fencing_token="session-fence-1",
            created_at=100,
            explicit_retry_intent=False,
        )


def test_run_current_session_id_is_nonblank_and_bounded() -> None:
    run = _claimed_state().runs["run-taskmaster"]

    with pytest.raises(ValueError, match="non-blank"):
        replace(run, current_session_id="")
    with pytest.raises(ValueError, match="4096"):
        replace(run, current_session_id="x" * 4097)


@pytest.mark.parametrize(
    ("reason", "source_kind"),
    (
        ("operator_cancel_work", "daemon"),
        ("operator_cancel_work", "runtime"),
        ("daemon_shutdown", "operator"),
        ("daemon_shutdown", "runtime"),
        ("runner_timeout", "operator"),
        ("runner_timeout", "daemon"),
        ("runtime_failure", "operator"),
        ("runtime_failure", "daemon"),
    ),
)
def test_cancellation_reason_requires_exact_source(
    reason: str,
    source_kind: str,
) -> None:
    with pytest.raises(ValueError, match="reason and source"):
        RunnerSessionCancellationRecord(
            request_id="cancel-1",
            session_id="session-1",
            dispatch_generation=1,
            reason=reason,
            source_kind=source_kind,
            actor_id="actor-1",
            requested_at=130,
            request_order=1,
            primary=True,
        )


def test_cancellation_attempt_refuses_secondary_request() -> None:
    state = _claimed_state()
    run = state.runs["run-taskmaster"]
    session = RunnerSessionRecord(
        session_id="session-1",
        run_id=run.run_ref.run_id,
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        state="cancellation_requested",
        created_at=100,
        start_intent_at=110,
        started_at=120,
        ended_at=None,
        durable_locator_digest="sha256:" + "a" * 64,
        cleanup_disposition="pending",
    )
    primary = RunnerSessionCancellationRecord(
        request_id="cancel-1",
        session_id=session.session_id,
        dispatch_generation=1,
        reason="operator_cancel_work",
        source_kind="operator",
        actor_id="operator-1",
        requested_at=130,
        request_order=1,
        primary=True,
    )
    secondary = RunnerSessionCancellationRecord(
        request_id="cancel-2",
        session_id=session.session_id,
        dispatch_generation=1,
        reason="daemon_shutdown",
        source_kind="daemon",
        actor_id="daemon-1",
        requested_at=131,
        request_order=2,
        primary=False,
    )
    state = replace(
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
        runner_session_cancellation_requests={
            primary.request_id: primary,
            secondary.request_id: secondary,
        },
    )

    decision = decide(
        state,
        RecordRunnerSessionCancellationAttempt(
            "attempt-secondary",
            run_ref=run.run_ref,
            session_id=session.session_id,
            dispatch_generation=1,
            session_fencing_token=session.session_fencing_token,
            expected_state="cancellation_requested",
            attempt_id="attempt-1",
            request_id=secondary.request_id,
            sequence=1,
            operation="cooperative_cancel",
            result="succeeded",
            started_at=135,
            completed_at=140,
            bounded_diagnostic_digest="sha256:" + "b" * 64,
        ),
        deterministic_context(transition_id="transition-attempt-secondary"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "runner_session_reconciliation_contradiction"


def test_cancellation_decision_refuses_reason_source_mismatch() -> None:
    state = _claimed_state()
    run = state.runs["run-taskmaster"]
    session = RunnerSessionRecord(
        session_id="session-1",
        run_id=run.run_ref.run_id,
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        state="running",
        created_at=100,
        start_intent_at=110,
        started_at=120,
        ended_at=None,
        durable_locator_digest="sha256:" + "a" * 64,
        cleanup_disposition="pending",
    )
    state = replace(
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
    )

    decision = decide(
        state,
        RequestRunnerSessionCancellation(
            "cancel-mismatch",
            run_ref=run.run_ref,
            session_id=session.session_id,
            dispatch_generation=1,
            session_fencing_token=session.session_fencing_token,
            expected_state="running",
            request_id="cancel-1",
            reason="daemon_shutdown",
            source_kind="operator",
            actor_id="operator-1",
            requested_at=130,
            request_order=1,
            primary=True,
        ),
        deterministic_context(transition_id="transition-cancel-mismatch"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "runner_session_reconciliation_contradiction"


@pytest.mark.parametrize(
    ("input_kind", "field_name"),
    (
        ("request", "reason"),
        ("request", "source_kind"),
        ("attempt", "operation"),
        ("attempt", "result"),
    ),
)
def test_runner_session_transition_refuses_unknown_enum(
    input_kind: str,
    field_name: str,
) -> None:
    state = _claimed_state()
    run_ref = state.runs["run-taskmaster"].run_ref
    if input_kind == "request":
        kwargs = {
            "reason": "operator_cancel_work",
            "source_kind": "operator",
        }
        kwargs[field_name] = "unknown"
        with pytest.raises(ValueError, match="unsupported"):
            RequestRunnerSessionCancellation(
                "cancel-unknown",
                run_ref=run_ref,
                session_id="session-1",
                dispatch_generation=1,
                session_fencing_token="session-fence-1",
                expected_state="running",
                request_id="cancel-1",
                actor_id="operator-1",
                requested_at=130,
                request_order=1,
                primary=True,
                **kwargs,
            )
    else:
        kwargs = {
            "operation": "cooperative_cancel",
            "result": "succeeded",
        }
        kwargs[field_name] = "unknown"
        with pytest.raises(ValueError, match="unsupported"):
            RecordRunnerSessionCancellationAttempt(
                "attempt-unknown",
                run_ref=run_ref,
                session_id="session-1",
                dispatch_generation=1,
                session_fencing_token="session-fence-1",
                expected_state="cancellation_requested",
                attempt_id="attempt-1",
                request_id="cancel-1",
                sequence=1,
                started_at=135,
                completed_at=140,
                bounded_diagnostic_digest="sha256:" + "a" * 64,
                **kwargs,
            )


@pytest.mark.parametrize(
    ("state", "start_intent_at", "started_at"),
    (
        ("created", 110, None),
        ("starting", None, None),
        ("running", 110, None),
        ("cancellation_requested", None, 120),
    ),
)
def test_runner_session_refuses_state_timestamp_contradictions(
    state: str,
    start_intent_at: int | None,
    started_at: int | None,
) -> None:
    with pytest.raises(ValueError, match="runner session"):
        RunnerSessionRecord(
            session_id="session-1",
            run_id="run-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            state=state,
            created_at=100,
            start_intent_at=start_intent_at,
            started_at=started_at,
            ended_at=None,
            durable_locator_digest="sha256:" + "a" * 64,
            cleanup_disposition="pending",
        )


@pytest.mark.parametrize(
    ("command_kind", "field_name", "invalid"),
    (
        ("create", "created_at", "100"),
        ("create", "created_at", 2**63),
        ("create", "explicit_retry_intent", 1),
        ("create", "explicit_retry_intent", "yes"),
        ("advance", "dispatch_generation", 0),
        ("advance", "dispatch_generation", 2**63),
        ("advance", "occurred_at", "110"),
        ("advance", "occurred_at", 2**63),
        ("request", "dispatch_generation", 0),
        ("request", "dispatch_generation", 2**63),
        ("request", "requested_at", "130"),
        ("request", "requested_at", 2**63),
        ("request", "request_order", 0),
        ("request", "request_order", 2**63),
        ("request", "primary", 1),
        ("request", "primary", "yes"),
        ("attempt", "dispatch_generation", 0),
        ("attempt", "dispatch_generation", 2**63),
        ("attempt", "sequence", 0),
        ("attempt", "sequence", 2**63),
        ("attempt", "started_at", "135"),
        ("attempt", "started_at", 2**63),
        ("attempt", "completed_at", "140"),
        ("attempt", "completed_at", 2**63),
    ),
)
def test_runner_session_commands_reject_invalid_scalar_values(
    command_kind: str,
    field_name: str,
    invalid: object,
) -> None:
    run_ref = _claimed_state().runs["run-taskmaster"].run_ref
    commands = {
        "create": CreateRunnerSession(
            "create-session",
            run_ref=run_ref,
            session_id="session-1",
            session_fencing_token="session-fence-1",
            created_at=100,
            explicit_retry_intent=False,
        ),
        "advance": AdvanceRunnerSession(
            "advance-session",
            run_ref=run_ref,
            session_id="session-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            expected_state="created",
            next_state="starting",
            occurred_at=110,
        ),
        "request": RequestRunnerSessionCancellation(
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
        "attempt": RecordRunnerSessionCancellationAttempt(
            "attempt-cancel-session",
            run_ref=run_ref,
            session_id="session-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            expected_state="cancellation_requested",
            attempt_id="attempt-1",
            request_id="cancel-1",
            sequence=1,
            operation="cooperative_cancel",
            result="succeeded",
            started_at=135,
            completed_at=140,
            bounded_diagnostic_digest="sha256:" + "a" * 64,
        ),
    }

    with pytest.raises(ValueError):
        replace(commands[command_kind], **{field_name: invalid})


def test_runner_session_cancellation_attempt_and_completion_are_durable() -> None:
    state = _claimed_state()
    run_ref = state.runs["run-taskmaster"].run_ref
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
            "session-running",
            run_ref=run_ref,
            session_id="session-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            expected_state="starting",
            next_state="running",
            occurred_at=120,
            durable_locator_digest="sha256:" + "a" * 64,
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
        RecordRunnerSessionCancellationAttempt(
            "attempt-cancel-session",
            run_ref=run_ref,
            session_id="session-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            expected_state="cancellation_requested",
            attempt_id="attempt-1",
            request_id="cancel-1",
            sequence=1,
            operation="cooperative_cancel",
            result="succeeded",
            started_at=135,
            completed_at=140,
            bounded_diagnostic_digest="sha256:" + "b" * 64,
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
    decision = decide(
        state,
        RecordRunnerSessionCompletion(
            "record-completion",
            run_ref=run_ref,
            expected_state="cancellation_requested",
            completion=completion,
        ),
        deterministic_context(transition_id="transition-record-completion"),
    )
    assert decision.accepted
    state = apply(state, decision)

    assert state.runner_sessions["session-1"].state == "interrupted"
    assert (
        state.runner_sessions["session-1"].durable_locator_digest
        == "sha256:" + "a" * 64
    )
    assert state.runner_session_cancellation_requests["cancel-1"].primary
    assert state.runner_session_cancellation_attempts["attempt-1"].sequence == 1
    assert state.runner_session_completions["session-1"] == completion

    duplicate = decide(
        state,
        RecordRunnerSessionCompletion(
            "record-completion-duplicate",
            run_ref=run_ref,
            expected_state="cancellation_requested",
            completion=completion,
        ),
        deterministic_context(transition_id="transition-completion-duplicate"),
    )
    assert duplicate.accepted is False
    assert duplicate.refusal is not None
    assert duplicate.refusal.reason == "duplicate_runner_session_completion"

    terminal_attempt = decide(
        state,
        RecordRunnerSessionCancellationAttempt(
            "attempt-after-completion",
            run_ref=run_ref,
            session_id="session-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            expected_state="interrupted",
            attempt_id="attempt-2",
            request_id="cancel-1",
            sequence=2,
            operation="transport_cleanup",
            result="succeeded",
            started_at=151,
            completed_at=152,
            bounded_diagnostic_digest="sha256:" + "d" * 64,
        ),
        deterministic_context(transition_id="transition-terminal-attempt"),
    )
    assert terminal_attempt.accepted is False
    assert terminal_attempt.refusal is not None
    assert terminal_attempt.refusal.reason == "invalid_runner_session_transition"
