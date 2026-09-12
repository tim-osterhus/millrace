from __future__ import annotations

import json
import time
from dataclasses import replace

import pytest

from cli.test_cli_bounded_execution_unit import _codex_success_config
from cli.test_cli_daemon_loop import _invoke
from millrace.adapters.cli.run import (
    reconcile_pending_runner_sessions,
    run_bounded_execution_unit,
)
from millrace.adapters.cli.session_completion import _persist_transition
from millrace.contracts.transition import (
    CancelQueuedWork,
    RefuseRunnerSessionSignal,
    RequestRunnerSessionCancellation,
)
from millrace.substrate.sqlite import SQLiteRuntimeStore
from support.run_controls import (
    add_run,
    epoch_for,
    pause,
    request_for,
    runtime_with_run,
)


def invoke_control(runtime, request):
    action = request.payload["action"].split(".")[1]
    return _invoke(
        [
            "--workspace",
            str(runtime.paths.workspace_path),
            "--json",
            "runs",
            action,
            "--request-json",
            request.canonical,
        ]
    )


def _runtime_with_generated_long_run(tmp_path, *, session, enqueue_id):
    from cli.test_cli_bounded_execution_unit import (
        _ready_state_with_selected_codex_authority,
        _runtime,
    )
    from millrace.adapters.cli.context import transition_context
    from millrace.contracts.ids import QueueFamilyId
    from millrace.contracts.transition import (
        ClaimWork,
        CreateRunnerSession,
        EnqueueWork,
        input_payload_digest,
    )
    from millrace.kernel import apply, decide

    state, _ = _ready_state_with_selected_codex_authority()
    enqueue = EnqueueWork(
        enqueue_id,
        queue_family_id=QueueFamilyId("prompt"),
        payload={"prompt_id": "long-reference", "body": "Admitted work"},
    )
    context = transition_context(
        command="queue.enqueue",
        input_id_value=f"{enqueue.input_id}:{input_payload_digest(enqueue)}",
    )
    decision = decide(state, enqueue, context)
    assert decision.accepted, decision.refusal
    state = apply(state, decision)
    # Match the public bounded-run claim and transition identity construction.
    claim = ClaimWork(
        f"cli:run.bounded:claim:{context.activation_id}",
        activation_id=context.activation_id,
    )
    decision = decide(
        state,
        claim,
        transition_context(command="run.bounded", input_id_value=claim.input_id),
    )
    assert decision.accepted, decision.refusal
    state = apply(state, decision)
    run = next(iter(state.runs.values()))
    runtime = _runtime(tmp_path, state)
    if session:
        create = CreateRunnerSession(
            "long-run-session",
            run_ref=run.run_ref,
            session_id="session-long-run",
            session_fencing_token="fence-long-run",
            created_at=100,
            explicit_retry_intent=False,
        )
        state = _persist_transition(runtime, create)
        assert state is not None
        run = state.runs[run.run_ref.run_id]
    return runtime, run


@pytest.mark.parametrize("session", [False, True])
@pytest.mark.parametrize("enqueue_id", ["native-enqueue", "native-" + "é" * 1000])
def test_public_generated_long_run_hold_reopen_history_and_budget(
    tmp_path, session, enqueue_id
):
    from millrace.contracts.controls import ControlRequest
    from support.runner_sessions import _config, _RecordingAdapter, _success_start

    runtime, run = _runtime_with_generated_long_run(
        tmp_path, session=session, enqueue_id=enqueue_id
    )
    run_id = run.run_ref.run_id
    assert run_id.startswith("cli:run.bounded:cli:run.bounded:claim:cli:queue.enqueue:")
    assert run_id.endswith(":activation:run")
    assert len(run_id.encode("utf-8")) == (
        157 if enqueue_id == "native-enqueue" else 2150
    )
    epoch = epoch_for(runtime, run, max_wall_seconds=10, wall_deadline=110)
    before = runtime.store.load_runtime_state(runtime.cas_store)
    budget = runtime.store.load_daemon_budget_epoch(epoch.budget_id)
    request = request_for(
        runtime, run, caller_id="c" * 128, actor_id="a" * 128, reason="r" * 512
    )
    code, out, err = invoke_control(runtime, request)
    assert code == 0 and not err, (out, err)
    receipt = json.loads(out)["data"]["receipt"]
    assert receipt["accepted"]
    assert receipt["target"]["run_id"] == run_id
    runtime.close()
    runtime = replace(runtime, store=SQLiteRuntimeStore.open(runtime.paths.db_path))
    held = runtime.store.load_runtime_state(runtime.cas_store)
    assert held.run_execution_controls[run_id].state == "paused"
    assert replace(held, run_execution_controls={}) == before
    assert runtime.store.show_operation(request)["receipt"] == receipt
    assert runtime.store.resolve_operation(request)["receipt"] == receipt

    adapter = _RecordingAdapter(_success_start)
    result = run_bounded_execution_unit(
        runtime,
        activation_id=run.activation_id,
        local_config=_config(adapter),
        driving_budget_id=epoch.budget_id,
        driving_budget_clock=lambda: 101,
    )
    assert result.code == "run_execution_held"
    assert not adapter.requests
    assert runtime.store.load_runtime_state(runtime.cas_store) == held
    assert runtime.store.load_daemon_budget_epoch(epoch.budget_id) == budget
    assert (
        runtime.store.pending_budgeted_runner_start_session_ids(epoch.budget_id) == ()
    )

    changed = ControlRequest.parse(json.dumps({**request.payload, "reason": "changed"}))
    code, out, err = invoke_control(runtime, changed)
    assert code != 0 and "idempotency_conflict" in out + err
    resume = request_for(
        runtime, run, action="runs.resume", pause_id=receipt["pause_id"]
    )
    code, out, err = invoke_control(runtime, resume)
    assert code == 0 and not err, (out, err)
    released = json.loads(out)["data"]["receipt"]
    assert (
        released["accepted"]
        and released["after_execution_state"] == "runnable_unstarted"
    )
    runtime.close()
    runtime = replace(runtime, store=SQLiteRuntimeStore.open(runtime.paths.db_path))
    after = runtime.store.load_runtime_state(runtime.cas_store)
    assert after.run_execution_controls[run_id].state == "resumed"
    assert replace(after, run_execution_controls={}) == before
    assert runtime.store.show_operation(resume)["receipt"] == released
    for original, expected in ((request, receipt), (resume, released)):
        code, out, err = invoke_control(runtime, original)
        assert code == 0 and not err, (out, err)
        data = json.loads(out)["data"]
        assert data["replayed"] and data["receipt"] == expected
    assert runtime.store.load_runtime_state(runtime.cas_store) == after
    assert runtime.store.load_daemon_budget_epoch(epoch.budget_id) == budget
    assert (
        runtime.store.daemon_budget_id_for_session(run.current_session_id or "absent")
        is None
    )
    runtime.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", ""),
        ("run_id", "run\ninvalid"),
        ("run_id", "\ud800"),
        ("run_id", "x" * (16 * 1024)),
        ("run_id", "é" * 8193),
        ("caller_id", "c" * 129),
        ("actor_id", "a" * 129),
        ("reason", "r" * 513),
    ],
)
def test_public_long_run_request_retains_malformed_envelope_and_caller_limits(
    tmp_path, field, value
):
    runtime, run = _runtime_with_generated_long_run(
        tmp_path, session=False, enqueue_id="native-enqueue"
    )
    before = runtime.store.load_runtime_state(runtime.cas_store)
    payload = request_for(runtime, run).payload
    (payload["target"] if field == "run_id" else payload)[field] = value
    code, out, err = _invoke(
        [
            "--workspace",
            str(runtime.paths.workspace_path),
            "--json",
            "runs",
            "pause",
            "--request-json",
            json.dumps(payload),
        ]
    )
    assert code != 0 and "invalid_control" in out + err
    assert runtime.store.load_runtime_state(runtime.cas_store) == before
    assert runtime.store._connection.execute(
        "SELECT COUNT(*) FROM control_operations"
    ).fetchone() == (0,)
    runtime.close()


@pytest.mark.parametrize("session", [False, True])
def test_public_pause_resume_and_historical_replay_are_finite(tmp_path, session):
    runtime, run = runtime_with_run(tmp_path, session=session)
    request = request_for(runtime, run)
    start = time.monotonic()
    code, out, err = invoke_control(runtime, request)
    assert time.monotonic() - start < 5
    assert code == 0 and not err
    receipt = json.loads(out)["data"]["receipt"]
    resume = request_for(
        runtime, run, action="runs.resume", pause_id=receipt["pause_id"]
    )
    code, out, err = invoke_control(runtime, resume)
    assert code == 0 and not err
    assert (
        json.loads(out)["data"]["receipt"]["after_execution_state"]
        == "runnable_unstarted"
    )
    assert invoke_control(runtime, request)[0] == 0
    assert (
        runtime.store.load_runtime_state(runtime.cas_store).runs[run.run_ref.run_id]
        == run
    )
    runtime.close()


def test_held_a_survives_restart_while_independent_b_completes(tmp_path):
    runtime, run = runtime_with_run(tmp_path, session=True)
    other = add_run(runtime, "b")
    before = runtime.store.load_runtime_state(runtime.cas_store)
    pause(runtime, run)
    runtime.close()
    runtime = replace(runtime, store=SQLiteRuntimeStore.open(runtime.paths.db_path))
    result = reconcile_pending_runner_sessions(
        runtime, local_config=_codex_success_config()
    )
    assert result.code == "observation_accepted"
    after = runtime.store.load_runtime_state(runtime.cas_store)
    assert any(
        item.run_id == other.run_ref.run_id
        for item in after.runner_observations.values()
    )
    assert after.runs[run.run_ref.run_id] == before.runs[run.run_ref.run_id]
    assert (
        after.runner_sessions[run.current_session_id]
        == before.runner_sessions[run.current_session_id]
    )
    assert after.run_execution_controls[run.run_ref.run_id].state == "paused"
    assert (
        run_bounded_execution_unit(
            runtime,
            activation_id=run.activation_id,
            local_config=_codex_success_config(),
        ).code
        == "run_execution_held"
    )
    runtime.close()


@pytest.mark.parametrize("cancel_first", [False, True])
def test_operator_cancellation_serializes_and_supersedes_without_budget_inference(
    tmp_path, cancel_first
):
    runtime, run = runtime_with_run(tmp_path, session=True)
    epoch = epoch_for(runtime, run)
    runtime.store._stop_daemon_budget_epoch(
        epoch.budget_id,
        observed_at=101,
        status="exhausted",
        reason="invocation_limit_exhausted",
    )
    held = None if cancel_first else pause(runtime, run)
    state = runtime.store.load_runtime_state(runtime.cas_store)
    session = state.runner_sessions[run.current_session_id]
    cancellation = RequestRunnerSessionCancellation(
        "cancel",
        run_ref=run.run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        expected_state="created",
        request_id="operator-cancel",
        reason="operator_cancel_work",
        source_kind="operator",
        actor_id="operator",
        requested_at=101,
        request_order=1,
        primary=True,
    )
    assert _persist_transition(runtime, cancellation) is not None
    assert not pause(runtime, run)["receipt"]["accepted"]
    current = runtime.store.load_runtime_state(runtime.cas_store)
    if held:
        control = current.run_execution_controls[run.run_ref.run_id]
        assert (control.state, control.cause) == (
            "superseded",
            "run_cancellation_in_progress",
        )
        assert control.control_revision == 2
    assert runtime.store.daemon_budget_id_for_session(session.session_id) is None
    assert current.runner_session_cancellation_requests["operator-cancel"].primary
    runtime.close()


@pytest.mark.parametrize("hold_first", [False, True, "resumed"])
def test_unexpected_unstarted_completion_retains_unknown_evidence(tmp_path, hold_first):
    runtime, run = runtime_with_run(tmp_path, session=True)
    if hold_first:
        held = pause(runtime, run)["receipt"]
        if hold_first == "resumed":
            release = request_for(
                runtime, run, action="runs.resume", pause_id=held["pause_id"]
            )
            assert runtime.store.execute_run_control(
                release, runtime.cas_store, supported_adapter_kinds=frozenset({"codex"})
            )["receipt"]["accepted"]
    session = runtime.store.load_runtime_state(runtime.cas_store).runner_sessions[
        run.current_session_id
    ]
    signal = RefuseRunnerSessionSignal(
        "unexpected-completion",
        run_ref=run.run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        expected_state="created",
        signal_kind="runner_completion_outcome",
        reason="runner_session_reconciliation_contradiction",
        signal_digest="sha256:" + "a" * 64,
    )
    assert _persist_transition(runtime, signal) is None
    current = runtime.store.load_runtime_state(runtime.cas_store)
    assert current.run_execution_controls[run.run_ref.run_id].state == "unknown"
    assert current.refusals[-1].input_id == signal.input_id
    assert pause(runtime, run)["receipt"]["reason_code"] == "run_aftermath_unknown"
    runtime.close()


def test_queue_cancel_authority_is_not_broadened_by_hold(tmp_path):
    runtime, run = runtime_with_run(tmp_path)
    pause(runtime, run)
    cancel = CancelQueuedWork(
        "close",
        work_item_id=run.work_item_id,
        plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
        actor_id="operator",
        reason="explicit queue cancellation",
    )
    assert _persist_transition(runtime, cancel) is None
    state = runtime.store.load_runtime_state(runtime.cas_store)
    assert not state.closed_work_items
    assert state.run_execution_controls[run.run_ref.run_id].state == "paused"
    runtime.close()


@pytest.mark.parametrize("resume_first", [False, True])
def test_selected_close_lineage_supersedes_hold_without_new_queue_authority(
    tmp_path, resume_first
):
    import millrace.operator as operator_api
    from millrace.contracts.transition import ClaimWork
    from millrace.kernel import apply, decide
    from millrace.testing import (
        deterministic_context,
        materialize_fake_runner_session_cas,
    )
    from substrate.test_persistence_integrity_refusals import _apply_generic_observation
    from support import generic_admission as admission
    from support.run_controls import lineage_hold_runtime

    runtime, run, plan, fingerprint = lineage_hold_runtime(tmp_path)
    receipt = pause(runtime, run)["receipt"]
    assert receipt["accepted"]
    resume = request_for(
        runtime, run, action="runs.resume", pause_id=receipt["pause_id"]
    )
    resumed = None
    if resume_first:
        resumed = runtime.store.execute_run_control(
            resume, runtime.cas_store, supported_adapter_kinds=frozenset({"codex"})
        )["receipt"]
        assert resumed["accepted"]
    state = runtime.store.load_runtime_state(runtime.cas_store)
    claim_parent = ClaimWork("claim-continuation", activation_id="continuation")
    parent_decision = decide(
        state,
        claim_parent,
        deterministic_context(
            transition_id="claim-continuation",
            run_id="run-continuation",
            claim_id="claim-continuation",
            fencing_token="fence-continuation",
        ),
    )
    assert parent_decision.accepted, parent_decision.refusal
    state = apply(state, parent_decision)
    state = _apply_generic_observation(
        state,
        plan,
        fingerprint,
        run_id="run-continuation",
        action_id=admission.RECOVERY_SOURCE_ACTION_ID,
        input_id="recover",
        context=deterministic_context(
            transition_id="recover", activation_id="recovery"
        ),
    )
    claim = ClaimWork("claim-recovery", activation_id="recovery")
    decision = decide(
        state,
        claim,
        deterministic_context(
            transition_id="claim-recovery",
            run_id="run-recovery",
            claim_id="claim-recovery",
            fencing_token="fence-recovery",
        ),
    )
    assert decision.accepted, decision.refusal
    state = apply(state, decision)
    state = _apply_generic_observation(
        state,
        plan,
        fingerprint,
        run_id="run-recovery",
        action_id=admission.RECOVERY_QUARANTINE_ACTION_ID,
        input_id="quarantine",
        context=deterministic_context(transition_id="quarantine"),
    )
    state = materialize_fake_runner_session_cas(
        state=state, cas_store=runtime.cas_store
    )
    runtime.store.persist_runtime_state(state, runtime.cas_store)
    state = runtime.store.load_runtime_state(runtime.cas_store)
    quarantine = next(iter(state.lineage_quarantines.values()))
    close = operator_api.build_close_lineage(
        state,
        operator_api.OperatorCloseLineageInput(
            input_id="close-lineage",
            option_id="admission.close",
            selected_plan_ref=quarantine.selected_plan_ref,
            quarantine_id=quarantine.quarantine_id,
            lineage_id=None,
            actor_id="operator",
            actor_kind="local_operator",
            reason="Selected operator closure",
            payload={},
        ),
    )
    assert _persist_transition(runtime, close) is not None
    after = runtime.store.load_runtime_state(runtime.cas_store)
    assert run.work_item_id in after.closed_work_items
    assert after.operator_interventions
    if not resume_first:
        control = after.run_execution_controls[run.run_ref.run_id]
        assert (control.state, control.cause, control.control_revision) == (
            "superseded",
            "run_work_closed",
            2,
        )
        fresh = request_for(
            runtime, run, action="runs.resume", pause_id=receipt["pause_id"]
        )
        assert not runtime.store.execute_run_control(
            fresh, runtime.cas_store, supported_adapter_kinds=frozenset({"codex"})
        )["receipt"]["accepted"]
    else:
        assert (
            runtime.store.execute_run_control(
                resume, runtime.cas_store, supported_adapter_kinds=frozenset({"codex"})
            )["receipt"]
            == resumed
        )
    runtime.close()


def test_selected_millforge_sessionless_hold_is_unstarted_only(tmp_path):
    from cli.test_cli_bounded_execution_unit import (
        _active_millforge_state_with_codex_default,
        _runtime,
    )

    state, _, _, _ = _active_millforge_state_with_codex_default()
    runtime = _runtime(tmp_path, state)
    run = state.runs["run-millforge-taskmaster"]
    assert run.current_session_id is None
    held = pause(runtime, run)["receipt"]
    release = request_for(runtime, run, action="runs.resume", pause_id=held["pause_id"])
    assert invoke_control(runtime, release)[0] == 0
    assert (
        runtime.store.load_runtime_state(runtime.cas_store).runs[run.run_ref.run_id]
        == run
    )
    runtime.close()


def test_run_hold_and_release_preserve_existing_dispatch_suspension(tmp_path):
    from millrace.contracts.transition import SuspendDispatch

    runtime, run = runtime_with_run(tmp_path)
    assert (
        _persist_transition(
            runtime,
            SuspendDispatch(
                "suspend",
                plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
                actor_id="operator",
                reason="Existing dispatch hold",
            ),
        )
        is not None
    )
    before = runtime.store.load_runtime_state(runtime.cas_store)
    held = pause(runtime, run)["receipt"]
    release = request_for(runtime, run, action="runs.resume", pause_id=held["pause_id"])
    assert invoke_control(runtime, release)[0] == 0
    after = runtime.store.load_runtime_state(runtime.cas_store)
    assert after.dispatch_suspension == before.dispatch_suspension
    assert after.pause == before.pause
    runtime.close()


def test_unrelated_operator_wait_resolves_while_run_is_held(tmp_path):
    from cli.test_cli_bounded_execution_unit import _runtime
    from millrace.contracts.ids import QueueFamilyId
    from millrace.contracts.transition import ClaimWork, EnqueueWork, OperatorResumeWait
    from substrate.test_persistence_integrity_refusals import (
        _generic_operator_wait_runtime_state,
    )
    from support import generic_fanout

    state = _generic_operator_wait_runtime_state()
    runtime = _runtime(tmp_path, state)
    waiting = next(iter(state.operator_waits.values()))
    assert (
        _persist_transition(
            runtime,
            EnqueueWork(
                "independent",
                queue_family_id=QueueFamilyId("parent"),
                payload=generic_fanout.packet_payload(),
            ),
        )
        is not None
    )
    state = runtime.store.load_runtime_state(runtime.cas_store)
    activation = next(
        item
        for item in state.activations.values()
        if item.created_by_input_id == "independent"
    )
    state = _persist_transition(
        runtime, ClaimWork("claim-independent", activation_id=activation.activation_id)
    )
    run = next(
        item
        for item in state.runs.values()
        if item.created_by_input_id == "claim-independent"
    )
    assert state.work_items[run.work_item_id].lineage_id != waiting.lineage_id
    held = pause(runtime, run)["receipt"]
    resolve = OperatorResumeWait(
        "resolve-unrelated",
        selected_plan_ref=waiting.selected_plan_ref,
        wait_id=waiting.wait_id,
        lineage_id=waiting.lineage_id,
        actor_id="operator",
        actor_kind="local_operator",
        payload={},
    )
    assert _persist_transition(runtime, resolve) is not None
    after = runtime.store.load_runtime_state(runtime.cas_store)
    assert after.operator_waits[waiting.wait_id].status == "resolved"
    assert after.run_execution_controls[run.run_ref.run_id].pause_id == held["pause_id"]
    assert after.run_execution_controls[run.run_ref.run_id].state == "paused"
    runtime.close()


def test_existing_workflow_pause_record_survives_independent_run_control(tmp_path):
    from cli.test_cli_bounded_execution_unit import (
        _active_millforge_state_with_codex_default,
        _runtime,
    )
    from millrace.testing import materialize_fake_runner_session_cas
    from support import kernel_ping

    state, plan, fingerprint, _ = _active_millforge_state_with_codex_default()
    runtime = _runtime(tmp_path, state)
    held_run = add_run(runtime, "separate-plan")
    state = runtime.store.load_runtime_state(runtime.cas_store)
    state = kernel_ping.apply_accepted_input(
        state,
        kernel_ping.runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-millforge-taskmaster",
            action_id="kernel_ping.pause_taskmaster_blocked",
            input_id="pause-workflow",
            artifact_payload={},
        ),
        kernel_ping.kernel_ping_context("pause-workflow"),
    )
    state = materialize_fake_runner_session_cas(
        state=state, cas_store=runtime.cas_store
    )
    runtime.store.persist_runtime_state(state, runtime.cas_store)
    before = runtime.store.load_runtime_state(runtime.cas_store)
    assert before.pause is not None
    held = pause(runtime, held_run)["receipt"]
    assert held["accepted"]
    release = request_for(
        runtime, held_run, action="runs.resume", pause_id=held["pause_id"]
    )
    assert invoke_control(runtime, release)[0] == 0
    after = runtime.store.load_runtime_state(runtime.cas_store)
    assert after.pause == before.pause
    assert after.quarantines == before.quarantines
    assert after.dispatch_suspension == before.dispatch_suspension
    runtime.close()


def test_resume_allows_same_session_completion_without_retry(tmp_path):
    runtime, run = runtime_with_run(tmp_path, session=True)
    held = pause(runtime, run)["receipt"]
    release = request_for(runtime, run, action="runs.resume", pause_id=held["pause_id"])
    assert invoke_control(runtime, release)[0] == 0
    result = run_bounded_execution_unit(
        runtime, activation_id=run.activation_id, local_config=_codex_success_config()
    )
    assert result.code == "observation_accepted"
    after = runtime.store.load_runtime_state(runtime.cas_store)
    assert after.runs[run.run_ref.run_id].current_session_id == run.current_session_id
    assert after.runs[run.run_ref.run_id].run_ref == run.run_ref
    assert after.runner_sessions[run.current_session_id].state == "completed"
    runtime.close()


def test_explicit_retry_has_new_fence_and_old_pause_target_is_refused(tmp_path):
    from millrace.contracts.transition import CreateRunnerSession
    from support.runner_sessions import _config, _RecordingAdapter, _refused_start

    runtime, run = runtime_with_run(tmp_path, session=True)
    adapter = _RecordingAdapter(_refused_start)
    result = run_bounded_execution_unit(
        runtime, activation_id=run.activation_id, local_config=_config(adapter)
    )
    assert result.code == "adapter_failure"
    state = runtime.store.load_runtime_state(runtime.cas_store)
    prior = state.runner_sessions[run.current_session_id]
    assert prior.state == "failed"
    old = request_for(runtime, run)
    assert not pause(runtime, run)["receipt"]["accepted"]
    retry = CreateRunnerSession(
        "explicit-retry",
        run_ref=run.run_ref,
        session_id="retry-session",
        session_fencing_token="retry-fence",
        created_at=prior.ended_at + 1,
        explicit_retry_intent=True,
    )
    assert _persist_transition(runtime, retry) is not None
    result = runtime.store.execute_run_control(
        old, runtime.cas_store, supported_adapter_kinds=frozenset({"codex"})
    )
    assert result["receipt"]["reason_code"] == "run_session_target_mismatch"
    held = pause(runtime, run)["receipt"]
    assert held["accepted"]
    after = runtime.store.load_runtime_state(runtime.cas_store)
    assert after.runs[run.run_ref.run_id].current_session_id == "retry-session"
    assert (
        after.runner_sessions["retry-session"].session_fencing_token
        != prior.session_fencing_token
    )
    runtime.close()


@pytest.mark.parametrize(
    "case", ["recover", "resume_absent", "resume_mismatch", "resume_accepted"]
)
def test_receipt_pause_identity_preserves_public_history(tmp_path, case):
    from uuid import uuid4

    from millrace.adapters.cli.run_controls import recover_native_control
    from millrace.contracts.controls import ControlRequest
    from millrace.substrate.errors import ControlOperationError

    runtime, run = runtime_with_run(tmp_path, session=True)
    try:
        held = (
            pause(runtime, run)["receipt"]
            if case in {"resume_mismatch", "resume_accepted"}
            else None
        )
        submitted_pause = (
            held["pause_id"] if case == "resume_accepted" else str(uuid4())
        )
        payload = request_for(
            runtime, run, action="runs.resume", pause_id=submitted_pause
        ).payload
        if case == "recover":
            payload.update(
                action="runs.recover",
                profile={
                    "profile_id": "history-test",
                    "profile_digest": "a" * 64,
                    "selected_adapter": "codex",
                    "native_state": "running",
                    "effect_boundary": "unqualified",
                },
            )
            payload["target"].update(
                mode="retire_native_continuation",
                expected_source_revision=runtime.store.control_identity()[
                    "source_revision"
                ],
                owner_id=str(uuid4()),
                witness_digest="b" * 64,
            )
        request = ControlRequest.parse(json.dumps(payload))
        result = (
            recover_native_control(runtime, request, time.monotonic() + 4)
            if case == "recover"
            else runtime.store.execute_run_control(
                request,
                runtime.cas_store,
                supported_adapter_kinds=frozenset({"codex", "millforge"}),
            )
        )
        receipt = result["receipt"]
        assert receipt["accepted"] is (case == "resume_accepted")
        assert receipt["pause_id"] == (held["pause_id"] if held else None)
        if case != "resume_accepted":
            assert receipt["pause_id"] != submitted_pause
        assert receipt["reason"] == "[redacted]"
        before = runtime.store.load_runtime_state(runtime.cas_store)
        identity = runtime.store.control_identity()
        rows = tuple(
            runtime.store._connection.execute("SELECT * FROM control_operations")
        )
        results = tuple(
            runtime.store._connection.execute("SELECT * FROM control_results")
        )
        history = runtime.store.control_history_records(run_id=run.run_ref.run_id)
        assert any(item["record"] == receipt for item in history)
        replay = runtime.store.execute_operation(
            request, lambda: pytest.fail("replayed decision")
        )
        assert replay["receipt"] == receipt
        assert replay["results"] == result["results"]
        for field, value in (
            ("pause_id", str(uuid4())),
            ("reason", "different private reason"),
        ):
            changed = dict(request.payload, **{field: value})
            with pytest.raises(
                ControlOperationError, match="operation_idempotency_conflict"
            ):
                runtime.store.execute_operation(
                    ControlRequest.parse(json.dumps(changed)),
                    lambda: pytest.fail("conflicting decision"),
                )
        code, stdout, stderr = _invoke(
            [
                "--json",
                "--bounded",
                "--workspace",
                str(runtime.paths.workspace_path),
                "runs",
                "show",
                run.run_ref.run_id,
            ]
        )
        assert code == 0, stderr
        assert json.loads(stdout)
        assert runtime.store.control_identity() == identity
        assert runtime.store.load_runtime_state(runtime.cas_store) == before
        assert (
            tuple(runtime.store._connection.execute("SELECT * FROM control_operations"))
            == rows
        )
        assert (
            tuple(runtime.store._connection.execute("SELECT * FROM control_results"))
            == results
        )
    finally:
        runtime.close()
