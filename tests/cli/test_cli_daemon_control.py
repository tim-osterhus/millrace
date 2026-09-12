from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from tests.cli.test_cli_bounded_execution_unit import (
    _codex_success_config,
    _ready_state_with_selected_codex_authority,
    _runtime,
)
from tests.substrate.test_daemon_controls import stop_request

from millrace.adapters.cli.daemon_control import (
    DaemonLifecycle,
    handle_daemon_control,
    inspect_daemon,
)
from millrace.adapters.cli.daemon_listener import (
    DaemonListener,
    endpoint_path,
    exchange,
    validate_path,
)
from millrace.contracts.controls import canonical_json
from millrace.kernel import empty_runtime_state
from millrace.substrate.sqlite import SQLiteRuntimeStore

CORE = Path(__file__).resolve().parents[2]


@pytest.fixture
def short_root(tmp_path):
    root = (
        Path(os.environ.get("MILLRACE_TEST_SOCKET_ROOT", str(tmp_path)))
        / uuid4().hex[:6]
    )
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root)


pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="exact native process/lifecycle qualification requires macOS",
)

def prepared(root, *, active=False, no_plan=False, delay=0):
    state, _ = _ready_state_with_selected_codex_authority()
    if no_plan:
        state = empty_runtime_state()
    elif not active:
        state = replace(
            empty_runtime_state(),
            admitted_plans=state.admitted_plans,
            default_plan_ref=state.default_plan_ref,
        )
    runtime = _runtime(root, state)
    paths = runtime.paths
    config_path = write_config(root, paths, delay=delay)
    runtime.close()
    return paths, config_path


def write_config(root, paths, delay=0):
    config = _codex_success_config(cwd=paths.workspace_path, timeout_seconds=20)
    cfg = config.adapters["codex"]._config
    payload = {
        name: getattr(cfg, name)
        for name in (
            "adapter_id",
            "wrapper_mode",
            "wrapper_argv",
            "cwd",
            "timeout_seconds",
            "max_input_bundle_bytes",
            "max_stdout_bytes",
            "max_stderr_diagnostic_bytes",
            "wrapper_protocol_version",
            "pre_cancelled",
            "live_test_opt_in_env_flags",
        )
    }
    payload["env_allowlist"] = {}
    payload["redaction_policy"] = {
        "policy_id": cfg.redaction_policy.policy_id,
        "secret_tokens": [],
    }
    if delay:
        payload["wrapper_argv"] = list(payload["wrapper_argv"])
        payload["wrapper_argv"][2] = (
            f"import time;time.sleep({delay})\n" + payload["wrapper_argv"][2]
        )
    config_path = root / "config.json"
    config_path.write_text(json.dumps({"codex": payload}, default=str))
    return config_path


def launch(paths, config, *, hold_exit=False, max_ticks=None):
    code = "from millrace.adapters.cli.main import main; import sys; result=main(); "
    code += "sys.stdin.read(1); " if hold_exit else ""
    code += "sys.exit(result)"
    args = [
        sys.executable,
        "-c",
        code,
        "--json",
        "--workspace",
        str(paths.workspace_path),
        "run",
        "daemon",
        "--idle-sleep",
        "0.05",
        "--launch-correlation-id",
        "real-cli-fixture",
    ]
    if config is not None:
        args += ["--adapter-config-json", str(config)]
    if max_ticks is not None:
        args += ["--max-ticks", str(max_ticks)]
    return subprocess.Popen(
        args,
        cwd=CORE,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def observe(paths, predicate, child, timeout=8):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        latest = inspect_daemon(paths, deadline=min(deadline, time.monotonic() + 1.5))
        if predicate(latest):
            return latest
        if child.poll() is not None:
            out, err = child.communicate()
            pytest.fail(
                f"daemon exited {child.returncode}: {out} {err}; observation={latest}"
            )
        time.sleep(0.02)
    pytest.fail(f"observation deadline: {latest}")


def request_for_target(target):
    return {
        "contract_id": "millrace.core.controls",
        "contract_revision": 1,
        "action": "daemon.stop",
        "operation_id": str(uuid4()),
        "caller_id": "test-client",
        "actor_id": "operator",
        "reason": "stop owned fixture",
        "correlation_id": "real-stop",
        "target": target,
    }


def public_stop(paths, request):
    return handle_daemon_control(
        SimpleNamespace(
            command="daemon.stop",
            workspace=str(paths.workspace_path),
            db=None,
            cas=None,
            request_json=canonical_json(request),
        )
    ).data


def terminate_owned(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=3)


def test_public_idle_ready_stop_shutdown_before_exact_exit_and_relaunch(short_root):
    paths, config = prepared(short_root)
    child = launch(paths, config, hold_exit=True)
    try:
        ready = observe(paths, lambda x: x.get("ready"), child)
        assert ready["status"] == "ready_idle" and ready["challenge_verified"]
        assert ready["runner_sessions"] == [] and ready["session_count"] == 0
        assert ready["readiness"]["state"] == "ready_idle"
        assert ready["readiness"]["source_revision"] == ready["source_revision"]
        assert ready["readiness"]["fresh_challenge_required"]
        assert ready["runtime_progress"]["availability"] == "no_work_yet"
        assert ready["runtime_progress"]["last_runtime_progress_at"] is None
        for _ in range(3):
            idle = inspect_daemon(paths, deadline=time.monotonic() + 1)
            assert idle["runtime_progress"] == ready["runtime_progress"]
            assert idle["source_revision"] == ready["source_revision"]
        assert (
            ready["consistency"] == "store_snapshot_with_separate_process_observation"
        )
        assert ready["target"]["expected_runtime"]["build_identity"] is None
        assert ready["process"]["pid"] == child.pid
        duplicate = launch(paths, config)
        out, err = duplicate.communicate(timeout=3)
        assert duplicate.returncode == 3 and "daemon_already_running" in err
        assert out == ""
        started = time.monotonic()
        request = request_for_target(ready["target"])
        accepted = public_stop(paths, request)
        assert time.monotonic() - started < 2
        assert accepted["receipt"]["disposition"] == "accepted_pending"
        final_live = observe(
            paths, lambda x: x.get("status") == "shutdown_complete", child
        )
        assert final_live["daemon_process"] == "live"
        assert final_live["runtime_cleanup"] == "not_required"
        assert not final_live["relaunch_allowed"]
        refused = launch(paths, config)
        refused.communicate(timeout=3)
        assert refused.returncode != 0
        child.communicate("x", timeout=3)
        clean = inspect_daemon(paths, deadline=time.monotonic() + 1)
        assert clean["status"] == "stopped_clean" and clean["relaunch_allowed"]
        replay = public_stop(paths, request)
        assert replay["receipt"] == accepted["receipt"]
        assert replay["results"][-1]["evidence"]["listener_teardown"] == "complete"
        replacement = launch(paths, config)
        try:
            new = observe(paths, lambda x: x.get("ready"), replacement)
            assert new["target"]["daemon_generation"] == 2
            assert new["target"]["process_nonce"] != ready["target"]["process_nonce"]
            assert public_stop(paths, request)["receipt"] == accepted["receipt"]
            assert inspect_daemon(paths, deadline=time.monotonic() + 1)["ready"]
            public_stop(paths, request_for_target(new["target"]))
            replacement.communicate(timeout=3)
        finally:
            terminate_owned(replacement)
    finally:
        terminate_owned(child)


def test_public_active_ready_real_child_cancel_and_all_fenced_cleanup(short_root):
    paths, config = prepared(short_root, active=True, delay=4)
    child = launch(paths, config)
    try:
        ready = observe(
            paths, lambda x: x.get("ready") and x.get("session_count", 0) > 0, child
        )
        assert ready["status"] == "ready_active"
        assert ready["runtime_progress"]["availability"] == "available"
        assert ready["runtime_progress"]["evidence"]["session_snapshot"]["session_id"]
        # Wait until the actual owned adapter subprocess was started.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            store = SQLiteRuntimeStore.open(paths.db_path)
            try:
                states = store._connection.execute(
                    "SELECT state FROM runner_sessions"
                ).fetchall()
            finally:
                store.close()
            if any(row[0] == "running" for row in states):
                break
            time.sleep(0.02)
        if not any(row[0] == "running" for row in states):
            child.terminate()
            out, err = child.communicate(timeout=3)
            pytest.fail(f"states={states}; out={out}; err={err}")
        fresh = inspect_daemon(paths, deadline=time.monotonic() + 1)
        accepted = public_stop(paths, request_for_target(fresh["target"]))
        assert accepted["receipt"]["accepted"]
        out, err = child.communicate(timeout=8)
        assert child.returncode == 0, (out, err)
        clean = inspect_daemon(paths, deadline=time.monotonic() + 1)
        assert clean["status"] == "stopped_clean"
        assert clean["session_count"] == len(clean["runner_sessions"]) == 1
        assert clean["runtime_progress"]["availability"] == "available"
        assert (
            clean["runtime_progress"]["evidence"]["daemon_revision"]
            > ready["runtime_progress"]["evidence"]["daemon_revision"]
        )
        assert (
            clean["runtime_progress"]["evidence"]["session_snapshot"]
            == ready["runtime_progress"]["evidence"]["session_snapshot"]
        )
        assert clean["runner_sessions"][0]["session_fencing_token"]
        assert clean["runner_sessions"][0]["cleanup"] in {"complete", "not_required"}
    finally:
        terminate_owned(child)


@pytest.mark.parametrize("missing", ["plan", "adapter"])
def test_public_missing_selected_authority_never_ready(short_root, missing):
    paths, config = prepared(short_root, no_plan=missing == "plan")
    child = launch(paths, config if missing == "plan" else None)
    out, err = child.communicate(timeout=3)
    assert child.returncode != 0 and "ready_state_refused" in err, (out, err)
    projection = inspect_daemon(paths, deadline=time.monotonic() + 1)
    assert not projection["ready"]
    assert projection["session_count"] == 0


def test_public_operator_signal_retains_one_synthetic_stop(short_root):
    paths, config = prepared(short_root)
    child = launch(paths, config)
    try:
        observe(paths, lambda x: x.get("ready"), child)
        child.send_signal(signal.SIGTERM)
        child.communicate(timeout=3)
        store = SQLiteRuntimeStore.open(paths.db_path)
        try:
            record = store.daemon_records()[-1]
            assert record["stop_key"][3] == "core.local-signal"
            assert (
                store._connection.execute(
                    "SELECT count(*) FROM control_operations"
                ).fetchone()[0]
                == 1
            )
        finally:
            store.close()
        assert (
            inspect_daemon(paths, deadline=time.monotonic() + 1)["status"]
            == "stopped_clean"
        )
    finally:
        terminate_owned(child)


def test_committed_stop_with_event_delivery_failure_reasserts_exact_incarnation(
    short_root,
):
    paths, _ = prepared(short_root)
    lifecycle = DaemonLifecycle(paths, "event-failure")
    lifecycle.initialize()

    class FailingEvent:
        def set(self):
            raise RuntimeError("injected event loss")

    lifecycle.stop_event = FailingEvent()
    try:
        runtime = SQLiteRuntimeStore.open(paths.db_path)
        runtime.update_daemon(lifecycle.scope, status="ready_idle")
        request = stop_request(SimpleNamespace(store=runtime), lifecycle.scope)
        runtime.close()
        with pytest.raises(RuntimeError, match="event loss"):
            lifecycle._handle(
                {
                    "method": "stop",
                    "challenge": str(uuid4()),
                    "request": request.payload,
                },
                time.monotonic() + 1,
            )
        store = SQLiteRuntimeStore.open(paths.db_path)
        try:
            store.daemon_scope = lifecycle.scope
            assert store.show_operation(request)["receipt"]["accepted"]
            with pytest.raises(RuntimeError, match="daemon_admission_stopped"):
                store.admit_daemon_unit()
        finally:
            store.close()
        import threading

        event = threading.Event()
        lifecycle.stop_event = event
        assert lifecycle.stop_requested() and event.is_set()
    finally:
        lifecycle.stop_event = None
        lifecycle.finish("signal")


@pytest.mark.parametrize(
    "unsafe", ["directory_mode", "socket_mode", "symlink", "long_path"]
)
def test_owner_path_refusals_preserve_endpoint(short_root, unsafe):
    path = endpoint_path(short_root)
    path.parent.mkdir(parents=True, mode=0o700)
    listener = DaemonListener(
        path, lambda message, _: {"challenge": message["challenge"]}
    )
    if unsafe == "long_path":
        with pytest.raises(ValueError, match="path_unsupported"):
            validate_path(path / ("x" * 104))
        listener.close()
        return
    listener.start()
    try:
        if unsafe == "directory_mode":
            path.parent.chmod(0o755)
        elif unsafe == "socket_mode":
            path.chmod(0o666)
        else:
            other = short_root / "symlink"
            other.symlink_to(path.parent, target_is_directory=True)
            path = other / "s"
        with pytest.raises(ValueError, match="unsafe"):
            validate_path(path)
    finally:
        listener.path.parent.chmod(0o700)
        listener.close()


def test_listener_refuses_foreign_peer_predicate_methods_and_oversized_input(
    short_root, monkeypatch
):
    from millrace.adapters.cli import daemon_listener

    path = endpoint_path(short_root)
    path.parent.mkdir(parents=True, mode=0o700)
    calls = []
    listener = DaemonListener(
        path, lambda msg, _: calls.append(msg) or {"challenge": msg["challenge"]}
    )
    listener.start()
    try:
        result = exchange(
            path,
            {"method": "status", "challenge": str(uuid4())},
            deadline=time.monotonic() + 1,
        )
        assert result["challenge"]
        assert len(calls) == 1
        for raw in (b'{"method":"execute","challenge":"x"}\n', b"x" * 17000 + b"\n"):
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(1)
                sock.connect(str(path))
                sock.sendall(raw)
                try:
                    assert sock.recv(100) == b""
                except ConnectionResetError:
                    pass
        monkeypatch.setattr(daemon_listener, "same_uid_peer", lambda _: False)
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(1)
            sock.connect(str(path))
            sock.sendall(b"{}\n")
            try:
                assert sock.recv(100) == b""
            except ConnectionResetError:
                pass
        assert len(calls) == 1  # Predicate fixture, not a real other-UID account.
    finally:
        assert listener.close()


def test_created_hold_classifies_before_drive_and_unknown_attempt_refuses(
    tmp_path, monkeypatch
):
    from millrace.adapters.cli import run
    from support.run_controls import pause, runtime_with_run, start_intent

    runtime, current = runtime_with_run(tmp_path, session=True)
    config = _codex_success_config()
    monkeypatch.setattr(
        run,
        "run_bounded_execution_unit",
        lambda *_a, **_k: pytest.fail("classification drove work"),
    )
    assert (
        run.classify_daemon_startup(runtime, local_config=config, adapter_kind=None)
        == "ready_active"
    )
    pause(runtime, current)
    assert (
        run.classify_daemon_startup(runtime, local_config=config, adapter_kind=None)
        == "ready_idle"
    )
    assert (
        runtime.store.load_runtime_state(runtime.cas_store)
        .runner_sessions[current.current_session_id]
        .state
        == "created"
    )
    runtime.close()
    other, current = runtime_with_run(tmp_path / "unknown", session=True)
    start_intent(other, current)
    assert (
        run.classify_daemon_startup(other, local_config=config, adapter_kind=None)
        == "not_ready"
    )
    other.close()


def test_lost_socket_never_proves_clean_and_stale_endpoint_not_adopted(short_root):
    paths, config = prepared(short_root)
    child = launch(paths, config)
    try:
        observe(paths, lambda x: x.get("ready"), child)
        endpoint_path(paths.workspace_path).unlink()
        observed = inspect_daemon(paths, deadline=time.monotonic() + 1)
        assert observed["status"] == "unknown" and observed["daemon_process"] == "live"
        assert not observed["relaunch_allowed"] and not observed["ready"]
        child.send_signal(signal.SIGTERM)
        child.communicate(timeout=3)
        observed = inspect_daemon(paths, deadline=time.monotonic() + 1)
        assert (
            observed["runtime_cleanup"] == "unknown"
            and not observed["relaunch_allowed"]
        )
        refused = launch(paths, config)
        refused.communicate(timeout=3)
        assert refused.returncode != 0
    finally:
        terminate_owned(child)


def test_real_blocked_endpoint_inspect_and_stop_caller_deadlines(short_root):
    from millrace.adapters.cli.context import CliCommandError

    paths, _ = prepared(short_root)
    lifecycle = DaemonLifecycle(paths, "blocked-endpoint")
    lifecycle.initialize()
    store = SQLiteRuntimeStore.open(paths.db_path)
    store.update_daemon(lifecycle.scope, status="ready_idle")
    request = stop_request(SimpleNamespace(store=store), lifecycle.scope)
    store.close()

    def blocked(message, _deadline):
        time.sleep(2.2)
        return {"challenge": message["challenge"]}

    lifecycle.listener.handler = blocked
    try:
        started = time.monotonic()
        with pytest.raises(CliCommandError) as failure:
            public_stop(paths, request.payload)
        assert failure.value.details["status"] == "unknown"
        assert 1.4 < time.monotonic() - started < 2
        time.sleep(0.6)
        started = time.monotonic()
        value = inspect_daemon(paths, deadline=time.monotonic() + 1.5)
        assert time.monotonic() - started < 2
        assert value["status"] == "unknown" and value["daemon_process"] == "live"
        assert not value["relaunch_allowed"]
    finally:
        time.sleep(0.8)
        lifecycle.finish("max_ticks")


@pytest.mark.parametrize("wait_for", ["readiness", "exit", "cleanup"])
def test_unready_observation_bound_is_ten_seconds_without_launch_or_cleanup_claim(
    short_root,
    wait_for,
):
    paths, config = prepared(short_root)
    child = launch(
        paths, config, hold_exit=True, max_ticks=1 if wait_for != "cleanup" else None
    )
    try:
        if wait_for == "cleanup":
            observe(paths, lambda x: x.get("ready"), child)
            endpoint_path(paths.workspace_path).unlink()
            child.send_signal(signal.SIGTERM)
        observe(paths, lambda x: x.get("status") == "shutdown_complete", child)
        started = time.monotonic()
        result = handle_daemon_control(
            SimpleNamespace(
                command="daemon.inspect",
                workspace=str(paths.workspace_path),
                db=None,
                cas=None,
                wait_for=wait_for,
                after_session=0,
                expected_source_revision=None,
            )
        ).data
        elapsed = time.monotonic() - started
        assert 9 <= elapsed < 10
        assert (
            not result["observation_satisfied"] and result["daemon_process"] == "live"
        )
        assert (
            result["status"] == "shutdown_complete" and not result["relaunch_allowed"]
        )
        child.communicate("x", timeout=3)
    finally:
        terminate_owned(child)


@pytest.mark.parametrize("mode", [0o777, 0o775])
def test_writable_ancestor_refuses_before_listener_creation(short_root, mode):
    private = short_root / ".millrace"
    private.mkdir(mode=mode)
    private.chmod(mode)
    path = endpoint_path(short_root)
    listener = DaemonListener(path, lambda *_: pytest.fail("unsafe admission"))
    try:
        with pytest.raises(ValueError, match="unsafe"):
            listener.start()
        assert not path.parent.exists()
    finally:
        private.chmod(0o755)
        listener.close()


def test_symlink_ancestor_refuses_without_creating_external_directory(short_root):
    external = short_root / "elsewhere"
    external.mkdir()
    (short_root / ".millrace").symlink_to(external, target_is_directory=True)
    listener = DaemonListener(endpoint_path(short_root), lambda *_: {})
    try:
        with pytest.raises(ValueError, match="unsafe"):
            listener.start()
        assert list(external.iterdir()) == []
    finally:
        listener.close()


def test_selected_adapter_missing_authority_pin_refuses_ready(tmp_path):
    from tests.cli.test_cli_bounded_execution_unit import _ready_state

    from millrace.adapters.cli.run import classify_daemon_startup

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    try:
        assert (
            classify_daemon_startup(
                runtime, local_config=_codex_success_config(), adapter_kind=None
            )
            == "not_ready"
        )
    finally:
        runtime.close()


def test_old_dead_owner_without_summary_blocks_relaunch(short_root):
    paths, config = prepared(short_root)
    child = launch(paths, config)
    try:
        observe(paths, lambda x: x.get("ready"), child)
        child.kill()
        child.communicate(timeout=3)
        value = inspect_daemon(paths, deadline=time.monotonic() + 1)
        assert value["status"] == "unknown" and value["daemon_process"] == "not_live"
        assert value["final_summary"] is None and not value["relaunch_allowed"]
        lock = paths.workspace_path / ".millrace" / "daemon.lock"
        assert lock.exists()
        before = lock.read_bytes()
        refused = launch(paths, config)
        refused.communicate(timeout=3)
        assert refused.returncode != 0 and lock.read_bytes() == before
    finally:
        terminate_owned(child)


def test_unmanaged_lock_refuses_public_takeover_without_private_pid_fallback(
    short_root,
):
    paths, _ = prepared(short_root)
    lock = paths.workspace_path / ".millrace" / "daemon.lock"
    lock.write_text("opaque old daemon lock, never parse as process authority")
    before = lock.read_bytes()
    result = inspect_daemon(paths, deadline=time.monotonic() + 1)
    assert result["status"] == "incompatible_unmanaged"
    assert not result["ready"] and not result["relaunch_allowed"]
    assert result["daemon_process"] == "unknown"
    assert lock.read_bytes() == before


def test_public_unsupported_long_endpoint_has_typed_lifecycle_refusal(tmp_path):
    paths, config = prepared(tmp_path)
    child = launch(paths, config, max_ticks=1)
    try:
        out, err = child.communicate(timeout=3)
        assert child.returncode == 3 and not out
        result = json.loads(err)
        assert result["code"] == "daemon_lifecycle_refused"
        assert result["details"] == {"status": "unknown", "ready": False}
    finally:
        terminate_owned(child)


@pytest.mark.parametrize(
    "field,value", [("runtime", {}), ("plan_fingerprint", "invalid")]
)
def test_repair1_bad_retained_identity_after_real_exit_blocks_public_replacement(
    short_root, field, value
):
    from tests.substrate.test_daemon_controls import (
        replace_retained_identity_for_corruption,
    )

    from millrace.substrate.errors import ControlOperationError

    paths, config = prepared(short_root)
    child = launch(paths, config, max_ticks=1)
    try:
        child.communicate(timeout=4)
        assert child.returncode == 0
        assert (
            inspect_daemon(paths, deadline=time.monotonic() + 1)["status"]
            == "stopped_clean"
        )
        store = SQLiteRuntimeStore.open(paths.db_path)
        replace_retained_identity_for_corruption(store, field, value)
        store.close()
        before = paths.db_path.read_bytes()
        with pytest.raises(ControlOperationError, match="unknown"):
            inspect_daemon(paths, deadline=time.monotonic() + 1)
        assert paths.db_path.read_bytes() == before
        replacement = launch(paths, config, max_ticks=1)
        try:
            out, err = replacement.communicate(timeout=4)
            assert replacement.returncode == 3 and not out
            assert json.loads(err)["code"] == "daemon_lifecycle_refused"
            assert paths.db_path.read_bytes() == before
        finally:
            terminate_owned(replacement)
    finally:
        terminate_owned(child)


def test_repair1_valid_historical_runtime_preserves_clean_evidence_and_relaunch(
    short_root,
):
    from tests.substrate.test_daemon_controls import (
        replace_retained_identity_for_corruption,
    )

    paths, config = prepared(short_root)
    child = launch(paths, config, max_ticks=1)
    try:
        child.communicate(timeout=4)
        assert child.returncode == 0
        old = inspect_daemon(paths, deadline=time.monotonic() + 1)
        historical = dict(
            old["target"]["expected_runtime"],
            runtime_version="0.21.0-historical",
            public_contract_revision=0,
            store_schema_version=10,
            public_schema_digest="0" * 64,
            build_identity="a" * 64,
        )
        store = SQLiteRuntimeStore.open(paths.db_path)
        replace_retained_identity_for_corruption(store, "runtime", historical)
        store.close()
        compatible = inspect_daemon(paths, deadline=time.monotonic() + 1)
        assert (
            compatible["status"] == "stopped_clean" and compatible["relaunch_allowed"]
        )
        assert compatible["target"]["expected_runtime"] == historical
        replacement = launch(paths, config, max_ticks=1)
        try:
            replacement.communicate(timeout=4)
            assert replacement.returncode == 0
            current = inspect_daemon(paths, deadline=time.monotonic() + 1)
            assert current["target"]["daemon_generation"] == 2
            assert current["target"]["expected_runtime"] != historical
        finally:
            terminate_owned(replacement)
    finally:
        terminate_owned(child)


@pytest.mark.parametrize(
    "arrival",
    [
        "during_read",
        "after_last_read",
        "during_handler_restore",
        "exception_return",
        "during_persist",
    ],
)
def test_repair1_signal_order_drains_before_context_exit(monkeypatch, arrival):
    from millrace.adapters.cli.daemon import _SignalStop
    from millrace.adapters.cli.daemon_control import ACTIVE_LIFECYCLE

    stop = _SignalStop()
    calls = []

    class Lifecycle:
        scope = {"daemon_id": "owned"}

        def stop_requested(self):
            if arrival == "during_read":
                stop._handle(signal.SIGTERM, None)
            return False

        def signal_stop(self):
            calls.append("persist")
            if arrival == "during_persist":
                stop._handle(signal.SIGINT, None)

    original_signal = signal.signal

    def restore_with_arrival(signum, handler):
        result = original_signal(signum, handler)
        if (
            arrival == "during_handler_restore"
            and signum == signal.SIGTERM
            and handler != stop._handle
        ):
            stop._handle(signal.SIGTERM, None)
        return result

    monkeypatch.setattr(signal, "signal", restore_with_arrival)
    token = ACTIVE_LIFECYCLE.set(Lifecycle())
    try:

        def terminal_path():
            with stop:
                if arrival == "during_persist":
                    stop._handle(signal.SIGTERM, None)
                result = stop.requested
                if arrival in {"after_last_read", "exception_return"}:
                    assert not result
                    stop._handle(signal.SIGTERM, None)
                if arrival == "exception_return":
                    raise RuntimeError("terminal fixture")
                return result

        if arrival == "exception_return":
            with pytest.raises(RuntimeError, match="terminal fixture"):
                terminal_path()
        else:
            assert terminal_path() == (arrival in {"during_read", "during_persist"})
        assert calls == ["persist"]
    finally:
        ACTIVE_LIFECYCLE.reset(token)


@pytest.mark.parametrize("attempt", range(12))
def test_repair1_repeated_real_public_signals_retain_one_receipt(short_root, attempt):
    # Each parameter owns a fresh process, endpoint and store. Both operator signals
    # must reach the same normal-code coalescing path, independent of timing.
    paths, config = prepared(short_root)
    child = launch(paths, config)
    try:
        observe(paths, lambda row: row.get("ready"), child)
        child.send_signal(signal.SIGTERM if attempt % 2 else signal.SIGINT)
        child.communicate(timeout=4)
        assert child.returncode == 0
        store = SQLiteRuntimeStore.open(paths.db_path)
        try:
            record = store.daemon_snapshot()["record"]
            assert record["stop_key"][3] == "core.local-signal"
            assert (
                store._connection.execute(
                    "SELECT count(*) FROM control_operations"
                ).fetchone()[0]
                == 1
            )
            assert record["final_summary"]["runtime_cleanup"] == "not_required"
        finally:
            store.close()
        assert (
            inspect_daemon(paths, deadline=time.monotonic() + 1)["status"]
            == "stopped_clean"
        )
    finally:
        terminate_owned(child)
