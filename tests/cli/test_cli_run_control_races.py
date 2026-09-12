from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from millrace.adapters.cli.context import transition_context
from millrace.contracts.transition import AdvanceRunnerSession, CreateRunnerSession
from millrace.kernel import apply, decide
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import ControlOperationError
from millrace.substrate.sqlite import SQLiteRuntimeStore
from support.run_controls import (
    epoch_for,
    pause,
    request_for,
    runtime_with_run,
    start_intent,
)


@pytest.mark.parametrize("kind", ["create", "start"])
def test_pause_wins_during_cas_staging_on_separate_connection(tmp_path, kind):
    runtime, run = runtime_with_run(tmp_path, session=kind == "start")
    if kind == "start":
        epoch_for(runtime, run)
    state = runtime.store.load_runtime_state(runtime.cas_store)
    if kind == "create":
        transition = CreateRunnerSession(
            "create",
            run_ref=run.run_ref,
            session_id="racing-session",
            session_fencing_token="racing-fence",
            created_at=100,
            explicit_retry_intent=False,
        )
    else:
        session = state.runner_sessions[run.current_session_id]
        transition = AdvanceRunnerSession(
            "start",
            run_ref=run.run_ref,
            session_id=session.session_id,
            dispatch_generation=session.dispatch_generation,
            session_fencing_token=session.session_fencing_token,
            expected_state="created",
            next_state="starting",
            occurred_at=101,
        )
    candidate = apply(
        state,
        decide(
            state,
            transition,
            transition_context(command="race", input_id_value=transition.input_id),
        ),
    )
    staged, release = threading.Event(), threading.Event()

    def writer():
        store = SQLiteRuntimeStore.open(runtime.paths.db_path)
        cas = ContentAddressedByteStore(runtime.paths.cas_path)
        original = cas.get_bytes

        def blocked(digest):
            assert not store._connection.in_transaction
            staged.set()
            assert release.wait(5)
            return original(digest)

        cas.get_bytes = blocked
        try:
            if kind == "start":
                store.persist_runner_start(
                    candidate,
                    cas,
                    candidate.runner_sessions[run.current_session_id],
                    "budget-a",
                    lambda: 101,
                )
            else:
                store.persist_runtime_state(candidate, cas)
        except ControlOperationError as exc:
            return str(exc)
        finally:
            store.close()
        return "unexpected_success"

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        assert staged.wait(5)
        assert pause(runtime, run)["receipt"]["accepted"]
        release.set()
        assert future.result(timeout=5) == "stale_runtime_controls"
    current = runtime.store.load_runtime_state(runtime.cas_store)
    assert current.run_execution_controls[run.run_ref.run_id].state == "paused"
    assert current.runner_sessions == state.runner_sessions
    assert (
        runtime.store.daemon_budget_id_for_session(run.current_session_id or "absent")
        is None
    )
    runtime.close()


def test_atomic_start_wins_before_pause_and_retains_explicit_binding(tmp_path):
    runtime, run = runtime_with_run(tmp_path, session=True)
    epoch_for(runtime, run)
    old_target = request_for(runtime, run)
    other = replace(runtime, store=SQLiteRuntimeStore.open(runtime.paths.db_path))
    try:
        assert start_intent(other, run, budget_id="budget-a") is not None
    finally:
        other.close()
    refused = runtime.store.execute_run_control(
        old_target, runtime.cas_store, supported_adapter_kinds=frozenset({"codex"})
    )
    assert refused["receipt"]["reason_code"] == "run_session_target_mismatch"
    assert (
        pause(runtime, run)["receipt"]["reason_code"] == "run_pause_unsupported_state"
    )
    assert (
        runtime.store.daemon_budget_id_for_session(run.current_session_id) == "budget-a"
    )
    runtime.close()


def test_locked_revision_revalidation_refuses_authority_change_during_load(
    tmp_path, monkeypatch
):
    runtime, run = runtime_with_run(tmp_path, session=True)
    request = request_for(runtime, run)
    original = runtime.store.load_runtime_state

    def changed(cas, **kwargs):
        state = original(cas, **kwargs)
        other = replace(runtime, store=SQLiteRuntimeStore.open(runtime.paths.db_path))
        try:
            assert start_intent(other, run) is not None
        finally:
            other.close()
        return state

    monkeypatch.setattr(runtime.store, "load_runtime_state", changed)
    with pytest.raises(ControlOperationError, match="run_authority_changed"):
        runtime.store.execute_run_control(
            request, runtime.cas_store, supported_adapter_kinds=frozenset({"codex"})
        )
    assert runtime.store.show_operation(request)["receipt"] is None
    monkeypatch.setattr(runtime.store, "load_runtime_state", original)
    assert not original(runtime.cas_store).run_execution_controls
    runtime.close()


def test_total_deadline_includes_staged_context_and_cannot_commit_late(
    tmp_path, monkeypatch
):
    from millrace.substrate import _sqlite_run_controls as controls

    runtime, run = runtime_with_run(tmp_path)
    request = request_for(runtime, run)
    original = runtime.cas_store.get_bytes
    clock = [0.0]
    monkeypatch.setattr(controls.time, "monotonic", lambda: clock[0])

    def expensive(digest):
        payload = original(digest)
        clock[0] = 4.1
        return payload

    monkeypatch.setattr(runtime.cas_store, "get_bytes", expensive)
    with pytest.raises(ControlOperationError, match="control_deadline_unknown"):
        runtime.store.execute_run_control(
            request, runtime.cas_store, supported_adapter_kinds=frozenset({"codex"})
        )
    assert runtime.store.show_operation(request)["receipt"] is None
    runtime.close()


def test_real_slow_cas_preparation_returns_public_unknown_within_five_seconds(
    tmp_path, monkeypatch
):
    import time

    from cli.test_cli_run_controls import invoke_control

    runtime, run = runtime_with_run(tmp_path)
    request = request_for(runtime, run)
    original = ContentAddressedByteStore.get_bytes

    def slow(store, digest):
        time.sleep(6)
        return original(store, digest)

    monkeypatch.setattr(ContentAddressedByteStore, "get_bytes", slow)
    began = time.monotonic()
    code, out, err = invoke_control(runtime, request)
    elapsed = time.monotonic() - began
    assert 3.5 <= elapsed < 5
    assert code != 0
    assert "control_deadline_unknown" in out + err
    assert runtime.store.show_operation(request)["receipt"] is None
    runtime.close()


@pytest.mark.parametrize("attach_first", [False, True])
def test_actual_context_attachment_serializes_with_hold_and_never_starts(
    tmp_path, monkeypatch, attach_first
):
    from types import SimpleNamespace

    from millrace.adapters.cli import session_completion
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.contracts.transition import AttachRunnerSessionContext
    from support.runner_sessions import _config, _RecordingAdapter, _success_start

    runtime, run = runtime_with_run(tmp_path, session=True, bound=True)
    docs = runtime.paths.workspace_path / "docs"
    docs.mkdir()
    (docs / "guide.txt").write_text("Pinned context\n")
    adapter = _RecordingAdapter(_success_start)
    adapter.config = SimpleNamespace(
        cwd=runtime.paths.workspace_path, wrapper_protocol_version=4
    )
    prepared, release = threading.Event(), threading.Event()
    original = session_completion._persist_transition

    def intercepted(current, transition, **kwargs):
        if isinstance(transition, AttachRunnerSessionContext):
            assert not current.store._connection.in_transaction
            if attach_first:
                result = original(current, transition, **kwargs)
                assert result is not None
            prepared.set()
            assert release.wait(5)
            return result if attach_first else original(current, transition, **kwargs)
        return original(current, transition, **kwargs)

    monkeypatch.setattr(session_completion, "_persist_transition", intercepted)

    def drive():
        worker = replace(
            runtime,
            store=SQLiteRuntimeStore.open(runtime.paths.db_path),
            cas_store=ContentAddressedByteStore(runtime.paths.cas_path),
        )
        try:
            return run_bounded_execution_unit(
                worker, activation_id=run.activation_id, local_config=_config(adapter)
            )
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(drive)
        assert prepared.wait(5)
        before = runtime.store.load_runtime_state(runtime.cas_store).runner_sessions[
            run.current_session_id
        ]
        assert (before.context_manifest_digest is not None) == attach_first
        assert pause(runtime, run)["receipt"]["accepted"]
        release.set()
        result = future.result(timeout=5)
    after = runtime.store.load_runtime_state(runtime.cas_store)
    assert not adapter.requests
    assert after.runner_sessions[run.current_session_id] == before
    assert after.run_execution_controls[run.run_ref.run_id].state == "paused"
    assert result.code in {
        "session_preparation_refused",
        "session_start_intent_refused",
        "run_execution_held",
    }
    runtime.close()
