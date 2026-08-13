from __future__ import annotations

from dataclasses import replace

import pytest

from kernel.kernel_ping_scenarios import bootstrap_to_taskmaster_claim
from millrace.compiler import authority_fingerprint, compile_workflow
from millrace.contracts.context_checkout import (
    ContextCheckoutFile,
    ContextCheckoutManifest,
    context_checkout_manifest_digest,
)
from millrace.contracts.state import RunnerSessionRecord
from millrace.contracts.transition import (
    AdvanceRunnerSession,
    AdvanceRunnerSessionRecord,
    AttachRunnerSessionContext,
    AttachRunnerSessionContextRecord,
    CreateRunnerSession,
    CreateRunnerSessionRecord,
)
from millrace.kernel import apply, decide
from millrace.kernel.errors import StateConcurrencyError
from millrace.testing import deterministic_context
from millrace.workflows import kernel_ping
from tests.compiler.test_context_bindings import _source_with_context_binding


def _plan_and_fingerprint(*, bound: bool) -> tuple[object, str]:
    source = _source_with_context_binding() if bound else kernel_ping.workflow_source()
    result = compile_workflow(source)
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def _created_session_state(*, bound: bool):
    plan, plan_fingerprint = _plan_and_fingerprint(bound=bound)
    state = bootstrap_to_taskmaster_claim(plan, plan_fingerprint)
    run = state.runs["run-taskmaster"]
    state = apply(
        state,
        decide(
            state,
            CreateRunnerSession(
                "create-session",
                run_ref=run.run_ref,
                session_id="session-1",
                session_fencing_token="session-fence-1",
                created_at=100,
                explicit_retry_intent=False,
            ),
            deterministic_context(transition_id="transition-create-session"),
        ),
    )
    return state, plan, plan_fingerprint


def _claimed_state(*, bound: bool):
    plan, plan_fingerprint = _plan_and_fingerprint(bound=bound)
    return (
        bootstrap_to_taskmaster_claim(plan, plan_fingerprint),
        plan,
        plan_fingerprint,
    )


def _manifest_digest(*, session_id: str = "session-1", generation: int = 1) -> str:
    return context_checkout_manifest_digest(
        ContextCheckoutManifest(
            session_id=session_id,
            dispatch_generation=generation,
            plan_fingerprint="sha256:" + "a" * 64,
            binding_id="kernel_ping.taskmaster_context",
            router_asset_id="kernel_ping.context_router",
            files=(
                ContextCheckoutFile(
                    checkout_path="router.txt",
                    source_kind="selected_router",
                    source_ref="kernel_ping.context_router",
                    content_digest="sha256:" + "b" * 64,
                    byte_length=6,
                    required=True,
                ),
            ),
            omissions=(),
        )
    )


def _attach_input(
    state,
    *,
    input_id: str = "attach-session-context",
    digest: str | None = None,
    **overrides: object,
) -> AttachRunnerSessionContext:
    run = state.runs["run-taskmaster"]
    session = state.runner_sessions[run.current_session_id or ""]
    values: dict[str, object] = {
        "run_ref": run.run_ref,
        "session_id": session.session_id,
        "dispatch_generation": session.dispatch_generation,
        "session_fencing_token": session.session_fencing_token,
        "context_manifest_digest": digest or _manifest_digest(),
        "selected_binding_id": "kernel_ping.taskmaster_context",
    }
    values.update(overrides)
    return AttachRunnerSessionContext(input_id, **values)


def test_runner_session_record_appends_optional_context_authority() -> None:
    session = RunnerSessionRecord(
        "session-1",
        "run-1",
        1,
        "session-fence-1",
        "created",
        100,
        None,
        None,
        None,
        None,
        "pending",
    )

    assert RunnerSessionRecord.schema_version == 2
    assert session.context_manifest_digest is None
    with pytest.raises(ValueError, match="sha256 digest"):
        RunnerSessionRecord(
            "session-1",
            "run-1",
            1,
            "session-fence-1",
            "created",
            100,
            None,
            None,
            None,
            None,
            "pending",
            "not-a-digest",
        )


def test_apply_rejects_created_runner_session_with_context_authority() -> None:
    state, _plan, _fingerprint = _claimed_state(bound=False)
    run = state.runs["run-taskmaster"]
    decision = decide(
        state,
        CreateRunnerSession(
            "create-session-bound-at-apply",
            run_ref=run.run_ref,
            session_id="session-1",
            session_fencing_token="session-fence-1",
            created_at=100,
            explicit_retry_intent=False,
        ),
        deterministic_context(transition_id="transition-create-bound-at-apply"),
    )
    assert decision.accepted
    create_mutation = next(
        mutation
        for mutation in decision.mutations
        if isinstance(mutation, CreateRunnerSessionRecord)
    )
    forged_mutation = replace(
        create_mutation,
        session=replace(
            create_mutation.session,
            context_manifest_digest=_manifest_digest(),
        ),
    )
    forged_decision = replace(
        decision,
        mutations=tuple(
            forged_mutation if mutation is create_mutation else mutation
            for mutation in decision.mutations
        ),
    )

    with pytest.raises(
        StateConcurrencyError,
        match="^runner session context authority changed$",
    ):
        apply(state, forged_decision)


def test_apply_rejects_forged_attach_session_state() -> None:
    state, _plan, _fingerprint = _created_session_state(bound=True)
    before_state = state
    attach_decision = decide(
        state,
        _attach_input(state, input_id="attach-forged-state"),
        deterministic_context(transition_id="transition-attach-forged-state"),
    )
    assert attach_decision.accepted
    attach_mutation = next(
        mutation
        for mutation in attach_decision.mutations
        if isinstance(mutation, AttachRunnerSessionContextRecord)
    )
    forged_mutation = replace(
        attach_mutation,
        session=replace(
            attach_mutation.session,
            state="starting",
            start_intent_at=110,
        ),
    )
    forged_decision = replace(
        attach_decision,
        mutations=tuple(
            forged_mutation if mutation is attach_mutation else mutation
            for mutation in attach_decision.mutations
        ),
    )

    with pytest.raises(
        StateConcurrencyError,
        match="^runner session context authority changed$",
    ):
        apply(state, forged_decision)
    assert state == before_state


def test_apply_rejects_forged_attach_missing_context_digest() -> None:
    state, _plan, _fingerprint = _created_session_state(bound=True)
    before_state = state
    attach_decision = decide(
        state,
        _attach_input(state, input_id="attach-forged-missing-digest"),
        deterministic_context(
            transition_id="transition-attach-forged-missing-digest"
        ),
    )
    assert attach_decision.accepted
    attach_mutation = next(
        mutation
        for mutation in attach_decision.mutations
        if isinstance(mutation, AttachRunnerSessionContextRecord)
    )
    forged_mutation = replace(
        attach_mutation,
        session=replace(attach_mutation.session, context_manifest_digest=None),
    )
    forged_decision = replace(
        attach_decision,
        mutations=tuple(
            forged_mutation if mutation is attach_mutation else mutation
            for mutation in attach_decision.mutations
        ),
    )

    with pytest.raises(
        StateConcurrencyError,
        match="^runner session context authority changed$",
    ):
        apply(state, forged_decision)
    assert state == before_state


def test_apply_rejects_forged_attach_digest_before_first_pin() -> None:
    state, _plan, _fingerprint = _created_session_state(bound=True)
    before_state = state
    attach = _attach_input(state, input_id="attach-forged-digest")
    decision = decide(
        state,
        attach,
        deterministic_context(transition_id="transition-attach-forged-digest"),
    )
    assert decision.accepted
    attach_mutation = next(
        mutation
        for mutation in decision.mutations
        if isinstance(mutation, AttachRunnerSessionContextRecord)
    )
    assert attach_mutation.session.context_manifest_digest == (
        attach.context_manifest_digest
    )
    forged_mutation = replace(
        attach_mutation,
        session=replace(
            attach_mutation.session,
            context_manifest_digest="sha256:" + "c" * 64,
        ),
    )
    forged_decision = replace(
        decision,
        mutations=tuple(
            forged_mutation if mutation is attach_mutation else mutation
            for mutation in decision.mutations
        ),
    )

    with pytest.raises(
        StateConcurrencyError,
        match="^runner session context authority changed$",
    ):
        apply(state, forged_decision)
    assert state == before_state


def test_apply_rejects_forged_attach_unrelated_session_fact() -> None:
    state, _plan, _fingerprint = _created_session_state(bound=True)
    before_state = state
    attach_decision = decide(
        state,
        _attach_input(state, input_id="attach-forged-session-fact"),
        deterministic_context(
            transition_id="transition-attach-forged-session-fact"
        ),
    )
    assert attach_decision.accepted
    attach_mutation = next(
        mutation
        for mutation in attach_decision.mutations
        if isinstance(mutation, AttachRunnerSessionContextRecord)
    )
    forged_mutation = replace(
        attach_mutation,
        session=replace(attach_mutation.session, created_at=101),
    )
    forged_decision = replace(
        attach_decision,
        mutations=tuple(
            forged_mutation if mutation is attach_mutation else mutation
            for mutation in attach_decision.mutations
        ),
    )

    with pytest.raises(
        StateConcurrencyError,
        match="^runner session context authority changed$",
    ):
        apply(state, forged_decision)
    assert state == before_state


def test_apply_rejects_stale_attach_with_different_digest_after_first_pin() -> None:
    state, _plan, _fingerprint = _created_session_state(bound=True)
    first_input = _attach_input(state, input_id="attach-race-first")
    second_input = _attach_input(
        state,
        input_id="attach-race-second",
        digest="sha256:" + "d" * 64,
    )
    first_decision = decide(
        state,
        first_input,
        deterministic_context(transition_id="transition-attach-race-first"),
    )
    second_decision = decide(
        state,
        second_input,
        deterministic_context(transition_id="transition-attach-race-second"),
    )

    assert first_decision.accepted
    assert second_decision.accepted
    assert first_input.context_manifest_digest != second_input.context_manifest_digest

    pinned_state = apply(state, first_decision)
    before_stale_apply = pinned_state

    with pytest.raises(
        StateConcurrencyError,
        match="^runner session context authority changed$",
    ):
        apply(pinned_state, second_decision)

    assert pinned_state == before_stale_apply
    assert pinned_state.runner_sessions["session-1"].context_manifest_digest == (
        first_input.context_manifest_digest
    )


@pytest.mark.parametrize(
    "forged_digest",
    ("sha256:" + "c" * 64, None),
)
def test_apply_rejects_advanced_runner_session_context_authority_drift(
    forged_digest: str | None,
) -> None:
    state, _plan, _fingerprint = _created_session_state(bound=True)
    attach = _attach_input(state, input_id="attach-before-advance-apply")
    state = apply(
        state,
        decide(
            state,
            attach,
            deterministic_context(transition_id="transition-attach-before-advance"),
        ),
    )
    run = state.runs["run-taskmaster"]
    session = state.runner_sessions["session-1"]
    decision = decide(
        state,
        AdvanceRunnerSession(
            "advance-session-context-at-apply",
            run_ref=run.run_ref,
            session_id=session.session_id,
            dispatch_generation=session.dispatch_generation,
            session_fencing_token=session.session_fencing_token,
            expected_state="created",
            next_state="starting",
            occurred_at=110,
        ),
        deterministic_context(transition_id="transition-advance-context-at-apply"),
    )
    assert decision.accepted
    advance_mutation = next(
        mutation
        for mutation in decision.mutations
        if isinstance(mutation, AdvanceRunnerSessionRecord)
    )
    forged_mutation = replace(
        advance_mutation,
        session=replace(
            advance_mutation.session,
            context_manifest_digest=forged_digest,
        ),
    )
    forged_decision = replace(
        decision,
        mutations=tuple(
            forged_mutation if mutation is advance_mutation else mutation
            for mutation in decision.mutations
        ),
    )

    with pytest.raises(
        StateConcurrencyError,
        match="^runner session context authority changed$",
    ):
        apply(state, forged_decision)


def test_unbound_session_can_start_without_context() -> None:
    state, _plan, _fingerprint = _created_session_state(bound=False)
    run = state.runs["run-taskmaster"]
    session = state.runner_sessions["session-1"]

    decision = decide(
        state,
        AdvanceRunnerSession(
            "start-unbound-session",
            run_ref=run.run_ref,
            session_id=session.session_id,
            dispatch_generation=session.dispatch_generation,
            session_fencing_token=session.session_fencing_token,
            expected_state="created",
            next_state="starting",
            occurred_at=110,
        ),
        deterministic_context(transition_id="transition-start-unbound"),
    )

    assert decision.accepted
    started = apply(state, decision).runner_sessions[session.session_id]
    assert started.state == "starting"
    assert started.context_manifest_digest is None


def test_bound_session_requires_attach_before_start_and_preserves_digest() -> None:
    state, _plan, _fingerprint = _created_session_state(bound=True)
    run = state.runs["run-taskmaster"]
    session = state.runner_sessions["session-1"]

    refused_start = decide(
        state,
        AdvanceRunnerSession(
            "start-bound-without-attach",
            run_ref=run.run_ref,
            session_id=session.session_id,
            dispatch_generation=session.dispatch_generation,
            session_fencing_token=session.session_fencing_token,
            expected_state="created",
            next_state="starting",
            occurred_at=110,
        ),
        deterministic_context(transition_id="transition-start-bound-refused"),
    )
    assert refused_start.accepted is False
    assert refused_start.refusal is not None
    assert refused_start.refusal.reason == "runner_session_reconciliation_contradiction"

    attach = _attach_input(state, input_id="attach-bound-session")
    attach_decision = decide(
        state,
        attach,
        deterministic_context(transition_id="transition-attach-bound"),
    )
    assert attach_decision.accepted
    state = apply(state, attach_decision)
    assert state.runner_sessions[session.session_id].context_manifest_digest == (
        attach.context_manifest_digest
    )

    start = AdvanceRunnerSession(
        "start-bound-session",
        run_ref=run.run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        expected_state="created",
        next_state="starting",
        occurred_at=110,
    )
    state = apply(
        state,
        decide(
            state,
            start,
            deterministic_context(transition_id="transition-start-bound"),
        ),
    )
    running = AdvanceRunnerSession(
        "run-bound-session",
        run_ref=run.run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        expected_state="starting",
        next_state="running",
        occurred_at=120,
    )
    state = apply(
        state,
        decide(
            state,
            running,
            deterministic_context(transition_id="transition-run-bound"),
        ),
    )
    assert state.runner_sessions[session.session_id].context_manifest_digest == (
        attach.context_manifest_digest
    )


def test_exact_attach_replay_returns_prior_receipt_without_audit_or_mutations() -> None:
    state, _plan, _fingerprint = _created_session_state(bound=True)
    attach = _attach_input(state)
    first = decide(
        state,
        attach,
        deterministic_context(transition_id="transition-attach-first"),
    )
    after_first = apply(state, first)

    replay = decide(
        after_first,
        attach,
        deterministic_context(transition_id="transition-attach-replay"),
    )

    assert replay.accepted
    assert replay.receipt_ref == first.receipt_ref
    assert replay.mutations == ()
    assert replay.governance_events == ()
    assert replay.trace_records == ()
    assert apply(after_first, replay) == after_first


@pytest.mark.parametrize(
    ("override", "expected_reason"),
    (
        ({"run_ref": "wrong"}, "runner_session_authority_mismatch"),
        ({"session_id": "wrong-session"}, "stale_runner_session"),
        ({"dispatch_generation": 2}, "stale_runner_session"),
        ({"session_fencing_token": "wrong-fence"}, "stale_runner_session"),
        (
            {"selected_binding_id": "wrong-binding"},
            "runner_session_reconciliation_contradiction",
        ),
    ),
)
def test_attach_refuses_stale_or_wrong_authority(
    override: dict[str, object],
    expected_reason: str,
) -> None:
    state, _plan, _fingerprint = _created_session_state(bound=True)
    values = dict(override)
    if values.get("run_ref") == "wrong":
        run = state.runs["run-taskmaster"]
        values["run_ref"] = replace(run.run_ref, run_id="wrong-run")

    decision = decide(
        state,
        _attach_input(state, input_id=f"attach-{expected_reason}", **values),
        deterministic_context(transition_id=f"transition-{expected_reason}"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == expected_reason


def test_attach_refuses_plan_drift_and_unbound_stage() -> None:
    bound_state, _plan, _fingerprint = _created_session_state(bound=True)
    run = bound_state.runs["run-taskmaster"]
    wrong_plan_ref = replace(
        run.run_ref.plan_ref,
        authority_fingerprint="sha256:" + "c" * 64,
    )
    wrong_plan = replace(run.run_ref, plan_ref=wrong_plan_ref)
    plan_decision = decide(
        bound_state,
        _attach_input(
            bound_state,
            input_id="attach-wrong-plan",
            run_ref=wrong_plan,
        ),
        deterministic_context(transition_id="transition-attach-wrong-plan"),
    )
    assert plan_decision.accepted is False
    assert plan_decision.refusal is not None
    assert plan_decision.refusal.reason == "runner_session_authority_mismatch"

    unbound_state, _plan, _fingerprint = _created_session_state(bound=False)
    no_binding = decide(
        unbound_state,
        _attach_input(unbound_state, input_id="attach-unbound-stage"),
        deterministic_context(transition_id="transition-attach-unbound-stage"),
    )
    assert no_binding.accepted is False
    assert no_binding.refusal is not None
    assert no_binding.refusal.reason == "runner_session_reconciliation_contradiction"


def test_different_second_digest_is_a_reconciliation_contradiction() -> None:
    state, _plan, _fingerprint = _created_session_state(bound=True)
    first_input = _attach_input(state, input_id="attach-first")
    state = apply(
        state,
        decide(
            state,
            first_input,
            deterministic_context(transition_id="transition-attach-first"),
        ),
    )
    second = _attach_input(
        state,
        input_id="attach-different-second",
        digest="sha256:" + "d" * 64,
    )

    decision = decide(
        state,
        second,
        deterministic_context(transition_id="transition-attach-different"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "runner_session_reconciliation_contradiction"


def test_attach_after_starting_is_refused() -> None:
    state, _plan, _fingerprint = _created_session_state(bound=True)
    session = state.runner_sessions["session-1"]
    state = replace(
        state,
        runner_sessions={
            session.session_id: replace(
                session,
                state="starting",
                start_intent_at=110,
            )
        },
    )

    decision = decide(
        state,
        _attach_input(state, input_id="attach-after-start"),
        deterministic_context(transition_id="transition-attach-after-start"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_runner_session_transition"


def test_attach_command_rejects_malformed_digest() -> None:
    state, _plan, _fingerprint = _created_session_state(bound=True)

    with pytest.raises(ValueError, match="sha256 digest"):
        _attach_input(state, digest="malformed")
