from __future__ import annotations

from dataclasses import replace

import pytest

from kernel.kernel_ping_scenarios import bootstrap_to_taskmaster_claim
from millrace.compiler import compile_workflow
from millrace.compiler.canonical import authority_fingerprint as fingerprint
from millrace.contracts.state import (
    RunnerSessionCancellationAttemptRecord,
    RunnerSessionCancellationRecord,
    RunnerSessionCompletionRecord,
    RunnerSessionRecord,
)
from millrace.contracts.transition import (
    AdvanceRunnerSession,
    CreateRunnerSession,
    RecordRunnerSessionCompletion,
    RefuseRunnerSessionSignal,
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
    return bootstrap_to_taskmaster_claim(result.plan, fingerprint(result.plan))


def _state_with_session(
    session_state: str, *, locator_digest: str | None,
    with_cancellation: bool = False,
):
    state = _claimed_state()
    run = state.runs["run-taskmaster"]
    session = RunnerSessionRecord(
        "session-1", run.run_ref.run_id, 1, "session-fence-1", session_state, 100,
        None if session_state == "created" else 110,
        (
            120
            if session_state
            in {"running", "cancellation_requested", "terminating", "completed"}
            else None
        ),
        130 if session_state == "completed" else None,
        locator_digest,
        "complete" if session_state == "completed" else "pending",
    )
    requests = {}
    if with_cancellation:
        request = RunnerSessionCancellationRecord(
            "cancel-1",
            session.session_id,
            1,
            "operator_cancel_work",
            "operator",
            "operator-1",
            130,
            1,
            True,
        )
        requests = {request.request_id: request}
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
        runner_session_cancellation_requests=requests,
    )


@pytest.mark.parametrize(
    "session_state",
    ("running", "cancellation_requested", "terminating"),
)
def test_active_session_locator_refresh_changes_only_locator_digest(
    session_state: str,
) -> None:
    old_digest = "sha256:" + "a" * 64
    new_digest = "sha256:" + "b" * 64
    state = _state_with_session(
        session_state,
        locator_digest=old_digest,
    )
    run_ref = state.runs["run-taskmaster"].run_ref
    session = state.runner_sessions["session-1"]
    transition_input = AdvanceRunnerSession(
        "refresh-session-locator",
        run_ref=run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        expected_state=session_state,
        next_state=session_state,
        occurred_at=120,
        durable_locator_digest=new_digest,
    )

    decision = decide(
        state,
        transition_input,
        deterministic_context(transition_id="transition-refresh-locator"),
    )

    assert decision.accepted
    applied = apply(state, decision)
    assert applied.runner_sessions[session.session_id] == replace(
        session,
        durable_locator_digest=new_digest,
    )
    assert applied.runs == state.runs


@pytest.mark.parametrize(
    ("override", "expected_reason"),
    (
        ({"durable_locator_digest": None}, "invalid_runner_session_transition"),
        ({"session_id": "stale-session"}, "stale_runner_session"),
        (
            {"session_fencing_token": "stale-session-fence"},
            "stale_runner_session",
        ),
    ),
)
def test_active_session_locator_refresh_refuses_incomplete_or_stale_authority(
    override: dict[str, object],
    expected_reason: str,
) -> None:
    state = _state_with_session(
        "running",
        locator_digest="sha256:" + "a" * 64,
    )
    run_ref = state.runs["run-taskmaster"].run_ref
    session = state.runner_sessions["session-1"]
    transition_input = AdvanceRunnerSession(
        "refuse-session-locator-refresh",
        run_ref=run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        expected_state="running",
        next_state="running",
        occurred_at=120,
        durable_locator_digest="sha256:" + "b" * 64,
    )

    decision = decide(
        state,
        replace(transition_input, **override),
        deterministic_context(transition_id="transition-refuse-locator-refresh"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == expected_reason


@pytest.mark.parametrize(
    ("session_state", "locator_digest"),
    (
        ("completed", "sha256:" + "a" * 64),
        ("starting", "sha256:" + "a" * 64),
        ("running", None),
    ),
)
def test_session_locator_refresh_refuses_non_refresh_states(
    session_state: str,
    locator_digest: str,
) -> None:
    state = _state_with_session(
        session_state,
        locator_digest=locator_digest,
    )
    run_ref = state.runs["run-taskmaster"].run_ref
    session = state.runner_sessions["session-1"]

    decision = decide(
        state,
        AdvanceRunnerSession(
            "refuse-session-locator-overwrite",
            run_ref=run_ref,
            session_id=session.session_id,
            dispatch_generation=session.dispatch_generation,
            session_fencing_token=session.session_fencing_token,
            expected_state=session_state,
            next_state=session_state,
            occurred_at=(
                session.started_at
                if session.started_at is not None
                else session.start_intent_at
            ),
            durable_locator_digest="sha256:" + "b" * 64,
        ),
        deterministic_context(transition_id="transition-refuse-locator-overwrite"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_runner_session_transition"


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
        "run_id", "work_item_id", "claim_id", "plan_id",
        "authority_fingerprint", "plan_format_version", "generation", "fencing_token",
    ),
)
def test_session_creation_refuses_every_runref_authority_drift(field_name: str) -> None:
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


def test_runner_session_signal_refusal_is_truthful_replay_safe_and_distinct() -> None:
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

    def signal(input_id: str, digest_char: str) -> RefuseRunnerSessionSignal:
        return RefuseRunnerSessionSignal(
            input_id,
            run_ref=run_ref,
            session_id="session-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            expected_state="created",
            signal_kind="runner_completion_outcome",
            reason="runner_session_reconciliation_contradiction",
            signal_digest="sha256:" + digest_char * 64,
        )

    first = signal("signal-session-1-completion-a", "a")
    first_decision = decide(
        state,
        first,
        deterministic_context(transition_id="transition-signal-a"),
    )
    assert first_decision.accepted is False
    assert first_decision.refusal is not None
    assert (
        first_decision.refusal.reason == "runner_session_reconciliation_contradiction"
    )
    assert first_decision.input_kind == "workflow.refuse_runner_session_signal"
    after_first = apply(state, first_decision)
    assert after_first.runner_sessions == state.runner_sessions

    replay = decide(
        after_first,
        first,
        deterministic_context(transition_id="transition-signal-a-replay"),
    )
    assert replay.receipt_ref == first_decision.receipt_ref
    assert replay.mutations == ()
    assert apply(after_first, replay) == after_first

    second = signal("signal-session-1-completion-b", "b")
    second_decision = decide(
        after_first,
        second,
        deterministic_context(transition_id="transition-signal-b"),
    )
    assert second_decision.disposition == "refused"
    after_second = apply(after_first, second_decision)
    assert len(after_second.refusals) == len(after_first.refusals) + 1


def test_runner_session_signal_refusal_validates_current_authority() -> None:
    state = _claimed_state()
    run_ref = state.runs["run-taskmaster"].run_ref
    decision = decide(
        state,
        RefuseRunnerSessionSignal(
            "stale-signal",
            run_ref=run_ref,
            session_id="missing-session",
            dispatch_generation=1,
            session_fencing_token="missing-fence",
            expected_state="running",
            signal_kind="runner_dispatch_echo",
            reason="runner_session_authority_mismatch",
            signal_digest="sha256:" + "a" * 64,
        ),
        deterministic_context(transition_id="transition-stale-signal"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "stale_runner_session"


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


_LEGAL_EDGES = (
    ("created", "starting"), ("created", "cancellation_requested"),
    ("created", "failed"),
    ("starting", "running"), ("starting", "completed"), ("starting", "failed"),
    ("starting", "cancellation_requested"), ("starting", "lost"),
    ("running", "completed"), ("running", "failed"),
    ("running", "cancellation_requested"), ("running", "lost"),
    ("cancellation_requested", "terminating"),
    ("cancellation_requested", "completed"),
    ("cancellation_requested", "interrupted"),
    ("cancellation_requested", "failed"), ("cancellation_requested", "lost"),
    ("terminating", "completed"), ("terminating", "interrupted"),
    ("terminating", "failed"), ("terminating", "lost"),
)


@pytest.mark.parametrize(("prior", "next_state"), _LEGAL_EDGES)
def test_runner_session_legal_state_transitions(prior: str, next_state: str) -> None:
    assert is_legal_runner_session_transition(prior, next_state)


@pytest.mark.parametrize(("prior", "next_state"), _LEGAL_EDGES)
def test_every_legal_runner_session_edge_decides_and_applies(
    prior: str, next_state: str,
) -> None:
    state = _state_with_session(
        prior,
        locator_digest=None,
        with_cancellation=prior in {"cancellation_requested", "terminating"},
    )
    run = state.runs["run-taskmaster"]
    session = state.runner_sessions["session-1"]
    started_at = session.started_at
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
            adapter_outcome_kind=("success" if next_state == "completed" else None),
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
                130 if prior in {"cancellation_requested", "terminating"} else None
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
        deterministic_context(transition_id=f"transition-{prior}-{next_state}"),
    )

    assert decision.accepted
    next_runtime_state = apply(state, decision)
    assert next_runtime_state.runner_sessions[session.session_id].state == next_state
    assert (session.session_id in next_runtime_state.runner_session_completions) == (
        next_state in terminal_states
    )


@pytest.mark.parametrize(
    ("prior", "next_state"),
    (
        ("created", "running"), ("running", "starting"),
        ("completed", "running"), ("interrupted", "failed"),
        ("failed", "starting"), ("lost", "created"),
    ),
)
def test_runner_session_illegal_state_transitions(prior: str, next_state: str) -> None:
    assert not is_legal_runner_session_transition(prior, next_state)


@pytest.mark.parametrize(
    ("state", "cleanup", "valid"),
    (
        ("completed", "complete", True), ("failed", "not_required", True),
        ("interrupted", "complete", True), ("lost", "orphan_risk", True),
        ("completed", "pending", False), ("failed", "orphan_risk", False),
        ("interrupted", "pending", False), ("lost", "complete", False),
    ),
)
def test_runner_session_terminal_cleanup_pairing(
    state: str, cleanup: str, valid: bool,
) -> None:
    args = (
        "session-1", "run-1", 1, "session-fence-1", state,
        100, 110, 120, 130, "sha256:" + "a" * 64, cleanup,
    )
    if valid:
        assert RunnerSessionRecord(*args).cleanup_disposition == cleanup
    else:
        with pytest.raises(ValueError):
            RunnerSessionRecord(*args)


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
        cancel_requested_at=(130 if prior == "cancellation_requested" else None),
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
        assert decision.refusal.reason == "runner_session_reconciliation_contradiction"


def _state_with_active_cancellation_history():
    return _state_with_session(
        "cancellation_requested",
        locator_digest="sha256:" + "a" * 64,
        with_cancellation=True,
    )


@pytest.mark.parametrize(
    ("terminal_state", "links_primary", "accepted"),
    (
        ("interrupted", False, False),
        ("interrupted", True, True),
        ("completed", False, True),
        ("failed", False, True),
        ("lost", False, True),
    ),
)
def test_completion_cancellation_history_linkage_preserves_terminal_races(
    terminal_state: str,
    links_primary: bool,
    accepted: bool,
) -> None:
    state = _state_with_active_cancellation_history()
    run_ref = state.runs["run-taskmaster"].run_ref
    completion = RunnerSessionCompletionRecord(
        completion_id=f"completion-{terminal_state}",
        session_id="session-1",
        run_id=run_ref.run_id,
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        terminal_state=terminal_state,
        exit_kind="success" if terminal_state == "completed" else "error",
        adapter_outcome_kind=("success" if terminal_state == "completed" else None),
        adapter_error_kind=(
            None if terminal_state == "completed" else "invocation_failed"
        ),
        runner_result_evidence_digest=(
            "sha256:" + "b" * 64 if terminal_state == "completed" else None
        ),
        primary_cancellation_request_id="cancel-1" if links_primary else None,
        cleanup_disposition=("orphan_risk" if terminal_state == "lost" else "complete"),
        started_at=120,
        cancel_requested_at=130 if links_primary else None,
        completed_at=140,
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id="redaction.default",
        diagnostic_digest="sha256:" + "c" * 64,
        application_input_id=(
            f"cli:run.session-completion:completion-{terminal_state}"
        ),
    )

    decision = decide(
        state,
        RecordRunnerSessionCompletion(
            f"complete-{terminal_state}",
            run_ref=run_ref,
            expected_state="cancellation_requested",
            completion=completion,
        ),
        deterministic_context(transition_id=f"transition-complete-{terminal_state}"),
    )

    assert decision.accepted is accepted
    if accepted:
        applied = apply(state, decision)
        assert applied.runner_sessions["session-1"].state == terminal_state
    else:
        assert decision.refusal is not None
        assert decision.refusal.reason == "runner_session_reconciliation_contradiction"
