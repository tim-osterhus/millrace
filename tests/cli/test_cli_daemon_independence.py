"""One public daemon with actual native owners and scripted loopback inference."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest
from tests.cli.test_cli_native_run_controls import fixtures


class IndependentCase(fixtures.LiveCase):
    def __init__(
        self, *, preclaimed=False, capacity=2, extra_args=(), explicit_activation=False
    ):
        self.root = Path(os.environ["MILLRACE_TEST_SOCKET_ROOT"]) / (
            "i" + uuid4().hex[:5]
        )
        self.root.mkdir(parents=True)
        self.server = fixtures.ModelServer(delay=2.0)
        plan, fingerprint = fixtures.compile_native_plan()
        state, _ = fixtures._ready_state_for_plan(plan, fingerprint)
        runtime = fixtures._runtime(self.root, state)
        self.paths = runtime.paths
        runtime.close()
        self.a_activation = next(iter(state.activations))
        self.b_activation = None
        self.base = [
            sys.executable,
            "-B",
            "-c",
            "from millrace.adapters.cli.main import main; raise SystemExit(main())",
            "--json",
            "--workspace",
            str(self.paths.workspace_path),
        ]
        if preclaimed:
            self.command(
                "dispatch", "claim", self.a_activation, "--input-id", "claim-A"
            )
            self.enqueue_b()
            self.command(
                "dispatch", "claim", self.b_activation, "--input-id", "claim-B"
            )
            assert len(self.state().runner_sessions) == 2
            assert all(
                s.state == "created" for s in self.state().runner_sessions.values()
            )
        profile, secret = fixtures.model_profile(self.server.http.server_port)
        payload = profile.model_dump(mode="json")
        payload.pop("configured_headers", None)
        self.config = self.root / "config.json"
        self.config.write_text(
            json.dumps(
                {
                    "millforge": {
                        "adapter_id": "millforge",
                        "workspace_root": str(self.paths.workspace_path),
                        "timeout_seconds": 30,
                        "model_profile": payload,
                        "secret_ref": secret.model_dump(mode="json"),
                        "redaction_policy": {
                            "policy_id": "native-proof-local",
                            "secret_tokens": [],
                        },
                    }
                }
            )
        )
        if explicit_activation:
            extra_args = ("--max-ticks", "1", "--activation-id", self.a_activation)
        environment = dict(os.environ, CORE_NATIVE_PROOF_KEY="local-script-only")
        self.child = subprocess.Popen(
            [
                *self.base,
                "run",
                "daemon",
                "--idle-sleep",
                "0.02",
                "--max-invocations",
                str(capacity),
                "--budget-id",
                "native-proof-epoch",
                "--adapter-config-json",
                str(self.config),
                *extra_args,
            ],
            cwd=fixtures.CORE,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def command(self, *arguments):
        result = subprocess.run(
            [*self.base, *arguments],
            cwd=fixtures.CORE,
            capture_output=True,
            text=True,
            timeout=8,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        return json.loads(result.stdout)["data"]

    def enqueue_b(self):
        self.command(
            "queue",
            "enqueue",
            "prompt",
            "--payload-json",
            json.dumps({"prompt_id": "B", "body": "independent-B"}),
            "--input-id",
            "enqueue-B",
        )
        self.b_activation = next(
            a.activation_id
            for a in self.state().activations.values()
            if a.created_by_input_id == "enqueue-B"
        )

    def request(self, **changes):
        from millforge.pause_control import PROFILE, PROFILE_DIGEST

        from support.run_controls import request_for

        with closing(self.runtime()) as runtime:
            state = runtime.store.load_runtime_state(runtime.cas_store)
            run = next(
                r for r in state.runs.values() if r.activation_id == self.a_activation
            )
            return request_for(
                runtime,
                run,
                profile={**PROFILE, "profile_digest": PROFILE_DIGEST},
                **changes,
            )

    def state(self):
        with closing(self.runtime()) as runtime:
            return runtime.store.load_runtime_state(runtime.cas_store)

    def budget(self):
        with closing(self.runtime()) as runtime:
            return runtime.store.load_daemon_budget_epoch("native-proof-epoch")


@pytest.mark.parametrize(
    "preclaimed", [True, False], ids=["both-claimed-before-hold", "late-enqueue"]
)
@pytest.mark.parametrize(
    "resume_early", [False, True], ids=["B-completes-held", "resume-before-B-completes"]
)
def test_same_daemon_independence(preclaimed, resume_early):
    case = IndependentCase(preclaimed=preclaimed)
    try:
        case.wait(case.server.started.is_set)
        assert case.budget().accepted_start_count == 1
        response = case.invoke(case.request())
        assert response.returncode == 0, (response.stdout, response.stderr)
        state = case.state()
        a = next(r for r in state.runs.values() if r.activation_id == case.a_activation)
        original = state.runner_sessions[a.current_session_id]
        control = state.run_execution_controls[a.run_ref.run_id]
        assert control.state == "paused"
        assert case.budget().accepted_start_count in ({1, 2} if preclaimed else {1})
        if not preclaimed:
            case.enqueue_b()
        case.wait(lambda: (case.paths.workspace_path / "b-before.txt").exists())
        assert not (case.paths.workspace_path / "after.txt").exists()
        assert case.budget().accepted_start_count == 2
        # A third public candidate must receive no start even after B settles.
        case.command(
            "queue",
            "enqueue",
            "prompt",
            "--payload-json",
            json.dumps({"prompt_id": "C", "body": "third-not-admitted"}),
            "--input-id",
            "enqueue-C",
        )
        if not resume_early:
            case.wait(lambda: len(case.state().runner_session_completions) == 1)
            assert (case.paths.workspace_path / "b-after.txt").read_text() == "after"
            assert not (case.paths.workspace_path / "after.txt").exists()
            assert (
                case.state().run_execution_controls[a.run_ref.run_id].state == "paused"
            )
            assert case.budget().status == "active"
        else:
            assert len(case.state().runner_session_completions) == 0
        response = case.invoke(
            case.request(action="runs.resume", pause_id=control.pause_id)
        )
        assert response.returncode == 0, (response.stdout, response.stderr)
        case.child.wait(timeout=12)
        state = case.state()
        assert len(state.runner_sessions) == 2
        assert len(state.runner_session_completions) == 2
        assert len(state.runner_observations) == 2
        assert all(s.state == "completed" for s in state.runner_sessions.values())
        current = state.runner_sessions[original.session_id]
        for name in (
            "session_id",
            "run_id",
            "dispatch_generation",
            "session_fencing_token",
            "start_intent_at",
            "started_at",
            "durable_locator_digest",
        ):
            assert getattr(current, name) == getattr(original, name)
        assert (case.paths.workspace_path / "after.txt").read_text() == "after"
        budget = case.budget()
        assert budget.status == "exhausted"
        assert budget.accepted_start_count == 2
        assert budget.cumulative_total_tokens == 40
        assert len(case.server.requests) == 8
    finally:
        case.close()


def test_capacity_one_does_not_admit_b_while_a_held():
    case = IndependentCase(capacity=1)
    try:
        case.wait(case.server.started.is_set)
        response = case.invoke(case.request())
        assert response.returncode == 0, (response.stdout, response.stderr)
        case.enqueue_b()
        time.sleep(0.7)
        assert case.budget().accepted_start_count == 1
        assert len(case.state().runner_sessions) == 1
        assert not (case.paths.workspace_path / "b-before.txt").exists()
        control = next(iter(case.state().run_execution_controls.values()))
        response = case.invoke(
            case.request(action="runs.resume", pause_id=control.pause_id)
        )
        assert response.returncode == 0, (response.stdout, response.stderr)
        case.child.wait(timeout=10)
        assert len(case.state().runner_observations) == 1
        assert case.budget().cumulative_total_tokens == 20
    finally:
        case.close()


@pytest.mark.parametrize("stop_kind", ["signal", "max_ticks"])
def test_public_stop_drains_both_accepted_owners(stop_kind):
    case = IndependentCase(
        preclaimed=True,
        extra_args=("--max-ticks", "2") if stop_kind == "max_ticks" else (),
    )
    try:
        case.wait(case.server.started.is_set)
        response = case.invoke(case.request())
        assert response.returncode == 0, (response.stdout, response.stderr)
        case.wait(lambda: (case.paths.workspace_path / "b-before.txt").exists())
        assert case.budget().accepted_start_count == 2
        if stop_kind == "signal":
            case.child.terminate()
        case.child.wait(timeout=12)
        state = case.state()
        assert len(state.runner_session_completions) == 2
        assert all(
            s.state in {"completed", "interrupted", "failed"}
            and s.cleanup_disposition == "complete"
            for s in state.runner_sessions.values()
        )
        assert not (case.paths.workspace_path / ".millrace/daemon.lock").exists()
    finally:
        case.close()


def _retained_fixture(tmp_path, *, config=None):
    from tests.cli.test_cli_bounded_execution_unit import (
        _ready_state,
        _ready_state_with_selected_codex_authority,
        _runtime,
    )
    from tests.cli.test_cli_daemon_session import _SignalWaitAdapter

    from millrace.adapters.cli import session_coordinator as sessions
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.adapters.runner_contract import AdapterLocalConfig

    state, _ = (
        _ready_state()
        if config is None
        else _ready_state_with_selected_codex_authority()
    )
    runtime = _runtime(tmp_path, state)
    owners = {}
    token = sessions._RETAINED_OWNERS.set(owners)
    try:
        result = run_bounded_execution_unit(
            runtime,
            local_config=config
            or AdapterLocalConfig(adapters={"codex": _SignalWaitAdapter()}),
        )
        assert result.code == "runner_session_retained", result
        return runtime.paths, next(iter(owners.values()))
    finally:
        sessions._RETAINED_OWNERS.reset(token)
        runtime.close()


def test_original_deadline_and_cooperative_cancellation_cursor(tmp_path, monkeypatch):
    from tests.cli.test_cli_daemon_session import _SignalWaitHandle

    from millrace.adapters.cli import daemon
    from millrace.adapters.cli import session_cancellation as cancel
    from millrace.adapters.cli import session_coordinator as sessions

    paths, a = _retained_fixture(tmp_path / "a")
    other_paths, b = _retained_fixture(tmp_path / "b")
    clock = [a.deadline - 1]
    monkeypatch.setattr(sessions, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(cancel, "_monotonic", lambda: clock[0])

    class SlowHandle(_SignalWaitHandle):
        def request_cancel(self):
            operation = super().request_cancel()
            self._cancelled = False
            return operation

        def kill(self):
            self._cancelled = True
            return super().kill()

    a.handle = SlowHandle(a.request)
    original_deadline = a.deadline
    for _ in range(3):
        with closing(daemon.open_runtime_context(paths, command="test")) as runtime:
            assert (
                sessions._step_retained_owner(runtime, a, stop_requested=False) is None
            )
        assert a.deadline == original_deadline
    clock[0] = original_deadline
    with closing(daemon.open_runtime_context(paths, command="test")) as runtime:
        assert sessions._step_retained_owner(runtime, a, stop_requested=False) is None
        assert sessions._step_retained_owner(runtime, a, stop_requested=False) is None
        assert a.cancellation.primary.reason == "runner_timeout"
        grace_deadline = a.cancellation.deadline
    # B reaches its original deadline and settles while A is still in its grace.
    b.deadline = clock[0]
    with closing(daemon.open_runtime_context(other_paths, command="test")) as runtime:
        assert sessions._step_retained_owner(runtime, b, stop_requested=False) is None
        result = sessions._step_retained_owner(runtime, b, stop_requested=False)
        assert result.adapter_error_kind == "cancelled"
        assert b.cancellation.primary.reason == "runner_timeout"
    with closing(daemon.open_runtime_context(paths, command="test")) as runtime:
        assert sessions._step_retained_owner(runtime, a, stop_requested=True) is None
        assert a.cancellation.primary.reason == "runner_timeout"
        assert a.cancellation.deadline == grace_deadline
        clock[0] = grace_deadline
        assert sessions._step_retained_owner(runtime, a, stop_requested=True) is None
        assert sessions._step_retained_owner(runtime, a, stop_requested=True) is None
        clock[0] = a.cancellation.deadline
        assert sessions._step_retained_owner(runtime, a, stop_requested=True) is None
        result = sessions._step_retained_owner(runtime, a, stop_requested=True)
        assert result.adapter_error_kind == "cancelled"
        state = runtime.store.load_runtime_state(runtime.cas_store)
        attempts = list(state.runner_session_cancellation_attempts.values())
        assert [x.operation for x in sorted(attempts, key=lambda x: x.sequence)] == [
            "cooperative_cancel",
            "terminate",
            "kill",
            "transport_cleanup",
        ]
        assert a.deadline == original_deadline


@pytest.mark.parametrize("cancel_during_write", [False, True])
def test_consumed_completion_survives_external_public_write(
    tmp_path, monkeypatch, cancel_during_write
):
    from tests.cli.test_cli_bounded_execution_unit import _codex_success_config
    from tests.cli.test_cli_daemon_loop import _invoke

    from millrace.adapters.cli import daemon
    from millrace.adapters.cli import session_coordinator as sessions
    from millrace.substrate.errors import StorageIntegrityError
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    paths, owner = _retained_fixture(tmp_path, config=_codex_success_config())
    original_persist = SQLiteRuntimeStore.persist_runtime_state
    injected = []

    def competing_write(store, state, cas):
        if state.runner_session_completions and not injected:
            injected.append(True)
            code, out, err = _invoke(
                [
                    "--json",
                    "--workspace",
                    str(paths.workspace_path),
                    "queue",
                    "enqueue",
                    "prompt",
                    "--payload-json",
                    json.dumps({"prompt_id": "racer", "body": "external"}),
                    "--input-id",
                    "external-during-completion",
                ]
            )
            assert code == 0, (out, err)
            if cancel_during_write:
                code, out, err = _invoke(
                    [
                        "--json",
                        "--workspace",
                        str(paths.workspace_path),
                        "runs",
                        "cancel",
                        owner.run_ref.run_id,
                        "--input-id",
                        "cancel-during-completion",
                    ]
                )
                assert code == 0, (out, err)
        return original_persist(store, state, cas)

    monkeypatch.setattr(SQLiteRuntimeStore, "persist_runtime_state", competing_write)
    consumed = []
    original_handle = owner.handle

    class CountedHandle:
        def __getattr__(self, name):
            return getattr(original_handle, name)

        def poll_completion(self):
            assert not consumed, "one-shot completion polled after consumption"
            value = original_handle.poll_completion()
            if value is not None:
                consumed.append(value)
            return value

    owner.handle = CountedHandle()
    conflicted = False
    result = None
    end = time.monotonic() + 10
    while result is None and time.monotonic() < end:
        with closing(daemon.open_runtime_context(paths, command="test")) as runtime:
            try:
                result = sessions._step_retained_owner(
                    runtime, owner, stop_requested=False
                )
            except StorageIntegrityError:
                conflicted = True
                assert owner.outcome is consumed[0]
        time.sleep(0.01)
    assert conflicted and injected and len(consumed) == 1
    assert result.code == "observation_accepted"
    with closing(daemon.open_runtime_context(paths, command="test")) as runtime:
        state = runtime.store.load_runtime_state(runtime.cas_store)
        assert (
            len(state.runner_session_completions) == len(state.runner_observations) == 1
        )
        assert "external-during-completion" in state.receipts
        if cancel_during_write:
            completion = state.runner_session_completions[owner.session.session_id]
            assert (
                completion.primary_cancellation_request_id == "cancel-during-completion"
            )
            assert len(state.runner_session_cancellation_requests) == 1


def test_explicit_activation_does_not_start_other_preclaimed_run():
    case = IndependentCase(preclaimed=True, explicit_activation=True)
    try:
        case.child.wait(timeout=12)
        state = case.state()
        a = next(r for r in state.runs.values() if r.activation_id == case.a_activation)
        b = next(r for r in state.runs.values() if r.activation_id == case.b_activation)
        assert state.runner_sessions[a.current_session_id].state == "completed"
        assert state.runner_sessions[b.current_session_id].state == "created"
        assert case.budget().accepted_start_count == 1
        assert len(case.server.requests) == 4
        assert not (case.paths.workspace_path / "b-before.txt").exists()
    finally:
        case.close()


@pytest.mark.parametrize("failure", ["poll", "store-open", "drain-error"])
def test_exception_drains_every_retained_handle_before_lock_release(
    tmp_path, monkeypatch, failure
):
    from tests.cli.test_cli_daemon_loop import _daemon_options, _invoke
    from tests.cli.test_cli_daemon_session import _SignalWaitAdapter

    from millrace.adapters.cli import daemon
    from millrace.adapters.cli import session_coordinator as sessions
    from millrace.adapters.cli.run import (
        BoundedExecutionUnitResult,
        run_bounded_execution_unit,
    )
    from millrace.adapters.runner_contract import AdapterLocalConfig

    paths, a = _retained_fixture(tmp_path)
    code, out, err = _invoke(
        [
            "--json",
            "--workspace",
            str(paths.workspace_path),
            "queue",
            "enqueue",
            "prompt",
            "--payload-json",
            json.dumps({"prompt_id": "b", "body": "b"}),
            "--input-id",
            "second-owner",
        ]
    )
    assert code == 0, (out, err)
    transferred = {}
    token = sessions._RETAINED_OWNERS.set(transferred)
    try:
        with closing(daemon.open_runtime_context(paths, command="test")) as runtime:
            result = run_bounded_execution_unit(
                runtime,
                local_config=AdapterLocalConfig(
                    adapters={"codex": _SignalWaitAdapter()}
                ),
            )
            assert result.code == "runner_session_retained"
    finally:
        sessions._RETAINED_OWNERS.reset(token)
    b = next(iter(transferred.values()))
    cleaned = []

    class Handle:
        def __init__(self, owner):
            self.original = owner.handle
            self.session_id = owner.session.session_id

        def __getattr__(self, name):
            return getattr(self.original, name)

        def poll_completion(self):
            if (
                failure in {"poll", "drain-error"}
                and self.session_id == b.session.session_id
            ):
                raise RuntimeError("injected B poll error")
            return self.original.poll_completion()

        def cleanup(self):
            assert (paths.workspace_path / ".millrace/daemon.lock").exists()
            cleaned.append(self.session_id)
            return self.original.cleanup()

    a.handle = Handle(a)
    b.handle = Handle(b)

    def startup(*args, **kwargs):
        owners = sessions._RETAINED_OWNERS.get()
        owners.update({a.session.session_id: a, b.session.session_id: b})
        return BoundedExecutionUnitResult("no_runner_session_reconciliation")

    monkeypatch.setattr(daemon, "_reconcile_startup_sessions", startup)
    if failure == "drain-error":
        real_step = sessions._step_retained_owner

        def failed_drain(runtime, owner, *, stop_requested):
            if owner is b and stop_requested:
                raise RuntimeError("injected drain persistence error")
            return real_step(runtime, owner, stop_requested=stop_requested)

        monkeypatch.setattr(sessions, "_step_retained_owner", failed_drain)
        from contextlib import nullcontext
        from types import SimpleNamespace

        monkeypatch.setattr(
            daemon,
            "_SignalStop",
            lambda: nullcontext(
                SimpleNamespace(requested=True, wait=lambda seconds: True)
            ),
        )
    original_open = daemon.open_runtime_context

    def opened(*args, **kwargs):
        if failure == "store-open" and sessions._RETAINED_OWNERS.get():
            raise OSError("injected unavailable store")
        return original_open(*args, **kwargs)

    monkeypatch.setattr(daemon, "open_runtime_context", opened)
    summary = daemon.run_daemon_loop(_daemon_options(paths, max_ticks=2))
    assert summary.stopped_reason == "session_reconciliation_required"
    assert set(cleaned) == {a.session.session_id, b.session.session_id}
    assert len(cleaned) == 2
    assert not (paths.workspace_path / ".millrace/daemon.lock").exists()


# Synchronize only outside SQL to exercise the two sides of admission's snapshot.
_HELD_ADMISSION_RACE = r"""
import json, time
from millrace.adapters.cli import daemon, run_controls, session_coordinator as sessions
from millrace.adapters.cli.context import _RunnerStartDeferred
from millrace.substrate.sqlite import SQLiteRuntimeStore
POINT = "RACE_POINT"
original_budget = daemon._budget_exhaustion_reason
original_start = SQLiteRuntimeStore.persist_runner_start
original_admit = run_controls.admit_runner_start
parked = False

def gate(root, name):
    (root / name).write_text('ready')
    until = time.monotonic() + 20
    while not (root / (name + '-release')).exists():
        if time.monotonic() >= until:
            raise RuntimeError('admission race fixture gate expired')
        time.sleep(.01)

def budget(options):
    global parked
    result = original_budget(options)
    owners = sessions._RETAINED_OWNERS.get() or {}
    if POINT == 'selection' and not parked and len(owners) == 1 and all(
        owner.held for owner in owners.values()
    ):
        parked = True
        gate(options.paths.workspace_path, 'r1-snapshot')
    return result

def start(store, state, cas, session, budget_id, clock=None):
    global parked
    owners = sessions._RETAINED_OWNERS.get() or {}
    if (POINT == 'persistence' and not parked and owners
            and session.session_id not in owners):
        assert not store._connection.in_transaction
        assert sessions._retained_start_eligible(state)
        parked = True
        # Use the same workspace as this source daemon command.
        from pathlib import Path
        workspace = Path(WORKSPACE_PATH)
        gate(workspace, 'r1-snapshot')
    return original_start(store, state, cas, session, budget_id, clock)

def admit(runtime, run_ref, session, **kwargs):
    root = runtime.paths.workspace_path
    try:
        result = original_admit(runtime, run_ref, session, **kwargs)
    except _RunnerStartDeferred as exc:
        assert not runtime.store._connection.in_transaction
        state = runtime.store.load_runtime_state(runtime.cas_store)
        current = state.runner_sessions[session.session_id]
        evidence = {
            'session_id': current.session_id, 'state': current.state,
            'start_intent_at': current.start_intent_at,
            'budget_binding': runtime.store.daemon_budget_id_for_session(
                current.session_id),
            'accepted_starts': runtime.store.load_daemon_budget_epoch(
                'native-proof-epoch').accepted_start_count,
            'conflict': None if exc.__cause__ is None else str(exc.__cause__),
        }
        (root / 'r1-deferred.json').write_text(json.dumps(evidence))
        gate(root, 'r1-deferred')
        raise
    if result is not None and (root / 'r1-deferred.json').exists():
        (root / 'r1-admitted.json').write_text(
            json.dumps({'session_id': result.session_id}))
    return result

daemon._budget_exhaustion_reason = budget
SQLiteRuntimeStore.persist_runner_start = start
run_controls.admit_runner_start = admit
from millrace.adapters.cli.main import main
raise SystemExit(main())
"""


@pytest.mark.parametrize("preclaimed", [False, True], ids=["late-B", "preclaimed-B"])
@pytest.mark.parametrize("race_point", ["selection", "persistence"])
def test_start_admission_defers_after_public_resume(
    monkeypatch, preclaimed, race_point
):
    original_popen = subprocess.Popen

    def launch(command, *args, **kwargs):
        if isinstance(command, list) and "daemon" in command and "-c" in command:
            command = list(command)
            workspace = command[command.index("--workspace") + 1]
            script = _HELD_ADMISSION_RACE.replace("RACE_POINT", race_point)
            script = script.replace("WORKSPACE_PATH", repr(workspace))
            command[command.index("-c") + 1] = script
        return original_popen(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", launch)
    case = IndependentCase(preclaimed=preclaimed)
    root = case.paths.workspace_path
    try:
        case.wait(case.server.started.is_set)
        response = case.invoke(case.request())
        assert response.returncode == 0, (response.stdout, response.stderr)
        if not preclaimed:
            case.enqueue_b()
        case.wait(lambda: (root / "r1-snapshot").exists())
        state = case.state()
        control = next(iter(state.run_execution_controls.values()))
        assert control.state == "paused"
        assert case.budget().accepted_start_count == 1
        response = case.invoke(
            case.request(action="runs.resume", pause_id=control.pause_id)
        )
        assert response.returncode == 0, (response.stdout, response.stderr)
        assert json.loads(response.stdout)["data"]["receipt"]["accepted"]
        current = next(iter(case.state().run_execution_controls.values()))
        assert current.state in {"resume_pending", "resumed"}
        (root / "r1-snapshot-release").write_text("resume committed")
        case.wait(lambda: (root / "r1-deferred").exists())
        deferred = json.loads((root / "r1-deferred.json").read_text())
        assert deferred["state"] == "created"
        assert deferred["start_intent_at"] is None
        assert deferred["budget_binding"] is None
        assert deferred["accepted_starts"] == 1
        assert deferred["conflict"] == (
            "stale_runtime_controls" if race_point == "persistence" else None
        )
        assert not (root / "b-before.txt").exists()
        assert not any(
            "independent-B" in json.dumps(request) for request in case.server.requests
        )
        b = next(
            r
            for r in case.state().runs.values()
            if r.activation_id == case.b_activation
        )
        assert b.current_session_id == deferred["session_id"]
        assert case.child.poll() is None
        (root / "r1-deferred-release").write_text("service resumed A")
        case.child.wait(timeout=12)
        admitted = json.loads((root / "r1-admitted.json").read_text())
        assert admitted["session_id"] == deferred["session_id"]
        state = case.state()
        assert len(state.runner_sessions) == len(state.runner_observations) == 2
        assert all(s.state == "completed" for s in state.runner_sessions.values())
        assert len(state.runner_session_cancellation_requests) == 0
        assert case.budget().accepted_start_count == 2
        assert case.budget().cumulative_total_tokens == 40
        assert (root / "b-after.txt").read_text() == "after"
    finally:
        (root / "r1-snapshot-release").write_text("cleanup")
        (root / "r1-deferred-release").write_text("cleanup")
        case.close()


@pytest.mark.parametrize("retained", [False, True])
@pytest.mark.parametrize("expected_state", ["created", "starting"])
@pytest.mark.parametrize(
    "error_type,message,recognized",
    [
        ("control", "stale_runtime_controls", True),
        ("storage", "stale runtime state history changed", True),
        ("control", "fencing_token_mismatch", False),
        ("storage", "corrupt durable record", False),
    ],
)
def test_start_conflict_conversion_is_scoped(
    monkeypatch, retained, expected_state, error_type, message, recognized
):
    from types import SimpleNamespace

    from tests.cli.test_cli_daemon_loop import _claim_only_state

    from millrace import kernel
    from millrace.adapters.cli import context, session_coordinator
    from millrace.contracts.transition import AdvanceRunnerSession
    from millrace.substrate.errors import ControlOperationError, StorageIntegrityError

    run = next(iter(_claim_only_state().runs.values()))
    transition = AdvanceRunnerSession(
        "conflict-scope",
        run_ref=run.run_ref,
        session_id="B",
        dispatch_generation=1,
        session_fencing_token="fence-B",
        expected_state=expected_state,
        next_state="starting",
        occurred_at=2,
    )
    state = SimpleNamespace(runner_sessions={"B": object()})
    error = (
        ControlOperationError(message)
        if error_type == "control"
        else StorageIntegrityError(message)
    )

    def persist(*args):
        raise error

    runtime = SimpleNamespace(
        store=SimpleNamespace(
            load_runtime_state=lambda cas: state, persist_runner_start=persist
        ),
        cas_store=object(),
    )
    monkeypatch.setattr(kernel, "decide", lambda *args: SimpleNamespace(accepted=True))
    monkeypatch.setattr(kernel, "apply", lambda *args: state)
    monkeypatch.setattr(session_coordinator, "_retained_start_eligible", lambda s: True)
    token = session_coordinator._RETAINED_OWNERS.set(
        {"A": object()} if retained else {}
    )
    try:
        deferred = retained and expected_state == "created" and recognized
        exception = context._RunnerStartDeferred if deferred else type(error)
        with pytest.raises(exception) as caught:
            context.persist_runner_transition(runtime, transition)
        if deferred:
            assert caught.value.__cause__ is error
        else:
            assert caught.value is error
    finally:
        session_coordinator._RETAINED_OWNERS.reset(token)
